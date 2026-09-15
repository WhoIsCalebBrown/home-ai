"""Read-only runtime versus Unraid-template drift report."""

import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ET


WATCH = {
    "Home-AI-Assistant": ["TOOLS_URL", "OLLAMA_URL", "WHISPER_URI", "LLM_MODEL", "LLM_CONTEXT", "TTS_PROVIDER"],
    "Home-AI-Tools": ["CLIDEBRID_BASE", "STANDARD_MEDIA_BACKEND_READY", "STANDARD_MEDIA_WRITES_ENABLED", "STANDARD_MOVIE_WRITES_ENABLED", "STANDARD_SEASON_WRITES_ENABLED"],
}


def ssh(host: str, command: str) -> str:
    return subprocess.run(["ssh", host, command], text=True, capture_output=True, check=True).stdout


def runtime_env(host: str, container: str) -> dict:
    text = ssh(host, f"docker inspect --format '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {container}")
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in WATCH[container]:
            values[key] = value
    return values


def runtime_image(host: str, container: str) -> str:
    return ssh(host, f"docker inspect --format '{{{{.Config.Image}}}}' {container}").strip()


def template_env(host: str, container: str) -> tuple[str | None, dict, bool]:
    xml_path = f"/boot/config/plugins/dockerMan/templates-user/{container}.xml"
    text = ssh(host, f"cat {xml_path}")
    root = ET.fromstring(text)
    image = root.findtext("Repository")
    values = {}
    for item in root.findall("Config"):
        target = item.attrib.get("Target")
        if target in WATCH[container]:
            values[target] = (item.text or "").strip()
    alias = "server-tools" in text if container == "Home-AI-Tools" else True
    return image, values, alias


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="unraid")
    args = parser.parse_args()
    report = {"host": args.host, "services": {}, "drift": []}
    for container in WATCH:
        image, template, alias = template_env(args.host, container)
        actual = runtime_env(args.host, container)
        actual_image = runtime_image(args.host, container)
        service = {"runtime_env": actual, "template_env": template, "runtime_image": actual_image, "template_image": image, "network_alias_present": alias}
        report["services"][container] = service
        if actual_image != image:
            report["drift"].append(f"{container}: image runtime/template differ")
        if container == "Home-AI-Tools" and not alias:
            report["drift"].append(f"{container}: missing persistent server-tools alias")
        for key in sorted(set(actual) | set(template)):
            if actual.get(key) != template.get(key):
                report["drift"].append(f"{container}: {key} runtime/template differ")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["drift"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
