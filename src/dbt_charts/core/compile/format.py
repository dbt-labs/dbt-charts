"""Format resolution and finalization — a compile-layer concern.

Resolving a format spec to a concrete d3 string is part of the style cascade,
so it lives in the compile layer where the cascade is baked. The render layer's
d3 formatters (format_value.py) import ``resolve_format`` from here.
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

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any, Literal

from d3_format import parse as _d3_parse
from d3_format.errors import D3FormatError
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatConfig,
    FormatTable,
    Notation,
    ResolvedFormat,
    signs_before_prefix,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED,
    ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
    ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX,
)
from dbt_charts.core.font_measure import compose_decimal_units
from dbt_charts.core.text.currencies import CURRENCIES
from dbt_charts.core.text.format_d3 import (
    SignPlacement,
    is_d3_si_spec,
    is_time_format,
    round_aware_spec,
)
from dbt_charts.core.text.numeral_scale import (
    SUB_UNIT_SCIENTIFIC_FLOOR,
    build_decimal_pad_table,
    sub_unit_digit_format,
    sub_unit_scientific_format,
)
from dbt_charts.core.text.predefined_formats import (
    ALL_PREDEFINED_NAMES,
    GRAIN_FORMAT_NAMES,
    GRAIN_NAMES,
    PREDEFINED_NATIVE,
    PREDEFINED_NUMBER_NAMES,
    PREDEFINED_SPECS,
    PREDEFINED_SUB_UNIT_FALLBACK,
    PREDEFINED_TEXT_NAMES,
    PREDEFINED_TIME_NAMES,
    PREDEFINED_TIME_SPECS,
    SUB_UNIT_FLOOR,
    PredefinedNumberFormat,
    PredefinedTimeFormat,
    is_money_format,
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
    formats: FormatTable | None = None,
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

    preset = _alias_preset(format_str, formats)
    if preset is not None and preset != format_str:
        # Through the board's own table, so `revenue: currency` prints what the
        # board made of `currency`. A name aliased to itself is the engine's own.
        return resolve_format(preset, formats)
    if preset is None and formats is not None and format_str in formats:
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
        return round_aware_spec(_engine_spec(format_str, formats))
    if format_str in GRAIN_NAMES:
        raise CompilationError.from_code(
            ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED,
            spec=format_str,
            field_path="a format",
        )
    if format_str in PREDEFINED_TIME_NAMES:
        return PREDEFINED_TIME_SPECS[format_str]

    # Path 3: inline d3 string — native d3, no trim.
    return format_str


def _engine_spec(name: str, formats: FormatTable | None) -> str:
    """The engine's spec for ``name``; a currency with no minor unit prints
    ``currency_full`` as whole units."""
    if (
        name == PredefinedNumberFormat.currency_full
        and formats is not None
        and formats.currency is not None
        and not CURRENCIES[formats.currency].digits
    ):
        name = PredefinedNumberFormat.currency_whole
    return PREDEFINED_SPECS[name].d3


def grain_is_redefined(name: str, aliases: Mapping[str, AuthoredFormat]) -> bool:
    """Whether following ``name`` through ``aliases`` ends at something other than
    an engine grain name: a self-alias, or one pointing at an unredefined grain,
    redefines nothing."""
    seen: set[str] = set()
    target: str | None = name
    while target is not None and target in aliases and target not in seen:
        seen.add(target)
        authored = aliases[target]
        target = authored.spec if isinstance(authored, FormatConfig) else authored
    return bool(target) and target not in GRAIN_NAMES


def grain_format_specs(formats: FormatTable | None) -> dict[str, str]:
    """Calendar unit -> the strftime spec a board redefined its ``date_<grain>``
    name to; an unredefined grain is absent."""
    if formats is None:
        return {}
    return {
        unit: resolve_format(name, formats)
        for unit, name in GRAIN_FORMAT_NAMES.items()
        if name in formats and grain_is_redefined(name, formats.aliases)
    }


def _alias_preset(name: str | None, formats: FormatTable | None) -> str | None:
    """The preset an alias names, if its target is one."""
    if formats is None or name is None or name not in formats:
        return None
    target = formats[name]
    spec = target.spec if isinstance(target, FormatConfig) else target
    return spec if spec in ALL_PREDEFINED_NAMES else None


def _resolved_raw(raw: str | None, formats: FormatTable | None) -> str | None:
    """The identity downstream rules key on: the preset a name resolves to, the
    author's literal spec when a board redefined an engine name (so the engine's
    rules for that name stop applying), else the name itself."""
    if (preset := _alias_preset(raw, formats)) is not None:
        # An alias of a name the board redefined follows the redefinition; a name
        # aliased to itself is the engine's own.
        return preset if preset == raw else _resolved_raw(preset, formats)
    if raw in ALL_PREDEFINED_NAMES and formats is not None and raw in formats:
        target = formats[raw]
        return (target.spec if isinstance(target, FormatConfig) else target) or None
    return raw


def sub_unit_spec(raw: str, formats: FormatTable | None) -> str:
    """The plain-digit spec an engine SI member swaps to below 1: the board's own
    spec for the ``_full`` sibling if it redefined it, else the engine's."""
    sibling = PREDEFINED_SUB_UNIT_FALLBACK[raw]
    if formats is not None and sibling in formats:
        return resolve_format(sibling, formats)
    return _engine_spec(sibling, formats)


