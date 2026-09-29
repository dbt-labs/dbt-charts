"""Variable normalization, validation, and dependency analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.cache import (
    INHERIT_CACHE,
    CachePatch,
    resolve_cache_policy,
)
from dbt_charts.core.compile.template.jinja import (
    RESERVED_VARIABLE_NAMES,
    extract_variable_dependencies,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_VARIABLE_NAME_INVALID

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory
from dbt_charts.core.compile.models.board.normalized import Board, Layout
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    _BaseChartFields,
)
from dbt_charts.core.compile.models.query.authored import _BaseQueryFields
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
)
from dbt_charts.core.compile.models.source import source_cache_layer
from dbt_charts.core.compile.models.variable.authored import (
    Variable,
    VariableInputType,
)
from dbt_charts.core.compile.template.variables import (
    choice_member_type,
    coerce_variable_values,
    parse_iso_date,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_SOURCE_REQUIRED,
    ERR_UNKNOWN_VARIABLE,
    ERR_VALIDATION_FIELD,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError


@dataclass
class UnknownVariableError:
    """Structured record for a single undefined-variable reference."""

    surface: str  # "Chart", "Query", or "Variable"
    owner_name: str
    var_name: str

    def to_diagnostic(self, registry_keys: list[str]) -> Diagnostic:
        """Promote to a Diagnostic with a Did-you-mean suggestion.

        No range: an undefined variable reference is never resolved to a YAML
        line, in the authored board or any nested one — an accepted, permanent
        degradation of the "At: <file>:<line>" display for this error type.

        Args:
            registry_keys: Sorted variable names for fuzzy suggestion.
        """
        from dbt_charts.core.compile.parse.yaml_error_formatter import (
            suggest_similar_value,
        )

        suggestion = suggest_similar_value(self.var_name, registry_keys)
        hint_parts = [
            f"💡 {self.surface} {self.owner_name!r} references unknown variable"
            f" {self.var_name!r}."
        ]
        if suggestion:
            hint_parts.append(f"Did you mean {suggestion!r}?")
        hint_parts.append("See: dct docs variables")
        fields: dict[str, str] = {
            "surface": self.surface,
            "owner_name": self.owner_name,
            "var_name": self.var_name,
        }
        if suggestion:
            fields["suggestion"] = suggestion
        return Diagnostic.from_code(
            ERR_UNKNOWN_VARIABLE,
            message=ERR_UNKNOWN_VARIABLE.message_template.format(
                surface=self.surface,
                owner_name=self.owner_name,
                var_name=self.var_name,
            ),
            hint=" ".join(hint_parts),
            fields=fields,
        )


class VariableReferenceErrors(CompilationError):
    """Batch of undefined-variable errors from normalize_board() validation.

    Carries a list of already-promoted Diagnostics so the compiler can
    accumulate all of them without collapsing to a single raise.
    """

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"{len(diagnostics)} undefined variable reference(s)")


_QUERY_NAME_RE = re.compile(r"^(?:queries\.)?[a-zA-Z_][a-zA-Z0-9_]*$")
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def synthetic_query_name(kind: str, *parts: str | int) -> str:
    """Return a unique name for a compiler-generated query.

    The leading ``_`` keeps synthetic names from colliding with authored ones.
    The exact string is internal — callers store it as a pointer and resolve
    queries by it; nothing parses it back — so it only has to be unique and
    stable within a compile. e.g. ``("var_options", "region") → _var_options_region``.
    """
    return "_" + "_".join(str(p) for p in (kind, *parts))


def detect_variable_input_type(var: Variable) -> VariableInputType:
    """Infer the input type for a variable from its authored fields.

    When input is "auto" (the default), examines options, default, min/max/step,
    hidden, and column fields to determine the appropriate input type.
    If input is explicitly set to something other than "auto", returns it unchanged.

    Returns:
        The resolved VariableInputType string.
    """
    if var.input != "auto":
        return var.input

    has_options = var.options and (var.options.static or var.options.query)
    has_slider_fields = (
        var.min is not None or var.max is not None or var.step is not None
    )

    # List default (with or without options) → multiselect
    if isinstance(var.default, list):
        return "multiselect"

    # Slider fields → slider
    if has_slider_fields:
        return "slider"

    # Bool default → checkbox
    if isinstance(var.default, bool):
        return "checkbox"

    # Options present → select
    if has_options:
        return "select"

    # Column binding → select
    if var.column:
        return "select"

    # Not visible (hidden) with no other signals → text
    if not var.visible:
        return "text"

    # No signals → text (generic fallback)
    return "text"


def _synthetic_sql_query(
    sql: str,
    source: str,
    query_name: str,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> SqlQuery:
    """Build a synthetic SqlQuery, stamped with the resolved cache cascade.

    Compiler-generated queries (variable option lists, column bindings) have no
    query-level cache layer of their own, but they sit in a source and in a
    dashboard — so both those layers still apply, not just the project root.
    Mirrors ``normalize_query``'s cascade resolution for the layers in scope.
    """
    # Lazy import: compile.config sits above the model layer this module
    # otherwise stays within; imported at the single point of use.
    from dbt_charts.core.compile.config import shipped_cache_root

    cache = resolve_cache_policy(
        cache_root if cache_root is not None else shipped_cache_root(),
        source_cache_layer(sources, source, query_name),
        board_cache,
    )
    return SqlQuery(sql=sql, source=source, cache=cache)


def promote_inline_option_queries(
    variables: dict[str, Variable],
    query_registry: dict[str, AnyQuery],
    default_source: str | None = None,
    base_dir: ProjectDirectory | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> None:
    """Convert inline option queries on variables to synthetic named queries.

    Handles two inline forms:
    - ``var.options.query`` as raw SQL string → SqlQuery in registry
    - ``var.query`` as an AuthoredQuery instance → compiled AnyQuery in registry

    After promotion, both fields are replaced with the synthetic query name so
    the render layer can look them up by name in the registry.

    Modifies both variables and query_registry in place.

    Args:
        variables: Dict of variable definitions to process
        query_registry: Query registry to add synthetic queries to
        default_source: Default source for queries without explicit source
        sources: Named source config dicts, so the synthetic query still
            inherits its source's cache layer (not just the project root).
        board_cache: The declaring dashboard's `cache:` layer, which its
            variables' option queries inherit like any other query.
    """
    from dbt_charts.core.compile.normalize.queries import normalize_query

    for var_name, var in variables.items():
        # Promote inline AuthoredQuery on var.query
        if isinstance(var.query, _BaseQueryFields):
            sname = synthetic_query_name("var_query", var_name)
            query_registry[sname] = normalize_query(
                sname,
                var.query,
                default_source,
                sources=sources,
                base_dir=base_dir,
                cache_root=cache_root,
                board_cache=board_cache,
            )
            var.query = sname

        # Promote inline SQL string on var.options.query
        if not var.options or not var.options.query:
            continue

        query_ref = var.options.query.strip()
        if not query_ref or _QUERY_NAME_RE.match(query_ref):
            continue

        sname = synthetic_query_name("var_options", var_name)
        if default_source is None:
            raise CompilationError.from_code(ERR_SOURCE_REQUIRED, query_name=sname)
        query_registry[sname] = _synthetic_sql_query(
            query_ref,
            default_source,
            sname,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
        var.options.query = sname


def promote_column_option_queries(
    variables: dict[str, Variable],
    query_registry: dict[str, AnyQuery],
    default_source: str | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> None:
    """Promote column-bound variable options to synthetic named queries.

    Handles two column-binding forms:
    - ``var.options.column = "table.col"`` — column ref inside options block
    - ``var.column = "table.col"`` — top-level column shorthand

    For each valid binding, synthesizes:
    ``SELECT DISTINCT col FROM tbl ORDER BY col``
    as a ``SqlQuery`` under the key ``_var_options_{name}``, then rewrites
    the variable's option-query pointer (``var.options.query``) to point at it
    and clears the ``column`` field so the render layer uses the named-query path.

    Identifier safety (SQL injection guard) runs at compile time here — invalid
    identifiers raise ``CompilationError`` rather than being silently dropped.

    Modifies both variables and query_registry in place.

    Args:
        variables: Dict of variable definitions to process
        query_registry: Query registry to add synthetic queries to
        default_source: Default source for queries without explicit source
        sources: Named source config dicts, so the synthetic query still
            inherits its source's cache layer (not just the project root).
        board_cache: The declaring dashboard's `cache:` layer, which its
            variables' option queries inherit like any other query.

    Raises:
        CompilationError: If a column reference contains invalid SQL identifiers
            or is missing the required ``table.column`` dot notation.
    """
    from dbt_charts.core.compile.models.variable.authored import VariableOptions

    for var_name, var in variables.items():
        # options.column binds a source table only when it's the sole option
        # source; alongside an options.query it selects a result column (the
        # model's documented meaning), so there's nothing to synthesize.
        if var.options and var.options.column and not var.options.query:
            col_ref = var.options.column
            source_field = f"variables.{var_name}.options.column"
        elif var.column:
            col_ref = var.column
            source_field = f"variables.{var_name}.column"
        else:
            continue

        # Require table.column notation
        if "." not in col_ref:
            raise CompilationError.from_code(
                ERR_VALIDATION_FIELD,
                field_path=source_field,
                pydantic_msg=(
                    f"column reference {col_ref!r} must use 'table.column' "
                    "dot notation (e.g. 'regions.name')"
                ),
            )

        table_name, column_name = col_ref.rsplit(".", 1)

        # The table half may be qualified (schema.table, database.schema.table) —
        # a table outside the connection's default schema is otherwise unreachable
        # here. Both halves are interpolated into the SQL below, so the dot may
        # only ever join bare identifiers.
        if not all(
            _SQL_IDENTIFIER_RE.fullmatch(part) for part in table_name.split(".")
        ):
            raise CompilationError(
                f"{source_field}: table name {table_name!r} is not a valid SQL "
                "identifier — use only letters, digits, and underscores, optionally "
                "schema-qualified (e.g. 'gis.fact_sales')"
            )
        if not _SQL_IDENTIFIER_RE.fullmatch(column_name):
            raise CompilationError(
                f"{source_field}: column name {column_name!r} is not a valid SQL "
                "identifier — use only letters, digits, and underscores"
            )

        sql = f"SELECT DISTINCT {column_name} FROM {table_name} ORDER BY {column_name}"
        sname = synthetic_query_name("var_options", var_name)
        if default_source is None:
            raise CompilationError.from_code(ERR_SOURCE_REQUIRED, query_name=sname)
        query_registry[sname] = _synthetic_sql_query(
            sql,
            default_source,
            sname,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )

        # Rewrite the pointer: ensure options block exists, clear the column
        # field, and set options.query to the synthetic name so both render
        # and collect_all_query_names reach it through the normal named-query path.
        if var.options is None:
            var.options = VariableOptions()
        var.options.query = sname
        var.options.column = None
        # Clear the top-level column field unconditionally (handles both cases)
        var.column = None


def validate_authored_variable_name(name: str) -> None:
    """Raise unless `name` is a legal Jinja identifier.

    Callers must apply this only to authored `variables:` keys, before
    `generate_layout_variables` merges in its own hidden tabs/details
    variables (`_tab_<id>`, `_details_<id>`) — those derive from a
    free-form chart/tab `id:` and are never referenced as bare Jinja names.

    isidentifier(), not an ASCII regex: Jinja accepts Unicode identifiers
    ("région" is a legal variable name).
    """
    if not name.isidentifier():
        raise CompilationError.from_code(ERR_VARIABLE_NAME_INVALID, name=name)


def build_variable_registry(board: Board) -> dict[str, Variable]:
    """Build global variable registry by traversing compiled board tree.

    Collects all variables from the entire board tree (root and nested boards).
    Validates that variable names are unique across the entire tree.

    Args:
        board: Root Board to traverse

    Returns:
        Dict mapping variable name to Variable object (all variables from entire tree)

    Raises:
        CompilationError: If duplicate variable names found
    """
    registry: dict[str, Variable] = {}
    _collect_variables_recursive(board, registry)
    return registry


def _collect_variables_recursive(board: Board, registry: dict[str, Variable]) -> None:
    """Recursively collect variables from board and nested boards.

    Args:
        board: Board to collect variables from
        registry: Registry dict to add variables to

    Raises:
        CompilationError: If duplicate variable names found
    """
    # Add variables from this board
    if board.variables:
        for var_name, var in board.variables.items():
            if var_name in RESERVED_VARIABLE_NAMES:
                raise CompilationError(
                    f"Variable name '{var_name}' is reserved. "
                    f"It conflicts with a dbt charts helper or dbt Jinja builtin. "
                    f"Choose a different name."
                )
            if var_name in registry:
                raise CompilationError(
                    f"Duplicate variable name '{var_name}'. Variable names must be unique "
                    "within a file (and those that are imported)."
                )
            registry[var_name] = var

    # Recursively collect from nested boards in layout
    if board.layout.items:
        for item in board.layout.items:
            if item.type == "board" and item.board:
                _collect_variables_recursive(item.board, registry)


def validate_variable_references(board: Board) -> list[UnknownVariableError]:
    """Validate that all Jinja variable references resolve against the registry.

    Checks charts, queries, and variable filter templates. Returns one
    UnknownVariableError per undefined reference; callers promote these to
    compile errors.

    Args:
        board: Root Board with variable_registry set.

    Returns:
        Structured records for every undefined variable reference found.
    """

    errors: list[UnknownVariableError] = []
    defined_vars = (
        set(board.variable_registry.keys()) if board.variable_registry else set()
    )

    _collect_referenced_vars_recursive(board, defined_vars, errors)

    return errors


def _chart_direct_deps(chart: Chart) -> set[str]:
    """Extract variable names the chart itself references (title, subtitle, inline query).

    For NAMED queries (query_is_inline=False), query deps are excluded here —
    they appear in board.queries and are validated via the query loop, which gives
    the correct "Query 'q'" owner attribution.

    For INLINE queries (query_is_inline=True), the query is NOT in board.queries,
    so its variable_dependencies are included here and attributed to the chart.
    """
    deps: set[str] = set()
    # title/subtitle are on _SharedChartFields (not KpiChart); label is on KpiChart.
    deps |= extract_variable_dependencies(getattr(chart, "title", ""))
    deps |= extract_variable_dependencies(getattr(chart, "subtitle", ""))
    deps |= extract_variable_dependencies(getattr(chart, "label", ""))
    if (
        isinstance(chart, _BaseChartFields)
        and chart.query_is_inline
        and chart.query is not None
    ):
        deps |= chart.query.variable_dependencies
    return deps


def _collect_referenced_vars_recursive(
    board: Board,
    defined_vars: set[str],
    errors: list[UnknownVariableError],
) -> None:
    """Recursively collect undefined variable references from board and nested boards."""
    for chart_name, chart in board.charts.items():
        for ref in sorted(_chart_direct_deps(chart)):
            if ref not in defined_vars:
                errors.append(
                    UnknownVariableError(
                        surface="Chart",
                        owner_name=chart_name,
                        var_name=ref,
                    )
                )

    for query_name, query in board.queries.items():
        for ref in sorted(query.variable_dependencies):
            if ref not in defined_vars:
                errors.append(
                    UnknownVariableError(
                        surface="Query",
                        owner_name=query_name,
                        var_name=ref,
                    )
                )

    # Variable-level filter templates (cascading-dropdown case).
    # Synthetic option queries (names starting with "_") have their
    # `variable_dependencies` filtered to only valid/defined deps during
    # compilation (for the UI re-evaluation graph), so invalid refs are stripped
    # and the query loop misses them. Re-extract from SQL to catch those.
    #
    # Named option queries (user-declared in queries:, no leading "_") are already
    # processed by the query loop above with their full variable_dependencies.
    # Do NOT re-extract for them — that produces duplicate errors.
    if board.variable_registry:
        for var_name, var in board.variable_registry.items():
            option_query_name = var.get_option_query()
            if (
                option_query_name
                and option_query_name in board.queries
                and option_query_name.startswith("_")
            ):
                opt_query = board.queries[option_query_name]
                if isinstance(opt_query, SqlQuery) and opt_query.sql:
                    for ref in sorted(extract_variable_dependencies(opt_query.sql)):
                        if ref not in defined_vars:
                            errors.append(
                                UnknownVariableError(
                                    surface="Variable",
                                    owner_name=var_name,
                                    var_name=ref,
                                )
                            )

    if board.layout.items:
        for item in board.layout.items:
            if item.type == "board" and item.board:
                _collect_referenced_vars_recursive(item.board, defined_vars, errors)


def generate_layout_variables(layout: Layout) -> dict[str, Variable]:
    """Generate hidden variables for tabs and details in this layout only.

    Tabs get a hidden select variable (slug values).
    Details get a hidden checkbox variable (boolean).

    Only generates variables for the current layout level — nested boards
    handle their own variables during their own normalize_board() call.

    Returns:
        Dict of variable_name → Variable for all auto-generated variables.
    """
    from dbt_charts.core.compile.models.variable.authored import VariableOptions

    variables: dict[str, Variable] = {}

    # Tabs: generate a select variable
    if layout.type == "tabs" and layout.tab_variable and layout.tab_slugs:
        variables[layout.tab_variable] = Variable(
            input="select",
            visible=False,
            default=layout.tab_slugs[layout.default_tab or 0],
            options=VariableOptions(
                static=[*layout.tab_slugs],
            ),
        )

    # Walk direct child items for details variables only.
    # Don't recurse into nested boards — they already ran normalize_board()
    # which called generate_layout_variables() for their own layouts.
    for item in layout.items:
        if item.details_variable:
            default = False
            if item.board and item.board.meta:
                default = item.board.meta.get("details_expanded_default", False)
            variables[item.details_variable] = Variable(
                input="checkbox",
                visible=False,
                default=default,
            )

    return variables


# Type validators: input_type -> (type_check_fn, expected_type_desc)
# text/select/input/textarea/radio accept any scalar type (no validation needed)
_TYPE_VALIDATORS: dict[str, tuple[type | tuple[type, ...], str]] = {
    "number": ((int, float), "a number"),
    "slider": ((int, float), "a number"),
    "range": ((int, float), "a number"),
    "checkbox": (bool, "a boolean"),
    "multiselect": (list, "a list"),
    # A date default may be an ISO string ("2024-01-01") or a native date object
    # (unquoted YAML `2024-01-01`, which safe_load types as datetime.date).
    "date": ((str, date), "a string or ISO date"),
    "datepicker": ((str, date), "a string or ISO date"),
}


def _validate_date_string(var_name: str, label: str, value: str) -> None:
    """Raise CompilationError if *value* is not a parseable ISO date."""
    try:
        parse_iso_date(value)
    except ValueError:
        raise CompilationError(
            f"Variable '{var_name}': {label} must be an ISO date (YYYY-MM-DD), "
            f"got {value!r}"
        ) from None


def validate_variable_value(var_name: str, var: Variable, value: Any) -> None:
    """Validate a variable value against its input type.

    Called at compile time to validate default values. Uses data-driven
    validation via _TYPE_VALIDATORS dict.

    Args:
        var_name: Variable name (for error messages)
        var: Variable definition with input type
        value: Value to validate

    Raises:
        CompilationError: If value doesn't match expected type/format
    """
    if value is None:
        return

    input_type = var.input

    # A choice input's default must be what its `data_type` / numeric option
    # list says its values are. Asked of the runtime coercer rather than
    # restated, so compile and render cannot disagree on what converts.
    if input_type in ("select", "radio", "multiselect") and choice_member_type(var):
        try:
            coerce_variable_values({var_name: value}, {var_name: var})
        except ExecutionError as e:
            raise CompilationError.from_code(
                ERR_VALIDATION_FIELD,
                field_path=f"variables.{var_name}.default",
                pydantic_msg=str(e).removeprefix(f"Variable '{var_name}': "),
            ) from None

    # Special case: daterange needs structural validation (2-element list)
    if input_type == "daterange":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise CompilationError.from_code(
                ERR_VALIDATION_FIELD,
                field_path=f"variables.{var_name}.default",
                pydantic_msg=(
                    "daterange default must be [start, end], got "
                    f"{type(value).__name__}"
                ),
            )
        for i, v in enumerate(value):
            if v is None:
                continue
            if isinstance(v, datetime):
                raise CompilationError(
                    f"Variable '{var_name}': daterange[{i}] must be a calendar date, "
                    f"not a datetime, got {v!r}"
                )
            if isinstance(v, date):
                continue
            if not isinstance(v, str):
                raise CompilationError(
                    f"Variable '{var_name}': daterange[{i}] must be string, "
                    f"got {type(v).__name__}"
                )
            _validate_date_string(var_name, f"daterange[{i}]", v)
        return

    # select/radio accept any scalar (see the comment on _TYPE_VALIDATORS
    # above) but must reject a list/tuple outright — that shape belongs to
    # multiselect/daterange, and letting it through here means it passes
    # `dct validate` only to abort the whole board at render time, where
    # format_variable_display_value raises for the same reason without a
    # variable name to point at.
    if input_type in ("select", "radio") and isinstance(value, (list, tuple)):
        raise CompilationError(
            f"Variable '{var_name}': {input_type} default must be a scalar "
            f"value, got a list: {value!r}"
        )

    # Standard type validation from dict
    if input_type in _TYPE_VALIDATORS:
        expected_type, desc = _TYPE_VALIDATORS[input_type]
        if not isinstance(value, expected_type):
            raise CompilationError(
                f"Variable '{var_name}': {input_type} default must be {desc}, "
                f"got {type(value).__name__}"
            )

    # Slider/range: additional min/max bounds check
    if input_type in ("slider", "range"):
        if var.min is not None and value < var.min:
            raise CompilationError(
                f"Variable '{var_name}': {input_type} default {value} < min {var.min}"
            )
        if var.max is not None and value > var.max:
            raise CompilationError(
                f"Variable '{var_name}': {input_type} default {value} > max {var.max}"
            )

    # date/datepicker defaults: reject a datetime (a date must not carry a time),
    # and parse-validate string forms as ISO so a bad default fails at `dct validate`
    # instead of at query-execution time. Placed after the slider check so the
    # isinstance narrowing here cannot leak into that numeric comparison (date and
    # slider inputs are disjoint).
    if input_type in ("date", "datepicker"):
        if isinstance(value, datetime):
            raise CompilationError(
                f"Variable '{var_name}': {input_type} default must be a calendar "
                f"date, not a datetime, got {value!r}"
            )
        if isinstance(value, str) and value.strip():
            _validate_date_string(var_name, f"{input_type} default", value)


def validate_choice_type(var_name: str, var: Variable) -> None:
    """`data_type` and a static option list must describe the same values.

    Separate from `validate_variable_value`, which only runs for a variable
    with a default: the common query-driven choice has none, and its option
    list is still a contract. Scoped to the choice inputs, the only kinds
    whose values `data_type` types."""
    static = var.options.static if var.options else None
    if var.input not in ("select", "radio", "multiselect") or not static:
        return
    if var.data_type not in ("number", "date", "boolean"):
        return
    for option in static:
        try:
            coerce_variable_values({var_name: option}, {var_name: var})
        except ExecutionError as e:
            raise CompilationError.from_code(
                ERR_VALIDATION_FIELD,
                field_path=f"variables.{var_name}.options.static",
                pydantic_msg=(
                    f"{option!r} does not match data_type {var.data_type!r}: "
                    + str(e).removeprefix(f"Variable '{var_name}': ")
                ),
            ) from None


def compute_variable_dependencies(
    variables: dict[str, Variable],
    query_registry: dict[str, AnyQuery],
) -> None:
    """Compute variable dependencies for cascading dropdowns.

    Analyzes each variable's options query SQL to find Jinja references
    to other variables. Updates the variable_dependencies field in-place.

    Also detects circular dependencies and raises an error if found.

    Args:
        variables: Dict of variable definitions to analyze
        query_registry: Query registry to look up options queries

    Raises:
        CompilationError: If circular variable dependencies detected

    Example:
        If variable 'state' has options.query = 'state_options' and the
        state_options query SQL contains {{ filter('country', country) }},
        then state.variable_dependencies will be {'country'}.
    """
    # Build dependency graph
    dep_graph: dict[str, set[str]] = {}

    for var_name, var in variables.items():
        deps: set[str] = set()

        # Get the options query name
        options_query_name = var.get_option_query()
        if options_query_name and options_query_name in query_registry:
            query = query_registry[options_query_name]

            # Extract variable dependencies from query SQL and setup_sql
            if isinstance(query, SqlQuery) and query.sql:
                sql_deps = extract_variable_dependencies(query.sql)
                # Filter to only include variables that actually exist
                deps = sql_deps & set(variables.keys())
            if isinstance(query, SqlQuery) and query.setup_sql:
                setup_deps = extract_variable_dependencies(query.setup_sql)
                deps |= setup_deps & set(variables.keys())

        # Also extract deps from enabled Jinja expressions so the UI
        # re-evaluates enabled state when any referenced variable changes.
        if isinstance(var.enabled, str):
            enabled_deps = extract_variable_dependencies(var.enabled)
            deps |= enabled_deps & set(variables.keys())

        # Remove self-reference (a variable can't depend on itself)
        deps.discard(var_name)

        # Update the variable's dependencies
        var.variable_dependencies = frozenset(deps)
        dep_graph[var_name] = deps

    # Check for circular dependencies
    _detect_circular_variable_deps(dep_graph)


def _detect_circular_variable_deps(dep_graph: dict[str, set[str]]) -> None:
    """Detect circular dependencies in variable dependency graph.

    Uses depth-first search to find cycles.

    Args:
        dep_graph: Dict mapping variable name to set of dependencies

    Raises:
        CompilationError: If circular dependency found
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if node in rec_stack:
            # Found a cycle - build the cycle path for error message
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            raise CompilationError(
                f"Circular variable dependency detected: {' → '.join(cycle)}. "
                "Variables cannot depend on each other in a cycle. "
                "Check the options queries for these variables."
            )

        if node in visited:
            return

        visited.add(node)
        rec_stack.add(node)

        for dep in dep_graph.get(node, set()):
            if dep in dep_graph:  # Only follow known variables
                dfs(dep, path + [node])

        rec_stack.remove(node)

    for var_name in dep_graph:
        dfs(var_name, [])


