"""Parameterized query rendering module.

Stage: COMPILE
Purpose: Render Jinja templates to parameterized SQL for SQL injection prevention.

Instead of interpolating values directly into SQL strings (vulnerable to injection):
    "SELECT * FROM orders WHERE date >= '2024-01-01'"

This module renders to parameterized queries (safe):
    sql = "SELECT * FROM orders WHERE date >= $1"
    params = ["2024-01-01"]

Entry Points:
    - render_parameterized(template, variables, dialect, warehouse) ->
      ParameterizedQuery. `dialect` is the placeholder style; `warehouse` is
      the engine whose SQL the helpers spell, and defaults to `dialect` — the
      two differ only where placeholders are an internal round trip that no
      engine parses (see InlinePlaceholderDialect).
    - make_filter_helper(emit, dialect) / make_filter_date_range_helper(emit,
      dialect) -> the filter() helpers Jinja calls, over a caller-supplied
      value-emission strategy. Both callers pass a collector's add_param; they
      differ in the placeholder style the collector's dialect emits, and in
      whether the result is bound by a driver or flattened to literals for one
      that takes no bindings. The dialect is the warehouse, not necessarily
      the collector's: it spells "compare this column as a date" per engine —
      see SQLDialect.date_expr().

The executor then uses: cursor.execute(sql, params)

Security Notes:
    - Values reach SQL only through the emission strategy, as a collected
      parameter standing behind a placeholder; never by interpolation in this
      module
    - Operator validation uses VALID_OPERATORS allowlist
    - Column/table names are validated to prevent injection via identifiers
    - Legacy filter helpers in jinja.py should NOT be used (deprecated)

Dependencies:
    - jinja2
    - dbt_charts.core.dialects (for parameter placeholder styles)
"""

import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, NoReturn, SupportsIndex

from jinja2 import (
    StrictUndefined,
    TemplateError,
    TemplateSyntaxError,
    UndefinedError,
)

from dbt_charts.core.compile.errors import JinjaError, TemplateOutputTooLargeError
from dbt_charts.core.compile.template._helpers import (
    _LenientUndefined,
    _QueryNamespace,
    template_error_location,
)
from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    render_with_budget,
)
from dbt_charts.core.dialects import VALID_OPERATORS, SQLDialect, get_dialect

# Bare identifiers joined by dots, to any depth: `col`, `table.col`, and
# `schema.table.col` for a table outside the connection's default schema.
_VALID_IDENTIFIER_PATTERN = re.compile(
    r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*"
)


def _validate_identifier(identifier: str, identifier_type: str = "column") -> None:
    """Validate SQL identifier (column or table.column) to prevent injection.

    Args:
        identifier: The identifier to validate
        identifier_type: Type for error messages ("column", "table", etc.)

    Raises:
        ValueError: If identifier is invalid (empty or contains invalid chars)

    Security:
        Only allows alphanumeric characters, underscores, and dots separating
        them. Every dot-separated part must start with a letter or underscore.

    Limitations:
        - Does not support quoted identifiers ("Column Name")
        - Does not support Unicode identifiers
        For these cases, use raw SQL with manual parameterization.
    """
    if not identifier:
        raise ValueError(f"Invalid {identifier_type} name: cannot be empty")

    if not _VALID_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            f"Invalid {identifier_type} name: {identifier!r}. "
            f"Must contain only letters, numbers, and underscores, optionally "
            f"qualified with dots (table.column, schema.table.column). Each part "
            f"must start with a letter or underscore."
        )


def _validate_operator(operator: object) -> str:
    """Validate SQL operator against allowlist to prevent injection.

    Typed ``object`` because the value arrives from Jinja template text — an
    author can pass a list or int here, and the validator is the boundary
    that must reject it with a usable error.

    Args:
        operator: The operator to validate

    Returns:
        The normalized (uppercase) operator

    Raises:
        ValueError: If operator is not a string or is not in the allowlist
    """
    if not isinstance(operator, str):
        raise ValueError(
            f"operator must be a string, got {type(operator).__name__}: {operator!r}. "
            f"Must be one of: {', '.join(sorted(VALID_OPERATORS))}"
        )
    op_upper = operator.upper().strip()
    if op_upper not in VALID_OPERATORS:
        raise ValueError(
            f"Invalid SQL operator: {operator!r}. "
            f"Must be one of: {', '.join(sorted(VALID_OPERATORS))}"
        )
    return operator


