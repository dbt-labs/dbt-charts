"""The per-path design target: what is editable on the object a click selects."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

# The rule tables are reached through the module rather than imported by name:
# `test_every_rule_table_names_a_field_that_still_exists` walks fifteen of them,
# and a fifteen-name private import block reads like an API this test is entitled
# to — the opposite of the message. They are `design.py`'s internals, and the
# aim is for most of them to stop existing.
from dbt_charts.agent_api import design as _design
from dbt_charts.agent_api.design import (
    DesignNode,
    DesignProperty,
    DesignTarget,
    build_design,
    build_design_target,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.authoring.yaml_patch import set_board_values
from dbt_charts.core.compile.errors import ParseError
from dbt_charts.core.compile.models.markers import Format
from dbt_charts.core.compile.models.schema_names import (
    FormatAlias,
    NumberFormatAlias,
    TimeFormatAlias,
)
from dbt_charts.core.compile.parse.parser import load_yaml_mapping, parse_yaml
from dbt_charts.core.compile.parse.source_map import build_source_index
from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from .._svg_normalize import normalize_same_run_svg

BOARD = """
title: Revenue Overview
style:
  background: "#fff"
charts:
  rev:
    type: line
    query: q
    x: month
    y: revenue
rows:
  - cols:
      - title: Monthly Revenue
        type: area
        query: q
        x: month
        y: revenue
      - label: Customers
        type: kpi
        query: q
        value: customers
