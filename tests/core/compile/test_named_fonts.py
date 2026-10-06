"""style.fonts registry and the name / extends forms on every font slot."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.primitives import (
    FontStyle,
    NamedFont,
    ResolvedFontStyle,
)
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.theme import RootFontStyle
from dbt_charts.core.compile.resolve.style.board import (
    apply_inherit,
    get_inherit_graph,
    resolve_style_and_context,
)

KNOWN = "body, caption, code, heading, label, numeral, subtitle"


def _pre_style(*patches: dict[str, Any]):
    return resolve_style_and_context(
        get_theme_style(), *(StylePatch.model_validate(p) for p in patches)
    )[1].pre_style


def test_theme_ships_the_seven_named_fonts() -> None:
    fonts = get_theme_style().fonts
    assert fonts is not None
    assert sorted(fonts) == sorted(KNOWN.split(", "))
    assert fonts["heading"].sizes == [24, 18, 14, 14, 11, 11]


def test_resolved_font_has_no_extends() -> None:
    assert "extends" not in ResolvedFontStyle.model_fields


def test_bare_string_slot_is_a_registry_lookup() -> None:
    style = _pre_style(
        {
            "fonts": {"label": {"size": 9, "weight": 700}},
            "charts": {"axis": {"labels": {"font": "label"}}},
        }
    )
    font = style.charts.axis.labels.font
    assert (font.size, font.weight, font.extends) == (9, 700, None)


def test_extends_with_overrides_wins() -> None:
    style = _pre_style(
        {
            "fonts": {"label": {"size": 9, "weight": 700}},
            "charts": {"axis": {"labels": {"font": {"extends": "label", "size": 12}}}},
        }
    )
    font = style.charts.axis.labels.font
    assert (font.size, font.weight) == (12, 700)


def test_named_font_sets_only_its_declared_fields() -> None:
    style = _pre_style(
        {
            "fonts": {"label": {"size": 9}},
            "charts": {"axis": {"labels": {"font": "label"}}},
        }
    )
    assert style.charts.axis.labels.font.weight is None


def test_board_redefining_a_font_reaches_theme_bound_slots() -> None:
    theme_bound = get_theme_style().charts.axis.labels.font.extends
    assert theme_bound is not None
    style = _pre_style({"fonts": {theme_bound: {"size": 31}}})
    assert style.charts.axis.labels.font.size == 31


def test_sizes_is_never_copied_into_a_slot() -> None:
    style = _pre_style({"title": {"font": "heading"}})
    assert not hasattr(style.title.font, "sizes")


def test_null_removes_a_registry_entry() -> None:
    style = _pre_style({"fonts": {"mine": {"size": 9}}}, {"fonts": {"mine": None}})
    assert style.fonts is not None
    assert "mine" not in style.fonts


def test_unknown_name_names_the_slot_and_known_fonts() -> None:
    with pytest.raises(CompilationError) as err:
        _pre_style({"charts": {"axis": {"labels": {"font": "nope"}}}})
    assert (
        "Unknown font 'nope' at style.charts.axis.labels.font. "
        f"Known fonts: {KNOWN}." in str(err.value)
    )


def test_registry_entry_extends_another_entry() -> None:
    style = _pre_style(
        {
            "fonts": {"h2": {"extends": "heading", "size": 20}},
            "title": {"font": "h2"},
        }
    )
    font = style.title.font
    assert font.size == 20
    assert font.weight == get_theme_style().fonts["heading"].weight
    assert style.fonts is not None
    assert style.fonts["h2"].extends is None
    assert style.fonts["h2"].sizes is None


def test_registry_entry_takes_a_bare_alias() -> None:
    style = _pre_style(
        {
            "fonts": {"label": {"size": 9}, "tag": "label"},
            "charts": {"axis": {"labels": {"font": "tag"}}},
        }
    )
    assert style.charts.axis.labels.font.size == 9


def test_registry_chains_resolve_with_own_fields_winning() -> None:
    style = _pre_style(
        {
            "fonts": {
                "a": {"size": 9, "weight": 700, "case": "upper"},
                "b": {"extends": "a", "size": 10},
                "c": {"extends": "b", "weight": 300},
            },
            "title": {"font": "c"},
        }
    )
    font = style.title.font
    assert (font.size, font.weight, font.case) == (10, 300, "upper")


def test_registry_entry_does_not_inherit_sizes() -> None:
    style = _pre_style({"fonts": {"h2": "heading"}})
    assert style.fonts is not None
    assert style.fonts["h2"].sizes is None
    assert style.fonts["heading"].sizes == [24, 18, 14, 14, 11, 11]


def test_registry_cycle_names_the_path() -> None:
    with pytest.raises(CompilationError, match="a -> b -> c -> a") as err:
        _pre_style({"fonts": {"a": "b", "b": "c", "c": {"extends": "a", "size": 9}}})
    assert err.value.code.code == "ERR-FONT-REGISTRY"


def test_registry_self_extends_is_a_cycle() -> None:
    with pytest.raises(CompilationError, match="a -> a"):
        _pre_style({"fonts": {"a": {"extends": "a"}}})


def test_registry_unknown_parent_names_entry_and_known_fonts() -> None:
    with pytest.raises(CompilationError) as err:
        _pre_style({"fonts": {"h2": {"extends": "nope", "size": 9}}})
    assert (
        "Unknown font 'nope' at style.fonts.h2. "
        "Known fonts: body, caption, code, h2, heading, label, numeral, subtitle."
        in str(err.value)
    )


def test_registry_entry_null_extends_has_no_parent() -> None:
    style = _pre_style({"fonts": {"mine": {"extends": None, "size": 9}}})
    assert style.fonts is not None
    assert style.fonts["mine"].size == 9


def test_registry_entry_cannot_inherit_a_size_into_heading() -> None:
    with pytest.raises(CompilationError, match="'heading' font takes sizes"):
        _pre_style({"fonts": {"heading": {"extends": "body"}}})


def test_upper_layer_extends_replaces_the_lower_registry_entry() -> None:
    style = _pre_style(
        {"fonts": {"x": {"size": 9, "weight": 700}}},
        {"fonts": {"x": {"extends": "label", "case": "lower"}}},
    )
    assert style.fonts is not None
    x = style.fonts["x"]
    label = get_theme_style().fonts["label"]
    assert (x.size, x.weight, x.case) == (label.size, label.weight, "lower")


def test_upper_layer_patch_without_extends_keeps_the_lower_extends() -> None:
    style = _pre_style(
        {"fonts": {"x": {"extends": "label", "case": "lower"}}},
        {"fonts": {"x": {"size": 40}}},
    )
    assert style.fonts is not None
    x = style.fonts["x"]
    assert (x.size, x.case, x.weight) == (
        40,
        "lower",
        get_theme_style().fonts["label"].weight,
    )


def test_only_heading_takes_sizes() -> None:
    with pytest.raises(ValidationError, match="Only the 'heading' font takes sizes"):
        merge_onto_base(
            get_theme_style(),
            StylePatch.model_validate({"fonts": {"label": {"sizes": [1] * 6}}}),
        )


def test_heading_rejects_size() -> None:
    with pytest.raises(
        ValidationError, match="The 'heading' font takes sizes \\(H1–H6\\), not size"
    ):
        merge_onto_base(
            get_theme_style(),
            StylePatch.model_validate({"fonts": {"heading": {"size": 20}}}),
        )


def test_heading_sizes_is_required() -> None:
    with pytest.raises(ValidationError, match="style.fonts.heading.sizes is required"):
        merge_onto_base(
            get_theme_style(),
            StylePatch.model_validate({"fonts": {"heading": None}}),
        )


def test_font_style_coerces_a_bare_string() -> None:
    assert FontStyle.model_validate("label").extends == "label"


_CHART_BOARD = """
style:
  fonts:
    mine:
      size: 23
      family: Zork
      color: "#ff0000"
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    style:
      axis:
        labels:
          font: {font}
