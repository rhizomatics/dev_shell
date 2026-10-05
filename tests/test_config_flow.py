"""Tests for the config flow's risk-acknowledgment gate (see config_flow.py's
own docstring for why) and the separate options flow that holds
expose_hass/expose_sql - editable after setup, not part of that gate.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_repl_server.const import DOMAIN

_ALL_ACKS = {
    "ack_stability": True,
    "ack_sql_not_guaranteed": True,
    "ack_secrets": True,
    "ack_token": True,
}


async def test_user_flow_shows_a_form_first(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # The feature toggles live in the options flow, not here.
    assert result["data_schema"] is not None
    assert "expose_hass" not in result["data_schema"].schema
    assert "expose_sql" not in result["data_schema"].schema


async def test_user_flow_rejects_submission_missing_an_acknowledgment(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    incomplete = {**_ALL_ACKS, "ack_token": False}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], incomplete
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "ack_required"}


async def test_user_flow_creates_entry_once_every_ack_is_checked(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _ALL_ACKS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The acknowledgments themselves aren't persisted - they're a one-time
    # gate, not runtime state. Both toggles default on in entry.options.
    assert result["data"] == {}
    assert result["options"] == {"expose_hass": True, "expose_sql": True}


async def test_import_flow_defaults_both_toggles_on_with_no_form(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data={}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    assert result["options"] == {"expose_hass": True, "expose_sql": True}


async def test_options_flow_shows_current_values_as_defaults(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={"expose_hass": False, "expose_sql": True}
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["data_schema"] is not None
    defaults = {k.schema: k.default() for k in result["data_schema"].schema}
    assert defaults == {"expose_hass": False, "expose_sql": True}


async def test_options_flow_updates_entry_options_and_reloads(
    recorder_mock, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "sql" in hass.data[DOMAIN].get("default").globals_

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"expose_hass": True, "expose_sql": False}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"expose_hass": True, "expose_sql": False}
    # OptionsFlowWithReload reloaded the entry - the session manager was
    # rebuilt with the new bindings, not the old ones.
    assert "sql" not in hass.data[DOMAIN].get("default").globals_
