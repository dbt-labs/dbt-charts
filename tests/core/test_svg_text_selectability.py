"""SVG chart text selectability — cursor and ::selection CSS rules.

All chart text must show cursor:text so users know it is selectable. Interaction,
so it ships in the host stylesheet beside the runtime, never inside a board.
"""

from dbt_charts.core.render.controls import controls_stylesheet


def test_chart_text_cursor_is_text() -> None:
    styles = controls_stylesheet()
    assert ".dbt-chart text" in styles
    assert "cursor: text" in styles
    # pointer-events: auto overrides the inherited none from Vega's <g pointer-events="none">
    assert "pointer-events: auto" in styles


def test_chart_text_selection_highlight_present() -> None:
    styles = controls_stylesheet()
    assert ".dbt-chart text::selection" in styles
    assert "background:" in styles


def test_clickable_chart_text_shows_the_pointer_over_the_text_cursor() -> None:
    # `.dbt-chart text` sets cursor: text on the text element itself, so a pointer set on the
    # chart never reaches it; an armed item, the text inside an armed legend entry, and the
    # clear control each need their own rule.
    styles = controls_stylesheet()
    for selector in (
        ".dbt-chart text[data-dbt-armed]",
        ".dbt-chart [data-dbt-armed] text",
        ".dbt-chart text[data-dbt-clear-var]",
    ):
        start = styles.index(selector)
        assert "cursor: pointer" in styles[start : styles.index("}", start)], selector
