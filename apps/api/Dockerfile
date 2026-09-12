# syntax=docker/dockerfile:1
#
# The API image. Build context is the repository root, because this is a uv
# workspace: `uv sync` resolves every member listed in the root pyproject, so
# the whole tree has to be present even though only one package is installed.

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
RUN uv sync --frozen --no-dev --no-editable --package agentforge-api

FROM python:3.13-slim

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run unprivileged: the API is internet-facing and holds cluster credentials.
USER 10001
EXPOSE 8000

CMD ["uvicorn", "agentforge_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
