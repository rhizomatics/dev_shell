"""REPL sessions: persistent namespaces that execute code on the running event loop.

Deliberately free of Home Assistant imports so it can be tested standalone.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import inspect
import io
import itertools
import linecache
import pydoc
import re
import sys
import textwrap
import time
import traceback
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pygments import lex
from pygments.lexers.python import PythonLexer
from rich.console import Console, Group
from rich.pretty import Pretty
from rich.syntax import Syntax
from rich.text import Text
from rich.traceback import Traceback

MAX_OUTPUT_CHARS = 1_000_000

_cell_counter = itertools.count(1)

# help() on a hass object documents its *type*; pydoc has no docstrings for core HA
# classes written with newcomers in mind, so point at the real docs instead. Keyed by
# fully-qualified class name so it applies no matter what name the object is bound to.
# This list only grows, and is useful independently of the code around it, hence the
# data file rather than a dict literal here.
_DOC_URLS: dict[str, str] = yaml.safe_load(
    (Path(__file__).parent / "doc_urls.yaml").read_text()
)


@dataclass
class ExecResult:
    """Outcome of running one snippet."""

    stdout: str = ""
    value: str | None = None
    error: dict[str, str] | None = None
    duration: float = 0.0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "value": self.value,
            "error": self.error,
            "duration": self.duration,
            "truncated": self.truncated,
        }


@dataclass
class Session:
    """A named namespace that persists between executions until reset."""

    name: str
    globals_: dict[str, Any]
    protected: dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    executions: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def run(
        self,
        source: str,
        timeout: float | None = None,
        *,
        color: bool = False,
        width: int = 88,
        auto_await: bool = True,
    ) -> ExecResult:
        """Execute source in this session, returning captured output and the last value."""
        async with self._lock:
            self.last_used = time.time()
            self.executions += 1
            out = io.StringIO()
            # hass/obj/open are re-seeded every run, not just at session creation, so a
            # snippet that does `obj = obj["/mqtt"]["..."]` only shadows them for its own
            # run - the next command always starts from the real bindings again.
            self.globals_.update(self.protected)
            self.globals_["print"] = _capturing_print(out)
            self.globals_["help"] = _capturing_help(out, color=color, width=width)
            self.globals_["_maybe_await"] = _maybe_await
            self.globals_["unawait"] = unawait
            result = ExecResult()
            start = time.perf_counter()
            try:
                coro = self._execute(source, auto_await=auto_await)
                value = await (asyncio.wait_for(coro, timeout) if timeout else coro)
                if value is not None:
                    self.globals_["_"] = value
                    # A value that already knows how to render itself as a
                    # rich renderable (e.g. the Table from sql(...).show())
                    # is shown as-is instead of being wrapped in Pretty's
                    # generic repr-style rendering, which would just dump
                    # its attributes instead of drawing the table.
                    renderable = (
                        value if hasattr(value, "__rich_console__") else Pretty(value)
                    )
                    result.value = _render(renderable, color=color, width=width)
            except asyncio.CancelledError:
                # Cancellation of the caller must propagate, not be reported as a result.
                raise
            except BaseException as err:  # noqa: BLE001 - report everything, incl. SystemExit
                result.error = _format_error(err, color=color, width=width)
            finally:
                result.duration = time.perf_counter() - start
            result.stdout = out.getvalue()
            for attr in ("stdout", "value"):
                text = getattr(result, attr)
                if text is not None and len(text) > MAX_OUTPUT_CHARS:
                    setattr(result, attr, text[:MAX_OUTPUT_CHARS])
                    result.truncated = True
            return result

    async def _execute(self, source: str, *, auto_await: bool = True) -> Any:
        # Not "<ha_repl-N>": rich.traceback refuses to show source for any
        # filename starting with "<" (treats it like "<stdin>"), no matter what
        # linecache holds. An absolute-looking path sidesteps that - rich joins a
        # relative one onto the cwd before the linecache lookup, which would miss.
        filename = f"/ha_repl/cell_{next(_cell_counter)}"
        # Register the source so tracebacks can show the offending lines.
        linecache.cache[filename] = (
            len(source),
            None,
            source.splitlines(keepends=True),
            filename,
        )
        tree = ast.parse(source, filename, "exec")
        if auto_await:
            tree = _AutoAwait().visit(tree)
            ast.fix_missing_locations(tree)

        # Like the interactive interpreter, echo the value of a trailing expression.
        last_expr = None
        last_stmt = tree.body[-1] if tree.body else None
        if isinstance(last_stmt, ast.Expr):
            tree.body.pop()
            last_expr = ast.Expression(last_stmt.value)

        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        # dont_inherit=True: compile() otherwise inherits this module's own
        # `from __future__ import annotations`, which would make every
        # annotation in the user's code a plain string - breaking the
        # FORWARDREF-based signature rendering in _format_signature below.
        if tree.body:
            await _run_code(
                compile(tree, filename, "exec", flags=flags, dont_inherit=True),
                self.globals_,
            )
        if last_expr is None:
            return None
        return await _run_code(
            compile(last_expr, filename, "eval", flags=flags, dont_inherit=True),
            self.globals_,
        )


def warm_rich_unicode_data() -> None:
    """Rich's first `Console.print()` anywhere in the process lazily
    `import_module()`s a sizeable unicode cell-width table - cheap once
    cached (it's behind rich's own @cache), but as a plain import that
    otherwise happens deep inside a user's first live command, it runs
    straight on the event loop: long enough for HA's blocking-call
    detector to flag it, and in practice long enough to stall the loop's
    other coroutines - including the websocket connection's own
    keepalive, which can make a client see that as a dropped connection.
    Call once, in the executor, at integration setup - every session's
    later _render() then just hits the warmed cache.
    """
    import rich._unicode_data

    rich._unicode_data.load()


def _render(renderable: Any, *, color: bool, width: int) -> str:
    """Render a Rich renderable (a value's pretty repr, a traceback) to text.

    `force_terminal`/`no_color` are set explicitly rather than auto-detected:
    the real terminal is on the far end of a websocket call, not this process,
    so the caller (which does know) decides via `color`.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        highlight=color,
        width=width,
    )
    console.print(renderable, end="")
    return buf.getvalue().rstrip("\n")


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
    plain version only runs if auto-await itself is off (`auto_await=False`)
    or `unawait` is used somewhere the rewrite doesn't reach.
    """
    return value


class _AutoAwait(ast.NodeTransformer):
    """Rewrites every call not already explicitly awaited to go through
    _maybe_await() first, so `hass.async_foo()` works whether or not the
    user remembered `await` - including nested inside attribute access
    (`hass.async_foo().attr`), a comprehension, or an argument list. Leaves
    nested (synchronous) function/lambda bodies alone: `await` there is a
    SyntaxError, and those calls run later, not as part of this statement
    anyway. `unawait(expr)` is the escape hatch - its argument is left
    completely untouched for when the bare coroutine is wanted. Opt out
    entirely with `auto_await=False` on the exec call (strict mode:
    forgetting await behaves exactly as in component code).
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


