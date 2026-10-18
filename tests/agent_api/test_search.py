"""Tests for dbt_charts.agent_api.search.

Covers:
- Typed Pydantic return shape (SearchResult / BoardSearchHit).
- Result contract (fields, scoring, sort, limits).
- Edge cases (empty/whitespace queries, unknown tags, bad limits, missing dirs).
- Relevance against a small fixture corpus (golden-query ranking + cache).
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from dbt_charts.agent_api.search import (
    DEFAULT_SEARCH_LIMIT,
    MAX_PARSED_CANDIDATES,
    MAX_SEARCH_LIMIT,
    MAX_SUMMARY_CHARS,
    BoardListing,
    BoardMatch,
    FileEntry,
    FileHit,
    SearchFilesArgs,
    SearchResult,
    paginate,
    search_boards_hits,
    search_files,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics import Diagnostic
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


class BoardSearch(BaseModel):
    """A board search page: the shape ``search_files`` returns for ``kind="boards"``."""

    success: bool
    errors: list[Diagnostic]
    results: list[BoardMatch | BoardListing]
    next: str | None = None


def board_search(
    query: str,
    project: Project,
    *,
    tags: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    cursor: str | None = None,
) -> BoardSearch:
    page = search_files(project, text=query, tags=tags, limit=limit, cursor=cursor)
    return BoardSearch.model_validate(page.model_dump())


def _matches(
    hits: Sequence[BoardMatch | BoardListing | FileHit | FileEntry],
) -> list[BoardMatch]:
    """The hits of a keyword search, each a ranked match rather than a listing entry."""
    matches = [hit for hit in hits if isinstance(hit, BoardMatch)]
    assert len(matches) == len(hits)
    return matches


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
        result = search_files(corpus_dir, text="revenue")

        assert isinstance(result, SearchResult)
        assert result.success is True
        assert isinstance(result.errors, list)
        assert 0 < len(result.results) <= DEFAULT_SEARCH_LIMIT

        for hit in _matches(result.results):
            assert isinstance(hit.title, str) and hit.title
            assert isinstance(hit.summary, str)
            assert isinstance(hit.board_path, str) and hit.board_path
            assert isinstance(hit.file_path, str) and hit.file_path
            assert isinstance(hit.match_score, float) and hit.match_score > 0
            assert hit.match_reasons
            assert all(isinstance(reason, str) for reason in hit.match_reasons)
            assert all(isinstance(chart_id, str) for chart_id in hit.matched_charts)

    def test_empty_query_returns_typed_model(self, corpus_dir: Project) -> None:
        """An empty query is a listing: the same typed result, every board."""
        result = search_files(corpus_dir)
        assert isinstance(result, SearchResult)
        assert result.success
        assert len(result.results) == 5

    def test_listing_hit_wire_shape_is_title_notes_and_paths(
        self, corpus_dir: Project
    ) -> None:
        wire = board_search(query="", project=corpus_dir).model_dump()

        assert {frozenset(hit) for hit in wire["results"]} == {
            frozenset({"title", "summary", "board_path", "file_path"})
        }

    def test_keyword_hit_wire_shape_adds_score_reasons_and_matched_charts(
        self, corpus_dir: Project
    ) -> None:
        wire = board_search(query="revenue", project=corpus_dir).model_dump()

        assert wire["results"]
        assert {frozenset(hit) for hit in wire["results"]} == {
            frozenset(
                {
                    "title",
                    "summary",
                    "board_path",
                    "file_path",
                    "match_score",
                    "match_reasons",
                    "matched_charts",
                }
            )
        }

    def test_custom_limit(self, corpus_dir: Project) -> None:
        """Custom limit is respected."""

        result = board_search(query="revenue", project=corpus_dir, limit=2)
        assert len(result.results) <= 2

    def test_max_limit_guard(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Limit is capped at a maximum value."""
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_SEARCH_LIMIT + 5):
            (boards / f"board_{i}.yml").write_text(f"title: Board {i}\nrows: []\n")

        result = board_search(
            query="", project=local_project(tmp_path), limit=MAX_SEARCH_LIMIT * 10
        )

        assert len(result.results) == MAX_SEARCH_LIMIT
        assert result.next == str(MAX_SEARCH_LIMIT)

    def test_results_sorted_by_score_descending(self, corpus_dir: Project) -> None:
        """Results are sorted by match_score descending."""

        result = board_search(query="revenue", project=corpus_dir)
        scores = [h.match_score for h in _matches(result.results)]
        assert scores == sorted(scores, reverse=True)

    def test_search_is_case_insensitive_per_query_word(
        self, corpus_dir: Project
    ) -> None:
        """The candidate pass matches text however the board spells it."""
        lower = search_boards_hits("sales performance", corpus_dir)
        mixed = search_boards_hits("SaLeS pErFoRmAnCe", corpus_dir)

        assert [h.file_path for h in mixed] == [h.file_path for h in lower]
        assert lower

    def test_long_notes_are_cut_to_a_short_summary(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "wordy.yml").write_text(
            f"title: Wordy\nnotes: {'word ' * 200}\nrows: []\n"
        )

        (hit,) = search_boards_hits("", local_project(tmp_path))

        assert len(hit.summary) == MAX_SUMMARY_CHARS
        assert hit.summary.endswith("…")


