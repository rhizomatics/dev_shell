"""Live mode's interactive shell: a real local Python session (LocalSession,
the same engine API client mode uses) with a remote Home Assistant session
alongside it.

Code runs here, in this process, by default - `import polars`, define
functions, keep a dataframe around; none of it touches Home Assistant. `sql`
and `hass_api` are local objects that fetch data over the wire (Arrow over
the websocket, and the REST API) and hand back local results.

`hass` and `obj` are the exception: they only exist inside Home Assistant,
so a command that uses either one (or a variable an earlier such command
left behind over there) is sent whole to the server-side session and run
there, as before, with its output shipped back as text - see LiveSession
for exactly how that's decided and what crosses over.
"""

from __future__ import annotations

import ast
import codeop
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.python import PythonLexer

from .cli import display_options, print_result
from .client import Client, HaReplError, client_version
from .local_session import LocalSession
from .rest import hass_api
from .sql import SqlTool

HISTORY = Path.home() / ".ha_repl_history"

# Names that only ever exist on the Home Assistant side.
REMOTE_ONLY = frozenset({"hass", "obj"})

# Local values bigger than this (as source text) aren't copied to the server.
_MAX_SHIPPED_CHARS = 100_000

_LITERAL_TYPES = (
    str,
    bytes,
    int,
    float,
    bool,
    type(None),
    list,
    tuple,
    dict,
    set,
)


