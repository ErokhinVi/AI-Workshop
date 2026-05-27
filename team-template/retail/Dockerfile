# Build context = корень репозитория команды.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "httpx>=0.27"

COPY retail/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
