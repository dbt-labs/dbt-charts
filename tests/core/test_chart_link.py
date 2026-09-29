"""Tests for chart link (drill-through / variable update) interactivity.

link: supports both navigation URLs and variable-update query strings:
  link: "/detail?month={{ x }}"   # navigate to URL
  link: "?region={{ x }}"         # update variable via query string

The old `href:` field is rejected at compile time with a message directing
authors to use `link:` instead.
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.controls import controls_stylesheet

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# The row-link band is one static class; it carries its theme link color as
# the --dbt-link custom property, which the host's hover rule reads.
_ROW_LINK_CLASS = r"dbt-table-row-link"


def _row_link_count(svg: str) -> int:
    return len(re.findall(rf'class="{_ROW_LINK_CLASS}"', svg))


def _row_link_index(svg: str) -> int:
    match = re.search(rf'class="{_ROW_LINK_CLASS}"', svg)
    assert match is not None, "no row-link rect found"
    return match.start()


_BASE_YAML = """\
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: "2024-01", revenue: 1000}
      - {month: "2024-02", revenue: 1500}
      - {month: "2024-03", revenue: 1200}
variables:
  region_var:
    label: Region
    default: All
rows:
  - c1
"""


def _yaml_with_chart(chart_yaml: str) -> str:
    return _BASE_YAML + f"charts:\n  c1:\n{_indent(chart_yaml, 4)}\n"


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# Compile-time: link field
# ---------------------------------------------------------------------------


def test_link_compiles_to_compiled_chart():
    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
link: "/detail?month={{ x }}"
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    assert chart.link == "/detail?month={{ x }}"


def test_link_none_by_default():
    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board.charts["c1"].link is None


def test_link_query_string_for_variable_update():
    """link: '?var={{ x }}' is the idiomatic way to update a variable on click."""
    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
link: "?region_var={{ x }}"
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board.charts["c1"].link == "?region_var={{ x }}"


# ---------------------------------------------------------------------------
# Compile-time: href is rejected with actionable message
# ---------------------------------------------------------------------------


def test_href_is_rejected_with_link_message():
    """href: must be rejected and the error must tell the author to use link:."""
    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
href: "/detail?month={{ x }}"
"""
    )
    result = compile(yaml)
    assert not result.success
    error_text = " ".join(str(e) for e in result.errors)
    assert "link" in error_text.lower(), (
        f"Error should mention 'link:', got: {error_text}"
    )


def test_href_rejected_on_kpi():
    """href: must be rejected on KPI charts too."""
    yaml = """\
title: Test
queries:
  q1:
    type: values
    rows:
      - {count: 42}
charts:
  c1:
    type: kpi
    query: q1
    value: count
    href: "/detail"
rows:
  - c1
"""
    result = compile(yaml)
    assert not result.success
    error_text = " ".join(str(e) for e in result.errors)
    assert "link" in error_text.lower(), (
        f"Error should mention 'link:', got: {error_text}"
    )


def test_href_rejected_on_table():
    """href: must be rejected on table charts too."""
    yaml = """\
title: Test
queries:
  q1:
    type: values
    rows:
      - {name: "foo", count: 1}
charts:
  c1:
    type: table
    query: q1
    href: "/detail"
rows:
  - c1
"""
    result = compile(yaml)
    assert not result.success
    error_text = " ".join(str(e) for e in result.errors)
    assert "link" in error_text.lower(), (
        f"Error should mention 'link:', got: {error_text}"
    )


# ---------------------------------------------------------------------------
# Compile-time: old interactions field is rejected
# ---------------------------------------------------------------------------


def test_interactions_field_is_rejected():
    """The old dead interactions schema must no longer be accepted."""
    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
interactions:
  click:
    action: set_variable
    target: region_var