"""


# Boards covering every authored layout plus the shapes past rounds broke on.
_PARSE_FIXTURES = {
    "rows-cols": (
        "title: T\n"
        "rows:\n"
        "  - cols:\n"
        "      - title: Revenue\n"
        "        type: area\n"
        "        query: q\n"
        "        x: month\n"
        "        y: revenue\n"
    ),
    "multi-metric": (
        "rows:\n"
        "  - title: Both\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: [revenue, cost]\n"
    ),
    "grid": (
        "grid:\n"
        "  columns: 2\n"
        "  items:\n"
        "    - item:\n"
        "        label: Left\n"
        "        type: kpi\n"
        "        query: q\n"
        "        value: v\n"
    ),
    "tabs": (
        "tabs:\n"
        "  items:\n"
        "    - title: One\n"
        "      rows:\n"
        "        - label: Inside\n"
        "          type: kpi\n"
        "          query: q\n"
        "          value: v\n"
    ),
    "multi-metric-bar": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: [revenue, cost]\n"
    ),
    # A bar chart may legally omit `x` while single-metric; every other fixture
    # with a `y` has one, so nothing exercised "make this multi-series on a
    # chart that has no x" until the list widget could produce that edit.
    "single-metric-bar-no-x": (
        "rows:\n  - title: Bar\n    type: bar\n    query: q\n    y: revenue\n"
    ),
    # The same model as the two above and none of their rules: `BarChart` backs
    # `Literal["bar", "histogram"]`, and the validator returns before any of the
    # multi-metric branches when the type is not `bar`. Every rule keyed by model
    # rather than by discriminator fires here wrongly, and nothing else in this
    # sweep is a chart whose class over-claims its own rules.
    "histogram-no-x": (
        "rows:\n  - title: H\n    type: histogram\n    query: q\n    y: revenue\n"
    ),
    "histogram-multi-metric": (
        "rows:\n"
        "  - title: H\n"
        "    type: histogram\n"
        "    query: q\n"
        "    y: [revenue, cost]\n"
        "    color: region\n"
    ),
    # A bar with a start takes one y and no stack; the panel must not offer
    # either edit that would break it.
    "bar-with-y-start": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    y_start: cost\n"
    ),
    # The mirror of the fixture above: a stacking bar takes no start, since a
    # stack computes every start itself.
    "bar-with-stack": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      stack: zero\n"
    ),
    "charts-map": (
        "charts:\n"
        "  k:\n"
        "    type: kpi\n"
        "    query: q\n"
        "    value: customers\n"
        "rows:\n"
        "  - charts.k\n"
    ),
    # The other half of every rule `reject_multi_series_channel_conflicts`
    # enforces. The suppression it already had runs the other way — hide the
    # second series source once `y` holds a list — so nothing exercised a chart
    # that authors one *first*, which is the shape the `list` widget turns into
    # a refusal.
    "bar-with-color": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: region\n"
    ),
    "bar-with-gradient-color": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "line-with-gradient-color": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "area-with-gradient-color": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "area-with-layers": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    layers:\n"
        "      - type: line\n"
        "        y: target\n"
    ),
    # The schema says `str | list[str]` and the model refuses any list but a
    # one-element one — the only family whose validator is stricter than its
    # own type.
    "spark-bar": (
        "rows:\n"
        "  - title: Spark\n"
        "    type: spark_bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # The pivot, whose three channels carry an arity rule between them that no
    # other family has. Both ends of it: the table that declares neither half of
    # the pair, and the one already pivoting on a single half.
    "table-plain": "rows:\n  - title: T\n    type: table\n    query: q\n",
    "table-pivot": (
        "rows:\n"
        "  - title: T\n"
        "    type: table\n"
        "    query: q\n"
        "    rows: [month]\n"
        "    columns: [region]\n"
    ),
    # The one cartesian family whose color channel is already its measure, so
    # the wide fold has nothing left to spend.
    "heatmap": (
        "rows:\n"
        "  - title: H\n"
        "    type: heatmap\n"
        "    query: q\n"
        "    x: month\n"
        "    y: region\n"
    ),
    # A variable is a target of its own, so its controls board the same gate
    # every chart's do — and `Variable` is the first target model that is
    # neither a board nor a chart.
    "variables": (
        "variables:\n"
        "  region:\n"
        "    input: select\n"
        "    label: Region\n"
        "rows:\n"
        "  - label: Customers\n"
        "    type: kpi\n"
        "    query: q\n"
        "    value: customers\n"
    ),
}


def _panel_interactions(prop):
    """Every edit one panel interaction can produce for this control.

    Both directions: the guard's whole claim is "any single panel interaction
    leaves a parseable board", and clearing is half the interaction set — the
    `x`-on-a-multi-metric-bar defect was reachable only by clearing.
    """
    edits = []
    if prop.enum_values and "channel" not in prop.facets:
        # Every value, not the first one. A select's values are not
        # interchangeable to the compiler — eight of a variable's thirteen input
        # types break a board that authors a default — and one sample happened to
        # draw `auto`, the harmless one, so the whole class was invisible to both
        # sweeps.
        #
        # A channel's enum values are exempt: they are column *suggestions* on a
        # free-text control, not a closed vocabulary the panel restricts input
        # to — putting a dimension in `y` fails the same designed diagnostic it
        # fails when typed by hand, which this sweep was never about.
        edits += [v for v in prop.enum_values if v != prop.value]
        # A vocabulary on a `list` control still has a multi-value shape to
        # reach, and it has to be reached in the vocabulary the field takes --
        # except a categorical palette (the `palette` facet), whose scalar
        # arm alone takes a name; a list is already literal color stops, and
        # a name inside it is neither a color nor a token (see `Palette`'s
        # own docstring). Wrapping two names into a list there is not a
        # panel interaction any control should offer, so this sweep does not
        # probe it -- the scalar sweep just above still exercises the
        # vocabulary the way the field actually takes it.
        if (
            prop.widget == "list"
            and len(prop.enum_values) > 1
            and "palette" not in prop.facets
        ):
            edits.append(list(prop.enum_values[:2]))
    elif prop.widget == "checkbox":
        edits.append(not prop.value)
    elif prop.widget == "number":
        edits.append(100)
    elif prop.widget == "list":
        # Both shapes the control can produce. The multi-value one is the whole
        # reason it exists, and it is what makes the compiler's multi-metric
        # rules reachable from the panel for the first time. A lone value is
        # wrapped where the field takes nothing else — that is the writer's rule
        # from `list_only`, and the panel applies it before it saves.
        edits += [["x"] if prop.list_only else "x", ["revenue", "cost"]]
    else:
        edits.append("x")
    # A required control renders no way to clear it, so `None` is not an
    # interaction the panel can produce there.
    if not prop.required:
        edits.append(None)
    return edits


# The generated snapshot of engine-predefined names — what a wheel-shipped
# artifact can know, and what a board's own aliases are offered beside. Split
# by kind: a slot that knows whether it paints a number or a date offers only
# that half, and only the kind-agnostic `format:` slots offer the union.
_BUILT_IN_ALIASES = frozenset(get_args(FormatAlias))
_NUMBER_ALIASES = frozenset(get_args(NumberFormatAlias))
_TIME_ALIASES = frozenset(get_args(TimeFormatAlias))


def _flat(node: DesignNode) -> dict[str, DesignProperty]:
    """Every property under `node`, keyed by dotted path relative to it.

    The verb emits structure; most of these tests predate it and assert on the
    dotted spelling, which is the one the renderer and `set_board_values` use.
    """
    out = dict(node.properties)
    for name, child in node.children.items():
        out.update({f"{name}.{key}": prop for key, prop in _flat(child).items()})
    return out


def _targets(board: str) -> dict[str, DesignTarget]:
    """Every design target on `board`, by resolving each authored path.

    The verb answers one path; these tests assert across a whole board. Every
    target owns at least one authored key, so resolving the source map's paths
    and keeping the distinct answers enumerates the same set the whole-board map
    used to — and exercises the walk-up on every one of them.
    """
    found: dict[str, DesignTarget] = {}
    for path in build_source_index(board, "<test>").source_map:
        target = build_design_target(board, path)
        found[target.path] = target
    return found


@pytest.fixture(scope="module")
def targets() -> dict[str, DesignTarget]:
    return _targets(BOARD)


def test_the_root_board_is_an_ordinary_target_at_the_empty_path(targets) -> None:
    """No special case for the board — it is the object at path ''."""
    assert targets[""].model == "AuthoredBoard"
    assert "style.background" in _flat(targets[""])


def test_schema_version_is_not_a_design_control(targets) -> None:
    """dct migrate-written, informational -- a Cloud user must not be able to
    hand-author it via the design panel. Regression test for _NOT_DESIGN's
    "_schema_version" entry: the exhaustive-membership test at the bottom of
    this file only checks that names in _NOT_DESIGN exist in the schema, not
    that this specific name is present, so a removed entry lands silently
    without this test.
    """
    assert "_schema_version" not in _flat(targets[""])


def test_theme_is_not_a_design_control(targets) -> None:
    """`theme` is a synthetic `SchemaSugar` field: desugared into `extends:`
    before any instance the panel walks exists, so `getattr(instance, "theme",
    None)` always finds nothing and a control here would render empty (the
    same defect `_schema_version`'s regression test above guards). Regression
    test for `_NOT_DESIGN`'s "theme" entry, same reasoning as
    `test_schema_version_is_not_a_design_control`.
    """
    assert "theme" not in _flat(targets[""])
    nested_targets = _targets(
        "theme: paper\nrows:\n  - title: Section\n    theme: neon\n    rows: []\n"
    )
    assert "theme" not in _flat(nested_targets[""])
    assert "theme" not in _flat(nested_targets["rows.0"])


def test_each_target_carries_its_own_models_name(targets) -> None:
    """The union is discriminated by the document, not guessed from the schema."""
    assert targets["charts.rev"].model == "LineChart"
    assert targets["rows.0.cols.0"].model == "AreaChart"
    assert targets["rows.0.cols.1"].model == "KpiChart"


def test_a_kpi_and_an_area_chart_expose_different_property_sets(targets) -> None:
    """Not a subset — a different set. This is why a static path list cannot work.

    Asserted as presence *and* absence on both sides: a count, or a one-sided
    check, would pass on a builder that simply emitted every field of every
    chart family.
    """
    kpi = _flat(targets["rows.0.cols.1"])
    area = _flat(targets["rows.0.cols.0"])

    assert {"value", "label", "variant"} <= set(kpi)
    assert "x" not in kpi and "y" not in kpi

    assert {"x", "title", "y_label"} <= set(area)
    assert "variant" not in area and "value" not in area


def test_authored_values_are_distinguished_from_inherited_ones(targets) -> None:
    """The panel grays what the file does not set; that needs the source map."""
    area = _flat(targets["rows.0.cols.0"])
    assert area["title"].value == "Monthly Revenue"
    assert area["title"].authored_here is True
    # Left to the theme cascade: present as an editable control, but not set here.
    assert area["height"].value is None
    assert area["height"].authored_here is False


def test_enum_fields_carry_their_choices(targets) -> None:
    variant = _flat(targets["rows.0.cols.1"])["variant"]
    assert variant.widget == "select"
    assert variant.enum_values is not None
    assert "compact" in variant.enum_values


def test_axis_x_ticks_visible_widget_stays_a_checkbox() -> None:
    """``style.axis_x.ticks.visible`` widened to ``bool | Literal["auto"] |
    None`` for the tick-stub auto rule -- but only ``DimensionTicksStyle``
    (axis_x's own tick slot) carries "auto"; axis_y/axis_quantitative/
    axis_band/the ``axis`` baseline all still use the shared
    ``AxisTicksStyle`` and stay ``bool | None``. If "auto" ever leaks onto
    that shared base again, every one of those four regresses from a
    checkbox to a ``select`` whose only option is "auto", making an
    authored ``true``/``false`` unauthorable from the panel."""
    targets = _targets(
        "rows:\n"
        "  - title: S\n"
        "    type: scatter\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      axis_x:\n"
        "        ticks:\n"
        "          visible: false\n"
    )
    props = _flat(targets["rows.0"])
    # axis_x is the one slot the auto rule actually resolves for, so it is a
    # select -- but the offer must still carry the bool arm, or an authored
    # true/false is neither showable nor restorable from the panel.
    axis_x = props["style.axis_x.ticks.visible"]
    assert axis_x.widget == "select"
    assert axis_x.enum_values == (True, False, "auto")
    assert axis_x.value is False
    assert props["style.axis_y.ticks.visible"].widget == "checkbox"
    assert props["style.axis_quantitative.ticks.visible"].widget == "checkbox"
    assert props["style.axis_band.ticks.visible"].widget == "checkbox"
    assert props["style.axis.ticks.visible"].widget == "checkbox"


def test_a_required_field_offers_no_default(targets) -> None:
    """Required fields hold `dataclasses.MISSING`, which must never be serialized."""
    value = _flat(targets["rows.0.cols.1"])["value"]
    assert value.required is True
    assert value.default_repr is None
    assert not [t for t in targets.values() if "_MISSING_TYPE" in t.model_dump_json()]


def test_style_is_a_group_on_the_owning_target(targets) -> None:
    assert targets["rows.0.cols.0"].children["style"].model == "AreaChartStylePatch"


def test_a_group_carries_the_description_of_the_field_that_introduced_it(
    targets,
) -> None:
    """A leaf's `data-hint` came from its `SchemaField.description`; a group's
    title had nothing to hover, because `_describe` built the child `DesignNode`
    from that same field and dropped its `description` on the floor. Checked
    against the schema's own answer, not a hard-coded string, so a prose edit to
    either field's docstring can't silently make this pass for the wrong reason.
    """
    schema = introspect()
    target = targets["rows.0.cols.1"]  # the KPI: a Vega-Lite family carries style.font
    style_field = next(
        f for f in schema.models[target.model].fields if f.name == "style"
    )
    assert style_field.description
    assert target.children["style"].description == style_field.description

    font_field = next(
        f
        for f in schema.models[target.children["style"].model].fields
        if f.name == "font"
    )
    assert font_field.description
    assert (
        target.children["style"].children["font"].description == font_field.description
    )


def test_the_root_target_has_no_introducing_field_so_no_description(targets) -> None:
    """The root is asked for, not reached through a field — nothing describes it."""
    assert targets[""].description == ""


def test_the_query_editors_surface_is_not_a_design_control(targets) -> None:
    """`query` is nested and belongs to the query editor, not the design panel."""
    for target in targets.values():
        assert "query" not in _flat(target)
        assert not any(name.startswith("query.") for name in _flat(target))


def test_a_bare_row_or_col_is_not_a_design_target() -> None:
    """A layout container is a hit-testing waypoint until it carries content."""
    targets = _targets(
        "rows:\n"
        "  - cols:\n"
        "      - title: A chart\n"
        "        type: area\n"
        "        query: q\n"
        "        x: month\n"
        "        y: revenue\n"
    )
    assert "rows.0" not in targets
    assert "rows.0.cols.0" in targets


def test_a_row_carrying_a_title_or_text_is_a_design_target() -> None:
    targets = _targets(
        "rows:\n"
        "  - title: A section\n"
        "    cols: []\n"
        "  - text: Some prose\n"
        "    cols: []\n"
    )
    assert targets["rows.0"].model == "AuthoredBoard"
    assert targets["rows.1"].model == "AuthoredBoard"


def test_a_charts_whole_style_graph_is_reachable() -> None:
    """The cap that hid it was the whole-board map's cost, not a fact about style.

    One target is affordable to describe fully, so a KPI's `style.value` group
    — and the sixteen others `_FLATTEN` dropped — arrive with it.
    """
    targets = _targets(
        "rows:\n  - label: A KPI\n    type: kpi\n    query: q\n    value: customers\n"
    )
    assert targets["rows.0"].model == "KpiChart"
    keys = _flat(targets["rows.0"])
    assert "style.background" in keys
    assert [key for key in keys if key.startswith("style.value.")]


def test_the_verb_reads_through_the_project_handle_not_the_filesystem(tmp_path) -> None:
    """A raw `Path.read_text()` would work here and break under Cloud's git store.

    Exercised through a real `Project` so the read goes where the host says,
    which is the whole reason project content is not reached by bare path.
    """
    from dbt_charts.cli.filesystem_project import FilesystemProject

    boards = tmp_path / "boards"
    boards.mkdir()
    (boards / "b.yml").write_text(
        "rows:\n  - label: A KPI\n    type: kpi\n    query: q\n    value: customers\n"
    )
    project = FilesystemProject(tmp_path)

    from dbt_charts.agent_api.design import design_target

    target = design_target(Path("boards/b.yml"), "rows.0", project=project)
    assert target.model == "KpiChart"


def test_a_field_that_admits_a_list_is_not_offered_as_a_text_box(targets) -> None:
    """`y: str | list[str] | None` reports container=None — the container is one
    arm, not the whole field — so the scalar guard used to let it through. A
    text box shows an authored two-series `y` as empty and collapses it to a
    string on write.

    The answer is the `list` widget, not silence: `y` was dropped entirely while
    `x`, `x_label` and `y_label` stayed, which made the panel look like it had a
    `y` control. A field whose non-list arms are scalars gets one that holds
    both shapes; a `dict[...]` arm, or a list of anything but scalars, still has
    no control the widget set can offer.
    """
    area = _flat(targets["rows.0.cols.0"])
    assert area["y"].widget == "list"
    # A `{column, scale}` object, not a list — the widget set has nothing for it.
    assert "background" not in _flat(targets["rows.0.cols.1"])


def test_a_downgraded_list_never_leaves_a_list_in_a_text_box() -> None:
    """A text box holds a name, so it must never be handed a list of them.

    `_takes_a_list` refuses the `list` widget on a chart that cannot take one,
    and the control falls back to text — but the authored value is still a
    list. Rendered, that box reads `[revenue, cost]`, and the obvious edit
    (adding a name inside the brackets) writes the whole bracketed string as
    one column name: legal YAML for the `str` arm, so the save endpoint's
    re-parse waves it through, and the chart names a column no query has.

    One entry is the one case a text box can hold honestly — it is that name.
    More than one is dropped, which is what the panel did before the `list`
    widget existed.
    """
    single = _targets(
        "rows:\n  - type: spark_bar\n    query: q\n    x: month\n    y: [revenue]\n"
    )["rows.0"]
    assert _flat(single)["y"].widget == "text"
    assert _flat(single)["y"].value == "revenue"

    # Heatmap, because the two-entry list has to *parse* for the downgrade to be
    # reachable at all — and heatmap is the family that refuses a list with no
    # validator behind the refusal, so nothing stops the board on the way in.
    many = _targets(
        "rows:\n"
        "  - type: heatmap\n"
        "    query: q\n"
        "    x: month\n"
        "    y: [region, revenue]\n"
    )["rows.0"]
    assert "y" not in _flat(many)


def test_a_closed_vocabulary_with_a_list_arm_still_gets_its_control() -> None:
    """`extends` and a categorical `palette` carry their values in the schema,
    and the list guard threw them away one step before the offer.

    Both are spelled `<enum> | str | list[str] | None`. The enum arm is not a
    scalar type name, so the non-list arms failed the scalar check and the two
    most-wanted controls on a board — the theme and the palette — projected to
    nothing at all. A set of strings is exactly what the `list` box holds, and
    its members ride along as suggestions the way a channel's inferred columns
    already do.
    """
    board = (
        "extends: [paper, ./_base.yml]\n"
        "rows:\n"
        "  - type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      color:\n"
        "        categorical:\n"
        "          palette: vivid-10\n"
    )
    targets = _targets(board)
    extends = _flat(targets[""])["extends"]
    assert extends.widget == "list"
    assert extends.value == ["paper", "./_base.yml"]
    assert "paper" in (extends.enum_values or ())

    palette = _flat(targets["rows.0"])["style.color.categorical.palette"]
    assert palette.widget == "list"
    assert palette.value == "vivid-10"
    assert "vivid-10" in (palette.enum_values or ())


def test_the_theme_sugar_authors_the_control_it_desugars_into() -> None:
    """`theme:` is folded into `extends:` by the parser, before the walk sees
    either — so the source map holds the spelling the file used and the walk
    asks for the one it resolved to. Reading them apart rendered an empty theme
    control over a board plainly using a theme, and the panel writes only what
    a control says is authored.
    """
    prop = build_design_target("title: T\ntheme: neon\nrows: []\n", "").properties[
        "extends"
    ]
    assert prop.value == "neon"
    assert prop.authored_here


def test_a_charts_only_board_still_has_a_root_entry() -> None:
    """A board with no title and no text is valid, and owns the whole frame.

    Gating the root on the same content rule as a nested row dropped every
    board-level style property — the surface the panel exists to render.
    """
    targets = _targets(
        "charts:\n"
        "  rev:\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "rows: [rev]\n"
    )
    assert targets[""].model == "AuthoredBoard"
    assert "style.background" in _flat(targets[""])


def test_frame_is_a_group_under_style(targets) -> None:
    """`style.frame` is the board's dimensions — the panel's most-used group.

    A group like any other now, reached at `children["style"].children["frame"]`
    — the dotted spelling `_flat` rebuilds is what a write path uses.
    """
    root = _flat(targets[""])
    assert root["style.frame.width"].widget == "number"
    assert {"style.frame.min_height", "style.frame.card_padding"} <= set(root)
    # Its siblings arrive too, which is the point of describing one target.
    assert [key for key in root if key.startswith("style.border.")]


def test_a_numeric_union_still_projects_to_a_number_control() -> None:
    """`int | float | None` is a number, not free text.

    Height and width are the two most obvious numeric controls on a chart, and
    both are authored as that union — matching the whole `type_repr` against a
    set of bare type names sent them to a text box.
    """
    targets = _targets(
        "rows:\n  - title: R\n    type: area\n    query: q\n    x: m\n    y: v\n"
    )
    assert _flat(targets["rows.0"])["height"].widget == "number"


def test_a_union_mixing_a_string_arm_stays_text() -> None:
    """`width: str | int` takes `100%`, which a number box cannot hold.

    Read on a nested board: at the root `width` is dropped as sugar for
    `style.frame.width`, which the compiler forbids authoring alongside it.
    """
    targets = _targets("rows:\n  - title: R\n    width: 300\n")
    assert _flat(targets["rows.0"])["width"].widget == "text"


def test_the_root_board_offers_exactly_one_width_control() -> None:
    """`width:` is sugar for `style.frame.width:` and authoring both raises.

    Flattening `frame` put them side by side on the root target, so setting one
    control and then the other wrote a board the normalizer refuses — and the
    panel that broke it degrades to "does not parse". The surface emits whichever
    one the file authors, and the canonical key when it authors neither.
    """
    unset = _flat(_targets("title: T\nrows: []\n")[""])
    assert "style.frame.width" in unset
    assert "width" not in unset

    sugar = _flat(_targets("width: 900\nrows: []\n")[""])
    assert "width" in sugar
    assert "style.frame.width" not in sugar

    canonical = _targets("style:\n  frame:\n    width: 900\nrows: []\n")[""]
    assert "style.frame.width" in _flat(canonical)
    assert "width" not in _flat(canonical)


def test_a_nested_board_keeps_its_own_width() -> None:
    """`width:` on a nested board is layout placement, not frame sugar.

    The equivalence is wired at the root only (`_root_width_style_patch`), so
    the pair the root must choose between does not exist here — the nested
    `width` is a real, honored field. Its `style.frame` twin is suppressed for
    a different reason: a nested board's `FrameStyle` is ignored entirely.
    """
    nested = _targets("rows:\n  - title: R\n    width: 300\n")["rows.0"]
    assert "width" in _flat(nested)
    assert "style.frame.width" not in _flat(nested)


def test_a_root_only_field_is_not_offered_on_a_nested_target() -> None:
    """`card_gap` raises on a nested board, so a checkbox for it is one click
    from a board that will not compile — the same class as the width pair, and
    cheaper to reach, since a checkbox commits on the first tick."""
    targets = _targets("card_gap: true\nrows:\n  - title: Section\n    rows: []\n")
    assert "card_gap" in _flat(targets[""])
    assert "card_gap" not in _flat(targets["rows.0"])


def test_a_chart_offers_no_model_control() -> None:
    """`model:` is semantic-layer query sugar — the query editor's surface.

    Editable here it is worse than the `width` pair: the other half of its
    conflict is `query:`, which the surface hides by design, so the panel offers
    no way to reconcile what it just broke. And on a chart with no `query:`
    there is still no value a text box can produce that compiles.
    """
    targets = _targets(
        "rows:\n  - title: R\n    type: area\n    query: q\n    x: m\n    y: v\n"
    )
    assert "model" not in _flat(targets["rows.0"])


# One full parse per control per interaction, across eight fixtures — ~34s on an
# idle machine, past the repo-wide 30s the moment CI loads a worker. Widening the
# ceiling rather than sampling the controls: the property is "*every* control",
# and a guard that skips controls is the guard that missed `color` and `card_gap`.
#
# Not `slow`, deliberately, at either this cost or the render gate's: every suite
# that runs this file runs `-m "not slow"`, so the marker does not defer the gate,
# it deletes it. Half a minute inside a `-n auto` shard is the price of the one
# test that has caught each round's regression. The budget is sized for a
# whole-repo `just ci` run, where six saturated workers stretch that half
# minute several-fold — 120 was killing this test under load while it passed
# every solo and package-suite run.
@pytest.mark.timeout(300)
def test_no_control_can_commit_a_board_that_stops_parsing() -> None:
    """The invariant four rounds of review kept catching, one field at a time.

    `type`, `card_gap`, `model` and `color` each shipped as an editable control
    whose value the compiler refuses, and each time the panel that broke the
    board could not repair it. An earlier attempt at a guard grepped the
    compiler's error prose; it could see neither `color` (whose message matches
    no common phrasing) nor `card_gap` (whose message names no field) — exactly
    the failure it existed to prevent.

    So this asserts the property rather than a spelling of it: set every control
    the surface offers to a schema-plausible value and require the result to
    still parse. It needs no list of forbidden fields, and a new conflict rule
    in the compiler fails here the day it lands.
    """
    for label, board in _PARSE_FIXTURES.items():
        targets = _targets(board)
        for target in targets.values():
            for key, prop in _flat(target).items():
                absolute = f"{target.writes_at}.{key}" if target.writes_at else key
                for value in _panel_interactions(prop):
                    updated = set_board_values(board, {absolute: value})
                    try:
                        parse_yaml(updated)
                    except Exception as exc:  # noqa: BLE001 — any refusal is the defect
                        action = (
                            "clearing" if value is None else f"setting to {value!r}"
                        )
                        raise AssertionError(
                            f"[{label}] the panel offers {absolute!r} on "
                            f"{target.model}, but {action} it leaves a board "
                            f"that no longer parses: {exc}"
                        ) from exc


# The same boards, carrying the query they name so they can actually run. Every
# family whose multi-metric rule fires at resolve rather than at parse is here:
# bar and area (guarded at parse too), line and scatter (parse accepts them), and
# the shapes that must keep rendering so the gate below cannot pass by refusing
# everything.
# `x` is a real, numeric column here because `_panel_interactions` writes that
# literal name into every control it sweeps. Absent, the pivot fails on a column
# its data does not have; non-numeric, area fails on a swapped encoding. Neither
# is the rule the sweep is about — a column's *type* is not something the panel
# can know, and both are what the fixture, not the design target, got wrong.
_RENDER_QUERY = (
    "queries:\n"
    "  q:\n"
    "    type: values\n"
    "    rows:\n"
    "      - {month: Jan, revenue: 1, cost: 2, region: A, customers: 3, x: 10}\n"
    "      - {month: Feb, revenue: 2, cost: 1, region: B, customers: 4, x: 20}\n"
)

_RENDER_FIXTURES = {
    "line-with-color": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: region\n"
    ),
    "line-with-layers": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    layers:\n"
        "      - type: line\n"
        "        y: cost\n"
    ),
    "scatter": (
        "rows:\n"
        "  - title: Scatter\n"
        "    type: scatter\n"
        "    query: q\n"
        "    x: revenue\n"
        "    y: cost\n"
    ),
    "scatter-with-layers": (
        "rows:\n"
        "  - title: Scatter\n"
        "    type: scatter\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    layers:\n"
        "      - type: scatter\n"
        "        y: cost\n"
    ),
    "bar-with-color": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: region\n"
    ),
    "bar-with-gradient-color": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "line-with-gradient-color": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "area-with-gradient-color": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "scatter-with-gradient-color": (
        "rows:\n"
        "  - title: Scatter\n"
        "    type: scatter\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    color: revenue\n"
        "    style:\n"
        "      color:\n"
        "        gradient:\n"
        "          palette: ['#ffffff', '#0000ff']\n"
    ),
    "area-plain": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # A categorical `x` under `curve: step` is the band-step path, and both
    # emitters raise `ERR-INTERNAL` there the moment `y` holds two columns. The
    # board parses either way, so only a render reaches it.
    "line-stepped": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      marks:\n"
        "        line:\n"
        "          curve: step\n"
    ),
    "area-stepped": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      marks:\n"
        "        area:\n"
        "          curve: step\n"
    ),
    # Two shapes whose baseline render used to be an `ERR-INTERNAL` crash, both
    # a single cleared-`y` away from ordinary boards: columns-multiples with no
    # measure fired the auto-mirror default at an absent y encoding
    # (MirrorAxisFeature), and a chart with neither channel emitted line/area
    # layers with no `encoding` key, which Vega-Lite 6.x cannot compile.
    "line-multiples-no-y": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    multiples:\n"
        "      columns: region\n"
    ),
    "line-bare": "rows:\n  - title: Line\n    type: line\n    query: q\n",
    "area-bare": "rows:\n  - title: Area\n    type: area\n    query: q\n",
    # `render/chart/table.py` raises `ERR-INTERNAL` for a pivot declaring
    # neither `rows` nor `values`, and every one of these three fields is a
    # `list` control, so the sweep reaches that rule from both directions.
    "table-plain": "rows:\n  - title: T\n    type: table\n    query: q\n",
    "table-rows-only": (
        "rows:\n"
        "  - title: T\n"
        "    type: table\n"
        "    query: q\n"
        "    rows: [month]\n"
        "    columns: [region]\n"
    ),
    # Authored on columns the sweep does not itself write. `TableChart`'s own
    # validator refuses a field claimed by two of the three channels at once,
    # and that one is a data-value collision rather than a structural rule: the
    # panel cannot know which columns a sibling already holds, `parse_yaml`
    # refuses it, and the save endpoint's re-parse is exactly the backstop for
    # that. Letting the sweep collide here would test the fixture, not the rule.
    "table-values-only": (
        "rows:\n"
        "  - title: T\n"
        "    type: table\n"
        "    query: q\n"
        "    values: [customers]\n"
        "    columns: [region]\n"
    ),
    "heatmap": (
        "rows:\n"
        "  - title: H\n"
        "    type: heatmap\n"
        "    query: q\n"
        "    x: month\n"
        "    y: region\n"
    ),
    # `axis_y.mirror` reflects one shared y-scale, so `mirror_axis.py` raises
    # `ERR-MIRROR-MULTI-SERIES` the moment `y` holds two columns — at render, on
    # all four wide-capable cartesian families. Both authored forms, because
    # `mirror` is `bool | AxisMirrorStyle` and the object form breaks the board
    # just as the bare `true` does.
    "line-mirrored": (
        "rows:\n"
        "  - title: Line\n"
        "    type: line\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      axis_y:\n"
        "        mirror: true\n"
    ),
    "area-mirrored": (
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      axis_y:\n"
        "        mirror: true\n"
    ),
    "scatter-mirrored": (
        "rows:\n"
        "  - title: Scatter\n"
        "    type: scatter\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      axis_y:\n"
        "        mirror: true\n"
    ),
    # Pinned to `orientation: vertical`: bar's renderer default is horizontal,
    # which puts the category on VL's own y encoding — the mirror ghost reads
    # that literal VL y, not dbt charts' axis_y/measure convention, so a
    # default-orientation bar here would mirror `month` instead of `revenue`
    # and gate_label_format would correctly refuse a percent format on it.
    "bar-mirrored-object": (
        "rows:\n"
        "  - title: Bar\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      orientation: vertical\n"
        "      axis_y:\n"
        "        mirror:\n"
        "          format: percent\n"
    ),
    # A variable's failures land past the parse gate on both sides of it —
    # `options.column` at compile, `options.query` at render — and none of them
    # is reachable through a `list` widget, which a variable has none of. So
    # this is the one target the sweep below walks in full; it can afford to,
    # because a variable offers a dozen controls rather than a chart's hundreds.
    "variables": (
        "variables:\n"
        "  region:\n"
        "    input: select\n"
        "    label: Region\n"
        "    default: A\n"
        "    options:\n"
        "      static: [A, B]\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # The other branch of the dot-notation rule, swept because it is the branch
    # where `options.column` is a control the panel *does* offer: with a query
    # named, a bare column name is what the field wants, and the claim being
    # pinned is that a wrong one empties the option list rather than breaking
    # the board.
    "variables_from_query": (
        "variables:\n"
        "  region:\n"
        "    input: select\n"
        "    label: Region\n"
        "    default: A\n"
        "    options:\n"
        "      query: q\n"
        "      column: month\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # The same variable with nothing to fall back on, which is a different board
    # to the compiler and the one where `required` bites: ticking it raises
    # before a query runs and takes the whole strip — and with it the click
    # target that could untick it — off the page. The guard withholds the
    # control, so what this fixture sweeps is the guard: drop it and the tick is
    # back in the interaction set.
    "variables_without_default": (
        "variables:\n"
        "  region:\n"
        "    input: select\n"
        "    label: Region\n"
        "    options:\n"
        "      static: [A, B]\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # A numeric variable, because every other fixture here is `input: select`
    # and the rules that read a *number* are unreachable from a string default.
    # Two of them: `min`/`max` are bounds-checked against the default for slider
    # and range only, and the type check that governs `input:` has a different
    # answer when the default is an int. Both are ordinary panel edits, both
    # leave a board that parses and refuses to compile, and neither was sweepable
    # before this fixture existed.
    "variables_numeric": (
        "variables:\n"
        "  size:\n"
        "    input: slider\n"
        "    label: Size\n"
        "    default: 5\n"
        "    min: 0\n"
        "    max: 10\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # The same numeric variable with the `input:` line left off, which is the
    # authored shape the compiler treats differently from every fixture above:
    # `input` defaults to `auto`, and `detect_variable_input_type` promotes an
    # auto variable to `slider` the moment any of `min`/`max`/`step` is set. So
    # the bound rules that `variables_numeric` reaches through an explicit
    # `slider` are reachable here through the panel *writing one of the bounds*,
    # on a variable whose authored text never says slider at all.
    "variables_auto": (
        "variables:\n"
        "  size:\n"
        "    label: Size\n"
        "    default: 5\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # And a boolean default, the one shape where the compile-time validator and
    # the runtime coercer disagree: `bool` subclasses `int`, so
    # `validate_variable_value` takes a bool wherever it takes a number, while
    # `_coerce_number` rejects it by name. A guard that asks only the first
    # offers `number`/`slider`/`range` here and the board renders an error.
    "variables_boolean": (
        "variables:\n"
        "  flag:\n"
        "    input: checkbox\n"
        "    label: Live\n"
        "    default: false\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
    # The two fixtures above are the two axes of the same trap, and each pins
    # only one of them: `variables_auto` leaves `input:` off but defaults to a
    # number, `variables_boolean` defaults to a bool but pins `input:` so
    # nothing can promote it. Crossed, they are the case neither one reaches —
    # promotion to `slider` on a default the runtime refuses to coerce.
    "variables_boolean_auto": (
        "variables:\n"
        "  flag:\n"
        "    label: Live\n"
        "    default: false\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    ),
}


def _render_failures(board: str) -> list[str]:
    """Every diagnostic a real render of `board` reports, by code.

    `parse_yaml` is the save gate, and it is blind to a rule the compiler
    enforces at resolve — the board parses, is written to git, and the chart
    fails the next time anyone looks at it. Rendering is the only call that
    reaches those, and a `values` query makes it offline and cheap.

    A compile refusal is returned rather than asserted: it is the *worst* thing
    an edit can do — no board at all, so no SVG, so no stamped path and no panel
    to undo from — and asserting it here would abort the sweep with a message
    that names neither the control nor the fixture. The baseline call at the top
    of each sweep still catches a fixture that was broken before any edit.
    """
    result = compile(board)
    compiled_board = result.board
    if not result.success or compiled_board is None:
        return [d.code for d in result.errors]
    executor = Executor(
        compiled_board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(compiled_board, executor, format="svg")
    codes = [d.code for d in rendered.chart_errors]
    if rendered.board_error is not None:
        codes.append(rendered.board_error.code)
    return codes


# ~0.1s per render, and only the `list` widget reaches these rules, so the sweep
# is bounded to those controls rather than to every control the parse gate walks.
# The variable target is the exception and every value of its `input` select is
# now a render of its own. `extends` and the two categorical palettes are the
# rest: a `list` control carrying a vocabulary sweeps every value of it as a
# scalar write. A categorical palette's list *items* are colors or color
# tokens, never palette names -- `palette: ["editorial-10", "#4e79a7"]` is
# rejected at resolve (ERR-PALETTE-UNKNOWN), same as a typo would be, so
# `_panel_interactions` does not build that shape (`Palette`'s own docstring
# in `core/compile/models/markers.py` has the full rule). ~2,200 renders,
# ~210s here; the ceiling is a runaway guard, not a budget — it sits clear of
# that measurement so a loaded xdist worker doesn't trip it.
@pytest.mark.timeout(600)
def test_no_list_control_can_commit_a_board_that_stops_rendering() -> None:
    """The parse gate's blind half: a rule the compiler enforces at resolve.

    `reject_multi_series_channel_conflicts` is a parse-time validator for bar and
    area only. Line and scatter enforce the identical rule from
    `resolve_wide_measure_channels` (`resolve/chart/_wide_fields.py`), reached
    from `resolve/chart/line.py` and `resolve/chart/scatter.py` respectively —
    both past the save endpoint's re-parse. So the panel offered a multi-value
    control on those two families, the save succeeded, and the board stopped
    rendering, with no way back from the panel that broke it.

    Bounded to the `list` widget on purpose: it is the edit that made these rules
    reachable at all, and it is the only one worth a full render apiece. The
    parse gate above still walks every control.

    The variable target is the exception, walked in full: a variable has no list
    control, and the rules it can break — a column reference without its table,
    a query name that names nothing — are reached only through text boxes. A
    dozen controls is a few seconds; a chart's hundreds would be minutes.
    """
    for label, chart in _RENDER_FIXTURES.items():
        board = _RENDER_QUERY + chart
        assert not _render_failures(board), (
            f"[{label}] fixture is broken before any edit"
        )
        targets = _targets(board)
        for target in targets.values():
            for key, prop in _flat(target).items():
                if prop.widget != "list" and target.model != "Variable":
                    continue
                absolute = f"{target.writes_at}.{key}" if target.writes_at else key
                for value in _panel_interactions(prop):
                    updated = set_board_values(board, {absolute: value})
                    failures = _render_failures(updated)
                    action = "clearing" if value is None else f"setting to {value!r}"
                    assert not failures, (
                        f"[{label}] the panel offers a list control on "
                        f"{target.model}.{key}, but {action} it leaves a board "
                        f"that parses and no longer renders: {failures}"
                    )


def test_grid_and_tab_layouts_produce_targets() -> None:
    """Two of the four authored layouts had none, and the walk-up hid it.

    `resolve_target` maps a leaf to its containing object by popping segments,
    so an unwalked layout family does not error — every click inside it lands
    silently on the board, and a save there writes board-level keys. The
    renderer stamps `grid.items.N` and `tabs.items.N`, so those are the keys.
    """
    grid = _targets(
        "grid:\n"
        "  columns: 2\n"
        "  items:\n"
        "    - item:\n"
        "        label: Left\n"
        "        type: kpi\n"
        "        query: q\n"
        "        value: v\n"
    )
    assert grid["grid.items.0"].model == "KpiChart"

    tabs = _targets(
        "tabs:\n"
        "  items:\n"
        "    - title: One\n"
        "      rows:\n"
        "        - label: Inside\n"
        "          type: kpi\n"
        "          query: q\n"
        "          value: v\n"
    )
    assert tabs["tabs.items.0.rows.0"].model == "KpiChart"


def test_a_nested_board_is_offered_no_frame_controls() -> None:
    """`compile/AGENTS.md`: "nested boards render into the parent's grid and
    their `FrameStyle` is ignored."

    Offering them is the quiet half of the `card_gap` defect: the save succeeds,
    the compiler discards the value, the board is unchanged, and the panel shows
    it as authored. No raise means nothing catches it — including the
    parse-based guard above.
    """
    targets = _targets(
        "style:\n  frame:\n    width: 900\nrows:\n  - title: Section\n    rows: []\n"
    )
    assert any(key.startswith("style.frame.") for key in _flat(targets[""]))
    nested = _flat(targets["rows.0"])
    assert not [key for key in nested if key.startswith("style.frame.")]


def test_a_nested_board_is_offered_no_extends_control() -> None:
    """`merged_patch` folds the extends chain once, on the root document
    (`compiler.py:957`), so a nested board's `extends:` is never merged at all.

    The same shape as the frame above: the save succeeds, the compiler discards
    the value, the board is unchanged, and the panel renders it back as
    authored. It arrived the moment `extends` gained a widget — the field is on
    every `AuthoredBoard` node, and until then no target had a control for it.
    """
    targets = _targets(
        "extends: paper\nrows:\n  - title: Section\n    extends: neon\n    rows: []\n"
    )
    assert "extends" in _flat(targets[""])
    assert "extends" not in _flat(targets["rows.0"])


def test_a_grid_item_writes_one_segment_deeper_than_it_is_clicked() -> None:
    """The renderer stamps the wrapper; the chart lives inside it.

    Writing at the clicked key inserts into `GridItem`, which declares its own
    `height`/`width` as row/col spans — so editing a chart's height changed the
    grid geometry, recompiled cleanly, and showed as saved. Every other key it
    could insert is one `extra="forbid"` rejects.
    """
    board = (
        "grid:\n"
        "  columns: 2\n"
        "  items:\n"
        "    - item:\n"
        "        label: Left\n"
        "        type: kpi\n"
        "        query: q\n"
        "        value: v\n"
    )
    target = _targets(board)["grid.items.0"]
    assert target.writes_at == "grid.items.0.item"
    # The authored value is found through the same spelling, so the panel shows
    # it rather than blanking every control under a grid item.
    assert _flat(target)["label"].authored_here


def test_a_chart_offers_no_type_control() -> None:
    """`type` is the union discriminator — identity, not design.

    As a select it made one click a structural edit: bar → histogram beside an
    unsupported `support_table`, or histogram → bar with a multi-metric `y` and no
    `x`. Each pair is a compiler rule the panel would otherwise re-encode.
    """
    targets = _targets(
        "rows:\n  - title: R\n    type: area\n    query: q\n    x: m\n    y: v\n"
    )
    assert "type" not in _flat(targets["rows.0"])


def test_a_union_with_a_model_arm_projects_to_no_control() -> None:
    """`projection: str | Projection | None` is the lossy case `_widget_for`
    exists to refuse — but `Projection` is a Vega-Lite contract, not an authored
    model, so the IR reports no nested model and the `list[`/`dict[` guard could
    not see it. A text box showed an authored object as empty, and one keystroke
    would have replaced it with a string."""
    targets = _targets(
        "rows:\n  - type: geoshape\n    query: q\n    projection: {type: albersUsa}\n"
    )
    assert "projection" not in _flat(targets["rows.0"])


def test_the_board_offers_no_chart_focus_control() -> None:
    """A wrong name raises past the parse gate, a right one collapses the board
    to a single chart, and a nested board ignores it — all structural."""
    targets = _targets("title: T\nrows: []\n")
    assert "chart_focus" not in _flat(targets[""])


def test_a_named_chart_dict_layout_item_gets_a_target() -> None:
    """`- my_chart: {type: bar, …}` is a documented layout shape stamped
    `rows.N`, same as an inline chart. It parsed to a plain dict, so the walk
    never reached the chart inside — a click resolved up to the board and
    editing "Title" wrote the *board's* title. The same silent fall-through the
    grid and tabs walk closed, one shape short.
    """
    targets = _targets(
        "rows:\n"
        "  - my_chart:\n"
        "      type: bar\n"
        "      query: q\n"
        "      x: month\n"
        "      y: revenue\n"
    )
    target = targets["rows.0"]
    assert target.model == "BarChart"
    # The name is a wrapper key: the chart's YAML is one segment deeper, so a
    # write at the clicked path would land in the wrapper.
    assert target.writes_at == "rows.0.my_chart"


def test_a_nested_only_field_is_not_offered_on_the_root_board() -> None:
    """The mirror of `_design._ROOT_ONLY`, and the same defect class without the raise.

    `AuthoredBoard.height` is documented "Height when nested" and is read only
    from the layout-item branch, so at the root the save succeeds, the compiler
    discards the value, the board is unchanged, and the panel renders it back as
    authored. No raise means the parse guard is structurally unable to see it —
    `style.frame.min_height` sits two rows away and *is* honored.
    """
    root = _flat(_targets("title: T\nrows: []\n")[""])
    assert "height" not in root
    assert "style.frame.min_height" in root

    nested = _targets("rows:\n  - title: R\n    height: 300\n")["rows.0"]
    assert "height" in _flat(nested)


def test_a_chart_offers_no_id_control() -> None:
    """`normalize/charts.py` stamps `id` from the `charts:` map key.

    An authored one is overwritten, so the control saves, the board is
    unchanged, and the panel renders the value back as authored — the
    `card_gap` shape without the raise that caught it. The board's own `id` is
    excluded for a different reason (identity, not a no-op) and is pinned by
    `test_the_board_offers_no_control_a_design_edit_should_not_reach`.
    """
    targets = _targets(
        "charts:\n"
        "  rev:\n"
        "    query:\n"
        "      sql: SELECT 1 AS m\n"
        "    type: kpi\n"
        "    value: m\n"
    )
    assert "id" not in _flat(targets["charts.rev"])


def _multi_metric(chart_type: str) -> dict[str, DesignProperty]:
    board = (
        "charts:\n"
        "  c:\n"
        "    query:\n"
        "      sql: SELECT 1 AS m, 2 AS a, 3 AS b\n"
        f"    type: {chart_type}\n"
        "    x: m\n"
        "    y: [a, b]\n"
    )
    return _flat(_targets(board)["charts.c"])


def test_the_multi_metric_rules_follow_the_families_that_enforce_them() -> None:
    """`color:` composes with a multi-metric `y:` on every wide family; the
    `x`-required raise is bar's.

    A `color:` column beside `y: [a, b]` is the dimension the measures are
    grouped by (one series per value per measure), so the control stays on
    offer on bar, area, line, and scatter alike -- scatter folds the same
    way as the other three, unlike heatmap (its own, different multi-measure
    render path, see `test_a_heatmap_spends_its_color_channel_on_the_measure_
    and_takes_no_list`).

    The mirror matters as much: applying bar's `x`-required raise to a family
    that does not make it hides a control the compiler accepts.
    """
    assert "color" in _multi_metric("bar")
    assert "color" in _multi_metric("area")
    assert "color" in _multi_metric("line")
    assert "color" in _multi_metric("scatter")
    assert "y" in _multi_metric("scatter")

    assert _multi_metric("bar")["x"].required
    assert not _multi_metric("line")["x"].required


def test_a_heatmap_spends_its_color_channel_on_the_measure_and_takes_no_list() -> None:
    """The wide fold has nowhere to go on a heatmap, and nothing says so.

    `reject_multi_series_channel_conflicts` states the rule every wide family
    obeys — folding measures onto one mark family spends the mark fill —
    and a heatmap's color *is* its measure, so there is nothing left to fold
    onto. It is the one cartesian family that neither calls that validator nor
    refuses from its own resolver: `y: [region, revenue]` renders, with no
    diagnostic anywhere, as a chart whose y band stacks two columns' values
    against each other and whose cell color has stopped encoding magnitude.
    """
    assert "y" not in _multi_metric("heatmap")
    single = _flat(
        _targets(
            "rows:\n  - title: H\n    type: heatmap\n"
            "    query: q\n    x: month\n    y: region\n"
        )["rows.0"]
    )
    assert single["y"].widget == "text"


def test_a_histogram_does_not_inherit_bar_s_rules() -> None:
    """One model, two types, and the validator branches on the type.

    `BarChart` backs `Literal["bar", "histogram"]` and `_validate_multi_series`
    returns early on a histogram — it bins `x` and derives its measure by
    counting, so it never reads `y` and none of bar's conflicts apply. Keying
    the rule tables on the model would enforce bar's rules on a chart the
    compiler exempts: a multi-value `y` withheld from a board that renders, and
    a `color` control hidden beside it.
    """
    board = (
        "rows:\n  - title: H\n    type: histogram\n    query: q\n"
        "    y: [revenue, cost]\n    color: region\n"
    )
    props = _flat(_targets(board)["rows.0"])
    assert props["y"].widget == "list"
    assert "color" in props


def test_but_a_histogram_can_never_clear_its_x() -> None:
    """Exempt from bar's conflicts, and stricter than bar on the one shared rule.

    `_emit_histogram` raises `ERR-HISTOGRAM-NON-NUMERIC` when there is no `x` to
    bin — on every histogram, whatever `y` holds. Bar's rule is the conditional
    one (`x` required only beside a list `y`), so exempting histogram from bar's
    table has to *raise* the requirement rather than drop it. `required=False`
    is what renders the clear, and the clear is the single edit a histogram
    cannot survive: the board still parses, so the save endpoint waves it
    through and the chart stops rendering.
    """
    for y in ("revenue", "[revenue, cost]"):
        board = f"rows:\n  - title: H\n    type: histogram\n    query: q\n    y: {y}\n"
        assert _flat(_targets(board)["rows.0"])["x"].required is True


def _curved(family: str, y: str, curve: str) -> dict[str, DesignProperty]:
    board = (
        f"rows:\n  - title: C\n    type: {family}\n    query: q\n"
        f"    x: month\n    y: {y}\n"
        f"    style:\n      marks:\n        {family}:\n          curve: {curve}\n"
    )
    return _flat(_targets(board)["rows.0"])


class TestTheBandStepConflict:
    """`curve: step` and a multi-metric `y` cannot both stand on a line or area.

    `emitters/line.py` and `emitters/area.py` raise `ERR-INTERNAL` — "band-aware
    step curve is not supported for multi-metric (`y: [...]`) charts" — and
    *band-aware* means the x column resolved to a categorical scale, which
    `resolve_cartesian_x` reads off the data. The panel cannot know which boards
    are the broken ones, so it withholds the pair on every board that could be.
    """

    def test_a_stepped_chart_offers_no_multi_metric_y(self) -> None:
        for family in ("line", "area"):
            assert _curved(family, "revenue", "step")["y"].widget == "text", family

    def test_a_multi_metric_chart_offers_no_curve_control(self) -> None:
        """The mirror, because either order of two clicks reaches the raise.

        Over-strict on purpose: the four other curve values are legal on a
        multi-metric chart. The alternative is a select offering every value
        but one, which no other control on this surface does.
        """
        for family in ("line", "area"):
            props = _curved(family, "[revenue, cost]", "linear")
            assert f"style.marks.{family}.curve" not in props, family

    def test_neither_rule_fires_where_the_emitter_does_not_raise(self) -> None:
        for family in ("line", "area"):
            stepped = _curved(family, "revenue", "step")
            assert f"style.marks.{family}.curve" in stepped, family
            assert _curved(family, "[revenue, cost]", "linear")["y"].widget == "list", (
                family
            )


def _table(chart: str) -> dict[str, DesignProperty]:
    board = "rows:\n  - title: T\n    type: table\n    query: q\n" + chart
    return _flat(_targets(board)["rows.0"])


class TestThePivotsArityRule:
    """A pivot needs a `rows:` or a `values:`, and only the render says so.

    `render/chart/table.py` raises `ERR-INTERNAL` — "declare at least one of
    `rows` or `values`" — for a table that sets `columns:` and neither of the
    pair: with both omitted the row and measure dimensions are ambiguous. The
    board parses, so the save endpoint's re-parse passes it and the panel is
    left showing a chart that no longer renders.
    """

    def test_a_table_declaring_neither_offers_no_columns_control(self) -> None:
        assert "columns" not in _table("")

    def test_declaring_either_half_brings_it_back(self) -> None:
        assert "columns" in _table("    rows: [month]\n")
        assert "columns" in _table("    values: [revenue]\n")

    def test_the_last_half_standing_cannot_be_cleared_while_pivoting(self) -> None:
        rows_only = _table("    rows: [month]\n    columns: [region]\n")
        assert rows_only["rows"].required
        assert not rows_only["values"].required

    def test_neither_half_is_required_once_both_are_authored(self) -> None:
        both = _table(
            "    rows: [month]\n    values: [revenue]\n    columns: [region]\n"
        )
        assert not both["rows"].required
        assert not both["values"].required

    def test_nor_on_a_table_that_is_not_pivoting_at_all(self) -> None:
        """No `columns:`, no rule — a flat table clears its `rows:` freely."""
        assert not _table("    rows: [month]\n")["rows"].required

    def test_a_file_already_in_that_shape_keeps_the_control_that_undoes_it(
        self,
    ) -> None:
        """Hiding `columns` there would leave the panel unable to repair it.

        The gate exists to stop the panel *creating* the illegal shape. A board
        hand-authored into it was broken before any click, and the one edit that
        fixes it from the panel is clearing the `columns:` that caused it.
        """
        broken = _table("    columns: [region]\n")
        assert "columns" in broken
        assert not broken["columns"].required


def test_the_board_offers_no_control_a_design_edit_should_not_reach() -> None:
    """Four fields the panel could commit on a blur that design does not own.

    None is reachable by the parse gate: each produces a board that parses
    perfectly well, and the damage lands later or not at all.

    - `source` is the warehouse connection. A string parses, nothing in
      `normalize/` validates the name, and `resolve_source_config` raises
      `ERR-SOURCE-NOT-FOUND` at execute for every query on the board.
    - `auto_link` is a `dct serve` behavior flag.
    - `html_policy` is a security tier Cloud hard-pins below `trusted-raw`, so
      the higher value is written, read back as authored, and downgraded.
    - `id` is identity. A chart's is overwritten from the `charts:` map key
      and a nested board's is never read, so it is the same silent no-op that
      already excludes it on a chart.
    """
    board = _targets(
        "charts:\n"
        "  rev:\n"
        "    query:\n"
        "      sql: SELECT 1 AS m\n"
        "    type: kpi\n"
        "    value: m\n"
    )[""]
    for key in ("source", "auto_link", "html_policy", "id"):
        assert key not in _flat(board)


def test_a_layout_container_is_not_a_target() -> None:
    """`grid:` and `tabs:` are containers; only the items under them are targets.

    Load-bearing rather than incidental: `id` is suppressed globally, and
    `TabLayout.id` — which names a tab's URL param and is genuinely authorable
    — is only spared because the container it lives on is never offered. If a
    future walk ever made containers targets, that control would vanish with
    every other test still green.
    """
    grid = _targets(
        "grid:\n"
        "  columns: 2\n"
        "  items:\n"
        "    - item:\n"
        "        label: Left\n"
        "        type: kpi\n"
        "        query: q\n"
        "        value: v\n"
    )
    assert "grid" not in grid
    assert "grid.items.0" in grid

    tabs = _targets(
        "tabs:\n"
        "  id: view\n"
        "  items:\n"
        "    - title: One\n"
        "      rows:\n"
        "        - label: Inside\n"
        "          type: kpi\n"
        "          query: q\n"
        "          value: v\n"
    )
    assert "tabs" not in tabs
    assert "tabs.items.0" in tabs


def test_a_nested_style_group_is_reachable_from_a_chart_target() -> None:
    """The control the whole-board surface could not afford to emit.

    `_FLATTEN` expanded `style` and `frame` and dropped the other sixteen groups
    a chart carries, because the map built one of these for every object on the
    board. Per-path builds one, so font size is reachable — and the reason it
    was not is gone rather than worked around.

    Targets the board's kpi rather than its line chart: the Vega-Lite families
    have no per-chart card, so they no longer carry `style.font` at all. The
    nesting this test is about is the same either way.
    """
    style = build_design_target(BOARD, "rows.0.cols.1").children["style"]
    assert "size" in _flat(style.children["font"])


def test_a_board_target_terminates_despite_the_cyclic_model_graph() -> None:
    """`AuthoredBoard -> rows -> AuthoredBoard` is one of seven cycles.

    The property walk is bounded by not descending the five fields that hold
    child *targets*; this is the shape that hangs if that bound is wrong.
    """
    root = build_design_target(BOARD, "")
    assert root.model == "AuthoredBoard"
    assert "rows" not in root.children and "charts" not in root.children


def test_a_union_of_non_string_scalars_projects_to_no_control() -> None:
    """`style.axis.labels.bound` is `bool | float | None` — text is not its widget.

    Text was the fallback for anything that was not all-numeric and not all-bool,
    which put a free-text box on a field where neither arm accepts free text.
    The parse gate found it the moment full recursion made it reachable.
    """
    board = "rows:\n  - title: R\n    type: area\n    query: q\n    x: m\n    y: v\n"
    labels = build_design_target(board, "rows.0").children["style"].children["axis"]
    assert "bound" not in labels.children["labels"].properties


def test_a_group_that_cannot_be_created_one_control_at_a_time_is_not_offered() -> None:
    """`details:` needs `summary`, `multiples:` needs `rows` or `columns`.

    Writing an optional sibling into a group that does not exist yet mints an
    object the compiler refuses, and the panel that broke the board renders
    "does not parse" instead of a repair. The schema answers this itself
    (`AuthorableModel.empty_is_valid`), so a new validator of the same shape is
    excluded the day it lands rather than after a review round finds it.
    """
    board = "title: T\nrows:\n  - title: R\n    type: area\n    query: q\n    x: m\n    y: v\n"
    assert "details" not in build_design_target(board, "").children
    assert "multiples" not in build_design_target(board, "rows.0").children


def test_the_group_graph_is_acyclic_so_the_ancestor_guard_never_fires() -> None:
    """Recursion is bounded by `_design._CHILD_TARGETS`; `ancestors` is defense in depth.

    Seven cycles are reachable from `AuthoredBoard` and every one runs through a
    layout container, which the property walk does not descend. If that stops
    being true the guard keeps the walk finite — but it does so by *dropping* a
    group, silently, which is the failure this pins rather than a hang.
    """
    from dbt_charts.core.compile.schema.introspection import introspect

    schema = introspect()
    cycles: list[str] = []

    def walk(name: str, ancestors: tuple[str, ...]) -> None:
        model = schema.models.get(name)
        if model is None:
            return
        for field in model.fields:
            # Depth-scoped exactly as `_describe` scopes it: the exclusion sets
            # name authored board/chart fields and mean nothing deeper in.
            if not ancestors and (
                field.name in _design._NOT_DESIGN
                or field.name in _design._CHILD_TARGETS
            ):
                continue
            group = _design._drills_into(field)
            if group is None:
                continue
            if group in ancestors:
                cycles.append(" -> ".join((*ancestors, name, group)))
                continue
            walk(group, (*ancestors, name))

    union = schema.models["AuthoredChart"].union
    assert union is not None
    # `TabItem` and `Variable` are targets too (`tabs.items.0` and
    # `variables.<name>`), so `_describe` can be entered at either and both need
    # a root.
    for root in (
        "AuthoredBoard",
        "TabItem",
        "Variable",
        *sorted(union.variants.values()),
    ):
        walk(root, ())
    assert not cycles, f"a design group reaches one of its own ancestors: {cycles}"


def test_a_chart_path_resolves_to_itself() -> None:
    assert build_design_target(BOARD, "rows.0.cols.0").path == "rows.0.cols.0"


def test_a_leaf_inside_a_chart_resolves_to_the_chart() -> None:
    """Clicking a chart's title selects the chart — a leaf is not a target."""
    assert build_design_target(BOARD, "rows.0.cols.0.title").path == "rows.0.cols.0"


def test_a_bare_container_resolves_to_its_nearest_styled_ancestor() -> None:
    """`rows.0` carries no content of its own, so it is not a design target."""
    assert build_design_target(BOARD, "rows.0").path == ""


def test_an_unknown_path_falls_back_to_the_board() -> None:
    """The walk stops where the document does; the board terminates it."""
    for path in ("charts.nope.title", "rows.9.cols.0", "grid.items.0", "nonsense"):
        assert build_design_target(BOARD, path).path == ""


def test_a_scale_parameter_group_appears_only_under_its_own_scale_type() -> None:
    """`log.base` means nothing unless `type: log`, and saying so is the control.

    The parse gate cannot reach this one, but not because the value is quietly
    dropped: the board parses, and `merge_onto_base` then *raises* when the
    cascade merges the patch onto a non-log scale. A broken render, one step
    past the gate that would have caught it.
    """
    linear = (
        "rows:\n  - title: S\n    type: scatter\n    query: q\n    x: m\n    y: v\n"
    )
    log = (
        "rows:\n  - title: S\n    type: scatter\n    query: q\n    x: m\n    y: v\n"
        "    style:\n      axis_y:\n        scale:\n          continuous:\n            type: log\n"
    )
    for board, expected in ((linear, []), (log, ["log"])):
        scale = (
            build_design_target(board, "rows.0")
            .children["style"]
            .children["axis_y"]
            .children["scale"]
        )
        assert sorted(scale.children["continuous"].children) == expected


def test_every_color_control_the_panel_offers_says_it_is_a_color() -> None:
    """The tripwire, not the classifier — the facets themselves were reviewed.

    A description grep finds only some of the colors (it misses
    `ColorStylePatch.static` and `KpiTonesStyle.positive`) and it flags things
    that are not colors, so it cannot decide the set. What it *can* do is fail
    the day someone adds a field whose own description says "color" and forgets
    the facet, which is the whole drift this replaces a hand-kept list to avoid.
    """
    # Reviewed and genuinely not colors, though they say the word. Whole paths,
    # not leaf names: `color` as a leaf name matches `style.font.color` and 50
    # other real swatches, so a leaf-name allowlist would exempt the very fields
    # this exists to protect — including the one the facet work is named for.
    # One entry, because one is what the walked targets actually contain: a
    # geoshape's `value` ("Data column mapped to the fill color") is the same
    # trap, but this bar board never reaches it, and an allowlist entry that
    # cannot fire is a claim about coverage the test does not have.
    allowed = {"color"}  # the chart's data channel — a bare column name
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    missing = []
    for path in ("", "rows.0"):
        target = build_design_target(board, path)
        for key, prop in _flat(target).items():
            # Only a free-text control can be mistaken for a color; a checkbox
            # or a select whose description happens to mention color cannot.
            if prop.widget != "text" or key in allowed:
                continue
            if "color" in prop.description.lower() and "color" not in prop.facets:
                missing.append(f"{path or '<board>'}:{key} — {prop.description}")
    assert not missing, (
        "these controls describe themselves as colors but carry no `Color()` "
        "facet, so an editor cannot tell them from free text:\n  "
        + "\n  ".join(sorted(missing))
    )


def test_format_fields_offer_the_built_in_aliases() -> None:
    """`format: currency` is legal on every built-in theme; the schema must say so.

    Thirteen aliases are fixed at build time in `_base.yaml`. Typed as bare
    `str` they reach no consumer, and an author is left typing a d3 spec from
    memory into nine separate controls on one bar chart.
    """
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    formats = {
        key: prop
        for key, prop in _flat(build_design_target(board, "rows.0")).items()
        if key.split(".")[-1] in ("format", "number_format", "time_format")
    }
    assert formats, "the bar family carries format controls; the walk lost them"
    # `currency` on every control except `time_format`, which is the one slot
    # where currency is not an alias but a wrong render — it offers the time
    # half instead, and must still offer something.
    unlisted = [
        key
        for key, prop in formats.items()
        if ("date_short" if key.endswith("time_format") else "currency")
        not in (prop.enum_values or ())
    ]
    assert not unlisted, (
        "these format controls offer no alias, so the engine's names are"
        " undiscoverable:\n  " + "\n  ".join(sorted(unlisted))
    )


def test_a_kind_fixed_format_control_offers_only_its_own_half() -> None:
    """`time_format: currency` is a wrong render that looks right — don't offer it.

    The combo was the whole vocabulary on every format control, so the panel put
    `currency` and `percent` one click away from a temporal axis, where they
    resolve to a d3 *number* spec and Vega paints garbage ticks. Each of the two
    slots that knows its own kind offers that half and nothing else; the raw d3
    spec stays authorable because the widget is still a combo.
    """
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    props = _flat(build_design_target(board, "rows.0"))

    time_fmt = props["style.time_format"]
    assert set(time_fmt.enum_values or ()) == _TIME_ALIASES
    assert time_fmt.widget == "combo", "a strftime spec must stay authorable"

    number_fmt = props["style.number_format"]
    assert set(number_fmt.enum_values or ()) == _NUMBER_ALIASES
    assert "date_short" not in (number_fmt.enum_values or ()), (
        "the narrowing runs both ways"
    )


def test_a_kind_agnostic_format_control_still_offers_the_whole_vocabulary() -> None:
    """An axis or column `format:` is judged by its column, not by its name.

    The same field is a currency on one chart and a date on the next, so
    narrowing it would make one of the two unreachable from the panel.
    """
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    offered = set(
        _flat(build_design_target(board, "rows.0"))[
            "style.axis.labels.format"
        ].enum_values
        or ()
    )
    assert offered == _BUILT_IN_ALIASES


def test_a_board_s_own_aliases_reach_both_halves() -> None:
    """`style.formats` keys carry no kind, so the split must not filter them.

    The engine cannot know whether `arr` targets `$,.0f` or `%b %Y`; withholding
    it from the time combo would tell an author their own alias is not a value
    there, which is the bug the facet exists to prevent.
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "rows:\n"
        "  - title: R\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: m\n"
        "    y: v\n"
    )
    props = _flat(build_design_target(board, "rows.0"))
    assert "arr" in (props["style.time_format"].enum_values or ())
    assert "arr" in (props["style.number_format"].enum_values or ())


def test_an_open_enum_union_is_a_combo_not_a_select() -> None:
    """A `Literal | str` field suggests; it does not constrain.

    A raw d3 spec is legal on every format field, so a select would make one
    unauthorable through the panel. The distinction is the widget's job: a
    consumer that renders `enum_values` as the only choices is right for
    `select` and wrong here.
    """
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    props = _flat(build_design_target(board, "rows.0"))
    fmt = props["style.axis.labels.format"]
    assert fmt.widget == "combo"
    assert fmt.enum_values and "currency" in fmt.enum_values
    locked = [
        key
        for key, prop in props.items()
        if prop.widget == "select" and key.split(".")[-1] == "format"
    ]
    assert not locked, f"open format fields locked to their aliases: {locked}"


def test_a_board_s_own_format_aliases_are_offered_beside_the_built_in_ones() -> None:
    """`style.formats` is board-global, so its keys are legal on every chart.

    A board authoring `arr: "$,.0f"` can write `number_format: arr` on any chart
    in it — `formats` merges key-wise down the cascade — and a snapshot of the
    engine's own names can never contain it. Offering the built-ins alone tells
    the author their own alias is not a value.
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "rows:\n"
        "  - title: R\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: m\n"
        "    y: v\n"
    )
    props = _flat(build_design_target(board, "rows.0"))
    fmt = props["style.number_format"]
    assert fmt.enum_values is not None
    assert "arr" in fmt.enum_values, "the board's own alias is not offered"
    assert "currency" in fmt.enum_values, "the built-ins are still the shortcuts"
    assert fmt.widget == "combo", "a board alias does not close the set"
    unoffered = [
        key
        for key, prop in props.items()
        if "format" in prop.facets and "arr" not in (prop.enum_values or ())
    ]
    assert not unoffered, (
        "these format controls miss the board's own alias:\n  "
        + "\n  ".join(sorted(unoffered))
    )


