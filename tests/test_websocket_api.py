"""Tests for the ha_repl_server/* websocket commands - via the test
harness's real `hass_ws_client`, so this exercises actual command
registration/schema validation, not just the handler functions directly."""

from __future__ import annotations

import base64

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_repl_server.const import DOMAIN
from homeassistant_repl.sql import SqlResult as ClientSqlResult


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


async def _setup(hass: HomeAssistant, **options) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_ws_sql_returns_arrow_the_client_can_read(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None, hass_ws_client
):
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({
        "id": 1,
        "type": "ha_repl_server/sql",
        "query": "select 1 as a, 'x' as b union all select 2, 'y'",
        "max_rows": 1,
    })
    response = await client.receive_json()

    assert response["success"]
    assert response["result"]["truncated"] is True
    result = ClientSqlResult.from_arrow(base64.b64decode(response["result"]["arrow"]))
    assert result.to_dicts() == [{"a": 1, "b": "x"}]


async def test_ws_sql_reports_a_rejected_query_as_sql_error(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None, hass_ws_client
):
    await _setup(hass)
    client = await hass_ws_client(hass)

    for msg_id, query in enumerate(("delete from states", "select * from nope"), 1):
        await client.send_json({
            "id": msg_id,
            "type": "ha_repl_server/sql",
            "query": query,
        })
        response = await client.receive_json()
        assert not response["success"]
        assert response["error"]["code"] == "sql_error"


async def test_ws_sql_tables_lists_recorder_tables_and_columns(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None, hass_ws_client
):
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "ha_repl_server/sql_tables"})
    response = await client.receive_json()

    tables = {t["name"]: t["columns"] for t in response["result"]["tables"]}
    assert "states" in tables
    assert {"name": "entity_id", "type": "VARCHAR(255)"} in tables["states_meta"]


async def test_ws_sql_commands_honour_expose_sql_off(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None, hass_ws_client
):
    await _setup(hass, expose_sql=False)
    client = await hass_ws_client(hass)

    for msg_id, msg in enumerate(
        (
            {"type": "ha_repl_server/sql", "query": "select 1"},
            {"type": "ha_repl_server/sql_tables"},
        ),
        1,
    ):
        await client.send_json({"id": msg_id, **msg})
        response = await client.receive_json()
        assert not response["success"]
        assert response["error"]["code"] == "sql_disabled"
