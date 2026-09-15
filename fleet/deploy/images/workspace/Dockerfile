# syntax=docker/dockerfile:1
#
# The image the native Kubernetes provider runs as a workspace.
#
# DevPod workspaces use a stock devcontainer image and install their tooling
# through devcontainer lifecycle hooks (packages/core/.../assets/bootstrap.sh).
# The native provider has no lifecycle hooks, so it needs an image that already
# contains node, opencode and T3 Code.
#
# The LLM configuration is per project, so it is written at container start from
# the pod's environment rather than baked in.

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

USER root
COPY install-tools.sh /usr/local/bin/fleet-install-tools
COPY entrypoint.sh /usr/local/bin/fleet-workspace-entrypoint
RUN chmod +x /usr/local/bin/fleet-install-tools /usr/local/bin/fleet-workspace-entrypoint \
 && FLEET_TOOLS=/usr/local /usr/local/bin/fleet-install-tools \
 && rm -f /usr/local/bin/fleet-install-tools

USER vscode
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/fleet-workspace-entrypoint"]
