"""QA-only Open WebUI transient-status compatibility probe."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


app = FastAPI()

STATUS_FRAMES = [
    {"choices": [{"index": 0, "delta": {
        "home_ai_status": {"phase": "tool_started", "label": "Reading Example News…"}
    }, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {
        "home_ai_status": {"phase": "tool_finished", "label": "Read Example News"}
    }, "finish_reason": None}]},
]


def sse_chunk(frame):
    return f"data: {json.dumps(frame, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def events():
    yield sse_chunk({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
    for frame in STATUS_FRAMES:
        yield sse_chunk(frame)
        await asyncio.sleep(1)
    yield sse_chunk({"choices": [{"index": 0, "delta": {"content": "Final probe answer."}, "finish_reason": None}]})
    yield sse_chunk({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(_: Request):
    return StreamingResponse(events(), media_type="text/event-stream")


def probe_models():
    return {"object": "list", "data": [{
        "id": "home-ai-probe", "object": "model", "owned_by": "home-ai-qa"
    }]}


@app.get("/v1/models")
async def models():
    return probe_models()


def decoded_probe_sse():
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={
            "model": "home-ai-probe", "stream": True,
            "messages": [{"role": "user", "content": "Run status probe"}],
        })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]


def test_probe_endpoint_orders_non_content_status_before_sole_final_content():
    sse_events = decoded_probe_sse()
    frames = [json.loads(event) for event in sse_events if event != "[DONE]"]
    assert [frame["choices"][0]["delta"] for frame in frames] == [
        {"role": "assistant"},
        STATUS_FRAMES[0]["choices"][0]["delta"],
        STATUS_FRAMES[1]["choices"][0]["delta"],
        {"content": "Final probe answer."},
        {},
    ]
    status = [f for f in frames if "home_ai_status" in f["choices"][0]["delta"]]
    assert [f["choices"][0]["delta"].get("content") for f in status] == [None, None]
    assert [f["choices"][0]["delta"].get("content") for f in frames if "content" in f["choices"][0]["delta"]] == ["Final probe answer."]
    assert frames[-1]["choices"][0]["finish_reason"] == "stop"
    assert sse_events.count("[DONE]") == 1


def test_probe_advertises_the_model_open_webui_can_select():
    assert probe_models()["data"] == [{"id": "home-ai-probe", "object": "model", "owned_by": "home-ai-qa"}]
