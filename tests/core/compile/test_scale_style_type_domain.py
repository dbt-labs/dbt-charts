"""ScaleContinuousStyle.type is a Literal of the scale types the engine actually
wires through end-to-end, and scale.continuous.domain is a strict, fixed
2-tuple of int|float|str (shape and per-element type enforced by the
annotation) plus a validator for what the annotation can't express:
type-consistency (temporal domains must be ISO date/datetime strings).

Regression coverage: an authored ``scale.continuous.type`` value the engine does
not understand, or a domain that does not match the declared scale type, must
raise at compile time -- not pass through silently to Vega-Lite and produce a
broken render.

All continuous-scale fields (type, domain, zero, base, exponent, constant) live
on ``ScaleContinuousStyle`` after the per-variant split; ``BaseScaleStyle``
carries only universal fields (round, clamp, nice, padding) and the four group
sub-objects.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.theme.axis import (
    ScaleContinuousStyle,
    ScaleLogStyle,
    ScalePowStyle,
    ScaleSymlogStyle,
)


def test_scale_type_accepts_known_literal_values():
    for t in ("linear", "log", "pow", "sqrt", "symlog", "temporal"):
        assert ScaleContinuousStyle(type=t).type == t


def test_scale_type_rejects_unknown_string():
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(type="nominal")


def test_scale_type_rejects_arbitrary_garbage():
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(type="not-a-scale-type")


def test_scale_domain_requires_exactly_two_elements():
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=[1.0])


def test_scale_domain_rejects_more_than_two_elements():
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=["A", "B", "C"])


def test_scale_domain_two_elements_ok_without_type():
    scale = ScaleContinuousStyle(domain=[400.0, 600.0])
    assert scale.domain == (400.0, 600.0)


def test_scale_type_temporal_with_iso_date_domain_ok():
    scale = ScaleContinuousStyle(type="temporal", domain=["1955-01-01", "2026-01-01"])
    assert scale.domain == ("1955-01-01", "2026-01-01")


def test_scale_type_temporal_with_iso_datetime_domain_ok():
    scale = ScaleContinuousStyle(
        type="temporal", domain=["1955-01-01T00:00:00", "2026-01-01T00:00:00"]
    )
    assert scale.type == "temporal"


def test_scale_type_temporal_with_z_suffixed_utc_domain_ok():
    """A trailing 'Z' UTC suffix must validate identically on Python 3.10
    (where fromisoformat rejects it) and 3.11+ (where it parses)."""
    scale = ScaleContinuousStyle(
        type="temporal", domain=["1955-01-01T00:00:00Z", "2026-01-01T00:00:00Z"]
    )
    assert scale.type == "temporal"


def test_scale_type_temporal_rejects_numeric_domain():
    """A temporal scale.type with a plain numeric domain is not date-shaped --
    the author almost certainly meant a quantitative scale (or forgot to quote
    the dates); raise rather than silently pass a broken domain through."""
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(type="temporal", domain=[1955, 2026])


def test_scale_type_temporal_rejects_non_iso_string_domain():
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(type="temporal", domain=["not-a-date", "also-not-a-date"])


def test_scale_domain_rejects_null_bound() -> None:
    """A one-ended `domain: [1.0, null]` must raise, not be silently discarded.

    The shape validates today (2 elements, non-temporal), then `null` fails the
    all-numeric guard in `numeric_domain_bounds` and the whole domain is dropped
    in three places — the VL scale, the tick ladder, and the unity-baseline gate
    — with no error and no sign anything was ignored. Rejection now comes from
    the field's own int|float|str element typing (None matches none of the
    three arms), not a hand-written check -- so no message is pinned here.
    """
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=[1.0, None])


def test_scale_domain_rejects_null_lower_bound() -> None:
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=[None, 1.2])


def test_scale_domain_iso_strings_still_valid_without_temporal_type() -> None:
    scale = ScaleContinuousStyle(domain=["1955-01-01", "2026-01-01"])
    assert scale.domain == ("1955-01-01", "2026-01-01")


def test_scale_type_temporal_rejects_non_list_iterable_domain():
    """A set/frozenset/deque/generator isn't a str or dict, so it slips past
    the "before" validator's isinstance guard -- the mixin's own message
    fires first (before the strict-tuple annotation gets a chance), giving a
    temporal-specific error rather than a generic "wrong container" one."""
    with pytest.raises(ValidationError, match="must be a list of 2 ISO-8601"):
        ScaleContinuousStyle(type="temporal", domain={1955, 2026})


def test_scale_domain_rejects_non_list_iterable_without_temporal_type():
    """Regression: domain's shape check must not be scoped to type: temporal.
    A set is silently coerced into a tuple by pydantic's lax mode -- in hash
    order, which is nondeterministic for a [low, high] domain -- unless the
    outer tuple itself is strict."""
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain={1955, 2026})


def test_scale_domain_rejects_non_scalar_element():
    """Each domain bound is int|float|str -- a non-scalar element (e.g. a dict)
    must raise, not pass through untyped to Vega-Lite."""
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=[{"not": "a scalar"}, 100])


def test_scale_domain_rejects_bool_element():
    """bool is an int subclass -- without an explicit exclusion, pydantic's lax
    mode would silently rewrite an authored `true`/`false` bound to `1`/`0`."""
    with pytest.raises(ValidationError):
        ScaleContinuousStyle(domain=[True, False])


# ---------------------------------------------------------------------------
# Authored boundary -- the patch model and the full board-compile path.
#
# ScaleContinuousStylePatch is generated by build_patch_model_ext, which
# rebuilds each field's FieldInfo from scratch and does NOT copy validators
# from ScaleContinuousStyle -- only validators on the patch's own base class
# survive. These tests pin that chart-local authored input gets the same
# domain rules as theme-stage input by two different mechanisms: shape,
# per-element type, and the bool exclusion all live on the field's own
# annotation (which the factory copies verbatim, so they reach the Patch
# regardless of the mixin); only the temporal ISO-date check is a mixin
# validator, and it reaches the Patch because ScaleDomainValidationMixin is
# on the Patch's base class.
# ---------------------------------------------------------------------------


def test_scale_style_patch_rejects_three_element_domain():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(domain=[1.0, 2.0, 3.0])


def test_scale_style_patch_rejects_non_scalar_element():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(domain=[{"not": "a scalar"}, 100])


def test_scale_style_patch_rejects_bool_element():
    """bool is an int subclass -- without an explicit exclusion, pydantic's lax
    mode would silently rewrite an authored `true`/`false` bound to `1`/`0`."""
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(domain=[True, False])


def test_scale_style_patch_rejects_non_list_iterable_domain():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(domain={1955, 2026})


def test_scale_style_patch_rejects_numeric_temporal_domain():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(type="temporal", domain=[1955, 2026])


def test_scale_style_patch_rejects_null_bound() -> None:
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    with pytest.raises(ValidationError):
        ScaleContinuousStylePatch(domain=[1.0, None])


def test_scale_style_patch_accepts_valid_temporal_domain():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    patch = ScaleContinuousStylePatch(
        type="temporal", domain=["1955-01-01", "2026-01-01"]
    )
    assert patch.domain == ("1955-01-01", "2026-01-01")


_BOARD_WITH_DOMAIN = """
queries:
  q:
    type: values
    columns: [d, v]
    values:
      - ["2024-01-01", 1]
