"""`sql` - run a read-only query against Home Assistant's own Recorder
database, reusing its already-configured SQLAlchemy engine (so sqlite/
postgresql/mysql all just work, the same adaptation every other HA component
gets for free) rather than opening a second connection or driver of our own.

Live mode only: this needs the real `hass`/recorder instance running in this
process - there's no REST/websocket equivalent reachable from API client mode.
The session-global `sql` is a SqlTool instance (one per session, see
PerSession in session.py): call it like a function (`sql("select ...")`),
inspect/change its default row cap via `sql.limit` (None removes it), or
look at `sql.tables` for the recorder's current tables without a query.

Results come back as nanoarrow arrays (https://arrow.apache.org/nanoarrow/) -
a ~1MB, dependency-free Arrow implementation, not the ~100MB pyarrow - with
`.to_pandas()`/`.to_polars()`/`.to_dicts()` for whichever (if any) dataframe
library the caller already has installed. Column types come from Home
Assistant's own declared recorder schema (homeassistant.components.recorder.
db_schema) wherever a result column's name matches a real column there - no
runtime value sniffing needed for the handful of stable tables (events,
states, statistics, ...) this is meant for. A column whose name isn't
recognised there (e.g. an aggregate like COUNT(*)) falls back to a one-off
peek at its own first non-null value, not a sniff of every column.
"""

from __future__ import annotations

import functools
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
from homeassistant.core import HomeAssistant
from rich.table import Table

DEFAULT_ROW_LIMIT = 1000
DEFAULT_SHOW_COLUMNS = 6
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


sa.Table.__repr__ = _table_repr  # type:ignore[method-assign] # ty:ignore[invalid-assignment]
sa.Column.__repr__ = _column_repr  # type:ignore[method-assign] # ty:ignore[invalid-assignment]


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
    """A query result as nanoarrow arrays, one per column. `columns` (name
    -> nanoarrow Array) is there directly for anyone who wants Arrow itself;
    .to_pandas()/.to_polars()/.to_dicts() convert on demand, lazily
    importing whichever library the caller already has installed.
    """

    columns: dict[str, Any]
    row_count: int
    truncated: bool

    def to_polars(self) -> Any:
        import polars as pl  # type: ignore[import-not-found]  # ty: ignore[unresolved-import]

        return pl.DataFrame(self.columns)

    def to_pandas(self) -> Any:
        import pandas as pd  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]

        return pd.DataFrame({
            name: list(arr.iter_py()) for name, arr in self.columns.items()
        })

    def to_dicts(self) -> list[dict[str, Any]]:
        names = list(self.columns)
        return [dict(zip(names, row, strict=True)) for row in self._rows()]

    @property
    def column_names(self) -> list[str]:
        """Just the names, in result order - `.columns` itself is name ->
        nanoarrow Array, which is the wrong shape when all you want is
        the list to pass to `.show(columns=...)` or a dict comprehension.
        """
        return list(self.columns)

    @property
    def table(self) -> sa.Table | None:
        """The one recorder table (the same sa.Table objects sql.tables
        exposes, not a copy) whose own columns are a superset of this
        result's - not SQL parsing, since joins/aliases/computed columns
        make "which table was this queried from" unreliable to parse, just
        whichever current table could have produced every column by name.
        None if no table fits (e.g. an aggregate's alias) or more than one
        does (e.g. a bare `entity_id` that several tables share).
        """
        names = set(self.columns)
        matches = [t for t in _current_tables() if names <= set(t.c.keys())]
        return matches[0] if len(matches) == 1 else None

    def show(self, columns: list[str] | None = None) -> Table:
        """A rich Table rendering of this result - the trailing-expression
        equivalent of obj.show(): meant to be the trailing expression at the
        REPL so the usual echo renders it, not printed directly here. Every
        cell goes through str() - the same "good enough to read, not meant
        to round-trip" rule _format_error's traceback rendering applies,
        rather than re-implementing per-type formatting rich's own Pretty
        already does better for the arrow-free single-value case.

        `columns` narrows (and/or reorders) which of this result's columns
        get shown; left to default, a wide result is cut down to its first
        `DEFAULT_SHOW_COLUMNS` (pass `columns=` explicitly for more, or to
        pick a different set) so a `select *` doesn't blow out the width of
        whatever's rendering this (REPL, notebook, ...).
        """
        all_names = self.column_names
        if columns is not None:
            names = columns
            columns_truncated = False
        else:
            names = all_names[:DEFAULT_SHOW_COLUMNS]
            columns_truncated = len(all_names) > DEFAULT_SHOW_COLUMNS
        caption = f"{self.row_count} row{'' if self.row_count == 1 else 's'}"
        if self.truncated:
            caption += " (truncated)"
        if columns_truncated:
            caption += f" ({len(names)}/{len(all_names)} cols)"
        table = Table(*names, caption=caption)
        for row in self._rows(names):
            table.add_row(*("" if v is None else str(v) for v in row))
        return table

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
        return f"<SqlResult {self.row_count} rows x {len(self.columns)} cols [{cols}]{suffix}>"


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
    hass: HomeAssistant, query: str, limit: int | None
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """Runs on the recorder's own executor thread - see sql() below for why
    (SQLite, and the recorder's pooled connections generally, are
    thread-affine, so this can't run on the event loop or hass's own
    generic executor).
    """
    chunk_size = (
        _FETCH_CHUNK_SIZE if limit is None else max(1, min(limit, _FETCH_CHUNK_SIZE))
    )
    with get_instance(hass).get_session() as session:
        result = session.execute(sa.text(query))
        columns = list(result.keys())
        rows: list[tuple[Any, ...]] = []
        truncated = False
        for row in result.yield_per(chunk_size):
            if limit is not None and len(rows) >= limit:
                truncated = True
                break
            rows.append(tuple(row))
        result.close()
        return columns, rows, truncated


