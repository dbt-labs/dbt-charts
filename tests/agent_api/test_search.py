"""Tests for dbt_charts.agent_api.search.

Covers:
- Typed Pydantic return shape (SearchResult / BoardSearchHit).
- Result contract (fields, scoring, sort, limits).
- Edge cases (empty/whitespace queries, unknown tags, bad limits, missing dirs).
- Relevance against a small fixture corpus (golden-query ranking + cache).
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api.search import (
    MAX_SEARCH_LIMIT,
    BoardSearchHit,
    ChartSearchEntry,
    SearchBoardsArgs,
    SearchResult,
    paginate,
    search_boards,
    search_boards_hits,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# Fixture corpus — 5 dashboards covering different domains / metadata
# ---------------------------------------------------------------------------

SALES_DASHBOARD = """\
title: Sales Performance
notes: Revenue and order metrics for the sales team
queries:
  revenue:
    sql: SELECT date, SUM(amount) as revenue FROM orders GROUP BY date
    source: warehouse
  orders:
    sql: SELECT date, COUNT(*) as order_count FROM orders GROUP BY date
    source: warehouse
charts:
  revenue_kpi:
    query: revenue
    type: kpi
    value: revenue
  orders_trend:
    query: orders
    type: line
    x: date
    y: order_count
rows:
  - revenue_kpi
  - orders_trend
tags:
  - sales
  - revenue
  - orders
"""

MARKETING_DASHBOARD = """\
title: Marketing Campaign Analytics
notes: Track campaign performance, spend, and conversion rates
queries:
  campaign_spend:
    sql: SELECT campaign, SUM(spend) as total_spend FROM marketing GROUP BY campaign
    source: warehouse
  conversions:
    sql: SELECT campaign, COUNT(*) as conversions FROM events WHERE type='convert' GROUP BY campaign
    source: warehouse
charts:
  spend_bar:
    query: campaign_spend
    type: bar
    x: campaign
    y: total_spend
  conversion_rate:
    query: conversions
    type: kpi
    value: conversions
rows:
  - spend_bar
  - conversion_rate
tags:
  - marketing
  - campaigns
  - conversions
"""

INVENTORY_DASHBOARD = """\
title: Inventory Status
notes: Stock levels, reorder points, and warehouse capacity
queries:
  stock:
    sql: SELECT product, quantity, reorder_point FROM inventory
    source: warehouse
charts:
  stock_table:
    query: stock
    type: table
rows:
  - stock_table
tags:
  - inventory
  - warehouse
  - stock
"""

FINANCE_DASHBOARD = """\
title: Finance Overview
notes: Revenue recognition, margins, and cash flow analysis
queries:
  margins:
    sql: SELECT date, revenue, cost, (revenue - cost) as margin FROM financials
    source: warehouse
  cashflow:
    sql: SELECT month, inflow, outflow FROM cashflow
    source: warehouse
charts:
  margin_trend:
    query: margins
    type: line
    x: date
    y: margin
  cashflow_bar:
    query: cashflow
    type: bar
    x: month
    y: inflow
rows:
  - margin_trend
  - cashflow_bar
tags:
  - finance
  - revenue
  - margins
"""

HR_DASHBOARD = """\
title: HR Headcount Report
notes: Employee headcount, attrition, and department breakdown
queries:
  headcount:
    sql: SELECT department, COUNT(*) as employees FROM staff GROUP BY department
    source: warehouse
charts:
  dept_bar:
    query: headcount
    type: bar
    x: department
    y: employees
rows:
  - dept_bar
tags:
  - hr
  - headcount
  - employees
