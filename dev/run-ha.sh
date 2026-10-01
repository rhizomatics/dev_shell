#!/usr/bin/env bash
# Run the dev Home Assistant on :8123 with the live custom_components/dev_shell.
# Restart it to pick up changes to the integration.
set -euo pipefail
cd "$(dirname "$0")/.."
HA_VENV="${HA_VENV:-$PWD/.venv-ha}"

mkdir -p dev/config/custom_components
ln -sfn ../../../custom_components/dev_shell dev/config/custom_components/dev_shell
exec "$HA_VENV/bin/hass" -c dev/config "$@"
