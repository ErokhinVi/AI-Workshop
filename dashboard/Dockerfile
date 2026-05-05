# Live Dashboard для AI-воркшопа.
# Build context = корень репо raif-bank-sandbox.
# Внутри контейнера репо ожидается в /repo (монтируется снаружи).

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30"

COPY dashboard /app/dashboard

EXPOSE 9000
CMD ["python", "dashboard/server.py", "--repo", "/repo", "--port", "9000"]
