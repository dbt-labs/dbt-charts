"""Tests for VariableRef, QueryRef, ChartRef typed Pydantic models.

These replace the magic-string cross-file reference pattern with typed models
that validate at parse time, eliminating:
  - Pydantic union-branch leakage in error locs
  - Silent drop of plain strings (e.g. region: "USA")
  - SQL three-part-identifier foot-gun
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.refs import ChartRef, QueryRef, VariableRef


def _board(**kwargs: object) -> AuthoredBoard:
    """Minimal AuthoredBoard — adds title to satisfy the layout validator."""
    return AuthoredBoard(title="t", **kwargs)


class TestVariableRef:
    def test_variable_ref_coerces_from_inline_string(self):
        """A bare string that matches the grammar is coerced to VariableRef."""
        board = _board(variables={"regn": "shared.yml.variables.regn"})
        assert board.variables is not None
        ref = board.variables["regn"]
        assert isinstance(ref, VariableRef)
        assert ref.ref == "shared.yml.variables.regn"

    def test_variable_ref_accepts_explicit_mapping(self):
        """The explicit {ref: ...} mapping form is also accepted."""
        board = _board(variables={"regn": {"ref": "shared.yml.variables.regn"}})
        assert board.variables is not None
        ref = board.variables["regn"]
        assert isinstance(ref, VariableRef)
        assert ref.ref == "shared.yml.variables.regn"

    def test_variable_ref_rejects_invalid_grammar(self):
        """A plain string that doesn't match the grammar raises ValidationError."""
        with pytest.raises(PydanticValidationError):
            _board(variables={"regn": "USA"})

    def test_variable_ref_rejects_wrong_section(self):
        """A string referencing the wrong section (.queries. instead of .variables.) raises."""
        with pytest.raises(PydanticValidationError):
            _board(variables={"regn": "shared.yml.queries.regn"})

    def test_variable_typo_loc_carries_no_model_label(self):
        """A typo in a Variable block should NOT have 'Variable' or 'VariableRef' in loc."""
        with pytest.raises(PydanticValidationError) as exc_info:
            _board(
                variables={
                    "region": {
                        "column": "orders.region",
                        "input": "select",
                        "typo_field": "bad",
                    }
                }
            )
        locs = [str(e["loc"]) for e in exc_info.value.errors()]
        for loc in locs:
            assert "Variable" not in loc, f"Found 'Variable' in loc: {loc}"
            assert "VariableRef" not in loc, f"Found 'VariableRef' in loc: {loc}"


class TestQueryRef:
    def test_query_ref_coerces_from_inline_string(self):
        """A bare string that matches the grammar is coerced to QueryRef."""
        board = _board(queries={"q": "shared.yml.queries.sales"})
        assert board.queries is not None
        ref = board.queries["q"]
        assert isinstance(ref, QueryRef)
        assert ref.ref == "shared.yml.queries.sales"

    def test_query_ref_with_leading_whitespace_strips(self):
        """A ref string with leading/trailing whitespace must be stripped before storing.

        The QueryRef `ref` pattern is matched against the stored value;
        if the whitespace is kept, it raises ValidationError even for valid refs.
        """
        board = _board(queries={"q": "  shared.yml.queries.sales  "})
        assert board.queries is not None
        ref = board.queries["q"]
        assert isinstance(ref, QueryRef)
        assert ref.ref == "shared.yml.queries.sales"

    def test_query_ref_accepts_explicit_mapping(self):
        """The explicit {ref: ...} mapping form is also accepted."""
        board = _board(queries={"q": {"ref": "shared.yml.queries.sales"}})
        assert board.queries is not None
        ref = board.queries["q"]
        assert isinstance(ref, QueryRef)
        assert ref.ref == "shared.yml.queries.sales"

    def test_query_string_without_ref_grammar_becomes_sql(self):
        """A bare string that doesn't match ref grammar is treated as SQL (normalized by parser)."""
        # The model validator converts non-ref strings to AuthoredQuery{sql=..., type="sql"}.
        # Rejection of bad SQL happens at compile time, not at model parse time.
        board = _board(queries={"q": "SELECT 1 FROM x"})
        assert board.queries is not None
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        assert isinstance(board.queries["q"], _BaseQueryFields)

    def test_query_wrong_section_string_becomes_sql(self):
        """A cross-file style string with wrong section (.charts.) becomes SQL, not a QueryRef."""
        # "shared.yml.charts.sales" doesn't match .queries. so it's treated as SQL identifier.
        board = _board(queries={"q": "shared.yml.charts.sales"})
        assert board.queries is not None
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        assert isinstance(board.queries["q"], _BaseQueryFields)

    def test_query_typo_loc_carries_no_model_label(self):
        """A typo in a Query block should NOT have 'Query' or 'QueryRef' in loc."""
        with pytest.raises(PydanticValidationError) as exc_info:
            _board(
                queries={
                    "q": {
                        "sql": "SELECT 1",
                        "source": "db",
                        "typo_field": "bad",
                    }
                }
            )
        locs = [str(e["loc"]) for e in exc_info.value.errors()]
        for loc in locs:
            assert "QueryRef" not in loc, f"Found 'QueryRef' in loc: {loc}"

    def test_chart_inline_query_typo_validates_one_arm(self):
        """A typo in a chart's inline query block should validate against exactly
        one union arm, not all three (str / AuthoredQuery / QueryRef)."""
        with pytest.raises(PydanticValidationError) as exc_info:
            _board(
                charts={
                    "c1": {
                        "type": "bar",
                        "query": {"sq": "select 1"},
                        "x": "a",
                        "y": "b",
                    }
                }
            )
        errors = exc_info.value.errors()
        assert len(errors) == 1, f"Expected one error, got: {errors}"
        assert errors[0]["loc"] == (
            "charts",
            "c1",
            "@inline",
            "bar",
            "query",
            "@inline",
            "sql",
            "sq",
        )
        loc_str = str(errors[0]["loc"])
        assert "QueryRef" not in loc_str, f"Found 'QueryRef' in loc: {loc_str}"
        assert "tagged-union" not in loc_str, f"Found 'tagged-union' in loc: {loc_str}"


