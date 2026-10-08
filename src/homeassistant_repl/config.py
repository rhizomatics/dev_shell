"""Persistent configuration: named servers and plugins, read from
`~/.config/ha-repl/` with a repo's own `.ha-repl/` directory layered over it.

    ~/.config/ha-repl/config.toml       servers, and defaults for the flags
    ~/.config/ha-repl/plugins/*.py      run at the start of every session
    <repo>/.ha-repl/                    the same layout, merged over the above

A repo directory arrives with a clone, and can run code (a plugin, a
`token_command`) and choose where a session connects - so it's ignored until
approved with `ha-repl trust`, and again whenever anything in it changes.
See docs/configuration.md and docs/plugins.md, and docs/developer/design/rfcs/0001 for why it's
this shape.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess  # nosec B404 - token_command is the user's own, see Server.resolve_token
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import HaReplError, _dotenv, resolve_token, resolve_url

REPO_DIR = ".ha-repl"

# Where a server's token comes from - exactly one per server.
_TOKEN_KEYS = ("token", "token_command", "token_env")
_SETTINGS: dict[str, type | tuple[type, ...]] = {
    "default": str,
    "session": str,
    "ttl": (int, float),
    "auto_await": bool,
}


@dataclass(frozen=True)
class Server:
    """One `[servers.NAME]` entry."""

    name: str
    url: str
    token: str | None = None
    token_command: str | None = None
    token_env: str | None = None

    @property
    def token_source(self) -> str | None:
        """Which key supplies the token - never the token itself."""
        return next((key for key in _TOKEN_KEYS if getattr(self, key)), None)

    def resolve_token(self) -> str:
        if self.token:
            return self.token
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise HaReplError(
                    f"Server {self.name!r}: environment variable "
                    f"{self.token_env} is not set"
                )
            return token
        if self.token_command:
            return self._run_token_command(self.token_command)
        raise HaReplError(
            f"Server {self.name!r} has no token: "
            f"give it one of {', '.join(_TOKEN_KEYS)}"
        )

    def _run_token_command(self, command: str) -> str:
        # No shell, and stderr left attached to the terminal: a secrets
        # manager's own prompt or error message is shown as it is, and
        # nothing the command prints can end up in one of our messages.
        try:
            result = subprocess.run(  # nosec B603
                shlex.split(command), stdout=subprocess.PIPE, text=True, check=False
            )
        except (OSError, ValueError) as err:
            raise HaReplError(
                f"Server {self.name!r}: token_command could not be run: {err}"
            ) from err
        token = result.stdout.strip()
        if result.returncode != 0 or not token:
            raise HaReplError(
                f"Server {self.name!r}: token_command gave no token "
                f"(exit status {result.returncode})"
            )
        return token


@dataclass
class Config:
    """The home and repo configuration, merged."""

    default: str | None = None
    session: str | None = None
    ttl: float | None = None
    auto_await: bool | None = None
    servers: dict[str, Server] = field(default_factory=dict)
    # Plugins in the order they run: home first, then the repo's.
    plugins: list[Path] = field(default_factory=list)
    # Things worth telling the user, for the caller to report its own way.
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Connection:
    """Where to connect, and as what."""

    url: str  # the websocket endpoint
    token: str
    name: str | None = None  # the configured server's name, if chosen by name


def home_dir() -> Path:
    """`~/.config/ha-repl` on every platform, macOS included - as git, gh
    and uv do, and where dotfiles managers expect it."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "ha-repl"


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "ha-repl"


def find_repo_dir(start: Path | None = None) -> Path | None:
    """The nearest `.ha-repl/` walking up from the working directory, not
    looking beyond the root of the git repository it's in."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / REPO_DIR
        if candidate.is_dir():
            return candidate
        if (directory / ".git").exists():
            return None
    return None


def display_path(path: Path) -> str:
    """A path as it's shown to the user: `~` for the home directory."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def _files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def _fingerprint(directory: Path) -> str:
    """A hash of every file in a directory, by name and content."""
    digest = hashlib.sha256()
    for path in _files(directory):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _trust_file() -> Path:
    return _state_dir() / "trusted.json"


