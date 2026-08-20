# ── Stage 1: Install dependencies only ────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Build tools needed for cffi / cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*

# Copy only the manifest (layer cache: deps rebuild only when pyproject.toml changes)
COPY pyproject.toml /app/

# Install ONLY the declared dependencies — NOT the package itself.
# This avoids a stub 'app' package landing in site-packages and shadowing
# the real source code at /app/app/ at runtime.
RUN python3 -c "\
import tomllib, subprocess, sys; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--prefix=/install'] + deps)"

# ── Stage 2: Runtime image (API — lean, no browser) ────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Ensure Python can find the 'app' package at /app/app/
# (installed binaries like 'alembic' don't get CWD in sys.path automatically)
ENV PYTHONPATH=/app

# Pull installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy full application source (alembic/, app/, scripts/, etc.)
COPY . /app

# Run as non-root
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8000

# Run migrations then start the API server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]


# ── Stage 3: Worker runtime (adds Chromium for the Playwright submitter) ───────
#
# Build with:   docker build --target worker -t applyforge-worker .
# The API image stays lean; only the worker container carries Chromium.
FROM runtime AS worker

USER root

# Playwright's own installer pulls Chromium into /ms-playwright and
# reports the OS packages it still needs. `install-deps` is a thin
# wrapper that apt-installs them (fonts, libnss, libatk, etc.).
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Playwright caches browsers under /ms-playwright by default. Make sure
# the non-root worker user can read them.
RUN chown -R appuser:appgroup /ms-playwright || true

USER appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
