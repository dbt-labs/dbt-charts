"""End-to-end: WARN_QUERY_RESULT_TRUNCATED surfaces from render() when a
chart's query result was truncated by execution.max_rows/max_result_bytes,
and never affects BoardRenderResult.status.

Proves the full seam — compile -> render() -> RenderResult.warnings, and
compile -> ProjectSession.render_board() -> BoardRenderResult.status — not a
hand-built WarningContext.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-QUERY-RESULT-TRUNCATED"

_BOARD_YAML = """\
title: Probe
charts:
  c:
    query: q
    type: table
    columns:
      - x
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""


def _make_executor(board: object, query_registry: object, truncated_reason: str | None):
    ok = Mock()
    ok.is_success = True
    ok.data = [{"x": 1}, {"x": 2}, {"x": 3}]
    ok.resolved_relations = None
    ok.truncated_reason = truncated_reason
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def test_fires_when_query_result_truncated() -> None:
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, "max_rows")

    render_result = render(result.board, executor, format="svg")
    warnings = list(render_result.warnings)

    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "c"
    assert w.level == "warning"
    assert "max_rows" in w.message
    assert "3" in w.message
    assert w.fix


def test_silent_when_not_truncated() -> None:
    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, None)

    render_result = render(result.board, executor, format="svg")
    codes = {w.code for w in render_result.warnings}
    assert _CODE not in codes


class TestBoardStatusUnaffected:
    """Truncation is advisory — BoardRenderResult.status must stay 'ok', not
    downgrade to 'partial'/'failed', per board.py's 'warnings never affect
    status' comment. Drives real truncation end-to-end via a low
    DCT_MAX_ROWS_CEILING against a real DuckDB source, rather than a mocked
    QueryResult.truncated_reason — this is the surface a user actually hits.
    """

    def test_status_stays_ok_when_truncated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.agent_api.project_session import ProjectSession
        from dbt_charts.cli.filesystem_project import FilesystemProject

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        project = FilesystemProject(tmp_path)
        (tmp_path / "charts").mkdir()
        board_path = tmp_path / "charts" / "board.yml"
        board_path.write_text(
            """\
title: Probe
queries:
  q:
    sql: SELECT x, x AS y FROM range(10) AS t(x)
    source: db
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
rows:
  - c
"""
        )
        (tmp_path / "dbt_charts.yml").write_text(
            """\
sources:
  db:
    type: duckdb
"""
        )

        session = ProjectSession.from_project(project)
        try:
            rendered = session.render_board(
                board=session.project.path("charts/board.yml").read_board(),
                format="svg",
            )
        finally:
            session.close()

        assert rendered.status == "ok"
        codes = {w.code for w in rendered.warnings}
        assert _CODE in codes, codes


class TestUnattributedTruncation:
    """A truncated upstream query demand-executed by a cache-ref composed query
    must surface WARN-QUERY-RESULT-TRUNCATED even though no chart owns the
    upstream directly. The warning must carry chart=None and
    path=queries.<upstream_name>.
    """

    def test_cache_ref_upstream_truncation_surfaces_as_board_level_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        # Ceiling low enough to truncate the upstream but not the composed result
        # (composed query limits are ceiling-bound on *its* fetch, not reused).
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")

        # "upstream" is charted nowhere; "composed" is the only charted query.
        board_yaml = """\
title: Probe
queries:
  upstream:
    sql: SELECT * FROM range(10) AS t(n)
    source: db
    cache: true
  composed:
    sql: SELECT * FROM {{ queries.upstream.cache }} LIMIT 1
    source: db
charts:
  c:
    query: composed
    type: table
    columns:
      - n
rows:
  - c
"""
        project = FilesystemProject(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  db:\n    type: duckdb\n", encoding="utf-8"
        )
        registry = build_adapter_registry(project)
        result = compile(board_yaml)
        assert result.success and result.board is not None, result.errors
        cache = TrivialDuckDBCache(db_path=tmp_path / "cache.duckdb")
        try:
            executor = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            render_result = render(result.board, executor, format="svg")
        finally:
            cache.close()

        codes = {w.code for w in render_result.warnings}
        assert _CODE in codes, (
            f"Expected {_CODE!r} in warnings for truncated upstream, got {codes}"
        )
        # The diagnostic must be board-level (no chart) and reference the
        # upstream query by name, not by the composed chart.
        upstream_w = next(
            (w for w in render_result.warnings if w.code == _CODE and w.chart is None),
            None,
        )
        assert upstream_w is not None, (
            f"Expected a board-level (chart=None) warning for the upstream "
            f"truncation, got {render_result.warnings}"
        )
        assert upstream_w.path == "queries.upstream", upstream_w


