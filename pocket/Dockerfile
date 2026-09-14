FROM python:3.12-slim

WORKDIR /app
COPY pocket/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY pocket/server.py ./server.py

EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/health').read()"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8095"]
