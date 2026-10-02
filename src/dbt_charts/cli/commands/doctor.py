"""doctor command — thin wrapper over dbt_charts.agent_api.doctor."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from dbt_charts.cli._console import dct_console
from dbt_charts.cli._json_output import print_json_result

if TYPE_CHECKING:
    from dbt_charts.agent_api.doctor import DoctorReport

_console = dct_console()
_MARKS = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "–"}


def doctor_command(
    project_dir: Path | None,
    *,
    with_warehouse: bool,
    json_output: bool,
) -> None:
    from dbt_charts.agent_api.doctor import run_doctor

    # dbt logs a failed adapter import to stdout; --json must stay parseable.
    with contextlib.redirect_stdout(sys.stderr):
        report = run_doctor(project_dir or Path.cwd(), with_warehouse)
    if json_output:
        print_json_result(report)
    else:
        _print_report(report)
    raise typer.Exit(0 if report.success else 1)


def _print_report(report: DoctorReport) -> None:
    for check in report.checks:
        subject = f"{check.source}: " if check.source else ""
        # soft_wrap: paths and install commands must stay on one line to paste.
        _console.print(
            f" {_MARKS[check.status]} {check.code:<11} "
            f"{escape(subject + check.message)}",
            soft_wrap=True,
        )
        if check.hint:
            _console.print(f"{'':15}{escape(check.hint)}", soft_wrap=True)
    failed = sum(check.status == "fail" for check in report.checks)
    warned = sum(check.status == "warn" for check in report.checks)
    _console.print(f"\n{failed} failed, {warned} warnings.")
