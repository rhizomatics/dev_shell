"""Home Assistant REPL: a live Python REPL inside Home Assistant, served over the websocket API.

Set up from Settings > Devices & services > Add integration, or with `ha_repl_server:` in
configuration.yaml (imported as a config entry). Executes arbitrary code as admin; never
enable it on an instance where admin accounts are not fully trusted.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .const import DOMAIN
from .objtree import ObjTree
from .rest import HassApiUnavailable, connect_hass_api, warm_urllib3_lazy_imports
from .session import SessionManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Commands stay registered for the life of the process (HA has no unregister);
    # they report an error while no config entry is loaded.
    websocket_api.async_register(hass)
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.async_add_executor_job(warm_urllib3_lazy_imports)
    try:
        hass_api = await connect_hass_api()
    except HassApiUnavailable as err:
        _LOGGER.warning("Home Assistant REPL: `hass_api` unavailable: %s", err)
        hass_api = None

    # In entry.options, not entry.data: editable after setup via Configure
    # (see config_flow.py's HaReplServerOptionsFlow), not fixed at creation.
    # Missing (not just False) means an entry from before these existed -
    # both default on, matching that version's always-on behaviour.
    bindings: dict[str, Any] = {"obj": ObjTree(hass), "hass_api": hass_api}
    if entry.options.get("expose_hass", True):
        bindings["hass"] = hass
    # sql isn't a session binding: the client's own `sql` object fetches
    # Arrow data through ha_repl_server/sql, which checks this feature.
    features = {"sql"} if entry.options.get("expose_sql", True) else set()

    hass.data[DOMAIN] = SessionManager(bindings, frozenset(features))
    _LOGGER.warning(
        "Home Assistant REPL is enabled: admin users can execute arbitrary Python in this instance"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.pop(DOMAIN, None)
    return True
