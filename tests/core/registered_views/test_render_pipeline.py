"""Tests for the registered-view render pipeline (core API function).

Covers the extracted render_registered_view function:
- Returns None when no route matches.
- Returns RenderSuccess with HTML when a route matches and renders cleanly.
- Returns RenderError when the source is unknown (registry query fails).
- Links in rendered HTML carry the url_prefix when link_context is supplied.

These tests drive through the real registered-view expansion pipeline using a
DuckDB source. They confirm the same behavior as the serve integration tests
but at the core-API level — no HTTP layer involved.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.project import Project
from dbt_charts.core.registered_views.render_pipeline import (
    RenderError,
    RenderSuccess,
    render_registered_view,
)


@pytest.fixture
def project_with_duckdb(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> Project:
    """Return a minimal project with a DuckDB source."""
    db_path = tmp_path / "wh.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, status VARCHAR)")
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
    )
    (tmp_path / "charts").mkdir()
    return local_project(tmp_path)


def _make_env(project: Project) -> object:
    """Build a real exec env for the given project."""
    from dbt_charts.core.execute.env import build_exec_env

    return build_exec_env(
        project,
        read_only=False,
        allow_external_access_in_readonly=False,
        duckdb_config=None,
        profile_type="duckdb",
        target="dev",
    )


class TestRenderRegisteredViewNoMatch:
    def test_unknown_path_returns_none(self, project_with_duckdb: Project) -> None:
        """A path that matches no registered route returns None."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/no/such/path/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert result is None


class TestRenderRegisteredViewSuccess:
    def test_data_root_returns_render_success(
        self, project_with_duckdb: Project
    ) -> None:
        """GET /data/ with a real source produces a RenderSuccess with HTML."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert result is not None
        assert isinstance(result, RenderSuccess)
        assert len(result.output) > 0
        assert "wh" in result.output

    def test_data_source_returns_render_success(
        self, project_with_duckdb: Project
    ) -> None:
        """GET /data/wh/ with a known source produces a RenderSuccess with HTML."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/wh/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert result is not None
        assert isinstance(result, RenderSuccess)
        assert len(result.output) > 0


class TestRenderRegisteredViewLinkContext:
    def test_root_applied_to_data_links(self, project_with_duckdb: Project) -> None:
        """Links inside a registered-view page carry the root prefix when provided.

        /data/ source links are ``/data/<source>/`` root-relative paths. With a
        Cloud root they must become ``/{org}/{project}/d/data/<source>/``.
        Without a root they must stay root-relative.
        """
        from dbt_charts.core.render.board_links import LinkContext

        env = _make_env(project_with_duckdb)

        # Without root: links are root-relative /data/...
        no_root = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert isinstance(no_root, RenderSuccess)
        assert "/data/wh" in no_root.output
        assert "/myorg/myproject/d/data/" not in no_root.output

        # With Cloud root: links become /{org}/{project}/d/data/...
        with_root = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            link_context=LinkContext(root="/myorg/myproject/d"),
        )
        assert isinstance(with_root, RenderSuccess)
        assert "/myorg/myproject/d/data/wh" in with_root.output
        assert '"/data/wh"' not in with_root.output

    def test_empty_root_leaves_links_root_relative(
        self, project_with_duckdb: Project
    ) -> None:
        """link_context with empty root (dct serve boards-at-root) leaves links unchanged."""
        from dbt_charts.core.render.board_links import LinkContext

        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            link_context=LinkContext(root=""),
        )
        assert isinstance(result, RenderSuccess)
        assert "/data/wh" in result.output


