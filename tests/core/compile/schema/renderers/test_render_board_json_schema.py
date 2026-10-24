"""render_yaml_schema output for the board: the schema editors validate against."""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema


@pytest.fixture(scope="module")
def board_schema():
    return render_yaml_schema(introspect())


class TestDefsKey:
    def test_uses_defs_not_definitions(self, board_schema) -> None:
        assert "$defs" in board_schema
        assert "definitions" not in board_schema

    def test_all_refs_use_defs_prefix(self, board_schema) -> None:
        schema_str = json.dumps(board_schema)
        assert "#/definitions/" not in schema_str
        assert "#/$defs/" in schema_str


class TestBoardRules:
    @pytest.mark.parametrize(
        ("board", "valid"),
        [
            ({"title": "T"}, True),
            ({"text": "hi"}, True),
            ({"notes": "n"}, False),
        ],
    )
    def test_states_the_board_content_rule(
        self, board_schema: dict[str, object], board: dict[str, str], valid: bool
    ) -> None:
        assert Draft202012Validator(board_schema).is_valid(board) is valid

    @pytest.mark.parametrize(
        ("rows", "valid"),
        [
            (["a", {"cols": ["a", "b"]}], True),
            ([{"revenue": {"type": "bar", "query": "q", "x": "a", "y": "b"}}], True),
            ([{"revenue": {"type": "nope"}}], False),
        ],
    )
    def test_layout_items_are_names_nested_boards_or_keyed_charts(
        self, board_schema: dict[str, object], rows: list[object], valid: bool
    ) -> None:
        board = {"rows": rows}
        assert Draft202012Validator(board_schema).is_valid(board) is valid

    def test_theme_enum_populated(self, board_schema) -> None:
        theme_prop = board_schema["properties"]["theme"]
        schema_str = json.dumps(theme_prop)
        assert "enum" in schema_str
        assert "clarity" in schema_str  # built-in theme name

    def test_authored_chart_type_enum_present(self, board_schema) -> None:
        # AuthoredChart.type must have an enum covering all authorable tags including "donut"
        from dbt_charts.core.compile.models.chart.authored import (
            AUTHORED_CHART_TYPE_TAGS,
        )

        chart_def = board_schema["$defs"]["AuthoredChart"]
        type_enum = chart_def["properties"]["type"]["enum"]
        for tag in AUTHORED_CHART_TYPE_TAGS:
            assert tag in type_enum, (
                f"authorable tag {tag!r} missing from AuthoredChart.type enum"
            )


class TestFieldSetParity:
    def _check_model(self, board_schema, model_name: str) -> None:
        ir = introspect()
        ir_fields = {f.name for f in ir.models[model_name].fields}
        defn = board_schema["$defs"].get(model_name, {})
        schema_props = set(defn.get("properties", {}).keys())
        missing = ir_fields - schema_props
        assert not missing, f"{model_name} missing fields in board schema: {missing}"

    def test_authored_chart_is_discriminated_union_def(self, board_schema) -> None:
        # AuthoredChart is a discriminated union; it has allOf (if/then) structure, not fields.
        chart_def = board_schema["$defs"]["AuthoredChart"]
        assert "allOf" in chart_def, (
            "AuthoredChart must have allOf discriminated-union structure"
        )
        assert "properties" in chart_def and "type" in chart_def["properties"]

    def test_variable_field_parity(self, board_schema) -> None:
        self._check_model(board_schema, "Variable")

    def test_grid_layout_field_parity(self, board_schema) -> None:
        self._check_model(board_schema, "GridLayout")

    def test_tab_layout_field_parity(self, board_schema) -> None:
        self._check_model(board_schema, "TabLayout")

    def test_all_definitions_have_descriptions_on_properties(
        self, board_schema
    ) -> None:
        failures = []
        for model_name, defn in board_schema["$defs"].items():
            for prop_name, prop in defn.get("properties", {}).items():
                if not prop.get("description"):
                    failures.append(f"{model_name}.{prop_name}")
        assert not failures, f"board schema properties missing description: {failures}"


