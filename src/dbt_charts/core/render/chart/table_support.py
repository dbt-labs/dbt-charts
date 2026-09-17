"""Shared table layout and style helpers for SVG rendering."""

from __future__ import annotations

import datetime
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeGuard, cast
from urllib.parse import quote_plus

from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.models.chart.authored import (
    match_predicate,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.resolved import ResolvedTableColumnConfig
from dbt_charts.core.compile.models.style.theme import (
    font_weight_as_css,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_TABLE_FORMAT_KIND_MISMATCH
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.chart.title_overflow import (
    TitleOverflowMode,
    prepare_title_text,
)
from dbt_charts.core.render.format_utils import (
    default_number_format,
    format_kpi_parts,
    format_value,
)
from dbt_charts.core.render.utils import resolve_tone_color, slug_to_text
from dbt_charts.core.text.case import CaseValue, apply_case
from dbt_charts.core.text.format_d3 import (
    NULL_DISPLAY,
    is_time_format,
    portable_strftime,
)
from dbt_charts.core.text.numeral_scale import decimal_pad_for
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_TIME_SPECS,
    PredefinedTimeFormat,
)
from dbt_charts.core.utils import coerce_numeric_cell, is_date_like
from mdsvg.fonts import FontMeasurer

# Swatch column geometry. The rect is 14px square (table.py _SWATCH_SIZE);
# _SWATCH_CELL_DEMAND adds 4px breathing room on each side so a swatch column's
# demand/floor reflects the painted rect, not the hex-color string the cell
# carries. Lives here (not in table.py) so the measurement helpers below can
# read it without a circular import.
_SWATCH_CELL_DEMAND = 18  # _SWATCH_SIZE (14) + 4px breathing room


if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.authored import (
        ConditionalRule,
        ScaleTargetConfig,
        TableColumnConfig,
    )
    from dbt_charts.core.compile.models.primitives import (
        FormatConfig,
        ResolvedScaleTarget,
    )
    from dbt_charts.core.compile.models.style.theme import (
        KpiTonesStyle,
        TableChartStyle,
    )

# Matches {{ col }} and {{ col | urlencode }}.
# Group 1: column name. Group 2: filter name (e.g. "urlencode"), or empty string.
_TEMPLATE_RE = re.compile(r"\{\{\s*(\w+)(?:\s*\|\s*(\w+))?\s*\}\}")


# Matches ISO date "YYYY-MM-DD", ISO datetime "YYYY-MM-DDTHH:MM:SS[...]",
# and Postgres-style space-separated timestamps "YYYY-MM-DD HH:MM:SS[...]".
# Does NOT match bare year strings like "2024" (those are numeric).
_ISO_TEMPORAL_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"  # date part: YYYY-MM-DD
    r"([T ]\d{2}:\d{2}:\d{2}(\.\d+)?"  # optional time (T or space separator)
    r"(Z|[+-]\d{2}:?\d{2})?)?$"  # optional TZ suffix
)

# Valid strftime/d3-time-format directive letters. Padding modifiers (-, _, 0)
# between % and the letter are allowed (e.g. %-d, %_d, %0H).
# %% is a literal-percent escape and is validated separately.
_VALID_STRFTIME_DIRECTIVES: frozenset[str] = frozenset(
    "aAbBcCdDeEfFgGhHIjklmMnOpPqrRsSTuUVwWxXyYzZ"
)

# Matches a % directive sequence: optional padding modifier + letter.
_STRFTIME_DIRECTIVE_RE = re.compile(r"%[-_0]?(.)")


def is_temporal_value(value: Any) -> bool:
    """Return True for Python date/datetime objects and ISO temporal strings.

    Covers:
    - datetime.date and datetime.datetime (including timezone-aware)
    - ISO strings "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS[...]"
    - Postgres-style space-separated timestamps "YYYY-MM-DD HH:MM:SS[...]"

    Does NOT match bare year strings like "2024" (those are numeric).
    TIME/INTERVAL values from DuckDB pass through str() — they are not
    date-like in the calendar sense and strftime would crash on them.

    Applies post-regex calendar validation so "2024-13-99" (regex-matching
    but logically invalid) returns False rather than causing a ValueError
    at format time.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (datetime.date, datetime.datetime)):
        return True
    if not isinstance(value, str) or len(value) < 10:
        return False
    stripped = value.strip()
    if not _ISO_TEMPORAL_RE.match(stripped):
        return False
    # Post-regex: validate the date part is a real calendar date.
    date_part = stripped[:10]
    try:
        datetime.date.fromisoformat(date_part)
    except ValueError:
        return False
    return True


def _validate_strftime_spec(format_spec: str) -> None:
    """Raise ValueError for format specs containing unknown % directives.

    Python's strftime silently passes unknown directives through (e.g. %Q → Q).
    We reject them explicitly so authors get a clear error rather than garbled output.

    ``%%`` (literal percent escape) is accepted and stripped before validation.
    """
    stripped = format_spec.replace("%%", "")
    for m in _STRFTIME_DIRECTIVE_RE.finditer(stripped):
        letter = m.group(1)
        if letter not in _VALID_STRFTIME_DIRECTIVES:
            raise ValueError(
                f"Invalid temporal format spec {format_spec!r}: "
                f"unknown directive %{letter!r}"
            )


def _raise_if_time_format_on_numeric(resolved: str) -> None:
    """Raise ``ChartDataError`` when a resolved format is a strftime spec.

    A strftime spec has no meaning against a numeric value; without this
    check, ``format_value``/``format_d3`` forwards it straight into the
    d3-format parser, which raises an opaque, uncoded parse error.
    """
    if is_time_format(resolved):
        raise ChartDataError.from_code(
            ERR_TABLE_FORMAT_KIND_MISMATCH,
            fmt=resolved,
            remedy=(
                "use a d3-format numeric spec (e.g. '.1f') or remove format: "
                "to use the theme default"
            ),
        )


def format_temporal_value(
    value: datetime.date | datetime.datetime | str,
    format_spec: str,
) -> str:
    """Apply a strftime format spec to a temporal value.

    Args:
        value: Python date/datetime or ISO temporal string.
        format_spec: strftime-style format string (e.g. "%-d %b %Y").

    Returns:
        Formatted date string.

    Raises:
        ValueError: If the format spec contains an invalid/unknown directive.
    """
    _validate_strftime_spec(format_spec)

    if isinstance(value, str):
        # Parse ISO string to a date/datetime so strftime works uniformly.
        # fromisoformat() on 3.10 doesn't handle the "Z" suffix — normalize it first.
        # Space-separated Postgres timestamps ("YYYY-MM-DD HH:MM:SS") are normalized
        # to ISO T-separator before parsing.
        stripped = value.strip().replace("Z", "+00:00")
        # Normalize space-separated timestamp to T-separator for fromisoformat().
        if len(stripped) > 10 and stripped[10] == " ":
            stripped = stripped[:10] + "T" + stripped[11:]
        if "T" in stripped:
            dt = datetime.datetime.fromisoformat(stripped)
            # A timestamp carries a time component -- keep it (portable_strftime
            # accepts date or datetime) so a time directive in format_spec sees
            # the real time, not midnight. Aware values still normalize to UTC.
            d: datetime.date | datetime.datetime = (
                dt.astimezone(datetime.timezone.utc) if dt.tzinfo is not None else dt
            )
        else:
            d = datetime.date.fromisoformat(stripped[:10])
    elif isinstance(value, datetime.datetime):
        # Aware datetime: convert to UTC first to match ISO string behavior.
        d = (
            value.astimezone(datetime.timezone.utc)
            if value.tzinfo is not None
            else value
        )
    else:
        d = value

    try:
        return portable_strftime(d, format_spec)
    except ValueError as e:
        raise ValueError(f"Invalid temporal format spec {format_spec!r}: {e}") from e


def parse_column_width(
    width_hint: int | str | None, available_width: float
) -> float | None:
    """Resolve a column width hint into pixels."""
    if width_hint is None:
        return None

    if isinstance(width_hint, int):
        return float(width_hint) if width_hint > 0 else None

    width_str = str(width_hint).strip()
    if not width_str:
        return None

    if width_str.endswith("%"):
        try:
            pct = float(width_str[:-1])
        except ValueError:
            return None
        return available_width * (pct / 100.0) if pct > 0 else None

    try:
        px = float(width_str)
    except ValueError:
        return None
    return px if px > 0 else None


def format_table_cell_value(
    value: Any,
    format_config: str | FormatConfig | None,
    formats: dict[str, str] | None = None,
) -> str:
    """Format a cell value for display.

    Temporal values (Python date/datetime objects and ISO timestamp strings)
    are formatted before numeric values so DuckDB-typed columns show
    human-readable dates without SQL-side strftime. The default format is the
    engine-predefined ``date_short`` spec from ``PREDEFINED_TIME_SPECS``.

    Explicit column ``format:`` strings recognized as strftime specs (see
    ``is_time_format``) are applied to temporal values directly. A format of
    the wrong kind -- a numeric d3 spec on a temporal value, or a strftime
    spec on a numeric value -- raises ``ChartDataError``.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return NULL_DISPLAY

    if isinstance(value, (list, tuple)):
        return f"[{len(value)} items]"

    if isinstance(value, bool):
        return str(value)

    # Temporal path: date/datetime objects and ISO strings — must come before
    # the numeric check because datetime.date is not int/float but authors
    # may still set a format: spec that should be treated as a time format.
    if is_temporal_value(value):
        if format_config is not None:
            resolved = resolve_format(format_config, formats)
            if is_time_format(resolved):
                # Author-provided strftime spec — raises ValueError for bad directives.
                return format_temporal_value(value, resolved)
            raise ChartDataError.from_code(
                ERR_TABLE_FORMAT_KIND_MISMATCH,
                fmt=resolved,
                remedy=(
                    "use a strftime spec (e.g. '%-d %b %Y') or remove format: "
                    "to use date_short"
                ),
            )
        date_format = PREDEFINED_TIME_SPECS[PredefinedTimeFormat.date_short]
        return format_temporal_value(value, date_format)

    if isinstance(value, (int, float)):
        if format_config is not None:
            resolved = resolve_format(format_config, formats)
            _raise_if_time_format_on_numeric(resolved)
            return format_value(value, format_config, formats)
        return format_value(value, default_number_format(), formats=formats)

    if isinstance(value, dict):
        return str(value)
    if format_config is not None:
        resolved = resolve_format(format_config, formats)
        try:
            numeric = float(value)
        except (ValueError, TypeError):
            pass
        else:
            # A date-like numeric string (e.g. a bare year "2024" in an
            # otherwise-temporal column) is not the mismatch this guards
            # against -- render it like any other date-like cell instead of
            # feeding a strftime spec into the numeric formatter.
            if is_date_like(value) and is_time_format(resolved):
                return str(value)
            _raise_if_time_format_on_numeric(resolved)
            try:
                return format_value(numeric, format_config, formats)
            except (ValueError, TypeError):
                pass
    # No explicit format: a numeric-looking string (CSV/warehouse adapters
    # deliver numbers as strings) gets the theme default. Date-like strings
    # (e.g. a bare year "2024") and genuinely non-numeric strings pass through
    # verbatim -- the same values the numeric-lane branch excludes upstream.
    if is_date_like(value):
        return str(value)
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return str(value)
    return format_value(numeric, default_number_format(), formats)


