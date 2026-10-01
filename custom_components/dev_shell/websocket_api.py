"""Websocket commands: dev_shell/exec, dev_shell/reset, dev_shell/sessions (all admin only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DEFAULT_SESSION, DOMAIN
from .session import SessionManager


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_exec)
    websocket_api.async_register_command(hass, ws_reset)
    websocket_api.async_register_command(hass, ws_sessions)


def _manager(hass: HomeAssistant) -> SessionManager:
    return hass.data[DOMAIN]


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "dev_shell/exec",
        vol.Required("code"): str,
        vol.Optional("session", default=DEFAULT_SESSION): str,
        vol.Optional("timeout"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_exec(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    session = _manager(hass).get(msg["session"])
    result = await session.run(msg["code"], msg.get("timeout"))
    connection.send_result(msg["id"], result.as_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "dev_shell/reset",
        vol.Optional("session", default=DEFAULT_SESSION): str,
    }
)
@callback
def ws_reset(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(msg["id"], {"reset": _manager(hass).reset(msg["session"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "dev_shell/sessions"})
@callback
def ws_sessions(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(msg["id"], {"sessions": _manager(hass).describe()})
