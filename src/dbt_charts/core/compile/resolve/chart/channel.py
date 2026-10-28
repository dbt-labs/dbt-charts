"""Chart style channel parsing and normalization.

Handles the {column, value, scale} grammar for chart-level color
channels (color, background, opacity, stroke.color, stroke.width).

Rule-driven styling lives in the chart-level ``conditional_formatting:``
block (indexed by column name), not per-channel. See ``types.py`` for
``FieldConditionalFormatting``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from dbt_charts.core.compile.models.chart.authored import (
    GEO_CHART_TYPES,
    ConditionalRule,
    FieldConditionalFormatting,
)
from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.primitives import (
    ColorStyle,
    ResolvedScaleTargetConfig,
    ScaleTargetConfig,
    StaticGradientColorStyle,
    bake_scale_target_stops,
)

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


@runtime_checkable
class ConditionalFormattingChart(Protocol):
    """Structural shape needed to project/validate conditional_formatting.

    Satisfied by both the ``Chart`` and any per-family normalized
    chart model (e.g. ``KpiChart``) — the two hierarchies share no common
    base class, but both declare these two fields identically.
    """

    @property
    def type(self) -> str:
        """Chart type discriminator (e.g. ``"kpi"``, ``"bar"``)."""
        ...

    @property
    def conditional_formatting(self) -> dict[str, FieldConditionalFormatting] | None:
        """Rule-driven styling, keyed by column name."""
        ...


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


def validate_channel_fields(
    channels: dict[str, ResolvedStyleChannel],
    available_columns: set[str],
) -> None:
    """Raise ValueError if any channel references a field not in available_columns.

    Only validates data-bound channels (mode in series/gradient/conditional).
    """
    for channel_name, ch in channels.items():
        if ch.mode == "literal":
            continue
        _validate_channel_field(channel_name, ch.data_field, available_columns)


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
            f"Channel '{channel_name}': '{data_field}' looks like a literal color — "
            f'use {{{channel_name}: {{value: "{data_field}"}}}}'
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
    it like a channel column. Sibling of ``validate_channel_fields`` so the raise
    lives in ``compile/`` (not ``render/``, which requires stamped errors).
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
    style_color: ColorStyle | StaticGradientColorStyle | str | None = None,
) -> dict[str, ResolvedStyleChannel]:
    """Parse and validate chart-level style channels.

    ``color`` is a data channel at chart root for all chart types.
    ``background`` is a data channel at chart root for KPI charts only
    (gradient background painted by value position in scale).
    Other paint fields (opacity, stroke) are not data channels.

    ``style_color``: when a color config with a gradient is provided, the
    series color channel is upgraded to gradient mode. Conditional formatting
    is projected before construction so each output channel is finalized once.

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

    if chart_type == "kpi":
        background_raw = chart.background
        if background_raw is not None:
            raw_channels["background"] = background_raw

    parsed_channels = {
        channel_name: _parse_style_channel_input(raw, channel_name)
        for channel_name, raw in raw_channels.items()
    }
    for channel_input in parsed_channels.values():
        _validate_parsed_channel_input(channel_input, available_columns)

    is_pivot_table = chart_type == "table" and bool(chart.columns)
    support_column = kpi_support_rule_column(chart) if chart_type == "kpi" else None
    projected = (
        _project_conditional_formatting_inputs(
            chart,
            available_columns,
            is_pivot_table=is_pivot_table,
            support_column=support_column,
        )
        if isinstance(chart, ConditionalFormattingChart)
        and chart.conditional_formatting is not None
        else {}
    )
    channels: dict[str, ResolvedStyleChannel] = {}
    channel_names = [
        *raw_channels,
        *(name for name in projected if name not in raw_channels),
    ]
    for channel_name in channel_names:
        parsed = parsed_channels.get(channel_name)
        conditional = projected.get(channel_name)
        if parsed is None:
            assert conditional is not None
            column, rules = conditional
            channels[channel_name] = ResolvedStyleChannel(
                channel=channel_name,
                mode="conditional",
                data_field=column,
                rules=rules,
            )
            continue
        if conditional is None:
            channels[channel_name] = _resolve_style_channel_input(parsed)
            continue
        if not isinstance(parsed, _GradientChannelInput):
            column, _rules = conditional
            raise ValueError(
                f"chart.{channel_name} conflicts with conditional_formatting "
                f"rules on column '{column}'. Use one surface: "
                "per-channel encoding (scale/value/column) OR rule-driven "
                "conditional_formatting."
            )
        column, rules = conditional
        channels[channel_name] = ResolvedStyleChannel(
            channel=channel_name,
            mode="conditional",
            data_field=column,
            rules=rules,
            fallback_scale=parsed.scale,
        )

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


