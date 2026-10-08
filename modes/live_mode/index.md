# Live Mode

Live mode is the most powerful, flexible and dangerous way to play around with Home Assistant. Use it with a devcontainer Home Assistant or development instance unless you really know what you're doing, and even then ...

## Pre-requisites

See [Live Mode Server Install](https://homeassistant-repl.rhizomatics.org.uk/configuration/server_install/index.md)

## Using the Live REPL

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

| Name          | Where it lives                                                                                                                                                                                   |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sql`         | Local. Sends the query to Home Assistant, downloads the result as Arrow data, and gives you a local result object - see [SQL Access](https://homeassistant-repl.rhizomatics.org.uk/sql/index.md) |
| `hass_api`    | Local. A REST API client                                                                                                                                                                         |
| `hass`, `obj` | Inside Home Assistant                                                                                                                                                                            |

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
- **One statement, one side** - `sql` and `hass_api` are local only, `hass` and `obj` are Home Assistant only, so a single statement can't use both. Assign one part to a variable first.

A value that crosses is a copy. Changing it on one side doesn't change it on the other.

### Examples

#### Entity names from `obj`, used locally

What `find_names()` and `find_paths()` return comes back, so the next statement can go through it with anything local - here, the REST client.

```python
names = obj.find_names(domain="light")
{n: hass_api.get_state(entity_id=n).state for n in names}
```

Runs inside Home Assistant, then locally.

#### Tree paths from `obj`, filtered locally

Once the paths are back, going through them is ordinary local Python.

```python
paths = obj.find_paths("/demo")
sorted(p for p in paths if "kitchen" in p)
```

Runs inside Home Assistant, then locally.

#### A local list, used inside Home Assistant

Plain local data is copied over before the statement that needs it.

```python
wanted = ["light.kitchen", "sun.sun"]
{n: hass.states.get(n).state for n in wanted}
```

Runs locally, then inside Home Assistant.

#### A number worked out inside Home Assistant

Numbers and strings come back just as lists of them do.

```python
count = len(hass.states.async_all())
count * 2
```

Runs inside Home Assistant, then locally.

#### Rows from `sql`, looked up in `hass`

A `sql` result is local, and crosses as its rows - a list of lists - when a statement inside Home Assistant loops over it.

```python
r = sql("select entity_id from states_meta")
{row[0]: hass.states.get(row[0]).state for row in r}
```

Runs locally, then inside Home Assistant.

#### Objects stay inside Home Assistant

A state, an entity, or anything else that isn't plain data stays where it is, and later statements that use it run there too.

```python
hass.states.get("sun.sun").state
```

Runs inside Home Assistant.

#### Entity names from `obj`, found in one statement and used in `hass` in another

The names come back as local data, and are copied over again for the statement that needs them inside Home Assistant.

```python
names = obj.find_names(domain="light")
{n: hass.states.get(n).state for n in names}
```

Runs inside Home Assistant, then inside Home Assistant.

### Not Supported

The shell refuses these with an explanation, rather than running them.

#### Both sides in one statement

`hass_api` and `sql` only exist locally, `hass` and `obj` only inside Home Assistant, and a statement runs in one place. Split it in two, as in the first example.

```python
{n: hass_api.get_state(entity_id=n).state for n in obj.find_names()}
```

#### A local function, called on something from `hass`

Only data crosses - functions, classes and dataframes don't.

```python
def shout(text):
    return text.upper()
shout(hass.states.get("sun.sun").state)
```

## One-Shot Snippets and Agents

`ha-repl exec` runs a snippet exactly as the live shell would, then exits - see [Exec Mode](https://homeassistant-repl.rhizomatics.org.uk/modes/exec_mode/index.md), which covers its JSON output and use by coding agents.

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
