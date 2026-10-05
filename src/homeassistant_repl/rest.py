"""`hass_api()` - the opinionated one-liner for Home Assistant's REST API
(https://developers.home-assistant.io/docs/api/rest/), parallel to
`connect()` for the websocket-based `obj` tree. Hands back a ready-to-use
`homeassistant_api.AsyncClient` (https://homeassistantapi.readthedocs.io)
rather than a thin wrapper of our own - that package already covers the
REST surface (get_state, get_states, trigger_service, get_config,
get_logbook_entries, get_entity_histories, render a template, ...) with
typed responses, so there's no reason to reinvent it here.

Named `hass_api`, not `api`: `obj.mode("api")` already uses "api" for the
cached/read-only view custom mode's `obj` can switch into, and the two are
easy to conflate in a transcript otherwise.
"""

from __future__ import annotations

from homeassistant_api import AsyncClient
from homeassistant_api.errors import HomeassistantAPIError

from .client import HaReplError, resolve_rest_url, resolve_token


async def hass_api(url: str | None = None, token: str | None = None) -> AsyncClient:
    """Connect to Home Assistant's REST API, returning a ready-to-use
    homeassistant_api.AsyncClient - see https://homeassistantapi.readthedocs.io/en/stable/

    `url`/`token` default to $HASS_SERVER/$HASS_TOKEN (and, inside a Home
    Assistant add-on, the supervisor) the same way `connect()` does. The
    underlying HTTP session stays open for the life of the process.
    """
    client = AsyncClient(resolve_rest_url(url), resolve_token(token))
    try:
        running = await client.check_api_running()
    except (HomeassistantAPIError, OSError) as err:
        raise HaReplError(f"Cannot reach {client.api_url}: {err}") from err
    if not running:
        raise HaReplError(f"{client.api_url} is not running")
    return client
