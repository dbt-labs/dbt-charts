"""Reading a dbt project's profiles.yml target without connecting.

Stage: EXECUTE
Purpose: Resolve a dbt profile and target to its connection dict and warehouse
    type. The connection itself is ``DbtBackend``.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TID251 — reads dbt profiles.yml/dbt_project.yml
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from dbt_charts.core.execute.source_resolver import DbtContext

from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    SUPPORTED_ADAPTER_TYPES,
    import_adapter_module,
)


def _read_profiles_yml(
    dbt_project_path: Path | None, profiles_dir: Path | None = None
) -> tuple[
    Path,
    dict[str, Any],  # type-state: explicit_any — raw dbt-owned profiles.yml mapping
]:
    """Read profiles.yml using the canonical resolution order.

    Resolution order: profiles_dir → DBT_PROFILES_DIR → project-local → ~/.dbt.
    Returns the file's path and its mapping. Raises FileNotFoundError if none exists.

    Args:
        dbt_project_path: The dbt project root (where dbt_project.yml lives), or
            None when no dbt project is linked (skips the project-local step).
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
    return candidate, data


# dbt spelling → the spelling dbt charts' own code reads the field under. Deliberately
# tiny: these are the fields *dbt charts* consumes, not a mirror of dbt's schema. Adding
# an entry here means some dbt charts reader hardcodes a key name — check that first.
_DBT_KEYS_DBT_CHARTS_READS: dict[str, dict[str, str]] = {
    # backends/dbt_core builds BigQuery's default_dataset from project/dataset;
    # database/schema are dbt's canonical names for the same two fields.
    "bigquery": {"database": "project", "schema": "dataset"},
    # normalize_duckdb_config reads duckdb_config; dbt-duckdb spells it config_options.
    "duckdb": {"config_options": "duckdb_config"},
}


# dbt v2's `LakeComputeConfig` keys that govern dbt's own work, not the lake
# compute connection: `database`/`schema` name where `dbt run` writes models
# (boards read fully qualified relations), `threads` stays beside the credentials
# as for every target, `snowflake_pat_duration_days` is dbt's own Snowflake PAT
# minting, and `organization` is never forwarded to the driver by dbt-auth either
# — sending it would connect differently from `dbt run`.
_LAKECOMPUTE_PROFILE_ONLY_KEYS = frozenset(
    {"database", "schema", "threads", "snowflake_pat_duration_days", "organization"}
)


def _read_lakecompute_target(
    target: dict[str, Any],  # type-state: explicit_any — dbt owns profiles.yml values
    profile_name: str,
    target_name: str,
    render: bool,
) -> dict[str, Any]:  # type-state: explicit_any — mirrors _read_target_dict's dict
    """The connection fields of a `type: lakecompute` target, validated without dbt Core.

    dbt Core 1.x has no lake compute adapter to ask, so the lakecompute source
    model is the validator. It renders the target's Jinja itself and the result
    is discarded: this only raises early, and the caller receives the same kind
    of dict every other type returns.
    """
    from dbt_charts.core.compile.models.source import (
        LAKECOMPUTE_ADAPTER,
        reject_retired_source_type,
    )
    from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict

    reject_retired_source_type(str(target["type"]).lower())
    if "path" in target:
        raise ValueError(
            "lakecompute 'path' selects dbt's local DuckDB engine; dbt Charts "
            "connects to the lake compute service — remove path or use a duckdb "
            "source."
        )
    connection = {
        key: value
        for key, value in target.items()
        if key not in _LAKECOMPUTE_PROFILE_ONLY_KEYS and key != "type"
    }
    # The variant is chosen from `method` before the model renders anything, so
    # a templated `method:` has to be rendered first.
    candidate = {"type": "lakecompute", **connection}
    if "method" in candidate:
        candidate["method"] = render_dbt_jinja_in_dict({"method": candidate["method"]})[
            "method"
        ]
    try:
        LAKECOMPUTE_ADAPTER.validate_python(candidate)
    except ValueError as exc:
        raise ValueError(
            f"Profile '{profile_name}' target '{target_name}' is not a valid "
            f"lakecompute connection: {exc}"
        ) from exc
    if render:
        connection = render_dbt_jinja_in_dict(connection)
    elif "method" in candidate:
        connection["method"] = candidate["method"]
    return {"type": "lakecompute", **connection}


def _read_trino_target(
    target: dict[str, Any],  # type-state: explicit_any — dbt owns profiles.yml values
    profile_name: str,
    target_name: str,
    render: bool,
) -> dict[str, Any]:  # type-state: explicit_any — mirrors _read_target_dict's dict
    """The connection fields of a `type: trino` target, validated without dbt Core.

    Trino connects through its own driver, so no dbt adapter is installed to
    validate the target and the Trino source model does it instead. `threads`
    is dbt's own setting and is dropped, as for every target. dbt spells the
    unauthenticated method `none`; the source model spells it by omission.
    """
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.source import TrinoSourceConfig
    from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict

    connection = {k: v for k, v in target.items() if k not in {"threads", "type"}}
    if connection.get("method") == "none":
        del connection["method"]
    try:
        TrinoSourceConfig.model_validate({"type": "trino", **connection})
    except ValidationError as exc:
        # Built from locations and messages only: the error's own text quotes
        # the input, which here is a credential block.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(
            f"Profile '{profile_name}' target '{target_name}' is not a valid "
            f"trino connection: {problems}"
        ) from None
    if render:
        connection = render_dbt_jinja_in_dict(connection)
    return {"type": "trino", **connection}


