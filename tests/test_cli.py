"""Tests for print_result()'s arrow-rendering path - the client side of the
sql wire protocol: a result carrying base64 `arrow` bytes gets rendered as
a table locally, not printed as plain text (see custom_components/
ha_repl_server/session.py's ExecResult and ._maybe_arrow() for the server
side that produces it).
"""

from __future__ import annotations

import base64

from homeassistant_repl.cli import print_result
from homeassistant_repl.sql import SqlResult


def _arrow_bytes(**columns: list) -> bytes:
    import io

    import nanoarrow as na
    from nanoarrow.ipc import StreamWriter

    arrays = {
        name: na.array(
            values, schema=na.string() if isinstance(values[0], str) else na.int64()
        )
        for name, values in columns.items()
    }
    schema = na.struct({name: arr.schema for name, arr in arrays.items()})
    batch = na.c_array_from_buffers(
        schema,
        length=len(next(iter(arrays.values()))),
        buffers=[],
        children=list(arrays.values()),
    )
    buf = io.BytesIO()
    with StreamWriter.from_writable(buf) as writer:
        writer.write_stream(batch)
    return buf.getvalue()


def _base_result(**overrides) -> dict:
    base = {
        "stdout": "",
        "value": None,
        "arrow": None,
        "arrow_truncated": False,
        "error": None,
        "duration": 0.0,
        "truncated": False,
    }
    base.update(overrides)
    return base


def test_print_result_renders_arrow_as_a_table(capsys):
    data = _arrow_bytes(a=[1, 2], b=["x", "y"])
    result = _base_result(
        arrow=base64.b64encode(data).decode("ascii"),
        arrow_truncated=False,
        value="<SqlResult 2 rows x 2 cols [a, b]>",
    )

    print_result(result)

    out = capsys.readouterr().out
    assert "a" in out and "b" in out
    assert "1" in out and "x" in out
    # The arrow path renders a table, not the plain repr() fallback text.
    assert "<SqlResult" not in out


def test_print_result_without_arrow_prints_value_as_before(capsys):
    result = _base_result(value="42")

    print_result(result)

    assert capsys.readouterr().out == "42\n"


def test_print_result_passes_truncated_flag_through_to_client_result(
    capsys, monkeypatch
):
    seen = {}
    original = SqlResult.from_arrow

    def spy(data, *, truncated=False):
        seen["truncated"] = truncated
        return original(data, truncated=truncated)

    monkeypatch.setattr(SqlResult, "from_arrow", spy)

    data = _arrow_bytes(a=[1])
    result = _base_result(
        arrow=base64.b64encode(data).decode("ascii"), arrow_truncated=True
    )
    print_result(result)

    assert seen["truncated"] is True
