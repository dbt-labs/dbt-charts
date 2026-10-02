"""Tests for `dct --version` output."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from typer.testing import CliRunner

import dbt_charts
from dbt_charts.cli.main import app

runner = CliRunner()


def test_version_command_prints_version_and_install_path() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    out = result.stdout
    assert dbt_charts.__version__ in out
    assert str(Path(dbt_charts.__file__).parent) in out
    assert platform.python_version() in out
    assert sys.executable in out


def test_version_flag_described_in_root_help() -> None:
    """dct --help must include a description for --version."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    combined = result.output + result.stderr
    assert "--version" in combined
    # The description must be present — not just the flag name with no text
    assert "Print version" in combined or "version and exit" in combined.lower()
