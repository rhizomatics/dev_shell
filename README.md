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

Home Assistant REPL also has an advanced `custom` mode that taps directly into a live Home Assistant using an optional server component available via [HACS](http://hacs.xyz).

## Custom Mode

This mode requires a custom component to be installed on the target Home Assistant server via HACS, or use the supplied scripts to install on a local devcontainer. It adds:

- Full access to the core Home Assistant Python API via `hass`
- Read/write access to the actual objects, e.g. entities and their helpers
- A frisson of danger

### HA REPL Server

A HACS component that taps into the Home Assistant and acts as a session server over web sockets. It has been designed for HomeAssistant 2026.8 or greater.

Needed for `custom` mode only, since `api` mode only uses standard Home Assistant APIs.

## Future Developments

See the [Roadmap](./developer/design/roadmap.md) for where this might go, and your feedback welcome.

>[!NOTE]
> It is not intended to ever be a replacement for a Python debugger, although it may complement one. It also does not intend to replicate [PyScript](https://pyscript.net), instead focusing on standard python (PyScript uses MicroPython) even at expense of general usability or home assistance access, and not a general automation script execution service. For most non-developer cases, [homeassistant-cli](https://pypi.org/project/homeassistant-cli/) is a better choice, with pre-packaged access to devices, entities, services etc.

## Quick Start

Use the `api` mode with `uv` (traditional install also available via `pip install homeassistant-repl`).

If Home Assistant available via `http://homeassistant.local:8123` then you can skip the `--server` argument. You will also need to create a [Long Lived Access Token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) by going to the personal settings on your Home Assistant mobile or desktop app.

```bash
uv run --with homeassistant-repl ha-repl --token <<<my long lived access token>>>
```

## Using the Shell

The shell is a full Python REPL shell, with multi-line editing, history etc, living inside an asyncio loop that exposes the live Home Assistant instance as:

* `obj` - the object tree exposed as a dictionary object and common methods
* `hass_api` - the Home Assistant web socket / REST API exposed using the [homeassistant-api](https://pypi.org/project/HomeAssistant-API/) library

In `custom` mode it offers, in addition to what `api` mode offers:

* `hass` - the `HomeAssistant` class at the root of the Python API

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

>[!NOTE]
> The `hass_api` object returns `AsyncEntity` objects rather than `ApiEntity` objects - this is because it comes from a separate project, [homeassistant-api](https://pypi.org/project/HomeAssistant-API/) that is focused solely on API development, whereas the `ApiEntity` class is designed for custom code development, being a subset of the real `Entity` in HomeAssistant, that you'd also get in `live` mode.

See also [Alternative Integration](alternative_integration.md) options for how to use this in your own plain python shell, ipython, Marimo or similar.

## The Object Tree

All of the objects (only entities for now) are arranged in a giant tree, like a file system, exposed as the global variable `objs` and implemented as Python [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping) object (which provides the [MappingView](https://docs.python.org/3/library/collections.abc.html#collections.abc.MappingView) views [ItemsView](https://docs.python.org/3/library/collections.abc.html#collections.abc.ItemsView) and [KeysView](https://docs.python.org/3/library/collections.abc.html#collections.abc.KeysView))

`objs` offers:

- Dictionary style access, using `[]`
  - Raises `KeyError` if entity or sub-path doesn't exist
  - Each level of the tree returns a sub-tree
  - `keys()`,`values()`,`items()` of the sub-tree shows only that level
  - An `OrderedView` is used rather than plain `MappingView` so can be accessed like a `list` and items are alphabetically organized
- `find()`
  - Returns a flat iterable of the entire tree
  - Optionally restrict by `platform`,`area`,`label`
- `show()`
  - Dump the most useful info on an object to console

All the usual Python tricks can of course also be used, iterators, comprehensions, classes, lambdas or a simple `len()`.

### `obj[]`

This allows dictionary ('Mapping') access to the object tree.

In the example tree below, the objects and subtrees of objects can be accessed like:

```python
>>> obj["/rflink/sensor/shed_temperature"].state  # prints out temperature
>>> obj[
    "/rflink/sensor/shed_temperature"
].state_attributes  # prints out additional attributes
>>> obj["/rflink"]  # the 'light','binary_sensor' and 'sensor' subtrees for rflink
>>> obj["/rflink/sensor"]  # all the sensors for rflink
>>> len(obj["/rflink/sensor"])  # count of rflink sensors in this example
```

##### Example Tree

```
...
- mqtt
- rflink
  - light
    - staircase_ceiling
    - shed
  - switch
    - upstairs_pixie
  - binary_sensor
    - porch_pir
    - shed_door
  - sensor
    - shed_temperature
    - kitchen_humidity
...
```

>[!NOTE]
>In the roadmap, there will be a visual Object Browser to view and select entities. For now, it is accessible only via Python code. It also may extend beyond entities, to things like areas, users, categories and devices.

#### `obj.find('..')`

Where the dictionary access gives a nested directory view of the object tree, `find` provides a flat iteration with no order guarantees, so its fast and simple and can be sorted the usual Python way if needed.

`find` also has built in filters, to narrow the big list of objects by one or more `platform`,`domain`,`area`,`label` - each of these will take a single string or list of strings, and they can be combined to narrow down the list.

```python
>>> {(o.entity_id, o.state) for o in obj.find(domain="binary_sensor")}
```

##### Raw Objects

In API Client mode, `find()` returns a local proxy for the remote class, normalized to look more like the same object you'd get in custom mode. Switching `raw=True` will bypass this and you'll get the object untouched as it was received from the API.

#### `obj.find_paths(..)`

Identical to `obj.find()` except it only returns an iterable of the object paths rather than the objects themselves. Ideal for plugging into some logic that will then call `obj[path]` on each one.

```python
>>> list(objs.find(area="kitchen"))  # list names of all entities in kitchen
>>> sorted(objs.find(area=["kitchen", "shed"]))
```

#### `obj.find_names(..)`

Same as `obj.find_paths()` except it returns the object bare name, as it would appear in Home Assistant, e.g. `sensor.bathroom_humidity`

#### `obj.show(..)`

The `show` function will give a pretty version of an object where it knows how. For entities this means it combines the `RegistryEntry` and `Entity` information, drops some boring internal stuff, filters out all the `None` values and turns `datetime.datetime` structures into local date times.

```yaml
obj.show('/unifi/sensor/kitchen_wifi_cpu_utilization')
```

## Starting the Shell

Use the `HASS_SERVER` environment variable, exported or in a local `.env` file, or the `--server` command line argument if the Home Assistant server is not running locally at usual address( i.e. `http://homeassistant.local:8123`). A [long lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) is needed at `--token` or in an `HASS_TOKEN` environment variable.

### Custom Mode for Real Server

On a real instance, install via HACS:
 - it's not in the default HACS repository, so you'll have to add `https://github.com/rhizomatics/homeassistant-repl` as a Custom Repository from the top-right dot menu first
 - Search for *Home Assistant REPL* in the HACS menu and choose *Download*
 - Restart Home Assistant for it to recognize the new custom component available
 - From **Settings → Devices & services → Add integration** find **Home Assistant REPL Server** in the list and install, there's no further config needed
   - Alternatively add `ha_repl_server:` to `configuration.yaml`, which is imported as a config entry) 
 - Run `ha-repl` with the `custom` argument


The quickest way to run the shell is using *uv*, which you can do without cloning this repo or making any other downloads.

```bash
uv run --with homeassistant-repl ha-repl         
```
Get help on the arguments in the usual way,

```bash
uv run --with homeassistant-repl ha-repl --help       ,
```

If you do have this repo checked out, you can also use a direct `run` which means you can also tinker locally with `homeassistant_repl` code.

```bash
uv run ha-repl         
```

## Configuration

All modes require a [Long Lived Access Token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token)which can be provided via `HASS_TOKEN` enivironment variable, a `HASS_TOKEN=xxxx` line in an `.env` file, or the `--token` argument on `ha-repl`.

The URL will default to `http://homeassistant.local:8123` or can be set using the `HASS_SERVER` enivironment variable, a `HASS_SERVER=xxxx` line in an `.env` file, or the `--server` argument on `ha-repl`.

Note, although `homeassistant-api` itself has different env vars, when used within `ha-repl` it will be set up automatically using the same server and token as the main shell.