def sub_unit_spec_for_value(
    raw: str | None, spec: str, value: float, formats: FormatTable | None
) -> str:
    """The spec a per-value surface paints ``value`` with: an engine SI member
    never prints an SI prefix below 1 (zero keeps ``spec``)."""
    magnitude = abs(value)
    if raw not in PREDEFINED_SUB_UNIT_FALLBACK or magnitude == 0 or magnitude >= 1:
        return spec
    if _in_sub_unit_band(value):
        return sub_unit_spec(raw, formats)
    if magnitude < SUB_UNIT_SCIENTIFIC_FLOOR:
        return sub_unit_scientific_format(spec)
    return sub_unit_digit_format(spec)


def _in_sub_unit_band(value: float) -> bool:
    return SUB_UNIT_FLOOR <= abs(value) < 1.0


def _alias_target(spec: str | None, formats: FormatTable | None) -> FormatConfig | None:
    """The ``FormatConfig`` alias target of ``spec``, if any."""
    if formats is not None and spec is not None and spec in formats:
        aliased = formats[spec]
        if isinstance(aliased, FormatConfig):
            return aliased
    return None


def _alias_prefix_suffix(
    spec: str | None, formats: FormatTable | None
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
    that bypasses d3 entirely -- except ``duration``, whose smallest unit is a
    whole second.
    """
    if not d3_spec:
        return None
    if d3_spec == PredefinedNumberFormat.duration:
        return 1.0
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
    formats: FormatTable | None = None,
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


_OPTIONS: tuple[str, ...] = ("notation", "sign_placement", "repeat")
# What a spec-less format takes from its default beside the spec: a redefined
# default carries its own affix and options.
_FILLED_FROM_DEFAULT = ("prefix", "suffix", *_OPTIONS)


def _format_options(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatTable | None,
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
    inherited = _named_options(spec, formats, frozenset())
    return {k: inherited[k] if v is None else v for k, v in own.items()}


# Engine names that carry options beyond their spec: the definition of the name
# itself, so every alias of it inherits them and a board's redefinition (a plain
# string, a FormatConfig) replaces them along with the spec.
_ENGINE_FORMATS: dict[str, FormatConfig] = {
    name: FormatConfig(spec=PREDEFINED_SPECS[name].d3, notation="analytic")
    for name in (
        PredefinedNumberFormat.compact,
        PredefinedNumberFormat.currency_compact,
    )
}


def _named_options(
    name: str | None, formats: FormatTable | None, seen: frozenset[str]
) -> dict[str, Any]:  # type-state: explicit_any — ResolvedFormat constructor kwargs
    """The ``_OPTIONS`` the format named ``name`` carries: its own, else those of
    what it names in turn. A board's entry wins over the engine's; a name aliased
    to itself fills what it leaves unset from the engine's definition."""
    none = dict.fromkeys(_OPTIONS)
    if name is None or name in seen:
        return none
    target = formats[name] if formats is not None and name in formats else None
    own = (
        {k: getattr(target, k) for k in _OPTIONS}
        if isinstance(target, FormatConfig)
        else none
    )
    inner = target.spec if isinstance(target, FormatConfig) else target
    if target is None or inner == name:
        engine = _ENGINE_FORMATS.get(name)
        followed = none if engine is None else {k: getattr(engine, k) for k in _OPTIONS}
    else:
        followed = _named_options(inner, formats, seen | {name})
    return {k: followed[k] if v is None else v for k, v in own.items()}


def format_fields(
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatTable | None,
) -> dict[str, Any]:  # type-state: explicit_any — ResolvedFormat constructor kwargs
    if isinstance(format_input, ResolvedFormat):
        fields = dict(format_input)
    else:
        prefix, suffix = get_format_prefix_suffix(format_input, formats)
        raw = _format_raw_str(format_input)
        options = _format_options(format_input, formats)
        resolved_raw = _resolved_raw(raw, formats)
        if not (prefix or suffix) and resolved_raw in PREDEFINED_SPECS:
            # An authored affix replaces the engine's own unit (`°`).
            suffix = PREDEFINED_SPECS[resolved_raw].suffix
        fields = {
            "spec": resolve_format(format_input, formats),
            "prefix": prefix,
            "suffix": suffix,
            **options,
            "raw": resolved_raw,
        }
    return fields


_Fields = dict[str, Any]  # type-state: explicit_any — ResolvedFormat constructor kwargs


_AnyFormat = dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input

_DEFAULT_NAME = {
    "number": PredefinedNumberFormat.number,
    "time": PredefinedTimeFormat.date_short,
}


def _is_other_kind(fields: _Fields, kind: Literal["number", "time"]) -> bool:
    """Whether ``fields`` spells a look of the other kind: a number look has
    nothing to say about a date, and a date look nothing about a number. A text
    name belongs to neither, so it is never the other kind."""
    spec = fields["spec"]
    return (
        bool(spec)
        and spec not in PREDEFINED_TEXT_NAMES
        and is_time_format(spec) != (kind == "time")
    )


def _merge_tiers(
    kind: Literal["number", "time"],
    formats: FormatTable | None,
    tiers: Sequence[AuthoredFormat | _AnyFormat | None],
    engine_default: Callable[[], str] | None,
) -> tuple[_Fields, bool]:
    """The one merge rule: each field comes from the strongest tier that sets it.

    ``tiers`` runs strongest first. The first is a slot's own format and keeps
    whatever kind it is; the rest are defaults for a column with none of its own,
    and one that spells the other kind contributes nothing, affix included. The default for the kind joins as the
    weakest tier only when no tier sets a spec, so a tier that names its own spec
    never inherits the default's affix. Returns the fields and whether the spec
    came from that default.
    """
    resolved = [
        fields
        for i, tier in enumerate(tiers)
        if tier is not None
        for fields in [format_fields(tier, formats)]
        if i == 0 or not _is_other_kind(fields, kind)
    ]
    from_default = not any(tier["spec"] for tier in resolved)
    if from_default:
        name = _DEFAULT_NAME[kind]
        default = format_fields(name, formats)
        if engine_default is not None and default["raw"] == name:
            # The destination's name resolves through the board like any other,
            # so a board that redefined it (affix included) prints it whole.
            instead = format_fields(engine_default(), formats)
            default = (
                instead
                | {"raw": instead["raw"] or None}
                | {k: default[k] for k in _FILLED_FROM_DEFAULT if not instead[k]}
            )
        resolved.append(default)
    spec_tier = next(tier for tier in resolved if tier["spec"] or tier is resolved[-1])
    fields = {k: spec_tier[k] for k in ("spec", "raw")} | {
        k: next((tier[k] for tier in resolved if tier[k]), resolved[-1][k])
        for k in _FILLED_FROM_DEFAULT
    }
    return fields, from_default


def column_format(
    kind: Literal["number", "time"],
    formats: FormatTable | None,
    fmt: AuthoredFormat | _AnyFormat | None = None,
    *,
    board: AuthoredFormat | _AnyFormat | None = None,
    column: AuthoredFormat | _AnyFormat | None = None,
    engine_default: Callable[[], str] | None = None,
    values: Iterable[float | None] | None = None,
    notation: Notation | None = None,
) -> ResolvedFormat:
    """The format a value of ``kind`` gets in one slot.

    Each layer is its own input, strongest first: ``fmt`` is the chart's own slot
    format; ``column`` is the format a query's ``formats:`` declares for the
    column; ``board`` is the board- or theme-level slot format. Below them sits the board's default for the kind, ``number`` or
    ``date_short`` through ``formats``, so a name the board redefined supplies
    spec, affix and notation together. Callers never pre-merge layers: every
    field (spec, affix, notation, options) comes from the strongest layer that
    sets it, so an affix-only layer keeps its affix and takes its spec from below.

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
    fields, _ = _merge_tiers(kind, formats, (fmt, column, board), engine_default)
    return ResolvedFormat(**_finished(fields, formats, values, notation))


def _finished(
    fields: _Fields,
    formats: FormatTable | None,
    values: Iterable[float | None] | None,
    notation: Notation | None,
) -> _Fields:
    """``fields`` with the destination's notation and the sub-unit vote applied.

    Money only: a Vega-painted ``number`` slot already paints its sub-1 values
    per value (the guarded digit register), so a slot-wide swap would strip SI
    compaction from the slot's larger values for nothing."""
    if notation is not None and fields["notation"] is None:
        fields = fields | {"notation": notation}
    if (
        values is not None
        and fields["raw"] in PREDEFINED_SUB_UNIT_FALLBACK
        and is_money_format(fields["spec"], fields["prefix"], fields["suffix"])
        and any(v is not None and _in_sub_unit_band(v) for v in values)
    ):
        fields = fields | {"spec": sub_unit_spec(fields["raw"], formats)}
    return fields


def reject_native(fmt: ResolvedFormat, field_path: str) -> ResolvedFormat:
    """``fmt``, or ``ERR-FORMAT-NATIVE-IN-VEGA-SLOT`` if it is a native.

    For a slot Vega paints from a bare d3 ``format`` that cannot carry the
    native's expression arm (support table, heatmap and geo tooltips). Checked
    on the resolved format, so a native inherited from a measure format is
    caught as well as an authored one.
    """
    if fmt.spec in PREDEFINED_NATIVE:
        raise CompilationError.from_code(
            ERR_FORMAT_NATIVE_IN_VEGA_SLOT,
            spec=fmt.spec,
            field_path=field_path,
            available=sorted(ALL_PREDEFINED_NAMES - PREDEFINED_NATIVE.keys()),
        )
    return fmt


def authored_sign_placement(
    authored: AuthoredFormat | None, formats: FormatTable | None
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
    formats: FormatTable | None = None,
    *,
    spec: str | None = None,
    notation: Notation | None = None,
) -> ResolvedFormat:
    """Resolve spec, prefix, suffix and notation; idempotent on a ``ResolvedFormat``.

    Resolves what was authored and nothing more: a slot's default comes from
    ``column_format``. ``spec``, when given, replaces the resolved digits (a
    baked tick spec); ``notation`` is the destination's register when none is set.
    """
    fields = _finished(format_fields(format_input, formats), formats, None, notation)
    if spec is not None:
        fields["spec"] = spec
    return ResolvedFormat(**fields)


def resolve_label_format(
    raw: AuthoredFormat | None,
    formats: FormatTable | None,
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
    formats: FormatTable | None,
    fmt: AuthoredFormat | None = None,
    *,
    board: AuthoredFormat | None = None,
    column: AuthoredFormat | None = None,
) -> bool:
    """True when the headline's merged format is a literal d3 spec a layer wrote,
    which prints its own raw characters: not one of the engine's house names, not
    what the engine's default supplied, with no authored notation."""
    fields, from_default = _merge_tiers("number", formats, (fmt, column, board), None)
    return (
        not from_default
        and fields["notation"] is None
        and not (
            fields["raw"] in PREDEFINED_NUMBER_NAMES and is_d3_si_spec(fields["spec"])
        )
    )


def finalize_kpi_value_format(
    fmt: AuthoredFormat | None,
    value: float | None,
    formats: FormatTable | None = None,
    *,
    board: AuthoredFormat | None = None,
    column: AuthoredFormat | None = None,
) -> ResolvedFormat | None:
    """The headline's final format: ``column_format``'s layers with the KPI's
    policy for the engine's own ``number`` (``chart_rendering.kpi.compact_spec``
    once ``|value|`` reaches ``compact_threshold``, exact digits below it), and
    narrative notation unless one is set. A date or text headline has no numeric
    value, so it merges as a date, and nothing is baked when no layer sets
    anything.
    """
    kpi_rendering = get_chart_rendering().kpi

    def compact() -> str:
        # Not a predefined name: the predefined "compact" is 3 sig figs, one
        # more than a tile wants.
        reached = value is not None and abs(value) >= kpi_rendering.compact_threshold
        return kpi_rendering.compact_spec if reached else ""

    layers = (fmt, column, board)
    if all(layer is None for layer in layers):
        if value is None:
            return None
        parts = column_format(
            "number", formats, engine_default=compact, notation="narrative"
        )
        return parts if parts.spec or parts.prefix or parts.suffix else None
    return column_format(
        "time" if value is None else "number",
        formats,
        fmt,
        board=board,
        column=column,
        engine_default=None if value is None else compact,
        notation="narrative",
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


def declared_format(
    declared: Mapping[str, str] | None, columns: str | Collection[str] | None
) -> str | None:
    """The format name a query's ``formats:`` gives every one of ``columns``.

    None when any column has no entry or the entries differ: a destination
    painting several columns with one format has no single column to speak for
    them, so it keeps its own default rather than guess.
    """
    if not declared or columns is None:
        return None
    names = {columns} if isinstance(columns, str) else set(columns)
    entries = {declared.get(name) for name in names}
    return entries.pop() if len(entries) == 1 else None


def missing_format_columns(
    declared: Mapping[str, str] | None, returned: Collection[str]
) -> list[str]:
    """The ``formats:`` keys the query's result does not carry, in authored order."""
    if not declared:
        return []
    return [column for column in declared if column not in returned]