async def sql(
    hass: HomeAssistant, query: str, *, limit: int | None = DEFAULT_ROW_LIMIT
) -> SqlResult:
    """Run a single read-only SQL SELECT against Home Assistant's Recorder
    database and return the result as nanoarrow-backed columns - see this
    module's docstring, and SqlResult.to_pandas()/.to_polars()/.to_dicts().

    Only one SELECT statement is allowed - the same restriction Home
    Assistant's own `sql` integration applies, since this runs against your
    live recorder database and a stray UPDATE/DELETE here is as real as one
    from any other integration. At most `limit` rows are fetched (default
    1000) - rows beyond it are never transferred from the database, not
    just discarded afterwards; pass a higher `limit` for more, or None for
    no cap at all.
    """
    if limit is not None and limit < 1:
        raise SqlError("limit must be at least 1")
    _check_select_only(query)
    try:
        instance = get_instance(hass)
    except KeyError:
        raise SqlError("the recorder is not set up on this instance") from None
    columns, rows, truncated = await instance.async_add_executor_job(
        _fetch_rows, hass, query, limit
    )
    column_values = list(zip(*rows, strict=True)) if rows else [() for _ in columns]
    arrays = {
        name: _build_column(name, list(values))
        for name, values in zip(columns, column_values, strict=True)
    }
    return SqlResult(arrays, len(rows), truncated)


_LIMIT_UNSET: Any = object()


@dataclass
class SqlTool:
    """The `sql` global bound into each live-mode session: callable to run a
    query, plus two bits of state a plain function can't hold - `.limit`
    (this session's own default row cap; set it to change the default for
    every call after, or to None to remove it entirely) and `.tables` (the
    recorder's current tables, to explore the schema without a query).
    One of these is created fresh per session (see PerSession in session.py)
    so `sql.limit = ...` in one session can't affect another's.
    """

    hass: HomeAssistant
    limit: int | None = DEFAULT_ROW_LIMIT

    async def __call__(
        self, query: str, *, limit: int | None = _LIMIT_UNSET
    ) -> SqlResult:
        """Same as sql() above, defaulting to this session's `.limit` - pass
        `limit=` explicitly (including None, for no cap) to override it for
        just this one call."""
        return await sql(
            self.hass, query, limit=self.limit if limit is _LIMIT_UNSET else limit
        )

    @property
    def tables(self) -> list[sa.Table]:
        return _current_tables()

    def __repr__(self) -> str:
        return f"<sql(query, limit=...) - default limit={self.limit!r}; see sql.tables>"


@functools.cache
def _current_tables() -> list[sa.Table]:
    """The recorder's own current (non-deprecated) tables as real SQLAlchemy
    Table objects - from homeassistant.components.recorder.db_schema, the
    same source _recorder_arrow_types() reads above, not a live reflection
    of the connected database: these tables are schema-defined and the same
    for every instance of a given HA version, so there's nothing a blocking
    round trip to the real database would add here.
    """
    tables: dict[str, sa.Table] = {}
    for name, obj in vars(db_schema).items():
        if name.startswith("Legacy"):
            continue
        table = getattr(obj, "__table__", None)
        if table is not None:
            tables[table.name] = table
    return sorted(tables.values(), key=lambda t: t.name)