def resolve_cell_link(
    link: str,
    row: dict[str, Any],
    columns: list[str],
) -> str | None:
    """Resolve a cell link value for a single row.

    Handles two Jinja-style template forms:
      ``{{ col }}``            — substitute raw string value.
      ``{{ col | urlencode }}`` — substitute URL-encoded value (for FK drill links
                                  where FK values may be strings with special chars).
    """

    if link in columns:
        val = row.get(link)
        return str(val) if val is not None else None

    if _TEMPLATE_RE.search(link):
        has_none = False

        def _sub(m: re.Match[str]) -> str:
            nonlocal has_none
            col_name = m.group(1)
            filter_name = m.group(2)
            val = row.get(col_name)
            if val is None:
                has_none = True
                return ""
            raw = str(val)
            if filter_name == "urlencode":
                return quote_plus(raw)
            if filter_name is not None:
                raise ValueError(
                    f"Unknown link template filter: {filter_name!r}. "
                    "Only 'urlencode' is supported."
                )
            return raw

        resolved = _TEMPLATE_RE.sub(_sub, link)
        if has_none:
            return None
        return resolved or None

    return link


def resolve_cell_link_with_board(
    link: str,
    row: dict[str, Any],
    columns: list[str],
) -> str | None:
    """Resolve a cell link and apply board-path rewriting if link context is set."""
    resolved = resolve_cell_link(link, row, columns)
    if resolved is None:
        return None
    ctx = get_link_context()
    if ctx is None:
        return resolved
    return resolve_href(resolved, ctx)


def resolve_table_style_value(
    spec: str | None,
    row: dict[str, Any],
) -> str | None:
    """Resolve a table style override from a literal or per-row field ref."""
    if spec is None:
        return None
    if spec in row:
        value = row.get(spec)
        return None if value in (None, "") else str(value)
    return spec


# ---------------------------------------------------------------------------
# Row typing — per-row semantic roles (value / summary / total)
# ---------------------------------------------------------------------------

_VALID_ROW_ROLES = frozenset({"value", "summary", "total"})


def resolve_row_role(row_role_spec: str | None, row: dict[str, Any]) -> str:
    """Resolve the row's semantic role using column-ID-first resolution.

    Returns one of: "value" (default), "summary", "total".
    Unknown values fall back to "value".

    The row-role signal comes from the query layer (ADR-010: data and its
    meaning belong to the query). dbt charts does not auto-detect summary rows
    from content; the query must emit a column whose per-row value is
    the role.
    """
    if row_role_spec is None:
        return "value"
    raw = resolve_table_style_value(row_role_spec, row)
    if raw is None or raw == "":
        return "value"
    normalized = raw.strip().lower()
    if normalized in _VALID_ROW_ROLES:
        return normalized
    return "value"


def is_summary_role(role: str) -> bool:
    """Return True if the role is summary or total (all summary rows)."""
    return role in ("summary", "total")


def is_total_role(role: str) -> bool:
    """Return True only for total rows (which get double-rule treatment)."""
    return role == "total"


def _apply_rule_outputs(
    rule: ConditionalRule,
    result: dict[str, Any],
    tones: KpiTonesStyle | None = None,
) -> None:
    """Merge a rule's style outputs into ``result`` (last-match-wins)."""
    if rule.background is not None:
        result["background"] = rule.background
    if rule.font is not None:
        f = rule.font
        if f.color is not None:
            result["color"] = f.color
        if f.weight is not None:
            result["weight"] = font_weight_as_css(f.weight)
        if f.style is not None:
            result["style"] = f.style
        if f.decoration is not None:
            result["decoration"] = f.decoration
    if rule.glyph is not None:
        result["glyph"] = rule.glyph
        # A later rule that swaps the glyph without specifying its color
        # must not inherit the previous rule's color — clear it so the
        # cell falls back to default ink.
        result.pop("glyph_color", None)
    if rule.glyph_color is not None:
        result["glyph_color"] = rule.glyph_color
    elif rule.tone is not None and tones is not None:
        # tones is None on the two callers that don't consume glyph_color:
        # the cell background/color path and resolve_cell_glyph (which discards
        # the color, keeping only the glyph char). The real cell-glyph render
        # path threads tones. So None here is a reachable, by-design skip.
        result["glyph_color"] = resolve_tone_color(rule.tone, tones)


