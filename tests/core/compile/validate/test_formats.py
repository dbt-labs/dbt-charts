"""ERR-FORMAT-INVALID: unresolvable format specs are caught at compile().

A format string that is not a theme/board alias, not a native (non-d3)
formatter key, and not a valid d3-format spec must fail compile() instead of
passing validation and blowing up as ERR-INTERNAL deep inside rasterization.
"""

from __future__ import annotations

import pytest
import yaml

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.text.format_d3 import format_d3

from ..._svg_render import render_board_to_svg


def _board_with_format(format_value: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: {format_value}
rows:
  - revenue
"""


def test_typo_format_fails_compile_with_did_you_mean() -> None:
    """format: percent_1 (typo for percent/percent_whole/percent_delta) fails compile."""
    result = compile_board(_board_with_format("percent_1"))

    assert not result.success, "percent_1 is not a valid alias or d3 spec — must fail"
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "percent_1" in error.message
    assert error.hint is not None
    assert any(
        alias in error.hint for alias in ("percent", "percent_whole", "percent_delta")
    )


def test_valid_alias_compiles_clean() -> None:
    """A real alias (percent) is unaffected by the new validation."""
    result = compile_board(_board_with_format("percent"))
    assert result.success, f"Compile failed: {result.errors}"


def test_valid_raw_d3_spec_compiles_clean() -> None:
    """A raw d3-format spec (not an alias) still passes through."""
    result = compile_board(_board_with_format('",.2f"'))
    assert result.success, f"Compile failed: {result.errors}"


def test_native_formatter_rejected_on_vega_number_format() -> None:
    """percent_number on number_format (Vega-painted) must fail at compile.

    number_format is rendered by Vega, which has no equivalent for the
    Python-only native formatter. Compile must reject it before Vega crashes.
    """
    result = compile_board(_board_with_format("percent_number"))
    assert not result.success, "percent_number on number_format must fail compile"
    assert result.errors[0].code == "ERR-FORMAT-NATIVE-IN-VEGA-SLOT"


def test_native_formatter_remedy_never_names_a_percent_spec_without_the_division() -> (
    None
):
    """These formats hold whole-number values; `.1%` alone paints them 100x
    too large, so the message may name it only after the divide-by-100."""
    for name in ("percent_number", "percentage_points_delta"):
        message = compile_board(_board_with_format(name)).errors[0].message
        assert message.count(".1%") == 1
        assert message.index("divide by 100") < message.index(".1%")


def test_year_alias_compiles_clean() -> None:
    """year is a theme alias (resolving to the raw d3 spec "d") — must not error."""
    result = compile_board(_board_with_format("year"))
    assert result.success, f"Compile failed: {result.errors}"


def test_percentage_points_delta_rejected_on_vega_number_format() -> None:
    """percentage_points_delta on number_format (Vega-painted) must fail at compile."""
    result = compile_board(_board_with_format("percentage_points_delta"))
    assert not result.success, (
        "percentage_points_delta on number_format must fail compile"
    )
    assert result.errors[0].code == "ERR-FORMAT-NATIVE-IN-VEGA-SLOT"


def test_percent_number_valid_on_kpi_format() -> None:
    """percent_number is a Python-painted slot on KPI — must compile clean."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 42.5 AS pct
charts:
  headline:
    query: q
    type: kpi
    value: pct
    style:
      value:
        format: percent_number
rows:
  - headline
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_percentage_points_delta_valid_on_kpi_support_format() -> None:
    """percentage_points_delta on KPI support.format is Python-painted — must compile clean."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 42.5 AS pct, 1.2 AS delta
charts:
  headline:
    query: q
    type: kpi
    value: pct
    support:
      value: delta
      format: percentage_points_delta
rows:
  - headline
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_axis_y_strftime_compiles_clean() -> None:
    """A raw strftime spec on axis_y.labels.format is a legitimate time-format slot."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          format: "%b %Y"
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_axis_y_mirror_strftime_compiles_clean() -> None:
    """A raw strftime spec on axis_y.mirror.format is also accepted."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        mirror:
          format: "%b %Y"
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_table_column_strftime_compiles_clean() -> None:
    """style.columns.<col>.format also accepts a raw strftime spec."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  detail:
    query: q
    type: table
    style:
      columns:
        month:
          format: "%b %Y"
rows:
  - detail
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_kpi_value_format_typo_fails_compile() -> None:
    """style.value.format is number-only — a typo there also fails compile."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 100 AS revenue
charts:
  headline:
    query: q
    type: kpi
    value: revenue
    style:
      value:
        format: percent_1
rows:
  - headline
"""
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_kpi_support_format_typo_fails_compile() -> None:
    """support.format (KPI) is also validated."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 100 AS revenue, 5 AS delta
charts:
  headline:
    query: q
    type: kpi
    value: revenue
    support:
      value: delta
      format: percent_1
rows:
  - headline
"""
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_support_table_entry_format_typo_fails_compile() -> None:
    """support_table entries' format is validated too."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: percent_1
rows:
  - revenue
"""
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_support_table_entry_affix_with_non_default_sign_flag_compiles_clean() -> None:
    """A support_table entry is exempt from the sign-conflict check that rejects the
    same shape on axis_y.labels.
    """
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: -100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    support_table:
      - source: revenue_eur
        format:
          spec: "(,.0f"
          suffix: " EUR"
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    # d3's accounting flag wraps a negative in parens; painted alongside the authored
    # suffix that must survive untouched.
    assert "(100) EUR" in svg
    assert "−100 EUR" not in svg


def test_support_table_entry_affix_with_no_spec_compiles_clean() -> None:
    """A spec-less FormatConfig ({prefix: "EUR "} alone) on a support_table entry has no
    digit format of its own.
    """
    no_format_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    support_table:
      - source: revenue_eur
rows:
  - revenue
"""
    no_format_result = compile_board(no_format_board)
    assert no_format_result.success, no_format_result.errors
    no_format_svg = render_board_to_svg(no_format_board)
    assert ">100<" in no_format_svg

    prefixed_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    support_table:
      - source: revenue_eur
        format:
          prefix: "EUR "
rows:
  - revenue
"""
    prefixed_result = compile_board(prefixed_board)
    assert prefixed_result.success, prefixed_result.errors
    prefixed_svg = render_board_to_svg(prefixed_board)
    assert "EUR 100" in prefixed_svg


def test_donut_total_affix_with_non_default_sign_flag_compiles_clean() -> None:
    """The donut center total (TotalStyle) is exempt from the same sign-conflict check
    for the same reason as support_table.
    """
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      format:
        spec: "(,.0f"
        prefix: "€"
rows:
  - share
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "€" in svg


def test_donut_total_digits_match_with_and_without_a_prefix() -> None:
    """Adding a prefix must change nothing but the prefix."""
    no_prefix_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: a, v: 168000}
      - {k: b, v: 28000}
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      label: Net
rows:
  - share
