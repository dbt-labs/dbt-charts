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

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from d3_format import parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatAliases,
    FormatConfig,
    Notation,
    ResolvedFormat,
    signs_before_prefix,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX,
)
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.text.format_d3 import (
    SignPlacement,
    is_d3_si_spec,
    round_aware_spec,
)
from dbt_charts.core.text.numeral_scale import build_decimal_pad_table
from dbt_charts.core.text.predefined_formats import (
    ALL_PREDEFINED_NAMES,
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_SPECS,
    PREDEFINED_SUB_UNIT_FALLBACK,
    PREDEFINED_TIME_NAMES,
    PREDEFINED_TIME_SPECS,
    PredefinedNumberFormat,
    PredefinedTimeFormat,
    si_sub_unit_floor,
)


def _format_raw_str(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
) -> str | None:
    """The authored spec, before name/alias resolution; None for an affix-only input."""
    if format_input is None:
        return None
    if isinstance(format_input, FormatConfig):
        return format_input.spec or None
    if isinstance(format_input, dict):
        return format_input.get("spec") or None
    return str(format_input)


def resolve_format(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatAliases | None = None,
) -> str:
    """Convert format input to a d3 format string.

    Resolution order (three-way contract):
    1. style.formats name — its preset, resolved as in 2, else its literal
       target spec, no trim. A board redefining an engine name lands here,
       so the author's spec replaces the engine's.
    2. Predefined enum member — engine-owned spec with round-aware trim.
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
    _raw = _format_raw_str(format_input)
    format_str = _raw or ""  # type-state: silent_fallback — "" sentinel for None

    if not format_str:
        return ""

    if (preset := _alias_preset(format_str, formats)) is not None:
        return resolve_format(preset)
    if formats and format_str in formats:
        aliased = formats[format_str]
        if isinstance(aliased, FormatConfig):
            return aliased.spec or ""  # type-state: silent_fallback — no spec authored
        return aliased

    # Path 2: predefined enum member — house rules, engine-owned spec + trim.
    # Native predefined formatters (PREDEFINED_NATIVE) are not d3 specs; they
    # pass through as-is so format_value / format_kpi_parts can detect them.
    if format_str in PREDEFINED_NUMBER_NAMES:
        if format_str not in PREDEFINED_SPECS:
            return format_str  # native formatter — pass through
        return round_aware_spec(PREDEFINED_SPECS[format_str])
    if format_str in PREDEFINED_TIME_NAMES:
        return PREDEFINED_TIME_SPECS[format_str]

    # Path 3: inline d3 string — native d3, no trim.
    return format_str


def _alias_preset(
    name: str | None, formats: Mapping[str, AuthoredFormat] | None
) -> str | None:
    """The preset an alias names, if its target is one."""
    if not formats or name is None or name not in formats:
        return None
    target = formats[name]
    spec = target.spec if isinstance(target, FormatConfig) else target
    if spec in PREDEFINED_NUMBER_NAMES or spec in PREDEFINED_TIME_NAMES:
        return spec
    return None


def _resolved_raw(
    raw: str | None, formats: Mapping[str, AuthoredFormat] | None
) -> str | None:
    """The identity downstream rules key on: the preset a name resolves to, the
    author's literal spec when a board redefined an engine name (so the engine's
    rules for that name stop applying), else the name itself."""
    if (preset := _alias_preset(raw, formats)) is not None:
        return preset
    if raw in ALL_PREDEFINED_NAMES and formats and raw in formats:
        target = formats[raw]
        return (target.spec if isinstance(target, FormatConfig) else target) or None
    return raw


def sub_unit_spec(raw: str, formats: FormatAliases | None) -> str:
    """The plain-digit spec an engine SI member swaps to below 1: the board's own
    spec for the ``_full`` sibling if it redefined it, else the engine's."""
    sibling = PREDEFINED_SUB_UNIT_FALLBACK[raw]
    if formats and sibling in formats:
        return resolve_format(sibling, formats)
    return PREDEFINED_SPECS[sibling]


def _alias_target(
    spec: str | None, formats: Mapping[str, AuthoredFormat] | None
) -> FormatConfig | None:
    """The ``FormatConfig`` alias target of ``spec``, if any."""
    if formats and spec is not None and spec in formats:
        aliased = formats[spec]
        if isinstance(aliased, FormatConfig):
            return aliased
    return None


