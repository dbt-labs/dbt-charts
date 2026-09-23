"""Shared-scale magnitude handling on the support_table strip.

A strip row declares its unit once, on the first cell it actually draws, and
leaves the rest bare — the ANCHOR half of ``core/text/numeral_scale.py``, which
until now only the vertical ruler used.
"""

from __future__ import annotations

import math
from typing import Literal

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableSource,
)
from dbt_charts.core.render.chart.support_table_attachment import (
    CARRIES_FIELD,
    StripAnchor,
    StripNumerals,
    attach_support_table,
    plain_numerals,
    strip_numerals_for_values,
)

_DRAWN = StripAnchor.by_drawn_index()


def _millions(repeat: bool = False) -> StripNumerals:
    """A row sharing the millions tier, spelled "$441mn" at the anchor."""
    return StripNumerals(
        divisor=1e6,
        digit_spec=",.0~f",
        anchor=_DRAWN,
        prefix="$",
        suffix="mn",
        suffix_is_magnitude=True,
        repeat_suffix=repeat,
    )


_MILLIONS = _millions()


def _charts_style():
    return get_theme_style().charts


def _base_spec():
    return {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 600,
    }


def _attach(
    support_table,
    entry_numerals=(),
    sampling_step: int = 1,
    label_period_filter_expr: str | None = None,
    axis_y_orient: Literal["left", "right"] = "right",
):
    charts_style = _charts_style()
    return attach_support_table(
        _base_spec(),
        support_table=support_table,
        style=get_theme_style().charts.support_table,
        charts_style=charts_style,
        axis_label_padding=charts_style.axis.labels.padding,
        axis_y_orient=axis_y_orient,
        entry_numerals=entry_numerals,
        sampling_step=sampling_step,
        label_period_filter_expr=label_period_filter_expr,
    )


def _cell_calc(spec, index: int = 0) -> str:
    """The `calculate` expression producing row `index`'s cell text."""
    cell = f"__support_table_{index}"
    for layer in spec["layer"]:
        for transform in layer.get("transform", []):
            if transform.get("as") == cell:
                return transform["calculate"]
    raise AssertionError(f"no calculate transform for {cell}")


def _transform_ops(spec, index: int = 0) -> list[str]:
    """The transform kinds applied to row `index`'s layer, in order."""
    cell = f"__support_table_{index}"
    for layer in spec["layer"]:
        transforms = layer.get("transform", [])
        if any(t.get("as") == cell for t in transforms):
            return [next(iter(t)) for t in transforms]
    raise AssertionError(f"no layer for {cell}")


def _goal_table(fmt: str = "$,.3s"):
    return ChartSupportTable(
        entries=[ChartSupportTableSource(source="goal", format=fmt, label="Goal")]
    )


def test_anchor_declares_the_magnitude_once_in_narrative_form():
    """Within one tier: the suffix rides the anchor cell and nothing else.

    Narrative ("mn"), not analytic (" M") — the strip's cells are already
    separated by band space, so the analytic form's leading space would be a
    second separator competing with the first.
    """
    calc = _cell_calc(_attach(_goal_table(), entry_numerals=[_MILLIONS]))

    assert '" M"' not in calc
    # The suffix is gated on the anchor, so it cannot reach a bare cell.
    assert (
        '(datum["goal"] !== 0 && datum.__support_table_carries === 1 '
        "&& datum.__support_table_drawn_index === 1 ? \"mn\" : '')" in calc
    )
    # The magnitude is divided out once, rather than each cell re-picking a
    # tier through d3's own `s` type.
    assert 'abs(datum["goal"]) / 1000000.0' in calc
    assert "$,.3s" not in calc


def test_currency_prefix_anchors_with_the_magnitude():
    """`$` travels with the suffix — one declaration, not a per-cell repeat."""
    calc = _cell_calc(_attach(_goal_table(), entry_numerals=[_MILLIONS]))

    assert calc.count('"$"') == 1
    prefix_at = calc.index('"$"')
    suffix_at = calc.index('"mn"')
    # Both gated on the same test, so the anchor cell reads "$441mn".
    assert calc.count("__support_table_drawn_index === 1") == 2
    assert prefix_at < suffix_at


def test_repeat_mode_puts_the_suffix_on_every_non_zero_cell():
    """Past one tier the magnitude no longer names the set — the ruler's rule.

    The currency prefix still anchors: repeating `$` on every cell
    disambiguates nothing (`non_compacting_tick_format`'s convention, shared
    with the axis), so only the suffix repeats.
    """
    calc = _cell_calc(
        _attach(
            _goal_table(),
            entry_numerals=[_millions(repeat=True)],
        )
    )

    assert '(datum["goal"] !== 0 ? "mn" : \'\')' in calc
    assert (
        "(datum.__support_table_carries === 1 "
        "&& datum.__support_table_drawn_index === 1 ? \"$\" : '')" in calc
    )


def test_no_shared_scale_leaves_the_cell_formatting_untouched():
    """A format naming no unit emits the author's spec, the same in every cell.

    Asserts the emitted expression outright rather than comparing two calls
    that would both take the same branch — the whole claim is that this row's
    text is what it was before the feature existed.
    """
    table = _goal_table(fmt=",.0f")
    calc = _cell_calc(
        _attach(
            table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
        )
    )

    assert calc == (
        'isValid(datum["goal"]) && (!isNumber(datum["goal"]) || isFinite(datum["goal"])) '
        "? format(datum[\"goal\"], ',.0f') : '-'"
    )


# --- Decimal-point alignment on a plain trim-`f` column ---


