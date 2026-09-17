"""Source detection utilities for database and dbt profiles.

Stage: COMPILE
Purpose: Centralized utilities for detecting database types and reading dbt profiles.

Entry Points:
    - detect_dbt_database_type(profiles_path, profile_name, target_name) -> dict
    - parse_dbt_profiles_yaml(content) -> list[dict]
    - detect_database_type_from_registry(registry) -> dict
    - detect_dbt_connection_string(cwd) -> tuple[str, str] | None
    - DB_INFO_MAP: Database type information dictionary

This module is the single canonical home for all database/source detection logic.
Surfaces (playground, cloud, CLI) import from here rather than implementing their own.
"""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TID251 — reads dbt profiles.yml/dbt_project.yml
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry

logger = logging.getLogger(__name__)

# Single source of truth for database-specific SQL syntax and introspection
DB_INFO_MAP: dict[str, dict[str, str]] = {
    "duckdb": {
        "type": "duckdb",
        "engine": "DuckDB",
        "dialect": "DuckDB SQL",
        "introspection": (
            "List tables: `SHOW TABLES;` | "
            "Describe table: `DESCRIBE table_name;` or `PRAGMA table_info('table_name');`"
        ),
        "notes": (
            "DuckDB does NOT support `SHOW COLUMNS FROM` or `PRAGMA show_columns()`. "
            "Use DESCRIBE or PRAGMA table_info instead."
        ),
    },
    "postgresql": {
        "type": "postgresql",
        "engine": "PostgreSQL",
        "dialect": "PostgreSQL SQL",
        "introspection": (
            "List tables: `SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public';` | "
            "Describe table: `SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'table_name';`"
        ),
        "notes": "PostgreSQL supports full information_schema queries.",
    },
    "snowflake": {
        "type": "snowflake",
        "engine": "Snowflake",
        "dialect": "Snowflake SQL",
        "introspection": (
            "List tables: `SHOW TABLES;` | Describe table: `DESCRIBE TABLE table_name;`"
        ),
        "notes": "Snowflake has its own SHOW/DESCRIBE commands and information_schema.",
    },
    "bigquery": {
        "type": "bigquery",
        "engine": "BigQuery",
        "dialect": "BigQuery SQL",
        "introspection": (
            "List tables: `SELECT table_name FROM project.dataset.INFORMATION_SCHEMA.TABLES;` | "
            "Describe table: `SELECT column_name, data_type FROM "
            "project.dataset.INFORMATION_SCHEMA.COLUMNS WHERE table_name = 'table_name';`"
        ),
        "notes": "BigQuery uses backtick-quoted identifiers for projects/datasets.",
    },
    "redshift": {
        "type": "redshift",
        "engine": "Amazon Redshift",
        "dialect": "Redshift SQL",
        "introspection": (
            "List tables: `SELECT tablename FROM pg_tables WHERE schemaname = 'public';` | "
            "Describe table: `SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'table_name';`"
        ),
        "notes": "Redshift supports PostgreSQL-style information_schema.",
    },
    "mysql": {
        "type": "mysql",
        "engine": "MySQL",
        "dialect": "MySQL SQL",
        "introspection": (
            "List tables: `SHOW TABLES;` | "
            "Describe table: `DESCRIBE table_name;` or `SHOW COLUMNS FROM table_name;`"
        ),
        "notes": "MySQL supports SHOW commands and information_schema.",
    },
    "databricks": {
        "type": "databricks",
        "engine": "Databricks",
        "dialect": "Databricks SQL (Spark SQL)",
        "introspection": (
            "List tables: `SHOW TABLES;` | Describe table: `DESCRIBE TABLE table_name;`"
        ),
        "notes": "Databricks supports Spark SQL syntax with SHOW/DESCRIBE commands.",
    },
    "athena": {
        "type": "athena",
        "engine": "AWS Athena",
        "dialect": "Athena SQL (Presto/Trino)",
        "introspection": (
            "List tables: `SHOW TABLES IN database_name;` | "
            "Describe table: `DESCRIBE table_name;`"
        ),
        "notes": "Athena uses Presto/Trino syntax.",
    },
    "trino": {
        "type": "trino",
        "engine": "Trino / Presto",
        "dialect": "Trino SQL (Presto)",
        "introspection": (
            "List tables: `SHOW TABLES FROM catalog.schema;` | "
            "Describe table: `DESCRIBE catalog.schema.table_name;`"
        ),
        "notes": "Trino queries a catalog.schema.table; `database` names the catalog.",
    },
    "clickhouse": {
        "type": "clickhouse",
        "engine": "ClickHouse",
        "dialect": "ClickHouse SQL",
        "introspection": (
            "List tables: `SHOW TABLES FROM database_name;` | "
            "Describe table: `DESCRIBE TABLE database_name.table_name;`"
        ),
        "notes": (
            "ClickHouse has one namespace level: the dbt `schema` is the "
            "ClickHouse database. `system.columns` lists every column on the server."
        ),
    },
    "sqlserver": {
        "type": "sqlserver",
        "engine": "Microsoft SQL Server",
        "dialect": "T-SQL",
        "introspection": (
            "List tables: `SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES;` | "
            "Describe table: `SELECT COLUMN_NAME, DATA_TYPE FROM "
            "INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'table_name';`"
        ),
        "notes": "SQL Server supports information_schema and sp_help procedures.",
    },
}


