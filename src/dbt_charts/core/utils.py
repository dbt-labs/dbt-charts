"""Cross-cutting utilities shared across compile, execute, and render modules."""

import math
import re
from collections.abc import Callable, Hashable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import yaml
import yaml.constructor
from pydantic import BaseModel

from dbt_charts.core.font_measure import get_font_measurer

# Query result rows, as every core layer passes them around.
Rows = list[dict[str, Any]]

# One raw query-result cell value (str/int/float/date/None), or a fold over
# such values (sum, min) — dynamic by construction, not a laundered type:
# the concrete type depends on the board's own query.
CellValue = Any  # type-state: explicit_any — raw query result cell value

# Breathing room between the widest label's near edge and the tick — purely
# cosmetic (avoids the label touching the tick line), not a gutter-sizing
# input. Small and fixed, like the axis label gap in chart-rendering config.
_BREATHING_ROOM_PX = 4.0

# Vega-Lite's own default axis.labelLimit when the theme leaves
# axis.labels.max_width unset — the pixel width VL truncates a rendered label
# to (with an ellipsis) before this module ever measures it.
DEFAULT_VL_LABEL_LIMIT: float = 180.0

_MERGE_TAG = "tag:yaml.org,2002:merge"

_YEAR_MIN = 1900
_YEAR_MAX = 2100
_YEAR_STRING_PATTERN = re.compile(r"^\d{4}$")

# Cell-scope date detector — shared by compile-time table column alignment
# (classify_date_column_align) and the render-time table cell/lane machinery
# (dbt_charts.core.render.chart.table.py and table_support.py both import
# is_date_like from here). Distinct from render/chart/type_inference.py's
# DATE_LIKE_PATTERNS (chart-scope: axis type inference), which is a
# separate, wider list — the two detectors are deliberately not unified.
_DATE_RE = re.compile(
    r"^\d{4}-\d{2}(-\d{2})?$"  # ISO: 2024-03 or 2024-03-15
    r"|^\d{2}/\d{2}/\d{4}$"  # US: 03/15/2024
    r"|^Q[1-4]\s+\d{4}$"  # Quarter: Q1 2024
    r"|^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}$"
    r"|^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$"
    r"|^\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$"
    r"|^\d{4}$"  # Year: 2024
)


# Every parse of board text goes through this one loader — libyaml's C
# scanner, never PyYAML's pure-Python one: the two scanners do not accept
# the same language (a tab as separation whitespace, for one), so a board
# must never be parsed by both. Never wrap this in try/except ImportError.
if not yaml.__with_libyaml__:
    raise ImportError(
        "dbt-charts requires PyYAML built with libyaml (yaml.CSafeLoader); "
        "install a PyYAML wheel or build it against libyaml"
    )


# The deepest authored board in the repo nests 12 levels; the compile path's
# own Python recursion gives out near 330. libyaml's node composer recurses
# in C with no guard at all, so ~25k levels (50 KB of brackets, or of "- ")
# segfault the interpreter instead of raising — the Python scanner raised a
# catchable RecursionError. Nothing above this loader can catch that, so the
# ceiling is enforced here, over libyaml's own event stream, which is
# iterative and safe at any depth.
MAX_YAML_NESTING = 100


class NestingTooDeepError(yaml.MarkedYAMLError):
    """Board text nested past ``MAX_YAML_NESTING`` — refused before composing."""


