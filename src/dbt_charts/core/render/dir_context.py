"""Built-in directory-navigation template variables.

Produces four auto-injected Jinja context keys scoped to a board's own directory:

    this_dir   — dict: name, path (project-relative), url
    parent_dir — same shape; None when the board is at the project root
    siblings   — lazy proxy for entries in the board's directory (one level)
    tree       — lazy proxy for a pre-rendered markdown indented listing (depth-bounded)

The ``list_dir_entries`` function is the shared implementation consumed by both
the serve-layer ``_render_directory_listing`` and ``lazy_dir_context``.  Both
source through ``Project.iter_dir`` — the single seam that defines the
dirs-first / case-insensitive / dotfile-and-SKIP_SCAN_DIRS-exclusion contract
once, for every host — so the two listing surfaces can never drift.

URL generation:
    URLs are serve-router-relative.  The serve router mounts the ``charts/``
    directory at ``/`` and explicitly 404s any ``/charts/*`` request.  The
    caller passes ``url_mount_dir`` (e.g. ``"charts"``) so the helper can strip
    that prefix from generated URLs, producing ``/reports/`` instead of
    ``/charts/reports/``.

Host-agnostic: ``lazy_dir_context`` takes a ``ProjectDirectory`` handle, never
a filesystem path, so it works identically for a disk-backed ``Project`` and a
git-blob-backed one (Cloud). "Confined to the project" is structural here —
a ``ProjectDirectory`` only ever carries a project-relative path — rather than
a runtime sandbox check against an absolute path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import PurePosixPath
from typing import Any

from dbt_charts.core.compile.config import get_inspector_config
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES,
    Project,
    ProjectDirectory,
    ProjectPath,
    board_namespace_for_root,
    package_board_roots,
)

# File extensions treated as renderable board files (.yml/.yaml/.md/.markdown).
_BOARD_EXTENSIONS = frozenset(BOARD_CANDIDATE_SUFFIXES)


@dataclass(frozen=True)
class DirEntry:
    """A single entry returned by list_dir_entries."""

    name: str
    url: str
    is_dir: bool
    ext: str  # empty string for directories
    label: str  # filename stem (without extension) for display
    handle: ProjectPath | ProjectDirectory  # the handle this entry was built from


def list_dir_entries(dir_handle: ProjectDirectory, url_prefix: str) -> list[DirEntry]:
    """Return sorted entries in *dir_handle*, sourced from ``Project.iter_dir``.

    Includes:
    - Subdirectories, in ``iter_dir``'s dirs-first / case-insensitive order
    - Files with a board extension (.yml, .yaml, .md, .markdown)

    Excludes:
    - Anything ``iter_dir`` itself excludes (dotfiles, SKIP_SCAN_DIRS
      directory names, symlink escapes on a filesystem host)
    - Files with non-board extensions
    - ``meta.yml`` / ``meta.yaml`` (cascade fragments, never standalone boards)

    Args:
        dir_handle: Directory to list.
        url_prefix: URL path prefix for building entry URLs (no trailing slash).

    Returns:
        List of DirEntry, in ``iter_dir``'s dirs-first / case-insensitive order
        (filtering out non-board files never reorders the remaining entries).
    """
    prefix = url_prefix.rstrip("/")
    entries: list[DirEntry] = []
    for child in dir_handle.project.iter_dir(dir_handle.relpath):
        name = child.name
        if isinstance(child, ProjectDirectory):
            entries.append(
                DirEntry(
                    name=name,
                    url=f"{prefix}/{name}/",
                    is_dir=True,
                    ext="",
                    label=name,
                    handle=child,
                )
            )
        else:
            if child.is_meta:
                continue
            suffix = PurePosixPath(name).suffix
            if suffix not in _BOARD_EXTENSIONS:
                continue
            # Strip the file extension from the URL: the serve router resolves
            # board slugs without extensions (e.g. /reports/sales not /reports/sales.yml).
            stem = PurePosixPath(name).stem
            entries.append(
                DirEntry(
                    name=name,
                    url=f"{prefix}/{stem}",
                    is_dir=False,
                    ext=suffix,
                    label=stem,
                    handle=child,
                )
            )
    return entries


def _render_tree(
    dir_handle: ProjectDirectory,
    url_prefix: str,
    depth: int = 0,
) -> list[str]:
    """Recursively build indented markdown lines for a directory tree."""
    if depth >= get_inspector_config().tree_max_depth:
        return []
    lines: list[str] = []
    indent = "  " * depth
    for entry in list_dir_entries(dir_handle, url_prefix):
        if isinstance(entry.handle, ProjectDirectory):
            lines.append(f"{indent}- [{entry.name}/]({entry.url})")
            lines.extend(_render_tree(entry.handle, entry.url.rstrip("/"), depth + 1))
        else:
            lines.append(f"{indent}- [{entry.name}]({entry.url})")
    return lines


def _dir_dict(name: str, path_rel: str, url: str) -> dict[str, str]:
    return {"name": name, "path": path_rel, "url": url}


def _strip_mount_prefix(url: str, mount_dir: str) -> str:
    """Rewrite *url*'s leading board-root prefix to its serve-router-relative form.

    ``mount_dir`` is a board root's own relpath (``core.project.BoardRoot.relpath``
    — ``"charts"`` for the host, ``"dbt_packages/<pkg>/charts"`` for a package):
    the whole prefix is replaced by ``/<namespace>`` (empty for the host root),
    which is a strip for the host root and a rewrite for a package root — the
    serve router mounts the host ``charts/`` directory at ``/`` and namespaces
    a package root at ``/<pkg>/``.

    A directory above the root (a package's ``dbt_packages/<pkg>`` parent) has
    no route of its own; it maps to ``/``, where the package is a virtual group.
    Does nothing when *mount_dir* is empty or the URL is outside the root.

    Examples:
        _strip_mount_prefix("/charts/reports/", "charts") → "/reports/"
        _strip_mount_prefix("/charts/", "charts") → "/"
        _strip_mount_prefix("/reports/", "charts") → "/reports/"  (no-op)
        _strip_mount_prefix("/charts/reports/", "") → "/charts/reports/"  (no-op)
        _strip_mount_prefix("/dbt_packages/pkg_a/charts/x/", "dbt_packages/pkg_a/charts") → "/pkg_a/x/"
        _strip_mount_prefix("/dbt_packages/pkg_a/", "dbt_packages/pkg_a/charts") → "/"
    """
    if not mount_dir:
        return url
    prefix = f"/{mount_dir}"
    namespace = board_namespace_for_root(mount_dir)
    replacement = f"/{namespace}" if namespace else ""
    if url == prefix or url == prefix + "/":
        return replacement + "/" if replacement else "/"
    if url.startswith(prefix + "/"):
        stripped = url[len(prefix) :]
        result = replacement + stripped
        return result if result else "/"
    if f"{mount_dir}/".startswith(url.lstrip("/")):
        return "/"
    return url


class _Lazy:
    """Thunk that computes on first use and memoizes per-instance.

    Forwards the access dunders Jinja templates use so the proxy is transparent:
    iteration (siblings), string coercion (tree), truthiness, length, item/attr
    access.  The board-text Jinja env has autoescape OFF so __html__ is never
    consulted; omitting it lets {{ tree }} fall through to __str__ which matches
    the prior eager-string behavior.
    """

    __slots__ = ("_thunk", "_value", "_done")

    def __init__(self, thunk: Callable[[], Any]) -> None:
        self._thunk = thunk
        self._done = False
        self._value: Any = None

    def _get(self) -> Any:
        if not self._done:
            self._value = self._thunk()
            self._done = True
        return self._value

    def __str__(self) -> str:
        return str(self._get())

    def __iter__(self) -> Any:
        return iter(self._get())

    def __len__(self) -> int:
        return len(self._get())

    def __bool__(self) -> bool:
        return bool(self._get())

    def __getitem__(self, key: Any) -> Any:
        return self._get()[key]

    def __getattr__(self, name: str) -> Any:
        # __slots__ names are resolved before __getattr__ so no recursion risk.
        return getattr(self._get(), name)


def _dir_meta(
    dir_handle: ProjectDirectory,
    url_mount_dir: str,
) -> tuple[str, dict[str, str], dict[str, str] | None]:
    """Compute the cheap, I/O-free parts of the dir context.

    Returns (sibling_url_prefix, this_dir, parent_dir). Pure relpath math — no
    filesystem resolution needed since a ProjectDirectory is already confined
    to its project by construction.
    """
    project_display_name = dir_handle.project.name
    dir_path_rel = dir_handle.relpath
    if dir_path_rel == ".":
        raw_url = "/"
        this_path = ""
        this_name = project_display_name
    else:
        raw_url = f"/{dir_path_rel}/"
        this_path = dir_path_rel
        this_name = dir_handle.name

    this_url = _strip_mount_prefix(raw_url, url_mount_dir)

    parent_dir: dict[str, str] | None = None
    if dir_path_rel != ".":
        parent = dir_handle.parent
        parent_rel = parent.relpath
        if parent_rel == ".":
            parent_raw_url = "/"
            parent_path = ""
            parent_name = project_display_name
        else:
            parent_raw_url = f"/{parent_rel}/"
            parent_path = parent_rel
            parent_name = parent.name
        parent_url = _strip_mount_prefix(parent_raw_url, url_mount_dir)
        parent_dir = _dir_dict(parent_name, parent_path, parent_url)

    sibling_url_prefix = this_url.rstrip("/")
    return sibling_url_prefix, _dir_dict(this_name, this_path, this_url), parent_dir


def _sibling_dicts(
    dir_handle: ProjectDirectory, sibling_url_prefix: str
) -> list[dict[str, Any]]:
    return [
        {
            "name": e.name,
            "url": e.url,
            "is_dir": e.is_dir,
            "ext": e.ext,
            "label": e.label,
        }
        for e in list_dir_entries(dir_handle, sibling_url_prefix)
    ]


def lazy_dir_context(
    dir_handle: ProjectDirectory,
    url_mount_dir: str = "",
) -> dict[str, Any]:
    """Build the four built-in directory-navigation variables for a board.

    this_dir and parent_dir are computed eagerly (pure relpath ops, no I/O).
    siblings and tree are wrapped in _Lazy: the project scan is deferred until
    the template actually accesses them.  Boards that never reference
    siblings/tree pay zero scan cost.

    Args:
        dir_handle: The board's own directory.
        url_mount_dir: On-disk directory name that the serve router mounts at
            ``/`` (e.g. ``"charts"``).  When non-empty, this prefix is stripped
            from all generated URLs so they are serve-router-relative.  Pass
            ``""`` (default) when the project root is served at ``/`` without a
            mounted subdirectory.

    Returns a dict containing:
        this_dir   — dict(name, path, url) for the board's own directory.
        parent_dir — same shape for the parent directory; None at the project root.
        siblings   — lazy proxy for DirEntry dicts; ``| tojson`` not supported
                     (force to list first if ever needed).
        tree       — lazy proxy for a pre-rendered markdown indented listing.
    """
    sibling_url_prefix, this_dir, parent_dir = _dir_meta(dir_handle, url_mount_dir)

    # siblings is a lazy proxy — `| tojson` in templates is not supported;
    # force to list first if ever needed: `siblings | list | tojson`.
    siblings: _Lazy = _Lazy(lambda: _sibling_dicts(dir_handle, sibling_url_prefix))
    tree: _Lazy = _Lazy(
        lambda: "\n".join(_render_tree(dir_handle, sibling_url_prefix, depth=0))
    )

    return {
        "this_dir": this_dir,
        "parent_dir": parent_dir,
        "siblings": siblings,
        "tree": tree,
    }


def directory_listing(
    dir_handles: list[ProjectDirectory], url_path: str, project: Project
) -> tuple[str, dict[str, str]]:
    """The board text and variables of a directory's listing page.

    *dir_handles* are every directory *url_path* names (host first); entries
    merge per URL, the first directory's entry winning, so a host board shadows
    the package board with the same slug. Render the text with the variables
    as built-in variables.
    """
    listing_lines: list[str] = []
    if url_path:
        parent_parts = url_path.strip("/").split("/")[:-1]
        parent_url = "/" + "/".join(parent_parts)
        listing_lines.append(f"- [../]({parent_url})")

    url_prefix = f"/{url_path}" if url_path else ""
    merged: dict[str, DirEntry] = {}
    for handle in dir_handles:
        for found in list_dir_entries(handle, url_prefix):
            merged.setdefault(found.url, found)
    entries = sorted(merged.values(), key=lambda e: (not e.is_dir, e.name.lower()))
    for entry in entries:
        # /inspect/<template> links require ?model=...&column=... query params;
        # advertising them as bare entries in the root listing 500s on click.
        if entry.name == "inspect" and not url_path:
            continue
        if entry.is_dir:
            listing_lines.append(f"- [{entry.name}/]({entry.url})")
        else:
            # list_dir_entries already restricts files to renderable board
            # extensions, so non-board files never reach here.
            listing_lines.append(f"- [{entry.name}]({entry.url})")

    # The root listing additionally shows one virtual group per installed
    # package that ships boards — a package root is a sibling subtree of
    # charts/, not a child directory list_dir_entries can see.
    if not url_path:
        for root in sorted(package_board_roots(project), key=lambda r: r.namespace):
            if f"/{root.namespace}/" not in merged:
                listing_lines.append(f"- [{root.namespace}/](/{root.namespace}/)")

    # ``directory_title``/``listing_markdown`` carry untrusted filenames, so they
    # reach the board only as render-time Jinja variable values below -- never
    # spliced into template text ahead of a render (same rule as
    # ``expand_query_refs``'s docstring: a value substituted before the final
    # pass is live template source by the time that pass reads it).
    listing_variables = {
        "directory_title": f"/{url_path}" if url_path else "/",
        "listing_markdown": "\n".join(listing_lines),
    }

    board_text = (
        files("dbt_charts.core.render")
        .joinpath("templates/directory.yml")
        .read_text(encoding="utf-8")
    )
    return board_text, listing_variables