"""
    prefixed_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: a, v: 168000}
      - {k: b, v: 28000}
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      label: Net
    style:
      total:
        value:
          format:
            prefix: "EUR "
rows:
  - share
"""
    no_prefix_result = compile_board(no_prefix_board)
    assert no_prefix_result.success, no_prefix_result.errors
    no_prefix_svg = render_board_to_svg(no_prefix_board)
    assert "196,000" in no_prefix_svg

    prefixed_result = compile_board(prefixed_board)
    assert prefixed_result.success, prefixed_result.errors
    prefixed_svg = render_board_to_svg(prefixed_board)
    assert "EUR 196,000" in prefixed_svg
    assert "196k" not in prefixed_svg


def _assert_format_invalid(yaml: str, needle: str) -> None:
    result = compile_board(yaml)
    assert not result.success, f"{needle} must fail compile, got success"
    assert [e.code for e in result.errors] == ["ERR-FORMAT-INVALID"], result.errors
    assert needle in result.errors[0].message


def test_affix_with_accounting_sign_flag_compiles_clean_on_every_vega_painted_slot() -> (
    None
):
    """A FormatConfig prefix/suffix combined with a "(" d3 sign flag used to fail
    compile on every Vega-painted slot.
    """
    board = """
title: T
style:
  formats:
    eur: {spec: "(,.0f", suffix: " EUR"}
  charts:
    tooltip:
      format: eur
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: -100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format:
            spec: "(,.0f"
            suffix: " EUR"
      marks:
        bar:
          labels:
            visible: true
            format:
              spec: "(,.0f"
              suffix: " EUR"
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert svg.count("(100) EUR") >= 3, svg


def test_affix_with_plus_sign_flag_compiles_clean_on_a_vega_painted_slot() -> None:
    """A "+" sign flag composes correctly on a Vega-painted slot too, for the same
    reason as the accounting "(" flag above.
    """
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format:
            spec: "+,.0f"
            prefix: "€"
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "+€100" in svg


def test_affix_with_plus_sign_flag_compiles_clean_on_a_kpi() -> None:
    """A "+" sign flag composes correctly with an authored affix on a Python-painted
    slot.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 1234 AS revenue_eur
charts:
  headline:
    query: q
    type: kpi
    value: revenue_eur
    style:
      value:
        format:
          spec: "+,.0f"
          prefix: "€"
rows:
  - headline
"""
    )
    assert result.success, result.errors


def test_affix_with_parenthesis_sign_flag_compiles_clean_on_a_kpi() -> None:
    """A "(" sign flag also composes correctly on a Python-painted slot."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT -154500 AS revenue_eur
charts:
  headline:
    query: q
    type: kpi
    value: revenue_eur
    style:
      value:
        format:
          spec: "(,.0f"
          prefix: "€"
rows:
  - headline
"""
    )
    assert result.success, result.errors


def test_affix_with_default_sign_flag_compiles_clean() -> None:
    """The default sign flag ('-', or unspecified) is unaffected."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue_eur
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format:
            spec: ",.0f"
            prefix: "€"
rows:
  - revenue
"""
    )
    assert result.success, result.errors


def _board_with_axis_y_format(format_yaml: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue_eur
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format: {format_yaml}
rows:
  - revenue
"""


def test_affix_with_native_dollar_prefix_compiles_clean_on_a_non_si_axis() -> None:
    """A fixed-point spec's own native "$" and an authored prefix are not a render
    conflict on a non-SI axis.
    """
    result = compile_board(_board_with_axis_y_format('{spec: "$,.2f", prefix: "€"}'))
    assert result.success, result.errors


def test_affix_with_native_dollar_prefix_compiles_clean_on_an_si_axis() -> None:
    """An SI ("~s") spec's own native "$" (the "currency" preset, "$.3~s") composes
    around an authored prefix, not just the fixed-point case above.
    """
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_usd: 12400000}
      - {month: Feb, revenue_usd: -3100000}
  q2:
    type: values
    rows:
      - {k: a, v: 154500}
      - {k: b, v: 41500}
charts:
  axis_and_number:
    query: q
    type: bar
    x: month
    y: revenue_usd
    style:
      axis_y:
        labels:
          format: {spec: "$.3~s", prefix: "US "}
  support_table_entry:
    query: q
    type: bar
    x: month
    y: revenue_usd
    support_table:
      - source: revenue_usd
        format: {spec: "$.3~s", prefix: "US "}
  total_donut:
    query: q2
    type: donut
    theta: v
    color: k
    total:
      format: {spec: "$.3~s", prefix: "US "}
rows:
  - axis_and_number
  - support_table_entry
  - total_donut
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    # Axis ladder: a spaced prefix leads the sign, the symbol follows it.
    assert "US $5mn" in svg
    assert "US −$5mn" in svg
    # support_table: each value's own SI magnitude, same composition.
    assert "US $12.4M" in svg
    assert "US −$3.1M" in svg
    # Donut total: same composition, no sign case (theta can't be negative).
    assert "US $196k" in svg
    assert "$US" not in svg


def test_affix_with_native_dollar_suffix_compiles_clean() -> None:
    """An authored SUFFIX combined with a spec that already carries its own "$" symbol
    is legal.
    """
    result = compile_board(_board_with_axis_y_format('{spec: "$,.0f", suffix: " net"}'))
    assert result.success, result.errors


def test_affix_with_native_dollar_prefix_compiles_clean_on_a_kpi() -> None:
    """The symbol conflict is scoped to Vega-painted slots, like the sign check above
    it.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 154500 AS revenue_usd
charts:
  headline:
    query: q
    type: kpi
    value: revenue_usd
    style:
      value:
        format:
          spec: "$,.0f"
          prefix: "US "
rows:
  - headline
"""
    )
    assert result.success, result.errors


def test_affix_with_native_dollar_prefix_paints_consistently_on_every_vega_slot() -> (
    None
):
    """ "$,.0f" + prefix: "US " must compile and paint prefix-then-sign-then-native-
    symbol-body identically on every Vega-painted slot.
    """
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_usd: 154500}
      - {month: Feb, revenue_usd: -41500}