rows:
  - c1
"""


def test_chart_local_font_name_reaches_the_rendered_chart() -> None:
    from .._svg_render import render_board_to_svg

    svg = render_board_to_svg(_CHART_BOARD.format(font="mine"))
    assert "Zork" in svg
    assert "#ff0000" in svg


def test_chart_local_unknown_font_name_fails_the_chart(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from .._svg_render import render_board_to_svg

    render_board_to_svg(_CHART_BOARD.format(font="nope"))
    assert "Unknown font 'nope' at charts.c1.style.axis.labels.font" in caplog.text


def _theme_bound_font_slots() -> list[tuple[str, dict[str, Any]]]:
    """``font: mine`` at board and ``style.charts`` slots a theme binds with its
    own color."""
    return [
        ("title.subtitle.font", {"title": {"subtitle": {"font": "mine"}}}),
        ("variables.font", {"variables": {"font": "mine"}}),
        (
            "charts.axis.labels.font",
            {"charts": {"axis": {"labels": {"font": "mine"}}}},
        ),
    ]


@pytest.mark.parametrize(
    ("slot", "patch"),
    _theme_bound_font_slots(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_board_named_font_beats_the_theme_slot_fields(
    slot: str, patch: dict[str, Any]
) -> None:
    theme_slot = get_theme_style()
    for part in slot.split("."):
        theme_slot = getattr(theme_slot, part)
    assert theme_slot.extends is not None
    assert theme_slot.color is not None
    style = _pre_style({"fonts": {"mine": {"color": "#ff0000"}}}, patch)
    resolved = style
    for part in slot.split("."):
        resolved = getattr(resolved, part)
    assert resolved.color == "#ff0000"


def test_extends_patch_replaces_the_base_slot_fields() -> None:
    base = FontStyle(extends="label", color="#111111", size=9)
    merged = merge_onto_base(base, FontStyle(extends="mine", weight=700))
    assert merged.model_dump(exclude_unset=True) == {"extends": "mine", "weight": 700}


def test_patch_without_extends_keeps_the_base_extends() -> None:
    base = FontStyle(extends="label", color="#111111")
    merged = merge_onto_base(base, FontStyle(size=20))
    assert merged.model_dump(exclude_unset=True) == {
        "extends": "label",
        "color": "#111111",
        "size": 20,
    }


def test_board_tweak_of_a_theme_bound_slot_survives() -> None:
    style = _pre_style(
        {"fonts": {"label": {"case": "upper", "size": 9}}},
        {"charts": {"axis": {"labels": {"font": {"size": 20}}}}},
    )
    font = style.charts.axis.labels.font
    assert (font.size, font.case) == (20, "upper")


def test_null_extends_unbinds_the_name_and_keeps_the_slot_fields() -> None:
    base = FontStyle(extends="label", color="#111111")
    merged = merge_onto_base(base, FontStyle(extends=None, weight=700))
    assert (merged.extends, merged.color, merged.weight) == (None, "#111111", 700)


def test_root_font_rejects_null_extends() -> None:
    with pytest.raises(ValidationError, match="style.font is the root font"):
        StylePatch.model_validate({"font": {"extends": None}})


def test_root_font_rejects_a_bare_name() -> None:
    with pytest.raises(ValidationError, match="style.font is the root font"):
        StylePatch.model_validate({"font": "body"})


def test_root_font_rejects_extends() -> None:
    with pytest.raises(ValidationError, match="style.font is the root font"):
        StylePatch.model_validate({"font": {"extends": "body"}})
    with pytest.raises(ValidationError, match="style.font is the root font"):
        RootFontStyle.model_validate({"extends": "body", "emoji": "disabled"})


def _shipped_schema() -> dict[str, Any]:
    from dbt_charts.core.compile.schema.introspection import introspect
    from dbt_charts.core.compile.schema.renderers.vscode_schema import (
        render_vscode_schema,
    )

    return render_vscode_schema(introspect())


def _definition(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return schema["definitions"][name]


def test_shipped_schema_font_slot_accepts_a_name() -> None:
    schema = _shipped_schema()
    arms = _definition(schema, "TitleStylePatch")["properties"]["font"]["anyOf"]
    assert {"type": "string"} in arms


def test_shipped_schema_registry_entries_take_extends_and_a_name() -> None:
    schema = _shipped_schema()
    assert "extends" in _definition(schema, "NamedFont")["properties"]
    registry = _definition(schema, "StylePatch")["properties"]["fonts"]
    assert {"type": "string"} in registry["anyOf"][0]["additionalProperties"]["anyOf"]


def test_font_alias_is_described_as_a_font_name() -> None:
    schema = _shipped_schema()
    extends = _definition(schema, "NamedFont")["properties"]["extends"]
    assert "style.fonts" in json.dumps(extends)


def test_shipped_schema_root_font_takes_no_name() -> None:
    schema = _shipped_schema()
    slot = _definition(schema, "StylePatch")["properties"]["font"]
    assert {"type": "string"} not in slot["anyOf"]
    assert "extends" not in _definition(schema, "RootFontStylePatch")["properties"]


def test_synthesized_font_patch_accepts_a_bare_name() -> None:
    style = _pre_style(
        {"fonts": {"mine": {"size": 23}}, "charts": {"series_label": {"font": "mine"}}}
    )
    assert style.charts.series_label.font.size == 23


def test_extends_replaces_a_subclass_slot_and_keeps_its_required_fields() -> None:
    style = _pre_style(
        {"fonts": {"mine": {"size": 23}}, "charts": {"series_label": {"font": "mine"}}}
    )
    font = style.charts.series_label.font
    assert font.size == 23
    assert font.compact_size == get_theme_style().charts.series_label.font.compact_size


def test_fragment_slot_fields_lose_to_a_later_named_font() -> None:
    from dbt_charts.core.compile.merge import merge_patches

    fragment = StylePatch.model_validate(
        {"charts": {"axis": {"labels": {"font": {"color": "#00ff00"}}}}}
    )
    board = StylePatch.model_validate(
        {
            "fonts": {"mine": {"color": "#ff0000"}},
            "charts": {"axis": {"labels": {"font": "mine"}}},
        }
    )
    style = _pre_style(
        merge_patches(fragment, board, nested=False).model_dump(exclude_unset=True)
    )
    assert style.charts.axis.labels.font.color == "#ff0000"


def test_meta_fragment_slot_fields_lose_to_the_boards_named_font(
    tmp_path, local_project
) -> None:
    from dbt_charts.core.compile import compile_file
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/meta.yml").write_text(
        "style:\n  title:\n    subtitle:\n      font:\n        color: '#00ff00'\n"
    )
    (tmp_path / "charts/board.yaml").write_text(
        "style:\n  fonts:\n    mine:\n      color: '#ff0000'\n"
        "  title:\n    subtitle:\n      font: mine\n"
        "title: T\nrows:\n  - text: hi\n"
    )
    project = local_project(tmp_path)
    result = compile_file(
        project.path("charts/board.yaml").read_board(), apply_meta=True
    )
    reset_config()
    assert result.success, result.errors
    assert result.board.resolved_style.title.subtitle.font.color == "#ff0000"


def test_board_charts_style_named_font_reaches_a_rendered_chart() -> None:
    from .._svg_render import render_board_to_svg

    svg = render_board_to_svg(
        _CHART_BOARD.replace(
            "    style:\n      axis:\n        labels:\n          font: {font}\n", ""
        )
        .replace(
            "style:\n  fonts:",
            "style:\n  charts:\n    axis:\n      labels:\n        font: mine\n  fonts:",
        )
        .format(font="")
    )
    assert "Zork" in svg
    assert "#ff0000" in svg


_TABLE_BOARD = """
style:
  fonts:
    mine:
      color: "#ff0000"
