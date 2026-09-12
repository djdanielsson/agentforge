# syntax=docker/dockerfile:1
#
# The orchestrator image. Same workspace caveat as the API: the build context
# must be the repository root so uv can resolve every member.

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

RUN uv sync --frozen --no-dev --no-editable --package agentforge-orchestrator

FROM python:3.13-slim

# kubectl is not needed: the Kubernetes provider talks to the API directly.
# git is needed because the git manager shells out inside workspace pods, and
# the orchestrator is the process that drives it.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001

CMD ["python", "-m", "agentforge_orchestrator.main"]
