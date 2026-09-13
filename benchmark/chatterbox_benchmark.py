import json
import os
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS
import chatterbox.tts_turbo as turbo_module


OUT = Path(os.environ.get("BENCHMARK_OUT", "/benchmark/samples"))
REF = Path(os.environ["REFERENCE_WAV"])
OUT.mkdir(parents=True, exist_ok=True)

# Resemble's current source path can receive float64 from newer librosa/scipy
# resampling wheels. The tokenizer expects float32 audio tensors.
_resample = turbo_module.librosa.resample
turbo_module.librosa.resample = lambda *args, **kwargs: np.asarray(_resample(*args, **kwargs), dtype=np.float32)

sentences = [
    ("server", "Hey, I checked your server. Everything looks good right now."),
    ("downloads", "You still have a few downloads waiting, but nothing appears stuck."),
    ("check", "Alright, give me a second and I'll check that for you."),
    ("chuckle", "Okay, that was unexpected. [chuckle] Let me take another look."),
]

def sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()

def vram():
    if not torch.cuda.is_available():
        return {}
    return {
        "allocated_mib": round(torch.cuda.memory_allocated() / 1024**2, 1),
        "reserved_mib": round(torch.cuda.memory_reserved() / 1024**2, 1),
        "max_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
    }

def save_audio(path: Path, audio):
    data = audio.detach().float().cpu().numpy().squeeze()
    sf.write(path, data, 24000, subtype="PCM_16")
    return len(data) / 24000.0

def generate(model, name, text, ref):
    sync()
    start = time.perf_counter()
    audio = model.generate(text, audio_prompt_path=str(ref))
    sync()
    elapsed = time.perf_counter() - start
    seconds = save_audio(OUT / f"{name}.wav", audio)
    return {
        "name": name,
        "text": text,
        "generation_seconds": round(elapsed, 4),
        "audio_seconds": round(seconds, 4),
        "realtime_factor": round(seconds / elapsed, 3) if elapsed else None,
        "time_to_first_audio_seconds": round(elapsed, 4),
        "vram": vram(),
    }

load_start = time.perf_counter()
model = ChatterboxTurboTTS.from_pretrained(device="cuda")
sync()
load_seconds = time.perf_counter() - load_start
load_vram = vram()

results = []
for index, (name, text) in enumerate(sentences):
    item = generate(model, name, text, REF)
    item["phase"] = "cold" if index == 0 else "warm"
    results.append(item)

print(json.dumps({
    "model": "ResembleAI Chatterbox-Turbo",
    "reference": str(REF),
    "load_seconds": round(load_seconds, 4),
    "load_vram": load_vram,
    "cuda_device": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
    "results": results,
}, indent=2))

hold_seconds = float(os.environ.get("BENCHMARK_HOLD_SECONDS", "0"))
if hold_seconds > 0:
    time.sleep(hold_seconds)
