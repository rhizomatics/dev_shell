"""`help()` for code that runs here rather than inside Home Assistant - API
client mode's `obj`, live mode's `sql` and its results, anything of your own.

The same idea as the server's condensed help (custom_components/
ha_repl_server/session.py, `_class_summary`), written again here rather
than shared, like the rest of the two sides: what an object is for and what
you can call on it, not pydoc's full page - which for a class adds every
special method, inherited method, data descriptor and the method resolution
order.
`help(thing, full=True)` is that full page.
"""

from __future__ import annotations

import dataclasses
import inspect
import pydoc
import sys
import textwrap
from typing import Any

from rich.console import Console
from rich.text import Text

_INTRO = (
    "help(thing) summarises an object: what it is, and its methods, "
    "properties and attributes.\n"
    "help(thing, full=True) is Python's own full help page: every method's "
    "docstring, special methods, inherited members and data descriptors.\n\n"
    "e.g. help(obj), help(sql) or help(sql.table('states'))"
)


def make_help(console: Console) -> Any:
    """A `help` that prints to this console."""

    def shell_help(*args: Any, full: bool = False) -> None:
        if not args:
            # Python's own help() with no arguments starts an interactive
            # prompt, which has no place inside this one.
            console.print(_INTRO, markup=False, highlight=False)
            return
        if len(args) > 1:
            raise TypeError(f"help() takes one object, not {len(args)}")
        (thing,) = args
        if full or not _is_summarisable(thing):
            # An explicit output, so pydoc writes the page out rather than
            # opening a pager on top of the shell.
            pydoc.Helper(output=sys.stdout)(thing)
            return
        console.print(_summary(thing, console.width))

    return shell_help


def _is_summarisable(obj: Any) -> bool:
    """Classes and instances get the summary; modules, functions, strings
    (a topic or keyword to pydoc) and other primitives go to pydoc, whose
    page for those is short already."""
    return not (
        obj is None
        or isinstance(obj, (bool, int, float, complex, str, bytes))
        or inspect.ismodule(obj)
        or inspect.isroutine(obj)
    )


def _summary(thing: Any, width: int) -> Text:
    is_class = inspect.isclass(thing)
    cls = thing if is_class else type(thing)
    out = Text()

    def section(title: str, lines: list[str]) -> None:
        if lines:
            out.append("\n")
            out.append(title + "\n", style="bold underline")
            out.append("\n".join(lines) + "\n")

    kind = f"class {cls.__qualname__}" if is_class else f"{cls.__qualname__} object"
    out.append(f"Help on {kind} in module {cls.__module__}:\n", style="bold")
    doc = inspect.getdoc(cls)
    # A dataclass with no docstring of its own gets its signature as one.
    if doc and not doc.startswith(f"{cls.__name__}("):
        out.append(f"\n{doc}\n")

    if is_class:
        section("Create:", [_wrap(f"  {cls.__qualname__}{_signature(cls)}", width)])
    else:
        call = getattr(cls, "__call__", None)  # noqa: B004 - the method itself is wanted
        if inspect.isfunction(call):
            sig = _signature(call, drop_self=True)
            section("Call it:", [_wrap(f"  {_name_of(thing)}{sig}", width)])

    members = {
        name: inspect.getattr_static(cls, name, None)
        for name in dir(cls)
        if not name.startswith("_")
    }
    section(
        "Methods:",
        [
            _wrap(f"  {name}{_signature(member, drop_self=True)}", width)
            for name, member in members.items()
            if inspect.isfunction(member)
        ],
    )
    section(
        "Properties:",
        [
            _described(name, inspect.getdoc(member), width)
            for name, member in members.items()
            if isinstance(member, property)
        ],
    )
    if dataclasses.is_dataclass(cls):
        section(
            "Attributes:",
            [
                _wrap(f"  {field.name}: {_type_name(field.type)}", width)
                for field in dataclasses.fields(cls)
                if not field.name.startswith("_")
            ],
        )
    out.append("\nhelp(thing, full=True) for Python's own full help page.")
    return out


def _name_of(thing: Any) -> str:
    """What an instance is bound as in the shell, if it's one of ours."""
    return {"SqlTool": "sql"}.get(type(thing).__name__, type(thing).__name__)


def _described(name: str, doc: str | None, width: int) -> str:
    first = " ".join((doc or "").split("\n\n")[0].split())
    return _wrap(f"  {name}" + (f" - {first}" if first else ""), width)


def _wrap(line: str, width: int) -> str:
    return textwrap.fill(
        line,
        width=max(width, 20),
        subsequent_indent="      ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def _type_name(annotation: Any) -> str:
    return (
        annotation
        if isinstance(annotation, str)
        else inspect.formatannotation(annotation)
    )


def _signature(target: Any, *, drop_self: bool = False) -> str:
    try:
        sig = inspect.signature(target)
    except TypeError, ValueError:
        return "(...)"
    params = list(sig.parameters.values())
    if drop_self and params and params[0].name == "self":
        sig = sig.replace(parameters=params[1:])
    if sig.return_annotation in (None, "None", type(None)):
        sig = sig.replace(return_annotation=sig.empty)
    # Annotations are plain strings in a module using `from __future__ import
    # annotations` - shown as written, not as quoted strings.
    return sig.format(quote_annotation_strings=False)
