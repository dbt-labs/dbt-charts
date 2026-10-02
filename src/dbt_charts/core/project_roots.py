"""Shared project/dbt root discovery used by render and MCP commands.

The reads here are deliberately NOT routed through the Project file-access
seam. find_root/find_dct_root/find_project_root walk *up* the filesystem from
cwd to locate the root — there is no project yet at that point. resolve_profiles_path / infer_dialect_from_dbt read dbt
profiles.yml from $DBT_PROFILES_DIR / ~/.dbt / project-local, i.e. paths outside
the project root that a project-relative plugin cannot address. Cloud supplies
the dbt connection config by another path; these stay filesystem-coupled.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path  # noqa: TID251 — walks disk to find the project root

import yaml

from dbt_charts.core.project import PROJECT_CONFIG_NAME

logger = logging.getLogger(__name__)

REPO_MARKERS = (".git",)
DCT_ROOT_MARKERS = (PROJECT_CONFIG_NAME, "dbt_project.yml")
_SERVE_ONLY_CONFIG_KEYS = {"server"}


def find_root(start: Path | None, markers: tuple[str, ...]) -> Path | None:
    """Walk up from *start* (default cwd); return the first dir with any marker, else None."""
    current = (start or Path.cwd()).resolve()
    while True:
        if any((current / marker).exists() for marker in markers):
            return current
        if current.parent == current:
            return None
        current = current.parent


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default cwd) to the repo root (dir containing ``.git``), else None.

    Locates where editor/AI-client config lives (``.cursor/``, ``.vscode/``, skill
    dirs) — distinct from the dbt charts project root.
    """
    return find_root(start, REPO_MARKERS)


def find_dbt_charts_dir() -> Path:
    """Return the dbt-charts/ package root (contains ``pyproject.toml`` + ``src/dbt_charts/``).

    Derived from the installed ``dbt_charts`` package location, not a git-root
    walk — resolves identically inside the monorepo and after a standalone OSS
    export, where dbt-charts/ IS the checkout root.
    """
    import dbt_charts

    pkg_file = dbt_charts.__file__
    assert pkg_file is not None
    candidate = Path(pkg_file).resolve().parent
    while not (
        (candidate / "pyproject.toml").exists()
        and (candidate / "src" / "dbt_charts").is_dir()
    ):
        if candidate.parent == candidate:
            raise RuntimeError(
                "Could not locate the dbt-charts package root "
                "(expected an ancestor with both pyproject.toml and src/dbt_charts)"
            )
        candidate = candidate.parent
    return candidate


