"""`hass_api` - an already-connected homeassistant_api.AsyncClient, bound in
custom mode alongside `hass`/`obj` for parity with API client mode (see
../../src/homeassistant_repl/rest.py). Duplicated rather than imported
across the packaging boundary for the same reason as objtree.py/paths.py -
this component is deployed via HACS with its own `manifest.json`
requirements, not as a dependent of the PyPI homeassistant_repl package.

Named `hass_api`, not `api`: `obj.mode("api")` already uses "api" for the
cached/read-only view `obj` can switch into, and the two are easy to
conflate in a transcript otherwise.

There's no notion of "the current admin's token" inside hass itself, so
this only resolves via the add-on supervisor proxy or an explicit
$HASS_SERVER/$HASS_TOKEN - see __init__.py for what happens when neither is
set (a warning, not a failed setup).
"""

from __future__ import annotations

import os

from homeassistant_api import AsyncClient
from homeassistant_api.errors import HomeassistantAPIError


class HassApiUnavailable(Exception):
    """No usable $SUPERVISOR_TOKEN or $HASS_SERVER/$HASS_TOKEN, or the API
    didn't respond/authenticate."""


async def connect_hass_api() -> AsyncClient:
    if token := os.environ.get("SUPERVISOR_TOKEN"):
        url = "http://supervisor/core/api"
    elif (server := os.environ.get("HASS_SERVER")) and (
        token := os.environ.get("HASS_TOKEN")
    ):
        url = server.rstrip("/") + "/api"
    else:
        raise HassApiUnavailable("no $SUPERVISOR_TOKEN or $HASS_SERVER/$HASS_TOKEN set")
    client = AsyncClient(url, token)
    try:
        running = await client.check_api_running()
    except (HomeassistantAPIError, OSError) as err:
        raise HassApiUnavailable(f"cannot reach {url}: {err}") from err
    if not running:
        raise HassApiUnavailable(f"{url} is not running")
    return client