class TestChartRef:
    def test_chart_ref_coerces_from_inline_string(self):
        """A bare string that matches the grammar is coerced to ChartRef."""
        board = _board(charts={"c": "shared.yml.charts.my_chart"})
        assert board.charts is not None
        ref = board.charts["c"]
        assert isinstance(ref, ChartRef)
        assert ref.ref == "shared.yml.charts.my_chart"

    def test_chart_ref_accepts_explicit_mapping(self):
        """The explicit {ref: ...} mapping form is also accepted."""
        board = _board(charts={"c": {"ref": "shared.yml.charts.my_chart"}})
        assert board.charts is not None
        ref = board.charts["c"]
        assert isinstance(ref, ChartRef)
        assert ref.ref == "shared.yml.charts.my_chart"

    def test_chart_ref_rejects_plain_string(self):
        """A bare string in charts: that doesn't match the ref grammar raises."""
        with pytest.raises(PydanticValidationError):
            _board(charts={"c": "not_a_ref"})

    def test_chart_ref_rejects_wrong_section(self):
        """A string referencing the wrong section raises ValidationError."""
        with pytest.raises(PydanticValidationError):
            _board(charts={"c": "shared.yml.queries.my_chart"})

    def test_chart_typo_loc_carries_no_model_label(self):
        """A typo in a Chart block should NOT have 'ChartRef' in loc."""
        with pytest.raises(PydanticValidationError) as exc_info:
            _board(
                charts={
                    "c": {
                        "type": "bar",
                        "x": "month",
                        "y": "revenue",
                        "typo_field": "bad",
                    }
                }
            )
        locs = [str(e["loc"]) for e in exc_info.value.errors()]
        for loc in locs:
            assert "ChartRef" not in loc, f"Found 'ChartRef' in loc: {loc}"


_REF_MODELS = [
    (VariableRef, "variables"),
    (QueryRef, "queries"),
    (ChartRef, "charts"),
]


