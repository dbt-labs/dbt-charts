"""Walk a board's transitive import closure.

Returns every file the board pulls in (directly or transitively) via layout
imports (``rows``/``cols``/``grid.items``/``tabs.items`` strings ending in
``.yml``/``.yaml``) and cross-file refs (bare strings and ``{ref: ...}``
dicts in ``queries``/``charts``/``variables``), plus ``extends:`` file refs,
the ``templates/<name>.svg.j2`` file each ``type: custom`` chart names, and
the boards a ``glob_boards``/``glob_folders`` call in its text lists.

The result includes the root board itself as the first entry and is
deduplicated — a file imported via multiple paths appears once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.board_listings import listing_dependencies
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.file_refs import resolve_ref
from dbt_charts.core.compile.template.custom_template import (
    TEMPLATE_FILE_SUFFIX,
    TEMPLATES_DIR,
)

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory, ProjectPath


def board_import_closure(board_path: ProjectPath) -> list[ProjectPath]:
    """Return all files in the transitive import closure of *board_path*.

    The list begins with *board_path* itself, followed by every file it imports
    (directly or transitively), in depth-first order with duplicates removed.
    Raises ``CompilationError`` on import cycles, unresolvable refs, or YAML
    errors.
    """
    result: list[ProjectPath] = []
    seen: set[str] = set()
    # A file reached through ``extends:`` from several folders lists from each.
    visited: set[tuple[str, str]] = set()
    resolving: set[str] = set()

    def add(path: ProjectPath) -> None:
        if path.relpath not in seen:
            seen.add(path.relpath)
            result.append(path)

    def walk(path: ProjectPath, listing_dir: ProjectDirectory) -> None:
        """*listing_dir* is the folder this file's ``text:`` lists from: its own,
        or the extending board's when reached through ``extends:``."""
        relpath = path.relpath
        if relpath in resolving:
            raise CompilationError(
                f"Import cycle detected: '{relpath}' imports itself transitively"
            )
        if (relpath, listing_dir.relpath) in visited:
            return
        visited.add((relpath, listing_dir.relpath))
        add(path)
        resolving.add(relpath)

        refs = path.project.import_refs(relpath)
        for ref in refs.extends:
            walk(resolve_ref(ref, path.parent, "file"), listing_dir)
        for ref, kind in refs.imports:
            target = resolve_ref(ref, path.parent, kind)
            walk(target, target.parent)
        leaves = [
            path.project.path(f"{TEMPLATES_DIR}/{name}{TEMPLATE_FILE_SUFFIX}")
            for name in refs.template_names
        ]
        for text in refs.listing_texts:
            leaves.extend(listing_dependencies(text, listing_dir))
        for leaf in leaves:
            add(leaf)

        resolving.discard(relpath)

    walk(board_path, board_path.parent)
    return result
