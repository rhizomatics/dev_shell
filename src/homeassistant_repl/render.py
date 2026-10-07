"""Showing what the server-side session sends back.

The server (custom_components/ha_repl_server/session.py) does no rendering
of its own - it runs inside Home Assistant, whose bundled rich is whatever
its own dependencies settle on. A value arrives as a tree describing its
shape and an error as its frames and their source lines; both are turned
back into something rich can draw here, where the rich version is ours to
choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.console import RenderResult, group
from rich.syntax import Syntax
from rich.text import Text
from rich.traceback import Frame, PathHighlighter, Stack, Trace, Traceback


class _Repr:
    """Stands in for an object that only exists inside Home Assistant - all
    there is of it here is its repr()."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


class _Struct:
    """Stands in for a dataclass, attrs instance or namedtuple: rich lays one
    of these out from __rich_repr__ just as it would the real thing."""

    def __init__(self, fields: list[tuple[str, Any]]) -> None:
        self._fields = fields

    def __rich_repr__(self) -> Any:
        yield from self._fields

    def __repr__(self) -> str:
        fields = ", ".join(f"{name}={value!r}" for name, value in self._fields)
        return f"{type(self).__name__}({fields})"


_CONTAINERS: dict[str, Any] = {"tuple": tuple, "set": set, "frozenset": frozenset}


def decode_value(node: Any) -> Any:
    """Rebuild the tree session.py's _encode_value() made of a value: real
    containers all the way down, with stand-ins at the leaves for whatever
    couldn't come across as itself."""
    if isinstance(node, list):
        return [decode_value(item) for item in node]
    if not isinstance(node, dict):
        return node
    tag = node["t"]
    if tag == "repr":
        return _Repr(node["r"])
    if tag == "dict":
        return {decode_value(k): decode_value(v) for k, v in node["v"]}
    if tag == "obj":
        # A class of its own, since rich takes the name to show from the type.
        cls = type(node["n"], (_Struct,), {})
        return cls([(name, decode_value(value)) for name, value in node["f"]])
    return _CONTAINERS[tag](decode_value(item) for item in node["v"])


@dataclass
class _RemoteFrame(Frame):
    # The lines around `lineno`, starting at line `first` - sent along with
    # the frame, since the file itself is on the Home Assistant host.
    source: list[str] = field(default_factory=list)
    first: int = 1


class _RemoteTraceback(Traceback):
    """A rich Traceback whose frames carry their own source, instead of
    being read from files on this machine."""

    def __init__(self, trace: Trace, hidden: dict[int, int]) -> None:
        super().__init__(trace)
        self._hidden = hidden

    @group()
    def _render_stack(self, stack: Stack) -> RenderResult:
        path_highlighter = PathHighlighter()
        hidden = self._hidden.get(id(stack), 0)
        for index, frame in enumerate(stack.frames):
            assert isinstance(frame, _RemoteFrame)
            if hidden and index == len(stack.frames) // 2:
                yield Text(
                    f"\n... {hidden} frames hidden ...",
                    justify="center",
                    style="traceback.error",
                )
            if index:
                yield ""
            yield Text.assemble(
                path_highlighter(Text(frame.filename, style="pygments.string")),
                (":", "pygments.text"),
                (str(frame.lineno), "pygments.number"),
                " in ",
                (frame.name, "pygments.function"),
                style="pygments.text",
            )
            if not frame.source:
                continue
            syntax = Syntax(
                # Without its last newline, or it counts as one more (empty) line.
                "".join(frame.source).removesuffix("\n"),
                "python",
                theme=self.theme,
                line_numbers=True,
                start_line=frame.first,
                highlight_lines={frame.lineno},
                word_wrap=self.word_wrap,
                code_width=self.code_width,
                indent_guides=self.indent_guides,
                dedent=False,
            )
            if frame.last_instruction is not None:
                _mark(syntax, frame)
            yield ""
            yield syntax


def _mark(syntax: Syntax, frame: _RemoteFrame) -> None:
    """Underline the part of the frame's source that failed, a line at a
    time so that indentation isn't underlined."""
    assert frame.last_instruction is not None
    (start_line, start_col), (end_line, end_col) = frame.last_instruction
    for line in range(start_line, end_line + 1):
        index = line - frame.first
        if not 0 <= index < len(frame.source):
            continue
        text = frame.source[index].rstrip("\n")
        left = start_col if line == start_line else len(text) - len(text.lstrip())
        right = end_col if line == end_line else len(text)
        # Positions are relative to the text given to Syntax, not `first`.
        syntax.stylize_range(
            "traceback.error_range", (index + 1, left), (index + 1, right)
        )


def remote_traceback(error: dict[str, Any]) -> Traceback | None:
    """The traceback for an error the server reported, or None if it sent no
    frames for it (a SyntaxError, an exception group, or a server too old to
    send any) - its plain "traceback" text is all there is to show then."""
    if not error.get("stacks"):
        return None
    stacks = []
    hidden = {}
    for sent in error["stacks"]:
        stack = Stack(
            exc_type=sent["type"],
            exc_value=sent["message"],
            is_cause=sent["is_cause"],
            notes=sent["notes"],
            frames=[
                _RemoteFrame(
                    filename=f["file"],
                    lineno=f["line"],
                    name=f["name"],
                    last_instruction=(
                        ((f["position"][0], f["position"][1]), tuple(f["position"][2:]))
                        if f["position"]
                        else None
                    ),
                    source=[
                        line if line.endswith("\n") else line + "\n"
                        for line in f["source"]
                    ],
                    first=f["first"],
                )
                for f in sent["frames"]
            ],
        )
        hidden[id(stack)] = sent["hidden"]
        stacks.append(stack)
    return _RemoteTraceback(Trace(stacks=stacks), hidden)
