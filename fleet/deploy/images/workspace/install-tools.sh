#!/usr/bin/env bash
# Install the agent toolchain into $FLEET_TOOLS. Runs at image build time.
set -euo pipefail

TOOLS="${FLEET_TOOLS:-/usr/local}"
NODE_VERSION="${FLEET_NODE_VERSION:-v24.21.0}"
OPENCODE_VERSION="${FLEET_OPENCODE_VERSION:-1.18.31}"
T3_VERSION="${FLEET_T3_VERSION:-0.0.40}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# python3/make/g++ are for node-gyp: T3 Code depends on node-pty, which builds a
# native addon. Without them the npm install fails and the image build fails.
apt-get install -y -qq --no-install-recommends \
  xz-utils ca-certificates curl git jq python3 make g++
rm -rf /var/lib/apt/lists/*

case "$(uname -m)" in
  x86_64) NODE_ARCH=x64 ;;
  aarch64 | arm64) NODE_ARCH=arm64 ;;
  *) NODE_ARCH=x64 ;;
esac

tarball="node-${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
curl -fsSL -o "/tmp/${tarball}" "https://nodejs.org/dist/${NODE_VERSION}/${tarball}"
tar -xJf "/tmp/${tarball}" -C "$TOOLS" --strip-components=1
rm -f "/tmp/${tarball}"

export PATH="$TOOLS/bin:$PATH"
npm install -g --prefix "$TOOLS" --no-fund --no-audit \
  "opencode-ai@${OPENCODE_VERSION}" "t3@${T3_VERSION}"

# Upstream T3 v0.0.40 crashes while formatting the error it should show:
# `openCodeRuntimeErrorDetail` calls `cause.message.trim()` without checking
# that `message` is a string, so an opencode failure whose Error carries no
# message (e.g. the 401 from a misconfigured gateway token) surfaces as
# `TypeError: Cannot read properties of undefined (reading 'trim')` instead
# of the real cause. Patch the installed bundle defensively; re-check on bump.
#
# Same story one screen down in the same file: the default model for the
# opencode driver is hard-coded to `openai/gpt-5`, which our workspaces never
# configure (the only provider is `fleet`), so every new thread 500s with
# `ProviderModelNotFoundError` until the user picks another model.
# `fleet/local-coder` is the default because it is allowed under every LLM
# policy, including `localOnly`; projects that allow more can switch to
# `fleet/smart` in the picker. Both strings occur exactly twice in the
# bundle (the turn default and the title-generation default); re-check on bump.
if [ -f "$TOOLS/lib/node_modules/t3/dist/bin.mjs" ]; then
  sed -i 's/cause\.message\.trim()\.length/((typeof cause.message === "string" ? cause.message : "")).trim().length/; s/return cause\.message\.trim()/return (typeof cause.message === "string" ? cause.message : "").trim()/' \
    "$TOOLS/lib/node_modules/t3/dist/bin.mjs" || true
  sed -i 's#"openai/gpt-5"#"fleet/local-coder"#g' \
    "$TOOLS/lib/node_modules/t3/dist/bin.mjs" || true
fi

node --version
"$TOOLS/bin/opencode" --version