def _profile_target_name(
    profile: dict[str, Any],  # type-state: explicit_any — raw dbt-owned profile mapping
    target_name: str | None,
) -> str:
    """The target *profile* connects with: *target_name*, else dbt's own default."""
    from dbt_charts.core.project_roots import resolve_dbt_target_name

    default_target = profile.get(
        "target", "dev"
    )  # type-state: silent_fallback — dbt's own default
    return resolve_dbt_target_name(target_name) or default_target


def _read_target_dict(
    dbt_project_path: Path | None,
    profile_name: str,
    target_name: str | None,
    profiles_dir: Path | None = None,
    render: bool = False,
) -> dict[str, Any]:  # type-state: explicit_any — profiles.yml target, dbt-owned shape
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
            None when no dbt project is linked: relative paths stay as authored
            and profiles.yml is looked up without the project-local step.
        profile_name: The dbt profile name.
        target_name: The dbt target name. None means ``$DBT_TARGET``, then the
            profile's declared default target (profile["target"]), falling back
            to "dev" if absent.
        profiles_dir: Explicit absolute path to the profiles.yml directory when the
            file does not live at dbt_project_path. None = standard resolution order.
        render: False (default) returns the target with its Jinja/env_var() left
            raw — for a caller that renders it exactly once itself downstream
            (source_resolver._expand_dbt_profile, via DbtTargetSourceConfig's
            validator). True renders it here, reusing the same render already
            computed for validation below — for a caller with no render step of
            its own (resolve_target_type). Never pass True from a caller
            that also renders the result, or an env_var() value that itself
            contains Jinja delimiters would be evaluated a second time.
    """
    profiles_path, profiles = _read_profiles_yml(
        dbt_project_path, profiles_dir=profiles_dir
    )
    profile = profiles.get(profile_name)
    if profile is None:
        available = [k for k in profiles if k != "config"]
        raise ValueError(
            f"Profile '{profile_name}' not found in {profiles_path}. "
            f"Available profiles: {available}"
        )
    resolved_target = _profile_target_name(profile, target_name)
    outputs = profile.get(
        "outputs", {}
    )  # type-state: silent_fallback — a profile without outputs has no target; reported just below
    target = outputs.get(resolved_target)
    if target is None:
        raise ValueError(
            f"Target '{resolved_target}' not found in profile '{profile_name}'. "
            f"Available targets: {list(outputs.keys())}"
        )

    # dbt v2 also writes a target as a list of connections, or a mapping with a
    # `lakecompute:` sibling key. Neither is one connection, so name the shape
    # before `dict(target)` below would turn it into a bare TypeError.
    if isinstance(target, list) or (
        isinstance(target, dict) and "lakecompute" in target
    ):
        raise ValueError(
            f"Profile '{profile_name}' target '{resolved_target}' uses dbt v2's "
            "multi-connection shape, which dbt Charts does not read yet. Add a "
            "target whose type is lakecompute, or declare the source directly "
            "with type: lakecompute."
        )
    # Before `load_plugin`, which fails for a type with no dbt-core adapter. The
    # retired spellings take this branch too, to be refused with their hint.
    if (
        isinstance(target, dict)
        and str(
            target.get(
                "type", ""
            )  # type-state: silent_fallback — a missing type reaches the missing-type error below
        ).lower()
        in {"lakecompute", "alt", "lake_compute"}
    ):
        return _read_lakecompute_target(target, profile_name, resolved_target, render)
    if isinstance(target, dict) and str(target.get("type")).lower() == "trino":
        return _read_trino_target(target, profile_name, resolved_target, render)

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
    # both callers guard on: an unknown adapter type, a duplicated alias
    # (project *and* database), or a schema violation. The exception is a mapped
    # adapter whose package is missing: AdapterNotInstalledError, so callers that
    # keep coded DbtChartsErrors surface its install command.
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
        # load_plugin's "Could not find adapter type" names no fix; for a type
        # dbt Charts maps, re-import it ourselves so a missing package raises
        # ERR-ADAPTER-NOT-INSTALLED with the install command instead.
        if typename.lower() in SUPPORTED_ADAPTER_TYPES:
            import_adapter_module(typename)
        raise ValueError(
            f"Profile '{profile_name}' target '{resolved_target}': {exc}"
        ) from exc

    if render:
        connection = rendered_connection

    # dbt accepts several spellings per field; a few of them are read downstream by
    # *dbt charts* under its own name (the BigQuery default_dataset build in
    # backends/dbt_core, normalize_duckdb_config), so those are renamed here or the
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
        if dbt_project_path is not None and not path_obj.is_absolute():
            target["path"] = str((dbt_project_path / path_obj).resolve())
    return target


def resolve_target_type(dbt_context: DbtContext) -> str:
    """The dbt target's warehouse type, read from profiles.yml without connecting.

    Raises:
        FileNotFoundError: no profiles.yml found.
        ValueError: no profile name resolves (see ``resolve_dbt_profile_name``), or
            the resolved profile/target is missing or invalid.
        AdapterNotInstalledError: the target's adapter package is missing.
    """
    from dbt_charts.core.project_roots import resolve_dbt_profile_name

    project_path = dbt_context.dbt_project_path
    # No render step of its own downstream, unlike
    # source_resolver._expand_dbt_profile — see the `render` arg.
    target_dict = _read_target_dict(
        project_path,
        resolve_dbt_profile_name(None, project_path),
        dbt_context.target,
        render=True,
    )
    return str(target_dict["type"])
