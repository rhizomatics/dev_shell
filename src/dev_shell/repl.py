"""Interactive shell: local line editing and history, remote execution."""

from __future__ import annotations

import ast
import codeop
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.python import PythonLexer

from .client import Client
from .cli import print_result

HISTORY = Path.home() / ".dev_shell_history"


async def run_repl(client: Client, session_name: str) -> int:
    compiler = codeop.CommandCompiler()
    compiler.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    prompt = PromptSession(history=FileHistory(str(HISTORY)), lexer=PygmentsLexer(PythonLexer))
    print(f"Hass Shell connected to {client.url} (session {session_name!r}). Ctrl-D to exit.")
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
            if compiler(source, "<dev_shell>", "single") is None:
                continue
        except (SyntaxError, OverflowError, ValueError):
            pass  # send it anyway so the server reports the error consistently
        lines.clear()
        if not source.strip():
            continue
        result = await client.call("dev_shell_server/exec", code=source, session=session_name)
        print_result(result)
