# Developer Shell for Home Assistant 

<img src="assets/icon.svg" width="64" height="64" align="left" alt="A slice of cherry pie, drawn like a 1990s Visual Basic icon">

Home Assistant environment designed for custom component developers and tinkerers. Makes it easy as pie!

Its aims are to make it easier to:

- Exploring of the APIs in context of a live working instance
- Trialling out snippets of code
- Debugging code (but see note below)
- Hotfixing issues that don't have built in support to do so from existing components.

This is primarily for developers of custom components, and their LLM agents, though may be of interest for other folk tinkering with Home Assistant. It is a potentially sharp tool, so NOT appropriate for general Home Assistant users.

>[!NOTE]
> It is not intended to ever be a replacement for a Python debugger, although it may complement one. It also does not intend to replicate [PyScript](https://pyscript.net), instead focusing on standard python (PyScript uses MicroPython) even at expense of general usability or home assistance access, and not a general automation script execution service.


## Dev Shell Server

A HACS component that taps into the Home Assistant and acts as a session server over web sockets.

## Shell

The shell is a full Python REPL shell, implemented as a VSCode NotebookController, with multi-line editing, history etc, living inside an asyncio loop that exposes the live Home Assistant instance as

* `hass` - the `HomeAssistant` class at the root of the Python API
* `obj[]` - the object tree exposed as a dictionary object

So I can write code at the command line like:

```python
pir=obj["/rflink/binary_sensor/hall_pir"]
pir.state="on" # non-strict mode, sets entity state with repl as context
```

The return value of the object is returned to the shell, value printed and available to Python code as `_`. Tracebacks are printed also, as if they were local (in general everything feels like its local)

## Running

Dev instance (devcontainer, or directly on a host with Python 3.14):

```sh
dev/setup.sh                 # installs HA into its own venv + the CLI (devcontainer runs this)
dev/run-ha.sh                # HA on :8123 with custom_components/dev_shell_server symlinked in
uv run python dev/bootstrap.py   # onboard (user dev/dev) and write dev/.env with a token
```

Using it (host or container):

```sh
set -a; . dev/.env; set +a
uv run dev_shell                                         # interactive
uv run dev_shell exec 'hass.states.get("sun.sun")'
uv run dev_shell exec - <<'PY'
await hass.services.async_call("light", "toggle", {"entity_id": "light.kitchen_lights"}, blocking=True)
hass.states.get("light.kitchen_lights").state
PY
```

On a real instance, install via HACS and add **Developer Shell for Home Assistant** from Settings → Devices & services → Add integration (or add `dev_shell_server:` to `configuration.yaml`, which is imported as a config entry), then set `HASS_URL` and `HASS_TOKEN` (an admin long-lived token).

Tests: `uv run pytest` covers the engine without needing HA.
