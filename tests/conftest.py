"""Shared fixtures for tests that need a real Home Assistant core instance.

test_session.py/test_paths.py/test_api_objtree.py load their modules directly
and never import `homeassistant`, so they don't need any of this. Tests that
do (e.g. exercising custom_components/ha_repl_server/__init__.py or
objtree.py against a live `hass`) get it from here instead.
"""

from __future__ import annotations

pytest_plugins = "pytest_homeassistant_custom_component"

# Deliberately not autouse: enable_custom_integrations (from the test harness)
# itself depends on `hass`, so forcing it on every test - including ones using
# recorder_mock, which asserts `hass` hasn't been built yet when it runs -
# would break recorder-backed tests that never touch custom-integration
# loading at all (only test_init.py's config_entries.async_setup() does).
# That test requests `enable_custom_integrations` directly instead.
