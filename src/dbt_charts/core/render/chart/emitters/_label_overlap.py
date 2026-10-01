"""Resolve x-axis label visibility and angle at render time."""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisElementStyle,
    ResolvedAxisLabelOverlapConfig,
    ResolvedAxisStyle,
)
from dbt_charts.core.font_measure import FontMeasurer, get_font_measurer
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    SINGLE_ROW_CADENCE_GRAINS,
    TIME_PART_UNITS,
    cadence_label_text,
    day_week_context,
    detect_time_unit,
    enumerated_axis_values,
    is_label_opener,
    resolve_label_time_unit,
    resolve_temporal_label_visibility,
)
from dbt_charts.core.render.chart.type_inference import (
    DetectedTimeUnit,
    infer_vega_type_from_data,
    is_utc_safe_ordinal_date,
)
from dbt_charts.core.text.category_label import category_label_text
from dbt_charts.core.text.format_d3 import is_time_format, portable_strftime


def authored_time_format(axis: ResolvedAxisStyle) -> str | None:
    """``axis.labels.format`` when it is the text Vega will paint on a
    temporal axis, else ``None``.

    ``labels.format`` is a shared authoring surface for two grammars: a
    d3-time-format spec (``'%b %Y'``, what ``style.time_format`` resolves to)
    and a d3 *number* format. Only the first is a label vocabulary a date can
    be rendered through, so only the first may reach a measurement —
    ``is_time_format`` is the same gate ``build_cartesian_x_encoding`` uses to
    route it, and it filters out a predefined *native* name (Python-painted,
    never valid on an axis) for free. What the resulting format means for the
    measurement: ``cadence_label_text``.
    """
    fmt = axis.labels.format
    if fmt is None or not is_time_format(fmt):
        return None
    return fmt


class AxisLabelLayout(NamedTuple):
    """Render-local choices that never mutate the resolved axis style.

    ``label_block_height`` is how tall the label block is at ``angle``: the
    rotated bounding-box height of the widest label, read as one line of text
    (so ``labels.font.size`` when the labels sit flat, the widest label's own
    width at -90). It is what the tilt costs vertically, not a line-stack
    count — a two-row temporal label is the ``support_table.label_max_lines``
    reservation's business, not this field's. Consumed by
    ``render/chart/support_table_attachment.py`` to size the gap a
    ``position: bottom`` strip leaves for the labels: the angle is decided
    here, from data and width, long after compile baked that gap.

    ``collision_label_count`` is ``None`` whenever this module didn't measure
    a residual collision — either because the layout it picked (skip/tilt/
    coarsen) is known to fit, or because no measurement applies (overlap
    disabled, no x field/data, a quantitative axis). It is set to the size of
    the label set actually measured — after whatever skip-narrowing, temporal
    coarsen/anchor thinning, or parity halving that call site applied — only
    at the exact point a "no more strategies left" fallback still doesn't
    fit. Consumed by ``render/chart/axis_label_collision.py`` to record a
    warning fact — this module has no warning-domain knowledge of its own. A
    re-count over the raw input data, as opposed to the set actually
    measured, would misreport by whatever factor that site's narrowing
    applied.
    """

    label_overlap: Literal["allow", "parity"] | None
    angle: float | None
    visibility_time_unit: str | None
    anchor_index: int
    format_time_unit: str
    label_block_height: float
    collision_label_count: int | None = None


class TiltChoice(NamedTuple):
    """A rung off the tilt ladder: the angle, whether it fits, what it costs."""

    angle: float
    fits: bool
    block_height: float


AxisDatum = str | int | float | Decimal | datetime.date | datetime.datetime | None


# The grains `cadence_label_text` does not cover: `yearmonthdate`'s single-row
# generic form (the two-row `_day_label` shape is measured separately, at the
# call sites that actually get it) and the time-part units, whose vocabulary
# has no cadence ladder to share it with.
_GENERIC_FORMATTERS: dict[str, Callable[[datetime.datetime], str]] = {
    "yearmonthdate": lambda value: portable_strftime(value, "%-d %b"),
    "monthofyear": lambda value: value.strftime("%b"),
    "dayofweek": lambda value: value.strftime("%a"),
    "dayofmonth": lambda value: str(value.day),
    "dayofyear": lambda value: str(value.timetuple().tm_yday),
    "hourofday": lambda value: str(value.hour),
}