charts:
  axis_and_number:
    query: q
    type: bar
    x: month
    y: revenue_usd
    style:
      axis_y:
        labels:
          format: {spec: "$,.0f", prefix: "US "}
      number_format: {spec: "$,.0f", prefix: "US "}
  bar_labels:
    query: q
    type: bar
    x: month
    y: revenue_usd
    style:
      marks:
        bar:
          labels:
            visible: true
            format: {spec: "$,.0f", prefix: "US "}
  support_table_entry:
    query: q
    type: bar
    x: month
    y: revenue_usd
    support_table:
      - source: revenue_usd
        format: {spec: "$,.0f", prefix: "US "}
rows:
  - axis_and_number
  - bar_labels
  - support_table_entry
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "US $154,500" in svg
    assert "US −$41,500" in svg
    assert "$US " not in svg


def test_affix_with_native_dollar_prefix_compiles_clean_on_a_donut_total() -> None:
    """The same "$,.0f" + prefix: "US " shape on the donut center total
    (``style.total.value.format``, or the chart-level ``total.format``
    shorthand) must compile and paint "US $196,000", the same composer as
    the axis/support_table case above (a donut's theta can never itself be
    negative -- ERR-PIE-NEGATIVE-THETA -- so there is no sign case to pin
    here)."""
    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: a, v: 154500}
      - {k: b, v: 41500}
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      format: {spec: "$,.0f", prefix: "US "}
rows:
  - share
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "US $196,000" in svg


def test_affix_with_spec_less_format_config_falls_back_on_a_vega_painted_slot() -> None:
    """A spec-less FormatConfig ({prefix: "EUR "} alone) has no digit format for a Vega
    painter to compose the affix around.
    """
    no_format_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: 1500}
      - {month: Feb, revenue_eur: 3200}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
rows:
  - revenue
"""
    no_format_result = compile_board(no_format_board)
    assert no_format_result.success, no_format_result.errors
    no_format_svg = render_board_to_svg(no_format_board)
    assert "1,000" in no_format_svg
    assert "3,000" in no_format_svg

    prefixed_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: 1500}
      - {month: Feb, revenue_eur: 3200}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format:
            prefix: "EUR "
rows:
  - revenue
"""
    prefixed_result = compile_board(prefixed_board)
    assert prefixed_result.success, prefixed_result.errors
    prefixed_svg = render_board_to_svg(prefixed_board)
    assert "EUR 1,000" in prefixed_svg
    assert "EUR 3,000" in prefixed_svg
    assert "1k" not in prefixed_svg


def _axis_y_sub_unit_ticks(svg: str) -> list[str]:
    import re

    return [t for t in re.findall(r"<text[^>]*>(.*?)</text>", svg) if "EUR" in t]


def test_spec_less_affix_on_axis_x_takes_the_non_compacting_sub_unit_guard() -> None:
    """A spec-less affix on a quantitative axis_x (a scatter's x column) must take the
    same non-compacting sub-unit guard the primary axis_y gets
    (``ResolvedFormat.is_spec_less_affix``, ``_axes.py``) -- 0.18/0.42/ 0.95 as
    ``EUR 0.2``/``EUR 0.4``, never d3's own raw SI prefix (``EUR 200m``, a milli
    misread as a magnitude suffix).
    """
    inline_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {x_val: 0.18, y_val: 5}
      - {x_val: 0.42, y_val: 15}
      - {x_val: 0.95, y_val: 25}
charts:
  scatter_chart:
    query: q
    type: scatter
    x: x_val
    y: y_val
    style:
      axis_x:
        labels:
          format: {prefix: "EUR "}
rows:
  - scatter_chart
"""
    inline_result = compile_board(inline_board)
    assert inline_result.success, inline_result.errors
    inline_svg = render_board_to_svg(inline_board)
    assert "m" not in "".join(_axis_y_sub_unit_ticks(inline_svg)), inline_svg
    assert any(
        "EUR 0.2" in t or "EUR 0.4" in t for t in _axis_y_sub_unit_ticks(inline_svg)
    )

    alias_board = """
title: T
style:
  formats:
    eur:
      prefix: "EUR "
queries:
  q:
    type: values
    rows:
      - {x_val: 0.18, y_val: 5}
      - {x_val: 0.42, y_val: 15}
      - {x_val: 0.95, y_val: 25}
charts:
  scatter_chart:
    query: q
    type: scatter
    x: x_val
    y: y_val
    style:
      axis_x:
        labels:
          format: eur
rows:
  - scatter_chart
"""
    alias_result = compile_board(alias_board)
    assert alias_result.success, alias_result.errors
    alias_svg = render_board_to_svg(alias_board)
    assert "m" not in "".join(_axis_y_sub_unit_ticks(alias_svg)), alias_svg
    assert _axis_y_sub_unit_ticks(alias_svg) == _axis_y_sub_unit_ticks(inline_svg)


def test_spec_less_affix_on_an_overlay_layer_axis_y_takes_the_sub_unit_guard() -> None:
    """The same non-compacting sub-unit guard as the test above, on an overlay layer's
    own ``axis_y``.
    """
    inline_board = """
title: T
queries:
  q:
    type: values
    rows:
      - {month: Jan, a: 100, b: 0.18}
      - {month: Feb, a: 200, b: 0.42}
      - {month: Mar, a: 150, b: 0.95}
charts:
  combo:
    query: q
    type: bar
    x: month
    y: a
    layers:
      - type: line
        y: b
        axis_y:
          position: right
          labels:
            format: {prefix: "EUR "}
rows:
  - combo
"""
    inline_result = compile_board(inline_board)
    assert inline_result.success, inline_result.errors
    inline_svg = render_board_to_svg(inline_board)
    inline_ticks = _axis_y_sub_unit_ticks(inline_svg)
    assert "m" not in "".join(inline_ticks), inline_svg
    assert any("EUR 0.5" in t or "EUR 1" in t for t in inline_ticks), inline_ticks

    alias_board = """
title: T
style:
  formats:
    eur:
      prefix: "EUR "
queries:
  q:
    type: values
    rows:
      - {month: Jan, a: 100, b: 0.18}
      - {month: Feb, a: 200, b: 0.42}
      - {month: Mar, a: 150, b: 0.95}
charts:
  combo:
    query: q
    type: bar
    x: month
    y: a
    layers:
      - type: line
        y: b
        axis_y:
          position: right
          labels:
            format: eur
rows:
  - combo
"""
    alias_result = compile_board(alias_board)
    assert alias_result.success, alias_result.errors
    alias_svg = render_board_to_svg(alias_board)
    alias_ticks = _axis_y_sub_unit_ticks(alias_svg)
    assert "m" not in "".join(alias_ticks), alias_svg
    assert alias_ticks == inline_ticks


def test_affix_with_spec_less_format_config_compiles_clean_on_a_kpi() -> None:
    """A spec-less FormatConfig is legal on a Python-painted slot."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 1234 AS revenue_eur
