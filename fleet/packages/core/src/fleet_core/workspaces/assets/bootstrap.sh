#!/usr/bin/env bash
# Fleet workspace bootstrap.
#
# Runs as the devcontainer's postCreateCommand on every `devpod up`, so it must
# be idempotent and fast when the workspace already has its tools.
#
# Everything it installs goes under $PWD/.fleet, which lives on the workspace's
# persistent volume. DevPod deletes the pod on `devpod stop` and recreates it
# from the image, so anything written outside the volume (including /usr/local)
# would be lost.
#
# Node and the agent CLIs are installed by the control plane, never by the
# agent, and the agent's LLM credentials are a scoped gateway token rather than
# a model-provider key (SPEC §11).

set -u

FLEET_HOME="$PWD/.fleet"
TOOLS="$FLEET_HOME/tools"
NODE_VERSION="${FLEET_NODE_VERSION:-v24.21.0}"
OPENCODE_VERSION="${FLEET_OPENCODE_VERSION:-1.18.31}"
T3_VERSION="${FLEET_T3_VERSION:-0.0.40}"
MARKER="$TOOLS/.ready"
STATUS="$FLEET_HOME/bootstrap.status"

log() { printf '[fleet-bootstrap] %s\n' "$*"; }

mkdir -p "$TOOLS" "$FLEET_HOME"

if [ -f "$MARKER" ] && command -v "$TOOLS/bin/node" >/dev/null 2>&1; then
  log "tools already present at $TOOLS; nothing to do"
  ln -sf "$TOOLS/bin/node" /usr/local/bin/node 2>/dev/null || true
  ln -sf "$TOOLS/bin/npm" /usr/local/bin/npm 2>/dev/null || true
  echo "ok $(date -u +%FT%TZ) cached" > "$STATUS"
  exit 0
fi

echo "running $(date -u +%FT%TZ)" > "$STATUS"

# The agent runs git against a repository the bootstrap created. When the two
# run as different users git refuses with "detected dubious ownership", which
# surfaced as a task failing with NOT_A_REPO.
git config --global --add safe.directory '*' 2>/dev/null || true

log "installing prerequisites"
# python3/make/g++ are for node-gyp: T3 Code depends on node-pty, which builds a
# native addon. Without them `npm install -g t3` fails and T3 never starts.
MISSING=""
for tool in xz python3 make g++; do
  command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
done
if [ -n "$MISSING" ]; then
  (apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      xz-utils ca-certificates python3 make g++) >>"$FLEET_HOME/bootstrap.log" 2>&1 \
    || log "apt install failed (continuing)"
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) NODE_ARCH=x64 ;;
  aarch64|arm64) NODE_ARCH=arm64 ;;
  *) NODE_ARCH=x64 ;;
esac

if [ ! -x "$TOOLS/bin/node" ]; then
  log "installing node $NODE_VERSION ($NODE_ARCH)"
  tarball="node-${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
  if curl -fsSL -o "/tmp/$tarball" "https://nodejs.org/dist/${NODE_VERSION}/${tarball}" \
     && tar -xJf "/tmp/$tarball" -C "$TOOLS" --strip-components=1; then
    log "node installed: $("$TOOLS/bin/node" --version)"
  else
    log "FATAL: node download/extract failed"
    echo "fail node $(date -u +%FT%TZ)" > "$STATUS"
    exit 1
  fi
fi

export PATH="$TOOLS/bin:$PATH"

if [ ! -x "$TOOLS/bin/opencode" ]; then
  log "installing opencode $OPENCODE_VERSION"
  npm install -g --prefix "$TOOLS" --no-fund --no-audit \
    "opencode-ai@${OPENCODE_VERSION}" >>"$FLEET_HOME/bootstrap.log" 2>&1 \
    || log "opencode install failed (see .fleet/bootstrap.log)"
fi

if [ "${FLEET_T3_ENABLED:-true}" = "true" ] && [ ! -x "$TOOLS/bin/t3" ]; then
  log "installing t3 code $T3_VERSION"
  npm install -g --prefix "$TOOLS" --no-fund --no-audit \
    "t3@${T3_VERSION}" >>"$FLEET_HOME/bootstrap.log" 2>&1 \
    || log "t3 install failed (see .fleet/bootstrap.log)"
fi

# The agent's model access: the fleet gateway, with a project-scoped token.
# The aliases are declared so `opencode run -m fleet/<alias>` works for each one
# the operator allows (SPEC §12, §14).
if [ -n "${FLEET_LLM_BASE_URL:-}" ]; then
  log "writing opencode config for the fleet gateway"
  MODELS_JSON=""
  for alias in $(printf '%s' "${FLEET_LLM_MODELS:-local-coder}" | tr ',' ' '); do
    [ -n "$MODELS_JSON" ] && MODELS_JSON="$MODELS_JSON,
"
    MODELS_JSON="$MODELS_JSON        \"$alias\": { \"name\": \"$alias\" }"
  done
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
$MODELS_JSON
      }
    }
  }
}
JSON
fi

# Project sources. A private repository is cloned with the project's own
# credential, which the control plane put in the environment (SPEC §15).
if [ -n "${FLEET_REPO_URL:-}" ] && [ ! -d "$FLEET_HOME/repo/.git" ]; then
  log "cloning $FLEET_REPO_URL"
  url="$FLEET_REPO_URL"
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    url="$(printf '%s' "$url" | sed -E "s#^https://#https://x-access-token:${GITHUB_TOKEN}@#")"
  fi
  if git clone --branch "${FLEET_REPO_BRANCH:-main}" "$url" "$FLEET_HOME/repo" \
       >>"$FLEET_HOME/bootstrap.log" 2>&1; then
    log "repository cloned into .fleet/repo"
    git -C "$FLEET_HOME/repo" remote set-url origin "$FLEET_REPO_URL"
  else
    log "clone failed (see .fleet/bootstrap.log)"
  fi
fi

if [ ! -d "$FLEET_HOME/repo/.git" ]; then
  log "no usable repository; initialising an empty one"
  git init -q "$FLEET_HOME/repo" 2>/dev/null || true
  git -C "$FLEET_HOME/repo" -c user.email=fleet@localhost -c user.name=fleet \
    commit -q --allow-empty -m "fleet: empty workspace" 2>/dev/null || true
fi

ln -sf "$TOOLS/bin/node" /usr/local/bin/node 2>/dev/null || true
ln -sf "$TOOLS/bin/npm" /usr/local/bin/npm 2>/dev/null || true
ln -sf "$TOOLS/bin/opencode" /usr/local/bin/opencode 2>/dev/null || true
ln -sf "$TOOLS/bin/t3" /usr/local/bin/t3 2>/dev/null || true

touch "$MARKER"
echo "ok $(date -u +%FT%TZ)" > "$STATUS"
log "bootstrap complete"
