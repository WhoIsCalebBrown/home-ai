from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from pocket_tts import TTSModel

app = FastAPI(title="Pocket TTS")
LANGUAGE = os.getenv("POCKET_LANGUAGE", "english")
VOICE_STATE = Path(os.getenv("POCKET_VOICE_STATE", "/data/ref.voice.safetensors"))
MODEL: TTSModel | None = None
VOICE_STATE_DATA = None
LOCK = asyncio.Lock()
THREAD_LOCK = threading.Lock()
SACRIFICIAL_PREFIX = os.getenv("POCKET_SACRIFICIAL_PREFIX", "Okay.").strip()
POCKET_TEMP = float(os.getenv("POCKET_TEMP", "0.1"))
POCKET_SAMPLER_DECODE_STEPS = int(os.getenv("POCKET_SAMPLER_DECODE_STEPS", "1"))
POCKET_QUANTIZE = os.getenv("POCKET_QUANTIZE", "0").lower() in {"1", "true", "yes"}
POCKET_DEVICE = os.getenv("POCKET_DEVICE", "auto").lower()
POCKET_WARMUP = os.getenv("POCKET_WARMUP", "1").lower() in {"1", "true", "yes"}


class SpeechRequest(BaseModel):
    input: str


def generation_input(text: str) -> str:
    return f"{SACRIFICIAL_PREFIX} {text}".strip() if SACRIFICIAL_PREFIX else text