class BoardLoader(yaml.CSafeLoader):
    """CSafeLoader that refuses to compose text nested past ``MAX_YAML_NESTING``."""

    def __init__(
        self,
        stream: object,  # type-state: object_annotation — narrowed to str below
    ) -> None:
        # Imported here, not at module top: an import sorter would hoist it
        # above the libyaml guard, and on a build without libyaml it is this
        # import that fails — the guard's install hint has to come first.
        from yaml._yaml import CParser

        # Text only: the depth pass below would drain a file-like stream and
        # leave the composer an empty document.
        if not isinstance(stream, str):
            raise TypeError("BoardLoader parses text, not a stream")
        depth = 0
        events = CParser(stream)
        try:
            while events.check_event():
                event = events.get_event()
                if isinstance(event, yaml.SequenceStartEvent | yaml.MappingStartEvent):
                    depth += 1
                    if depth > MAX_YAML_NESTING:
                        raise NestingTooDeepError(
                            problem=f"nesting deeper than {MAX_YAML_NESTING} levels",
                            problem_mark=event.start_mark,
                        )
                elif isinstance(event, yaml.SequenceEndEvent | yaml.MappingEndEvent):
                    depth -= 1
        finally:
            events.dispose()
        super().__init__(stream)


YAML_LOADER = BoardLoader


class UniqueKeyLoader(BoardLoader):
    """BoardLoader that raises ConstructorError on duplicate mapping keys.

    Scan runs on the raw node list before flatten_mapping so merge-key overrides
    (<<: *anchor + explicit key) are not flagged as duplicates.
    """

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"duplicate key: {key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)
            except TypeError:
                # Non-hashable key (e.g. a sequence) — can't be a duplicate, skip.
                pass
        return super().construct_mapping(node, deep=deep)