charts:
  headline:
    query: q
    type: kpi
    value: revenue_eur
    style:
      value:
        format:
          prefix: "€"
rows:
  - headline
"""
    )
    assert result.success, result.errors


def test_affix_with_si_shaped_spec_compiles_clean() -> None:
    """An authored prefix combined with a bare SI ("~s") spec is legal."""
    result = compile_board(_board_with_axis_y_format('{spec: ".3~s", prefix: "€"}'))
    assert result.success, result.errors


def test_affix_with_predefined_currency_name_compiles_clean() -> None:
    """The predefined-name branch resolves to its own d3 spec ($.3~s for "currency")."""
    result = compile_board(_board_with_axis_y_format('{spec: currency, prefix: "€"}'))
    assert result.success, result.errors


def test_affix_with_predefined_plain_name_compiles_clean() -> None:
    """A predefined name with no native symbol and no SI shape (e.g. "integer" ->
    ",.0f") has no house ladder to collide with.
    """
    result = compile_board(_board_with_axis_y_format('{spec: integer, prefix: "€"}'))
    assert result.success, result.errors


def test_affix_with_alias_sign_flag_compiles_clean() -> None:
    """A style.formats alias name (not a predefined name, not a raw literal) resolves to
    its own target spec before this check runs.
    """
    board = """
title: T
style:
  formats:
    signed: "(,.0f"
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue_eur: -100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue_eur
    style:
      axis_y:
        labels:
          format: {spec: signed, prefix: "€"}
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "€(100)" in svg


def test_affix_on_time_format_rejected() -> None:
    """A strftime spec has no d3 symbol/sign/SI shape for an affix to compose against,
    and no render path paints a FormatConfig affix on a temporal slot.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::date AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: line
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format:
            spec: "%b %Y"
            prefix: "FY "
rows:
  - revenue
"""
    )
    assert not result.success
    assert [e.code for e in result.errors] == ["ERR-FORMAT-AFFIX-TIME-UNSUPPORTED"]


def test_time_affix_error_never_sends_notation_to_a_kpi_or_table() -> None:
    from dbt_charts.core.diagnostics.codes_compile import (
        ERR_FORMAT_AFFIX_TIME_UNSUPPORTED,
    )

    template = ERR_FORMAT_AFFIX_TIME_UNSUPPORTED.message_template
    assert "move a prefix/suffix" in template
    assert "prefix/suffix/notation to" not in template
    assert "move it" not in template


def test_affix_on_predefined_time_name_rejected() -> None:
    """A predefined TIME name (date_short) has no entry in PREDEFINED_SPECS (number-
    only).
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::date AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: line
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format:
            spec: date_short
            prefix: "FY "
rows:
  - revenue
"""
    )
    assert not result.success
    assert [e.code for e in result.errors] == ["ERR-FORMAT-AFFIX-TIME-UNSUPPORTED"]


def test_affix_on_alias_time_name_rejected() -> None:
    """A style.formats alias whose own target is a strftime pattern must also reject an
    authored affix.
    """
    result = compile_board(
        """
title: T
style:
  formats:
    mymonth: "%b %Y"
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::date AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: line
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format:
            spec: mymonth
            prefix: "FY "
rows:
  - revenue
"""
    )
    assert not result.success
    assert [e.code for e in result.errors] == ["ERR-FORMAT-AFFIX-TIME-UNSUPPORTED"]


@pytest.mark.parametrize(
    "style",
    [
        {"columns": {"period": {"format": {"spec": "%b %Y", "prefix": "FY "}}}},
        {"columns": {"period": {"format": {"spec": "date_short", "suffix": " (FY)"}}}},
        {"columns": {"period": {"format": {"spec": "%b %Y", "notation": "analytic"}}}},
        {"column_defaults": {"format": {"spec": "date_short", "suffix": " (FY)"}}},
        {"column_defaults": {"format": {"spec": "%b %Y", "prefix": "FY "}}},
    ],
    ids=[
        "column-strftime-prefix",
        "column-predefined-suffix",
        "column-notation",
        "defaults-predefined-suffix",
        "defaults-strftime-prefix",
    ],
)
def test_affix_beside_a_date_spec_on_a_table_column_still_compiles(
    style: dict[str, object],
) -> None:
    """A table column's format accepted a FormatConfig before affixes were composed
    anywhere, and paints a date through its own date painter.
    """
    result = compile_board(
        yaml.safe_dump(
            {
                "title": "T",
                "queries": {
                    "q": {
                        "source": "db",
                        "sql": "SELECT '2024-01-01'::date AS period, 100 AS revenue",
                    }
                },
                "charts": {"t": {"query": "q", "type": "table", "style": style}},
                "rows": ["t"],
            }
        )
    )
    assert result.success, result.errors


def test_mark_label_format_is_validated() -> None:
    """style.marks.bar.labels.format feeds the same consumer as number_format."""
    _assert_format_invalid(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      marks:
        bar:
          labels:
            format: percent_1
rows:
  - revenue
""",
        "percent_1",
    )


def test_donut_total_format_is_validated() -> None:
    """The donut center total carries its own format slot."""
    _assert_format_invalid(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'a' AS k, 1 AS v
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      format: percent_1
rows:
  - share
""",
        "percent_1",
    )


def test_layer_axis_y_label_format_is_validated() -> None:
    """Per-layer y-axis label format is authored input like any other."""
    _assert_format_invalid(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 1 AS a, 2 AS b
charts:
  combo:
    query: q
    type: bar
    x: month
    y: a
    layers:
      - type: line
        x: month
        y: b
        axis_y:
          labels:
            format: percent_1
rows:
  - combo
""",
        "percent_1",
    )


def test_alias_table_values_are_validated() -> None:
    """A bogus alias target must fail, not pass by virtue of being a key."""
    _assert_format_invalid(
        """
title: T
style:
  formats:
    my_alias: percent_1
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: my_alias
rows:
  - revenue
""",
        "percent_1",
    )


def test_nested_board_charts_are_validated() -> None:
    """A nested board carries its own charts and its own alias table."""
    _assert_format_invalid(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
rows:
  - height: 600
    charts:
      inner:
        query: q
        type: bar
        x: month
        y: revenue
        style:
          number_format: percent_1
    rows:
      - inner
""",
        "percent_1",
    )


def test_number_format_does_not_accept_a_strftime_spec() -> None:
    """number_format is the number slot; time specs belong on axis/column slots."""
    _assert_format_invalid(_board_with_format('"%b %Y"'), "%b %Y")


