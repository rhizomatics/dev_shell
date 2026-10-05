"""Tests for the ha_repl_server/* websocket commands - via the test
harness's real `hass_ws_client`, so this exercises actual command
registration/schema validation, not just the handler functions directly."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_repl_server.const import DOMAIN


async def test_ws_info_returns_manifest_version(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None, hass_ws_client
):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    integration = await async_get_integration(hass, DOMAIN)

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "ha_repl_server/info"})
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] == {"version": integration.version}