def to_plain_dict(obj: object) -> Any:
    """Recursively convert Pydantic models and nested objects to plain Python containers.

    This is the emit-boundary converter: typed Vega-Lite contract models
    (Config, Transform, etc.) are serialized to plain dicts here, just
    before they enter the final Vega-Lite spec dict for JSON emission.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(by_alias=True, exclude_unset=True)
    if isinstance(obj, dict):
        return {k: to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_plain_dict(v) for v in obj]
    return obj


_UNIT_SUFFIXES: dict[str, str] = {
    "usd": "($)",
    "eur": "(€)",
    "gbp": "(£)",
    "pct": "(%)",
    "percent": "(%)",
    "ratio": "(ratio)",
    "cnt": "(Count)",
    "num": "(#)",
    "qty": "(Qty)",
}

_ABBREVIATIONS: dict[str, str] = {
    "yoy": "YoY",
    "mom": "MoM",
    "qoq": "QoQ",
    "avg": "Avg",
    "arr": "ARR",
    "mrr": "MRR",
    "nrr": "NRR",
    "ltv": "LTV",
    "cac": "CAC",
    "roi": "ROI",
    "roas": "ROAS",
    "csat": "CSAT",
    "nps": "NPS",
    "enps": "eNPS",
    "api": "API",
    "id": "ID",
    "url": "URL",
    "sql": "SQL",
    "hr": "HR",
    "ip": "IP",
}


def slug_to_text(slug: str) -> str:
    """Tokenize a slug/ID into human-readable text — WITHOUT letter-case transforms.

    Handles common data column naming conventions:
    - snake_case and kebab-case → space-separated tokens (lowercase)
    - Unit suffixes: ``_usd`` → ``($)``, ``_pct`` → ``(%)``
    - Abbreviations: ``yoy`` → ``YoY``, ``arr`` → ``ARR``

    Letter-case (title/sentence/upper/lower) is NOT applied here — the caller
    supplies a ``font.case`` value and passes the result through
    ``apply_font_case`` (or ``format_display_text``).

    Examples:
        >>> slug_to_text("revenue_usd")
        'revenue ($)'
        >>> slug_to_text("yoy_growth_pct")
        'YoY growth (%)'
        >>> slug_to_text("logo_churn_pct")
        'logo churn (%)'
        >>> slug_to_text("avg_deal_size")
        'Avg deal size'
    """
    if not slug:
        return ""

    tokens = slug.replace("-", "_").replace(" ", "_").split("_")
    tokens = [t for t in tokens if t]

    suffix_label = ""
    if len(tokens) > 1 and tokens[-1].lower() in _UNIT_SUFFIXES:
        suffix_label = _UNIT_SUFFIXES[tokens[-1].lower()]
        tokens = tokens[:-1]

    words = []
    for token in tokens:
        lower = token.lower()
        if lower in _ABBREVIATIONS:
            words.append(_ABBREVIATIONS[lower])
        else:
            words.append(lower)

    text = " ".join(words)
    if suffix_label:
        text = f"{text} {suffix_label}".strip()
    return text


def numeric_column_values(rows: Rows, field: str) -> list[float]:
    """Extract ``field``'s numeric values from ``rows``: int/float/Decimal and
    numeric strings, matching real warehouse row shapes (some adapters return
    measure columns as strings). Shared by compile-time resolve checks and
    render-time baseline features so both agree on what counts as numeric."""
    values: list[float] = []
    for row in rows:
        cell = row.get(field)
        numeric = coerce_numeric_cell(cell)
        if numeric is not None:
            values.append(numeric)
    return values


def coerce_numeric_cell(cell: Any) -> float | None:
    """Coerce a single row cell to float, or None if it isn't numeric.

    Same numeric rule as ``numeric_column_values`` (int/float/Decimal and
    numeric strings, bool excluded) — shared so a compile-time format
    decision and the render-time formatting of the same cell agree on
    whether it's numeric. Non-finite values (NaN, ±Infinity) return None
    so all callers agree on the null rule: no color, no domain contribution.
    """
    if isinstance(cell, (int, float, Decimal)) and not isinstance(cell, bool):
        result = float(cell)
        return result if math.isfinite(result) else None
    if isinstance(cell, str):
        try:
            result = float(cell)
            return result if math.isfinite(result) else None
        except ValueError:
            return None
    return None


def is_year_shaped(samples: list[Any]) -> bool:
    """True iff every non-null sample is a year-range value.

    Value-only heuristic (no column-name signal): an ``int`` in
    ``[1900, 2100]``, a ``str`` matching ``^\\d{4}$`` in that range, or an
    integral ``Decimal`` (``v == v.to_integral_value()``) in range — warehouses
    (BigQuery NUMERIC, Snowflake NUMBER) return year integers as ``Decimal``.
    A non-integral ``Decimal`` (e.g. ``2024.5``) is not a year. ``bool`` and
    ``float`` are excluded — bool is never a year, and float years don't occur
    in practice. Empty input is not year-shaped. Shared by compile-time bar
    orientation/axis inference and render-time time-unit detection so both
    agree on what counts as a year.
    """
    if not samples:
        return False
    for v in samples:
        if isinstance(v, bool):
            return False
        if isinstance(v, int):
            if not (_YEAR_MIN <= v <= _YEAR_MAX):
                return False
        elif isinstance(v, Decimal):
            if v != v.to_integral_value() or not (_YEAR_MIN <= v <= _YEAR_MAX):
                return False
        elif isinstance(v, str) and _YEAR_STRING_PATTERN.match(v):
            if not (_YEAR_MIN <= int(v) <= _YEAR_MAX):
                return False
        else:
            return False
    return True


def is_vega_numeric_value(
    value: int | float | Decimal | bool | str | date | datetime,
) -> bool:
    """Whether ``value`` counts as numeric for Vega-Lite type inference.

    The single source of truth for "is this cell numeric" as far as deciding
    a color/measure encoding's VL type goes — ``render/chart/type_inference.py``'s
    ``infer_vega_type_from_data``, ``render/chart/emitters/_channels.py``'s
    ``_numeric_extent``, and ``compile/resolve/chart/_channels.py``'s
    ``_flag_quantitative_color`` (via ``vega_infers_quantitative`` below) all
    call this so they cannot independently drift onto different numeric
    rules (that drift once caused a real bug: a numeric-*string* column got
    a numeric domain baked onto a scale VL was rendering nominal, since a
    looser string-coercing rule was used to compute the domain). Deliberately
    does NOT accept numeric strings — a `"77"` cell is nominal data VL cannot
    compare against a raw number, no matter how a downstream consumer feels
    about coercing it. Also
    deliberately does NOT exclude ``bool`` (unlike ``coerce_numeric_cell``,
    which does, for a different, render-formatting-cell contract): a boolean
    series field must reach the quantitative "no reorder" skip path (see
    test_shared_spatial_series_order), and ``bool`` is an ``int`` subclass, so
    counting it as numeric is the natural default of the isinstance check.
    Includes ``Decimal`` so warehouse NUMERIC/DECIMAL columns (BigQuery,
    DuckDB) are not misclassified nominal.
    """
    return isinstance(value, (int, float, Decimal))


def vega_infers_quantitative(data: Rows, field: str) -> bool:
    """Whether Vega-Lite's own type inference would resolve ``field`` to a
    quantitative encoding.

    The one shared predicate behind both sides of the hover-emphasis
    magnitude gate: ``render/chart/emitters/_channels.py``'s
    ``channel_to_encoding`` calls ``infer_vega_type_from_data`` directly to
    pick the VL encoding type for a bare ``color: <field>`` series channel;
    ``compile/resolve/chart/_channels.py``'s ``_flag_quantitative_color``
    calls this function to predict the same verdict at compile time, before
    the emitter ever runs — so the two can no longer independently drift on
    the sample window, the numeric rule, or the all-or-nothing threshold.
    Mirrors ``infer_vega_type_from_data``'s own numeric sample exactly: the
    first 10 rows, skipping ``None`` cells, every remaining sampled value
    must read as numeric. A field absent from the first row returns
    ``False`` (the same row-0 guard ``infer_vega_type_from_data`` applies);
    an entirely-null sample (the field present but every sampled value
    ``None``) returns vacuous-true instead, since
    ``infer_vega_type_from_data`` now delegates its own verdict to this same
    function — an all-null color column still renders Vega-Lite's
    quantitative gradient legend, so the gate must flag it too.
    """
    if not data or field not in data[0]:
        return False
    sample_values = [
        row.get(field) for row in data[: min(10, len(data))] if field in row
    ]
    if not sample_values:
        return False
    return all(
        is_vega_numeric_value(value) for value in sample_values if value is not None
    )


def is_date_like(value: Any) -> bool:
    """Return True for date-like values: Python date/datetime objects or
    string values matching date patterns.

    Recognized string patterns: ISO (2024-03, 2024-03-15), US (03/15/2024),
    short month name (Mar 15), long form (Mar 15, 2024), euro
    (15 Mar 2024), quarter (Q1 2024), year (2024).

    Accepts datetime.date / datetime.datetime directly so that layout
    decisions (right-align, tabular font, no-wrap) apply to native Python
    temporal objects returned by DuckDB fetchall(), not just pre-formatted
    strings.

    Cell-scope date detector used by the table renderer and by
    classify_date_column_align for column-level alignment. Does not accept
    full month names ("15 January 2024") — a known gap; widening this
    pattern is a separate decision from any single caller.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (date, datetime)):
        return True
    if not isinstance(value, str) or not value:
        return False
    return bool(_DATE_RE.match(value.strip()))


