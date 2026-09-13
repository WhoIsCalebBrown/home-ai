import importlib.util
import time

spec = importlib.util.spec_from_file_location("voice_api_app", "/app/voice-api-app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
from nemo_text_processing.text_normalization.normalize import Normalizer

started = time.perf_counter()
app.speech_normalizer = Normalizer(
    input_case="cased", lang="en", cache_dir=str(app.NEMO_CACHE_DIR),
    overwrite_cache=False, post_process=True,
)
app.pronunciation_entries = app.load_pronunciation_lexicon()
print("init_seconds", round(time.perf_counter() - started, 6))
samples = [
    "You have 1,575 movies and 69 GB free.",
    "There are 346 TV shows, 13 TB remaining, and 85% used.",
    "It is 22°C and the download is 3.5 GB.",
    "September 13, 2026 at 3:45 PM.",
    "Lidarr and qBittorrent are healthy.",
]
long = " ".join(["The server is healthy and Lidarr has no active downloads."] * 12)
for label, text in [("short", samples[0]), ("units", samples[1]), ("temperature", samples[2]), ("date", samples[3]), ("lexicon", samples[4]), ("100_words", long)]:
    timings = []
    output = None
    for _ in range(5):
        started = time.perf_counter()
        _, _, output = app.normalize_for_speech(text)
        timings.append(time.perf_counter() - started)
    print(label, "warm_seconds", [round(x, 6) for x in timings], "output", output)
