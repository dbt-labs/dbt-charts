"""Walk a board's transitive import closure.

Returns every file the board pulls in (directly or transitively) via layout
imports (``rows``/``cols``/``grid.items``/``tabs.items`` strings ending in
``.yml``/``.yaml``) and cross-file refs (bare strings and ``{ref: ...}``
dicts in ``queries``/``charts``/``variables``), plus ``extends:`` file refs.

The result includes the root board itself as the first entry and is
deduplicated — a file imported via multiple paths appears once.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.file_refs import RefKind, resolve_ref
from dbt_charts.core.compile.models.refs import (
    CHART_REF_RE,
    QUERY_REF_RE,
    VAR_REF_RE,
)
from dbt_charts.core.utils import YAML_LOADER

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectPath

_DOTTED_REF_RES: tuple[tuple[re.Pattern[str], RefKind], ...] = (
    (QUERY_REF_RE, "queries"),
    (CHART_REF_RE, "charts"),
    (VAR_REF_RE, "variables"),
)


def board_import_closure(board_path: ProjectPath) -> list[ProjectPath]:
    """Return all files in the transitive import closure of *board_path*.

    The list begins with *board_path* itself, followed by every file it imports
    (directly or transitively), in depth-first order with duplicates removed.
    Raises ``CompilationError`` on import cycles, unresolvable refs, or YAML
    errors.
    """
    result: list[ProjectPath] = []
    seen: set[str] = set()
    resolving: set[str] = set()

    def walk(path: ProjectPath) -> None:
        relpath = path.relpath
        if relpath in resolving:
            raise CompilationError(
                f"Import cycle detected: '{relpath}' imports itself transitively"
            )
        if relpath in seen:
            return
        seen.add(relpath)
        result.append(path)
        resolving.add(relpath)

        content = path.read_text()
        data = _load_board_yaml(content, relpath)

        if isinstance(data, dict):
            for ref, kind in _extract_imports(data):
                walk(resolve_ref(ref, path.parent, kind))

        resolving.discard(relpath)

    walk(board_path)
    return result


def _load_board_yaml(content: str, relpath: str) -> Any:
    """Parse board content to a Python object.

    Handles ``.md`` boards via their frontmatter YAML (not the raw markdown text,
    which contains two YAML documents and raises ``ComposerError``).
    Converts ``yaml.YAMLError`` to ``CompilationError`` so callers see one
    declared exception type.
    """
    try:
        if relpath.endswith((".md",)):
            from dbt_charts.core.compile.parse.markdown import parse_markdown_board

            content, _metadata = parse_markdown_board(content)
        return yaml.load(content, Loader=YAML_LOADER)
    except (ValueError, yaml.YAMLError) as exc:
        raise CompilationError(f"Parse error in '{relpath}': {exc}") from exc


def _extract_imports(data: dict[str, Any]) -> list[tuple[str, RefKind]]:
    """Collect every file ref (and how it names its file) in a board mapping."""
    refs: list[tuple[str, RefKind]] = []
    _collect_extends(data, refs)
    _scan_dict(data, refs)
    return refs


def _collect_extends(data: dict[str, Any], refs: list[tuple[str, RefKind]]) -> None:
    """Extract .yml/.yaml file refs from the top-level extends: key.

    extends: can be a scalar or a list. Built-in theme names (cream, editorial,
    etc.) do not end in .yml/.yaml and are skipped automatically.
    """
    extends = data.get("extends")
    entries = extends if isinstance(extends, list) else [extends]
    refs.extend(
        (entry, "file")
        for entry in entries
        if isinstance(entry, str) and entry.endswith((".yml", ".yaml"))
    )


def _dotted_ref(value: str, refs: list[tuple[str, RefKind]]) -> None:
    """Anchored grammar (models/refs.py): prose like 'style.charts.callout' is
    not a ref, and misreading it disables invalidation for the board."""
    for regex, section in _DOTTED_REF_RES:
        if regex.fullmatch(value):
            refs.append((value, section))
            return


def _scan_dict(obj: dict[str, Any], refs: list[tuple[str, RefKind]]) -> None:
    ref = obj.get("ref")
    if isinstance(ref, str):
        _dotted_ref(ref, refs)
        return  # ref dict is terminal — don't recurse into its siblings
    for v in obj.values():
        if isinstance(v, str):
            # Bare-string cross-file refs: the authored form per refs.py is a
            # plain string like "_shared.yml.queries.rev", coerced to {ref: ...}
            # at Pydantic parse time. We read authored YAML, so detect the
            # bare-string form here.
            _dotted_ref(v, refs)
        elif isinstance(v, list):
            _scan_list(v, refs)
        elif isinstance(v, dict):
            _scan_dict(v, refs)


def _scan_list(items: list[Any], refs: list[tuple[str, RefKind]]) -> None:
    for item in items:
        if isinstance(item, str) and item.endswith((".yml", ".yaml")):
            refs.append((item, "file"))
        elif isinstance(item, dict):
            _scan_dict(item, refs)
