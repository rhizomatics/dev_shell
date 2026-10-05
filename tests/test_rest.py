"""Tests for rest.py's `hass_api()` - homeassistant_api.AsyncClient wiring
and HaReplError translation - using niquests_mock rather than a live instance."""

from __future__ import annotations

import pytest

from homeassistant_repl.client import HaReplError
from homeassistant_repl.rest import hass_api

BASE = "http://ha.example:8123"


async def test_hass_api_returns_connected_client(niquests_mock):
    niquests_mock.get(f"{BASE}/api/").respond(json={"message": "API running."})
    client = await hass_api(BASE, "sometoken")
    assert client.api_url == f"{BASE}/api/"


async def test_hass_api_sends_bearer_token(niquests_mock):
    route = niquests_mock.get(f"{BASE}/api/").respond(json={"message": "API running."})
    await hass_api(BASE, "sometoken")
    assert route.calls[-1].request.headers["Authorization"] == "Bearer sometoken"


async def test_hass_api_raises_when_not_running(niquests_mock):
    niquests_mock.get(f"{BASE}/api/").respond(json={"message": "nope"})
    with pytest.raises(HaReplError, match="is not running"):
        await hass_api(BASE, "sometoken")


async def test_hass_api_raises_on_server_error(niquests_mock):
    niquests_mock.get(f"{BASE}/api/").respond(status_code=502)
    with pytest.raises(HaReplError, match="Cannot reach"):
        await hass_api(BASE, "sometoken")


async def test_hass_api_raises_on_unauthorized(niquests_mock):
    niquests_mock.get(f"{BASE}/api/").respond(status_code=401)
    with pytest.raises(HaReplError, match="Cannot reach"):
        await hass_api(BASE, "badtoken")
