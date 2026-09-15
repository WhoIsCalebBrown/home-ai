import io
import wave

from assistant.tts_audio import apply_pcm16_headroom, merge_wav_chunks


def wav(samples: list[int], rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True) for value in samples))
    return output.getvalue()


def samples(payload: bytes) -> list[int]:
    with wave.open(io.BytesIO(payload), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return [int.from_bytes(raw[index:index + 2], "little", signed=True) for index in range(0, len(raw), 2)]


def test_pocket_wav_chunks_are_reassembled_as_one_continuous_wav():
    merged = merge_wav_chunks([wav([1, 2, 3]), wav([4, 5])])
    with wave.open(io.BytesIO(merged), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getnframes() == 5
    assert samples(merged) == [1, 2, 3, 4, 5]


def test_hot_pcm_is_scaled_instead_of_hard_clipped():
    safe = apply_pcm16_headroom(wav([-32768, 0, 32767]))
    values = samples(safe)
    assert max(abs(value) for value in values) == 29490
    assert values[0] < 0 and values[-1] > 0


def test_normal_pcm_is_not_rescaled():
    original = wav([-12000, 0, 12000])
    assert apply_pcm16_headroom(original) == original


def test_mixed_stream_formats_fail_closed():
    try:
        merge_wav_chunks([wav([1], 24000), wav([2], 22050)])
    except ValueError as exc:
        assert "inconsistent" in str(exc)
    else:
        raise AssertionError("mixed Pocket stream formats must not be joined")
