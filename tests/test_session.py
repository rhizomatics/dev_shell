"""Tests for the execution engine, run without Home Assistant."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

# Load session.py directly: importing the package would pull in Home Assistant.
_path = (
    Path(__file__).parent.parent / "custom_components" / "ha_repl_server" / "session.py"
)
_spec = importlib.util.spec_from_file_location("ha_repl_session", _path)
assert _spec is not None and _spec.loader is not None
session_mod = importlib.util.module_from_spec(_spec)
sys.modules["ha_repl_session"] = session_mod
_spec.loader.exec_module(session_mod)


@pytest.fixture
def manager():
    return session_mod.SessionManager({"hass": "fake-hass"})


async def run(manager, code, session="default", **kw):
    return await manager.get(session).run(code, **kw)


async def test_trailing_expression_is_echoed(manager):
    result = await run(manager, "x = 2\nx * 21")
    assert result.value == "42"
    assert result.error is None


async def test_statement_has_no_value(manager):
    assert (await run(manager, "y = 1")).value is None


async def test_bindings_available(manager):
    assert (await run(manager, "hass")).value == "'fake-hass'"


async def test_namespace_persists_and_resets(manager):
    await run(manager, "a = 5")
    assert (await run(manager, "a + 1")).value == "6"
    assert (await run(manager, "_")).value == "6"
    assert manager.reset("default") is True
    result = await run(manager, "a")
    assert result.error["type"] == "NameError"


async def test_sessions_are_isolated(manager):
    await run(manager, "a = 1", session="one")
    assert (await run(manager, "a", session="two")).error["type"] == "NameError"


async def test_print_and_stderr_captured(manager):
    result = await run(
        manager, "import sys\nprint('hi')\nprint('err', file=sys.stderr)"
    )
    assert result.stdout == "hi\nerr\n"


async def test_top_level_await(manager):
    code = "import asyncio\nawait asyncio.sleep(0)\nawait asyncio.sleep(0, result=7)"
    assert (await run(manager, code)).value == "7"


async def test_unawaited_coroutine_is_awaited_automatically(manager):
    # Forgetting `await` finishes the call instead of handing back an
    # unawaited coroutine object - the shell's job, not asyncio's.
    result = await run(manager, "import asyncio\nc = asyncio.sleep(0, result=9)\nc")
    assert result.value == "9"


async def test_unawaited_coroutine_in_attribute_chain_is_awaited(manager):
    code = (
        "class Thing:\n"
        "    domain = 'light'\n"
        "async def get_thing():\n"
        "    return Thing()\n"
        "get_thing().domain"
    )
    assert (await run(manager, code)).value == "'light'"


async def test_unawaited_coroutine_in_comprehension_is_awaited(manager):
    code = "async def double(n):\n    return n * 2\n[double(i) for i in range(3)]"
    assert (await run(manager, code)).value == "[0, 2, 4]"


async def test_auto_await_false_restores_strict_mode(manager):
    # Opt-out (ha-repl --no-auto-await): forgetting await behaves exactly as
    # it would in component code.
    result = await run(
        manager, "import asyncio\nc = asyncio.sleep(0, result=9)\nc", auto_await=False
    )
    assert result.value.startswith("<coroutine object sleep")
    await run(manager, "c.close()", auto_await=False)


async def test_auto_await_does_not_rewrite_nested_function_bodies(manager):
    # If the rewrite accidentally descended into this plain (sync) nested
    # def's body, wrapping the inner call in `await` would be a SyntaxError
    # ("await outside async function") - it must stay a plain, unawaited
    # call there, auto-awaited only once it surfaces from `wrapper()` itself.
    code = (
        "async def helper():\n"
        "    return 1\n"
        "def wrapper():\n"
        "    return helper()\n"
        "wrapper()"
    )
    result = await run(manager, code)
    assert result.error is None
    assert result.value == "1"


async def test_unawait_returns_bare_coroutine(manager):
    # The escape hatch: the rewrite would otherwise auto-await this on its
    # own, which defeats the purpose of using unawait() here.
    result = await run(manager, "import asyncio\nc = unawait(asyncio.sleep(0))\nc")
    assert result.value.startswith("<coroutine object sleep")
    await run(manager, "c.close()")


async def test_unawait_enables_concurrent_gather(manager):
    # The motivating use case: batch unawaited coroutines for
    # asyncio.gather() instead of each one being awaited where it's created.
    code = (
        "import asyncio\n"
        "async def double(n):\n"
        "    return n * 2\n"
        "await asyncio.gather(*[unawait(double(i)) for i in range(3)])"
    )
    assert (await run(manager, code)).value == "[0, 2, 4]"


async def test_function_definitions_and_closures(manager):
    code = "async def f(n):\n    return [i * n for i in range(3)]\nawait f(2)"
    assert (await run(manager, code)).value == "[0, 2, 4]"


async def test_traceback_starts_at_user_code(manager):
    result = await run(manager, "def boom():\n    1 / 0\nboom()")
    tb = result.error["traceback"]
    assert result.error["type"] == "ZeroDivisionError"
    assert "1 / 0" in tb
    assert "session.py" not in tb


async def test_syntax_error(manager):
    result = await run(manager, "def (")
    assert result.error["type"] == "SyntaxError"


async def test_timeout(manager):
    result = await run(manager, "import asyncio\nawait asyncio.sleep(10)", timeout=0.05)
    assert result.error["type"] == "TimeoutError"


async def test_system_exit_is_reported_not_raised(manager):
    result = await run(manager, "raise SystemExit(3)")
    assert result.error["type"] == "SystemExit"


async def test_executions_serialised_within_session(manager):
    s = manager.get("default")
    await asyncio.gather(s.run("n = 0"), s.run("n += 1"), s.run("n += 1"))
    assert (await s.run("n")).value == "2"


async def test_describe(manager):
    await run(manager, "foo = 1")
    [info] = manager.describe()
    assert info["name"] == "default"
    assert "foo" in info["variables"]
    assert "print" not in info["variables"]
    assert "help" not in info["variables"]
    assert "_maybe_await" not in info["variables"]
    assert "unawait" not in info["variables"]


async def test_help_on_module_is_captured_not_printed(manager, capsys):
    result = await run(manager, "import sys\nhelp(sys)")
    assert "sys" in result.stdout
    captured = capsys.readouterr()
    assert captured.out == ""  # must not leak to the real process stdout


async def test_help_on_keyword(manager):
    result = await run(manager, "help('for')")
    assert "for" in result.stdout.lower()


async def test_help_with_no_args_does_not_hang(manager):
    # pydoc's real interactive help> loop spins the CPU forever here when its
    # input isn't a real tty (confirmed against the stdlib directly, independent
    # of this codebase) - so bare help() shows the intro banner and stops there
    # rather than entering that loop.
    result = await run(manager, "help()")
    assert result.error is None
    assert "Welcome" in result.stdout
    assert "help(hass)" in result.stdout


async def test_doc_urls_loaded_from_yaml_file():
    # Guards the data file itself: it must parse, and a couple of entries we
    # know should be there (one hand-written, one from the per-domain sweep).
    assert (
        session_mod._DOC_URLS["homeassistant.core.HomeAssistant"]
        == "https://developers.home-assistant.io/docs/dev_101_hass"
    )
    assert (
        session_mod._DOC_URLS["homeassistant.components.light.LightEntity"]
        == "https://developers.home-assistant.io/docs/core/entity/light"
    )


async def test_help_shows_preloaded_doc_url_for_known_types(manager):
    # Fake out a HomeAssistant-typed object by monkeypatching the lookup table
    # directly would require importing the real class; instead exercise the
    # lookup helper itself.
    class FakeHass:
        pass

    FakeHass.__module__ = "homeassistant.core"
    FakeHass.__qualname__ = "HomeAssistant"
    assert (
        session_mod._doc_url(FakeHass())
        == "https://developers.home-assistant.io/docs/dev_101_hass"
    )


async def test_help_on_instance_is_condensed(manager):
    # Class docstring + constructor signature + method signatures only - no
    # per-method docstrings (that's what help(obj.the_method) is for) and no
    # live attribute dump.
    code = (
        "class Point:\n"
        "    '''A point.'''\n"
        "    def __init__(self, x, y):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "    def dist(self, other):\n"
        "        '''Distance to another point.'''\n"
        "        return 0\n"
        "p = Point(1, 2)\n"
        "help(p)"
    )
    result = await run(manager, code)
    assert "A point." in result.stdout
    assert "Point(x, y)" in result.stdout
    assert "dist(other)" in result.stdout  # self dropped
    assert "Distance to another point." not in result.stdout
    assert "Current values:" not in result.stdout


async def test_help_excludes_cached_properties_from_methods(manager):
    # cached_property is a non-data descriptor (defines __get__ but not __set__),
    # which fools inspect.isroutine()'s method-descriptor check - it isn't a
    # method, it's a read-only property, and Home Assistant's own entity classes
    # lean on exactly this pattern (functools' and propcache's cached_property)
    # for things like `state`, `name`, `available`.
    code = (
        "import functools\n"
        "class Widget:\n"
        "    @functools.cached_property\n"
        "    def total(self):\n"
        "        return 42\n"
        "    def go(self):\n"
        "        pass\n"
        "w = Widget()\n"
        "help(w)"
    )
    result = await run(manager, code)
    assert "go()" in result.stdout
    assert "total" not in result.stdout


async def test_help_lists_properties_separately_from_methods(manager):
    code = (
        "class Widget:\n"
        "    '''A widget.'''\n"
        "    @property\n"
        "    def size(self):\n"
        "        '''How big it is.'''\n"
        "        return 42\n"
        "    def go(self):\n"
        "        pass\n"
        "w = Widget()\n"
        "help(w)"
    )
    result = await run(manager, code)
    assert "Properties:" in result.stdout
    assert "size - How big it is." in result.stdout
    assert "go()" in result.stdout


async def test_help_signature_formatting(manager):
    code = (
        "import decimal\n"
        "class Box:\n"
        "    def set(self, value: decimal.Decimal = decimal.Decimal(0), *, n: int = 1) -> None:\n"
        "        pass\n"
        "b = Box()\n"
        "help(b)"
    )
    result = await run(manager, code)
    # self dropped, no "-> None", "Decimal" not "decimal.Decimal", no spaces around "=".
    assert "set(value: Decimal=Decimal('0'), *, n: int=1)" in result.stdout
    assert "-> None" not in result.stdout
    assert "self" not in result.stdout


async def test_help_survives_unresolvable_forward_ref_annotation(manager):
    # Real-world trigger: homeassistant.helpers.entity.Entity has a method annotated
    # with "EntityPlatform", a name only imported under TYPE_CHECKING - so eval_str
    # resolution raises NameError (not TypeError/ValueError) deep inside inspect.
    # help() must fall back to the raw annotation rather than crash the session.
    code = (
        "class Widget:\n"
        "    def attach(self, platform: 'EntityPlatform') -> None:\n"
        "        pass\n"
        "w = Widget()\n"
        "help(w)"
    )
    result = await run(manager, code)
    assert result.error is None
    assert "attach(platform" in result.stdout
    assert "EntityPlatform" in result.stdout


async def test_help_constructor_drops_self_return(manager):
    # A class whose __new__ is annotated -> Self shouldn't show "-> Self" on the
    # constructor line (it's implied); session_mod._format_signature is exercised
    # directly since crafting a real __new__ -> Self case in exec'd source is awkward.
    class Widget:
        def __new__(cls) -> Widget:  # noqa: PYI034 - overwritten with Self below
            return super().__new__(cls)

    import typing

    Widget.__new__.__annotations__["return"] = typing.Self
    assert session_mod._format_signature(Widget, drop_return=(typing.Self,)) == "()"


async def test_help_on_class_is_condensed(manager):
    code = (
        "class Point:\n"
        "    '''A point.'''\n"
        "    def __init__(self, x, y):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "help(Point)"
    )
    result = await run(manager, code)
    assert "Help on class Point" in result.stdout
    assert "Point(x, y)" in result.stdout


async def test_help_on_method_keeps_its_docstring(manager):
    # Drilling into one specific method still gets the full pydoc treatment.
    code = (
        "class Point:\n"
        "    def dist(self, other):\n"
        "        '''Distance to another point.'''\n"
        "        return 0\n"
        "p = Point()\n"
        "help(p.dist)"
    )
    result = await run(manager, code)
    assert "Distance to another point." in result.stdout
