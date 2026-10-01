"""Parsing for the object tree's "/integration/domain/object_id" paths.

Deliberately free of Home Assistant imports so it can be tested standalone,
same as session.py.
"""

from __future__ import annotations


def parse_path(path: str) -> tuple[str, str, str]:
    """Split "/integration/domain/object_id" into its three parts.

    Raises ValueError (with a message fit to show the user directly) if path
    isn't exactly three non-empty, slash-separated segments.
    """
    parts = [p for p in path.split("/") if p]
    if len(parts) != 3:
        raise ValueError(f"{path!r} - expected /integration/domain/object_id")
    integration, domain, object_id = parts
    return integration, domain, object_id