def test_axis_format_still_accepts_a_strftime_spec() -> None:
    """Axis ticks can render dates, so strftime stays valid there."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format: "%b %Y"
rows:
  - revenue
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_axis_band_format_still_accepts_a_strftime_spec() -> None:
    """style.axis_band merges into the resolved band axis, same as axis_x/axis_y."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_band:
        labels:
          format: "%b %Y"
rows:
  - revenue
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_axis_quantitative_format_still_accepts_a_strftime_spec() -> None:
    """style.axis_quantitative merges into the resolved quantitative axis, same as axis_x/axis_y."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_quantitative:
        labels:
          format: "%b %Y"
rows:
  - revenue
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_axis_format_override_still_accepts_a_strftime_spec() -> None:
    """style.axis (applies to both x and y) merges into the resolved axes too."""
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis:
        labels:
          format: "%b %Y"
rows:
  - revenue
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_board_level_style_format_is_validated() -> None:
    """A board-level `style:` block reaches the same consumer as chart-local style."""
    _assert_format_invalid(
        """
title: T
style:
  charts:
    kpi:
      value:
        format: percent_1
queries:
  q:
    source: db
    sql: SELECT 2 AS y
charts:
  c1:
    query: q
    type: kpi
    value: y
rows:
  - c1
""",
        "percent_1",
    )


def test_chart_id_cannot_widen_time_capability_of_its_slots() -> None:
    """A chart id spelling a time-capable field name must not relax its siblings.

    number_format is a number slot wherever it appears; naming the chart
    `columns` must not make a strftime spec acceptable underneath it.
    """
    _assert_format_invalid(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  columns:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: "%b %Y"
rows:
  - columns
""",
        "%b %Y",
    )


def test_board_level_timestamp_strftime_is_accepted() -> None:
    """style.timestamp.format is authored strftime and must not be rejected."""
    result = compile_board(
        """
title: T
style:
  timestamp:
    format: "%H:%M %Z on %-d %b %Y"
queries:
  q:
    source: db
    sql: SELECT 2 AS y
charts:
  c1:
    query: q
    type: kpi
    value: y
rows:
  - c1
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_full_d3_type_compiles_in_a_d3_evaluated_slot() -> None:
    """An axis format is handed to real d3 inside vl-convert.

    `p` (percent-to-significant-digits) is valid d3, so it must survive compile.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          format: ".1p"
rows:
  - revenue
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_full_d3_type_compiles_and_renders_in_a_port_evaluated_slot() -> None:
    """A KPI value is formatted by our Python port, not by d3 inside vl-convert.

    Accepting a spec at compile that the port then refuses at render is the
    exact ERR-INTERNAL failure this validation exists to remove, so the two
    grammars have to be the same one. Compiling is only half the claim --
    assert the port actually formats the spec.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 0.1234 AS rate
charts:
  rate:
    query: q
    type: kpi
    value: rate
    style:
      value:
        format: ".1p"
rows:
  - rate
"""
    )
    assert result.success, f"Compile failed: {result.errors}"
    assert format_d3(0.1234, ".1p") == "10%"


def test_genuinely_invalid_spec_still_fails() -> None:
    """Widening to full d3 must not weaken the check the task exists for."""
    _assert_format_invalid(_board_with_format("percent_1"), "percent_1")


def test_list_nested_format_error_carries_a_source_range() -> None:
    """List paths must match the source-map grammar (dots, not brackets).

    The map keys sequence items as `layers.0.…`; emitting `layers[0].…` looks
    up a key that never exists and silently drops the line number.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 1 AS a, 2 AS b
charts:
  combo:
    query: q
    type: bar
    x: month
    y: a
    layers:
      - type: line
        x: month
        y: b
        axis_y:
          labels:
            format: percent_1
rows:
  - combo
""",
        file="charts/t.yml",
    )
    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "[" not in (error.path or ""), f"bracket path won't resolve: {error.path}"
    assert error.range is not None, "list-nested error lost its source location"


