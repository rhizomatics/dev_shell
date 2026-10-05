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

`.keys()`/iteration mirror that: `alexa.keys()` lists just the domains directly
under `/alexa_devices` (one level, like `ls` on a directory), not every entity
underneath it recursively. `obj.find()` is the flat alternative - every
(path, entity) pair under a subtree, optionally filtered by
platform/domain/area/label; `obj.find_paths()` is the same search with just
the tree path strings (e.g. "/demo/light/kitchen_lights"), and
`obj.find_names()` with just the HA entity_id strings (e.g.
"light.kitchen_lights") instead. `obj.show(path)` renders one entity's
registry entry plus its live state.

Both return the live Entity object - the actual LightEntity/SensorEntity/etc.
instance a component wrote, not the hass.states.get() State snapshot - since this
is strict mode: you use it exactly as you would from component code.

`obj.mode("api")` switches that: entity lookups/find() return the same
read-only, dict-shaped ApiEntity data API client mode's `obj` would give you
(built locally from the live entity/registry, not a second round-trip
through the API) instead of the live Entity object - e.g. to preview how a
snippet will behave once ported to API client mode, or just to avoid live
side effects. It's a session-wide switch, not a one-off: it persists on
every view sharing this `obj`'s root until `obj.mode("live")` is called.
`obj.mode()` with no argument returns the current mode.

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

from collections.abc import (
    Callable,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    Sequence,
    ValuesView,
)
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import DATA_DOMAIN_ENTITIES
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util

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


def _as_set(value: str | list[str] | None) -> set[str]:
    if value is None:
        return set()
    return {value} if isinstance(value, str) else set(value)


def _require_str(value: Any, what: str = "path") -> str:
    """A clear TypeError at the boundary beats a confusing one from deep
    inside parse_path (e.g. passing an Entity instead of its path - an easy
    slip, since indexing returns one and it's natural to then pass it back
    in somewhere a path string was wanted)."""
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str, not {type(value).__name__}")
    return value


@dataclass(frozen=True)
class ApiEntity:
    """One entity's worth of `obj.mode("api")` data - the same read-only
    subset of fields API client mode's own ApiEntity exposes (see
    homeassistant_repl.api_objtree.ApiEntity), built here from the already-
    live Entity/registry entry directly rather than a second round-trip
    through the API. Not the live component instance: no calling methods on
    it, no fields an integration keeps off the registry/state.
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


@dataclass
class _Mode:
    """Mutable, shared by reference across every ObjTree view derived from
    the same root - a plain field on the (frozen, otherwise immutable)
    ObjTree dataclass would only flip that one view, not "the rest of the
    session" the way obj.mode(...) promises."""

    api: bool = False


class _Found(NamedTuple):
    """Internal only - never returned from find()/find_paths()/find_names(),
    just the shared (path, entity) pair each of them projects from
    (.entity, .path, and .entity_id respectively) so the filtering logic in
    _find() lives in exactly one place. Any attribute other than path/entity
    falls through to `entity`, so e.g. found.entity_id works without
    found.entity.entity_id."""

    path: str
    entity: Entity | ApiEntity

    def __getattr__(self, name: str) -> Any:
        return getattr(self.entity, name)


def _resolve_ids(
    values: str | list[str] | None,
    kind: str,
    by_id: Callable[[str], Any],
    by_name: Callable[[str], Any],
    id_attr: str,
) -> set[str]:
    """Resolve each of `values` (an id or a display name) to its canonical id.

    Raises KeyError on anything that matches neither - a typo'd area/label
    name should fail loudly, not just quietly match nothing in find().
    """
    ids: set[str] = set()
    for value in _as_set(values):
        found = by_id(value) or by_name(value)
        if found is None:
            raise KeyError(f"no such {kind}: {value!r}")
        ids.add(getattr(found, id_attr))
    return ids


def _entity_area_id(entry: er.RegistryEntry, devices: dr.DeviceRegistry) -> str | None:
    """The entry's own area, falling back to its device's - the same rule the
    frontend uses to decide which area an entity "is in"."""
    if entry.area_id is not None:
        return entry.area_id
    if entry.device_id is not None:
        device = devices.async_get(entry.device_id)
        if device is not None:
            return device.area_id
    return None


