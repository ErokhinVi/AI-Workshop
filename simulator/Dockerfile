# Build context = корень монорепо.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "httpx>=0.27" \
    "asyncpg>=0.29"

COPY simulator/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