@dataclass
class ParameterizedQuery:
    """Result of parameterized SQL rendering.

    Attributes:
        sql: SQL with parameter placeholders (e.g., $1, $2)
        params: List of parameter values in order
        template_hash: Hash of the original template for caching
    """

    sql: str
    params: list[Any] = field(default_factory=list)
    template_hash: str = ""


# How one runtime variable value becomes the SQL text standing for it. The
# filter helpers never write a value into SQL themselves — they call this, and
# the caller's collector decides which placeholder style stands in for the
# value until something downstream binds or inlines it.
Emitter = Callable[[Any], str]  # type-state: explicit_any — a runtime variable value


class _ParameterCollector:
    """Collects parameters during Jinja rendering.

    This class intercepts variable access during Jinja rendering,
    collecting values for parameterization and returning placeholders.
    """

    def __init__(
        self,
        variables: dict[str, Any],
        dialect: SQLDialect,
        exclude_vars: set[str] | None = None,
    ):
        """Initialize parameter collector.

        Args:
            variables: Original variable values
            dialect: SQL dialect for placeholder generation
            exclude_vars: Variable names to exclude from parameterization
                (e.g., 'queries' namespace, helper functions)
        """
        self.variables = variables
        self.dialect = dialect
        self.exclude_vars = exclude_vars or set()
        self.params: list[Any] = []
        self._param_index = 0
        # Deduplication only works for indexed-placeholder dialects ($1, $2, …) where
        # the same $N can repeat in SQL against a single param entry.  For positional-only
        # placeholders (?, %s) every occurrence needs its own param entry.
        self._deduplicate = dialect.param(1) != dialect.param(2)
        self._seen_params: dict[tuple[Any, ...], int] = {}

    def add_param(
        self,
        value: Any,  # type-state: explicit_any — a runtime variable value; any scalar or list the author bound
    ) -> str:  # type-state: explicit_any — a runtime variable value; any scalar or list the author bound
        """Append one value and return its placeholder.

        No dedup: the caller is the filter helpers, whose values arrive as
        expression results rather than named variables, so there is no name to
        key a repeat on.
        """
        self._param_index += 1
        self.params.append(value)
        return self.dialect.param(self._param_index)

    def get_param(self, name: str, value: Any) -> str:
        """Get parameter placeholder for a variable.

        If the same variable with the same value is used multiple times,
        reuses the same parameter. Different values get new parameters,
        even if they have the same variable name (handles {% set %} reassignment).

        Args:
            name: Variable name
            value: Variable value

        Returns:
            Parameter placeholder string (e.g., '$1')
        """
        # Create a key based on name and value
        # Use id() for mutable objects, value itself for immutable primitives
        try:
            # Try to use the value directly (works for hashable types)
            key = (name, value)
            hash(key)  # Test if hashable
        except TypeError:
            # For unhashable types (lists, dicts), use object identity
            key = (name, id(value))

        # Reuse the same placeholder index only for indexed-placeholder dialects.
        if self._deduplicate and key in self._seen_params:
            return self.dialect.param(self._seen_params[key])

        # Add new parameter
        self._param_index += 1
        self._seen_params[key] = self._param_index
        self.params.append(value)
        return self.dialect.param(self._param_index)


class _NullValue:
    """Marker for NULL values in parameterized context.

    Renders as 'NULL' in SQL and is recognized by filter helpers
    as requiring special handling (returning 1=1 for nullable filters).
    """

    def __str__(self) -> str:
        return "NULL"

    def __repr__(self) -> str:
        return "NULL"

    def __bool__(self) -> bool:
        # NULL values are falsy for conditionals
        return False

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _NullValue) or other is None

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)


