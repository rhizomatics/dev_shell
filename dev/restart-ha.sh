#!/usr/bin/env bash
# Restart the dev Home Assistant on :8123 - kills any instance already
# running from run-ha.sh/restart-ha.sh and starts a fresh one, so Python
# re-imports custom_components/ha_repl_server's latest code (a plain config
# entry reload doesn't - HA never re-imports an already-loaded custom
# component's modules, just re-runs the cached ones).
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f "hass -c dev/config" >/dev/null; then
    pkill -f "hass -c dev/config"
    while pgrep -f "hass -c dev/config" >/dev/null; do sleep 0.2; done
fi

exec dev/run-ha.sh "$@"
