"""The x-axis overlap resolver's discrete branch must measure the number
format and case Vega actually paints on a numeric CATEGORY axis, not the
raw row value.

``_ordinal_label_texts`` (added for #9167) only reformats date-shaped
ordinal strings against an authored TIME format. A numeric category column
(e.g. ``revenue_bucket: "1000000"``) with an authored d3 NUMBER format
(``axis_x.labels.format: "$,.0f"``) paints "$1,000,000" — nearly twice the
width of the raw "1000000" the resolver used to measure — so a tilt/skip
decision made from the raw text can under-reserve room for the real label.
"""

from __future__ import annotations

import dataclasses
from typing import Any


def _axis(
    *,
    number_format: str | None = None,
    case: str | None = None,
    angle: float | None = None,
) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", "nominal", chart_type="", label_authored=False
    )
    font = axis_x.labels.font
    if case is not None:
        font = font.model_copy(update={"case": case})
    labels = dataclasses.replace(
        axis_x.labels,
        overlap=dataclasses.replace(axis_x.labels.overlap, skip=True, tilt=True),
        angle=angle,
        format=number_format,
        font=font,
    )
    return dataclasses.replace(axis_x, labels=labels)


def _numeric_category_rows(count: int) -> list[dict[str, Any]]:
    return [{"bucket": str((i + 1) * 1_000_000), "y": 1} for i in range(count)]


def test_authored_number_format_changes_the_resolved_layout() -> None:
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data = _numeric_category_rows(8)
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "continuous_temporal": False,
        "chart_width": 420.0,
    }

    bare = resolve_axis_x_overlap(_axis(), "bucket", data, **kwargs)
    formatted = resolve_axis_x_overlap(
        _axis(number_format="$,.0f"), "bucket", data, **kwargs
    )

    assert bare != formatted


def test_upper_case_changes_the_resolved_layout() -> None:
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    # Uppercase glyphs measure wider than the same lowercase string in most
    # fonts, so "upper" is the case whose painted width diverges from the
    # raw row value most reliably.
    data = [{"bucket": f"segment {chr(97 + i)}", "y": 1} for i in range(30)]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "continuous_temporal": False,
        "chart_width": 420.0,
    }

    bare = resolve_axis_x_overlap(_axis(), "bucket", data, **kwargs)
    uppered = resolve_axis_x_overlap(_axis(case="upper"), "bucket", data, **kwargs)

    assert bare != uppered


def test_pinned_angle_measures_the_formatted_and_cased_text() -> None:
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "continuous_temporal": False,
        "chart_width": 420.0,
    }
    numbers = _numeric_category_rows(8)
    bare = resolve_axis_x_overlap(_axis(angle=-45.0), "bucket", numbers, **kwargs)
    formatted = resolve_axis_x_overlap(
        _axis(angle=-45.0, number_format="$,.0f"), "bucket", numbers, **kwargs
    )
    assert formatted.label_block_height > bare.label_block_height

    words = [{"bucket": f"segment {chr(97 + i)}", "y": 1} for i in range(8)]
    lower = resolve_axis_x_overlap(_axis(angle=-45.0), "bucket", words, **kwargs)
    upper = resolve_axis_x_overlap(
        _axis(angle=-45.0, case="upper"), "bucket", words, **kwargs
    )
    assert upper.label_block_height > lower.label_block_height
