"""Minimal Home Assistant websocket API client."""

from __future__ import annotations

import itertools
import json
import os
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import websockets


class DevShellError(Exception):
    """Connection, auth or command failure (not an error in the user's code)."""


def resolve_url(url: str | None) -> str:
    """Accept http(s)://host:8123 or ws(s)://... and return the websocket endpoint."""
    if not url:
        # Inside a Home Assistant add-on (e.g. Studio Code Server) talk via the supervisor.
        if os.environ.get("SUPERVISOR_TOKEN"):
            return "ws://supervisor/core/websocket"
        url = "http://localhost:8123"
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    path = parts.path.rstrip("/")
    if not path.endswith("/websocket"):
        path += "/api/websocket"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def resolve_token(token: str | None) -> str:
    token = token or os.environ.get("HASS_TOKEN") or os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise DevShellError(
            "No access token: set HASS_TOKEN or pass --token "
            "(create one under Profile > Security > Long-lived access tokens)"
        )
    return token


class Client:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self._token = token
        self._ids = itertools.count(1)
        self._ws: Any = None

    async def __aenter__(self) -> Self:
        try:
            self._ws = await websockets.connect(self.url, max_size=None)
        except (OSError, websockets.InvalidURI, websockets.InvalidHandshake) as err:
            raise DevShellError(f"Cannot connect to {self.url}: {err}") from err
        msg = await self._recv()
        if msg.get("type") != "auth_required":
            raise DevShellError(f"Unexpected greeting: {msg}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        msg = await self._recv()
        if msg.get("type") != "auth_ok":
            raise DevShellError(f"Authentication failed: {msg.get('message', msg)}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._ws.close()

    async def _recv(self) -> dict[str, Any]:
        try:
            return json.loads(await self._ws.recv())
        except websockets.ConnectionClosed as err:
            raise DevShellError(f"Connection closed: {err}") from err

    async def call(self, type_: str, **payload: Any) -> Any:
        msg_id = next(self._ids)
        await self._ws.send(json.dumps({"id": msg_id, "type": type_, **payload}))
        while True:
            msg = await self._recv()
            if msg.get("id") != msg_id or msg.get("type") != "result":
                continue
            if not msg["success"]:
                error = msg.get("error", {})
                if error.get("code") == "unknown_command":
                    raise DevShellError(
                        f"{type_} not available: is the dev_shell_server integration "
                        "installed and `dev_shell_server:` in configuration.yaml?"
                    )
                raise DevShellError(f"{type_} failed: {error.get('message', error)}")
            return msg["result"]