def test_nested_board_alias_is_not_judged_against_the_root_alias_table() -> None:
    """A nested board's chart resolves against the nested board's own aliases.

    Normalization hoists nested-board charts into every ancestor's `charts`
    registry, so the same chart object is reachable from the root -- whose
    alias table never defined the alias the chart uses. Validating it there
    rejects a board that renders correctly.
    """
    result = compile_board(
        """
title: Root
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
rows:
  - title: Nested
    style:
      formats:
        mine: "$,.2f"
    charts:
      inner:
        query: q
        type: bar
        x: month
        y: revenue
        style:
          number_format: mine
    rows:
      - inner
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_nested_board_still_rejects_a_spec_no_alias_table_defines() -> None:
    """The nested-board carve-out must not become a blanket exemption."""
    result = compile_board(
        """
title: Root
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
rows:
  - title: Nested
    charts:
      inner:
        query: q
        type: bar
        x: month
        y: revenue
        style:
          number_format: percent_1
    rows:
      - inner
"""
    )
    assert not result.success, "percent_1 is not an alias anywhere — must fail"
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_a_nested_board_s_own_alias_merges_onto_the_root_s() -> None:
    """A nested board's own `formats` merges onto the root's — both resolve.

    The nested-scope cascade merges `formats` key-wise (`merge_patches` with
    the field's inferred `BY_KEY` strategy), not replace: a chart inside the
    nested board can use the root's alias (`arr`) and the nested board's own
    (`bps`) side by side.
    """
    result = compile_board(
        """
title: Root
style:
  formats:
    arr: "$,.0f"
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
rows:
  - title: Nested
    style:
      formats:
        bps: ".2%"
    charts:
      root_alias:
        query: q
        type: bar
        x: month
        y: revenue
        style:
          number_format: arr
      own_alias:
        query: q
        type: bar
        x: month
        y: revenue
        style:
          number_format: bps
    rows:
      - root_alias
      - own_alias
"""
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_style_formats_null_does_not_break_number() -> None:
    """Setting ``style.formats: null`` must NOT break number.

    ``number`` is now a predefined engine-owned format (PredefinedNumberFormat),
    not a theme alias. It resolves via the engine's own spec regardless of whether
    the authored board clears ``style.formats`` to null.
    """
    board_yaml = """
title: T
style:
  formats: null
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""
    result = compile_board(board_yaml)
    assert result.success, (
        f"style.formats: null must not break number (now predefined): {result.errors}"
    )


def test_tabs_nested_scope_explicit_formats_null_clears_the_table() -> None:
    """A tab that explicitly nulls ``style.formats`` must not inherit the root's.

    A `rows:`/`cols:` nested board honors an explicit ``formats: null`` already
    (it clears the inherited table). `tabs:` items go through a different
    normalization path (``_resolve_tab_items`` round-trips the tab through a
    dict) that must preserve the same explicit-null-vs-unset distinction.
    """
    result = compile_board(
        """
title: Root
style:
  formats:
    arr: "$,.0f"
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
tabs:
  items:
    - title: Detail
      style:
        formats: null
        background: "#ffffff"
      rows:
        - title: Inner
          type: bar
          query: q
          x: month
          y: revenue
          style:
            number_format: arr
"""
    )
    assert not result.success, (
        "a tab that explicitly nulls formats must not see the root's arr alias"
    )
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_style_formats_key_shadowing_predefined_member_raises() -> None:
    """A style.formats key equal to a predefined enum member name is a compile error.

    The engine owns those names. Shadowing one with a user-defined alias is
    always a mistake (the author almost certainly wanted to USE the predefined
    format, not replace it). Raise at compile, naming the offending key.
    """
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
style:
  formats:
    number: ",.0f"
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""
    )
    assert not result.success, (
        "shadowing a predefined format name in style.formats must fail compile()"
    )
    assert result.errors[0].code == "ERR-FORMAT-PREDEFINED-SHADOW"
    assert "number" in result.errors[0].message
    assert "Cannot define" in result.errors[0].message


def _board_with_alias(alias_target: str, slot: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
style:
  formats:
    mine: {alias_target}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      {slot}: mine
rows:
  - revenue
"""


@pytest.mark.parametrize(
    "alias_target",
    [
        pytest.param("currency", id="preset"),
        pytest.param('{spec: number, prefix: "EUR "}', id="preset-with-affix"),
    ],
)
def test_style_formats_alias_may_target_a_preset(alias_target: str) -> None:
    """An alias can name a preset, so a named house format keeps house rules."""
    result = compile_board(_board_with_alias(alias_target, "number_format"))
    assert result.success, [e.message for e in result.errors]


def test_alias_to_a_native_preset_is_rejected_in_a_vega_slot() -> None:
    """The alias is judged as if its preset were written in the slot."""
    result = compile_board(_board_with_alias("percent_number", "number_format"))
    assert [e.code for e in result.errors] == ["ERR-FORMAT-NATIVE-IN-VEGA-SLOT"]


def test_alias_to_a_number_preset_is_rejected_on_time_format() -> None:
    result = compile_board(_board_with_alias("currency", "time_format"))
    assert [e.code for e in result.errors] == ["ERR-FORMAT-KIND-MISMATCH"]


# Boards used to pin native-in-Vega rejection for every _VEGA_PAINTED_PARENTS entry.
# Each board puts "percent_number" in a different Vega-painted slot.  The test
# parametrizes over (description, yaml_str) so that a future _VEGA_PAINTED_PARENTS
# gap produces an explicit failure naming the missed slot.
_NATIVE_IN_VEGA_CASES: list[tuple[str, str]] = [
    (
        "axis.labels.format",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis:
        labels:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "axis_x.labels.format",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "axis_y.labels.format",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        labels:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "axis_quantitative.labels.format",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_quantitative:
        labels:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "axis_band.labels.format",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_band:
        labels:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "marks.bar.labels.format (via _VEGA_PAINTED_PARENTS[labels])",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      marks:
        bar:
          labels:
            format: percent_number
rows:
  - revenue
""",
    ),
    (
        "marks.bar.total_label.format (via _VEGA_PAINTED_PARENTS[total_label])",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      marks:
        bar:
          total_label:
            format: percent_number
rows:
  - revenue
""",
    ),
    (
        "donut total.format (via _VEGA_PAINTED_PARENTS[total])",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'a' AS k, 1 AS v
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      format: percent_number
rows:
  - share
""",
    ),
    (
        "axis_y.mirror.format (via _VEGA_PAINTED_PARENTS[mirror])",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        mirror:
          format: percent_number
rows:
  - revenue
""",
    ),
    (
        "tooltip.format (via _VEGA_PAINTED_PARENTS[tooltip])",
        """
title: T
style:
  charts:
    tooltip:
      format: percent_number
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
""",
    ),
    (
        "number_format (always Vega-painted regardless of parent)",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: percent_number
rows:
  - revenue
""",
    ),
    (
        "time_format (always Vega-painted regardless of parent)",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      time_format: percent_number
rows:
  - revenue
""",
    ),
    (
        "support_table.format (via _VEGA_PAINTED_PARENTS[support_table])",
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    support_table:
      - source: revenue
        format: percent_number
rows:
  - revenue
""",
    ),
]


@pytest.mark.parametrize(
    ("slot", "board_yaml"),
    _NATIVE_IN_VEGA_CASES,
    ids=[s for s, _ in _NATIVE_IN_VEGA_CASES],
)
def test_native_formatter_rejected_on_vega_painted_slot(
    slot: str, board_yaml: str
) -> None:
    """ERR-FORMAT-NATIVE-IN-VEGA-SLOT is raised for every _VEGA_PAINTED_PARENTS entry.

    Covers every field name in the allowlist so a future gap (a new Vega-painted
    slot added to the model without a corresponding _VEGA_PAINTED_PARENTS entry)
    is caught by CI rather than surfacing as a Vega runtime crash.
    """
    result = compile_board(board_yaml)
    assert not result.success, (
        f"percent_number on {slot} must fail compile with ERR-FORMAT-NATIVE-IN-VEGA-SLOT"
    )
    assert result.errors[0].code == "ERR-FORMAT-NATIVE-IN-VEGA-SLOT", (
        f"Expected ERR-FORMAT-NATIVE-IN-VEGA-SLOT on {slot}, got {result.errors[0].code}"
    )


def test_native_formatter_rejected_on_donut_total_format_regression() -> None:
    """Regression: percent_number on donut total.format must fail compile.

    ChartTotal.format is a Vega text-mark encoding (pie.py emits it directly
    into vl-convert). This was NOT in _VEGA_PAINTED_PARENTS initially and
    passed compile clean, then crashed Vega with 'invalid format: percent_number'.
    """
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 'a' AS k, 1 AS v
charts:
  share:
    query: q
    type: donut
    theta: v
    color: k
    total:
      format: percent_number
rows:
  - share
"""
    result = compile_board(board)
    assert not result.success, "percent_number on donut total.format must fail compile"
    assert result.errors[0].code == "ERR-FORMAT-NATIVE-IN-VEGA-SLOT"


# ── Format kind: a number alias in a time slot (and vice versa) ──────────────
# `time_format` feeds a temporal axis and `number_format` a quantitative one.
# Both are `<Alias> | str`, so the `str` arm swallows every name Pydantic would
# otherwise reject — the kind check is the only thing standing between
# `time_format: currency` and a chart that renders wrong and looks right.


def _board_with_time_format(format_value: str, *, formats: str = "") -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
style:
{formats or "  background: white"}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      time_format: {format_value}
rows:
  - revenue
"""


