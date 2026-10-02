"""Typed doctor verb: why can't `dct` reach my data?

Reports the install method, whether the project and `profiles.yml` resolve,
and whether each source's warehouse adapter imports. Only
`with_warehouse=True` opens a warehouse connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

import dbt_charts
from dbt_charts._install_hint import is_uv_tool_install
from dbt_charts.agent_api import version_info
from dbt_charts.agent_api._paths import find_dct_root, resolve_dbt_project_dir
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.connections import test_connection
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    SUPPORTED_ADAPTER_TYPES,
    AdapterNotInstalledError,
    import_adapter_module,
)
from dbt_charts.core.project_roots import resolve_profiles_path


class DoctorCheck(BaseModel):
    """One diagnostic line. `code` is a stable wire id; `source` names the source
    on per-source `adapter`, `connection`, and `profiles` checks."""

    model_config = ConfigDict(frozen=True)

    code: Literal["install", "project", "profiles", "adapter", "connection"]
    source: str | None = None
    status: Literal["pass", "warn", "fail", "skip"]
    message: str
    hint: str | None = None


class DoctorReport(BaseModel):
    """Every check in run order; `success` is False when any check failed."""

    model_config = ConfigDict(frozen=True)

    success: bool
    checks: list[DoctorCheck]


def run_doctor(project_dir: Path, with_warehouse: bool) -> DoctorReport:
    """Diagnose the install, project, `profiles.yml`, and per-source adapters.

    Args:
        project_dir: Directory to start from; the project root is found by
            walking up to the nearest `dbt_charts.yml` or `dbt_project.yml`.
        with_warehouse: Also probe each non-file source with `SELECT 1`.

    Returns:
        The report. A missing project, missing `profiles.yml`, or an
        unexpandable `dbt_profile` source is a check result, never an exception.
    """
    checks = [_install_check()]
    root = find_dct_root(project_dir)
    project_check, dbt_dir = _project_check(root, project_dir)
    checks.append(project_check)
    if root is None or project_check.status != "pass":
        reason = "no project found" if root is None else "project check failed"
        checks += [
            DoctorCheck(code="profiles", status="skip", message="no dbt project"),
            DoctorCheck(code="adapter", status="skip", message=reason),
            DoctorCheck(code="connection", status="skip", message=reason),
        ]
    else:
        checks += _source_checks(root, dbt_dir, with_warehouse)
    return DoctorReport(
        success=all(check.status != "fail" for check in checks), checks=checks
    )


def _install_check() -> DoctorCheck:
    info = version_info.collect()
    if info.editable:
        method = "editable"
    elif is_uv_tool_install():
        method = "uv tool"
    else:
        method = "pip"
    return DoctorCheck(
        code="install",
        status="pass",
        message=(
            f"dbt Charts {dbt_charts.__version__} ({method}), "
            f"Python {info.python_version} at {info.python_executable}"
        ),
    )


def _project_check(
    root: Path | None, searched_from: Path
) -> tuple[DoctorCheck, Path | None]:
    """The project check, plus the dbt project directory when there is one."""
    if root is None:
        return (
            DoctorCheck(
                code="project",
                status="warn",
                message=(
                    f"no dbt_charts.yml or dbt_project.yml in {searched_from} "
                    "or any parent"
                ),
                hint="run from inside your project, or pass --project-dir",
            ),
            None,
        )
    try:
        dbt_dir = resolve_dbt_project_dir(root, None)
    except (FileNotFoundError, NotADirectoryError, TypeError) as exc:
        return DoctorCheck(code="project", status="fail", message=str(exc)), None
    found = []
    if (root / "dbt_charts.yml").exists():
        found.append(f"dbt_charts.yml at {root}")
    has_dbt = (dbt_dir / "dbt_project.yml").exists()
    if has_dbt:
        found.append(f"dbt project at {dbt_dir}")
    return (
        DoctorCheck(code="project", status="pass", message="; ".join(found)),
        dbt_dir if has_dbt else None,
    )


def _profiles_check(
    dbt_dir: Path, profiles_dir: str | None, source: str | None
) -> DoctorCheck:
    explicit = (dbt_dir / profiles_dir).resolve() if profiles_dir else None
    try:
        path = resolve_profiles_path(dbt_dir, explicit)
    except FileNotFoundError as exc:
        return DoctorCheck(
            code="profiles", source=source, status="fail", message=str(exc)
        )
    return DoctorCheck(code="profiles", source=source, status="pass", message=str(path))


def _profiles_checks(
    dbt_dir: Path | None, profiles_dirs: dict[str, str | None]
) -> list[DoctorCheck]:
    """One check per `dbt_profile` source that sets its own `profiles_dir`, plus
    the default search unless every `dbt_profile` source overrides it."""
    if dbt_dir is None:
        return [DoctorCheck(code="profiles", status="skip", message="no dbt project")]
    checks = [
        _profiles_check(dbt_dir, directory, name)
        for name, directory in profiles_dirs.items()
        if directory is not None
    ]
    if not profiles_dirs or None in profiles_dirs.values():
        checks.insert(0, _profiles_check(dbt_dir, None, None))
    return checks


def _source_checks(
    root: Path, dbt_dir: Path | None, with_warehouse: bool
) -> list[DoctorCheck]:
    with ProjectSession.open(root) as session:
        try:
            registry = session.adapter_registry
            names = [source["name"] for source in registry.list_sql_sources()]
            profiles_dirs = {
                name: raw.get("profiles_dir")
                for name in names
                if (raw := registry.resolve_source_config(name))["type"]
                == "dbt_profile"
            }
        except (DbtChartsError, TypeError) as exc:
            return [
                *_profiles_checks(dbt_dir, {}),
                DoctorCheck(code="adapter", status="fail", message=str(exc)),
                DoctorCheck(
                    code="connection",
                    status="skip",
                    message="sources failed to load",
                ),
            ]
        profiles = _profiles_checks(dbt_dir, profiles_dirs)
        if not names:
            return [
                *profiles,
                DoctorCheck(code="adapter", status="skip", message="no sources"),
                DoctorCheck(code="connection", status="skip", message="no sources"),
            ]
        adapters = {name: _adapter_check(registry, name) for name in names}
        checks = [*profiles, *(check for check, _ in adapters.values())]
        if not with_warehouse:
            return checks + [
                DoctorCheck(
                    code="connection",
                    status="skip",
                    message="connections not tested",
                    hint="run `dct doctor --with-warehouse`",
                )
            ]
        return checks + [
            _connection_check(name, check, config)
            for name, (check, config) in adapters.items()
        ]


def _adapter_skip(name: str, message: str) -> DoctorCheck:
    return DoctorCheck(code="adapter", source=name, status="skip", message=message)


def _adapter_check(
    registry: AdapterRegistry, name: str
) -> tuple[DoctorCheck, ResolvedSourceConfig | None]:
    """The source's adapter check, plus its resolved config for the probe."""
    try:
        # A named query is the registry's public way to expand a `dbt_profile`
        # source into its concrete warehouse config.
        config = registry.resolve_query_source(SqlQuery(sql="SELECT 1", source=name))
        if config is None:
            raise RuntimeError(f"source {name!r} resolved to no config")
        if config.source_category != "database":
            return _adapter_skip(
                name, f"{config.type} source, no adapter needed"
            ), config
        if config.type.lower() not in SUPPORTED_ADAPTER_TYPES:
            native = f"{config.type} source, runs natively (no dbt adapter needed)"
            return _adapter_skip(name, native), config
        import_adapter_module(config.type)
    except AdapterNotInstalledError as exc:
        failed = DoctorCheck(
            code="adapter",
            source=name,
            status="fail",
            message=(
                f"the {exc.fields['package']} adapter is not installed in this "
                "Python environment"
            ),
            hint=exc.fields["install"],
        )
        return failed, None
    except ModuleNotFoundError as exc:
        failed = DoctorCheck(
            code="adapter",
            source=name,
            status="fail",
            message=f"the adapter failed to import: missing module {exc.name!r}",
        )
        return failed, None
    except DbtChartsError as exc:
        failed = DoctorCheck(
            code="adapter", source=name, status="fail", message=str(exc)
        )
        return failed, None
    passed = DoctorCheck(
        code="adapter",
        source=name,
        status="pass",
        message=f"{config.type} adapter installed",
    )
    return passed, config


def _connection_check(
    name: str, adapter: DoctorCheck, config: ResolvedSourceConfig | None
) -> DoctorCheck:
    if config is None:
        return DoctorCheck(
            code="connection",
            source=name,
            status="skip",
            message="adapter check failed",
        )
    if adapter.status == "skip":
        return DoctorCheck(
            code="connection",
            source=name,
            status="skip",
            message="no connection to test",
        )
    ok, error = test_connection(config)
    if ok:
        return DoctorCheck(
            code="connection", source=name, status="pass", message="connected"
        )
    return DoctorCheck(
        code="connection", source=name, status="fail", message=str(error)
    )