class TestSearchFindsEveryFieldItScores:
    """Candidates come from a text pass, so a field is findable only if its
    text reaches the pass: tags, chart ids, query names and SQL all do."""

    @pytest.mark.parametrize(
        ("query", "reason"),
        [
            ("zebratag", "tag_match"),
            ("giraffe_chart", "chart_match"),
            ("okapi_query", "metric_overlap"),
            ("warthog_table", "sql_match"),
        ],
    )
    def test_a_board_is_found_by_a_word_only_that_field_holds(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        query: str,
        reason: str,
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "plain.yml").write_text(
            "title: Plain\n"
            "tags: [zebratag]\n"
            "queries:\n"
            "  okapi_query:\n"
            "    sql: SELECT n FROM warthog_table\n"
            "charts:\n"
            "  giraffe_chart:\n"
            "    query: okapi_query\n"
            "    type: kpi\n"
            "    value: n\n"
            "rows:\n"
            "  - giraffe_chart\n"
        )
        (boards / "other.yml").write_text("title: Other\nrows: []\n")

        (hit,) = _matches(search_boards_hits(query, local_project(tmp_path)))

        assert hit.title == "Plain"
        assert reason in hit.match_reasons

    def test_a_board_is_found_by_a_word_only_its_filename_holds(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "quarterly_pipeline.yml").write_text("rows: []\n")

        hits = search_boards_hits("pipeline", local_project(tmp_path))

        # Found as a candidate; an untitled board's title is its slug, which
        # is what scores.
        assert [h.file_path for h in hits] == ["charts/quarterly_pipeline.yml"]


