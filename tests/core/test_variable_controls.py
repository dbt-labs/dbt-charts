"""Tests for the drawn variable-control strip and its reserved band."""

import dataclasses

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.variable.authored import (
    Variable,
    VariableOptions,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.controls import (
    controls_runtime_source,
)
from dbt_charts.core.render.sizing import compute_variable_controls_height

from ._control_utils import render_strip_for


def _rs():
    return resolve_style(get_theme_style())


class TestVariableControlsContainerHeight:
    """The reserved band must expand when controls wrap to multiple rows."""

    def test_height_grows_when_controls_wrap(self):
        """With many text inputs at a narrow width, the controls wrap to multiple
        rows, and the band the board reserves for them grows to match."""
        variables = {
            f"field_{i}": Variable(input="text", label=f"Label {i}") for i in range(5)
        }
        rs = _rs()
        height = compute_variable_controls_height(variables, 200.0, {}, rs.variables)
        assert height > 50, f"Expected height > 50 for wrapping controls, got {height}"

    def test_height_stable_for_single_control(self):
        """A lone control always occupies one row regardless of available width."""
        variable = {"v": Variable(input="text", label="A")}
        rs = _rs()
        height_wide = compute_variable_controls_height(
            variable, 2000.0, {}, rs.variables
        )
        height_narrow = compute_variable_controls_height(
            variable, 50.0, {}, rs.variables
        )
        assert height_wide == height_narrow


class TestVariablesStripBackground:
    """The variables strip is transparent chrome — it must not paint a background
    band. Regression: on a dark theme the strip previously used the input
    background (neon #222222) as its own bg, rendering a visible band over the
    #161616 canvas (invisible only on themes where input bg happened to equal the
    canvas). The strip is now transparent; only the inputs carry a background.
    """

    def test_svg_native_strip_has_no_leading_background_rect(self):
        """The non-interactive SVG strip must not prepend a full-width background
        rect (the removed band). Inputs may still fill their own cells."""
        rs = resolve_style(get_theme_style("neon"))
        variables = {"region": Variable(input="select", label="Region")}
        svg, _ = render_strip_for(
            variables, {}, 800.0, None, rs, variables_path="variables"
        )
        assert not svg.lstrip().startswith('<rect x="0" y="0"'), (
            "SVG variables strip still prepends a full-width background rect"
        )


class TestControlsRuntimeSource:
    """The runtime a host ships beside the board."""

    def test_runtime_smoke_covers_removed_injection_and_svg_scope(self):
        """Runtime smoke test should cover the removed injection path."""
        script = controls_runtime_source()

        assert "vars[name] = value;" in script
        assert "variableTypes" not in script
        assert (
            "if (!link.ownerSVGElement && link.namespaceURI !== "
            "'http://www.w3.org/2000/svg') return;"
        ) in script
        assert "if (!params.toString()) {" in script

    def test_runtime_bundles_the_table_runtime_ahead_of_the_controls(self):
        """mount() hands every table to window.dbtTableRuntime, so it must exist first."""
        script = controls_runtime_source()

        assert script.index("window.dbtTableRuntime = ") < script.index(
            "window.dbtTableRuntime.mount(scope)"
        )
        assert "/*{#" not in script


class _MockExecutor:
    """Minimal executor duck-type for disabled-state tests."""

    def __init__(self, responses: dict[str, list[dict]]):
        self._responses = responses

    def execute_query(self, query_name: str, variables: dict) -> list[dict]:
        if query_name not in self._responses:
            raise ValueError(f"Query '{query_name}' not found")
        return self._responses[query_name]


class TestDisabledVariableControls:
    """Tests for the enabled field on variable controls (enabled=False means disabled)."""

    def test_disabled_jinja_deps_added_to_variable_dependencies(self):
        """Variables referenced in enabled expressions appear in variable_dependencies."""
        from dbt_charts.core.compile.normalize.variables import (
            compute_variable_dependencies,
        )

        variables = {
            "starter_pattern": Variable(
                input="select", options=VariableOptions(static=["Random"])
            ),
            "initial_live_cells": Variable(
                input="text",
                enabled="{{ starter_pattern == 'Random' }}",
            ),
        }
        compute_variable_dependencies(variables, {})
        assert (
            "starter_pattern" in variables["initial_live_cells"].variable_dependencies
        )

    def test_a_disabled_control_is_drawn_dimmed(self):
        """A gated-off control reads as gated-off in the picture, not just in an attribute.

        This used to grep the stylesheet for `.variable-control[data-disabled]`,
        a class the deleted HTML layer emitted — so it kept passing against a
        rule nothing could match. The dimming now rides on the drawn group, and
        an artifact carries it with no runtime at all, so assert the render.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        from ._control_utils import render_strip_for

        rs = resolve_style(get_theme_style())
        variables = {"region": Variable(input="text", enabled=False)}

        svg, _ = render_strip_for(
            variables, {}, 800.0, _MockExecutor({}), rs, variables_path="variables"
        )

        assert 'data-dbt-enabled="false"' in svg
        assert 'opacity="0.55"' in svg


class TestVariablesLabelValueSubstyles:
    """label/value substyles cascade and render correctly."""

    def _modified_rs(self, **label_font_kwargs):
        """Return a ResolvedStyle with variables.label.font overrides applied."""
        base = resolve_style(get_theme_style())
        new_label_font = base.variables.label.font.model_copy(update=label_font_kwargs)
        new_label = base.variables.label.model_copy(update={"font": new_label_font})
        new_variables = base.variables.model_copy(update={"label": new_label})
        return dataclasses.replace(base, variables=new_variables)

    def test_label_font_family_propagates_in_readonly_svg(self):
        """variables.label.font.family must appear in the read-only SVG tspan."""
        rs = self._modified_rs(family="Comic Sans MS")
        svg, _ = render_strip_for(
            {"x": Variable(input="text", default="hello")},
            {},
            800.0,
            None,
            rs,
            variables_path="variables",
        )
        assert "Comic Sans MS" in svg

    def test_label_font_weight_in_readonly_tspan(self):
        """Read-only tspan must not hardcode weight=500; it reads variables.label.font.weight."""
        rs = self._modified_rs(weight="700")
        svg, _ = render_strip_for(
            {"x": Variable(input="text", default="world")},
            {},
            800.0,
            None,
            rs,
            variables_path="variables",
        )
        assert 'font-weight="700"' in svg

    def test_label_font_weight_default_no_fractional(self):
        """Default theme must emit font-weight="500" not "500.0" in read-only tspan."""
        rs = resolve_style(get_theme_style())
        svg, _ = render_strip_for(
            {"x": Variable(input="text", default="world")},
            {},
            800.0,
            None,
            rs,
            variables_path="variables",
        )
        assert 'font-weight="500"' in svg
        assert 'font-weight="500.0"' not in svg

    def test_label_color_cascades_from_variables_font(self):
        """variables.label.font.color must cascade from variables.font.color via inherit resolver."""
        from dbt_charts.core.compile.resolve.style.inherit_graph import (
            get_inherit_graph,
        )
        from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit

        compiled = get_theme_style()
        sentinel = "#bada55"
        new_variables_font = compiled.variables.font.model_copy(
            update={"color": sentinel}
        )
        patched = compiled.model_copy(
            update={
                "variables": compiled.variables.model_copy(
                    update={"font": new_variables_font}
                )
            }
        )
        cascaded = apply_inherit(patched, get_inherit_graph())
        assert cascaded.variables.label.font.color == sentinel

    def test_value_color_cascades_from_variables_font(self):
        """variables.value.font.color must cascade from variables.font.color.

        Regression: hardcoding value.font.color in the base theme blocks the
        cascade and pins value text to a near-black hex on dark themes that
        override variables.font.color to a light value.
        """
        from dbt_charts.core.compile.resolve.style.inherit_graph import (
            get_inherit_graph,
        )
        from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit

        compiled = get_theme_style()
        sentinel = "#eaeaea"
        new_variables_font = compiled.variables.font.model_copy(
            update={"color": sentinel}
        )
        patched = compiled.model_copy(
            update={
                "variables": compiled.variables.model_copy(
                    update={"font": new_variables_font}
                )
            }
        )
        cascaded = apply_inherit(patched, get_inherit_graph())
        assert cascaded.variables.value.font.color == sentinel


class TestVariablesPlaceholderSubstyle:
    """Placeholder substyle: unselected variable text reads lighter than selected."""

    def test_placeholder_field_present_on_compiled_theme(self):
        """VariablesStyle.placeholder must exist and have a font field."""
        compiled = get_theme_style()
        assert compiled.variables.placeholder.font is not None

    def test_editorial_placeholder_resolves_to_disabled_gray(self):
        """editorial theme: placeholder color resolves to dbt-grays.6 (#B0B2B8)."""
        rs = resolve_style(get_theme_style())
        assert rs.variables.placeholder.font.color is not None
        assert rs.variables.placeholder.font.color.lower() == "#b0b2b8"

    def test_editorial_cream_placeholder_resolves_to_disabled_cream(self):
        """cream: placeholder color resolves to dbt-creams.6 (#B2A691)."""
        rs = resolve_style(get_theme_style("paper"))
        assert rs.variables.placeholder.font.color is not None
        assert rs.variables.placeholder.font.color.lower() == "#b2a691"

    def test_placeholder_family_cascades_from_variables_font(self):
        """variables.placeholder.font.family must cascade from variables.font.family.

        Unlike color (set explicitly on placeholder so it reads lighter than the
        value text), unset font fields like family must inherit through the same
        cascade as label and value.
        """
        from dbt_charts.core.compile.resolve.style.inherit_graph import (
            get_inherit_graph,
        )
        from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit

        compiled = get_theme_style()
        sentinel_family = "Sentinel Sans"
        new_variables_font = compiled.variables.font.model_copy(
            update={"family": sentinel_family}
        )
        patched = compiled.model_copy(
            update={
                "variables": compiled.variables.model_copy(
                    update={"font": new_variables_font}
                )
            }
        )
        cascaded = apply_inherit(patched, get_inherit_graph())
        assert cascaded.variables.placeholder.font.family == sentinel_family


def test_jinja_filter_macro_dependency_extraction() -> None:
    """A {{ filter('col', var) }} call in SQL registers the variable dependency."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.models.query.normalized import is_sql_query

    result = compile(
        """
title: Test
variables:
  region:
    input: text
queries:
  sales:
    sql: |
      SELECT * FROM orders
      WHERE {{ filter('region', region) }}
    source: test
charts:
  c:
    query: sales
    type: table
rows:
  - c
"""
    )
    assert result.success
    query = result.board.queries["sales"]
    assert is_sql_query(query)
    assert "region" in query.variable_dependencies


def test_variable_dependencies_flow_through_composed_queries() -> None:
    """Queries, charts and cache keys depend on variables read by embedded queries."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute.duckdb_cache import compute_relevant_variables

    result = compile(
        """
title: Test
source: test
variables:
  show_recent:
    input: checkbox
    default: true
queries:
  base:
    sql: |
      select 1 as day, 10 as n, false as is_recent
      where true {% if not show_recent %}and 1 = 1{% endif %}
  mid:
    sql: select * from {{ queries.base }}
  top:
    sql: select * from {{ queries.mid }}
  unrelated:
    sql: select 1 as day, 2 as n
charts:
  composed:
    query: top
    type: bar
    x: day
    y: n
  inline:
    query: select * from {{ queries.mid }}
    type: bar
    x: day
    y: n
  plain:
    query: unrelated
    type: bar
    x: day
    y: n
rows:
  - cols: [composed, inline, plain]
"""
    )
    assert result.success
    board = result.board
    assert board.queries["top"].variable_dependencies == {"show_recent"}
    assert board.charts["composed"].variable_dependencies == {"show_recent"}
    assert board.charts["inline"].variable_dependencies == {"show_recent"}
    assert not board.charts["plain"].variable_dependencies
    assert compute_relevant_variables(board.queries["top"], {"show_recent": False}) == {
        "show_recent": False
    }


def test_unknown_variable_is_blamed_only_on_the_query_that_names_it() -> None:
    """A query embedding one that names an undeclared variable is not reported for it."""
    from dbt_charts.core.compile import compile

    result = compile(
        """
title: Test
source: test
queries:
  base:
    sql: select {{ nope }} as day
  top:
    sql: select * from {{ queries.base }}
charts:
  c:
    query: top
    type: bar
    x: day
    y: day
rows:
  - c
"""
    )
    assert not result.success
    assert [(e.code, e.message.split(" references")[0]) for e in result.errors] == [
        ("ERR-UNKNOWN-VARIABLE", "Query 'base'")
    ]


def test_layer_query_variables_are_chart_dependencies() -> None:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.normalize.chart_focus import focus_on_chart

    result = compile(
        """
title: Test
source: test
variables:
  region:
    input: text
  show_recent:
    input: checkbox
    default: true
queries:
  base:
    sql: select 1 as day, 10 as n where {{ show_recent }}
  target:
    sql: select 1 as day, 5 as t from {{ queries.base }}
  main:
    sql: select 1 as day, 10 as n
charts:
  named_layer:
    query: main
    type: line
    x: day
    y: n
    layers:
      - type: line
        query: target
        y: t
  inline_layer:
    query: main
    type: line
    x: day
    y: n
    layers:
      - type: line
        query: select 1 as day, 5 as t where '{{ region }}' = 'x'
        y: t
  composed_inline_layer:
    query: main
    type: line
    x: day
    y: n
    layers:
      - type: line
        query: select 1 as day, 5 as t from {{ queries.base }}
        y: t
  plain:
    query: main
    type: line
    x: day
    y: n
rows:
  - named_layer
  - inline_layer
  - composed_inline_layer
  - plain
"""
    )
    assert result.success
    charts = result.board.charts
    assert charts["named_layer"].variable_dependencies == {"show_recent"}
    assert charts["inline_layer"].variable_dependencies == {"region"}
    assert charts["composed_inline_layer"].variable_dependencies == {"show_recent"}
    assert not charts["plain"].variable_dependencies
    focused = focus_on_chart(result.board, "inline_layer")
    assert set(focused.variables) == {"region"}
