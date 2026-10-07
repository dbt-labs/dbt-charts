"""TDD tests for render_vscode_schema: IR → VS Code JSON Schema 2020-12."""

import json

import pytest
from jsonschema import Draft202012Validator

from dbt_charts.core.compile.schema.introspection import introspect


@pytest.fixture(scope="module")
def vscode_schema():
    from dbt_charts.core.compile.schema.renderers.vscode_schema import (
        render_vscode_schema,
    )

    return render_vscode_schema(introspect())


class TestDefsKey:
    def test_uses_defs_not_definitions(self, vscode_schema) -> None:
        assert "$defs" in vscode_schema
        assert "definitions" not in vscode_schema

    def test_all_refs_use_defs_prefix(self, vscode_schema) -> None:
        schema_str = json.dumps(vscode_schema)
        assert "#/definitions/" not in schema_str
        assert "#/$defs/" in schema_str


class TestIdeExtras:
    def test_file_match_is_exactly_the_current_conventions(self, vscode_schema) -> None:
        """fileMatch is the project config, the `charts/` directory convention,
        and `*.board.yml` — nothing else. The retired pre-rename suffix
        aliases must not reappear (pre-launch: no read-compat aliases)."""
        assert vscode_schema["fileMatch"] == [
            "dbt_charts.yml",
            "dbt_charts.yaml",
            "charts/*.yml",
            "charts/*.yaml",
            "charts/**/*.yml",
            "charts/**/*.yaml",
            "*.board.yml",
        ]

    @pytest.mark.parametrize(
        ("board", "valid"),
        [
            ({"title": "T"}, True),
            ({"text": "hi"}, True),
            ({"notes": "n"}, False),
        ],
    )
    def test_states_the_board_content_rule(
        self, vscode_schema: dict[str, object], board: dict[str, str], valid: bool
    ) -> None:
        assert Draft202012Validator(vscode_schema).is_valid(board) is valid

    def test_theme_enum_populated(self, vscode_schema) -> None:
        theme_prop = vscode_schema["properties"]["theme"]
        schema_str = json.dumps(theme_prop)
        assert "enum" in schema_str
        assert "clarity" in schema_str  # built-in theme name

    def test_authored_chart_type_enum_present(self, vscode_schema) -> None:
        # AuthoredChart.type must have an enum covering all authorable tags including "donut"
        from dbt_charts.core.compile.models.chart.authored import (
            AUTHORED_CHART_TYPE_TAGS,
        )

        chart_def = vscode_schema["$defs"]["AuthoredChart"]
        type_enum = chart_def["properties"]["type"]["enum"]
        for tag in AUTHORED_CHART_TYPE_TAGS:
            assert tag in type_enum, (
                f"authorable tag {tag!r} missing from AuthoredChart.type enum"
            )


class TestFieldSetParity:
    def _check_model(self, vscode_schema, model_name: str) -> None:
        ir = introspect()
        ir_fields = {f.name for f in ir.models[model_name].fields}
        defn = vscode_schema["$defs"].get(model_name, {})
        schema_props = set(defn.get("properties", {}).keys())
        missing = ir_fields - schema_props
        assert not missing, f"{model_name} missing fields in VS Code schema: {missing}"

    def test_authored_chart_is_discriminated_union_def(self, vscode_schema) -> None:
        # AuthoredChart is a discriminated union; it has allOf (if/then) structure, not fields.
        chart_def = vscode_schema["$defs"]["AuthoredChart"]
        assert "allOf" in chart_def, (
            "AuthoredChart must have allOf discriminated-union structure"
        )
        assert "properties" in chart_def and "type" in chart_def["properties"]

    def test_variable_field_parity(self, vscode_schema) -> None:
        self._check_model(vscode_schema, "Variable")

    def test_grid_layout_field_parity(self, vscode_schema) -> None:
        self._check_model(vscode_schema, "GridLayout")

    def test_tab_layout_field_parity(self, vscode_schema) -> None:
        self._check_model(vscode_schema, "TabLayout")

    def test_all_definitions_have_descriptions_on_properties(
        self, vscode_schema
    ) -> None:
        failures = []
        for model_name, defn in vscode_schema["$defs"].items():
            for prop_name, prop in defn.get("properties", {}).items():
                if not prop.get("description"):
                    failures.append(f"{model_name}.{prop_name}")
        assert not failures, (
            f"VS Code schema properties missing description: {failures}"
        )