def classify_date_column_align(values: Sequence[Any]) -> Literal["right"] | None:
    """Return "right" iff every non-null value in a table column is
    date-like, else None (no verdict).

    A table column is not a set of independently-aligned cells: a per-cell
    date match is not sufficient to right-align the whole column, because
    the same column can mix formats a date detector only partially
    recognizes (e.g. "9 May 2026" matches, "30 March 2026" doesn't) and a
    per-cell decision then renders one column with two different
    text-anchors. Only a unanimous match earns a verdict; anything else
    (mixed content, non-date content, an empty column) returns None and
    the caller's normal alignment default applies.

    Null/blank cells don't count against the verdict — the caller renders
    them as a placeholder regardless of column alignment.

    ``values`` are the raw executor rows resolve holds; render normalizes a
    non-JSON-native scalar (``normalize_scalar_for_json``, applied before
    any of render's own per-cell checks run) by calling ``str()`` on it. A
    bare ``datetime.date`` stringifies to a clean ISO date and still
    matches; a ``datetime.datetime`` always stringifies with a time
    component (even at midnight), which the date patterns below don't
    match. Classifying a raw ``datetime.datetime`` as date-like without
    accounting for that would right-align a column whose cells render
    non-tabular, proportional-font text — this pre-stringifies exactly the
    values render will also stringify, so the two can't disagree on a value
    that renders as plain text once normalized.
    """
    non_null = [
        v
        for v in values
        if v is not None and not (isinstance(v, str) and not v.strip())
    ]
    if not non_null:
        return None
    normalized = [str(v) if isinstance(v, datetime) else v for v in non_null]
    return "right" if all(is_date_like(v) for v in normalized) else None


