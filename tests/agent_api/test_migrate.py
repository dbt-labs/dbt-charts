from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest

from dbt_charts.agent_api.migrate import (
    MigrateError,
    MigrateSummary,
    migrate_paths,
)
from dbt_charts.core.compile.errors import ParseError
from dbt_charts.core.project import Project


def test_migrate_summary_exposes_a_machine_readable_success_flag() -> None:
    summary = MigrateSummary(updated=[PurePosixPath("charts/new.yaml")])

    assert summary.success is True


def test_migrate_summary_is_unsuccessful_when_any_file_failed() -> None:
    summary = MigrateSummary(
        errors=[
            MigrateError(path=PurePosixPath("charts/bad.yaml"), message="manual action")
        ]
    )

    assert summary.success is False


def test_migrate_paths_does_not_leak_a_warning_for_a_pending_boundary_construct(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> None:
    """A board with a real frozen-boundary change (`style.board:`, renamed to
    `style.frame:` at the 0.4.0 -> 0.5.0 boundary) also carries a construct
    on the pending (unreleased) boundary -- `description:`, still unmigrated
    after the frozen-capped rewrite. The frozen change is what makes
    migrate_paths write the file at all (_schema_version is only stamped
    alongside a real change); the leftover `description:` is what re-triggers
    the in-memory migration path when migrate_paths re-parses the result to
    verify it. That path's own SchemaMigrationWarning must be caught here,
    not leaked to the default warning handler, and must not show up as a
    MigrateNote (it is not a removed-field reason, and dct migrate already
    ran)."""
    project = in_memory_project(
        tmp_path,
        {
            "charts/a.yaml": "title: T\ndescription: p\nstyle:\n  board:\n"
            "    width: 800\ncharts:\n  c:\n    query: q\n    type: line\n"
        },
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        summary = migrate_paths(
            [PurePosixPath("a.yaml")], project=project, dry_run=True
        )

    assert caught == []
    assert summary.updated == [PurePosixPath("charts/a.yaml")]
    assert summary.notes == []


def test_migrate_paths_with_no_explicit_paths_touches_host_boards_only(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> None:
    """Regression: migrate's default (no explicit --paths) walk must stay
    scoped to the host charts/ root -- _migration_paths passes
    under=CHARTS_SUBDIR to iter_boards deliberately, since package boards are
    read-only (write_text on a dbt_packages/ path raises). Dropping that
    scope silently (switching to iter_boards() over every root) would send
    every migratable package board through the same write attempt."""
    migratable = (
        "title: T\ndescription: p\nstyle:\n  board:\n"
        "    width: 800\ncharts:\n  c:\n    query: q\n    type: line\n"
    )
    project = in_memory_project(
        tmp_path,
        {
            "charts/a.yaml": migratable,
            "dbt_packages/pkg_a/charts/b.yaml": migratable,
        },
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        summary = migrate_paths(None, project=project, dry_run=True)

    touched = {str(p) for p in summary.updated + summary.current}
    touched.update(str(e.path) for e in summary.errors)
    assert touched == {"charts/a.yaml"}


def test_migrates_a_project_backed_board_without_a_filesystem_path(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = in_memory_project(tmp_path, {"charts/remote.yaml": "title: Remote\n"})

    from dbt_charts.core.compile import migrations

    def migrate_text(text: str, *, locked_schema_version: str | None) -> str:
        return text + "# migrated\n"

    monkeypatch.setattr(
        migrations,
        "migrate_board_yaml_text",
        migrate_text,
    )

    summary = migrate_paths(
        [PurePosixPath("remote.yaml")], project=project, dry_run=False
    )

    assert summary.updated == [PurePosixPath("charts/remote.yaml")]
    assert project.read_text("charts/remote.yaml").endswith("# migrated\n")


_BAR_FONT_BOARD = """title: T
queries:
  q:
    source: db
    sql: SELECT 1 AS m, 2 AS v
charts:
  c:
    type: bar
    query: q
    x: m
    y: v
rows:
  - c
style:
  charts:
    bar:
      font:
        size: 12
"""


def test_migrate_paths_surfaces_deletion_reason_for_a_real_board(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> None:
    """The real dct migrate file-rewrite path, end to end: the engine plans the
    rewrite, the writer strikes the retired key and stamps the file, and the
    deletion's reason reaches the summary."""
    from dbt_charts.core.compile.migrations.migrations import package_schemas

    project = in_memory_project(tmp_path, {"charts/bar.yaml": _BAR_FONT_BOARD})

    summary = migrate_paths([PurePosixPath("bar.yaml")], project=project, dry_run=False)

    assert summary.updated == [PurePosixPath("charts/bar.yaml")]
    assert [n.path for n in summary.notes] == [PurePosixPath("charts/bar.yaml")]
    assert summary.notes[0].message.startswith(
        "`charts.bar.font` was removed: bar has no"
    )
    rewritten = project.read_text("charts/bar.yaml")
    assert "font:" not in rewritten
    # The stamp lands on the >=1-iteration path: a real structural deletion
    # fired before the walk reached the latest released grammar.
    latest = package_schemas().latest_released.version
    assert f'_schema_version: "{latest}"' in rewritten


def test_migrate_paths_records_parse_error_as_migrate_error(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ParseError from migrate_board_yaml_text is recorded as MigrateError, not raised.

    A deletion that empties a board raises ParseError from load_yaml_mapping.
    Before the fix, this escaped migrate_paths' (OSError, ValueError) handler
    and aborted the rest of the migration run.
    """
    project = in_memory_project(tmp_path, {"charts/meta.yml": "style:\n  color: red\n"})

    from dbt_charts.core.compile import migrations

    def migrate_text(text: str, *, locked_schema_version: str | None) -> str:
        raise ParseError("Empty YAML document")

    monkeypatch.setattr(migrations, "migrate_board_yaml_text", migrate_text)

    summary = migrate_paths([PurePosixPath("meta.yml")], project=project, dry_run=False)

    assert summary.errors == [
        MigrateError(
            path=PurePosixPath("charts/meta.yml"), message="Empty YAML document"
        )
    ]
    assert not summary.updated
