"""Tests for sql() against a real (in-memory) recorder - via
pytest-homeassistant-custom-component's `recorder_mock` fixture, since this
needs Home Assistant's own SQLAlchemy engine/schema, not a fake."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from homeassistant.components.recorder import get_instance
from homeassistant.core import HomeAssistant

from custom_components.ha_repl_server.sql import (
    SqlError,
    SqlResult,
    SqlTable,
    SqlTool,
    sql,
)


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
    assert result.rowcount == 1
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


async def test_sql_max_rows_truncates_and_flags_it(recorder_mock, hass: HomeAssistant):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    result = await sql(hass, "select entity_id from states_meta", max_rows=2)
    assert result.rowcount == 2
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


async def test_sql_rejects_bad_max_rows(recorder_mock, hass: HomeAssistant):
    with pytest.raises(SqlError, match="max_rows"):
        await sql(hass, "select 1", max_rows=0)


async def test_sql_to_dicts(recorder_mock, hass: HomeAssistant):
    # to_pandas()/to_polars() live only on the client-side SqlResult now -
    # see tests/test_sql_client.py - this server-side one stops at to_dicts().
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(hass, "select entity_id from states_meta")
    assert result.to_dicts() == [{"entity_id": "sensor.test"}]


async def test_sql_unknown_column_falls_back_to_value_inference(
    recorder_mock, hass: HomeAssistant
):
    result = await sql(hass, "select count(*) as n from states_meta")
    assert result.to_dicts()[0]["n"] == 0
    assert str(result.columns["n"].schema) == "<Schema> int64"


async def test_sqltool_uses_its_own_max_rows_as_the_default(
    recorder_mock, hass: HomeAssistant
):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, max_rows=2)
    result = await tool("select entity_id from states_meta")
    assert result.rowcount == 2
    assert result.truncated is True


async def test_sqltool_max_rows_none_removes_the_cap(
    recorder_mock, hass: HomeAssistant
):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, max_rows=2)
    tool.max_rows = None
    result = await tool("select entity_id from states_meta")
    assert result.rowcount == 5
    assert result.truncated is False


async def test_sqltool_per_call_max_rows_overrides_but_does_not_stick(
    recorder_mock, hass: HomeAssistant
):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    tool = SqlTool(hass, max_rows=2)
    result = await tool("select entity_id from states_meta", max_rows=None)
    assert result.rowcount == 5
    # The override was for that one call only - the tool's own default
    # still applies to the next one.
    result = await tool("select entity_id from states_meta")
    assert result.rowcount == 2


async def test_sqltool_tables_lists_known_recorder_tables(hass: HomeAssistant):
    tool = SqlTool(hass)
    names = [t.name for t in tool.tables]
    assert "states" in names
    assert "events" in names
    assert all(isinstance(t, SqlTable) for t in tool.tables)
    assert not any(name.lower().startswith("legacy") for name in names)


async def test_sqltable_column_names_and_columns(hass: HomeAssistant):
    tool = SqlTool(hass)
    states = next(t for t in tool.tables if t.name == "states")

    assert "entity_id" in states.column_names()
    assert all(isinstance(c, sa.Column) for c in states.columns())
    assert [c.name for c in states.columns()] == states.column_names()


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

    rowcount = len(next(iter(columns.values())))
    arrays = {
        name: na.array(
            values, schema=na.string() if isinstance(values[0], str) else na.int64()
        )
        for name, values in columns.items()
    }
    return SqlResult(arrays, rowcount, truncated=False)


def test_sql_result_project_returns_just_those_columns():
    result = _result(a=[1, 2, 3], b=["x", "y", "z"], c=[7, 8, 9])

    projected = result.project(["c", "a"])
    assert projected.column_names == ["c", "a"]
    assert projected.rowcount == 3
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
    assert result[:3].rowcount == 3
    assert result[-1:].rowcount == 1


def test_sql_result_getitem_rejects_a_plain_index():
    result = _result(a=[1, 2, 3])
    with pytest.raises(TypeError, match="slicing"):
        result[0]  # type: ignore


def test_sql_result_len_reports_rowcount():
    result = _result(a=[1, 2, 3])
    assert len(result) == 3
    assert len(result) == result.rowcount


def test_sql_result_sample_returns_the_requested_count_in_original_order():
    result = _result(a=list(range(100)))

    sampled = result.sample(10)

    assert len(sampled) == 10
    values = [row["a"] for row in sampled.to_dicts()]
    assert len(set(values)) == 10
    assert values == sorted(values)


def test_sql_result_sample_caps_at_the_available_rows():
    result = _result(a=[1, 2, 3])

    sampled = result.sample(20)

    assert len(sampled) == 3
    assert sorted(row["a"] for row in sampled.to_dicts()) == [1, 2, 3]


def test_sql_result_iterates_rows_as_lists():
    result = _result(a=[1, 2, 3], b=["x", "y", "z"])

    rows = list(result)

    assert rows == [[1, "x"], [2, "y"], [3, "z"]]
    assert all(type(row) is list for row in rows)


def test_sql_result_iteration_does_not_go_through_getitem():
    # __iter__ must be used directly - falling back to the old
    # __getitem__(0), __getitem__(1), ... protocol would hit the
    # slice-only TypeError guard on the very first row.
    result = _result(a=[1, 2, 3])
    assert list(result) == [[1], [2], [3]]


def test_sql_result_arrow_round_trips_through_ipc():
    import nanoarrow as na

    result = _result(a=[1, 2, 3], b=["x", "y", "z"])

    data = result.arrow()

    assert isinstance(data, bytes)
    roundtrip = na.ArrayStream.from_readable(data).read_all()
    assert roundtrip.to_pylist() == [
        {"a": 1, "b": "x"},
        {"a": 2, "b": "y"},
        {"a": 3, "b": "z"},
    ]
