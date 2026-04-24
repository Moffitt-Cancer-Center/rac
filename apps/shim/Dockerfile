# Stage 1: builder — install dependencies into a venv
FROM python:3.12-slim AS builder

WORKDIR /build

# Install uv
RUN pip install --no-cache-dir uv

# Copy workspace-level lock file and root pyproject so uv can resolve the workspace
COPY pyproject.toml uv.lock ./
COPY apps/shim/pyproject.toml apps/shim/
COPY apps/shim/src/ apps/shim/src/

# Sync only production deps (no dev group) into /build/.venv
RUN uv sync --frozen --no-dev --project apps/shim


# Stage 2: runtime — minimal image with just the venv and source
FROM python:3.12-slim AS runtime

WORKDIR /app

# Non-root user
RUN addgroup --system shim && adduser --system --ingroup shim shim

# Copy venv from builder
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/apps/shim/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/_shim/health')" || exit 1

USER shim

CMD ["gunicorn", "rac_shim.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8080"]
