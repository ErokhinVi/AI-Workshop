# Build context = корень монорепы (см. docker-compose.yml).
# Копируем contracts/ и retail/ относительно корня.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic[email]>=2.7" \
    "jinja2>=3.1" \
    "httpx>=0.27" \
    "python-multipart>=0.0.9" \
    "asyncpg>=0.29"

COPY contracts /app/contracts
COPY cases/_seed /app/cases/_seed
COPY retail/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
