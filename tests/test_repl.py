"""Tests for live mode's LiveSession - which side each command runs on, and
what's copied across - against a fake websocket client, so no Home
Assistant is involved."""

from __future__ import annotations

from typing import Any

from homeassistant_repl.local_session import LocalSession
from homeassistant_repl.repl import LiveSession, is_quit_call


class FakeClient:
    """Records exec calls; `variables` stands in for the server session's
    own namespace as ha_repl_server/sessions would report it."""

    def __init__(self, variables: list[str] | None = None) -> None:
        self.executed: list[str] = []
        self.variables = variables or ["hass", "obj", "sql", "hass_api"]
        self.fail_on: str | None = None

    async def call(self, type_: str, **payload: Any) -> Any:
        if type_ == "ha_repl_server/sessions":
            return {"sessions": [{"name": "default", "variables": self.variables}]}
        assert type_ == "ha_repl_server/exec"
        self.executed.append(payload["code"])
        error = None
        if self.fail_on and self.fail_on in payload["code"]:
            error = {"traceback": "ImportError: nope\n"}
        return {
            "stdout": "",
            "value": None,
            "arrow": None,
            "arrow_truncated": False,
            "error": error,
            "duration": 0.0,
            "truncated": False,
        }


def _live(client: FakeClient, **bindings: Any) -> LiveSession:
    return LiveSession(
        client,
        "default",
        LocalSession(dict(bindings)),
        shared=frozenset(bindings),
    )


async def test_plain_python_runs_locally_and_never_reaches_the_server():
    client = FakeClient()
    live = _live(client)

    await live.run("import json\nx = json.dumps([1, 2])")

    assert client.executed == []
    assert live.local.globals_["x"] == "[1, 2]"


async def test_command_using_hass_or_obj_runs_on_the_server():
    client = FakeClient()
    live = _live(client)

    await live.run('hass.states.get("sun.sun")')
    await live.run('obj["/sun"]')

    assert client.executed == ['hass.states.get("sun.sun")', 'obj["/sun"]']


async def test_variable_left_on_the_server_routes_later_commands_there():
    client = FakeClient()
    live = _live(client)

    await live.run('s = hass.states.get("sun.sun")')
    client.variables.append("s")
    await live.refresh_remote_names()
    await live.run("s.state")

    assert client.executed[-1] == "s.state"
    assert "s" not in live.local.globals_


async def test_name_on_both_sides_counts_as_local():
    client = FakeClient(["hass", "obj", "sql", "x"])
    live = _live(client, sql="local sql")
    await live.refresh_remote_names()

    await live.run("x = 5")
    await live.run("y = (x, sql)")

    assert client.executed == []
    assert live.local.globals_["y"] == (5, "local sql")


async def test_plain_local_data_is_copied_over_before_a_server_command():
    client = FakeClient()
    live = _live(client)

    await live.run('eid = "sun.sun"\nwanted = {"a": [1, 2.5, None]}')
    await live.run("hass.states.get(eid), wanted")

    assert client.executed == [
        "eid = 'sun.sun'\nwanted = {'a': [1, 2.5, None]}",
        "hass.states.get(eid), wanted",
    ]


async def test_locally_imported_module_is_imported_on_the_server_too():
    client = FakeClient()
    live = _live(client)

    await live.run("import json as j")
    await live.run("j.dumps(hass.config.as_dict())")

    assert client.executed == [
        "import json as j",
        "j.dumps(hass.config.as_dict())",
    ]


async def test_failed_copy_stops_before_the_command_itself(capsys):
    client = FakeClient()
    client.fail_on = "import json"
    live = _live(client)

    await live.run("import json")
    await live.run("json.dumps(hass.config.as_dict())")

    assert client.executed == ["import json as json"]
    assert "ImportError" in capsys.readouterr().err


async def test_uncopyable_local_value_refuses_the_command_with_a_reason(capsys):
    client = FakeClient()
    live = _live(client)

    await live.run("class Frame: pass\ndf = Frame()")
    await live.run("hass.states.get(df)")

    assert client.executed == []
    err = capsys.readouterr().err
    assert "`df`" in err and "`hass`" in err


async def test_uncopyable_local_name_rebound_by_the_command_is_ignored():
    client = FakeClient()
    live = _live(client)

    await live.run("class Frame: pass\ns = Frame()")
    await live.run("[s.entity_id for s in hass.states.async_all()]")

    assert client.executed == ["[s.entity_id for s in hass.states.async_all()]"]


async def test_shared_bindings_are_not_copied_over():
    client = FakeClient()
    live = _live(client, sql=object(), hass_api=None)

    await live.run("hass, sql, hass_api")

    assert client.executed == ["hass, sql, hass_api"]


async def test_underscore_follows_whichever_side_answered_last():
    client = FakeClient(["hass", "obj", "_"])
    live = _live(client)
    await live.refresh_remote_names()

    await live.run("1 + 1")
    assert live.local.globals_["_"] == 2
    await live.run("_")
    assert client.executed == []

    await live.run("hass.config.version")
    await live.run("_")
    assert client.executed == ["hass.config.version", "_"]


async def test_syntax_error_is_reported_locally(capsys):
    client = FakeClient()
    live = _live(client)

    await live.run("hass.(")

    assert client.executed == []
    assert "SyntaxError" in capsys.readouterr().err


def test_is_quit_call():
    assert is_quit_call("quit()")
    assert is_quit_call("exit()")
    assert not is_quit_call("quit(1)")
    assert not is_quit_call("x = quit()")