class TestStyleSubtree:
    def test_bar_chart_style_is_bar_style_patch_ref(self, vscode_schema) -> None:
        defns = vscode_schema["$defs"]
        assert "BarChart" in defns, "BarChart missing from $defs"
        style_prop = defns["BarChart"]["properties"].get("style", {})
        schema_str = json.dumps(style_prop)
        assert "#/$defs/BarChartStylePatch" in schema_str

    def test_bar_style_patch_has_properties(self, vscode_schema) -> None:
        defns = vscode_schema["$defs"]
        assert "BarChartStylePatch" in defns
        bar_props = defns["BarChartStylePatch"].get("properties", {})
        assert bar_props, "BarChartStylePatch must have properties"


class TestStringShorthands:
    def test_variables_preserves_string_shorthand(self, vscode_schema) -> None:
        v = vscode_schema["properties"]["variables"]
        schema_str = json.dumps(v)
        assert "Variable" in schema_str, "Variable model branch dropped"
        assert '"type": "string"' in schema_str, (
            "string shorthand dropped from variables"
        )

    def test_queries_preserves_string_shorthand(self, vscode_schema) -> None:
        q = vscode_schema["properties"]["queries"]
        schema_str = json.dumps(q)
        assert "AuthoredQuery" in schema_str, "AuthoredQuery model branch dropped"
        assert '"type": "string"' in schema_str, "string shorthand dropped from queries"

    def test_queries_bare_sql_shorthand_valid_in_schema(self, vscode_schema) -> None:
        """Bare SQL shorthand (queries: {sales: 'SELECT * FROM sales'}) must be valid in the schema.

        The queries additionalProperties anyOf must include an unconstrained string arm
        (no pattern constraint) so that bare SQL strings don't get red-squiggles in the
        VS Code IDE extension. Runtime (Pydantic) already enforces the grammar.
        """
        q_schema = vscode_schema["properties"]["queries"]
        object_form = next(
            branch for branch in q_schema["anyOf"] if branch.get("type") == "object"
        )
        arms = object_form["additionalProperties"]["anyOf"]
        # There must be at least one string arm with no 'pattern' constraint
        unconstrained_string_arms = [
            arm for arm in arms if arm.get("type") == "string" and "pattern" not in arm
        ]
        assert unconstrained_string_arms, (
            "queries additionalProperties anyOf must contain an unconstrained "
            '{"type": "string"} arm for bare SQL shorthand; only pattern-constrained '
            "string arms found — bare SQL will be red-squiggles in VS Code"
        )

    def test_cache_scalar_shorthands_valid_in_schema(self, vscode_schema) -> None:
        """`cache: 1h` and `cache: false` are the primary authoring forms.

        CachePatch's before-validator expands them, which JSON Schema generation
        cannot see — without explicit boolean and unconstrained-string arms the
        IDE red-squiggles the spelling we tell authors to use.
        """
        arms = vscode_schema["$defs"]["CachePatch"]["oneOf"]
        types = {arm.get("type") for arm in arms}
        assert "boolean" in types, f"cache: false has no boolean arm: {arms}"
        assert any(
            arm.get("type") == "string" and "pattern" not in arm for arm in arms
        ), f"cache: 1h has no unconstrained string arm: {arms}"
        assert any(arm.get("type") == "object" for arm in arms), "block form dropped"

    def test_cache_block_ttl_accepts_a_duration_string(self, vscode_schema) -> None:
        """The block form must accept `ttl: 1h`, not just the `forever` literal."""
        block = next(
            arm
            for arm in vscode_schema["$defs"]["CachePatch"]["oneOf"]
            if arm.get("type") == "object"
        )
        ttl = block["properties"]["ttl"]
        assert any(branch.get("type") == "string" for branch in ttl["anyOf"]), (
            f"ttl rejects duration strings — only {ttl['anyOf']} allowed"
        )

    def test_layout_item_grid_uses_allof_not_bare_ref(self, vscode_schema) -> None:
        grid_prop = vscode_schema["$defs"]["LayoutItem"]["oneOf"][2]["properties"][
            "grid"
        ]
        assert "allOf" in grid_prop, "grid must use allOf to allow sibling description"
        assert "$ref" not in grid_prop, "bare $ref must not sit alongside description"

    def test_layout_item_tabs_uses_allof_not_bare_ref(self, vscode_schema) -> None:
        tabs_prop = vscode_schema["$defs"]["LayoutItem"]["oneOf"][2]["properties"][
            "tabs"
        ]
        assert "allOf" in tabs_prop, "tabs must use allOf to allow sibling description"
        assert "$ref" not in tabs_prop, "bare $ref must not sit alongside description"


class TestSourceConnectors:
    def test_no_board_level_source_surface(self, vscode_schema) -> None:
        """Boards cannot define sources inline, so the board schema exposes neither
        a board-level `sources:` property nor a `Source` connector definition."""
        assert "sources" not in vscode_schema["properties"]
        assert "Source" not in vscode_schema["$defs"]