def resolve_conditional_styles(
    when_rules: Sequence[ConditionalRule] | None,
    value: Any,
    tones: KpiTonesStyle | None = None,
) -> dict[str, Any]:
    """Evaluate a list of ``when`` rules and return merged style overrides.

    Non-default rules are evaluated in list order; all matching rules
    contribute their style keys with last-match-wins semantics.

    A trailing ``default: true`` rule is a catch-all — it only applies when
    no non-default rule matched. This lets authors write
    ``[{lt:0, ...}, {gt:100, ...}, {default: true, ...}]`` and have the
    default fire only for rows in (0, 100]. Position of the default rule is
    validated on FieldConditionalFormatting; evaluation here is tolerant of
    ordering.

    Keys: ``background`` (str), ``color`` (str), ``weight`` (str),
    ``style`` (str), ``decoration`` (str).
    """
    if not when_rules:
        return {}

    result: dict[str, Any] = {}
    matched_any = False
    default_rule: ConditionalRule | None = None
    for rule in when_rules:
        if rule.default is True:
            default_rule = rule
            continue
        if match_predicate(rule, value):
            matched_any = True
            _apply_rule_outputs(rule, result, tones)
    if not matched_any and default_rule is not None:
        _apply_rule_outputs(default_rule, result, tones)
    return result


def resolve_header_overflow(
    table_config: TableChartStyle,
    promoted: str | None = None,
) -> TitleOverflowMode:
    """Resolve the effective table-header overflow mode.

    Precedence (highest first):
      1. ``promoted`` — ResolvedChart.header_overflow (the authored Patch
         value lifted to a first-class field at resolve time).
      2. ``table_config.header_overflow`` — board-level TableChartStyle override.
      3. ``table_config.header.overflow`` — the theme value, always populated.

    Column headers deliberately do not inherit the *chart title's* overflow:
    ``measure_column_demands`` treats header width as a secondary demand on the
    assumption that a too-wide header wraps rather than truncates.
    """
    if promoted in {"clip", "truncate", "wrap-two", "wrap"}:
        return cast(Literal["clip", "truncate", "wrap-two", "wrap"], promoted)
    if table_config.header_overflow is not None:
        return table_config.header_overflow
    return table_config.header.overflow


# Compact-column classifier threshold.  Any auto-column whose measured demand
# falls at or below this value is pinned to that demand rather than joining the
# proportional text pool.  ~220px corresponds to roughly 30 characters at the
# default 13px body font — enough for short enums, numbers, and dates.
_COMPACT_DEMAND_CEILING: float = 220.0

# URL prefix filter for min-word floor measurement.
_URL_PREFIX_RE = re.compile(r"^https?://")

# Max token length included in min-word floor (tokens longer than this are
# opaque IDs / base64 chunks and would force an unreachable floor).
_MAX_FLOOR_TOKEN_LEN = 40


def _candidate_tokens(text: str) -> Iterator[str]:
    """Whitespace-delimited tokens eligible to set a min-word floor.

    Skips URLs and tokens longer than ``_MAX_FLOOR_TOKEN_LEN`` — opaque IDs
    and base64 chunks would force a floor no column could reach.
    """
    for token in text.split():
        if _URL_PREFIX_RE.match(token):
            continue
        if len(token) > _MAX_FLOOR_TOKEN_LEN:
            continue
        yield token


def _widest_token_width(text: str, measurer: FontMeasurer, font_size: float) -> float:
    """Return the pixel width of ``text``'s widest eligible token."""
    return max(
        (measurer.measure(token, font_size) for token in _candidate_tokens(text)),
        default=0.0,
    )


def _measure_min_word_width(
    values: list[Any],
    header_label: str,
    measurer: FontMeasurer,
    font_size: float,
    header_font_size: float,
) -> float:
    """Return the pixel width of the widest eligible token across the column.

    Searches ``values`` (sampled cell strings) and the ``header_label``.

    Cell tokens are measured at ``font_size`` and the header label's at
    ``header_font_size``: a theme with larger headers would otherwise get a
    floor narrower than the header word it exists to keep whole.

    This gives the minimum column width that avoids mid-word line breaks.
    """
    return max(
        _widest_token_width(header_label, measurer, header_font_size),
        max(
            (_widest_token_width(str(val), measurer, font_size) for val in values),
            default=0.0,
        ),
    )


def _classify_columns(
    demands: dict[str, float],
    column_configs: Mapping[str, TableColumnConfig],
) -> tuple[set[str], set[str]]:
    """Classify auto-columns as compact or text.

    Compact columns are pinned to their measured demand; text columns compete
    proportionally for the remaining budget.

    A column is compact when:
      - Its demand <= ``_COMPACT_DEMAND_CEILING``, OR
      - It has a spark config (spark columns own their own width).

    Returns ``(compact_keys, text_keys)``.
    """
    from dbt_charts.core.compile.models.chart.authored import SparkConfig

    compact: set[str] = set()
    text: set[str] = set()
    for col, demand in demands.items():
        cfg = column_configs.get(col)
        has_spark = cfg is not None and isinstance(cfg.spark, SparkConfig)
        if has_spark or demand <= _COMPACT_DEMAND_CEILING:
            compact.add(col)
        else:
            text.add(col)
    return compact, text


# Anti-pathological cap: no single auto-column may claim more than this
# fraction of the auto-column budget in proportional allocation.
_PROPORTIONAL_CAP = 0.6


def _proportional_allocate(
    demands: dict[str, float], budget: float
) -> dict[str, float]:
    """Allocate budget to columns proportionally to their demands.

    Single-column: always gets the full budget.
    Multi-column: iteratively caps any column at ``_PROPORTIONAL_CAP * budget``
    and redistributes the excess among the uncapped columns until stable.
    Budget is always fully consumed.
    """
    if len(demands) <= 1:
        col = next(iter(demands)) if demands else None
        return {col: budget} if col is not None else {}

    cap = budget * _PROPORTIONAL_CAP
    active = dict(demands)
    fixed: dict[str, float] = {}
    remaining = budget

    while active:
        total = sum(active.values())
        if total <= 0:
            per_col = remaining / len(active)
            fixed.update(dict.fromkeys(active, per_col))
            break

        to_fix = {
            col: cap for col, d in active.items() if (d / total) * remaining > cap
        }
        if not to_fix:
            for col, d in active.items():
                fixed[col] = (d / total) * remaining
            break

        fixed.update(to_fix)
        remaining -= sum(to_fix.values())
        for col in to_fix:
            del active[col]

    return fixed


def _cluster_and_equalize_widths(
    widths: dict[str, float], budget: float, threshold: float
) -> dict[str, float]:
    """Snap near-equal widths to a shared value, then rescale to budget.

    Sorted walk: columns sorted ascending, clusters grow while
    cluster_min / new_width >= threshold. This is the cluster extremes check —
    not adjacent-pair — so 100/110/120/130/140/150 does NOT collapse into one
    cluster even though each adjacent ratio is >= 0.8. Each cluster snaps to
    its max. A final uniform rescale restores the budget, preserving
    within-cluster equality.

    threshold=1.0 means only exactly-equal widths cluster (effectively disabled).
    threshold=0.0 means all widths cluster (force-equal).
    """
    if len(widths) <= 1:
        return dict(widths)

    sorted_items = sorted(widths.items(), key=lambda kv: kv[1])

    clusters: list[list[str]] = []
    cluster_cols: list[str] = []
    cluster_min = sorted_items[0][1]

    for col, w in sorted_items:
        if cluster_cols and cluster_min / (w + 1e-9) < threshold:
            clusters.append(cluster_cols)
            cluster_cols = [col]
            cluster_min = w
        else:
            cluster_cols.append(col)
    clusters.append(cluster_cols)

    snapped: dict[str, float] = {}
    col_to_w = dict(widths)
    for cluster in clusters:
        cluster_max = max(col_to_w[c] for c in cluster)
        for c in cluster:
            snapped[c] = cluster_max

    total = sum(snapped.values())
    if total > 0:
        scale = budget / total
        return {c: v * scale for c, v in snapped.items()}
    return snapped


