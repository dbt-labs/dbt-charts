"""Tests for dbt_charts.agent_api.project_session."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.agent_api import (
    ProjectSession,
    migrate as _migrate_module,
    query as _query_module,
    search as _search_module,
)
from dbt_charts.agent_api.migrate import MigrateSummary
from dbt_charts.agent_api.query import BoardQueryLookupResult
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core import board as _core_dashboard_module
from dbt_charts.core.compile import config as config_module
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.adapters.adapter_registry import (
    AdapterRegistry,
    build_adapter_registry as _real_build_adapter_registry,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.project import InMemoryBoard, Project

_VALID_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""


class TestProjectSessionOpenStoresProject:
    def test_open_resolves_project_root_from_project_dir(self, tmp_path: Path) -> None:
        with ProjectSession.open(project_dir=tmp_path) as project:
            assert isinstance(project.project, FilesystemProject)
            assert project.project.root == tmp_path.resolve()


def test_project_session_exposes_project_object(tmp_path: Path) -> None:
    with ProjectSession.open(tmp_path) as session:
        assert isinstance(session.project, FilesystemProject)
        assert session.project.root == tmp_path.resolve()


@pytest.fixture
def project_with_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> tuple[ProjectSession, Path]:
    boards = tmp_path / "charts"
    boards.mkdir()
    board_path = boards / "rev.yml"
    board_path.write_text(_VALID_BOARD)
    return ProjectSession(project=local_project(tmp_path)), board_path


def test_project_lookup_board_query_sql_forwards(
    project_with_board: tuple[ProjectSession, Path],
) -> None:
    project, board_path = project_with_board
    result = project.lookup_board_query_sql(name="revenue", path=board_path)
    assert isinstance(result, BoardQueryLookupResult)
    assert result.success is True
    assert "SUM(revenue)" in result.sql
    assert result.source == "analytics"


def test_project_lookup_board_query_sql_forwards_project_root_as_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_lookup(  # type: ignore[no-untyped-def]
        name: str,
        path: Path,
        *,
        project: Project | None = None,
        vars: dict[str, object] | None = None,
    ):
        captured["name"] = name
        captured["path"] = path
        captured["project"] = project
        return _query_module.BoardQueryLookupResult(success=True, sql="SELECT 1")

    monkeypatch.setattr(_query_module, "lookup_board_query_sql", fake_lookup)

    board = tmp_path / "board.yml"
    board.write_text(_VALID_BOARD)
    with ProjectSession.open(project_dir=tmp_path) as project:
        project.lookup_board_query_sql(name="revenue", path=board)

    assert isinstance(captured["project"], FilesystemProject)
    assert captured["project"].root == tmp_path.resolve()


# ── Cache lifecycle + render_board verb ───────────────────────────────────


def test_project_accepts_cache_and_does_not_close_passed_in_cache(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Caller-owned cache (direct constructor): close() must NOT close it."""
    cache = MagicMock(spec=TrivialDuckDBCache)

    project = ProjectSession(project=local_project(tmp_path), cache=cache)
    assert project.cache is cache

    project.close()
    cache.close.assert_not_called()


def test_project_open_does_not_close_caller_supplied_cache(tmp_path: Path) -> None:
    """Caller-owned cache (via ProjectSession.open(cache=...)): close() must NOT close it."""
    cache = MagicMock(spec=TrivialDuckDBCache)

    with ProjectSession.open(tmp_path, cache=cache) as project:
        assert project.cache is cache
    cache.close.assert_not_called()


def test_project_open_with_no_cache_leaves_cache_none(tmp_path: Path) -> None:
    """When no cache is passed, ProjectSession.open leaves project.cache as None.

    Callers that want caching pass `cache=open_cache(path)` (or
    `project_cache_ctx(project)`) explicitly; when omitted, project.cache stays
    None and the Executor skips
    persistent caching (result_cache is None). Synthesizing a default in-memory
    TrivialDuckDBCache here would silently cache across requests and serve stale
    results when the user edits an underlying CSV / DuckDB file mid-session.
    """
    with ProjectSession.open(tmp_path) as project:
        assert project.cache is None


