#!/usr/bin/env bash
# Install Home Assistant (own venv) and the dev_shell CLI (project venv). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
HA_VENV="${HA_VENV:-$PWD/.venv-ha}"

command -v uv >/dev/null || pip install --user uv

[ -x "$HA_VENV/bin/python" ] || uv venv --python 3.14 "$HA_VENV"
VIRTUAL_ENV="$HA_VENV" uv pip install "homeassistant${HA_VERSION:+==$HA_VERSION}"
uv sync --group dev
