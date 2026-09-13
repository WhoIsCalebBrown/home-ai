FROM python:3.12-slim

WORKDIR /app
COPY tools/server-tools-app.py ./server-tools-app.py
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn[standard]==0.34.0 httpx==0.28.1 pydantic==2.10.6

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)"
CMD ["uvicorn", "server-tools-app:app", "--host", "0.0.0.0", "--port", "8090"]