class TestSearchParsesOnlyCandidates:
    def test_a_query_names_no_more_boards_than_the_candidate_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_PARSED_CANDIDATES + 5):
            (boards / f"revenue_{i:03d}.yml").write_text(
                f"title: Revenue {i}\nnotes: revenue metrics\nrows: []\n"
            )

        hits = search_boards_hits("revenue", local_project(tmp_path))

        assert len(hits) == MAX_PARSED_CANDIDATES

    def test_boards_that_mention_more_query_words_are_kept_over_the_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_PARSED_CANDIDATES + 5):
            (boards / f"a_{i:03d}.yml").write_text(
                f"title: Alpha {i}\nnotes: alpha only\nrows: []\n"
            )
        (boards / "z_both.yml").write_text(
            "title: Alpha Beta\nnotes: alpha beta\nrows: []\n"
        )

        hits = search_boards_hits("alpha beta", local_project(tmp_path))

        assert "charts/z_both.yml" in [h.file_path for h in hits]

    def test_a_board_that_cannot_be_read_as_text_is_skipped(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "binary_revenue.yml").write_bytes(b"\xff\xfe\x00 revenue")
        (boards / "good.yml").write_text("title: Revenue\nnotes: revenue\nrows: []\n")

        hits = search_boards_hits("revenue", local_project(tmp_path))

        assert [h.title for h in hits] == ["Revenue"]

    def test_visibility_narrows_candidates_before_the_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A caller who may see only a few boards still finds them when
        hidden boards would otherwise fill the candidate cap."""
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_PARSED_CANDIDATES + 5):
            (boards / f"hidden_{i:03d}.yml").write_text(
                f"title: Revenue Revenue {i}\nnotes: revenue revenue\nrows: []\n"
            )
        (boards / "shown.yml").write_text("title: Shown\nnotes: revenue\nrows: []\n")

        hits = search_boards_hits(
            "revenue",
            local_project(tmp_path),
            visible=lambda paths: [p for p in paths if p == "charts/shown.yml"],
        )

        assert [h.file_path for h in hits] == ["charts/shown.yml"]

    def test_visibility_narrows_a_listing(self, corpus_dir: Project) -> None:
        hits = search_boards_hits(
            "", corpus_dir, visible=lambda paths: [p for p in paths if "hr/" in p]
        )

        assert [h.file_path for h in hits] == ["charts/hr/headcount.yml"]


class TestCandidateCapKeepsWhatMatters:
    def test_a_titled_board_outranks_boards_that_only_repeat_the_word_in_sql(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_PARSED_CANDIDATES + 10):
            (boards / f"sales_{i:03d}.yml").write_text(
                f"title: Sales {i}\n"
                "queries:\n"
                "  q:\n"
                "    sql: |\n"
                "      SELECT revenue FROM a\n"
                "      UNION ALL SELECT revenue FROM b\n"
                "      UNION ALL SELECT revenue FROM c\n"
                "rows: []\n"
            )
        (boards / "z_overview.yml").write_text("title: Revenue\nrows: []\n")

        hits = search_boards_hits("revenue", local_project(tmp_path))

        assert hits[0].file_path == "charts/z_overview.yml"

    def test_tags_narrow_keyword_candidates_before_the_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Untagged boards that outrank the tagged one must not fill the cap."""
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_PARSED_CANDIDATES + 10):
            (boards / f"a_{i:03d}.yml").write_text(
                f"title: Revenue {i}\nnotes: revenue\nrows: []\n"
            )
        (boards / "z_tagged.yml").write_text(
            "title: Plain\nnotes: revenue\ntags: [finance]\nrows: []\n"
        )

        hits = search_boards_hits("revenue", local_project(tmp_path), tags=["finance"])

        assert [h.file_path for h in hits] == ["charts/z_tagged.yml"]

    def test_a_tags_only_listing_returns_every_tagged_board(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        count = MAX_PARSED_CANDIDATES + 11
        for i in range(count):
            (boards / f"b_{i:03d}.yml").write_text(
                f"title: Board {i}\ntags: [finance]\nrows: []\n"
            )
        project = local_project(tmp_path)

        hits = search_boards_hits("", project, tags=["finance"])
        first = board_search("", project, tags=["finance"], limit=50)
        second = board_search(
            "", project, tags=["finance"], limit=50, cursor=first.next
        )

        assert len(hits) == count
        assert len(first.results) + len(second.results) == count
        assert second.next is None

    @pytest.mark.parametrize("requested", ["Ärger", "ärger"])
    def test_a_tag_with_a_non_ascii_capital_matches_its_own_board(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        requested: str,
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "angry.yml").write_text("title: Angry\ntags: [Ärger]\nrows: []\n")
        (boards / "calm.yml").write_text("title: Calm\ntags: [calm]\nrows: []\n")

        hits = search_boards_hits("", local_project(tmp_path), tags=[requested])

        assert [h.title for h in hits] == ["Angry"]


class TestMarkdownBoardsAreSearchable:
    def test_a_markdown_board_is_listed_and_found(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "weekly.md").write_text(
            "---\n"
            "board:\n"
            "  title: Weekly Review\n"
            "  notes: pipeline health\n"
            "  tags: [ops]\n"
            "  queries:\n"
            "    q:\n"
            "      columns: [n]\n"
            "      values:\n"
            "        - [1]\n"
            "  charts:\n"
            "    pipe:\n"
            "      query: q\n"
            "      type: kpi\n"
            "      value: n\n"
            "---\n"
            "Intro prose.\n\n"
            "{{ chart pipe }}\n"
        )
        project = local_project(tmp_path)

        (listed,) = search_boards_hits("", project)
        (found,) = _matches(search_boards_hits("pipeline", project))

        assert (listed.title, listed.board_path) == ("Weekly Review", "weekly.md")
        assert found.file_path == "charts/weekly.md"
        assert "notes_match" in found.match_reasons
        assert [h.file_path for h in search_boards_hits("", project, tags=["ops"])] == [
            "charts/weekly.md"
        ]

    def test_an_untitled_markdown_board_is_titled_by_its_first_heading(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "notes.md").write_text("# Team Notes\n\nSome prose.\n")

        (listed,) = search_boards_hits("", local_project(tmp_path))

        assert listed.title == "Team Notes"


class TestSearchDashboardsHits:
    """search_boards_hits is the unbounded ranked list a host with a
    per-principal access model (Cloud) filters before truncating. Local/OSS
    callers never touch it: search_files's contract (this class) is
    unchanged by its existence."""

    def test_unbounded_listing_exceeds_the_public_search_cap(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        for i in range(MAX_SEARCH_LIMIT + 5):
            (boards / f"board_{i}.yml").write_text(f"title: Board {i}\nrows: []\n")
        project = local_project(tmp_path)

        hits = search_boards_hits("", project)
        capped = board_search(query="", project=project, limit=MAX_SEARCH_LIMIT + 5)

        assert len(hits) == MAX_SEARCH_LIMIT + 5
        assert len(capped.results) == MAX_SEARCH_LIMIT

    def test_search_boards_is_exactly_hits_truncated_to_limit(
        self, corpus_dir: Project
    ) -> None:
        hits = search_boards_hits("revenue", corpus_dir)
        result = board_search(query="revenue", project=corpus_dir, limit=2)

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
        assert all(type(h) is BoardListing for h in hits)

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
        full = [h.file_path for h in board_search("", corpus_dir).results]
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            page = board_search("", corpus_dir, limit=2, cursor=cursor)
            seen.extend(h.file_path for h in page.results)
            pages += 1
            cursor = page.next
            if cursor is None:
                break

        assert seen == full
        assert pages == 3

    def test_the_last_page_has_no_next(self, corpus_dir: Project) -> None:
        assert board_search("", corpus_dir, limit=5).next is None
        assert board_search("", corpus_dir, limit=4).next == "4"

    def test_a_zero_limit_page_has_no_cursor_to_loop_on(self) -> None:
        assert paginate([1, 2, 3], limit=0, cursor=None) == ([], None)

    def test_ranked_search_pages_too(self, corpus_dir: Project) -> None:
        everything = search_boards_hits("revenue", corpus_dir)
        first = board_search("revenue", corpus_dir, limit=1)
        second = board_search("revenue", corpus_dir, limit=1, cursor=first.next)

        assert [first.results[0], second.results[0]] == everything[:2]

    @pytest.mark.parametrize("cursor", ["abc", "-1", "1.5", ""])
    def test_a_cursor_that_is_not_a_next_value_is_refused(self, cursor: str) -> None:
        with pytest.raises(ValidationError, match="next"):
            SearchFilesArgs(cursor=cursor)

    def test_next_is_dropped_from_the_wire_when_absent(
        self, corpus_dir: Project
    ) -> None:
        wire = board_search("", corpus_dir).model_dump(exclude_none=True)

        assert "next" not in wire


# ---------------------------------------------------------------------------
# Chart-grain tests — per-chart entries, title/id ranking boosts
# ---------------------------------------------------------------------------


class TestChartGrainSearch:
    """Hits name the charts that matched, by id, from the same YAML dict."""

    def test_inline_chart_query_does_not_crash_search(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Charts routinely define their query inline (`query: {sql: ...}` —
        the documented quick-start shape). Search reads raw YAML and must not
        crash on it."""
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

        result = board_search(query="quick board", project=local_project(tmp_path))

        assert result.success
        assert [h.title for h in result.results] == ["Quick Board"]

    def test_non_string_chart_title_falls_back_to_the_id(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Search reads raw (possibly invalid) YAML — a junk-typed chart title
        degrades to the chart id, never crashes."""
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

        result = board_search(query="odd", project=local_project(tmp_path))

        assert result.success
        (hit,) = result.results
        assert isinstance(hit, BoardMatch)
        assert hit.matched_charts == ["odd"]

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

        result = board_search(query="2024", project=local_project(tmp_path))

        assert result.success
        (hit,) = result.results
        assert isinstance(hit, BoardMatch)
        assert hit.matched_charts == ["2024"]

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

        result = board_search(query="revenue", project=local_project(tmp_path))

        hit = result.results[0]
        assert isinstance(hit, BoardMatch)
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

        result = board_search(query="revenue", project=local_project(tmp_path))

        hit = result.results[0]
        assert isinstance(hit, BoardMatch)
        assert hit.matched_charts == []
        assert "chart_match" not in hit.match_reasons

    def test_hit_carries_no_chart_list_query_names_or_sql(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A hit is for picking a board; the detail is describe_board's."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "styled.yml").write_text(
            "title: Styled Board\n"
            "notes: A board with a chart\n"
            "queries:\n"
            "  q:\n"
            "    sql: SELECT 1\n"
            "charts:\n"
            "  fancy:\n"
            "    query: q\n"
            "    type: line\n"
            "rows:\n"
            "  - fancy\n"
        )

        result = board_search(query="styled", project=local_project(tmp_path))

        assert set(result.model_dump()["results"][0]) == {
            "title",
            "summary",
            "board_path",
            "file_path",
            "match_score",
            "match_reasons",
            "matched_charts",
        }

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

        result = board_search(query="monthly revenue", project=local_project(tmp_path))
        titles = [h.title for h in result.results]
        assert titles[0] == "Analytics Board"

        top_hit = result.results[0]
        assert isinstance(top_hit, BoardMatch)
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

        result = board_search(query="topline", project=local_project(tmp_path))
        assert len(result.results) == 1
        hit = result.results[0]
        assert isinstance(hit, BoardMatch)
        assert hit.matched_charts == ["topline_kpi"]


# ---------------------------------------------------------------------------
# Edge case tests — empty/noisy/invalid inputs
# ---------------------------------------------------------------------------


class TestSearchDashboardsEdgeCases:
    """Edge tests for empty query, unknown filter, and limit bounds."""

    def test_whitespace_only_query(self, corpus_dir: Project) -> None:
        """Whitespace-only query is the empty query: a listing."""

        result = board_search(query="   ", project=corpus_dir)
        assert len(result.results) == 5

    def test_unknown_tag_filter(self, corpus_dir: Project) -> None:
        """Filtering by a nonexistent tag returns empty results."""

        result = board_search(
            query="revenue", project=corpus_dir, tags=["nonexistent_tag_xyz"]
        )
        assert result.results == []

    @pytest.mark.parametrize("limit", [0, -1], ids=["zero", "negative"])
    def test_nonpositive_limit_returns_empty(
        self, corpus_dir: Project, limit: int
    ) -> None:
        """Zero or negative limit returns empty results."""

        result = board_search(query="revenue", project=corpus_dir, limit=limit)
        assert result.results == []

    def test_no_matching_dashboards(self, corpus_dir: Project) -> None:
        """Query that matches nothing returns empty results."""

        result = board_search(query="xyzzyplugh_no_match", project=corpus_dir)
        assert result.results == []


# ---------------------------------------------------------------------------
# Relevance / golden-query tests
# ---------------------------------------------------------------------------


class TestSearchDashboardsRelevance:
    """Golden-query tests for ranking stability and relevance."""

    def test_revenue_query_finds_sales_and_finance(self, corpus_dir: Project) -> None:
        """'revenue' should rank Sales and Finance dashboards highest."""

        result = board_search(query="revenue", project=corpus_dir)
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

        result = board_search(query=query, project=corpus_dir)
        assert len(result.results) > 0
        assert result.results[0].title == expected_top_title

    def test_tag_filter_narrows_results(self, corpus_dir: Project) -> None:
        """Filtering by tag 'sales' returns only the sales dashboard."""

        result = board_search(query="revenue", project=corpus_dir, tags=["sales"])
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
        result = board_search(query="zephyr", project=local_project(tmp_path))
        assert len(result.results) == 2
        paths = [str(h.file_path) for h in result.results]
        assert paths == sorted(paths), f"Expected lexicographic order, got {paths}"

    def test_title_match_reason_present(self, corpus_dir: Project) -> None:
        """Exact title term match should produce a 'title_match' reason."""

        result = board_search(query="Sales", project=corpus_dir)
        sales_hit = next(
            h for h in _matches(result.results) if h.title == "Sales Performance"
        )
        assert "title_match" in sales_hit.match_reasons

    def test_tag_match_reason_present(self, corpus_dir: Project) -> None:
        """Exact tag match should produce a 'tag_match' reason."""

        result = board_search(query="inventory", project=corpus_dir)
        inv_hit = next(
            h for h in _matches(result.results) if h.title == "Inventory Status"
        )
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
        result = board_search(query="above", project=local_project(tmp_path))
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

        result = board_search(query="zendesk", project=local_project(tmp_path))

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

        result = board_search(query="zendesk", project=local_project(tmp_path))

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

        result = board_search(query="zendesk", project=local_project(tmp_path))

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

        result = board_search(query="zendesk", project=local_project(tmp_path))

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

        result = board_search(query="sub dashboard", project=project)
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

        result = board_search(query="sub dashboard", project=project)
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

        result = board_search(query="zendesk", project=project)
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

        result = board_search(query="sub dashboard", project=project)
        hit = result.results[0]

        assert isinstance(hit.file_path, str)
        assert isinstance(hit.board_path, str)


class TestSearchFilesArgsWireSchema:
    """The wire model advertises exactly the search inputs — no path inputs."""

    def test_wire_schema_has_expected_properties(self) -> None:
        schema = SearchFilesArgs.model_json_schema()
        assert set(schema["properties"].keys()) == {
            "kind",
            "path",
            "text",
            "tags",
            "limit",
            "cursor",
        }
        assert "required" not in schema

    def test_project_dir_is_not_in_wire_schema(self) -> None:
        schema = SearchFilesArgs.model_json_schema()
        assert "project_dir" not in schema["properties"]

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchFilesArgs.model_validate({"text": "x", "directory": "some/path"})