def get_database_info(db_type: str) -> dict[str, str] | None:
    """Get database info for a given type.

    Args:
        db_type: Database type string (e.g., "duckdb", "postgres", "snowflake")

    Returns:
        Database info dict with type, engine, dialect, introspection, notes
        Returns None if db_type is not recognized
    """
    normalized_type = db_type.lower().strip()
    # Normalize common aliases
    if normalized_type == "postgres":
        normalized_type = "postgresql"
    return DB_INFO_MAP.get(normalized_type)


def detect_dbt_database_type(
    profiles_path: Path,
    profile_name: str,
    target_name: str | None = None,
    *,
    fallback_type: str | None = None,
) -> dict[str, Any] | None:
    """Detect database type from dbt profiles.yml file.

    Reads the profiles.yml configuration and extracts database type information
    for the specified profile and target.

    Args:
        profiles_path: Path to profiles.yml file
        profile_name: Profile name to read (e.g., "my_project")
        target_name: Target name to read (e.g., "dev", "prod"). If None, uses
                     the default target or first available.
        fallback_type: If set and the profile is not found but the file exists,
                       return info for this database type instead of None.

    Returns:
        Dict with database type info including:
            - type: Database type (e.g., "duckdb", "postgresql")
            - engine: Human-readable engine name
            - dialect: SQL dialect name
            - introspection: Introspection query examples
            - notes: Important notes about SQL syntax
            - description: Auto-generated description
            - profile_name: The profile name used
            - target_name: The target name used
        Returns None if profile not found or file doesn't exist
        (unless fallback_type is set and the file exists)

    Example:
        >>> info = detect_dbt_database_type(
        ...     Path("profiles.yml"), "my_project", "dev"
        ... )
        >>> info["engine"]
        'DuckDB'
    """
    if not profiles_path.exists():
        return None

    import yaml

    try:
        with profiles_path.open(encoding="utf-8") as f:
            profiles = yaml.safe_load(f)

        if not profiles or profile_name not in profiles:
            return _fallback_info(fallback_type) if fallback_type else None

        profile = profiles[profile_name]
        if not isinstance(profile, dict) or "outputs" not in profile:
            return _fallback_info(fallback_type) if fallback_type else None

        outputs = profile["outputs"]
        if not outputs:
            return _fallback_info(fallback_type) if fallback_type else None

        # Use specified target, default target, or first available
        actual_target = target_name
        if actual_target is None or actual_target not in outputs:
            # Try default target from profile
            actual_target = profile.get("target")
            if actual_target is None or actual_target not in outputs:
                # Fall back to first available target
                actual_target = next(iter(outputs.keys()))

        target_config = outputs[actual_target]
        db_type = target_config.get("type", "").lower()

        db_info = get_database_info(db_type)
        if db_info:
            result = db_info.copy()
            result["description"] = f"SQL queries execute via {db_info['engine']}"
            result["profile_name"] = profile_name
            result["target_name"] = actual_target
            return result

        # Unknown type - return basic info
        return {
            "type": db_type or "unknown",
            "engine": db_type.title() if db_type else "Unknown",
            "dialect": "SQL",
            "introspection": "",
            "notes": f"Unknown database type: {db_type}",
            "description": f"SQL queries execute via unknown database type: {db_type}",
            "profile_name": profile_name,
            "target_name": actual_target,
        }

    except (OSError, yaml.YAMLError, AttributeError, KeyError, TypeError) as e:
        logger.debug(f"Failed to read or parse profiles.yml: {e}")
        return _fallback_info(fallback_type) if fallback_type else None


