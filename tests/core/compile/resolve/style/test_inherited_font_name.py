"""Which named font a slot reads, and whether it is tweaked there."""

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import merge_style
from dbt_charts.core.compile.resolve.style.fonts import inherited_font_name


def test_a_slot_with_no_name_reads_its_parents_and_is_tweaked_by_its_own_fields() -> (
    None
):
    theme = get_theme_style(None)
    parent = theme.variables.font.extends
    assert parent
    name, tweaked = inherited_font_name(theme, None, "variables.label.font")
    assert name == parent
    assert tweaked is any(
        v is not None
        for k, v in theme.variables.label.font.model_dump().items()
        if k != "extends"
    )


def test_a_slot_the_theme_binds_directly_reads_that_name() -> None:
    theme = get_theme_style(None)
    assert inherited_font_name(theme, None, "title.font")[0] == theme.title.font.extends


def test_a_charts_level_tweak_counts_beside_the_root_layers_own_fields() -> None:
    theme = get_theme_style(None)
    merged, _ = merge_style(
        theme,
        StylePatch.model_validate({"charts": {"title": {"font": {"weight": 700}}}}),
    )
    name, tweaked = inherited_font_name(merged, "bar", "title.font")
    assert name == theme.title.font.extends
    assert tweaked


def test_a_chain_naming_no_font_is_empty() -> None:
    assert inherited_font_name(
        get_theme_style(None), "bar", "marks.bar.labels.font"
    ) == (
        "",
        False,
    )
