"""Run a read-only query against Home Assistant's own Recorder database
and hand the rows back as Arrow - reusing the recorder's already-configured
SQLAlchemy engine (so sqlite/postgresql/mysql all just work) rather than
opening a second connection or driver of our own.

That is all this side does: one SELECT in, row-capped Arrow IPC bytes out
(see websocket_api.py's ha_repl_server/sql). There is no result object
here to explore - the client package (homeassistant_repl.sql) rebuilds one
from the bytes, and rendering, slicing, dataframes and the rest live there,
in the developer's own process and environment. Every dependency here is
one the running Home Assistant instance pays for, hence nanoarrow
(https://arrow.apache.org/nanoarrow/, ~1MB and dependency-free) and not
pyarrow.

Column types come from Home Assistant's own declared recorder schema
(homeassistant.components.recorder.db_schema) wherever a result column's
name matches a real column there - no runtime value sniffing needed for the
handful of stable tables (events, states, statistics, ...) this is meant
for. A column whose name isn't recognised there (e.g. an aggregate like
COUNT(*)) falls back to a one-off peek at its own first non-null value, not
a sniff of every column.
"""

from __future__ import annotations

import functools
import inspect
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import nanoarrow as na
import sqlalchemy as sa
import sqlparse
from homeassistant.components.recorder import db_schema, get_instance
from homeassistant.components.recorder.core import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import session_scope
from nanoarrow.ipc import StreamWriter

DEFAULT_MAX_ROWS = 1000
_FETCH_CHUNK_SIZE = 200


class SqlError(Exception):
    """A rejected or failed query - not a bug in the shell itself."""


# Most specific first: SmallInteger/BigInteger are themselves Integer
# subclasses in SQLAlchemy, and Text is a String subclass - the first
# isinstance match wins, so the narrower types have to come first.
_ARROW_TYPE_BY_SQLA_TYPE: tuple[tuple[type, Any], ...] = (
    (sa.SmallInteger, na.int32()),
    (sa.BigInteger, na.int64()),
    (sa.Integer, na.int64()),
    (sa.Boolean, na.bool_()),
    (sa.Float, na.float64()),
    (sa.Numeric, na.float64()),
    (sa.LargeBinary, na.binary()),
    (sa.Text, na.string()),
    (sa.String, na.string()),
)


@functools.cache
def _recorder_arrow_types() -> dict[str, Any]:
    """{column name: nanoarrow type} for every current (non-deprecated)
    column across Home Assistant's own recorder tables - built once from
    homeassistant.components.recorder.db_schema, the actual SQLAlchemy
    models every other recorder-reading component already trusts, rather
    than a copy of our own that could drift from a future schema migration.

    A name that means different things on different tables (plain "state":
    text on States, a float on Statistics) is left out rather than guessed
    at - _build_column() falls back to a one-off peek at the query's own
    result for those.
    """
    by_name: dict[str, Any] = {}
    sqla_type_by_name: dict[str, type] = {}
    ambiguous: set[str] = set()
    for name, obj in vars(db_schema).items():
        # Legacy* models are old schema versions kept around for migrations,
        # not what a live query actually reads - including them just creates
        # spurious "ambiguous" exclusions for columns that are perfectly
        # unambiguous in the current schema (e.g. LegacyStatisticsMeta.id
        # was Integer; the current StatisticsMeta.id is BigInteger).
        if name.startswith("Legacy"):
            continue
        table = getattr(obj, "__table__", None)
        if table is None:
            continue
        for column in table.columns:
            if isinstance(column.type, (db_schema.Unused, db_schema.UnusedDateTime)):
                continue
            match = next(
                (
                    (sqla_type, arrow)
                    for sqla_type, arrow in _ARROW_TYPE_BY_SQLA_TYPE
                    if isinstance(column.type, sqla_type)
                ),
                None,
            )
            if match is None:
                continue
            sqla_type, arrow_type = match
            name = column.name
            if name in sqla_type_by_name and sqla_type_by_name[name] is not sqla_type:
                ambiguous.add(name)
                continue
            sqla_type_by_name[name] = sqla_type
            by_name[name] = arrow_type
    for name in ambiguous:
        by_name.pop(name, None)
    return by_name


def _coerce_for_arrow(value: Any) -> Any:
    """Recorder/DBAPI values nanoarrow's schema-explicit builder wouldn't
    otherwise accept as-is - everything else (including a plain int where a
    float column is declared, which SQLite's dynamic typing can hand back)
    is left alone; nanoarrow itself coerces that case fine.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _infer_arrow_type(values: list[Any]) -> Any:
    """A one-off peek at a single unrecognised column's own first non-null
    value (e.g. an aggregate like COUNT(*)) - not a sniff of every column,
    only the ones _recorder_arrow_types() doesn't already know."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return na.bool_()
        if isinstance(value, int):
            return na.int64()
        if isinstance(value, float):
            return na.float64()
        if isinstance(value, (bytes, bytearray)):
            return na.binary()
        break
    return na.string()