def _fallback_info(fallback_type: str) -> dict[str, Any]:
    """Build a fallback database info dict for a given type."""
    db_info = get_database_info(fallback_type)
    if db_info:
        result = db_info.copy()
        result["description"] = (
            f"SQL queries execute via {db_info['engine']} (fallback)"
        )
        return result
    return {
        "type": fallback_type,
        "engine": fallback_type.title(),
        "dialect": "SQL",
        "introspection": "",
        "notes": f"Fallback database type: {fallback_type}",
        "description": f"SQL queries execute via {fallback_type} (fallback)",
    }


# Canonical topology field → the dbt profile keys it may appear under. These are
# the non-secret fields safe to surface (host, dataset, etc.); credential keys
# (password, keyfile, keyfile_json, token, …) are deliberately absent so a
# whitelist read can never copy a secret out of profiles.yml.
_TOPOLOGY_FIELDS: dict[str, tuple[str, ...]] = {
    "host": ("host", "server"),
    "port": ("port",),
    "database": ("database", "dbname"),
    "schema": ("schema",),
    "username": ("user", "username"),
    "account": ("account",),  # Snowflake
    "warehouse": ("warehouse",),  # Snowflake
    "project": ("project",),  # BigQuery
    "dataset": ("dataset",),  # BigQuery
    "path": ("path",),  # DuckDB
}


def _render_target(target: dict[str, object]) -> dict[str, object]:
    """Render dbt Jinja in a target dict, per-field-resilient.

    SECURITY: profiles.yml may be remote-committed (e.g. by a Cloud org member),
    so ``env_var()`` is resolved against an **empty** environment (``env={}``) —
    only author-supplied defaults render. The process environment is never
    reachable, so ``{{ env_var('DATABASE_URL') }}`` in attacker-controlled YAML
    cannot exfiltrate a server secret into the parsed output.

    Fast path: render the whole dict in one pass (matches dbt). If any field's
    Jinja is unresolvable (an unset ``env_var()`` with no default), fall back to
    rendering field-by-field so the failing field becomes ``None`` and is simply
    omitted, rather than dropping every other (literal) field on the target.
    Non-string values pass through unchanged.
    """
    from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict

    try:
        return render_dbt_jinja_in_dict(target, env={})
    except ValueError:
        rendered: dict[str, object] = {}
        for key, value in target.items():
            if not isinstance(value, str):
                rendered[key] = value
                continue
            try:
                rendered[key] = render_dbt_jinja_in_dict({key: value}, env={})[key]
            except ValueError:
                rendered[key] = None
        return rendered