def test_number_alias_on_time_format_fails_compile() -> None:
    """time_format: currency resolves to "$,.2f" — a d3 *number* spec.

    Baked onto a temporal axis that spec produces garbage tick labels, which is
    a wrong render that looks right until someone reads the axis.
    """
    result = compile_board(_board_with_time_format("currency"))

    assert not result.success, "currency is a number format — illegal in time_format"
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-KIND-MISMATCH"
    assert "currency" in error.message
    assert "time_format" in error.message
    assert "date_short" in error.message, "the legal names must be named"


def test_a_typo_in_a_time_slot_is_never_pointed_at_a_number_alias() -> None:
    """`currencyy` in a time slot must not be answered with "did you mean currency?".

    The unknown-spec hint pool is the whole vocabulary by default, so the
    nearest match to a mistyped number name is the number name — and taking that
    suggestion just trades ERR-FORMAT-INVALID for ERR-FORMAT-KIND-MISMATCH. The
    pool is scoped to the slot's own half so the hint is always followable.
    """
    result = compile_board(_board_with_time_format("currencyy"))

    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "currency" not in (error.hint or ""), (
        "the hint points at a value the next compile rejects"
    )


def test_a_retired_format_name_is_told_its_successor_at_the_compile_boundary() -> None:
    """The hint is the entire migration path for the format-name flip.

    A value rename cannot be a schema migration — `value_map` is total over its
    field's domain and `format:` is an open string — so an author porting a
    board off `currency_compact` gets one chance, on this error. A fuzzy match
    is actively wrong here: the nearest string is `currency_whole`, which
    compiles clean and silently drops both compaction and cents.

    Asserted at the boundary, not on the helper: the unit tests pin what
    `suggest_close_format` returns; this pins that it reaches `error.hint`.
    """
    for retired, successor in (
        ("currency_compact", "currency"),
        ("compact", "number"),
        ("number_default", "number"),
    ):
        result = compile_board(
            f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: {retired}
"""
        )
        assert not result.success, f"{retired} must fail compile"
        error = result.errors[0]
        assert error.code == "ERR-FORMAT-INVALID"
        assert error.hint == f"{retired!r} was renamed to {successor!r}.", (
            f"{retired} got {error.hint!r}"
        )


def test_the_kind_error_names_the_escape_hatch_its_slot_actually_has() -> None:
    """A time slot's way out is a strftime spec, not "a raw d3 spec".

    Generic advice to "use a raw d3 spec" is wrong on exactly this error: a d3
    number spec baked onto a temporal axis is the defect being rejected.
    """
    message = compile_board(_board_with_time_format("currency")).errors[0].message
    assert "%b %Y" in message
    assert "d3" not in message, "a d3 number spec is what this slot must not take"

    number_message = compile_board(_board_with_format("date_short")).errors[0].message
    assert ",.0f" in number_message


def test_time_alias_on_time_format_compiles_clean() -> None:
    """date_short is the engine's temporal alias — the one name that belongs."""
    result = compile_board(_board_with_time_format("date_short"))
    assert result.success, f"Compile failed: {result.errors}"


def test_strftime_on_time_format_compiles_clean() -> None:
    """A raw d3 time spec is the other honest value for a time slot."""
    result = compile_board(_board_with_time_format('"%b %Y"'))
    assert result.success, f"Compile failed: {result.errors}"


def test_board_alias_on_time_format_compiles_clean() -> None:
    """`style.formats` keys are user-defined and carry no kind — legal on both.

    The engine cannot know whether `fiscal` targets a time or a number spec, so
    narrowing the engine's own vocabulary must not narrow the board's.
    """
    result = compile_board(
        _board_with_time_format("fiscal", formats='  formats:\n    fiscal: "%b \'%y"\n')
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_time_alias_on_number_format_fails_compile() -> None:
    """The narrowing runs both ways: date_short is not a number format."""
    result = compile_board(_board_with_format("date_short"))

    assert not result.success, "date_short is a time format — illegal in number_format"
    assert result.errors[0].code == "ERR-FORMAT-KIND-MISMATCH"
    assert "number_format" in result.errors[0].message


# ── Date-format slot messaging: no directive, and unsupported directives ────


def test_date_format_with_no_directive_names_the_slot_a_date_format() -> None:
    """`MMM YYYY` has no `%` directive -- it fails the d3 number-spec parse too,
    but the slot (time_format) is a definitively known date/time slot, so the
    message must not call it a "number format"."""
    result = compile_board(_board_with_time_format('"MMM YYYY"'))

    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "date" in error.message.lower()
    assert "number format" not in error.message.lower()
    assert "%b %Y" in error.message, "should suggest a d3 time pattern"
    assert "d3-format spec" not in error.message, (
        "a date slot's typo isn't a number-spec parse failure"
    )


def test_iso_date_pattern_with_no_directive_also_names_a_date_format() -> None:
    """`YYYY-MM-DD` is the other common no-directive pattern authors type."""
    result = compile_board(_board_with_time_format('"YYYY-MM-DD"'))

    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "date" in error.message.lower()
    assert "number format" not in error.message.lower()


def test_unsupported_strftime_directive_fails_compile_not_at_render() -> None:
    """%K has a directive but neither d3-time-format nor the engine's Python
    painter implements it. This must be a compile-time validation error
    (ERR-FORMAT-INVALID) that lists the accepted directives, never
    ERR-INTERNAL -- the fallback code for an unclassified crash.
    """
    result = compile_board(_board_with_time_format('"%K"'))

    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-FORMAT-INVALID"
    assert "%K" in error.message
    assert "%b" in error.message, "the accepted directives should be listed"


def test_unsupported_directive_on_axis_format_also_fails_compile() -> None:
    """The same directive check applies wherever a raw strftime spec is legal."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format: "%K"
rows:
  - revenue
"""
    result = compile_board(board)
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-INVALID"


def test_d3_only_millisecond_directive_compiles_clean_on_time_format() -> None:
    """%L (milliseconds) is d3-time-format vocabulary Vega paints directly,
    even though Python's portable_strftime has no computer for it -- the
    compile-time check accepts either engine's directive set.
    """
    result = compile_board(_board_with_time_format('"%H:%M:%S.%L"'))
    assert result.success, f"Compile failed: {result.errors}"


def test_d3_only_millisecond_directive_compiles_clean_in_style_formats_alias() -> None:
    """The same d3-only directive is legal through a `style.formats` alias."""
    result = compile_board(
        _board_with_time_format("ms", formats='  formats:\n    ms: "%H:%M:%S.%L"\n')
    )
    assert result.success, f"Compile failed: {result.errors}"


def test_time_alias_on_a_kind_agnostic_format_slot_compiles_clean() -> None:
    """An axis label format is either kind — its column decides, not the field.

    Only `number_format`/`time_format` name their kind in the field itself; a
    plain `format:` slot must keep the whole vocabulary.
    """
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      axis_x:
        labels:
          format: date_short
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_alias_spec_less_affix_falls_back_through_bare_alias_spelling() -> None:
    """The affix fallback must follow the alias indirection."""
    board = """
title: T
style:
  formats:
    eur:
      prefix: "EUR "
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 1500}
      - {month: Feb, revenue: 3200}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: eur
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "EUR 1,000" in svg
    assert "EUR 3,000" in svg
    assert "1k" not in svg


def test_alias_sign_flag_compiles_clean_through_bare_alias_spelling() -> None:
    """The sign flag must compose correctly through the alias indirection on a Vega-
    painted slot too.
    """
    board = """
title: T
style:
  formats:
    eur:
      spec: "(,.0f"
      prefix: "€"
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: -100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: eur
rows:
  - revenue
"""
    result = compile_board(board)
    assert result.success, result.errors
    svg = render_board_to_svg(board)
    assert "€(100)" in svg


def test_alias_spec_less_affix_compiles_clean_through_bare_alias_spelling_on_a_kpi() -> (
    None
):
    """The alias-indirection fix must not flip vega_painted for a Python-painted slot."""
    result = compile_board(
        """
title: T
style:
  formats:
    eur:
      prefix: "EUR "
queries:
  q:
    source: db
    sql: SELECT 154500 AS revenue
charts:
  headline:
    query: q
    type: kpi
    value: revenue
    style:
      value:
        format: eur
rows:
  - headline
"""
    )
    assert result.success, result.errors


@pytest.mark.parametrize(
    "eur",
    [
        pytest.param('{prefix: "EUR "}', id="spec-less"),
        pytest.param('{spec: ",.0f", prefix: "EUR "}', id="number-spec"),
    ],
)
def test_affixed_alias_on_time_format_rejected(eur: str) -> None:
    """An alias smuggles an affix onto style.time_format, which never paints one."""
    result = compile_board(
        f"""
title: T
style:
  formats:
    eur: {eur}
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::date AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: line
    x: month
    y: revenue
    style:
      time_format: eur
rows:
  - revenue
"""
    )
    assert [e.code for e in result.errors] == ["ERR-FORMAT-AFFIX-TIME-UNSUPPORTED"]


# ── Time directive vocabulary: only directives Vega paints identically ───────
# time_format is Vega-painted (baked onto axis.labels.format) — the x-axis
# overlap resolver also measures it with portable_strftime. A directive Vega
# renders differently than portable_strftime computes (or doesn't recognize
# at all) must fail compile, not silently mismeasure a live label.


def test_quarter_directive_on_time_format_compiles_clean() -> None:
    """%q is now a Vega-safe directive (quarter number)."""
    result = compile_board(_board_with_time_format('"%q"'))
    assert result.success, f"Compile failed: {result.errors}"


def test_milliseconds_and_epoch_directives_on_time_format_compile_clean() -> None:
    """%L (ms) and %Q (epoch ms) are now Vega-safe directives."""
    result = compile_board(_board_with_time_format('"%L %Q"'))
    assert result.success, f"Compile failed: {result.errors}"


def test_locale_offset_directive_on_time_format_fails_compile() -> None:
    """%Z is real d3 grammar, but portable_strftime renders it differently
    (empty on a naive datetime, a named zone rather than d3's "+0000"-style
    offset on an aware one) -- must fail compile rather than mismeasure.
    """
    result = compile_board(_board_with_time_format('"%Z"'))
    assert not result.success, "%Z diverges from Vega's paint -- must fail compile"
    assert result.errors[0].code == "ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED"
    assert "%Z" in result.errors[0].message


def test_locale_composite_directives_on_time_format_fail_compile() -> None:
    """%c/%x/%X are real d3 grammar (locale composites) portable_strftime
    renders via the host's C-locale strftime, not d3's en-US "%x, %X" style
    -- must fail compile rather than mismeasure.
    """
    for directive in ("%c", "%x", "%X"):
        result = compile_board(_board_with_time_format(f'"{directive}"'))
        assert not result.success, f"{directive} diverges from Vega -- must fail"
        assert result.errors[0].code == "ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED"
        assert directive in result.errors[0].message


def test_directive_d3_does_not_define_fails_compile() -> None:
    """%C is not d3-time-format grammar at all -- Vega paints the literal
    letter "C" while portable_strftime computes the century.
    """
    result = compile_board(_board_with_time_format('"%C"'))
    assert not result.success, "%C is not a d3 directive -- must fail compile"
    assert result.errors[0].code == "ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED"


def test_style_formats_alias_on_time_format_cannot_bypass_the_directive_gate() -> None:
    """A `style.formats` alias targeting a Vega-unsafe directive must fail
    compile the same as authoring the directive inline -- the alias is just
    another spelling of the same Vega-painted `time_format` slot, and
    `%C` is not d3-time-format grammar at all (Vega paints the literal
    letter while portable_strftime computes an unrelated value).
    """
    result = compile_board(
        _board_with_time_format("myfmt", formats='  formats:\n    myfmt: "%C"\n')
    )
    assert not result.success, "an alias must not smuggle %C past the directive gate"
    assert result.errors[0].code == "ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED"
    assert "%C" in result.errors[0].message


def test_style_formats_alias_on_a_non_vega_slot_is_unaffected_by_the_directive_gate() -> (
    None
):
    """The same alias is legitimate in a Python-painted slot (a table column)
    -- the directive gate is keyed to the *use* site being Vega-painted, not
    to the alias definition.
    """
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
style:
  formats:
    myfmt: "%C"
charts:
  detail:
    query: q
    type: table
    style:
      columns:
        month:
          format: myfmt
rows:
  - detail
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_table_column_format_is_not_narrowed_by_the_vega_directive_check() -> None:
    """style.columns.<col>.format renders in Python, not Vega -- no measure-vs-
    paint divergence is possible there, so the Vega-only directive vocabulary
    must not narrow it. %C (century) is real, CRT-portable Python output with
    no Vega equivalent at all.
    """
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT '2024-01-01'::DATE AS month, 100 AS revenue
charts:
  detail:
    query: q
    type: table
    style:
      columns:
        month:
          format: "%C"
rows:
  - detail
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"