def _build_column(name: str, values: list[Any]) -> Any:
    arrow_type = _recorder_arrow_types().get(name) or _infer_arrow_type(values)
    converted = [_coerce_for_arrow(v) for v in values]
    try:
        return na.array(converted, schema=arrow_type)
    except ValueError, TypeError:
        # The declared/inferred type didn't actually fit this query's
        # values (e.g. a cast, or a dialect quirk) - a plain string column
        # always works, and a confusing value beats a failed query.
        return na.array(
            [None if v is None else str(v) for v in converted], schema=na.string()
        )


def _check_select_only(query: str) -> None:
    statements = [s for s in sqlparse.parse(query) if s.token_first(skip_cm=True)]
    if not statements:
        raise SqlError("empty query")
    if len(statements) > 1:
        raise SqlError("only a single statement is allowed")
    if statements[0].get_type() != "SELECT":
        raise SqlError("only SELECT queries are allowed")


def _fetch_rows(
    hass: HomeAssistant, query: str, max_rows: int | None
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """Runs on the recorder's own executor thread - see sql() below for why
    (SQLite, and the recorder's pooled connections generally, are
    thread-affine, so this can't run on the event loop or hass's own
    generic executor).
    """
    chunk_size = (
        _FETCH_CHUNK_SIZE
        if max_rows is None
        else max(1, min(max_rows, _FETCH_CHUNK_SIZE))
    )
    with session_scope(hass=hass, read_only=True) as session:
        result = session.execute(sa.text(query))
        columns = list(result.keys())
        rows: list[tuple[Any, ...]] = []
        truncated = False
        for row in result.yield_per(chunk_size):
            if max_rows is not None and len(rows) >= max_rows:
                truncated = True
                break
            rows.append(tuple(row))
        result.close()
        return columns, rows, truncated


@dataclass(frozen=True)
class ArrowResult:
    """One query's rows, already serialized: `data` is a single Arrow IPC
    stream holding one struct-typed batch, one named field per column.
    `truncated` isn't part of the Arrow data (it describes the query, not
    the rows), so it travels alongside.
    """

    data: bytes
    rowcount: int
    truncated: bool


async def sql(
    hass: HomeAssistant, query: str, *, max_rows: int | None = DEFAULT_MAX_ROWS
) -> ArrowResult:
    """Run a single read-only SQL SELECT against Home Assistant's Recorder
    database and return the rows as Arrow IPC bytes.

    Only one SELECT statement is allowed - the same restriction Home
    Assistant's own `sql` integration applies, since this runs against your
    live recorder database and a stray UPDATE/DELETE here is as real as one
    from any other integration. At most `max_rows` rows are fetched (default
    1000) - rows beyond it are never transferred from the database, not
    just discarded afterwards; None means no cap at all.
    """
    if max_rows is not None and max_rows < 1:
        raise SqlError("max_rows must be at least 1")
    _check_select_only(query)
    try:
        instance: Recorder = get_instance(hass)
    except KeyError:
        raise SqlError("the recorder is not set up on this instance") from None
    columns, rows, truncated = await instance.async_add_executor_job(
        _fetch_rows, hass, query, max_rows
    )
    column_values = list(zip(*rows, strict=True)) if rows else [() for _ in columns]
    arrays = {
        name: _build_column(name, list(values))
        for name, values in zip(columns, column_values, strict=True)
    }
    schema = na.struct({name: arr.schema for name, arr in arrays.items()})
    batch = na.c_array_from_buffers(
        schema, length=len(rows), buffers=[], children=list(arrays.values())
    )
    buf = io.BytesIO()
    with StreamWriter.from_writable(buf) as writer:
        writer.write_stream(batch)
    return ArrowResult(buf.getvalue(), len(rows), truncated)


@functools.cache
def table_schemas() -> list[dict[str, Any]]:
    """The recorder's own current (non-deprecated) tables and their columns,
    as plain data - from homeassistant.components.recorder.db_schema, the
    same source _recorder_arrow_types() reads above, not a live reflection
    of the connected database: these tables are schema-defined and the same
    for every instance of a given HA version. `class` and `doc` are the
    name and docstring of the db_schema class mapped to each table, and a
    column is `legacy` when db_schema declares it with one of its
    UNUSED_LEGACY_* types: still in the table, no longer written to.
    """
    unused = [v for k, v in vars(db_schema).items() if k.startswith("UNUSED_LEGACY")]
    classes: dict[str, type] = {}
    for name, obj in vars(db_schema).items():
        # only the mapped classes themselves, not an alias of one (OLD_STATE)
        if name.startswith("Legacy") or not isinstance(obj, type):
            continue
        table = getattr(obj, "__table__", None)
        if table is not None:
            classes[table.name] = obj
    return [
        {
            "name": name,
            "class": cls.__name__,
            "doc": inspect.cleandoc(cls.__doc__ or ""),
            "columns": [
                {
                    "name": column.name,
                    "type": str(column.type),
                    "legacy": any(column.type is u for u in unused),
                }
                for column in cls.__table__.c  # type:ignore[attr-defined] # ty: ignore[unresolved-attribute]
            ],
        }
        for name, cls in sorted(classes.items())
    ]
