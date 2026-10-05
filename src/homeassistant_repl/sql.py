"""The client-side half of `sql` results: a separate, independent SqlResult
from custom_components/ha_repl_server/sql.py's, sharing nothing but the
Arrow IPC bytes `.arrow()` produces there and `SqlResult.from_arrow()`
reads here - see this project's docs/developer/design/design_principles.md
("maximum freedom for the developer on the client, maximum restrictions on
the server side"). The server's own job stops at running the query and
downloading a complete (`max_rows`-capped, not streamed - HA's own tables
are small enough that lazy/paged loading isn't worth the complexity yet)
in-memory Arrow dataset; everything past that - rendering (`.show()`),
dataframe conversion (`.to_pandas()`/`.to_polars()`), CSV export - runs
here, against that already-downloaded data, so installing pandas/polars
(or anything else) is a `pip install` in your own environment, never a
change to the running Home Assistant instance.

Once downloaded, a SqlResult behaves like a wrapped local database handle
in its own right (think `sqlite3`'s cursor, except the storage underneath
is an Arrow buffer, not a `.db` file) - every operation below (slicing,
`.project()`, `.sample()`, iteration, ...) works off that one in-memory
copy, no further round trip to Home Assistant.
"""

from __future__ import annotations

import base64
import csv
import io
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nanoarrow as na
from nanoarrow.ipc import StreamWriter
from rich.table import Table

from .client import HaReplError

DEFAULT_MAX_ROWS = 1000
DEFAULT_SHOW_ROWS = 30
DEFAULT_SHOW_COLUMNS = 6


class SqlError(Exception):
    """A rejected or failed query - not a bug in the shell itself."""


@dataclass(frozen=True)
class SqlColumn:
    """One column of a recorder table: its name and SQL type, as text."""

    name: str
    type: str

    def __repr__(self) -> str:
        return f"Column({self.name!r}, {self.type})"


@dataclass(frozen=True)
class SqlTable:
    """A recorder table, as described by the server - plain data, not a
    live handle on anything."""

    name: str
    _columns: tuple[SqlColumn, ...]

    def column_names(self) -> list[str]:
        """Just the names, in schema order."""
        return [column.name for column in self._columns]

    def columns(self) -> list[SqlColumn]:
        """Name and type of each column, in schema order."""
        return list(self._columns)

    def __repr__(self) -> str:
        return f"Table({self.name!r}, columns={self.column_names()!r})"