"""


@pytest.fixture
def corpus_dir(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> Project:
    """5-dashboard corpus across sales, marketing, inventory, finance, and HR."""
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "sales.yml").write_text(SALES_DASHBOARD)
    (boards / "marketing.yml").write_text(MARKETING_DASHBOARD)
    (boards / "inventory.yml").write_text(INVENTORY_DASHBOARD)
    (boards / "finance.yml").write_text(FINANCE_DASHBOARD)

    hr_dir = boards / "hr"
    hr_dir.mkdir()
    (hr_dir / "headcount.yml").write_text(HR_DASHBOARD)

    return local_project(tmp_path)


# ---------------------------------------------------------------------------
# Result contract — input/output shape validation
# ---------------------------------------------------------------------------


class TestSearchIndexesEveryBoard:
    """The index gate and ``list_boards``' gate are one question, asked twice."""

    def test_prose_only_board_is_searchable(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A ``text:``-only board renders, so it has to be findable."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "runbook.yml").write_text(
            "title: Incident Runbook\ntext: |\n  Paging rotation and escalation.\n"
        )

        hits = search_boards_hits("incident", local_project(tmp_path))

        assert [h.title for h in hits] == ["Incident Runbook"]

    def test_extends_only_board_is_searchable(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Composition folds the base's layout in; the child declares no content."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "regional.yml").write_text(
            "extends: sales_base\ntitle: Regional Sales\n"
        )

        hits = search_boards_hits("regional", local_project(tmp_path))

        assert [h.title for h in hits] == ["Regional Sales"]

    def test_meta_cascade_file_is_not_indexed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """``meta.yml`` is directory defaults, not a board someone can open."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "meta.yml").write_text("title: Regional defaults\nextends: cream\n")

        assert search_boards_hits("regional", local_project(tmp_path)) == []

    def test_package_board_is_indexed_with_namespaced_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A pack's boards are discoverable through the same search index.

        board_path carries the extension, like a host board's, but is
        prefixed with the package's namespace (``pkg_a/overview.yml``): a
        bare root-relative path can't round-trip through
        ``render_board(path=...)`` for a package board, since
        ``resolve_board_relpath``'s candidate search only matches a package
        root when the slug is namespace-prefixed (see
        ``TestSearchHitPathCoordinateSystems.test_package_board_path_is_namespaced_and_resolves_through_render_board``
        for the round-trip proof)."""
        (tmp_path / "charts").mkdir()
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(
            "title: Zendesk Overview\ntext: ticket volume\n"
        )

        hits = search_boards_hits("zendesk", local_project(tmp_path))

        assert [h.title for h in hits] == ["Zendesk Overview"]
        assert hits[0].board_path == "pkg_a/overview.yml"
        assert hits[0].file_path == "dbt_packages/pkg_a/charts/overview.yml"