async def _run_code(code: Any, globals_: dict[str, Any]) -> Any:
    # No separate "bare coroutine" backstop here: when auto_await is on, the
    # _AutoAwait rewrite already resolves every call site, and NOT doing so
    # unconditionally is exactly what `unawait(...)` asks for - a backstop
    # here would silently defeat it for a trailing `unawait(f())`.
    result = eval(code, globals_)  # nosec B307 - the whole point of a dev shell
    if code.co_flags & inspect.CO_COROUTINE:
        result = await result
    return result


def _capturing_print(out: io.StringIO):
    def shell_print(*args: Any, file: Any = None, **kwargs: Any) -> None:
        # stdout and stderr both come back to the client; other files are honoured.
        if file is None or file is sys.stdout or file is sys.stderr:
            file = out
        builtins.print(*args, file=file, **kwargs)

    return shell_print


def _capturing_help(out: io.StringIO, *, color: bool, width: int):
    # A fresh Helper per call, output redirected into the same buffer as print().
    helper = pydoc.Helper(input=io.StringIO(), output=out)

    def shell_help(*args: Any) -> None:
        if not args:
            # help()'s real interactive loop (reading "help> " commands) spins the
            # CPU forever on this Python version when its input isn't a real tty -
            # verified in isolation, nothing project-specific about it - and there's no
            # live stdin to browse with anyway over this request/response API. Show
            # the same intro banner and stop there instead of entering interact().
            helper.intro()
            out.write(
                "\nGet help on any object, with links for known Home Assistant classes\n"
            )
            out.write("\ne.g. help(hass) or help(obj['/sun/sun']).\n")
            return
        if len(args) > 1:
            helper(*args)  # raises the same TypeError real help() would
            return
        (thing,) = args
        if _is_summarisable(thing):
            out.write(_class_summary(thing, color=color, width=width))
        else:
            helper(thing)
        url = _doc_url(thing)
        if url:
            out.write(f"\nSee also: {url}\n")

    return shell_help


