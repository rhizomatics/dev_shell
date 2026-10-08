"""Tests for API client mode's obj tree (ApiObjTree), against a fake Client - no
real Home Assistant needed. Payload shapes mirror what get_states and the
config/*_registry/list websocket commands actually return."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from homeassistant_repl.api_objtree import ApiEntity, ApiObjTree, Cache


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def call(self, type_: str, **payload: Any) -> Any:
        self.calls.append(type_)
        return self.responses[type_]


STATES = [
    {
        "entity_id": "light.kitchen_lights",
        "state": "on",
        "attributes": {"brightness": 200, "friendly_name": "Kitchen Lights"},
    },
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
async def tree() -> ApiObjTree:
    client = FakeClient({
        "get_states": STATES,
        "config/entity_registry/list": ENTITY_REGISTRY,
        "config/device_registry/list": DEVICE_REGISTRY,
        "config/area_registry/list": AREA_REGISTRY,
        "config/label_registry/list": LABEL_REGISTRY,
    })
    cache = Cache(client, ttl=30)
    await cache.refresh()
    return ApiObjTree(cache)


async def test_root_keys_are_integrations(tree: ApiObjTree):
    assert list(tree.keys()) == ["/alexa_devices", "/demo", "/hue"]


async def test_domain_level_keys(tree: ApiObjTree):
    hue = tree["/hue"]
    assert isinstance(hue, ApiObjTree)
    assert list(hue.keys()) == ["/light"]


async def test_leaf_lookup_returns_api_entity(tree: ApiObjTree):
    entity = tree["/hue/light/kitchen_lights"]
    assert isinstance(entity, ApiEntity)
    assert entity.state == "on"
    assert entity.state_attributes == {"brightness": 200}
    # friendly_name is promoted to .name, not left sitting in state_attributes:
    assert entity.name == "Kitchen Lights"


async def test_name_falls_back_to_none_without_friendly_name(tree: ApiObjTree):
    lounge_lamp = tree["/hue/light/lounge_lamp"]
    assert isinstance(lounge_lamp, ApiEntity)
    assert lounge_lamp.name is None


async def test_bare_entity_id_lookup(tree: ApiObjTree):
    kitchen_lights = tree["light.kitchen_lights"]
    assert isinstance(kitchen_lights, ApiEntity)
    assert kitchen_lights.entity_id == "light.kitchen_lights"


async def test_bare_entity_id_lookup_scoped_to_integration(tree: ApiObjTree):
    hue = tree["/hue"]
    assert isinstance(hue, ApiObjTree)
    kitchen_lights = hue["light.kitchen_lights"]
    assert isinstance(kitchen_lights, ApiEntity)
    assert kitchen_lights.entity_id == "light.kitchen_lights"
    assert kitchen_lights is tree["light.kitchen_lights"]


async def test_bare_entity_id_lookup_wrong_integration_fails(tree: ApiObjTree):
    # media_player.kitchen_show belongs to alexa_devices, not hue.
    hue = tree["/hue"]
    assert isinstance(hue, ApiObjTree)
    with pytest.raises(KeyError):
        hue["media_player.kitchen_show"]


async def test_missing_intermediate_path_raises(tree: ApiObjTree):
    with pytest.raises(KeyError):
        tree["/hue/sensor"]  # hue has no sensors


async def test_find_paths_flat_no_filters(tree: ApiObjTree):
    assert sorted(tree.find_paths()) == [
        "/alexa_devices/media_player/kitchen_show",
        "/demo/sensor/outside_temp",
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_paths_by_area_inherits_from_device(tree: ApiObjTree):
    # light.kitchen_lights has no area of its own, but its device (dev_hue) does.
    assert sorted(tree.find_paths(area="Kitchen")) == [
        "/alexa_devices/media_player/kitchen_show",
        "/hue/light/kitchen_lights",
    ]


async def test_find_paths_by_area_id_too(tree: ApiObjTree):
    assert sorted(tree.find_paths(area="area_lounge")) == ["/hue/light/lounge_lamp"]


async def test_find_paths_by_platform(tree: ApiObjTree):
    assert sorted(tree.find_paths(platform="hue")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_paths_by_label_name(tree: ApiObjTree):
    assert list(tree.find_paths(label="Important")) == ["/hue/light/kitchen_lights"]


async def test_find_paths_scoped_by_path(tree: ApiObjTree):
    assert sorted(tree.find_paths("/hue")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_paths_by_domain_across_integrations(tree: ApiObjTree):
    assert sorted(tree.find_paths(domain="light")) == [
        "/hue/light/kitchen_lights",
        "/hue/light/lounge_lamp",
    ]


async def test_find_paths_filters_and_together(tree: ApiObjTree):
    # domain=light AND platform=demo: no light is on the demo platform here.
    assert list(tree.find_paths(domain="light", platform="demo")) == []


async def test_find_paths_accepts_a_regex_prefix(tree: ApiObjTree):
    # "." and "*" make this a regex, not a literal 3-segment path.
    assert sorted(tree.find_paths("/hue/light/kitchen.*")) == [
        "/hue/light/kitchen_lights",
    ]


async def test_find_names_regex_can_cross_integrations(tree: ApiObjTree):
    # A plain /integration/domain/object_id prefix can't express this -
    # kitchen_lights (hue) and kitchen_show (alexa_devices) are unrelated
    # integrations, only a pattern over the whole path can match both.
    assert sorted(tree.find_names(".*kitchen.*")) == [
        "light.kitchen_lights",
        "media_player.kitchen_show",
    ]


async def test_find_paths_regex_combines_with_platform_filter(tree: ApiObjTree):
    assert sorted(tree.find_paths(".*kitchen.*", platform="hue")) == [
        "/hue/light/kitchen_lights",
    ]


async def test_find_paths_invalid_regex_raises(tree: ApiObjTree):
    with pytest.raises(re.error):
        list(tree.find_paths("/hue/light/["))


async def test_find_yields_bare_entities(tree: ApiObjTree):
    [entity] = list(tree.find(domain="light", platform="hue", area="Kitchen"))
    assert isinstance(entity, ApiEntity)
    assert entity.entity_id == "light.kitchen_lights"
    assert entity.state == "on"
    # Same object indexing would give you - not a separate lookup/copy.
    assert entity is tree["/hue/light/kitchen_lights"]


async def test_find_raw_yields_show_shaped_dict_not_a_pair(tree: ApiObjTree):
    [raw] = list(tree.find(domain="light", platform="hue", area="Kitchen", raw=True))
    assert isinstance(raw, dict)
    assert raw == tree.show("/hue/light/kitchen_lights")
    assert raw["entity_id"] == "light.kitchen_lights"
    assert raw["state"] == "on"
    assert raw["name"] == "Kitchen Lights"


async def test_find_names_vs_find_paths_unaffected_by_raw(tree: ApiObjTree):
    # find_paths()/find_names() never pass raw through - they only need
    # .path/.entity_id, which stay simple strings either way.
    assert list(tree.find_names(area="Kitchen", platform="hue")) == [
        "light.kitchen_lights"
    ]
    assert list(tree.find_paths(area="Kitchen", platform="hue")) == [
        "/hue/light/kitchen_lights"
    ]


async def test_find_names_returns_entity_ids(tree: ApiObjTree):
    assert sorted(tree.find_names(platform="hue")) == [
        "light.kitchen_lights",
        "light.lounge_lamp",
    ]


async def test_find_names_vs_find_paths(tree: ApiObjTree):
    # Same filter, two different string forms of the same match.
    assert list(tree.find_names(area="Kitchen", platform="hue")) == [
        "light.kitchen_lights"
    ]
    assert list(tree.find_paths(area="Kitchen", platform="hue")) == [
        "/hue/light/kitchen_lights"
    ]


async def test_find_unknown_area_raises_immediately(tree: ApiObjTree):
    with pytest.raises(KeyError):
        tree.find(area="Nonexistent")  # must raise before iteration, not during


async def test_passing_an_entity_instead_of_a_path_raises_typeerror(tree: ApiObjTree):
    # Regression: tree.show(tree["/hue/light/kitchen_lights"]) used to crash
    # deep inside parse_path with a confusing AttributeError.
    entity = tree["/hue/light/kitchen_lights"]
    with pytest.raises(TypeError):
        tree.show(entity)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        # must raise immediately, not only once iterated
        tree.find(entity)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        tree[entity]  # type: ignore[index]  # ty: ignore[invalid-argument-type]


async def test_show_cleans_and_merges(tree: ApiObjTree):
    result = tree.show("/hue/light/kitchen_lights")
    assert result["state"] == "on"
    assert result["state_attributes"] == {"brightness": 200}
    # "name" is the live-equivalent display name (from friendly_name), not
    # the registry's own (here None) override field:
    assert result["name"] == "Kitchen Lights"
    assert result["platform"] == "hue"
    assert result["labels"] == ["lbl_important"]
    # None / empty list dropped, but an empty dict is kept (only None and
    # empty-collection-standing-in-for-a-set are dropped, same as live mode):
    assert "aliases" not in result
    assert result["options"] == {}
    # timestamp converted to a local ISO 8601 string, not left as a float:
    assert isinstance(result["created_at"], str)
    assert "T" in result["created_at"]


async def test_ordered_views_support_indexing(tree: ApiObjTree):
    keys = tree.keys()
    assert keys[0] == "/alexa_devices"
    assert keys[-1] == "/hue"
    assert list(reversed(keys)) == ["/hue", "/demo", "/alexa_devices"]


async def test_reset_cache_marks_stale(tree: ApiObjTree):
    assert not tree.cache.is_stale()
    tree.reset_cache()
    assert tree.cache.is_stale()


async def test_entity_reads_like_a_live_entity(tree: ApiObjTree):
    """The spellings that work on a real Entity work here too."""
    entity = tree["/hue/light/kitchen_lights"]
    assert isinstance(entity, ApiEntity)

    # platform: still the plain string, and the live EntityPlatform's attributes
    assert entity.platform == "hue"
    assert entity.platform.platform_name == "hue"
    assert entity.platform.domain == "light"
    assert json.dumps(entity.platform) == '"hue"'
    assert copy.deepcopy(entity.platform).domain == "light"

    # registry_entry: attribute access as on a RegistryEntry, and still a dict
    entry = entity.registry_entry
    assert entry.platform == entry["platform"] == "hue"
    assert entity.unique_id == entry.get("unique_id")
    with pytest.raises(AttributeError):
        entry.no_such_field  # noqa: B018

    assert entity.available is True
    assert entity.enabled is True
    assert entity.assumed_state is False
    assert entity.device_class == entity.state_attributes.get("device_class")
    assert entity.icon == entity.state_attributes.get("icon")


async def test_entity_with_no_state_is_not_available():
    cache = Cache(
        FakeClient({
            "get_states": [],
            "config/entity_registry/list": [
                {**ENTITY_REGISTRY[0], "disabled_by": "user"}
            ],
            "config/device_registry/list": DEVICE_REGISTRY,
            "config/area_registry/list": AREA_REGISTRY,
            "config/label_registry/list": LABEL_REGISTRY,
        }),
        ttl=30,
    )
    await cache.refresh()

    [entity] = cache.entities.values()

    assert entity.state is None
    assert entity.available is False
    assert entity.enabled is False
