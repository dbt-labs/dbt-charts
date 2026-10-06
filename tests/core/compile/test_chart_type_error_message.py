import pytest

from dbt_charts.core.compile.compiler import compile


def _compile_chart_type(chart_type: str) -> str:
    result = compile(
        f"""\
charts:
  c:
    type: {chart_type}
    x: month
    y: revenue
rows:
  - c
"""
    )
    assert not result.success
    assert len(result.errors) == 1
    return result.errors[0].message


def test_unknown_chart_type_error_names_supported_authored_types() -> None:
    result = compile(
        """\
charts:
  c28_boxplot:
    type: boxplot
    x: orbit_family
    y: mass_kg
rows:
  - c28_boxplot
"""
    )

    assert not result.success
    assert len(result.errors) == 1

    error = result.errors[0]
    assert "Unknown chart type 'boxplot'" in error.message
    assert "Supported chart types:" in error.message
    assert "bar" in error.message
    assert "spark_bar" in error.message
    assert "boxplot" not in error.message.split("Supported chart types:", 1)[1]
    assert "_discriminate_authored_chart" not in error.message


def test_unknown_chart_type_error_suggests_close_authored_type() -> None:
    result = compile(
        """\
charts:
  typo:
    type: barr
    x: month
    y: revenue
rows:
  - typo
"""
    )

    assert not result.success
    assert len(result.errors) == 1

    error = result.errors[0]
    assert "Unknown chart type 'barr'" in error.message
    assert "Did you mean 'bar'?" in error.message
    assert "_discriminate_authored_chart" not in error.message


def test_missing_chart_type_error_names_required_type_field() -> None:
    result = compile(
        """\
charts:
  missing_type:
    x: month
    y: revenue
rows:
  - missing_type
"""
    )

    assert not result.success
    assert len(result.errors) == 1

    error = result.errors[0]
    assert "Missing required field 'type'" in error.message
    assert "Supported chart types:" in error.message
    assert error.hint is None
    assert "_discriminate_authored_chart" not in error.message


def test_composable_shape_error_names_the_recipe_instead_of_only_the_tag_list() -> None:
    """The reported bug: 'streamgraph' read as "dbt charts cannot draw one"."""
    message = _compile_chart_type("streamgraph")

    assert "Unknown chart type 'streamgraph'" in message
    assert "style.stack: center" in message
    assert "type: area" in message
    # A recipe is an answer, not a near-miss guess.
    assert "Did you mean" not in message


@pytest.mark.parametrize(
    "authored",
    ["streamgraph", "Streamgraph", "STREAMGRAPH", "stream-graph", "stream graph"],
)
def test_composable_shape_recipe_survives_the_spellings_authors_reach_for(
    authored: str,
) -> None:
    assert "style.stack: center" in _compile_chart_type(authored)


def test_unsupported_shape_error_says_so_rather_than_guessing_a_near_miss() -> None:
    """An honest negative is the half an alias can never deliver."""
    message = _compile_chart_type("sankey")

    assert "Unknown chart type 'sankey'" in message
    assert "cannot draw" in message
    # The guessing that produced RJ's broken lollipop starts with a bad suggestion.
    assert "Did you mean" not in message


def test_spider_error_says_so_rather_than_suggesting_pie() -> None:
    """Regression: `spider` fell through both lookup tables into the fuzzy
    matcher and landed on 'pie' — telling the author to draw the wrong chart."""
    message = _compile_chart_type("spider")

    assert "Unknown chart type 'spider'" in message
    assert "cannot draw" in message
    assert "Did you mean" not in message


@pytest.mark.parametrize("noun", ["alluvial", "word cloud", "wordcloud", "network"])
def test_more_unsupported_shapes_say_so_rather_than_guessing(noun: str) -> None:
    message = _compile_chart_type(noun)

    assert "cannot draw" in message
    assert "Did you mean" not in message


@pytest.mark.parametrize(
    ("noun", "recipe_fragment"),
    [
        ("gantt", "date y_start"),
        ("candlestick", "y_start: low"),
        ("ohlc", "y_start: low"),
        ("waterfall", "y_start from a running total"),
        ("dumbbell", "y_start for the far end"),
        ("ranged dot", "y_start for the low end"),
        ("floating bar", "type: bar with y_start"),
    ],
)
def test_now_supported_shapes_name_the_recipe_instead_of_cannot_draw(
    noun: str, recipe_fragment: str
) -> None:
    """A shape drawn with `type: bar` and `y_start` gets its recipe, not a
    "cannot draw"."""
    message = _compile_chart_type(noun)

    assert "cannot draw" not in message
    assert recipe_fragment in message


def test_unsupported_shape_error_still_lists_the_supported_types() -> None:
    message = _compile_chart_type("treemap")

    assert "cannot draw" in message
    assert "Supported chart types:" in message
    assert "bar" in message


def test_typo_still_gets_a_did_you_mean_suggestion() -> None:
    """The hint map must not swallow the fuzzy path it sits in front of."""
    message = _compile_chart_type("barr")

    assert "Did you mean 'bar'?" in message
    assert "cannot draw" not in message