def _generic_temporal_labels(
    values: list[str],
    encoding_time_unit: str,
    format_time_unit: str,
    authored_format: str | None = None,
) -> list[str]:
    """The text each of ``values`` paints, one entry per band.

    A time-part encoding collapses many source values into one band (every
    January is the same ``monthofyear`` bucket), so the dedup below is what
    makes the returned list one-per-band. It keys on the *vocabulary* label
    rather than the returned text: under an ``authored_format`` every date
    formats differently, and deduping on that would hand the caller one
    "band" per source row.
    """
    if not format_time_unit and authored_format is None:
        return values
    parsed: list[datetime.datetime] = []
    for value in values:
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            parsed.append(datetime.datetime.fromisoformat(normalized))
        except ValueError:
            return (
                list(dict.fromkeys(values))
                if encoding_time_unit in TIME_PART_UNITS
                else values
            )

    vocabulary: list[str]
    if format_time_unit in SINGLE_ROW_CADENCE_GRAINS:
        vocabulary = [cadence_label_text(value, format_time_unit) for value in parsed]
    elif (formatter := _GENERIC_FORMATTERS.get(format_time_unit)) is not None:
        vocabulary = [formatter(value) for value in parsed]
    else:
        vocabulary = values
    if encoding_time_unit in TIME_PART_UNITS:
        bands: dict[str, datetime.datetime] = {}
        for label, moment in zip(vocabulary, parsed, strict=True):
            bands.setdefault(label, moment)
        vocabulary = list(bands)
        parsed = list(bands.values())
    if authored_format is not None:
        return [portable_strftime(value, authored_format) for value in parsed]
    return vocabulary


def _generic_layout(
    axis: ResolvedAxisStyle,
    overlap: ResolvedAxisLabelOverlapConfig,
    widths: list[float],
    usable_width: float,
) -> AxisLabelLayout:
    flat_height = axis.labels.font.size
    if _fits_flat(widths, usable_width):
        return AxisLabelLayout("allow", 0.0, None, 0, "", flat_height)

    directive: Literal["allow", "parity"] = "allow"
    considered_widths = widths
    if overlap.skip:
        directive = "parity"
        considered_widths = widths[::2]
        if _fits_flat(considered_widths, usable_width):
            return AxisLabelLayout(directive, 0.0, None, 0, "", flat_height)
    if overlap.tilt:
        tilt = _pick_tilt_for_widths(axis.labels, considered_widths, usable_width)
        return AxisLabelLayout(
            directive,
            tilt.angle,
            None,
            0,
            "",
            tilt.block_height,
            collision_label_count=None if tilt.fits else len(considered_widths),
        )
    # Reached only when flat and (if attempted) skip both failed, and tilt
    # is disabled — no strategy left to try, and it does not fit.
    return AxisLabelLayout(
        directive,
        0.0,
        None,
        0,
        "",
        flat_height,
        collision_label_count=len(considered_widths),
    )


def _ordinal_label_texts(values: list[str], authored_format: str | None) -> list[str]:
    """What an ordinal axis's bands paint.

    Date-shaped bucket strings ("2022-01") infer as ordinal, not temporal, so
    they never reach ``_temporal_layout`` — but ``build_cartesian_x_encoding``
    still routes an authored time format on a non-temporal axis into
    ``utcFormat(toDate(datum.value), fmt)``, which repaints those bands. This
    is the same "measure what Vega paints" rule as ``cadence_label_text``,
    reached by the other door: such an axis paints "Jan 2022" over a datum
    reading "2022-01".

    Only ``is_utc_safe_ordinal_date`` values are reformatted, matching
    exactly the bands that expression can parse. A nominal category or a
    non-ISO bucket label ("Q1 2025") paints "Invalid Date" there — a defect
    in the emission, not a width this function should invent a number for —
    so it keeps its own text, and all-nominal values make this a no-op.
    """
    if authored_format is None:
        return values
    return [
        portable_strftime(
            datetime.date.fromisoformat(value if len(value) > 7 else f"{value}-01"),
            authored_format,
        )
        if is_utc_safe_ordinal_date(value)
        else value
        for value in values
    ]


def _max_pair(widths: list[float]) -> float:
    if len(widths) == 1:
        return widths[0]
    return max((widths[i] + widths[i + 1]) / 2 for i in range(len(widths) - 1))


