"""Read-only contract validator.

It validates the non-secret, machine-readable contract and can optionally
compare a sanitized docker-inspect JSON export.  It never repairs runtime
state and never calls a write-capable application endpoint.
"""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_contract(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"contract_version", "network", "services", "edges", "libraries", "confirmation", "registry"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"contract missing fields: {missing}")
    return data


def validate_invariants(contract: dict) -> list[str]:
    errors = []
    libraries = contract["libraries"]
    if set(libraries["standard"]) & set(libraries["permanent"]):
        errors.append("standard/permanent library names overlap")
    if any(path.startswith("/data/media/") for path in libraries["standard"].values()):
        errors.append("standard library points into permanent media storage")
    tools = contract["services"]["tools"]
    tool_edge = next((e for e in contract["edges"] if e["from"] == "assistant" and e["to"] == "tools"), None)
    if not tool_edge or tool_edge["endpoint"] != f"http://{tools['internal_alias']}:{tools['port']}":
        errors.append("assistant/tools endpoint is not the declared stable alias")
    if contract["confirmation"].get("ttl_seconds") != 120:
        errors.append("confirmation TTL drift")
    return errors


def compare_sanitized_runtime(contract: dict, runtime: dict) -> list[str]:
    """Compare only fields intentionally supplied by a sanitized inspect export."""
    errors = []
    containers = runtime.get("containers", {})
    for logical, expected in contract["services"].items():
        actual = containers.get(expected.get("container"))
        if not actual:
            continue
        if expected.get("image") and actual.get("image") != expected["image"]:
            errors.append(f"{logical}: image drift ({actual.get('image')} != {expected['image']})")
        if actual.get("network") and contract["network"] not in actual["network"]:
            errors.append(f"{logical}: missing network {contract['network']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=ROOT / "config/home-ai-system-contract.json")
    parser.add_argument("--runtime-json", type=Path)
    args = parser.parse_args()
    contract = load_contract(args.contract)
    errors = validate_invariants(contract)
    if args.runtime_json:
        errors.extend(compare_sanitized_runtime(contract, json.loads(args.runtime_json.read_text(encoding="utf-8"))))
    print(json.dumps({"ok": not errors, "contract_version": contract["contract_version"], "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
