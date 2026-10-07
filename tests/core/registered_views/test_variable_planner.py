"""Tests for entity view variable planner.

Purpose: Validate column-to-variable mapping, include/exclude semantics,
         invalid column name handling, default selection heuristics, and
         integration with AuthoredBoard variable validation.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.registered_views.variable_planner import (
    InvalidColumnNameError,
    PlannerColumn,
    plan_entity_variables,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col(name: str, actual_type: str = "VARCHAR") -> PlannerColumn:
    """Build a PlannerColumn with the given name and type."""
    return PlannerColumn(name=name, actual_type=actual_type)


# ---------------------------------------------------------------------------
# PlannerColumn
# ---------------------------------------------------------------------------


class TestPlannerColumn:
    def test_name_required(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            PlannerColumn(name="", actual_type="VARCHAR")

    def test_is_valid_variable_id_true_for_simple_names(self) -> None:
        assert PlannerColumn(name="status", actual_type="VARCHAR").is_valid_variable_id
        assert PlannerColumn(
            name="order_id", actual_type="INTEGER"
        ).is_valid_variable_id
        assert PlannerColumn(name="_private", actual_type="TEXT").is_valid_variable_id
        assert PlannerColumn(name="col1", actual_type="TEXT").is_valid_variable_id

    def test_is_valid_variable_id_false_for_spaces(self) -> None:
        assert not PlannerColumn(
            name="order id", actual_type="VARCHAR"
        ).is_valid_variable_id

    def test_is_valid_variable_id_false_for_dashes(self) -> None:
        assert not PlannerColumn(
            name="order-id", actual_type="VARCHAR"
        ).is_valid_variable_id

    def test_is_valid_variable_id_false_for_leading_digit(self) -> None:
        assert not PlannerColumn(
            name="1col", actual_type="VARCHAR"
        ).is_valid_variable_id

    def test_is_valid_variable_id_false_for_dots(self) -> None:
        assert not PlannerColumn(name="a.b", actual_type="VARCHAR").is_valid_variable_id


# ---------------------------------------------------------------------------
# plan_entity_variables — invalid column name handling
# ---------------------------------------------------------------------------


class TestInvalidColumnNames:
    def test_invalid_column_raises_by_default(self) -> None:
        """Columns with names that can't be variable ids raise InvalidColumnNameError."""
        cols = [_col("order-id", "VARCHAR")]
        with pytest.raises(InvalidColumnNameError, match="order-id"):
            plan_entity_variables(cols)

    def test_invalid_column_names_error_mentions_column_name(self) -> None:
        cols = [_col("has space", "VARCHAR")]
        with pytest.raises(InvalidColumnNameError) as exc_info:
            plan_entity_variables(cols)
        assert "has space" in str(exc_info.value)

    def test_multiple_invalid_columns_names_all_mentioned(self) -> None:
        """All invalid column names appear in one error — fail fast with context."""
        cols = [
            _col("bad-col", "VARCHAR"),
            _col("1start", "VARCHAR"),
            _col("ok", "VARCHAR"),
        ]
        with pytest.raises(InvalidColumnNameError) as exc_info:
            plan_entity_variables(cols)
        msg = str(exc_info.value)
        assert "bad-col" in msg
        assert "1start" in msg

    def test_invalid_column_excluded_when_not_in_include_list(self) -> None:
        """An invalid-id column excluded by include-list restriction does not raise."""
        cols = [_col("order-id", "VARCHAR"), _col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        assert "status" in result
        assert "order-id" not in result

    def test_valid_column_always_accepted(self) -> None:
        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        assert "status" in result


# ---------------------------------------------------------------------------
# plan_entity_variables — exact column-name variable id contract
# ---------------------------------------------------------------------------


class TestVariableIdMatchesColumnName:
    def test_variable_id_equals_column_name(self) -> None:
        """Generated variable id must exactly match the source column name."""
        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        assert "status" in result
        # The dict key IS the variable id
        # (dict key == variable id is the contract)

    def test_multiple_columns_each_id_matches_name(self) -> None:
        cols = [
            _col("status", "VARCHAR"),
            _col("region", "VARCHAR"),
            _col("is_active", "BOOLEAN"),
        ]
        result = plan_entity_variables(cols, include={"status", "region", "is_active"})
        assert set(result.keys()) == {"status", "region", "is_active"}


# ---------------------------------------------------------------------------
# plan_entity_variables — include/exclude semantics
# ---------------------------------------------------------------------------


class TestIncludeSemantics:
    def test_no_include_returns_default_selection(self) -> None:
        """No include means use the metadata-chosen default set."""
        cols = [
            _col("id", "INTEGER"),
            _col("status", "VARCHAR"),
            _col("amount", "DECIMAL"),
            _col("created_at", "TIMESTAMP"),
            _col("name", "VARCHAR"),
        ]
        result = plan_entity_variables(cols)
        # default should include status (low-cardinality string) and created_at (date)
        # but not id (numeric PK-like) or amount (numeric measure-like)
        assert "status" in result
        assert "created_at" in result

    def test_include_means_only_those_columns(self) -> None:
        cols = [
            _col("status", "VARCHAR"),
            _col("region", "VARCHAR"),
            _col("amount", "DECIMAL"),
        ]
        result = plan_entity_variables(cols, include={"status", "region"})
        assert set(result.keys()) == {"status", "region"}

    def test_include_unknown_column_is_silently_ignored(self) -> None:
        """An include name that doesn't exist in cols is ignored (col doesn't exist)."""
        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status", "does_not_exist"})
        assert set(result.keys()) == {"status"}


# ---------------------------------------------------------------------------
# plan_entity_variables — default selection heuristics
# ---------------------------------------------------------------------------


class TestDefaultSelectionHeuristics:
    def test_boolean_columns_selected_by_default(self) -> None:
        cols = [_col("is_active", "BOOLEAN"), _col("amount", "DECIMAL")]
        result = plan_entity_variables(cols)
        assert "is_active" in result

    def test_string_columns_selected_by_default(self) -> None:
        """String/varchar columns are selected by default as potential dimension filters."""
        cols = [_col("status", "VARCHAR"), _col("amount", "DECIMAL")]
        result = plan_entity_variables(cols)
        assert "status" in result

    def test_date_columns_selected_by_default(self) -> None:
        """Date columns are useful time filters — include in default."""
        cols = [_col("created_at", "DATE"), _col("amount", "DECIMAL")]
        result = plan_entity_variables(cols)
        assert "created_at" in result

    def test_timestamp_columns_selected_by_default(self) -> None:
        cols = [_col("updated_at", "TIMESTAMP"), _col("amount", "DECIMAL")]
        result = plan_entity_variables(cols)
        assert "updated_at" in result

    def test_complex_types_excluded_from_default(self) -> None:
        """ARRAY, VARIANT, JSON, etc. are not useful filter controls."""
        cols = [
            _col("tags", "ARRAY"),
            _col("payload", "VARIANT"),
            _col("status", "VARCHAR"),
        ]
        result = plan_entity_variables(cols)
        assert "tags" not in result
        assert "payload" not in result

    def test_numeric_columns_excluded_from_default(self) -> None:
        """Raw numeric columns (measures) are typically not useful filters."""
        cols = [
            _col("amount", "DECIMAL"),
            _col("count", "INTEGER"),
            _col("status", "VARCHAR"),
        ]
        result = plan_entity_variables(cols)
        assert "amount" not in result
        assert "count" not in result

    def test_empty_column_list_returns_empty(self) -> None:
        result = plan_entity_variables([])
        assert result == {}


# ---------------------------------------------------------------------------
# plan_entity_variables — generated Variable shape
# ---------------------------------------------------------------------------


class TestGeneratedVariableShape:
    def test_generated_variable_is_variable_instance(self) -> None:
        from dbt_charts.core.compile.models.variable.authored import Variable

        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        assert isinstance(result["status"], Variable)

    def test_generated_variable_has_text_or_select_input(self) -> None:
        """Generated variables use a sensible input type for their column type."""
        from dbt_charts.core.compile.models.variable.authored import Variable

        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        var = result["status"]
        assert isinstance(var, Variable)
        # input should not be "auto" — planner resolves it
        assert var.input != "auto"

    def test_boolean_column_generates_checkbox_variable(self) -> None:
        from dbt_charts.core.compile.models.variable.authored import Variable

        cols = [_col("is_active", "BOOLEAN")]
        result = plan_entity_variables(cols, include={"is_active"})
        var = result["is_active"]
        assert isinstance(var, Variable)
        assert var.input == "checkbox"

    def test_date_column_generates_datepicker_or_daterange_variable(self) -> None:
        from dbt_charts.core.compile.models.variable.authored import Variable

        cols = [_col("created_at", "DATE")]
        result = plan_entity_variables(cols, include={"created_at"})
        var = result["created_at"]
        assert isinstance(var, Variable)
        assert var.input in ("datepicker", "daterange", "date")

    def test_generated_variable_is_visible(self) -> None:
        """Generated filter variables should be visible (shown in UI)."""
        cols = [_col("status", "VARCHAR")]
        result = plan_entity_variables(cols, include={"status"})
        assert result["status"].visible is True


# ---------------------------------------------------------------------------
# Integration — generated variables pass normal board validation
# ---------------------------------------------------------------------------


class TestIntegrationWithBoardValidation:
    def test_generated_variables_pass_board_validator(self) -> None:
        """Variables produced by the planner are accepted by the normal board validator."""
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.validate.dispatch import validate_board

        cols = [_col("status", "VARCHAR"), _col("is_active", "BOOLEAN")]
        variables = plan_entity_variables(cols, include={"status", "is_active"})

        board = AuthoredBoard(
            title="Test Entity",
            variables=dict(variables.items()),
        )
        errors = validate_board(board)
        assert errors == [], f"Validation errors: {errors}"

    def test_generated_variables_survive_expander_template_loop(self) -> None:
        """Variables generated by the planner can be round-tripped via a template."""
        import textwrap

        from dbt_charts.core.compile.models.board.authored import AuthoredBoard
        from dbt_charts.core.compile.template.query_result import QueryResult
        from dbt_charts.core.compile.validate.dispatch import validate_board
        from dbt_charts.core.registered_views.expander import render_template

        # Simulate what happens when the expander loops over schema query rows
        # to emit variable keys in YAML — the variable planner is called after
        # template expansion produces the variable section.
        cols = [
            {"name": "status", "actual_type": "VARCHAR"},
            {"name": "is_active", "actual_type": "BOOLEAN"},
        ]
        query_result = QueryResult("q", rows=cols)

        tmpl = textwrap.dedent(
            """\
            title: "entity"
            variables:
            [% for col in queries.cols.rows %]
              [[ col.name ]]:
                input: text
            [% endfor %]
            """
        )
        rendered = render_template(
            tmpl, path_params={}, query_results={"cols": query_result}
        )

        from dbt_charts.core.compile.parse.parser import parse_yaml

        board = parse_yaml(rendered)
        assert isinstance(board, AuthoredBoard)
        assert "status" in board.variables
        assert "is_active" in board.variables

        errors = validate_board(board)
        assert errors == []


# ---------------------------------------------------------------------------
# PlannerColumn.is_identity_keyable — URL-round-trippable types only
# ---------------------------------------------------------------------------


class TestIdentityKeyable:
    """Row-identity keys must round-trip exactly through a URL '=' param."""

    def test_integers_and_strings_are_keyable(self) -> None:
        for db_type in ("BIGINT", "INTEGER", "INT64", "VARCHAR", "TEXT"):
            assert PlannerColumn("c", db_type).is_identity_keyable, db_type

    def test_exact_fixed_point_is_keyable(self) -> None:
        # Snowflake's canonical integer id type and precision-only decimals.
        for db_type in ("NUMBER(38,0)", "NUMBER", "DECIMAL(10)", "NUMERIC(18,0)"):
            assert PlannerColumn("c", db_type).is_identity_keyable, db_type

    def test_booleans_are_not_keyable(self) -> None:
        # ≤2 distinct values can never identify a row.
        for db_type in ("BOOLEAN", "BOOL"):
            assert not PlannerColumn("c", db_type).is_identity_keyable, db_type

    def test_inexact_numerics_are_not_keyable(self) -> None:
        for db_type in ("FLOAT", "DOUBLE", "REAL", "NUMBER(10,2)", "DECIMAL(9,4)"):
            assert not PlannerColumn("c", db_type).is_identity_keyable, db_type

    def test_temporals_and_complex_are_not_keyable(self) -> None:
        for db_type in ("TIMESTAMP", "DATE", "TIME", "ARRAY<INT>", "JSON", "VARIANT"):
            assert not PlannerColumn("c", db_type).is_identity_keyable, db_type


# ---------------------------------------------------------------------------
# INTERVAL regression — INTERVAL is temporal in db_types, must be consistent
# ---------------------------------------------------------------------------


class TestIntervalIsTemporalNotText:
    """INTERVAL is in db_types.TEMPORAL_TYPES.

    Before the consolidation, variable_planner._TEMPORAL_BASES was a hand-copy
    that omitted INTERVAL, so an INTERVAL column wrongly fell through to a text
    input and was incorrectly considered for identity keys.
    """

    def test_interval_column_gets_datepicker_input(self) -> None:
        """INTERVAL should produce a datepicker variable, not a text variable."""
        from dbt_charts.core.compile.models.variable.authored import Variable

        cols = [PlannerColumn(name="duration", actual_type="INTERVAL")]
        result = plan_entity_variables(cols, include={"duration"})
        var = result["duration"]
        assert isinstance(var, Variable)
        assert var.input == "datepicker", (
            f"Expected datepicker for INTERVAL, got {var.input!r}. "
            "INTERVAL is in db_types.TEMPORAL_TYPES — the planner must use "
            "the canonical set, not its own hand-copy that omits INTERVAL."
        )

    def test_interval_column_selected_by_default(self) -> None:
        """INTERVAL is a temporal type and should appear in default selection."""
        cols = [
            PlannerColumn(name="duration", actual_type="INTERVAL"),
            PlannerColumn(name="amount", actual_type="DECIMAL"),
        ]
        result = plan_entity_variables(cols)
        assert "duration" in result, (
            "INTERVAL column was not default-selected. "
            "INTERVAL is temporal — it must be included alongside DATE/TIMESTAMP."
        )

    def test_interval_is_not_identity_keyable(self) -> None:
        """INTERVAL values do not round-trip through a URL '=' param exactly."""
        assert not PlannerColumn("c", "INTERVAL").is_identity_keyable, (
            "INTERVAL should not be an identity key — temporal types are excluded."
        )
