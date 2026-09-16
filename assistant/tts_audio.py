"""Small, dependency-free helpers for safe TTS transport."""

from __future__ import annotations

import io
import sys
import wave
from array import array


def merge_wav_chunks(chunks: list[bytes]) -> bytes:
    """Join independently framed PCM WAV chunks into one continuous WAV.

    Pocket TTS emits short WAV-framed stream chunks.  Sending those frames as
    separate Web Audio sources makes every decoder/source boundary audible on
    some browsers.  We validate the format and join the PCM frames before the
    browser sees them.
    """
    if not chunks:
        return b""
    rate = width = channels = None
    frames = bytearray()
    for payload in chunks:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            current = (wav.getframerate(), wav.getsampwidth(), wav.getnchannels(), wav.getcomptype())
            if current[3] != "NONE":
                raise ValueError("compressed TTS WAV is unsupported")
            if rate is None:
                rate, width, channels = current[:3]
            elif current[:3] != (rate, width, channels):
                raise ValueError("inconsistent TTS WAV format")
            frames.extend(wav.readframes(wav.getnframes()))
    if width != 2 or channels != 1:
        raise ValueError("Pocket TTS transport requires mono 16-bit PCM")
    return _wav_from_pcm(bytes(frames), rate, width, channels)


def apply_pcm16_headroom(wav_bytes: bytes, target_peak: int = 29490) -> bytes:
    """Avoid hard-clipping already-hot PCM while preserving normal levels."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate, width, channels = wav.getframerate(), wav.getsampwidth(), wav.getnchannels()
        frames = wav.readframes(wav.getnframes())
    if width != 2 or channels != 1 or not frames:
        return wav_bytes
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(value) for value in samples)
    if peak <= target_peak:
        return wav_bytes
    scale = target_peak / peak
    for index, value in enumerate(samples):
        samples[index] = max(-32768, min(32767, round(value * scale)))
    if sys.byteorder != "little":
        samples.byteswap()
    return _wav_from_pcm(samples.tobytes(), rate, width, channels)


def prepend_silence(wav_bytes: bytes, milliseconds: int = 120) -> bytes:
    """Add a short clean attack so the first phoneme is not clipped by playback."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate, width, channels = wav.getframerate(), wav.getsampwidth(), wav.getnchannels()
        frames = wav.readframes(wav.getnframes())
    if width != 2 or not frames or milliseconds <= 0:
        return wav_bytes
    silence = b"\x00" * int(rate * milliseconds / 1000) * width * channels
    return _wav_from_pcm(silence + frames, rate, width, channels)


def _wav_from_pcm(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return output.getvalue()