class TestUpwardRelativePathGrammar:
    """Cross-file refs may start with one or more literal `../` segments.

    A board at charts/gtm_weekly/x.yaml must be able to import from a sibling
    directory (charts/sales/) without a symlink workaround. Only a leading run
    of `../` is admitted — a bare leading `.` (hidden-file-style) is not.
    """

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_single_upward_segment_validates(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        ref_str = f"../sales/x.yaml.{section}.y"
        assert model_cls(ref=ref_str).ref == ref_str

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_multi_level_upward_segments_validate(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        ref_str = f"../../shared/x.yaml.{section}.y"
        assert model_cls(ref=ref_str).ref == ref_str

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_leading_single_dot_still_rejected(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        with pytest.raises(PydanticValidationError):
            model_cls(ref=f".hidden.yaml.{section}.y")

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_leading_triple_dot_still_rejected(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        with pytest.raises(PydanticValidationError):
            model_cls(ref=f"...junk.yaml.{section}.y")

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_absolute_path_still_rejected(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        with pytest.raises(PydanticValidationError):
            model_cls(ref=f"/etc/passwd.yaml.{section}.y")

    @pytest.mark.parametrize(("model_cls", "section"), _REF_MODELS)
    def test_empty_file_portion_still_rejected(
        self, model_cls: type[VariableRef | QueryRef | ChartRef], section: str
    ) -> None:
        with pytest.raises(PydanticValidationError):
            model_cls(ref=f".{section}.y")


class TestSQLDottedSchemaNotMisrouted:
    """Regression: SQL with dotted three-part identifiers must not be misrouted as cross-file refs."""

    def test_sql_with_dotted_schema_compiles_as_sql(self):
        """SELECT id FROM mydb.queries.users must compile as SQL, not a cross-file ref."""
        from dbt_charts.core.compile.compiler import compile

        yaml_content = """
title: Test
source: my_db
queries:
  sales: "SELECT id FROM mydb.queries.users"
charts:
  chart:
    query: sales
    type: table
rows:
  - cols:
    - chart
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        query = result.board.queries["sales"]
        assert isinstance(query, SqlQuery)
        assert "mydb.queries.users" in query.sql


class TestNestedBoardSQLQueries:
    """Regression: nested boards with bare-SQL queries must compile."""

    def test_nested_board_sql_string_query_parses(self):
        """A nested board (in rows) with a bare SQL string query must not raise."""
        from dbt_charts.core.compile.models.query.authored import _BaseQueryFields

        board = AuthoredBoard.model_validate(
            {
                "title": "Top",
                "rows": [
                    {
                        "title": "Nested",
                        "queries": {
                            "q": "SELECT product, SUM(revenue) AS revenue FROM orders"
                        },
                        "charts": {
                            "c": {"query": "q", "type": "kpi", "value": "revenue"}
                        },
                        "rows": ["c"],
                    }
                ],
            }
        )
        nested = board.rows[0]
        assert isinstance(nested, AuthoredBoard)
        assert isinstance(nested.queries["q"], _BaseQueryFields)


class TestLoadFromReferenceTypedSignature:
    """load_from_reference now accepts typed ref objects."""

    def test_load_variable_ref_typed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """load_from_reference(VariableRef(...), base_dir) returns a Variable."""
        from dbt_charts.core.compile.compiler import load_from_reference
        from dbt_charts.core.compile.models.variable.authored import Variable

        external_file = tmp_path / "vars.yml"
        external_file.write_text(
            "variables:\n"
            "  region:\n"
            "    label: Region\n"
            "    input: select\n"
            "    options:\n"
            "      static: [North, South]\n"
        )

        ref = VariableRef(ref="vars.variables.region")
        result = load_from_reference(
            ref, base_dir=local_project(root=tmp_path).directory(), sources={}
        )
        assert isinstance(result, Variable)
        assert result.label == "Region"

    def test_load_query_ref_typed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """load_from_reference(QueryRef(...), base_dir) returns an AnyQuery."""
        from dbt_charts.core.compile.compiler import load_from_reference

        external_file = tmp_path / "shared.yml"
        external_file.write_text(
            "queries:\n  sales:\n    sql: SELECT 1 AS n\n    source: db\n"
        )

        ref = QueryRef(ref="shared.queries.sales")
        result = load_from_reference(
            ref, base_dir=local_project(root=tmp_path).directory(), sources={}
        )
        assert result.sql == "SELECT 1 AS n"

    def test_load_chart_ref_typed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ):
        """load_from_reference(ChartRef(...), base_dir) returns a chart dict."""
        from dbt_charts.core.compile.compiler import load_from_reference

        external_file = tmp_path / "charts.yml"
        external_file.write_text(
            "charts:\n"
            "  my_chart:\n"
            "    type: bar\n"
            "    x: month\n"
            "    y: revenue\n"
            "    query: sales\n"
        )

        ref = ChartRef(ref="charts.charts.my_chart")
        result = load_from_reference(
            ref, base_dir=local_project(root=tmp_path).directory(), sources={}
        )
        assert result is not None