"""
    )
    result = compile(yaml)
    assert not result.success


# ---------------------------------------------------------------------------
# Render-time: link → Vega-Lite spec with calculate transform + field encoding
# ---------------------------------------------------------------------------


def test_link_renders_to_vega_lite_calculate_and_field(
    local_project: Callable[..., FilesystemProject],
):
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
link: "/detail?month={{ x }}"
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["c1"]
    data = executor.execute_query(chart.query_name)

    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data)

    # VL internal encoding channel is still named "href" (Vega-Lite wire format)
    encoding = spec.get("encoding", {})
    assert "href" in encoding, f"No href in encoding. Keys: {list(encoding.keys())}"
    href_enc = encoding["href"]
    assert "field" in href_enc, f"href encoding must use 'field', got: {href_enc}"
    assert href_enc["field"] == "__df_href__"

    # calculate transform must be present with the right expression
    transforms = spec.get("transform", [])
    calc = next(
        (t for t in transforms if "calculate" in t and t.get("as") == "__df_href__"),
        None,
    )
    assert calc is not None, f"Missing calculate transform. transforms: {transforms}"
    # Navigation URL: sentinel prefix + path and field reference
    assert "http://dct.invalid" in calc["calculate"], (
        f"sentinel missing: {calc['calculate']}"
    )
    assert "month" in calc["calculate"], f"field missing: {calc['calculate']}"


def test_link_query_string_renders_with_sentinel(
    local_project: Callable[..., FilesystemProject],
):
    """link: '?var={{ x }}' uses sentinel so variables.js can intercept after SVG post-processing."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    yaml = _yaml_with_chart(
        """\
query: q1
type: bar
x: month
y: revenue
link: "?region_var={{ x }}"
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["c1"]
    data = executor.execute_query(chart.query_name)

    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data)

    transforms = spec.get("transform", [])
    calc = next(
        (t for t in transforms if "calculate" in t and t.get("as") == "__df_href__"),
        None,
    )
    assert calc is not None
    # Query-string URL: sentinel prefix + ?var=value expression
    assert "http://dct.invalid" in calc["calculate"]
    assert "region_var" in calc["calculate"]
    assert "month" in calc["calculate"]


# ---------------------------------------------------------------------------
# Render-time: temporal x-axis fields use timeFormat, not string concat
# ---------------------------------------------------------------------------


def test_link_temporal_x_uses_timeformat_not_string_concat(
    local_project: Callable[..., FilesystemProject],
):
    """Clicking a bar whose x-axis is explicitly temporal must produce an ISO date
    string in the link URL, not a Unix millisecond timestamp.
    """

    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    yaml = """\
title: Test
queries:
  daily:
    type: values
    rows:
      - {day: "2026-04-23", cost: 12.50}
      - {day: "2026-04-24", cost: 8.75}
charts:
  daily_bar:
    query: daily
    type: bar
    x: day
    y: cost
    link: "/list/?date_start={{ x }}&date_end={{ x }}"
    style:
      axis_x:
        type: temporal
rows:
  - daily_bar
"""
    result = compile(yaml)
    assert result.success, result.errors

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["daily_bar"]
    data = executor.execute_query(chart.query_name)
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data)

    transforms = spec.get("transform", [])
    calc = next(
        (t for t in transforms if "calculate" in t and t.get("as") == "__df_href__"),
        None,
    )
    assert calc is not None, f"Missing calculate transform: {transforms}"

    expr = calc["calculate"]
    assert "timeFormat" in expr, (
        f"Expected timeFormat for temporal x-axis field, got: {expr!r}"
    )
    assert "'' + datum['day']" not in expr, (
        f"String concat on temporal field produces ms timestamps: {expr!r}"
    )
    assert "%Y-%m-%d" in expr, f"Expected ISO date format in timeFormat: {expr!r}"


# ---------------------------------------------------------------------------
# SVG post-processing: sentinel xlink:href → plain href
# ---------------------------------------------------------------------------


def test_svg_postprocess_strips_sentinel_and_fixes_href_attribute():
    """After vl_convert, sentinel xlink:href must become plain href so variables.js works."""
    from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs

    svg_input = (
        '<g class="mark container">'
        '<a xlink:href="http://dct.invalid?region_var=January">'
        '<path d="M1,0"/></a>'
        '<a xlink:href="http://dct.invalid/detail?month=February">'
        '<path d="M2,0"/></a>'
        "</g>"
    )
    result = _fix_chart_click_hrefs(svg_input)

    assert '<a href="?region_var=January">' in result
    assert '<a href="/detail?month=February">' in result
    # Sentinel must be gone
    assert "dct.invalid" not in result
    # xlink:href must be gone on those elements
    assert 'xlink:href="http://dct.invalid' not in result


def test_svg_postprocess_does_not_double_escape_multi_param_link():
    from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs

    svg_input = '<a xlink:href="http://dct.invalid/detail?x=1&amp;y=2"><path/></a>'
    result = _fix_chart_click_hrefs(svg_input)

    assert '<a href="/detail?x=1&amp;y=2">' in result
    assert "&amp;amp;" not in result


def test_svg_postprocess_leaves_external_hrefs_untouched():
    """Non-sentinel xlink:href values (external navigation) are left as-is."""
    from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs

    svg_input = '<a xlink:href="https://example.com/detail?month=January"><path/></a>'
    result = _fix_chart_click_hrefs(svg_input)

    # External URL must remain unchanged
    assert result == svg_input


# ---------------------------------------------------------------------------
# Table: chart-root link as default + column override
# ---------------------------------------------------------------------------


def test_table_chart_root_link_compiles():
    """Table chart accepts chart-root link."""
    yaml = """\
