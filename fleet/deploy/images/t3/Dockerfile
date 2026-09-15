# syntax=docker/dockerfile:1
#
# The shared T3 Code environment (docs/T3-INTEGRATION.md §A).
#
# This image is the whole of "one environment owns every project": T3 Code, the
# agent CLIs it drives (opencode), node and git, in one place. Projects are
# directories on a volume mounted at /projects, so the environment is the only
# thing that has to exist — not one server per project.
#
# It is built from the same devcontainer base as the DevPod workspaces on
# purpose: the agent user (`vscode`) and `setpriv` then exist, so a fleet-run
# task drops privileges the same way it does in a DevPod workspace (FINDINGS
# §9.13 — `opencode run` deadlocks as root in this image).
#
# The toolchain is installed to /usr/local rather than to a workspace volume:
# there is no per-project volume here to install into.

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

USER root
# COPY paths are relative to the *build context*, not to this Dockerfile. The
# context is the fleet/ directory (its uv workspace root).
COPY deploy/images/workspace/install-tools.sh /usr/local/bin/fleet-install-tools
COPY deploy/images/t3/entrypoint.sh /usr/local/bin/fleet-t3-entrypoint
RUN chmod +x /usr/local/bin/fleet-install-tools /usr/local/bin/fleet-t3-entrypoint \
 && FLEET_TOOLS=/usr/local /usr/local/bin/fleet-install-tools \
 && rm -f /usr/local/bin/fleet-install-tools \
 && mkdir -p /projects /state /root/.config/opencode /home/vscode/.config/opencode

# T3 Code's data directory: its own store (the project registry `t3 project add`
# writes) plus per-project state. On the projects volume, so it survives a
# restart of the environment.
ENV T3CODE_HOME=/state/t3code
ENV HOME=/root
EXPOSE 5733

ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /projects
ENTRYPOINT ["/usr/local/bin/fleet-t3-entrypoint"]
