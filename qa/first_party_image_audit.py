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
        labels = item.get("container_labels") or {}
        if digests:
            provenance_basis = "repository_digest"
        elif labels.get("com.docker.compose.project.config_files"):
            provenance_basis = "compose_managed_local_or_private_image"
        elif name == "Faster-Whisper":
            provenance_basis = "local_tag_with_upstream_project_declared"
        else:
            provenance_basis = "unresolved_local_image"
        # A local/Compose image is not automatically a policy violation. The
        # strict third-party image policy needs separate review for a
        # third-party application that is locally tagged or rebuilt.
        owner_review = provenance_basis == "unresolved_local_image"
        third_party_policy_review = name == "Faster-Whisper" and not digests
        rows.append({
            "container": name,
            "image": ref,
            "image_id": details.get("image_id"),
            "repo_digests": digests,
            "provenance_basis": provenance_basis,
            "owner_review_required": owner_review,
            "third_party_policy_review_required": third_party_policy_review,
            "status": item.get("status"),
        })
    result = {
        "container_count": len(rows),
        "owner_review_count": sum(row["owner_review_required"] for row in rows),
        "third_party_policy_review_count": sum(row["third_party_policy_review_required"] for row in rows),
        "owner_review": [row for row in rows if row["owner_review_required"]],
        "third_party_policy_review": [row for row in rows if row["third_party_policy_review_required"]],
        "all_images": rows,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
