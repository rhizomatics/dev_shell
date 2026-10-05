"""Smoke test for the custom component's real setup path - uses the real
`hass` fixture from pytest-homeassistant-custom-component instead of the
hand-rolled fakes test_session.py/test_api_objtree.py use, so it actually
exercises async_setup_entry/async_unload_entry rather than just the
HA-free pieces (session.py, api_objtree.py).

SUPERVISOR_TOKEN/HASS_SERVER/HASS_TOKEN are cleared before every test here,
not left to whatever the environment happens to have: if a developer's
shell has one set (e.g. from sourcing dev/.env for manual testing),
connect_hass_api() would otherwise make a real network call mid-test -
which pytest-homeassistant-custom-component's socket blocking turns into a
failure instead of the deterministic HassApiUnavailable path these tests
expect.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_repl_server.const import DOMAIN
from custom_components.ha_repl_server.objtree import ObjTree


@pytest.fixture(autouse=True)
def _no_real_hass_api_env(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASS_SERVER", raising=False)
    monkeypatch.delenv("HASS_TOKEN", raising=False)


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = hass.data[DOMAIN]
    session = manager.get("default")
    assert session.globals_["hass"] is hass
    assert isinstance(session.globals_["obj"], ObjTree)
    # No $HASS_SERVER/$HASS_TOKEN (nor a supervisor) here - connect_hass_api()
    # can't reach anything, so this only warns, it doesn't fail setup.
    assert session.globals_["hass_api"] is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data


async def test_setup_entry_connects_hass_api_when_configured(
    hass: HomeAssistant, monkeypatch, niquests_mock
) -> None:
    monkeypatch.setenv("HASS_SERVER", "http://ha.example:8123")
    monkeypatch.setenv("HASS_TOKEN", "sometoken")
    niquests_mock.get("http://ha.example:8123/api/").respond(
        json={"message": "API running."}
    )

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    session = hass.data[DOMAIN].get("default")
    hass_api = session.globals_["hass_api"]
    assert hass_api is not None
    assert hass_api.api_url == "http://ha.example:8123/api/"

    assert await hass.config_entries.async_unload(entry.entry_id)