def _trusted() -> dict[str, str]:
    try:
        stored = json.loads(_trust_file().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


def trust(directory: Path) -> list[Path]:
    """Approve a repo directory as it stands now. Returns the files covered."""
    directory = directory.resolve()
    stored = _trusted()
    stored[str(directory)] = _fingerprint(directory)
    path = _trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    return _files(directory)


def _untrusted_reason(directory: Path) -> str | None:
    """None if this repo directory has been approved as it stands."""
    approved = _trusted().get(str(directory.resolve()))
    if approved is None:
        return "ignoring"
    if approved != _fingerprint(directory):
        return "changed since it was trusted, ignoring"
    return None


def _read(path: Path, warnings: list[str]) -> dict[str, Any]:
    """One config.toml, checked - {} if there isn't one."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise HaReplError(f"{display_path(path)}: {err}") from err

    for key, kind in _SETTINGS.items():
        # bool is an int as far as isinstance() goes, but not as a ttl
        if key in data and (
            not isinstance(data[key], kind)
            or (kind is not bool and isinstance(data[key], bool))
        ):
            raise HaReplError(f"{display_path(path)}: `{key}` has the wrong type")
    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        raise HaReplError(f"{display_path(path)}: `servers` must be a table")
    holds_token = False
    for name, entry in servers.items():
        where = f"{display_path(path)}: server {name!r}"
        if not isinstance(entry, dict):
            raise HaReplError(f"{where} must be a table")
        for key in ("url", *_TOKEN_KEYS):
            if key in entry and not (isinstance(entry[key], str) and entry[key]):
                raise HaReplError(f"{where}: `{key}` must be a string")
        if "://" in name:
            raise HaReplError(f"{where}: a name can't contain `://`")
        if len([key for key in _TOKEN_KEYS if key in entry]) > 1:
            raise HaReplError(f"{where}: give only one of {', '.join(_TOKEN_KEYS)}")
        holds_token = holds_token or "token" in entry
    if holds_token and os.name != "nt" and path.stat().st_mode & 0o077:
        warnings.append(
            f"{display_path(path)} holds a token and is readable by other "
            f"users - `chmod 600` it"
        )
    return data


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Top-level keys are replaced; `servers` is merged by name, then key
    by key - except that a token given one way replaces one given another."""
    merged = {**base, **over}
    servers = {name: dict(entry) for name, entry in base.get("servers", {}).items()}
    for name, entry in over.get("servers", {}).items():
        current = servers.get(name, {})
        if any(key in entry for key in _TOKEN_KEYS):
            current = {k: v for k, v in current.items() if k not in _TOKEN_KEYS}
        servers[name] = {**current, **entry}
    merged["servers"] = servers
    return merged


def _plugin_files(directory: Path) -> list[Path]:
    return sorted((directory / "plugins").glob("*.py"), key=lambda path: path.name)


def load_config(cwd: Path | None = None) -> Config:
    """Read the home configuration and, if it has been trusted, the repo's."""
    config = Config()
    directories = [home_dir()]
    repo = find_repo_dir(cwd)
    if repo is not None:
        reason = _untrusted_reason(repo)
        if reason is None:
            directories.append(repo)
        else:
            config.warnings.append(
                f"{reason} {display_path(repo)} - run `ha-repl trust` to allow it"
            )

    data: dict[str, Any] = {}
    for directory in directories:
        data = _merge(data, _read(directory / "config.toml", config.warnings))
        config.plugins += _plugin_files(directory)

    config.default = data.get("default")
    config.session = data.get("session")
    config.ttl = data.get("ttl")
    config.auto_await = data.get("auto_await")
    for name, entry in data["servers"].items():
        if "url" not in entry:
            raise HaReplError(f"Server {name!r} has no `url`")
        config.servers[name] = Server(
            name, entry["url"], *(entry.get(key) for key in _TOKEN_KEYS)
        )
    return config


def resolve_connection(
    server: str | None, token: str | None, config: Config
) -> Connection:
    """Decide where to connect. `server` is a configured name or a URL; left
    out, it's $HASS_SERVER, then a `.env` file's, then the configured
    `default`, then http://homeassistant.local:8123.

    A server chosen by name brings its own token, which only an explicit
    `token` overrides - $HASS_TOKEN is never paired with a server out of a
    config file, since a token from one place sent to an address from
    another is the mistake named servers are there to prevent.
    """
    if not server and os.environ.get("SUPERVISOR_TOKEN"):
        # Inside a Home Assistant add-on: via the supervisor, as ever.
        return Connection(resolve_url(None), resolve_token(token))
    target = server or os.environ.get("HASS_SERVER") or _dotenv().get("HASS_SERVER")
    if not target and config.default:
        if config.default not in config.servers:
            raise HaReplError(
                f"`default = {config.default!r}` in config.toml names no "
                f"configured server{_known(config)}"
            )
        target = config.default
    if not target or "://" in target:
        return Connection(resolve_url(target), resolve_token(token))
    entry = config.servers.get(target)
    if entry is None:
        raise HaReplError(f"No server named {target!r}{_known(config)}")
    return Connection(resolve_url(entry.url), token or entry.resolve_token(), target)


def _known(config: Config) -> str:
    if not config.servers:
        return f" - none are configured in {display_path(home_dir() / 'config.toml')}"
    return f" - configured: {', '.join(sorted(config.servers))}"
