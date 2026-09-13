import io
import os
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import chatterbox.tts_turbo as turbo_module
from chatterbox.tts_turbo import ChatterboxTurboTTS

# Keep the official model's tokenizer input type stable with current wheels.
_resample = turbo_module.librosa.resample
turbo_module.librosa.resample = lambda *args, **kwargs: np.asarray(_resample(*args, **kwargs), dtype=np.float32)

REFERENCE = Path(os.getenv("CHATTERBOX_REFERENCE", "/chatterbox/reference/selected.wav"))
app = FastAPI(title="Chatterbox-Turbo")
model = None


class SpeechRequest(BaseModel):
    text: str
    response_format: str = "wav"


@app.on_event("startup")
def load_model():
    global model
    model = ChatterboxTurboTTS.from_pretrained(device="cuda")
    if torch.cuda.is_available():
        torch.cuda.synchronize()


@app.get("/health")
def health():
    return {"ok": model is not None, "model": "ResembleAI Chatterbox-Turbo", "device": torch.cuda.get_device_name() if torch.cuda.is_available() else "cpu", "reference": str(REFERENCE)}


@app.post("/v1/audio/speech")
def speech(request: SpeechRequest):
    if model is None:
        raise HTTPException(503, "model is not loaded")
    if not request.text.strip():
        raise HTTPException(400, "text is required")
    if not REFERENCE.is_file():
        raise HTTPException(503, "reference voice is missing")
    started = time.perf_counter()
    print(f"CHATTERBOX_TIMING event=generation_start t={time.time():.6f} text={request.text.strip()!r}", flush=True)
    with torch.inference_mode():
        audio = model.generate(request.text.strip(), audio_prompt_path=str(REFERENCE))
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    pcm = audio.detach().float().cpu().numpy().squeeze()
    output = io.BytesIO()
    sf.write(output, pcm, 24000, format="WAV", subtype="PCM_16")
    elapsed = time.perf_counter() - started
    print(f"CHATTERBOX_TIMING event=generation_complete t={time.time():.6f} elapsed={elapsed:.4f} text={request.text.strip()!r}", flush=True)
    return Response(content=output.getvalue(), media_type="audio/wav", headers={"X-TTS-Elapsed": f"{elapsed:.4f}", "X-TTS-Provider": "chatterbox-turbo"})
