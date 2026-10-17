"""Parity: the Vega numeral-register expression must match ``format_d3``.

Values dbt charts formats itself go through ``format_d3``'s analytic/narrative
post-process. Values Vega-Lite paints (axis ticks, mark labels) are formatted
by Vega's own d3 implementation from a bare spec string, so the post-process
never runs on them — ``numeral_vega_expr`` builds the Vega expression
equivalent so both paths reach the same house register.

This test renders the emitted expression through real Vega (``vl_convert``)
and compares the result to ``format_d3`` over a value x spec x notation
matrix. It never hand-writes an expected string: the two independent d3
implementations (the Python ``d3_format`` lib, and Vega's own in-browser d3)
must agree with each other.
"""

from __future__ import annotations

import html
import json
import re
from typing import get_args

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.models.primitives import (
    NumberLocale,
    Regional,
    RegionalLocale,
)
from dbt_charts.core.diagnostics.codes_render import ERR_NUMERAL_EXPR_EMPTY_SPEC
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.numeral_expr import Notation, numeral_vega_expr
from dbt_charts.core.text.format_d3 import (
    format_d3,
    regional_scope,
    round_aware_spec,
)

_TEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.S)


def _render_via_vega(value: float | None, expr: str) -> str:
    """Render ``expr`` (a ``datum.value`` expression) as a lone text mark."""
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"value": value}]},
        "transform": [{"calculate": expr, "as": "label"}],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    svg = vlc.vegalite_to_svg(spec)
    match = _TEXT_RE.search(svg)
    assert match, f"no <text> element in rendered SVG: {svg}"
    return html.unescape(match.group(1))


# Every SI tier boundary d3 names (k through Y), each negated, plus zero.
_TIER_VALUES = [0.0]
for _i in range(1, 9):
    _TIER_VALUES.append(10.0 ** (3 * _i))
    _TIER_VALUES.append(-(10.0 ** (3 * _i)))

_SI_SPEC = ".3~s"

# Every valid notation, read off the emitter's own Literal type rather than
# hardcoded — a register the type adds is a register the matrix covers.
_NOTATIONS: tuple[Notation, ...] = get_args(Notation)


@pytest.mark.parametrize("value", _TIER_VALUES)
@pytest.mark.parametrize("notation", _NOTATIONS)
def test_si_spec_matches_format_d3(value: float, notation: Notation) -> None:
    expr = numeral_vega_expr("datum.value", _SI_SPEC, notation=notation)
    rendered = _render_via_vega(value, expr)
    expected = format_d3(value, _SI_SPEC, notation=notation)
    assert rendered == expected, (
        f"value={value!r} notation={notation!r}: Vega expr rendered {rendered!r}, "
        f"format_d3 produced {expected!r}"
    )


# Words ending in a later tier's d3 letter: each value takes one substitution.
_LETTER_WORDS = NumberLocale.model_validate(
    {
        "decimal": ".",
        "thousands": ",",
        "grouping": [3],
        "currency": ["$", ""],
        "analytic": [" M", " G", " T", " P"],
        "narrative": ["M", "G", "T", "P"],
    }
)


@pytest.mark.parametrize("value", _TIER_VALUES)
@pytest.mark.parametrize("notation", _NOTATIONS)
def test_locale_words_match_format_d3(value: float, notation: Notation) -> None:
    regional = Regional(locale=RegionalLocale(number=_LETTER_WORDS))
    with regional_scope(
        regional.format_locale,
        regional.time_locale,
        regional.week_start,
    ):
        expr = numeral_vega_expr("datum.value", _SI_SPEC, notation=notation)
        expected = format_d3(value, _SI_SPEC, notation=notation)
    assert _render_via_vega(value, expr) == expected


def test_no_notation_argument_emits_no_post_process() -> None:
    """Omitting ``notation`` must match ``format_d3`` with no notation — both
    produce native d3 output with no SI suffix substitution.

    One representative value is enough: the analytic/narrative parametrization
    above already covers the full tier matrix for explicit notations.
    """
    value = 1_500_000_000.0
    expr = numeral_vega_expr("datum.value", _SI_SPEC)
    rendered = _render_via_vega(value, expr)
    expected = format_d3(value, _SI_SPEC)
    assert rendered == expected