title: Test
queries:
  q1:
    type: values
    rows:
      - {name: "foo", id: 1}
charts:
  c1:
    type: table
    query: q1
    link: "/items/detail?id={{ id }}"
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    assert chart.link == "/items/detail?id={{ id }}"


def test_table_column_link_overrides_chart_root():
    """Column-level link in style.columns overrides chart-root link for that column."""
    yaml = """\
title: Test
queries:
  q1:
    type: values
    rows:
      - {name: "foo", status: "open", id: 1}
charts:
  c1:
    type: table
    query: q1
    link: "/items/detail?id={{ id }}"
    style:
      columns:
        status:
          link: "/items/backlog?status={{ status }}"
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    assert chart.link == "/items/detail?id={{ id }}"
    assert chart.style.columns["status"].link == "/items/backlog?status={{ status }}"


# ---------------------------------------------------------------------------
# Board link resolution: chart/table links run through resolve_href
# ---------------------------------------------------------------------------


def test_svg_postprocess_applies_board_resolver_for_serve():
    """After stripping sentinel, board-root paths are rewritten for serve mode."""
    from dbt_charts.core.render.board_links import LinkContext, set_link_context
    from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs

    ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
    set_link_context(ctx)
    try:
        svg_input = '<a xlink:href="http://dct.invalid/zendesk/tickets/detail?id=42"><path/></a>'
        result = _fix_chart_click_hrefs(svg_input)
    finally:
        set_link_context(None)

    assert '<a href="/charts/zendesk/tickets/detail?id=42">' in result
    assert "dct.invalid" not in result


def test_svg_postprocess_query_string_link_unchanged_by_board_resolver():
    """Variable-update links (?var=...) pass through board resolver unchanged."""
    from dbt_charts.core.render.board_links import LinkContext, set_link_context
    from dbt_charts.core.render.converters.chart import _fix_chart_click_hrefs

    ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
    set_link_context(ctx)
    try:
        svg_input = '<a xlink:href="http://dct.invalid?region_var=North"><path/></a>'
        result = _fix_chart_click_hrefs(svg_input)
    finally:
        set_link_context(None)

    assert '<a href="?region_var=North">' in result
    assert "dct.invalid" not in result


def test_kpi_link_board_resolver():
    """KPI link is run through board resolver at render time."""
    from dbt_charts.core.render.board_links import (
        LinkContext,
        resolve_href,
        set_link_context,
    )

    ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
    set_link_context(ctx)
    try:
        resolved = resolve_href("/zendesk/tickets/detail", ctx)
    finally:
        set_link_context(None)

    assert resolved == "/charts/zendesk/tickets/detail"


@pytest.mark.parametrize(
    ("raw_link", "expected"),
    [
        ("/zendesk/tickets/detail", "/charts/zendesk/tickets/detail"),
        ("?var=value", "?var=value"),  # in-page link passes through
        ("https://example.com", "https://example.com"),  # external passes through
    ],
)
def test_table_cell_link_board_resolver(raw_link: str, expected: str) -> None:
    """resolve_cell_link_with_board applies board resolver after template substitution."""
    from dbt_charts.core.render.board_links import LinkContext, set_link_context
    from dbt_charts.core.render.chart.table_support import resolve_cell_link_with_board

    ctx = LinkContext(root="/charts", current_board_slug="zendesk/overview")
    set_link_context(ctx)
    try:
        result = resolve_cell_link_with_board(raw_link, {}, [])
    finally:
        set_link_context(None)

    assert result == expected


def test_table_render_chart_root_link_is_row_scoped_column_link_wins() -> None:
    """A chart-root link renders as ONE row-scoped anchor per row, not per cell.

    Row-link UX contract:
    - a value row with a chart-root link emits exactly one row-band ``<a>``
      wrapping a ``dbt-table-row-link`` rect (the whole-row affordance)
    - a column with its own ``link`` still emits its own cell anchor
    - a plain column (no link of its own) is NOT wired to the chart-root link
      — no per-cell anchor, no link-colored text
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [
        {"ticket_id": "T1", "status": "Open"},
        {"ticket_id": "T2", "status": "Closed"},
    ]
    board_style = resolve_style(get_theme_style())

    # style.columns is styling-only — every query column renders regardless.
    # Only status gets a column-level link.
    yaml = """\
title: Tickets
queries:
  q1:
    type: values
    rows:
      - {ticket_id: "T1", status: "Open"}
      - {ticket_id: "T2", status: "Closed"}
charts:
  c1:
    type: table
    query: q1
    link: "/zendesk/ticket/{{ ticket_id }}"
    style:
      columns:
        ticket_id:
          label: "Ticket ID"
        status:
          link: "/zendesk/backlog/?status={{ status }}"
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved, data, width=800, height=400, board_style=board_style
    )

    # One row-band anchor per value row — not one anchor per non-linked cell.
    assert _row_link_count(svg) == 2
    assert 'href="/zendesk/ticket/T1"' in svg
    assert 'href="/zendesk/ticket/T2"' in svg
    # The nameless-rect anchor carries an aria-label (the row's first value) so
    # AT and keyboard focus announce something.
    assert 'aria-label="T1"' in svg
    # status keeps its own column-level cell link (rendered as a cell anchor).
    assert 'href="/zendesk/backlog/?status=Open"' in svg
    assert 'href="/zendesk/backlog/?status=Closed"' in svg
    # The row-link rect paints BEFORE the status cell anchor so the cell link
    # wins the click (SVG document order = z-order, later = on top).
    assert _row_link_index(svg) < svg.index('href="/zendesk/backlog/?status=Open"')
    # The plain ticket_id cell carries the inert-cell class; its stylesheet
    # rule is specificity-qualified (``.dbt-chart text.dbt-table-cell-inert``)
    # so it beats the board's ``.dbt-chart text { pointer-events: auto }`` and
    # the click/hover falls through to the row band. A bare presentation
    # attribute would lose that cascade, so assert the class + the rule, not an
    # attribute. The wired "Open" cell keeps events and is inked as a link.
    assert re.search(r'<text class="dbt-table-cell-inert"[^>]*>T1</text>', svg)
    assert ".dbt-chart text.dbt-table-cell-inert" not in svg
    assert re.search(
        r"\.dbt-chart text\.dbt-table-cell-inert[^{]*\{[^}]*pointer-events:\s*none",
        controls_stylesheet(),
    )
    open_text = re.search(r"<text([^>]*)>[^<]*Open</text>", svg)
    assert open_text is not None
    assert "dbt-table-cell-inert" not in open_text.group(1)
    assert 'class="dbt-table-link-text"' in open_text.group(1)


def test_table_chart_root_link_does_not_ink_plain_cells() -> None:
    """Plain dimension columns under a chart-root link are text, not links.

    A table whose ONLY link is the chart-root link must not wrap any cell in a
    ``dbt-table-link`` anchor — the row band is the sole clickable affordance,
    so ``priority``/``status``-style dimensions stay plain text.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [
        {"ticket_id": "T1", "priority": "low", "status": "new"},
        {"ticket_id": "T2", "priority": "high", "status": "open"},
    ]
    board_style = resolve_style(get_theme_style())

    yaml = """\
title: Tickets
queries:
  q1:
    type: values
    rows:
      - {ticket_id: "T1", priority: "low", status: "new"}
      - {ticket_id: "T2", priority: "high", status: "open"}
charts:
  c1:
    type: table
    query: q1
    link: "/zendesk/ticket/{{ ticket_id }}"
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved, data, width=800, height=400, board_style=board_style
    )

    # Row band exists, one per value row.
    assert _row_link_count(svg) == 2
    # No per-cell link inking: no cell anchor, no inked link-text element.
    # (The static hover CSS references `.dbt-table-link-text`, so match the
    # class attribute a wired cell would carry, not the stylesheet rule.)
    assert '<g class="dbt-table-link"' not in svg
    assert 'class="dbt-table-link-text"' not in svg
    # Row-hover selection background CSS is present and distinct from link text.
    assert re.search(rf"\.{_ROW_LINK_CLASS}:hover", controls_stylesheet()) is not None
    assert (
        re.search(rf'class="{_ROW_LINK_CLASS}"[^>]*style="--dbt-link: ', svg)
        is not None
    )
    # Plain cell text carries the inert-cell class, and the stylesheet drops its
    # pointer events via a rule qualified to outrank the board's
    # ``.dbt-chart text { pointer-events: auto }`` — this is what lets a
    # click/hover on the value reach the row band in the composed page. (No
    # browser lane exists in this package; the real composed-page interaction
    # was verified manually against `dct serve`.)
    assert '<text class="dbt-table-cell-inert"' in svg
    assert re.search(
        r"\.dbt-chart text\.dbt-table-cell-inert[^{]*\{[^}]*pointer-events:\s*none",
        controls_stylesheet(),
    )


def test_table_row_link_skips_summary_and_total_rows() -> None:
    """Summary/total rows get no row-band link or hover affordance."""
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [
        {"company": "Apex", "id": 1, "kind": "value"},
        {"company": "Bright", "id": 2, "kind": "value"},
        {"company": "Total", "id": 0, "kind": "total"},
    ]
    board_style = resolve_style(get_theme_style())

    yaml = """\
