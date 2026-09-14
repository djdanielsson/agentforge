# The OpenHands image, plus what its in-pod runtime needs.
#
# AgentForge runs the agent *inside* the workspace pod (`RUNTIME=local`): the pod
# is already the sandbox, and there is no Docker socket in it by design. That
# runtime drives the agent through tmux and constructs a browser during startup,
# and the stock image ships neither:
#
#   ValueError: tmux is not properly installed or available on the path.
#
# `enable_browser` defaults to true, so omitting the browser would only move the
# failure later.
FROM ghcr.io/all-hands-ai/openhands:latest

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends tmux \
 && rm -rf /var/lib/apt/lists/*

RUN python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
