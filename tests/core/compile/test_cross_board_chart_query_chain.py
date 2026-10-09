"""Cross-board chart import must bring its own query chain with it.

A board importing a chart via the dotted `charts.<name>` ref grammar should
not have to manually re-declare every query the chart (and its layers)
depend on, transitively, under the source board's private internal names.

The core design point, pinned explicitly below: queries are scoped
*lexically* to the source board (resolved against its own `queries:` section,
anchored at its own directory) while variables stay board-global (resolved
against the *importing* board's declared variables at execute time). That
asymmetry is the entire feature — see `compiler.py` for the design rationale.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.executor import Executor


def _project_with_db_source(
    root: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    """A Project rooted at `root` whose sources registry resolves 'db' to
    an in-memory DuckDB — for the executor-based tests below."""
    project = local_project(root=root)
    vars(project)["sources"] = ProjectSourcesConfig(
        sources={"db": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


class TestCrossBoardChartQueryChain:
    def test_imported_chart_pulls_its_multi_hop_query_chain(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Importing a chart with no matching local `queries:` block still
        compiles: the chart's 2-hop `{{ queries.X }}` chain resolves entirely
        against the source board, with zero manual re-declaration."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
queries:
  mqls_by_week_base:
    sql: |
      SELECT '2026-01-01' AS week, 10 AS mqls
    source: db
  mqls_by_week:
    sql: |
      SELECT week, SUM(mqls) AS mqls
      FROM {{ queries.mqls_by_week_base }}
      GROUP BY week
    source: db
charts:
  mqls_by_week:
    type: bar
    x: week
    y: mqls
    query: mqls_by_week
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        result = compile(main_yaml, base_dir=local_project(root=tmp_path).directory())

        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None
        assert "mqls_by_week" in result.board.charts
        # No local queries: block was declared at all — the chain resolved
        # purely from the source board.
        chart = result.board.charts["mqls_by_week"]
        assert chart.query_name is not None
        assert chart.query_name in result.query_registry

    def test_imported_variable_override_recuts_the_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The asymmetry: queries scope to the source board, variables do not.
        The importing board's variable value re-cuts the pulled-in query."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
variables:
  parent_region_name:
    input: text
    default: APAC
queries:
  mqls_by_week:
    sql: |
      SELECT * FROM (
        SELECT 'APAC' AS parent_region_name, 10 AS mqls
        UNION ALL
        SELECT 'EMEA' AS parent_region_name, 20 AS mqls
      ) t
      WHERE {{ filter('parent_region_name', parent_region_name) }}
    source: db
charts:
  mqls_by_week:
    type: bar
    x: parent_region_name
    y: mqls
    query: mqls_by_week
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
source: db
variables:
  parent_region_name:
    input: text
    default: EMEA
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )

        apac_rows = executor.execute_chart(
            "mqls_by_week", {"parent_region_name": "APAC"}
        )
        emea_rows = executor.execute_chart(
            "mqls_by_week", {"parent_region_name": "EMEA"}
        )

        assert apac_rows == [{"parent_region_name": "APAC", "mqls": 10}]
        assert emea_rows == [{"parent_region_name": "EMEA", "mqls": 20}]

    def test_importer_missing_variable_raises_compile_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """validate_variable_references already walks every query in
        board.queries and raises for undeclared variable refs. This is
        load-bearing for the feature (it's the only thing stopping a query
        that silently no-ops its filter), so it gets its own regression test
        even though no new code implements it."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
variables:
  parent_region_name:
    input: text
    default: APAC
queries:
  mqls_by_week:
    sql: |
      SELECT 'APAC' AS parent_region_name, 10 AS mqls
      WHERE {{ filter('parent_region_name', parent_region_name) }}
    source: db
charts:
  mqls_by_week:
    type: bar
    x: parent_region_name
    y: mqls
    query: mqls_by_week
rows:
  - mqls_by_week