def test_plain_trim_format_appends_a_decimal_pad_to_the_cell_expression():
    """A mixed whole/fractional plain-numeric column carries a decimal pad.

    Reuses the same Vega-expression shape the axis ruler appends
    (``_apply_decimal_pad``/``_decimal_pad_vega_expr``): a length-diff ternary
    appended after the plain ``format(...)`` call, not a rewritten spec.
    """
    from dbt_charts.core.compile.format import decimal_pad_table_for
    from dbt_charts.core.render.chart.vl_field_maps import _apply_decimal_pad

    table = _goal_table(fmt=",.2~f")
    calc = _cell_calc(
        _attach(
            table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
        )
    )

    pad_table = decimal_pad_table_for(",.2~f", "Inter")
    assert pad_table, "a trim-enabled fixed-point spec must produce a pad table"
    expected_text_e = _apply_decimal_pad("format(datum[\"goal\"], ',.2~f')", pad_table)
    assert calc == (
        'isValid(datum["goal"]) && (!isNumber(datum["goal"]) || isFinite(datum["goal"])) '
        f"? {expected_text_e} : '-'"
    )


def test_si_column_pad_glyphs_appear_literally_in_the_cell_expression():
    """CRITICAL 1/2 regression, non-tautological: board 26A's own
    30.0/84.3/128.0 case (an SI ``.3~s`` spec, mixed whole/fractional
    depth). Builds the EXPECTED pad table independently, from
    ``fractional_digit_count`` and ``build_decimal_pad_table`` directly --
    the same primitives production code uses -- WITHOUT ever calling
    ``_apply_decimal_pad`` (the Vega-expression builder under test), then
    confirms each of that table's distinct pad glyphs is embedded literally
    (JSON-encoded, matching how ``_decimal_pad_vega_expr`` embeds string
    literals) in the emitted cell expression.

    30 and 128 are both whole (0 fractional digits after the fix's
    digit-only count -- CRITICAL 1's exact repro); 84.3 already shows the
    column's observed max depth (1 digit) and needs no pad at all.
    """
    import json

    from dbt_charts.core.font_measure import compose_decimal_units
    from dbt_charts.core.text.numeral_scale import (
        build_decimal_pad_table,
        fractional_digit_count,
    )

    values = [30.0, 84.3, 128.0]
    strip = strip_numerals_for_values(values, ".3~s", _DRAWN, font_family="Inter")
    assert strip.decimal_pad_table, "mixed observed depth must bake a pad"

    # format_value(v, ".3~s", None) trims 30.0/84.3/128.0 to "30"/"84.3"/"128"
    # (3 significant figures each; verified directly, not via the pad-table
    # builder under test). Independently re-derive the observed max
    # fractional depth (1, from "84.3"'s digit "3" after the dot; "30" and
    # "128" are both whole) and the pad table it should produce.
    observed_max_depth = max(
        fractional_digit_count(trimmed) for trimmed in ("30", "84.3", "128")
    )
    assert observed_max_depth == 1
    digit_unit, dot_unit = compose_decimal_units("Inter")
    expected_table = build_decimal_pad_table(observed_max_depth, digit_unit, dot_unit)
    assert strip.decimal_pad_table == expected_table

    table = _goal_table(fmt=".3~s")
    calc = _cell_calc(_attach(table, entry_numerals=[strip]))
    for pad in expected_table:
        assert json.dumps(pad) in calc, (pad, calc)
    # The non-empty pads (what a whole cell like 30/128 actually paints)
    # must be present, not just the trivially-everywhere empty string.
    non_empty_pads = [p for p in expected_table if p]
    assert non_empty_pads, "expected at least one real (non-empty) pad glyph"


def test_whole_number_cell_gets_measured_pad_not_trailing_zeros():
    """A whole-number cell measures wider (via a measured pad) without ever
    painting a trailing-zero digit — false precision the source never had.
    """
    from dbt_charts.core.compile.format import decimal_pad_table_for
    from dbt_charts.core.font_measure import get_font_measurer

    numerals = StripNumerals(
        digit_spec=",.2~f",
        anchor=StripAnchor.nowhere(),
        decimal_pad_table=(),
    )
    padded = StripNumerals(
        digit_spec=",.2~f",
        anchor=StripAnchor.nowhere(),
        decimal_pad_table=decimal_pad_table_for(",.2~f", "Inter"),
    )

    # No trailing zeros painted either way -- the digits themselves never change.
    assert numerals.bare_text(85) == "85"
    assert padded.bare_text(85).startswith("85")
    assert "0" not in padded.bare_text(85)[len("85") :]

    # A fractional value that already reaches the column's full precision gets
    # no pad (nothing was trimmed).
    assert padded.bare_text(93.46) == "93.46"

    # The padded whole number's rendered width now matches the fractional
    # sibling's -- the actual decimal-point-alignment claim.
    measurer = get_font_measurer("Inter", numeric=True)
    assert measurer.measure(padded.bare_text(85), 12) == pytest.approx(
        measurer.measure(padded.bare_text(93.46), 12), abs=0.5
    )