def test_project_close_does_not_raise_when_cache_is_none(tmp_path: Path) -> None:
    """ProjectSession.close() is a no-op for the cache side when cache is None."""
    project = ProjectSession.open(tmp_path)
    project.close()


def test_project_render_board_forwards_cache_and_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """render_board verb threads self.cache + self.project.root through to agent_api."""
    from dbt_charts.core.render.board_links import LinkContext

    cache = MagicMock(spec=TrivialDuckDBCache)
    project = ProjectSession(project=local_project(tmp_path), cache=cache)

    captured: dict[str, Any] = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)

    # Prime the cached property BEFORE calling render_board so we can assert
    # render_board forwards the cached object, not a freshly-built one.
    pre_built = project.adapter_registry
    ctx = LinkContext()
    board = InMemoryBoard("", path=project.project.path("charts/x.yml"))
    result = project.render_board(board=board, format="json", link_context=ctx)

    assert result == "sentinel-result"
    assert captured["result_cache"] is cache
    assert captured["project"].root == tmp_path
    assert captured["board"] is board
    assert captured["format"] == "json"
    # Pin identity: render_board must forward the cached registry, not rebuild.
    assert captured["adapter_registry"] is pre_built
    assert captured["link_context"] is ctx


def test_project_render_board_forwards_compile_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """A caller that already compiled (e.g. to authorize a yaml_content
    render before executing it) must be able to render from that SAME
    CompileResult -- passing compile_result= is the seam that lets one
    compile govern both the authorization answer and the executed rows,
    instead of a second, differently-anchored compile disagreeing with the
    first."""
    from dbt_charts.core.compile.compiler import CompileResult

    project = ProjectSession(project=local_project(tmp_path))
    captured: dict[str, Any] = {}

    def fake_render(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)

    compile_result = CompileResult()
    board = InMemoryBoard("", path=project.project.path("charts/x.yml"))
    result = project.render_board(
        board=board, format="json", compile_result=compile_result
    )

    assert result == "sentinel-result"
    assert captured["compile_result"] is compile_result
    assert captured["board"] is board


def test_project_render_board_binds_the_svg_cache_for_the_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """The memo passed to render_board must be the active one *while* the render
    runs — binding it around the call is what lets the converter reach it without
    every signature in between growing a parameter."""
    from dbt_charts.core.render.svg_cache import RenderedSvgCache, active_svg_cache

    svg_cache = RenderedSvgCache()
    project = ProjectSession(project=local_project(tmp_path))
    seen: list[object] = []

    def fake_render(**kwargs: Any) -> str:
        seen.append(active_svg_cache())
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)
    project.render_board(format="json", svg_cache=svg_cache)

    assert seen == [svg_cache]
    assert active_svg_cache() is None


def test_project_render_board_runs_uncached_without_an_svg_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """Opting out is the default — no memo passed means no ambient cache."""
    from dbt_charts.core.render.svg_cache import active_svg_cache

    project = ProjectSession(project=local_project(tmp_path))
    seen: list[object] = []

    def fake_render(**kwargs: Any) -> str:
        seen.append(active_svg_cache())
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)
    project.render_board(format="json")

    assert seen == [None]


def test_project_render_board_forwards_chart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """The chart focus argument threads through to the agent_api delegate."""
    project = ProjectSession(project=local_project(tmp_path))
    captured: dict[str, Any] = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)

    board = InMemoryBoard("", path=project.project.path("charts/x.yml"))
    project.render_board(board=board, chart="monthly_sales")

    assert captured["chart"] == "monthly_sales"