def test_a_nested_scope_s_own_aliases_merge_onto_its_parent_s() -> None:
    """A tab that authors its own `style.formats` merges it onto the parent's.

    Both `arr` (the board's) and `bps` (the tab's own) resolve inside the tab
    — the compiler merges `formats` key-wise, not replaces it — so the panel
    must offer both.
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "tabs:\n"
        "  items:\n"
        "    - title: Detail\n"
        "      style:\n"
        "        formats:\n"
        '          bps: ".2%"\n'
        "      rows:\n"
        "        - title: R\n"
        "          type: bar\n"
        "          query: q\n"
        "          x: m\n"
        "          y: v\n"
    )
    props = _flat(build_design_target(board, "tabs.items.0.rows.0"))
    offered = props["style.number_format"].enum_values or ()
    assert "bps" in offered, "the tab's own alias is not offered"
    assert "arr" in offered, "the parent's alias, merged in, is not offered"
    assert set(offered) >= _NUMBER_ALIASES, "the engine's names resolve in every scope"


def test_a_rows_nested_board_scopes_its_aliases_the_same_way() -> None:
    """The shape a reader assumes is covered: a nested board inside `rows:`.

    `tabs:` and `rows:` reach the same scope rule by different hops, and the
    compiler pins its half of this
    (`tests/core/compile/validate/test_formats.py::
    test_a_nested_board_s_own_alias_merges_onto_the_root_s` — both `arr` and
    `bps` compile inside the nested board). This is the panel agreeing.
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "rows:\n"
        "  - title: Section\n"
        "    style:\n"
        "      formats:\n"
        '        bps: ".2%"\n'
        "    rows:\n"
        "      - title: R\n"
        "        type: bar\n"
        "        query: q\n"
        "        x: m\n"
        "        y: v\n"
    )
    offered = (
        _flat(build_design_target(board, "rows.0.rows.0"))[
            "style.number_format"
        ].enum_values
        or ()
    )
    assert "bps" in offered
    assert "arr" in offered