def find_dct_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default cwd) to the dbt charts project root, else None.

    The root is the nearest ancestor with a ``dbt_charts.yml`` / ``dbt_project.yml``.
    Returns None outside a dbt charts project; callers that want a fallback append
    ``or Path.cwd()``.
    """
    return find_root(start, DCT_ROOT_MARKERS)


def _has_render_project_config(path: Path) -> bool:
    """Return True when dbt_charts.yml contains render-relevant project config."""
    config_path = path / PROJECT_CONFIG_NAME
    if not config_path.exists():
        return False
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return True
    return not (
        isinstance(data, dict) and data and set(data) <= _SERVE_ONLY_CONFIG_KEYS
    )


def find_project_root(
    start_dir: Path,
    boundary: Path | None,
) -> Path:
    """Walk upward from ``start_dir`` to find the project root for a render request.

    The project root is the nearest ancestor that contains a non-serve-only
    ``dbt_charts.yml``.

    If ``boundary`` is None, walks until the filesystem root. If set, stops after
    processing the ``boundary`` directory (inclusive).
    """
    start_resolved = start_dir.resolve()
    current = start_resolved
    boundary_resolved = boundary.resolve() if boundary is not None else None

    while True:
        if _has_render_project_config(current):
            return current

        if current.parent == current:
            break
        if boundary_resolved is not None and current == boundary_resolved:
            break

        current = current.parent

    return boundary_resolved or start_resolved


def resolve_profiles_path(
    project_dir: Path,
    profiles_dir: Path | None,
) -> Path:
    """Locate profiles.yml using the canonical resolution order.

    Resolution order:
      1. ``profiles_dir/profiles.yml`` — explicit field from DbtProfileSourceConfig
      2. ``$DBT_PROFILES_DIR/profiles.yml`` — env var
      3. ``project_dir/profiles.yml`` — project-local
      4. ``~/.dbt/profiles.yml`` — global fallback

    Raises ``FileNotFoundError`` with a clear message when none contains
    profiles.yml. Never falls back silently past a candidate that was
    explicitly named (profiles_dir set but missing → error, not next step).

    Args:
        project_dir: The dbt charts project root (where dbt_charts.yml lives).
        profiles_dir: Explicit directory from the ``profiles_dir`` source field,
            already resolved to an absolute Path. None means not set.
    """
    # Step 1: explicit profiles_dir from source config — no fallback when set
    if profiles_dir is not None:
        candidate = profiles_dir / "profiles.yml"
        if candidate.exists():
            return candidate
        raise FileNotFoundError(
            f"profiles_dir={profiles_dir!r} does not contain profiles.yml. "
            "Create profiles.yml there or remove the profiles_dir field."
        )

    # Step 2: DBT_PROFILES_DIR env var — no fallback when set
    env_dir = os.environ.get("DBT_PROFILES_DIR")  # noqa: TID251 — dbt-convention parity
    if env_dir:
        candidate = Path(env_dir) / "profiles.yml"
        if candidate.exists():
            return candidate
        raise FileNotFoundError(
            f"DBT_PROFILES_DIR={env_dir!r} does not contain profiles.yml. "
            "Create profiles.yml there or unset DBT_PROFILES_DIR."
        )

    # Step 3: project-local profiles.yml
    candidate = project_dir / "profiles.yml"
    if candidate.exists():
        return candidate

    # Step 4: global ~/.dbt/profiles.yml
    candidate = Path.home() / ".dbt" / "profiles.yml"
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"No profiles.yml found. Checked: {project_dir}/profiles.yml, ~/.dbt/profiles.yml. "
        "Set profiles_dir in your dbt_profile source or export DBT_PROFILES_DIR."
    )


def resolve_dbt_project_dir(project_dir: Path, explicit: Path | None) -> Path:
    """Locate the linked dbt project directory using the canonical resolution order.

    Resolution order:
      1. ``explicit``: the CLI's ``--dbt-project-dir``/``DBT_PROJECT_DIR``
         value, already merged by Typer before this is called (no separate
         env-var read here, unlike ``resolve_profiles_path``: that function
         has no competing CLI flag, this one does).
      2. ``dbt_project_dir:`` key in ``dbt_charts.yml``, resolved relative to
         *project_dir*.
      3. ``project_dir``: today's sibling default.

    A bare ``dbt_project_dir:`` key (YAML null) is treated as absent, same
    as omitting the key, matching ``Config.dbt_project_dir``'s own
    None-means-sibling-default contract. Otherwise raises ``TypeError`` for
    a non-string value, ``FileNotFoundError`` for a path that does not
    exist, or ``NotADirectoryError`` for a path that exists but is not a
    directory. Never falls back silently past a candidate that was
    explicitly named, same contract as ``resolve_profiles_path``.

    Args:
        project_dir: The dbt charts project root (where dbt_charts.yml lives).
        explicit: Already-resolved explicit dbt project directory, or None
            when not set. Returned unvalidated: the CLI's own
            `DbtProjectDirOption` (`exists=True, file_okay=False`) is the
            validation point for this branch.
    """
    if explicit is not None:
        return explicit

    config_path = project_dir / PROJECT_CONFIG_NAME
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("dbt_project_dir") is not None:
            raw_dbt_dir = data["dbt_project_dir"]
            if not isinstance(raw_dbt_dir, str):
                raise TypeError(
                    f"{config_path}: dbt_project_dir must be a string, got "
                    f"{type(raw_dbt_dir).__name__}: {raw_dbt_dir!r}"
                )
            if not raw_dbt_dir.strip():
                raise TypeError(f"{config_path}: dbt_project_dir must not be blank")
            candidate = (project_dir / raw_dbt_dir).resolve()
            if not candidate.exists():
                raise FileNotFoundError(
                    f"dbt_project_dir={raw_dbt_dir!r} in {config_path} resolves "
                    f"to {candidate}, which does not exist. Fix the path or "
                    "remove the dbt_project_dir key."
                )
            if not candidate.is_dir():
                raise NotADirectoryError(
                    f"dbt_project_dir={raw_dbt_dir!r} in {config_path} resolves "
                    f"to {candidate}, which is not a directory. Fix the path or "
                    "remove the dbt_project_dir key."
                )
            return candidate

    return project_dir


def infer_dialect_from_dbt(
    project_dir: Path,
    target_name: str | None = None,
) -> str | None:
    """Read the resolved dbt profile target and return its adapter ``type``.

    Resolution order for profiles.yml: profiles_dir field → DBT_PROFILES_DIR
    → project_dir → ~/.dbt. See resolve_profiles_path.

    Returns ``None`` when the dbt project or profile cannot be resolved.
    """
    import yaml

    dbt_project_path = project_dir / "dbt_project.yml"
    if not dbt_project_path.exists():
        return None

    try:
        dbt_config = yaml.safe_load(dbt_project_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(dbt_config, dict):
        return None
    profile_name = dbt_config.get("profile")
    if not profile_name:
        return None

    try:
        profiles_path = resolve_profiles_path(project_dir, profiles_dir=None)
    except FileNotFoundError:
        return None

    try:
        profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(profiles, dict):
        return None
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return None
    outputs = profile.get("outputs")
    if not isinstance(outputs, dict):
        return None
    default_target = profile.get(
        "target", "dev"
    )  # type-state: silent_fallback — as _read_target_dict
    target_config = outputs.get(target_name or default_target)
    if not isinstance(target_config, dict):
        return None
    adapter_type = target_config.get("type")
    return adapter_type if isinstance(adapter_type, str) else None