def validate_conditional_formatting_columns(
    chart: ConditionalFormattingChart,
    available_columns: set[str],
    *,
    is_pivot_table: bool,
) -> None:
    """Raise ValueError if conditional_formatting targets an unknown column.

    Skipped for pivot tables: post-pivot column names are runtime-determined
    (distinct values of the columns channel field), so the table renderer
    validates them against actual post-pivot data at render time.
    """
    cf = chart.conditional_formatting
    if not cf or is_pivot_table:
        return
    for column_name in cf:
        if available_columns and column_name not in available_columns:
            raise ValueError(
                f"conditional_formatting targets column '{column_name}' which is "
                f"not in the query result. Available: {sorted(available_columns)}"
            )


def kpi_support_rule_column(
    chart: Any,  # type-state: explicit_any — normalized or authored KpiChart
) -> str | None:
    """The column whose rules style the KPI support row, else ``None``.

    A column that also feeds the headline ``value`` stays a headline rule column.
    """
    support = chart.support
    if support is None or support.value is None or support.value == chart.value:
        return None
    return support.value


def _validate_support_rules(column: str, entry: FieldConditionalFormatting) -> None:
    """Raise on rule outputs the support row has no slot for."""
    for rule in entry.when:
        unsupported = (["background"] if rule.background is not None else []) + sorted(
            f"font.{key}"
            for key in (rule.font.model_dump(exclude_none=True) if rule.font else {})
            if key != "color"
        )
        if unsupported:
            raise ValueError(
                f"conditional_formatting rules on '{column}', the column feeding "
                f"`support.value`, style the support row, which supports only "
                f"font.color, glyph, glyph_color and tone. Remove: {unsupported}."
            )


def _project_conditional_formatting_inputs(
    chart: ConditionalFormattingChart,
    available_columns: set[str],
    *,
    is_pivot_table: bool = False,
    support_column: str | None = None,
) -> dict[str, tuple[str, tuple[ConditionalRule, ...]]]:
    """Return non-Resolved conditional outputs for final channel selection.

    Rules on ``support_column`` are excluded: render applies them to the KPI
    support row.
    """
    cf = chart.conditional_formatting
    if not cf:
        return {}

    chart_type = chart.type
    validate_conditional_formatting_columns(
        chart, available_columns, is_pivot_table=is_pivot_table
    )
    if support_column is not None and support_column in cf:
        _validate_support_rules(support_column, cf[support_column])
        cf = {c: e for c, e in cf.items() if c != support_column}

    outputs: dict[str, Callable[[ConditionalRule], str | None]]
    if chart_type == "kpi":
        for column, entry in cf.items():
            if any(r.glyph is not None or r.tone is not None for r in entry.when):
                raise ValueError(
                    f"conditional_formatting rules on '{column}' set glyph or tone. "
                    "The headline takes its arrow and tone from the chart-root "
                    "`good:`; hand-set glyph or tone rules belong on the column "
                    "feeding `support.value`."
                )
        outputs = {
            "background": lambda r: r.background,
            "color": lambda r: r.font.color if r.font is not None else None,
        }
    else:
        # table: lowered per-cell by the engine's table family, not here. Table is
        # the only other family whose authored model still declares
        # conditional_formatting, so it's the only one that can reach this
        # branch with a non-empty `cf`.
        return {}

    projected: dict[str, tuple[str, tuple[ConditionalRule, ...]]] = {}
    for channel_name, extractor in outputs.items():
        per_column = [
            (column, tuple(r for r in entry.when if extractor(r) is not None))
            for column, entry in cf.items()
        ]
        per_column = [(c, rs) for c, rs in per_column if rs]
        if not per_column:
            continue
        if len(per_column) > 1:
            raise ValueError(
                f"conditional_formatting on channel '{channel_name}' supports "
                "one column's rules per chart in v1 — got rules for: "
                f"{sorted(c for c, _ in per_column)}."
            )
        column, rules = per_column[0]
        projected[channel_name] = (column, rules)
    return projected
