"""Tests for print_result() - how an exec result from the server is shown."""

from __future__ import annotations

from homeassistant_repl.cli import print_result


def _base_result(**overrides) -> dict:
    base = {
        "stdout": "",
        "value": None,
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
    result = _base_result(value="<SqlResult 2 rows x 2 cols [a, b]>")

    print_result(result)

    assert capsys.readouterr().out == "<SqlResult 2 rows x 2 cols [a, b]>\n"


def test_print_result_pretty_prints_a_value_sent_as_a_tree(capsys):
    tree = {"t": "dict", "v": [["a", {"t": "repr", "r": "<state sun.sun=above>"}]]}

    print_result(_base_result(value="ignored", value_tree=tree))

    assert capsys.readouterr().out == "{'a': <state sun.sun=above>}\n"


def test_print_result_draws_a_traceback_from_the_frames_sent(capsys):
    error = {
        "type": "ZeroDivisionError",
        "message": "division by zero",
        "traceback": "plain text, unused\n",
        "stacks": [
            {
                "type": "ZeroDivisionError",
                "message": "division by zero",
                "is_cause": False,
                "notes": [],
                "hidden": 0,
                "frames": [
                    {
                        "file": "/usr/src/homeassistant/homeassistant/core.py",
                        "line": 12,
                        "name": "boom",
                        "first": 11,
                        "source": ["def boom():\n", "    return 1 / 0\n"],
                        "position": [12, 11, 12, 16],
                    }
                ],
            }
        ],
    }

    print_result(_base_result(error=error))

    err = capsys.readouterr().err
    assert "core.py:12 in boom" in err
    assert "12 " in err and "return 1 / 0" in err
    assert "ZeroDivisionError: division by zero" in err
    assert "plain text" not in err


def test_print_result_falls_back_to_the_plain_traceback(capsys):
    error = {"type": "SyntaxError", "message": "m", "traceback": "SyntaxError: m\n"}

    print_result(_base_result(error=error))

    assert capsys.readouterr().err == "SyntaxError: m\n"
