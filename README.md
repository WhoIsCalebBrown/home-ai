# Home AI

Source for the two custom services used by the local Home AI assistant:

- `home-ai-assistant`: browser, voice, orchestration, conversation state, and LLM loop.
- `home-ai-tools`: typed, allowlisted server and media tools.

Runtime credentials and service configuration stay outside the images. The images are
published by GitHub Actions to GHCR and are tagged with the Git revision and release
version.

## Local checks

```sh
python3 -m py_compile assistant/voice-api-app.py tools/server-tools-app.py
```

## Image builds

```sh
docker build -f assistant/Dockerfile -t home-ai-assistant:dev .
docker build -f tools/Dockerfile -t home-ai-tools:dev .
```