class TestSearchDashboardsContract:
    """Verify the tool's input/output contract."""

    def test_well_formed_revenue_result(self, corpus_dir: Project) -> None:
        """One outcome — a revenue search on the corpus — has many facets:
        typed Pydantic shape, attribute access, populated hit list, every
        documented hit field present with the expected type, default limit
        cap. All asserted on a single search call."""
        result = search_boards(query="revenue", project=corpus_dir)

        assert isinstance(result, SearchResult)
        assert result.success is True
        assert isinstance(result.errors, list)
        assert isinstance(result.results, list)
        assert 0 < len(result.results) <= 10  # default limit cap

        for hit in result.results:
            assert isinstance(hit, BoardSearchHit)
            assert isinstance(hit.title, str) and hit.title
            assert isinstance(hit.summary, str)
            assert isinstance(hit.board_path, str) and hit.board_path
            assert isinstance(hit.file_path, str) and hit.file_path
            assert isinstance(hit.match_score, float) and hit.match_score >= 0
            assert isinstance(hit.match_reasons, list)
            for reason in hit.match_reasons:
                assert isinstance(reason, str)
            assert isinstance(hit.query_names, list)
            assert isinstance(hit.charts, list)
            for chart in hit.charts:
                assert isinstance(chart, ChartSearchEntry)
                assert isinstance(chart.id, str) and chart.id
                assert isinstance(chart.title, str) and chart.title
                assert isinstance(chart.type, str)
                assert chart.query is None or isinstance(chart.query, str)
            assert hit.sample_sql is None or isinstance(hit.sample_sql, str)
            assert isinstance(hit.referenced_data_paths, list)

    def test_empty_query_returns_typed_model(self, corpus_dir: Project) -> None:
        """An empty query is a listing: the same typed result, every board."""
        result = search_boards(query="", project=corpus_dir)
        assert isinstance(result, SearchResult)
        assert result.success
        assert len(result.results) == 5

    def test_model_dump_produces_wire_shape(self, corpus_dir: Project) -> None:
        """model_dump is a distinct operation; assert the wire keys."""
        wire = search_boards(query="revenue", project=corpus_dir).model_dump()
        assert {"success", "errors", "results"} <= set(wire)
        assert isinstance(wire["results"], list)
        if wire["results"]:
            hit = wire["results"][0]
            for key in (
                "title",
                "summary",
                "match_score",
                "match_reasons",
                "board_path",
                "file_path",
                "query_names",
                "charts",
                "matched_charts",
                "sample_sql",
                "referenced_data_paths",
            ):
                assert key in hit
            if hit["charts"]:
                for key in ("id", "title", "type", "query"):
                    assert key in hit["charts"][0]

    def test_extracts_literal_file_paths_from_sql(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        dashboard = boards / "revenue.yml"
        dashboard.write_text(
            """
title: Revenue
queries:
  won_revenue:
    sql: |
      SELECT *
      FROM read_csv('fixtures/data/pied_piper/opportunities.csv')
charts:
  revenue:
    query: won_revenue
    type: table
rows:
  - revenue
"""
        )

        result = search_boards(query="revenue", project=local_project(tmp_path))

        assert result.results[0].referenced_data_paths == [
            "fixtures/data/pied_piper/opportunities.csv"
        ]
        assert "read_csv('fixtures/data/pied_piper/opportunities.csv')" in (
            result.results[0].sample_sql or ""
        )

    def test_custom_limit(self, corpus_dir: Project) -> None:
        """Custom limit is respected."""

        result = search_boards(query="revenue", project=corpus_dir, limit=2)
        assert len(result.results) <= 2

    def test_max_limit_guard(self, corpus_dir: Project) -> None:
        """Limit is capped at a maximum value."""

        result = search_boards(query="revenue", project=corpus_dir, limit=9999)
        assert len(result.results) <= 50

    def test_results_sorted_by_score_descending(self, corpus_dir: Project) -> None:
        """Results are sorted by match_score descending."""

        result = search_boards(query="revenue", project=corpus_dir)
        scores = [h.match_score for h in result.results]
        assert scores == sorted(scores, reverse=True)


class TestSearchDashboardsHits:
    """search_boards_hits is the unbounded ranked list a host with a
    per-principal access model (Cloud) filters before truncating. Local/OSS
    callers never touch it: search_boards's contract (this class) is
    unchanged by its existence."""

    def test_unbounded_hits_exceed_the_public_search_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_SEARCH_LIMIT + 5):
            (boards / f"revenue_{i}.yml").write_text(
                f"title: Revenue {i}\nnotes: revenue metrics\nrows: []\n"
            )
        project = local_project(tmp_path)

        hits = search_boards_hits("revenue", project)
        capped = search_boards(
            query="revenue", project=project, limit=MAX_SEARCH_LIMIT + 5
        )

        assert len(hits) == MAX_SEARCH_LIMIT + 5
        assert len(capped.results) == MAX_SEARCH_LIMIT

    def test_search_boards_is_exactly_hits_truncated_to_limit(
        self, corpus_dir: Project
    ) -> None:
        hits = search_boards_hits("revenue", corpus_dir)
        result = search_boards(query="revenue", project=corpus_dir, limit=2)

        assert result.results == hits[:2]

    def test_empty_query_lists_every_board_in_path_order(
        self, corpus_dir: Project
    ) -> None:
        hits = search_boards_hits("   ", corpus_dir)

        assert [h.file_path for h in hits] == [
            "charts/finance.yml",
            "charts/hr/headcount.yml",
            "charts/inventory.yml",
            "charts/marketing.yml",
            "charts/sales.yml",
        ]
        assert {h.match_score for h in hits} == {0.0}
        assert all(h.match_reasons == [] for h in hits)

    def test_empty_query_listing_honors_tags(self, corpus_dir: Project) -> None:
        hits = search_boards_hits("", corpus_dir, tags=["revenue"])

        assert [h.title for h in hits] == ["Finance Overview", "Sales Performance"]

    def test_punctuation_only_query_still_matches_nothing(
        self, corpus_dir: Project
    ) -> None:
        assert search_boards_hits("!!!", corpus_dir) == []