@dataclass
class SqlResult:
    """A query result downloaded from live mode's `sql`, as nanoarrow
    arrays, one per column. Build one with `SqlResult.from_arrow()`, not
    this constructor directly, unless you already have per-column arrays
    in hand.
    """

    columns: dict[str, Any]
    rowcount: int
    truncated: bool
    table: SqlTable | None = None
    """The recorder table these columns come from, when exactly one table
    has them all - None for a join, an aggregate, or a result that didn't
    come from `sql` in the first place."""

    @classmethod
    def from_arrow(
        cls,
        data: bytes,
        *,
        truncated: bool = False,
        tables: list[SqlTable] | None = None,
    ) -> SqlResult:
        """Rebuild a SqlResult from `.arrow()`'s bytes (the one shared
        contract with the server-side SqlResult that produced them) - a
        single Arrow IPC stream holding one struct-typed batch, one named
        field per column. `truncated` isn't itself encoded in the Arrow
        data (it's metadata about the query, not the rows), so it travels
        alongside the bytes rather than inside them.
        """
        batch = na.ArrayStream.from_readable(data).read_all()
        names = [field.name for field in batch.schema.fields]
        columns = dict(zip(names, batch.iter_children(), strict=True))
        matches = [t for t in tables or () if set(names) <= set(t.column_names())]
        table = matches[0] if len(matches) == 1 else None
        return cls(columns, len(batch), truncated, table)

    def arrow(self) -> bytes:
        """Re-serialize back to the same Arrow IPC stream format
        `from_arrow()` reads - round-tripping, or handing this result to
        any other `pyarrow.ipc.open_stream()`/`polars.read_ipc_stream()`
        /etc.-compatible reader.
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

    def to_dicts(self) -> list[dict[str, Any]]:
        names = list(self.columns)
        return [dict(zip(names, row, strict=True)) for row in self._rows()]

    def to_polars(self) -> Any:
        import polars as pl  # type: ignore[import-not-found]  # ty: ignore[unresolved-import]

        return pl.DataFrame(self.columns)

    def to_pandas(self) -> Any:
        import pandas as pd  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]

        return pd.DataFrame({
            name: list(arr.iter_py()) for name, arr in self.columns.items()
        })

    def __iter__(self) -> Iterator[list[Any]]:
        """Rows as plain lists - see the server-side SqlResult's own
        docstring for the same method; identical behaviour, just local.
        """
        for row in self._rows():
            yield list(row)

    @property
    def column_names(self) -> list[str]:
        return list(self.columns)

    def show(
        self,
        columns: list[str] | None = None,
        *,
        max_rows: int | None = DEFAULT_SHOW_ROWS,
        max_cols: int | None = DEFAULT_SHOW_COLUMNS,
    ) -> Table:
        """A rich Table rendering of this result - see the server-side
        SqlResult's own show() for the full rationale; identical
        behaviour, just rendered locally instead of over the wire.
        """
        all_names = self.column_names
        if columns is not None:
            names = columns
            cols_truncated = False
        elif max_cols is not None and len(all_names) > max_cols:
            names = all_names[:max_cols]
            cols_truncated = True
        else:
            names = all_names
            cols_truncated = False

        if max_rows is not None and self.rowcount > max_rows:
            row_cap = max_rows
            rows_truncated = True
        else:
            row_cap = self.rowcount
            rows_truncated = False

        caption = f"{self.rowcount} row{'' if self.rowcount == 1 else 's'}"
        if self.truncated:
            caption += " (truncated)"
        if cols_truncated:
            caption += f" ({len(names)}/{len(all_names)} cols)"
        if rows_truncated:
            caption += f" (showing first {row_cap} rows)"

        table = Table(*names, caption=caption)
        for i, row in enumerate(self._rows(names)):
            if i >= row_cap:
                break
            table.add_row(*("" if v is None else str(v) for v in row))
        return table

    def __rich__(self) -> Table:
        """What the shell prints for a bare `sql(...)` / `result` at the
        prompt - the same table show() gives with its defaults."""
        return self.show()

    def project(self, columns: list[str]) -> SqlResult:
        """A new SqlResult with just these columns (same rows, same
        underlying arrays - no data is copied).
        """
        return SqlResult(
            {name: self.columns[name] for name in columns},
            self.rowcount,
            self.truncated,
            self.table,
        )

    def __getitem__(self, key: slice) -> SqlResult:
        """Slice the rows with standard Python slice notation (`r[:10]`,
        `r[-1:]`, `r[10:20]`) - a new SqlResult, not a view.
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
        return SqlResult(columns, rowcount, self.truncated, self.table)

    def __len__(self) -> int:
        return self.rowcount

    def sample(self, count: int = 20) -> SqlResult:
        """A new SqlResult with `count` rows chosen at random, without
        replacement (capped at the rows actually here). Rows keep their
        original relative order, only which ones are picked is random.
        """
        chosen = set(random.sample(range(self.rowcount), min(count, self.rowcount)))
        columns = {
            name: na.array(
                [value for i, value in enumerate(arr.to_pylist()) if i in chosen],
                schema=arr.schema,
            )
            for name, arr in self.columns.items()
        }
        return SqlResult(columns, len(chosen), self.truncated, self.table)

    def export_csv(self, path: str | Path | None = None, **kwargs: Any) -> Path:
        """Write this result to a CSV file at `path` (your own machine,
        not Home Assistant's) - a header row, then every row with each
        cell through str(). Without a `path` it's named after `.table`
        ("states.csv"), or "result.csv" when there isn't one. `kwargs` go
        straight to csv.writer (dialect, delimiter, ...). The path actually
        written to is returned.
        """
        if path is None:
            path = f"{self.table.name if self.table else 'result'}.csv"
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp, **kwargs)
            writer.writerow(self.column_names)
            for row in self._rows():
                writer.writerow("" if v is None else str(v) for v in row)
        return path

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


_MAX_ROWS_UNSET: Any = object()


@dataclass
class SqlTool:
    """Live mode's `sql`: a local object, not a name on the Home Assistant
    side. Calling it sends the query over the websocket, downloads the
    whole (`max_rows`-capped) result as Arrow and hands back a local
    SqlResult - so `sql(...).show()`, `.to_polars()` and anything else you
    do with the result runs in this process, with whatever you've
    installed here.

    `.max_rows` is this shell's default row cap (set it to change the
    default for every call after, or to None to remove it entirely) and
    `.tables` the recorder's tables, to explore the schema without a query.
    """

    client: Any
    max_rows: int | None = DEFAULT_MAX_ROWS
    tables: list[SqlTable] = field(default_factory=list)

    @classmethod
    async def connect(cls, client: Any) -> SqlTool:
        """A SqlTool with `.tables` already downloaded. Raises HaReplError
        if the server has no sql to offer (switched off in the
        integration's options, or a server too old to have the command)."""
        reply = await client.call("ha_repl_server/sql_tables")
        tables = [
            SqlTable(
                t["name"], tuple(SqlColumn(c["name"], c["type"]) for c in t["columns"])
            )
            for t in reply["tables"]
        ]
        return cls(client, tables=tables)

    async def __call__(
        self, query: str, *, max_rows: int | None = _MAX_ROWS_UNSET
    ) -> SqlResult:
        """Run a single read-only SELECT against Home Assistant's Recorder
        database. At most `max_rows` rows are fetched (default: this
        object's own `.max_rows`) - pass None for no cap at all."""
        try:
            reply = await self.client.call(
                "ha_repl_server/sql",
                query=query,
                max_rows=self.max_rows if max_rows is _MAX_ROWS_UNSET else max_rows,
            )
        except HaReplError as err:
            if err.code != "sql_error":
                raise
            raise SqlError(err.detail) from None
        return SqlResult.from_arrow(
            base64.b64decode(reply["arrow"]),
            truncated=reply["truncated"],
            tables=self.tables,
        )

    def __repr__(self) -> str:
        return (
            f"<sql(query, max_rows=...) - default max_rows={self.max_rows!r}; "
            "see sql.tables>"
        )
