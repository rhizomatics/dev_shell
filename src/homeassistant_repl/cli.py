"""ha-repl - run Python against a live Home Assistant.

  ha-repl                                         interactive API client shell (default)
                                                    `obj` only, read-only, no HACS component needed
  ha-repl live                                    interactive custom-component shell
                                                    local Python with `sql` + `hass_api`; anything
                                                    using `hass`/`obj` runs inside Home Assistant;
                                                    needs the ha_repl_server component
  ha-repl exec 'hass.states.get("sun.sun")'       run a snippet the way the live shell would
  ha-repl exec 'sql("select ...").show()'           (local Python + sql, hass/obj inside HA)
  ha-repl --json exec 'sql("select ...")'         result as JSON: {stdout, value, error, ...};
                                                    a sql result's value is {columns, rows, ...}
  ha-repl exec -f snippet.py                      run a file
  ha-repl exec - <<'EOF' ... EOF                  read the snippet from stdin
  ha-repl reset / ha-repl sessions                manage live-mode server-side sessions

Connection: HASS_SERVER (default http://homeassistant.local:8123), HASS_TOKEN, HASS_SESSION.
Both HASS_SERVER/HASS_TOKEN also fall back to a `.env` file in the current directory, below real env vars.
API client mode: --ttl seconds before the cached snapshot is refreshed (default 30).
Exit status of exec is 1 when the snippet raised, 2 on connection/usage errors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from typing import Any

from .client import Client, HaReplError, resolve_token, resolve_url


def main() -> None:
    args = _parser().parse_args()
    try:
        sys.exit(asyncio.run(_dispatch(args)))
    except HaReplError as err:
        print(f"ha-repl: {err}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha-repl",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--server", default=None, help="defaults to $HASS_SERVER")
    parser.add_argument("--token", default=None, help="defaults to $HASS_TOKEN")
    parser.add_argument(
        "-s", "--session", default=os.environ.get("HASS_SESSION", "default")
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON results")
    parser.add_argument(
        "--ttl",
        type=float,
        default=30.0,
        help="API client mode: seconds before the cached snapshot is refreshed (default: 30)",
    )
    parser.add_argument(
        "--no-auto-await",
        action="store_true",
        help="don't automatically await a call you forgot to `await` - "
        "report it as an unawaited coroutine instead, as plain Python would",
    )
    sub = parser.add_subparsers(dest="command")

    exec_ = sub.add_parser(
        "exec", help="run code and print output and result (custom mode only)"
    )
    exec_.add_argument("code", nargs="?", help="code to run, or - for stdin")
    exec_.add_argument("-f", "--file", help="run the contents of a file")
    exec_.add_argument("-t", "--timeout", type=float, help="cancel after N seconds")
    exec_.add_argument(
        "--reset", action="store_true", help="reset the session before running"
    )

    sub.add_parser(
        "api",
        help="interactive API client shell (the default): obj only, no HACS component needed",
    )
    sub.add_parser(
        "live",
        help="interactive custom-component shell: local Python + sql, with hass + obj "
        "run inside Home Assistant; needs the ha_repl_server component",
    )
    sub.add_parser("reset", help="discard the live-mode session's variables")
    sub.add_parser("sessions", help="list live-mode sessions on the server")
    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    url = resolve_url(args.server)
    async with Client(url, resolve_token(args.token)) as client:
        match args.command:
            case "exec":
                return await _exec(client, args)
            case "reset":
                result = await client.call("ha_repl_server/reset", session=args.session)
                _emit(args, result, "reset" if result["reset"] else "no such session")
                return 0
            case "sessions":
                result = await client.call("ha_repl_server/sessions")
                _emit(args, result, _format_sessions(result["sessions"]))
                return 0
            case "live":
                from .repl import run_repl

                return await run_repl(
                    client, args.session, auto_await=not args.no_auto_await
                )
            case _:  # "api", or no subcommand at all - API client mode is the default
                from .apirepl import run_api_repl

                return await run_api_repl(
                    client, args.ttl, auto_await=not args.no_auto_await
                )


async def _exec(client: Client, args: argparse.Namespace) -> int:
    if args.file:
        # One-shot CLI read, not a hot path - a threaded read would be overkill.
        with open(args.file, encoding="utf-8") as fp:  # noqa: ASYNC230
            code = fp.read()
    elif args.code in (None, "-"):
        if args.code is None and sys.stdin.isatty():
            raise HaReplError("exec needs code, -f FILE, or - to read stdin")
        code = sys.stdin.read()
    else:
        code = args.code
    if args.reset:
        await client.call("ha_repl_server/reset", session=args.session)

    from .repl import Captured, connect_live

    # The same local-plus-server session the interactive shell uses, so a
    # snippet means the same thing here: `sql` and plain Python run in this
    # process, statements using `hass`/`obj` inside Home Assistant.
    capture = Captured() if args.json else None
    live = await connect_live(
        client,
        args.session,
        auto_await=not args.no_auto_await,
        capture=capture,
        timeout=args.timeout,
    )
    start = time.perf_counter()
    try:
        ok = await asyncio.wait_for(live.run(code), args.timeout)
    except TimeoutError:
        ok = False
        message = f"timed out after {args.timeout:g}s"
        if capture is None:
            print(f"ha-repl: {message}", file=sys.stderr)
        else:
            capture.error = {
                "type": "TimeoutError",
                "message": message,
                "traceback": f"TimeoutError: {message}\n",
            }
    if capture is not None:
        print(
            json.dumps(
                {
                    "stdout": capture.stdout,
                    "value": capture.value,
                    "error": capture.error,
                    "duration": time.perf_counter() - start,
                    "truncated": capture.truncated,
                },
                indent=2,
            )
        )
    return 0 if ok else 1


def display_options() -> dict[str, Any]:
    """Color/width hints for the server to render values and tracebacks with.

    Decided here, not there: the server only sees a websocket, not a terminal,
    and stdout here might be piped (a script, a redirected log) rather than a
    person watching it live.
    """
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    width = shutil.get_terminal_size((88, 24)).columns
    return {"color": color, "width": width}


def print_result(result: dict[str, Any]) -> None:
    if result["stdout"]:
        sys.stdout.write(result["stdout"])
        if not result["stdout"].endswith("\n"):
            sys.stdout.write("\n")
    if result["value"] is not None:
        print(result["value"])
    if result["error"]:
        sys.stderr.write(result["error"]["traceback"])
    if result.get("truncated"):
        print("[ha-repl: output truncated]", file=sys.stderr)
    sys.stdout.flush()


def _emit(args: argparse.Namespace, result: Any, text: str) -> None:
    print(json.dumps(result, indent=2) if args.json else text)


def _format_sessions(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "no sessions"
    return "\n".join(
        f"{s['name']}: {s['executions']} runs, vars: {', '.join(s['variables'])}"
        for s in sessions
    )
