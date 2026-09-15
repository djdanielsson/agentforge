#!/usr/bin/env bash
# Entrypoint for the shared T3 Code environment (docs/T3-INTEGRATION.md §A).
#
# Three jobs, in order: make the volume usable, write the opencode configuration
# the agent CLIs in this image read (the fleet gateway and the fleet MCP server),
# then hand the pod to `t3 serve` in the foreground.
#
# The configuration is written at container start rather than baked into the
# image, because the MCP URL and the tokens are properties of the deployment.
# Nothing here writes a token *value* to disk: the files reference
# `{env:FLEET_API_TOKEN}`, which opencode resolves from the process environment.
set -u

PROJECTS="${FLEET_T3_PROJECTS_DIR:-/projects}"
T3_HOME="${T3CODE_HOME:-/state/t3code}"
PORT="${FLEET_T3_PORT:-5733}"
AGENT_USER="${FLEET_WORKSPACE_AGENT_USER:-vscode}"

mkdir -p "$PROJECTS" "$T3_HOME"
# The checkouts in here are created and owned by the control plane, which drops
# them to the agent user. The two directories the environment itself writes have
# to be writable by the process running `t3 serve` (root).
mkdir -p "$T3_HOME"

write_opencode_config() {
  local target="$1"
  local mcp_url="${FLEET_MCP_URL:-}"
  [ -n "$mcp_url" ] || return 0
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "mcp": {
    "fleet": {
      "type": "remote",
      "url": "${mcp_url}",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:FLEET_API_TOKEN}" }
    }
  }
}
JSON
}

# The environment's default config: the fleet tools, wherever a session is
# opened. A checkout additionally carries its own opencode.json (with the fleet
# gateway provider, which needs a project-scoped token), written by the control
# plane when the project is created.
write_opencode_config /root/.config/opencode/opencode.json
write_opencode_config "/home/${AGENT_USER}/.config/opencode/opencode.json"

# `t3 project add` writes the registry into $T3CODE_HOME; the CLI and the server
# have to agree on it, so it is exported as well as passed on the command line.
export T3CODE_HOME="$T3_HOME"
export PATH="/usr/local/bin:$PATH"

echo "fleet T3 environment starting: projects=$PROJECTS home=$T3_HOME mcp=${FLEET_MCP_URL:-(unset)}"
# No project is bootstrapped from the working directory: the control plane
# registers each project explicitly with `t3 project add` when it creates the
# checkout, so a project here always corresponds to a fleet project.
exec t3 serve \
  --host 0.0.0.0 \
  --port "$PORT" \
  --mode web \
  --no-browser \
  --base-dir "$T3_HOME"