def _fits_flat(widths: list[float], usable_width: float) -> bool:
    if not widths:
        return True
    band = usable_width / len(widths)
    return _max_pair(widths) <= band


def _submonth_candidate_indices(
    dates: list[datetime.date], encoding_time_unit: str, format_time_unit: str
) -> list[int]:
    if encoding_time_unit == "yearmonthdate" and format_time_unit == "yearweek":
        return [i for i, date in enumerate(dates) if date.weekday() == 0]
    return list(range(len(dates)))


def _submonth_candidate_fits(
    dates: list[datetime.date],
    encoding_time_unit: str,
    format_time_unit: str,
    fiscal_year_start_month: int,
    measurer: FontMeasurer,
    font_size: float,
    usable_width: float,
) -> bool:
    indices = _submonth_candidate_indices(dates, encoding_time_unit, format_time_unit)
    if not indices:
        return False
    band = usable_width / len(dates)
    axis_config = get_chart_rendering().axis
    gaps = (
        axis_config.label_gap_spaces_numeric * measurer.measure(" ", font_size),
        axis_config.label_gap_spaces * measurer.measure(" ", font_size),
    )
    rows: tuple[list[tuple[int, str]], list[tuple[int, str]]] = ([], [])
    for position, index in enumerate(indices):
        date = dates[index]
        context = day_week_context(
            date, format_time_unit, position, fiscal_year_start_month
        )
        rows[0].append((index, str(date.day)))
        if context:
            rows[1].append((index, context))
    for row, gap in zip(rows, gaps, strict=True):
        for (left_i, left), (right_i, right) in zip(row, row[1:], strict=False):
            widths = (
                measurer.measure(left, font_size) + gap,
                measurer.measure(right, font_size) + gap,
            )
            if sum(widths) / 2 > (right_i - left_i) * band:
                return False
    return True


def _label_block_height(max_width: float, line_height: float, angle: float) -> float:
    """Height of the widest label's bounding box once rotated by ``angle``.

    The transpose of the footprint width ``_pick_tilt_for_widths`` fits against:
    a label sweeps into the vertical axis exactly as much as it leaves the
    horizontal one, so ``line_height`` flat and ``max_width`` at -90.
    ``max_width`` is the widest label as its caller measured it — the ladder
    measures band widths, which carry the inter-label gap, so the height it
    reports runs a space-width conservative at a steep tilt.
    """
    radians = math.radians(abs(angle))
    return max_width * math.sin(radians) + line_height * math.cos(radians)


def _axis_label_values(
    x_field: str,
    data: list[dict[str, AxisDatum]],
    domain_values: list[Any] | None,  # type-state: explicit_any — raw x values
) -> list[str]:
    """The axis's distinct band values, in first-seen order.

    ``domain_values`` wins when the overlay layers widened the scale past this
    axis's own rows — same precedence the crowding measurement uses.
    """
    return list(
        dict.fromkeys(
            str(v)
            for v in (
                domain_values
                if domain_values is not None
                else (
                    row[x_field]
                    for row in data
                    if x_field in row and row[x_field] is not None
                )
            )
        )
    )


class _Unresolved:
    """Sentinel: the caller has no ``resolve_cartesian_x_type`` verdict for this axis."""

    def __repr__(self) -> str:
        return "<unresolved>"


_UNRESOLVED_GRAIN = _Unresolved()


def _bucketed_grain(
    axis: ResolvedAxisStyle,
    values: list[str],
    resolved_time_unit: DetectedTimeUnit | _Unresolved = _UNRESOLVED_GRAIN,
) -> DetectedTimeUnit:
    """axis.time_unit-else-detected-grain precedence, for the pinned-angle path.

    An authored, bucketable ``axis.time_unit`` always wins outright — it is
    an instruction, not a guess. Otherwise, a caller that already has
    ``resolve_cartesian_x_type``'s own verdict for this axis must pass it as
    ``resolved_time_unit`` rather than let this function re-detect from
    ``values``: that verdict is gated by the ordinal-scaffold budget (dropped
    to ``None`` once a grain would outrun it), which a fresh
    ``detect_time_unit`` call has no way to see, so re-detecting here could
    "find" a grain the axis itself no longer carries. A caller with no such
    verdict yet falls back to detecting from the raw values — the same
    expression ``_temporal_layout`` still spells out inline for its own,
    unrelated auto-tilt-picking path.
    """
    if axis.time_unit in BUCKETED_CALENDAR_UNITS | TIME_PART_UNITS:
        return axis.time_unit
    if not isinstance(resolved_time_unit, _Unresolved):
        return resolved_time_unit
    return detect_time_unit(values)


