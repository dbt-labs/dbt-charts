"""Tests for board_to_dict row capping (max_rows_per_query)."""

from __future__ import annotations

import typing
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_to_dict import (
    CHART_FIELDS,
    _render_chart_item,
    board_to_dict,
)
from dbt_charts.core.render.errors import RenderError

from .._board_utils import _default_chart_style_context, _default_resolved_style


def _make_executor(data: list[dict[str, Any]]) -> MagicMock:
    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = data
    return executor


def _make_board(chart: Any) -> Board:
    return Board(
        id="test-board",
        title="Test Board",
        layout=Layout(
            type="rows",
            items=[LayoutItem(type="chart", chart=chart, width=600, height=300)],
            width=600,
            height=600,
        ),
        resolved_style=_default_resolved_style(),
        chart_style_context=_default_chart_style_context(),
        level=1,
    )


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"month": f"m{i}", "revenue": i} for i in range(n)]


def test_no_cap_returns_all_rows_without_truncation_record(
    make_chart: Callable[..., Any],
) -> None:
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    result = board_to_dict(board, _make_executor(_rows(3)), {})
    item = result["items"][0]
    assert len(item["data"]) == 3
    assert "rows_truncated" not in item


def test_cap_below_row_count_keeps_both_ends(make_chart: Callable[..., Any]) -> None:
    """The crop keeps head and tail — the chart's right edge (latest points of
    an ascending series) survives, as does the top of a ranked result."""
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    rows = _rows(5)
    result = board_to_dict(board, _make_executor(rows), {}, max_rows_per_query=2)
    item = result["items"][0]
    assert item["data"] == [rows[0], rows[4]]
    assert item["rows_truncated"] == {"head": 1, "tail": 1, "total": 5}


def test_odd_cap_gives_extra_row_to_the_head(make_chart: Callable[..., Any]) -> None:
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    rows = _rows(9)
    result = board_to_dict(board, _make_executor(rows), {}, max_rows_per_query=3)
    item = result["items"][0]
    assert item["data"] == [rows[0], rows[1], rows[8]]
    assert item["rows_truncated"] == {"head": 2, "tail": 1, "total": 9}


def test_cap_of_one_keeps_only_the_head(make_chart: Callable[..., Any]) -> None:
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    rows = _rows(4)
    result = board_to_dict(board, _make_executor(rows), {}, max_rows_per_query=1)
    item = result["items"][0]
    assert item["data"] == [rows[0]]
    assert item["rows_truncated"] == {"head": 1, "tail": 0, "total": 4}


def test_cap_at_or_above_row_count_is_a_no_op(make_chart: Callable[..., Any]) -> None:
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    result = board_to_dict(board, _make_executor(_rows(2)), {}, max_rows_per_query=2)
    item = result["items"][0]
    assert len(item["data"]) == 2
    assert "rows_truncated" not in item


def test_cap_below_one_raises(make_chart: Callable[..., Any]) -> None:
    board = _make_board(make_chart("bar", x="month", y="revenue"))
    with pytest.raises(RenderError, match="max_rows_per_query"):
        board_to_dict(board, _make_executor(_rows(2)), {}, max_rows_per_query=0)


