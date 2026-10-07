"""End-to-end wiring tests: text truncation warnings through the real render path.

Each test calls render_dashboard so that truncation is detected during the
real render_board_svg (not a synthetic re-render at a different width), and
asserts the expected warning code appears in result.warnings.

HIGH-4: These tests can detect the loss of any recording site — deleting a
record_text_truncation call from the source makes its surface's test fail.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock


def _mock_adapter(rows: list[dict[str, Any]]) -> Mock:
    """Adapter registry mock that returns ``rows`` for every query."""
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}
    return registry


# ── KPI label ────────────────────────────────────────────────────────────────


def test_kpi_label_truncated_fires_warning(tmp_path, local_project) -> None:
    """KPI with a label too long for its slot → WARN_KPI_LABEL_TRUNCATED.

    The label must be tested at the REAL slot width (not the full board width),
    since the slot is narrower: internal padding removes ~32px from the render
    width.  This pins that collect_text_truncations() is open during
    render_board_svg — not during a synthetic re-render at the full board width.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_KPI_LABEL_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"value": 42}])

    # A label long enough to truncate at the real KPI card width (200px board
    # width forces a narrow slot: 200 - 2*margin - 2*card_padding).
    long_label = "Total Revenue This Quarter Across All Global Regions"
    truncated_board = InMemoryBoard(
        f"""
width: 200
source: examples_db
queries:
  q: SELECT value FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: "{long_label}"
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_KPI_LABEL_TRUNCATED.code in codes, (
        f"Expected WARN_KPI_LABEL_TRUNCATED; got: {codes}"
    )
    kpi_warn = next(
        w for w in result.warnings if w.code == WARN_KPI_LABEL_TRUNCATED.code
    )
    assert kpi_warn.path == "charts.k.label"

    # A short label must NOT fire.
    clean_board = InMemoryBoard(
        """
width: 200
source: examples_db
queries:
  q: SELECT value FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: Revenue
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    clean_codes = {w.code for w in clean.warnings}
    assert WARN_KPI_LABEL_TRUNCATED.code not in clean_codes


# ── KPI inline fallback ──────────────────────────────────────────────────────


def test_kpi_inline_fallback_fires_warning(tmp_path, local_project) -> None:
    """style.variant: inline whose run overflows the card -> WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED.

    HIGH-4 (review): the fallback from inline to stacked must be reported,
    not silent. Mirrors the label-truncation test above — same real-render
    path (not a synthetic re-render), so a deleted `record_text_truncation`
    call at the fallback site fails this test.

    `type: kpi` doesn't accept a chart-level `width:` (`ERR-EXTRA-FIELD`), so
    both boards size the card off the theme's default `preferred_width`
    (300px) — the overflow board's content is long enough to exceed it, the
    clean board's is short enough to fit inside it.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"value": 84.33, "delta": -10.23}])

    overflow_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT value, delta FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: "Label, Value and Support"
    style:
      variant: inline
    support:
      value: delta
      label: vs. prior period
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=overflow_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED.code in codes, (
        f"Expected WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED; got: {codes}"
    )
    fallback_warn = next(
        w
        for w in result.warnings
        if w.code == WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED.code
    )
    assert fallback_warn.path == "charts.k.style.variant"

    # A run short enough to fit the default 300px card must NOT fire.
    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT value, delta FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: "Revenue"
    style:
      variant: inline
    support:
      value: delta
      label: vs LQ
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    clean_codes = {w.code for w in clean.warnings}
    assert WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED.code not in clean_codes


# ── chart title ──────────────────────────────────────────────────────────────


def test_chart_title_truncated_fires_warning(tmp_path, local_project) -> None:
    """Chart with an over-long title → WARN_CHART_TITLE_TRUNCATED."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_CHART_TITLE_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"month": "Jan", "revenue": 10}])

    long_title = "Revenue Performance By Enterprise Segment And Partner Region Over The Last Quarter"
    truncated_board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    title: "{long_title}"
    width: 200
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_CHART_TITLE_TRUNCATED.code in codes, (
        f"Expected WARN_CHART_TITLE_TRUNCATED; got: {codes}"
    )
    warn = next(w for w in result.warnings if w.code == WARN_CHART_TITLE_TRUNCATED.code)
    assert warn.path == "charts.c.title"

    # Short title → no warning.
    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue
    width: 200
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_CHART_TITLE_TRUNCATED.code not in {w.code for w in clean.warnings}


# ── table column header ──────────────────────────────────────────────────────


def test_table_column_header_truncated_fires_warning(tmp_path, local_project) -> None:
    """Table with a column header too long for its slot → WARN_TABLE_TEXT_TRUNCATED."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_TABLE_TEXT_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    long_col = "another_extremely_long_header_label_here"
    registry = _mock_adapter([{long_col: "v1"}, {long_col: "v2"}])

    truncated_board = InMemoryBoard(
        f"""
width: 200
source: examples_db
queries:
  q: SELECT {long_col} FROM t
charts:
  t:
    query: q
    type: table
rows:
  - t
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_TABLE_TEXT_TRUNCATED.code in codes, (
        f"Expected WARN_TABLE_TEXT_TRUNCATED; got: {codes}"
    )
    warn = next(w for w in result.warnings if w.code == WARN_TABLE_TEXT_TRUNCATED.code)
    assert warn.path is not None and long_col in warn.path

    # A short header must NOT fire.
    clean_board = InMemoryBoard(
        """
width: 200
source: examples_db
queries:
  q: SELECT col FROM t
charts:
  t:
    query: q
    type: table
rows:
  - t
""",
        path=project.path("charts/_t.yml"),
    )
    clean_registry = _mock_adapter([{"col": "v1"}, {"col": "v2"}])
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=clean_registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_TABLE_TEXT_TRUNCATED.code not in {w.code for w in clean.warnings}


# ── callout ──────────────────────────────────────────────────────────────────


def test_callout_title_truncated_fires_warning(tmp_path, local_project) -> None:
    """Callout with a title too long for its card → WARN_CALLOUT_TEXT_TRUNCATED."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_CALLOUT_TEXT_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    # No data needed — callout has no query.
    registry = _mock_adapter([])

    long_title = (
        "This is a very long callout title that will definitely overflow the card "
        "and require truncation at render time because it is just too long."
    )
    truncated_board = InMemoryBoard(
        f"""
width: 200
charts:
  co:
    type: callout
    message: Hello
    title: "{long_title}"
rows:
  - co
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_CALLOUT_TEXT_TRUNCATED.code in codes, (
        f"Expected WARN_CALLOUT_TEXT_TRUNCATED; got: {codes}"
    )

    # Short title → no warning.
    clean_board = InMemoryBoard(
        """
width: 200
charts:
  co:
    type: callout
    message: Hello
    title: Hi
rows:
  - co
""",
        path=project.path("charts/_t.yml"),
    )
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_CALLOUT_TEXT_TRUNCATED.code not in {w.code for w in clean.warnings}


# ── spark bar label ──────────────────────────────────────────────────────────


def test_spark_label_truncated_fires_warning(tmp_path, local_project) -> None:
    """Spark bar with a label too long for its slot → WARN_SPARK_LABEL_TRUNCATED."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_SPARK_LABEL_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    long_label = "An Extremely Long Category Label That Cannot Fit In A Spark Bar Row"
    registry = _mock_adapter([{"category": long_label, "value": 10}])

    # spark_bar: x = count/frequency column, y = row-label column.
    # The long label must be in y_field for the truncation guard to see it.
    truncated_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT category, value FROM t
charts:
  s:
    query: q
    type: spark_bar
    x: value
    y: category
rows:
  - s
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_SPARK_LABEL_TRUNCATED.code in codes, (
        f"Expected WARN_SPARK_LABEL_TRUNCATED; got: {codes}"
    )

    # Short label → no warning.
    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT category, value FROM t
charts:
  s:
    query: q
    type: spark_bar
    x: value
    y: category
rows:
  - s
""",
        path=project.path("charts/_t.yml"),
    )
    clean_registry = _mock_adapter([{"category": "Jan", "value": 10}])
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=clean_registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_SPARK_LABEL_TRUNCATED.code not in {w.code for w in clean.warnings}