def format_error_summary(errors: list[str]) -> str:
    """Format a list of errors into a human-readable summary (first 5 shown)."""
    error_summary = f"Found {len(errors)} error(s):\n"
    for i, error in enumerate(errors[:5], 1):
        error_summary += f"{i}. {error}\n"
    if len(errors) > 5:
        error_summary += f"... and {len(errors) - 5} more errors\n"
    return error_summary


def normalize_data_for_json(data: Rows) -> Rows:
    """Normalize data types for JSON serialization.

    Converts dates, datetimes, Decimals, and other non-JSON-serializable types
    to JSON-compatible formats.
    """
    normalized: Rows = []
    for row in data:
        normalized_row: dict[str, Any] = {}
        for key, value in row.items():
            if value is None:
                normalized_row[key] = None
            elif isinstance(value, (int, float, str, bool)):
                normalized_row[key] = value
            elif isinstance(value, Decimal):
                normalized_row[key] = float(value)
            elif isinstance(value, (date, datetime)):
                normalized_row[key] = value.isoformat()
            else:
                normalized_row[key] = str(value)
        normalized.append(normalized_row)
    return normalized


# The aggregate Vega-Lite folds a category's rows into before ordering a field
# sort. Which one it uses is a property of the composed spec, not of the sort:
# a stacking mark gets ``sum``, everything else ``min``. Reproducing the
# rendered order therefore means knowing which — never assuming.
VlSortOp = Literal["sum", "min"]


def _js_min(values: list[CellValue]) -> CellValue:
    """The fold Vega applies for ``op: "min"``, in d3's shape.

    Not Python's ``min``: d3 keeps its running minimum unless ``min > value``,
    so a value it cannot compare is passed over rather than raising — and the
    result is therefore order-dependent for a heterogeneous column, which is
    what Vega draws. ``min()`` would raise ``TypeError`` on rows Vega renders
    fine.

    Exact for the columns a query returns: all-numeric, all-string (JS compares
    two strings lexicographically, as Python does), all-date. It diverges from
    JS only where a number and a *numeric string* share one column — JS coerces
    to Number there, Python does not — which a SQL column, having one type,
    does not produce.
    """
    result: CellValue = None
    for value in values:
        if result is None:
            result = value
            continue
        try:
            if value < result:
                result = value
        except TypeError:
            continue
    return result


_VL_SORT_AGGREGATE: dict[VlSortOp, Callable[[list[CellValue]], CellValue]] = {
    "sum": sum,
    "min": _js_min,
}


def domain_sort_aggregates(
    rows: Rows,
    x_field: str,
    sort_field: str,
    op: VlSortOp,
) -> dict[Hashable, CellValue]:
    """Per-x-category aggregate of ``sort_field`` for domain ordering.

    ``x_domain_order`` is the single caller and owns picking ``op``.

    ``sum`` is arithmetic, so it coerces (``coerce_numeric_cell`` also reads
    the numeric strings some adapters return for measure columns) and drops
    what will not coerce. ``min`` is a comparison, and Vega compares strings
    and dates natively — a ``sort:`` by a month-start date or a label is an
    ordering, and coercing it to a number first would throw that away.
    """
    by_category: dict[Hashable, list[CellValue]] = {}
    for row in rows:
        x = row.get(x_field)
        if x is None:
            continue
        raw = row.get(sort_field)
        value = coerce_numeric_cell(raw) if op == "sum" else raw
        if value is not None:
            by_category.setdefault(x, []).append(value)
    aggregate = _VL_SORT_AGGREGATE[op]
    return {x: aggregate(vals) for x, vals in by_category.items()}


