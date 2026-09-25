"""dbt adapter integration for SQL query execution.

Stage: EXECUTE (inside RENDER stage)
Purpose: Execute SQL queries via dbt's adapter system.

This adapter leverages dbt's adapter API to execute SQL queries,
supporting dbt-specific features like ref() and source() resolution.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TID251 — reads dbt profiles.yml/dbt_project.yml
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.project import Project

from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_sql_query,
)
from dbt_charts.core.compile.sql_guard import validate_select_only
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.parameterized import (
    _check_placeholders_present,
    _ParameterCollector,
    make_filter_date_range_helper,
    make_filter_helper,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_INVALID_TYPE
from dbt_charts.core.dialects import (
    DIALECTS,
    SQLDialect,
    get_dialect,
    list_dialects,
)
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    classify_warehouse_error,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    ConnectionSetupFailed,
    open_connection,
)
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
)


def _read_profiles_yml(
    dbt_project_path: Path, profiles_dir: Path | None = None
) -> dict[str, Any]:
    """Read and return profiles.yml using the canonical resolution order.

    Resolution order: profiles_dir → DBT_PROFILES_DIR → project-local → ~/.dbt.
    Raises FileNotFoundError if none exists.

    Args:
        dbt_project_path: The dbt project root (where dbt_project.yml lives).
        profiles_dir: Explicit absolute path to the directory containing profiles.yml,
            already resolved. None means use the standard resolution order.
    """
    from dbt_charts.core.project_roots import resolve_profiles_path

    candidate = resolve_profiles_path(dbt_project_path, profiles_dir=profiles_dir)
    with candidate.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"profiles.yml at {candidate} did not parse to a mapping. "
            f"Got: {type(data).__name__}"
        )
    return data


# dbt spelling → the spelling dbt charts' own code reads the field under. Deliberately
# tiny: these are the fields *dbt charts* consumes, not a mirror of dbt's schema. Adding
# an entry here means some dbt charts reader hardcodes a key name — check that first.
_DBT_KEYS_DBT_CHARTS_READS: dict[str, dict[str, str]] = {
    # sql_adapter builds BigQuery's default_dataset from project/dataset;
    # database/schema are dbt's canonical names for the same two fields.
    "bigquery": {"database": "project", "schema": "dataset"},
    # normalize_duckdb_config reads duckdb_config; dbt-duckdb spells it config_options.
    "duckdb": {"config_options": "duckdb_config"},
}


def _read_target_dict(
    dbt_project_path: Path,
    profile_name: str,
    target_name: str | None,
    profiles_dir: Path | None = None,
    render: bool = False,
) -> dict[str, Any]:
    """Return the connection fields for the given profile+target from profiles.yml.

    profiles.yml belongs to dbt, so the installed dbt adapter's credentials class
    validates the target — dbt charts declares no schema of its own over a file it
    does not own, which is what rejected valid dbt config (`threads`, canonical
    BigQuery `database`/`schema`). Following dbt's own sequence: drop the
    profile-level `threads` (dbt keeps it beside the credentials, not in them),
    resolve the plugin from `type`, translate dbt's field aliases, then validate.
    Every connection field dbt accepts passes through untouched — dropping one
    would connect with different semantics than dbt, which is worse than erroring.

    dbt-adapters only: dbt.config / dbt.flags are off limits; dbt-core is used
    only for manifest loading (WritableManifest) and test execution.

    Relative `path:` entries (DuckDB) are resolved against `dbt_project_path`
    so the adapter opens the file dbt would have opened, regardless of CWD. With
    `render=True` this anchors the rendered path (see the `render` arg below) —
    with `render=False` a templated `path:` is anchored unrendered, a
    pre-existing gap the resolver path's own later render doesn't correct.

    Args:
        dbt_project_path: The dbt project root; used as the anchor for relative
            DuckDB path: entries and as the fallback location for profiles.yml.
        profile_name: The dbt profile name.
        target_name: The dbt target name. None means use the profile's declared
            default target (profile["target"]), falling back to "dev" if absent.
        profiles_dir: Explicit absolute path to the profiles.yml directory when the
            file does not live at dbt_project_path. None = standard resolution order.
        render: False (default) returns the target with its Jinja/env_var() left
            raw — for a caller that renders it exactly once itself downstream
            (source_resolver._expand_dbt_profile, via DbtTargetSourceConfig's
            validator). True renders it here, reusing the same render already
            computed for validation below — for a caller with no render step of
            its own (DbtAdapter._resolve_target_dict). Never pass True from a caller
            that also renders the result, or an env_var() value that itself
            contains Jinja delimiters would be evaluated a second time.
    """
    profiles = _read_profiles_yml(dbt_project_path, profiles_dir=profiles_dir)
    profile = profiles.get(profile_name)
    if profile is None:
        available = [k for k in profiles if k != "config"]
        raise ValueError(
            f"Profile '{profile_name}' not found in profiles.yml "
            f"(checked {dbt_project_path}). "
            f"Available profiles: {available}"
        )
    resolved_target = target_name or profile.get("target", "dev")
    outputs = profile.get("outputs", {})
    target = outputs.get(resolved_target)
    if target is None:
        raise ValueError(
            f"Target '{resolved_target}' not found in profile '{profile_name}'. "
            f"Available targets: {list(outputs.keys())}"
        )

    from dbt.adapters.factory import load_plugin
    from dbt_common.exceptions import DbtRuntimeError
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict

    target = dict(target)
    if "type" not in target:
        raise ValueError(
            f"Profile '{profile_name}' target '{resolved_target}' has no 'type' field. "
            f"Name the warehouse dbt connects with, e.g. `type: postgres`. "
            f"Found: {sorted(target)}"
        )
    typename = str(target["type"])
    connection = {k: v for k, v in target.items() if k not in {"threads", "type"}}

    # Everything dbt can reject about a target arrives as ValueError, which is what
    # all three callers guard on: an unknown adapter type, a duplicated alias
    # (project *and* database), or a schema violation.
    #
    # Validation needs dbt's canonical spelling AND its rendered values — dbt renders
    # Jinja first, so validating the raw YAML would reject an env_var() in any field
    # dbt types as non-string (port, threads, …). rendered_connection is computed
    # once here; render=True reuses it as the returned target instead of rendering
    # a second time (see the `render` docstring above for why a second render is
    # unsafe).
    rendered_connection = render_dbt_jinja_in_dict(dict(connection))
    try:
        credentials_cls = load_plugin(typename)
        credentials_cls.validate(credentials_cls.translate_aliases(rendered_connection))
    except JsonSchemaValidationError as exc:
        raise ValueError(
            f"Profile '{profile_name}' target '{resolved_target}' is not a valid "
            f"{typename} connection for the installed dbt-{typename} adapter: "
            f"{exc.message}"
        ) from exc
    except DbtRuntimeError as exc:
        raise ValueError(
            f"Profile '{profile_name}' target '{resolved_target}': {exc}"
        ) from exc

    if render:
        connection = rendered_connection

    # dbt accepts several spellings per field; a few of them are read downstream by
    # *dbt charts* under its own name (the BigQuery default_dataset build in
    # sql_adapter, normalize_duckdb_config), so those are renamed here or the
    # setting is silently lost. Only fields dbt charts itself consumes — everything
    # else stays exactly as authored, for dbt's own credentials class to interpret.
    for dbt_name, dbt_charts_name in _DBT_KEYS_DBT_CHARTS_READS.get(
        typename,
        {},  # type-state: silent_fallback — map is deliberately sparse
    ).items():
        if dbt_name in connection and dbt_charts_name not in connection:
            connection[dbt_charts_name] = connection.pop(dbt_name)

    target = connection
    target["type"] = typename

    # Anchoring must read the rendered path (render=True) — an unrendered
    # env_var()/Jinja literal is never absolute, so it would join onto
    # dbt_project_path as text and the adapter would open the wrong file.
    raw_path = target.get("path")
    if isinstance(raw_path, str) and raw_path and raw_path != ":memory:":
        path_obj = Path(raw_path)
        if not path_obj.is_absolute():
            target["path"] = str((dbt_project_path / path_obj).resolve())
    return target


class DbtAdapter(BaseAdapter):
    """Adapter for executing SQL queries via dbt's adapter system.

    Supported query types: sql

    Leverages dbt's adapter API to execute SQL queries with support for
    dbt-specific features like ref(), source(), etc.

    This adapter requires dbt-adapters and a warehouse-specific dbt package
    (e.g. dbt-duckdb, dbt-postgres) — not dbt-core. A valid dbt project
    with profiles.yml configuration is also required.

    Example:
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>> project_path = Path("./my_dbt_project")
        >>> adapter = DbtAdapter(
        ...     project=FilesystemProject(project_path),
        ...     dbt_project_path=project_path,
        ...     target_name="dev",
        ... )
        >>> query = SqlQuery(sql="SELECT * FROM {{ ref('customers') }}", source="my_dbt_source")
        >>> result = adapter.execute(query)
    """

    def __init__(
        self,
        *,
        project: Project,
        dbt_project_path: Path,
        target_name: str,
        profile_name: str | None = None,
    ):
        """Initialize dbt adapter.

        Args:
            project: The dbt charts project (manifest reads route through this,
                not dbt_project_path — see DbtRefResolver).
            dbt_project_path: Path to dbt project. Still used for profiles.yml
                / dbt_project.yml resolution, which stays a raw filesystem
                read (out of the Project seam) since dbt's own config
                resolution order is disk-anchored.
            target_name: dbt target name
            profile_name: dbt profile name (default: from dbt_project.yml)
        """
        self.project = project
        self.dbt_project_path = Path(dbt_project_path).resolve()
        self.profile_name = profile_name
        self.target_name = target_name
        self._adapter: Any = None
        # _dialect is populated by _get_dbt_adapter(), which always runs before
        # any validate_select_only() or filter-binding call site and stores this
        # field before the adapter another thread short-circuits on.
        self._dialect: str = ""
        self._dbt_refs = DbtRefResolver(project)

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"sql"}

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Claim source-less SQL that uses dbt jinja ({{ ref() }}).

        A dbt-jinja query whose source resolves to no connection config defers to
        the dbt project's manifest/profiles.yml. A named `type: dbt_profile`
        source expands to its concrete warehouse type in the resolver before
        routing, so it lands on the type-owning adapter instead (see
        test_dbt_profile_routing.py).
        """
        return (
            is_sql_query(query) and source_config is None and has_dbt_jinja(query.sql)
        )

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute a SQL query via dbt adapter.

        Args:
            query: AnyQuery object (SqlQuery expected)
            variables: Variable values for Jinja resolution
            params: Accepted for interface compatibility; not used by dbt adapter.

        Returns:
            QueryResult with data or error
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        try:
            adapter = (
                self._adapter if self._adapter is not None else self._get_dbt_adapter()
            )
        except Exception as e:  # noqa: BLE001 — setup, not a warehouse rejection
            return handle_adapter_error("dbt adapter setup", e)

        try:
            resolved_sql = self._resolve_dbt_sql(
                query.sql, variables, strict=not query.lenient_variables
            )
        except Exception as e:  # noqa: BLE001 — names the actual failure, not setup
            return handle_adapter_error("query template rendering", e)

        try:
            validate_select_only(resolved_sql, dialect=self._dialect)
        except Exception as e:  # noqa: BLE001 — guard rejection, not a warehouse rejection
            return handle_adapter_error("dbt adapter setup", e)

        # Bounds the driver's own fetch (execute(..., limit=...) ->
        # cursor.fetchmany()) — resolved_sql is sent to the warehouse
        # unmodified, so every statement shape behaves exactly as it would
        # without this ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        # Some dbt-adapters cursors (dbt-spark's Hive/ODBC wrappers) implement
        # fetchall but not fetchmany — passing limit= there raises AttributeError
        # inside get_result_from_cursor. Omit it for those dialects and fall back
        # to the post-fetch slice below.
        driver_limit = (
            row_fetch_limit.fetch_limit
            if get_dialect(self._dialect).cursor_supports_driver_limit
            else None
        )

        # auto_begin=False, like dbt's own select path: a SELECT needs no
        # transaction, and one opened here is only ended by the release on
        # context exit. Nothing to end is stronger than something to clean up.
        try:
            with open_connection(adapter, "dbt_charts_query"):
                try:
                    _, table = adapter.execute(
                        resolved_sql,
                        auto_begin=False,
                        fetch=True,
                        limit=driver_limit,
                    )
                except Exception as e:  # noqa: BLE001 — any driver's rejection, classified below
                    return classify_warehouse_error(
                        "dbt SQL execution", e, self._dialect
                    )
        except ConnectionSetupFailed as e:
            return connection_failure(self._dialect, e.cause)

        # Result materialization is client-side (no further warehouse round
        # trip) — left unguarded so a defect here surfaces as a crash, not a
        # mislabeled "warehouse rejected the query".
        columns = list(table.column_names)
        rows = list(table.rows)
        rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
        data = [dict(zip(columns, row, strict=False)) for row in rows]
        return QueryResult(
            data=data, columns=columns, truncated_reason=truncated_reason
        )

    def _resolve_target_dict(
        self,
    ) -> dict[str, Any]:  # type-state: explicit_any — mirrors _read_target_dict's dict
        """Read this adapter's profile+target connection dict from profiles.yml.

        See ``resolve_target_type()`` for the exceptions this can raise.
        """
        if not self.profile_name:
            project_config_path = self.dbt_project_path / "dbt_project.yml"
            if project_config_path.exists():
                with project_config_path.open(encoding="utf-8") as f:
                    project_dict = yaml.safe_load(f)
                profile_from_project = project_dict.get("profile")
                if not profile_from_project:
                    raise ValueError(
                        f"dbt_project.yml at {project_config_path} has no 'profile:' key"
                    )
                self.profile_name = profile_from_project
            else:
                raise FileNotFoundError(
                    f"No dbt_project.yml found at {project_config_path} and no "
                    f"profile_name was provided to DbtAdapter"
                )

        # This path has no render step of its own downstream, unlike
        # source_resolver._expand_dbt_profile — see the `render` arg.
        return _read_target_dict(
            self.dbt_project_path,
            self.profile_name,
            self.target_name,
            render=True,
        )

    def resolve_target_type(self) -> str:
        """The dbt target's warehouse type, read from profiles.yml without connecting.

        Raises:
            FileNotFoundError: no dbt_project.yml at dbt_project_path and no
                profile_name was provided.
            ValueError: dbt_project.yml has no 'profile:' key, or the
                resolved profile/target is missing or invalid.
        """
        return str(self._resolve_target_dict()["type"])

    def _get_dbt_adapter(self) -> Any:  # type-state: explicit_any — untyped dbt adapter
        """Get dbt adapter instance (lazy-loaded).

        Delegates adapter construction to build_adapter() in the factory —
        single point of truth for source_config → dbt adapter.
        """
        if self._adapter is not None:
            return self._adapter

        target_dict = self._resolve_target_dict()
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        # Dialect first, adapter second, and not the other way round: workers run
        # this concurrently against one DbtAdapter and _execute short-circuits on
        # `self._adapter is not None`, so storing the adapter first opens a window
        # where another thread reads the empty dialect. `_read_target_dict` has
        # already raised if 'type' is missing, so reading it here is safe.
        # read_only=True: DbtAdapter is SELECT-only (validate_select_only gates
        # every execute), so the read-only DuckDB path is safe and lets us
        # coexist with other read-only connections on the same file.
        self._dialect = target_dict["type"]
        self._adapter = build_adapter(target_dict, read_only=True)
        return self._adapter

    def _resolve_dbt_sql(
        self,
        sql: str,
        variables: dict[str, Any] | None = None,
        *,
        strict: bool = True,
    ) -> str:
        """Resolve dbt refs, then render variables and filters to literal SQL.

        Variables resolve exactly as they always have on this path. Only the
        filter helpers change: instead of the raising interpolation stubs, the
        render context gets helpers that collect each value as a parameter and
        emit a placeholder for it, which the inline pass below flattens to an
        escaped literal — dbt's adapter.execute() takes a SQL string and no
        bindings.

        One Jinja render, deliberately. A second *render* of this output would
        be re-rendering substituted variable values, and values come from URL
        query parameters: they are data, never template source. The inline pass
        is not a render — it substitutes a fixed NUL-delimited token that
        cannot occur in authored SQL. A Jinja filter applied to a predicate is
        caught on the way through, in the two shapes it can take: a token the
        filter rewrote (`| upper`) is refused by the inline pass, and one it
        removed outright — leaving a collected value with nowhere to bind, so
        the query would run unconstrained — by the placeholder check above it.

        Binding only the helpers is also what keeps a variable written *inside*
        a literal (`'%{{ q }}%'`, `'{{ year }}-01-01'`) rendering as before; a
        filtered value arrives already quoted and cannot be spliced into the
        middle of somebody else's string.

        Args:
            sql: Query SQL, possibly containing dbt jinja and variable Jinja.
            variables: Variable values available to the template.
            strict: False lets undefined variables render empty, for queries
                that declare lenient_variables.
        """
        resolved, _ = self._dbt_refs.resolve(sql)
        # The helpers' date spelling, by the target's type as read from the
        # profile. Not `_warehouse()`: that refuses an unknown type, which is
        # right for escaping a value but would fail a query that binds nothing
        # before it reaches the adapter's own type check.
        warehouse = get_dialect(self._dialect)
        collector = _ParameterCollector(variables={}, dialect=INLINE_PLACEHOLDERS)
        rendered = resolve_jinja_template(
            resolved,
            variables,
            strict=strict,
            filter_helpers={
                "filter": make_filter_helper(collector.add_param, warehouse),
                "filter_date_range": make_filter_date_range_helper(
                    collector.add_param, warehouse
                ),
            },
        )
        if not collector.params:
            return rendered
        # Before inlining: the guard inside inline_params_for_dialect can only
        # see a mangled token, never one a filter removed outright — and a
        # dropped parameter is a query that runs unconstrained.
        _check_placeholders_present(rendered, collector, INLINE_PLACEHOLDERS)
        try:
            return inline_params_for_dialect(
                rendered,
                collector.params,
                INLINE_PLACEHOLDERS,
                escaping=self._warehouse(),
            )
        except ValueError as e:
            # A surviving placeholder means a Jinja filter transformed one
            # before it could be substituted. Coded here rather than left bare,
            # which would reach the executor as ERR-INTERNAL.
            raise JinjaError(str(e), resolved) from e

    def _warehouse(self) -> SQLDialect:
        """The dialect whose literal grammar the inlined values must satisfy.

        Raises:
            DbtChartsError: The dbt target's warehouse type has no dialect here,
                so its literal grammar is unknown. `get_dialect` would answer
                postgres, which leaves backslashes alone — the wrong answer for
                a value on any engine that escapes them, and wrong in the
                direction that puts part of a value in code position.
        """
        warehouse = DIALECTS.get(self._dialect.lower())
        if warehouse is None:
            raise DbtChartsError.from_code(
                ERR_SOURCE_INVALID_TYPE,
                offending_value=self._dialect,
                available=list_dialects(),
            )
        return warehouse
