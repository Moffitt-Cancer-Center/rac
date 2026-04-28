# Multi-stage Dockerfile for RAC Control Plane
# Stage 1: Frontend builder
# Stage 2: Backend builder
# Stage 3: Runtime

# ==============================================================================
# Stage 1: Frontend Builder
# ==============================================================================
FROM node:20-alpine AS fe-builder

WORKDIR /app/frontend

# Vite reads VITE_* env vars at build time and inlines them into the static
# bundle. Anything env-specific (tenant, client IDs, API scope) MUST be
# passed as a --build-arg and re-exported as ENV before `pnpm build` so it
# ends up in the compiled JS. Without these the SPA throws at load
# ("Missing required env vars") and the Entra interactive flow never starts.
ARG VITE_TENANT_ID
ARG VITE_FRONTEND_CLIENT_ID
# Use VITE_API_APP_ID (the API app reg's client ID) — the SPA builds the
# scope as api://<id>/submit, which is tenant-portable. VITE_API_SCOPE
# overrides if a non-app-id identifierUri is in use (rare).
ARG VITE_API_APP_ID
ARG VITE_API_SCOPE
ARG VITE_API_BASE_URL
ARG VITE_INSTITUTION_NAME
ARG VITE_BRAND_LOGO_URL
ENV VITE_TENANT_ID=$VITE_TENANT_ID \
    VITE_FRONTEND_CLIENT_ID=$VITE_FRONTEND_CLIENT_ID \
    VITE_API_APP_ID=$VITE_API_APP_ID \
    VITE_API_SCOPE=$VITE_API_SCOPE \
    VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_INSTITUTION_NAME=$VITE_INSTITUTION_NAME \
    VITE_BRAND_LOGO_URL=$VITE_BRAND_LOGO_URL

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

# `git` is needed at runtime by the detection-rules engine, which clones the
# submitted GitHub repo to a tmpdir before running rules over it. Without it,
# every submission logs FileNotFoundError: 'git' and falls through to "no
# detection ran" — which is silent on the user side.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user up front so all COPY layers below can use --chown=10001
# without having to recursively chown afterwards. The recursive chown over the
# .venv (~thousands of Python package files) would otherwise dominate every
# build at ~8 minutes — and this layer sits below COPY-of-source so the chown
# layer was never cache-hit. Using --chown on each COPY does the ownership
# work at copy time and lets BuildKit cache each layer independently on its
# content hash. The .venv layer is the largest by far, and now it only re-runs
# when uv.lock / pyproject.toml change.
RUN useradd -u 10001 -m -s /sbin/nologin appuser

# Copy virtual environment from builder (cached on .venv content hash)
COPY --from=be-builder --chown=10001:10001 /app/.venv /app/.venv

# Copy backend source (small; chowned at copy time)
COPY --chown=10001:10001 apps/control-plane/backend/src /app/src

# Copy alembic migration assets so `alembic upgrade head` can be run from
# inside the container via `az containerapp exec` against the deployed PG.
COPY --chown=10001:10001 apps/control-plane/backend/alembic.ini /app/alembic.ini
COPY --chown=10001:10001 apps/control-plane/backend/migrations /app/migrations

# Copy built frontend SPA from fe-builder
COPY --from=fe-builder --chown=10001:10001 /app/frontend/dist /app/src/rac_control_plane/static

# Set up Python path and activate venv
ENV PYTHONPATH=/app/src
ENV PATH="/app/.venv/bin:$PATH"

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
