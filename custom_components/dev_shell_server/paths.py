"""Parsing for the object tree's "/integration/domain/object_id" paths.

Deliberately free of Home Assistant imports so it can be tested standalone,
same as session.py.
"""

from __future__ import annotations


def parse_path(path: str) -> tuple[str, ...]:
    """Split "/integration[/domain[/object_id]]" into 1-3 parts.

    A path may name just an integration, an integration and domain, or go all
    the way down to an object_id - each is a valid, progressively narrower
    position in the object tree. Raises ValueError (with a message fit to show
    the user directly) if path has no segments or more than three.
    """
    parts = tuple(p for p in path.split("/") if p)
    if not parts or len(parts) > 3:
        raise ValueError(f"{path!r} - expected /integration/domain/object_id")
    return parts
