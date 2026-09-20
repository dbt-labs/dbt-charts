"""Authored variable models: Variable, VariableOptions, SingleRowBoolProbe, VariableInputType.

Stage: COMPILE (Input)
Purpose: Define variable types that map directly to the YAML schema.

Variables provide dynamic values to queries and charts. They can be bound to
user inputs (select, slider, etc.) or have static default values.
"""

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

from dbt_charts.core.compile.models.markers import DisplayText
from dbt_charts.core.compile.models.primitives import VariableDependencies
from dbt_charts.core.compile.models.query.authored import AuthoredQuery
from dbt_charts.core.compile.models.refs import infer_query_type_from_keys

# ============================================================================
# ENUMS & LITERALS
# ============================================================================

VariableInputType = Literal[
    "auto",
    "select",
    "multiselect",
    "input",
    "text",
    "number",
    "textarea",
    "slider",
    "range",
    "date",
    "datepicker",
    "daterange",
    "checkbox",
    "radio",
]


def _normalize_var_query(v: object) -> object:
    if isinstance(v, dict) and "type" not in v:
        return {**v, "type": infer_query_type_from_keys(v)}
    return v


# ============================================================================
# VARIABLE TYPES
# ============================================================================


class VariableOptions(BaseModel):
    """Options configuration for variable inputs.

    Defines where the options come from for select/multiselect inputs.
    """

    model_config = ConfigDict(extra="forbid")

    static: list[str | int | float] | None = Field(
        default=None,
        description=(
            "Option values written out in place, all strings or all numbers. "
            "Numeric options type the value the control sends back as a number "
            "unless data_type says otherwise."
        ),
    )

    @field_validator("static")
    @classmethod
    def _one_type(
        cls, static: list[str | int | float] | None
    ) -> list[str | int | float] | None:
        if (
            static
            and any(isinstance(o, str) for o in static)
            and any(not isinstance(o, str) for o in static)
        ):
            raise ValueError(
                f"options.static must be all strings or all numbers (one type), got {static!r}"
            )
        return static

    query: str | None = Field(
        default=None, description="Query name whose result rows provide option values."
    )
    column: str | None = Field(
        default=None, description="Column in the query result to use as option values."
    )
    label_column: str | None = Field(
        default=None,
        description="Column in the query result to use as display labels (separate from values).",
    )