def x_domain_order(
    rows: Rows, x_field: str, sort_by: str, descending: bool, *, op: VlSortOp
) -> list[Hashable]:
    """A categorical x domain in Vega-Lite's rendered order.

    Shared by the resolve-time stacked-label predicate and render-time domain
    consumers, which must agree on which category is first and last.

    An empty ``sort_by`` means no authored sort, and VL preserves first-
    occurrence row order when the encoding carries ``sort: null``. Otherwise VL
    sorts by the named field, folding each category's rows with ``op`` first.
    ``op`` is keyword-only and undefaulted because getting it wrong is silent:
    one row per category makes every aggregate agree, so a wrong ``op`` only
    surfaces on the multi-row data a caller is least likely to test with.

    A category with no value for the sort field is placed at the end, which is
    where VL puts it descending; ascending, VL leads with it. The pin makes the
    two agree by construction, so the difference is a deliberate simplification
    rather than a mismatch.

    Where nothing ranks at all — text under ``sum`` totals to NaN, and a number
    cannot be compared against a string — this returns row order, measured
    against ``vl_convert`` for the fixtures in ``test_cartesian_primitives.py``.
    It is not universal: Vega sorts with a comparator that reports "equal" for
    pairs it cannot order, and ``Array.prototype.sort`` over a non-transitive
    comparator can partially rank depending on its own pivots. Row order is the
    deterministic reading of that, not a claim about every arrangement.
    """
    xs = list(
        dict.fromkeys(row[x_field] for row in rows if row.get(x_field) is not None)
    )
    if not sort_by:
        return xs
    totals = domain_sort_aggregates(rows, x_field, sort_by, op)
    ranked = [x for x in xs if x in totals]
    try:
        ranked.sort(key=lambda x: totals[x], reverse=descending)
    except TypeError:
        return xs
    return ranked + [x for x in xs if x not in totals]


def bar_sort_op(sort_by: str, measure_field: str | None, stacked: bool) -> VlSortOp:
    """The aggregate a bar's categorical sort folds each category's rows with.

    ``min`` is the sort column's own value: the one a well-formed column holds
    across a category's rows, where it equals ``max`` — so one ``op`` serves
    both directions and Vega's reverse does the rest. ``sum`` is a stacked
    total, and that is what exactly one sort means: a stacked bar sorted by
    its own measure. ``measure_field`` is the field the chart actually emits
    on the measure channel, so a wide chart's synthetic fold field never
    matches an authored ``sort.by`` and its sorts read as ``min``, which is
    what a sort by one of the folded columns means.

    A grouped bar gets ``min`` too, and that is a decision rather than a
    consequence: ``EncodingSortField.op`` folds every row in the category
    whether or not ``xOffset`` drew them side by side, so ``sum`` there would
    rank by a group total the bars never draw. The aggregate follows what the
    chart stacks, not whether it carries a color channel.
    """
    return "sum" if stacked and sort_by == measure_field else "min"


