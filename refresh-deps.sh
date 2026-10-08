#!/bin/bash
echo "Pull in auto bot PRs ..."
git pull
echo "Upgrading uv and friends ..."
mise upgrade
echo "Upgrading uv deps ..."
# without ~/.config/uv/uv.toml, whose settings would end up in uv.lock and fail CI
XDG_CONFIG_HOME=/dev/null uv lock --upgrade
echo "Refreshing venv ..."
uv sync --dev --group docs
echo "Pre-commit autoupdate ..."
pre-commit autoupdate
