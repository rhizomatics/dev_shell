# What's New

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