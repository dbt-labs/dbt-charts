"""Serialization helpers for chart rendering outputs."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.render.utils import normalize_data_types


def build_dbt_charts_json(
    chart: Chart,
    data: list[dict[str, Any]],
    width: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    """Build the normalized dbt charts JSON representation for a chart."""
    normalized_data = normalize_data_types(data)
    result: dict[str, Any] = {"type": chart.type}

    # These fields vary by chart family — use getattr for polymorphic access.
    for _fname in ("title", "label", "subtitle", "notes"):
        _val = getattr(chart, _fname, None)
        if _val:
            result[_fname] = _val

    for field in ["x", "y", "color", "size", "shape"]:
        value = getattr(chart, field, None)
        if value is not None:
            result[field] = value

    for field in ["x_label", "y_label"]:
        value = getattr(chart, field, None)
        if value is not None:
            result[field] = value

    _format = getattr(chart, "format", None)
    if _format is not None:
        result["format"] = (
            _format.model_dump(exclude_none=True)
            if isinstance(_format, FormatConfig)
            else _format
        )

    # KPI quantitative-text-object fields. `support` is a Pydantic model so it
    # needs ``model_dump`` to be JSON-serializable.
    _support = getattr(chart, "support", None)
    if _support is not None:
        result["support"] = _support.model_dump(exclude_none=True)

    for field in [
        "geo",
        "geo_source",
        "lookup",
        "value",
        "glyph",
        "projection",
        "latitude",
        "longitude",
    ]:
        value = getattr(chart, field, None)
        if value is not None:
            result[field] = value

    _style = getattr(chart, "style", None)
    if _style:
        result["style"] = _style.model_dump(exclude_none=True)

    _link = getattr(chart, "link", None)
    if _link:
        result["link"] = _link

    if chart.type == "table":
        result["columns"] = list(normalized_data[0].keys()) if normalized_data else []

    result["data"] = normalized_data
    if width is not None:
        result["width"] = width
    if height is not None:
        result["height"] = height

    return result