def test_si_column_below_compaction_still_decimal_aligns_on_observed_depth():
    """An SI/default-numeric column whose values never need an SI tier at all
    (``shared_scale_for_column`` refuses -- no compaction, the board-26 26A
    case) still decimal-aligns: no shared magnitude to divide by, so each
    cell keeps its own per-cell significant-figures spec, but the column's
    own OBSERVED max fractional depth (not a spec-declared one) still builds
    a pad (``_unscaled_decimal_pad``). Distinct from
    ``test_si_shared_scale_column_gets_a_decimal_pad_on_its_digit_portion``
    below, where a shared tier *is* found and the digit_spec itself changes.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    si_table = _goal_table(fmt="number")
    values = [85.0, 93.46]
    si_numerals = _entry_numerals(
        si_table, [values], formats=None, font_family="Inter", anchor=_DRAWN
    )
    strip = si_numerals[0]
    assert strip.digit_spec == ".3~s", "digit_spec stays the author's own spec"
    assert strip.divisor == 1.0, "no shared magnitude -- nothing to divide by"
    assert strip.decimal_pad_table, "mixed observed depth must still bake a pad"

    # 85.0 at 3 sig figs prints "85" (whole); 93.46 prints "93.5" (fractional).
    assert strip.bare_text(93.46) == "93.5"
    padded = strip.bare_text(85.0)
    assert padded.startswith("85")
    assert padded != "85"
    assert "0" not in padded[len("85") :], "pad must never add a false-precision digit"


def test_below_compaction_but_single_real_tier_still_decimal_aligns():
    """1200/3000/5600 all sit in the thousands tier -- every cell prints the
    same "k" suffix -- but the extreme value's 4 integer digits miss
    ``shared_scale_for_column``'s own 6-digit compaction floor, so the
    shared bake refuses anyway. That refusal is orthogonal to whether a
    tier is shared: a bare compaction-floor miss still has one shared
    decimal position to pad to.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    si_table = _goal_table(fmt="number")
    values = [1200.0, 3000.0, 5600.0]
    si_numerals = _entry_numerals(
        si_table, [values], formats=None, font_family="Inter", anchor=_DRAWN
    )
    strip = si_numerals[0]
    assert strip.divisor == 1.0, "below the compaction floor -- no shared magnitude"
    assert strip.decimal_pad_table, "homogeneous single-tier column must still pad"

    # 1200/5600 trim to one decimal ("1.2k"/"5.6k"); 3000 trims to whole ("3k").
    assert strip.bare_text(1200.0) == "1.2k"
    assert strip.bare_text(5600.0) == "5.6k"
    padded = strip.bare_text(3000.0)
    assert padded.startswith("3k")
    assert padded != "3k"
    assert "0" not in padded[len("3k") :], "pad must never add a false-precision digit"


def test_genuine_tier_mix_bakes_no_decimal_pad_at_all():
    """A real mix -- one value prints with a "k" suffix, one prints bare --
    is not the ``test_si_column_below_compaction...`` case above (every value
    below the smallest SI tier). ``shared_scale_for_column`` refuses this
    column too (a real tier can tie with "no tier"), but a bare "900" and a
    "12.1 k" (=12,100) have no shared decimal position to align on, so no pad
    should be baked at all.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    si_table = _goal_table(fmt="number")
    values = [12_100.0, 900.0]
    si_numerals = _entry_numerals(
        si_table, [values], formats=None, font_family="Inter", anchor=_DRAWN
    )
    strip = si_numerals[0]
    assert strip.divisor == 1.0, "no shared magnitude -- nothing to divide by"
    assert strip.decimal_pad_table == ()


def test_a_non_finite_cell_does_not_veto_the_rest_of_the_columns_pad():
    """One inf/nan row must not silently kill the pad every other cell in a
    genuinely no-tier column still deserves. Regression: an earlier gate
    filtered only zeros before comparing natural tiers, so a single
    non-finite value (which has no natural tier of its own) made an
    otherwise-agreeing column read as a tier mix and dropped its pad
    entirely -- a real behavior loss versus the pre-fix
    ``_unscaled_decimal_pad``, which already filtered non-finite values.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    si_table = _goal_table(fmt="number")
    for bad in (float("inf"), float("nan")):
        values = [30.0, 84.3, 128.0, bad]
        si_numerals = _entry_numerals(
            si_table, [values], formats=None, font_family="Inter", anchor=_DRAWN
        )
        strip = si_numerals[0]
        assert strip.decimal_pad_table, f"non-finite value {bad} must not veto the pad"


def test_currency_and_percent_columns_get_no_decimal_pad():
    """Currency/percent support_table entries are unaffected -- their resolved
    specs are never trim-enabled ``f`` type, so ``decimal_pad_table_for``
    self-gates to ``()`` regardless of the values.
    """
    currency_table = _goal_table(fmt="currency_full")
    currency_numerals = plain_numerals(
        currency_table, None, "Inter", [[]] * len(currency_table.entries)
    )
    assert currency_numerals[0].decimal_pad_table == ()

    percent_table = _goal_table(fmt="percent")
    percent_numerals = plain_numerals(
        percent_table, None, "Inter", [[]] * len(percent_table.entries)
    )
    assert percent_numerals[0].decimal_pad_table == ()


def test_plain_numerals_si_column_pads_a_homogeneous_tier_but_not_a_genuine_mix():
    """Every existing ``plain_numerals`` test in this suite passes ``[[]] *
    len(entries)`` -- an empty column, which is vacuously "one shared unit"
    regardless of the gate, so it can't distinguish a correct gate from a
    deleted one. Exercise it with real values instead: a homogeneous
    single-tier column (1.2M/3M/5M, every cell prints "M") must still pad,
    and a genuine tier mix (12100/900, "k"-suffixed vs bare) must not.
    """
    si_table = _goal_table(fmt="number")

    same_tier = plain_numerals(si_table, None, "Inter", [[1.2e6, 3.0e6, 5.0e6]])
    assert same_tier[0].decimal_pad_table != ()

    mixed_tier = plain_numerals(si_table, None, "Inter", [[12_100.0, 900.0]])
    assert mixed_tier[0].decimal_pad_table == ()