charts:
  c:
    query: q
    type: bar
    x: d
    y: v
    style:
      axis_x:
        scale:
          continuous:
            domain: {domain}
"""


def _compile_board_with_domain(domain_yaml: str):
    from dbt_charts.core.compile.compiler import compile as compile_board

    return compile_board(_BOARD_WITH_DOMAIN.format(domain=domain_yaml))


def test_board_compile_rejects_three_element_authored_domain():
    """The user-facing boundary: a 3-element scale.continuous.domain in board YAML
    fails compile with a structured field error, not a silent pass-through."""
    result = _compile_board_with_domain('["a", "b", "c"]')
    assert result.errors, "expected a compile error for a 3-element domain"
    assert any(
        e.code == "ERR-VALIDATION-FIELD"
        and e.path is not None
        and e.path.endswith("continuous.domain")
        for e in result.errors
    )


def test_board_compile_accepts_two_element_authored_domain():
    result = _compile_board_with_domain("[400, 600]")
    assert not result.errors, f"unexpected compile errors: {result.errors}"


# ---------------------------------------------------------------------------
# Log/pow/symlog scale params -- base/exponent/constant require the matching
# type, and type: log is incompatible with zero: true (a log domain cannot
# include 0). These validators live on ScaleContinuousStyle itself, NOT on the
# shared ScaleDomainValidationMixin -- unlike the domain temporal-type check,
# a scalar field's
# type pairing can legitimately span two different cascade layers (e.g. a theme
# sets scale.continuous.type: log globally, a chart-local patch sets only
# base: 2), so the check must fire on the fully-merged ScaleContinuousStyle,
# not on an isolated authored-patch fragment. See test_scale_style_patch_lenient_*
# below.
# ---------------------------------------------------------------------------


def test_scale_style_base_requires_log_type():
    scale = ScaleContinuousStyle(type="log", log=ScaleLogStyle(base=2.0))
    assert scale.log.base == 2.0


def test_scale_style_base_without_log_type_raises():
    with pytest.raises(ValidationError, match="log.base"):
        ScaleContinuousStyle(log=ScaleLogStyle(base=2.0))


def test_scale_style_base_with_wrong_type_raises():
    with pytest.raises(ValidationError, match="log.base"):
        ScaleContinuousStyle(type="linear", log=ScaleLogStyle(base=2.0))


def test_scale_style_exponent_requires_pow_type():
    scale = ScaleContinuousStyle(type="pow", pow=ScalePowStyle(exponent=0.5))
    assert scale.pow.exponent == 0.5


def test_scale_style_exponent_without_pow_type_raises():
    with pytest.raises(ValidationError, match="pow.exponent"):
        ScaleContinuousStyle(pow=ScalePowStyle(exponent=0.5))


def test_scale_style_constant_requires_symlog_type():
    scale = ScaleContinuousStyle(type="symlog", symlog=ScaleSymlogStyle(constant=1.0))
    assert scale.symlog.constant == 1.0


def test_scale_style_constant_without_symlog_type_raises():
    with pytest.raises(ValidationError, match="symlog.constant"):
        ScaleContinuousStyle(symlog=ScaleSymlogStyle(constant=1.0))


def test_scale_style_log_type_with_zero_true_raises():
    with pytest.raises(ValidationError, match="zero"):
        ScaleContinuousStyle(type="log", zero=True)


@pytest.mark.parametrize("domain", [[0, 1_000], [-5, 1_000]])
def test_scale_style_log_type_with_non_positive_domain_raises(
    domain: list[int],
) -> None:
    with pytest.raises(ValidationError, match="domain"):
        ScaleContinuousStyle(type="log", domain=domain)


def test_scale_style_log_type_with_positive_domain_ok() -> None:
    scale = ScaleContinuousStyle(type="log", domain=[1, 1_000])
    assert scale.domain == (1, 1_000)


@pytest.mark.parametrize("base", [0.0, -2.0, 1.0])
def test_scale_style_log_base_outside_valid_range_raises(base: float) -> None:
    with pytest.raises(ValidationError, match="log.base"):
        ScaleContinuousStyle(type="log", log=ScaleLogStyle(base=base))


def test_scale_style_log_type_with_zero_false_ok():
    scale = ScaleContinuousStyle(type="log", zero=False)
    assert scale.type == "log"


def test_scale_style_log_type_without_zero_ok():
    scale = ScaleContinuousStyle(type="log")
    assert scale.zero is None


def test_scale_style_patch_lenient_on_base_without_type():
    """An isolated chart-local patch fragment must NOT enforce base<->type
    pairing -- the type may come from a different cascade layer (e.g. a theme
    default) than the one setting base. Only the fully-merged ScaleContinuousStyle
    enforces the pairing (see test_merge_onto_base_* below)."""
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    patch = ScaleContinuousStylePatch(log={"base": 2.0})
    assert patch.log.base == 2.0


def test_scale_style_patch_lenient_on_zero_with_log_type():
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    patch = ScaleContinuousStylePatch(type="log", zero=True)
    assert patch.zero is True


def test_merge_onto_base_validates_base_paired_across_layers():
    """A theme-cascade base with type: log, merged with a chart-local patch
    that only sets base, must produce a valid merged ScaleContinuousStyle --
    proving the pairing validator fires on the merged result, not on the
    isolated patch."""
    from dbt_charts.core.compile.merge import merge_onto_base
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    base = ScaleContinuousStyle(type="log")
    patch = ScaleContinuousStylePatch(log={"base": 2.0})
    merged = merge_onto_base(base, patch)
    assert merged.type == "log"
    assert merged.log.base == 2.0


def test_merge_onto_base_rejects_base_without_type_across_layers():
    """The inverse: a linear-type base merged with a chart-local base-only
    patch must still raise -- the merged result has base set with no log type
    from either layer."""
    from dbt_charts.core.compile.merge import merge_onto_base
    from dbt_charts.core.compile.models.style.authored import ScaleContinuousStylePatch

    base = ScaleContinuousStyle(type="linear")
    patch = ScaleContinuousStylePatch(log={"base": 2.0})
    with pytest.raises(ValidationError, match="log.base"):
        merge_onto_base(base, patch)