class TestSearchPaging:
    """A listing larger than ``limit`` pages through ``next`` without gaps."""

    def test_pages_cover_the_listing_exactly_once(self, corpus_dir: Project) -> None:
        full = [h.file_path for h in search_boards("", corpus_dir).results]
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            page = search_boards("", corpus_dir, limit=2, cursor=cursor)
            seen.extend(h.file_path for h in page.results)
            pages += 1
            cursor = page.next
            if cursor is None:
                break

        assert seen == full
        assert pages == 3

    def test_the_last_page_has_no_next(self, corpus_dir: Project) -> None:
        assert search_boards("", corpus_dir, limit=5).next is None
        assert search_boards("", corpus_dir, limit=4).next == "4"

    def test_a_zero_limit_page_has_no_cursor_to_loop_on(self) -> None:
        assert paginate([1, 2, 3], limit=0, cursor=None) == ([], None)

    def test_ranked_search_pages_too(self, corpus_dir: Project) -> None:
        everything = search_boards_hits("revenue", corpus_dir)
        first = search_boards("revenue", corpus_dir, limit=1)
        second = search_boards("revenue", corpus_dir, limit=1, cursor=first.next)

        assert [first.results[0], second.results[0]] == everything[:2]

    @pytest.mark.parametrize("cursor", ["abc", "-1", "1.5", ""])
    def test_a_cursor_that_is_not_a_next_value_is_refused(self, cursor: str) -> None:
        with pytest.raises(ValidationError, match="next"):
            SearchBoardsArgs(query="", cursor=cursor)

    def test_next_is_dropped_from_the_wire_when_absent(
        self, corpus_dir: Project
    ) -> None:
        wire = search_boards("", corpus_dir).model_dump(exclude_none=True)

        assert "next" not in wire


# ---------------------------------------------------------------------------
# Chart-grain tests — per-chart entries, title/id ranking boosts
# ---------------------------------------------------------------------------


