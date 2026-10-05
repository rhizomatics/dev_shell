# What's New

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