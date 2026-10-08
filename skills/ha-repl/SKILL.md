---
name: ha-repl
description: Run Python against a running Home Assistant with the `ha-repl` command, to check entity state, inspect `hass` objects, query the recorder database or call services. Use when developing or debugging a Home Assistant custom component and an idea needs checking against a real instance rather than guessed from source.
---

# Home Assistant REPL

`ha-repl exec` runs one snippet of Python against a running Home Assistant and exits. Use it to
check how something really behaves before writing or changing component code.

It runs any Python inside Home Assistant. Only use it against a development instance or
devcontainer, unless the user has said the target instance is fine to change.

## Before the first call

- `ha-repl` must be on the path, or run it as `uv run --with homeassistant-repl ha-repl`.
  It needs Python 3.14.
- `HASS_SERVER` and `HASS_TOKEN` must be set in the environment or in a `.env` file in the
  working directory. If they are missing, ask the user; don't look for a token elsewhere.
- `exec` needs the `ha_repl_server` custom component on the instance. If it isn't installed,
  `exec` exits with status `2`; see "Without the server component" below.
- `ha-repl` and the server component should be the same version. Prior to v1.0.0 they are likely to be breaking changes and backward compatibility between server and client is not assured.

## Running a snippet

Send the snippet on standard input, ask for JSON, and always set a timeout:

```bash
ha-repl --json exec -t 30 - <<'PY'
hass.states.get("sun.sun").state
PY
```

`--json` and `-s NAME` go before `exec`; `-t SECONDS`, `--reset` and `-f FILE` go after it.

The output is one JSON object: `stdout`, `value` (the last expression), `error` (`null`, or
`type`, `message` and `traceback`), `duration` and `truncated`. Exit status is `0` on success,
`1` if the snippet raised or timed out, `2` if Home Assistant could not be reached or the
arguments were wrong.

`await` works at the top level, and a call to an async function is awaited if the `await` is
left out.

## Names available in a snippet

| Name | What it is | Where it runs |
| ---- | ---------- | ------------- |
| `hass` | The live `HomeAssistant` object | Inside Home Assistant |
| `obj` | Entities as a tree: `obj["/mqtt/sensor/name"]`, `obj["sensor.name"]`, `obj.find(...)`, `obj.find_names(...)`, `obj.find_paths(...)`, `obj.show(path)` | Inside Home Assistant |
| `sql` | `sql("select ...", max_rows=N)` queries the recorder and gives a local result object; `sql.tables` lists tables and columns | Local |
| `hass_api` | A `homeassistant-api` REST client | Local |

A statement that uses `hass` or `obj` is sent to Home Assistant and run there. Everything else
runs in the local `ha-repl` process.

Only plain data crosses between the two: strings, numbers, `None`, and lists, tuples, sets and
dicts of them, up to about 100,000 characters. What crosses is a copy.

- Plain local data and `sql` results can be used in a statement that runs inside Home Assistant.
  A `sql` result arrives as a list of rows. A module imported locally is imported there too.
- A variable assigned by a statement that used `hass` or `obj` comes back, and is local
  afterwards, if it holds plain data. So does what `obj.find_names()` and `obj.find_paths()`
  return: an iterator over strings, to go through once.
- Anything else - a state, an entity, a config entry - stays inside Home Assistant, and later
  statements that use that variable run there too.
- Don't use `sql` or `hass_api` in the same statement as `hass` or `obj` - it is refused. Assign
  one part on its own line first.
- Local functions, classes and dataframes don't cross. Calling a local function on a value from
  `hass` in one statement is refused; assign the plain value first, then call the function.

```bash
ha-repl --json exec -t 30 - <<'PY'
names = obj.find_names(domain="light")
{n: hass_api.get_state(entity_id=n).state for n in names}
PY
```

The first statement runs inside Home Assistant, the second locally with the names it returned.

## Finding entities

- `obj[path]` gives a sub-tree or an entity, and raises `KeyError` if there is none. `keys()`,
  `values()` and `items()` cover that level only, so `list(obj["/mqtt"].keys())` shows what is
  below a path.
- `obj.find()` goes through the whole tree in no fixed order. It takes a full or partial path or
  a regular expression, and the filters `domain`, `platform`, `area` and `label`, each a string or
  a list of strings.
