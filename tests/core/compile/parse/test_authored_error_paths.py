"""Error paths name the YAML the author wrote, never the chart-type union tag."""

from dbt_charts.core.compile import compile

_QUERIES = """title: T
queries:
  q:
    columns: [seg, v]
    values: [[a, 1]]
"""


def _first_error(board: str):
    result = compile(_QUERIES + board)
    assert not result.success
    return result.errors[0]


def test_literal_color_path_has_no_type_tag() -> None:
    error = _first_error(
        """charts:
  c:
    query: q
    type: bar
    x: seg
    y: v
    color: '#ff0000'
rows: [c]
"""
    )

    assert error.path == "charts.c.color"
    assert "c.bar" not in error.message


def test_unknown_style_key_names_the_authored_path_everywhere() -> None:
    error = _first_error(
        """charts:
  c:
    query: q
    type: kpi
    y: v
    style:
      format: x
rows: [c]
"""
    )

    assert error.path == "charts.c.style.format"
    assert (
        error.message
        == "Unknown field 'format' at charts.c.style. Full path: charts.c.style.format."
    )


def test_inline_layout_chart_path_has_no_type_tag() -> None:
    error = _first_error(
        """rows:
  - type: bar
    query: q
    x: seg
    y: v
    bogus: 1
"""
    )

    assert error.path == "rows.0.bogus"
    assert "rows.0.bar" not in error.message


def test_a_chart_id_named_like_a_type_keeps_its_id() -> None:
    error = _first_error(
        """charts:
  bar:
    query: q
    type: bar
    x: seg
    y: v
    color: '#ff0000'
rows: [bar]
"""
    )

    assert error.path == "charts.bar.color"


def test_a_layout_item_keyed_by_a_chart_id_named_like_a_type_keeps_its_id() -> None:
    error = _first_error(
        """charts:
  bar:
    query: q
    type: bar
    x: seg
    y: v
rows:
  - bar:
      bogus: 1
"""
    )

    assert error.path == "rows.0.bar"
    assert "at rows.0." in error.message


def test_bar_label_position_error_names_the_authored_path() -> None:
    error = _first_error(
        """charts:
  c:
    query: q
    type: bar
    x: seg
    y: v
    style:
      marks:
        bar:
          labels:
            position: right
rows: [c]
"""
    )

    assert "Field 'charts.c.style.marks.bar.labels.position'" in error.message
    assert "c.bar.style" not in error.message


def test_a_literal_color_fix_ends_in_yaml_that_can_be_copied() -> None:
    error = _first_error(
        """charts:
  c:
    query: q
    type: bar
    x: seg
    y: v
    color: '#ff0000'
rows: [c]
"""
    )

    assert error.message.splitlines()[-1].strip() == "static: '#ff0000'"
