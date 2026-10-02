"""`objs[...]` - safe mode's read-only mirror of `obj[]`, built entirely from
Home Assistant's standard websocket API (get_states, config/*_registry/list) -
no dev_shell_server component needed, works against any instance an admin
token can reach. Entities only have what that API exposes: state + attributes
from get_states, and whatever the entity registry's own partial dict exposes
(no live component instance, so no calling methods on it, no fields an
integration keeps off the registry/state).

Mirrors custom_components/dev_shell_server/objtree.py's shape - __getitem__,
.keys()/.values()/.items(), find(), show() all behave the same way modulo the
smaller attribute set - but lives in this package rather than that one (a
HACS-deployed component with its own packaging boundary), so the handful of
small, stable, HA-import-free pieces it needs (the ordered-view mixin,
parse_path, the None/empty-collection cleanup) are duplicated here rather than
imported across that boundary. Revisit if safe mode sticks and a shared
package becomes worth the packaging work.

Unlike the live tree, this one is explicitly a *snapshot*: fetching is async
(it's a websocket call) but Mapping's `__getitem__`/`__iter__`/`__len__` are
not, so refreshing can't happen lazily on access without risking asyncio
reentrancy (this is normally used from inside an already-running event loop).
Instead the owning REPL loop decides when to call `await cache.refresh()`
(e.g. between prompts, when `cache.is_stale()`) - see saferepl.py. `reset_cache()`
just marks the cache stale; it does not fetch.
"""

from __future__ import annotations

import time
from collections.abc import (
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    Sequence,
    ValuesView,
)
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .client import Client


def parse_path(path: str) -> tuple[str, ...]:
    """Split "/integration[/domain[/object_id]]" into 1-3 parts. Duplicated
    from custom_components/dev_shell_server/paths.py - see the module
    docstring for why."""
    parts = tuple(p for p in path.split("/") if p)
    if not parts or len(parts) > 3:
        raise ValueError(f"{path!r} - expected /integration/domain/object_id")
    return parts


def _as_set(value: str | list[str] | None) -> set[str]:
    if value is None:
        return set()
    return {value} if isinstance(value, str) else set(value)


class _OrderedView(Iterable[Any]):
    """Positional access/slicing on top of a MappingView's existing order -
    duplicated from objtree.py's identical mixin, see that module for the
    rationale."""

    def __getitem__(self, index: int | slice) -> Any:
        return tuple(self)[index]

    def __reversed__(self) -> Iterator[Any]:
        return reversed(tuple(self))


class OrderedKeysView(_OrderedView, KeysView, Sequence):
    pass


class OrderedValuesView(_OrderedView, ValuesView, Sequence):
    pass


class OrderedItemsView(_OrderedView, ItemsView, Sequence):
    pass


@dataclass(frozen=True)
class SafeEntity:
    """One entity's worth of safe-mode data - only what get_states and the
    entity registry's partial dict expose, not the live component instance."""

    entity_id: str
    platform: str
    domain: str
    object_id: str
    state: str | None
    attributes: dict[str, Any]
    area_id: str | None
    labels: frozenset[str]
    registry: dict[str, Any]


# The two registry JSON fields that are Unix-timestamp floats rather than the
# ISO datetime strings get_states already returns - see show()/_clean().
_TIMESTAMP_FIELDS = frozenset({"created_at", "modified_at"})


class Cache:
    """Fetches and holds one snapshot of states + registries. Refresh is
    driven by the REPL loop between commands - see the module docstring."""

    def __init__(self, client: Client, ttl: float) -> None:
        self.client = client
        self.ttl = ttl
        self._fetched_at: float | None = None
        self.entities: dict[str, SafeEntity] = {}
        self.areas: dict[str, dict[str, Any]] = {}
        self.labels: dict[str, dict[str, Any]] = {}

    def is_stale(self) -> bool:
        return self._fetched_at is None or (time.monotonic() - self._fetched_at) > self.ttl

    def reset(self) -> None:
        """Mark the cache stale. Doesn't fetch - see the module docstring."""
        self._fetched_at = None

    async def refresh(self) -> None:
        states = {s["entity_id"]: s for s in await self.client.call("get_states")}
        registry_entries = await self.client.call("config/entity_registry/list")
        devices = {d["id"]: d for d in await self.client.call("config/device_registry/list")}
        self.areas = {a["area_id"]: a for a in await self.client.call("config/area_registry/list")}
        self.labels = {l["label_id"]: l for l in await self.client.call("config/label_registry/list")}

        entities: dict[str, SafeEntity] = {}
        for entry in registry_entries:
            entity_id = entry["entity_id"]
            domain, object_id = entity_id.split(".", 1)
            state = states.get(entity_id, {})
            area_id = entry.get("area_id")
            if area_id is None and entry.get("device_id"):
                device = devices.get(entry["device_id"])
                if device is not None:
                    area_id = device.get("area_id")
            entities[entity_id] = SafeEntity(
                entity_id=entity_id,
                platform=entry["platform"],
                domain=domain,
                object_id=object_id,
                state=state.get("state"),
                attributes=state.get("attributes", {}),
                area_id=area_id,
                labels=frozenset(entry.get("labels", ())),
                registry=entry,
            )
        self.entities = entities
        self._fetched_at = time.monotonic()


