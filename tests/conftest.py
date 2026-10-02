"""Shared fixtures for tests that need a real Home Assistant core instance.

test_session.py/test_paths.py/test_api_objtree.py load their modules directly
and never import `homeassistant`, so they don't need any of this. Tests that
do (e.g. exercising custom_components/dev_shell_server/__init__.py or
objtree.py against a live `hass`) get it from here instead.
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Custom integrations are disabled by default in the test harness."""
