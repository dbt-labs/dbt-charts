"""A table squeezed by its slot degrades quietly; the sink must see each rung.

The ladder runs shrink font -> wrap headers -> squeeze columns -> paginate ->
ellipsize. WARN-TABLE-CRAMPED owns the width rung — columns divided into less
than their demand, read out as wrapped headers; the rows-per-page rung belongs
to WARN-TABLE-PAGE-SQUEEZED, recorded where the paginator overrides the height
the sizer reserved. These tests pin both rungs from authored boards, and the
roomy controls must stay silent, or the warnings are noise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_WIDE_COLUMNS: list[tuple[str, str]] = [
    ("region", "EMEA"),
    ("product_line", "Ingestion Cloud"),
    ("customer_segment", "Mid-Market"),
    ("order_channel", "Partner Reseller"),
    ("fulfillment_status", "Partially Shipped"),
    ("net_revenue", "2,013,880"),
    ("gross_margin", "51.2%"),
    ("units_shipped", "24,553"),
    ("return_rate", "2.1%"),
    ("last_order_date", "2025-11-04"),
]

_LONG_NOTE = (
    "Customer reported intermittent sync failures across three connectors and "
    "requested a full backfill before the quarter close, escalated to tier two"
)


def _board(rows: list[dict[str, Any]], height: str | None = None) -> str:
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: '{v}'" for k, v in row.items()) + "}"
        for row in rows
    )
    layout = (
        f"rows:\n  - height: {height}\n    rows:\n      - t\n"
        if height
        else "rows:\n  - t\n"
    )
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t: {query: q, type: table}\n" + layout
    )


def _warning_codes(yaml_text: str) -> set[str]:
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    return {w.code for w in output.warnings}


def _cramped(codes: set[str]) -> bool:
    return any("CRAMPED" in code for code in codes)


def _page_squeezed(codes: set[str]) -> bool:
    return any("PAGE-SQUEEZED" in code for code in codes)


def test_wrapped_headers_are_reported() -> None:
    """Ten realistic columns need 799px and get 768: headers wrap and collide."""
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    assert _cramped(_warning_codes(_board(rows)))


def test_pagination_collapse_is_page_squeezed_not_cramped() -> None:
    """A pinned slot drops four long-note rows to one per page.

    The height axis is WARN-TABLE-PAGE-SQUEEZED's: its fix (grow the slot)
    matches the cause. Cramping's width fix would not clear this, so a
    cramped warning here would hand the author a dead-end suggestion.
    """
    codes = _warning_codes(
        _board(
            [
                {
                    "ticket": f"T-{i:03d}",
                    "owner": "Priya R",
                    "status": "Open",
                    "notes": _LONG_NOTE,
                }
                for i in range(4)
            ],
            height="150px",
        )
    )
    assert _page_squeezed(codes)
    assert not _cramped(codes)


def test_roomy_table_reports_nothing() -> None:
    """Four short columns fit with slack; a warning here would be noise."""
    rows = [dict(_WIDE_COLUMNS[:4]) for _ in range(4)]
    assert not _cramped(_warning_codes(_board(rows)))


def test_short_notes_control_reports_nothing() -> None:
    """Same shape as the pagination case but short text: no collapse, no warning."""
    codes = _warning_codes(
        _board(
            [
                {
                    "ticket": f"T-{i:03d}",
                    "owner": "Priya R",
                    "status": "Open",
                    "notes": "Backfill requested",
                }
                for i in range(4)
            ]
        )
    )
    assert not _cramped(codes)
    assert not _page_squeezed(codes)


def _fixes(yaml_text: str) -> list[str]:
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    return [str(w.fix) for w in output.warnings if "CRAMPED" in w.code]


def _board_at(rows: list[dict[str, Any]], width: int) -> str:
    return _board(rows).replace(
        "title: T\n", f"title: T\nstyle:\n  frame:\n    width: {width}\n", 1
    )


def test_suggested_width_clears_the_warning() -> None:
    """The number in the fix must actually work — an agent will paste it back.

    A suggestion that leaves the warning standing is worse than none: the
    second pass changes the board and learns nothing.
    """
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    fixes = _fixes(_board(rows))
    assert fixes, "expected a cramping fix to parse a width out of"
    suggested = int(fixes[0].split("style.frame.width to about ")[1].split("px")[0])
    assert not _cramped(_warning_codes(_board_at(rows, suggested)))


def test_partially_widened_board_warns_until_demand_is_met() -> None:
    """At 950 the headers still wrap because the padded demand is still short.

    The demand counts each header at the same padded width the wrap threshold
    uses — an unpadded count under-measured by two cell pads per column, which
    made ~950 look sufficient, silenced the warning there, and left the named
    headers wrapped. Now 950 keeps warning, and its own suggestion clears.
    """
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    fixes = _fixes(_board_at(rows, 950))
    assert fixes, "expected the partially-widened board to still warn"
    suggested = int(fixes[0].split("style.frame.width to about ")[1].split("px")[0])
    assert not _cramped(_warning_codes(_board_at(rows, suggested)))


def _board_with_columns(rows: list[dict[str, Any]], columns_yaml: str) -> str:
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: '{v}'" for k, v in row.items()) + "}"
        for row in rows
    )
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n    style:\n"
        "      columns:\n" + columns_yaml + "rows:\n  - t\n"
    )


def test_fully_pinned_columns_do_not_warn() -> None:
    """A column pinned with width: demands exactly its pin — met by construction.

    Unpinned, these ten columns demand 799px against 768 and warn; pinned to
    70px each they sum to 700, and telling the author to widen the board
    would change nothing.
    """
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    columns_yaml = "".join(
        f"        {name}:\n          width: 70\n" for name, _ in _WIDE_COLUMNS
    )
    assert not _cramped(_warning_codes(_board_with_columns(rows, columns_yaml)))


def test_max_width_cap_bounds_the_demand() -> None:
    """A max_width: cap bounds what a column could ever take, so it bounds demand."""
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    columns_yaml = "".join(
        f"        {name}:\n          max_width: 60\n" for name, _ in _WIDE_COLUMNS
    )
    assert not _cramped(_warning_codes(_board_with_columns(rows, columns_yaml)))


def test_percent_pinned_columns_suggestion_clears_in_one_paste_back() -> None:
    """A %-pinned column keeps its slice of any budget. Scaling the raw
    shortfall under-shot on every paste-back (verified: ten rounds of
    following the suggestion never cleared); the solved suggestion must
    clear in one."""
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    columns_yaml = "".join(
        f"        {name}:\n          width: '45%'\n"
        if name in ("product_line", "customer_segment")
        else f"        {name}: {{}}\n"
        for name, _ in _WIDE_COLUMNS
    )
    yaml_text = _board_with_columns(rows, columns_yaml)
    fixes = _fixes(yaml_text)
    assert fixes, "expected the %-pinned board to warn"
    suggested = int(fixes[0].split("style.frame.width to about ")[1].split("px")[0])
    widened = yaml_text.replace(
        "title: T\n", f"title: T\nstyle:\n  frame:\n    width: {suggested}\n", 1
    )
    assert not _cramped(_warning_codes(widened))


def test_cols_slot_suggestion_clears_in_one_paste_back() -> None:
    """In a 2-col row the table holds ~half the board, so the raw shortfall
    must be scaled up ~2x — the case where an unscaled suggestion converges
    toward clearing instead of clearing."""
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: '{v}'" for k, v in row.items()) + "}"
        for row in rows
    )
    yaml_text = (
        "title: T\nstyle:\n  frame:\n    width: 1200\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t: {query: q, type: table}\n"
        "  k: {query: q, type: kpi, value: net_revenue}\n"
        "cols:\n  - t\n  - k\n"
    )
    fixes = _fixes(yaml_text)
    assert fixes, "expected the half-width table to warn"
    suggested = int(fixes[0].split("style.frame.width to about ")[1].split("px")[0])
    widened = yaml_text.replace("width: 1200", f"width: {suggested}", 1)
    assert not _cramped(_warning_codes(widened))


def test_percent_column_in_zero_budget_slot_renders_without_error() -> None:
    """A collapsed slot can hand a table a zero column budget; a %-pinned
    column there must not divide by it. Twelve tables in one 600px row each
    rendered an internal-error card before the guard."""
    body = "      - {a: 'x'}"
    charts = "\n".join(
        f"  t{i}:\n    query: q\n    type: table\n    style:\n"
        f"      columns:\n        a:\n          width: '50%'"
        for i in range(12)
    )
    cols = "\n".join(f"  - t{i}" for i in range(12))
    yaml_text = (
        "title: T\nstyle:\n  frame:\n    width: 600\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n" + charts + "\ncols:\n" + cols + "\n"
    )
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    assert not [e for e in output.chart_errors if "INTERNAL" in str(e)], (
        output.chart_errors
    )