def _resolve_from_cache(
    values: str | list[str] | None,
    by_id: dict[str, dict[str, Any]],
    kind: str,
    id_key: str,
) -> set[str]:
    """Resolve each of `values` (an id or a display name) to its canonical id.

    Raises KeyError on anything that matches neither - a typo'd area/label
    name should fail loudly, not just quietly match nothing in find().
    """
    ids: set[str] = set()
    for value in _as_set(values):
        if value in by_id:
            ids.add(value)
            continue
        match = next(
            (k for k, v in by_id.items() if v.get("name", "").casefold() == value.casefold()),
            None,
        )
        if match is None:
            raise KeyError(f"no such {kind}: {value!r}")
        ids.add(match)
    return ids


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return _clean(value)
    return value


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively drop None/empty-collection values and render the known
    timestamp fields as local ISO 8601 strings.

    Empty *lists* are dropped here too, not just empty sets: labels/aliases
    etc. arrive over the wire as JSON lists (JSON has no set type), so a list
    is this mode's wire-equivalent of the live tree's empty-set fields - and
    keeping both modes behaving the same way is the point.
    """
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key in _TIMESTAMP_FIELDS and isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value).astimezone().isoformat()
        else:
            value = _normalize(value)
        if value is None or value == set() or value == []:
            continue
        cleaned[key] = value
    return cleaned


@dataclass(frozen=True)
class SafeObjTree(Mapping[str, "SafeEntity | SafeObjTree"]):
    """Safe mode's view of the object tree - see the module docstring."""

    cache: Cache
    integration: str | None = None
    domain: str | None = None

    def reset_cache(self) -> None:
        self.cache.reset()

    def __getitem__(self, key: str) -> SafeEntity | SafeObjTree:
        if "/" not in key and self.integration is None:
            entity = self.cache.entities.get(key)
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
            subtree = SafeObjTree(self.cache, *full)
            if not subtree:
                raise KeyError(key)
            return subtree
        integration, domain, object_id = full
        entity = self.cache.entities.get(f"{domain}.{object_id}")
        if entity is None or entity.platform != integration:
            raise KeyError(key)
        return entity

    def _entries(self, prefix: tuple[str, ...]) -> Iterator[SafeEntity]:
        for entity in self.cache.entities.values():
            if len(prefix) >= 1 and entity.platform != prefix[0]:
                continue
            if len(prefix) >= 2 and entity.domain != prefix[1]:
                continue
            if len(prefix) >= 3 and entity.object_id != prefix[2]:
                continue
            yield entity

    def __iter__(self) -> Iterator[str]:
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        children = {
            (entity.platform, entity.domain, entity.object_id)[len(scope)]
            for entity in self._entries(scope)
        }
        yield from sorted(children)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self) -> OrderedKeysView:
        return OrderedKeysView(self)

    def values(self) -> OrderedValuesView:
        return OrderedValuesView(self)

    def items(self) -> OrderedItemsView:
        return OrderedItemsView(self)

    def find(
        self,
        path: str = "/",
        *,
        platform: str | list[str] | None = None,
        area: str | list[str] | None = None,
        label: str | list[str] | None = None,
    ) -> Iterator[str]:
        """Same shape as the live tree's find() - see that docstring. No order
        guarantee here either; wrap in sorted(...) if you want one."""
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        try:
            prefix = scope if path in ("", "/") else scope + parse_path(path)
        except ValueError as err:
            raise KeyError(str(err)) from None
        if len(prefix) > 3:
            raise KeyError(path)

        platforms = _as_set(platform)
        area_ids = _resolve_from_cache(area, self.cache.areas, "area", "area_id")
        label_ids = _resolve_from_cache(label, self.cache.labels, "label", "label_id")

        def _matches() -> Iterator[str]:
            for entity in self._entries(prefix):
                if platforms and entity.platform not in platforms:
                    continue
                if area_ids and entity.area_id not in area_ids:
                    continue
                if label_ids and not (entity.labels & label_ids):
                    continue
                yield "/" + "/".join(
                    (entity.platform, entity.domain, entity.object_id)[len(scope) :]
                )

        return _matches()

    def show(self, path: str) -> dict[str, Any]:
        """Same shape as the live tree's show() - the registry entry's own
        fields plus `state`/`state_attributes`, cleaned the same way (None/
        empty-collection dropped, timestamps as local ISO 8601)."""
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        try:
            full = scope + parse_path(path)
        except ValueError as err:
            raise KeyError(str(err)) from None
        if len(full) != 3:
            raise KeyError(f"{path!r} - show() needs a full entity path")
        integration, domain, object_id = full
        entity = self.cache.entities.get(f"{domain}.{object_id}")
        if entity is None or entity.platform != integration:
            raise KeyError(path)

        data: dict[str, Any] = dict(entity.registry)
        data["state"] = entity.state
        data["state_attributes"] = entity.attributes
        return _clean(data)

    def __repr__(self) -> str:
        scope = "/".join(p for p in (self.integration, self.domain) if p is not None)
        label = f"objs:/{scope}" if scope else "objs"
        kind = "entities" if self.domain is not None else "domains" if self.integration else "integrations"
        return f"<{label}: {len(self)} {kind}>"