"""
        )
        # Importer does NOT declare parent_region_name at all.
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        result = compile(main_yaml, base_dir=local_project(root=tmp_path).directory())

        assert not result.success
        messages = " ".join(str(e) for e in result.errors)
        assert "parent_region_name" in messages
        assert "mqls_by_week" in messages

    def test_importer_missing_variable_on_layer_query_raises_compile_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Same load-bearing free-compile-error property as the test above,
        but for a variable referenced only by a *layer's* query — layers
        pull their query chain into board.queries exactly like the chart's
        own top-level query does."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
variables:
  parent_region_name:
    input: text
    default: APAC
queries:
  mqls_by_week:
    sql: SELECT 'APAC' AS parent_region_name, 10 AS mqls
    source: db
  mqls_target:
    sql: |
      SELECT 'APAC' AS parent_region_name, 12 AS mqls
      WHERE {{ filter('parent_region_name', parent_region_name) }}
    source: db
charts:
  mqls_by_week:
    type: bar
    x: parent_region_name
    y: mqls
    query: mqls_by_week
    layers:
      - type: line
        query: mqls_target
rows:
  - mqls_by_week
"""
        )
        # Importer does NOT declare parent_region_name at all.
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        result = compile(main_yaml, base_dir=local_project(root=tmp_path).directory())

        assert not result.success
        messages = " ".join(str(e) for e in result.errors)
        assert "parent_region_name" in messages
        assert "mqls_target" in messages

    def test_importer_own_unrelated_query_same_bare_name_no_interaction(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The importer can declare its own, completely unrelated query under
        the exact bare name the source chain uses internally — lexical
        scoping means the two never touch."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
queries:
  mqls_by_week_contacts:
    sql: SELECT 999 AS value
    source: db
charts:
  mqls_by_week:
    type: kpi
    value: value
    query: mqls_by_week_contacts
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
source: db
queries:
  mqls_by_week_contacts:
    sql: SELECT 1 AS value
    source: db
charts:
  local_chart:
    type: kpi
    value: value
    query: mqls_by_week_contacts
  imported_chart: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - local_chart
  - imported_chart
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )

        local_rows = executor.execute_chart("local_chart")
        imported_rows = executor.execute_chart("imported_chart")

        assert local_rows == [{"value": 1}]
        assert imported_rows == [{"value": 999}]

    def test_source_query_that_is_itself_a_cross_file_ref_resolves_relative_to_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A query declared inside the source board, which is itself a
        cross-file ref, must resolve relative to the *source* board's own
        directory — not the importer's. Nested subdirectories mean an
        importer-relative resolution would 404."""
        shared = tmp_path / "sales" / "shared" / "base_queries.yaml"
        shared.parent.mkdir(parents=True)
        shared.write_text(
            """
queries:
  raw_mqls:
    sql: SELECT 42 AS mqls
    source: db
"""
        )
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.write_text(
            """
title: Weekly funnel
queries:
  mqls_by_week: shared/base_queries.yaml.queries.raw_mqls