class TestRenderRegisteredViewUnknownSource:
    def test_unknown_source_returns_render_error(
        self, project_with_duckdb: Project
    ) -> None:
        """An unknown source name returns a RenderError with a BoardRenderResult."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/bogus/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert result is not None
        assert isinstance(result, RenderError)
        assert result.dashboard is not None
        assert result.dashboard.status == "failed"


class _StopAtExpand(Exception):
    """Raised by a spied expand_registered_view to short-circuit before compile/execute."""


class TestRenderRegisteredViewSourceTypeIsNotADialect:
    """A source `type` is a connector kind, not a sqlglot dialect. csv/json/
    parquet are file sources and dbt_profile's real dialect lives in the
    profile — sqlglot raises rather than degrading on an unknown dialect
    name, so the resolver must guard and raise a structured error (which
    exits the pipeline as a RenderError), not let a bare ValueError escape.

    Captures the lazy resolver via a route with no pre-template query
    (`/data/<source>/` — same isolation trick as
    TestRenderRegisteredViewResolvesSourceDialect below) so this test
    exercises only the resolver's own guard, not the separate, pre-existing
    dbt_profile/file-source gap in the inspect resolver's schema-query path
    (out of scope here — see the task worksheet's Context section).
    """

    class _StubExecEnv:
        def __init__(self, source_type: str) -> None:
            self.sources = SourceRegistry(
                ProjectSourcesConfig(sources={"bq": {"type": source_type}})
            )
            self.project = None

    @pytest.mark.parametrize("source_type", ["dbt_profile", "csv"])
    def test_non_dialect_source_type_raises_expansion_error_not_value_error(
        self,
        project_with_duckdb: Project,
        monkeypatch: pytest.MonkeyPatch,
        source_type: str,
    ) -> None:
        import dbt_charts.core.registered_views.render_pipeline as render_pipeline_mod
        from dbt_charts.core.registered_views.expander import ExpansionError

        captured: dict[str, object] = {}

        def spy(
            match: object,
            query_results: object,
            resolve_dialect: Callable[[], str | None] = lambda: None,
        ) -> object:
            captured["resolve_dialect"] = resolve_dialect
            raise _StopAtExpand

        monkeypatch.setattr(render_pipeline_mod, "render_registered_view_yaml", spy)

        with pytest.raises(_StopAtExpand):
            render_registered_view(
                request_path="/data/bq/",
                project=project_with_duckdb,
                env=self._StubExecEnv(source_type),
                result_cache=None,
            )
        resolve_dialect = captured["resolve_dialect"]
        assert callable(resolve_dialect)
        with pytest.raises(ExpansionError, match=source_type):
            resolve_dialect()


class TestRenderRegisteredViewResolvesSourceDialect:
    """Regression: a BigQuery-typed source must reach the expander with its own
    dialect, not the ANSI default — the root cause of the /data/ 404 on BigQuery
    projects. Stops the pipeline right after dialect resolution (no real BigQuery
    connection needed) by spying on render_registered_view_yaml and short-circuiting.

    Dialect resolution is lazy (a zero-arg resolver, not an already-resolved
    string, per TestRenderRegisteredViewSourceTypeIsNotADialect above). This
    test calls the captured resolver to confirm it resolves to the matched
    source's dialect when invoked.
    """

    class _StubExecEnv:
        sources = SourceRegistry(
            ProjectSourcesConfig(sources={"bq": {"type": "bigquery"}})
        )
        project = None

    def test_bigquery_source_dialect_reaches_expander(
        self,
        project_with_duckdb: Project,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import dbt_charts.core.registered_views.render_pipeline as render_pipeline_mod

        captured: dict[str, object] = {}

        def spy(
            match: object,
            query_results: object,
            resolve_dialect: Callable[[], str | None] = lambda: None,
        ) -> object:
            captured["resolve_dialect"] = resolve_dialect
            raise _StopAtExpand

        monkeypatch.setattr(render_pipeline_mod, "render_registered_view_yaml", spy)

        with pytest.raises(_StopAtExpand):
            render_registered_view(
                request_path="/data/bq/",
                project=project_with_duckdb,
                env=self._StubExecEnv(),
                result_cache=None,
            )
        resolve_dialect = captured["resolve_dialect"]
        assert callable(resolve_dialect)
        assert resolve_dialect() == "bigquery"


class TestRenderRegisteredViewFragmentFormat:
    """A caller that renders inside its own chrome (Cloud) needs a bare
    fragment, not the CLI's whole standalone document -- ``format``/
    ``controls`` thread straight through to the shared ``render()`` call,
    and ``RenderSuccess.title`` gives the caller the board's title without
    re-parsing it out of the rendered document.
    """

    def test_default_format_is_unchanged_whole_document(
        self, project_with_duckdb: Project
    ) -> None:
        """dct serve's caller passes no format/controls -- must keep getting
        the standalone HTML page it always got (no regression)."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
        )
        assert isinstance(result, RenderSuccess)
        assert result.output.lstrip().startswith("<!DOCTYPE html>")

    def test_svg_format_returns_a_bare_fragment(
        self, project_with_duckdb: Project
    ) -> None:
        """Cloud requests format='svg' to get the same bare-SVG fragment shape
        board_view already puts inside #dashboard-content -- no <html>/<body>."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            format="svg",
            controls=True,
        )
        assert isinstance(result, RenderSuccess)
        assert result.output.lstrip().startswith("<svg")
        assert "<!DOCTYPE" not in result.output
        assert "<html" not in result.output

    def test_title_is_the_compiled_boards_title(
        self, project_with_duckdb: Project
    ) -> None:
        """The board's own title, so a host doesn't have to re-derive it by
        parsing a data-dbt-page-title attribute out of the rendered output."""
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/wh/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            format="svg",
            controls=True,
        )
        assert isinstance(result, RenderSuccess)
        assert result.title != ""
        assert "wh" in result.title

    def test_format_and_controls_reach_render_unchanged(
        self, project_with_duckdb: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both params are plain passthroughs to render() -- pin the kwargs
        actually reaching it, the same way test_request_variables_reach_render
        pins `variables` below. A registered view never emits a `select`
        variable (variable_planner.py only ever assigns text/checkbox/
        datepicker), so `data-dbt-options` presence -- the usual controls
        discriminator (test_variables_chrome.py) -- can't observe this; this
        is the only place that fails if `controls=controls` or `format=format`
        is ever dropped from the render() call in render_pipeline.py.
        """
        import sys

        from dbt_charts.core.render import render as real_render

        render_pkg = sys.modules["dbt_charts.core.render"]
        captured: dict[str, object] = {}

        def spy(board: object, ctx: object, ledger: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return real_render(board, ctx, ledger, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(render_pkg, "render", spy)
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            format="svg",
            controls=True,
        )
        assert result is not None
        assert captured.get("format") == "svg"
        assert captured.get("controls") is True


class TestRenderRegisteredViewPayloadErrorsGate:
    """``render_registered_view``'s success gate must judge the *payload* the
    requested format actually produced, not every chart error render() saw.

    A chart that paints no marks fails the draw but not the yaml format's
    layout-tree walk — Cloud's "Make this a board" clone (which requests
    ``format="yaml"`` purely to get the round-trip dump) must still succeed
    for such a board, while the same board requested as ``format="svg"``
    (whose payload IS the drawing) must still report failure.
    """

    def test_draw_only_failure_still_materializes_yaml_but_fails_svg(
        self, project_with_duckdb: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        from dbt_charts.core.diagnostics import ERR_CHART_PAINTED_NO_MARKS, Diagnostic
        from dbt_charts.core.render.render_result import RenderResult

        render_pkg = sys.modules["dbt_charts.core.render"]
        paint_error = Diagnostic.from_code(
            ERR_CHART_PAINTED_NO_MARKS,
            message="Chart 'c1' received 1 row(s) but painted no marks",
            fields={"chart_id": "c1"},
        )

        def fake_render(
            board: object,
            ctx: object,
            ledger: object,
            format: str = "html",
            **kwargs: object,
        ) -> RenderResult:
            if format == "svg":
                return RenderResult(
                    output="<svg></svg>",
                    chart_errors=[paint_error],
                    payload_errors=[paint_error],
                )
            # A data-bearing format's payload is the layout-tree walk, which
            # never draws — the draw-only failure reaches chart_errors (an
            # agent asking for json must still see it) but not payload_errors.
            return RenderResult(
                output="title: fake\ncharts: {}\n",
                chart_errors=[paint_error],
                payload_errors=[],
            )

        monkeypatch.setattr(render_pkg, "render", fake_render)
        env = _make_env(project_with_duckdb)

        yaml_result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            format="yaml",
        )
        assert isinstance(yaml_result, RenderSuccess), (
            "a draw-only chart error must not fail the yaml 'make this a "
            f"board' clone, got {yaml_result}"
        )

        svg_result = render_registered_view(
            request_path="/data/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            format="svg",
        )
        assert isinstance(svg_result, RenderError)
        assert svg_result.dashboard.chart_errors == [paint_error]


class TestRenderRegisteredViewYamlFormat:
    """``format="yaml"`` dumps a registered view's compiled board to
    re-compilable authored YAML — collapsed into ``render_registered_view``
    itself (no separate ``dump_registered_view_yaml``): ``format`` is already
    a plain passthrough parameter, and ``"yaml"`` is one of the data formats
    ``render()`` short-circuits before any SVG/link/controls work, so a
    second function asking for the identical output was a pure restatement.

    This is the "make this a board" clone: a generated data view has no YAML
    file in git, so the source of the clone is the compiled board itself, not
    a file on disk. The round-trip contract is the only thing that matters —
    the dump must compile back to the same charts and queries the view
    rendered, not merely produce *some* YAML.
    """

    def test_table_view_dumps_yaml_that_recompiles_to_the_same_charts_and_queries(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Dumping /data/<source>/<schema>/<table>/ round-trips through compile().

        The table view (`data/table-index.yaml`) compiles to one `rows` query
        (raw SQL against the table) and one `row_data` table chart. The dump
        must compile back to a board carrying the same chart id, chart type,
        and query name, with the query's rows now inlined as `values:` data
        matching the fixture — not merely *some* rows, per render_board_yaml's
        contract.
        """
        from dbt_charts.core.compile import compile as compile_yaml
        from dbt_charts.core.execute.env import build_exec_env

        db_path = tmp_path / "wh.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE tickets (id INTEGER, status VARCHAR)")
        conn.execute("INSERT INTO tickets VALUES (1, 'open'), (2, 'closed')")
        conn.close()
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
        )
        (tmp_path / "charts").mkdir()
        project = local_project(tmp_path)
        # read_only=True (build_exec_env's own default, and what dct
        # serve/Cloud use for /data/ browsing) — not _make_env's
        # read_only=False: the table route's schema pre-query and its main SQL
        # query both open the same DuckDB file, and DuckDB refuses a second
        # connection to one file with a different read_only setting than an
        # already-open connection.
        env = build_exec_env(
            project,
            read_only=True,
            profile_type="duckdb",
            target="dev",
        )

        result = render_registered_view(
            request_path="/data/wh/main/tickets/",
            project=project,
            env=env,
            result_cache=None,
            format="yaml",
        )

        assert isinstance(result, RenderSuccess)
        recompiled = compile_yaml(result.output)
        assert recompiled.success, recompiled.errors
        board = recompiled.board
        assert board is not None
        assert "row_data" in board.charts
        assert board.charts["row_data"].type == "table"
        query_name = board.charts["row_data"].query_name
        assert query_name in board.queries
        query = board.queries[query_name]
        assert query.rows == [
            {"id": 1, "status": "open"},
            {"id": 2, "status": "closed"},
        ]

    def test_request_variables_filter_the_baked_rows(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """CRITICAL: request_variables must reach the yaml-format render the
        same way they reach every other format (already true of
        render_registered_view, which is why collapsing dump_registered_view_yaml
        into it fixes this for free) -- otherwise a row-detail drill's ?id=
        would freeze on the template's default (unfiltered) rows instead of
        the one the user drilled into."""
        from dbt_charts.core.compile import compile as compile_yaml
        from dbt_charts.core.execute.env import build_exec_env

        db_path = tmp_path / "wh.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE tickets (id INTEGER, status VARCHAR)")
        conn.execute("INSERT INTO tickets VALUES (1, 'open'), (2, 'closed')")
        conn.close()
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  wh:\n    type: duckdb\n    path: '{db_path}'\n"
        )
        (tmp_path / "charts").mkdir()
        project = local_project(tmp_path)
        env = build_exec_env(
            project,
            read_only=True,
            profile_type="duckdb",
            target="dev",
        )

        result = render_registered_view(
            request_path="/data/wh/main/tickets/",
            project=project,
            env=env,
            result_cache=None,
            format="yaml",
            request_variables={"status": "open"},
        )

        assert isinstance(result, RenderSuccess)
        recompiled = compile_yaml(result.output)
        assert recompiled.success, recompiled.errors
        board = recompiled.board
        assert board is not None
        query_name = board.charts["row_data"].query_name
        assert board.queries[query_name].rows == [{"id": 1, "status": "open"}]


class TestRenderRegisteredViewThreadsVariables:
    """Request query params must reach the render as variables.

    Without this, no registered view is interactive — details/expander toggles,
    tabs, and /data row-detail selection are all inert because the URL query
    string never reaches render().
    """

    def test_request_variables_reach_render(
        self, project_with_duckdb: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        from dbt_charts.core.render import render as real_render

        # The pipeline does `from dbt_charts.core.render import render` at call
        # time, resolving the package's re-exported name — patch that.
        render_pkg = sys.modules["dbt_charts.core.render"]

        captured: dict[str, object] = {}

        def spy(board: object, ctx: object, ledger: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return real_render(board, ctx, ledger, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(render_pkg, "render", spy)
        env = _make_env(project_with_duckdb)
        result = render_registered_view(
            request_path="/inspector/wh/main/orders/",
            project=project_with_duckdb,
            env=env,
            result_cache=None,
            request_variables={"_details_probe": "true"},
        )
        assert result is not None
        assert captured.get("variables") == {"_details_probe": "true"}
