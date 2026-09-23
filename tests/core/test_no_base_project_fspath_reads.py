"""Guard: no filesystem-path read on a base-typed ``Project`` in core/agent_api.

`Project` (`core/project.py`) is the host-substitution seam: it holds no
`Path` at all. `root`, `charts_dir`, `path_for_fspath`, `directory_for_fspath`,
`dbt_root`, `dbt_project`, and `ProjectDirectory.project_root` (removed) live
only on `FilesystemProject` (`cli/filesystem_project.py`) — the one host for which a
filesystem `Path` is real edge currency. A read of one of these members
through a base-typed `Project` is invisible under `FilesystemProject` and
silently wrong under any other host (e.g. Cloud's `CloudManagedProject`, whose
`root` used to resolve to the worker's CWD — the production defect this seam
closes).

The type checker (`pyright`) is the primary gate: `Project` no longer declares
these members, so a base-typed read is a type error. This AST scan is the
mechanical ratchet on top, for the same reason `test_no_disk_io_in_core.py`
exists alongside the `TID251` ruff gate — a fast, LLM-free check that doesn't
depend on running pyright, and that documents every sanctioned FS-narrowed
read with a reason.

Every current read is either inside a function/parameter explicitly typed
`FilesystemProject`, or behind an `isinstance(x, FilesystemProject)` narrow.
`ALLOWED` is a ratchet: it must equal the offender set exactly. Fixing a site
(narrowing it further, or removing the read) means deleting its entry, not
widening the list. Do NOT "fix" a violation by widening this allowlist to
paper over an un-narrowed base-`Project` read — that defeats the gate.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .._paths import DBT_CHARTS_PKG_DIR

_SCAN_DIRS = (DBT_CHARTS_PKG_DIR / "core", DBT_CHARTS_PKG_DIR / "agent_api")

# Members that live only on FilesystemProject — any read of these outside a
# FilesystemProject-typed/narrowed context is unconditionally a violation
# (unlike `.root`, these names don't collide with unrelated classes).
_FS_ONLY_MEMBERS = frozenset(
    {
        "charts_dir",
        "config_file",
        "path_for_fspath",
        "directory_for_fspath",
        "dbt_root",
        "dbt_project",
    }
)

# `.root` is ambiguous by name alone (SchemaIR.root, LinkContext.root, plain
# Path.root all exist in this codebase) — only flag it when the object being
# accessed is textually project-shaped (`project.root`, `core_project.root`,
# `base_dir.project.root`, `cast("FilesystemProject", self.project).root`, ...).
_PROJECT_ISH_RE = re.compile(r"project", re.IGNORECASE)

# (relpath from DBT_CHARTS_PKG_DIR, line number) -> one-line reason the read is
# sanctioned: the local/parameter is explicitly typed FilesystemProject, or
# narrowed via isinstance immediately above. Ratchet — shrink, don't grow.
ALLOWED: dict[tuple[str, int], str] = {
    ("core/compile/config.py", 565): (
        "resolve_cache_boot(project: FilesystemProject) — already FS-typed"
    ),
    ("core/execute/adapters/adapter_registry.py", 180): (
        "isinstance(project, FilesystemProject)-guarded data_dir computation"
    ),
    ("core/execute/adapters/adapter_registry.py", 186): (
        "isinstance(project, FilesystemProject)-guarded resolved_dbt_path computation "
        "(project.dbt_root, linked-dbt-project sibling/external rule)"
    ),
    ("core/execute/adapters/adapter_registry.py", 188): (
        "isinstance(project, FilesystemProject)-guarded resolved_dbt_path computation "
        "(project.dbt_project.exists, same narrow as the line above)"
    ),
    # dct serve is filesystem-only: create_server(project: FilesystemProject)
    # and app.state.project are FilesystemProject throughout server.py — no
    # runtime isinstance narrow needed, only the FilesystemProject import
    # (under TYPE_CHECKING) and explicit local annotations below.
    ("core/serve/server.py", 501): (
        "_render_board_file(project: FilesystemProject) — already FS-typed"
    ),
    ("core/serve/server.py", 502): (
        "_render_board_file(project: FilesystemProject) — already FS-typed"
    ),
    ("core/serve/server.py", 604): (
        "_render_board_download(project: FilesystemProject) — already FS-typed"
    ),
    ("core/serve/server.py", 605): (
        "_render_board_download(project: FilesystemProject) — already FS-typed"
    ),
    ("core/serve/server.py", 1006): (
        "create_server: core_project inferred FilesystemProject from the parameter"
    ),
    ("core/serve/server.py", 1007): (
        "create_server: core_project inferred FilesystemProject from the parameter"
    ),
    ("core/serve/server.py", 1046): (
        "create_server: core_project inferred FilesystemProject from the parameter; "
        "the live-reload watch is opened over the project root"
    ),
    ("core/serve/server.py", 1076): (
        "_reload_on_change: project explicitly typed FilesystemProject"
    ),
    ("core/serve/server.py", 1088): (
        "_reload_on_change: project explicitly typed FilesystemProject"
    ),
    ("core/serve/server.py", 1172): (
        "profile_table: inspect_project explicitly typed FilesystemProject"
    ),
    ("core/serve/server.py", 1258): (
        "get_board: app.state.project is always FilesystemProject (dct serve is "
        "filesystem-only); the local a few lines below makes this explicit"
    ),
    ("core/serve/server.py", 1361): (
        "get_board: project explicitly typed FilesystemProject just above"
    ),
    ("core/serve/server.py", 1363): (
        "get_board: project explicitly typed FilesystemProject just above"
    ),
    ("core/serve/server.py", 1385): (
        "get_board: project explicitly typed FilesystemProject just above"
    ),
    (
        "agent_api/pack.py",
        171,
    ): "propose_pack(project: FilesystemProject) — already FS-typed",
    (
        "agent_api/pack.py",
        454,
    ): "apply_proposal(project: FilesystemProject) — already FS-typed",
    (
        "agent_api/pack.py",
        458,
    ): "apply_proposal(project: FilesystemProject) — already FS-typed",
    (
        "agent_api/pack.py",
        459,
    ): "apply_proposal(project: FilesystemProject) — already FS-typed",
    ("agent_api/_paths.py", 114): (
        "_relpath_for_fs_location: isinstance(project, FilesystemProject)-guarded"
    ),
    ("agent_api/_paths.py", 169): (
        "resolve_board_or_error: isinstance(project, FilesystemProject)-guarded"
    ),
    ("agent_api/_paths.py", 319): (
        "compile_editor_buffer: project = FilesystemProject(root) constructed above"
    ),
    ("agent_api/project_session.py", 300): (
        'ProjectSession.charts_dir: cast("FilesystemProject", self.project)-narrowed'
    ),
    ("agent_api/serve.py", 80): (
        "prepare_serve(project: FilesystemProject) — already FS-typed"
    ),
    ("agent_api/serve.py", 88): (
        "prepare_serve(project: FilesystemProject), already FS-typed "
        "(dbt_root, dialect inference reads the linked dbt project directory)"
    ),
}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for scan_dir in _SCAN_DIRS:
        files.extend(sorted(scan_dir.rglob("*.py")))
    return files


def _find_violations(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relpath = path.relative_to(DBT_CHARTS_PKG_DIR).as_posix()
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in _FS_ONLY_MEMBERS or (
            node.attr == "root" and _PROJECT_ISH_RE.search(ast.unparse(node.value))
        ):
            hits.append((relpath, node.lineno))
    return hits


def test_base_project_fspath_reads_match_reasoned_allowlist() -> None:
    violations: set[tuple[str, int]] = set()
    for py_file in _iter_py_files():
        violations.update(_find_violations(py_file))

    assert violations == set(ALLOWED), (
        "A base-typed Project read of root/charts_dir/config_file/"
        "path_for_fspath/directory_for_fspath/dbt_root/dbt_project was found "
        "outside the reasoned allowlist below (core/AGENTS.md 'Project-file access'). These "
        "members live only on FilesystemProject — narrow the read (isinstance "
        "or an explicit FilesystemProject type) and add a reasoned ALLOWED "
        "entry, or fix the site so it no longer needs a filesystem Path.\n"
        f"found: {sorted(violations)}\n"
        f"allowlisted: {sorted(ALLOWED)}"
    )
