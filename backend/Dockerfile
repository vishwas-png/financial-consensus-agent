# Production image for the FastAPI backend (Render, or any Docker host).
# Build:  docker build -t fca-api backend
# Run:    docker run -p 8000:8000 --env-file backend/.env -e APP_ENV=production fca-api
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Run as a non-root user
RUN useradd --create-home --uid 10001 appuser

# Dependencies first, so code changes don't reinstall them
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# Writable folders for downloaded 10-Ks and the vector index. On a host with an ephemeral
# disk (Render free) they start empty after each restart and refill on demand (lazy ingestion).
RUN mkdir -p /app/data/filings /app/chroma_db && chown -R appuser:appuser /app/data /app/chroma_db

USER appuser

# Download the ~80 MB embedding model at BUILD time into the image (appuser's cache),
# so the first user request doesn't wait for it (Stage 4 DEVLOG: 9 minutes on a slow link).
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warm up'])"

ENV APP_ENV=production \
    PORT=8000
EXPOSE 8000

# Apply database migrations, then start the API. Hosts like Render set $PORT.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