def test_si_shared_scale_column_gets_a_decimal_pad_on_its_digit_portion():
    """An SI/default-numeric column whose shared-scale bake succeeds (values
    span a real SI tier) aligns on the decimal point too -- mirrors
    ``_table.py``'s shared-scale column bake
    (``_with_resolved_scale_stops``/``_shared_scale_decimal_pad``): pad built
    from the *scaled* values and the rebuilt fixed-point ``digit_spec``, not
    the original ``~s`` spec (``decimal_pad_table_for`` only accepts ``"f"``).

    845M and 932M need zero decimals at three significant figures once
    scaled; 44.6M needs one -- exactly the plain-vs-fractional mix this
    mechanism exists to align, now on the SI/default-numeric path as well as
    the plain trim-`f` path.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals
    from dbt_charts.core.render.chart.vl_field_maps import _apply_decimal_pad

    table = _goal_table(fmt="number")
    values = [[845.0e6, 44.6e6, 932.0e6]]
    numerals = _entry_numerals(
        table, values, formats=None, font_family="Inter", anchor=_DRAWN
    )
    strip = numerals[0]

    assert strip.digit_spec == ",.1~f"
    assert strip.decimal_pad_table, (
        "a mixed-depth shared-scale column must bake a pad table"
    )

    # Whole numbers pad, never grow a trailing-zero digit.
    assert strip.bare_text(845.0e6).startswith("845")
    assert strip.bare_text(845.0e6) != "845"
    assert "0" not in strip.bare_text(845.0e6)[len("845") :]
    # A value that already reaches the shared precision gets no pad.
    assert strip.bare_text(44.6e6) == "44.6"

    calc = _cell_calc(_attach(table, entry_numerals=[strip]))
    scaled_value_e = f'abs(datum["goal"]) / {strip.divisor!r}'
    expected_digits = _apply_decimal_pad(
        f"format({scaled_value_e}, '{strip.digit_spec}')",
        strip.decimal_pad_table,
    )
    assert expected_digits in calc


def test_anchor_index_is_numbered_after_every_filter():
    """ "First drawn" must survive sampling — number the rows after thinning.

    The pre-existing ``__support_table_row_index`` is numbered *before* its own
    filter, so reusing it would anchor on a row that was thinned away and the
    unit would silently never render.
    """
    ops = _transform_ops(
        _attach(_goal_table(), entry_numerals=[_MILLIONS], sampling_step=3)
    )

    assert ops.count("window") == 2
    assert ops.index("filter") < ops.index("window", ops.index("window") + 1)
    assert ops[-1] == "calculate"


def test_anchor_index_is_numbered_after_the_label_period_filter():
    """The other thinning path — quarterly openers on a monthly band."""
    ops = _transform_ops(
        _attach(
            _goal_table(),
            entry_numerals=[_MILLIONS],
            label_period_filter_expr="datum.month % 3 === 0",
        )
    )

    assert ops.index("filter") < ops.index("window")
    assert ops[-1] == "calculate"


def test_each_row_anchors_independently():
    """A goal row and an actual row each declare their own magnitude."""
    table = ChartSupportTable(
        entries=[
            ChartSupportTableSource(source="goal", format="$,.3s", label="Goal"),
            ChartSupportTableSource(source="actual", format="$,.3s", label="Actual"),
        ]
    )
    spec = _attach(table, entry_numerals=[_MILLIONS, _MILLIONS])

    for index in (0, 1):
        assert '"mn"' in _cell_calc(spec, index)


def _goal_values(*values: float):
    return list(values)


def test_one_tier_row_anchors():
    """The brief's case: goals climbing 441M to 516M all name the same tier."""
    strip = strip_numerals_for_values(
        _goal_values(441e6, 448e6, 456e6, 473e6, 505e6, 516e6),
        "$,.3s",
        _DRAWN,
        font_family="Inter",
    )

    assert strip.repeat_suffix is False
    assert strip.divisor == 1e6
    assert strip.prefix == "$"
    assert strip.suffix == "mn"
    # 3 significant figures, all three spent on "516" — no decimals.
    assert strip.digit_spec == ",.0~f"


def test_row_that_outgrows_its_tier_repeats():
    """Past ~one tier the magnitude stops naming the set — the ruler's rule.

    Millions win the tier vote 3-1, but the 3.4bn outlier scales to 3,400 —
    four integer digits, so the magnitude no longer names the whole row.
    """
    strip = strip_numerals_for_values(
        _goal_values(2.0e6, 400e6, 500e6, 3_400e6), "$,.3s", _DRAWN, font_family="Inter"
    )

    assert strip.repeat_suffix is True


def test_sub_thousand_row_has_no_shared_magnitude():
    """Nothing to compact — the row keeps its own formatting entirely."""
    numerals = strip_numerals_for_values(
        _goal_values(4.0, 12.0, 37.0), "$,.3s", _DRAWN, font_family="Inter"
    )

    assert (numerals.prefix, numerals.suffix) == ("", "")
    assert numerals.digit_spec == "$,.3s"


def test_plain_format_declares_nothing():
    """A bare number format repeats nothing worth stating once."""
    numerals = strip_numerals_for_values(
        _goal_values(441e6, 516e6), ",.0f", _DRAWN, font_family="Inter"
    )

    assert (numerals.prefix, numerals.suffix) == ("", "")
    assert numerals.digit_spec == ",.0f"


def test_decimal_depth_follows_the_significant_figures_asked_for():
    """A row topping out at 5.5M keeps the decimal the bar above it shows.

    This is the precision mismatch: formatted per-cell, the strip rounded
    independently of the axis and showed 5 under a bar reading 5.5.
    """
    strip = strip_numerals_for_values(
        _goal_values(4.1e6, 5.5e6, 5.2e6, 12e6), "$,.2s", _DRAWN, font_family="Inter"
    )

    assert strip.digit_spec == ",.1~f"


def test_precision_none_si_derives_decimal_depth_from_six_significant_figures():
    """$~s (precision=None) defaults to 6 sig figs; depth comes from the values.

    An inline SI spec authored without a precision ($~s / ~s) defaults to 6 sig
    figs. The decimal-depth formula must use 6 when no precision is authored.
    (The predefined names no longer reach this branch — currency and
    compact resolve to $.3~s / .3~s — so an inline literal is the path that
    keeps it live.) Verify digit_spec for a row
    where 6 sig figs requires 5 decimals for the smallest value.

    column_digit_format forces comma=True, so digit_spec always has comma
    grouping regardless of whether the authored format included it.
    """
    # column_digit_format: sig_figs=6, finest_scaled=4.1 → _decimal_exponent(4.1)=1
    # decimal_precision = max(0, 6-1) = 5
    strip = strip_numerals_for_values(
        _goal_values(4.1e6, 12e6), "$~s", _DRAWN, font_family="Inter"
    )

    assert strip.digit_spec == ",.5~f"


