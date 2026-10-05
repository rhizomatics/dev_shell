"""Tests for the client-side SqlResult (src/homeassistant_repl/sql.py) -
deliberately independent of custom_components/ha_repl_server/sql.py's own
tests (test_sql.py), since the two classes share nothing but the Arrow IPC
bytes one produces and the other reads - see sql.py's own module docstring.
"""

from __future__ import annotations

import base64
from pathlib import Path

import nanoarrow as na
import pytest
from rich.table import Table

from homeassistant_repl.client import HaReplError
from homeassistant_repl.sql import SqlColumn, SqlError, SqlResult, SqlTable, SqlTool


def _server_arrow_bytes(**columns: list) -> bytes:
    """Build the exact bytes custom_components/ha_repl_server/sql.py's own
    SqlResult.arrow() would produce for these columns, without importing
    that module (it needs a real `homeassistant` install) - the struct/IPC
    construction it uses internally, inlined here as the fixture.
    """
    import io

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


def _result(**columns: list) -> SqlResult:
    return SqlResult.from_arrow(_server_arrow_bytes(**columns))


def test_from_arrow_round_trips_columns_and_rowcount():
    data = _server_arrow_bytes(a=[1, 2, 3], b=["x", "y", "z"])

    result = SqlResult.from_arrow(data)

    assert result.rowcount == 3
    assert result.to_dicts() == [
        {"a": 1, "b": "x"},
        {"a": 2, "b": "y"},
        {"a": 3, "b": "z"},
    ]


def test_from_arrow_carries_truncated_out_of_band():
    data = _server_arrow_bytes(a=[1])

    assert SqlResult.from_arrow(data).truncated is False
    assert SqlResult.from_arrow(data, truncated=True).truncated is True


def test_arrow_round_trips_back_through_from_arrow():
    result = _result(a=[1, 2, 3])

    data = result.arrow()

    assert SqlResult.from_arrow(data).to_dicts() == result.to_dicts()


def test_iterates_rows_as_lists():
    result = _result(a=[1, 2, 3], b=["x", "y", "z"])

    assert list(result) == [[1, "x"], [2, "y"], [3, "z"]]


def test_len_reports_row_count():
    result = _result(a=[1, 2, 3])
    assert len(result) == 3


def test_project_returns_just_those_columns():
    result = _result(a=[1, 2, 3], b=["x", "y", "z"], c=[7, 8, 9])

    projected = result.project(["c", "a"])

    assert projected.column_names == ["c", "a"]
    assert projected.to_dicts() == [
        {"c": 7, "a": 1},
        {"c": 8, "a": 2},
        {"c": 9, "a": 3},
    ]


def test_slicing_returns_a_new_result():
    result = _result(a=list(range(10)))

    assert result[:3].to_dicts() == [{"a": 0}, {"a": 1}, {"a": 2}]
    assert result[-1:].to_dicts() == [{"a": 9}]
    assert result[:3].rowcount == 3


def test_getitem_rejects_a_plain_index():
    result = _result(a=[1, 2, 3])
    with pytest.raises(TypeError, match="slicing"):
        result[0]  # type: ignore[call-overload]  # ty: ignore[invalid-argument-type]


def test_sample_caps_at_available_rows_and_keeps_order():
    result = _result(a=list(range(100)))

    sampled = result.sample(10)
    assert len(sampled) == 10
    values = [row["a"] for row in sampled.to_dicts()]
    assert len(set(values)) == 10
    assert values == sorted(values)

    assert len(result.sample(1000)) == 100


def test_show_returns_a_rich_table_with_defaults():
    result = _result(**{f"c{i}": [1] for i in range(8)})

    table = result.show()

    assert isinstance(table, Table)
    assert [str(c.header) for c in table.columns] == [f"c{i}" for i in range(6)]
    assert "6/8 cols" in str(table.caption)


def test_show_max_rows_and_max_cols_override_defaults():
    result = _result(a=list(range(40)))

    table = result.show(max_rows=5)
    assert len(list(table.columns[0].cells)) == 5
    assert "showing first 5 rows" in str(table.caption)

    table = result.show(max_rows=None)
    assert len(list(table.columns[0].cells)) == 40