def parse_dbt_profiles_yaml(content: str) -> list[dict[str, object]]:
    """Parse dbt profiles.yml *content* into one dict per profile/target.

    The single walk over profiles.yml shared by every consumer (connection
    prefill, connection detection). dbt Jinja — `{{ env_var(...) }}` and the
    rest — is rendered per target (see :func:`_render_target`), so values
    resolve exactly as dbt would (env_var defaults included).

    Only non-secret topology fields (see ``_TOPOLOGY_FIELDS``) are emitted, by
    whitelist — credential keys are never read out, even when present as
    literals. A topology field whose Jinja can't be resolved is omitted (the
    target still surfaces with its remaining fields).

    Returns one dict per target with: ``profile``, ``target``, ``label``
    (``"<profile> / <target>"``), ``type``, plus whichever topology fields are
    present and non-empty. Invalid or empty YAML yields ``[]``.
    """
    import yaml

    try:
        profiles = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError):
        # Nesting deep enough to outrun the interpreter stack surfaces as a bare
        # RecursionError, not a YAMLError — PyYAML's parser recurses per level.
        # It is a parse failure like any other, and this content is
        # remote-committed, so degrade rather than 500 the caller.
        return []
    if not isinstance(profiles, dict):
        return []

    targets: list[dict[str, object]] = []
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        outputs = profile.get("outputs")
        if not isinstance(outputs, dict):
            # Absent, null, or a non-mapping (e.g. a half-written `outputs:`
            # stub). profiles.yml is org-member-authored, so degrade — never
            # crash the form on malformed-but-parseable YAML.
            continue
        for target_name, target in outputs.items():
            if not isinstance(target, dict):
                continue
            rendered = _render_target(target)

            entry: dict[str, object] = {
                "profile": profile_name,
                "target": target_name,
                "label": f"{profile_name} / {target_name}",
                "type": rendered.get("type"),
            }
            for field, keys in _TOPOLOGY_FIELDS.items():
                for key in keys:
                    value = rendered.get(key)
                    if value not in (None, ""):
                        entry[field] = value
                        break
            targets.append(entry)
    return targets


def detect_database_type_from_registry(
    registry: AdapterRegistry,
    *,
    default_profile_name: str = "default",
    default_target_name: str = "dev",
) -> dict[str, str]:
    """Detect database type information from an adapter registry.

    Inspects registered SQL adapters (DbtAdapter, SqlAdapter; DuckDBAdapter has
    no dbt profile and reports "unknown") to determine the database type by
    reading their dbt profiles.yml configuration.

    Args:
        registry: AdapterRegistry instance to inspect.
        default_profile_name: Profile name to use when adapter doesn't specify one.
        default_target_name: Target name to use when adapter doesn't specify one.

    Returns:
        Dict with database type information. Always returns a dict — never None.
        Keys: type, engine, dialect, notes (and optionally profile_name, target_name).
    """
    # tach-ignore(pre-existing compile->execute coupling — accepted debt)
    from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

    # tach-ignore(pre-existing compile->execute coupling — accepted debt)
    from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

    # tach-ignore(pre-existing compile->project_roots coupling — accepted debt)
    from dbt_charts.core.project_roots import resolve_profiles_path

    sql_adapters = registry.get_adapters_for_type("sql")

    for adapter in sql_adapters:
        if isinstance(adapter, DbtAdapter):
            dbt_path = Path(adapter.dbt_project_path)
            profile_name = adapter.profile_name or default_profile_name
            target_name = adapter.target_name or default_target_name

            try:
                profiles_path = resolve_profiles_path(dbt_path, profiles_dir=None)
                db_info = detect_dbt_database_type(
                    profiles_path, profile_name, target_name, fallback_type="duckdb"
                )
            except FileNotFoundError:
                db_info = None

            if db_info:
                db_info["profile_name"] = profile_name
                db_info["target_name"] = target_name
                return db_info

            # DbtAdapter but can't determine type — default to DuckDB
            info = DB_INFO_MAP["duckdb"].copy()
            info["description"] = "SQL queries execute via DuckDB (default)"
            return info

        elif isinstance(adapter, SqlAdapter):
            if adapter.dbt_project_path:
                dbt_path = Path(adapter.dbt_project_path)
                try:
                    profiles_path = resolve_profiles_path(dbt_path, profiles_dir=None)
                    db_info = detect_dbt_database_type(
                        profiles_path,
                        default_profile_name,
                        default_target_name,
                        fallback_type="duckdb",
                    )
                except FileNotFoundError:
                    db_info = None

                if db_info:
                    return db_info

            # SqlAdapter without dbt project — fall back to the profile_type
            # the executor already trusts (sql_adapter.py) before giving up.
            if adapter.profile_type:
                profile_type_info = get_database_info(adapter.profile_type)
                if profile_type_info:
                    return profile_type_info.copy()

            return {
                "type": "unknown",
                "engine": "Unknown",
                "description": "SQL adapter configured but database type unknown",
                "dialect": "SQL",
                "notes": "Database type could not be determined. Use generic SQL syntax.",
            }

    return {
        "type": "none",
        "engine": "None",
        "description": "No SQL adapter available",
        "dialect": "N/A",
        "notes": "",
    }


