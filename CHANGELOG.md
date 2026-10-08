# What's New

!!! note
    For live mode, generally the client and server component have to be on the same version. Different versions *might* work
    with each other for point releases

# 0.11.1
## 🐛 Fixes
### Live Mode
- A name used only in a type annotation no longer decides which side a statement runs on, and no longer stops it running inside Home Assistant because it "only exists in the local session"
- A bare declaration, such as `hass: HomeAssistant`, is no longer treated as an assignment

## ✨ Enhancements
### Plugins
- `homeassistant_repl.plugin` declares `MODE`, `SERVER`, `hass`, `obj`, `sql` and `hass_api` for linters and type checkers, to import under `TYPE_CHECKING`
- The package now carries a `py.typed` marker

## 📚 Documentation
- *Plugins* covers importing a component's own classes, and type checking a plugin

# 0.11.0
## ✨ Enhancements
### Configuration
- Implementation of [RFC-0001: Persistent Config](https://homeassistant-repl.rhizomatics.org.uk/developer/design/rfcs/0001-persistent-config/)
- Servers can be named in `~/.config/ha-repl/config.toml` and chosen with `--server NAME` or `HASS_SERVER=NAME`, with `default` picking one when neither is given. `--server` still takes a URL
  - A server's token comes from the file, from a command (`token_command`, for a secrets manager) or from a named environment variable (`token_env`)
  - `HASS_TOKEN` is only used with a URL, never with a named server
  - `ha-repl servers` lists the servers configured, without their tokens
- Python files in `~/.config/ha-repl/plugins/` are run at the start of every session, including `ha-repl exec`, for helper functions and bookmarked expressions. `--no-plugins` skips them
  - `MODE` (`"live"`, `"exec"` or `"api"`) and `SERVER` (the configured server's name) are set, so one file can adapt to where it is running
- A repo can carry its own `.ha-repl/` directory, layered over the one in the home directory. It is ignored until allowed with `ha-repl trust`, and again whenever its contents change
- `session`, `ttl` and `auto_await` can be given defaults in `config.toml`. `--auto-await` is new, to turn it back on for one run
- `homeassistant_repl.connect()` accepts a server name, e.g. `connect("house")`
- See the new *Configuration* and *Plugins* pages

### API Mode
- `ApiEntity` is closer to the real `Entity`, so more code works unchanged in both `api` and `live` mode
  - `registry` is renamed `registry_entry`, and can be read by attribute as well as by key: `e.registry_entry.unique_id`
  - `platform` is still the integration's name as a string, and also has `platform_name` and `domain`
  - New properties with the real `Entity` names: `unique_id`, `available`, `enabled`, `device_class`, `unit_of_measurement`, `icon`, `entity_picture`, `supported_features`, `assumed_state`, `attribution`, `entity_category`, `has_entity_name` and `translation_key`
  - The same applies to `obj.mode("api")` in live mode, so the server component needs updating too

## 📚 Documentation
- Reorganized and improved the configuration and installation

# 0.10.0
## ✨ Enhancements
### Help
- `help()` on something local - `obj` in API mode, `sql` and its results, tables and rows - now gives a short summary of what it is for, its methods, properties and attributes, as it already did for objects inside Home Assistant. Special methods, inherited members, data descriptors and the method resolution order are left out
- `help(thing, full=True)` gives Python's own full help page, on either side
- `help(obj)` explains how to use the object tree
- Type annotations in the summary are no longer shown in quotes
### SQL
- `sql.table("states")` returns the `Table` object for a single table, by name
- `Table` objects have `class_name` and `description`, the name and docstring of the class the Recorder maps to the table, e.g. `States` and `State change history.`
- Legacy columns - those Home Assistant marks `UNUSED_LEGACY_COLUMN`, still in a table but no longer written to - are now told apart
  - `Column` objects have a `legacy` flag
  - `columns` and `column_names` on a `Table` leave them out, unless the table came from `sql.table("states", legacy=True)`
  - A query result keeps them out of view - in `show()`, rows, dataframes and exports - unless the query names them or `sql(..., legacy=True)` is used. Setting `legacy = True` on the result afterwards brings them into view without a new query
- `show()` on a result no longer cuts off at 6 columns by default, so it shows the same columns as a row from `result[0]`. Use `max_cols` to cap them
- Indexing a result with a row number, e.g. `sql("select count(*) from events")[0]`, returns a `Row` object - its values, readable by position or column name, with its column names and `Table`
- `columns` and `column_names` on a `Table` object are now properties, not methods
## 📚 Documentation
- Added help on using `ha-repl` from the **Studio Code Server** and **Advanced SSH** Home Assistant apps (aka addons), and using `ttyd`
- Lots more examples of how to use all the builtins

# 0.9.0
## Live Server
- No longer needs `homeassistant-api`: `hass_api` is the shell's own local REST client, and is no longer also bound inside Home Assistant
## Live Shell
- A variable assigned inside Home Assistant now comes back, and is local afterwards, when it holds plain data - so `names = obj.find_names(...)` can be followed by local code using `names`. See *Mixing Both Sides* in the live mode docs
- A statement using `sql` or `hass_api` together with `hass` or `obj` is refused with an explanation, rather than failing inside Home Assistant
- `ha-repl --json exec` reports a plain-data value from inside Home Assistant as data, not as its printed form

# 0.8.0
## Live Server
- No longer uses `rich`, so installing no longer upgrades the much older `rich` bundled with Home Assistant 2026.10 - which could fail setup with `cannot import name 'SyntaxPosition' from 'rich.syntax'` until a second restart
  - Values and errors are now sent to the client as data, and rendered there
  - Needs a matching client: an older `ha-repl` still works, but shows plain values and tracebacks
## Live Shell
- Fix for a class whose body calls something (e.g. a `dataclasses.field()` default) failing with `'await' outside function`
- New gallery of examples of blending local and server side objects, the gallery also acts as a set of automated tests

# 0.7.0
## Live Shell
- The live mode is now local first, so arbitrary local imports can be made.
  - `obj` and `hass` are the only fake objects, redirecting their inputs and outputs to the real equivalents inside the live server
  - Both these objects are limited to accepting local primitive values only as arguments, and can't be combined in a single statement
## SQL
- Results object has new features:
  - It can now be sliced like a list, e.g. `[:-10]` for last 10 rows, `[:10]` for first 10
  - `project()` method which takes a list of columns and produces same table with just those columns
  - `sample()` returns a random sample of rows from the table, defaulting to 20, overridable with `count=` 
  -  `export_csv()` method, with same keyword arguments as `csv.writer`
  - `len()` can be called on result set to get size, subject to row limits
  - The results can be iterated over with a list of values per row
    `for e in sql('select * from events'):
        print(e[0],e[5])`
  - The Apache Arrow / Feather data is available as `arrow()` using the PyCapsule interface for max compatibility
  - Internally the data is more efficiently stored, as single native Arrow object
- Queries all executed under `read_only` session scope so no commits expected (this is not a security measure)
## Live Server
- Config switches now to optionally switch off `sql` and/or `hass` access


# 0.6.0
- Live Mode now has direct SQL query access
 - New `sql` global var, with `sql.tables` to show choice of tables
   - `sql('select data_id,shared_data from event_data')`
   - Tabular `show()` on results using `rich`
   - Zero-copy export to polars or pandas dataframes (optional dependencies)
 - Goes through Home Assistant's *Recorder* integration for maximum compatibility with Home Assistant and with any of the DB types used (e.g. sqlite, MySQL, PostgreSQL)
 - Uses `arrow` format for client/server communication for efficiency and compatibility. 
   - Direct integration into `polars` or `pandas` without relying on these libs
   - `nanofeather` used for lightest Arrow implementation
- `custom` mode is now `live` mode
- Version of REPL client shown at start up, and of server if in live mode
- `help()` response now uses `rich` formatting
- Fix for `rich` lazy import breaking connection

## 0.5.1
- Re lock `uv` for release, no code change

## 0.5.0
- `obj` will take a plain entity name now as well as a path
  - For example, `obj("binary_sensor.kitchen_pir")`
- `obj.find`,`obj.find_names` and `obj.find_paths` now take regular expressions
  - For example, `obj.find_names("/mqtt/binary_sensor/kitchen.*")` or `obj.find_names("/mqtt/binary_sensor/.*_leak")` or just `obj.find_names(".*_leak")`
- Renamed `--url` to `--server` on `ha-repl` for consistency with environment variable names

## 0.4.0
- `live` mode now offers switch back to `api` mode when calling `obj`
  - Use `obj.mode("api")` or `obj.mode("live")` to control session
- To minimize confusion, the [homeassistant-api](https://github.com/HomeAssistant-API/HomeAssistantAPI) integration now uses the `hass_api` variable

## 0.3.0
- Integrated [homeassistant-api](https://github.com/HomeAssistant-API/HomeAssistantAPI) client object as `api` variable for pure REST/WS API access
  - `api.get_state(entity_id="binary_sensor.kitchen_pir")`
  - `api` has its own `AsyncEntity` class which differs from the `ApiEntity` used by the core REPL
     - reason is that `ApiEntity` is designed to offer a subset of the real entity and allow easier prototyping of custom component code whereas `AsyncEntity` is the `homeassistant-api` package way of working purely with API data
- Auto await will do the `await` for you with coroutines if you forget.
  - Can be switched off completely with `--no-auto-await`
  - Can be one-off switched off by doing an `unawait` to get raw coroutine
- Replaced use of `websockets` with `niquests[ws]`

## 0.2.0
- Renamed to `Home Assistant REPL` and cli tool to `ha_repl` to make it clearer what it does and avoid confusion in package name space or terminal with other dev shells.

## 0.1.2
- Simpler `connect` method to use the API and `obj` from any Python async code
- Environment variables can be sourced from an `.env` file

## 0.1.1
- Changed `api` mode prompt to standard Python `>>>`
- Improved docs and tests
- Default Home Assistant URL is now `http://homeassistant.local:8123`

## 0.1.0

Initial working shell, useful in both `api` (API use only) and `custom` (custom server component) modes.

See [Roadmap](./developer/design/roadmap.md) for where this might go next.