"""Report image provenance gaps without changing Docker state.

This intentionally does not claim that a repository is compliant merely
because it has a registry prefix.  It identifies images whose owner/source
cannot be established from the live image reference and RepoDigests so they
can be reviewed without silently treating local/custom images as upstream.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("/tmp/home-ai-inventory-enriched.json"))
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    rows = []
    for name, item in sorted(inventory.get("containers", {}).items()):
        ref = str(item.get("image") or "")
        details = item.get("image_details") or {}
        digests = details.get("repo_digests") or []
        # A bare image name, a local sha256 ref, or a repository without a
        # registry is not automatically a violation; it is an ownership gap.
        owner_review = not digests
        rows.append({
            "container": name,
            "image": ref,
            "image_id": details.get("image_id"),
            "repo_digests": digests,
            "owner_review_required": owner_review,
            "status": item.get("status"),
        })
    result = {
        "container_count": len(rows),
        "owner_review_count": sum(row["owner_review_required"] for row in rows),
        "owner_review": [row for row in rows if row["owner_review_required"]],
        "all_images": rows,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
