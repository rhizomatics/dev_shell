"""Tests for config.py - the home and repo config.toml, the trust step, and
which server and token a connection ends up with - run without Home Assistant."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from homeassistant_repl import cli
from homeassistant_repl.client import HaReplError, _dotenv
from homeassistant_repl.config import (
    Config,
    Server,
    find_repo_dir,
    load_config,
    resolve_connection,
    trust,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """An empty home config directory, an empty trust store and a working
    directory inside a git repo - nothing of the developer's own leaks in."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.delenv("HASS_SESSION", raising=False)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    _dotenv.cache_clear()
    yield tmp_path / "xdg-config" / "ha-repl"
    _dotenv.cache_clear()


def _write(directory: Path, text: str, *, mode: int = 0o600) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _config(**servers: Server) -> Config:
    return Config(servers=servers)


HOUSE = Server("house", "https://ha.example.org", token="house-token")


# --- loading ---------------------------------------------------------------


def test_no_configuration_at_all_is_an_empty_config():
    config = load_config()

    assert config.servers == {}
    assert config.default is None
    assert config.plugins == []
    assert config.warnings == []


def test_home_config_is_read(home):
    _write(
        home,
        """
        default = "dev"
        session = "work"
        ttl = 5
        auto_await = false

        [servers.dev]
        url = "http://localhost:8123"
        token = "abc"

        [servers.house]
        url = "https://ha.example.org"
        token_command = "op read op://Private/ha/token"
        """,
    )

    config = load_config()

    assert (config.default, config.session, config.ttl) == ("dev", "work", 5)
    assert config.auto_await is False
    assert config.servers["dev"] == Server("dev", "http://localhost:8123", token="abc")
    assert config.servers["house"].token_source == "token_command"


def test_xdg_config_home_unset_means_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write(tmp_path / ".config" / "ha-repl", 'default = "dev"\n')

    assert load_config().default == "dev"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("default = [", "config.toml"),
        ("ttl = 'soon'", "`ttl` has the wrong type"),
        ("ttl = true", "`ttl` has the wrong type"),
        ("servers = 3", "`servers` must be a table"),
        ("[servers.a]\nurl = 3", "`url` must be a string"),
        ("[servers.a]\ntoken = 't'", "has no `url`"),
        (
            "[servers.a]\nurl = 'http://a'\ntoken = 't'\ntoken_env = 'E'",
            "give only one of",
        ),
    ],
)
def test_a_bad_config_file_is_an_error(home, text, match):
    _write(home, text)

    with pytest.raises(HaReplError, match=match):
        load_config()


def test_a_token_in_a_file_others_can_read_is_warned_about(home):
    _write(home, "[servers.a]\nurl = 'http://a'\ntoken = 'secret'\n", mode=0o644)

    (warning,) = load_config().warnings

    assert "readable by other users" in warning
    assert "secret" not in warning


def test_no_warning_when_the_token_is_kept_out_of_the_file(home):
    _write(home, "[servers.a]\nurl = 'http://a'\ntoken_env = 'A_TOKEN'\n", mode=0o644)

    assert load_config().warnings == []


# --- the repo directory ----------------------------------------------------


def test_repo_dir_is_found_from_a_subdirectory(tmp_path, monkeypatch):
    (tmp_path / "repo" / ".ha-repl").mkdir()
    deep = tmp_path / "repo" / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    assert find_repo_dir() == tmp_path / "repo" / ".ha-repl"


def test_repo_dir_search_stops_at_the_git_root(tmp_path, monkeypatch):
    (tmp_path / ".ha-repl").mkdir()  # above the repo - not this repo's

    assert find_repo_dir() is None


def test_an_untrusted_repo_dir_is_ignored_with_a_warning(tmp_path):
    repo = tmp_path / "repo" / ".ha-repl"
    _write(repo, 'default = "evil"\n[servers.evil]\nurl = "http://evil"\ntoken = "x"\n')
    (repo / "plugins").mkdir()
    (repo / "plugins" / "10-evil.py").write_text("print('hi')\n", encoding="utf-8")

    config = load_config()

    assert config.default is None
    assert config.servers == {}
    assert config.plugins == []
    (warning,) = config.warnings
    assert "run `ha-repl trust`" in warning


