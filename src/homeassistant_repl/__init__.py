"""Command line client for Home Assistant REPL."""

from __future__ import annotations

import asyncio
import warnings

from .api_objtree import ApiObjTree, Cache
from .client import Client, HaReplError
from .config import load_config, resolve_connection
from .rest import hass_api

__all__ = ["ApiObjTree", "Cache", "Client", "HaReplError", "connect", "hass_api"]


async def connect(
    url: str | None = None, token: str | None = None, *, ttl: float | None = None
) -> ApiObjTree:
    """Connect to Home Assistant's websocket API and return a ready-to-use
    `obj` tree - the one-liner version of API client mode (see
    apirepl.py/api_objtree.py for the pieces this wires together), for
    scripts and plain Python sessions rather than the `ha-repl` REPL.

    `url` is a URL or the name of a server in `config.toml`, as the CLI's
    `--server` is - `connect("house")`. `url`/`token` default to
    $HASS_SERVER/$HASS_TOKEN, then `config.toml`'s `default` server (and,
    inside a Home Assistant add-on, the supervisor) the same way the CLI
    does - see config.py's resolve_connection(). Plugins are the
    shells' own, and aren't run here. `ttl` is how long the snapshot is used
    before it's considered stale (`config.toml`'s `ttl`, or 30 seconds);
    this helper doesn't auto-refresh, so call `await obj.cache.refresh()`
    yourself when you want a newer one.

    The websocket stays open for the life of the process - call
    `await obj.cache.client.close()` when you're done with it, if that
    matters for your script.
    """
    config = load_config()
    for warning in config.warnings:
        warnings.warn(f"ha-repl: {warning}", stacklevel=2)
    # In a thread: a server's `token_command` is a subprocess to wait for.
    connection = await asyncio.to_thread(resolve_connection, url, token, config)
    client = await Client(connection.url, connection.token).open()
    if ttl is None:
        ttl = 30.0 if config.ttl is None else float(config.ttl)
    cache = Cache(client, ttl)
    await cache.refresh()
    return ApiObjTree(cache)
