import tempfile
import time
import importlib.util

from nemo_text_processing.text_normalization.normalize import Normalizer

spec = importlib.util.spec_from_file_location("voice_api_app", "/app/voice-api-app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def main() -> None:
    with tempfile.TemporaryDirectory() as cache:
        started = time.perf_counter()
        app.speech_normalizer = Normalizer(
            input_case="cased",
            lang="en",
            cache_dir=cache,
            overwrite_cache=False,
            post_process=True,
        )
        init_seconds = time.perf_counter() - started
        app.pronunciation_entries = app.load_pronunciation_lexicon()
        cases = {
            "You have 1,575 movies and 69 GB free.": "one thousand five hundred and seventy five movies and sixty nine gigabytes free.",
            "There are 346 TV shows, 13 TB remaining, and 85% used.": "three hundred and forty six TV shows, thirteen terabytes remaining, and eighty five percent used.",
            "It is 22°C and the download is 3.5 GB.": "twenty two degrees Celsius and the download is three point five gigabytes.",
            "September 13, 2026": "september thirteenth, twenty twenty six",
            "Lidarr and qBittorrent are healthy.": "lid arr and Q Bittorrent are healthy.",
        }
        for original, expected_fragment in cases.items():
            _, normalized, adjusted = app.normalize_for_speech(original)
            assert expected_fragment in adjusted, (original, normalized, adjusted)
            assert original != adjusted
        print(f"normalization tests passed; init_seconds={init_seconds:.3f}")


if __name__ == "__main__":
    main()
