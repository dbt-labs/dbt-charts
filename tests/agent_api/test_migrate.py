from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

import pytest

from dbt_charts.agent_api.migrate import (
    MigrateError,
    MigrateNote,
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

    def migrate_text(text: str) -> str:
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real dct migrate file-rewrite path, end to end -- migrate_paths,
    migrate_board_yaml_text, and migrate_yaml_text are all real and unstubbed.

    Only ``_board_migration_context`` is replaced, with a synthetic catalog
    carrying an already-*frozen* Deletion (source and target both real
    schema-catalog positions, not the pending ``0.5.0 -> current`` boundary)
    -- `dct migrate` caps at the latest frozen version, so a deletion sourced
    from the pending boundary would no longer fire and this test would
    assert nothing. The retired field is real
    (``style.charts.bar.font``, actually removed from ``AuthoredBoard`` --
    only its migration *declaration* is presently pending), so the migrated
    output still validates against the real, current model.
    """
    from dbt_charts.core.compile.migrations import migrations as migrations_module
    from dbt_charts.core.compile.migrations.migrations import (
        Deletion,
        MigrationRegistry,
    )
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        YamlSchemaCatalog,
        YamlSchemaEntry,
    )

    v1, v2 = "0.1.0", "0.2.0"
    board_props = {"title": {}, "queries": {}, "charts": {}, "rows": {}}
    v1_schema = {
        "type": "object",
        "properties": {**board_props, "style": {}},
        "additionalProperties": False,
    }
    v2_schema = {
        "type": "object",
        "properties": board_props,
        "additionalProperties": False,
    }
    dev = "0.3.0"
    catalog = YamlSchemaCatalog(
        entries=(
            YamlSchemaEntry(
                dev,
                status="DEV",
                released_at=None,
                filename=None,
                sha256=None,
                predecessor=v2,
            ),
            YamlSchemaEntry(
                v2,
                status="RELEASED",
                released_at=date.today(),
                filename="v2.json",
                sha256="t",
                predecessor=v1,
            ),
            YamlSchemaEntry(
                v1,
                status="RELEASED",
                released_at=date.today(),
                filename="v1.json",
                sha256="t",
                predecessor=None,
            ),
        ),
        _schemas={v1: v1_schema, v2: v2_schema, dev: v2_schema},
        current_schema=v2_schema,
    )
    reason = (
        "bar has no per-chart card to paint it onto (it renders via Vega-Lite "
        "with no card surface separate from the board frame). The field had "
        "no effect and has been removed."
    )
    registry = MigrationRegistry(
        moves=[],
        deletions=[Deletion(v1, v2, ("style",), reason=reason)],
        catalog=catalog,
    )
    monkeypatch.setattr(
        migrations_module,
        "_board_migration_context",
        lambda: (catalog, registry),
    )

    project = in_memory_project(tmp_path, {"charts/bar.yaml": _BAR_FONT_BOARD})

    summary = migrate_paths([PurePosixPath("bar.yaml")], project=project, dry_run=False)

    assert summary.updated == [PurePosixPath("charts/bar.yaml")]
    assert summary.notes == [
        MigrateNote(
            path=PurePosixPath("charts/bar.yaml"),
            message=f"`style` was removed: {reason}",
        )
    ]
    rewritten = project.read_text("charts/bar.yaml")
    assert "style:" not in rewritten
    # Exercises the stamp on the >=1-iteration path (a real structural
    # deletion fired before reaching stop_target), not just the early-return
    # "already current" and "0-iteration, already at target" paths covered
    # by the migrations.py unit tests.
    assert f'_schema_version: "{v2}"' in rewritten


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

    def migrate_text(text: str) -> str:
        raise ParseError("Empty YAML document")

    monkeypatch.setattr(migrations, "migrate_board_yaml_text", migrate_text)

    summary = migrate_paths([PurePosixPath("meta.yml")], project=project, dry_run=False)

    assert summary.errors == [
        MigrateError(
            path=PurePosixPath("charts/meta.yml"), message="Empty YAML document"
        )
    ]
    assert not summary.updated
