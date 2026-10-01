"""ERR-FORMAT-INVALID: unresolvable format specs are caught at compile().

A format string that is not a theme/board alias, not a native (non-d3)
formatter key, and not a valid d3-format spec must fail compile() instead of
passing validation and blowing up as ERR-INTERNAL deep inside rasterization.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.text.format_d3 import format_d3


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


def _assert_format_invalid(yaml: str, needle: str) -> None:
    result = compile_board(yaml)
    assert not result.success, f"{needle} must fail compile, got success"
    assert [e.code for e in result.errors] == ["ERR-FORMAT-INVALID"], result.errors
    assert needle in result.errors[0].message


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


def test_style_formats_target_predefined_name_raises() -> None:
    """A style.formats alias whose TARGET is a predefined name is a compile error.

    `formats: {mine: currency}` is not a valid format alias: "currency" is not a
    d3-format spec. At runtime, resolve_format("mine") returns the literal string
    "currency" and _d3_format("currency") raises in the browser. Catch it at compile.
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
    mine: currency
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
        "style.formats target that is a predefined name (not a d3 spec) must fail compile()"
    )
    assert result.errors[0].code == "ERR-FORMAT-INVALID"
    assert "currency" in result.errors[0].message


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