queries:
  q1:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
charts:
  t:
    query: q1
    type: table
    columns: [month, revenue]
    style:
      columns:
        revenue:
          font: {column_font}
    conditional_formatting:
      revenue:
        when:
          - gt: 1
            font: {rule_font}
rows:
  - t
"""


def _expanded_table(column_font: str = "mine", rule_font: str = "mine"):
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.resolve.style.fonts import expand_named_fonts

    result = compile(_TABLE_BOARD.format(column_font=column_font, rule_font=rule_font))
    assert result.success, [e.message for e in result.errors]
    return expand_named_fonts(
        result.board.charts["t"], {"mine": NamedFont(color="#ff0000")}, "charts.t"
    )


def test_table_column_font_name_expands() -> None:
    chart = _expanded_table()
    assert chart.style.columns["revenue"].font.color == "#ff0000"


def test_conditional_rule_font_name_expands() -> None:
    chart = _expanded_table()
    assert chart.conditional_formatting["revenue"].when[0].font.color == "#ff0000"


def test_unknown_font_in_a_table_column_raises() -> None:
    with pytest.raises(CompilationError, match="charts.t.style.columns.revenue.font"):
        _expanded_table(column_font="nope")


def test_unknown_font_in_a_conditional_rule_raises() -> None:
    with pytest.raises(CompilationError, match="when\\[0\\].font"):
        _expanded_table(rule_font="nope")


_LABEL_CHROME = [
    "charts.tooltip.label.font",
    "charts.tooltip.value.font",
    "charts.table.header.font",
    "variables.label.font",
    "variables.value.font",
    "variables.placeholder.font",
]


def _slot(node: Any, path: str) -> Any:
    for key in path.split("."):
        node = getattr(node, key)
    return node


@pytest.mark.parametrize("path", _LABEL_CHROME)
def test_label_size_reaches_chrome_slots(path: str) -> None:
    style = _pre_style({"fonts": {"label": {"size": 9}}})
    resolved = apply_inherit(style, get_inherit_graph())
    assert _slot(resolved, path).size == 9


def test_label_color_reaches_the_table_empty_state() -> None:
    style = _pre_style({"fonts": {"label": {"color": "#123456"}}})
    resolved = apply_inherit(style, get_inherit_graph())
    assert resolved.charts.table.empty_state.font.color == "#123456"


def test_variables_font_size_reaches_its_children() -> None:
    style = _pre_style({"variables": {"font": {"size": 15}}})
    resolved = apply_inherit(style, get_inherit_graph())
    assert resolved.variables.label.font.size == 15


def test_heading_extending_with_null_size_is_allowed() -> None:
    style = _pre_style({"fonts": {"heading": {"extends": "label", "size": None}}})
    assert style.fonts is not None
    assert style.fonts["heading"].size is None
