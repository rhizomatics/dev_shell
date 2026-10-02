"""API client mode's interactive shell: same local line editing/history as
repl.py, but code runs right here (LocalSession) - no `hass`, no
dev_shell_server/exec call, no HACS component required on the HA side at
all. `obj` is bound the same name as custom mode, just to an ApiObjTree
instead of the live ObjTree, so a snippet that only touches `obj` runs
unchanged in either mode.

The cache refresh happens here, between prompts, not inside ApiObjTree's own
Mapping methods - see api_objtree.py's module docstring for why (asyncio
reentrancy: this loop is the one safe place that's both async and knows when
"between commands" is).
"""

from __future__ import annotations

import ast
import codeop
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.python import PythonLexer

from .api_objtree import ApiObjTree, Cache
from .client import Client
from .local_session import LocalSession
from .repl import is_quit_call

HISTORY = Path.home() / ".dev_shell_api_history"


async def run_api_repl(client: Client, ttl: float) -> int:
    cache = Cache(client, ttl)
    await cache.refresh()
    session = LocalSession({"obj": ApiObjTree(cache)})

    compiler = codeop.CommandCompiler()
    compiler.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    prompt: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY)), lexer=PygmentsLexer(PythonLexer)
    )
    print(
        f"Dev Shell (API client mode) connected to {client.url}. "
        "`obj` only, read-only - no `hass`. Ctrl-D to exit."
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
            if compiler(source, "<dev_shell>", "single") is None:
                continue
        except SyntaxError, OverflowError, ValueError:
            pass  # run it anyway so LocalSession reports the error consistently
        lines.clear()
        if not source.strip():
            continue
        if is_quit_call(source):
            return 0
        if cache.is_stale():
            await cache.refresh()
        await session.run(source)
