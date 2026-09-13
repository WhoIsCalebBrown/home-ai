FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY assistant/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY assistant/voice-api-app.py ./voice-api-app.py
COPY assistant/voice-api-index.html ./voice-api-index.html
COPY assistant/tts-comparison.html ./tts-comparison.html

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health').read()"
CMD ["uvicorn", "voice-api-app:app", "--host", "0.0.0.0", "--port", "8088"]
