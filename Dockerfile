# ── Stage 1: Install dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps needed for building some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Copy only the dependency manifest first (layer cache)
COPY pyproject.toml /app/

# Install all production dependencies into a prefix
RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . /app

# Create non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8000

# Run DB migrations, then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
