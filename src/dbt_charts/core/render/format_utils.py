"""D3 formatting utilities for value display.

Stage: RENDER
Purpose: Resolve format aliases from the theme cascade and format values
         using D3-style format strings.

The format alias vocabulary lives in theme YAML (style.formats). Callers
pass compiled_style.formats to resolve_format(); unknown keys and raw d3
specs pass through unchanged for d3 to parse.

The d3-format spec parsing and number formatting is delegated to the
``d3_format`` library (``libs/d3-format/``), which implements byte-for-byte
parity with d3.js across the whole grammar. A spec that renders on a chart
axis (real d3, inside vl-convert) therefore renders identically here, which is
what lets compile validate every format slot against one grammar.

dbt charts-specific extensions over d3-format:
- ``analytic`` notation: SI ``k/M/G/T`` suffixes mapped to ``K/M/B/T`` with a
  space separator (e.g. d3's ``"1.5G"`` becomes ``"1.5 B"``).
- ``narrative`` notation: ``k/M/G/T`` mapped to ``k/mn/bn/trn`` (no space,
  journalistic abbreviations — not part of the d3-format standard). Above
  trillions, narrative borrows the analytic (spaced) suffix — no accepted
  narrative form exists past "trn".
- ``None`` value: rendered as ``NULL_DISPLAY`` (em dash) regardless of spec.

See also:
    - https://d3js.org/d3-format
    - vega_lite.py: Uses these utilities for KPI chart formatting
"""

from typing import Any

from d3_format import format as _d3_format

# resolve_format / resolve_format_parts live in the compile layer
# (format-alias resolution is a cascade concern); imported here for internal
# use by format_value/format_kpi_parts. Render callers must import from
# dbt_charts.core.compile.format directly.
from dbt_charts.core.compile.format import resolve_format, resolve_format_parts
from dbt_charts.core.compile.models.primitives import AuthoredFormat, FormatAliases
from dbt_charts.core.compile.models.style.resolved.table import (
    ResolvedColumnSharedScale,
)
from dbt_charts.core.diagnostics.codes_render import ERR_PERCENT_RANGE
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.text.format_d3 import (
    _D3_TO_ANALYTIC,
    _D3_TO_NARRATIVE,
    NULL_DISPLAY,
    SignPlacement,
    _is_si_spec,
    format_d3,
    split_leading_sign,
)
from dbt_charts.core.text.numeral_scale import SuffixMode, suffix_at_register
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_NATIVE,
    PREDEFINED_SPECS,
    PREDEFINED_SUB_UNIT_FALLBACK,
    PredefinedNumberFormat,
    _with_minus,
    si_sub_unit_floor,
)

# Magnitude heuristic: 0-100-shaped data stored at board value lands at 10x+
# (27.6 for a 27.6% rate, 91.5 for a 91.5% share). Setting the threshold at
# 10 catches those gross mistakes without false-positiving on large legitimate
# ratios — +300% growth (3.0), 9× ROI (9.0). Values below 10 (e.g. 3.2
# meaning 3.2%) are below the threshold and will still mis-render silently —
# the heuristic catches large mistakes, not all mistakes.
_PERCENT_RANGE_THRESHOLD = 10.0


def _check_percent_range(value: int | float, format_spec: str) -> None:
    """Raise if a non-delta percent d3 spec is fed a 0-100-shaped value.

    Guards specs ending in ``%`` that do **not** start with ``+``. The ``+``
    prefix is d3's sign option — it marks a delta/signed format (e.g.
    ``+.1%`` = ``percent_delta``). Deltas legitimately exceed 10.0: +1200%
    growth (12.0) is correct data, not a SQL mistake, so delta specs are
    unconditionally exempt. ``percent`` and ``percent_whole`` are the primary
    targets — they describe shares and rates where >10 is almost always a
    forgotten ÷100 in SQL.

    Callers are responsible for short-circuiting ``None`` before this call.
    """
    if not format_spec.endswith("%") or format_spec.startswith("+"):
        return
    if abs(float(value)) >= _PERCENT_RANGE_THRESHOLD:
        raise RenderError.from_code(
            ERR_PERCENT_RANGE, value=value, format_spec=format_spec
        )


# ============================================================================
# D3 FORMATTING IMPLEMENTATION
# ============================================================================