def find_sacrificial_boundary(audio):
    """Remove the generated prefix using its real speech pause, not a fixed trim."""
    if not SACRIFICIAL_PREFIX:
        return 0
    samples = audio.detach().float().cpu().numpy().reshape(-1)
    if samples.size == 0:
        return None
    rate = MODEL.config.mimi.sample_rate
    frame = max(1, rate // 100)  # 10 ms analysis frames
    peak = float(np.max(np.abs(samples)))
    threshold = max(0.008, peak * 0.03)
    voiced = False
    quiet_start = None
    minimum_end = max(0.20, 0.08 * len(SACRIFICIAL_PREFIX.split()))
    for pos in range(0, len(samples) - frame + 1, frame):
        rms = float(np.sqrt(np.mean(np.square(samples[pos:pos + frame]))))
        elapsed = (pos + frame) / rate
        if rms >= threshold:
            voiced = True
            quiet_start = None
            continue
        if not voiced:
            continue
        quiet_start = quiet_start if quiet_start is not None else pos
        # The first post-prefix pause is expected shortly after the prefix.
        # Do not mistake a later intra-sentence pause for the prefix boundary.
        if elapsed > minimum_end + 0.35:
            break
        if elapsed >= minimum_end and pos + frame - quiet_start >= int(0.05 * rate):
            cut = pos + frame
            print(f"PREFIX_BOUNDARY prefix={SACRIFICIAL_PREFIX!r} cut_ms={cut * 1000 / rate:.1f}", flush=True)
            return cut
    return None


def strip_sacrificial_prefix(audio):
    cut = find_sacrificial_boundary(audio)
    if cut is None:
        print("PREFIX_BOUNDARY_NOT_FOUND", flush=True)
        return None
    return audio[..., cut:]


def generate_audio_without_prefix(text: str):
    """Generate once with the stabilizing prefix, then fail safe if it cannot be located."""
    audio = MODEL.generate_audio(VOICE_STATE_DATA, generation_input(text))
    stripped = strip_sacrificial_prefix(audio)
    if stripped is not None:
        return stripped
    print("PREFIX_FALLBACK_REGENERATE_WITHOUT_PREFIX", flush=True)
    return MODEL.generate_audio(VOICE_STATE_DATA, text)


def generate_original_stream(text: str):
    return MODEL.generate_audio_stream(VOICE_STATE_DATA, text)


def wav_bytes(audio) -> bytes:
    samples = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    samples = np.asarray(samples).reshape(-1)
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(MODEL.config.mimi.sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


@app.on_event("startup")
async def load() -> None:
    global MODEL, VOICE_STATE_DATA
    MODEL = TTSModel.load_model(language=LANGUAGE, temp=POCKET_TEMP, sampler_decode_steps=POCKET_SAMPLER_DECODE_STEPS, quantize=POCKET_QUANTIZE)
    device = "cuda" if POCKET_DEVICE == "auto" and torch.cuda.is_available() else POCKET_DEVICE
    if device not in {"", "auto", "cpu"}:
        MODEL.to(device)
    elif device == "cpu":
        MODEL.to("cpu")
    if not VOICE_STATE.is_file():
        raise RuntimeError(f"Pocket voice state not found: {VOICE_STATE}")
    VOICE_STATE_DATA = MODEL.get_state_for_audio_prompt(str(VOICE_STATE))
    if POCKET_WARMUP:
        started = time.perf_counter()
        await asyncio.to_thread(MODEL.generate_audio, VOICE_STATE_DATA, generation_input("Ready."))
        print(f"TTS_TIMING event=startup_warmup_complete elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)


@app.get("/health")
async def health():
    device = str(getattr(MODEL, "device", "unknown")) if MODEL is not None else "not_loaded"
    cuda_available = bool(torch.cuda.is_available())
    return {
        "ok": MODEL is not None,
        "language": LANGUAGE,
        "voice_state": str(VOICE_STATE),
        "device": device,
        "cuda_available": cuda_available,
        "gpu": torch.cuda.get_device_name(0) if cuda_available else None,
        "temperature": POCKET_TEMP,
        "sampler_decode_steps": POCKET_SAMPLER_DECODE_STEPS,
        "quantize": POCKET_QUANTIZE,
        "sacrificial_prefix": bool(SACRIFICIAL_PREFIX),
        "model_resident": MODEL is not None,
        "voice_state_cached": VOICE_STATE_DATA is not None,
        "startup_warmup": POCKET_WARMUP,
    }


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest):
    if MODEL is None or VOICE_STATE_DATA is None:
        raise HTTPException(status_code=503, detail="Pocket model is not ready")
    text = request.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is required")
    async with LOCK:
        started = time.perf_counter()
        print(f"TTS_TIMING event=request_started t={time.time():.6f}", flush=True)
        audio = await asyncio.to_thread(generate_audio_without_prefix, text)
        print(f"TTS_TIMING event=generation_complete elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)
    return Response(wav_bytes(audio), media_type="audio/wav")


@app.post("/v1/audio/stream")
async def stream(request: SpeechRequest):
    if MODEL is None or VOICE_STATE_DATA is None:
        raise HTTPException(status_code=503, detail="Pocket model is not ready")
    text = request.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is required")

    async def chunks():
        output: queue.Queue = queue.Queue()

        def generate():
            try:
                with THREAD_LOCK:
                    started = time.perf_counter()
                    print(f"TTS_TIMING event=stream_request_started t={time.time():.6f}", flush=True)
                    prefix_chunks = []
                    prefix_removed = not bool(SACRIFICIAL_PREFIX)
                    first_usable_sent = False
                    for chunk in MODEL.generate_audio_stream(VOICE_STATE_DATA, generation_input(text)):
                        if not prefix_removed:
                            prefix_chunks.append(chunk)
                            combined = torch.cat(prefix_chunks, dim=0)
                            cut = find_sacrificial_boundary(combined)
                            if cut is None:
                                continue
                            audio = combined[..., cut:]
                            prefix_removed = True
                            prefix_chunks = []
                            print(f"TTS_TIMING event=prefix_boundary_identified elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)
                        else:
                            audio = chunk
                        if audio.numel():
                            if not first_usable_sent:
                                first_usable_sent = True
                                print(f"TTS_TIMING event=first_real_pcm_available elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)
                            output.put(wav_bytes(audio))
                    if not prefix_removed:
                        print("PREFIX_BOUNDARY_NOT_FOUND", flush=True)
                        print("PREFIX_FALLBACK_REGENERATE_WITHOUT_PREFIX", flush=True)
                        for chunk in generate_original_stream(text):
                            if chunk.numel():
                                if not first_usable_sent:
                                    first_usable_sent = True
                                    print(f"TTS_TIMING event=first_real_pcm_available elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)
                                output.put(wav_bytes(chunk))
                    print(f"TTS_TIMING event=generation_complete elapsed_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)
            except Exception as exc:  # surfaced as a terminal stream error
                output.put(exc)
            finally:
                output.put(None)

        threading.Thread(target=generate, daemon=True).start()
        while True:
            item = await asyncio.to_thread(output.get)
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield (json.dumps({"audio": base64.b64encode(item).decode("ascii")}) + "\n").encode("utf-8")

    return StreamingResponse(chunks(), media_type="application/octet-stream")
