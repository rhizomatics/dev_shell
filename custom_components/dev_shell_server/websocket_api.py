"""Websocket commands: dev_shell_server/exec, dev_shell_server/reset,
dev_shell_server/sessions (all admin only)."""

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


def _manager(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> SessionManager | None:
    if (manager := hass.data.get(DOMAIN)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hass Shell integration is not loaded")
    return manager


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "dev_shell_server/exec",
        vol.Required("code"): str,
        vol.Optional("session", default=DEFAULT_SESSION): str,
        vol.Optional("timeout"): vol.Coerce(float),
        vol.Optional("color", default=False): bool,
        vol.Optional("width", default=88): vol.Coerce(int),
    }
)
@websocket_api.async_response
async def ws_exec(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    result = await manager.get(msg["session"]).run(
        msg["code"], msg.get("timeout"), color=msg["color"], width=msg["width"]
    )
    connection.send_result(msg["id"], result.as_dict())


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "dev_shell_server/reset",
        vol.Optional("session", default=DEFAULT_SESSION): str,
    }
)
@callback
def ws_reset(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"reset": manager.reset(msg["session"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "dev_shell_server/sessions"})
@callback
def ws_sessions(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"sessions": manager.describe()})
