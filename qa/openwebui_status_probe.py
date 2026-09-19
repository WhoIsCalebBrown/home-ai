"""QA-only Open WebUI transient-status compatibility probe."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse


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


def probe_frames():
    return [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        *STATUS_FRAMES,
        {"choices": [{"index": 0, "delta": {"content": "Final probe answer."}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]


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


def decoded_probe_frames():
    return iter(probe_frames())


def content_text(frames):
    return "".join(frame["choices"][0]["delta"].get("content", "") for frame in frames)


async def emitted_events():
    return [event async for event in events()]


def test_probe_never_places_status_in_content():
    frames = list(decoded_probe_frames())
    status = [f for f in frames if "home_ai_status" in f["choices"][0]["delta"]]
    assert [f["choices"][0]["delta"].get("content") for f in status] == [None, None]
    assert content_text(frames) == "Final probe answer."


def test_probe_stream_ends_once_after_stop():
    stream = asyncio.run(emitted_events())
    assert stream[-2].startswith("data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}")
    assert stream.count("data: [DONE]\n\n") == 1


def test_probe_advertises_the_model_open_webui_can_select():
    assert probe_models()["data"] == [{"id": "home-ai-probe", "object": "model", "owned_by": "home-ai-qa"}]
