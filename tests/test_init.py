"""Smoke test for the custom component's real setup path - uses the real
`hass` fixture from pytest-homeassistant-custom-component instead of the
hand-rolled fakes test_session.py/test_api_objtree.py use, so it actually
exercises async_setup_entry/async_unload_entry rather than just the
HA-free pieces (session.py, api_objtree.py).

`recorder_mock` is listed first in each test's own parameters (not just
relied on via conftest.py) because it needs to prepare the database before
`hass` itself is built; `recorder` is now a declared manifest dependency
(for sql()), so config_entries.async_setup() needs a working one.
`enable_custom_integrations` has no such ordering requirement (it depends on
`hass` itself), so it's requested directly rather than via an autouse
fixture - see conftest.py for why that matters here specifically.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_repl_server.const import DOMAIN
from custom_components.ha_repl_server.objtree import ObjTree


async def test_setup_and_unload_entry(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = hass.data[DOMAIN]
    session = manager.get("default")
    assert session.globals_["hass"] is hass
    assert isinstance(session.globals_["obj"], ObjTree)
    # sql is served by its own websocket command, never bound into sessions.
    assert "sql" not in session.globals_
    assert manager.has_feature("sql")
    # Nor is hass_api: that's a REST client, which the shell has locally.
    assert "hass_api" not in session.globals_

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data


async def test_setup_entry_respects_expose_hass_and_expose_sql_false(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={"expose_hass": False, "expose_sql": False}
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    session = hass.data[DOMAIN].get("default")
    assert "hass" not in session.globals_
    assert not hass.data[DOMAIN].has_feature("sql")
    assert isinstance(session.globals_["obj"], ObjTree)

    assert await hass.config_entries.async_unload(entry.entry_id)
