"""Tests for render.py - rebuilding what the server sends into something
rich can draw."""

from __future__ import annotations

from rich.pretty import pretty_repr

from homeassistant_repl.render import decode_value, remote_traceback


def test_decode_rebuilds_real_containers():
    tree = [
        1,
        {"t": "tuple", "v": [1, "a"]},
        {"t": "set", "v": [2]},
        {"t": "frozenset", "v": []},
        {"t": "dict", "v": [[{"t": "tuple", "v": [1, 2]}, None]]},
    ]

    assert decode_value(tree) == [1, (1, "a"), {2}, frozenset(), {(1, 2): None}]


def test_decoded_stand_ins_read_like_the_originals():
    tree = [
        {"t": "repr", "r": "<state sun.sun=above>"},
        {"t": "obj", "n": "Point", "f": [["x", 1], ["tag", {"t": "repr", "r": "<T>"}]]},
    ]

    value = decode_value(tree)

    assert repr(value) == "[<state sun.sun=above>, Point(x=1, tag=<T>)]"
    assert pretty_repr(value, max_width=20) == (
        "[\n    <state sun.sun=above>,\n    Point(\n        x=1,\n        tag=<T>\n    )\n]"
    )


def test_no_traceback_without_frames():
    assert (
        remote_traceback({"type": "E", "message": "m", "traceback": "E: m\n"}) is None
    )
