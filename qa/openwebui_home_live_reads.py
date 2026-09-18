#!/usr/bin/env python3
"""Read-only Open WebUI-network acceptance for Home Assistant conversation."""

import json
import os
import time
import urllib.request


base = os.environ["OPENAI_API_BASE_URL"].rstrip("/")
key = os.environ["OPENAI_API_KEY"]
headers = {
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "X-OpenWebUI-User-Id": "home-live-qa",
    "X-OpenWebUI-Chat-Id": "conversational-home-read-acceptance",
}
turns = [
    "What's on?",
    "What about the bedroom?",
    "What's unavailable?",
    "How long has that one been unavailable?",
    "Bedroom Lamp",
    "Can you check again?",
    "How many lights do I have?",
    "Which lights can change colour?",
    "What's the state of Light Fixture 1?",
    "Can that light be dimmed?",
    "When did Light Fixture 1 turn on?",
    "Why didn't Bedroom Lamp respond?",
    "What scenes do I have?",
]

for text in turns:
    payload = {"model": "home-ai", "messages": [{"role": "user", "content": text}], "stream": False}
    request = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(), headers=headers)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read())
    answer = body["choices"][0]["message"]["content"]
    print(json.dumps({"question": text, "answer": answer,
                      "latency_ms": round((time.perf_counter() - started) * 1000, 1)}))
