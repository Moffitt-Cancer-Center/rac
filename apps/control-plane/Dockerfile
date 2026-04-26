# Multi-stage Dockerfile for RAC Control Plane
# Stage 1: Frontend builder
# Stage 2: Backend builder
# Stage 3: Runtime

# ==============================================================================
# Stage 1: Frontend Builder
# ==============================================================================
FROM node:20-alpine AS fe-builder

WORKDIR /app/frontend

# Copy package manifests
COPY apps/control-plane/frontend/package.json apps/control-plane/frontend/pnpm-lock.yaml ./

# Install dependencies
RUN npm install -g pnpm && \
    pnpm install --frozen-lockfile

# Copy frontend source
COPY apps/control-plane/frontend/ .

# Build SPA (outputs to dist/)
RUN pnpm build

# ==============================================================================
# Stage 2: Backend Builder
# ==============================================================================
FROM python:3.12-slim AS be-builder

WORKDIR /app

# Install curl and uv
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/* && \
    curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files. uv treats this repo as a workspace whose members
# include both apps/control-plane/backend and apps/shim, so both
# pyproject.toml files must be present on disk for `uv sync` to plan
# successfully against the lockfile — even though we only need the
# control-plane package itself.
COPY pyproject.toml uv.lock ./
COPY apps/control-plane/backend/pyproject.toml apps/control-plane/backend/
COPY apps/shim/pyproject.toml apps/shim/

# Install only the control-plane workspace member's runtime deps.
RUN uv sync --frozen --no-dev --package rac-control-plane

# ==============================================================================
# Stage 3: Runtime
# ==============================================================================
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=be-builder /app/.venv /app/.venv

# Copy backend source
COPY apps/control-plane/backend/src /app/src

# Copy alembic migration assets so `alembic upgrade head` can be run from
# inside the container via `az containerapp exec` against the deployed PG.
COPY apps/control-plane/backend/alembic.ini /app/alembic.ini
COPY apps/control-plane/backend/migrations /app/migrations

# Copy built frontend SPA from fe-builder
COPY --from=fe-builder /app/frontend/dist /app/src/rac_control_plane/static

# Set up Python path and activate venv
ENV PYTHONPATH=/app/src
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user
RUN useradd -u 10001 -m -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Expose port
EXPOSE 8080

# Start application
CMD ["gunicorn", "rac_control_plane.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8080", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