def _pinned_angle_block_height(
    axis: ResolvedAxisStyle,
    x_field: str | None,
    data: list[dict[str, AxisDatum]],
    domain_values: list[Any] | None,  # type-state: explicit_any — raw x values
    resolved_time_unit: DetectedTimeUnit | _Unresolved = _UNRESOLVED_GRAIN,
) -> float:
    """Label-block height for an angle this module did not pick.

    ``resolve_axis_x_overlap``'s two short-circuits — an authored
    ``labels.angle``, and ``labels.overlap`` switched off — skip the tilt
    ladder, so nothing measures their labels. A consumer sizing the space
    under the axis needs an author-pinned tilt's block just as much as a
    picked one. Measures the same strings the ladder would: the formatted
    vocabulary on a bucketed temporal axis, the authored format's own text
    on a date-shaped ordinal band (``_ordinal_label_texts``), and the band
    values themselves otherwise. One band per row is what makes those
    measurable.

    ``resolved_time_unit`` is ``resolve_cartesian_x_type``'s own verdict for
    this axis, threaded in by the caller — see ``_bucketed_grain``.
    """
    font = axis.labels.font
    angle = axis.labels.angle
    if not angle or not x_field or not data:
        return font.size
    raw_type = infer_vega_type_from_data(data, x_field)
    if raw_type == "quantitative":
        # Vega's own ticks, at Vega's own number format: neither the values
        # nor their count are the rows'. Reporting the flat line leaves a
        # pinned tilt on a quantitative axis unreserved — a known gap, not a
        # measurement the rows' own digits could stand in for.
        return font.size
    values = _axis_label_values(x_field, data, domain_values)
    if not values:
        return font.size
    authored_format = authored_time_format(axis)
    if raw_type != "temporal":
        # The ordinal door: date-shaped bucket strings ("2022-01") infer
        # ordinal, and `build_cartesian_x_encoding` repaints exactly those
        # bands through the authored format — see `_ordinal_label_texts`.
        values = _ordinal_label_texts(values, authored_format)
    else:
        encoding_time_unit = _bucketed_grain(axis, values, resolved_time_unit)
        if encoding_time_unit is not None:
            format_time_unit = resolve_label_time_unit(
                encoding_time_unit, axis.labels.time_unit
            )
            values = _generic_temporal_labels(
                sorted(values),
                encoding_time_unit,
                format_time_unit if format_time_unit is not None else "",
                authored_format,
            )
        # No bucketed grain (a sub-daily timestamp, spacing no cadence
        # explains, or resolve_cartesian_x_type's own scaffold-budget gate
        # dropped it): the axis goes continuous and Vega labels its own
        # ticks — a clock vocabulary from default_subday_label_expr_for
        # ("12:30am", ":30", "Midnight") whose text depends on tick count,
        # card width and any authored domain, none of which reach this
        # module. An authored labels.format DOES reach it, and wins outright
        # (Vega applies it to whatever ticks it generates); with none, the
        # datum's own text stands in — a deliberate over-reservation, usually
        # by some way: the strip clears the labels at the cost of an empty
        # band.
        elif authored_format is not None:
            values = _generic_temporal_labels(sorted(values), "", "", authored_format)
    if axis.labels.expr is None:
        label_format = axis.labels.format if raw_type != "temporal" else None
        values = [category_label_text(v, label_format, font.case) for v in values]
    measurer = get_font_measurer(font.family)
    max_width = max(measurer.measure(value, font.size) for value in values)
    return _label_block_height(max_width, font.size, angle)


