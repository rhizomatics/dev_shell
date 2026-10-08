# Live Mode

Live mode is the most powerful, flexible and dangerous way to play around with Home Assistant. Use it with a devcontainer Home Assistant or development instance unless you really know what you're doing, and even then ...

## Pre-requisites

See [Live Mode Server Install](../configuration/server_install.md)

## Using the Live REPL

![HACS Component Direct Access](../assets/screenshots/hacs_comp_live.png)

Run `ha-repl` with the `live` argument

The quickest way to run the shell is using *uv*, which you can do without cloning this repo or making any other downloads.

```bash
uv run --with homeassistant-repl ha-repl live     
```

Once you're in, `hass` gives you direct access to the running instance of the [HomeAssistant](https://developers.home-assistant.io/docs/dev_101_hass) class, from which everything else is accessible, its the ultimate god object for the platform.

Also, `sql` gives you query access to the primary HomeAssistant database.

Get help on the arguments in the usual way.

`hass` and `sql` can be used together in a limited way, a statement at a time - see [Mixing Both Sides](#mixing-both-sides).

## What Runs Where

The live shell is an ordinary Python session on your own machine. Import whatever you have installed, define functions, keep dataframes around - none of that touches Home Assistant.

| Name | Where it lives |
| ---- | -------------- |
| `sql` | Local. Sends the query to Home Assistant, downloads the result as Arrow data, and gives you a local result object - see [SQL Access](../sql.md) |
| `hass_api` | Local. A REST API client |
| `hass`, `obj` | Inside Home Assistant |

Since `hass` and `obj` only exist inside Home Assistant, any statement that uses one of them is sent there and run there, with its result sent back to be displayed. A variable assigned by such a statement stays there too, and later statements that use it follow it:

```python
>>> import polars as pl                      # local
>>> df = sql("select * from states").to_polars()   # local, on downloaded data
>>> s = hass.states.get("sun.sun")           # runs inside Home Assistant
>>> s.state                                  # so does this - `s` lives there
'below_horizon'
```

The decision is made per statement, so several lines pasted or run together can use both sides.

## Mixing Both Sides

The two sides are separate Python sessions, and the shell does a small amount of work to let a statement on one side use a value from the other. Treat it as a convenience for simple cases rather than something to build on: it is deliberately limited, and anything it can't do is refused rather than attempted.

What it does:

- **Only plain data crosses** - strings, numbers, `None`, and lists, tuples, sets and dicts of them, up to about 100,000 characters.
- **Local to Home Assistant** - a local variable holding plain data is copied over before a statement that uses it. A module you imported locally is imported there under the same name. A `sql` result crosses as its rows, a list of lists.
- **Home Assistant to local** - a variable assigned inside Home Assistant comes back, and is local from then on, when what it holds is plain data. So does what `obj.find_names()` and `obj.find_paths()` return: an iterator over strings, good for going through once.
- **Everything else stays where it is** - states, entities, dataframes, functions and classes don't cross in either direction.
- **Annotations are ignored** - a name used only in a type annotation doesn't decide where a statement runs, and is never copied over.
- **One statement, one side** - `sql` and `hass_api` are local only, `hass` and `obj` are Home Assistant only, so a single statement can't use both. Assign one part to a variable first.

A value that crosses is a copy. Changing it on one side doesn't change it on the other.

### Examples

<!-- blended-examples -->

## One-Shot Snippets and Agents

`ha-repl exec` runs a snippet exactly as the live shell would, then exits - see [Exec Mode](exec_mode.md), which covers its JSON output and use by coding agents.

## Running from a clone/fork of this repo

If you do have this repo checked out, you can also use a direct `run` which means you can also tinker locally with `homeassistant_repl` code.

```bash
uv run ha-repl         
```

### Install Local Home Assistant with the Live Server

The `homeassistant-repl` repo has a [DevContainer](https://containers.dev) defined, and helper scripts in the `/dev` directory to manage it.

Dev instance (devcontainer, or directly on a host with Python 3.14):

```sh
dev/setup.sh                 # installs HA into its own venv + the CLI (devcontainer runs this)
dev/restart-ha.sh            # Kill current HA instance and start a new
dev/run-ha.sh                # HA on :8123 with custom_components/ha_repl_server symlinked in
uv run python dev/bootstrap.py   # onboard (user dev/dev) and write dev/.env with a token
```

Using it (host or container):

```sh
set -a; . dev/.env; set +a
uv run ha-repl                                         # interactive
uv run ha-repl exec 'hass.states.get("sun.sun")'
uv run ha-repl exec - <<'PY'
await hass.services.async_call("light", "toggle", {"entity_id": "light.kitchen_lights"}, blocking=True)
hass.states.get("light.kitchen_lights").state
PY
```
