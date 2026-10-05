"""`sql` - run a read-only query against Home Assistant's own Recorder
database, reusing its already-configured SQLAlchemy engine (so sqlite/
postgresql/mysql all just work, the same adaptation every other HA component
gets for free) rather than opening a second connection or driver of our own.

Live mode only: this needs the real `hass`/recorder instance running in this
process - there's no REST/websocket equivalent reachable from API client mode.
The session-global `sql` is a SqlTool instance (one per session, see
PerSession in session.py): call it like a function (`sql("select ...")`),
inspect/change its default row cap via `sql.max_rows` (None removes it), or
look at `sql.tables` for the recorder's current tables without a query.

This SqlResult is deliberately the minimal, server-side half of the type:
the query's rows as nanoarrow arrays (https://arrow.apache.org/nanoarrow/) -
a ~1MB, dependency-free Arrow implementation, not the ~100MB pyarrow - plus
`.arrow()` to serialize them as a single Arrow IPC stream. Rendering
(`.show()`) and dataframe conversion (`.to_pandas()`/`.to_polars()`) live
only on the separate, full-featured SqlResult in homeassistant_repl (the
CLI/client package) that reconstructs from those bytes - see that module's
own docstring for why: every dependency here is a dependency the running
Home Assistant instance pays for, not just the developer typing the query,
so rich/pandas/polars have no business being manifest.json requirements of
this component when the client can freely install them instead. Column
types come from Home Assistant's own declared recorder schema
(homeassistant.components.recorder.db_schema) wherever a result column's
name matches a real column there - no runtime value sniffing needed for the
handful of stable tables (events, states, statistics, ...) this is meant
for. A column whose name isn't recognised there (e.g. an aggregate like
COUNT(*)) falls back to a one-off peek at its own first non-null value, not
a sniff of every column.
"""

from __future__ import annotations

import functools
import io
import random
import re
from collections.abc import Iterator
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


def _table_repr(table: sa.Table) -> str:
    """Just the name and column names - SQLAlchemy's own Table.__repr__
    spells out every one of its Columns' own (equally verbose) repr, which
    is unreadable at the REPL for anything but a toy schema.
    """
    return f"Table({table.name!r}, columns={table.c.keys()!r})"


_ORIGINAL_COLUMN_REPR = sa.Column.__repr__
_COLUMN_REPR_TABLE_KWARG = re.compile(r",\s*table=<[^>]*>")


def _column_repr(column: sa.Column) -> str:
    """SQLAlchemy's own Column.__repr__ includes `table=<name>` - pointless
    once you're already looking at one column on its own (you got it from a
    Table you already know, e.g. sql.tables[i].c.foo); everything else from
    the original repr (type, primary_key, nullable, ...) stays as-is.
    """
    return _COLUMN_REPR_TABLE_KWARG.sub("", _ORIGINAL_COLUMN_REPR(column))


sa.Table.__repr__ = _table_repr  # type:ignore[method-assign] # ty:ignore[invalid-assignment]
sa.Column.__repr__ = _column_repr  # type:ignore[method-assign] # ty:ignore[invalid-assignment]


@dataclass
class SqlTable:
    """A recorder table - wraps the real sa.Table (the same one
    _recorder_arrow_types() reads column types from, so there's exactly
    one source of truth for what a table's columns are) rather than
    re-describing it under a parallel schema of our own.
    """

    _table: sa.Table

    @property
    def name(self) -> str:
        return self._table.name

    def column_names(self) -> list[str]:
        """Just the names, in schema order."""
        return list(self._table.c.keys())

    def columns(self) -> list[sa.Column]:
        """The real sa.Column objects, in schema order."""
        return list(self._table.c)

    def __repr__(self) -> str:
        return repr(self._table)


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


