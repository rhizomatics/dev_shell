"""Interactive shell: local line editing and history, remote execution."""

from __future__ import annotations

import ast
import codeop
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.python import PythonLexer

from .cli import display_options, print_result
from .client import Client

HISTORY = Path.home() / ".ha_repl_history"


async def run_repl(client: Client, session_name: str) -> int:
    compiler = codeop.CommandCompiler()
    compiler.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    prompt: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY)), lexer=PygmentsLexer(PythonLexer)
    )
    print(
        f"Home Assistant REPL connected to {client.url} (session {session_name!r}). Ctrl-D to exit."
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
            pass  # send it anyway so the server reports the error consistently
        lines.clear()
        if not source.strip():
            continue
        if is_quit_call(source):
            return 0
        result = await client.call(
            "ha_repl_server/exec",
            code=source,
            session=session_name,
            **display_options(),
        )
        print_result(result)


def is_quit_call(source: str) -> bool:
    """True for a bare `quit()` or `exit()` - the standard way to leave a REPL.

    Sending that to the server would just raise SystemExit there, caught and
    reported back as a confusing traceback instead of actually exiting
    anything, so it's handled locally like Ctrl-D instead.
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