class SingleRowBoolProbe(BaseModel):
    """Single-row boolean query probe.

    Executes a named query and reads one boolean cell to decide a yes/no
    condition. Used by ``Variable.enabled`` and ``LayoutItem.visible``.
    The query must return exactly one row; the named column must hold a
    boolean-coercible value (true/false/1/0/yes/no).

    Example YAML (enabled)::

        enabled:
          query: seed_control_state
          column: is_enabled

    Example YAML (visible)::

        visible:
          query: layout_flags
          column: show_panel
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Name of the query to execute.")
    column: str = Field(
        description="Column in the single result row holding the boolean value."
    )


class Variable(BaseModel):
    """Variable definition from YAML.

    Variables provide dynamic values to queries and charts.
    They can be bound to user inputs (select, slider, etc.) or
    have static default values.

    Cascading Variables:
        Variables can depend on other variables when their options query
        uses Jinja templates referencing other variables. For example:

        variables:
          country:
            input: select
            options:
              query: country_options

          state:
            input: select
            options:
              query: state_options  # SQL uses {{ filter('country', country) }}

        When country changes, state's options are re-fetched with the new
        country value, and state's current value is reset.

    Invisible Variables:
        Variables with visible=False are not rendered in the UI but can still
        be used in queries. This is useful for:
        - Server-side variables passed via URL query params
        - Internal system values (theme, model name, connection strings)
        - Variables that shouldn't be user-editable

    Example YAML:
        variables:
          date_range:
            input: daterange
            label: "Date Range"
            default: ["2024-01-01", "2024-12-31"]
          category:
            input: select
            options:
              static: ["Electronics", "Clothing"]
          model:
            input: text
            visible: false  # Set via URL param, not shown in UI
    """

    model_config = ConfigDict(extra="forbid")

    input: VariableInputType = Field(
        default="auto",
        description="UI control type (select, multiselect, slider, daterange, etc.). 'auto' detects from options.",
    )
    input_auto_detected: bool = Field(
        default=False,
        json_schema_extra={"internal": True},
        description="Internal: True when input type was resolved from 'auto' to a concrete type.",
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None, description="Caption naming what this input sets."
    )
    notes: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Help text for this input, carried to the host rather than drawn on the board.",
    )
    default: Any | None = Field(
        default=None,
        description="Value the variable takes when neither a URL param nor --var supplies one.",
    )
    placeholder: str | None = Field(
        default=None, description="Hint text shown inside the input while it is empty."
    )
    required: bool | None = Field(
        default=False,
        description="When True, a value must be provided before queries execute.",
    )
    visible: bool = Field(
        default=True,
        description="When False, the variable is not rendered in the UI but can still be set via URL params.",
    )
    enabled: bool | str | SingleRowBoolProbe | None = Field(
        default=None,
        description=(
            "Enable this control. Accepts: static bool; a variable name or Jinja "
            "boolean expression string (no {{ }} required, bare names auto-wrap); "
            "or a {query, column} form that reads a single boolean cell from a named "
            "query. None = enabled. Absent variable in a string expression raises (use a default)."
        ),
    )

    # Source binding (one of these for data-driven options)
    column: str | None = Field(
        default=None,
        description=(
            "Table column to draw option values from, as 'table.column'. The table "
            "may be schema-qualified ('schema.table.column') when it is not in the "
            "connection's default schema."
        ),
    )
    query: Annotated[
        str | AuthoredQuery | None,
        BeforeValidator(_normalize_var_query),
        Field(
            default=None,
            description="Query name or inline query definition for populating options.",
        ),
    ] = None
    # Options
    options: VariableOptions | None = Field(
        default=None,
        description="Where the selectable values come from: a written-out list or a query.",
    )

    data_type: Literal["string", "number", "date", "boolean", "array"] | None = Field(
        default=None,
        description=(
            "The type of the values a select, radio or multiselect sends back. "
            "'number', 'date' and 'boolean' convert the value before it reaches "
            "SQL; needed when the options come from a query, since a static "
            "numeric list already implies 'number'. 'string' and 'array' leave "
            "the value as sent."
        ),
    )

    # Numeric bounds: a slider needs them to draw, a number input enforces them
    min: int | float | None = Field(
        default=None, description="Minimum value for number, slider and range inputs."
    )
    max: int | float | None = Field(
        default=None, description="Maximum value for number, slider and range inputs."
    )
    step: int | float | None = Field(
        default=None, description="Step size for number, slider and range inputs."
    )

    @field_validator("min", "max", "step")
    @classmethod
    def _bounds_are_finite(cls, value: int | float | None) -> int | float | None:
        # A control can only enforce a bound it can read back; `.inf`/`.nan`
        # publish as tokens the runtime would have to ignore.
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"min/max/step must be a finite number, got {value!r}")
        return value

    # Operator for filter generation
    operator: str | None = Field(
        default=None,
        description="SQL operator used when generating filter expressions (e.g., '=', 'IN', 'LIKE').",
    )

    # Computed during compilation — not an authored field.
    defined_in_other_file: bool = Field(
        default=False,
        json_schema_extra={"internal": True},
        description=(
            "Internal: True when a VariableRef pulled this definition in from "
            "another file, so the importing document holds only the name."
        ),
    )
    variable_dependencies: VariableDependencies = Field(
        default_factory=frozenset,
        json_schema_extra={"internal": True},
        description="Other variable names this variable's options query depends on (computed during compilation).",
    )

    def get_option_query(self) -> str | None:
        """Get the query name used for populating options.

        Only valid after compilation — `_promote_inline_option_queries` converts
        inline `AuthoredQuery` instances on `self.query` to synthetic string names
        before this method is called for dependency tracking.

        Returns:
            Query name if options are populated from a named query, None otherwise.
            Returns None for inline AuthoredQuery (pre-promotion) or no query binding.
        """
        if self.options and self.options.query:
            return self.options.query
        if isinstance(self.query, str):
            return self.query
        return None


# Derived from the `internal` marker on the fields themselves rather than
# restated as a literal: a dump site that must not leak compilation internals
# (user-facing JSON/YAML output, the Cloud chart editor) excludes these, and
# marking one more field internal must not require finding every such site.
INTERNAL_VARIABLE_FIELDS: frozenset[str] = frozenset(
    name
    for name, field in Variable.model_fields.items()
    if isinstance(field.json_schema_extra, dict)
    and field.json_schema_extra.get("internal") is True
)
