# Home Assistant REPL

[![Rhizomatics Open Source](https://img.shields.io/badge/rhizomatics%20open%20source-lightseagreen)](https://github.com/rhizomatics)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/rhizomatics/homeassistant-repl/python-package.yml)
[![PyPI](https://img.shields.io/pypi/v/homeassistant-repl)](https://pypi.org/project/homeassistant-repl/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/homeassistant-repl)](https://www.python.org/downloads/)
[![Docs](https://img.shields.io/badge/docs-latest-blue)](https://homeassistant-repl.rhizomatics.org.uk)
![GitHub](https://img.shields.io/github/license/rhizomatics/homeassistant-repl)
![GitHub last commit](https://img.shields.io/github/last-commit/rhizomatics/homeassistant-repl)

<img src="https://homeassistant-repl.rhizomatics.org.uk/assets/icon.png" width="128" height="128" align="left" alt="A slice of cherry pie, drawn like a 1990s Visual Basic icon">

A REPL shell custom designed for Home Assistant custom component developers, tinkerers and native Python speakers. Makes it easy as pie!

It is an opinionated REPL ([Read-Eval-Print-Loop](https://en.wikipedia.org/wiki/Read–eval–print_loop)) shell that aims are to make it easier without any configuration to:

- Exploring of the APIs in context of a live working instance
- Trialling out snippets of code
- Debugging code (but see note below)
- Hotfixing issues that don't have built in support to do so from existing components.

<!-- termynal -->
```bash
$ uv run --with homeassistant-repl ha-repl --token=<insert token here>
Home Assistant REPL (API client mode) connected to ws://homeassistant.local:8123/api/websocket. `obj` only, read-only - no `hass`. Ctrl-D to exit.
>>> obj["/mqtt/binary_sensor/kitchen_terrace_window_tilt"].state
'off'
>>> [o.name for o in obj["/rflink/binary_sensor"].values() if o.state=='unavailable']
['Shed Intruder Alarm', 'Panic Keyfob']
>>> {(o.entity_id, o.state) for o in obj.find(domain="binary_sensor",platform="mqtt")}
{
    ('binary_sensor.terrace_pir_occupancy', 'unavailable'),
    ('binary_sensor.scullery_smoke_alarm_battery_low', 'off'),
    ('binary_sensor.kitchen_terrace_window_tweaked', 'off'),
    ('binary_sensor.scullery_water_detector_water_leak', 'dry'),
    ('binary_sensor.boiler_co_detector_battery_low', 'off'),
    ('binary_sensor.pantry_sensor_occupancy', 'off')
}
>>>
```

If you're not already comfortable using Python tools to manipulate data on the fly, or better REPL shells in other languages, this is a great way to learn, and faster at the keyboard than clicking around Jupyter notebooks.

This is primarily for developers of custom components, and their LLM agents, though may be of interest for other folk tinkering with Home Assistant. It is a potentially sharp tool, so NOT appropriate for general Home Assistant users.

## Features

- Integrated with `rich` for pretty object printouts and stack traces
- Access to all entities via dictionary like interface, `obj`
- Usual multi-line editing support and history of Python
- Integrated with [homeassistant-api](https://pypi.org/project/HomeAssistant-API/) as `hass_api` object, available in both modes
- Auto-awaits coroutines for easy shell use (can be switched off or overridden)
- Dedicated shell that can be run without installation with `uv`
- Usable from inside `ipython` shell, Marimo notebooks or plain `python -m asyncio`

All of the above works with standard Home Assistant APIs, referred to as `api` mode.

Home Assistant REPL also has an advanced `live` mode that taps directly into a live Home Assistant using an optional server component available via [HACS](http://hacs.xyz).

## Live Mode

This mode requires a custom component to be installed on the target Home Assistant server via HACS, or use the supplied scripts to install on a local devcontainer. It adds:

- Full access to the core Home Assistant Python API via `hass`
- Read/write access to the actual objects, e.g. entities and their helpers
- A frisson of danger

### HA REPL Server

A HACS component that taps into the Home Assistant and acts as a session server over web sockets. It has been designed for HomeAssistant 2026.8 or greater.

Needed for `live` mode only, since `api` mode only uses standard Home Assistant APIs.

## Future Developments

See the [Roadmap](./developer/design/roadmap.md) for where this might go, and your feedback welcome.

!!! note
    It is not intended to ever be a replacement for a Python debugger, although it may complement one. It also does not intend to replicate [PyScript](https://pyscript.net), instead focusing on standard python (PyScript uses MicroPython) even at expense of general usability or home assistance access, and not a general automation script execution service. For most non-developer cases, [homeassistant-cli](https://pypi.org/project/homeassistant-cli/) is a better choice, with pre-packaged access to devices, entities, services etc.

## Quick Start

Use the `api` mode with `uv` (traditional install also available via `pip install homeassistant-repl`).

If Home Assistant available via `http://homeassistant.local:8123` then you can skip the `--server` argument. You will also need to create a [Long Lived Access Token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) by going to the personal settings on your Home Assistant mobile or desktop app.

```bash
uv run --with homeassistant-repl ha-repl --token <<<my long lived access token>>>
```

## Using the Shell

The shell is a full Python REPL shell, with multi-line editing, history etc, living inside an asyncio loop that exposes the live Home Assistant instance as:

* `obj` - the [Object Tree](./obj_tree.md) exposed as a dictionary object and common methods
* `hass_api` - the Home Assistant web socket / REST API exposed using the [homeassistant-api](https://pypi.org/project/HomeAssistant-API/) library

In `live` mode it offers, in addition to what `api` mode offers:

* `hass` - the `HomeAssistant` class at the root of the Python API
* `sql`  - [SQL access](./sql.md) to the Recorder databases

So I can write code at the command line like:

```python
>>> pir = obj["/rflink/binary_sensor/hall_pir"]
>>> pir.state = "on"  # non-strict mode, sets entity state with repl as context
```

The return value of the object is returned to the shell, value printed and available to Python code as `_`. Tracebacks are printed also, as if they were local (in general everything feels like its local)

The `homeassistant-api` integration can be used like:

```python
>>> len(hass_api.get_domain('cover'))
12
```

!!! note
    The `hass_api` object returns `AsyncEntity` objects rather than `ApiEntity` objects - this is because it comes from a separate project, [homeassistant-api](https://pypi.org/project/HomeAssistant-API/) that is focused solely on API development, whereas the `ApiEntity` class is designed for custom code development, being a subset of the real `Entity` in HomeAssistant, that you'd also get in `live` mode.

See also [Alternative Integration](alternative_integration.md) options for how to use this in your own plain python shell, ipython, Marimo or similar.



## Starting the Shell

Use the `HASS_SERVER` environment variable, exported or in a local `.env` file, or the `--server` command line argument if the Home Assistant server is not running locally at usual address( i.e. `http://homeassistant.local:8123`). A [long lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) is needed at `--token` or in an `HASS_TOKEN` environment variable.

The quickest way to run the shell is using *uv*, which you can do without cloning this repo or making any other downloads.

```bash
uv run --with homeassistant-repl ha-repl         
```
Get help on the arguments in the usual way,

```bash
uv run --with homeassistant-repl ha-repl --help       ,
```

## Configuration

All modes require a [Long Lived Access Token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) which can be provided via `HASS_TOKEN` enivironment variable, a `HASS_TOKEN=xxxx` line in an `.env` file, or the `--token` argument on `ha-repl`.

The URL will default to `http://homeassistant.local:8123` or can be set using the `HASS_SERVER` enivironment variable, a `HASS_SERVER=xxxx` line in an `.env` file, or the `--server` argument on `ha-repl`.

Note, although `homeassistant-api` itself has different env vars, when used within `ha-repl` it will be set up automatically using the same server and token as the main shell.