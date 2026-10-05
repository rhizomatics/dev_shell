"""Tests for print_result() - how an exec result from the server is shown."""

from __future__ import annotations

from homeassistant_repl.cli import print_result


def _base_result(**overrides) -> dict:
    base = {
        "stdout": "",
        "value": None,
        "arrow": None,
        "arrow_truncated": False,
        "error": None,
        "duration": 0.0,
        "truncated": False,
    }
    base.update(overrides)
    return base


def test_print_result_prints_value(capsys):
    print_result(_base_result(value="42"))

    assert capsys.readouterr().out == "42\n"


def test_print_result_shows_a_sql_result_as_its_repr_not_a_table(capsys):
    result = _base_result(arrow="AAAA", value="<SqlResult 2 rows x 2 cols [a, b]>")

    print_result(result)

    assert capsys.readouterr().out == "<SqlResult 2 rows x 2 cols [a, b]>\n"
