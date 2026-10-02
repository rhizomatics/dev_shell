"""`obj[...]` - a live, read-only lookup over Home Assistant's entities.

Entities only for now; the full integration/domain/area/label tree from the
README's Object Browser design is a later step. Lookup forms:

    obj["light.kitchen_lights"]            # plain entity_id
    obj["/demo/light/kitchen_lights"]      # /integration/domain/object_id

A path that doesn't reach all the way to an object_id instead returns an
ObjTree restricted to that part of the tree, so the object browser's
directories can be navigated a level at a time:

    alexa = obj["/alexa_devices"]          # restricted to that integration
    alexa["media_player"]                  # -> restricted to that domain too
    alexa["media_player/kitchen_show"]     # -> the live entity

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
    """A view of the object tree, optionally restricted to a subtree.

    `integration` and/or `domain` pin this view to that part of the tree -
    the way `obj["/alexa_devices"]` or `obj["/alexa_devices/media_player"]`
    does. Indexing a restricted view only needs the remaining path segments,
    given either as a single "a/b" string or one segment at a time.
    """

    hass: HomeAssistant
    integration: str | None = None
    domain: str | None = None

    def __getitem__(self, key: str) -> Entity | ObjTree:
        if "/" not in key and self.integration is None:
            entity = _get_entity(self.hass, key)
            if entity is None:
                raise KeyError(key)
            return entity
        try:
            parts = parse_path(key)
        except ValueError as err:
            raise KeyError(str(err)) from None
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        full = scope + parts
        if len(full) > 3:
            raise KeyError(key)
        if len(full) < 3:
            return ObjTree(self.hass, *full)
        entity = self._entity_at(*full)
        if entity is None:
            raise KeyError(key)
        return entity

    def _entity_at(self, integration: str, domain: str, object_id: str) -> Entity | None:
        entity_id = f"{domain}.{object_id}"
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != integration:
            return None
        return _get_entity(self.hass, entity_id)

    def __iter__(self) -> Iterator[str]:
        scope_len = sum(p is not None for p in (self.integration, self.domain))
        registry = er.async_get(self.hass)
        domain_entities = self.hass.data.get(DATA_DOMAIN_ENTITIES, {})
        for domain, entities in domain_entities.items():
            if self.domain is not None and domain != self.domain:
                continue
            for entity_id in entities:
                entry = registry.async_get(entity_id)
                if entry is None:
                    continue
                if self.integration is not None and entry.platform != self.integration:
                    continue
                object_id = entity_id.split(".", 1)[1]
                remaining = (entry.platform, domain, object_id)[scope_len:]
                yield "/" + "/".join(remaining)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        scope = "/".join(p for p in (self.integration, self.domain) if p is not None)
        label = f"obj:/{scope}" if scope else "obj"
        return f"<{label}: {len(self)} entities>"

    def keys(self) -> Iterator[str]:
        return iter(self)

    def values(self) -> Iterator[Entity]:
        return (self[key] for key in self)
