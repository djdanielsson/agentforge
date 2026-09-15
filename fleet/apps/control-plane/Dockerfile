# syntax=docker/dockerfile:1
#
# The control-plane image.
#
# Build context is the `fleet/` directory, because this is a uv workspace:
# `uv sync` resolves every member listed in the root pyproject, so the whole
# tree has to be present even though only one package is installed.
#
# DevPod is baked in rather than installed at runtime: it is the workspace
# provider, and a provider that has to download itself on first use turns a
# request into a network failure mode.

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY apps/ apps/

# --no-editable installs the workspace members as real packages into the venv,
# which is what makes the venv copyable into the runtime stage.
RUN uv sync --frozen --no-dev --no-editable --package fleet-api


FROM python:3.13-slim

ARG DEVPOD_VERSION=v0.6.15
ARG TARGETARCH=amd64

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/devpod \
      "https://github.com/loft-sh/devpod/releases/download/${DEVPOD_VERSION}/devpod-linux-${TARGETARCH}" \
 && chmod +x /usr/local/bin/devpod \
 && devpod version

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown -R app:app /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    FLEET_DATA_DIR=/data \
    FLEET_DEVPOD_HOME=/data/devpod \
    FLEET_KUBECONFIG=/data/kubeconfig

# Run unprivileged. The process holds cluster credentials and is reachable from
# every workspace, so it is the last thing that should run as root.
USER 10001
EXPOSE 8000

CMD ["uvicorn", "fleet_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