def test_a_scope_that_styles_without_touching_formats_inherits_the_parent_s() -> None:
    """Authoring `style:` without `formats:` leaves the parent's table intact.

    A tab with only a background resolves `formats` from the parent, unchanged
    — the compiler merges (an unset field falls through to the base), so the
    panel offers the board's `arr` inside it same as anywhere else.
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "tabs:\n"
        "  items:\n"
        "    - title: Detail\n"
        "      style:\n"
        '        background: "#fff"\n'
        "      rows:\n"
        "        - title: R\n"
        "          type: bar\n"
        "          query: q\n"
        "          x: m\n"
        "          y: v\n"
    )
    offered = (
        _flat(build_design_target(board, "tabs.items.0.rows.0"))[
            "style.number_format"
        ].enum_values
        or ()
    )
    assert set(offered) == _NUMBER_ALIASES | {"arr"}


def test_an_explicit_formats_null_clears_the_table_for_that_scope() -> None:
    """`style.formats: null` still clears — merging is only the unmentioned case.

    Distinct from the scope above: this tab explicitly nulls `formats`, so the
    parent's `arr` must not be offered inside it, matching the compiler
    (`tests/core/compile/validate/test_formats.py::
    test_tabs_nested_scope_explicit_formats_null_clears_the_table`).
    """
    board = (
        "style:\n"
        "  formats:\n"
        '    arr: "$,.0f"\n'
        "tabs:\n"
        "  items:\n"
        "    - title: Detail\n"
        "      style:\n"
        "        formats: null\n"
        '        background: "#fff"\n'
        "      rows:\n"
        "        - title: R\n"
        "          type: bar\n"
        "          query: q\n"
        "          x: m\n"
        "          y: v\n"
    )
    offered = (
        _flat(build_design_target(board, "tabs.items.0.rows.0"))[
            "style.number_format"
        ].enum_values
        or ()
    )
    assert set(offered) == _NUMBER_ALIASES


def test_a_board_with_no_aliases_of_its_own_offers_only_the_built_ins() -> None:
    """The engine's names are the whole vocabulary until a board adds to it."""
    board = "rows:\n  - title: R\n    type: bar\n    query: q\n    x: m\n    y: v\n"
    fmt = _flat(build_design_target(board, "rows.0"))["style.number_format"]
    assert fmt.enum_values is not None
    assert set(fmt.enum_values) == _NUMBER_ALIASES


