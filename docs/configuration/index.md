title: Configuration
---
# Configuration

All modes require a [Long Lived Access Token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token) which can be provided via `HASS_TOKEN` enivironment variable, a `HASS_TOKEN=xxxx` line in an `.env` file, or the `--token` argument on `ha-repl`.

The URL will default to `http://homeassistant.local:8123` or can be set using the `HASS_SERVER` enivironment variable, a `HASS_SERVER=xxxx` line in an `.env` file, or the `--server` argument on `ha-repl`.

Note, although `homeassistant-api` itself has different env vars, when used within `ha-repl` it will be set up automatically using the same server and token as the main shell.

If you work with more than one Home Assistant, name them in `~/.config/ha-repl/config.toml` and pick one with `--server NAME`. Python files in `~/.config/ha-repl/plugins/` are run at the start of every session, for helper functions and bookmarked expressions. See [Configuration](client_configuration.md)

{{ pagetree(siblings) }}