def detect_dbt_connection_string(cwd: Path) -> tuple[str, str] | None:
    """Auto-detect database connection from dbt project files.

    Walks up from *cwd* to find ``dbt_project.yml``, then resolves
    ``profiles.yml`` via the canonical order (DBT_PROFILES_DIR → project_dir
    → ~/.dbt) and builds a connection string + dialect pair.

    Args:
        cwd: Directory to start searching from.

    Returns:
        ``(connection_string, dialect)`` or ``None`` if no dbt project found.

    Raises:
        ValueError: if the target references ``{{ env_var('X') }}`` with no
            default and ``X`` is unset — a misconfigured profile fails loud
            rather than building a connection from an unrendered literal.
    """
    import yaml

    # tach-ignore(pre-existing compile->project_roots coupling — accepted debt)
    from dbt_charts.core.project_roots import resolve_profiles_path

    # Walk up to find dbt_project.yml
    search_path = cwd
    dbt_project_path = None
    for _ in range(10):
        candidate = search_path / "dbt_project.yml"
        if candidate.exists():
            dbt_project_path = candidate
            break
        if search_path.parent == search_path:
            break
        search_path = search_path.parent

    if not dbt_project_path:
        return None

    project_dir = dbt_project_path.parent

    try:
        with dbt_project_path.open(encoding="utf-8") as f:
            dbt_project = yaml.safe_load(f)
        profile_name = dbt_project.get("profile", "default")
    except (FileNotFoundError, yaml.YAMLError, TypeError):
        return None

    try:
        profiles_path = resolve_profiles_path(project_dir, profiles_dir=None)
    except FileNotFoundError:
        return None

    try:
        with profiles_path.open(encoding="utf-8") as f:
            profiles = yaml.safe_load(f)

        profile = profiles.get(profile_name, {})
        target_name = profile.get("target", "dev")
        outputs = profile.get("outputs", {})
        target = outputs.get(target_name, {})
        # Render dbt `{{ env_var('NAME', 'default') }}` templating in the target's
        # string values — profiles.yml routinely templates the path/host/etc., and
        # an unrendered literal `{{ ... }}` would be used verbatim as the connection.
        # An unset var with no default raises here for normal fields; dbt defers
        # `password` keypaths, so a missing password fails at connect, not here.
        target = render_dbt_jinja_in_dict(target)
        db_type = target.get("type", "duckdb")

        if db_type == "duckdb":
            path = target.get("path", ":memory:")
            if not path.startswith(":") and not Path(path).is_absolute():
                path = str(project_dir / path)
            return (path, "duckdb")
        elif db_type == "postgres":
            host = target.get("host", "localhost")
            port = target.get("port", 5432)
            user = quote_plus(target.get("user", "postgres"))
            password = quote_plus(target.get("password", ""))
            dbname = target.get("dbname", "postgres")
            return (
                f"postgresql://{user}:{password}@{host}:{port}/{dbname}",
                "postgres",
            )
        elif db_type == "bigquery":
            project = target.get("project", target.get("database", ""))
            if project:
                return (f"bigquery://{project}", "bigquery")
        elif db_type == "snowflake":
            account = target.get("account", "")
            database = target.get("database", "")
            db_schema = target.get("schema", "")
            warehouse = target.get("warehouse", "")
            if account:
                conn = f"snowflake://{account}/{database}/{db_schema}"
                if warehouse:
                    conn += f"?warehouse={warehouse}"
                return (conn, "snowflake")
        elif db_type == "databricks":
            host = target.get("host", "")
            http_path = target.get("http_path", "")
            token = target.get("token", "")
            if host:
                conn = f"databricks://{host}/{http_path}"
                if token:
                    conn += f"?token={token}"
                return (conn, "databricks")
    except (FileNotFoundError, yaml.YAMLError, TypeError, AttributeError):
        return None

    return None


__all__ = [
    "DB_INFO_MAP",
    "detect_database_type_from_registry",
    "detect_dbt_connection_string",
    "parse_dbt_profiles_yaml",
    "detect_dbt_database_type",
    "get_database_info",
]