def _pick_tilt_for_widths(
    label: ResolvedAxisElementStyle,
    widths: list[float],
    usable_width: float,
) -> TiltChoice:
    """Shallowest ladder angle whose rotated footprint fits one band.

    Footprint is the rotated label's bounding-box width, ``w*cos(t) +
    line_height*sin(t)``, which does NOT decrease monotonically down the
    ladder. A rung is narrower than every shallower one only for labels wider
    than ``line_height * cot(t/2)`` — at the theme's 11px labels that is
    w > 19px for -60, > 27px for -45, > 41px for -30. Below those widths a mild
    tilt genuinely occupies more horizontal room than flat text ("Apr" rotated
    30 degrees is wider than "Apr" sitting flat), so a short temporal
    vocabulary steps straight from flat to vertical. That is the geometry, not
    a dead rung: a `2015`/`W07` axis at a 22-24px band does pick -60.

    Walking the ladder in order stays optimal even where the sequence widens.
    A rung is only reached once every shallower rung has failed, so a rung no
    narrower than the narrowest of those failures cannot fit either — skipping
    such rungs early would change no result, only the comparison count.
    """
    increments = label.tilt_increments
    if increments is None:
        raise ValueError("label.tilt_increments is not baked in theme")
    line_height = label.font.size
    if not increments or not widths:
        return TiltChoice(0.0, True, line_height)
    band = usable_width / len(widths)
    max_width = max(widths)
    for angle in increments:
        radians = math.radians(abs(angle))
        footprint = max_width * math.cos(radians) + line_height * math.sin(radians)
        if footprint <= band:
            return TiltChoice(
                float(angle), True, _label_block_height(max_width, line_height, angle)
            )
    last = float(increments[-1])
    return TiltChoice(last, False, _label_block_height(max_width, line_height, last))


