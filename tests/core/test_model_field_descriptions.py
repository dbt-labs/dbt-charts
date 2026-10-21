"""Tests that key compiled and input models have Field(description=...) annotations.

Field descriptions enable:
- Better IDE autocomplete tooltips in JSON Schema
- AI understanding of field semantics
- The generated YAML field reference (the docs site's YAML reference)

Design: denylist approach — assert all model fields have descriptions,
with explicit exemptions for fields intentionally left bare (e.g., private/internal
fields, fields inherited from abstract bases that can't be annotated here).

TDD: these tests were written before the descriptions were added and drove the
implementation. Any field added to these models without a description will now fail CI.
"""

from pydantic import BaseModel

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.chart.authored import (
    BarChart,
    KpiChart,
    PieChart,
)
from dbt_charts.core.compile.models.chart.normalized import (
    _BaseChartFields,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.query.normalized import (
    HttpQuery,
    Query,
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.compile.models.style.authored._base import (
    EndpointLabelsConfig,
)
from dbt_charts.core.compile.models.variable.authored import Variable


def _fields_missing_description(
    model: type[BaseModel], exemptions: set[str] | None = None
) -> list[str]:
    """Return field names that lack a description, excluding exempted fields."""
    skip = exemptions or set()
    return [
        name
        for name, field_info in model.model_fields.items()
        if name not in skip and not field_info.description
    ]


class TestChartDescriptions:
    """Every field on _BaseChartFields must have a description.

    _BaseChartFields is the base for all normalized chart family models — it's the
    primary output of compilation and the surface AI assistants see when writing render code.
    """

    def test_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(_BaseChartFields)
        assert not missing, (
            f"_BaseChartFields fields missing description: {missing}\n"
            "Add Field(description='...') to each field in models/chart/normalized.py"
        )


class TestChartFieldsDescriptions:
    """Every field on per-family chart patch classes must have a description.

    Per-family patches define the authored vocabulary for each chart type.
    """

    def test_bar_patch_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(BarChart)
        assert not missing, (
            f"BarChart fields missing description: {missing}\n"
            "Add Field(description='...') to each field in chart/authored.py"
        )

    def test_kpi_patch_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(KpiChart)
        assert not missing, (
            f"KpiChart fields missing description: {missing}\n"
            "Add Field(description='...') to each field in chart/authored.py"
        )

    def test_pie_patch_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(PieChart)
        assert not missing, (
            f"PieChart fields missing description: {missing}\n"
            "Add Field(description='...') to each field in chart/authored.py"
        )


class TestBoardDescriptions:
    """Every field on Board must have a description."""

    def test_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(Board)
        assert not missing, (
            f"Board fields missing description: {missing}\n"
            "Add Field(description='...') to each field in models/board/normalized.py"
        )


class TestLayoutDescriptions:
    """Every field on Layout and LayoutItem must have a description."""

    def test_layout_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(Layout)
        assert not missing, f"Layout fields missing description: {missing}"

    def test_layout_item_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(LayoutItem)
        assert not missing, f"LayoutItem fields missing description: {missing}"


class TestQueryBaseDescriptions:
    """Every field on Query base class must have a description."""

    def test_all_fields_have_descriptions(self) -> None:
        # variable_dependencies uses default_factory=frozenset — described via Field
        missing = _fields_missing_description(Query)
        assert not missing, f"Query base fields missing description: {missing}"


class TestQueryTypeDescriptions:
    """Every field on each query type must have a description."""

    def test_sql_query_all_fields_have_descriptions(self) -> None:
        # Exclude base class fields (tested separately via TestQueryBaseDescriptions)
        base_fields = set(Query.model_fields)
        missing = _fields_missing_description(SqlQuery, exemptions=base_fields)
        assert not missing, f"SqlQuery fields missing description: {missing}"

    def test_http_query_all_fields_have_descriptions(self) -> None:
        base_fields = set(Query.model_fields)
        missing = _fields_missing_description(HttpQuery, exemptions=base_fields)
        assert not missing, f"HttpQuery fields missing description: {missing}"

    def test_values_query_all_fields_have_descriptions(self) -> None:
        base_fields = set(Query.model_fields)
        missing = _fields_missing_description(ValuesQuery, exemptions=base_fields)
        assert not missing, f"ValuesQuery fields missing description: {missing}"


class TestInputTypeDescriptions:
    """Key input model fields must have descriptions."""

    # input_auto_detected is an exclude=True internal tracking field
    _VARIABLE_EXEMPTIONS: set[str] = {"input_auto_detected"}

    def test_board_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(AuthoredBoard)
        assert not missing, f"AuthoredBoard fields missing description: {missing}"

    def test_variable_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(Variable, self._VARIABLE_EXEMPTIONS)
        assert not missing, f"Variable fields missing description: {missing}"

    def test_format_config_all_fields_have_descriptions(self) -> None:
        missing = _fields_missing_description(FormatConfig)
        assert not missing, f"FormatConfig fields missing description: {missing}"


class TestDescriptionContent:
    """Descriptions are the agent's reference and the design panel's help text.

    Presence is not the bar. The YAML field reference is rendered from these strings
    and served to the agent by the MCP server; Cloud's design panel renders the
    same strings as its group tooltips (`DesignProperty.description` is "Field
    help from the schema"). A description that restates the cascade contract
    every style field shares, or that states a default wrong, misdirects both
    readers at once.
    """

    # Phrases that describe the authoring mechanism rather than the field. Each
    # is true of every field in its class, so it costs a reader a sentence and
    # returns nothing.
    _FILLER = (
        "authors opt in",
        "opt in per chart",
        "overrides the theme default for this chart",
        "opt-in:",
    )

    def _style_descriptions(self) -> dict[str, str]:
        import importlib
        import inspect
        import pkgutil

        import dbt_charts.core.compile.models.style as style_pkg

        found: dict[str, str] = {}
        for module in pkgutil.walk_packages(
            style_pkg.__path__, f"{style_pkg.__name__}."
        ):
            mod = importlib.import_module(module.name)
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if not issubclass(obj, BaseModel) or obj is BaseModel:
                    continue
                # Re-imports pull in models from sibling packages; this test
                # speaks only for style/.
                if not obj.__module__.startswith(style_pkg.__name__):
                    continue
                for name, field_info in obj.model_fields.items():
                    if field_info.description:
                        found[f"{obj.__name__}.{name}"] = field_info.description
        return found

    def test_no_cascade_mechanism_filler_in_style_descriptions(self) -> None:
        offenders = [
            f"{path}: {text}"
            for path, text in self._style_descriptions().items()
            for phrase in self._FILLER
            if phrase in text.lower()
        ]
        assert not offenders, "Descriptions restating the cascade contract: " + str(
            offenders
        )

    def test_endpoint_labels_visible_states_the_shipped_default(self) -> None:
        """Pinned against the themes, so the prose cannot drift from the value.

        Every family the sentence names is read, over every shipped theme.
        """
        from typing import get_args

        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.schema_names import ThemeName

        shipped = {
            getattr(get_theme_style(name).charts, family).endpoint_labels.visible
            for name in get_args(ThemeName)
            for family in ("bar", "line", "area")
        }
        assert shipped == {"auto"}
        text = (EndpointLabelsConfig.model_fields["visible"].description or "").lower()
        assert "'auto' on every built-in theme" in text

    def test_endpoint_labels_visible_scopes_the_claim_to_shapes_that_get_a_rail(
        self,
    ) -> None:
        """A bar only direct-labels when stacked, and grouped is the default.

        The first draft of this copy said "multi-series bar" flat, which reads
        as a promise the commonest bar shape does not keep: `stack` is `none`
        on every shipped theme, and resolve hands a grouped bar its legend
        back. Without this the correction can be undone with CI green.
        """
        text = (EndpointLabelsConfig.model_fields["visible"].description or "").lower()
        assert "stacked bars" in text
        assert "grouped bars" in text

    def test_endpoint_labels_visible_names_the_legend_relationship(self) -> None:
        """A reader hunting "the legend" has to land here, not on `legend:`."""
        text = (EndpointLabelsConfig.model_fields["visible"].description or "").lower()
        assert "legend" in text

    def test_endpoint_labels_help_carries_the_words_a_user_searches(self) -> None:
        blob = " ".join(
            [EndpointLabelsConfig.__doc__ or ""]
            + [f.description or "" for f in EndpointLabelsConfig.model_fields.values()]
        ).lower()
        for term in ("series", "legend", "label"):
            assert term in blob, f"endpoint_labels help never says {term!r}"

    def test_endpoint_labels_help_uses_no_em_dash(self) -> None:
        strings = [EndpointLabelsConfig.__doc__ or ""] + [
            f.description or "" for f in EndpointLabelsConfig.model_fields.values()
        ]
        assert not [s for s in strings if "—" in s]
