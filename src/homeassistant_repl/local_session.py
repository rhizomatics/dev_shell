"""API client mode's exec engine: runs snippets right here in the CLI
process, not on a remote Home Assistant session - there's no server to send
code to, since API client mode talks to Home Assistant only through its
standard API (see api_objtree.py). Deliberately mirrors
custom_components/ha_repl_server/session.py's exec model (top-level await,
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
    auto_await: bool = True

    def __post_init__(self) -> None:
        self.globals_.setdefault("__name__", "__ha_repl_api__")
        self.globals_.setdefault("__builtins__", builtins)
        self.globals_.setdefault("_maybe_await", _maybe_await)
        self.globals_.setdefault("unawait", unawait)

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
        # Not "<ha_repl-N>": rich.traceback refuses to show source for any
        # filename starting with "<" (treats it like "<stdin>"), no matter what
        # linecache holds. An absolute-looking path sidesteps that - rich joins a
        # relative one onto the cwd before the linecache lookup, which would miss.
        filename = f"/ha_repl/api_cell_{next(_cell_counter)}"
        linecache.cache[filename] = (
            len(source),
            None,
            source.splitlines(keepends=True),
            filename,
        )
        tree = ast.parse(source, filename, "exec")
        if self.auto_await:
            tree = _AutoAwait().visit(tree)
            ast.fix_missing_locations(tree)

        last_expr = None
        last_stmt = tree.body[-1] if tree.body else None
        if isinstance(last_stmt, ast.Expr):
            tree.body.pop()
            last_expr = ast.Expression(last_stmt.value)

        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        # dont_inherit=True: compile() otherwise inherits this module's own
        # `from __future__ import annotations`, which would make every
        # annotation in the user's code a plain string instead of a real value.
        if tree.body:
            await _run_code(
                compile(tree, filename, "exec", flags=flags, dont_inherit=True),
                self.globals_,
                auto_await=self.auto_await,
            )
        if last_expr is None:
            return None
        return await _run_code(
            compile(last_expr, filename, "eval", flags=flags, dont_inherit=True),
            self.globals_,
            auto_await=self.auto_await,
        )


async def _maybe_await(value: Any) -> Any:
    """Finish a coroutine (or other awaitable) the user forgot to `await`;
    anything else passes straight through. Used both by the AST rewrite below
    (an unawaited call nested inside a larger expression) and _run_code's own
    check (a bare reference to one created earlier, e.g. `c = f(); c`)."""
    return await value if inspect.isawaitable(value) else value


def unawait(value: Any) -> Any:
    """Identity function and auto-await escape hatch: `unawait(f())` returns
    f()'s bare, un-awaited result (a coroutine, if f is async) - for when
    that's genuinely wanted, e.g. batching into `asyncio.gather(*[unawait(f())
    for f in ...])`. Recognised by name in the `_AutoAwait` rewrite below,
    which skips its whole argument rather than calling this at runtime; this
    plain version only runs if auto-await itself is off (--no-auto-await) or
    `unawait` is used somewhere the rewrite doesn't reach.
    """
    return value


class _AutoAwait(ast.NodeTransformer):
    """Rewrites every call not already explicitly awaited to go through
    _maybe_await() first, so `obj.async_method()` works whether or not the
    user remembered `await` - including nested inside attribute access
    (`obj.async_method().attr`), a comprehension, or an argument list.
    Leaves nested (synchronous) function/lambda bodies alone: `await` there
    is a SyntaxError, and those calls run later, not as part of this
    statement anyway. `unawait(expr)` is the escape hatch - its argument is
    left completely untouched for when the bare coroutine is wanted.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.Lambda:
        return node

    def visit_Await(self, node: ast.Await) -> ast.Await:
        # Already explicit: don't double-await this call, but still rewrite
        # anything nested inside its own arguments.
        if isinstance(node.value, ast.Call):
            node.value.func = self.visit(node.value.func)
            node.value.args = [self.visit(a) for a in node.value.args]
            node.value.keywords = [self.visit(k) for k in node.value.keywords]
        else:
            node.value = self.visit(node.value)
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "unawait"
            and len(node.args) == 1
            and not node.keywords
        ):
            return node.args[0]
        self.generic_visit(node)
        wrapped = ast.Call(
            func=ast.Name(id="_maybe_await", ctx=ast.Load()), args=[node], keywords=[]
        )
        return ast.Await(value=wrapped)


async def _run_code(code: Any, globals_: dict[str, Any], *, auto_await: bool) -> Any:
    result = eval(code, globals_)  # nosec B307 - the whole point of a dev shell
    if code.co_flags & inspect.CO_COROUTINE:
        # Runs the top-level-await-compiled code itself (and, when
        # auto_await is on, the _AutoAwait rewrite's own awaits) - not
        # necessarily the user's own forgotten await, hence the check below.
        result = await result
    if auto_await and inspect.isawaitable(result):
        # Backstop for a bare reference to an already-existing coroutine
        # (e.g. `c = f(); c` across two statements) that the AST rewrite,
        # which only sees call sites, can't catch.
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