def _temporal_layout(
    axis: ResolvedAxisStyle,
    overlap: ResolvedAxisLabelOverlapConfig,
    values: list[str],
    label_usable_ratio: float,
    chart_width: float,
    bucket_aligned_temporal: bool,
    edge_labels_flushed: bool,
    continuous_temporal: bool,
) -> AxisLabelLayout:
    supported_time_units = BUCKETED_CALENDAR_UNITS | TIME_PART_UNITS
    encoding_time_unit = (
        axis.time_unit
        if axis.time_unit in supported_time_units
        else detect_time_unit(values)
    )
    if encoding_time_unit is None:
        # No custom thinning was computed for this cadence (e.g. a 28-day
        # gap detect_time_unit refuses to call "weekly"). axis_to_vl maps
        # "allow" to labelOverlap=False and None to omitted (VL's own
        # adaptive default) — so skip=False (never drop a label) must
        # resolve to "allow", everything else to None.
        return AxisLabelLayout(
            None if overlap.skip else "allow",
            0.0,
            None,
            0,
            "",
            axis.labels.font.size,
        )

    font = axis.labels.font
    measurer = get_font_measurer(font.family)
    usable_width = chart_width * label_usable_ratio
    format_time_unit = resolve_label_time_unit(
        encoding_time_unit, axis.labels.time_unit
    )
    # An authored format overrides every vocabulary below — `cadence_label_text`.
    authored_format = authored_time_format(axis)
    # A continuous temporal scale spaces and ticks by calendar, not by row:
    # quiet days with no row still take a band and can still open a month, so
    # measure the span Vega draws. An ordinal axis has one band per row, and
    # a month opener with no row there has nothing to label.
    dates = (
        [
            datetime.date.fromisoformat(str(value)[:10])
            for value in enumerated_axis_values(
                values, encoding_time_unit, axis.fiscal_year_start_month
            )
        ]
        if continuous_temporal and encoding_time_unit in BUCKETED_CALENDAR_UNITS
        else [datetime.date.fromisoformat(value[:10]) for value in values]
    )
    month_openers = (
        [
            date
            for date in dates
            if is_label_opener(
                date,
                encoding_time_unit,
                "yearmonth",
                axis.fiscal_year_start_month,
            )
        ]
        if encoding_time_unit in {"yearweek", "yearmonthdate"}
        else []
    )
    # The two sub-month branches below both model the stacked day/week shape
    # `_day_label` paints. An authored format replaces that shape with one row
    # of its own text, so neither branch describes the axis any more; fall
    # through to the single-row ladder instead.
    if (
        encoding_time_unit in {"yearweek", "yearmonthdate"}
        and axis.labels.time_unit in (None, "auto")
        and (
            bucket_aligned_temporal
            or (continuous_temporal and encoding_time_unit == "yearmonthdate")
        )
        and authored_format is None
    ):
        candidates = (
            ("yearmonthdate", "yearweek")
            if encoding_time_unit == "yearmonthdate"
            else ("yearweek",)
        )
        for candidate in candidates:
            if _submonth_candidate_fits(
                dates,
                encoding_time_unit,
                candidate,
                axis.fiscal_year_start_month,
                measurer,
                font.size,
                usable_width,
            ):
                visibility = candidate if candidate != encoding_time_unit else None
                indices = _submonth_candidate_indices(
                    dates, encoding_time_unit, candidate
                )
                return AxisLabelLayout(
                    "allow",
                    0.0,
                    visibility,
                    indices[0],
                    candidate,
                    font.size,
                )
        if len(month_openers) >= 2:
            format_time_unit = "yearmonth"
        else:
            candidate = candidates[-1]
            indices = _submonth_candidate_indices(dates, encoding_time_unit, candidate)
            widths = [
                measurer.measure(str(dates[index].day), font.size) for index in indices
            ]
            tilt = (
                _pick_tilt_for_widths(axis.labels, widths, usable_width)
                if overlap.tilt
                else TiltChoice(0.0, _fits_flat(widths, usable_width), font.size)
            )
            return AxisLabelLayout(
                "allow",
                tilt.angle,
                candidate if candidate != encoding_time_unit else None,
                indices[0],
                candidate,
                tilt.block_height,
                collision_label_count=None if tilt.fits else len(widths),
            )
    elif (
        encoding_time_unit in {"yearweek", "yearmonthdate"}
        and axis.labels.time_unit in (None, "auto")
        and authored_format is None
    ):
        gap = get_chart_rendering().axis.label_gap_spaces * measurer.measure(
            " ", font.size
        )
        if encoding_time_unit == "yearmonthdate":
            # _day_label renders two rows ("%-d" over a possibly-blank
            # month/year row); measure that shape directly rather than the
            # single-row _generic_temporal_labels vocabulary, which is wider
            # than what's drawn and would wrongly promote away from daily
            # labels that actually fit.
            native_widths = [
                max(
                    measurer.measure(str(date.day), font.size),
                    measurer.measure(
                        day_week_context(
                            date,
                            encoding_time_unit,
                            position,
                            axis.fiscal_year_start_month,
                        ),
                        font.size,
                    ),
                )
                + gap
                for position, date in enumerate(dates)
            ]
        else:
            native_labels = _generic_temporal_labels(
                values, encoding_time_unit, encoding_time_unit
            )
            native_widths = [
                measurer.measure(value, font.size) + gap for value in native_labels
            ]
        native_fits = _fits_flat(native_widths, usable_width)
        if len(month_openers) < 2 or (
            encoding_time_unit == "yearmonthdate" and native_fits
        ):
            return _generic_layout(axis, overlap, native_widths, usable_width)._replace(
                format_time_unit=encoding_time_unit
            )
        format_time_unit = "yearmonth"

    if (
        encoding_time_unit not in BUCKETED_CALENDAR_UNITS
        or format_time_unit not in BUCKETED_CALENDAR_UNITS
    ):
        if format_time_unit is None:
            format_time_unit = (
                encoding_time_unit if encoding_time_unit in TIME_PART_UNITS else ""
            )
        labels = _generic_temporal_labels(
            values,
            encoding_time_unit,
            format_time_unit,
            authored_format,
        )
        widths = [measurer.measure(value, font.size) for value in labels]
        gap = get_chart_rendering().axis.label_gap_spaces * measurer.measure(
            " ", font.size
        )
        return _generic_layout(
            axis, overlap, [width + gap for width in widths], usable_width
        )._replace(format_time_unit=format_time_unit)

    band = usable_width / len(dates)
    visibility, fits = resolve_temporal_label_visibility(
        dates,
        encoding_time_unit,
        format_time_unit,
        measurer,
        font.size,
        band,
        axis.fiscal_year_start_month,
        allow_skip=overlap.skip,
        edge_labels_flushed=edge_labels_flushed,
        authored_format=authored_format,
    )
    visible_indices = [
        i
        for i, date in enumerate(dates)
        if is_label_opener(
            date,
            encoding_time_unit,
            visibility,
            axis.fiscal_year_start_month,
        )
    ]
    anchor_index = visible_indices[0] if visible_indices else 0
    # At year cadence the label vocabulary promotes to the bare year (every
    # visible tick is a January, so "Jan" repeated at every tick carries no
    # information) — see resolve_temporal_label_visibility's docstring. Every
    # other rung keeps the caller's own vocabulary unchanged. The promotion is
    # the smart labelExpr's, so an authored format (which suppresses that
    # expression) keeps its own text at year cadence too — `cadence_label_text`
    # short-circuits, and this stays the vocabulary the EMITTER is told about.
    promoted_format_time_unit = "year" if visibility == "year" else format_time_unit
    if fits:
        return AxisLabelLayout(
            "allow", 0.0, visibility, anchor_index, promoted_format_time_unit, font.size
        )

    visible_dates = [dates[i] for i in visible_indices]
    directive: Literal["allow", "parity"] = "allow"
    narrowed_further = False
    if overlap.skip and visibility == "year":
        directive = "parity"
        visible_dates = visible_dates[::2]
        narrowed_further = True
    if promoted_format_time_unit == "yearmonthdate" and authored_format is None:
        # _day_label renders two rows ("%-d" over a possibly-blank month/year
        # row) — measure that shape, not a single-row string nothing draws.
        widths = [
            max(
                measurer.measure(str(date.day), font.size),
                measurer.measure(
                    day_week_context(
                        date,
                        promoted_format_time_unit,
                        position,
                        axis.fiscal_year_start_month,
                    ),
                    font.size,
                ),
            )
            for position, date in enumerate(visible_dates)
        ]
    else:
        widths = [
            measurer.measure(
                cadence_label_text(date, promoted_format_time_unit, authored_format),
                font.size,
            )
            for date in visible_dates
        ]
    if overlap.tilt:
        tilt = _pick_tilt_for_widths(axis.labels, widths, usable_width)
        return AxisLabelLayout(
            directive,
            tilt.angle,
            visibility,
            anchor_index,
            promoted_format_time_unit,
            tilt.block_height,
            collision_label_count=None if tilt.fits else len(widths),
        )
    # No tilt to try. Absent further narrowing, `widths` is the exact set
    # `resolve_temporal_label_visibility` already judged not-fit via its
    # flush-edge-aware pairwise check above — re-deriving with `_fits_flat`'s
    # coarser uniform-band average could disagree and silently overwrite a
    # real collision. Only the year-cadence parity skip produces a set that
    # was never checked and needs a fresh verdict.
    fits = _fits_flat(widths, usable_width) if narrowed_further else False
    return AxisLabelLayout(
        directive,
        0.0,
        visibility,
        anchor_index,
        promoted_format_time_unit,
        font.size,
        collision_label_count=None if fits else len(widths),
    )