class TestChartGrainSearch:
    """Hits carry chart-level detail parsed from the same YAML dict."""

    def test_chart_entries_carry_id_title_type_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "revenue.yml").write_text(
            "title: Revenue Board\n"
            "notes: Revenue metrics\n"
            "queries:\n"
            "  revenue:\n"
            "    sql: SELECT date, SUM(amount) as revenue FROM orders\n"
            "charts:\n"
            "  monthly_revenue:\n"
            "    title: Monthly Revenue\n"
            "    query: revenue\n"
            "    type: line\n"
            "  top_deals:\n"
            "    query: revenue\n"
            "    type: table\n"
            "rows:\n"
            "  - monthly_revenue\n"
            "  - top_deals\n"
        )

        result = search_boards(query="revenue", project=local_project(tmp_path))
        assert len(result.results) == 1
        charts = {c.id: c for c in result.results[0].charts}

        assert charts["monthly_revenue"].title == "Monthly Revenue"
        assert charts["monthly_revenue"].type == "line"
        assert charts["monthly_revenue"].query == "revenue"

        # No explicit title on top_deals -> falls back to the chart id,
        # mirroring the board-title fallback (filename stem) already in
        # _build_index.
        assert charts["top_deals"].title == "top_deals"
        assert charts["top_deals"].type == "table"

    def test_inline_chart_query_does_not_crash_search(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Charts routinely define their query inline (`query: {sql: ...}` —
        the documented quick-start shape). An inline query has no name to
        reference, so the entry's `query` is None — and search must not crash.
        Regression: ChartSearchEntry once received the raw dict and blew up
        the whole search verb with a ValidationError."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "quick.yml").write_text(
            "title: Quick Board\n"
            "notes: Inline query chart\n"
            "charts:\n"
            "  rev:\n"
            "    query:\n"
            "      sql: SELECT month, SUM(revenue) FROM orders GROUP BY 1\n"
            "    type: bar\n"
            "    x: month\n"
            "rows:\n"
            "  - rev\n"
        )

        result = search_boards(query="quick board", project=local_project(tmp_path))

        assert result.success
        assert len(result.results) == 1
        (chart,) = result.results[0].charts
        assert chart.id == "rev"
        assert chart.query is None

    def test_non_string_chart_title_and_type_fall_back(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Search reads raw (possibly invalid) YAML — junk-typed title/type
        must degrade to the id fallback / empty string, never crash."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "junk.yml").write_text(
            "title: Junk Board\n"
            "notes: malformed chart fields\n"
            "charts:\n"
            "  odd:\n"
            "    title: [not, a, string]\n"
            "    type: 7\n"
            "rows:\n"
            "  - odd\n"
        )

        result = search_boards(query="junk board", project=local_project(tmp_path))

        assert result.success
        (chart,) = result.results[0].charts
        assert chart.title == "odd"
        assert chart.type is None

    def test_non_string_chart_key_does_not_crash_search(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """YAML mapping keys need not be strings (`2024:` parses as int) —
        the id is stringified at ingestion, never crashing the search verb."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "yearly.yml").write_text(
            "title: Yearly Board\n"
            "notes: year-keyed charts\n"
            "charts:\n"
            "  2024:\n"
            "    type: bar\n"
            "rows:\n"
            "  - 2024\n"
        )

        result = search_boards(query="yearly board", project=local_project(tmp_path))

        assert result.success
        (chart,) = result.results[0].charts
        assert chart.id == "2024"
        assert chart.title == "2024"

    def test_matched_charts_lists_every_matching_chart_uncapped(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Every matching chart is named, in board order, under one reason."""
        boards = tmp_path / "charts"
        boards.mkdir()
        ids = [f"revenue_{c}" for c in "fedcba"]
        chart_lines = "".join(
            f"  {i}:\n    title: Revenue {i}\n    type: bar\n" for i in ids
        )
        row_lines = "".join(f"  - {i}\n" for i in ids)
        (boards / "many.yml").write_text(
            "title: Many Charts\n"
            "notes: revenue everywhere\n"
            "charts:\n" + chart_lines + "  costs:\n    type: bar\n"
            "rows:\n" + row_lines + "  - costs\n"
        )

        result = search_boards(query="revenue", project=local_project(tmp_path))

        hit = result.results[0]
        assert hit.matched_charts == ids
        assert hit.match_reasons.count("chart_match") == 1

    def test_board_matched_without_chart_match_has_no_matched_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "notes_only.yml").write_text(
            "title: Finance\n"
            "notes: quarterly revenue review\n"
            "charts:\n"
            "  costs:\n"
            "    type: bar\n"
            "rows:\n"
            "  - costs\n"
        )

        result = search_boards(query="revenue", project=local_project(tmp_path))

        hit = result.results[0]
        assert hit.matched_charts == []
        assert "chart_match" not in hit.match_reasons

    def test_zero_charts_board_returns_empty_chart_list(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "text_only.yml").write_text(
            "title: Text Only Board\n"
            "notes: Just a queries mapping, no charts\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "rows: []\n"
        )

        result = search_boards(query="text only", project=local_project(tmp_path))
        assert len(result.results) == 1
        assert result.results[0].charts == []

    def test_chart_entries_are_compact_not_full_yaml_dumps(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Per-chart entries stay id/title/type/query scale — no style/marks/etc."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "styled.yml").write_text(
            "title: Styled Board\n"
            "notes: A board with heavily styled charts\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "charts:\n"
            "  fancy:\n"
            "    title: Fancy Chart\n"
            "    query: q\n"
            "    type: line\n"
            "    style:\n"
            "      palette: [red, blue]\n"
            "      marks:\n"
            "        point:\n"
            "          radius: 4\n"
            "rows:\n"
            "  - fancy\n"
        )

        result = search_boards(query="styled", project=local_project(tmp_path))
        assert len(result.results) == 1
        assert set(ChartSearchEntry.model_fields) == {"id", "title", "type", "query"}

    def test_chart_title_match_boosts_score_and_reason(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A chart-title match ranks its dashboard above a notes-only match
        and names the answering chart in matched_charts."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "revenue_chart.yml").write_text(
            "title: Analytics Board\n"
            "notes: General analytics\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "charts:\n"
            "  monthly_revenue:\n"
            "    title: Monthly Revenue\n"
            "    query: q\n"
            "    type: line\n"
            "rows:\n"
            "  - monthly_revenue\n"
        )
        (boards / "desc_only.yml").write_text(
            "title: Other Board\n"
            "notes: Mentions monthly figures but revenue only once\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "charts:\n"
            "  unrelated:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - unrelated\n"
        )

        result = search_boards(query="monthly revenue", project=local_project(tmp_path))
        titles = [h.title for h in result.results]
        assert titles[0] == "Analytics Board"

        top_hit = result.results[0]
        assert top_hit.matched_charts == ["monthly_revenue"]
        assert "chart_match" in top_hit.match_reasons

    def test_untitled_chart_matches_on_its_id(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """With no title, the chart id itself is what matches."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "topline.yml").write_text(
            "title: Overview\n"
            "notes: General metrics\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "charts:\n"
            "  topline_kpi:\n"
            "    query: q\n"
            "    type: kpi\n"
            "    value: v\n"
            "rows:\n"
            "  - topline_kpi\n"
        )

        result = search_boards(query="topline", project=local_project(tmp_path))
        assert len(result.results) == 1
        assert result.results[0].matched_charts == ["topline_kpi"]


# ---------------------------------------------------------------------------
# Edge case tests — empty/noisy/invalid inputs
# ---------------------------------------------------------------------------


class TestSearchDashboardsEdgeCases:
    """Edge tests for empty query, unknown filter, and limit bounds."""

    def test_whitespace_only_query(self, corpus_dir: Project) -> None:
        """Whitespace-only query is the empty query: a listing."""

        result = search_boards(query="   ", project=corpus_dir)
        assert len(result.results) == 5

    def test_unknown_tag_filter(self, corpus_dir: Project) -> None:
        """Filtering by a nonexistent tag returns empty results."""

        result = search_boards(
            query="revenue", project=corpus_dir, tags=["nonexistent_tag_xyz"]
        )
        assert result.results == []

    @pytest.mark.parametrize("limit", [0, -1], ids=["zero", "negative"])
    def test_nonpositive_limit_returns_empty(
        self, corpus_dir: Project, limit: int
    ) -> None:
        """Zero or negative limit returns empty results."""

        result = search_boards(query="revenue", project=corpus_dir, limit=limit)
        assert result.results == []

    def test_no_matching_dashboards(self, corpus_dir: Project) -> None:
        """Query that matches nothing returns empty results."""

        result = search_boards(query="xyzzyplugh_no_match", project=corpus_dir)
        assert result.results == []


# ---------------------------------------------------------------------------
# Relevance / golden-query tests
# ---------------------------------------------------------------------------


class TestSearchDashboardsRelevance:
    """Golden-query tests for ranking stability and relevance."""

    def test_revenue_query_finds_sales_and_finance(self, corpus_dir: Project) -> None:
        """'revenue' should rank Sales and Finance dashboards highest."""

        result = search_boards(query="revenue", project=corpus_dir)
        titles = [h.title for h in result.results]
        assert "Sales Performance" in titles
        assert "Finance Overview" in titles

    @pytest.mark.parametrize(
        ("query", "expected_top_title"),
        [
            ("campaign", "Marketing Campaign Analytics"),
            ("headcount", "HR Headcount Report"),
            ("stock levels", "Inventory Status"),
            ("margin", "Finance Overview"),
        ],
        ids=["campaign", "headcount", "inventory_stock", "margin"],
    )
    def test_query_ranks_expected_dashboard_first(
        self, corpus_dir: Project, query: str, expected_top_title: str
    ) -> None:
        """Domain-specific keyword should rank its dashboard first."""

        result = search_boards(query=query, project=corpus_dir)
        assert len(result.results) > 0
        assert result.results[0].title == expected_top_title

    def test_tag_filter_narrows_results(self, corpus_dir: Project) -> None:
        """Filtering by tag 'sales' returns only the sales dashboard."""

        result = search_boards(query="revenue", project=corpus_dir, tags=["sales"])
        assert len(result.results) > 0
        for hit in result.results:
            assert hit.title == "Sales Performance"

    def test_deterministic_sort_equal_scores(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Ties in score are broken lexicographically by str(file_path)."""
        # Two dashboards with identical description and tags for query "zephyr"
        # so both score the same (no title match for either).
        # "b_widget.yml" > "a_gadget.yml" lexicographically, so a_gadget first.
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "b_widget.yml").write_text(
            "title: Widget B\nnotes: zephyr wind data\ntags:\n  - zephyr\nrows: []\n"
        )
        (boards / "a_gadget.yml").write_text(
            "title: Gadget A\nnotes: zephyr wind data\ntags:\n  - zephyr\nrows: []\n"
        )
        result = search_boards(query="zephyr", project=local_project(tmp_path))
        assert len(result.results) == 2
        paths = [str(h.file_path) for h in result.results]
        assert paths == sorted(paths), f"Expected lexicographic order, got {paths}"

    def test_title_match_reason_present(self, corpus_dir: Project) -> None:
        """Exact title term match should produce a 'title_match' reason."""

        result = search_boards(query="Sales", project=corpus_dir)
        sales_hit = next(h for h in result.results if h.title == "Sales Performance")
        assert "title_match" in sales_hit.match_reasons

    def test_tag_match_reason_present(self, corpus_dir: Project) -> None:
        """Exact tag match should produce a 'tag_match' reason."""

        result = search_boards(query="inventory", project=corpus_dir)
        inv_hit = next(h for h in result.results if h.title == "Inventory Status")
        assert "tag_match" in inv_hit.match_reasons


