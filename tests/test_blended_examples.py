"""Runs every example in blended_examples.yaml - the same file the docs show
them from - through a real LiveSession talking to a real server-side
SessionManager, over a stand-in for the websocket and a small fake Home
Assistant. To cover another combination, add an entry to the YAML file.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nanoarrow as na
import pytest
import yaml
from nanoarrow.ipc import StreamWriter

from homeassistant_repl.local_session import LocalSession
from homeassistant_repl.repl import Captured, LiveSession
from homeassistant_repl.sql import SqlResult

EXAMPLES = yaml.safe_load((Path(__file__).parent / "blended_examples.yaml").read_text())

# Load session.py directly: importing the package would pull in Home Assistant.
_path = (
    Path(__file__).parent.parent / "custom_components" / "ha_repl_server" / "session.py"
)
_spec = importlib.util.spec_from_file_location("ha_repl_session_blended", _path)
assert _spec is not None and _spec.loader is not None
session_mod = importlib.util.module_from_spec(_spec)
sys.modules["ha_repl_session_blended"] = session_mod
_spec.loader.exec_module(session_mod)

# The whole of the fake Home Assistant: tree path -> (entity_id, state).
WORLD = {
    "/demo/light/kitchen": ("light.kitchen", "on"),
    "/demo/light/hall": ("light.hall", "off"),
    "/sun/sun/sun": ("sun.sun", "above_horizon"),
}
STATES = dict(WORLD.values())


class FakeStates:
    def get(self, entity_id: str) -> Any:
        return SimpleNamespace(entity_id=entity_id, state=STATES[entity_id])

    def async_all(self) -> list[Any]:
        return [self.get(entity_id) for entity_id in STATES]


class FakeObj:
    def _find(self, path: str, domain: str | None) -> list[tuple[str, str]]:
        return [
            (found, entity_id)
            for found, (entity_id, _) in WORLD.items()
            if found.startswith(path)
            and (domain is None or entity_id.startswith(domain + "."))
        ]

    def find_paths(
        self, path: str = "/", *, domain: str | None = None
    ) -> Iterator[str]:
        return iter([found for found, _ in self._find(path, domain)])

    def find_names(
        self, path: str = "/", *, domain: str | None = None
    ) -> Iterator[str]:
        return iter([entity_id for _, entity_id in self._find(path, domain)])


class FakeHassApi:
    async def get_state(self, *, entity_id: str) -> Any:
        return SimpleNamespace(entity_id=entity_id, state=STATES[entity_id])


def fake_sql(query: str) -> SqlResult:
    """Every query answers with the same two entity ids."""
    column = na.array(["light.kitchen", "sun.sun"], na.string())
    batch = na.c_array_from_buffers(
        na.struct({"entity_id": column.schema}),
        length=2,
        buffers=[],
        children=[column],
    )
    buf = io.BytesIO()
    with StreamWriter.from_writable(buf) as writer:
        writer.write_stream(batch)
    return SqlResult.from_arrow(buf.getvalue())


class Bridge:
    """Stands in for the websocket: hands each call to the server-side
    session and its reply back, both by way of JSON as on the real wire."""

    def __init__(self) -> None:
        self.manager = session_mod.SessionManager({
            "hass": SimpleNamespace(states=FakeStates()),
            "obj": FakeObj(),
        })

    async def call(self, type_: str, **payload: Any) -> Any:
        payload = json.loads(json.dumps(payload))
        if type_ == "ha_repl_server/sessions":
            return {"sessions": self.manager.describe()}
        assert type_ == "ha_repl_server/exec"
        result = await self.manager.get(payload.pop("session")).run(
            payload.pop("code"), payload.pop("timeout", None), **payload
        )
        return json.loads(json.dumps(result.as_dict()))


class Watched(LiveSession):
    """A LiveSession that notes which side each step was sent to."""

    ran: list[str]

    async def _run_local(self, source: str, *, echo: bool) -> bool:
        self.ran.append("local")
        return await super()._run_local(source, echo=echo)

    async def _run_remote(self, source: str, names: Any, *, echo: bool) -> bool:
        self.ran.append("server")
        return await super()._run_remote(source, names, echo=echo)


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["title"] for e in EXAMPLES])
async def test_blended_example(example: dict[str, Any]) -> None:
    bindings = {"hass_api": FakeHassApi(), "sql": fake_sql}
    live = Watched(
        Bridge(),
        "default",
        LocalSession(dict(bindings)),
        shared=frozenset(bindings),
        capture=Captured(),
    )
    live.ran = []
    await live.refresh_remote_names()

    ok = await live.run(example["code"])

    assert live.capture is not None
    if example.get("refused"):
        assert not ok
        assert live.capture.error is not None
        assert live.capture.error["type"] == "HaReplError", live.capture.error
        return
    assert live.capture.error is None, live.capture.error["traceback"]
    assert ok
    assert live.ran == example["runs"]
    if "value" in example:
        assert live.capture.value == example["value"]