def _alias_prefix_suffix(
    spec: str | None, formats: Mapping[str, AuthoredFormat] | None
) -> tuple[str, str]:
    """The alias target's prefix/suffix, empty when it has none."""
    aliased = _alias_target(spec, formats)
    if aliased is None:
        return "", ""
    prefix = aliased.prefix or ""  # type-state: silent_fallback — no prefix
    suffix = aliased.suffix or ""  # type-state: silent_fallback — no suffix
    return prefix, suffix


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
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: Mapping[str, AuthoredFormat] | None = None,
) -> tuple[str, str]:
    """The prefix/suffix of ``format_input``; an explicit affix wins over its alias's."""
    if format_input is None:
        return "", ""
    if isinstance(format_input, FormatConfig):
        prefix = format_input.prefix or ""  # type-state: silent_fallback — no prefix
        suffix = format_input.suffix or ""  # type-state: silent_fallback — no suffix
        if prefix or suffix:
            return prefix, suffix
        return _alias_prefix_suffix(format_input.spec, formats)
    if isinstance(format_input, dict):
        prefix = format_input.get("prefix", "")  # type-state: silent_fallback — unset
        suffix = format_input.get("suffix", "")  # type-state: silent_fallback — unset
        if prefix or suffix:
            return prefix, suffix
        return _alias_prefix_suffix(format_input.get("spec"), formats)
    return _alias_prefix_suffix(format_input, formats)


_OPTIONS = ("notation", "sign_placement", "repeat")
# What a spec-less format takes from its default beside the spec: a redefined
# default carries its own affix and options.
_FILLED_FROM_DEFAULT = ("prefix", "suffix", *_OPTIONS)


def _format_options(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: Mapping[str, AuthoredFormat] | None,
) -> dict[str, Any]:  # type-state: explicit_any — ResolvedFormat constructor kwargs
    """The authored ``_OPTIONS``; an unset one follows the alias the spec names."""
    if isinstance(format_input, FormatConfig):
        own = {k: getattr(format_input, k) for k in _OPTIONS}
        spec = format_input.spec
    elif isinstance(format_input, dict):
        own = {k: format_input.get(k) for k in _OPTIONS}
        spec = format_input.get("spec")
    else:
        own = dict.fromkeys(_OPTIONS)
        spec = format_input
    aliased = _alias_target(spec, formats)
    return {
        k: getattr(aliased, k) if v is None and aliased is not None else v
        for k, v in own.items()
    }


def _format_fields(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatAliases | None,
) -> dict[str, Any]:  # type-state: explicit_any — ResolvedFormat constructor kwargs
    if isinstance(format_input, ResolvedFormat):
        fields = dict(format_input)
    else:
        prefix, suffix = get_format_prefix_suffix(format_input, formats)
        raw = _format_raw_str(format_input)
        options = _format_options(format_input, formats)
        fields = {
            "spec": resolve_format(format_input, formats),
            "prefix": prefix,
            "suffix": suffix,
            **options,
            "raw": _resolved_raw(raw, formats),
        }
    return fields


_Fields = dict[str, Any]  # type-state: explicit_any — ResolvedFormat constructor kwargs


def _fill(fields: _Fields, lower: _Fields) -> _Fields:
    """``fields`` with the spec of a lower tier, and any affix or option it
    leaves unset."""
    return (
        fields
        | {k: lower[k] for k in ("spec", "raw")}
        | {k: lower[k] for k in _FILLED_FROM_DEFAULT if not fields[k]}
    )


_AnyFormat = dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input

_DEFAULT_NAME = {
    "number": PredefinedNumberFormat.number,
    "time": PredefinedTimeFormat.date_short,
}


def column_format(
    kind: Literal["number", "time"],
    formats: FormatAliases | None,
    fmt: AuthoredFormat | _AnyFormat | None = None,
    *,
    engine_default: Callable[[], str] | None = None,
    values: Iterable[float | None] | None = None,
    notation: Notation | None = None,
) -> ResolvedFormat:
    """The format a value of ``kind`` gets in one slot.

    ``fmt`` is the slot's own format after the style cascade, if any. One with a
    spec decides the look whole. Without one (no format, or only an affix), the
    board's default for the kind decides: ``number`` or ``date_short`` through
    ``formats``, so a name the board redefined supplies spec, affix and notation
    together, and an affix ``fmt`` keeps its own.

    ``engine_default`` is a destination's policy for the engine's own default,
    returning the format name or spec to print instead (a KPI compacts it with
    ``.2~s``, ``""`` leaves digits exact; a donut total says ``integer``). It
    never replaces a board's redefinition of the default, which prints as
    written; the name it returns resolves through ``formats`` whole, and the
    default's own affix fills only what that name leaves unset.

    ``values`` is what a Vega-painted slot will paint: Vega bakes one spec per
    slot, so any value inside the sub-unit band pulls an engine SI member to its
    plain-digit sibling. ``notation`` is the destination's register when the
    format sets none.
    """
    fields = None if fmt is None else _format_fields(fmt, formats)
    if fields is None or not fields["spec"]:
        name = _DEFAULT_NAME[kind]
        default = _format_fields(name, formats)
        if engine_default is not None and default["raw"] == name:
            # The destination's name resolves through the board like any other,
            # so a board that redefined it (affix included) prints it whole.
            instead = _format_fields(engine_default(), formats)
            default = (
                instead
                | {"raw": instead["raw"] or None}
                | {k: default[k] for k in _FILLED_FROM_DEFAULT if not instead[k]}
            )
        fields = default if fields is None else _fill(fields, default)
    return ResolvedFormat(**_finished(fields, formats, values, notation))