def sorted_series_by_stack_order(
    series: list[str],
    data: Rows,
    series_field: str,
    stack_order: str | None,
    *,
    y_field: str = "",
) -> list[str]:
    """Return *series* sorted for stacked chart rendering by stack_order.

    Baseline = cumulative zero (the series placed first paints at the bottom).
    - None / "value": largest global sum at baseline (matches VL's joinaggregate default).
      Requires *y_field* to be set.
    - "alphabetical": alphabetically first series at baseline.
    - "data": globally first-encountered series at baseline (first-encounter in *data*).
    """
    if stack_order == "alphabetical":
        return sorted(series)
    if stack_order == "data":
        encounter: dict[str, int] = {}
        for row in data:
            s = row.get(series_field)
            if s is not None:
                key = str(s)
                if key not in encounter:
                    encounter[key] = len(encounter)
        unseen_rank = len(encounter)

        def _first_encounter_rank(name: str) -> tuple[int, str]:
            # Not-yet-seen series sorts last (stable "first encounter"
            # order) — a ranking tie-break, not a fallback masking bad input.
            rank = encounter.get(
                name, unseen_rank
            )  # type-state: silent_fallback — ranking tie-break, not masked bad input
            return rank, name

        return sorted(series, key=_first_encounter_rank)
    # None / "value": sort by descending global sum.
    global_sums: dict[str, float] = {}
    for row in data:
        s = row.get(series_field)
        y = row.get(y_field)
        if s is None or y is None:
            continue
        key = str(s)
        # Accumulator init, not a fallback masking bad input.
        running = global_sums.get(
            key, 0.0
        )  # type-state: silent_fallback — accumulator init, not masked bad input
        global_sums[key] = running + float(y)

    def _global_sum_rank(name: str) -> tuple[float, str]:
        # Unseen series sorts last (lowest priority) — a ranking default,
        # not a fallback masking bad input.
        total = global_sums.get(
            name, 0.0
        )  # type-state: silent_fallback — unseen series sorts last, not masked bad input
        return -total, name

    return sorted(series, key=_global_sum_rank)


def cumulative_stack_midpoints(
    data: Rows,
    x_field: str,
    y_field: str,
    series_field: str,
    series_names: list[str],
    sort_by: str,
    descending: bool,
    stack_mode: str = "zero",
    stack_order: str | None = None,
) -> list[tuple[str, float]]:
    """Return (series, x_midpoint) for the top categorical row (un-nudged).

    The "top row" is the first categorical value — first in query order, or
    first under an authored ``sort:``. Midpoint is the cumulative x
    at the series segment's center, ordered through the same ``stack_order``
    authority as the emitted segments. These are the exact positions the
    horizontal rail renders at — no vertical dodge/nudge pass exists for this
    layout; a colliding rail is disqualified wholesale at resolve time
    (``_horizontal_rail_labels_would_collide`` in
    ``compile/resolve/chart/bar.py``) rather than negotiated here.

    A series absent from the top row is zero-width there and anchors on the
    seam between its neighbors — see ``_stacked_midpoints`` (the vertical
    rail's own, unrelated anchor helper in render/chart/features/endpoint_labels.py).

    For ``stack_mode == "normalize"``, midpoints are divided by the top-row total
    so they land on the 0..1 scale that VL renders for normalize stacks.
    """
    x_values = x_domain_order(
        data,
        x_field,
        sort_by,
        descending,
        op=bar_sort_op(sort_by, y_field, stacked=stack_mode != "none"),
    )
    if not x_values:
        return []
    top_row_x = x_values[0]

    # Gather y-values per series for the top row.
    series_y: dict[str, float] = dict.fromkeys(series_names, 0.0)
    for row in data:
        s = row.get(series_field)
        y = row.get(y_field)
        if s is None or y is None:
            continue
        key = str(s)
        if row.get(x_field) == top_row_x:
            series_y[key] = float(y)

    if not series_y:
        return []

    series_order = sorted_series_by_stack_order(
        series_names, data, series_field, stack_order, y_field=y_field
    )

    total = sum(series_y[s] for s in series_order)
    result: list[tuple[str, float]] = []
    cumulative = 0.0
    for s in series_order:
        y = series_y[s]
        mid = cumulative + y / 2.0
        if stack_mode == "normalize" and total > 0:
            mid = mid / total
        result.append((s, mid))
        cumulative += y
    return result