def test_project_render_board_forwards_extra_render_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """Extra **render_options kwargs (e.g. terminal width/height) must thread
    through ProjectSession.render_board to the agent_api delegate unchanged so
    format-specific renderer options can be supplied without each one needing
    a named kwarg on every layer."""
    project = ProjectSession(project=local_project(tmp_path))
    captured: dict[str, Any] = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        return "sentinel-result"

    monkeypatch.setattr(_core_dashboard_module, "render_dashboard", fake_render)

    board = InMemoryBoard("", path=project.project.path("charts/x.yml"))
    project.render_board(
        board=board,
        format="terminal",
        width=120,
        height=40,
    )

    assert captured["width"] == 120
    assert captured["height"] == 40


def test_project_adapter_registry_lazy(tmp_path: Path) -> None:
    """adapter_registry builds on first access, caches on subsequent access."""
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")

    with (
        patch(
            "dbt_charts.agent_api.project_session.build_adapter_registry",
            side_effect=_real_build_adapter_registry,
        ) as spy,
        ProjectSession.open(tmp_path) as project,
    ):
        assert spy.call_count == 0  # lazy — not built at open()
        registry_a = project.adapter_registry
        assert isinstance(registry_a, AdapterRegistry)
        assert spy.call_count == 1
        registry_b = project.adapter_registry
        assert registry_b is registry_a  # cached
        assert spy.call_count == 1


def test_project_refresh_rebuilds_registry(tmp_path: Path) -> None:
    """refresh() closes the current registry and forces a rebuild on next access."""
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")

    with ProjectSession.open(tmp_path) as project:
        first = project.adapter_registry
        with patch.object(first, "close") as close_spy:
            project.refresh()
            close_spy.assert_called_once()
        second = project.adapter_registry
        assert second is not first


def test_project_close_closes_registry(tmp_path: Path) -> None:
    """ProjectSession.close() closes the registry if one was built; no-op otherwise."""
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")

    # Built path: close() forwards.
    project = ProjectSession.open(tmp_path)
    registry = project.adapter_registry
    with patch.object(registry, "close") as close_spy:
        project.close()
        close_spy.assert_called_once()

    # Unbuilt path: close() does not build a registry just to close it.
    project2 = ProjectSession.open(tmp_path)
    with patch(
        "dbt_charts.agent_api.project_session.build_adapter_registry"
    ) as build_spy:
        project2.close()
        build_spy.assert_not_called()


# ── sources / warnings_ignore fresh-read accessors ───────────────────────────


def test_project_sources_loader_called_with_project_root(tmp_path: Path) -> None:
    """sources loader is called on first access (lazy); subsequent reads use the cache."""
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  warehouse:\n    type: duckdb\n    path: wh.duckdb\n"
    )

    fake = ProjectSourcesConfig(sources={})
    with (
        patch.object(
            config_module, "load_project_sources", return_value=fake
        ) as loader,
        ProjectSession.open(tmp_path) as project,
    ):
        assert loader.call_count == 0  # not called at open() time — lazy
        first = project.sources
        assert loader.call_count == 1  # called on first access
        (arg,) = loader.call_args.args
        assert isinstance(arg, FilesystemProject) and arg.root == tmp_path.resolve()
        assert first is fake
        _ = project.sources
        _ = project.sources
        assert loader.call_count == 1  # no additional calls on subsequent reads


def test_project_warnings_ignore_loader_called_with_project_root(
    tmp_path: Path,
) -> None:
    """warnings_ignore loader is called on first access (lazy); subsequent reads use the cache."""
    (tmp_path / "dbt_charts.yml").write_text("warnings:\n  ignore:\n    - ERR-001\n")

    fake = frozenset({"ERR-001"})
    with (
        patch.object(
            config_module, "get_project_warnings_ignore", return_value=fake
        ) as loader,
        ProjectSession.open(tmp_path) as project,
    ):
        assert loader.call_count == 0  # not called at open() time — lazy
        first = project.warnings_ignore
        assert loader.call_count == 1  # called on first access
        (arg,) = loader.call_args.args
        assert isinstance(arg, FilesystemProject) and arg.root == tmp_path.resolve()
        assert first is fake
        _ = project.warnings_ignore
        assert loader.call_count == 1  # no additional calls on subsequent reads


