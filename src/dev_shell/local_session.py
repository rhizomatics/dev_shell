"""API client mode's exec engine: runs snippets right here in the CLI
process, not on a remote Home Assistant session - there's no server to send
code to, since API client mode talks to Home Assistant only through its
standard API (see api_objtree.py). Deliberately mirrors
custom_components/dev_shell_server/session.py's exec model (top-level await,
trailing-expression echo via rich, rich tracebacks) rather than importing it:
that module lives in a separate HACS-deployed package with its own packaging
boundary, and duplicating ~100 lines here is simpler than bridging it.

Simpler than session.py in one respect: output goes straight to the real
terminal, so there's no print()/help() capturing-and-shipping-back machinery,
and rich's Console can auto-detect color/width instead of being told.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import itertools
import linecache
import sys
import traceback
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.pretty import Pretty
from rich.traceback import Traceback

_cell_counter = itertools.count(1)

_console = Console()
_error_console = Console(stderr=True)


@dataclass
class LocalSession:
    """A namespace that persists between `run()` calls until the process exits."""

    globals_: dict[str, Any]

    def __post_init__(self) -> None:
        self.globals_.setdefault("__name__", "__dev_shell_api__")
        self.globals_.setdefault("__builtins__", builtins)

    async def run(self, source: str) -> None:
        """Execute source, printing the trailing expression's value (if any)
        or a traceback directly to the real terminal - there's no result to
        ship back over a wire, so this doesn't return one."""
        try:
            value = await self._execute(source)
        except BaseException as err:  # noqa: BLE001 - report everything, incl. SystemExit
            _print_error(err)
            return
        if value is not None:
            self.globals_["_"] = value
            _console.print(Pretty(value))

    async def _execute(self, source: str) -> Any:
        # Not "<dev_shell-N>": rich.traceback refuses to show source for any
        # filename starting with "<" (treats it like "<stdin>"), no matter what
        # linecache holds. An absolute-looking path sidesteps that - rich joins a
        # relative one onto the cwd before the linecache lookup, which would miss.
        filename = f"/dev_shell/api_cell_{next(_cell_counter)}"
        linecache.cache[filename] = (
            len(source),
            None,
            source.splitlines(keepends=True),
            filename,
        )
        tree = ast.parse(source, filename, "exec")

        last_expr = None
        last_stmt = tree.body[-1] if tree.body else None
        if isinstance(last_stmt, ast.Expr):
            tree.body.pop()
            last_expr = ast.Expression(last_stmt.value)

        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        if tree.body:
            await _run_code(compile(tree, filename, "exec", flags=flags), self.globals_)
        if last_expr is None:
            return None
        return await _run_code(
            compile(last_expr, filename, "eval", flags=flags), self.globals_
        )


async def _run_code(code: Any, globals_: dict[str, Any]) -> Any:
    result = eval(code, globals_)  # nosec B307 - the whole point of a dev shell
    if code.co_flags & inspect.CO_COROUTINE:
        result = await result
    return result


def _print_error(err: BaseException) -> None:
    tb = err.__traceback__
    # Drop the frames belonging to this module so the traceback starts at user code.
    while tb is not None and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next
    if isinstance(err, SyntaxError):
        # No frames worth showing for this one - plain is fine.
        print(
            "".join(traceback.format_exception_only(type(err), err)),
            end="",
            file=sys.stderr,
        )
        return
    _error_console.print(Traceback.from_exception(type(err), err, tb))
