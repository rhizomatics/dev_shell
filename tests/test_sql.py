"""Tests for sql() against a real (in-memory) recorder - via
pytest-homeassistant-custom-component's `recorder_mock` fixture, since this
needs Home Assistant's own SQLAlchemy engine/schema, not a fake."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from homeassistant.components.recorder import get_instance
from homeassistant.core import HomeAssistant
from rich.table import Table

from custom_components.ha_repl_server.sql import SqlError, SqlResult, SqlTool, sql


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    await get_instance(hass).async_block_till_done()


async def test_sql_select_returns_arrow_backed_result(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    # states.entity_id itself is a deprecated/unused column in the current
    # schema (the real value lives in states_meta, joined by metadata_id) -
    # a join like this, not a bare `select entity_id from states`, is what a
    # real query against this schema actually looks like.
    result = await sql(
        hass,
        "select states_meta.entity_id, states.state from states "
        "join states_meta on states.metadata_id = states_meta.metadata_id",
    )

    assert isinstance(result, SqlResult)
    assert result.row_count == 1
    assert not result.truncated
    assert result.to_dicts() == [{"entity_id": "sensor.test", "state": "42"}]


async def test_sql_column_types_from_recorder_schema(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(hass, "select metadata_id from states_meta")
    # metadata_id is BigInteger in the current schema - should be a real
    # int64 column, not a generic/string fallback.
    assert str(result.columns["metadata_id"].schema) == "<Schema> int64"


async def test_sql_limit_truncates_and_flags_it(recorder_mock, hass: HomeAssistant):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    result = await sql(hass, "select entity_id from states_meta", limit=2)
    assert result.row_count == 2
    assert result.truncated is True


async def test_sql_rejects_non_select(recorder_mock, hass: HomeAssistant):
    with pytest.raises(SqlError, match="only SELECT"):
        await sql(hass, "delete from states")


async def test_sql_rejects_multiple_statements(recorder_mock, hass: HomeAssistant):
    with pytest.raises(SqlError, match="single statement"):
        await sql(hass, "select 1; select 2")


async def test_sql_rejects_empty_query(recorder_mock, hass: HomeAssistant):
    with pytest.raises(SqlError, match="empty"):
        await sql(hass, "   ")


async def test_sql_rejects_bad_limit(recorder_mock, hass: HomeAssistant):
    with pytest.raises(SqlError, match="limit"):
        await sql(hass, "select 1", limit=0)


async def test_sql_to_dicts_to_pandas_to_polars(recorder_mock, hass: HomeAssistant):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(hass, "select entity_id from states_meta")
    assert result.to_dicts() == [{"entity_id": "sensor.test"}]

    pd = pytest.importorskip("pandas")
    pdf = result.to_pandas()
    assert list(pdf["entity_id"]) == ["sensor.test"]
    assert isinstance(pdf, pd.DataFrame)

    pl = pytest.importorskip("polars")
    pldf = result.to_polars()
    assert pldf["entity_id"].to_list() == ["sensor.test"]
    assert isinstance(pldf, pl.DataFrame)


async def test_sql_unknown_column_falls_back_to_value_inference(
    recorder_mock, hass: HomeAssistant
):
    result = await sql(hass, "select count(*) as n from states_meta")
    assert result.to_dicts()[0]["n"] == 0
    assert str(result.columns["n"].schema) == "<Schema> int64"


async def test_sqltool_uses_its_own_limit_as_the_default(
    recorder_mock, hass: HomeAssistant
):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, limit=2)
    result = await tool("select entity_id from states_meta")
    assert result.row_count == 2
    assert result.truncated is True


async def test_sqltool_limit_none_removes_the_cap(recorder_mock, hass: HomeAssistant):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, limit=2)
    tool.limit = None
    result = await tool("select entity_id from states_meta")
    assert result.row_count == 5
    assert result.truncated is False


async def test_sqltool_per_call_limit_overrides_but_does_not_stick(
    recorder_mock, hass: HomeAssistant
):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, limit=2)
    result = await tool("select entity_id from states_meta", limit=None)
    assert result.row_count == 5
    # The override was for that one call only - the tool's own default
    # still applies to the next one.
    result = await tool("select entity_id from states_meta")
    assert result.row_count == 2


async def test_sql_result_show_returns_a_rich_table(recorder_mock, hass: HomeAssistant):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(
        hass,
        "select states_meta.entity_id, states.state from states "
        "join states_meta on states.metadata_id = states_meta.metadata_id",
    )
    table = result.show()

    assert isinstance(table, Table)
    assert [str(c.header) for c in table.columns] == ["entity_id", "state"]
    assert [list(c.cells) for c in table.columns] == [["sensor.test"], ["42"]]


async def test_sql_result_show_notes_truncation_in_caption():
    import nanoarrow as na

    result = SqlResult({"n": na.array([1], schema=na.int64())}, 1, truncated=True)
    assert "truncated" in str(result.show().caption)


async def test_sqltool_tables_lists_known_recorder_tables(hass: HomeAssistant):
    tool = SqlTool(hass)
    names = [t.name for t in tool.tables]
    assert "states" in names
    assert "events" in names
    assert all(isinstance(t, sa.Table) for t in tool.tables)
    assert not any(name.lower().startswith("legacy") for name in names)


async def test_sql_result_column_names_lists_just_the_names(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(
        hass,
        "select states_meta.entity_id, states.state from states "
        "join states_meta on states.metadata_id = states_meta.metadata_id",
    )
    assert result.column_names == ["entity_id", "state"]


async def test_sql_result_table_is_the_same_object_as_sqltool_tables(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    tool = SqlTool(hass)
    result = await tool(
        "select states_meta.entity_id, states.state from states "
        "join states_meta on states.metadata_id = states_meta.metadata_id"
    )
    states_table = next(t for t in tool.tables if t.name == "states")
    assert result.table is states_table


async def test_sql_result_table_is_none_when_ambiguous_or_unmatched(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    # "entity_id" alone is shared by more than one recorder table.
    ambiguous = await sql(hass, "select entity_id from states_meta")
    assert ambiguous.table is None

    # An aggregate alias isn't a real column on any table.
    aggregate = await sql(hass, "select count(*) as n from states_meta")
    assert aggregate.table is None


async def test_sql_result_show_defaults_to_first_six_columns():
    import nanoarrow as na

    columns = {f"c{i}": na.array([1], schema=na.int64()) for i in range(8)}
    result = SqlResult(columns, 1, truncated=False)

    table = result.show()
    assert [str(c.header) for c in table.columns] == [f"c{i}" for i in range(6)]
    assert "6/8 cols" in str(table.caption)


async def test_sql_result_show_columns_kwarg_overrides_the_default():
    import nanoarrow as na

    columns = {f"c{i}": na.array([1], schema=na.int64()) for i in range(8)}
    result = SqlResult(columns, 1, truncated=False)

    table = result.show(columns=["c7", "c0"])
    assert [str(c.header) for c in table.columns] == ["c7", "c0"]
    assert "cols" not in str(table.caption)


async def test_sql_result_show_defaults_to_first_thirty_rows():
    import nanoarrow as na

    column = na.array(list(range(40)), schema=na.int64())
    result = SqlResult({"n": column}, 40, truncated=False)

    table = result.show()
    assert len(list(table.columns[0].cells)) == 30
    assert "showing first 30 rows" in str(table.caption)


async def test_sql_result_show_max_rows_overrides_the_default():
    import nanoarrow as na

    column = na.array(list(range(40)), schema=na.int64())
    result = SqlResult({"n": column}, 40, truncated=False)

    table = result.show(max_rows=None)
    assert len(list(table.columns[0].cells)) == 40
    assert "showing" not in str(table.caption)

    table = result.show(max_rows=5)
    assert len(list(table.columns[0].cells)) == 5
    assert "showing first 5 rows" in str(table.caption)


async def test_sql_result_show_max_cols_overrides_the_default():
    import nanoarrow as na

    columns = {f"c{i}": na.array([1], schema=na.int64()) for i in range(8)}
    result = SqlResult(columns, 1, truncated=False)

    table = result.show(max_cols=None)
    assert [str(c.header) for c in table.columns] == [f"c{i}" for i in range(8)]
    assert "cols" not in str(table.caption)

    table = result.show(max_cols=3)
    assert [str(c.header) for c in table.columns] == ["c0", "c1", "c2"]
    assert "3/8 cols" in str(table.caption)


def test_table_repr_shows_just_name_and_columns():
    m = sa.MetaData()
    t = sa.Table("states", m, sa.Column("state_id", sa.BigInteger, primary_key=True))
    assert repr(t) == "Table('states', columns=['state_id'])"


def test_column_repr_omits_the_table():
    m = sa.MetaData()
    t = sa.Table("states", m, sa.Column("state_id", sa.BigInteger, primary_key=True))
    assert "table=" not in repr(t.c.state_id)
    assert (
        repr(t.c.state_id)
        == "Column('state_id', BigInteger(), primary_key=True, nullable=False)"
    )


def _result(**columns: list[Any]) -> SqlResult:
    import nanoarrow as na

    row_count = len(next(iter(columns.values())))
    arrays = {
        name: na.array(
            values, schema=na.string() if isinstance(values[0], str) else na.int64()
        )
        for name, values in columns.items()
    }
    return SqlResult(arrays, row_count, truncated=False)


def test_sql_result_project_returns_just_those_columns():
    result = _result(a=[1, 2, 3], b=["x", "y", "z"], c=[7, 8, 9])

    projected = result.project(["c", "a"])
    assert projected.column_names == ["c", "a"]
    assert projected.row_count == 3
    assert projected.to_dicts() == [
        {"c": 7, "a": 1},
        {"c": 8, "a": 2},
        {"c": 9, "a": 3},
    ]


def test_sql_result_slicing_returns_a_new_result():
    result = _result(a=list(range(10)))

    assert result[:3].to_dicts() == [{"a": 0}, {"a": 1}, {"a": 2}]
    assert result[-1:].to_dicts() == [{"a": 9}]
    assert result[3:6].to_dicts() == [{"a": 3}, {"a": 4}, {"a": 5}]
    assert result[:3].row_count == 3
    assert result[-1:].row_count == 1


def test_sql_result_getitem_rejects_a_plain_index():
    result = _result(a=[1, 2, 3])
    with pytest.raises(TypeError, match="slicing"):
        result[0]  # type: ignore


def test_sql_result_export_csv_writes_header_and_rows(tmp_path):
    result = _result(a=[1, 2], b=["x", "y"])
    path = tmp_path / "out.csv"

    result.export_csv(path)

    assert path.read_text() == "a,b\r\n1,x\r\n2,y\r\n"


def test_sql_result_export_csv_passes_kwargs_to_csv_writer(tmp_path):
    result = _result(a=[1, 2], b=["x", "y"])
    path = tmp_path / "out.csv"

    result.export_csv(path, delimiter="|")

    assert path.read_text() == "a|b\r\n1|x\r\n2|y\r\n"


def test_sql_result_export_csv_returns_the_path_written(tmp_path):
    result = _result(a=[1])
    path = tmp_path / "out.csv"

    assert result.export_csv(path) == path


async def test_sql_result_export_csv_defaults_to_table_name_csv(
    recorder_mock, hass: HomeAssistant, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(hass, "select entity_id, metadata_id from states_meta")
    path = result.export_csv()

    assert path == Path("states_meta.csv")
    assert path.read_text() == "entity_id,metadata_id\r\nsensor.test,1\r\n"


def test_sql_result_export_csv_defaults_to_result_csv_when_table_is_ambiguous(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    result = _result(n=[1])

    path = result.export_csv()

    assert path == Path("result.csv")