title: Accounts
queries:
  q1:
    type: values
    rows:
      - {company: "Apex", id: 1, kind: "value"}
      - {company: "Bright", id: 2, kind: "value"}
      - {company: "Total", id: 0, kind: "total"}
charts:
  c1:
    type: table
    query: q1
    link: "/accounts/{{ id }}"
    style:
      row:
        role: kind
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved, data, width=800, height=400, board_style=board_style
    )

    # Two value rows get a row band; the total row does not.
    assert _row_link_count(svg) == 2
    assert 'href="/accounts/1"' in svg
    assert 'href="/accounts/2"' in svg
    assert 'href="/accounts/0"' not in svg


def test_table_row_link_skips_row_number_column() -> None:
    """The row-number gutter is excluded from the row-band link geometry.

    With the index gutter visible the band must start to the RIGHT of where it
    starts without the gutter — the row link never covers the row-number column.
    """
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [{"company": "Apex", "id": 1}, {"company": "Bright", "id": 2}]
    board_style = resolve_style(get_theme_style())

    def _render(row_numbers_visible: bool) -> str:
        yaml = f"""\
title: Accounts
queries:
  q1:
    type: values
    rows:
      - {{company: "Apex", id: 1}}
      - {{company: "Bright", id: 2}}
charts:
  c1:
    type: table
    query: q1
    link: "/accounts/{{{{ id }}}}"
    style:
      row_numbers:
        visible: {str(row_numbers_visible).lower()}
rows:
  - c1
"""
        result = compile(yaml)
        assert result.success, result.errors
        chart = result.board.charts["c1"]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        return render_table_svg(
            resolved, data, width=800, height=400, board_style=board_style
        )

    def _band_x(svg: str) -> float:
        xs = re.findall(rf'<rect class="{_ROW_LINK_CLASS}"[^>]*? x="([0-9.]+)"', svg)
        assert len(xs) == 2, svg
        return float(xs[0])

    x_with_gutter = _band_x(_render(True))
    x_no_gutter = _band_x(_render(False))
    assert x_with_gutter > x_no_gutter


