"""Tests for the object tree's path parsing, run without Home Assistant."""

import importlib.util
import sys
from pathlib import Path

import pytest

_path = (
    Path(__file__).parent.parent / "custom_components" / "ha_repl_server" / "paths.py"
)
_spec = importlib.util.spec_from_file_location("ha_repl_paths", _path)
assert _spec is not None and _spec.loader is not None
paths_mod = importlib.util.module_from_spec(_spec)
sys.modules["ha_repl_paths"] = paths_mod
_spec.loader.exec_module(paths_mod)


def test_parses_three_segments():
    assert paths_mod.parse_path("/rflink/binary_sensor/hall_pir") == (
        "rflink",
        "binary_sensor",
        "hall_pir",
    )


def test_leading_slash_not_required():
    assert paths_mod.parse_path("demo/light/kitchen") == ("demo", "light", "kitchen")


def test_trailing_slash_ignored():
    assert paths_mod.parse_path("/demo/light/kitchen/") == ("demo", "light", "kitchen")


def test_partial_paths_are_valid():
    assert paths_mod.parse_path("/demo") == ("demo",)
    assert paths_mod.parse_path("demo/light") == ("demo", "light")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "demo/light/kitchen/extra",
    ],
)
def test_wrong_segment_count_raises(path):
    with pytest.raises(ValueError, match="integration/domain/object_id"):
        paths_mod.parse_path(path)