def test_a_trusted_repo_dir_is_merged_over_home(home, tmp_path):
    _write(
        home,
        """
        default = "house"
        session = "work"

        [servers.house]
        url = "https://ha.example.org"
        token = "house-token"

        [servers.dev]
        url = "http://localhost:8123"
        token = "dev-token"
        """,
    )
    repo = tmp_path / "repo" / ".ha-repl"
    _write(
        repo,
        """
        default = "dev"

        [servers.dev]
        url = "http://localhost:9123"

        [servers.house]
        token_env = "HOUSE_TOKEN"
        """,
    )
    trust(repo)

    config = load_config()

    assert config.warnings == []
    assert config.default == "dev"
    assert config.session == "work"  # untouched by the repo file
    # merged key by key: the url replaced, the token kept
    assert config.servers["dev"] == Server(
        "dev",
        "http://localhost:9123",
        token="dev-token",
    )
    # a token given another way replaces the one beneath, not joins it
    assert config.servers["house"] == Server(
        "house", "https://ha.example.org", token_env="HOUSE_TOKEN"
    )


def test_changing_a_trusted_repo_dir_needs_it_trusted_again(tmp_path):
    repo = tmp_path / "repo" / ".ha-repl"
    _write(repo, 'session = "one"\n')
    trust(repo)
    assert load_config().session == "one"

    (repo / "plugins").mkdir()
    (repo / "plugins" / "10-new.py").write_text("x = 1\n", encoding="utf-8")

    config = load_config()
    assert config.session is None
    assert "changed since it was trusted" in config.warnings[0]

    trust(repo)
    assert load_config().session == "one"


def test_plugin_files_run_home_first_then_repo_each_in_name_order(home, tmp_path):
    repo = tmp_path / "repo" / ".ha-repl"
    for directory, names in (
        (home, ["20-b.py", "10-a.py", "notes.txt"]),
        (repo, ["05-c.py"]),
    ):
        (directory / "plugins").mkdir(parents=True)
        for name in names:
            (directory / "plugins" / name).write_text("", encoding="utf-8")
    trust(repo)

    assert [path.name for path in load_config().plugins] == [
        "10-a.py",
        "20-b.py",
        "05-c.py",
    ]


# --- choosing a server and its token ---------------------------------------


def test_a_name_picks_the_configured_server_and_its_token():
    connection = resolve_connection("house", None, _config(house=HOUSE))

    assert connection.url == "wss://ha.example.org/api/websocket"
    assert connection.token == "house-token"
    assert connection.name == "house"


def test_a_url_is_used_as_it_always_was(monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "env-token")

    connection = resolve_connection("http://10.0.0.5:8123", None, _config(house=HOUSE))

    assert connection.url == "ws://10.0.0.5:8123/api/websocket"
    assert connection.token == "env-token"
    assert connection.name is None


def test_an_unknown_name_is_an_error_listing_the_known_ones():
    with pytest.raises(HaReplError, match=r"No server named 'hose'.*house"):
        resolve_connection("hose", None, _config(house=HOUSE))


def test_hass_server_can_be_a_name(monkeypatch):
    monkeypatch.setenv("HASS_SERVER", "house")

    assert resolve_connection(None, None, _config(house=HOUSE)).name == "house"


def test_hass_token_is_not_paired_with_a_named_server(monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "env-token")

    connection = resolve_connection("house", None, _config(house=HOUSE))

    assert connection.token == "house-token"


def test_an_explicit_token_overrides_a_named_servers_own():
    connection = resolve_connection("house", "flag-token", _config(house=HOUSE))

    assert connection.token == "flag-token"


def test_the_default_server_is_used_when_none_is_named():
    config = _config(house=HOUSE)
    config.default = "house"

    assert resolve_connection(None, None, config).name == "house"


