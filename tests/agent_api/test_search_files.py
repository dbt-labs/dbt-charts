"""``search_files``: one search over a project's boards and its other files."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api.search import (
    FileEntry,
    FileHit,
    SearchFilesArgs,
    search_file_hits,
    search_files,
)
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tools import dispatch_tool_call
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

BOARD = "title: Revenue\nnotes: Monthly revenue\ncharts:\n  a:\n    type: kpi\n"


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    files = {
        "charts/revenue.yml": BOARD,
        "charts/finance/ledger.yml": "title: Ledger\nnotes: Journal entries\ncharts:\n  a:\n    type: kpi\n",
        "charts/meta.yml": "style:\n  palette: warm\n",
        "charts/finance/meta.yml": "style:\n  palette: cool\n",
        "models/orders.sql": "select 1 as order_id\n",
        "models/staging/schema.yml": "models:\n  - name: orders\n    columns:\n      - name: order_id\n",
        "dbt_project.yml": "name: shop\n",
        "dbt_charts.yml": "sources:\n  warehouse:\n    type: duckdb\n",
        "README.md": "order_id lives here\n",
    }
    for relpath, content in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return local_project(tmp_path)


def _paths(
    project: Project,
    *,
    kind: str = "boards",
    path: str | None = None,
    text: str | None = None,
) -> list[str]:
    result = search_files(project, kind=kind, path=path, text=text)
    return [hit.file_path for hit in result.results]


def test_meta_lists_every_meta_file(project: FilesystemProject) -> None:
    assert _paths(project, kind="meta") == [
        "charts/finance/meta.yml",
        "charts/meta.yml",
    ]


def test_meta_lists_files_as_entries(project: FilesystemProject) -> None:
    result = search_files(project, kind="meta")

    assert all(isinstance(hit, FileEntry) for hit in result.results)


def test_text_returns_the_matching_lines(project: FilesystemProject) -> None:
    result = search_files(project, kind="meta", text="palette")

    lines = [h for h in result.results if isinstance(h, FileHit)]
    assert len(lines) == len(result.results)
    assert [(h.file_path, h.line, h.text) for h in lines] == [
        ("charts/finance/meta.yml", 2, "  palette: cool"),
        ("charts/meta.yml", 2, "  palette: warm"),
    ]


def test_models_covers_sql_and_schema_files_only(project: FilesystemProject) -> None:
    assert _paths(project, kind="models", text="order_id") == [
        "models/orders.sql",
        "models/staging/schema.yml",
    ]


def test_config_is_the_two_project_files(project: FilesystemProject) -> None:
    assert _paths(project, kind="config") == ["dbt_charts.yml", "dbt_project.yml"]


def test_all_reaches_every_file(project: FilesystemProject) -> None:
    assert "README.md" in _paths(project, kind="all", text="order_id")


def test_path_narrows_within_the_kind(project: FilesystemProject) -> None:
    assert _paths(project, kind="meta", path="charts/finance/*") == [
        "charts/finance/meta.yml"
    ]
    assert _paths(project, kind="models", text="order_id", path="**/schema.yml") == [
        "models/staging/schema.yml"
    ]


def test_path_narrows_which_boards(project: FilesystemProject) -> None:
    assert _paths(project, path="charts/finance/*") == ["charts/finance/ledger.yml"]


def test_boards_is_the_default_kind(project: FilesystemProject) -> None:
    assert _paths(project, text="revenue") == ["charts/revenue.yml"]
    assert _paths(project) == ["charts/finance/ledger.yml", "charts/revenue.yml"]


def test_file_results_page_with_a_cursor(project: FilesystemProject) -> None:
    first = search_files(project, kind="all", limit=2)
    second = search_files(project, kind="all", limit=2, cursor=first.next)

    assert first.next == "2"
    assert not {h.file_path for h in first.results} & {
        h.file_path for h in second.results
    }


def test_visibility_filters_before_the_cap(project: FilesystemProject) -> None:
    hits = search_file_hits(
        project,
        kind="all",
        visible=lambda paths: [p for p in paths if p == "README.md"],
    )

    assert [h.file_path for h in hits] == ["README.md"]


def test_an_unknown_kind_is_an_argument_error() -> None:
    with pytest.raises(ValidationError, match="kind"):
        SearchFilesArgs.model_validate({"kind": "everything"})


def test_a_path_escaping_the_project_is_an_argument_error() -> None:
    with pytest.raises(ValidationError, match="path"):
        SearchFilesArgs.model_validate({"kind": "all", "path": "../outside/*"})


def test_dispatch_reports_an_unknown_kind_with_the_registered_code(
    project: FilesystemProject,
) -> None:
    from dbt_charts.agent_api.project_session import ProjectSession

    with ProjectSession.from_project(project) as session:
        result = dispatch_tool_call(
            "search_files",
            {"kind": "everything"},
            context=DbtChartsAIContext(project_session=session),
        )

    assert result["success"] is False
    assert result["error_code"] == "ERR-TOOL-ARGUMENTS-INVALID"


def test_all_skips_build_and_cache_directories(project: FilesystemProject) -> None:
    venv = project.root / ".venv"
    venv.mkdir()
    (venv / "secret.py").write_text("order_id\n")

    assert not any(".venv" in p for p in _paths(project, kind="all", text="order_id"))


def test_a_store_backed_project_searches_its_own_files(
    tmp_path: Path, in_memory_project: Callable[..., Project]
) -> None:
    store = in_memory_project(
        tmp_path / "store-root", {"models/schema.yml": "models:\n  - name: orders\n"}
    )

    assert _paths(store, kind="models", text="orders") == ["models/schema.yml"]


def test_every_result_names_the_sources_sql_may_run_on(
    project: FilesystemProject,
) -> None:
    """An agent with only MCP tools otherwise reads dbt_charts.yml to learn
    what `execute_sql(source=...)` accepts."""
    for kind in ("boards", "models"):
        result = search_files(project, kind=kind, text="order")
        assert result.sources is not None
        assert [s.model_dump(exclude_none=True) for s in result.sources] == [
            {"name": "warehouse", "dialect": "duckdb"}
        ]


def test_source_entries_mark_the_implicit_dbt_source_as_the_default() -> None:
    from dbt_charts.agent_api.search import source_entries
    from dbt_charts.core.compile.config import ProjectSourcesConfig

    config = ProjectSourcesConfig(
        sources={"shop": {"type": "dbt_profile"}}, implicit_source="shop"
    )
    [entry] = source_entries(config)
    assert entry.name == "shop"
    assert entry.default is True


def test_a_broken_sources_section_is_reported_not_raised(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`kind: config` is how an agent finds the broken dbt_charts.yml, so the
    search still runs and names the config error."""
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  warehouse:\n    type: nosuchtype\n"
    )
    result = search_files(local_project(tmp_path), kind="config", text="")
    assert result.sources is None
    assert [hit.file_path for hit in result.results] == ["dbt_charts.yml"]
    assert "warehouse" in result.errors[0].message


@pytest.mark.parametrize(
    ("dialect", "type_"), [(None, None), ("duckdb", "csv")], ids=["neither", "both"]
)
def test_a_source_entry_has_exactly_one_of_dialect_or_type(
    dialect: str | None, type_: str | None
) -> None:
    from dbt_charts.agent_api.search import SourceEntry

    with pytest.raises(ValidationError, match="exactly one"):
        SourceEntry(name="db", dialect=dialect, type=type_)
