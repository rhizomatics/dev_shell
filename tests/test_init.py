"""Smoke test for the custom component's real setup path - uses the real
`hass` fixture from pytest-homeassistant-custom-component instead of the
hand-rolled fakes test_session.py/test_api_objtree.py use, so it actually
exercises async_setup_entry/async_unload_entry rather than just the
HA-free pieces (session.py, api_objtree.py).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dev_shell_server.const import DOMAIN
from custom_components.dev_shell_server.objtree import ObjTree


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = hass.data[DOMAIN]
    session = manager.get("default")
    assert session.globals_["hass"] is hass
    assert isinstance(session.globals_["obj"], ObjTree)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data