class TestStyleSubtree:
    def test_bar_chart_style_is_bar_style_patch_ref(self, board_schema) -> None:
        defns = board_schema["$defs"]
        assert "BarChart" in defns, "BarChart missing from $defs"
        style_prop = defns["BarChart"]["properties"].get("style", {})
        schema_str = json.dumps(style_prop)
        assert "#/$defs/BarChartStylePatch" in schema_str

    def test_bar_style_patch_has_properties(self, board_schema) -> None:
        defns = board_schema["$defs"]
        assert "BarChartStylePatch" in defns
        bar_props = defns["BarChartStylePatch"].get("properties", {})
        assert bar_props, "BarChartStylePatch must have properties"


class TestStringShorthands:
    def test_variables_preserves_string_shorthand(self, board_schema) -> None:
        v = board_schema["properties"]["variables"]
        schema_str = json.dumps(v)
        assert "Variable" in schema_str, "Variable model branch dropped"
        assert '"type": "string"' in schema_str, (
            "string shorthand dropped from variables"
        )

    def test_queries_preserves_string_shorthand(self, board_schema) -> None:
        q = board_schema["properties"]["queries"]
        schema_str = json.dumps(q)
        assert "AuthoredQuery" in schema_str, "AuthoredQuery model branch dropped"
        assert '"type": "string"' in schema_str, "string shorthand dropped from queries"

    def test_queries_bare_sql_shorthand_valid_in_schema(self, board_schema) -> None:
        """Bare SQL shorthand (queries: {sales: 'SELECT * FROM sales'}) must be valid in the schema.

        The queries additionalProperties anyOf must include an unconstrained string arm
        (no pattern constraint) so that bare SQL strings don't get red-squiggles in the
        VS Code IDE extension. Runtime (Pydantic) already enforces the grammar.
        """
        q_schema = board_schema["properties"]["queries"]
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

    @pytest.mark.parametrize(
        ("value", "valid"),
        [
            ("1h", True),
            ("1H30m", True),
            ("forever", True),
            (True, True),
            (False, True),
            ({"ttl": "1h"}, True),
            ({"ttl": "forever"}, True),
            ("5x", False),
            ("sometimes", False),
            ({"ttl": None}, False),
            ({"ttl": "5x"}, False),
            ({"enabled": True}, False),
        ],
    )
    def test_cache_accepts_exactly_the_authorable_forms(
        self, board_schema, value: object, valid: bool
    ) -> None:
        """Scalars are expanded by a before-validator JSON Schema cannot see;
        the annotation's input type must state them, and only them."""
        board_cache = {
            "$ref": "#/properties/cache",
            "$defs": board_schema["$defs"],
            "properties": board_schema["properties"],
        }
        assert Draft202012Validator(board_cache).is_valid(value) is valid


class TestSourceConnectors:
    def test_no_board_level_source_surface(self, board_schema) -> None:
        """Boards cannot define sources inline, so the board schema exposes neither
        a board-level `sources:` property nor a `Source` connector definition."""
        assert "sources" not in board_schema["properties"]
        assert "Source" not in board_schema["$defs"]


class TestFieldLevelDiscriminatedUnions:
    """A `Field(discriminator=...)` union narrows by tag, so editors suggest
    only the selected variant's keys instead of merging every branch's."""

    @pytest.mark.parametrize(
        ("field", "union", "suffix", "tags"),
        [
            (
                "annotations",
                "Annotation",
                "Annotation",
                {"rule", "band", "note", "trend"},
            ),
            ("layers", "CartesianLayer", "Layer", {"bar", "line", "area", "scatter"}),
        ],
    )
    def test_items_ref_a_union_that_branches_on_type(
        self,
        board_schema: dict[str, Any],
        field: str,
        union: str,
        suffix: str,
        tags: set[str],
    ) -> None:
        defs = board_schema["$defs"]
        assert f"#/$defs/{union}" in json.dumps(defs["BarChart"]["properties"][field])
        definition = defs[union]
        assert "anyOf" not in definition
        branches = {
            branch["if"]["properties"]["type"]["enum"][0]: branch["then"]
            for branch in definition["allOf"]
        }
        assert set(branches) == tags
        for tag, then in branches.items():
            assert then == {"$ref": f"#/$defs/{tag.capitalize()}{suffix}"}
