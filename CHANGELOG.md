# What's New

# 0.7.0
## Live Mode
- The live mode is now local first, so arbitrary local imports can be made.
  - `obj` and `hass` are the only fake objects, redirecting their inputs and outputs to the real equivalents inside the live server
  - Both these objects are limited to accepting local primitive values only as arguments
## SQL
- Results object has new features
  - It can now be sliced like a list, e.g. `[:-10]` for last 10 rows, `[:10]` for first 10
  - `project()` method which takes a list of columns and produces same table with just those columns
  - `sample()` returns a random sample of rows from the table, defaulting to 20, overridable with `count=` 
  -  `export_csv()` method, with same keyword arguments as `csv.writer`
  - `len()` can be called on result set to get size, subject to row limits
  - The results can be iterated over with a list of values per row
    `for e in sql('select * from events'):
        print(e[0],e[5])`
  - The Apache Arrow / Feather data is available as `arrow()` using the PyCapsule interface for max compatibility
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