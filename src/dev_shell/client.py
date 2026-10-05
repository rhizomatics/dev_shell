"""Minimal Home Assistant websocket API client."""

from __future__ import annotations

import functools
import itertools
import json
import os
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import websockets


class DevShellError(Exception):
    """Connection, auth or command failure (not an error in the user's code)."""


@functools.cache
def _dotenv() -> dict[str, str]:
    """Best-effort KEY=VALUE pairs from a `.env` file in the current
    directory, if one exists - checked only once real environment variables
    come up empty, see resolve_url()/resolve_token(). Deliberately minimal
    rather than pulling in python-dotenv: comments, blank lines, an optional
    "export " prefix and quoted values are all a token/url file needs.
    Cached since it's read-only for the life of the process and both
    resolve_url() and resolve_token() may call it.
    """
    path = Path(".env")
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().removeprefix("export ")
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve_url(url: str | None) -> str:
    """Accept http(s)://host:8123 or ws(s)://... and return the websocket endpoint."""
    if not url:
        # Inside a Home Assistant add-on (e.g. Studio Code Server) talk via the supervisor.
        if os.environ.get("SUPERVISOR_TOKEN"):
            return "ws://supervisor/core/websocket"
        url = (
            os.environ.get("HASS_SERVER")
            or _dotenv().get("HASS_SERVER")
            or "http://homeassistant.local:8123"
        )
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    path = parts.path.rstrip("/")
    if not path.endswith("/websocket"):
        path += "/api/websocket"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def resolve_token(token: str | None) -> str:
    token = (
        token
        or os.environ.get("HASS_TOKEN")
        or os.environ.get("SUPERVISOR_TOKEN")
        or _dotenv().get("HASS_TOKEN")
    )
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
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def open(self) -> Self:
        """Connect and authenticate - what `async with Client(...)` does,
        exposed directly for callers that want to keep the connection open
        past the enclosing scope (e.g. `connect()` in __init__.py)."""
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

    async def close(self) -> None:
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