def test_project_sources_caches_until_refresh(tmp_path: Path) -> None:
    """sources is read at open() time; refresh() re-reads and the value reflects disk."""
    dbt_charts_yml = tmp_path / "dbt_charts.yml"
    dbt_charts_yml.write_text("sources:\n  x:\n    type: duckdb\n    path: x.duckdb\n")

    with ProjectSession.open(tmp_path) as project:
        assert "x" in project.sources.sources

        dbt_charts_yml.write_text(
            "sources:\n  y:\n    type: duckdb\n    path: y.duckdb\n"
        )
        project.refresh()
        assert "y" in project.sources.sources


def test_project_warnings_ignore_caches_until_refresh(tmp_path: Path) -> None:
    """warnings_ignore is read at open() time; refresh() re-reads and the value reflects disk."""
    dbt_charts_yml = tmp_path / "dbt_charts.yml"
    dbt_charts_yml.write_text("warnings:\n  ignore:\n    - WARN-FANOUT-RISK\n")

    with ProjectSession.open(tmp_path) as project:
        assert project.warnings_ignore == frozenset({"WARN-FANOUT-RISK"})

        dbt_charts_yml.write_text("warnings:\n  ignore:\n    - WARN-REAGGREGATION\n")
        project.refresh()
        assert project.warnings_ignore == frozenset({"WARN-REAGGREGATION"})


def test_project_sources_is_read_only(tmp_path: Path) -> None:
    """project.sources cannot be reassigned from outside — lifecycle belongs to ProjectSession."""
    with ProjectSession.open(tmp_path) as project, pytest.raises(AttributeError):
        project.sources = ProjectSourcesConfig(sources={})  # type: ignore[misc]


def test_project_warnings_ignore_is_read_only(tmp_path: Path) -> None:
    """project.warnings_ignore cannot be reassigned from outside — lifecycle belongs to ProjectSession."""
    with ProjectSession.open(tmp_path) as project, pytest.raises(AttributeError):
        project.warnings_ignore = frozenset({"ERR-X"})  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Injected adapter_registry
# ---------------------------------------------------------------------------


def test_project_accepts_injected_adapter_registry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """ProjectSession(project=..., adapter_registry=reg) returns reg without lazy-build."""
    registry = _real_build_adapter_registry(local_project(tmp_path), read_only=True)
    project = ProjectSession(project=local_project(tmp_path), adapter_registry=registry)
    assert project.adapter_registry is registry