@dataclass
class LiveSession:
    """Decides, one command at a time, which side runs it.

    A command runs on the server if it mentions a name that lives there and
    not here: `hass`, `obj`, or a variable a previous server-side command
    assigned (`s = hass.states.get("sun.sun")`, then `s.state`). Everything
    else runs locally. A name that exists on both sides counts as local.

    The two namespaces are otherwise separate. The one thing that crosses
    is local -> server, for a server-side command that uses local names:
    plain data (strings, numbers, lists/dicts of them) is copied over, and
    an imported module is imported there under the same name. Anything else
    local (a dataframe, a SqlResult, a function) can't follow, and the
    command is refused with an explanation rather than failing over there
    with a puzzling NameError.
    """

    client: Any
    session_name: str
    local: LocalSession
    auto_await: bool = True
    remote_names: set[str] = field(default_factory=set)
    # Bound on both sides by design (`sql`, `hass_api`) - never copied over.
    shared: frozenset[str] = frozenset()

    async def refresh_remote_names(self) -> None:
        reply = await self.client.call("ha_repl_server/sessions")
        for session in reply["sessions"]:
            if session["name"] == self.session_name:
                self.remote_names = set(session["variables"])
                return
        self.remote_names = set()

    async def run(self, source: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError, ValueError:
            await self.local.run(source)  # reports the error itself
            return
        used = _Names()
        used.visit(tree)
        local_names = self._local_names()
        remote_refs = (used.loaded - local_names) & (REMOTE_ONLY | self.remote_names)
        if not remote_refs:
            await self.local.run(source)
            return

        preamble: list[str] = []
        stuck: list[str] = []
        for name in sorted(used.loaded & (local_names - self.shared)):
            line = _ship(name, self.local.globals_[name])
            if line is not None:
                preamble.append(line)
            elif name not in used.bound:
                stuck.append(name)
        if stuck:
            names = ", ".join(f"`{n}`" for n in stuck)
            via = ", ".join(f"`{n}`" for n in sorted(remote_refs))
            print(
                f"ha-repl: not run - this would run inside Home Assistant (it uses {via}), "
                f"but {names} only exist{'s' if len(stuck) == 1 else ''} in this local session "
                "and can't be copied over (only plain data and imported modules can).",
                file=sys.stderr,
            )
            return
        if preamble and not await self._exec_remote("\n".join(preamble)):
            return
        await self._exec_remote(source)
        # `_` is whichever side answered last: drop the local one so a
        # following `_` is looked up on the server.
        self.local.globals_.pop("_", None)
        await self.refresh_remote_names()

    async def _exec_remote(self, source: str) -> bool:
        result = await self.client.call(
            "ha_repl_server/exec",
            code=source,
            session=self.session_name,
            auto_await=self.auto_await,
            **display_options(),
        )
        print_result(result)
        return not result["error"]

    def _local_names(self) -> set[str]:
        return {
            name
            for name in self.local.globals_
            if not name.startswith("__") and name not in ("_maybe_await", "unawait")
        }


class _Names(ast.NodeVisitor):
    """Every name a command reads (`loaded`) and every name it binds itself
    (`bound` - assignments, imports, defs, arguments, loop/comprehension
    variables), anywhere in it."""

    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.bound: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        (self.loaded if isinstance(node.ctx, ast.Load) else self.bound).add(node.id)

    def visit_alias(self, node: ast.alias) -> None:
        self.bound.add((node.asname or node.name).partition(".")[0])

    def visit_arg(self, node: ast.arg) -> None:
        self.bound.add(node.arg)
        self.generic_visit(node)

    def _visit_def(self, node: Any) -> None:
        self.bound.add(node.name)
        self.generic_visit(node)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_def


def _ship(name: str, value: Any) -> str | None:
    """A line of source that recreates this local value under the same name
    on the server, or None if it isn't something that can be recreated."""
    if isinstance(value, types.ModuleType):
        return f"import {value.__name__} as {name}"
    if not isinstance(value, _LITERAL_TYPES):
        return None
    try:
        text = repr(value)
        if len(text) > _MAX_SHIPPED_CHARS or ast.literal_eval(text) != value:
            return None
    except Exception:  # noqa: BLE001 - any failure just means "can't be copied"
        return None
    return f"{name} = {text}"


async def run_repl(
    client: Client, session_name: str, *, auto_await: bool = True
) -> int:
    compiler = codeop.CommandCompiler()
    compiler.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    prompt: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY)), lexer=PygmentsLexer(PythonLexer)
    )
    try:
        info = await client.call("ha_repl_server/info")
        server = f"Live Server v{info['version']}"
    except HaReplError:
        # An older server predating this command - not fatal, just less to show.
        server = "Live Server"

    bindings: dict[str, Any] = {}
    try:
        bindings["hass_api"] = await hass_api(client.url, client.token)
    except HaReplError as err:
        print(f"ha-repl: hass_api unavailable: {err}", file=sys.stderr)
        bindings["hass_api"] = None
    try:
        bindings["sql"] = await SqlTool.connect(client)
    except HaReplError as err:
        # Switched off on the server, or a server predating the command -
        # left unbound, so `sql` falls through to whatever the server has.
        print(f"ha-repl: local sql unavailable: {err}", file=sys.stderr)

    live = LiveSession(
        client,
        session_name,
        LocalSession(dict(bindings), auto_await=auto_await),
        auto_await=auto_await,
        shared=frozenset(bindings),
    )
    await live.refresh_remote_names()
    print(
        f"Live client v{client_version()} connected to {server} at HA API "
        f"{client.url} (session {session_name!r}). Ctrl-D to exit.\n"
        "Python runs locally; anything using `hass` or `obj` runs inside Home Assistant."
    )
    lines: list[str] = []
    while True:
        try:
            line = await prompt.prompt_async("... " if lines else ">>> ")
        except KeyboardInterrupt:
            lines.clear()
            continue
        except EOFError:
            return 0
        lines.append(line)
        source = "\n".join(lines)
        try:
            # None means incomplete input: keep reading continuation lines.
            if compiler(source, "<ha_repl>", "single") is None:
                continue
        except SyntaxError, OverflowError, ValueError:
            pass  # run it anyway so the error is reported consistently
        lines.clear()
        if not source.strip():
            continue
        if is_quit_call(source):
            return 0
        await live.run(source)


def is_quit_call(source: str) -> bool:
    """True for a bare `quit()` or `exit()` - the standard way to leave a REPL.

    Running that would raise SystemExit inside the session, caught and
    reported back as a confusing traceback instead of actually exiting
    anything, so it's handled like Ctrl-D instead.
    """
    try:
        (stmt,) = ast.parse(source).body
    except SyntaxError, ValueError:
        return False
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id in ("quit", "exit")
        and not stmt.value.args
        and not stmt.value.keywords
    )
