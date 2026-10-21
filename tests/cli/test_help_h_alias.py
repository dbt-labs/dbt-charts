"""Guard that `-h` works as a `--help` alias on every command surface.

`dct inspect table` (the private-package-only subcommand) is intentionally
not covered here — it can only ever run with the private package installed.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _normalize(output: str) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", output)).strip()


# Root + one level deep covers every Typer() instance in the CLI tree.
# Click inherits help_option_names down the context, so subcommand-of-subcommand
# coverage falls out for free.
HELP_TARGETS: list[list[str]] = [
    [],  # dct -h
    ["validate"],
    ["describe"],
    ["render"],
    ["query"],
    ["search"],
    ["skills"],
    ["serve"],
    ["init"],
    ["init", "mcp"],
    ["mcp"],
    ["mcp", "serve"],
    ["inspect"],
]


@pytest.mark.parametrize(
    "argv",
    HELP_TARGETS,
    ids=lambda a: " ".join(["dct", *a]) or "dct",
)
def test_h_matches_help(argv: list[str]) -> None:
    short = runner.invoke(app, [*argv, "-h"])
    long = runner.invoke(app, [*argv, "--help"])
    assert short.exit_code == 0, (
        f"`dct {' '.join(argv)} -h` failed (exit {short.exit_code}):\n{short.output}"
    )
    assert long.exit_code == 0
    assert _normalize(short.output) == _normalize(long.output)


def test_query_metavars_are_context_and_query() -> None:
    result = runner.invoke(app, ["query", "--help"])
    assert result.exit_code == 0
    output = _ANSI.sub("", result.output)
    assert "CONTEXT" in output
    assert "QUERY" in output


def test_skills_metavar_is_skill_name() -> None:
    result = runner.invoke(app, ["skills", "--help"])
    assert result.exit_code == 0
    assert "SKILL_NAME" in _ANSI.sub("", result.output)
