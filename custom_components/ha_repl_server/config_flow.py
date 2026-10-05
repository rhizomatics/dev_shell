"""Config flow for Home Assistant REPL: a single entry, added from the UI
(gated behind explicit risk acknowledgment - see _ACK_FIELDS below, the same
"are you sure you know what you're doing" pattern HACS's own config flow
uses) or imported from YAML (`ha_repl_server:` in configuration.yaml, left
as a no-form, always-on path for backward compatibility with existing
installs that predate this gate).

What's exposed (`expose_hass`/`expose_sql`) is deliberately not on that
initial form: it's an ongoing setting, not a one-time risk acknowledgment,
so it lives in the entry's *options* - editable any time from Settings >
Devices & services > Home Assistant REPL > Configure - rather than data
fixed at creation.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)

from .const import DOMAIN

TITLE = "Home Assistant REPL Live Server"

# Both default on - matches the always-on behaviour of every version before
# this option existed, so an entry that predates it (or was never
# reconfigured) keeps working the same way.
_DEFAULT_OPTIONS = {"expose_hass": True, "expose_sql": True}

# Every one of these must be truthy in user_input before the entry is
# created (see async_step_user) - each is its own field (rather than one
# "I agree" checkbox) so the form, and this module's own code, name the
# specific thing being acknowledged.
_ACK_FIELDS = (
    "ack_stability",
    "ack_sql_not_guaranteed",
    "ack_secrets",
    "ack_token",
)

_ACK_SCHEMA = vol.Schema({
    vol.Required(field, default=False): bool for field in _ACK_FIELDS
})


class HaReplServerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> HaReplServerOptionsFlow:
        return HaReplServerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if all(user_input.get(field) for field in _ACK_FIELDS):
                return self.async_create_entry(
                    title=TITLE, data={}, options=_DEFAULT_OPTIONS
                )
            errors["base"] = "ack_required"
        return self.async_show_form(
            step_id="user",
            data_schema=_ACK_SCHEMA,  # type: ignore[arg-type]
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title=TITLE, data={}, options=_DEFAULT_OPTIONS)


class HaReplServerOptionsFlow(OptionsFlowWithReload):
    """`expose_hass`/`expose_sql` - editable after setup; changing either
    reloads the entry automatically (OptionsFlowWithReload's own job) so
    async_setup_entry re-reads them immediately, no manual listener needed.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Required("expose_hass", default=options.get("expose_hass", True)): bool,
            vol.Required("expose_sql", default=options.get("expose_sql", True)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)  # type: ignore[arg-type]