@dataclass
class SqlResult:
    """A query result as nanoarrow arrays, one per column - `columns` (name
    -> nanoarrow Array) is there directly for anyone who wants Arrow itself.
    This is the minimal, server-side half of the type: see this module's
    own docstring for why rendering/dataframe conversion live only on the
    client's own SqlResult (homeassistant_repl.sql), reconstructed from
    `.arrow()`'s bytes, not here.
    """

    columns: dict[str, Any]
    rowcount: int
    truncated: bool

    def to_dicts(self) -> list[dict[str, Any]]:
        names = list(self.columns)
        return [dict(zip(names, row, strict=True)) for row in self._rows()]

    def arrow(self) -> bytes:
        """This result as a single Arrow IPC stream: every column as one
        named field of a single struct-typed batch, in column order - the
        wire format this is actually meant to cross a client/server
        boundary in (unlike every other `.to_*()` here, which only ever
        make sense inside the one process that already holds the live
        nanoarrow Arrays), and a `pyarrow.ipc.open_stream()`/
        `polars.read_ipc_stream()`/etc.-readable file format in its own
        right.
        """
        schema = na.struct({name: arr.schema for name, arr in self.columns.items()})
        batch = na.c_array_from_buffers(
            schema,
            length=self.rowcount,
            buffers=[],
            children=list(self.columns.values()),
        )
        buf = io.BytesIO()
        with StreamWriter.from_writable(buf) as writer:
            writer.write_stream(batch)
        return buf.getvalue()

    def __iter__(self) -> Iterator[list[Any]]:
        """Rows as plain lists (not `.to_dicts()`'s dicts, not the tuples
        `_rows()` zips internally) - so `for row in result:`, `print(row)`
        and a comprehension like `[row[0] for row in result]` reach
        straight for a row's values.
        """
        for row in self._rows():
            yield list(row)

    @property
    def column_names(self) -> list[str]:
        """Just the names, in result order - `.columns` itself is name ->
        nanoarrow Array, which is the wrong shape when all you want is
        the list to pass to `.show(columns=...)` or a dict comprehension.
        """
        return list(self.columns)

    @property
    def table(self) -> SqlTable | None:
        """The one recorder table (the same SqlTable objects sql.tables
        exposes, not a copy) whose own columns are a superset of this
        result's - not SQL parsing, since joins/aliases/computed columns
        make "which table was this queried from" unreliable to parse, just
        whichever current table could have produced every column by name.
        None if no table fits (e.g. an aggregate's alias) or more than one
        does (e.g. a bare `entity_id` that several tables share).
        """
        names = set(self.columns)
        matches = [t for t in _current_tables() if names <= set(t.column_names())]
        return matches[0] if len(matches) == 1 else None

    def project(self, columns: list[str]) -> SqlResult:
        """A new SqlResult with just these columns (same rows, same
        underlying arrays - no data is copied) - for narrowing or
        reordering a result after the fact, without re-running the query.
        """
        return SqlResult(
            {name: self.columns[name] for name in columns},
            self.rowcount,
            self.truncated,
        )

    def __getitem__(self, key: slice) -> SqlResult:
        """Slice the rows with standard Python slice notation (`r[:10]`,
        `r[-1:]`, `r[10:20]`) - a new SqlResult, not a view, since nanoarrow
        Arrays don't support slicing directly; only `truncated` carries
        over, since it describes the underlying query, not this slice.
        """
        if not isinstance(key, slice):
            raise TypeError(
                f"SqlResult only supports slicing (e.g. result[:10]), not {key!r}"
            )
        columns = {
            name: na.array(arr.to_pylist()[key], schema=arr.schema)
            for name, arr in self.columns.items()
        }
        rowcount = len(range(*key.indices(self.rowcount)))
        return SqlResult(columns, rowcount, self.truncated)

    def __len__(self) -> int:
        return self.rowcount

    def sample(self, count: int = 20) -> SqlResult:
        """A new SqlResult with `count` rows chosen at random, without
        replacement (capped at the rows actually here, so this never
        raises for a small result) - an unbiased look at a big result,
        unlike show()/slicing's plain "first N". Rows keep their original
        relative order, only which ones are picked is random.
        """
        chosen = set(random.sample(range(self.rowcount), min(count, self.rowcount)))
        columns = {
            name: na.array(
                [value for i, value in enumerate(arr.to_pylist()) if i in chosen],
                schema=arr.schema,
            )
            for name, arr in self.columns.items()
        }
        return SqlResult(columns, len(chosen), self.truncated)

    def _rows(self, names: list[str] | None = None) -> Iterator[tuple[Any, ...]]:
        arrays = (
            self.columns.values()
            if names is None
            else (self.columns[name] for name in names)
        )
        return zip(*(arr.iter_py() for arr in arrays), strict=True)

    def __repr__(self) -> str:
        suffix = " (truncated)" if self.truncated else ""
        cols = ", ".join(self.columns)
        return f"<SqlResult {self.rowcount} rows x {len(self.columns)} cols [{cols}]{suffix}>"


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