def test_precision_none_bare_text_is_consistent_with_digit_spec():
    """bare_text for precision-None uses the derived digit_spec, not the raw spec.

    column_digit_format forces comma=True; digit_spec always includes comma.
    """
    # column_digit_format: sig_figs=6, finest_scaled=441 → _decimal_exponent(441)=3
    # decimal_precision = max(0, 6-3) = 3
    strip = strip_numerals_for_values(
        _goal_values(441e6, 448e6, 456e6), "$~s", _DRAWN, font_family="Inter"
    )
    assert strip.digit_spec == ",.3~f"
    bare = strip.bare_text(448e6)
    # bare_text removes prefix ($) and suffix (mn); 448e6/1e6=448 → "448"
    assert bare == "448"


def test_percent_row_declares_the_sign_once():
    """The TV board's case: a goal row in percent states "%" on the anchor."""
    numerals = strip_numerals_for_values(
        _goal_values(0.055, 0.059, 0.062, 0.25), ",.1%", _DRAWN, font_family="Inter"
    )

    assert numerals.suffix == "%"
    assert numerals.prefix == ""
    # The author's spec is carried verbatim: it already scales by 100 and
    # appends the sign, so nothing is rebuilt and nothing can be dropped.
    assert numerals.divisor == 1.0
    assert numerals.digit_spec == ",.1%"


def test_percent_sign_survives_a_zero_cell():
    """0% is a real reading, unlike "0mn" -- only magnitudes drop on zero."""
    numerals = strip_numerals_for_values(
        _goal_values(0.0, 0.055), ",.1%", _DRAWN, font_family="Inter"
    )
    assert numerals.suffix_is_magnitude is False

    calc = _cell_calc(_attach(_goal_table(), entry_numerals=[numerals]))

    assert "replace(format(datum[\"goal\"], ',.1%'), \"%\", '')" in calc
    assert 'datum["goal"] !== 0' not in calc


def test_currency_without_a_magnitude_still_anchors_its_symbol():
    """`$441 448 456` -- nothing to compact, but the unit is still declared."""
    numerals = strip_numerals_for_values(
        _goal_values(441.0, 448.0), "$,.0f", _DRAWN, font_family="Inter"
    )

    assert numerals.prefix == "$"
    assert numerals.suffix == ""
    assert numerals.divisor == 1.0


def test_derived_decimal_depth_is_trimmed_but_authored_depth_is_not():
    """Trailing zeros are a ruler's business, not a data value's.

    Padding 25 out to "25.0" so it matches a 5.5 neighbor would assert a
    precision the source data may not carry. Where WE derived the depth from a
    significant-figure request, trim it; where the author wrote the decimal
    count themselves, honor it exactly.
    """
    derived = strip_numerals_for_values(
        _goal_values(5.5e6, 25.0e6), "$,.2s", _DRAWN, font_family="Inter"
    )
    authored = strip_numerals_for_values(
        _goal_values(5.5, 25.0), "$,.2f", _DRAWN, font_family="Inter"
    )

    assert derived.digit_spec.endswith("~f")
    assert not authored.digit_spec.endswith("~f")


def _per_series_spec(entry_numerals):
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    charts_style = _charts_style()
    spec = _base_spec()
    spec["encoding"]["color"] = {"field": "category", "type": "nominal"}
    return attach_support_table(
        spec,
        support_table=ChartSupportTable(
            entries=[ChartSupportTablePerSeries(per_series="goal", format="$,.3s")]
        ),
        style=get_theme_style().charts.support_table,
        charts_style=charts_style,
        axis_label_padding=charts_style.axis.labels.padding,
        axis_y_orient="right",
        series_order=["Tools", "Electronics"],
        entry_numerals=entry_numerals,
    )


def test_per_series_rows_declare_their_unit_like_every_other_row():
    """A per_series row is still a row — it cannot repeat what its neighbors state once.

    Aggregate and per_series entries sit in the same strip on the same chart,
    so a rule that reached only one of them would put "$" on every cell of one
    row and the first cell of the row directly above it.
    """
    spec = _per_series_spec([_MILLIONS])

    for index in (0, 1):
        calc = _cell_calc(spec, index)
        assert '"mn"' in calc, f"row {index} did not declare its magnitude"
        assert "datum.__support_table_drawn_index === 1" in calc


def test_per_series_rows_anchor_after_their_own_series_filter():
    """Each series numbers its own drawn cells, so each anchors its own leftmost.

    The per-series filter runs before the drawn-index window, so series B's
    anchor is B's first cell -- not whichever row happened to sort first
    across the (x, series) cross-product.
    """
    spec = _per_series_spec([_MILLIONS])

    ops = _transform_ops(spec, 1)
    assert ops.index("filter") < ops.index("window")
    assert ops[-1] == "calculate"


def test_a_small_value_keeps_a_significant_digit_instead_of_printing_zero():
    """Depth is significant figures, not a difference of digit counts.

    Subtracting digit counts floors a scaled-below-one value at zero decimals,
    which printed 400,000 as "0" — a real value rendering as nothing.
    """
    from dbt_charts.core.text.numeral_scale import decimal_pad_for

    numerals = strip_numerals_for_values(
        _goal_values(441e6, 448e6, 400e3), "$,.1s", _DRAWN, font_family="Inter"
    )

    assert numerals.bare_text(400e3) == "0.4"
    # 441 (whole) and 0.4 (fractional) mix depths within the row, so 441 now
    # carries the same measured decimal pad the axis/table sides already
    # apply -- it still prints no fractional digits, just a trailing pad.
    padded_441 = numerals.bare_text(441e6)
    assert padded_441.startswith("441")
    assert padded_441 != "441"
    assert numerals.decimal_pad_table
    assert padded_441 == "441" + decimal_pad_for(numerals.decimal_pad_table, "441")