class _TokenText(str):
    """The placeholder text a parameterized variable renders as.

    A string transform on this text corrupts the placeholder: nothing
    downstream matches it, so the parameter is silently never substituted.
    The overrides below raise at render, naming the variable — the treatment
    `| int` / `| float` get on `_ParameterizedValue` — for the transforms
    Jinja routes through these `str` methods (`| upper`, `| lower`,
    `| capitalize`, `| replace`, and explicit method calls). They are not the
    whole defense: filters that bypass `str` methods are caught by the
    token-presence check after rendering (dropped tokens) or the placeholder
    guard in `inline_params_for_dialect` (mangled tokens). `| trim` with no
    argument cannot change a token (no edge whitespace) and stays allowed.
    """

    __slots__ = ("_var_name",)
    _var_name: str

    def __new__(cls, token: str, var_name: str) -> "_TokenText":
        self = super().__new__(cls, token)
        self._var_name = var_name
        return self

    def _refuse(self, transform: str, sql_equivalent: str | None) -> NoReturn:
        """Raise the render-time error for a token-corrupting transform.

        sql_equivalent is a SQL function spelling valid on the supported
        warehouses, or None when there is no single portable spelling — the
        message then stays generic rather than prescribing invalid SQL.
        """
        remedy = (
            f"e.g. {sql_equivalent}('{{{{ {self._var_name} }}}}')"
            if sql_equivalent
            else "with an equivalent SQL expression"
        )
        raise JinjaError(
            f"Variable '{self._var_name}': the '{transform}' string transform "
            f"cannot be applied to a parameterized variable — it would corrupt "
            f"the parameter placeholder and the value would never be bound. "
            f"Apply the transform in SQL instead, {remedy}."
        )

    def upper(self) -> str:
        self._refuse("upper", "upper")

    def lower(self) -> str:
        self._refuse("lower", "lower")

    def title(self) -> str:
        self._refuse("title", None)

    def capitalize(self) -> str:
        self._refuse("capitalize", None)

    def casefold(self) -> str:
        self._refuse("casefold", "lower")

    def swapcase(self) -> str:
        self._refuse("swapcase", None)

    def replace(self, old: str, new: str, count: SupportsIndex = -1, /) -> str:
        self._refuse("replace", None)

    def translate(self, table: Any, /) -> str:
        self._refuse("translate", None)


class _ParameterizedValue:
    """Wrapper for parameterized variable values.

    When rendered by Jinja, returns the parameter placeholder.
    """

    def __init__(self, name: str, value: Any, collector: _ParameterCollector):
        self._name = name
        self._value = value
        self._collector = collector

    def __str__(self) -> str:
        rendered = self._collector.get_param(self._name, self._value)
        return _TokenText(rendered, self._name)

    def __repr__(self) -> str:
        return self.__str__()

    # Support common operations that might be used in templates
    def __eq__(self, other: Any) -> bool:
        return self._value == other

    def __ne__(self, other: Any) -> bool:
        return self._value != other

    def __bool__(self) -> bool:
        return bool(self._value)

    def __iter__(self) -> Iterator[Any]:
        if hasattr(self._value, "__iter__"):
            return iter(self._value)
        raise TypeError(f"'{type(self._value).__name__}' is not iterable")

    def __getitem__(self, key: Any) -> Any:
        if hasattr(self._value, "__getitem__"):
            return self._value[key]
        raise TypeError(f"'{type(self._value).__name__}' is not subscriptable")

    def __int__(self) -> int:
        # Jinja's | int filter calls int(value) and catches TypeError, returning
        # 0 on failure — so without this override the filter silently emits 0.
        # Raising here bypasses that swallowing and surfaces the error.
        raise JinjaError(
            f"Variable '{self._name}': the '| int' filter cannot coerce a parameterized "
            f"variable — it would silently return 0. Use SQL arithmetic on the variable "
            f"directly instead: {{{{ {self._name} }}}} - 1 emits $N - 1 where $N is "
            f"the bound parameter (e.g. INTERVAL -({{{{ {self._name} }}}} - 1) MONTH)."
        )

    def __index__(self) -> int:
        raise JinjaError(
            f"Variable '{self._name}': integer index conversion is not supported "
            f"on parameterized variables. Use SQL arithmetic on the variable "
            f"directly instead: {{{{ {self._name} }}}} - 1 emits the bound parameter "
            f"minus 1."
        )

    def __float__(self) -> float:
        raise JinjaError(
            f"Variable '{self._name}': the '| float' filter cannot coerce a parameterized "
            f"variable — it would silently return 0.0. Use SQL arithmetic on the variable "
            f"directly instead: {{{{ {self._name} }}}} * 1.0 or cast in SQL."
        )