def test_every_format_field_carries_the_format_facet() -> None:
    """The facet is what tells a consumer an alias belongs here.

    Without it the scope's aliases would have to be matched by field name or by
    comparing values against the built-in set — the hand-kept list the facets
    exist to retire, and the thing that drifts the day a format field is added.

    Swept over the schema IR rather than over a walked board: eight of the
    sixteen declarations take a `FormatConfig` arm, so `_widget_for` gives them
    no control and no board target would reach them — while a missing facet
    there is exactly as wrong, and lands the day one of them gains a control.
    """
    missing = []
    faceted = []
    for model_name, model in introspect().models.items():
        for field in model.fields:
            offered = set(field.enum_values or ())
            if not (offered >= _NUMBER_ALIASES or offered >= _TIME_ALIASES):
                continue
            name = f"{model_name}.{field.name}"
            (
                faceted if any(isinstance(f, Format) for f in field.facets) else missing
            ).append(name)
    assert not missing, (
        "these fields offer the built-in format aliases but carry no "
        "`Format()` facet, so a scope's own aliases cannot reach them:\n  "
        + "\n  ".join(sorted(missing))
    )
    # The sweep reaches past the widget-reachable set — a walked board cannot
    # see either of these, and an empty sweep would pass the assert above.
    assert {"TableColumnConfig.format", "LayerAxisYLabels.format"} <= set(faceted)


def test_a_multi_metric_y_is_offered_and_shows_both_series() -> None:
    """`y` is the most-used channel on a cartesian chart and had no control.

    Its type is `str | list[str] | None`; `_widget_for` required every union arm
    to be scalar, so it was dropped — while `x`, `x_label` and `y_label` stayed,
    which made the panel look like it had a `y`.

    Both halves matter. A widget rule alone renders the control *empty* on a
    chart that authors two series, because `_scalar` returns None for a list —
    a wrong result that looks right, and verbatim the failure the suppression
    was there to prevent.
    """
    board = (
        "rows:\n  - title: B\n    type: bar\n    query: q\n"
        "    x: month\n    y: [revenue, cost]\n"
    )
    y = _flat(build_design_target(board, "rows.0"))["y"]
    assert y.widget == "list"
    assert y.value == ["revenue", "cost"], (
        "the control renders empty on a chart that authors two series"
    )


def test_a_single_metric_y_still_reads_as_one_value() -> None:
    """The same field is a scalar most of the time; the control has to hold both
    shapes without turning `y: revenue` into `y: [revenue]` on the way back."""
    board = "rows:\n  - title: B\n    type: bar\n    query: q\n    x: month\n    y: revenue\n"
    y = _flat(build_design_target(board, "rows.0"))["y"]
    assert y.widget == "list"
    assert y.value == "revenue"


def test_a_data_channel_says_so_rather_than_the_panel_keeping_a_list() -> None:
    """`x` and `y` decide what the chart *says*; a font size decides how.

    An editor cannot tell them apart from type or description — both are
    `str | None` with prose — so every consumer that wants the distinction has
    had to keep its own list of channel names. That is what the facet
    vocabulary exists to retire.
    """
    board = (
        "rows:\n  - title: B\n    type: bar\n    query: q\n"
        "    x: month\n    y: revenue\n    color: region\n"
    )
    props = _flat(build_design_target(board, "rows.0"))
    for name in ("x", "y", "color"):
        assert "channel" in props[name].facets, f"{name} is a data channel"
    # Appearance, on the same object, at the same level.
    assert "channel" not in props["title"].facets
    assert "channel" not in props["style.background"].facets