def test_non_finite_values_do_not_reach_the_scale():
    """NaN/inf floor and count digits inside the shared-scale resolver."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        # Must not raise — finite voters still get a shared scale.
        strip_numerals_for_values(
            _goal_values(441e6, bad), "$,.3s", _DRAWN, font_family="Inter"
        )


def test_the_mode_is_decided_on_the_number_the_cell_prints():
    """A magnitude that survives truncation can still print four digits.

    999,600 at `.3s` truncates to three integer digits — so the shared
    resolver anchors — but rounds to "1,000" in the cell, which is the case
    the four-digit rule exists to catch.
    """
    numerals = strip_numerals_for_values(
        _goal_values(999_600.0, 500_000.0, 300_000.0),
        ".3s",
        _DRAWN,
        font_family="Inter",
    )

    assert numerals.repeat_suffix is True
    assert numerals.bare_text(999_600.0).endswith("k")


def test_a_repeat_row_without_a_prefix_needs_no_drawn_index():
    """Its suffix rides every non-zero cell, so no cell reads the index."""
    bare = strip_numerals_for_values(
        _goal_values(2e6, 400e6, 500e6, 3400e6), ",.3s", _DRAWN, font_family="Inter"
    )
    with_prefix = strip_numerals_for_values(
        _goal_values(2e6, 400e6, 500e6, 3400e6), "$,.3s", _DRAWN, font_family="Inter"
    )

    assert bare.repeat_suffix is True
    assert bare.needs_drawn_index_window is False
    assert with_prefix.needs_drawn_index_window is True


def test_a_row_starting_at_zero_declares_the_unit_on_its_first_nonzero_cell():
    """The anchor is the first cell that CAN carry the unit, not the first drawn.

    A metric whose first period is 0 would otherwise anchor "$0" and declare
    the magnitude nowhere. The carries window ranks unit-carrying cells so the
    anchor test lands on the first non-zero one.
    """
    calc = _cell_calc(_attach(_goal_table(), entry_numerals=[_MILLIONS]))

    # The suffix and prefix both gate on the carries+rank pair.
    assert (
        "datum.__support_table_carries === 1 && datum.__support_table_drawn_index === 1"
        in calc
    )


def test_the_drawn_index_window_ranks_only_unit_carrying_cells():
    """The window sums a per-cell 'carries' flag, so its running total is 1 on
    the first valid non-zero cell -- not a bare row_number that counts zeros."""
    spec = _attach(_goal_table(), entry_numerals=[_MILLIONS])
    transforms = [
        t
        for layer in spec["layer"]
        for t in layer.get("transform", [])
        if "window" in t or t.get("as") == "__support_table_carries"
    ]
    carries = next(t for t in transforms if t.get("as") == "__support_table_carries")
    window = next(t for t in transforms if "window" in t)
    assert "!== 0" in carries["calculate"] and "isFinite" in carries["calculate"]
    assert window["window"][0]["op"] == "sum"
    assert window["frame"] == [None, 0]


def test_a_negative_magnitude_cell_signs_before_the_currency_symbol():
    """d3 renders a negative currency as "-$448"; the composed split must too.

    The symbol is anchored separately, so a naive "$" + format(-448) gave
    "$-448mn". The sign is composed ahead of the symbol, with d3's own minus
    glyph (U+2212), so the anchor reads "-$448mn".
    """
    calc = _cell_calc(_attach(_goal_table(), entry_numerals=[_MILLIONS]))

    # sign expr precedes the anchored prefix in the concatenation
    sign_at = calc.index("datum[\"goal\"] < 0 ? '−'")
    prefix_at = calc.index('? "$"')
    assert sign_at < prefix_at
    assert 'abs(datum["goal"])' in calc


def test_the_painted_text_and_the_measured_text_agree_exactly():
    """Parity is the whole design: the lane is measured with `bare_text` and
    painted with the emitted expression. Emulate the emitted expression's bare
    branch in Python and require it to equal `bare_text` on real values."""
    from dbt_charts.core.render.format_utils import format_value

    for spec, values in (
        ("$,.3s", _goal_values(441e6, 448e6, 5e6)),
        (",.1%", _goal_values(0.055, 0.25)),
        ("$,.0f", _goal_values(441.0, 448.0)),
        (",.0f", _goal_values(441.0, 448.0)),
    ):
        n = strip_numerals_for_values(values, spec, _DRAWN, font_family="Inter")
        for v in values:
            if n.suffix_is_magnitude:
                emitted = format_value(abs(v) / n.divisor, n.digit_spec, None)
                if v < 0:
                    emitted = "−" + emitted
            else:
                emitted = format_value(v, n.digit_spec, None)
                for affix in (n.prefix, n.suffix):
                    if affix:
                        emitted = emitted.replace(affix, "", 1)
            assert emitted == n.bare_text(v), (spec, v, emitted, n.bare_text(v))


# --- CRITICAL 1: format provenance gate ---


def test_non_magnitude_zero_first_cell_carries_its_affix():
    """$0 and 0% are real readings — only magnitude suffixes are skipped on zero.

    A currency-prefix row whose first cell is 0 must anchor on that cell
    (showing "$0"), not skip to the first non-zero one. Before the fix,
    CARRIES_FIELD included `!== 0` unconditionally, so a $0 first cell stranded
    its "$" and the prefix appeared on the wrong cell.
    """
    spec = _attach(
        _goal_table(fmt="$,.0f"),
        entry_numerals=[
            strip_numerals_for_values(
                _goal_values(0.0, 441.0, 448.0), "$,.0f", _DRAWN, font_family="Inter"
            )
        ],
    )
    transforms = [
        t
        for layer in spec["layer"]
        for t in layer.get("transform", [])
        if t.get("as") == CARRIES_FIELD
    ]
    assert transforms, "expected a carries transform"
    # Non-magnitude row must NOT gate on !==0 — $0 carries its prefix.
    assert "!== 0" not in transforms[0]["calculate"]


def test_percent_zero_first_cell_carries_its_affix():
    """0% is a valid reading — percent rows must not skip zero in the carries gate."""
    spec = _attach(
        _goal_table(fmt=",.1%"),
        entry_numerals=[
            strip_numerals_for_values(
                _goal_values(0.0, 0.055), ",.1%", _DRAWN, font_family="Inter"
            )
        ],
    )
    transforms = [
        t
        for layer in spec["layer"]
        for t in layer.get("transform", [])
        if t.get("as") == CARRIES_FIELD
    ]
    assert transforms
    assert "!== 0" not in transforms[0]["calculate"]


def test_magnitude_zero_first_cell_still_skips_zero():
    """'0mn' is meaningless — magnitude rows still skip zero in the carries gate."""
    spec = _attach(
        _goal_table(),
        entry_numerals=[_MILLIONS],
    )
    transforms = [
        t
        for layer in spec["layer"]
        for t in layer.get("transform", [])
        if t.get("as") == CARRIES_FIELD
    ]
    assert transforms
    assert "!== 0" in transforms[0]["calculate"]


# --- Fix 1: raw d3 specs now anchor (provenance gate removed) ---


def test_raw_d3_spec_does_not_anchor():
    """A raw d3 spec must NOT anchor -- house rules apply only to predefined names.

    "$,.3s" is a valid d3 literal, but it carries no provenance: the anchor is
    an engine house convention layered on the predefined-name vocabulary, not
    something a raw literal or user alias silently opts into.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        StripAnchor,
        _entry_numerals,
    )

    table = _goal_table(fmt="$,.3s")
    values = [[441e6, 448e6, 456e6]]
    for hang in ("left", "right"):
        numerals = _entry_numerals(
            table, values, formats=None, font_family="Inter", anchor=_DRAWN, hang=hang
        )
        assert numerals[0].anchor == StripAnchor.nowhere(), (
            f"a raw d3 spec must not anchor (hang={hang!r})"
        )