def test_export_csv_writes_header_and_rows(tmp_path):
    result = _result(a=[1, 2], b=["x", "y"])
    path = tmp_path / "out.csv"

    written = result.export_csv(path)

    assert written == path
    assert path.read_bytes() == b"a,b\r\n1,x\r\n2,y\r\n"


def test_export_csv_defaults_to_result_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _result(a=[1])

    path = result.export_csv()

    assert path == Path("result.csv")
    assert path.is_file()


def test_to_polars_builds_a_dataframe():
    pl = pytest.importorskip("polars")
    result = _result(a=[1, 2, 3])

    df = result.to_polars()

    assert isinstance(df, pl.DataFrame)
    assert df["a"].to_list() == [1, 2, 3]


def test_to_pandas_builds_a_dataframe():
    pd = pytest.importorskip("pandas")
    result = _result(a=[1, 2, 3])

    df = result.to_pandas()

    assert isinstance(df, pd.DataFrame)
    assert list(df["a"]) == [1, 2, 3]


class _FakeClient:
    """Stands in for the websocket Client: canned replies per command."""

    def __init__(self, arrow: bytes = b"", error: Exception | None = None) -> None:
        self.arrow = arrow
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def call(self, type_: str, **payload):
        self.calls.append((type_, payload))
        if type_ == "ha_repl_server/sql_tables":
            return {
                "tables": [
                    {
                        "name": "t",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "name", "type": "VARCHAR(255)"},
                        ],
                    },
                    {"name": "other", "columns": [{"name": "zzz", "type": "TEXT"}]},
                ]
            }
        if self.error is not None:
            raise self.error
        return {
            "arrow": base64.b64encode(self.arrow).decode("ascii"),
            "truncated": True,
        }


async def test_sqltool_connect_downloads_tables():
    tool = await SqlTool.connect(_FakeClient())

    assert [t.name for t in tool.tables] == ["t", "other"]
    assert tool.tables[0].column_names() == ["id", "name"]
    assert repr(tool.tables[0].columns()[1]) == "Column('name', VARCHAR(255))"
    assert repr(tool.tables[0]) == "Table('t', columns=['id', 'name'])"


async def test_sqltool_call_returns_a_local_result_with_its_table():
    client = _FakeClient(_server_arrow_bytes(id=[1, 2], name=["a", "b"]))
    tool = await SqlTool.connect(client)

    result = await tool("select * from t")

    assert isinstance(result, SqlResult)
    assert list(result) == [[1, "a"], [2, "b"]]
    assert result.truncated is True
    assert result.table is tool.tables[0]
    assert result[:1].table is tool.tables[0]
    assert client.calls[-1] == (
        "ha_repl_server/sql",
        {"query": "select * from t", "max_rows": 1000},
    )


async def test_sqltool_max_rows_default_and_per_call_override():
    client = _FakeClient(_server_arrow_bytes(id=[1]))
    tool = await SqlTool.connect(client)

    tool.max_rows = 5
    await tool("select 1")
    await tool("select 1", max_rows=None)

    assert [c[1]["max_rows"] for c in client.calls[1:]] == [5, None]


async def test_sqltool_turns_a_server_sql_error_into_sqlerror():
    error = HaReplError(
        "ha_repl_server/sql failed: only SELECT queries are allowed",
        code="sql_error",
        detail="only SELECT queries are allowed",
    )
    tool = await SqlTool.connect(_FakeClient(error=error))

    with pytest.raises(SqlError, match=r"^only SELECT queries are allowed$"):
        await tool("delete from t")


async def test_sqltool_leaves_other_failures_alone():
    tool = await SqlTool.connect(_FakeClient(error=HaReplError("Connection closed")))

    with pytest.raises(HaReplError, match="Connection closed"):
        await tool("select 1")


def test_export_csv_default_path_is_named_after_the_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    table = SqlTable("states", (SqlColumn("id", "INTEGER"),))
    result = SqlResult.from_arrow(_server_arrow_bytes(id=[1]), tables=[table])

    assert result.export_csv().name == "states.csv"


def test_bare_result_renders_as_its_table():
    result = SqlResult.from_arrow(_server_arrow_bytes(id=[1]))

    assert result.__rich__().row_count == 1