# ── false-positive regression: authored ellipsis must not fire ────────────────


def test_authored_ellipsis_in_title_does_not_fire_warning(
    tmp_path, local_project
) -> None:
    """A title containing an authored '…' that fully fits must NOT warn.

    Pins the fix for CRITICAL-2: the old '…' sniff fires on this authored text
    even though the SVG renders the full title intact at width 1400.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_CHART_TITLE_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"month": "Jan", "revenue": 10}])

    board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    title: "Loading… revenue"
    width: 1400
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_CHART_TITLE_TRUNCATED.code not in codes, (
        "Authored '…' in a fully-visible title must not fire WARN_CHART_TITLE_TRUNCATED; "
        f"got warnings: {codes}"
    )


def test_double_space_clip_title_does_not_fire_warning(tmp_path, local_project) -> None:
    """A title with double spaces that fits after normalization must NOT warn.

    Pins the fix for CRITICAL-2: the old len(result) < len(original) sniff
    fires on whitespace normalization, even though no character was clipped.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_CHART_TITLE_TRUNCATED
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"month": "Jan", "revenue": 10}])

    board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    title: "Revenue  by  month"
    width: 1400
    style:
      title:
        overflow: clip
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_CHART_TITLE_TRUNCATED.code not in codes, (
        "Double-space title that fits after whitespace normalization must not warn; "
        f"got warnings: {codes}"
    )