class TestTruncationWarningSurvivesCacheWarm:
    """The truncation fact must be a property of the cached data, not of how
    it was fetched this render. A second render that warms from the
    persistent cache (a memo miss, persistent-cache hit) must still surface
    WARN-QUERY-RESULT-TRUNCATED — not silently serve the same truncated rows
    with no warning, which is what happens when only a fresh SQL-LIMIT-bound
    fetch records the truncation.
    """

    def _project_and_registry(self, tmp_path: Path) -> tuple[object, object]:
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        project = FilesystemProject(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text("sources:\n  db:\n    type: duckdb\n")
        registry = build_adapter_registry(project)
        return project, registry

    def test_warning_survives_a_persistent_cache_warm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        board_yaml = """\
title: Probe
queries:
  q:
    sql: SELECT x, x AS y FROM range(10) AS t(x)
    source: db
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
rows:
  - c
"""
        result = compile(board_yaml)
        assert result.success and result.board is not None, result.errors
        _, registry = self._project_and_registry(tmp_path)
        cache = TrivialDuckDBCache(db_path=tmp_path / "cache.duckdb")
        try:
            executor1 = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            fresh_render = render(result.board, executor1, format="svg")
            fresh_codes = {w.code for w in fresh_render.warnings}
            assert _CODE in fresh_codes, fresh_codes

            # A brand-new Executor sharing the same persistent cache: the
            # in-memory memo is empty, so this must warm from the persistent
            # entry — not re-run the query — and still carry the warning.
            executor2 = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            assert executor2.is_cached("q")
            warm_render = render(result.board, executor2, format="svg")
            warm_codes = {w.code for w in warm_render.warnings}
            assert _CODE in warm_codes, (
                "a persistent-cache warm hit must still surface the "
                f"truncation warning, got {warm_codes}"
            )
        finally:
            cache.close()

    def test_warm_read_re_slices_under_a_lowered_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache entry written under a loose ceiling must not be served
        verbatim once the deployment ceiling is lowered — the warm-read path
        must route through the same enforcement as a fresh fetch, not just
        pass through whatever truncated_reason the write recorded (which, for
        an entry that was NOT truncated at write time, is None — so a warm
        read that skips re-enforcement would serve the full, untruncated
        rowset under the new, tighter ceiling)."""
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        board_yaml = """\
title: Probe
queries:
  q:
    sql: SELECT x, x AS y FROM range(10) AS t(x)
    source: db
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
rows:
  - c
"""
        result = compile(board_yaml)
        assert result.success and result.board is not None, result.errors
        _, registry = self._project_and_registry(tmp_path)
        cache = TrivialDuckDBCache(db_path=tmp_path / "cache.duckdb")
        try:
            # Write under a loose ceiling: all 10 rows fit, no truncation.
            monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "100")
            executor1 = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            written_rows = executor1.execute_query("q")
            assert len(written_rows) == 10
            assert executor1.truncations() == {}

            # A tighter ceiling takes effect (e.g. a later deployment change).
            # The warm read must re-slice to the new ceiling, not serve the
            # full 10 rows the earlier write cached.
            monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
            executor2 = Executor(
                result.board,
                adapter_registry=registry,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            assert executor2.is_cached("q")
            warm_rows = executor2.execute_query("q")
            assert len(warm_rows) == 3, (
                "warm read must re-enforce the current ceiling, not serve "
                f"the full cached rowset — got {len(warm_rows)} rows"
            )
            assert "q" in executor2.truncations()
        finally:
            cache.close()
