"""Bootstrap (or refresh) a dbt charts project layout."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from importlib_resources import files
from pydantic import BaseModel, ConfigDict

from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    SUPPORTED_ADAPTER_TYPES,
    AdapterNotInstalledError,
    import_adapter_module,
)
from dbt_charts.core.project import CHARTS_SUBDIR
from dbt_charts.core.project_roots import infer_dialect_from_dbt

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

GITIGNORE_ENTRIES = ("renders/", ".venv/", "__pycache__/", "*.duckdb")

_EXCLUDED_TEMPLATE_NAMES = frozenset({"__init__.py", "__pycache__"})

# The user's file the moment it exists: `force` refreshes engine-owned scaffold
# files, never a project README.
_NEVER_REFRESHED = frozenset({"README.md"})


def _walk_templates(
    node: Traversable, prefix: str = ""
) -> Iterator[tuple[str, Traversable]]:
    """Yield (relative posix path, file handle) for every template file, recursively."""
    for entry in sorted(node.iterdir(), key=lambda e: e.name):
        if entry.name in _EXCLUDED_TEMPLATE_NAMES:
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _walk_templates(entry, f"{rel}/")
        else:
            yield rel, entry


_TEMPLATES = files("dbt_charts.agent_api._init_templates")
_TEMPLATE_FILES: tuple[tuple[str, Traversable], ...] = tuple(
    _walk_templates(_TEMPLATES)
)
if not _TEMPLATE_FILES:
    raise RuntimeError("dbt_charts.agent_api._init_templates shipped no template files")

# Every path init_project may write. A caller that runs init_project against a
# materialized partial tree (Cloud's scaffold task builds one in a temp dir)
# must seed these paths with the real repo's versions first — otherwise the
# skip/merge protections see an empty tree and recreate everything from
# templates.
SCAFFOLD_PATHS: tuple[str, ...] = (*(rel for rel, _ in _TEMPLATE_FILES), ".gitignore")


class InitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_dir: Path
    dbt_detected: bool
    created_files: list[Path] = []
    skipped_files: list[Path] = []
    refreshed_files: list[Path] = []
    hints: list[str] = []


def init_project(
    project_dir: Path | None = None,
    *,
    force: bool = False,
    eject_inspect: bool = False,
) -> InitResult:
    """Bootstrap (or refresh) a dbt charts project layout.

    Safe to re-run: scaffold files are skipped unless *force* is set.
    Agent markdown is never written; agent onboarding lives in installed skills.
    """
    root = (project_dir or Path()).resolve()
    dbt_detected = (root / "dbt_project.yml").exists()

    result = InitResult(project_dir=root, dbt_detected=dbt_detected)

    for rel, handle in _TEMPLATE_FILES:
        target = root / rel
        if target.exists() and (not force or rel in _NEVER_REFRESHED):
            result.skipped_files.append(Path(rel))
            continue
        content = handle.read_text(encoding="utf-8")
        if target.exists():
            target.write_text(content, encoding="utf-8")
            result.refreshed_files.append(Path(rel))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            result.created_files.append(Path(rel))

    _ensure_gitignore_entries(root, result)

    if dbt_detected:
        _hint_missing_dbt_adapter(root, result)

    if eject_inspect:
        from dbt_charts.agent_api import inspect as _api_inspect

        try:
            ejected = _api_inspect.eject_templates(
                root / CHARTS_SUBDIR / "inspect", templates=None, force=False
            )
            result.created_files.extend(p.relative_to(root) for p in ejected)
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            result.hints.append(
                f"warning: inspect templates could not be ejected ({exc!r}); "
                "your install may be partial. Run 'pip show dbt-charts' to verify."
            )

    return result


def _hint_missing_dbt_adapter(root: Path, result: InitResult) -> None:
    """Warn now, not at the first render, when the dbt profile's adapter is absent."""
    dialect = infer_dialect_from_dbt(root)
    if dialect is None or dialect.lower() not in SUPPORTED_ADAPTER_TYPES:
        return
    try:
        import_adapter_module(dialect)
    except AdapterNotInstalledError as exc:
        result.hints.append(str(exc))


def _ensure_gitignore_entries(root: Path, result: InitResult) -> None:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n", encoding="utf-8")
        result.created_files.append(Path(".gitignore"))
        return

    text = gitignore.read_text(encoding="utf-8")
    existing = set(text.splitlines())
    missing = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if not missing:
        return

    separator = "" if not text or text.endswith("\n") else "\n"
    gitignore.write_text(
        f"{text}{separator}" + "\n".join(missing) + "\n", encoding="utf-8"
    )
    result.refreshed_files.append(Path(".gitignore"))
