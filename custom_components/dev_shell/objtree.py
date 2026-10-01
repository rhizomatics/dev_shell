"""`obj[...]` - a live, read-only lookup over Home Assistant's entities.

Entities only for now; the full integration/domain/area/label tree from the
README's Object Browser design is a later step. Two lookup forms:

    obj["light.kitchen_lights"]            # plain entity_id
    obj["/demo/light/kitchen_lights"]      # /integration/domain/object_id

Both return the live Entity object - the actual LightEntity/SensorEntity/etc.
instance a component wrote, not the hass.states.get() State snapshot - since this
is strict mode: you use it exactly as you would from component code.

Only entities in the entity registry are reachable by the /integration/... path,
since that's where the owning integration ("platform") is recorded; legacy YAML
entities without a unique_id aren't registered there. Both lookup forms require a
live Entity object backing the entity - true for everything set up the normal way
through an EntityPlatform (YAML or config-entry), but not for a bare state set
directly via hass.states.async_set() with no Entity subclass behind it (rare
outside of tests/templates). See the README's Object Browser enhancements for the
registry-less follow-up.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import DATA_DOMAIN_ENTITIES

from .paths import parse_path


def _get_entity(hass: HomeAssistant, entity_id: str) -> Entity | None:
    """The live Entity instance behind entity_id, or None.

    hass.data[DATA_DOMAIN_ENTITIES] is maintained by EntityPlatform itself (for
    every platform, YAML or config-entry) as {domain: {entity_id: Entity}} - the
    one lookup that's both centralized and independent of which integration or
    setup style owns the entity. It's a private implementation detail of
    homeassistant.helpers.entity_platform, not public API, so this is written
    defensively (plain dict .get chains) rather than assuming its shape.
    """
    domain = entity_id.partition(".")[0]
    return hass.data.get(DATA_DOMAIN_ENTITIES, {}).get(domain, {}).get(entity_id)


@dataclass(frozen=True)
class ObjTree:
    hass: HomeAssistant

    def __getitem__(self, key: str) -> Entity:
        if "/" in key:
            try:
                entity = self._by_path(key)
            except ValueError as err:
                raise KeyError(str(err)) from None
        else:
            entity = _get_entity(self.hass, key)
        if entity is None:
            raise KeyError(key)
        return entity

    def _by_path(self, path: str) -> Entity | None:
        integration, domain, object_id = parse_path(path)
        entity_id = f"{domain}.{object_id}"
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != integration:
            return None
        return _get_entity(self.hass, entity_id)

    def __iter__(self) -> Iterator[str]:
        registry = er.async_get(self.hass)
        domain_entities = self.hass.data.get(DATA_DOMAIN_ENTITIES, {})
        for domain, entities in domain_entities.items():
            for entity_id in entities:
                entry = registry.async_get(entity_id)
                if entry is None:
                    continue
                object_id = entity_id.split(".", 1)[1]
                yield f"/{entry.platform}/{domain}/{object_id}"

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        return f"<obj: {len(self)} entities>"

    def keys(self) -> Iterator[str]:
        return iter(self)

    def values(self) -> Iterator[Entity]:
        return (self[key] for key in self)
