# Plugins

A plugin is a plain Python file that is run at the start of every session. Use one for bookmarked expressions, helper functions and classes that you would otherwise retype or dig out of history.

Plugins live in a `plugins/` directory beside [`config.toml`](client_configuration.md):

```
~/.config/ha-repl/plugins/      # $XDG_CONFIG_HOME/ha-repl/plugins if that is set
  10-helpers.py
  20-bookmarks.py

<repo>/.ha-repl/plugins/        # nearest .ha-repl walking up from the working directory
  50-this-component.py
```

A repo's plugins are not run until its `.ha-repl/` directory has been [trusted](client_configuration.md#trusting-a-repo-directory).

Every `*.py` file in `plugins/` is run at the start of a session: the home directory's first and then the repo's, each in name order. A later file can redefine a name from an earlier one, so a repo can override a helper.

```python
# ~/.config/ha-repl/plugins/20-bookmarks.py
import datetime as dt

sensors = hass.data["entity_components"]["sensor"]


def stale(hours=24):
    """Entities not updated in the last `hours`."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    return [s.entity_id for s in hass.states.async_all() if s.last_updated < cutoff]
```

The interactive banner lists the files that were loaded. `--no-plugins` skips them all.

## How a Plugin Runs

A file is run as though its contents had been typed at the prompt, one statement at a time:

- In live mode the usual [What Runs Where](../modes/live_mode.md#what-runs-where) rules apply to each statement. `stale` above uses `hass`, so it is defined inside Home Assistant. The `import` is local, and is repeated there because `stale` needs it.
- In `api` mode there is no `hass`. Defining `stale` works, since the name is only looked up when it is called, but the `sensors` line fails.
- A statement that fails is reported as a warning, naming the file and line. The rest of the file and the remaining files still run, so a session always starts.
- Nothing is echoed. The value of a bare expression is discarded, and anything a file prints goes to standard error.

```
ha-repl: plugin: ~/.config/ha-repl/plugins/20-bookmarks.py:4: NameError: name 'hass' is not defined
```

## Adapting to the Mode

Two variables are set before the plugins run, and stay available in the session:

| Variable | Value |
| -------- | ----- |
| `MODE` | `"live"` in the live shell, `"exec"` in `ha-repl exec`, `"api"` in API client mode |
| `SERVER` | The name of the configured server, or `None` when connected by URL |

One file can then serve every mode, without a warning in any. `hass` exists in `live` and `exec`, so test for `api` to decide whether it can be used:

```python
if MODE == "api":
    sensors = obj.find(domain="sensor")
else:
    sensors = hass.data["entity_components"]["sensor"]

if MODE != "exec":
    print("helpers loaded")  # only where a person is watching

if SERVER == "house":
    print("careful - this is the house")
```

They are ordinary variables, so nothing stops a session reassigning them.

## In Exec Mode

Plugins are also run by [`ha-repl exec`](../modes/exec_mode.md), so a snippet and an agent see the same names as the interactive shell. Warnings go to standard error, so standard output, and the JSON object from `--json`, hold only the snippet's own result. Each statement that uses `hass` is a round trip to Home Assistant on every call, so keep those few, put them under `if MODE != "exec":`, or use `--no-plugins` where speed matters.

## In Other Python Shells

Plugins are ordinary Python with no special syntax. The same file can be named in `PYTHONSTARTUP`, linked into IPython's own startup directory, or run with `exec(open(path).read())` in a notebook. Only the lines that use `hass` depend on live mode. `MODE` and `SERVER` don't exist there, so a file shared with those shells should use `globals().get("MODE")`.