# --- Fix 2: predefined-name formats now anchor on left-oriented axes too ---
# (orientation gate removed; provenance gate -- predefined names only -- kept)


def test_predefined_prefix_free_si_format_anchors_on_left_axis():
    """A predefined, prefix-free SI format anchors on a left-oriented axis.

    'number' (predefined, no currency prefix) on a left-axis anchors fine:
    the magnitude suffix is declared once on the anchor cell, trailing the
    digits. Only prefix-carrying formats route to nowhere on left-axis.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    table = _goal_table(fmt="number")
    values = [[441e6, 448e6, 456e6]]
    numerals = _entry_numerals(
        table, values, formats=None, font_family="Inter", anchor=_DRAWN, hang="right"
    )
    assert numerals[0].suffix == "mn", "prefix-free SI format must anchor on left axis"
    assert numerals[0].hang == "right"


def test_prefix_carrying_format_left_axis_routes_to_nowhere():
    """A format with a currency prefix routes to StripAnchor.nowhere() on a left-oriented axis.

    Anchoring a currency strip on a left axis would require the prefix to appear
    somewhere. Trailing it after digits ('441$mn') is non-standard typography, and
    leading it misaligns digit columns. The strip instead repeats the full format
    on every cell (standard per-cell behavior) so the unit is never suppressed.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        StripAnchor,
        _entry_numerals,
    )

    table = _goal_table(fmt="currency")
    values = [[441e6, 448e6, 456e6]]
    numerals = _entry_numerals(
        table, values, formats=None, font_family="Inter", anchor=_DRAWN, hang="right"
    )
    assert numerals[0].anchor == StripAnchor.nowhere(), (
        "currency prefix format must route to nowhere on left-axis"
    )