def _finished(
    fields: _Fields,
    formats: FormatAliases | None,
    values: Iterable[float | None] | None,
    notation: Notation | None,
) -> _Fields:
    """``fields`` with the destination's notation and the sub-unit vote applied."""
    if notation is not None and fields["notation"] is None:
        fields = fields | {"notation": notation}
    if values is not None and fields["raw"] in PREDEFINED_SUB_UNIT_FALLBACK:
        floor = si_sub_unit_floor(fields["spec"], fields["prefix"], fields["suffix"])
        if any(v is not None and floor < abs(v) < 1.0 for v in values):
            fields = fields | {"spec": sub_unit_spec(fields["raw"], formats)}
    return fields


def authored_sign_placement(
    authored: AuthoredFormat | None, formats: FormatAliases | None
) -> SignPlacement | None:
    """The ``sign_placement`` the author wrote, inline or via the alias the spec
    names. A ``ResolvedFormat`` input (a synthesized column) carries another
    surface's format, not one authored for this slot."""
    if isinstance(authored, ResolvedFormat):
        return None
    placement: SignPlacement | None = _format_options(authored, formats)[
        "sign_placement"
    ]
    return placement


def check_anchored_sign(fmt: ResolvedFormat, field_path: str) -> None:
    """Reject ``fmt`` if it signs before its prefix; call where the affix is
    anchored."""
    if signs_before_prefix(fmt):
        raise CompilationError.from_code(
            ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX, field_path=field_path
        )


def column_symbol_mode(
    fmt: ResolvedFormat | None, symbol_mode: Literal["all", "anchors"]
) -> Literal["all", "anchors"]:
    """A table column's symbol mode: its format's ``repeat``, else the table's."""
    if fmt is None or fmt.repeat is None:
        return symbol_mode
    return "anchors" if fmt.repeat == "anchor" else "all"


def resolve_format_parts(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatAliases | None = None,
    *,
    spec: str | None = None,
    notation: Notation | None = None,
) -> ResolvedFormat:
    """Resolve spec, prefix, suffix and notation; idempotent on a ``ResolvedFormat``.

    Resolves what was authored and nothing more: a slot's default comes from
    ``column_format``. ``spec``, when given, replaces the resolved digits (a
    baked tick spec); ``notation`` is the destination's register when none is set.
    """
    fields = _finished(_format_fields(format_input, formats), formats, None, notation)
    if spec is not None:
        fields["spec"] = spec
    return ResolvedFormat(**fields)


def resolve_label_format(
    raw: AuthoredFormat | None,
    formats: FormatAliases | None,
    values: Iterable[float | None] = (),
) -> tuple[ResolvedFormat | None, bool]:
    """Resolve a label format over ``values``; ``(None, False)`` when it has no spec.

    ``is_house`` is True for a predefined SI member or a spec-less affix; a
    literal d3 spec or user alias opts out.
    """
    if raw is None:
        return None, False
    parts = column_format("number", formats, raw)
    if not parts.spec:
        return None, False
    voted = column_format("number", formats, raw, values=values)
    return voted, parts.is_house and is_d3_si_spec(parts.spec)


def kpi_format_native(
    fmt_raw: AuthoredFormat | None,
    formats: FormatAliases | None,
) -> bool:
    """True when the headline's authored format is a literal d3 spec.

    An alias, predefined name or explicit ``notation`` takes house rules; a
    spec-less ``FormatConfig`` is never native.
    """
    fmt_spec = fmt_raw.spec if isinstance(fmt_raw, FormatConfig) else fmt_raw
    if not fmt_spec:
        return False
    _, is_house = resolve_label_format(fmt_raw, formats)
    if isinstance(fmt_raw, FormatConfig) and fmt_raw.notation is not None:
        is_house = True
    return not is_house


def finalize_kpi_value_format(
    format_input: AuthoredFormat | None,
    value: float | None,
    formats: FormatAliases | None = None,
) -> ResolvedFormat | None:
    """The headline's final format: ``column_format`` with the KPI's policy for
    the engine's own ``number`` (``chart_rendering.kpi.compact_spec`` once
    ``|value|`` reaches ``compact_threshold``, exact digits below it), and
    narrative notation unless one is set. A date or text headline has no
    numeric value, so no number look reaches it.
    """
    kpi_rendering = get_chart_rendering().kpi

    def compact() -> str:
        # Not a predefined name: the predefined "compact" is 3 sig figs, one
        # more than a tile wants.
        reached = value is not None and abs(value) >= kpi_rendering.compact_threshold
        return kpi_rendering.compact_spec if reached else ""

    if value is None:
        if format_input is None:
            return None
        return resolve_format_parts(format_input, formats, notation="narrative")
    parts = column_format(
        "number",
        formats,
        format_input,
        engine_default=compact,
        notation="narrative",
    )
    if format_input is None and not (parts.spec or parts.prefix or parts.suffix):
        return None
    return parts


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
