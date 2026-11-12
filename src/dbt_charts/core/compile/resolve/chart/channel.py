"""Chart style channel parsing and normalization.

Handles the {column, value, scale} grammar for chart-level color
channels (color, background, opacity, stroke.color, stroke.width).

Rule-driven styling is ``style.conditional`` (a table's or a KPI's), not a
channel.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.authored import GEO_CHART_TYPES
from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.primitives import (
    ColorStyle,
    GradientColorStyle,
    ResolvedScaleTargetConfig,
    ScaleTargetConfig,
    bake_scale_target_stops,
)
from dbt_charts.core.compile.models.style.theme import KpiBackgroundGradient

_RawStyleChannel = str | dict[str, Any]


@dataclass(frozen=True)
class _SeriesChannelInput:
    channel: str
    data_field: str


@dataclass(frozen=True)
class _LiteralChannelInput:
    channel: str
    raw: _RawStyleChannel


@dataclass(frozen=True)
class _GradientChannelInput:
    channel: str
    data_field: str
    scale: ResolvedScaleTargetConfig


_ParsedChannelInput = _SeriesChannelInput | _LiteralChannelInput | _GradientChannelInput


def parse_style_channel(
    raw: _RawStyleChannel,
    channel_name: str,
) -> ResolvedStyleChannel:
    """Parse a raw authored channel value into a typed ResolvedStyleChannel.

    Handles shorthand (string) and four expanded forms.
    Validates mutual exclusion. Does NOT validate field references (needs data).

    Args:
        raw: The authored value — string shorthand or {column,value,scale,when} dict
        channel_name: The channel name for error messages

    Raises:
        ValueError: On mutual exclusion violations or unknown form
    """

    return _resolve_style_channel_input(_parse_style_channel_input(raw, channel_name))


def _parse_style_channel_input(
    raw: _RawStyleChannel,
    channel_name: str,
) -> _ParsedChannelInput:
    """Parse authored channel grammar without crossing the resolved boundary."""

    if isinstance(raw, str):
        return _SeriesChannelInput(channel_name, raw)

    has_column = "column" in raw
    has_value = "value" in raw
    has_scale = "scale" in raw

    extra = set(raw.keys()) - {"column", "value", "scale"}
    if extra:
        raise ValueError(
            f"Channel '{channel_name}': unknown keys {sorted(extra)} (supported: column, value, scale)."
        )

    if has_value and (has_column or has_scale):
        raise ValueError(
            f"Channel '{channel_name}': 'value' cannot be combined with 'column' or 'scale'."
        )

    if has_value:
        if raw["value"] is None:
            raise ValueError(
                f"Channel '{channel_name}': 'value' cannot be None — use a non-null style value."
            )
        return _LiteralChannelInput(channel_name, raw)

    if not has_column:
        raise ValueError(
            f"Channel '{channel_name}': must specify 'column' or 'value' (got keys: {list(raw.keys())})."
        )

    data_field = raw["column"]

    if has_scale:
        scale_cfg = _parse_channel_scale(raw, channel_name)
        return _GradientChannelInput(channel_name, data_field, scale_cfg)

    return _SeriesChannelInput(channel_name, data_field)


def _resolve_style_channel_input(
    parsed: _ParsedChannelInput,
) -> ResolvedStyleChannel:
    """Construct one final resolved channel from a validated parsed input."""
    if isinstance(parsed, _SeriesChannelInput):
        return ResolvedStyleChannel(
            channel=parsed.channel,
            mode="series",
            data_field=parsed.data_field,
        )
    if isinstance(parsed, _GradientChannelInput):
        return ResolvedStyleChannel(
            channel=parsed.channel,
            mode="gradient",
            data_field=parsed.data_field,
            scale=parsed.scale,
        )
    assert isinstance(parsed.raw, dict)
    return ResolvedStyleChannel(
        channel=parsed.channel,
        mode="literal",
        literal_value=parsed.raw["value"],
    )


def _parse_channel_scale(
    raw: dict[str, Any], channel_name: str
) -> ResolvedScaleTargetConfig:
    scale = raw["scale"]
    if not isinstance(scale, dict):
        raise ValueError(
            f"Channel '{channel_name}': 'scale' must be a mapping, "
            f"got {type(scale).__name__}."
        )
    scale_dict = dict(scale)
    if channel_name in ("opacity", "stroke_width") and "palette" not in scale_dict:
        scale_dict["palette"] = [0.2, 1.0]
    # A named dbt charts palette resolves to hex stops via bake_scale_target_stops;
    # a Vega scheme name (e.g. "viridis") is left alone and forwarded to VL as
    # `scheme:` directly (gradient_scale_to_vl).
    return bake_scale_target_stops(ScaleTargetConfig.model_validate(scale_dict))


def _validate_channel_field(
    channel_name: str,
    data_field: str,
    available_columns: set[str],
) -> None:
    if not data_field or data_field in available_columns:
        return
    # Common mistake: author writes `color: "#fff"` intending a literal.
    # The string shorthand parses as series mode with data_field="#fff".
    if data_field.startswith("#"):
        raise ValueError(
            f"Channel '{channel_name}': '{data_field}' looks like a literal color, "
            "but a channel binds a data column. Set a literal color on the "
            "chart's mark, e.g. for a bar chart:\n  style:\n    marks:\n      bar:\n"
            f'        color: "{data_field}"'
        )
    raise ValueError(
        f"Channel '{channel_name}' column '{data_field}' not found. "
        f"Available: {sorted(available_columns)}"
    )


def validate_label_field_columns(
    label_fields: Iterable[str],
    available_columns: set[str],
) -> None:
    """Raise ValueError if a ``labels.field`` names a column not in the data.

    ``labels.field`` sources value-label text from a named column; an unknown
    column silently renders blank labels (Vega-Lite emits empty text), so validate
    it like a channel column. The raise lives in ``compile/``, not ``render/``,
    which requires stamped errors.
    """
    for field in label_fields:
        if field not in available_columns:
            raise ValueError(
                f"labels.field column '{field}' not found. "
                f"Available: {sorted(available_columns)}"
            )


_COLOR_CHANNELS = frozenset({"color", "background"})
_NUMERIC_CHANNELS: frozenset[str] = frozenset()


def _validate_palette_type(channel_name: str, scale: ScaleTargetConfig) -> None:
    palette = scale.palette
    if not palette:
        return
    if channel_name in _COLOR_CHANNELS:
        if not all(isinstance(c, str) for c in palette):
            raise ValueError(
                f"Channel '{channel_name}' expects a color palette (list of strings), "
                f"got numeric values. Use strings like '#ffffff'."
            )
    elif channel_name in _NUMERIC_CHANNELS and not all(
        isinstance(c, (int, float)) and not isinstance(c, bool) for c in palette
    ):
        raise ValueError(
            f"Channel '{channel_name}' expects a numeric palette (list of floats), "
            f"got non-numeric values."
        )


def _validate_parsed_channel_input(
    parsed: _ParsedChannelInput,
    available_columns: set[str],
) -> None:
    if isinstance(parsed, _LiteralChannelInput):
        return
    if available_columns:
        _validate_channel_field(parsed.channel, parsed.data_field, available_columns)
    if isinstance(parsed, _GradientChannelInput):
        _validate_palette_type(parsed.channel, parsed.scale)


def normalize_chart_channels(
    chart: Any,
    available_columns: set[str],
    style_color: ColorStyle | GradientColorStyle | str | None = None,
) -> dict[str, ResolvedStyleChannel]:
    """Parse and validate chart-level style channels.

    ``color`` is a data channel at chart root for all chart types. Other paint
    fields (opacity, stroke) are not data channels, and a KPI's gradient
    background is ``kpi_background_channel``.

    ``style_color``: when a color config with a gradient is provided, the
    series color channel is upgraded to gradient mode.

    Raises ValueError on unresolvable field references or invalid syntax.
    """
    raw_channels: dict[str, _RawStyleChannel] = {}

    chart_type = getattr(chart, "type", None)

    color_raw = getattr(chart, "color", None)
    style_gradient = (
        style_color.gradient
        if style_color is not None
        and not isinstance(style_color, str)
        and chart_type not in GEO_CHART_TYPES
        else None
    )
    if color_raw is not None:
        if style_gradient is not None and (
            isinstance(color_raw, str)
            or ("column" in color_raw and "scale" not in color_raw)
        ):
            gradient_dict = style_gradient.model_dump(exclude_none=True)
            color_raw = (
                {"column": color_raw, "scale": gradient_dict}
                if isinstance(color_raw, str)
                else {**color_raw, "scale": gradient_dict}
            )
        raw_channels["color"] = color_raw
    elif style_gradient is not None:
        raise ValueError(
            "style.color: {gradient: ...} requires chart.color to name a data field.\n"
            "Add a data column binding, e.g.:\n"
            "  color: revenue\n"
            "  style:\n    color:\n      gradient:\n        palette: ['#fff', '#00f']"
        )

    parsed_channels = {
        channel_name: _parse_style_channel_input(raw, channel_name)
        for channel_name, raw in raw_channels.items()
    }
    for channel_input in parsed_channels.values():
        _validate_parsed_channel_input(channel_input, available_columns)

    channels = {
        channel_name: _resolve_style_channel_input(parsed)
        for channel_name, parsed in parsed_channels.items()
    }

    # Geo renderers only consume the color field name — gradient/conditional modes
    # silently drop palette or rules. Raise early so the author gets a clear error.
    if (
        chart_type in GEO_CHART_TYPES
        and (parsed_color := parsed_channels.get("color")) is not None
        and isinstance(parsed_color, _GradientChannelInput)
    ):
        raise ValueError(
            f"Geo charts ('{chart_type}') only support series or literal color channels — "
            "got mode 'gradient'. Use chart.color: <field> or chart.style.color: <hex>."
        )

    return channels


def kpi_background_channel(
    gradient: KpiBackgroundGradient, available_columns: set[str]
) -> ResolvedStyleChannel:
    """The KPI card's background channel: its color scale, read at the value's column.

    Raises:
        ValueError: The column is not in the query result, or the palette is not
            a list of colors.
    """
    if available_columns:
        _validate_channel_field("background", gradient.column, available_columns)
    scale = bake_scale_target_stops(gradient.scale)
    _validate_palette_type("background", scale)
    return ResolvedStyleChannel(
        channel="background",
        mode="gradient",
        data_field=gradient.column,
        scale=scale,
    )