async def sql(
    hass: HomeAssistant, query: str, *, max_rows: int | None = DEFAULT_MAX_ROWS
) -> SqlResult:
    """Run a single read-only SQL SELECT against Home Assistant's Recorder
    database and return the result as nanoarrow-backed columns - see this
    module's docstring, and SqlResult.to_pandas()/.to_polars()/.to_dicts().

    Only one SELECT statement is allowed - the same restriction Home
    Assistant's own `sql` integration applies, since this runs against your
    live recorder database and a stray UPDATE/DELETE here is as real as one
    from any other integration. At most `max_rows` rows are fetched (default
    1000) - rows beyond it are never transferred from the database, not
    just discarded afterwards; pass a higher `max_rows` for more, or None
    for no cap at all.
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
    return SqlResult(arrays, len(rows), truncated)


_MAX_ROWS_UNSET: Any = object()


@dataclass
class SqlTool:
    """The `sql` global bound into each live-mode session: callable to run a
    query, plus two bits of state a plain function can't hold - `.max_rows`
    (this session's own default row cap; set it to change the default for
    every call after, or to None to remove it entirely) and `.tables` (the
    recorder's current tables, to explore the schema without a query).
    One of these is created fresh per session (see PerSession in session.py)
    so `sql.max_rows = ...` in one session can't affect another's.
    """

    hass: HomeAssistant
    max_rows: int | None = DEFAULT_MAX_ROWS

    async def __call__(
        self, query: str, *, max_rows: int | None = _MAX_ROWS_UNSET
    ) -> SqlResult:
        """Same as sql() above, defaulting to this session's `.max_rows` -
        pass `max_rows=` explicitly (including None, for no cap) to
        override it for just this one call."""
        return await sql(
            self.hass,
            query,
            max_rows=self.max_rows if max_rows is _MAX_ROWS_UNSET else max_rows,
        )

    @property
    def tables(self) -> list[SqlTable]:
        return _current_tables()

    def __repr__(self) -> str:
        return (
            f"<sql(query, max_rows=...) - default max_rows={self.max_rows!r}; "
            "see sql.tables>"
        )


@functools.cache
def _current_tables() -> list[SqlTable]:
    """The recorder's own current (non-deprecated) tables, wrapped as
    SqlTable - from homeassistant.components.recorder.db_schema, the same
    source _recorder_arrow_types() reads above, not a live reflection of
    the connected database: these tables are schema-defined and the same
    for every instance of a given HA version, so there's nothing a blocking
    round trip to the real database would add here. Cached so repeated
    calls (SqlTool.tables, SqlResult.table) return the exact same SqlTable
    objects, not equal-but-distinct copies.
    """
    tables: dict[str, sa.Table] = {}
    for name, obj in vars(db_schema).items():
        if name.startswith("Legacy"):
            continue
        table = getattr(obj, "__table__", None)
        if table is not None:
            tables[table.name] = table
    return [SqlTable(t) for t in sorted(tables.values(), key=lambda t: t.name)]