charts:
  mqls_by_week:
    type: kpi
    value: mqls
    query: mqls_by_week
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        rows = executor.execute_chart("mqls_by_week")
        assert rows == [{"mqls": 42}]

    def test_source_hash_external_query_resolves_relative_to_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A `#`-external query ref written inside the source board resolves
        against the *source* board's own directory, like every other hop in the
        chain. Anchoring it at the importer's directory 404s here."""
        shared = tmp_path / "sales" / "_shared.yml"
        shared.parent.mkdir(parents=True)
        shared.write_text(
            """
queries:
  raw_mqls:
    sql: SELECT 42 AS mqls
    source: db
"""
        )
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.write_text(
            """
title: Weekly funnel
charts:
  mqls_by_week:
    type: kpi
    value: mqls
    query: _shared.yml#raw_mqls
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        assert executor.execute_chart("mqls_by_week") == [{"mqls": 42}]

    def test_importer_hash_external_query_same_spelling_no_interaction(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The importer's own `#`-ref keeps resolving against the importer's
        directory, even when it is spelled identically to the imported chart's
        — two different files, two different queries, no cross-talk."""
        (tmp_path / "_shared.yml").write_text(
            """
queries:
  raw_mqls:
    sql: SELECT 1 AS mqls
    source: db
"""
        )
        sales = tmp_path / "sales"
        sales.mkdir()
        (sales / "_shared.yml").write_text(
            """
queries:
  raw_mqls:
    sql: SELECT 999 AS mqls
    source: db
"""
        )
        (sales / "weekly_funnel.yaml").write_text(
            """
title: Weekly funnel
charts:
  mqls_by_week:
    type: kpi
    value: mqls
    query: _shared.yml#raw_mqls
"""
        )
        main_yaml = """
title: Slide
source: db
charts:
  local_chart:
    type: kpi
    value: mqls
    query: _shared.yml#raw_mqls
  imported_chart: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - local_chart
  - imported_chart
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        assert executor.execute_chart("local_chart") == [{"mqls": 1}]
        assert executor.execute_chart("imported_chart") == [{"mqls": 999}]

    def test_source_hash_external_query_on_a_layer_resolves_relative_to_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`layers[].query` takes the same source-anchored `#` resolution as
        the chart's own `query:`."""
        sales = tmp_path / "sales"
        sales.mkdir()
        (sales / "_shared.yml").write_text(
            """
queries:
  raw_mqls:
    sql: SELECT '2026-01-01' AS week, 10 AS mqls
    source: db
  target_mqls:
    sql: SELECT '2026-01-01' AS week, 12 AS mqls
    source: db
"""
        )
        (sales / "weekly_funnel.yaml").write_text(
            """
title: Weekly funnel
charts:
  mqls_by_week:
    type: bar
    x: week
    y: mqls
    query: _shared.yml#raw_mqls
    layers:
      - type: line
        query: _shared.yml#target_mqls
"""
        )
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        assert executor.execute_chart("mqls_by_week") == [
            {"week": "2026-01-01", "mqls": 10}
        ]

    def test_unresolvable_source_query_file_names_the_chart_ref_and_anchor(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Every way the source board's query file can fail to yield a
        `queries:` section names the chart import, the authored ref, and the
        directory it anchored at — the author is looking at neither file when
        the resolution breaks, so a bare filename is not enough to act on."""
        sales = tmp_path / "sales"
        sales.mkdir()
        (sales / "not_a_board.yml").write_text("title: no queries here\n")
        (sales / "broken.yml").write_text("queries: [unclosed\n")
        (sales / "a_list.yml").write_text("- a\n- b\n")
        (sales / "weekly_funnel.yaml").write_text(
            """
title: Weekly funnel
charts:
  gone:
    type: kpi
    value: mqls
    query: missing.yml#raw_mqls
  sectionless:
    type: kpi
    value: mqls
    query: not_a_board.yml#raw_mqls
  unparseable:
    type: kpi
    value: mqls
    query: broken.yml#raw_mqls
  not_a_mapping:
    type: kpi
    value: mqls
    query: a_list.yml#raw_mqls
"""
        )
        project = _project_with_db_source(tmp_path, local_project)

        for chart_name, expected in (
            ("sectionless", "'sales/not_a_board.yml' — that file has no queries:"),
            ("unparseable", "failed to load sales/broken.yml"),
            # A list/scalar top level parses fine and only fails on the
            # `queries:` lookup — as an AttributeError out of compile(),
            # not a diagnostic, unless the shape is checked at the read.
            ("not_a_mapping", "'sales/a_list.yml' — not a YAML mapping"),
        ):
            result = compile(
                f"title: Slide\ncharts:\n"
                f"  c: sales/weekly_funnel.yaml.charts.{chart_name}\n"
                f"rows:\n  - c\n",
                base_dir=project.directory(),
            )
            assert not result.success
            message = " ".join(str(e) for e in result.errors)
            assert expected in message, message
            # The chart import that pulled it in, and the anchor it resolved
            # against — neither is visible from the failing file itself.
            assert f"sales/weekly_funnel.yaml.charts.{chart_name}" in message, message

        # A file the chain cannot resolve is the one resolver's error: it names
        # the ref and its anchor, and is placed on the chart import's own line.
        result = compile(
            "title: Slide\ncharts:\n  c: sales/weekly_funnel.yaml.charts.gone\n"
            "rows:\n  - c\n",
            base_dir=project.directory(),
        )
        (error,) = result.errors
        assert error.code == "ERR-UNRESOLVED-REFERENCE"
        assert "'sales/missing.yml' does not exist" in error.message
        assert error.path == "charts.c"

    def test_board_level_source_inherited_by_cross_board_lexical_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A source board's board-level `source:` default must be inherited by
        its queries when they are pulled in via a cross-board chart import.
        No per-query `source:` is declared — the source comes only from the
        source file's top-level `source:` key (lexical scoping)."""
        source = tmp_path / "sales" / "weekly_funnel.yaml"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
title: Weekly funnel
source: db
queries:
  mqls_by_week:
    sql: SELECT '2026-01-01' AS week, 10 AS mqls
charts:
  mqls_by_week:
    type: bar
    x: week
    y: mqls
    query: mqls_by_week
rows:
  - mqls_by_week
"""
        )
        main_yaml = """
title: Slide
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        rows = executor.execute_chart("mqls_by_week")
        assert rows == [{"week": "2026-01-01", "mqls": 10}]

    def test_board_level_source_inherited_by_cross_file_query_ref(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A source file's board-level `source:` default must be inherited by
        its queries when they are pulled in via a plain QueryRef. No per-query
        `source:` is declared — the source comes only from the source file's
        top-level `source:` key."""
        shared = tmp_path / "shared" / "metrics.yaml"
        shared.parent.mkdir(parents=True)
        shared.write_text(
            """
source: db
queries:
  revenue:
    sql: SELECT 42 AS revenue
"""
        )
        main_yaml = """
title: Report
queries:
  revenue: shared/metrics.yaml.queries.revenue
charts:
  revenue:
    type: kpi
    value: revenue
    query: revenue
rows:
  - revenue
"""
        project = _project_with_db_source(tmp_path, local_project)
        result = compile(main_yaml, base_dir=project.directory())
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        rows = executor.execute_chart("revenue")
        assert rows == [{"revenue": 42}]

    @pytest.mark.windows
    def test_upward_relative_chart_ref_pulls_its_query_chain(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The two features compose: a chart imported via a `../` sibling-
        directory ref (Allow upward-relative paths in cross-board references)
        still gets its query chain pulled in lexically, with zero manual
        re-declaration — a `charts/gtm_weekly/` slide board importing a chart
        straight from `charts/sales/`."""
        sales_dir = tmp_path / "charts" / "sales"
        sales_dir.mkdir(parents=True)
        (sales_dir / "weekly_funnel.yaml").write_text(
            """
title: Weekly funnel
queries:
  mqls_by_week_base:
    sql: SELECT '2026-01-01' AS week, 10 AS mqls
    source: db
  mqls_by_week:
    sql: |
      SELECT week, SUM(mqls) AS mqls
      FROM {{ queries.mqls_by_week_base }}
      GROUP BY week
    source: db
charts:
  mqls_by_week:
    type: bar
    x: week
    y: mqls
    query: mqls_by_week
"""
        )
        main_yaml = """
title: Slide
source: db
charts:
  mqls_by_week: ../sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
        project = _project_with_db_source(tmp_path, local_project)
        base_dir = project.directory("charts/gtm_weekly")
        result = compile(main_yaml, base_dir=base_dir)
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None

        registry = build_adapter_registry(project)
        executor = Executor(
            result.board,
            registry,
            query_registry=result.query_registry,
            use_cache=False,
        )
        rows = executor.execute_chart("mqls_by_week")
        assert rows == [{"week": "2026-01-01", "mqls": 10}]


def test_raw_ref_chain_splits_a_dotted_target_name_on_its_section(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A source board's raw `{ref: b.queries.rev.v2}` is file `b`, query `rev.v2`.

    This raw chain skips the ref models' name grammar, so the file part is
    what precedes `.queries.` — not everything before the last two dots,
    which names `b.queries.yml` (planted here as a decoy).
    """
    (tmp_path / "dbt_charts.yml").write_text(
        'sources:\n  db:\n    type: duckdb\n    path: ":memory:"\n'
    )
    (tmp_path / "a.yml").write_text(
        "title: A\nqueries:\n  q:\n    ref: b.queries.rev.v2\n"
        "charts:\n  c:\n    type: kpi\n    value: x\n    query: q\n"
    )
    (tmp_path / "b.yml").write_text(
        "title: B\nqueries:\n  rev.v2:\n    sql: select 1 as x\n    source: db\n"
    )
    (tmp_path / "b.queries.yml").write_text(
        "title: D\nqueries:\n  v2:\n    sql: select 99 as x\n    source: db\n"
    )
    project = local_project(tmp_path)
    result = compile(
        "title: M\ncharts:\n  c: a.yml.charts.c\nrows:\n  - c\n",
        base_dir=project.directory(),
        project_sources=project.sources,
    )
    assert result.success, result.errors
    assert [q.sql for q in result.board.queries.values()] == ["select 1 as x"]


def test_imported_chart_from_a_retired_grammar_board_is_migrated(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The source board compiles on its own because it is migrated in memory;
    a chart imported from it sees the same migrated grammar, and every compile
    says which file needs `dct migrate`."""
    source = tmp_path / "sales" / "weekly_funnel.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
title: Weekly funnel
queries:
  mqls_by_week:
    sql: |
      SELECT '2026-01-01' AS week, 10 AS mqls
    source: db
charts:
  mqls_by_week:
    type: bar
    x: week
    y: mqls
    query: mqls_by_week
    style:
      legend:
        visible: true
        position: bottom
rows:
  - mqls_by_week
"""
    )
    project = local_project(root=tmp_path)
    main_yaml = """
title: Slide
charts:
  mqls_by_week: sales/weekly_funnel.yaml.charts.mqls_by_week
rows:
  - mqls_by_week
"""
    for _ in range(2):
        result = compile(main_yaml, base_dir=project.directory())

        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None
        legend = result.board.charts["mqls_by_week"].style.legend
        assert legend.position.edge == "bottom"
        assert any(
            w.code == "WARN-SCHEMA-MIGRATED"
            and w.message.startswith("sales/weekly_funnel.yaml:")
            for w in result.warnings
        ), [w.message for w in result.warnings]
