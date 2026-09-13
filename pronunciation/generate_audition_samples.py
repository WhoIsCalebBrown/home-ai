#!/usr/bin/env python3
"""Generate inactive before/after Chatterbox pronunciation audition samples."""
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


MANIFEST = Path(__file__).with_name("pronunciation-candidates.yaml")
API = "http://127.0.0.1:8088/v1/audio/speech"
OUT = Path("/chatterbox/pronunciation-audition")


def wav(text: str, path: Path) -> None:
    body = json.dumps({"text": text, "response_format": "wav"}).encode()
    request = Request(API, data=body, headers={"Content-Type": "application/json"})
    path.write_bytes(urlopen(request, timeout=45).read())


def replace_once(text: str, term: str, spoken: str) -> str:
    return re.sub(re.escape(term), spoken, text, count=1, flags=re.I)


data = yaml.safe_load(MANIFEST.read_text())
lookup = {}
for group in ("high-confidence-automatic-candidates", "needs-listening-review", "probably-fine-without-override"):
    for term, item in (data.get(group) or {}).items():
        lookup[term] = item["spoken"]

OUT.mkdir(parents=True, exist_ok=True)
for term, proposed in [(x["term"], x["sentence"]) for x in data["audition"][:30]]:
    spoken = lookup[term]
    original = proposed
    if spoken != term and spoken in proposed:
        original = proposed.replace(spoken, term, 1)
    override = replace_once(original, term, spoken)
    safe = re.sub(r"[^A-Za-z0-9]+", "-", term).strip("-").lower()
    (OUT / f"{safe}.txt").write_text(json.dumps({"term": term, "original": original, "override": override}, ensure_ascii=False, indent=2))
    wav(original, OUT / f"{safe}-without.wav")
    wav(override, OUT / f"{safe}-with.wav")
    time.sleep(0.1)
    print(term, "|", original, "|", override, flush=True)