# Magnitude suffixes (K/M/B/G/…) that format_kpi_parts splits into its suffix
# field. Unlike a currency prefix or a %/unit suffix, these carry the number's
# magnitude, so table `symbol_mode: anchors` must keep them on every row —
# stripping "K" would make 3000 read as "3". Includes both house notation values
# (K/M/B/T) AND raw d3 keys (k/M/G/T): the native-d3 path (inline specs, user
# aliases) emits raw d3 characters, so "G" (giga/billions) must be included or
# it would be stripped on non-first anchor rows. Stripped uniformly: above
# trillions, narrative borrows the analytic (spaced) suffix.
MAGNITUDE_SUFFIXES: frozenset[str] = frozenset(
    v.strip()
    for v in (
        *_D3_TO_ANALYTIC.keys(),
        *_D3_TO_ANALYTIC.values(),
        *_D3_TO_NARRATIVE.values(),
    )
)


def default_number_format() -> str:
    """Engine default format name for a numeric value carrying no explicit format.

    Returns the predefined name ``"number"`` so callers that pass it to
    ``format_value``/``format_kpi_parts`` preserve the house-notation signal.
    Resolution (spec + round-aware trim + analytic notation) happens inside
    those callers via the normal three-way contract.
    """
    return PredefinedNumberFormat.number


def format_value(
    value: int | float | None,
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatAliases | None = None,
) -> str:
    """Format a value using the given format configuration.

    A spec-less affix takes the theme's ``number`` (SI) spec.

    Args:
        value: Numeric value to format
        format_input: Format specification (string, FormatConfig, dict, or None)
        formats: Theme format alias dict from compiled_style.formats

    Returns:
        Formatted string
    """
    parts = resolve_format_parts(
        format_input, formats, no_format_default=PredefinedNumberFormat.number
    )
    format_spec = parts.spec
    prefix, suffix = parts.prefix, parts.suffix
    if format_spec in PREDEFINED_NATIVE:
        number, unit = PREDEFINED_NATIVE[format_spec](value)
        return f"{prefix}{number}{unit}{suffix}"
    if value is not None and parts.raw in PREDEFINED_SUB_UNIT_FALLBACK:
        floor = si_sub_unit_floor(format_spec, prefix, suffix)
        # Strict floor: zero is exactly representable, not "below the minor
        # unit", so it must not take the fallback (si_sub_unit_floor's money
        # floor is 0.0 -- `<` excludes zero there; the non-money floor is
        # 1.0, where this comparison is always false either way).
        if floor < abs(value) < 1.0:
            format_spec = PREDEFINED_SPECS[PREDEFINED_SUB_UNIT_FALLBACK[parts.raw]]
    if value is not None:
        _check_percent_range(value, format_spec)
    effective_notation = (
        parts.notation
        if parts.notation is not None
        else ("analytic" if parts.is_house else None)
    )
    return format_d3(
        value,
        format_spec,
        prefix,
        suffix,
        notation=effective_notation,
        sign_placement=parts.placement,
    )


def _sign_into_prefix(
    explicit_prefix: str,
    d3_prefix: str,
    number: str,
    sign_placement: SignPlacement,
) -> tuple[str, str, str]:
    """Place ``number``'s sign ahead of both prefixes, or (``after_prefix``)
    between the authored prefix and d3's ``$``, which joins the number lane."""
    if not (explicit_prefix or d3_prefix):
        return explicit_prefix, d3_prefix, number
    sign, digits = split_leading_sign(number)
    if explicit_prefix and sign_placement == "after_prefix":
        return explicit_prefix, "", sign + d3_prefix + digits
    return sign + explicit_prefix, d3_prefix, digits


