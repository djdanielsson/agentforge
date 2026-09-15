#!/usr/bin/env bash
# Workspace entrypoint for the native Kubernetes provider.
#
# Prepares the project's checkout and the agent's gateway configuration from the
# environment the control plane injected, then idles so the provider can exec
# into the container.
set -u

FLEET_HOME="${FLEET_HOME:-/workspace/.fleet}"
mkdir -p "$FLEET_HOME"

if [ -n "${FLEET_LLM_BASE_URL:-}" ]; then
  cat > "$FLEET_HOME/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "model": "fleet/${FLEET_LLM_MODEL:-local-coder}",
  "provider": {
    "fleet": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Fleet Gateway",
      "options": {
        "baseURL": "${FLEET_LLM_BASE_URL}",
        "apiKey": "{env:FLEET_LLM_TOKEN}",
        "headers": {
          "X-Fleet-Agent": "{env:FLEET_AGENT}",
          "X-Fleet-Task": "{env:FLEET_TASK_ID}"
        }
      },
      "models": {
        "${FLEET_LLM_MODEL:-local-coder}": { "name": "${FLEET_LLM_MODEL:-local-coder}" }
      }
    }
  }
}
JSON
fi

if [ -n "${FLEET_REPO_URL:-}" ] && [ ! -d "$FLEET_HOME/repo/.git" ]; then
  url="$FLEET_REPO_URL"
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    url="$(printf '%s' "$url" | sed -E "s#^https://#https://x-access-token:${GITHUB_TOKEN}@#")"
  fi
  git clone --branch "${FLEET_REPO_BRANCH:-main}" "$url" "$FLEET_HOME/repo" || true
  git -C "$FLEET_HOME/repo" remote set-url origin "$FLEET_REPO_URL" 2>/dev/null || true
fi
if [ ! -d "$FLEET_HOME/repo/.git" ]; then
  git init -q "$FLEET_HOME/repo" 2>/dev/null || true
  git -C "$FLEET_HOME/repo" -c user.email=fleet@localhost -c user.name=fleet \
    commit -q --allow-empty -m "fleet: empty workspace" 2>/dev/null || true
fi

echo "fleet workspace ready: ${FLEET_PROJECT:-?} / ${FLEET_WORKSPACE:-?}"
exec sleep infinity