def test_project_injected_registry_skips_build(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """When adapter_registry is pre-injected, build_adapter_registry is never called."""
    registry = _real_build_adapter_registry(local_project(tmp_path), read_only=True)
    with patch(
        "dbt_charts.agent_api.project_session.build_adapter_registry"
    ) as mock_build:
        project = ProjectSession(
            project=local_project(tmp_path), adapter_registry=registry
        )
        _ = project.adapter_registry
    mock_build.assert_not_called()


def test_project_injected_registry_refresh_skips_registry_rebuild_but_clears_caches(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """refresh() skips registry rebuild but clears both config caches for injected-registry projects.

    After sources() and warnings_ignore() are each called once (priming both caches),
    refresh() must leave the injected registry unchanged and cause both loaders to be
    re-invoked on the next access.
    """
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  x:\n    type: duckdb\n    path: x.duckdb\n"
        "warnings:\n  ignore:\n    - WARN-FANOUT-RISK\n"
    )

    from dbt_charts.core.compile.config import (
        get_project_warnings_ignore as real_warnings_loader,
        load_project_sources as real_sources_loader,
    )

    registry = _real_build_adapter_registry(local_project(tmp_path), read_only=True)
    with (
        patch.object(
            config_module, "load_project_sources", side_effect=real_sources_loader
        ) as sources_loader,
        patch.object(
            config_module,
            "get_project_warnings_ignore",
            side_effect=real_warnings_loader,
        ) as warnings_loader,
    ):
        project = ProjectSession(
            project=local_project(tmp_path), adapter_registry=registry
        )

        # Lazy init: no loaders called at construction time.
        assert sources_loader.call_count == 0
        assert warnings_loader.call_count == 0

        _ = project.sources  # first access triggers load
        _ = project.warnings_ignore  # first access triggers load
        assert sources_loader.call_count == 1
        assert warnings_loader.call_count == 1

        project.refresh()  # invalidates caches, skips registry rebuild
        # Registry is unchanged — skipped because we don't own it.
        assert project.adapter_registry is registry

        _ = project.sources  # re-load after refresh
        _ = project.warnings_ignore  # re-load after refresh
        assert sources_loader.call_count == 2  # one re-load after refresh
        assert warnings_loader.call_count == 2  # one re-load after refresh


def test_refresh_sees_dbt_charts_yml_added_after_open(tmp_path: Path) -> None:
    """refresh() must pick up a dbt_charts.yml that didn't exist at open() time.

    Regression guard: if `Project` is reused across refresh() calls, its cached
    `sources` / `warnings_ignore` probes go stale. `dct serve` and `dct mcp serve`
    depend on refresh() picking up YAML files that appear after startup.
    """
    with ProjectSession.open(tmp_path) as session:
        assert session.warnings_ignore == frozenset()

        (tmp_path / "dbt_charts.yml").write_text(
            "warnings:\n  ignore:\n    - WARN-FANOUT-RISK\n"
        )
        session.refresh()

        assert session.warnings_ignore == frozenset({"WARN-FANOUT-RISK"})


def test_open_threads_resolver_to_adapter_registry(tmp_path: Path) -> None:
    """ProjectSession.open(resolver=...) must forward the resolver to the built AdapterRegistry.

    Regression: ProjectSession constructed with an injected registry (_owns_registry=False)
    makes refresh() a silent no-op. open() sets _owns_registry=True and must store
    resolver so the adapter_registry property passes it to build_adapter_registry().
    """
    from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

    class _SentinelResolver(DefaultSourceResolver):
        pass

    sentinel = _SentinelResolver()
    with ProjectSession.open(tmp_path, resolver=sentinel) as project:
        registry = project.adapter_registry
        assert registry._resolver is sentinel, (
            "Resolver passed to ProjectSession.open() must reach AdapterRegistry._resolver. "
            "Check that ProjectSession._resolver is set in open() and forwarded in the "
            "adapter_registry property's build_adapter_registry() call."
        )


@pytest.mark.parametrize("read_only", [True, False])
def test_project_init_passes_read_only_to_registry(
    read_only: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    from dbt_charts.agent_api import project_session as project_module

    captured: dict[str, object] = {}

    def fake_build(project_root: Path, **kwargs: object) -> object:
        captured.update(kwargs)

        class _Stub:
            def close(self) -> None: ...

        return _Stub()

    monkeypatch.setattr(project_module, "build_adapter_registry", fake_build)
    p = ProjectSession(project=local_project(tmp_path), read_only=read_only)
    _ = p.adapter_registry
    p.close()
    assert captured["read_only"] is read_only


@pytest.mark.parametrize("read_only", [True, False])
def test_project_open_threads_read_only_to_registry(
    read_only: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts.agent_api import project_session as project_module

    captured: dict[str, object] = {}

    def fake_build(project_root: Path, **kwargs: object) -> object:
        captured.update(kwargs)

        class _Stub:
            def close(self) -> None: ...

        return _Stub()

    monkeypatch.setattr(project_module, "build_adapter_registry", fake_build)
    p = ProjectSession.open(tmp_path, read_only=read_only)
    _ = p.adapter_registry
    p.close()
    assert captured["read_only"] is read_only


def test_project_init_defaults_to_read_only_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    from dbt_charts.agent_api import project_session as project_module

    captured: dict[str, object] = {}

    def fake_build(project_root: Path, **kwargs: object) -> object:
        captured.update(kwargs)

        class _Stub:
            def close(self) -> None: ...

        return _Stub()

    monkeypatch.setattr(project_module, "build_adapter_registry", fake_build)
    p = ProjectSession(project=local_project(tmp_path))
    _ = p.adapter_registry
    p.close()
    assert captured["read_only"] is True


class TestProjectSessionFromBoard:
    def test_from_board_discovers_project_root_above_board_file(
        self, tmp_path: Path
    ) -> None:
        # Mark tmp_path as a project root via the dbt_charts.yml marker.
        (tmp_path / "dbt_charts.yml").write_text("")
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        board = boards_dir / "f.yml"
        board.write_text("title: hi\ntext: x\n")

        with ProjectSession.from_board(board) as project:
            assert isinstance(project.project, FilesystemProject)
        assert project.project.root == tmp_path.resolve()


def test_project_session_forwards_migrate_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = ProjectSession.open(tmp_path)
    captured: dict[str, object] = {}

    def fake_migrate_paths(
        paths: list[PurePosixPath] | None,
        *,
        project: Project,
        dry_run: bool,
    ) -> MigrateSummary:
        captured.update(
            paths=paths,
            project=project,
            dry_run=dry_run,
        )
        return MigrateSummary()

    monkeypatch.setattr(_migrate_module, "migrate_paths", fake_migrate_paths)

    assert session.migrate_paths(
        [PurePosixPath("charts/example.yaml")], dry_run=True
    ).success
    assert captured["paths"] == [PurePosixPath("charts/example.yaml")]
    assert captured["project"] is session.project
    assert captured["dry_run"] is True


def test_project_session_migrates_an_in_memory_project(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], Project],
) -> None:
    """A structurally-current board needs no update -- unstamped and all,
    since ``_schema_version`` is never written as the sole reason to rewrite
    an otherwise-untouched file. A regression to stamping every touched file
    would make this land in ``.updated`` instead."""
    project = in_memory_project(tmp_path, {"charts/remote.yaml": "title: Remote\n"})
    session = ProjectSession(project)

    summary = session.migrate_paths([PurePosixPath("remote.yaml")], dry_run=True)

    assert summary.current == [PurePosixPath("charts/remote.yaml")]


def test_project_injected_registry_close_does_not_close_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """close() on a ProjectSession with an injected registry must NOT close the registry.

    The caller injected the registry and retains its lifecycle. ProjectSession does not
    own it (analogous to the _owns_cache / supplied-cache contract).
    """
    close_calls: list[str] = []

    class _SpyRegistry:
        def close(self) -> None:
            close_calls.append("close")

    project = ProjectSession(
        project=local_project(tmp_path),
        adapter_registry=_SpyRegistry(),  # type: ignore[arg-type]
    )
    project.close()
    assert close_calls == [], (
        "ProjectSession must not close an injected registry it does not own; "
        f"got close_calls={close_calls!r}"
    )


def test_project_injected_registry_with_owns_registry_closes_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """close() DOES close an injected registry when the caller opts in via owns_registry=True."""
    close_calls: list[str] = []

    class _SpyRegistry:
        def close(self) -> None:
            close_calls.append("close")

    project = ProjectSession(
        project=local_project(tmp_path),
        adapter_registry=_SpyRegistry(),  # type: ignore[arg-type]
        owns_registry=True,
    )
    with project:
        pass
    assert close_calls == ["close"], (
        "ProjectSession must close an injected registry when owns_registry=True; "
        f"got close_calls={close_calls!r}"
    )


def test_project_injected_registry_with_owns_registry_refresh_leaves_it_untouched(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """refresh() must not close or rebuild an injected registry, even when owns_registry=True."""
    close_calls: list[str] = []

    class _SpyRegistry:
        def close(self) -> None:
            close_calls.append("close")

    registry = _SpyRegistry()
    project = ProjectSession(
        project=local_project(tmp_path),
        adapter_registry=registry,  # type: ignore[arg-type]
        owns_registry=True,
    )

    project.refresh()

    assert project.adapter_registry is registry
    assert close_calls == []


def test_project_search_boards_forwards_project_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ProjectSession.search_boards() forwards self.project and limit to agent_api.search."""
    from dbt_charts.agent_api.search import SearchResult

    captured: dict[str, object] = {}

    def fake_search_boards(
        query: str, project: Project, *, limit: int = 10, **kwargs: object
    ) -> SearchResult:
        captured["query"] = query
        captured["project"] = project
        captured["limit"] = limit
        return SearchResult(success=True, errors=[], results=[])

    monkeypatch.setattr(_search_module, "search_boards", fake_search_boards)

    with ProjectSession.open(project_dir=tmp_path) as session:
        session.search_boards("foo", limit=5)

    assert captured["query"] == "foo"
    assert captured["project"] is session.project
    assert captured["limit"] == 5


def test_project_emit_board_forwards_project_registry_and_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """ProjectSession.emit_board() forwards self.project/.adapter_registry/.cache
    to agent_api.board_artifact.emit_board_artifact, plus the call's own args."""
    from dbt_charts.agent_api import board_artifact as _board_artifact_module
    from dbt_charts.agent_api.board_artifact import EmitBoardArtifactResult

    cache = MagicMock(spec=TrivialDuckDBCache)
    session = ProjectSession(project=local_project(tmp_path), cache=cache)
    captured: dict[str, Any] = {}

    def fake_emit_board_artifact(*args: Any, **kwargs: Any) -> EmitBoardArtifactResult:
        captured["args"] = args
        captured.update(kwargs)
        return EmitBoardArtifactResult(success=True)

    monkeypatch.setattr(
        _board_artifact_module, "emit_board_artifact", fake_emit_board_artifact
    )

    pre_built = session.adapter_registry
    board_path = tmp_path / "board.yml"
    artifact_path = tmp_path / "out" / "board.artifact.json"
    recording_path = tmp_path / "out" / "board.recording.json"

    result = session.emit_board(
        board_path, artifact_path, recording_path, variables={"region": "West"}
    )

    assert result.success
    assert captured["args"] == (board_path, artifact_path, recording_path)
    assert captured["project"] is session.project
    assert captured["adapter_registry"] is pre_built
    assert captured["result_cache"] is cache
    assert captured["variables"] == {"region": "West"}


def test_project_adapter_registry_threads_project(tmp_path: Path) -> None:
    """ProjectSession.adapter_registry passes the project object to build_adapter_registry.

    Locks the threading contract: build_adapter_registry receives the same Project
    instance stored on the session (identity, not equality). The project carries
    the sources snapshot via its cached_property.
    """
    with patch(
        "dbt_charts.agent_api.project_session.build_adapter_registry"
    ) as mock_build:
        mock_build.return_value = MagicMock(spec=["close"])
        with ProjectSession.open(tmp_path) as session:
            _ = session.adapter_registry
            assert mock_build.called, "build_adapter_registry was never called"
            passed_project = (
                mock_build.call_args.args[0]
                if mock_build.call_args.args
                else mock_build.call_args.kwargs.get("project")
            )
            assert passed_project is session.project, (
                "build_adapter_registry must receive the same Project instance "
                "stored on the session (identity, not equality). "
                "A new Project() inside adapter_registry would break source caching."
            )