def test_left_axis_currency_prefix_routes_to_nowhere_expression():
    """A predefined currency format on a left axis emits a plain per-cell expression.

    With StripAnchor.nowhere(), every cell formats through its own spec unchanged
    -- no replace(), no anchor_test, just format(datum["goal"], '$~s').
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    table = _goal_table(fmt="currency")
    values = [[441e6, 448e6, 456e6]]
    numerals_left = _entry_numerals(
        table, values, formats=None, font_family="Inter", anchor=_DRAWN, hang="right"
    )
    calc = _cell_calc(
        _attach(table, entry_numerals=numerals_left, axis_y_orient="left")
    )

    assert "replace(" not in calc
    assert "__support_table_drawn_index" not in calc
    assert "format(datum[\"goal\"], '$.3~s')" in calc


# --- Fix 3: tier_distance refusal parity with table ---


def test_value_one_tier_below_majority_still_anchors():
    """A value one SI tier below the shared majority still anchors.

    OLD: min(abs) / magnitude < 0.1 — 5000/1e6 = 0.005 < 0.1 → refused.
    NEW: tier_distance(5000, 6) = 1 <= 1 → anchors. Matches table's gate.
    """
    strip = strip_numerals_for_values(
        [5e3, 4e6, 5e6], "$,.3s", _DRAWN, font_family="Inter"
    )
    assert strip.suffix == "mn", "value one tier below must not prevent anchoring"


def test_value_below_smallest_si_tier_refuses_shared_bake():
    """A value below the smallest SI tier (< 1000) refuses the shared bake.

    tier_distance returns None for sub-thousand values; None refuses the bake,
    matching the table's identical refusal for values with no natural SI tier.
    """
    strip = strip_numerals_for_values(
        [500.0, 4e6, 5e6], "$,.3s", _DRAWN, font_family="Inter"
    )
    assert strip.suffix == "", "sub-tier outlier must refuse the shared bake"
    assert strip.digit_spec == "$,.3s", "original spec must be preserved on refusal"


def test_value_formatting_to_zero_refuses_shared_bake():
    """A value that formats as '0' in the shared tier refuses the bake.

    [1000, 4e6, 6e6] shares the mn tier. 1000/1e6 = 0.001, which the derived
    digit_spec rounds to 0 -- indistinguishable from a real zero row. The second
    guard from _table.py detects this: any voter formatting with no nonzero digit
    causes the bake to refuse, restoring per-cell formatting.
    """
    strip = strip_numerals_for_values(
        [1000.0, 4e6, 6e6], ".0s", _DRAWN, font_family="Inter"
    )
    assert strip.suffix == "", "value that formats to 0 must refuse the shared bake"
    assert strip.digit_spec == ".0s", "original spec must be preserved on refusal"


def test_strip_digit_spec_parity_with_column_digit_format():
    """strip_numerals_for_values must delegate decimal depth to column_digit_format.

    Guards the reuse contract: the strip's digit_spec for a shared-scale row
    must equal column_digit_format's output for the same (resolved spec, finest
    scaled value) inputs. If the strip rolls its own formula, this test fails.
    """
    from dbt_charts.core.text.numeral_scale import (
        column_digit_format,
        shared_scale_for_column,
    )

    values = [441e6, 448e6, 456e6]
    resolved = "$,.3s"
    strip = strip_numerals_for_values(values, resolved, _DRAWN, font_family="Inter")

    voters = [v for v in values if v != 0 and math.isfinite(v)]
    scale = shared_scale_for_column(voters)
    assert scale is not None
    magnitude = 10.0**scale.exponent
    finest = min(voters, key=abs)
    finest_scaled = abs(finest) / magnitude
    _, expected_digit_spec = column_digit_format(resolved, finest_scaled)

    assert strip.digit_spec == expected_digit_spec


# --- SVG integration: verify the unit fires in the actual rendered output ---


def test_raw_spec_does_not_paint_anchored_unit_in_svg():
    """A raw d3 spec must NOT anchor in the actual SVG output -- repeats per-cell.

    End-to-end: compile -> resolve -> render -> vl_convert -> SVG text.
    Skipped when vl_convert is not installed.
    """
    pytest.importorskip("vl_convert")

    from pydantic import TypeAdapter

    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_raw_spec_svg",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "revenue", "format": "$,.3s", "label": "Rev"}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    data = [
        {"month": "Jan", "revenue": 441e6},
        {"month": "Feb", "revenue": 448e6},
        {"month": "Mar", "revenue": 456e6},
    ]
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        data,
        format="svg",
        width=600,
        height=320,
    )
    reset_config()
    # A raw spec never anchors: d3 picks its own SI tier per cell ("M"), and
    # the house "mn" narrative suffix -- only ever emitted by the anchor path
    # -- must not appear anywhere.
    assert "mn" not in svg, (
        "a raw d3 spec must not anchor; 'mn' should never appear for a raw literal"
    )


def test_trimmed_hang_right_suffix_only_anchor_arm_uses_bare_plus_suffix():
    """hang="right" trimmed path: anchor arm is bare + suffix, not the full format.

    For a left-axis percent strip, the Vega expression for the anchor cell must
    be `(bare + conditional_suffix)`, NOT `format(v, '.1%')`. The difference:
    the anchor arm starts with `replace(` (for bare), not `format(` (for full).

    Reverting the hang="right" branch to the hang="left" one-liner
    `(anchor_test ? full : bare)` puts `format(` directly after the first `?`,
    making `"? format("` appear in the expression and failing this assertion.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _entry_numerals

    table = _goal_table(fmt="percent")
    values = [[0.041, 0.055, 0.062]]
    numerals = _entry_numerals(
        table, values, formats=None, font_family="Inter", anchor=_DRAWN, hang="right"
    )
    spec = _attach(
        _goal_table("percent"), entry_numerals=numerals, axis_y_orient="left"
    )
    calc = _cell_calc(spec)
    # hang="right": anchor arm is `(replace(...) + suffix)`, not `format(...)`.
    # The one-liner (hang="left") would put `format(` directly after the first `?`.
    assert "? format(" not in calc, (
        "hang='right' anchor arm must be bare+suffix, not the full format; "
        "reverting to hang='left' one-liner puts '? format(' in the expression"
    )


def test_time_format_name_on_support_table_does_not_reach_strip_numerals_for_values():
    """A time-shaped predefined name (date_short) on a support_table entry format
    must not crash -- the provenance gate in _entry_numerals routes it to
    StripAnchor.nowhere() before strip_numerals_for_values (and its unguarded
    _d3_parse) is ever called.

    compile/validate/formats.py accepts a predefined name on a support_table slot
    unconditionally (ALL_PREDEFINED_NAMES includes time names, and support_table
    is not in _TIME_CAPABLE_FIELDS, so `time_format=False` never gates a
    predefined-name spec there) -- so `format: date_short` on a support_table
    entry is valid, compile-accepted YAML that resolves to a strftime string
    (e.g. "%-d %b %Y"), which is not valid d3-format number grammar.
    `date_short` is a PredefinedTimeFormat, not a PredefinedNumberFormat, so
    the gate's `not in PREDEFINED_NUMBER_NAMES` check catches it the same way
    it catches every other non-number-format spec.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        StripAnchor,
        _entry_numerals,
    )

    table = _goal_table(fmt="date_short")
    numerals = _entry_numerals(
        table,
        [[1.0, 2.0]],
        formats=None,
        font_family="Inter",
        anchor=_DRAWN,
        hang="left",
    )
    assert numerals[0].anchor == StripAnchor.nowhere()
