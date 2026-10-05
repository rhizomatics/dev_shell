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

import csv
import io
import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nanoarrow as na
from nanoarrow.ipc import StreamWriter
from rich.table import Table

DEFAULT_SHOW_ROWS = 30
DEFAULT_SHOW_COLUMNS = 6


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

    @classmethod
    def from_arrow(cls, data: bytes, *, truncated: bool = False) -> SqlResult:
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
        return cls(columns, len(batch), truncated)

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

    def project(self, columns: list[str]) -> SqlResult:
        """A new SqlResult with just these columns (same rows, same
        underlying arrays - no data is copied).
        """
        return SqlResult(
            {name: self.columns[name] for name in columns},
            self.rowcount,
            self.truncated,
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
        return SqlResult(columns, rowcount, self.truncated)

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
        return SqlResult(columns, len(chosen), self.truncated)

    def export_csv(self, path: str | Path = "result.csv", **kwargs: Any) -> Path:
        """Write this result to a CSV file at `path` (your own machine,
        not Home Assistant's) - a header row, then every row with each
        cell through str(). `kwargs` go straight to csv.writer (dialect,
        delimiter, ...). The path actually written to is returned.
        """
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
