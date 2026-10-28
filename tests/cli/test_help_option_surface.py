"""Each verb's ``--help`` documents its option surface.

Catches a verb silently losing (or unexpectedly gaining) a flag. Help text is
pure argument-parser output, so this needs no subprocess — the e2e lane pays
~7s of runner CPU per ``dct`` spawn, and none of it buys signal here.

Under ``CliRunner`` the captured stream is not a terminal, so Typer falls back
to plain Click formatting at a hard 80 columns — ``COLUMNS`` has no effect. A
long option name can therefore wrap mid-token, which a naive substring match
would miss, so matching runs against a whitespace-stripped copy of the output.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()


def _squashed(text: str) -> str:
    """Output with all whitespace removed, so a wrapped option name still matches."""
    return "".join(text.split())


@pytest.mark.parametrize(
    ("argv", "expected", "forbidden"),
    [
        pytest.param(
            ["serve", "--help"],
            [
                "--port",
                "--host",
                "--project-dir",
                "--dbt-project-dir",
                "--dialect",
                "--target",
            ],
            [],
            id="serve",
        ),
        pytest.param(
            ["init", "--help"],
            [
                "--project-dir",
                "--force",
            ],
            ["--yes", "--skills", "--mcp", "--vscode", "--cursor"],
            id="init",
        ),
        pytest.param(
            ["init", "mcp", "--help"],
            ["--all", "--force", "--project-dir"],
            ["--no-skills"],
            id="init-mcp",
        ),
        pytest.param(
            ["mcp", "serve", "--help"],
            ["--project-dir", "--dbt-project-dir"],
            [],
            id="mcp-serve",
        ),
        pytest.param(
            ["inspect", "--help"],
            ["table", "audit", "eject", "templates", "validate-templates"],
            [],
            id="inspect",
        ),
    ],
)
def test_help_documents_option_surface(
    argv: list[str], expected: list[str], forbidden: list[str]
) -> None:
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, f"`dct {' '.join(argv)}` exited {result.exit_code}"
    output = _squashed(result.output)
    for option in expected:
        assert option in output, f"`dct {' '.join(argv)}` no longer documents {option}"
    for option in forbidden:
        assert option not in output, (
            f"`dct {' '.join(argv)}` unexpectedly documents {option}"
        )


def test_playground_help_renders_as_a_leaf_command() -> None:
    """playground is a leaf command with options only, not a Typer group.

    Registered as a group (``add_typer`` rather than ``@app.command``) the usage
    line gains ``COMMAND [ARGS]...``, so those two tokens are what distinguishes
    the two registrations.
    """
    result = runner.invoke(app, ["playground", "--help"])
    assert result.exit_code == 0
    assert "playground" in result.output.lower()

    usage_line = next(
        (line for line in result.output.splitlines() if line.startswith("Usage:")),
        "",
    )
    assert usage_line, f"no Usage: line in help output:\n{result.output}"
    assert "COMMAND" not in usage_line, (
        f"playground is a leaf command — spurious COMMAND token in: {usage_line!r}"
    )
    assert "[ARGS]" not in usage_line, (
        f"playground is a leaf command — spurious [ARGS] token in: {usage_line!r}"
    )