def _doc_url(obj: Any) -> str | None:
    cls = obj if inspect.isclass(obj) else type(obj)
    return _DOC_URLS.get(f"{cls.__module__}.{cls.__qualname__}")


def _is_summarisable(obj: Any) -> bool:
    """Classes and plain instances get the condensed summary below; modules,
    functions/methods, and primitives go through pydoc as usual (already short)."""
    return not (
        obj is None
        or isinstance(obj, (bool, int, float, complex, str, bytes))
        or inspect.ismodule(obj)
        or inspect.isroutine(obj)
    )


_SIGNATURE_LEXER = PythonLexer()
# rich.syntax.Syntax itself renders as a block (its own padding/background
# handling, one truecolor SGR sequence per token even with
# background_color="default") - fine stacked one-at-a-time in a real
# terminal, but composed many-to-a-page inside a Group it came out jumbled
# for a class with lots of methods. Lexing with pygments directly and
# building a plain Text keeps this a single clean inline run per line, no
# block rendering involved, and ansi_dark's named 16-colour styles
# (`bright_cyan` etc.) over Syntax's default truecolor theme travel through
# a websocket/terminal pair more predictably.
_SIGNATURE_THEME = Syntax.get_theme("ansi_dark")


def _signature_line(line: str, *, width: int) -> Text:
    """A class/method signature as a one-line syntax-highlighted Text
    (pygments' Python lexer tokenizes it fine even though, as a bare
    "name(args) -> ret" fragment, it isn't valid standalone Python) - so
    type annotations, defaults and operators get real highlighting instead
    of being one flat-coloured string.

    Home Assistant's own methods lean hard on generics (Callable[[Unpack[
    _Ts]], ...]), so a real signature routinely runs past any reasonable
    width - wrapped here with a hanging indent *before* lexing (so the
    inserted newlines/spaces are just more whitespace tokens to pygments)
    rather than left to Rich's own word-wrap, which breaks at the console
    width with no indent at all and reads as a jumble of unrelated lines
    once there are dozens of methods back to back.
    """
    wrapped = "\n".join(
        textwrap.wrap(
            line,
            width=max(width, 20),
            subsequent_indent="      ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    tokens = list(lex(wrapped, _SIGNATURE_LEXER))
    while tokens and not tokens[-1][1].strip():
        tokens.pop()  # pygments always appends a trailing "\n" token
    text = Text(no_wrap=True)
    for token_type, value in tokens:
        text.append(value, style=_SIGNATURE_THEME.get_style_for_token(token_type))
    return text


def _class_summary(thing: Any, *, color: bool, width: int) -> str:
    """A class's full pydoc page repeats its docstring once per method, which
    balloons for a class like HomeAssistant with hundreds of methods. A method's
    own docstring is one `help(hass.the_method)` away, so here we only show the
    class docstring, the constructor signature, and the methods' names and
    signatures (no live attribute values either - that's what `hass.foo` is for).
    """
    cls = thing if inspect.isclass(thing) else type(thing)
    header = (
        f"Help on class {cls.__qualname__} in module {cls.__module__}:"
        if inspect.isclass(thing)
        else f"Help on {cls.__qualname__} object in module {cls.__module__}:"
    )
    parts: list[Any] = [Text(header, style="bold"), Text("")]
    doc = inspect.getdoc(cls)
    if doc:
        parts += [Text(doc), Text("")]
    # A constructor "returning Self" is implied, not useful to state.
    ctor_sig = f"{cls.__qualname__}{_format_signature(cls, drop_return=(typing.Self,))}"
    parts.append(_signature_line(ctor_sig, width=width))
    method_names = sorted(
        name
        for name in dir(cls)
        if not name.startswith("_") and _is_plain_method(getattr(cls, name, None))
    )
    if method_names:
        parts += [Text(""), Text("Methods:", style="bold underline")]
        for name in method_names:
            sig = _format_signature(getattr(cls, name), drop_self=True)
            parts.append(_signature_line(f"  {name}{sig}", width=width))
    return _render(Group(*parts), color=color, width=width)


def _is_plain_method(obj: Any) -> bool:
    # inspect.isroutine() also matches any non-data descriptor (defines __get__ but
    # not __set__) via its ismethoddescriptor() check - which, alongside genuine
    # methods, catches cached_property (both functools's and propcache's, used all
    # over Home Assistant's own entity classes), listing a read-only property as a
    # fake "name(...)" method. isfunction/ismethod is the properly narrow check.
    return inspect.isfunction(obj) or inspect.ismethod(obj)


# Matches a dotted path ending in an identifier, e.g. "homeassistant.core.State"
# or the "collections.abc.Coroutine" inside a bigger type expression - used to
# shorten type annotations down to just the class name.
_DOTTED_NAME = re.compile(r"\b(?:[A-Za-z_]\w*\.)+([A-Za-z_]\w*)\b")

# Python 3.14+ (PEP 649): __annotations__ access eagerly evaluates every annotation
# by default, which raises for names only imported under TYPE_CHECKING - common in
# HA's own codebase (e.g. Entity methods taking an "EntityPlatform"). FORWARDREF
# format resolves what it can and leaves the rest as an inert placeholder instead
# of raising. Not available before 3.14, hence the getattr dance.
_FORWARDREF_FORMAT = getattr(getattr(inspect, "Format", None), "FORWARDREF", None)


def _format_signature(
    target: Any, *, drop_self: bool = False, drop_return: tuple = ()
) -> str:
    """Render target's signature, trimmed down for a quick overview:
    self dropped (when `drop_self`), a void or otherwise uninformative return
    annotation dropped (`drop_return`), type annotations shortened to their bare
    class name, and no padding around `=` for default values.
    """
    sig = None
    if _FORWARDREF_FORMAT is not None:
        try:
            sig = inspect.signature(target, annotation_format=_FORWARDREF_FORMAT)
        except Exception:  # noqa: BLE001 - fall through to the attempts below
            sig = None
    if sig is None:
        try:
            sig = inspect.signature(target, eval_str=True)
        except Exception:  # noqa: BLE001 - unresolvable forward refs can raise almost
            # anything (NameError, AttributeError, ...); a signature with raw/partial
            # annotations is still better than losing the method entirely.
            try:
                sig = inspect.signature(target)
            except TypeError, ValueError:
                return "(...)"
    params = list(sig.parameters.values())
    if drop_self and params and params[0].name == "self":
        sig = sig.replace(parameters=params[1:])
    ret = sig.return_annotation
    if ret is not sig.empty and (
        ret is None or ret is type(None) or any(ret is d for d in drop_return)
    ):
        sig = sig.replace(return_annotation=sig.empty)
    text = _DOTTED_NAME.sub(r"\1", str(sig))
    return re.sub(r"\s*=\s*", "=", text)


def _format_error(err: BaseException, *, color: bool, width: int) -> dict[str, str]:
    tb = err.__traceback__
    # Drop the frames belonging to this module so the traceback starts at user code.
    while tb is not None and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next
    if isinstance(err, SyntaxError):
        # No frames worth showing for this one, just the offending line and caret -
        # a plain rendering already does that job, so it skips the Rich treatment.
        text = "".join(traceback.format_exception_only(type(err), err))
    else:
        text = _render(
            Traceback.from_exception(type(err), err, tb, width=width),
            color=color,
            width=width,
        )
    return {
        "type": type(err).__name__,
        "message": str(err),
        "traceback": text,
    }


@dataclass(frozen=True)
class PerSession:
    """Wraps a zero-arg factory for a binding that needs its own instance in
    every session - e.g. sql's mutable `.limit`, which would otherwise leak
    between sessions since `hass`/`obj` and friends are deliberately one
    shared instance for all of them. SessionManager.get() calls the factory
    once, the first time each session is created.
    """

    factory: Callable[[], Any]


class SessionManager:
    """Holds sessions by name; a session lives until reset or process restart."""

    def __init__(self, bindings: dict[str, Any]) -> None:
        self._bindings = bindings
        self._sessions: dict[str, Session] = {}

    def get(self, name: str) -> Session:
        if (session := self._sessions.get(name)) is None:
            resolved = {
                k: (v.factory() if isinstance(v, PerSession) else v)
                for k, v in self._bindings.items()
            }
            globals_ = {
                "__name__": "__ha_repl__",
                "__builtins__": builtins,
                **resolved,
            }
            session = self._sessions[name] = Session(name, globals_, resolved)
        return session

    def reset(self, name: str) -> bool:
        return self._sessions.pop(name, None) is not None

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "created": s.created,
                "last_used": s.last_used,
                "executions": s.executions,
                "variables": sorted(
                    k
                    for k in s.globals_
                    if not k.startswith("__")
                    and k not in ("print", "help", "_maybe_await", "unawait")
                ),
            }
            for s in self._sessions.values()
        ]
