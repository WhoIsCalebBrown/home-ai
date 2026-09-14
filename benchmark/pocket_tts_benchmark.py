"""Offline Pocket-vs-Kokoro benchmark; does not touch production configuration."""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import httpx
import numpy as np
import scipy.io.wavfile
import torch
from pocket_tts import TTSModel

TEXTS = [
    "Hey, I checked your server.",
    "It's currently 16.5 degrees Celsius in Welland, Ontario.",
    "Plex has 1,575 movies available across the current libraries.",
    "Lidarr, Sonarr, Radarr, Frigate, Qwen, Ollama, and qBittorrent are all available.",
    "Your RTX 3070 is using approximately 7.1 gigabytes of VRAM.",
]
OUT = Path(os.environ.get("POCKET_BENCH_OUT", "/tmp/pocket-tts-benchmark"))
OUT.mkdir(parents=True, exist_ok=True)


def stats(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "p90": float(np.percentile(values, 90)), "p95": float(np.percentile(values, 95))}


def main() -> None:
    reference = Path(os.environ.get("POCKET_REFERENCE", "/home/caleb/Documents/ref.mp3"))
    model_start = time.perf_counter()
    model = TTSModel.load_model(language="english")
    model_load = time.perf_counter() - model_start
    conditioning_start = time.perf_counter()
    state = model.get_state_for_audio_prompt(reference, truncate=True)
    conditioning = time.perf_counter() - conditioning_start
    pocket_rows = []
    for index, text in enumerate(TEXTS):
        firsts, totals, durations = [], [], []
        for run in range(10):
            started = time.perf_counter()
            chunks = []
            first = None
            for chunk in model.generate_audio_stream(state, text):
                if first is None:
                    first = time.perf_counter() - started
                chunks.append(chunk.detach().cpu())
            elapsed = time.perf_counter() - started
            audio = torch.cat(chunks).numpy()
            duration = audio.shape[-1] / model.config.mimi.sample_rate
            firsts.append(first or elapsed)
            totals.append(elapsed)
            durations.append(duration)
            if run == 0:
                scipy.io.wavfile.write(OUT / f"pocket-{index}.wav", model.config.mimi.sample_rate, audio)
        pocket_rows.append({"text": text, "first_chunk": stats(firsts), "total": stats(totals), "audio_seconds": statistics.median(durations), "rtf": statistics.median(t / d for t, d in zip(totals, durations))})

    kokoro_rows = []
    for index, text in enumerate(TEXTS):
        values = []
        for run in range(10):
            started = time.perf_counter()
            response = httpx.post("http://192.168.40.44:10400/v1/audio/speech", json={"model": "kokoro", "input": text, "voice": "am_puck", "response_format": "wav", "speed": 1.18}, timeout=30)
            response.raise_for_status()
            elapsed = time.perf_counter() - started
            values.append(elapsed)
            if run == 0:
                (OUT / f"kokoro-{index}.wav").write_bytes(response.content)
        kokoro_rows.append({"text": text, "warm_request": stats(values)})
    (OUT / "results.json").write_text(json.dumps({"reference": str(reference), "model_load": model_load, "conditioning": conditioning, "pocket": pocket_rows, "kokoro": kokoro_rows}, indent=2))
    print(json.dumps({"model_load": model_load, "conditioning": conditioning, "pocket": pocket_rows, "kokoro": kokoro_rows}, indent=2))


if __name__ == "__main__":
    main()