def _glyph_possible(
    column_configs: Mapping[str, TableColumnConfig],
    column_when_rules: Mapping[str, Sequence[ConditionalRule]],
    col: str,
) -> bool:
    """Whether any cell in ``col`` can paint a glyph.

    Cheap gate ahead of the per-cell ``cell_glyph_run`` call, which otherwise
    evaluates every rule against every sampled value.
    """
    cfg = column_configs.get(col)
    return bool(column_when_rules.get(col)) or (
        cfg is not None and cfg.glyph is not None
    )


def _is_padded_numeric(
    v: Any,  # type-state: explicit_any — arbitrary warehouse cell value
) -> TypeGuard[int | float]:
    """Whether a cell value is one the decimal pad applies to.

    Mirrors both paint sites (`table.py`'s row-paint and lane-position loops),
    which gate the pad on this shape. `bool` is an `int` subclass and is
    excluded there too; `Decimal` is excluded because paint excludes it, even
    though the bake reaches it via `coerce_numeric_cell`.

    A `TypeGuard` rather than a plain `bool` so the numeric narrowing reaches
    `format_kpi_parts`, whose signature takes `int | float | None`.
    """
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def measure_column_demands(
    columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    data: list[dict[str, Any]],
    measurer: FontMeasurer,
    *,
    font_size: float,
    header_font_size: float,
    header_case: CaseValue = "none",
    cell_pad: int,
    max_sample_rows: int = 50,
    formats: dict[str, str] | None = None,
    header_visible: bool = True,
    column_when_rules: Mapping[str, Sequence[ConditionalRule]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Measure cell-content demands and header demands per column.

    Returns ``(cell_demands, header_demands)`` where:
    - ``cell_demands[col]`` = p95 cell-content width + ``cell_pad * 2``.
      This drives compact/text classification and budget allocation.
    - ``header_demands[col]`` = header label pixel width (0 when header hidden).
      Callers pass this to ``calculate_column_layout`` via ``header_demands``
      so spare budget can grow columns toward their header width.

    Cell content is primary — columns size to their values.  Headers are
    secondary — they fill in only when budget remains.  Long headers wrap
    or clip (``DEFAULT_TITLE_OVERFLOW = "wrap-two"``); they no longer steal
    budget from value columns.

    ``column_when_rules`` carries the chart's ``conditional_formatting`` rules
    so a cell that paints a glyph is measured with it (``cell_glyph_run``).

    Swatch columns are pinned to ``_SWATCH_CELL_DEMAND`` in both dicts;
    their cell value is a color string, not rendered as text.
    """
    cell_demands: dict[str, float] = {}
    header_demands: dict[str, float] = {}
    sample = data[:max_sample_rows]

    for col in columns:
        col_config = column_configs.get(col)
        if col_config and col_config.swatch:
            cell_demands[col] = _SWATCH_CELL_DEMAND + cell_pad * 2
            header_demands[col] = _SWATCH_CELL_DEMAND
            continue

        if not header_visible:
            header_w = 0.0
        else:
            display_name = (col_config.label if col_config else None) or slug_to_text(
                col
            )
            display_name = apply_case(display_name, header_case)
            header_w = measurer.measure(display_name, header_font_size)

        glyph_possible = _glyph_possible(column_configs, column_when_rules, col)
        pad_table = (
            col_config.decimal_pad_table
            if isinstance(col_config, ResolvedTableColumnConfig)
            else ()
        )
        shared_scale = (
            col_config.shared_scale
            if isinstance(col_config, ResolvedTableColumnConfig)
            else None
        )
        cell_widths: list[float] = []
        for row in sample:
            val = row.get(col, "")
            fmt = col_config.format if col_config else None
            # Coerce string values the same way _compute_lane_positions and
            # the row-paint loop do (CSV/warehouse adapters can deliver
            # numbers as strings) -- a narrower isinstance(val, (int, float))
            # check here would silently skip the shared-scale measurement for
            # those cells while decimal_pad_table (built below from the
            # scaled digit_spec) still expects it, mismatching pad depth.
            try:
                num_val = float(val) if isinstance(val, str) else val
            except (ValueError, TypeError):
                num_val = val
            # One predicate, decided once and used for both computing the pad
            # basis and applying it. Mirrors both paint sites exactly
            # (table.py's row-paint and lane-position loops): numeric, not
            # bool, not date-like. Deliberately narrower than the bake, which
            # reaches Decimal via coerce_numeric_cell -- paint excludes it, so
            # measure does. A wider predicate here mis-measures the column; a
            # wider one at the compute also crashes, since format_kpi_parts
            # sends the spec through d3 number parsing and a time-capable
            # column may legally carry a strftime spec.
            # Carries the narrowed value rather than a flag so the TypeGuard
            # reaches format_kpi_parts below; None means "this cell is not on
            # the column's decimal lane".
            pad_num = (
                num_val
                if pad_table and _is_padded_numeric(num_val) and not is_date_like(val)
                else None
            )
            if shared_scale is not None and _is_padded_numeric(num_val):
                # A shared-scale column's real cell text is the scaled digit
                # string, not the whole-precision SI string
                # format_table_cell_value would render -- measure what will
                # actually paint. is_anchor=True: measures the widest case (a
                # real suffix string), since an actual non-anchor row's blank
                # suffix can only be narrower.
                prefix, number, suffix = format_kpi_parts(
                    num_val,
                    fmt,
                    formats,
                    default_number=True,
                    shared_scale=shared_scale,
                    is_anchor=True,
                )
                rendered = prefix + number + suffix
                pad_basis = number
            else:
                rendered = format_table_cell_value(val, fmt, formats)
                # The pad is selected from the NUMBER lane, never the whole
                # cell: decimal_pad_for counts every digit after the first '.',
                # so an authored suffix carrying one (' CO2', 'm2', 'ft3')
                # would inflate the observed depth past the table's precision
                # and raise. Both paint sites pad the number lane alone --
                # format_kpi_parts splits the suffix out before padding.
                pad_basis = ""
                if pad_num is not None:
                    _, pad_basis, _ = format_kpi_parts(
                        pad_num, fmt, formats, default_number=True, is_anchor=True
                    )
            if glyph_possible:
                rendered = (
                    cell_glyph_run(column_configs, column_when_rules, col, val)
                    + rendered
                )
            # Text format_table_cell_value produced by falling through to
            # str(value) is not on the column's decimal lane -- its fractional
            # depth owes nothing to the column's format -- so `pads` excludes it.
            if pad_num is not None:
                rendered += decimal_pad_for(pad_table, pad_basis)
            cell_widths.append(measurer.measure(rendered, font_size))

        if cell_widths:
            cell_widths.sort()
            p95_idx = min(int(len(cell_widths) * 0.95), len(cell_widths) - 1)
            p95_w = cell_widths[p95_idx]
        else:
            p95_w = 0.0

        cell_demands[col] = p95_w + cell_pad * 2
        header_demands[col] = header_w

    return cell_demands, header_demands


def measure_column_word_floors(
    columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    data: list[dict[str, Any]],
    measurer: FontMeasurer,
    *,
    font_size: float,
    header_font_size: float,
    header_case: CaseValue = "none",
    cell_pad: int = 0,
    max_sample_rows: int = 50,
    header_visible: bool = True,
    column_when_rules: Mapping[str, Sequence[ConditionalRule]],
) -> dict[str, float]:
    """Return the min-word floor (px) for each column including cell padding.

    The floor is the pixel width of the widest whitespace-delimited token in
    any sampled cell value or the column header label, plus ``cell_pad * 2``
    (both sides of cell padding).  URL tokens and tokens longer than
    ``_MAX_FLOOR_TOKEN_LEN`` are excluded (see ``_measure_min_word_width``).

    A cell that paints a glyph competes with its own ``glyph run + widest
    token``: the glyph shares a line with the word after it and cannot break
    away from it. That is measured per cell rather than as one column-wide
    sum — the widest token and the widest glyph can belong to different cells
    (or, for the header label, to no painted cell at all), and pairing them
    would reserve a first-line unit nothing renders.

    Header tokens are measured at ``header_font_size`` and cell tokens at
    ``font_size``: a theme that sets ``table.header.font.size`` above the body
    size would otherwise get a floor narrower than the header word it exists to
    keep whole, and the mid-word break would come back.

    The padding offset ensures that when the layout assigns a column a width
    equal to its floor, the content area (``width - 2 * cell_pad``) is still
    wide enough to display the longest token without mid-word wrapping.

    Callers pass this to ``calculate_column_layout`` as the ``word_floors``
    kwarg so that text-column allocations are never narrower than their longest
    non-URL word.

    When ``header_visible`` is False, the header label is excluded — a hidden
    label can't drive a min-width floor.

    Swatch columns pin to the rect width (no text), bypassing token measurement.
    """
    floors: dict[str, float] = {}
    sample = data[:max_sample_rows]

    for col in columns:
        col_config = column_configs.get(col)
        if col_config and col_config.swatch:
            floors[col] = _SWATCH_CELL_DEMAND + cell_pad * 2
            continue
        if not header_visible:
            display_name = ""
        else:
            display_name = (col_config.label if col_config else None) or slug_to_text(
                col
            )
            display_name = apply_case(display_name, header_case)

        cell_values = [row.get(col, "") for row in sample]
        token_w = _measure_min_word_width(
            cell_values, display_name, measurer, font_size, header_font_size
        )
        if _glyph_possible(column_configs, column_when_rules, col):
            token_w = max(
                token_w,
                max(
                    (
                        measurer.measure(
                            cell_glyph_run(column_configs, column_when_rules, col, val),
                            font_size,
                        )
                        + _widest_token_width(str(val), measurer, font_size)
                        for val in cell_values
                    ),
                    default=0.0,
                ),
            )
        floors[col] = token_w + cell_pad * 2

    return floors


def _allocate_text_columns(
    text_keys: set[str],
    text_demands: dict[str, float],
    text_budget: float,
    max_widths: dict[str, float | None],
    word_floors: dict[str, float],
    width_similarity_threshold: float,
) -> dict[str, float]:
    """Proportionally allocate ``text_budget`` among text columns.

    Enforces per-column ``max_width`` caps through iterative redistribution.
    Applies ``word_floors`` as a minimum (clamped at ``max_width`` so a floor
    never silently exceeds a declared cap).  Cluster-equalizes uncapped columns.
    """
    if len(text_keys) == 1:
        (only_col,) = text_keys
        mw = max_widths[only_col]
        floor = word_floors.get(only_col, 0.0)
        # Floor is clamped at max_width — max_width is the hard upper bound.
        effective_floor = floor if mw is None else min(floor, mw)
        allocated_w = text_budget if mw is None else min(text_budget, mw)
        return {only_col: max(allocated_w, effective_floor)}

    text_allocated = _proportional_allocate(text_demands, text_budget)

    # Iteratively cap columns at max_width and redistribute leftover to uncapped
    # columns.  Each iteration resolves at least one more column; the loop is
    # guaranteed to terminate in at most len(text_keys) passes.
    final_text: dict[str, float] = {}
    to_allocate = dict(text_allocated)

    while True:
        capped_now: dict[str, float] = {}
        free: dict[str, float] = {}
        leftover = 0.0
        for col, w in to_allocate.items():
            mw = max_widths[col]
            if mw is not None and w > mw:
                capped_now[col] = mw
                leftover += w - mw
            else:
                free[col] = w
        final_text.update(capped_now)
        if not leftover or not free:
            final_text.update(free)
            break
        # Redistribute leftover to uncapped columns proportionally.
        total_free = sum(free.values())
        if total_free > 0:
            new_total = total_free + leftover
            for col in free:
                free[col] = free[col] / total_free * new_total
        to_allocate = free

    # Cluster-equalize only non-capped columns; capped columns are excluded
    # so the snap-to-cluster-max cannot push them above their declared cap.
    non_capped = {c: v for c, v in final_text.items() if max_widths[c] is None}
    capped_only = {c: v for c, v in final_text.items() if max_widths[c] is not None}
    if len(non_capped) > 1:
        non_capped = _cluster_and_equalize_widths(
            non_capped,
            sum(non_capped.values()),
            threshold=width_similarity_threshold,
        )
    final_text = {**capped_only, **non_capped}

    # Apply word_floors after all allocation.  Floor is clamped at max_width
    # so a word-floor wider than a declared cap cannot silently override it.
    for col in text_keys:
        floor = word_floors.get(col, 0.0)
        mw = max_widths[col]
        effective_floor = floor if mw is None else min(floor, mw)
        if effective_floor > 0 and final_text[col] < effective_floor:
            final_text[col] = effective_floor

    return final_text


def _grow_toward_header_width(
    widths: dict[str, float],
    header_demands: dict[str, float],
    surplus: float,
) -> None:
    """Spend ``surplus`` px growing columns toward their header width, in place.

    A column's content area is ``width - cell_pad * 2`` and ``widths`` already
    carries the padding, so ``header_demands[col]`` is the target at which the
    header fits on one line. Columns already at or above their target take no
    share until every other column has reached its own. Whatever is left over
    afterwards is distributed proportionally to current width, so the full
    surplus is always spent.
    """
    gaps: dict[str, float] = {}
    for col, width in widths.items():
        target = header_demands.get(col, 0.0)
        if target > width:
            gaps[col] = target - width
    total_gap = sum(gaps.values())
    if total_gap > surplus:
        for col, gap in gaps.items():
            widths[col] += surplus * (gap / total_gap)
        return
    for col, gap in gaps.items():
        widths[col] += gap
    residual = surplus - total_gap
    if residual <= 0.1:
        return
    # Every caller pins each column at >= its cell demand, which the classifier
    # floors at 1.0 — so the total is positive whenever there is a column at all.
    total = sum(widths.values())
    for col in widths:
        widths[col] += residual * (widths[col] / total)


def calculate_column_layout(
    columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    available_width: float,
    *,
    demands: dict[str, float] | None = None,
    header_demands: dict[str, float] | None = None,
    width_similarity_threshold: float = 0.8,
    word_floors: dict[str, float] | None = None,
    content_headroom: float = 0.0,
) -> tuple[dict[str, float], list[float], float]:
    """Compute per-column widths, x offsets, and actual content width.

    Returns ``(col_widths, col_x_offsets, actual_content_width)``.

    When ``demands`` is provided and there are multiple auto-columns:

    1. Columns with ``width:`` set are pinned and removed from the auto pool.
    2. Auto-columns are classified as *compact* or *text* (``_classify_columns``).
       Compact columns are pinned to their measured cell-content demand; text
       columns share the remaining budget via ``_allocate_text_columns``.
    3. Any leftover budget (e.g. from ``max_width:`` caps) goes to compact
       columns via ``_grow_toward_header_width``: every column reaches its
       ``header_demands`` value first, then the residual is spread across all of
       them proportionally, so columns can end up wider than their header. When
       no compact columns exist the gap is left unconsumed — clipping is honest,
       mid-word wrapping is not.
    4. All compact, no text: over-subscription scales every column down to fit;
       otherwise the surplus grows columns toward their header width (same
       helper as step 3), never a flat proportional scale-up.

    When ``content_headroom > 0`` and the table has both compact and text columns,
    each compact column's pin is inflated by ``1 + content_headroom`` to give
    breathing room above the measured p95 demand.  The extra width is funded by
    the text-column budget.  In an all-compact table the headroom is not applied
    because proportional scaling would cancel the factor exactly.

    ``header_demands`` maps column name → header label pixel width.  When
    provided, leftover budget grows compact columns toward their header width
    rather than proportionally.  Columns whose current width already meets or
    exceeds their header demand are skipped.

    Without ``demands`` or for a single auto-column: equal distribution.
    """
    col_widths: dict[str, float] = {}
    if not columns:
        col_x_offsets: list[float] = []
        return col_widths, col_x_offsets, available_width

    # --- Phase 1: resolve explicit width: pins ---
    explicit_widths: dict[str, float] = {}
    for col in columns:
        col_cfg = column_configs.get(col)
        width_hint = parse_column_width(
            col_cfg.width if col_cfg else None, available_width
        )
        if width_hint is not None:
            explicit_widths[col] = width_hint

    auto_columns = [col for col in columns if col not in explicit_widths]
    explicit_total = sum(explicit_widths.values())
    available_for_auto = max(available_width - explicit_total, 0.0)

    if not auto_columns:
        col_widths.update(explicit_widths)
        actual_content_width = math.fsum(
            col_widths.get(
                col, 0
            )  # type-state: silent_fallback — not auto_columns means every column in `columns` is already a key in explicit_widths, just merged into col_widths above; the 0 default is defensive and unreachable here
            for col in columns
        )
        col_x_offsets = []
        current_x = 0.0
        for col in columns:
            col_x_offsets.append(current_x)
            current_x += col_widths.get(col, 100)
        return col_widths, col_x_offsets, actual_content_width

    # --- Phase 2: smart allocation when demands are provided ---
    if demands and len(auto_columns) > 1:
        # Normalized once, explicitly: an omitted mapping means "no floors /
        # no header demands", never a per-lookup default.
        floors = {} if word_floors is None else word_floors
        headers = {} if header_demands is None else header_demands
        caps: dict[str, float | None] = {}
        for col in auto_columns:
            cfg = column_configs.get(col)
            caps[col] = (
                parse_column_width(cfg.max_width, available_width)
                if cfg is not None and cfg.max_width is not None
                else None
            )

        # The min-word floor is folded into the demand, not applied afterwards.
        # A column narrower than its widest token splits that word mid-glyph
        # ("Conver" / "ted in…"), which no overflow mode can undo — so it is a
        # demand like any other. Enforcing it as a later corrective pass would
        # fight whatever the allocator had just decided, dragging columns back
        # below the header width they were grown to.
        auto_demands: dict[str, float] = {}
        for col in auto_columns:
            floor = floors.get(col, 0.0)
            cap = caps[col]
            # A declared max_width is the hard bound; a floor never overrides it.
            auto_demands[col] = max(
                demands.get(col, 1.0),
                floor if cap is None else min(floor, cap),
                1.0,
            )
        compact_keys, text_keys = _classify_columns(auto_demands, column_configs)

        # Apply content_headroom to compact column pins when there are text columns
        # to absorb the extra cost.  In a mixed table (compact + text) each compact
        # column is widened by the headroom factor so cells above the p95 have
        # breathing room; the corresponding reduction comes from the text budget.
        # In an all-compact table the factor would cancel in the proportional scale,
        # so headroom is intentionally not applied there (it can't manufacture width).
        # Cap headroom so compact pins never exceed the auto-column budget on
        # their own: headroom is meant to be funded by the text budget, not to
        # push compact_total past available_for_auto (which would zero the text
        # budget yet still overflow and later widen the table).
        raw_compact_total = sum(auto_demands[c] for c in compact_keys)
        if text_keys and raw_compact_total > 0:
            requested_factor = 1.0 + content_headroom
            max_factor = available_for_auto / raw_compact_total
            headroom_factor = min(requested_factor, max(1.0, max_factor))
        else:
            headroom_factor = 1.0
        compact_pinned: dict[str, float] = {
            c: auto_demands[c] * headroom_factor for c in compact_keys
        }
        compact_total = sum(compact_pinned.values())
        text_budget = max(available_for_auto - compact_total, 0.0)

        if not text_keys:
            # All compact: cover content demand, then spend the surplus on
            # header width before scaling. Scaling by content alone hands the
            # slack to whichever column has the widest *values* — a column with
            # short numbers under a long label ends up below its own header.
            if compact_total >= available_for_auto:
                scale = available_for_auto / compact_total
                col_widths.update({c: w * scale for c, w in compact_pinned.items()})
            else:
                _grow_toward_header_width(
                    compact_pinned, headers, available_for_auto - compact_total
                )
                col_widths.update(compact_pinned)
        else:
            max_widths = {c: caps[c] for c in text_keys}
            text_result = _allocate_text_columns(
                text_keys,
                {c: auto_demands[c] for c in text_keys},
                text_budget,
                max_widths,
                floors,
                width_similarity_threshold,
            )
            col_widths.update(text_result)

            # Any leftover after text allocation (e.g. from max_width caps) goes
            # to compact columns, growing each toward its header width first.
            # No compact → gap is left unconsumed (clipping is honest).
            text_used = sum(col_widths.get(c, 0.0) for c in text_keys)
            remainder = available_for_auto - compact_total - text_used
            if remainder > 0.1 and compact_pinned:
                _grow_toward_header_width(compact_pinned, headers, remainder)

            col_widths.update(compact_pinned)
    else:
        # Fallback: equal distribution (no demands, or single auto-column)
        per_col = available_for_auto / len(auto_columns)
        for col in auto_columns:
            col_widths[col] = per_col

    col_widths.update(explicit_widths)
    actual_content_width = math.fsum(
        col_widths.get(
            col, 0
        )  # type-state: silent_fallback — every column in `columns` is covered by either explicit_widths or the compact/text partition of auto_columns above; the 0 default is defensive and unreachable here
        for col in columns
    )

    col_x_offsets = []
    current_x = 0.0
    for col in columns:
        col_x_offsets.append(current_x)
        current_x += col_widths.get(col, 100)
    return col_widths, col_x_offsets, actual_content_width


_WRAP_OVERFLOW_MODES: frozenset[str] = frozenset({"wrap-two", "wrap"})


def reserve_header_band(table_config: TableChartStyle, body_font_size: int) -> int:
    """Height to reserve for the header row before column widths are known.

    ``resolve_wrapped_headers`` returns the exact height, but it needs the
    resolved column widths — which only the renderer computes. The sizer runs
    before layout, so it reserves the two-line worst case whenever a wrap mode
    is in play. Erring high costs dead space at the bottom of the slot; erring
    low drops rows or paginates early, so the estimate deliberately rounds up.

    ``wrap`` can exceed two lines and is still under-reserved here — unchanged
    from before ``wrap-two`` became the effective default, and opt-in.
    """
    if not table_config.header.visible:
        return 0
    flat = int(table_config.header.height)
    modes = {resolve_header_overflow(table_config)}
    modes.update(
        cfg.header_overflow
        for cfg in (table_config.columns if table_config.columns else {}).values()
        if cfg.header_overflow is not None
    )
    if not (modes & _WRAP_OVERFLOW_MODES):
        return flat
    header_font_size = (
        int(table_config.header.font.size)
        if table_config.header.font.size is not None
        else body_font_size
    )
    line_height = header_font_size + table_config.text_baseline_offset
    padding = int(table_config.outer_padding)
    return max(flat, int(padding * 2 + 2 * line_height))


def resolve_wrapped_headers(
    columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    col_widths: dict[str, float],
    *,
    header_overflow: TitleOverflowMode,
    header_height: float,
    header_font: FontStyle,
    padding: int,
    table_config: TableChartStyle,
    cell_pad: int | None = None,
    measurer: FontMeasurer,
) -> tuple[dict[str, list[str]], dict[str, bool], float]:
    """Return wrapped header lines, per-column truncated flags, and effective header height.

    Wrapping uses the full cell content area (``cw - 2 * pad``) for every
    column — both text and numeric headers get the same available width.
    Wide headers wrap into the next line when they'd overflow.

    Wrapping decisions always use real glyph metrics via ``measurer`` to
    avoid false-positive wraps (e.g. "Poverty" wrapping at widths where it
    actually fits).
    """
    if not columns:
        return {}, {}, header_height

    pad = cell_pad if cell_pad is not None else table_config.column_layout.cell_padding
    header_font_size = int(header_font.size) if header_font.size is not None else 12
    header_line_height = header_font_size + table_config.text_baseline_offset
    wrapped_headers: dict[str, list[str]] = {}
    truncated_headers: dict[str, bool] = {}

    for col in columns:
        cw = col_widths.get(col, 100)
        col_config = column_configs.get(col)
        name = (col_config.label if col_config else None) or slug_to_text(col)
        # Apply header case transform so wrapped lines match what render_table_headers emits.
        _header_case = header_font.case
        if _header_case is not None and _header_case != "none":
            from dbt_charts.core.text.case import apply_case

            name = apply_case(name, _header_case)
        column_overflow: TitleOverflowMode = (
            col_config.header_overflow
            if col_config and col_config.header_overflow is not None
            else header_overflow
        )
        content_area = max(int(cw - pad * 2), 1)
        # Short-circuit: if the full header fits on one line under real font
        # metrics, skip the char-count heuristic entirely.
        if measurer.measure(name, header_font_size) <= content_area:
            wrapped_headers[col] = [name]
            truncated_headers[col] = False
            continue
        rendered, truncated = prepare_title_text(
            name,
            overflow=column_overflow,
            limit=content_area,
            font_size=header_font_size,
            font_family=header_font.family,
        )
        wrapped_headers[col] = rendered.splitlines() or [name]
        truncated_headers[col] = truncated
    max_lines = max(len(lines) for lines in wrapped_headers.values())
    effective_height = max(header_height, padding * 2 + max_lines * header_line_height)
    return wrapped_headers, truncated_headers, effective_height


# ---------------------------------------------------------------------------
# Conditional formatting helpers (when rules + scale color mapping)
# ---------------------------------------------------------------------------


def _expand_hex(c: str) -> str:
    """Expand 3-char hex (#RGB) to 6-char (#RRGGBB). Passes 6-char through."""
    h = c.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return f"#{h}"


def _parse_hex(c: str) -> tuple[int, int, int]:
    h = _expand_hex(c).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Linearly interpolate between two hex colors."""
    r1, g1, b1 = _parse_hex(c1)
    r2, g2, b2 = _parse_hex(c2)
    r = int(r1 + (r2 - r1) * t + 0.5)
    g = int(g1 + (g2 - g1) * t + 0.5)
    b = int(b1 + (b2 - b1) * t + 0.5)
    return f"#{r:02x}{g:02x}{b:02x}"


def resolve_palette_stops(target: ResolvedScaleTarget) -> list[str]:
    """Resolve a scale target's palette to a list of hex stops.

    Inline list passes through unchanged (new list object returned).

    A named palette string carries resolved_stops only on
    ResolvedNamedPaletteScaleTargetConfig — the structurally guaranteed form
    produced by bake_scale_target_stops and _with_resolved_scale_stops. A plain
    ResolvedScaleTargetConfig (Vega scheme or inline list) reaching this function
    with a string palette is a caller bug; it raises ValueError rather than
    asserting so the error is visible in production.
    """
    if isinstance(target.palette, list):
        if not target.palette:
            raise ValueError("palette must not be empty")
        return [str(c) for c in target.palette]
    from dbt_charts.core.compile.models.primitives import (  # noqa: PLC0415
        ResolvedNamedPaletteScaleTargetConfig,
    )

    if isinstance(target, ResolvedNamedPaletteScaleTargetConfig):
        return list(target.resolved_stops)
    raise ValueError(
        f"resolve_palette_stops called with a Vega scheme palette={target.palette!r} — "
        "scheme names are rendered directly and must not reach this function."
    )


def resolve_hinge(
    config: ScaleTargetConfig,
    lo: float,
    hi: float,
    col_format: str | None,
) -> float | None:
    """Decide the diverging pivot from config + domain context.

    Returns ``None`` for sequential (``config.hinge is None``).
    Explicit float in ``config.hinge`` short-circuits the decision tree.
    ``"auto"`` runs the tree: zero-crossing → 0; percent-format crosses 1.0
    → 1.0; else midpoint.
    """
    if config.hinge is None:
        return None
    # Degenerate domain: diverging is meaningless; fall back to sequential.
    if lo == hi:
        return None
    if isinstance(config.hinge, (int, float)):
        return float(config.hinge)
    # "auto" decision tree.
    if lo < 0.0 < hi or (lo < 0.0 and hi == 0.0):
        return 0.0
    is_percent = col_format is not None and "%" in col_format
    if is_percent and lo < 1.0 < hi:
        return 1.0
    return (lo + hi) / 2.0


def _interpolate_arm(t: float, stops: list[str]) -> str:
    """Interpolate at normalized position t∈[0,1] along a palette arm.

    ``stops`` must already be expanded to 6-char hex. ``t`` is clamped to
    [0, 1] before indexing.
    """
    t = max(0.0, min(1.0, t))
    n_segments = len(stops) - 1
    segment = t * n_segments
    i = min(int(segment), n_segments - 1)
    return _lerp_color(stops[i], stops[i + 1], segment - i)


def interpolate_scale_color(
    value: float,
    min_val: float,
    max_val: float,
    palette: list[str],
    *,
    hinge: float | None = None,
    arm_mode: str = "asymmetric",
) -> str:
    """Map a numeric value to a color via linear interpolation across palette stops.

    Sequential mode (``hinge is None``):
      Values below min clamp to the first stop; above max clamp to the last.
      When min == max, returns the middle stop.

    Diverging mode (``hinge is not None``):
      Palette is split at the midpoint. Left half maps to [min_val, hinge];
      right half maps to [hinge, max_val].

      ``arm_mode="asymmetric"`` (default): each arm's t is computed relative
      to the actual arm width — per-unit intensity is consistent across the
      pivot regardless of arm length.

      ``arm_mode="symmetric"``: each arm stretches fully from neutral to
      its extreme regardless of absolute width — useful when visual parity
      between arms matters more than per-unit consistency.
    """
    palette = [_expand_hex(c) for c in palette]

    if hinge is None:
        # Sequential path — unchanged behavior.
        if min_val == max_val:
            return palette[len(palette) // 2]
        t = (value - min_val) / (max_val - min_val)
        t = max(0.0, min(1.0, t))
        n_segments = len(palette) - 1
        segment = t * n_segments
        i = min(int(segment), n_segments - 1)
        return _lerp_color(palette[i], palette[i + 1], segment - i)

    # Diverging path.
    if len(palette) < 3 or len(palette) % 2 == 0:
        raise ValueError(
            f"diverging palette must have an odd number of stops >= 3, got {len(palette)}"
        )

    # Clamp hinge to [min_val, max_val] so out-of-domain author values don't
    # produce negative arm widths or meaningless interpolation.
    hinge = max(min_val, min(max_val, hinge))

    mid = len(palette) // 2
    neg_stops = palette[: mid + 1][::-1]  # neutral → neg extreme
    pos_stops = palette[mid:]  # neutral → pos extreme

    # Clamp value to domain.
    clamped = max(min_val, min(max_val, value))

    neg_width = hinge - min_val
    pos_width = max_val - hinge
    # Asymmetric uses the longer arm as the common intensity unit so that
    # equal absolute distance from the hinge produces equal palette depth
    # on both sides — the shorter arm will never reach full palette saturation.
    # Symmetric uses per-arm width so both arms always fill their full palette
    # range regardless of absolute length.
    if clamped <= hinge:
        distance = hinge - clamped
        if arm_mode == "asymmetric":
            denom = max(neg_width, pos_width, 1e-12)
        else:
            denom = max(neg_width, 1e-12)
        return _interpolate_arm(distance / denom, neg_stops)
    else:
        distance = clamped - hinge
        if arm_mode == "asymmetric":
            denom = max(neg_width, pos_width, 1e-12)
        else:
            denom = max(pos_width, 1e-12)
        return _interpolate_arm(distance / denom, pos_stops)


def compute_scale_domain(
    data: list[dict[str, Any]], field: str, cfg: ScaleTargetConfig
) -> tuple[float, float]:
    """Compute the effective [min, max] domain for a scale target.

    Uses explicit overrides when set, otherwise infers from data.
    Falls back to (0, 1) when no numeric values are present.
    """

    has_min = cfg.min is not None
    has_max = cfg.max is not None

    if has_min and has_max:
        return (float(cfg.min), float(cfg.max))  # type: ignore[arg-type]

    values: list[float] = []
    for row in data:
        coerced = coerce_numeric_cell(row.get(field))
        if coerced is not None:
            values.append(coerced)

    if not values:
        return (
            float(cfg.min) if has_min else 0.0,  # type: ignore[arg-type]
            float(cfg.max) if has_max else 1.0,  # type: ignore[arg-type]
        )

    lo = float(cfg.min) if has_min else min(values)  # type: ignore[arg-type]
    hi = float(cfg.max) if has_max else max(values)  # type: ignore[arg-type]
    return (lo, hi)


def resolve_cell_conditional_styles(
    col_config: ResolvedTableColumnConfig,
    value: Any,
    data: list[dict[str, Any]],
    when_rules: Sequence[ConditionalRule] | None = None,
    col_format: str | None = None,
    row_role: str = "value",
    col_name: str = "",
) -> tuple[str | None, str | None, str | float | None, str | None, str | None]:
    """Resolve effective cell styling.

    Precedence (each layer only overrides keys it sets):
      1. base column style
      2. scale — continuous numeric mapping (skipped for summary/total rows)
      3. when — discrete predicate rules from the chart-level
         ``conditional_formatting`` block (``when_rules``)

    Args:
        col_config: Column configuration carrying base style and scale config.
        value: Raw cell value (may be a numeric string from CSV loads).
        data: Rows used to compute the scale domain. Callers must exclude
            summary/total rows so the domain is not skewed by aggregate values.
        when_rules: Author-specified threshold rules. Applied to all row roles.
        col_format: D3 format string for the column, passed to ``resolve_hinge``
            so the "auto" branch can detect percent-format domains.
        row_role: Semantic role of this row — ``"value"``, ``"summary"``, or
            ``"total"``. Scale (heatmap) fills are skipped for non-value rows
            because coloring aggregate rows by their own value relative to the
            column domain is visually misleading. ``when`` rules still apply.

    Returns ``(background, color, weight, style, decoration)``.
    ``weight`` is the raw ``FontStyle.weight`` value — ``str | float | None``
    — left for callers to normalize via ``font_weight_as_css`` before CSS
    emission.
    """
    bg = col_config.background
    col_font = col_config.font
    color = col_font.color if col_font is not None else None
    fw = col_font.weight if col_font is not None else None
    fstyle: str | None = None
    fdecoration: str | None = None

    # Layer 2: scale — skipped for summary/total rows.
    if col_config.scale and not is_summary_role(row_role):
        scale = col_config.scale
        for attr, target in [("background", scale.background), ("color", scale.color)]:
            if target is None:
                continue
            if value is None:
                if target.null_color is not None:
                    if attr == "background":
                        bg = target.null_color
                    else:
                        color = target.null_color
            elif (numeric_value := coerce_numeric_cell(value)) is not None:
                lo, hi = compute_scale_domain(data, col_name, target)
                palette = resolve_palette_stops(target)
                hinge = resolve_hinge(target, lo, hi, col_format)
                scaled = interpolate_scale_color(
                    numeric_value,
                    lo,
                    hi,
                    palette,
                    hinge=hinge,
                    arm_mode=target.arm_mode,
                )
                if attr == "background":
                    bg = scaled
                else:
                    color = scaled

    # Layer 3: when rules from the chart-level conditional_formatting block.
    if when_rules:
        overrides = resolve_conditional_styles(when_rules, value)
        if "background" in overrides:
            bg = overrides["background"]
        if "color" in overrides:
            color = overrides["color"]
        if "weight" in overrides:
            fw = overrides["weight"]
        if "style" in overrides:
            fstyle = overrides["style"]
        if "decoration" in overrides:
            fdecoration = overrides["decoration"]

    return bg, color, fw, fstyle, fdecoration


def resolve_cell_glyph(
    col_config: TableColumnConfig | None,
    value: Any,
    when_rules: Sequence[ConditionalRule] | None,
) -> tuple[str | None, str | None]:
    """Resolve the effective glyph + glyph_color for a single cell.

    A column's static ``glyph`` is the base; a matching ``when`` rule's
    ``glyph`` overrides it. When a rule sets ``glyph`` but not
    ``glyph_color``, the cell falls back to default ink rather than
    inheriting the static or earlier-rule color (see
    ``_apply_rule_outputs``).
    """
    return resolve_cell_glyph_from_overrides(
        col_config,
        resolve_conditional_styles(when_rules, value) if when_rules else {},
    )


def glyph_run(glyph: str | None) -> str:
    """The run a cell paints ahead of its value (``"● "``), or ``""``.

    The renderer emits the glyph and its separating space as a leading tspan,
    so a column measured on the bare value is short by exactly this run and
    its widest cells spill past the cell background into the next column.
    Every width measurement composes this in front of the cell text — the one
    spelling of the separator, so measure and paint cannot drift apart.
    """
    return f"{glyph} " if glyph else ""


def cell_glyph_run(
    column_configs: Mapping[str, TableColumnConfig],
    column_when_rules: Mapping[str, Sequence[ConditionalRule]],
    col: str,
    value: Any,
) -> str:
    """``glyph_run`` for a cell whose glyph still has to be resolved."""
    return glyph_run(
        resolve_cell_glyph(column_configs.get(col), value, column_when_rules.get(col))[
            0
        ]
    )


def resolve_cell_glyph_from_overrides(
    col_config: TableColumnConfig | None,
    overrides: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Variant that reads from an already-merged ``overrides`` dict.

    Lets callers that already evaluated ``resolve_conditional_styles``
    (e.g. the table renderer's per-cell loop) avoid a second pass.
    """
    if "glyph" in overrides:
        return overrides["glyph"], overrides.get("glyph_color")
    if col_config is not None and col_config.glyph is not None:
        return col_config.glyph, col_config.glyph_color
    return None, None
