"""dev_shell - run Python inside a live Home Assistant.

  dev_shell exec 'hass.states.get("sun.sun")'    run a snippet
  dev_shell exec -f snippet.py                    run a file
  dev_shell exec - <<'EOF' ... EOF                read the snippet from stdin
  dev_shell                                       interactive REPL
  dev_shell reset / dev_shell sessions            manage server-side sessions

Connection: HASS_URL (default http://localhost:8123), HASS_TOKEN, HASS_SESSION.
Exit status of exec is 1 when the snippet raised, 2 on connection/usage errors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from .client import Client, DevShellError, resolve_token, resolve_url


def main() -> None:
    args = _parser().parse_args()
    try:
        sys.exit(asyncio.run(_dispatch(args)))
    except DevShellError as err:
        print(f"dev_shell: {err}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_shell", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default=os.environ.get("HASS_URL"))
    parser.add_argument("--token", default=None, help="defaults to $HASS_TOKEN")
    parser.add_argument(
        "-s", "--session", default=os.environ.get("HASS_SESSION", "default")
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON results")
    sub = parser.add_subparsers(dest="command")

    exec_ = sub.add_parser("exec", help="run code and print output and result")
    exec_.add_argument("code", nargs="?", help="code to run, or - for stdin")
    exec_.add_argument("-f", "--file", help="run the contents of a file")
    exec_.add_argument("-t", "--timeout", type=float, help="cancel after N seconds")
    exec_.add_argument(
        "--reset", action="store_true", help="reset the session before running"
    )

    sub.add_parser("repl", help="interactive shell (the default)")
    sub.add_parser("reset", help="discard the session's variables")
    sub.add_parser("sessions", help="list sessions on the server")
    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    url = resolve_url(args.url)
    async with Client(url, resolve_token(args.token)) as client:
        match args.command:
            case "exec":
                return await _exec(client, args)
            case "reset":
                result = await client.call("dev_shell_server/reset", session=args.session)
                _emit(args, result, "reset" if result["reset"] else "no such session")
                return 0
            case "sessions":
                result = await client.call("dev_shell_server/sessions")
                _emit(args, result, _format_sessions(result["sessions"]))
                return 0
            case _:
                from .repl import run_repl

                return await run_repl(client, args.session)


async def _exec(client: Client, args: argparse.Namespace) -> int:
    if args.file:
        with open(args.file, encoding="utf-8") as fp:
            code = fp.read()
    elif args.code in (None, "-"):
        if args.code is None and sys.stdin.isatty():
            raise DevShellError("exec needs code, -f FILE, or - to read stdin")
        code = sys.stdin.read()
    else:
        code = args.code
    if args.reset:
        await client.call("dev_shell_server/reset", session=args.session)
    payload: dict[str, Any] = {"code": code, "session": args.session}
    if args.timeout:
        payload["timeout"] = args.timeout
    result = await client.call("dev_shell_server/exec", **payload)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_result(result)
    return 1 if result["error"] else 0


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
        print("[dev_shell: output truncated]", file=sys.stderr)
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
