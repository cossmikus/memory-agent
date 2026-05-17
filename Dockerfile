FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi==0.115.6" \
        "uvicorn[standard]==0.34.0" \
        "pydantic==2.10.4" \
        "pydantic-settings==2.7.0" \
        "aiosqlite==0.20.0" \
        "sqlite-vec==0.1.6" \
        "openai==1.59.7" \
        "tiktoken==0.8.0" \
        "numpy==2.2.1" \
        "structlog==24.4.0" \
        "python-multipart==0.0.20" \
        "httpx==0.28.1"

COPY src/ ./src/

RUN mkdir -p /data
VOLUME ["/data"]

ENV PYTHONPATH=/app/src \
    DB_PATH=/data/memory.db \
    HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uvicorn", "memory_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
