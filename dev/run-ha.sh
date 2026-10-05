#!/usr/bin/env bash
# Run the dev Home Assistant on :8123 with the live custom_components/ha_repl_server.
# Restart it to pick up changes to the integration.
set -euo pipefail
cd "$(dirname "$0")/.."
HA_VENV="${HA_VENV:-$PWD/.venv-ha}"

mkdir -p dev/config/custom_components
ln -sfn ../../../custom_components/ha_repl_server dev/config/custom_components/ha_repl_server

# Same .env the CLI client reads (HASS_SERVER/HASS_TOKEN) - without it in
# the server's own environment too, rest.py's connect_hass_api() has
# nothing to point `hass_api` at and live mode's `hass_api` stays None.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

exec "$HA_VENV/bin/hass" -c dev/config "$@"
