"""Install the private Home-AI auth guard into the mounted cli_debrid build.

The running image already uses a small startup patch for local routing. This
guard is deliberately fail-closed and only protects the two request-ingress
routes; read-only status pages remain available for health checks.
"""

from pathlib import Path


TARGET = Path("/app/routes/webhook_routes.py")
TOKEN_PATH = "/run/secrets/home_ai_bridge_token"
MARKER = "# HOME_AI_BRIDGE_AUTH_GUARD"


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    if MARKER in source:
        if "import secrets\n" not in source:
            lines = source.splitlines(keepends=True)
            insert_at = next((i + 1 for i, line in enumerate(lines)
                              if line.startswith("import os") or line.startswith("import logging")), 0)
            lines.insert(insert_at, "import secrets\n")
            source = "".join(lines)
            TARGET.write_text(source, encoding="utf-8")
        return
    if "def webhook():\n" not in source or "def agregarr_create_request():\n" not in source:
        raise SystemExit("bridge auth refused: expected request routes were not found")

    helper = f'''\n{MARKER}\ndef _home_ai_bridge_authorized():\n    try:\n        expected = Path({TOKEN_PATH!r}).read_text(encoding="utf-8").strip()\n    except OSError:\n        return False\n    supplied = request.headers.get("X-Home-AI-Bridge-Token", "")\n    return bool(expected) and secrets.compare_digest(supplied, expected)\n'''
    if "import secrets\n" not in source:
        lines = source.splitlines(keepends=True)
        insert_at = next((i + 1 for i, line in enumerate(lines)
                          if line.startswith("import os") or line.startswith("import logging")), 0)
        lines.insert(insert_at, "import secrets\n")
        source = "".join(lines)
    source = source.replace("webhook_bp = Blueprint('webhook', __name__)\n", "webhook_bp = Blueprint('webhook', __name__)\n" + helper, 1)
    source = source.replace("def webhook():\n    data = request.json\n", "def webhook():\n    if not _home_ai_bridge_authorized():\n        return jsonify({\"error\": \"unauthorized\"}), 401\n    data = request.json\n", 1)
    source = source.replace("def agregarr_create_request():\n", "def agregarr_create_request():\n", 1)
    request_marker = "    \"\"\"\n    try:\n        data = request.json\n"
    if request_marker not in source:
        raise SystemExit("bridge auth refused: request handler body marker changed")
    source = source.replace(request_marker, "    \"\"\"\n    if not _home_ai_bridge_authorized():\n        return jsonify({\"error\": \"unauthorized\"}), 401\n    try:\n        data = request.json\n", 1)
    TARGET.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