def _compute_template_hash(template: str) -> str:
    """Compute hash of template for caching.

    Args:
        template: SQL template string

    Returns:
        SHA-256 hash of template (first 16 chars)
    """
    return hashlib.sha256(template.encode()).hexdigest()[:16]


def render_parameterized(
    template: str,
    variables: dict[str, Any] | None = None,
    dialect: SQLDialect | None = None,
    profile_type: str = "postgres",
    strict: bool = True,
    warehouse: SQLDialect | None = None,
) -> ParameterizedQuery:
    """Render Jinja template to parameterized SQL.

    Converts {{ variable }} expressions to database parameter placeholders.

    For simple variable substitution like:
        "SELECT * FROM orders WHERE region = '{{ region }}'"

    Produces:
        sql = "SELECT * FROM orders WHERE region = $1"
        params = ["North"]

    Complex Jinja expressions (conditionals, loops, filters) are still
    evaluated, but variable values within them become parameters.

    Args:
        template: SQL template with Jinja expressions
        variables: Variable values for substitution
        dialect: SQL dialect instance (overrides profile_type)
        profile_type: Database type string (e.g., 'postgres', 'duckdb')
        strict: If True (default), raises error on undefined variables.
                If False, undefined variables become empty strings (useful for
                chart editor where variables may not all be set).
        warehouse: The engine whose SQL the filter helpers spell (see
            `SQLDialect.date_expr`). Defaults to `dialect`; pass it when
            `dialect` is a placeholder style no engine parses.

    Returns:
        ParameterizedQuery with SQL, params, and template_hash

    Raises:
        JinjaError: If template syntax is invalid or required variable is missing
            (only in strict mode)

    Example:
        >>> result = render_parameterized(
        ...     "SELECT * FROM users WHERE status = '{{ status }}'",
        ...     variables={"status": "active"},
        ...     profile_type="postgres"
        ... )
        >>> result.sql
        'SELECT * FROM users WHERE status = $1'
        >>> result.params
        ['active']
    """
    if not template:
        return ParameterizedQuery(sql="", params=[], template_hash="")

    # Quick check for Jinja syntax - if no Jinja, return as-is
    if "{{" not in template and "{%" not in template:
        return ParameterizedQuery(
            sql=template,
            params=[],
            template_hash=_compute_template_hash(template),
        )

    variables = variables or {}

    # Get dialect
    if dialect is None:
        dialect = get_dialect(profile_type)

    # Create parameter collector
    collector = _ParameterCollector(
        variables=variables,
        dialect=dialect,
        exclude_vars={"queries", "filter", "filter_date_range"},
    )

    # Build context with parameterized wrapper for each variable
    context: dict[str, Any] = {}

    for name, value in variables.items():
        if name in collector.exclude_vars:
            # Keep special context items as-is
            context[name] = value
        elif value is None:
            # None values should render as NULL and be tracked as None
            # We use a special marker that the filter helpers recognize
            context[name] = _NullValue()
        else:
            # Wrap value for parameterization
            context[name] = _ParameterizedValue(name, value, collector)

    if warehouse is None:
        warehouse = dialect
    context["filter"] = make_filter_helper(collector.add_param, warehouse)
    context["filter_date_range"] = make_filter_date_range_helper(
        collector.add_param, warehouse
    )

    # Handle queries namespace if present
    if "queries" in variables:
        context["queries"] = variables["queries"]

    try:
        undefined_cls = StrictUndefined if strict else _LenientUndefined
        env = BoardTemplateEnvironment(undefined=undefined_cls)
        jinja_template = env.from_string(template)
        rendered_sql = render_with_budget(jinja_template, context)

        # Clean up any surrounding quotes around parameter placeholders
        # e.g., "'$1'" -> "$1" for proper parameterization
        rendered_sql = _clean_parameter_quotes(
            rendered_sql, dialect, len(collector.params)
        )

        _check_placeholders_present(rendered_sql, collector, dialect)

        return ParameterizedQuery(
            sql=rendered_sql,
            params=collector.params,
            template_hash=_compute_template_hash(template),
        )

    except TemplateOutputBudgetExceeded as e:
        raise TemplateOutputTooLargeError(
            emitted_bytes=e.emitted_bytes, ceiling=e.ceiling
        ) from e
    except UndefinedError as e:
        raise JinjaError(
            f"Undefined variable: {e}{template_error_location(template, e)}", template
        ) from e
    except TemplateSyntaxError as e:
        raise JinjaError(
            f"Template syntax error: {e}{template_error_location(template, e)}",
            template,
        ) from e
    except TemplateError as e:
        # Catches the sandbox's SecurityError, a TemplateRuntimeError sibling of
        # UndefinedError rather than a subclass. Uncoded it reaches the executor
        # and stamps ERR-INTERNAL, which this repo treats as a bug rather than
        # an author-facing error.
        raise JinjaError(f"Template error: {e}", template) from e
    except ValueError as e:
        # Re-raise validation errors (operator, column name) with context
        raise JinjaError(f"Validation error: {e}", template) from e
    except (KeyError, TypeError) as e:
        # Key lookup failures, type errors
        raise JinjaError(f"Template error: {e}", template) from e


