"""Tests for ObjTree's `mode("api"/"live")` switch, against a real (if
minimal) entity via pytest-homeassistant-custom-component - unlike
test_session.py's "fake-hass" string, this needs a genuine live Entity and
entity registry entry to exercise the api/live branches honestly."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockEntity,
    setup_test_component_platform,
)

from custom_components.ha_repl_server.objtree import ApiEntity, ObjTree


@pytest.fixture
async def obj(hass: HomeAssistant) -> ObjTree:
    entity = MockEntity(entity_id="sensor.test_one", unique_id="t1", name="Test One")
    entity._attr_state = "42"
    setup_test_component_platform(hass, "sensor", [entity])
    assert await async_setup_component(
        hass, "sensor", {"sensor": [{"platform": "test"}]}
    )
    await hass.async_block_till_done()
    return ObjTree(hass)


async def test_default_mode_is_live(obj: ObjTree):
    assert obj.mode() == "live"
    entity = obj["sensor.test_one"]
    assert isinstance(entity, MockEntity)
    assert entity.state == "42"


async def test_mode_api_returns_dict_shaped_entity(obj: ObjTree):
    obj.mode("api")
    assert obj.mode() == "api"
    entity = obj["sensor.test_one"]
    assert isinstance(entity, ApiEntity)
    assert entity.entity_id == "sensor.test_one"
    assert entity.platform == "test"
    assert entity.domain == "sensor"
    assert entity.object_id == "test_one"
    assert entity.state == "42"
    assert entity.name == "Test One"


async def test_mode_persists_on_derived_subtree(obj: ObjTree):
    obj.mode("api")
    subtree = obj["/test"]
    assert isinstance(subtree, ObjTree)
    entity = subtree["sensor/test_one"]
    assert isinstance(entity, ApiEntity)


async def test_mode_live_reverts(obj: ObjTree):
    obj.mode("api")
    obj.mode("live")
    assert obj.mode() == "live"
    assert isinstance(obj["sensor.test_one"], MockEntity)


async def test_mode_rejects_bad_value(obj: ObjTree):
    with pytest.raises(ValueError, match="mode must be"):
        obj.mode("bogus")


async def test_find_respects_mode(obj: ObjTree):
    obj.mode("api")
    [found] = list(obj.find())
    assert isinstance(found, ApiEntity)
    obj.mode("live")
    [found] = list(obj.find())
    assert isinstance(found, MockEntity)