def _defer_thinning_when_uncertain(
    layout: AxisLabelLayout, overlap: ResolvedAxisLabelOverlapConfig
) -> AxisLabelLayout:
    """Stop forbidding Vega to thin a TEMPORAL axis this module couldn't fit.

    ``"allow"`` becomes VL ``labelOverlap: false`` — an instruction NOT to
    remove overlapping labels, which is only earned by a prediction that
    holds. ``collision_label_count`` is set exactly where one didn't: every
    enabled strategy was tried and the labels still collide. Dropping the
    directive to ``None`` (VL's own per-scale adaptive default) spends the
    one fallback left — Vega measures the real painted text after layout,
    which is precisely the check a width prediction can get wrong — so a
    mispredicted width degrades to dropped labels rather than a pile-up. The
    warning still fires either way; this only decides what the reader sees.

    Omitting the key means "whatever Vega defaults to for this scale", which
    is adaptive removal only on a continuous time scale — on the band scale a
    bucketed bar or heatmap x resolves to, Vega already defaults to not
    thinning, so this changes nothing there.

    Temporal only — the discrete branch deliberately does not call this; see
    the comment at its own fallthrough for why. Two temporal layouts keep the
    assertion too: ``skip: false`` is the author forbidding dropped labels
    outright, and ``"parity"`` is already a thinning directive of its own.
    """
    if layout.collision_label_count is None or layout.label_overlap != "allow":
        return layout
    if not overlap.skip:
        return layout
    return layout._replace(label_overlap=None)