def _check_placeholders_present(
    sql: str, collector: _ParameterCollector, dialect: SQLDialect
) -> None:
    """Every collected parameter's placeholder must survive rendering verbatim.

    A Jinja filter can rewrite or remove a placeholder after the value was
    collected (`| urlencode` and `| tojson` escape the token's NUL bytes;
    `| trim` with a chars argument strips them) — the value would then never
    be bound. Catching it here names the variable; the guards further down
    can only see a mangled token, not a vanished one.

    On dialects whose placeholder is the same for every index (`?`, `%s`) a
    later occurrence stands in for a missing earlier one — those styles are
    driver-bound, and the driver's own arity check owns that mismatch.
    """
    index_names = {idx: key[0] for key, idx in collector._seen_params.items()}
    for idx in range(1, len(collector.params) + 1):
        placeholder = dialect.param(idx)
        if placeholder not in sql:
            # Positional styles overwrite repeated keys in _seen_params, so an
            # early index may have no recorded name — degrade to a bare label.
            name = index_names.get(idx)
            label = f"Variable '{name}'" if name else "A variable"
            raise JinjaError(
                f"{label}: its parameter placeholder is missing from "
                f"the rendered SQL — a Jinja filter transformed or removed it, "
                f"so the value would never be bound. Apply the transform in "
                f"SQL instead."
            )


def _clean_parameter_quotes(sql: str, dialect: SQLDialect, param_count: int) -> str:
    """Remove quotes around parameter placeholders.

    SQL like "WHERE name = '$1'" should become "WHERE name = $1"
    because the database driver handles string quoting for parameters.

    The placeholder shapes come from ``dialect.param`` rather than a chain of
    dialect names, so a dialect cannot be silently omitted: leaving the author's
    quotes on inlines a *quoted* literal inside them, and an empty value then
    yields ``''''`` — which BigQuery's tokenizer reads as a triple-quoted string
    opener and scans to EOF on.

    Uses regex patterns to avoid incorrectly matching placeholders that appear
    inside SQL string literals or comments.

    Args:
        sql: Rendered SQL string
        dialect: SQL dialect
        param_count: Number of parameters

    Returns:
        SQL with quotes removed from around parameter placeholders
    """
    if param_count == 0:
        return sql

    result = sql
    # Deduped: ?-style and %s-style dialects return one placeholder for every index.
    for placeholder in dict.fromkeys(dialect.params(param_count)):
        pattern = re.escape(placeholder)
        result = re.sub(rf"'({pattern})'", r"\1", result)
        result = re.sub(rf'"({pattern})"', r"\1", result)

    return result


