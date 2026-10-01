"""Hass Shell: a live Python REPL inside Home Assistant, served over the websocket API.

Enabled with `dev_shell:` in configuration.yaml. Executes arbitrary code as admin; never
enable it on an instance where admin accounts are not fully trusted.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .browser import open_target
from .const import DOMAIN
from .objtree import ObjTree
from .session import SessionManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data[DOMAIN] = SessionManager(
        {"hass": hass, "obj": ObjTree(hass), "open": open_target}
    )
    websocket_api.async_register(hass)
    _LOGGER.warning(
        "Hass Shell is enabled: admin users can execute arbitrary Python in this instance"
    )
    return True
