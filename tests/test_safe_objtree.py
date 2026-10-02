"""Tests for safe mode's obj tree (SafeObjTree), against a fake Client - no
real Home Assistant needed. Payload shapes mirror what get_states and the
config/*_registry/list websocket commands actually return."""

from __future__ import annotations

from typing import Any

import pytest

from dev_shell.safe_objtree import Cache, SafeEntity, SafeObjTree


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def call(self, type_: str, **payload: Any) -> Any:
        self.calls.append(type_)
        return self.responses[type_]


STATES = [
    {"entity_id": "light.kitchen_lights", "state": "on", "attributes": {"brightness": 200}},
    {"entity_id": "light.lounge_lamp", "state": "off", "attributes": {}},
    {"entity_id": "media_player.kitchen_show", "state": "idle", "attributes": {}},
    {"entity_id": "sensor.outside_temp", "state": "12.3", "attributes": {"unit": "C"}},
]

ENTITY_REGISTRY = [
    {
        "entity_id": "light.kitchen_lights",
        "platform": "hue",
        "area_id": None,
        "device_id": "dev_hue",
        "labels": ["lbl_important"],
        "unique_id": "uid1",
        "name": None,
        "created_at": 1700000000.0,
        "modified_at": 1700000000.0,
        "aliases": [],
        "options": {},
    },
    {
        "entity_id": "light.lounge_lamp",
        "platform": "hue",
        "area_id": "area_lounge",
        "device_id": None,
        "labels": [],
        "unique_id": "uid2",
    },
    {
        "entity_id": "media_player.kitchen_show",
        "platform": "alexa_devices",
        "area_id": "area_kitchen",
        "device_id": None,
        "labels": [],
        "unique_id": "uid3",
    },
    {
        "entity_id": "sensor.outside_temp",
        "platform": "demo",
        "area_id": None,
        "device_id": None,
        "labels": [],
        "unique_id": "uid4",
    },
]

DEVICE_REGISTRY = [{"id": "dev_hue", "area_id": "area_kitchen"}]
AREA_REGISTRY = [
    {"area_id": "area_kitchen", "name": "Kitchen"},
    {"area_id": "area_lounge", "name": "Lounge"},
]
LABEL_REGISTRY = [{"label_id": "lbl_important", "name": "Important"}]


@pytest.fixture
async def tree() -> SafeObjTree:
    client = FakeClient(
        {
            "get_states": STATES,
            "config/entity_registry/list": ENTITY_REGISTRY,
            "config/device_registry/list": DEVICE_REGISTRY,
            "config/area_registry/list": AREA_REGISTRY,
            "config/label_registry/list": LABEL_REGISTRY,
        }
    )
    cache = Cache(client, ttl=30)
    await cache.refresh()
    return SafeObjTree(cache)


async def test_root_keys_are_integrations(tree: SafeObjTree):
    assert list(tree.keys()) == ["/alexa_devices", "/demo", "/hue"]


async def test_domain_level_keys(tree: SafeObjTree):
    hue = tree["/hue"]
    assert list(hue.keys()) == ["/light"]


async def test_leaf_lookup_returns_safe_entity(tree: SafeObjTree):
    entity = tree["/hue/light/kitchen_lights"]
    assert isinstance(entity, SafeEntity)
    assert entity.state == "on"
    assert entity.attributes == {"brightness": 200}


async def test_bare_entity_id_lookup(tree: SafeObjTree):
    assert tree["light.kitchen_lights"].entity_id == "light.kitchen_lights"


async def test_missing_intermediate_path_raises(tree: SafeObjTree):
    with pytest.raises(KeyError):
        tree["/hue/sensor"]  # hue has no sensors


async def test_find_names_flat_no_filters(tree: SafeObjTree):
    assert sorted(tree.find_names()) == [
        "/alexa_devices/media_player/kitchen_show",
        "/demo/sensor/outside_temp",
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_names_by_area_inherits_from_device(tree: SafeObjTree):
    # light.kitchen_lights has no area of its own, but its device (dev_hue) does.
    assert sorted(tree.find_names(area="Kitchen")) == [
        "/alexa_devices/media_player/kitchen_show",
        "/hue/light/kitchen_lights",
    ]


async def test_find_names_by_area_id_too(tree: SafeObjTree):
    assert sorted(tree.find_names(area="area_lounge")) == ["/hue/light/lounge_lamp"]


async def test_find_names_by_platform(tree: SafeObjTree):
    assert sorted(tree.find_names(platform="hue")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_names_by_label_name(tree: SafeObjTree):
    assert list(tree.find_names(label="Important")) == ["/hue/light/kitchen_lights"]


async def test_find_names_scoped_by_path(tree: SafeObjTree):
    assert sorted(tree.find_names("/hue")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_names_by_domain_across_integrations(tree: SafeObjTree):
    assert sorted(tree.find_names(domain="light")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_names_filters_and_together(tree: SafeObjTree):
    # domain=light AND platform=demo: no light is on the demo platform here.
    assert list(tree.find_names(domain="light", platform="demo")) == []


async def test_find_yields_path_entity_pairs(tree: SafeObjTree):
    [found] = list(tree.find(domain="light", platform="hue", area="Kitchen"))
    path, entity = found  # plain tuple-unpacking must work
    assert path == "/hue/light/kitchen_lights"
    assert entity is found.entity
    assert found.path == path
    assert entity.state == "on"
    # Same object indexing would give you - not a separate lookup/copy.
    assert entity is tree["/hue/light/kitchen_lights"]


async def test_find_unknown_area_raises_immediately(tree: SafeObjTree):
    with pytest.raises(KeyError):
        tree.find(area="Nonexistent")  # must raise before iteration, not during


async def test_passing_an_entity_instead_of_a_path_raises_typeerror(tree: SafeObjTree):
    # Regression: tree.show(tree["/hue/light/kitchen_lights"]) used to crash
    # deep inside parse_path with a confusing AttributeError.
    entity = tree["/hue/light/kitchen_lights"]
    with pytest.raises(TypeError):
        tree.show(entity)
    with pytest.raises(TypeError):
        tree.find(entity)  # must raise immediately, not only once iterated
    with pytest.raises(TypeError):
        tree[entity]


async def test_show_cleans_and_merges(tree: SafeObjTree):
    result = tree.show("/hue/light/kitchen_lights")
    assert result["state"] == "on"
    assert result["state_attributes"] == {"brightness": 200}
    assert result["platform"] == "hue"
    assert result["labels"] == ["lbl_important"]
    # None / empty list dropped, but an empty dict is kept (only None and
    # empty-collection-standing-in-for-a-set are dropped, same as live mode):
    assert "name" not in result
    assert "aliases" not in result
    assert result["options"] == {}
    # timestamp converted to a local ISO 8601 string, not left as a float:
    assert isinstance(result["created_at"], str)
    assert "T" in result["created_at"]


async def test_ordered_views_support_indexing(tree: SafeObjTree):
    keys = tree.keys()
    assert keys[0] == "/alexa_devices"
    assert keys[-1] == "/hue"
    assert list(reversed(keys)) == ["/hue", "/demo", "/alexa_devices"]


async def test_reset_cache_marks_stale(tree: SafeObjTree):
    assert not tree.cache.is_stale()
    tree.reset_cache()
    assert tree.cache.is_stale()
