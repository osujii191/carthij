# Cloud Run production container for Carthij FastAPI backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    REPORT_STORAGE_BACKEND=firestore

WORKDIR /app

# Install uv for fast, deterministic dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project metadata, README (referenced by hatchling), and source before installing
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache ".[gcp]"

# Run as non-root user for Cloud Run security best practices
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