def test_kpi_align_overflow_fires_warning(tmp_path, local_project) -> None:
    """align: right on a value too wide for the card -> WARN_KPI_ALIGN_OVERFLOW.

    Real-render path, not a synthetic re-render, so deleting the
    `record_text_truncation` call at the clamp site fails this test. The clean
    board pins the other half: a value that fits must apply align silently.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_KPI_ALIGN_OVERFLOW
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    registry = _mock_adapter([{"value": 1234567890123.0, "delta": -10.23}])

    overflow_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT value, delta FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: "Revenue"
    style:
      variant: compact
      align: right
      value:
        format: ",.0f"
    support:
      value: delta
      label: vs. prior period
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=overflow_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_KPI_ALIGN_OVERFLOW.code in codes, (
        f"Expected WARN_KPI_ALIGN_OVERFLOW; got: {codes}"
    )
    warn = next(w for w in result.warnings if w.code == WARN_KPI_ALIGN_OVERFLOW.code)
    assert warn.path == "charts.k.style.align"

    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT value, delta FROM t
charts:
  k:
    query: q
    type: kpi
    value: value
    label: "Revenue"
    style:
      variant: compact
      align: right
      value:
        format: currency
    support:
      value: delta
      label: vs LQ
rows:
  - k
""",
        path=project.path("charts/_t.yml"),
    )
    clean = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_KPI_ALIGN_OVERFLOW.code not in {w.code for w in clean.warnings}


def test_support_table_cell_overflow_fires_warning(tmp_path, local_project) -> None:
    """Dense strip cells wider than their band -> WARN_SUPPORT_TABLE_CELL_OVERFLOW.

    Real-render path, so dropping the detector from the registry or the record
    call from the post-pass fails it; the sparse board pins the quiet half.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.diagnostics import WARN_SUPPORT_TABLE_CELL_OVERFLOW
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)

    def warn_codes(n_rows: int) -> set[str]:
        registry = _mock_adapter(
            [{"month": f"m{i:02d}", "revenue": 1234567.0 + i} for i in range(n_rows)]
        )
        board = InMemoryBoard(
            """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: ",.0f"
    style:
      orientation: vertical
rows:
  - c
""",
            path=project.path("charts/_t.yml"),
        )
        result = render_dashboard(
            board=board,
            adapter_registry=registry,
            format="svg",
            project=project,
            result_cache=None,
        )
        return {w.code for w in result.warnings}

    assert WARN_SUPPORT_TABLE_CELL_OVERFLOW.code in warn_codes(27)
    assert WARN_SUPPORT_TABLE_CELL_OVERFLOW.code not in warn_codes(3)