def format_kpi_parts(
    value: int | float | None,
    format_input: AuthoredFormat
    | dict[str, Any]  # type-state: explicit_any — validator/JSON boundary input
    | None,
    formats: FormatAliases | None = None,
    default_number: bool = False,
    native: bool = False,
    shared_scale: ResolvedColumnSharedScale | None = None,
    is_anchor: bool = False,
    keep_affix_gaps: bool = False,
    sign_in_prefix: bool = False,
) -> tuple[str, str, str]:
    """Format a KPI value into (prefix, number, suffix) parts.

    Separates prefix symbols ($), magnitude suffixes (M, K, bn), and unit
    suffixes (%, USD) from the formatted number so they can be rendered or
    positioned independently. An authored prefix/suffix rides in its lane
    beside the unit (``percent_number`` + ``" YoY"`` is ``("", "12.3", "% YoY")``).

    Args:
        value: Numeric value to format
        format_input: Format specification
        formats: Theme format alias dict from compiled_style.formats
        default_number: when ``format_input`` resolves to no spec, apply the
            theme's ``number`` (SI) instead of the exact-digit fallback.
            Table cells pass True; KPI callers leave it False to keep their
            below-threshold exact-digit contract.
        shared_scale: a table column's resolved shared SI/compact magnitude
            (``ResolvedTableColumnConfig.shared_scale``). When set, the value
            is scaled and formatted through ``shared_scale.digit_spec``
            instead of independently picking its own SI suffix -- the table
            analog of an axis ruler's tick painting (mirrors
            ``quantitative_tick_labels``'s ``ruler``-aware branch). ``None``
            (the default) is today's per-row independent SI behavior,
            unchanged.
        is_anchor: whether this row is the column's anchor row (only
            meaningful when ``shared_scale`` is set). In ANCHOR mode the
            magnitude suffix appears only when ``is_anchor`` is True; in
            REPEAT mode it appears on every non-zero row regardless.
        keep_affix_gaps: keep an authored affix's whitespace against the number
            (``"EUR "``); off, lanes are edge-stripped because the painter
            kerns between them.
        sign_in_prefix: place the sign per ``sign_placement`` (a KPI, whose
            prefix lane always paints). Off, it stays in the number lane, which
            ``anchors`` mode keeps when it clears the prefix.
    """
    if value is None:
        return "", NULL_DISPLAY, ""

    parts = resolve_format_parts(
        format_input,
        formats,
        no_format_default=(PredefinedNumberFormat.number if default_number else None),
    )
    explicit_prefix, explicit_suffix = parts.prefix, parts.suffix
    # parts.raw keeps the predefined identity the sub-unit-floor guard keys on,
    # through the spec-less-affix default substitution.
    _raw = parts.raw
    _is_house = parts.is_house
    format_spec = parts.spec
    if not format_spec and default_number:
        # number is always a predefined (house) format. _raw must follow
        # format_spec here, or the sub-unit-floor guard below (keyed on
        # _raw) never sees this row as PREDEFINED_SUB_UNIT_FALLBACK's
        # "number" member and a money-prefixed cell keeps its SI spec.
        _is_house = True
        _raw = default_number_format()
        format_spec = resolve_format(_raw, formats)

    # Money below $1 has no sub-cent unit to name and must not take the SI
    # spec (see si_sub_unit_floor). Only meaningful when this row picks its
    # own spec -- shared_scale has already baked the column's one magnitude,
    # so a per-row floor decision here would second-guess that vote.
    if shared_scale is None and _raw in PREDEFINED_SUB_UNIT_FALLBACK:
        floor = si_sub_unit_floor(format_spec, explicit_prefix, explicit_suffix)
        # See the strict-floor note in format_value: zero must not take the
        # fallback.
        if floor < abs(value) < 1.0:
            format_spec = PREDEFINED_SPECS[PREDEFINED_SUB_UNIT_FALLBACK[_raw]]

    # Predefined native formatters bypass D3 and know their own unit; the
    # authored affix rides around number-and-unit.
    if format_spec in PREDEFINED_NATIVE:
        number, unit = PREDEFINED_NATIVE[format_spec](value)
        if sign_in_prefix:
            explicit_prefix, _, number = _sign_into_prefix(
                explicit_prefix, "", number, parts.placement
            )
        native_prefix = (
            explicit_prefix.lstrip() if keep_affix_gaps else explicit_prefix.strip()
        )
        return native_prefix, number, unit + explicit_suffix.rstrip()
    _check_percent_range(value, format_spec)

    d3_prefix = ""
    d3_suffix = ""
    magnitude_suffix = ""

    if format_spec:
        # Pull $ out of the D3 spec so it renders as a separate element
        if "$" in format_spec:
            d3_prefix = "$"
            format_spec_clean = format_spec.replace("$", "")
        else:
            format_spec_clean = format_spec

        # House notation applies only to predefined enum members; inline d3
        # and user aliases are native-d3 and get no post-process.
        effective_notation = (
            parts.notation
            if parts.notation is not None
            else ("analytic" if _is_house else None)
        )

        # Shared-scale table column: format through the column's one baked
        # magnitude instead of re-deriving a per-row SI suffix -- mirrors
        # quantitative_tick_labels's ruler-aware branch. Never re-enter the
        # value through format_spec_clean's own `~s` type here: that would
        # let d3 pick a *second* suffix on top of the shared one.
        if shared_scale is not None:
            scaled_value = value / (10.0**shared_scale.exponent)
            number_str = _d3_format(shared_scale.digit_spec)(float(scaled_value))
            if value != 0 and (shared_scale.mode is SuffixMode.REPEAT or is_anchor):
                # An explicitly *authored* notation: wins over the
                # mode-derived register -- a shared magnitude changes which
                # rows show the suffix, not the author's chosen register.
                # Deliberately reads `notation` (the raw authored value), not
                # `effective_notation`: the latter also carries the house
                # format's own "default to analytic" fallback (below), which
                # would silently override REPEAT mode's narrative register on
                # every plain `number` column -- shared_scale's own
                # mode-based register is the smarter default here and must
                # not be shadowed by that generic one.
                register = (
                    parts.notation
                    if parts.notation is not None
                    else shared_scale.register
                )
                # .strip(): suffix_at_register's analytic form carries a
                # leading space (e.g. " M") for prose use; every other
                # magnitude_suffix assignment in this function stores the
                # bare token (MAGNITUDE_SUFFIXES is built the same way), so
                # anchors-mode's own suffix.startswith(...) check matches.
                magnitude_suffix = suffix_at_register(
                    shared_scale.exponent, register
                ).strip()
        # SI/compact: split magnitude suffix into its own field. format_spec
        # already carries the round-aware trim (baked once by resolve_format/
        # default_number_format, above) -- no re-injection needed here.
        elif _is_si_spec(format_spec_clean):
            # Format via the lib (without notation post-process), then split suffix
            raw = _d3_format(format_spec_clean)(float(value))
            number_str = raw
            if native or effective_notation is None:
                # No notation substitution: use raw d3 suffix chars (M, k, G, T).
                # native=True: caller requests raw chars explicitly.
                # effective_notation is None: inline-d3 / user-alias path (three-way
                # contract — no house post-process), but symbol_mode still needs the
                # suffix in its own field so anchors-mode can suppress it on non-first
                # rows without stripping it from the embedded number string.
                for d3_sfx in _D3_TO_ANALYTIC:
                    if raw.endswith(d3_sfx):
                        number_str = raw[: -len(d3_sfx)]
                        magnitude_suffix = d3_sfx
                        break
            elif effective_notation == "analytic":
                for d3_sfx, analytic_sfx in _D3_TO_ANALYTIC.items():
                    if raw.endswith(d3_sfx):
                        number_str = raw[: -len(d3_sfx)]
                        magnitude_suffix = analytic_sfx.strip()
                        break
            elif effective_notation == "narrative":
                for d3_sfx, narrative_sfx in _D3_TO_NARRATIVE.items():
                    if raw.endswith(d3_sfx):
                        number_str = raw[: -len(d3_sfx)]
                        # Stripped: above trillions the narrative suffix
                        # borrows the analytic (spaced) form (invariant: no
                        # accepted narrative form exists past "trn"). The
                        # lane split carries visual spacing as geometry
                        # (kpi.py's dx="2"), not a string character, so
                        # every magnitude suffix in this field stays bare.
                        magnitude_suffix = narrative_sfx.strip()
                        break
            # else: effective_notation is None -- no substitution, raw d3 suffix intact
        else:
            number_str = format_d3(
                value, format_spec_clean, notation=effective_notation
            )

        # Pull trailing % that format_d3 appended
        if "%" in format_spec and number_str.endswith("%"):
            d3_suffix = "%"
            number_str = number_str[:-1]
    else:
        # No spec and no default supplied (KPI below-threshold contract):
        # render exact digits — integers plain, floats at 2 decimals. Plain
        # Python f-strings default to ASCII "-"; _with_minus swaps in the
        # house glyph, same as PREDEFINED_NATIVE — every other branch above
        # gets it for free from d3_format.
        v = float(value)
        if v == int(v) and abs(v) < 1e15:
            number_str = _with_minus(f"{int(v):,}")
        else:
            number_str = _with_minus(f"{v:,.2f}")

    number_str = number_str.strip()
    if sign_in_prefix:
        explicit_prefix, d3_prefix, number_str = _sign_into_prefix(
            explicit_prefix, d3_prefix, number_str, parts.placement
        )
    if keep_affix_gaps:
        prefix = (explicit_prefix + d3_prefix).lstrip()
        suffix = (magnitude_suffix + d3_suffix + explicit_suffix).rstrip()
    else:
        prefix = (explicit_prefix + d3_prefix).strip()
        suffix = (magnitude_suffix + d3_suffix + explicit_suffix).strip()
    return prefix, number_str, suffix
