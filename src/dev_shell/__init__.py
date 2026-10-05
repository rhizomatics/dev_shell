"""Command line client for the Developer Shell."""

from __future__ import annotations

from .api_objtree import ApiObjTree, Cache
from .client import Client, DevShellError, resolve_token, resolve_url

__all__ = ["ApiObjTree", "Cache", "Client", "DevShellError", "connect"]


async def connect(
    url: str | None = None, token: str | None = None, *, ttl: float = 30.0
) -> ApiObjTree:
    """Connect to Home Assistant's websocket API and return a ready-to-use
    `obj` tree - the one-liner version of API client mode (see
    apirepl.py/api_objtree.py for the pieces this wires together), for
    scripts and plain Python sessions rather than the `dev_shell` REPL.

    `url`/`token` default to $HASS_SERVER/$HASS_TOKEN (and, inside a Home
    Assistant add-on, the supervisor) the same way the CLI does - see
    resolve_url()/resolve_token(). `ttl` is how long the snapshot is used
    before it's considered stale; this helper doesn't auto-refresh, so call
    `await obj.cache.refresh()` yourself when you want a newer one.

    The websocket stays open for the life of the process - call
    `await obj.cache.client.close()` when you're done with it, if that
    matters for your script.
    """
    client = await Client(resolve_url(url), resolve_token(token)).open()
    cache = Cache(client, ttl)
    await cache.refresh()
    return ApiObjTree(cache)
