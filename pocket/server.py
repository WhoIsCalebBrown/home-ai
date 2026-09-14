from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import queue
import threading
import wave
from pathlib import Path

import numpy as np
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


class SpeechRequest(BaseModel):
    input: str


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
    MODEL = TTSModel.load_model(language=LANGUAGE)
    if not VOICE_STATE.is_file():
        raise RuntimeError(f"Pocket voice state not found: {VOICE_STATE}")
    VOICE_STATE_DATA = MODEL.get_state_for_audio_prompt(str(VOICE_STATE))


@app.get("/health")
async def health():
    return {"ok": MODEL is not None, "language": LANGUAGE, "voice_state": str(VOICE_STATE)}


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest):
    if MODEL is None or VOICE_STATE_DATA is None:
        raise HTTPException(status_code=503, detail="Pocket model is not ready")
    text = request.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is required")
    async with LOCK:
        audio = await asyncio.to_thread(MODEL.generate_audio, VOICE_STATE_DATA, text)
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
                    for chunk in MODEL.generate_audio_stream(VOICE_STATE_DATA, text):
                        output.put(wav_bytes(chunk))
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