# Every chart family, and the fields on it that name a column of its query.
# Marking three of them on the cartesian base is the hand-curated list the
# facet exists to retire, one level up: a KPI's only channel is `value`, and
# unmarked it sits among the fonts while the chart shows no data section at all.
_CHANNELS_BY_FAMILY = {
    "type: bar\n    query: q\n    x: month\n    y: revenue\n": {
        "x",
        "y",
        "y_start",
        "color",
        "support_table",
    },
    "type: line\n    query: q\n    x: month\n    y: revenue\n": {
        "x",
        "y",
        "color",
        "support_table",
    },
    "type: area\n    query: q\n    x: month\n    y: revenue\n": {
        "x",
        "y",
        "color",
        "support_table",
    },
    "type: heatmap\n    query: q\n    x: month\n    y: revenue\n": {"x", "y", "color"},
    "type: scatter\n    query: q\n    x: spend\n    y: revenue\n": {
        "x",
        "y",
        "color",
        "size",
        "shape",
    },
    "type: pie\n    query: q\n    theta: revenue\n": {"theta", "color"},
    "type: kpi\n    query: q\n    value: revenue\n": {"value"},
    "type: spark_bar\n    query: q\n    x: month\n    y: revenue\n": {"x", "y"},
    # Pivoting, because `columns` is not a control until the pivot declares one
    # half of its `rows`/`values` pair — see `TestThePivotsArityRule`.
    "type: table\n    query: q\n    rows: [month]\n": {"rows", "columns", "values"},
    "type: geoshape\n    query: q\n    geo: us_states\n    lookup: state\n"
    "    value: revenue\n": {"color", "lookup", "value"},
    "type: point_map\n    query: q\n    latitude: lat\n    longitude: lon\n": {
        "color",
        "lookup",
        "value",
        "latitude",
        "longitude",
        "size",
    },
}


@pytest.mark.parametrize(("chart", "expected"), _CHANNELS_BY_FAMILY.items())
def test_every_family_marks_the_fields_that_name_a_column(chart, expected) -> None:
    """A channel is a channel on every chart, not only the cartesian ones.

    The rule is "this field holds a column name of the chart's query" — so
    pie's `theta`, kpi's `value` and the maps' `latitude`/`longitude` are in,
    while `sort` and `multiples` (specs that *contain* a column — `ChartSort.by`
    names one, but `sort` itself holds a model) and `conditional_formatting`
    (keyed by column, holding style) are out. The facet marks the field the
    panel would offer a column control for; a nested spec has no such control
    at all, so marking its container would name a widget that does not exist —
    `chart.support_table` is the one deliberate exception, because
    `_support_table_property` gives it a real one (a flat `list` of the entries'
    column names) via a special-cased projection instead of the generic
    drill-in-to-a-group walk every other nested spec gets.
    """
    # No `title:` on the item: a KPI rejects one outright, and an unparseable
    # chart answers with an empty target rather than a failure.
    props = _flat(build_design_target(f"rows:\n  - {chart}", "rows.0"))
    marked = {name for name, prop in props.items() if "channel" in prop.facets}
    assert marked == expected


_VARIABLES_BOARD = (
    "variables:\n"
    "  region:\n"
    "    input: select\n"
    "    label: Region\n"
    "rows:\n"
    "  - label: Customers\n"
    "    type: kpi\n"
    "    query: q\n"
    "    value: customers\n"
)

# The same variable on the other branch of the dot-notation rule: it names the
# query its options come from, so the column is read from that query by bare name.
_QUERY_OPTIONS_BOARD = _VARIABLES_BOARD.replace(
    "    label: Region\n",
    "    label: Region\n    options:\n      query: q\n      column: region\n",
)


def test_a_variable_is_a_target_of_its_own() -> None:
    """The one authored object on a board that could not be selected at all.

    It is addressed exactly like a chart — a name under a dict — so the walk
    needs the same hop, and the panel then reaches `label`, `input` and
    `placeholder` without the code panel.
    """
    target = build_design_target(_VARIABLES_BOARD, "variables.region")

    assert target.model == "Variable"
    assert target.path == "variables.region"
    assert target.writes_at == "variables.region"
    props = _flat(target)
    assert props["label"].value == "Region"
    assert props["input"].enum_values is not None


def test_a_variable_offers_no_control_over_where_its_options_come_from() -> None:
    """`column` is the query layer reached one level down, and it commits.

    `normalize/variables.py:320` refuses a bare column name — the field wants
    `table.column` dot notation — but that rule runs at compile, past the save
    endpoint's re-parse. So typing the obvious thing into a text box described
    as *"Table column to draw option values from"* wrote a board that parses, is
    pushed to git, and raises `ERR-VALIDATION-FIELD` the next time anyone opens
    it. `source` already sits in `_design._NOT_DESIGN` for the same reason and lands
    softer: it fails at execute, this fails at compile, so nothing renders.

    `dimension` and `measure` are the semantic-layer spelling of the same
    reference, and `model:` — their third — was already excluded.
    `options.query` names a query and lands later still, as `ERR-INTERNAL` at
    render.
    """
    props = _flat(build_design_target(_VARIABLES_BOARD, "variables.region"))

    assert not [
        key
        for key in props
        if key.startswith("options.") or key in ("column", "dimension", "measure")
    ]
    assert "label" in props


def test_a_variables_option_column_returns_once_it_has_a_query_to_read() -> None:
    """The dot-notation rule the exclusion answers fires on one branch only.

    `normalize/variables.py:305-329` demands `table.column` of `options.column`
    *when the variable names no query of its own*; with `options.query`
    authored, a bare column name is exactly what the field wants and a wrong one
    leaves the option list empty rather than breaking the board. So the
    exclusion is guarded rather than blanket — withholding it on the branch
    where it commits, and leaving the real control standing on the branch where
    it does not.

    `options.query` stays out on both: it is the query layer either way.
    """
    props = _flat(build_design_target(_QUERY_OPTIONS_BOARD, "variables.region"))

    assert "options.column" in props
    assert "options.label_column" in props
    assert "options.query" not in props

    # The validator tests `not var.options.query`, so the guard has to as well:
    # an authored empty string is a key in the source map and no query at all.
    empty = _QUERY_OPTIONS_BOARD.replace(
        "query: q\n      column", 'query: ""\n      column'
    )
    assert "options.column" not in _flat(build_design_target(empty, "variables.region"))


def test_a_variables_input_select_offers_only_what_its_default_survives() -> None:
    """The one rule on this surface that is asked of the compiler, not restated.

    `input:` and `default:` are one decision authored as two fields, and the
    panel writes one at a time: on a variable defaulting to `A`, eight of the
    thirteen input types the schema lists make `validate_variable_value` raise —
    seven as `ERR-INTERNAL`, which `AGENTS.md` calls a defect in itself. The
    board still parses, so the save gate takes every one of them.

    Withholding `input` outright would answer that and cost the variable its
    primary control on every board that authors a default, which is most of
    them. The narrower answer is available here and nowhere else in this module:
    the compiler exposes the rule as a function, so the panel offers the values
    that function accepts and no copy of it can drift. A defaultless variable is
    the control's full range, which is the same answer from the same call.
    """
    with_default = _VARIABLES_BOARD.replace(
        "    label: Region\n", "    label: Region\n    default: A\n"
    )
    offered = _flat(build_design_target(with_default, "variables.region"))["input"]
    assert offered.enum_values is not None
    assert set(offered.enum_values) == {
        "auto",
        "select",
        "radio",
        "input",
        "text",
        "textarea",
    }

    full = _flat(build_design_target(_VARIABLES_BOARD, "variables.region"))["input"]
    assert full.enum_values is not None
    assert set(full.enum_values) > set(offered.enum_values)
    assert {"number", "checkbox", "daterange", "multiselect"} <= set(full.enum_values)


def test_a_variables_data_type_select_offers_only_what_its_options_survive() -> None:
    """`data_type` is the same shape of decision as `input`: it names what the
    options and default already are, and a value they cannot be is a control
    that only fails."""
    # No default, deliberately: the option list alone must carry the
    # rejection, which is the case only `validate_choice_type` reaches.
    words = _VARIABLES_BOARD.replace(
        "    label: Region\n", "    label: Region\n    options:\n      static: [A, B]\n"
    )
    offered = _flat(build_design_target(words, "variables.region"))["data_type"]
    assert offered.enum_values is not None
    assert set(offered.enum_values) == {"string", "array"}

    full = _flat(build_design_target(_VARIABLES_BOARD, "variables.region"))["data_type"]
    assert full.enum_values is not None
    assert {"number", "date", "boolean"} <= set(full.enum_values)


def test_a_variable_offers_required_only_once_it_has_a_default() -> None:
    """The one control whose single edit breaks the board past all repair.

    `required: true` with nothing to satisfy it raises
    `MissingRequiredVariablesError` before a query runs; `renderer.py` answers
    with a `board_error` and no variables strip, so nothing stamps
    `data-authored-path` and the checkbox that did it has no click target left.
    `variables_resolve.py` already names that state as one with "no control
    layer left to recover from".

    Absent is the renderer's own test rather than falsiness: `default: false` on
    a checkbox is a value, and reading it as absent would withhold a control
    that works. Both callers now share `variable_value_is_absent`.
    """
    assert "required" not in _flat(
        build_design_target(_VARIABLES_BOARD, "variables.region")
    )

    for default in ("A", "false", "0"):
        board = _VARIABLES_BOARD.replace(
            "    input: select\n", f"    input: checkbox\n    default: {default}\n"
        )
        assert "required" in _flat(build_design_target(board, "variables.region")), (
            default
        )

    blank = _VARIABLES_BOARD.replace(
        "    label: Region\n", '    label: Region\n    default: ""\n'
    )
    assert "required" not in _flat(build_design_target(blank, "variables.region"))


