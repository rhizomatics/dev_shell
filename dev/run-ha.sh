#!/usr/bin/env bash
# Run the dev Home Assistant on :8123 with the live custom_components/ha_repl_server.
# Restart it to pick up changes to the integration.
set -euo pipefail
cd "$(dirname "$0")/.."
HA_VENV="${HA_VENV:-$PWD/.venv-ha}"

mkdir -p dev/config/custom_components
ln -sfn ../../../custom_components/ha_repl_server dev/config/custom_components/ha_repl_server
exec "$HA_VENV/bin/hass" -c dev/config "$@"