def get_transitive_variable_dependencies(
    variable_name: str,
    variables: dict[str, Variable],
) -> set[str]:
    """Get all transitive dependencies for a variable.

    If variable A depends on B, and B depends on C, then A's transitive
    dependencies are {B, C}.

    Args:
        variable_name: Variable to get dependencies for
        variables: Dict of all variables

    Returns:
        Set of all variable names that this variable depends on (directly or transitively)
    """
    result: set[str] = set()
    visited: set[str] = set()

    def collect_deps(var_name: str) -> None:
        if var_name in visited:
            return
        visited.add(var_name)

        var = variables.get(var_name)
        if not var:
            return

        for dep in var.variable_dependencies:
            result.add(dep)
            collect_deps(dep)

    collect_deps(variable_name)
    return result


def expand_chart_variable_dependencies(
    charts: dict[str, Chart],
    variables: dict[str, Variable],
) -> None:
    """Expand chart variable dependencies to include transitive dependencies.

    If a chart depends on variable A, and A depends on B (cascading dropdown),
    then the chart should also track dependency on B so the UI knows to
    re-render when B changes.

    Modifies charts in place.

    Args:
        charts: Dict of compiled charts
        variables: Dict of variables with computed dependencies
    """
    for chart in charts.values():
        expanded_deps: set[str] = set()

        # For each variable the chart directly depends on
        for var_name in chart.variable_dependencies:
            expanded_deps.add(var_name)
            # Add all transitive dependencies of that variable
            transitive = get_transitive_variable_dependencies(var_name, variables)
            expanded_deps |= transitive

        # Update chart's variable dependencies
        chart.variable_dependencies = frozenset(expanded_deps)