def test_variable_dict_excludes_internal_fields() -> None:
    """User-facing variable projection must not leak compile-internal fields.

    ``input_auto_detected`` and ``variable_dependencies`` are no longer
    ``exclude=True`` on ``Variable`` (they must round-trip in the
    resolved-board artifact), so the exclusion moves to this call site
    instead — an author never wrote these and cannot author them back.
    """
    from dbt_charts.core.compile import compile

    result = compile(
        """
title: T
variables:
  category:
    options:
      static: [a, b, c]
queries:
  qv:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: qv
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
    )
    assert result.success, result.errors
    board = result.board
    # Sanity: the field this test guards against leaking is actually set.
    assert board.variables["category"].input_auto_detected is True

    out = board_to_dict(board, _make_executor(_rows(1)), {})

    var_dict = out["variables"]["category"]
    assert "input_auto_detected" not in var_dict
    assert "variable_dependencies" not in var_dict


def test_render_chart_item_error_diagnostic_carries_authored_path(
    make_chart: Callable[..., Any],
) -> None:
    """The JSON/YAML/text chart-render-error surface reuses the same
    chart-identity path as the SVG surface (`chart.source_path`), not a
    separately invented convention."""
    chart = make_chart("bar", x="month", y="revenue")
    executor = MagicMock(spec=Executor)
    executor.execute_chart.side_effect = ExecutionError("timeout")
    collector: list[Any] = []

    _render_chart_item(chart, executor, {}, error_collector=collector)

    assert collector[0].path == f"charts.{chart.id}"


# --- CHART_FIELDS reconciliation --------------------------------------------

#: Resolved-chart fields deliberately absent from ``CHART_FIELDS``, each with
#: the reason. A field that is neither projected nor listed here fails the
#: reconciliation test below — so growing the model forces a decision rather
#: than silently dropping the field from every data-bearing format.
_OMITTED_FROM_PROJECTION = {
    # Emitted under a different key, or carried by the structure itself.
    "chart_type": "emitted as `type`",
    "query_name": "emitted as `query`",
    "pivot_columns": "emitted as `columns` (pure rename of authored `columns`)",
    "id": "the key of the charts map",
    "measure_type": "derived at resolve from the y and y_start columns' data",
    # Shares a name with an authored field but not a shape: the resolved layer
    # carries the full set of baked mark styles (line_mark/point_mark/...) plus
    # `query_name`, none of which the authored layer accepts. Projecting it
    # makes the yaml document fail to re-compile.
    "layers": "resolved layer carries baked mark styles; no authored counterpart",
    # Authored via the style cascade (`style.stack`); chart-root `stack:` is
    # rejected on every authored family, so re-emitting it here would produce
    # something that cannot be authored back.
    "stack": "authored as style.stack, not a chart-root field",
    # Reads like an authored field but is resolved from the style cascade and
    # rejected on the authored bar surface.
    "orientation": "resolved from style; not authorable",
    # Where the chart was authored, not part of what it is. Projecting it would
    # emit a key no authored chart accepts, and the position is already carried
    # where it is used — `Diagnostic.path` and the `data-editor-path` handle.
    "source_path": "authoring coordinates, not chart content",
    "defined_in_other_file": "internal: ChartRef provenance, not chart content",
    # Resolved/derived internals with no authored counterpart.
    "aspect_ratio": "derived at resolve time",
    "layout_padding": "internal layout info",
    "legend": "resolved legend",
    "max_height": "derived sizing",
    "min_height": "derived sizing",
    # Authored at the BOARD level (`style.charts.category_colors`), then
    # narrowed per chart at resolve. Emitting it on a chart would produce a
    # key no authored chart accepts.
    "category_colors": "board-level binding, narrowed per chart at resolve",
    "palette": "resolved palette",
    "requested_alias_palette": "resolved palette input",
    "requested_alias_substitute": "resolved palette input",
    "resolved_channels": "internal channel bindings",
    "stacked_domain_max": "derived at resolve time",
    "panel_axes": "derived small-multiples partition; no authored counterpart (see `multiples`)",
    "variable_dependencies": "internal dependency set",
    "dark_companion_stops": "derived dark-mode palette companion",
    "resolution_width": "pie layout finalization input",
    "outer_fraction": "resolved pie wheel geometry",
    "attached_table_gap": "resolved pie attachment geometry",
    "hybrid_heading_gap": "resolved pie heading geometry",
    "slice_label_indices": "resolved pie label row selection",
    "attached_table": "resolved pie companion table",
    "attached_row_indices": "resolved pie companion row selection",
    "presentation_fingerprint": "resolved pie row fingerprint",
    "attached_table_placement": "resolved pie composition geometry",
    "attached_table_width": "resolved pie composition geometry",
    "wheel_width": "resolved pie composition geometry",
    "attached_heading": "resolved pie companion heading",
    "attached_heading_font": "resolved pie companion heading style",
    "identity_field": "resolved pie wedge identity source (color, else inferred); no authored counterpart",
    # Table internals: the authored surface is `columns`/`rows`/`values`, which
    # are projected; these are the resolved expansions of them.
    "header_overflow": "resolved header overflow policy",
    # Render-native-key-space input only (pivot leaf/measure columns and
    # transpose's __metric__/__value__ pair) — every other column already has
    # this merged into `columns`. See compile/resolve/chart/_table.py.
    "column_defaults": "render input for pivot-leaf/transpose-output columns",
    # Same-name/different-shape, like `layers`: resolved is a per-column style
    # dict, while the authored `columns:` is a list of pivot field names.
    "columns": "resolved column-style dict; authored `columns` is a field list",
    "value_field": "resolved KPI value binding",
    "format_native": "render-internal provenance flag for KPI value format register; not authored",
    "lookup_field": "resolved geo lookup binding",
    # Geo/basemap descriptors resolved from `geo_source`/`basemap`.
    "geo_feature": "resolved from geo_source",
    "geo_format_type": "resolved from geo_source",
    "geo_join_key": "resolved from geo_source",
    "geo_key_examples": "resolved from geo_source",
    "geo_key_format": "resolved from geo_source",
    "geo_projection_params": "resolved from projection",
    "geo_projection_type": "resolved from projection",
    "geo_property": "resolved from geo_source",
    "geo_source_name": "resolved from geo_source",
    "geo_url": "resolved from geo_source",
    "basemap_fill": "resolved from basemap",
    "basemap_geo_format": "resolved from basemap",
    "basemap_geo_url": "resolved from basemap",
    "basemap_stroke": "resolved from basemap",
    # Chart-local presentation facts baked at resolve time for VL emission
    # (background/title-overflow); no authored counterpart.
    "background": "resolved chart-local or board background",
    "title_style": "resolved chart-local title style (overflow, case)",
    # Support-table strip geometry baked at resolve time so render never re-runs axis cascade.
    "support_table_axis_offset": "pixel offset baked at resolve time for support_table strip placement",
    "effective_support_table_style": "final per-chart support_table style (board theme merged with family override)",
    # Fold-resolved multi-y list; authors write `y: [a, b]` which the resolver
    # normalizes into `y=WIDE_VALUE_FIELD, wide_measures=(a, b)`.
    "wide_measures": "resolved from multi-y authoring; no direct authored counterpart",
}


def _resolved_chart_field_names() -> set[str]:
    """Every field across all resolved chart families.

    ``ResolvedChart`` is ``Annotated[A | B | ..., Discriminator(...)]``, so the
    union must be unwrapped twice — reading only ``get_args(ResolvedChart)[0]``
    yields the union object itself, and iterating that collapses to the first
    variant, silently exempting the other eleven families from the check.
    """
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart

    union = typing.get_args(ResolvedChart)[0]
    members = typing.get_args(union)
    assert len(members) > 1, f"expected the full family union, got {members!r}"

    names: set[str] = set()
    for model in members:
        if hasattr(model, "model_fields"):
            names |= set(model.model_fields)
    return names


def test_chart_fields_covers_resolved_surface() -> None:
    """Every resolved-chart field is projected or explicitly justified.

    ``CHART_FIELDS`` is a hand-kept list feeding both the ``yaml`` and ``data``
    formats. Without this check, adding a chart field silently drops it from
    both — indistinguishable, to a consumer, from the author not setting it.
    """
    unaccounted = (
        _resolved_chart_field_names()
        - set(CHART_FIELDS)
        - set(_OMITTED_FROM_PROJECTION)
    )
    assert not unaccounted, (
        "Resolved chart fields are neither projected by CHART_FIELDS nor listed "
        "in _OMITTED_FROM_PROJECTION with a reason: "
        f"{sorted(unaccounted)}. Add them to the projection, or record why they "
        "are excluded."
    )
