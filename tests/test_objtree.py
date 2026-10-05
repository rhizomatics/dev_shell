"""Tests for ObjTree's `mode("api"/"live")` switch, against a real (if
minimal) entity via pytest-homeassistant-custom-component - unlike
test_session.py's "fake-hass" string, this needs a genuine live Entity and
entity registry entry to exercise the api/live branches honestly."""

from __future__ import annotations

import re

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
    one = MockEntity(entity_id="sensor.test_one", unique_id="t1", name="Test One")
    one._attr_state = "42"
    two = MockEntity(entity_id="sensor.test_two", unique_id="t2", name="Test Two")
    two._attr_state = "7"
    setup_test_component_platform(hass, "sensor", [one, two])
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


async def test_bare_entity_id_courtesy_lookup_at_root(obj: ObjTree):
    entity = obj["sensor.test_one"]
    assert isinstance(entity, MockEntity)


async def test_bare_entity_id_courtesy_lookup_scoped_to_integration(obj: ObjTree):
    scoped = obj["/test"]
    assert isinstance(scoped, ObjTree)
    entity = scoped["sensor.test_one"]
    assert isinstance(entity, MockEntity)
    assert entity is obj["sensor.test_one"]


async def test_bare_entity_id_courtesy_lookup_wrong_integration_fails(obj: ObjTree):
    # Constructed directly (not via obj["/not_test"]) so the mismatch itself
    # is what's under test, not an empty-subtree KeyError from navigating
    # into an integration scope with no entities at all.
    scoped = ObjTree(obj.hass, integration="not_test")
    with pytest.raises(KeyError):
        scoped["sensor.test_one"]


async def test_find_respects_mode(obj: ObjTree):
    obj.mode("api")
    found = list(obj.find())
    assert found and all(isinstance(f, ApiEntity) for f in found)
    obj.mode("live")
    found = list(obj.find())
    assert found and all(isinstance(f, MockEntity) for f in found)


async def test_find_paths_accepts_a_regex_prefix(obj: ObjTree):
    assert sorted(obj.find_paths("/test/sensor/test_one.*")) == [
        "/test/sensor/test_one"
    ]


async def test_find_names_regex_matches_multiple(obj: ObjTree):
    assert sorted(obj.find_names(".*test_(one|two)")) == [
        "sensor.test_one",
        "sensor.test_two",
    ]


async def test_find_paths_regex_combines_with_domain_filter(obj: ObjTree):
    assert sorted(obj.find_paths(".*test_one", domain="sensor")) == [
        "/test/sensor/test_one",
    ]
    assert list(obj.find_paths(".*test_one", domain="light")) == []


async def test_find_paths_invalid_regex_raises(obj: ObjTree):
    with pytest.raises(re.error):
        list(obj.find_paths("/test/sensor/["))