@pytest.mark.parametrize("value", [1234.5, -1234.5, 0.0, 999.0])
def test_non_si_spec_emits_no_substitution_chain(value: float) -> None:
    """A non-SI spec must carry no suffix ``replace()`` chain — and match
    format_d3, which also skips the notation post-process for non-SI specs."""
    format_spec = ",.2f"
    expr = numeral_vega_expr("datum.value", format_spec)
    assert expr == 'format(datum.value,",.2f")', (
        f"non-SI spec must emit a bare format() call with no replace() chain, got: {expr!r}"
    )
    rendered = _render_via_vega(value, expr)
    expected = format_d3(value, format_spec)
    assert rendered == expected


def test_no_null_guard_wraps_the_expression() -> None:
    """The only production caller (``inject_axis_numeral_expr``) always
    divides ``datum.value`` by a magnitude first — in JS, ``null / n`` is
    ``0``, so a ``value_expr == null`` guard can never fire there, and axis
    ticks are never null anyway. A guard that cannot fire at its one call
    site is dead code dressed as null-safety; the emitted expression must
    not carry one."""
    expr = numeral_vega_expr("datum.value", ".3~s")
    assert "null" not in expr


def test_inline_si_spec_matches_format_d3_through_vega() -> None:
    """An inline SI spec ``",.2s"`` produces the same digit count on both
    Python (``format_d3``) and Vega paths — neither injects ``~`` trim for
    inline d3 (three-way contract: trim only for predefined names).

    Without an explicit notation, both paths produce native d3 output — no
    house suffix substitution. "1.0M" on a clean million is correct for
    ``",.2s"`` without trim; authors who want clean "1M" write ``",.2~s"``
    or use the predefined "compact" format name."""
    format_spec = ",.2s"
    expr = numeral_vega_expr("datum.value", format_spec)
    for value in (1_000_000.0, 1_250_000.0):
        rendered = _render_via_vega(value, expr)
        expected = format_d3(value, format_spec)
        assert rendered == expected
    # Inline spec without ~: trailing zero preserved on both Python and Vega.
    assert _render_via_vega(1_000_000.0, expr) == "1.0M"
    assert format_d3(1_000_000.0, ",.2s") == "1.0M"


def test_empty_format_spec_raises() -> None:
    """format_d3 treats an empty spec as a distinct "no d3 formatting" path
    (returns the bare Python value, bypassing d3 entirely) that a Vega
    expression can't reproduce byte-for-byte — Python's and JS's default
    numeral rendering diverge (confirmed: -5 renders as the ASCII hyphen
    '-5' in format_d3 but the real minus sign '−5' through Vega's
    format(), and a whole float like 1e9 renders '1000000000.0' vs
    '1000000000'). Reject rather than silently emit a diverging expression.
    """
    with pytest.raises(RenderError) as exc_info:
        numeral_vega_expr("datum.value", "")
    assert exc_info.value.code is ERR_NUMERAL_EXPR_EMPTY_SPEC


def test_value_expr_is_verbatim_datum_reference() -> None:
    """The emitted expression must reference the caller's value_expr, not a
    hardcoded ``datum.value`` — mark labels in a ``calculate`` transform read
    ``datum.<field>``, not ``datum.value``."""
    expr = numeral_vega_expr("datum.revenue", _SI_SPEC)
    assert "datum.revenue" in expr
    assert "datum.value" not in expr


def test_no_notation_emits_plain_format_call_no_replace_chain() -> None:
    """Calling numeral_vega_expr without a notation argument must produce a plain
    format() call with no replace() chain.

    Callers that want house notation pass it explicitly (notation="analytic" /
    notation="narrative"). The default must be no post-process so that inline d3
    specs produce native d3 output in Vega, matching format_d3's own no-notation
    behavior.
    """
    expr = numeral_vega_expr("datum.value", _SI_SPEC)
    assert "replace" not in expr, (
        f"no notation → no replace() chain must be emitted; got: {expr!r}"
    )
    assert expr == f"format(datum.value,{json.dumps(round_aware_spec(_SI_SPEC))})", (
        f"no notation must emit a bare format() call; got: {expr!r}"
    )