def test_environment_and_dotenv_come_before_the_default(tmp_path, monkeypatch):
    config = _config(house=HOUSE)
    config.default = "house"
    Path(".env").write_text(
        "HASS_SERVER=http://dotenv.example:8123\nHASS_TOKEN=dotenv-token\n",
        encoding="utf-8",
    )

    connection = resolve_connection(None, None, config)
    assert connection.url == "ws://dotenv.example:8123/api/websocket"
    assert connection.token == "dotenv-token"

    monkeypatch.setenv("HASS_SERVER", "http://env.example:8123")
    assert resolve_connection(None, None, config).url == (
        "ws://env.example:8123/api/websocket"
    )


def test_a_default_naming_no_server_is_an_error():
    config = _config(house=HOUSE)
    config.default = "gone"

    with pytest.raises(HaReplError, match="names no configured server"):
        resolve_connection(None, None, config)


def test_with_nothing_configured_the_usual_address_and_hass_token(monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "env-token")

    connection = resolve_connection(None, None, Config())

    assert connection.url == "ws://homeassistant.local:8123/api/websocket"
    assert connection.token == "env-token"


def test_inside_an_addon_the_supervisor_still_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    config = _config(house=HOUSE)
    config.default = "house"

    connection = resolve_connection(None, None, config)

    assert connection.url == "ws://supervisor/core/websocket"
    assert connection.token == "supervisor-token"
    assert resolve_connection("house", None, config).name == "house"


def test_token_env(monkeypatch):
    server = Server("test", "http://t", token_env="HA_TEST_TOKEN")
    with pytest.raises(HaReplError, match="HA_TEST_TOKEN is not set"):
        server.resolve_token()

    monkeypatch.setenv("HA_TEST_TOKEN", "from-env")
    assert server.resolve_token() == "from-env"


def test_token_command_is_run_without_a_shell():
    # A shell would expand $HOME and act on the `;` - run directly, both
    # are just arguments.
    command = f"{sys.executable} -c 'import sys; print(sys.argv[1])' '$HOME;x'"
    server = Server("house", "http://h", token_command=command)

    assert server.resolve_token() == "$HOME;x"


def test_token_command_failing_is_an_error_without_its_output():
    command = f"{sys.executable} -c 'print(\"half-a-secret\"); raise SystemExit(3)'"
    server = Server("house", "http://h", token_command=command)

    with pytest.raises(HaReplError, match="exit status 3") as raised:
        server.resolve_token()
    assert "half-a-secret" not in str(raised.value)


def test_a_server_with_no_token_is_an_error():
    with pytest.raises(HaReplError, match="has no token"):
        resolve_connection("bare", None, _config(bare=Server("bare", "http://b")))


# --- the commands ----------------------------------------------------------


def _main(monkeypatch, *argv: str) -> object:
    monkeypatch.setattr(sys, "argv", ["ha-repl", *argv])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code


def test_servers_lists_names_and_marks_the_default_without_tokens(
    home, monkeypatch, capsys
):
    _write(
        home,
        """
        default = "dev"

        [servers.dev]
        url = "http://localhost:8123"
        token = "very-secret"

        [servers.house]
        url = "https://ha.example.org"
        token_command = "op read op://Private/ha/token"
        """,
    )

    assert _main(monkeypatch, "servers") == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "* dev    http://localhost:8123  (token)",
        "  house  https://ha.example.org  (token_command)",
    ]
    assert "very-secret" not in out


def test_servers_as_json(home, monkeypatch, capsys):
    _write(home, "[servers.dev]\nurl = 'http://localhost:8123'\ntoken = 'secret'\n")

    assert _main(monkeypatch, "--json", "servers") == 0

    out = capsys.readouterr().out
    assert '"token_source": "token"' in out
    assert "secret" not in out


def test_trust_command_allows_the_repo_dir(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo" / ".ha-repl"
    _write(repo, 'session = "one"\n')
    assert load_config().session is None

    assert _main(monkeypatch, "trust") == 0

    assert "config.toml" in capsys.readouterr().out
    assert load_config().session == "one"


def test_trust_command_with_nothing_to_trust(monkeypatch, capsys):
    assert _main(monkeypatch, "trust") == 2

    assert "no .ha-repl directory" in capsys.readouterr().err
