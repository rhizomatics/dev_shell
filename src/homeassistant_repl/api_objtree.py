"""ApiObjTree - API client mode's read-only `obj[...]`, built entirely from
Home Assistant's standard websocket API (get_states, config/*_registry/list)
- no ha_repl_server component needed, works against any instance an admin
token can reach. Bound to the same name, `obj`, as the live tree (see
apirepl.py) so a snippet that only touches `obj` runs unchanged in either
mode. Entities only have what that API exposes: state + attributes from
get_states, and whatever the entity registry's own partial dict exposes (no
live component instance, so no calling methods on it, no fields an
integration keeps off the registry/state).

Mirrors custom_components/ha_repl_server/objtree.py's shape - __getitem__,
.keys()/.values()/.items(), find(), show() all behave the same way modulo the
smaller attribute set - but lives in this package rather than that one (a
HACS-deployed component with its own packaging boundary), so the handful of
small, stable, HA-import-free pieces it needs (the ordered-view mixin,
parse_path, the None/empty-collection cleanup) are duplicated here rather than
imported across that boundary. Revisit if API client mode sticks and a shared
package becomes worth the packaging work.

Unlike the live tree, this one is explicitly a *snapshot*: fetching is async
(it's a websocket call) but Mapping's `__getitem__`/`__iter__`/`__len__` are
not, so refreshing can't happen lazily on access without risking asyncio
reentrancy (this is normally used from inside an already-running event loop).
Instead the owning REPL loop decides when to call `await cache.refresh()`
(e.g. between prompts, when `cache.is_stale()`) - see apirepl.py. `reset_cache()`
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
from typing import Any, NamedTuple, Protocol


class _ApiClient(Protocol):
    """What Cache actually needs from a client - just enough to let tests use
    a lightweight fake instead of a real websocket Client."""

    async def call(self, type_: str, **payload: Any) -> Any: ...


def parse_path(path: str) -> tuple[str, ...]:
    """Split "/integration[/domain[/object_id]]" into 1-3 parts. Duplicated
    from custom_components/ha_repl_server/paths.py - see the module
    docstring for why."""
    parts = tuple(p for p in path.split("/") if p)
    if not parts or len(parts) > 3:
        raise ValueError(f"{path!r} - expected /integration/domain/object_id")
    return parts


def _as_set(value: str | list[str] | None) -> set[str]:
    if value is None:
        return set()
    return {value} if isinstance(value, str) else set(value)


def _require_str(value: Any, what: str = "path") -> str:
    """A clear TypeError at the boundary beats a confusing one from deep
    inside parse_path - duplicated from objtree.py's identical helper, see
    that module for the rationale."""
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str, not {type(value).__name__}")
    return value


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


class OrderedItemsView(_OrderedView, ItemsView, Sequence):  # type: ignore[misc]  # ty: ignore[invalid-method-override]
    # Sequence.__contains__(value) vs ItemsView.__contains__(item: tuple) is a
    # real static Liskov mismatch, but fine at runtime - both just delegate to
    # tuple(self).__contains__ via _OrderedView/Iterable, deliberately getting
    # both Set and Sequence behavior at once (see the module docstring).
    pass


@dataclass(frozen=True)
class ApiEntity:
    """One entity's worth of API client mode data - only what get_states and
    the entity registry's partial dict expose, not the live component instance.

    Named to match the real Entity's own properties where there's a direct
    equivalent - state, state_attributes, name - per
    https://developers.home-assistant.io/docs/core/entity/. `name` comes from
    get_states' `friendly_name` attribute: that's already HA's own fully
    resolved result of computing Entity.name (registry override, device name,
    has_entity_name, etc.), published into the state machine for exactly this
    kind of external consumer - cheaper and more faithful than recomputing it
    from the pieces ourselves. `state_attributes` is everything else in that
    attributes dict; broader than the live property of the same name (which
    excludes things like unit_of_measurement that come from separate Entity
    properties), since the state machine doesn't preserve that distinction.
    """

    entity_id: str
    platform: str
    domain: str
    object_id: str
    state: str | None
    name: str | None
    state_attributes: dict[str, Any]
    area_id: str | None
    labels: frozenset[str]
    registry: dict[str, Any]


class _Found(NamedTuple):
    """Internal only - never returned from find()/find_paths()/find_names(),
    just the shared (path, entity) pair each of them projects from
    (.entity, .path, and .entity_id respectively) so the filtering logic in
    _find() lives in exactly one place. Any attribute other than path/entity
    falls through to `entity`, so e.g. found.entity_id works without
    found.entity.entity_id."""

    path: str
    entity: ApiEntity

    def __getattr__(self, name: str) -> Any:
        return getattr(self.entity, name)


# The two registry JSON fields that are Unix-timestamp floats rather than the
# ISO datetime strings get_states already returns - see show()/_clean().
_TIMESTAMP_FIELDS = frozenset({"created_at", "modified_at"})


class Cache:
    """Fetches and holds one snapshot of states + registries. Refresh is
    driven by the REPL loop between commands - see the module docstring."""

    def __init__(self, client: _ApiClient, ttl: float) -> None:
        self.client = client
        self.ttl = ttl
        self._fetched_at: float | None = None
        self.entities: dict[str, ApiEntity] = {}
        self.areas: dict[str, dict[str, Any]] = {}
        self.labels: dict[str, dict[str, Any]] = {}

    def is_stale(self) -> bool:
        return (
            self._fetched_at is None or (time.monotonic() - self._fetched_at) > self.ttl
        )

    def reset(self) -> None:
        """Mark the cache stale. Doesn't fetch - see the module docstring."""
        self._fetched_at = None

    async def refresh(self) -> None:
        states = {s["entity_id"]: s for s in await self.client.call("get_states")}
        registry_entries = await self.client.call("config/entity_registry/list")
        devices = {
            d["id"]: d for d in await self.client.call("config/device_registry/list")
        }
        self.areas = {
            a["area_id"]: a for a in await self.client.call("config/area_registry/list")
        }
        self.labels = {
            l["label_id"]: l
            for l in await self.client.call("config/label_registry/list")
        }

        entities: dict[str, ApiEntity] = {}
        for entry in registry_entries:
            entity_id = entry["entity_id"]
            domain, object_id = entity_id.split(".", 1)
            state = states.get(entity_id, {})
            area_id = entry.get("area_id")
            if area_id is None and entry.get("device_id"):
                device = devices.get(entry["device_id"])
                if device is not None:
                    area_id = device.get("area_id")
            # friendly_name is already HA's own fully-resolved Entity.name,
            # published for exactly this kind of external consumer - see
            # ApiEntity's docstring. Copy the dict before popping: it's
            # `states[entity_id]`'s own attributes dict, not ours to mutate.
            attributes = dict(state.get("attributes", {}))
            name = attributes.pop("friendly_name", None)
            entities[entity_id] = ApiEntity(
                entity_id=entity_id,
                platform=entry["platform"],
                domain=domain,
                object_id=object_id,
                state=state.get("state"),
                name=name,
                state_attributes=attributes,
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
            (
                k
                for k, v in by_id.items()
                if v.get("name", "").casefold() == value.casefold()
            ),
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
class ApiObjTree(Mapping[str, "ApiEntity | ApiObjTree"]):
    """API client mode's view of the object tree - see the module docstring."""

    cache: Cache
    integration: str | None = None
    domain: str | None = None

    def reset_cache(self) -> None:
        self.cache.reset()

    def __getitem__(self, key: str) -> ApiEntity | ApiObjTree:
        _require_str(key, "key")
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
            subtree = ApiObjTree(self.cache, *full)
            if not subtree:
                raise KeyError(key)
            return subtree
        integration, domain, object_id = full
        entity = self.cache.entities.get(f"{domain}.{object_id}")
        if entity is None or entity.platform != integration:
            raise KeyError(key)
        return entity

    def _entries(self, prefix: tuple[str, ...]) -> Iterator[ApiEntity]:
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
        for child in sorted(children):
            yield "/" + child

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self) -> OrderedKeysView:
        return OrderedKeysView(self)

    def values(self) -> OrderedValuesView:
        return OrderedValuesView(self)

    def items(self) -> OrderedItemsView:
        return OrderedItemsView(self)

    def _find(
        self,
        path: str,
        *,
        platform: str | list[str] | None,
        domain: str | list[str] | None,
        area: str | list[str] | None,
        label: str | list[str] | None,
    ) -> Iterator[_Found]:
        """The real search, shared by find()/find_paths()/find_names() - each
        just projects a different field from the (path, entity) pairs this
        yields. `domain` matches the HA domain (e.g. "light") across
        integrations, the same way `platform` does for the owning
        integration; `area`/`label` each take an id or a display name. Each
        of the four ORs within itself when given a list, and they AND
        together. No order guarantee; wrap in sorted(...) if you want one.
        """
        _require_str(path)
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        try:
            prefix = scope if path in ("", "/") else scope + parse_path(path)
        except ValueError as err:
            raise KeyError(str(err)) from None
        if len(prefix) > 3:
            raise KeyError(path)

        platforms = _as_set(platform)
        domains = _as_set(domain)
        area_ids = _resolve_from_cache(area, self.cache.areas, "area", "area_id")
        label_ids = _resolve_from_cache(label, self.cache.labels, "label", "label_id")

        def _matches() -> Iterator[_Found]:
            for entity in self._entries(prefix):
                if platforms and entity.platform not in platforms:
                    continue
                if domains and entity.domain not in domains:
                    continue
                if area_ids and entity.area_id not in area_ids:
                    continue
                if label_ids and not (entity.labels & label_ids):
                    continue
                full_path = "/" + "/".join(
                    (entity.platform, entity.domain, entity.object_id)[len(scope) :]
                )
                yield _Found(full_path, entity)

        return _matches()

    def find(
        self,
        path: str = "/",
        *,
        platform: str | list[str] | None = None,
        domain: str | list[str] | None = None,
        area: str | list[str] | None = None,
        label: str | list[str] | None = None,
        raw: bool = False,
    ) -> Iterator[ApiEntity] | Iterator[dict[str, Any]]:
        """Every entity (or raw dict, if raw=True) matching the filters under
        `path` (relative to this view, "/" meaning this view's whole
        subtree) - flat, skipping the directory-style one-level-at-a-time
        grouping .keys()/indexing give you. find_paths()/find_names() are the
        same search with just the tree-path or entity_id strings, if that's
        all you want - see _find() for the filters.

        `raw=True` yields the object as received instead of the typed,
        renamed-to-match-Entity ApiEntity view - exactly what show(path)
        would return for that path (the merged, cleaned registry+state
        dict). The dict's own "entity_id" key identifies which entity it
        came from.
        """
        matches = self._find(
            path, platform=platform, domain=domain, area=area, label=label
        )
        if raw:
            return (self.show(found.path) for found in matches)
        return (found.entity for found in matches)

    def find_paths(
        self,
        path: str = "/",
        *,
        platform: str | list[str] | None = None,
        domain: str | list[str] | None = None,
        area: str | list[str] | None = None,
        label: str | list[str] | None = None,
    ) -> Iterator[str]:
        """Just the tree-path strings from find() (e.g.
        "/demo/light/kitchen_lights") - see _find() for the filters."""
        return (
            found.path
            for found in self._find(
                path, platform=platform, domain=domain, area=area, label=label
            )
        )

    def find_names(
        self,
        path: str = "/",
        *,
        platform: str | list[str] | None = None,
        domain: str | list[str] | None = None,
        area: str | list[str] | None = None,
        label: str | list[str] | None = None,
    ) -> Iterator[str]:
        """Just the HA entity_id strings from find() (e.g.
        "light.kitchen_lights") - see _find() for the filters. Not the same
        as find_paths(): this is the flat entity_id, not this tree's
        /integration/domain/object_id path."""
        return (
            found.entity_id
            for found in self._find(
                path, platform=platform, domain=domain, area=area, label=label
            )
        )

    def show(self, path: str) -> dict[str, Any]:
        """Same shape as the live tree's show() - the registry entry's own
        fields plus `state`/`state_attributes`, cleaned the same way (None/
        empty-collection dropped, timestamps as local ISO 8601)."""
        _require_str(path)
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
        data["name"] = entity.name
        data["state"] = entity.state
        data["state_attributes"] = entity.state_attributes
        return _clean(data)

    def __repr__(self) -> str:
        # "(api)" is just a display hint for a human reading output - the
        # binding name and API are identical to the live tree's on purpose.
        scope = "/".join(p for p in (self.integration, self.domain) if p is not None)
        label = f"obj:/{scope}" if scope else "obj"
        kind = (
            "entities"
            if self.domain is not None
            else "domains"
            if self.integration
            else "integrations"
        )
        return f"<{label}: {len(self)} {kind} (api)>"