- `obj.find_paths()` and `obj.find_names()` take the same arguments and give tree paths or entity
  ids instead of the objects.

## Querying the recorder

A `sql` result is a local object holding the downloaded rows. `len(r)`, slices such as `r[:10]`
and looping over rows work on it. `r[0]` is one row, read by position or column name, so
`sql("select count(*) from events")[0][0]` is the number itself. Also:

- `r.column_names`, `r.rowcount`, and `r.truncated`, which is true when the query hit `max_rows`
- `r.to_dicts()` for a list of dicts, `r.project([...])` for some of the columns, `r.sample()`
  for random rows
- `r.to_polars()` or `r.to_pandas()` if that library is installed locally, `r.export_csv()` to
  write a local file

Some recorder columns are legacy: still in the table, no longer written to, such as
`states.entity_id`, now in `states_meta`. A result keeps them out of view unless the query names them,
`sql(..., legacy=True)` is used, or `r.legacy = True` is set afterwards. A table's `columns` and
`column_names` leave them out too, unless it came from `sql.table(name, legacy=True)`.

`sql.max_rows` sets the default row limit for the rest of the snippet. `sql.table("states")`
gives one table from `sql.tables`; its `column_names` and `columns` describe it without
running a query, and `class_name` and `description` are the class and docstring it has in
`homeassistant.components.recorder.db_schema`.

## Getting data back

A value from inside Home Assistant comes back in `value` as data when it is plain data (strings,
numbers, lists and dicts of them), so shape the answer that way as the last expression:

```bash
ha-repl --json exec -t 30 - <<'PY'
{s.entity_id: s.state for s in hass.states.async_all("light")}
PY
```

Anything else - a state object, or a list containing one - comes back as a string of its `repr()`.

A `sql` result as the last expression comes back as data: `columns`, `rows`, `rowcount` and
`truncated`.

## State between calls

Each call is a new local process, so local variables don't carry over. Variables inside Home
Assistant do, in a named session, until it is reset or Home Assistant restarts.

- Use your own session name, `ha-repl -s <task-name> --json exec ...`, so other agents' variables
  aren't overwritten.
- `ha-repl sessions` lists sessions and their variables. `ha-repl -s <name> reset` clears one.

## Working safely

- Prefer reading. `hass.states.get(...)`, `obj[...]` and `sql(...)` are safe to repeat. Service
  calls and changes to `hass` objects take effect on the instance straight away.
- A snippet stops at the first statement that fails; statements before it have already run.
- Keep `max_rows` small while exploring (the default is 1000). Only a single `SELECT` is accepted;
  anything else raises `SqlError`.
- `hass` or `sql` access may have been switched off in the server component's options. If either
  is refused, tell the user instead of working around it.
- For an error raised inside Home Assistant, read `type` and `message`; `traceback` is formatted
  for a terminal.
- `help(thing)` inside a snippet prints a short summary of an object's methods and properties,
  for `obj`, `sql` and its results as well as `hass` objects. `help(thing, full=True)` is Python's
  own full help page.

## Without the server component

Only read access through the standard Home Assistant API is available, and not through `exec`.
Use the library from a script instead - `connect()` gives the same `obj`, without `hass` or `sql`:

```python
import asyncio
import homeassistant_repl


async def main() -> None:
    obj = await homeassistant_repl.connect()  # reads HASS_SERVER and HASS_TOKEN
    print([(e.entity_id, e.state) for e in obj.find(domain="light")])


asyncio.run(main())
```

## More detail

Each documentation page is available as Markdown:

- Index of pages: <https://homeassistant-repl.rhizomatics.org.uk/llms.txt>
- All pages in one file: <https://homeassistant-repl.rhizomatics.org.uk/llms-full.txt>
- Exec mode: <https://homeassistant-repl.rhizomatics.org.uk/exec_mode/index.md>
- What runs where, with worked examples of mixing local and Home Assistant code, and those that
  are refused: <https://homeassistant-repl.rhizomatics.org.uk/live_mode/index.md>
- Object tree: <https://homeassistant-repl.rhizomatics.org.uk/obj_tree/index.md>
- SQL access: <https://homeassistant-repl.rhizomatics.org.uk/sql/index.md>
- Using the library from plain Python, ipython or Marimo:
  <https://homeassistant-repl.rhizomatics.org.uk/alternative_integration/index.md>