def test_table_links_use_body_color_medium_weight_and_hover_underline() -> None:
    """Linked table cells stay neutral at rest and reveal underline on interaction."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [{"company": "Cyberdyne", "bookings": 2787}]
    board_style = resolve_style(get_theme_style())

    yaml = """\
title: Accounts
queries:
  q1:
    type: values
    rows:
      - {company: "Cyberdyne", bookings: 2787}
charts:
  c1:
    type: table
    query: q1
    style:
      columns:
        company:
          link: "/accounts/{{ company }}"
        bookings: {}
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved, data, width=600, height=240, board_style=board_style
    )

    assert '<a href="/accounts/Cyberdyne">' in svg
    css = controls_stylesheet()
    assert ".dbt-table-link:hover .dbt-table-link-text" in css
    assert "a:focus-visible .dbt-table-link-text" in css
    assert "text-decoration-thickness: 1px" in css
    assert "text-underline-offset: 1px" in css
    assert ".dbt-table-link:hover" not in svg
    assert f'fill="{board_style.font.color}"' in svg
    assert 'font-weight="500"' in svg
    assert "dbt-table-link-text" in svg


def test_table_render_no_link_produces_no_anchors() -> None:
    """Table chart without link: renders no <a> wrappers."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [{"name": "Alice", "score": 95}]
    board_style = resolve_style(get_theme_style())

    yaml = """\
