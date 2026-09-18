#!/usr/bin/env python3
"""Exercise candidate Home Assistant adapters read-only inside the Tools runtime."""

import asyncio
import importlib.util
import json
import os


async def main():
    path = os.getenv("HOME_AI_CANDIDATE_TOOLS", "/tmp/server-tools-app-candidate.py")
    spec = importlib.util.spec_from_file_location("candidate_home_tools", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cases = [
        ("inventory", module.home_find_device({"query": ""})),
        ("on", module.home_get_state({"state": "on"})),
        ("off", module.home_get_state({"state": "off"})),
        ("unavailable", module.home_get_state({"state": "unavailable"})),
        ("living_room", module.home_get_area_state({"area": "Living Room"})),
        ("colour_lights", module.home_find_device({"query": "lights"})),
        ("routines", module.home_list_routines({"kind": "all"})),
        ("history", module.home_get_activity({"entity_or_area": "Living Room", "hours": 1})),
    ]
    output = {}
    for name, awaitable in cases:
        value = await awaitable
        if name == "history":
            value = {**value, "changes": (value.get("changes") or [])[-5:]}
        output[name] = value
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