def make_filter_helper(emit: Emitter, dialect: SQLDialect) -> Callable[..., str]:
    """Create the filter() helper Jinja calls, over a value-emission strategy.

    Returns a function that generates filter clauses:
        {{ filter('column', value) }} -> "column = <emitted>"
        {{ filter('column', value, '!=') }} -> "column != <emitted>"
        {{ filter('column', [a, b]) }} -> "column IN (<emitted>, <emitted>)"
        {{ filter('column', a_date, '>=') }}
            -> "<dialect.date_expr('column')> >= <emitted>"

    A calendar date compares the column as a date, whatever the column's own
    type: a TIMESTAMP column compared bare against a DATE value is a type
    error on BigQuery and a silent midnight comparison everywhere else (`=`
    never matches a row with a time of day). A datetime value carries a time
    of day and leaves the column alone.

    Security:
        - Column names are validated against SQL injection
        - Operators are validated against VALID_OPERATORS allowlist
        - Values only ever reach SQL through `emit`, never interpolated here

    Args:
        emit: The value-emission strategy, and the only route a value takes
            into the statement. Both callers pass a `_ParameterCollector`'s
            `add_param`; the collector's dialect decides the placeholder style,
            and what resolves it — a driver binding the parameter, or the
            inline pass flattening it to an escaped literal for dbt's
            adapter.execute(), which takes no bindings.
        dialect: The warehouse the SQL runs on — its `date_expr()` spells
            "compare this column as a date".

    Returns:
        Filter helper function
    """

    def filter_helper(
        column: str,
        value: Any,
        operator: str = "=",
        *,
        none: str = "allow",
    ) -> str:
        """Generate parameterized filter clause.

        Args:
            column: Column name (must be valid SQL identifier, not user input)
            value: Filter value (will be parameterized)
            operator: SQL operator (validated against allowlist)
            none: Fallback when value is null/empty/_NullValue. 'allow' (default)
                returns '1=1' (no constraint, show all rows). 'deny' returns
                '1=0' (zero rows).

        Returns:
            SQL clause with parameter placeholder, '1=1' (allow all) on missing
            value, or '1=0' (deny all) on missing value when none='deny'.

        Raises:
            ValueError: If column name, operator, or `none` is invalid.
        """
        if none not in ("allow", "deny"):
            raise ValueError(f"none= must be 'allow' or 'deny', got {none!r}")

        # Validate column name to prevent SQL injection
        _validate_identifier(column, "column")

        # Handle None/empty/NullValue - return fallback per `none` kwarg
        if value is None or value == "" or isinstance(value, _NullValue):
            return "1=0" if none == "deny" else "1=1"

        # Unwrap if it's already a ParameterizedValue
        actual_value = value._value if isinstance(value, _ParameterizedValue) else value

        _validate_operator(operator)

        if isinstance(actual_value, list):
            # Refused before the emptiness check so a malformed board fails
            # whether or not the viewer selected anything.
            op = operator.upper().strip()
            if op in ("=", "IN"):
                membership = "IN"
            elif op in ("!=", "<>", "NOT IN"):
                membership = "NOT IN"
            else:
                raise ValueError(
                    f"filter(): operator {operator!r} cannot apply to a list "
                    f"value; use 'IN' (default) or 'NOT IN'"
                )
            items = [
                item._value if isinstance(item, _ParameterizedValue) else item
                for item in actual_value
            ]
            if not items:
                return "1=0" if none == "deny" else "1=1"
            if any(item is None or isinstance(item, _NullValue) for item in items):
                raise ValueError(
                    f"filter(): a list value cannot contain NULL ({items!r}); "
                    f"IN never matches NULL and NOT IN matches nothing"
                )
            dated = [_is_calendar_date(item) for item in items]
            if all(dated):
                column = dialect.date_expr(column)
            elif any(dated):
                raise ValueError(
                    f"filter(): a list mixes dates with other values "
                    f"({items!r}); the column can only be compared one way"
                )
            emitted = ", ".join(emit(item) for item in items)
            return f"{column} {membership} ({emitted})"

        if _is_calendar_date(actual_value):
            column = dialect.date_expr(column)
        return f"{column} {operator} {emit(actual_value)}"

    return filter_helper


def _is_calendar_date(
    value: object,  # type-state: object_annotation — a runtime variable value, any scalar the author bound
) -> bool:
    """A date without a time of day — `datetime` is a `date` subclass."""
    return isinstance(value, date) and not isinstance(value, datetime)


