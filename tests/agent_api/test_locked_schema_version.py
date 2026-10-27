"""``locked_schema_version``: a project pins the board grammar it targets."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from dbt_charts.agent_api.migrate import migrate_paths
from dbt_charts.agent_api.validate import ValidateResult, validate
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import validate_project_config_data
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

_LOCK = "0.10.0"
_BOARD = """
queries:
  q:
    sql: SELECT region, revenue FROM orders
    source: s
charts:
  c:
    query: q
    type: bar
    x: region
    y: revenue
{extra}
rows:
  - c
"""
_DEV_ONLY_CHART = "    annotations:\n      - type: rule\n        y: 12\n"
_OLD_BOARD = (
    "title: T\nstyle:\n  board:\n    width: 800\n"
    "charts:\n  c:\n    query: q\n    type: line\n"
)


def _project(
    root: Path, files: dict[str, str], lock: str | None = _LOCK
) -> FilesystemProject:
    config = "name: p\n" + (f"locked_schema_version: {lock}\n" if lock else "")
    for relpath, text in {"dbt_charts.yml": config, **files}.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return FilesystemProject(root)


def _validate(
    project: FilesystemProject, relpath: str = "charts/b.yaml"
) -> ValidateResult:
    return validate(Path(relpath), project=project)


def test_lock_reads_a_released_version_and_defaults_to_none(tmp_path: Path) -> None:
    assert _project(tmp_path, {}).locked_schema_version == _LOCK
    assert _project(tmp_path, {}, lock=None).locked_schema_version is None


@pytest.mark.parametrize("bad", ["9.9.9", load_yaml_schema_catalog().dev.version])
def test_lock_must_name_a_released_version(bad: str) -> None:
    with pytest.raises(CompilationError, match=r"released versions: 0\.3\.0"):
        validate_project_config_data(
            {"locked_schema_version": bad}, filename="dbt_charts.yml"
        )


def test_validate_rejects_dev_only_grammar_under_the_lock(tmp_path: Path) -> None:
    board = {"charts/b.yaml": _BOARD.format(extra=_DEV_ONLY_CHART)}

    unlocked = _validate(_project(tmp_path / "a", board, lock=None))
    locked = _validate(_project(tmp_path / "b", board))

    assert unlocked.success
    assert not locked.success
    assert f"locked_schema_version {_LOCK}" in locked.errors[0].message
    assert "charts.c: Additional properties are not allowed ('annotations'" in (
        locked.errors[0].message
    )


@pytest.mark.parametrize(
    "config",
    [
        "name: p\nlocked_schema_version: '9.9.9'\n",
        "name: p\nwarnings:\n  ignore: [WARN-NOPE]\n",
    ],
)
def test_validate_reports_a_bad_project_config_as_a_diagnostic(
    tmp_path: Path, config: str
) -> None:
    project = _project(tmp_path, {"charts/b.yaml": _BOARD.format(extra="")}, lock=None)
    (tmp_path / "dbt_charts.yml").write_text(config)

    result = _validate(project)

    assert not result.success
    assert result.errors[0].code == "ERR-PROJECT-CONFIG-SCHEMA"


def test_validate_checks_meta_files_against_the_lock(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "charts/b.yaml": _BOARD.format(extra=""),
            "charts/meta.yml": "style:\n  annotations:\n    band: {}\n",
        },
    )

    result = _validate(project)

    assert not result.success
    assert "charts/meta.yml" in result.errors[0].message


def test_validate_rejects_dev_only_grammar_beside_a_meta_lint_block(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        {
            "charts/b.yaml": _BOARD.format(extra=""),
            "charts/meta.yml": (
                "lint:\n  ignore: []\nstyle:\n  annotations:\n    band: {}\n"
            ),
        },
    )

    result = _validate(project)

    assert not result.success
    assert "charts/meta.yml" in result.errors[0].message


def test_validate_reports_a_fragment_it_cannot_check(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "charts/b.yaml": _BOARD.format(extra=""),
            "charts/meta.yml": "bogus_key: 1\n",
        },
    )

    result = _validate(project)

    assert not result.success
    assert "cannot check against locked_schema_version" in result.errors[0].message


def test_validate_accepts_an_empty_meta_file(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "charts/b.yaml": _BOARD.format(extra=""),
            "charts/meta.yml": "# shared\n",
        },
    )

    assert _validate(project).success


def test_validate_checks_extends_fragments_against_the_lock(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "charts/b.yaml": "extends: _frag.yaml\n" + _BOARD.format(extra=""),
            "charts/_frag.yaml": "style:\n  annotations:\n    band: {}\n",
        },
    )

    result = _validate(project)

    assert not result.success
    assert "charts/_frag.yaml" in result.errors[0].message


def test_lock_silences_warnings_newer_than_it(tmp_path: Path) -> None:
    catalog = load_yaml_schema_catalog()
    newer = catalog.warnings_newer_than("0.3.0")

    unlocked = _project(tmp_path / "u", {}, lock=None)
    locked = _project(tmp_path / "l", {}, lock="0.3.0")

    assert unlocked.warnings_ignore == frozenset()
    assert newer and newer <= locked.warnings_ignore


def test_migrate_stops_at_the_lock(tmp_path: Path) -> None:
    files = {"charts/a.yaml": _OLD_BOARD}
    path = [PurePosixPath("charts/a.yaml")]

    at_lock = migrate_paths(
        path, project=_project(tmp_path / "a", files, lock="0.4.0"), dry_run=True
    )
    unlocked = migrate_paths(
        path, project=_project(tmp_path / "b", files, lock=None), dry_run=True
    )

    assert at_lock.current == path
    assert unlocked.updated == path


def test_migrate_leaves_a_board_newer_than_the_lock_unchanged(tmp_path: Path) -> None:
    newer = "title: T\nstyle:\n  frame:\n    width: 800\ncharts:\n  c:\n    query: q\n    type: line\n"
    project = _project(tmp_path, {"charts/a.yaml": newer}, lock="0.4.0")

    summary = migrate_paths(
        [PurePosixPath("charts/a.yaml")], project=project, dry_run=True
    )

    assert summary.current == [PurePosixPath("charts/a.yaml")]