def test_a_bound_control_is_offered_only_where_it_cannot_strand_the_default() -> None:
    """The other half of the same two-fields-one-decision problem.

    `min:`/`max:` and `default:` are one decision, and only slider and range
    read them — so the same numeric bound is a live hazard on one input and
    inert YAML on every other. It fails the way `required` does rather than the
    way `input` does: the board stops compiling, Cloud renders the error with no
    SVG, and nothing stamps the path the panel would need to offer the undo.

    Not restated: `_variable_bound_is_safe` probes a value across the default and
    keeps whatever the compiler keeps. A bound is monotone, so one probe settles
    the whole domain.
    """
    slider = (
        "variables:\n"
        "  size:\n"
        "    input: slider\n"
        "    default: 5\n"
        "    min: 0\n"
        "    max: 10\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    )
    offered = _flat(build_design_target(slider, "variables.size"))
    assert "min" not in offered
    assert "max" not in offered
    # `step` is read by the same inputs and bounded by nothing, so it stays.
    assert "step" in offered

    # No default is nothing to strand — `validate_variable_value` returns on the
    # first line and the bounds are ordinary controls again.
    unbounded = _flat(
        build_design_target(slider.replace("    default: 5\n", ""), "variables.size")
    )
    assert "min" in unbounded
    assert "max" in unbounded

    # And an input that never reads a bound keeps them for the same reason: the
    # question asked is the compiler's, not the field name's.
    numeric = _flat(
        build_design_target(
            slider.replace("input: slider", "input: number"), "variables.size"
        )
    )
    assert "min" in numeric
    assert "max" in numeric

    # A bool default is a number to the validator — `bool` subclasses `int` and
    # the type table tests with `isinstance` — so the same crossing strands it.
    # This is the case a "not a real number" early-return would wave through,
    # and it is the one the probe exists to catch.
    boolean = _flat(
        build_design_target(
            slider.replace("default: 5", "default: true"), "variables.size"
        )
    )
    assert "min" not in boolean


def test_the_bound_guard_reads_the_input_the_compiler_will_resolve() -> None:
    """The hazard is reachable from a variable whose text never says `slider`.

    `input:` defaults to `auto`, and `dispatch.py` resolves it through
    `detect_variable_input_type` *before* it validates the default. That
    detector promotes an auto variable to `slider` the moment any one of
    `min`/`max`/`step` is set — so writing a bound is not "editing a slider's
    bound", it is *making the variable a slider* and then bounding it, in one
    edit. A probe that skips the resolution asks the validator about an `auto`
    variable, which it accepts unconditionally, and the guard passes everything.

    Two boards, and the second is why `step` cannot be exempted by name: with a
    string default, every one of the three fields breaks the board — not by
    crossing a bound, but by promoting the variable to an input its default was
    never valid for.
    """
    auto = (
        "variables:\n"
        "  size:\n"
        "    label: Size\n"
        "    default: 5\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    )
    numeric = _flat(build_design_target(auto, "variables.size"))
    assert "min" not in numeric
    assert "max" not in numeric
    # Nothing to strand in the other direction: a numeric default survives the
    # promotion, so the field that only promotes is still an ordinary control.
    assert "step" in numeric

    worded = _flat(
        build_design_target(
            auto.replace("default: 5", "default: alpha"), "variables.size"
        )
    )
    assert "min" not in worded
    assert "max" not in worded
    assert "step" not in worded


def test_a_boolean_default_keeps_the_numeric_inputs_off_its_list() -> None:
    """The validator and the coercer disagree, and only one of them is compile.

    `bool` subclasses `int`, so `validate_variable_value`'s type table takes a
    bool wherever it takes a number and `input: number` on a checkbox passes
    compile. `_coerce_number` — the runtime validator, on the render path —
    rejects a bool by name. So the board compiles, the panel offers the switch,
    and the render says `number value must be numeric, got a boolean`.

    That is repairable, unlike the bound: the board is gone but the *strip* is
    not, so the path is still stamped and the panel can put it back. It is a
    live violation of what the list sweep asserts all the same, and the fix is
    the one this surface keeps making — ask the other validator too, rather
    than write down which of the thirteen inputs it happens to refuse.
    """
    checkbox = (
        "variables:\n"
        "  flag:\n"
        "    input: checkbox\n"
        "    default: false\n"
        "rows:\n"
        "  - title: Area\n"
        "    type: area\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
    )
    offered = _flat(build_design_target(checkbox, "variables.flag"))["input"]
    assert offered.enum_values is not None
    assert not {"number", "slider", "range"} & set(offered.enum_values)
    # Still a real list, not an empty one — `text` takes a bool's string form
    # and `radio` takes any scalar, so the control is narrowed, not withheld.
    assert "checkbox" in offered.enum_values


def test_a_variable_is_not_offered_as_a_control_on_the_board() -> None:
    """A dict of variables is a place to walk into, not a value to type.

    `charts` works the same way — the board target lists neither, because
    clicking one selects it.
    """
    assert "variables" not in _flat(build_design_target(_VARIABLES_BOARD, ""))


def test_a_cross_file_variable_reference_resolves_to_no_target() -> None:
    """`region: shared.yml.variables.region` names a definition in another file.

    Descending into the ref would offer `ref` as a text box — an edit that
    repoints the reference rather than styling anything, and every real control
    the user came for is in the file the ref names. So the click resolves up to
    the board, the way an unwalked path already does.
    """
    board = (
        "variables:\n"
        "  region: shared.yml.variables.region\n"
        "rows:\n"
        "  - label: C\n    type: kpi\n    query: q\n    value: customers\n"
    )

    target = build_design_target(board, "variables.region")

    assert target.model == "AuthoredBoard"
    assert target.path == ""


def test_a_cross_file_chart_reference_resolves_to_no_target() -> None:
    """The same rule on the map that already had a hop, where it was latent.

    `charts:` has always been `dict[str, AuthoredChart | ChartRef]`, so a ref
    there resolved to a `ChartRef` target offering one control — `ref` — whose
    every edit repoints the chart. It went unnoticed because nothing else walked
    a union with a reference arm; adding `variables` makes it the shared rule.
    """
    board = "charts:\n  k: shared.yml.charts.k\nrows:\n  - charts.k\n"

    target = build_design_target(board, "charts.k")

    assert target.model == "AuthoredBoard"
    assert target.path == ""


class TestChannelColumnSuggestions:
    """Channel controls offer the query's statically-inferred output columns.

    Suggestions, not a vocabulary: the widget stays free-text (`combo`), a
    query whose projection is wholly unreadable (a bare `SELECT *`, an
    unparseable string, a cross-file ref) degrades to the plain control, a
    partly-readable one offers what it can name, and
    `options_complete` says whether the offer names every output — the one
    license a consumer needs before closing it into a choice.
    """

    @staticmethod
    def _chart(board: str, path: str = "charts.c") -> DesignTarget:
        return build_design_target(board, path)

    def test_an_inline_sql_query_fills_the_x_suggestions(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query:\n"
            "      sql: SELECT month, SUM(amount) AS revenue FROM sales GROUP BY month\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        prop = self._chart(board).properties["x"]
        assert prop.widget == "combo"
        assert prop.enum_values == ("month", "revenue")

    def test_a_named_board_query_resolves_to_its_columns(self) -> None:
        board = (
            "queries:\n"
            "  q: SELECT region, total FROM sales\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: q\n"
            "    x: region\n"
            "    y: total\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("region", "total")

    def test_a_queries_prefixed_ref_still_resolves_columns(self) -> None:
        """`query: queries.q` is legal chart-ref spelling — the normalizer
        strips the prefix before its lookup, and so does the suggestion
        resolver."""
        board = (
            "queries:\n"
            "  q: SELECT region, total FROM sales\n"
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query: queries.q\n"
            "    x: region\n"
            "    y: total\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("region", "total")

    def test_a_bare_sql_string_shorthand_is_parsed_directly(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("month", "revenue")

    def test_a_values_query_offers_its_row_keys(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query:\n"
            "      rows:\n"
            "        - {region: east, total: 5}\n"
            "    x: region\n"
            "    y: total\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("region", "total")

    def test_a_compact_values_query_offers_its_columns(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: bar\n"
            "    query:\n"
            "      columns: [region, total]\n"
            "      values: [[east, 5]]\n"
            "    x: region\n"
            "    y: total\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("region", "total")

    def test_select_star_degrades_to_the_plain_control(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query: SELECT * FROM t\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        prop = self._chart(board).properties["x"]
        assert prop.widget == "text"
        assert prop.enum_values is None

    def test_jinja_in_the_where_clause_does_not_block_inference(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query:\n"
            "      sql: |\n"
            "        SELECT month, revenue FROM t WHERE {{ filter(month) }}\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        assert self._chart(board).properties["x"].enum_values == ("month", "revenue")

    def _x(self, sql: str) -> DesignProperty:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query:\n"
            f"      sql: '{sql}'\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        return self._chart(board).properties["x"]

    def test_a_partial_offer_is_marked_incomplete(self) -> None:
        """A projection the skeleton cannot name still yields the nameable
        columns as suggestions, but `options_complete` stays False — a
        consumer that closed the offer into a choice would make the columns
        behind the star (or the unaliased aggregate) unbindable. Counted
        against `parsed.selects`: `named_selects` silently omits unnamed
        expressions, so its own length proves nothing."""
        for sql in (
            "SELECT month, {{ extra }} FROM t",
            "SELECT month, a.* FROM a",
            "SELECT month, sum(x) FROM t GROUP BY 1",
            "SELECT month, COUNT(*) FROM t GROUP BY 1",
            "SELECT month, region, SUM(revenue) FROM t GROUP BY 1, 2",
        ):
            prop = self._x(sql)
            assert "month" in (prop.enum_values or ()), sql
            assert not prop.options_complete, sql
            assert prop.widget == "combo", sql

    def test_a_jinja_control_block_is_marked_incomplete(self) -> None:
        """The skeleton emits only an `{% if %}`'s primary branch and runs a
        `{% for %}` body once, so the projection count proves nothing about
        the other branches — and a `{{ }}` inside an alias survives as a
        fabricated `__dct_jN__` name. None of these may close into a
        choice."""
        for sql in (
            "SELECT month{% if show_cost %}{% else %}, cost{% endif %} FROM t",
            "SELECT month{% if a %}, x{% elif b %}, y, z{% endif %} FROM t",
            "SELECT month{% for c in cols %}, sum(v) AS r_{{ c }}{% endfor %} FROM t",
            "SELECT month, sum(v) AS r_{{ c }} FROM t",
        ):
            assert not self._x(sql).options_complete, sql

    def test_a_fully_named_projection_is_marked_complete(self) -> None:
        prop = self._x("SELECT month, SUM(revenue) AS revenue FROM t GROUP BY 1")
        assert prop.enum_values == ("month", "revenue")
        assert prop.options_complete

    def test_heterogeneous_values_rows_are_marked_incomplete(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query:\n"
            "      rows:\n"
            "        - {month: 1, revenue: 2}\n"
            "        - {month: 2, revenue: 3, cost: 4}\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        prop = self._chart(board).properties["x"]
        assert prop.enum_values == ("month", "revenue")
        assert not prop.options_complete

    def test_a_multi_value_channel_keeps_its_list_widget_with_suggestions(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query: SELECT month, revenue, cost FROM t\n"
            "    x: month\n"
        )
        prop = self._chart(board).properties["y"]
        assert prop.widget == "list"
        assert prop.enum_values == ("month", "revenue", "cost")

    def test_a_nested_board_chart_reads_the_enclosing_queries(self) -> None:
        board = (
            "queries:\n"
            "  q: SELECT month, revenue FROM t\n"
            "rows:\n"
            "  - cols:\n"
            "      - title: Revenue\n"
            "        type: area\n"
            "        query: q\n"
            "        x: month\n"
            "        y: revenue\n"
        )
        prop = build_design_target(board, "rows.0.cols.0").properties["x"]
        assert prop.enum_values == ("month", "revenue")

    def test_an_unknown_query_name_degrades_to_the_plain_control(self) -> None:
        board = (
            "charts:\n"
            "  c:\n"
            "    type: line\n"
            "    query: not_defined\n"
            "    x: month\n"
            "    y: revenue\n"
        )
        prop = self._chart(board).properties["x"]
        assert prop.widget == "text"
        assert prop.enum_values is None


class TestGradientIsNotADesignSurface:
    """`style.color.gradient` is withheld everywhere: resolve requires its
    `palette` sibling, which projects to no control, and raises without a
    bound `color:` channel — so any single gradient edit writes a board that
    parses and stops rendering (found live: one exploration click). A table
    has no `color:` field at all, so that board would have no panel path back
    to validity."""

    def test_gradient_controls_are_withheld_without_a_channel(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n    x: month\n    y: revenue\n"
        )
        keys = _flat(build_design_target(board, "charts.c"))
        assert not any(key.startswith("style.color.gradient") for key in keys)

    def test_withheld_even_with_a_bound_channel(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n    color: region\n"
        )
        keys = _flat(build_design_target(board, "charts.c"))
        assert not any(key.startswith("style.color.gradient") for key in keys)

    def test_withheld_on_a_table_which_has_no_color_field(self) -> None:
        board = (
            "charts:\n  c:\n    type: table\n    query: q\n"
            "    rows: [region]\n    values: [total]\n"
        )
        keys = _flat(build_design_target(board, "charts.c"))
        assert not any(key.startswith("style.color.gradient") for key in keys)


class TestSupportTableDesignProperty:
    """chart.support_table surfaces as a `list` control — the panel's one path to
    attach or edit a strip, matching the scalar-listable feel `y:` already has.
    A strip the list widget cannot round-trip stays visible but read-only
    rather than being rewritten to bare strings on the next save."""

    @staticmethod
    def _chart(board: str, path: str = "charts.c") -> DesignTarget:
        return build_design_target(board, path)

    def test_unset_support_table_offers_an_empty_attachable_list(self) -> None:
        board = (
            "charts:\n  c:\n    type: line\n"
            "    query: SELECT month, revenue, cost FROM t\n"
            "    x: month\n    y: revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.value is None
        assert prop.readonly is False
        assert prop.list_only is True
        assert prop.enum_values == ("month", "revenue", "cost")

    def test_unset_support_table_offered_on_an_unstyled_bar(self) -> None:
        # `resolve_cartesian_x` decides bar orientation from the query's real
        # rows — unreachable from here — but both orientations attach a
        # support_table cleanly (rows above/below a vertical bar, columns
        # beside a horizontal one), so the ambiguity is no longer a reason to
        # withhold the control.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n    y: revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_unset_support_table_offered_on_a_bar_authored_vertical(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n    y: revenue\n"
            "    style:\n      orientation: vertical\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_unset_support_table_offered_on_a_layered_bar(self) -> None:
        # The overlay renderer always draws the base on y regardless of the
        # x column's type, so a layered bar is vertical without needing
        # style.orientation authored too — compile/resolve/chart/_channels.py::
        # _bar_orientation's second branch, purely static.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue, target FROM t\n"
            "    x: month\n    y: revenue\n"
            "    layers:\n      - type: line\n        y: target\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_unset_support_table_offered_on_a_time_bucketed_bar(self) -> None:
        # A bucketed x rides a temporal scale whatever its labels look like, so
        # `_bar_orientation` forces vertical on it before it ever samples the
        # data — its third branch, and as static as the `layers:` one above.
        # A chart-local `time_unit` is decidable here: it wins the axis cascade
        # outright, so the merged `time_unit` the resolver reads is this one.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n    y: revenue\n"
            "    style:\n      axis_x:\n        time_unit: yearmonth\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_an_explicit_horizontal_bar_offers_support_table_even_when_bucketed(
        self,
    ) -> None:
        # An explicit horizontal bar's category axis is vertical, so
        # support_table attaches as value columns beside the plot — offered
        # regardless of bucketing, which only matters to a vertical bar's
        # (unreachable) orientation inference.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n    y: revenue\n"
            "    style:\n      orientation: horizontal\n"
            "      axis_x:\n        time_unit: yearmonth\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_offered_unset_on_a_horizontal_layered_bar(self) -> None:
        # style.orientation wins outright over layers: — _bar_orientation
        # returns the authored value before it ever looks at layers:. That
        # no longer matters for support_table eligibility either way: an
        # authored `horizontal` attaches as columns just as cleanly with a
        # layer authored alongside it.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue, target FROM t\n"
            "    x: month\n    y: revenue\n"
            "    style:\n      orientation: horizontal\n"
            "    layers:\n      - type: line\n        y: target\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.readonly is False

    def test_support_table_withheld_on_a_bar_with_mirror(self) -> None:
        # A mirrored axis_y reserves its own chrome on both edges of the
        # category axis; if the query's real rows resolve the bar horizontal
        # (unreachable from here), that axis is the one the column block
        # sits beside, and the two reservations can together starve the plot
        # below the width floor on an otherwise ordinary card. This verb
        # cannot see that coming, so it withholds the control rather than
        # offering one that can break the next render.
        board = (
            "charts:\n  c:\n    type: bar\n"
            "    query: SELECT month, revenue FROM t\n"
            "    x: month\n    y: revenue\n"
            "    style:\n      axis_y:\n        mirror: true\n"
        )
        assert "support_table" not in self._chart(board).properties

    def test_bare_source_entries_round_trip_as_a_plain_string_list(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    support_table:\n      - revenue\n      - cost\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.widget == "list"
        assert prop.value == ["revenue", "cost"]
        assert prop.readonly is False

    def test_a_source_entry_carrying_a_format_is_read_only(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    support_table:\n"
            "      - source: revenue\n"
            '        format: "$,.0f"\n'
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True
        # Display-only, never resubmitted: a readonly row still names its
        # columns rather than rendering the generic "unset" placeholder a
        # None value would draw.
        assert prop.value == ["revenue"]

    def test_a_source_entry_carrying_a_label_is_read_only(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    support_table:\n"
            "      - source: revenue\n"
            "        label: Revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True

    def test_an_aggregate_entry_is_read_only(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    support_table:\n"
            "      - aggregate: sum\n"
            "        source: revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True

    def test_a_per_series_entry_is_read_only(self) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n    color: region\n"
            "    support_table:\n"
            "      - per_series: revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True
        assert prop.value == ["revenue"]

    def test_a_mixed_bare_and_rich_list_is_read_only(self) -> None:
        # One entry the widget could round-trip is not enough — a save from
        # this control would drop the aggregate row silently.
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    support_table:\n"
            "      - revenue\n"
            "      - aggregate: sum\n"
            "        source: revenue\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True

    def test_not_offered_on_a_chart_type_that_forbids_it(self) -> None:
        board = (
            "charts:\n  c:\n    type: heatmap\n    query: q\n"
            "    x: month\n    y: region\n"
        )
        assert "support_table" not in self._chart(board).properties

    def test_not_offered_unset_on_a_multi_metric_chart(self) -> None:
        # Attaching bare source entries here would require per_series/
        # by_measure entries the list widget cannot author, and the write
        # would fail chart.support_table's own multi-y rule on the next parse.
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: [revenue, cost]\n"
        )
        assert "support_table" not in self._chart(board).properties

    def test_not_offered_unset_on_a_faceted_chart(self) -> None:
        # render/chart/features/facet.py refuses support_table unconditionally
        # once a chart facets into panels — no query needed to know that.
        board = (
            "charts:\n  c:\n    type: line\n    query: q\n"
            "    x: month\n    y: revenue\n"
            "    multiples:\n      columns: region\n"
        )
        assert "support_table" not in self._chart(board).properties

    def test_not_offered_unset_without_an_x_encoding(self) -> None:
        # support_table_attachment.py requires an x-encoding to align strip
        # columns to; a single-metric line may legally omit x.
        board = "charts:\n  c:\n    type: line\n    query: q\n    y: revenue\n"
        assert "support_table" not in self._chart(board).properties

    def test_not_offered_unset_on_a_color_encoded_chart(self) -> None:
        # A color-encoded chart is long-format; the correct entry there is
        # per_series:, which this control cannot author. Static — withheld
        # regardless of whether the query happens to return one row per x.
        board = (
            "charts:\n  c:\n    type: line\n    query: q\n"
            "    x: month\n    y: revenue\n    color: region\n"
        )
        assert "support_table" not in self._chart(board).properties

    def test_still_read_only_on_a_multi_metric_chart_with_by_measure_entries(
        self,
    ) -> None:
        board = (
            "charts:\n  c:\n    type: bar\n    query: q\n"
            "    x: month\n    y: [revenue, cost]\n"
            "    support_table:\n"
            "      - per_series: revenue\n        by_measure: true\n"
            "      - per_series: cost\n        by_measure: true\n"
        )
        prop = self._chart(board).properties["support_table"]
        assert prop.readonly is True


class TestFontFamilySuggestions:
    """`font.family` is free text, but the wheel knows exactly which faces it
    ships — those are on offer as combo shortcuts, the way a format field
    offers its aliases. Free text stays: any CSS stack remains authorable."""

    def test_family_offers_the_shipped_faces_as_a_combo(self) -> None:
        # kpi, not bar: only the card-drawing families carry `style.font`.
        board = "charts:\n  c:\n    type: kpi\n    query: q\n    value: revenue\n"
        node = build_design_target(board, "charts.c")
        font = node.children["style"].children["font"]
        family = font.properties["family"]
        assert family.widget == "combo"
        assert family.enum_values
        assert "Inter Variable" in family.enum_values
        assert "Source Serif 4" in family.enum_values
        # Internal weight-alias faces are vl-convert plumbing, never an offer.
        assert not any("SemiBold" in str(v) for v in family.enum_values)


# ---------------------------------------------------------------------------
# Agreement tests: the constants that copy a compiler rule no sweep can reach.
#
# Six of `design.py`'s rule tables are already gated against the compiler, because
# the two sweeps above call the real validator and a stale table fails them. Five
# are not, each for a structural reason:
#
#   - `_design._ROOT_SUGAR` states a rule about a *pair* of keys, and the sweeps set one
#     key at a time, so nothing ever authors both halves.
#   - `_design._ROOT_ONLY` and `_design._NESTED_ONLY` name fields the compiler silently discards.
#     The board parses, renders, and is unchanged — there is no raise to observe.
#   - `_design._CONDITIONAL_GROUPS` and `_design._ALWAYS_REQUIRED` fire on controls the render
#     sweep skips: it is bounded to the `list` widget for cost.
#
# What follows asserts the compiler's half and the panel's half in the same test,
# so the two cannot drift apart quietly. A test that only read the panel would
# keep passing on the day the validator moved — which is the state this replaces.


def _render_output(board: str) -> str:
    """A real render's SVG, normalized so two renders of one board compare equal.

    Four tokens vary between renders in a single process, and `data-rendered-at`
    is the one that bites: it is stamped at *second* resolution, so a raw
    comparison passes whenever both renders land inside the same second and
    fails when a second boundary falls between them — reporting a stale constant
    against code that never changed. `_svg_normalize` owns the full list.
    """
    result = compile(board)
    assert result.success, (
        f"fixture does not compile: {[d.code for d in result.errors]}"
    )
    compiled = result.board
    assert compiled is not None
    executor = Executor(
        compiled,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    svg = render(compiled, executor, format="svg").output
    # `output` is `str | bytes | None` because a png render returns bytes.
    # Asserting beats narrowing silently: a None here would compare equal to
    # itself and pass every assertion below without rendering anything.
    assert isinstance(svg, str), "an svg render returns text"
    return normalize_same_run_svg(svg)


_AGREEMENT_QUERY = (
    "queries:\n"
    "  q:\n"
    "    type: values\n"
    "    rows:\n"
    "      - {m: 1, v: 1}\n"
    "      - {m: 2, v: 2}\n"
)
# A nested board — a layout item carrying `rows:` of its own — is the scope the
# root-only and nested-only rules are about. It holds a chart because a
# text-only nested board is content-sized and never consults a slot height.
_NESTED = _AGREEMENT_QUERY + (
    "rows:\n"
    "  - title: R\n"
    "    rows:\n"
    "      - title: S\n"
    "        type: bar\n"
    "        query: q\n"
    "        x: m\n"
    "        y: v\n"
)
_ROOT = _AGREEMENT_QUERY + (
    "rows:\n  - title: A\n    type: bar\n    query: q\n    x: m\n    y: v\n"
)


def test_the_width_pair_the_panel_splits_is_the_pair_the_compiler_refuses() -> None:
    """`_design._ROOT_SUGAR`, asked of the compiler rather than restated.

    The sweeps cannot reach this one: they set a single key per edit, and the
    rule is about authoring *both*. Worse, the raise is `_root_width_style_patch`'s
    at normalize — the save endpoint only re-parses, so the panel's own gate is
    blind to it, and a `_PARSE_FIXTURES` entry does not catch it either.

    Three claims, and the constant is only correct while all three hold: the pair
    is refused, `width:` really is sugar for the canonical key, and the panel
    emits one control rather than two.
    """
    both = compile("width: 900\nstyle:\n  frame:\n    width: 800\nrows: []\n")
    assert not both.success, "the compiler no longer refuses the width pair"

    sugar = compile("width: 900\nrows: []\n")
    assert sugar.success and sugar.board is not None
    assert sugar.board.resolved_style.frame.width == 900.0, (
        "`width:` no longer desugars onto `style.frame.width`"
    )

    offered = _flat(_targets("width: 900\nrows: []\n")[""])
    assert ("width" in offered) != ("style.frame.width" in offered), (
        "the panel must offer exactly one half of a pair the compiler refuses"
    )


def test_card_gap_is_read_at_the_root_and_refused_on_a_nested_board() -> None:
    """`_design._ROOT_ONLY`'s loud half — the original defect, and the cheapest to reach,
    since a checkbox commits on its first tick."""
    assert not compile(_NESTED + "    card_gap: true\n").success, (
        "a nested `card_gap` no longer refuses to compile"
    )

    root = compile("card_gap: true\n" + _ROOT)
    assert root.success and root.board is not None
    assert root.board.card_gap is True, "the root board no longer reads `card_gap`"

    assert "card_gap" not in _flat(_targets(_NESTED)["rows.0"])
    assert "card_gap" in _flat(_targets(_ROOT)[""])


def test_a_nested_boards_frame_changes_nothing_the_reader_can_see() -> None:
    """`_design._ROOT_ONLY`'s quiet half, and the reason it needed a constant at all.

    A nested board's `FrameStyle` reaches the compiled model intact — it is the
    *renderer* that ignores it, because a nested board renders into the parent's
    grid. So the save succeeds, the value is stored, the picture is identical,
    and the panel reads the value back as authored. No raise, nothing for either
    sweep to observe, and a model-level assertion would miss it too.
    """
    assert _render_output(_NESTED) == _render_output(
        _NESTED + "    style:\n      frame:\n        width: 777\n"
    ), "a nested board's frame now reaches the render — `_design._ROOT_ONLY` is stale"

    root_default = compile(_ROOT)
    root_framed = compile("style:\n  frame:\n    width: 1600\n" + _ROOT)
    assert root_default.board is not None and root_framed.board is not None
    assert (
        root_default.board.resolved_style.frame.width
        != root_framed.board.resolved_style.frame.width
    ), "the root board no longer reads its own frame"

    assert "style.frame.width" not in _flat(_targets(_NESTED)["rows.0"])


def test_height_is_read_on_a_nested_board_and_discarded_at_the_root() -> None:
    """`_design._NESTED_ONLY`, both directions.

    The mirror is what makes this worth a test rather than a comment: hiding
    `height` at the root is only right while the root really does discard it, and
    offering it when nested is only right while the layout item really reads it.
    """
    nested = compile(_NESTED + "    height: 400\n")
    assert nested.success and nested.board is not None
    assert nested.board.layout.items[0].layout_height == "400", (
        "a nested board's `height` no longer reaches its layout slot"
    )

    assert _render_output(_ROOT) == _render_output("height: 400\n" + _ROOT), (
        "the root board now honors `height` — `_design._NESTED_ONLY` is stale"
    )

    assert "height" not in _flat(_targets(_ROOT)[""])
    assert "height" in _flat(_targets(_NESTED)["rows.0"])


def test_a_scale_parameter_block_is_refused_without_its_own_scale_type() -> None:
    """`_design._CONDITIONAL_GROUPS`, and the render sweep cannot see it twice over.

    `log.base` draws a *number*, and the sweep is bounded to the `list` widget;
    and the raise is `_validate_log_pow_symlog_params`' on the merged style, so
    the board parses either way. A line chart, not a bar: bar refuses a log scale
    outright (`ERR-BAR-LOG-SCALE-NOT-SUPPORTED`) and would pass this test for the
    wrong reason.
    """
    line = "    type: line\n    query: q\n    x: m\n    y: v\n"

    def board(scale_type: str) -> str:
        declared = f"            type: {scale_type}\n" if scale_type else ""
        return (
            _AGREEMENT_QUERY
            + "rows:\n  - title: A\n"
            + line
            + "    style:\n      axis_y:\n        scale:\n          continuous:\n"
            + declared
            + "            log:\n              base: 2\n"
        )

    assert _render_failures(board("linear")), (
        "a `log.base` under a non-log scale type no longer fails"
    )
    assert _render_failures(board("")), (
        "a `log.base` with no scale type at all no longer fails"
    )
    assert not _render_failures(board("log")), (
        "a `log.base` under `type: log` must still render"
    )

    # The panel's half, so the two cannot drift apart in this file either.
    props = _flat(_targets(board("linear"))["rows.0"])
    assert not any(key.startswith("style.axis_y.scale.continuous.log") for key in props)


def test_a_histogram_that_loses_its_x_stops_rendering() -> None:
    """`_design._ALWAYS_REQUIRED`. `required=False` is what draws the clear, and the clear
    is the one edit a histogram cannot survive — the board still parses, so the
    save endpoint waves it through."""
    histogram = _AGREEMENT_QUERY + "rows:\n  - title: H\n    type: histogram\n"
    assert _render_failures(histogram + "    query: q\n    y: v\n"), (
        "a histogram with no `x` no longer fails to render"
    )
    assert not _render_failures(histogram + "    query: q\n    x: v\n"), (
        "a histogram with an `x` must still render"
    )

    # The panel's half: `required` is what withholds the clear.
    props = _flat(_targets(histogram + "    query: q\n    y: v\n")["rows.0"])
    assert props["x"].required is True


def test_a_bar_that_loses_its_y_stops_rendering() -> None:
    """`_design._REQUIRED_WITH`. A bar's `y` is a list control beside `color:`
    (the two compose), so clearing it is an edit the sweeps reach — and
    `BarChart` refuses an `x` with no measure at parse, so `required` must
    withhold the clear exactly while an `x` stands: with no `x`, a y-less bar
    parses and renders and the clear stays on offer."""
    bar = _AGREEMENT_QUERY + "rows:\n  - title: B\n    type: bar\n    query: q\n"
    assert _render_failures(bar + "    x: v\n    color: v\n"), (
        "a bar with an `x` and no `y` no longer fails"
    )
    assert not _render_failures(bar + "    x: v\n    y: v\n"), (
        "a bar with a `y` must still render"
    )
    assert not _render_failures(bar), "a bar with neither `x` nor `y` must render"

    props = _flat(_targets(bar + "    x: v\n    y: v\n")["rows.0"])
    assert props["y"].required is True
    props = _flat(_targets(bar + "    y: v\n")["rows.0"])
    assert props["y"].required is False


def test_every_rule_table_names_a_field_that_still_exists() -> None:
    """The staleness no sweep and no agreement test can see.

    Every table above names its models and fields as bare strings. Rename a
    field and the entry does not fail — it stops matching, silently, and the rule
    it encoded quietly stops being enforced. The panel then offers a control the
    compiler still refuses, which is the original defect with the guard removed.

    This gates spelling, not behavior: it says the names resolve, never that the
    rule is right. The agreement tests above are what say that.

    Model keys resolve two ways on purpose — `_design._BY_DISCRIMINATOR`'s reason. Most
    tables are filed by model name (`AreaChart`), bar's by the authored `type:`
    (`bar`), and `_design._CONDITIONAL_GROUPS` by the compiled style model, which arrives
    as both `ScaleContinuousStyle` and `...Patch` depending on where it sits.
    """
    schema = introspect()
    union = schema.models["AuthoredChart"].union
    assert union is not None
    variants = dict(union.variants)

    def model_named(key: str) -> str | None:
        for candidate in (key, variants.get(key, ""), f"{key}Patch"):
            if candidate in schema.models:
                return candidate
        return None

    def resolves(model: str, dotted: str) -> bool:
        current = model
        for segment in dotted.split("."):
            owner = schema.models.get(current)
            if owner is None:
                return False
            field = next((f for f in owner.fields if f.name == segment), None)
            if field is None:
                return False
            current = _design._drills_into(field) or ""
        return True

    stale: list[str] = []

    def check(table: str, key: str, *dotted: str) -> None:
        model = model_named(key)
        if model is None:
            stale.append(f"{table}: model {key!r} names nothing in the schema")
            return
        stale.extend(
            f"{table}: {key}.{d} names nothing in the schema"
            for d in dotted
            if not resolves(model, d)
        )

    for key, (field, siblings) in _design._LIST_CONFLICTS.items():
        check("_design._LIST_CONFLICTS", key, field, *siblings)
    for key, rules in _design._VALUE_CONFLICTS.items():
        for list_field, value_key, _ in rules:
            check("_design._VALUE_CONFLICTS", key, list_field, value_key)
    for key, fields in _design._NEVER_A_LIST.items():
        check("_design._NEVER_A_LIST", key, *fields)
    for key, (pair, gated) in _design._PIVOT_ARITY.items():
        check("_design._PIVOT_ARITY", key, *pair, *gated)
    for key, groups in _design._CONDITIONAL_GROUPS.items():
        for group, (sibling, _) in groups.items():
            check("_design._CONDITIONAL_GROUPS", key, group, sibling)
    for key, required in _design._REQUIRED_WITH_LIST.items():
        for field, guard in required.items():
            check("_design._REQUIRED_WITH_LIST", key, field, guard)
    for key, fields in _design._ALWAYS_REQUIRED.items():
        check("_design._ALWAYS_REQUIRED", key, *fields)
    for key, required in _design._REQUIRED_WITH.items():
        for field, siblings in required.items():
            check("_design._REQUIRED_WITH", key, field, *siblings)
    for key, rules in _design._NOT_DESIGN_ON.items():
        for dotted, guard in rules:
            check("_design._NOT_DESIGN_ON", key, dotted, *((guard,) if guard else ()))
    for key in _design._BY_DISCRIMINATOR:
        check("_design._BY_DISCRIMINATOR", key)

    for dotted in (
        *_design._ROOT_ONLY,
        *_design._NESTED_ONLY,
        *_design._CONTENT_FIELDS,
        *_design._CHILD_TARGETS,
    ):
        check("board-scoped tables", "AuthoredBoard", dotted)
    for sugar, canonical in _design._ROOT_SUGAR:
        check("_design._ROOT_SUGAR", "AuthoredBoard", sugar, canonical)

    # `_design._NOT_DESIGN` is by field name across every authored model rather than
    # scoped to one, so the question is only whether some model still has it.
    every_field = {f.name for model in schema.models.values() for f in model.fields}
    stale.extend(
        f"_design._NOT_DESIGN: {name!r} names nothing in the schema"
        for name in sorted(_design._NOT_DESIGN)
        if name not in every_field
    )

    assert not stale, "\n".join(stale)


class TestOneParsePerBuild:
    """`build_design` composes the board once. The pydantic model, the source
    map and the mapping Cloud reads the written `type:` from all come off that
    one node tree — parsing a board is expensive enough that nothing here may
    scan it twice."""

    def test_the_board_is_scanned_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        scans = 0
        loads = 0
        real = yaml.CSafeLoader.get_single_node

        def spy(loader: yaml.CSafeLoader) -> yaml.Node | None:
            nonlocal scans
            scans += 1
            return real(loader)

        def spy_load(loader: yaml.constructor.BaseConstructor) -> object:
            nonlocal loads
            loads += 1
            return None

        # Every board loader here derives from the pinned CSafeLoader
        # (dbt_charts.core.utils.YAML_LOADER), so the spy sees the compose
        # behind the model and the one behind the source map alike;
        # `get_single_data` is `yaml.load`'s scan-and-construct entry, which
        # the one composed node feeding `construct_document` never enters.
        monkeypatch.setattr(yaml.CSafeLoader, "get_single_node", spy)
        monkeypatch.setattr(
            yaml.constructor.BaseConstructor, "get_single_data", spy_load
        )
        build_design(BOARD, "rows.0")
        assert (scans, loads) == (1, 0)

    def test_a_duplicate_key_still_raises_with_its_line(self) -> None:
        board = "title: T\nrows: []\ntitle: U\n"
        with pytest.raises(ParseError, match="duplicate key") as info:
            build_design(board, "")
        assert info.value.line == 3

    def test_merge_keys_are_marked_as_written_not_as_constructed(self) -> None:
        """Construction flattens `<<: *anchor` into the node tree in place; the
        source map is read before that, so a merged-in field is inherited,
        not authored, exactly as the string form reports it."""
        board = (
            "queries:\n  q: select 1\n"
            "charts:\n"
            "  a: &base\n    type: line\n    query: q\n    x: day\n    y: n\n"
            "  b:\n    <<: *base\n    title: B\n"
            "rows:\n  - a\n  - b\n"
        )
        target, mapping = build_design(board, "charts.b")
        assert target.properties["title"].authored_here
        assert not target.properties["x"].authored_here
        assert mapping["charts"]["b"]["x"] == "day"
        authored = frozenset(build_source_index(board, "<test>").source_map)
        assert "charts.b.<<" in authored and "charts.b.x" not in authored

    def test_the_mapping_is_the_board_as_written(self) -> None:
        target, authored = build_design(BOARD, "rows.0.cols.0")
        assert target.path == "rows.0.cols.0"
        assert authored == load_yaml_mapping(BOARD)
