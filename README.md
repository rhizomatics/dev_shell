# Developer Shell for Home Assistant 

<img src="assets/icon.svg" width="64" height="64" align="left" alt="A slice of cherry pie, drawn like a 1990s Visual Basic icon">

Home Assistant environment designed for custom component developers and tinkerers. Makes it easy as pie!

It is an opinionated shell that aims are to make it easier without any configuration to:

- Exploring of the APIs in context of a live working instance
- Trialling out snippets of code
- Debugging code (but see note below)
- Hotfixing issues that don't have built in support to do so from existing components.

This is primarily for developers of custom components, and their LLM agents, though may be of interest for other folk tinkering with Home Assistant. It is a potentially sharp tool, so NOT appropriate for general Home Assistant users.

Other features:

- Integrated with `rich` for pretty object printouts and stack traces
- Access to all entities via dictionary like interface, `obj`
- Usual multi-line editing support and history of Python

All of the above works with standard Home Assistant APIs. 

Dev Shell also has an advanced `direct` mode that taps directly into a live Home Assistant using an optional server component available via [HACS](http://hacs.xyz).

Direct mode adds:

- Access to the core Home Assistant Python API via `hass`
- Read/write access to objects, e.g. entity state
- A frisson of danger


>[!NOTE]
> It is not intended to ever be a replacement for a Python debugger, although it may complement one. It also does not intend to replicate [PyScript](https://pyscript.net), instead focusing on standard python (PyScript uses MicroPython) even at expense of general usability or home assistance access, and not a general automation script execution service.

See the [Roadmap](docs/developer/design/roadmap.md) for where this might go, and your feedback welcome.


## Dev Shell Server

A HACS component that taps into the Home Assistant and acts as a session server over web sockets. 

Needed for `direct` mode only, since `safe` mode only uses standard Home Assistant APIs.

## Shell

The shell is a full Python REPL shell, implemented as a VSCode NotebookController, with multi-line editing, history etc, living inside an asyncio loop that exposes the live Home Assistant instance as

* `obj` - the object tree exposed as a dictionary object and common methods

In `direct` mode it also offers:

* `hass` - the `HomeAssistant` class at the root of the Python API

So I can write code at the command line like:

```python
pir=obj["/rflink/binary_sensor/hall_pir"]
pir.state="on" # non-strict mode, sets entity state with repl as context
```

The return value of the object is returned to the shell, value printed and available to Python code as `_`. Tracebacks are printed also, as if they were local (in general everything feels like its local)

## Object Tree

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
obj["/rflink/sensor/shed_temperature"].state. # prints out temperature
obj["/rflink"]        # the 'light','binary_sensor' and 'sensor' subtrees for rflink
obj["/rflink/sensor"] # all the sensors for rflink
len(obj["/rflink/sensor"]) # count of rflink sensors in this example
```
#### Example Tree

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
{o.entity_id, o.state for o in objs.find(domain="binary_sensor")}
```

#### `obj.find_names(..)`

Identical to `obj.find()` except it only returns an iterable of the object paths rather than the objects themselves.

```python
list(objs.find(area="kitchen"))  # list names of all entities in kitchen
sorted(objs.find(area=["kitchen","shed"])) 
```

#### `obj.show(..)`

The `show` function will give a pretty version of an object where it knows how. For entities this means it combines the `RegistryEntry` and `Entity` information, drops some boring internal stuff, filters out all the `None` values and turns `datetime.datetime` structures into local date times.

```yaml
obj.show('/unifi/sensor/kitchen_wifi_cpu_utilization')
```

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
