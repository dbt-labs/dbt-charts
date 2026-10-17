"""Detector: WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER — see its `doc`
in core/diagnostics/codes_render.py for what this fires on.

Detection rule (v1, name-match only):

  Currency signals — field name (lowercased) ends in:
    _usd, _dollars, _revenue, _amount, _price, _cost, _spend,
    _gmv, _arr, _mrr
  or contains the substrings: revenue, dollars, usd.

  Percent signals — field name (lowercased) ends in:
    _pct, _percent, _percentage, _rate, _share
  or contains the substring: percent.

  Either set also matches a bare field name (no prefix) that reliably implies
  money/percent standalone — currency: usd, dollars, revenue, price, gmv, arr,
  mrr; percent: pct, percent, percentage, share. Generic nouns that only
  signal a kind when prefixed (amount, rate, cost, spend) are excluded
  from the bare set; only their `_`-prefixed suffix form still matches.

  Fire when a matching field's effective y-axis format does not suit its kind.
  Every format source (authored ``style.axis_y.labels.format``, the query's
  ``formats:``, the theme default) is baked into the single
  resolved ``style.axis_y.labels.format`` by the time this detector runs, so it reads
  that one value rather than trying to recover authored-vs-default intent. A
  percent format carries ``%``; a currency format carries ``$``; the generic SI
  default (``.3~s``) suits neither.
  - Cartesian charts: check the chart's ``style.axis_y.labels.format``.
  - Overlay layers: once any layer pins ``axis_y.position`` the chart resolves
    its y axes independently, and a layer that sets its own
    ``axis_y.labels.format`` (a dual-axis combo's percent line) renders against
    it; a layer that sets none inherits the chart's. With no side pinned there
    is one shared axis and the chart's format wins, whatever a layer authors.
    One warning per layer whose y field matches a signal and whose rendered
    format is unfit.

  Skip: charts with no y-axis encoding (callout, text, markdown, pivot,
  pie/donut, map families) that do not carry a layer list.

KPI: the headline ``value`` gets the same name check against its resolved
format, when the first row's cell is a number. The ``support`` value is a change
(``revenue_change`` shown as ``+4%`` or ``+$2k``), so it warns only when its
format suits neither kind.

Table columns: a visible column with no resolved format whose non-null values
are all numbers warns when its name classifies (the same ``_classify``), else
when the values are all real numbers in [0, 1] and not all exactly 0 or 1
(a flag), a likely fraction; unformatted it renders as plain digits ("0.4367"
-> "0.4367"). One warning per column. Pivot measure columns are not materialized
at resolve and are not checked.

Out of scope for v1: value-range secondary signal for the y-axis (median >100 for
currency; all values in [0,1] for percent). Add as a tightener if precision
validation surfaces false positives.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart,
    ResolvedKpiChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_fields
from dbt_charts.core.diagnostics import (
    WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER,
    Diagnostic,
)
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.text.numeral_scale import UNCOMPACTED_COLUMN_SPEC
from dbt_charts.core.utils import Rows, coerce_numeric_cell

# Cartesian chart types whose y-axis renders and may need a format. Overlay
# layers ride on one of these base types, so they need no entry of their own.
_Y_AXIS_TYPES = frozenset(
    {
        "bar",
        "line",
        "area",
        "scatter",
        "heatmap",
    }
)

# Currency: name suffix signals (checked after lowercasing field name).
_CURRENCY_SUFFIXES: frozenset[str] = frozenset(
    {
        "_usd",
        "_dollars",
        "_revenue",
        "_amount",
        "_price",
        "_cost",
        "_spend",
        "_gmv",
        "_arr",
        "_mrr",
    }
)

# Currency: substring signals (field name must contain one of these).
_CURRENCY_SUBSTRINGS: frozenset[str] = frozenset({"revenue", "dollars", "usd"})

# Percent: name suffix signals.
_PERCENT_SUFFIXES: frozenset[str] = frozenset(
    {"_pct", "_percent", "_percentage", "_rate", "_share"}
)

# Percent: substring signals.
_PERCENT_SUBSTRINGS: frozenset[str] = frozenset({"percent"})

# Bare-name signals (curated, not derived — see module docstring).
_CURRENCY_BARE_NAMES: frozenset[str] = frozenset(
    {"usd", "dollars", "revenue", "price", "gmv", "arr", "mrr"}
)
_PERCENT_BARE_NAMES: frozenset[str] = frozenset(
    {"pct", "percent", "percentage", "share"}
)

# Trailing "_<code>" tokens whose symbol the fix suggestion names (from
# core/text/humanize.py's unit suffixes). "usd" is excluded: the dollar spec fits.
_CURRENCY_SYMBOLS = {"eur": "€", "gbp": "£"}


def _classify(field_name: str) -> str | None:
    """Return 'currency', 'percent', or None for the given field name.

    Suffixes beat substrings, and a percent suffix beats any currency signal:
    ``revenue_share`` is a ratio of revenue, not revenue.
    """
    name = field_name.lower()
    if any(name.endswith(s) for s in _PERCENT_SUFFIXES):
        return "percent"
    if any(name.endswith(s) for s in _CURRENCY_SUFFIXES):
        return "currency"
    if name in _CURRENCY_BARE_NAMES or any(s in name for s in _CURRENCY_SUBSTRINGS):
        return "currency"
    if name in _PERCENT_BARE_NAMES or any(s in name for s in _PERCENT_SUBSTRINGS):
        return "percent"
    return None


def _format_suits_kind(fmt: ResolvedFormat | None, kind: str) -> bool:
    """Whether a resolved number format renders ``kind`` sanely.

    The generic SI default suits neither kind. A percent format carries ``%``,
    a currency format ``$``; an authored affix ("€") counts as currency, and
    only a ``%`` in it as percent.
    """
    if fmt is None:
        return False
    affix = fmt.prefix + fmt.suffix
    if kind == "currency" and affix:
        return True
    if kind == "percent" and "%" in affix:
        return True
    if not fmt.spec:
        return False
    return "%" in fmt.spec if kind == "percent" else "$" in fmt.spec


def _currency_format_hint(field: str) -> str:
    """The ``{currency_format}`` of the fix suggestion: the field's "_<code>"
    currency (``revenue_eur`` -> "€"), else the dollar spec."""
    tokens = field.lower().split("_")
    code = tokens[-1] if len(tokens) > 1 else ""
    if code not in _CURRENCY_SYMBOLS:
        return "a currency format (e.g. `$,.2f`)"
    symbol = _CURRENCY_SYMBOLS[code]
    return f"a `,.2f` format with `prefix: {symbol!r}`"


_PERCENT_FORMAT_HINT = (
    "a percent format (`.1%` for a 0-1 share, "
    "or `suffix: '%'` for values already in percent points)"
)


def _diagnostic(
    chart_id: str,
    field: str,
    kind: str,
    surface: str,
    format_state: str,
    format_key: str,
    suggestion: str,
    path: str | None = None,
) -> Diagnostic:
    code = WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER
    return Diagnostic.from_code(
        code,
        chart=chart_id,
        # `format` is usually an *absent* key (that is the complaint), so the path
        # leans on the candidate walk to land on the nearest present parent.
        path=path or f"charts.{chart_id}.{format_key}",
        field=field,
        message=code.message_template.format(
            chart_id=chart_id,
            field=field,
            kind=kind,
            surface=surface,
            format_state=format_state,
        ),
        fix=code.fix_template.format(format_key=format_key, suggestion=suggestion),
    )


def _format_state(fmt: ResolvedFormat | None) -> str:
    return (
        f"format is {fmt.spec!r}" if fmt is not None and fmt.spec else "has no format"
    )


def _warn(
    chart_id: str, field: str, kind: str, axis_format: str | None, layer: int | None
) -> Diagnostic:
    """``layer`` is the index of the layer whose own axis needs the format, if any."""
    axis = "style.axis_y" if layer is None else f"layers.{layer}.axis_y"
    key = "style.axis_y" if layer is None else f"layers[{layer}].axis_y"
    currency_format = (
        _currency_format_hint(field)
        if kind == "currency"
        else "a currency format (e.g. `$,.2f`)"
    )
    return _diagnostic(
        chart_id,
        field,
        kind,
        "y-axis",
        "has no format" if axis_format is None else f"format is {axis_format!r}",
        f"{key}.labels.format",
        f"{currency_format} or {_PERCENT_FORMAT_HINT}",
        # `format` lives on `axis_y.labels`, not on `axis_y`: the bare
        # `axis_y.format` spelling is pre-0.4.0 and emits a migration warning.
        path=f"charts.{chart_id}.{axis}.labels.format",
    )


def _kind_suggestion(field: str, kind: str) -> str:
    return _currency_format_hint(field) if kind == "currency" else _PERCENT_FORMAT_HINT


def _y_axis_checks(
    chart: ResolvedChart,
) -> list[tuple[str, ResolvedFormat | None, int | None]]:
    """``(y field, the format its axis renders with, own-axis layer index)`` per y field.

    Mirrors the overlay emitter: a layer's own ``axis_y`` is rendered only when
    some layer pins ``axis_y.position``, which resolves every layer's y axis
    independently — each seeded from the chart's axis, so a layer without its
    own format still renders the chart's. Otherwise all layers share the
    chart's axis and format.
    Everything else (kpi, table, pie, maps, …) has no y-axis to format and
    yields nothing.
    """
    if not isinstance(chart, _CartesianResolvedChartFields):
        return []
    if chart.chart_type not in _Y_AXIS_TYPES:
        return []
    base_format = chart.style.axis_y.labels.format
    fields: list[str] = []
    if chart.y:
        # Wide charts fold y: list via VL's transform; check the authored measures,
        # not the synthetic WIDE_VALUE_FIELD that replaces chart.y at resolve time.
        if wide_fields := wide_measure_fields(chart):
            fields.extend(wide_fields)
        else:
            # Heatmap is the only remaining family that can still carry a
            # list y (its own multi-measure render path, not the fold).
            fields.extend(chart.y if isinstance(chart.y, list) else [chart.y])
    checks: list[tuple[str, ResolvedFormat | None, int | None]] = [
        (field, base_format, None) for field in fields
    ]
    if isinstance(chart, LayeredResolvedChart):
        independent = any(layer.axis_y.position for layer in chart.layers)
        for idx, layer in enumerate(chart.layers):
            if not layer.y:
                continue
            if not independent:
                checks.append((layer.y, base_format, None))
                continue
            labels = layer.axis_y.labels
            own_format = labels.format if labels is not None else None
            checks.append(
                (layer.y, own_format if own_format is not None else base_format, idx)
            )
    return checks


def _is_unformatted_fraction(rows: Rows, name: str) -> bool:
    """Whether column ``name``'s non-null values are all numbers in [0, 1] and
    not all exactly 0/1 (a flag)."""
    present = [v for row in rows if (v := row.get(name)) is not None]
    numbers = [n for v in present if (n := coerce_numeric_cell(v)) is not None]
    if not present or len(numbers) != len(present):
        return False
    return all(0 <= n <= 1 for n in numbers) and any(n not in (0, 1) for n in numbers)


def _all_numeric(rows: Rows, name: str) -> bool:
    present = [v for row in rows if (v := row.get(name)) is not None]
    return bool(present) and all(coerce_numeric_cell(v) is not None for v in present)


def _table_column_warnings(
    chart_id: str, chart: ResolvedTableChart, rows: Rows
) -> list[Diagnostic]:
    warnings: list[Diagnostic] = []
    if chart.columns is None:
        return warnings
    for name, column in chart.columns.items():
        # A column the resolver baked exact digits onto is still unformatted.
        if (
            column.format is not None and column.format.raw != UNCOMPACTED_COLUMN_SPEC
        ) or column.visible is False:
            continue
        format_key = f"style.columns.{name}.format"
        if (kind := _classify(name)) is not None:
            if not _all_numeric(rows, name):
                continue
            suggestion = _kind_suggestion(name, kind)
        elif _is_unformatted_fraction(rows, name):
            kind = "a fraction between 0 and 1"
            suggestion = (
                "a percent format such as `.1%`, or a plain format "
                "like `.2f` if the values are not fractions"
            )
        else:
            continue
        warnings.append(
            _diagnostic(
                chart_id,
                name,
                kind,
                "table column",
                "has no format",
                format_key,
                suggestion,
            )
        )
    return warnings


def _kpi_warnings(
    chart_id: str, chart: ResolvedKpiChart, rows: Rows
) -> list[Diagnostic]:
    """The headline and the support value, each against its own format."""
    if not rows:
        return []
    checks = [(chart.value, chart.format, "KPI value", "style.value.format", False)]
    if chart.support is not None and chart.support.value is not None:
        checks.append(
            (
                chart.support.value,
                chart.support.format,
                "KPI support value",
                "style.support.format",
                True,
            )
        )
    warnings: list[Diagnostic] = []
    for field, fmt, surface, format_key, is_change in checks:
        kind = _classify(field)
        if kind is None or coerce_numeric_cell(rows[0].get(field)) is None:
            continue
        kinds = ("currency", "percent") if is_change else (kind,)
        if any(_format_suits_kind(fmt, k) for k in kinds):
            continue
        warnings.append(
            _diagnostic(
                chart_id,
                field,
                kind,
                surface,
                _format_state(fmt),
                format_key,
                _kind_suggestion(field, kind),
            )
        )
    return warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per y field, KPI value or table column whose format
    is unfit for its kind.

    A currency/percent-named y field warns unless the format of the axis it
    renders on suits that kind (see ``_format_suits_kind``).
    """
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if chart_id in ctx.chart_results:
            rows = ctx.chart_results[chart_id]
            if isinstance(chart, ResolvedTableChart):
                warnings += _table_column_warnings(chart_id, chart, rows)
            elif isinstance(chart, ResolvedKpiChart):
                warnings += _kpi_warnings(chart_id, chart, rows)
        for field, axis_format, layer in _y_axis_checks(chart):
            kind = _classify(field)
            if kind is not None and not _format_suits_kind(axis_format, kind):
                spec = axis_format.spec if axis_format is not None else None
                warnings.append(_warn(chart_id, field, kind, spec, layer))

    return warnings