def resolve_axis_x_overlap(
    axis: ResolvedAxisStyle,
    x_field: str | None,
    data: list[dict[str, AxisDatum]],
    label_usable_ratio: float,
    *,
    is_horizontal_bar: bool = False,
    bucket_aligned_temporal: bool = True,
    edge_labels_flushed: bool,
    continuous_temporal: bool,
    chart_width: float,
    domain_values: list[Any] | None = None,
    resolved_time_unit: DetectedTimeUnit | _Unresolved = _UNRESOLVED_GRAIN,
) -> AxisLabelLayout:
    """Return render-local overlap, angle, and temporal visibility choices.

    ``domain_values`` is the x scale's full band domain when overlay layers
    extend it past the base series (``overlay_x_domain_values``). Crowding
    must be measured against the bands that actually render — measuring the
    base's own rows on a layered chart under-counts them and picks a flatter
    angle or finer cadence than the rendered axis has room for. Honored on
    both the temporal and the ordinal/nominal branch: the tilt angle below is
    picked from the same ``widths``/``usable_width`` the flat-fit gate just
    measured, never re-derived from ``data`` alone.

    ``resolved_time_unit`` is ``resolve_cartesian_x_type``'s own verdict for
    this axis (its third return value) — forwarded only to the two pinned-
    angle branches below (``_pinned_angle_block_height``), which measure a
    tilt this module did not pick and so cannot re-derive a grain that
    agrees with what the encoding actually resolved to. Unset falls back to
    detecting the grain from the raw values, unchanged from before this
    parameter existed.
    """
    flat_height = axis.labels.font.size
    overlap = axis.labels.overlap
    if overlap is None:
        return AxisLabelLayout(
            None,
            axis.labels.angle,
            None,
            0,
            "",
            _pinned_angle_block_height(
                axis, x_field, data, domain_values, resolved_time_unit
            ),
        )
    if axis.labels.angle is not None:
        return AxisLabelLayout(
            "allow",
            axis.labels.angle,
            None,
            0,
            "",
            _pinned_angle_block_height(
                axis, x_field, data, domain_values, resolved_time_unit
            ),
        )
    if is_horizontal_bar:
        return AxisLabelLayout("allow", 0.0, None, 0, "", flat_height)
    if not x_field or not data:
        return AxisLabelLayout("allow", 0.0, None, 0, "", flat_height)

    raw_type = infer_vega_type_from_data(data, x_field)
    if raw_type == "quantitative":
        return AxisLabelLayout("allow", 0.0, None, 0, "", flat_height)
    values = _axis_label_values(x_field, data, domain_values)
    if not values:
        return AxisLabelLayout("allow", 0.0, None, 0, "", flat_height)
    if raw_type == "temporal":
        return _defer_thinning_when_uncertain(
            _temporal_layout(
                axis,
                overlap,
                sorted(values),
                label_usable_ratio,
                chart_width,
                bucket_aligned_temporal,
                edge_labels_flushed,
                continuous_temporal,
            ),
            overlap,
        )

    font = axis.labels.font
    measurer = get_font_measurer(font.family)
    labels = _ordinal_label_texts(values, authored_time_format(axis))
    if axis.labels.expr is None:
        labels = [category_label_text(v, axis.labels.format, font.case) for v in labels]
    widths = [measurer.measure(value, font.size) for value in labels]
    gap = get_chart_rendering().axis.label_gap_spaces * measurer.measure(" ", font.size)
    widths = [width + gap for width in widths]
    usable_width = chart_width * label_usable_ratio
    flat_fits = _fits_flat(widths, usable_width)
    # No _defer_thinning_when_uncertain here. A dropped date label is
    # recoverable — a temporal axis is a ruler, and the reader reads the
    # missing tick off its neighbors. A dropped CATEGORY is not: nothing on a
    # discrete axis says what the unlabeled band was. So this branch keeps
    # forbidding Vega to thin even when its own labels collide.
    if overlap.tilt and not flat_fits:
        tilt = _pick_tilt_for_widths(axis.labels, widths, usable_width)
        return AxisLabelLayout(
            "allow",
            tilt.angle,
            None,
            0,
            "",
            tilt.block_height,
            collision_label_count=None if tilt.fits else len(widths),
        )
    return AxisLabelLayout(
        "allow",
        0.0,
        None,
        0,
        "",
        flat_height,
        collision_label_count=None if flat_fits else len(widths),
    )
