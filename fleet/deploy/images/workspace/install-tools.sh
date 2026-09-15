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

node --version
"$TOOLS/bin/opencode" --version
