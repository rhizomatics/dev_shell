"""Tests for the server side of sql - one query in, Arrow IPC bytes out -
against a real (in-memory) recorder via pytest-homeassistant-custom-
component's `recorder_mock` fixture, since this needs Home Assistant's own
SQLAlchemy engine/schema, not a fake. What a client does with those bytes
is tests/test_sql_client.py's business."""

from __future__ import annotations

from typing import Any

import nanoarrow as na
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ha_repl_server.sql import (
    ArrowResult,
    SqlError,
    sql,
    table_schemas,
)


async def _settle(hass: HomeAssistant) -> None:
    # The harness's own "everything is written" helper: it forces a commit
    # between waits, which a bare block_till_done on each side doesn't - a
    # state set just before could otherwise still be uncommitted when the
    # query runs.
    await async_wait_recording_done(hass)


def _batch(result: ArrowResult) -> Any:
    return na.ArrayStream.from_readable(result.data).read_all()


async def test_sql_select_returns_rows_as_arrow_ipc(recorder_mock, hass: HomeAssistant):
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

    assert isinstance(result, ArrowResult)
    assert result.rowcount == 1
    assert not result.truncated
    assert _batch(result).to_pylist() == [{"entity_id": "sensor.test", "state": "42"}]


async def test_sql_column_types_from_recorder_schema(
    recorder_mock, hass: HomeAssistant
):
    hass.states.async_set("sensor.test", "42")
    await _settle(hass)

    result = await sql(hass, "select metadata_id from states_meta")
    # metadata_id is BigInteger in the current schema - should be a real
    # int64 column, not a generic/string fallback.
    (field,) = _batch(result).schema.fields
    assert (field.name, str(field.type)) == ("metadata_id", "Type.INT64")


async def test_sql_unknown_column_falls_back_to_value_inference(
    recorder_mock, hass: HomeAssistant
):
    result = await sql(hass, "select count(*) as n from states_meta")

    batch = _batch(result)
    assert batch.to_pylist() == [{"n": 0}]
    assert str(batch.schema.fields[0].type) == "Type.INT64"


async def test_sql_with_no_rows_still_carries_the_columns(
    recorder_mock, hass: HomeAssistant
):
    result = await sql(hass, "select entity_id from states_meta where 1 = 0")

    assert result.rowcount == 0
    assert [f.name for f in _batch(result).schema.fields] == ["entity_id"]


async def test_sql_max_rows_truncates_and_flags_it(recorder_mock, hass: HomeAssistant):
    for i in range(5):
        hass.states.async_set(f"sensor.test_{i}", "on")
    await _settle(hass)

    result = await sql(hass, "select entity_id from states_meta", max_rows=2)
    assert result.rowcount == 2
    assert result.truncated is True
    assert len(_batch(result)) == 2


async def test_sql_max_rows_none_removes_the_cap(recorder_mock, hass: HomeAssistant):
    result = await sql(
        hass, "select 1 as a union all select 2 union all select 3", max_rows=None
    )
    assert result.rowcount == 3
    assert result.truncated is False


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


def test_table_schemas_lists_current_recorder_tables_with_columns():
    tables = {t["name"]: t["columns"] for t in table_schemas()}

    assert {"states", "states_meta", "events", "statistics"} <= set(tables)
    assert {
        "name": "entity_id",
        "type": "VARCHAR(255)",
        "legacy": False,
    } in tables["states_meta"]
    assert {"name": "entity_id", "type": "CHAR", "legacy": True} in tables["states"]
    assert list(tables) == sorted(tables)
    described = {t["name"]: (t["class"], t["doc"]) for t in table_schemas()}
    assert described["states"] == ("States", "State change history.")