def make_filter_date_range_helper(
    emit: Emitter, dialect: SQLDialect
) -> Callable[..., str]:
    """Create the filter_date_range() helper, over the same emission strategy.

    Returns a function that generates BETWEEN clauses:
        {{ filter_date_range('date', date_range) }}
        -> "<dialect.date_expr('date')> BETWEEN <emitted> AND <emitted>"

    Security:
        - Column names are validated against SQL injection
        - Date values only ever reach SQL through `emit`

    Args:
        emit: Value-emission strategy — see `make_filter_helper`.
        dialect: Target SQL dialect — its `date_expr()` spells the
            "compare this column as a date" comparison, which differs on
            SQLite (no native DATE type).

    Returns:
        Date range filter helper function
    """

    def filter_date_range_helper(
        column: str,
        date_range: Any,
    ) -> str:
        """Generate parameterized date range filter.

        Args:
            column: Date column name (must be valid SQL identifier)
            date_range: Tuple/list of [start, end] dates

        Returns:
            SQL BETWEEN clause with parameter placeholders

        Raises:
            ValueError: If column name is invalid
        """
        # Validate column name to prevent SQL injection
        _validate_identifier(column, "column")

        if not date_range or isinstance(date_range, _NullValue):
            return "1=1"

        # Unwrap if it's a ParameterizedValue
        actual_value = (
            date_range._value
            if isinstance(date_range, _ParameterizedValue)
            else date_range
        )

        # Handle JSON string
        if isinstance(actual_value, str):
            import json

            try:
                actual_value = json.loads(actual_value)
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"Invalid date_range JSON: {actual_value!r}") from e

        if not isinstance(actual_value, (list, tuple)):
            raise ValueError(
                f"date_range must be a list/tuple [start, end], got {type(actual_value).__name__}"
            )

        if len(actual_value) != 2:
            raise ValueError(
                f"date_range must have exactly 2 elements [start, end], got {len(actual_value)}"
            )

        start, end = actual_value
        if not start or not end:
            return "1=1"  # Empty dates = no filter (intentional)

        # A date range is inclusive of the whole end day, so the column is
        # compared as a date; bounds carrying a time of day are compared raw.
        timed = (isinstance(start, datetime), isinstance(end, datetime))
        if all(timed):
            return f"{column} BETWEEN {emit(start)} AND {emit(end)}"
        if any(timed):
            raise ValueError(
                f"date_range mixes a date with a datetime ({start!r}, {end!r}); "
                f"the column can only be compared one way"
            )
        return f"{dialect.date_expr(column)} BETWEEN {emit(start)} AND {emit(end)}"

    return filter_date_range_helper


def render_parameterized_with_queries(
    template: str,
    variables: dict[str, Any] | None = None,
    queries: dict[str, Any] | None = None,
    dialect: SQLDialect | None = None,
    profile_type: str = "postgres",
    strict: bool = True,
    warehouse: SQLDialect | None = None,
) -> ParameterizedQuery:
    """Render parameterized SQL with query reference support.

    Like render_parameterized but also handles {{ queries.* }} references.
    Used on both the render path (executor.py) and the direct-query path
    (adapter_registry.execute) so composition cannot be silently omitted.

    Args:
        template: SQL template with Jinja expressions
        variables: Variable values for substitution
        queries: Query registry for {{ queries.* }} resolution
        dialect: SQL dialect instance
        profile_type: Database type string
        strict: If True (default), raises on undefined variables.
        warehouse: The engine whose SQL the filter helpers spell; see
            `render_parameterized`.

    Returns:
        ParameterizedQuery with SQL, params, and template_hash
    """
    variables_with_queries: dict[str, Any] | None
    if queries:
        # Include queries in variables for resolution
        variables_with_queries = dict(variables or {})
        variables_with_queries["queries"] = _QueryNamespace(queries)
    else:
        variables_with_queries = variables

    return render_parameterized(
        template=template,
        variables=variables_with_queries,
        dialect=dialect,
        profile_type=profile_type,
        strict=strict,
        warehouse=warehouse,
    )