def measured_label_padding(labels: list[str], family: str, size: float) -> float:
    """Gutter width so the widest ``labels`` entry's near edge sits at the tick.

    Returns 0.0 for an empty label list (nothing to reserve for). Does NOT
    add tick length: Vega-Lite's own ``labelPadding`` is already measured
    from the tick's outer edge, not the axis line — confirmed empirically
    (a vl-convert probe varying ``tickSize``/``labelPadding``/``ticks``
    independently shows the rendered label anchor always lands at
    ``(tickSize if ticks-visible else 0) + labelPadding``). Adding tick
    length again here double-counts it whenever ticks are visible, and adds
    dead space even when they aren't — this is the "labels moved further
    from the axis than they should" bug. Callers no longer need to resolve
    or pass a tick size for this calculation.
    """
    if not labels:
        return 0.0
    measurer = get_font_measurer(family)
    max_width = max(measurer.measure(label, size) for label in labels)
    return max_width + _BREATHING_ROOM_PX


def cap_padding_to_label_limit(padding: float, label_limit: float) -> float:
    """Cap a computed gutter at what Vega-Lite will actually render.

    ``measured_label_padding`` sizes the gutter to the widest label's full,
    untruncated text width. Vega-Lite truncates any rendered label wider than
    ``labelLimit`` (``axis.labels.max_width``, or its own ``DEFAULT_VL_LABEL_LIMIT``
    when unset) to that width plus an ellipsis — so reserving more than
    ``label_limit + breathing_room`` leaves dead gutter space no rendered
    label ever fills. Capping the final padding value is equivalent to
    capping each label's width before taking the max (min/max commute here),
    so this needs no access to the underlying label list.
    """
    return min(padding, label_limit + _BREATHING_ROOM_PX)


def layered_endpoint_rail_shape(
    x: str | list[str] | None, y: str | list[str] | None
) -> bool:
    """True when x/y are both plain scalar columns.

    Shared by the resolve-time y-axis auto-orient (``_bake_ay_orient`` in
    compile/resolve/chart/_axes.py) and the render-time firing gate
    (``EndpointLabelFeature.applies_to`` in
    render/chart/features/endpoint_labels.py), which must agree on whether a
    layered chart's endpoint-label rail can fire at all — otherwise the axis
    flips left for a pane that never renders. The layered rail
    (``EndpointLabelFeature._apply_layered_single_series``) anchors one shared
    y-scale off a single base x/y pair; a folded wide y (list) or an absent
    x/y has no single endpoint to draw the base series' own anchor from.
    """
    return isinstance(x, str) and isinstance(y, str)


def layered_endpoint_rail_fires(
    shape_ok: bool, layer_is_colorless: Sequence[bool]
) -> bool:
    """True when the layered single-series endpoint-label rail can fire.

    The single answer to "which series will the rail actually name?",
    shared by compile's resolve-time gates (``_bake_ay_orient``,
    ``_suppress_legend_for_endpoint_labels`` in compile/resolve/chart/_axes.py)
    and render's firing gate (``EndpointLabelFeature.applies_to`` in
    render/chart/features/endpoint_labels.py) — each call site must reach
    the same verdict, or the axis flips/legend suppresses for a rail that
    renders differently than assumed.

    ``shape_ok`` is the caller's own ``layered_endpoint_rail_shape(x, y)``.
    ``layer_is_colorless`` is one ``layer.color is None`` per layer, in any
    order — this function owns the "at least one, and every one" combination
    so that fact isn't re-approximated three different ways at three call
    sites (the exact drift that caused two prior review rounds: a layer
    with its own color field splits into several sub-series with no single
    endpoint to anchor one label on, ``_apply_layered_single_series`` skips
    it — so rather than naming only the colorless layers on the rail and
    leaving that one sub-series to the legend, the whole rail falls back to
    legend-only).
    """
    return shape_ok and bool(layer_is_colorless) and all(layer_is_colorless)