def _public_attrs(obj: Any) -> dict[str, Any]:
    """Every attrs-declared field on `obj`, minus _cache and anything else
    marked private by the leading-underscore convention attrs itself uses for
    fields like RegistryEntry._cache."""
    return {
        a.name: getattr(obj, a.name)
        for a in getattr(obj, "__attrs_attrs__", ())
        if not a.name.startswith("_")
    }


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return dt_util.as_local(value).isoformat()
    if isinstance(value, Mapping):
        return _clean(dict(value))
    return value


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively drop None/empty-set values and render datetimes as local
    ISO 8601 strings - applied throughout, not just at the top level, since
    things like state_attributes are themselves dicts that can hold either."""
    cleaned = {}
    for key, value in data.items():
        value = _normalize(value)
        if value is None or value == set():
            continue
        cleaned[key] = value
    return cleaned


class _OrderedView(Iterable[Any]):
    """Adds positional access/slicing on top of a MappingView's existing order.

    ObjTree.__iter__ already yields children in a meaningful (alphanumeric)
    order, so the views built on it can afford to be real Sequences too - not
    just the usual Collection (ValuesView) or Set (KeysView/ItemsView) - without
    giving up set arithmetic or `isinstance(x, KeysView)` duck-typing: this is a
    mixin, not a replacement, so a class using it keeps its MappingView base's
    behaviour and only adds `__getitem__`/`__reversed__` on top.

    Inherits Iterable (abstract, no `__iter__` of its own) only so type checkers
    know `self` supports `tuple(self)` here - the concrete classes below supply
    the real `__iter__` via their MappingView base, same as at runtime.
    """

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
    # both Set and Sequence behavior at once.
    pass


@dataclass(frozen=True)
class ObjTree(Mapping[str, "Entity | ApiEntity | ObjTree"]):
    """A view of the object tree, optionally restricted to a subtree.

    `integration` and/or `domain` pin this view to that part of the tree -
    the way `obj["/alexa_devices"]` or `obj["/alexa_devices/media_player"]`
    does. Indexing a restricted view only needs the remaining path segments,
    given either as a single "a/b" string or one segment at a time.

    Implementing collections.abc.Mapping (on top of __getitem__, __iter__ and
    __len__) gets `in`, .get(), and real KeysView/ItemsView/ValuesView from
    .keys()/.items()/.values() for free, consistent with any other dict-like
    object - overridden below to also be Sequences, since every level in this
    tree is naturally a list (see _OrderedView): `obj["/alexa_devices"].keys()[0]`
    and slicing work, alongside the usual Set/Collection behaviour.
    """

    hass: HomeAssistant
    integration: str | None = None
    domain: str | None = None
    # Shared by reference with every view derived from this one (see _Mode's
    # docstring) - not compared/hashed, since it's a toggle, not identity.
    _mode: _Mode = field(default_factory=_Mode, compare=False)

    def mode(self, value: str | None = None) -> str | None:
        """Switch between "live" (the default - indexing/find() return the
        real, live Entity, exactly as component code sees it) and "api"
        (the same read-only, dict-shaped ApiEntity data API client mode's
        `obj` would give you) - see the module docstring. Persists on every
        view sharing this obj's root until mode() is called again; with no
        argument, returns the current mode instead of changing it.
        """
        if value is None:
            return "api" if self._mode.api else "live"
        if value not in ("live", "api"):
            raise ValueError(f"mode must be 'live' or 'api', not {value!r}")
        self._mode.api = value == "api"
        return None

    def __getitem__(self, key: str) -> Entity | ApiEntity | ObjTree:
        _require_str(key, "key")
        if "/" not in key and self.integration is None:
            if self._mode.api:
                # No integration to check against here (that's the whole
                # point of a bare entity_id) - just need entry+entity, not
                # _api_entity_at's platform match.
                entry = er.async_get(self.hass).async_get(key)
                entity = _get_entity(self.hass, key) if entry is not None else None
                if entry is None or entity is None:
                    raise KeyError(key)
                domain, _, object_id = key.partition(".")
                return self._to_api_entity(entry, domain, object_id, entity)
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
            # Not `ObjTree(self.hass, *full, _mode=...)`: mypy can't tell a
            # variable-length tuple unpack won't collide with a later keyword.
            padded = full + (None,) * (2 - len(full))
            subtree = ObjTree(self.hass, padded[0], padded[1], _mode=self._mode)
            # Without this, `in`/.get() (Mapping's default __contains__ tries
            # self[key]) would say yes to any made-up integration/domain name,
            # disagreeing with .keys() - which only ever lists ones with entities.
            if not subtree:
                raise KeyError(key)
            return subtree
        if self._mode.api:
            api_entity = self._api_entity_at(*full)
            if api_entity is None:
                raise KeyError(key)
            return api_entity
        entity = self._entity_at(*full)
        if entity is None:
            raise KeyError(key)
        return entity

    def _entity_at(
        self, integration: str, domain: str, object_id: str
    ) -> Entity | None:
        entity_id = f"{domain}.{object_id}"
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != integration:
            return None
        return _get_entity(self.hass, entity_id)

    def _api_entity_at(
        self, integration: str, domain: str, object_id: str
    ) -> ApiEntity | None:
        entity_id = f"{domain}.{object_id}"
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != integration:
            return None
        entity = _get_entity(self.hass, entity_id)
        if entity is None:
            return None
        return self._to_api_entity(entry, domain, object_id, entity)

    def _to_api_entity(
        self, entry: er.RegistryEntry, domain: str, object_id: str, entity: Entity
    ) -> ApiEntity:
        devices = dr.async_get(self.hass)
        name = entity.name
        return ApiEntity(
            entity_id=entry.entity_id,
            platform=entry.platform,
            domain=domain,
            object_id=object_id,
            # Mirrors what /api/states actually publishes: both come back as
            # plain strings there, not the live property's broader type
            # (state: StateType, name: str | UndefinedType | None).
            state=None if entity.state is None else str(entity.state),
            name=None if name is UNDEFINED else name,
            state_attributes=dict(entity.state_attributes or {}),
            area_id=_entity_area_id(entry, devices),
            labels=frozenset(entry.labels),
            registry=_public_attrs(entry),
        )

    def _entries(
        self, prefix: tuple[str, ...]
    ) -> Iterator[tuple[er.RegistryEntry, str, str]]:
        """Every (registry entry, domain, object_id) whose (platform, domain,
        object_id) matches `prefix` position by position - `prefix` may have
        0-3 elements, a narrower prefix just matching fewer positions. The
        shared scan both __iter__ (one level, deduped) and find() (full depth,
        flat) are built on.
        """
        registry = er.async_get(self.hass)
        domain_entities = self.hass.data.get(DATA_DOMAIN_ENTITIES, {})
        for domain, entities in domain_entities.items():
            if len(prefix) >= 2 and domain != prefix[1]:
                continue
            for entity_id in entities:
                entry = registry.async_get(entity_id)
                if entry is None:
                    continue
                if len(prefix) >= 1 and entry.platform != prefix[0]:
                    continue
                object_id = entity_id.split(".", 1)[1]
                if len(prefix) >= 3 and object_id != prefix[2]:
                    continue
                yield entry, domain, object_id

    def __iter__(self) -> Iterator[str]:
        """The immediate child names one level down, alphanumeric order - a
        directory listing, not a recursive flattening down to every entity
        (that's find(), a different, explicitly flat view, not this one)."""
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        children = {
            (entry.platform, domain, object_id)[len(scope)]
            for entry, domain, object_id in self._entries(scope)
        }
        yield from sorted(children)

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
        yields. Skips anything with no live Entity object backing it - same
        requirement as indexing - so an entity id from here always works if
        passed back into this view.

        `platform` matches the registry entry's platform (owning integration)
        directly; `domain` matches the HA domain (e.g. "light"), letting you
        search across integrations without fixing `path` to one; `area`/`label`
        each take an id or a display name, and an entity's area falls back to
        its device's when it has none of its own - the same rule the frontend
        uses. Each of the four ORs within itself when given a list, and they
        AND together. No order guarantee; wrap in sorted(...) if you want one.
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
        areas = ar.async_get(self.hass)
        labels = lr.async_get(self.hass)
        area_ids = _resolve_ids(
            area, "area", areas.async_get_area, areas.async_get_area_by_name, "id"
        )
        label_ids = _resolve_ids(
            label,
            "label",
            labels.async_get_label,
            labels.async_get_label_by_name,
            "label_id",
        )
        devices = dr.async_get(self.hass)

        def _matches() -> Iterator[_Found]:
            for entry, dom, object_id in self._entries(prefix):
                if platforms and entry.platform not in platforms:
                    continue
                if domains and dom not in domains:
                    continue
                if area_ids and _entity_area_id(entry, devices) not in area_ids:
                    continue
                if label_ids and not (entry.labels & label_ids):
                    continue
                entity = _get_entity(self.hass, entry.entity_id)
                if entity is None:
                    continue
                full_path = "/" + "/".join(
                    (entry.platform, dom, object_id)[len(scope) :]
                )
                found_entity = (
                    self._to_api_entity(entry, dom, object_id, entity)
                    if self._mode.api
                    else entity
                )
                yield _Found(full_path, found_entity)

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
    ) -> Iterator[Entity | ApiEntity] | Iterator[dict[str, Any]]:
        """Every entity (or raw dict, if raw=True) matching the filters under
        `path` (relative to this view, "/" meaning this view's whole
        subtree) - flat, skipping the directory-style one-level-at-a-time
        grouping .keys()/indexing give you. find_paths()/find_names() are the
        same search with just the tree-path or entity_id strings, if that's
        all you want - see _find() for the filters.

        `raw=True` yields the object as received instead of the live Entity -
        exactly what show(path) would return for that path (the registry
        entry's fields plus state/state_attributes, cleaned the same way).
        The dict's own "entity_id" key identifies which entity it came from.
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
        """The registry entry's own fields (minus _cache and other data this
        hides by the leading-underscore convention) plus the live entity's
        `state` and `state_attributes` - meant to be the trailing expression
        at the REPL, so the usual Pretty-printed echo renders it; this doesn't
        print anything itself. Datetimes come back as local ISO 8601 strings,
        and None/empty-set values are dropped throughout, including inside
        nested dicts like state_attributes.
        """
        _require_str(path)
        scope = tuple(p for p in (self.integration, self.domain) if p is not None)
        try:
            full = scope + parse_path(path)
        except ValueError as err:
            raise KeyError(str(err)) from None
        if len(full) != 3:
            raise KeyError(f"{path!r} - show() needs a full entity path")
        integration, domain, object_id = full
        entity = self._entity_at(integration, domain, object_id)
        if entity is None:
            raise KeyError(path)

        entry = er.async_get(self.hass).async_get(f"{domain}.{object_id}")
        data: dict[str, Any] = _public_attrs(entry) if entry is not None else {}
        # Overrides the registry's own (often-None, user-override-only) "name"
        # field with the live, fully-resolved display name - the direct
        # equivalent per https://developers.home-assistant.io/docs/core/entity/.
        data["name"] = entity.name
        data["state"] = entity.state
        data["state_attributes"] = entity.state_attributes
        return _clean(data)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self) -> OrderedKeysView:
        return OrderedKeysView(self)

    def values(self) -> OrderedValuesView:
        return OrderedValuesView(self)

    def items(self) -> OrderedItemsView:
        return OrderedItemsView(self)

    def __repr__(self) -> str:
        scope = "/".join(p for p in (self.integration, self.domain) if p is not None)
        label = f"obj:/{scope}" if scope else "obj"
        kind = (
            "entities"
            if self.domain is not None
            else "domains"
            if self.integration
            else "integrations"
        )
        return f"<{label}: {len(self)} {kind}>"
