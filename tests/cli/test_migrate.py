"""Tests for the ``dct migrate`` CLI command."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api.migrate import (
    MigratedFile,
    MigrateError,
    MigrateNote,
    MigrateSummary,
)
from dbt_charts.cli.commands import migrate as migrate_command
from dbt_charts.cli.main import app

runner = CliRunner()


def test_migrate_help_describes_preview_and_paths() -> None:
    result = runner.invoke(app, ["migrate", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "PATH" in result.output


def test_migrate_dry_run_reports_would_update_and_forwards_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, object] = {}

    def fake_migrate(
        paths: list[Path] | None,
        *,
        project_dir: Path | None,
        dbt_project_dir: Path | None = None,
        dry_run: bool,
        diff: bool,
    ) -> None:
        received.update(
            paths=paths, project_dir=project_dir, dry_run=dry_run, diff=diff
        )
        migrate_command._emit(
            MigrateSummary(
                updated=[
                    MigratedFile(
                        path=PurePosixPath("charts/revenue.yml"), changes=[], diff=""
                    )
                ]
            ),
            dry_run=dry_run,
            diff=diff,
        )

    monkeypatch.setattr(migrate_command, "migrate_command", fake_migrate)

    result = runner.invoke(
        app,
        [
            "migrate",
            "--dry-run",
            "charts/revenue.yml",
            "--project-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Would update charts/revenue.yml" in result.output
    assert "1 files would be updated" in result.output
    assert received == {
        "paths": [Path("charts/revenue.yml")],
        "project_dir": tmp_path,
        "dry_run": True,
        "diff": False,
    }


def test_migrate_prints_deletion_reason_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_migrate(
        paths: list[Path] | None,
        *,
        project_dir: Path | None,
        dbt_project_dir: Path | None = None,
        dry_run: bool,
        diff: bool,
    ) -> None:
        migrate_command._emit(
            MigrateSummary(
                updated=[
                    MigratedFile(
                        path=PurePosixPath("charts/bar.yaml"), changes=[], diff=""
                    )
                ],
                notes=[
                    MigrateNote(
                        path=PurePosixPath("charts/bar.yaml"),
                        message="bar has no per-chart card to paint it onto.",
                    )
                ],
            ),
            dry_run=dry_run,
            diff=diff,
        )

    monkeypatch.setattr(migrate_command, "migrate_command", fake_migrate)

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert "Updated charts/bar.yaml" in result.output
    assert (
        "charts/bar.yaml: bar has no per-chart card to paint it onto." in result.output
    )


def test_migrate_reports_each_failed_file_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_migrate(
        paths: list[Path] | None,
        *,
        project_dir: Path | None,
        dbt_project_dir: Path | None = None,
        dry_run: bool,
        diff: bool,
    ) -> None:
        migrate_command._emit(
            MigrateSummary(
                errors=[
                    MigrateError(
                        path=PurePosixPath("charts/revenue.yml"),
                        message="Move the retired field manually.",
                    )
                ]
            ),
            dry_run=dry_run,
            diff=diff,
        )

    monkeypatch.setattr(migrate_command, "migrate_command", fake_migrate)

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 1
    assert "Error charts/revenue.yml: Move the retired field manually." in result.output
    assert "1 files with errors" in result.output


@pytest.mark.parametrize(
    ("flags", "shows_diff"),
    [
        pytest.param(["--dry-run"], False, id="dry-run"),
        pytest.param(["--dry-run", "--diff"], True, id="dry-run-diff"),
        pytest.param(["--diff"], True, id="diff"),
    ],
)
def test_migrate_lists_each_change_and_diffs_only_on_request(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], shows_diff: bool
) -> None:
    diff = "--- charts/bar.yaml\n+++ charts/bar.yaml (migrated)\n@@ -1 +1 @@\n-a\n+b\n"

    def fake_migrate(
        paths: list[Path] | None,
        *,
        project_dir: Path | None,
        dbt_project_dir: Path | None = None,
        dry_run: bool,
        diff: bool,
    ) -> None:
        migrate_command._emit(
            MigrateSummary(
                updated=[
                    MigratedFile(
                        path=PurePosixPath("charts/bar.yaml"),
                        changes=["style.board → style.frame"],
                        diff=diff_text,
                    )
                ],
            ),
            dry_run=dry_run,
            diff=diff,
        )

    diff_text = diff
    monkeypatch.setattr(migrate_command, "migrate_command", fake_migrate)

    result = runner.invoke(app, ["migrate", *flags])

    assert result.exit_code == 0, result.output
    assert "  style.board → style.frame\n" in result.output
    assert ("+++ charts/bar.yaml (migrated)" in result.output) is shows_diff