title: Scores
queries:
  q1:
    type: values
    rows:
      - {name: "Alice", score: 95}
charts:
  c1:
    type: table
    query: q1
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_table_svg(
        resolved, data, width=600, height=200, board_style=board_style
    )

    assert "<a " not in svg


_SERIES_YAML = """\
title: Test Dashboard
queries:
  q1:
    type: values
    rows:
      - {month: "2024-01", region: East, revenue: 1000}
      - {month: "2024-02", region: East, revenue: 1500}
      - {month: "2024-01", region: West, revenue: 800}
      - {month: "2024-02", region: West, revenue: 900}
rows:
  - c1
"""


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_link_survives_the_endpoint_label_concat_wrapper(
    local_project: Callable[..., FilesystemProject],
    orientation: str,
):
    """A multi-series stacked bar direct-labels by default, which wraps the
    chart in a concat — hconcat for vertical, vconcat for horizontal.

    Neither VL nor Vega reads a top-level `encoding`/`transform` on a concat
    spec, so an href stamped there never reaches the marks and `link:` goes
    dead with no error. It has to land on the chart pane.
    """
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    yaml = (
        _SERIES_YAML
        + f"""\
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    color: region
    link: "/detail?month={{{{ x }}}}"
    style:
      stack: zero
      orientation: {orientation}
"""
    )
    result = compile(yaml)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["c1"]
    data = executor.execute_query(chart.query_name)
    spec = generate_vega_lite_spec(chart, data)

    concat = spec.get("hconcat") or spec.get("vconcat")
    assert concat is not None, "expected the endpoint-label rail to wrap the chart"

    def _panes(node):
        for key in ("hconcat", "vconcat"):
            if key in node:
                for child in node[key]:
                    yield from _panes(child)
                return
        yield node

    hrefs = [p for p in _panes(spec) if "href" in (p.get("encoding") or {})]
    assert hrefs, "no pane carries the href encoding — link: is dead"
    for pane in hrefs:
        assert any(
            t.get("as") == "__df_href__" for t in (pane.get("transform") or [])
        ), "href encoding without its calculate transform in the same pane"


def test_row_link_band_carries_its_link_color_as_a_property():
    """One class for every theme: the band carries its theme link color as
    `--dbt-link`, which the host's single `:hover` rule reads off the rect
    itself — so two tables with different link colors on one page cannot
    collide the way a class minted per color once had to prevent. Pinned on
    the emitted shape of one table."""
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    data = [
        {"ticket_id": "T1", "status": "new"},
        {"ticket_id": "T2", "status": "open"},
    ]
    yaml = """\
title: Tickets
queries:
  q1:
    type: values
    rows:
      - {ticket_id: "T1", status: "new"}
      - {ticket_id: "T2", status: "open"}
charts:
  c1:
    type: table
    query: q1
    link: "/zendesk/ticket/{{ ticket_id }}"
rows:
  - c1
"""
    result = compile(yaml)
    assert result.success, result.errors
    resolved = resolve(
        result.board.charts["c1"], data, chart_style_context=_BOARD_STYLE
    )
    svg = render_table_svg(
        resolved,
        data,
        width=800,
        height=400,
        board_style=resolve_style(get_theme_style()),
    )

    bands = re.findall(
        rf'<rect class="{_ROW_LINK_CLASS}" fill="transparent" style="--dbt-link: (#[0-9A-Fa-f]{{6}})"',
        svg,
    )
    assert bands, "no row-link band carrying --dbt-link"
    assert len(set(bands)) == 1, "one table, one link color"
    assert "var(--dbt-link)" in controls_stylesheet()
