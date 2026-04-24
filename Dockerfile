# ── Stage 1: Install dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Build tools needed for some Python packages (cffi, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Copy manifest first for layer caching
COPY pyproject.toml /app/

# Stub the 'app' package directory so setuptools can collect metadata & deps
# (the real source is copied in stage 2)
RUN mkdir -p /app/app && touch /app/app/__init__.py

# Install all production deps into an isolated prefix
RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy the actual application source (overrides the stub app/__init__.py)
COPY . /app

# Create non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8000

# Run DB migrations then start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
