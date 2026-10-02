"""Channel classification and bar-orientation inference shared across cartesian resolvers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.style.theme import BarChartStyle
from dbt_charts.core.compile.resolve.chart.channel import (
    normalize_chart_channels,
)
from dbt_charts.core.compile.resolve.chart.enrich import (
    classify_column_type,
    first_non_null_samples,
    is_column_discrete_for_bar_orientation,
)
from dbt_charts.core.utils import is_year_shaped, vega_infers_quantitative

__all__ = [
    "_bar_orientation",
    "_channels_for",
    "_classify_to_channel_type",
    "_column_numeric_values",
]


def _flag_quantitative_color(
    channels: dict[str, ResolvedStyleChannel],
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
) -> dict[str, ResolvedStyleChannel]:
    """Mark a bare numeric ``color:`` channel as ``quantitative_data``.

    A bare ``color: <field>`` authoring resolves to mode="series" regardless
    of the field's own data -- ``normalize_chart_channels`` never inspects
    rows, so "this chart's color is a magnitude ramp" is otherwise
    undetectable for the common bare-authoring case by any consumer working
    off the resolved channel alone (e.g. hover-emphasis's palette gate).
    ``mode`` itself stays "series" -- ``channel_to_encoding`` dispatches its
    whole encoding shape on that value, so flipping it to "gradient" here
    would also require a real ``ch.scale``, taking over the emitter's own
    gradient-scale construction and changing what actually renders.
    ``quantitative_data`` is purely additive: nothing but the palette-gate
    check reads it.

    Shared by every cartesian family through ``_channels_for`` -- a bar,
    line, area, or scatter colored by a bare numeric field renders exactly
    the same continuous gradient legend a heatmap does
    (``infer_vega_type_from_data`` in ``channel_to_encoding`` decides the VL
    encoding type from the same "is every sampled value numeric" fact), so
    the flag can't be family-scoped without missing that legend for every
    family but heatmap.

    Calls ``vega_infers_quantitative`` -- not ``classify_column_type`` -- so
    this gate applies the emitter's own numeric rule (rejects numeric
    strings, counts ``bool``) and all-or-nothing threshold over the first 10
    rows, rather than the old >80%-of-20-samples verdict. That closes the
    *rule* divergence between the two predicates; a narrower *row-list* one
    can still remain, since this gate samples resolve-time rows while
    ``channel_to_encoding`` samples whatever rows reach the emitter, which a
    render-time transform (gap-fill, reordering) can leave different from
    the resolve-time list. The old gap diverged in both directions: a
    numeric string like ``"77"`` was flagged quantitative here but rendered
    nominal by the emitter (fail-closed -- an unwanted recede-block, not the
    bug this gate exists to catch); the row-10 cliff (numeric within this
    gate's sample but not within the emitter's) and an all-null column were
    unflagged here yet rendered quantitative (fail-open -- the actual bug:
    hover recedes a mark whose color encodes a value).
    """
    color = channels.get("color")
    if color is None or color.mode != "series" or not color.data_field:
        return channels
    if not vega_infers_quantitative(data, color.data_field):
        return channels
    return {
        **channels,
        # dataclasses.replace()/model_copy() on a Resolved*-typed value is
        # banned tree-wide (tests/test_no_replace_on_resolved.py) --
        # rebuilding via the constructor is the sanctioned pattern,
        # so every field is forwarded explicitly here rather than only the
        # ones happening to be non-default for mode="series" today, or a
        # future field added to ResolvedStyleChannel would silently drop out
        # the moment a bare color channel passed through this gate.
        "color": ResolvedStyleChannel(
            channel=color.channel,
            mode=color.mode,
            data_field=color.data_field,
            literal_value=color.literal_value,
            scale=color.scale,
            rules=color.rules,
            fallback_scale=color.fallback_scale,
            quantitative_data=True,
        ),
    }


def _channels_for(
    normalized: _BaseChartFields,
    data: list[dict[str, Any]],
) -> dict[str, ResolvedStyleChannel]:
    """Resolve channel bindings from normalized chart + data column names."""
    available = set(data[0].keys()) if data else set()
    # Pass style.color so normalize_chart_channels can upgrade the series channel
    # to gradient mode when color.gradient is set.
    # style is declared on every concrete chart model but not the _BaseChartFields
    # annotation, so read it structurally.
    style = getattr(normalized, "style", None)
    style_color = getattr(style, "color", None) if style is not None else None
    channels = normalize_chart_channels(normalized, available, style_color=style_color)
    return _flag_quantitative_color(channels, data)


def _bar_orientation(
    normalized: BarChart,
    bar: BarChartStyle,
    data: list[dict[str, Any]],
    bucketed_time: bool,
) -> Literal["vertical", "horizontal"] | None:
    """Resolve bar orientation: authored > bucketed-time > column-type inference > None.

    Priority:
    1. Authored orientation ("horizontal"/"vertical") from the cascaded bar
       style — the chart's inline ``style.orientation`` merged onto the board's
       ``style.charts.bar.orientation``, so an inline value still wins.
    2. Overlay layers → vertical. The overlay renderer draws every layer with the
       measure on y (vertical), so a horizontal base would put its measure on x
       and desync from the overlays. A bar base with layers is always vertical.
    3. Bucketed time (``bucketed_time=True``, e.g. axis_x.time_unit set) → vertical.
       monthofyear-style buckets emit nominal-looking labels but ride a temporal
       scale, so they must not flip to horizontal.
    4. Column-type inference via is_column_discrete_for_bar_orientation (strict):
       categorical/string x → horizontal; numeric/temporal x → vertical.
    5. No data or no x → None; nothing sets the key, so the emitter's own
       vertical default stands.
    """
    authored = bar.orientation
    if authored in ("horizontal", "vertical"):
        return authored  # type: ignore[return-value]
    if normalized.layers:
        return "vertical"
    if bucketed_time:
        return "vertical"
    if not data or normalized.x is None:
        return None
    samples = first_non_null_samples(normalized.x, data)
    if not samples:
        return None
    return (
        "horizontal" if is_column_discrete_for_bar_orientation(samples) else "vertical"
    )


def _classify_to_channel_type(
    field: str | None, data: list[dict[str, Any]], is_dimension: bool
) -> str:
    """Map compile.enrich column classification to cascade channel_type.

    Returns "quantitative", "temporal", or "nominal" — the three values that
    resolved_axis_style dispatches on (ordinal/nominal both hit the band path).

    Strict: Python str values are never quantitative regardless of content.
    Only native int/float Python types qualify as quantitative — the database
    returned those types, so the DB declared this column numeric.

    ``is_dimension`` gates the year-shape check to dimension/x callers only —
    a measure is never a year, so a scatter y (or any other measure channel)
    must pass ``is_dimension=False`` and stay quantitative even when its
    values happen to fall in the year range.
    """
    if not field or not data:
        return "nominal"
    samples = first_non_null_samples(field, data)
    if not samples:
        return "nominal"
    # Year-shaped INTEGER/VARCHAR x bakes the same band/temporal axis-style
    # cascade as a genuine DATE column — otherwise the quantitative-axis SI
    # suffix default (.3~s) leaks onto year ticks even though orientation and
    # vl_type already agree the column is continuous-temporal.
    if is_dimension and is_year_shaped(samples):
        return "temporal"
    # Native numeric check first — str "123" is not quantitative.
    if all(
        isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
        for v in samples
    ):
        return "quantitative"
    ct = classify_column_type(field, samples)
    return "temporal" if ct == "temporal" else "nominal"


def _column_numeric_values(
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
    field: str | None,
) -> list[float]:
    """Every numeric value one column paints, for a per-chart format vote.

    Shared extraction step behind ``resolve_format_parts_for_values`` (compile/
    format.py): a donut's theta column, a cartesian family's y (and
    scatter's x) measure(s), and a geo/heatmap chart's color/size channel
    all need "pull this column's raw values off ``data``" before voting on
    the sub-$1 register. A non-numeric field (e.g. scatter's nominal y in a
    dot plot) silently contributes nothing, so the vote falls through to
    today's unchanged spec rather than crashing on a formatting-only
    decision.
    """
    if not field:
        return []
    values = []
    for raw in (row.get(field) for row in data):
        if not isinstance(raw, (int, float, Decimal)) or isinstance(raw, bool):
            continue
        try:
            values.append(float(raw))
        except OverflowError:
            continue
    return values