class TestSearchSummarySanitization:
    def test_strips_ai_notes_references_from_notes(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        board = boards / "dense.yml"
        board.write_text(
            "title: Dense Board\n"
            "notes: Above-the-fold layout; see ai_notes/dense-board-design.md.\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: table\n"
            "rows:\n  - c\n"
        )
        result = search_boards(query="above", project=local_project(tmp_path))
        assert len(result.results) == 1
        assert "ai_notes" not in result.results[0].summary
        assert result.results[0].summary == "Above-the-fold layout"


class TestSearchDashboardsProjectScope:
    def test_root_only_board_returns_zero_results(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Boards at project root with no charts/ dir are not discovered — search scans charts/ only."""
        # No charts/ dir — boards live directly at project root.
        (tmp_path / "root_dash.yml").write_text(
            "title: Root Dashboard\nnotes: Zendesk root metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )

        result = search_boards(query="zendesk", project=local_project(tmp_path))

        assert result.success is True
        assert len(result.results) == 0

    def test_boards_subdir_excludes_root_level_decoy(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """When charts/ has candidates, only charts/ is searched — root decoys are ignored."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "support.yml").write_text(
            "title: Support Trends\nnotes: Zendesk support metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )
        # Root-level decoy — must NOT appear when charts/ has content.
        (tmp_path / "root_decoy.yml").write_text(
            "title: Root Decoy\nnotes: Zendesk decoy\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )

        result = search_boards(query="zendesk", project=local_project(tmp_path))

        assert result.success is True
        titles = {h.title for h in result.results}
        assert "Support Trends" in titles
        assert "Root Decoy" not in titles

    def test_markdown_only_boards_subdir_yields_zero_results(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """charts/ exists but contains only .md — no YAML boards, so search returns nothing.

        Discovery always scans charts/ only; a root-level YAML is not discovered.
        """
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "README.md").write_text(
            "# Docs\nThis project uses the alias_index layout.\n"
        )
        # Root-level YAML — must NOT be found; discovery scans charts/ only.
        (tmp_path / "root_dash.yml").write_text(
            "title: Root Dashboard\nnotes: Zendesk root metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )

        result = search_boards(query="zendesk", project=local_project(tmp_path))

        assert result.success is True
        assert len(result.results) == 0

    def test_project_search_excludes_markdown_board_candidates(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # .md file with YAML-like content — must NOT appear in search results.
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "notes.md").write_text("charts:\n  c: hi\n")
        (boards / "real.yml").write_text(
            "title: Real Dashboard\nnotes: zendesk metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )

        result = search_boards(query="zendesk", project=local_project(tmp_path))

        assert result.success is True
        assert len(result.results) == 1
        assert result.results[0].title == "Real Dashboard"


class TestSearchHitPathCoordinateSystems:
    """A hit must carry both path forms its consumers need, each usable
    unchanged by its tool — no model-side conversion between coordinate
    systems. Regression for reconcile-board-relative-and-root-relative-paths-
    across-agent-tools: search used to return one field (``source_path``,
    charts/ prefix stripped) that worked for ``render_dashboard`` but broke
    ``read_file``, which resolves relative to the project root.
    """

    def _write_nested_board(self, tmp_path: Path) -> None:
        nested = tmp_path / "charts" / "sub"
        nested.mkdir(parents=True)
        (nested / "x.yaml").write_text(
            "title: Sub Dashboard\n"
            "notes: nested board for path coordinate regression\n"
            "queries:\n  q:\n    columns: [n]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: n\n"
            "rows:\n  - c\n"
        )

    @pytest.mark.windows
    def test_file_path_is_root_relative_and_resolves_through_read_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.files import read_file

        self._write_nested_board(tmp_path)
        project = local_project(tmp_path)

        result = search_boards(query="sub dashboard", project=project)
        assert len(result.results) == 1
        hit = result.results[0]

        assert hit.file_path == "charts/sub/x.yaml"
        read_result = read_file(hit.file_path, project=project)
        assert read_result.success, read_result.error

    @pytest.mark.windows
    def test_board_path_is_board_relative_and_resolves_through_render_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api import ProjectSession
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        self._write_nested_board(tmp_path)
        project = local_project(tmp_path)

        result = search_boards(query="sub dashboard", project=project)
        assert len(result.results) == 1
        hit = result.results[0]

        assert hit.board_path == "sub/x.yaml"
        board = resolve_board_or_error(Path(hit.board_path), project)

        assert not isinstance(board, Diagnostic), board
        rendered = ProjectSession(project=project).render_board(
            board=board, as_link=True, server_port=8000
        )
        assert rendered.status == "ok", rendered.validation_errors

    @pytest.mark.windows
    def test_package_board_path_is_namespaced_and_resolves_through_render_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A package board's board_path is namespace-prefixed (``pkg_a/overview.yml``)
        so it round-trips through render_board(path=...) the same way a host
        board's does — a bare root-relative path (``overview.yml``) does not:
        resolve_board_relpath's candidate search only matches a package root
        when the slug is namespace-prefixed."""
        from dbt_charts.agent_api import ProjectSession
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        (tmp_path / "charts").mkdir()
        pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
        pkg_charts.mkdir(parents=True)
        (pkg_charts / "overview.yml").write_text(
            "title: Zendesk Overview\n"
            "notes: ticket volume\n"
            "queries:\n  q:\n    columns: [n]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: n\n"
            "rows:\n  - c\n"
        )
        project = local_project(tmp_path)

        result = search_boards(query="zendesk", project=project)
        assert len(result.results) == 1
        hit = result.results[0]

        assert hit.board_path == "pkg_a/overview.yml"
        board = resolve_board_or_error(Path(hit.board_path), project)

        assert not isinstance(board, Diagnostic), board
        rendered = ProjectSession(project=project).render_board(
            board=board, as_link=True, server_port=8000
        )
        assert rendered.status == "ok", rendered.validation_errors

    def test_file_path_and_board_path_are_plain_str_not_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Pydantic serializes a ``Path`` field via ``str()``, which emits
        OS-native separators on Windows (``WindowsPath.__str__`` rejoins
        with backslash) — invisible on macOS/Linux, where ``str(PosixPath)``
        is already POSIX, so a round-trip through the model alone can't
        catch the regression on this host. Pin the property that actually
        prevents it instead: the field's runtime type must be ``str``, never
        a ``PurePath`` subclass, on any host.
        """
        self._write_nested_board(tmp_path)
        project = local_project(tmp_path)

        result = search_boards(query="sub dashboard", project=project)
        hit = result.results[0]

        assert isinstance(hit.file_path, str)
        assert isinstance(hit.board_path, str)


class TestSearchBoardsArgsWireSchema:
    """The MCP wire model advertises exactly {query, tags, limit, cursor} — no path inputs."""

    def test_wire_schema_has_expected_properties(self) -> None:
        schema = SearchBoardsArgs.model_json_schema()
        assert set(schema["properties"].keys()) == {
            "query",
            "tags",
            "limit",
            "cursor",
        }
        assert schema["required"] == ["query"]

    def test_project_dir_is_not_in_wire_schema(self) -> None:
        schema = SearchBoardsArgs.model_json_schema()
        assert "project_dir" not in schema["properties"]

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchBoardsArgs(query="x", directory="some/path")  # type: ignore[call-arg]
