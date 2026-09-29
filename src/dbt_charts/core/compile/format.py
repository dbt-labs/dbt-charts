"""Format resolution and finalization — a compile-layer concern.

Resolving a format spec to a concrete d3 string is part of the style cascade,
so it lives in the compile layer where the cascade is baked. The render layer's
d3 formatters (format_utils.py) import ``resolve_format`` from here.
This module also owns ``finalize_kpi_value_format()``, the KPI headline's
data-aware format defaulting rule — baked into ``ResolvedKpiChart.format`` at
resolve, consumed as-is by render.

Three-way resolution contract (see predefined_formats.py's module docstring):

| source         | behavior                                               |
|----------------|---------------------------------------------------------|
| enum member    | house rules: engine spec + round-aware trim             |
| style.formats  | native d3: literal spec, no trim, no post-process       |
| inline d3      | native d3: literal spec, no trim, no post-process       |

House glyphs (MINUS, NULL_DISPLAY) are typography, not format semantics, and
remain universal regardless of resolution path.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from d3_format import parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.text.format_d3 import is_d3_si_spec, round_aware_spec
from dbt_charts.core.text.numeral_scale import build_decimal_pad_table
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_SPECS,
    PREDEFINED_SUB_UNIT_FALLBACK,
    PREDEFINED_TIME_NAMES,
    PREDEFINED_TIME_SPECS,
    si_sub_unit_floor,
)


def resolve_format(
    format_input: str | FormatConfig | dict[str, Any] | None,
    formats: dict[str, str] | None = None,
) -> str:
    """Convert format input to a d3 format string.

    Resolution order (three-way contract):
    1. Predefined enum member — engine-owned spec with round-aware trim.
    2. style.formats alias — literal target spec, no trim.
    3. Inline d3 passthrough — literal spec, no trim.

    This function itself never validates the passthrough case — compile-time
    validation (``compile/validate/formats.py``) rejects specs that are none
    of these three, before this function's caller would raise deep inside
    rendering.

    Args:
        format_input: Format specification (string, FormatConfig, dict, or None)
        formats: The resolved format alias dict (compiled_style.formats).
                 None = no user aliases defined.

    Returns:
        D3 format string, or "" for null/empty input.
    """
    if format_input is None:
        return ""

    if isinstance(format_input, FormatConfig):
        format_str = format_input.spec or ""
    elif isinstance(format_input, dict):
        format_str = format_input.get("spec", "")
    else:
        format_str = str(format_input)

    if not format_str:
        return ""

    # Path 1: predefined enum member — house rules, engine-owned spec + trim.
    # Native predefined formatters (PREDEFINED_NATIVE) are not d3 specs; they
    # pass through as-is so format_value / format_kpi_parts can detect them.
    if format_str in PREDEFINED_NUMBER_NAMES:
        if format_str not in PREDEFINED_SPECS:
            return format_str  # native formatter — pass through
        return round_aware_spec(PREDEFINED_SPECS[format_str])
    if format_str in PREDEFINED_TIME_NAMES:
        return PREDEFINED_TIME_SPECS[format_str]

    # Path 2: user-defined alias — native d3, no trim.
    if formats and format_str in formats:
        return formats[format_str]

    # Path 3: inline d3 string — native d3, no trim.
    return format_str


def tick_min_step_for_format(d3_spec: str) -> float | None:
    """Minimum tick step a fixed-decimal d3 spec can distinguish, or None.

    A quantitative axis with no authored tick cadence lets Vega-Lite pick its
    own "nice" step (e.g. 0.5 on a [0, 2] domain). If the axis's own number
    format has a fixed decimal count coarser than that step, distinct ticks
    round to the same label -- an integer format on 0, 0.5, 1, 1.5, 2 paints
    0, 1, 1, 2, 2. This is the floor that prevents that: the smallest value
    the format can paint as distinct from zero.

    ``d`` (always whole) and ``f`` (fixed ``precision`` decimals) derive
    ``10 ** -precision``. ``%`` derives two extra zeros of precision, since it
    multiplies the underlying value by 100 before applying its own decimals
    (``.0%`` distinguishes down to 0.01, not 1). A precision-less ``f``/``%``
    (a bare ``,f``) falls back to d3's own default of 6 -- the same fallback
    this module's sibling ``decimal_pad_table_for`` already uses, for the
    same reason: d3 itself paints 6 decimals when none is authored, not 0.
    Every other type -- SI (``s``), or no type at all (auto) -- has no fixed
    decimal count to derive from and returns None. Also None for a spec
    ``_d3_parse`` can't parse: a native predefined name (``percent_number``)
    that bypasses d3 entirely.
    """
    if not d3_spec:
        return None
    try:
        parsed = _d3_parse(d3_spec)
    except D3FormatError:
        return None
    precision = 6 if parsed.precision is None else parsed.precision
    if parsed.type == "d":
        return 1.0
    if parsed.type == "f":
        return 10.0**-precision
    if parsed.type == "%":
        return 10.0 ** -(precision + 2)
    return None


def get_format_prefix_suffix(
    format_input: str
    | FormatConfig
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
) -> tuple[str, str]:
    """Extract prefix and suffix from format configuration.

    Lives here (not render/format_utils.py) because resolve_format_for_values
    needs it internally; render still imports it from here, same as
    resolve_format.
    """
    if format_input is None:
        return "", ""
    if isinstance(format_input, FormatConfig):
        prefix = format_input.prefix or ""  # type-state: silent_fallback — no prefix
        suffix = format_input.suffix or ""  # type-state: silent_fallback — no suffix
        return prefix, suffix
    if isinstance(format_input, dict):
        prefix = format_input.get("prefix", "")  # type-state: silent_fallback — unset
        suffix = format_input.get("suffix", "")  # type-state: silent_fallback — unset
        return prefix, suffix
    # String format has no prefix/suffix
    return "", ""


def resolve_format_for_values(
    format_input: str
    | FormatConfig
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: dict[str, str] | None,
    values: Iterable[float | None],
) -> str:
    """Resolve a format spec once for a whole Vega-painted slot, voting from its data.

    ``format_value``/``format_kpi_parts`` (render/format_utils.py) apply
    ``si_sub_unit_floor`` per value because Python paints each KPI/table cell
    individually. Vega paints per-datum inside its own runtime, so no Python
    code runs per value there — the floor has to be decided once, at
    resolve, from the values the slot will actually paint, and baked into a
    plain spec string. This is the same shape as
    ``finalize_kpi_value_format`` (data-aware, decided once, baked before
    render).

    Per-set semantics: **any** value in ``values`` that falls in the sub-$1
    band pulls the *whole* slot to the plain-digit fallback, even members
    that are >= $1 — a donut's center total and its slice tooltips vote on
    one set (pass both) so they never disagree about the same 67 cents. A
    mixed set ($0.42 next to $3.00) paints every value in the two-decimal
    register rather than misreading the sub-$1 member as SI milli.

    Args:
        format_input: Format specification, as passed to resolve_format.
        formats: Theme format alias dict (compiled_style.formats).
        values: Every value this slot will paint (e.g. a donut's theta
            column plus its sum, or a cartesian family's quantitative
            channel values). Non-numeric callers filter before calling; a
            None entry is skipped, matching format_value's own null guard.
    """
    resolved = resolve_format(format_input, formats)
    raw = (
        format_input.spec
        if isinstance(format_input, FormatConfig)
        else format_input
        if isinstance(format_input, str)
        else None
    )
    if raw not in PREDEFINED_SUB_UNIT_FALLBACK:
        return resolved
    prefix, suffix = get_format_prefix_suffix(format_input)
    floor = si_sub_unit_floor(resolved, prefix, suffix)
    if any(v is not None and floor < abs(v) < 1.0 for v in values):
        return PREDEFINED_SPECS[PREDEFINED_SUB_UNIT_FALLBACK[raw]]
    return resolved


def resolve_label_format(
    raw: str | FormatConfig | None,
    formats: dict[str, str] | None,
) -> tuple[str | None, bool]:
    """Resolve a label/KPI format and decide whether house rules apply.

    ``is_house`` is True when the raw value is a predefined enum member whose
    resolved spec is SI-shaped. Predefined members are engine-owned semantics;
    a literal d3 spec or a user alias is always a native-d3 opt-out.

    Returns (resolved_spec, is_house); resolved_spec is None both when raw is
    None and when raw is a FormatConfig with no spec (e.g. prefix/suffix only).
    """
    if raw is None:
        return None, False
    if isinstance(raw, FormatConfig):
        if raw.spec is None:
            return None, False
        raw_str = raw.spec
    else:
        raw_str = raw
    if not raw_str:
        return None, False
    resolved = resolve_format(raw, formats)
    is_house = raw_str in PREDEFINED_NUMBER_NAMES and is_d3_si_spec(resolved)
    return resolved or None, is_house


# d3's first SI prefix ("k") engages at 1000. Below it, ".2s" doesn't compact —
# it rounds to 2 significant figures on plain digits instead (131 -> "130"),
# which drops information the author never asked to lose. Digit-integrity
# rule: rounding is acceptable under SI compaction, never on plain digits —
# so the default only compacts once compaction is real.
_KPI_SI_COMPACT_THRESHOLD = 1000.0


def finalize_kpi_value_format(
    format_input: str | FormatConfig | None,
    value: float | None,
) -> FormatConfig | None:
    """Finalize the KPI headline's number format from its cascaded style and value.

    KPI is a hero number that the reader pauses on, so it defaults to the
    narrative notation register (``1.5mn``) instead of the analytic register
    (``1.5 M``) used on axis ticks and table cells. When neither spec nor
    notation is authored, and ``value`` is large enough to actually compact
    (``|value| >= 1000``), it also defaults to compact SI form (``".2~s"``) so
    narrative notation shows up — notation alone is a no-op against a non-SI
    spec. Below that threshold there is nothing to compact, so the value
    renders its exact digits (no spec at all) rather than a misleadingly
    rounded plain number. An explicit spec always wins over the SI default.
    """
    compact_eligible = value is not None and abs(value) >= _KPI_SI_COMPACT_THRESHOLD
    if isinstance(format_input, str):
        return FormatConfig(spec=format_input, notation="narrative")
    spec = format_input.spec if format_input is not None else None
    notation = format_input.notation if format_input is not None else None
    if notation is None:
        notation = "narrative"
    if spec is None and notation in ("narrative", "analytic") and compact_eligible:
        # 2 sig figs (the KPI precision) with trim. Not a predefined name: the
        # predefined "compact" is 3 sig figs, one more than a tile wants.
        # FormatConfig.notation above carries "narrative" so the inline spec still
        # gets narrative register.
        spec = ".2~s"
    if format_input is None and spec is None:
        return None
    return FormatConfig(
        spec=spec,
        prefix=format_input.prefix if format_input is not None else None,
        suffix=format_input.suffix if format_input is not None else None,
        notation=notation,
    )


def decimal_pad_table_for(
    fmt: str | None, font_family: str, max_precision: int | None = None
) -> tuple[str, ...]:
    """Pre-computed pad table for a trim-enabled fixed-point spec, or empty.

    Returns a tuple of length ``precision + 2`` (indices 0..precision+1) where
    index ``i`` is the trailing pad for ``missing_len == i``.

    Compute missing_len from the trimmed string via
    ``fractional_digit_count`` (``core/text/numeral_scale.py`` -- DIGIT
    characters only, never a raw ``len(num_str.partition(".")[2])`` count,
    which a trailing non-digit like a magnitude suffix letter or an
    accounting-sign format's closing ")" would corrupt)::

        frac = fractional_digit_count(num_str)
        missing_len = precision - frac if frac else precision + 1

    Then ``num_str += decimal_pad_table[missing_len]`` -- ``decimal_pad_for``
    (``core/text/numeral_scale.py``) is the canonical selector doing exactly
    this.

    Returns ``()`` for non-trim-enabled or non-fixed-point specs (SI, no-spec,
    PREDEFINED_NATIVE, percent, or any d3 spec whose type is not ``"f"`` or
    whose ``trim`` flag is off).

    ``max_precision``, when given, caps the table at the caller's own
    observed maximum fractional depth rather than the spec's full declared
    precision. A column whose format has no explicit precision (a bare
    ``~s``/``~f``) falls back to 6 -- correct for the format's own
    significant-figure contract, but usually far wider than any row's
    actual trimmed depth ever reaches, over-provisioning the pad table (and
    the column width measured against it) for a case that in practice never
    occurs. The cap only ever narrows the table; it never widens one that
    would otherwise be built from a real precision.
    """
    if not fmt:
        return ()
    try:
        spec = _d3_parse(fmt)
    except D3FormatError:
        # PREDEFINED_NATIVE names (percent_number, date_short, …) and strftime
        # directives are not d3 grammar -- D3FormatError is the expected normal
        # path for any format that isn't a literal d3 spec.
        return ()
    if not spec.trim or spec.type != "f":
        return ()
    precision = spec.precision if spec.precision is not None else 6
    if max_precision is not None:
        precision = min(precision, max_precision)
    # precision=0 means no fractional digits are ever produced; trimming is a
    # no-op, so mixed-depth cannot occur and the pad table would only inflate.
    if precision == 0:
        return ()
    digit_unit, dot_unit = compose_decimal_units(font_family)
    return build_decimal_pad_table(precision, digit_unit, dot_unit)
