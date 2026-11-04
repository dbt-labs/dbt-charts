"""Local-filesystem ``Project`` — the concrete host built at the CLI composition root.

``FilesystemProject`` turns a directory on disk into a ``Project`` and lives here,
at the ``dbt_charts.cli`` leaf, rather than in ``dbt_charts.core``. ``core``/``agent_api``/
``ai`` receive a ``Project`` and may reference this type only under ``TYPE_CHECKING``
(tach ignores type-only imports), so a runtime ``FilesystemProject(...)`` in any of
those three layers fails ``tach check`` (``core`` via its ``depends_on`` allowlist,
``agent_api``/``ai`` via ``cannot_depend_on``). Other composition roots outside those
layers (``dbt_charts.cli``, ``dbt_charts.integrations.markdown``, and sibling
distributions) construct it directly — that edge is not tach-covered.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dbt_charts.core.project import (
    ALL_FILES_GLOB,
    CHARTS_SUBDIR,
    DBT_PACKAGES_SUBDIR,
    PROJECT_CONFIG_NAME,
    PROJECT_ROOT_FILES,
    SKIP_SCAN_DIRS,
    GrepHit,
    Project,
    ProjectDirectory,
    ProjectFileQueries,
    ProjectPath,
    ascii_lower,
    assert_relpath,
    canonical_in_listing,
    canonical_listing,
    canonical_relpath,
    glob_literal_prefix,
    glob_to_regex,
    is_package_relpath,
    package_board_roots,
    posix_relpath,
    stored_relpath,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dbt_charts.core.compile.config import ProjectSourcesConfig


def symlinked_package_path(root: Path, path: Path) -> Path | None:
    """The logical ``root``-relative form of *path* iff it is read through a
    ``dbt_packages/<pkg>`` symlink and stays inside that package's target."""
    logical = Path(os.path.normpath(path.absolute()))
    # *root* is resolved; *path* may be spelled through a symlinked ancestor
    # (/tmp -> /private/tmp), so find the ancestor that is the root, outermost first.
    for ancestor in reversed((logical, *logical.parents)):
        if ancestor.resolve() == root:
            break
    else:
        return None
    candidate = root / logical.relative_to(ancestor)
    parts = candidate.relative_to(root).parts
    if len(parts) < 2 or parts[0] != DBT_PACKAGES_SUBDIR:
        return None
    package = root / parts[0] / parts[1]
    if package.is_symlink() and candidate.resolve().is_relative_to(package.resolve()):
        return candidate
    return None


# What dct keeps in a checkout, such as the board index. It exists only on disk, so
# walking past it is this class's concern, not ``SKIP_SCAN_DIRS``'s.
LOCAL_STATE_DIR = ".dct"


class FilesystemFileQueries(ProjectFileQueries):
    """Native filesystem query implementation; owns glob and grep directly.

    ``glob`` enumerates via the project's ``iter_files`` walk, bounded by the
    pattern's literal prefix. ``grep`` is a bytes-scan: read the whole file as
    bytes, run one ``bytes.find``-speed substring check before ever decoding,
    and decode (once) only the files that actually contain the needle — the
    overwhelming majority of a repo scan is no-match files, and those never
    pay for a UTF-8 decode.
    """

    def __init__(self, project: Project) -> None:
        self._project = project

    def glob(self, pattern: str) -> Iterator[ProjectPath]:
        assert_relpath(pattern)
        under = glob_literal_prefix(pattern)
        if under != "." and SKIP_SCAN_DIRS.intersection(under.split("/")):
            return
        regex = glob_to_regex(pattern)
        for relpath in self._project.iter_files(under, recursive=True):
            if regex.match(relpath):
                yield ProjectPath(self._project, relpath)

    def grep(
        self, term: str, glob: str = ALL_FILES_GLOB, ignore_case: bool = False
    ) -> Iterator[GrepHit]:
        if ignore_case:
            term = ascii_lower(term)
        needle = term.encode("utf-8")
        for pf in self.glob(glob):
            try:
                data = self._project.read_bytes(pf.relpath)
            except OSError:
                continue
            # bytes.lower() folds ASCII only, the same fold as ascii_lower.
            if needle not in (data.lower() if ignore_case else data):
                continue  # one C-speed scan; no decode for the common no-match file
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # not valid text -> whole file skipped, never raised
            for i, line in enumerate(text.splitlines(), start=1):
                if term in (ascii_lower(line) if ignore_case else line):
                    yield GrepHit(pf.relpath, i, line)


class FilesystemProject(Project):
    """Local-filesystem ``Project`` — dct core's historical concrete implementation.

    The four file-access methods and ``sources`` read the local filesystem
    rooted at *root*. This is the default when no embedding host overrides
    file access. ``root`` and everything derived from it (``charts_dir``,
    ``path_for_fspath``, ``directory_for_fspath``) live here, not on the
    abstract base — a real filesystem ``Path`` is edge currency only this
    host has.
    """

    def __init__(self, root: Path, dbt_root: Path | None = None) -> None:
        self._root_input = root
        self._dbt_root_input = dbt_root

    @cached_property
    def root(self) -> Path:
        return self._root_input.resolve()

    @cached_property
    def dbt_root(self) -> Path:
        """The linked dbt project directory, ``root`` unless overridden via
        ``FilesystemProject(root, dbt_root=...)`` (``resolve_dbt_project_dir``)."""
        return (
            self._dbt_root_input.resolve()
            if self._dbt_root_input is not None
            else self.root
        )

    @cached_property
    def dbt_project(self) -> FilesystemProject:
        """``self`` when ``dbt_root == root``, else a second
        ``FilesystemProject`` rooted at the linked dbt directory."""
        return self if self.dbt_root == self.root else FilesystemProject(self.dbt_root)

    def manifest_project(self) -> Project:
        return self.dbt_project

    @cached_property
    def boards_dir(self) -> str:
        """The folder that holds boards (``boards_dir:`` in ``dbt_charts.yml``).

        Every path this project takes or yields is canonical (``charts/...``);
        the storage methods below translate to and from where files really are.
        Reading the config itself never needs the translation: it is not a
        board path. A config that never spells the key cannot set it, so it is
        not parsed: a project whose config fails to parse can still read its boards.
        """
        config = self.root / PROJECT_CONFIG_NAME
        if not config.exists() or "boards_dir" not in config.read_text(
            encoding="utf-8"
        ):
            return CHARTS_SUBDIR
        # Lazy import avoids a cycle: compile/config.py imports Project at top-level.
        from dbt_charts.core.compile.config import get_project_boards_dir

        return get_project_boards_dir(self)

    def _stored(self, relpath: str) -> str:
        """Where the file at canonical, normalized *relpath* really is; raises
        ``FileNotFoundError`` for a path that names nothing (``stored_relpath``)."""
        if relpath in PROJECT_ROOT_FILES:
            return relpath
        return stored_relpath(relpath, self.boards_dir)

    def _stored_for_write(self, relpath: str) -> str:
        """``_stored`` for a write or delete, which refuses with ``ValueError``.
        An absolute path under the root is its own location."""
        if Path(relpath).is_absolute():
            return relpath
        try:
            return self._stored(assert_relpath(relpath))
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from None

    @cached_property
    def charts_dir(self) -> Path:
        return self.root / self._stored(CHARTS_SUBDIR)

    def package_charts_dirs(self) -> list[Path]:
        """Absolute paths of every installed package's ``charts/`` directory.

        Filesystem-only edge currency (mirrors ``charts_dir``) — probed per
        call, not cached, so ``dct serve``'s alias-index invalidation watch
        notices a package added or removed by a ``dbt deps`` re-run.
        """
        return [self.root / root.relpath for root in package_board_roots(self)]

    @cached_property
    def files(self) -> ProjectFileQueries:
        return FilesystemFileQueries(self)

    @cached_property
    def name(self) -> str:
        return self.root.name

    def config_document(self) -> tuple[str, Any] | None:
        if not self.exists(PROJECT_CONFIG_NAME):
            return None
        return PROJECT_CONFIG_NAME, self.read_yaml(PROJECT_CONFIG_NAME)

    def path_for_fspath(self, path: Path) -> ProjectPath:
        """Return a ProjectPath handle for an absolute path under this project root.

        Raises ValueError if the path is not inside the project root.
        """
        return ProjectPath(self, self._relpath_for_fspath(path))

    def directory_for_fspath(self, path: Path) -> ProjectDirectory:
        """Return a ProjectDirectory handle for an absolute path under this project root.

        Raises ValueError if the path is not inside the project root.
        """
        return ProjectDirectory(self, self._relpath_for_fspath(path))

    def _relpath_for_fspath(self, path: Path) -> str:
        """Resolve an absolute path to a canonical project-relative path string.

        Raises ValueError if the path is outside the project root, or inside a
        ``charts/`` folder that ``boards_dir`` displaced.
        """
        stored = self._stored_relpath_for_fspath(path)
        if stored in PROJECT_ROOT_FILES:
            return stored
        relpath = canonical_relpath(stored, self.boards_dir)
        if relpath is None:
            raise ValueError(
                f"Path {path!r} is in a charts/ folder that boards_dir "
                f"{self.boards_dir!r} displaced"
            )
        return relpath

    def _stored_relpath_for_fspath(self, path: Path) -> str:
        """Resolve an absolute path to a project-relative path string.

        Tries two containment checks in order:
        1. resolve() — follows all symlinks (handles /var→/private/var on macOS).
        2. normpath — collapses .. without following symlinks (accepts logical paths
           through intentional project symlinks like charts/tasks/ → ../../tasks/).

        Raises ValueError if neither check places the path inside the project root.
        """
        # First try: fully resolved path. Handles OS-level symlinks (/var on macOS).
        resolved = path.resolve()
        try:
            return posix_relpath(resolved, self.root)
        except ValueError:
            pass

        # Second try: normpath without following symlinks. Accepts logical access
        # paths through project-internal symlinks (e.g. charts/tasks/ -> ../../tasks/).
        normalized = Path(os.path.normpath(path.absolute()))
        try:
            return posix_relpath(normalized, self.root)
        except ValueError:
            raise ValueError(
                f"Path {path!r} is outside project root {self.root!r}"
            ) from None

    def data_path(self, relpath: str) -> Path:
        """Return a resolved filesystem path for a project-relative data file.

        Used by the execution layer to open data files directly via a native
        engine (DuckDB file search path, SQLite path resolution). Returns the
        path without checking existence — callers probe with ``.exists()``
        themselves. Permissive by contract — does not reject paths that
        escape the project root: DuckDB/SQLite ``path:`` values come from
        trusted authored config, not untrusted runtime input, and a
        deliberate cross-project relative path (e.g. a shared dev database)
        is a legitimate use.

        A canonical ``charts/...`` path resolves to where ``boards_dir`` keeps it.

        Not part of the pluggable file-content interface
        (``read_text``/``read_bytes``/``exists``/``iter_files``) — filesystem
        only, which is why it lives here rather than on ``Project``.
        """
        return (self.root / self._stored(relpath)).resolve()

    def exists(self, relpath: str) -> bool:
        """Raises ValueError if *relpath* is absolute, empty/blank, or escapes
        above the project root."""
        try:
            stored = self._stored(assert_relpath(relpath))
        except FileNotFoundError:
            return False
        return (self.root / stored).exists()

    def read_text(self, relpath: str) -> str:
        """Raises FileNotFoundError if the path does not exist, or ValueError
        if *relpath* is absolute, empty/blank, or escapes above the project
        root."""
        return (self.root / self._stored(assert_relpath(relpath))).read_text(
            encoding="utf-8"
        )

    def read_bytes(self, relpath: str) -> bytes:
        """Raises FileNotFoundError if the path does not exist, or ValueError
        if *relpath* is absolute, empty/blank, or escapes above the project
        root."""
        return (self.root / self._stored(assert_relpath(relpath))).read_bytes()

    def file_version(self, relpath: str) -> str:
        """Stat signature (``mtime_ns:size``) — no file read on the common path.

        This is exactly git's racy-git change detection: mtime + size is a
        reliable "did this file change?" signal for the local edit-refresh loop,
        for tracked, dirty, and untracked files alike, and costs one ``stat``.
        """
        st = (self.root / self._stored(assert_relpath(relpath))).stat()
        return f"{st.st_mtime_ns}:{st.st_size}"

    def iter_files(
        self,
        under: str,
        *,
        recursive: bool,
        onerror: Callable[[str, OSError], None] | None = None,
    ) -> Iterator[str]:
        """Skips traversal-noise directories (``SKIP_SCAN_DIRS``).

        ``onerror``, when given, is called with a project-relative path and
        the ``OSError`` for a directory ``os.walk``'s own ``scandir`` cannot
        enter (permission denied, most commonly). Without it, ``os.walk``'s
        default (``onerror=None``) silently skips that directory -- the
        walk simply yields fewer files, with no way for a caller to tell
        "no boards here" from "couldn't read this directory". Every
        existing caller passes no ``onerror``, so this is additive only;
        ``list_boards`` is the first to use it.
        """
        boards_dir = self.boards_dir
        subtree_relpath = self._stored(under)
        subtree = self.root if subtree_relpath == "." else self.root / subtree_relpath
        if not subtree.is_dir():
            return

        def _report(stored: str, exc: OSError) -> None:
            shown = canonical_in_listing(under, boards_dir, stored)
            if onerror is not None and shown is not None:
                onerror(shown, exc)

        yield from canonical_listing(
            under,
            boards_dir,
            self._walk(subtree, recursive, _report if onerror is not None else None),
        )

    def _walk(
        self,
        subtree: Path,
        recursive: bool,
        onerror: Callable[[str, OSError], None] | None,
    ) -> Iterator[str]:
        """Stored project-relative paths of the files under *subtree*, sorted."""
        if recursive:
            candidates = []

            def _record_walk_error(exc: OSError) -> None:
                if onerror is None:
                    return
                failed = Path(exc.filename) if exc.filename else subtree
                onerror(posix_relpath(failed, self.root), exc)

            walk_onerror = _record_walk_error if onerror is not None else None
            for root_str, dirs, files in os.walk(
                subtree, topdown=True, onerror=walk_onerror
            ):
                dirs[:] = sorted(
                    d for d in dirs if d not in SKIP_SCAN_DIRS and d != LOCAL_STATE_DIR
                )
                root_path = Path(root_str)
                for name in files:
                    candidates.append(posix_relpath(root_path / name, self.root))
            yield from sorted(candidates)
        else:
            for p in sorted(subtree.iterdir()):
                if p.is_file():
                    yield posix_relpath(p, self.root)

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        """``os.scandir`` over *under*, filtered/sorted per ``Project.iter_dir``.

        The symlink-escape check mirrors the historical ``list_dir_entries``
        sandbox guard: an entry whose resolved target lands outside the
        project root is excluded (e.g. a ``charts/tasks -> ../../tasks``
        symlink), even though ``os.scandir`` itself would happily follow it.
        The one exception is a symlinked ``dbt_packages/<pkg>`` (see
        ``symlinked_package_path``).
        """
        boards_dir = self.boards_dir
        stored_under = self._stored(under)
        subtree = self.root if stored_under == "." else self.root / stored_under
        if not subtree.is_dir():
            return

        dirs: list[str] = []
        files: list[str] = []
        with os.scandir(subtree) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                # An entry the boards folder moved elsewhere (or the config file
                # sitting in a boards folder that is the project root) is not a
                # child of *under*.
                child = entry.name if under == "." else f"{under}/{entry.name}"
                stored_child = (
                    entry.name
                    if stored_under == "."
                    else f"{stored_under}/{entry.name}"
                )
                if canonical_in_listing(under, boards_dir, stored_child) != child:
                    continue
                entry_path = Path(entry.path)
                if (
                    not entry_path.resolve().is_relative_to(self.root)
                    and symlinked_package_path(self.root, entry_path) is None
                ):
                    continue
                if entry.is_dir():
                    if entry.name in SKIP_SCAN_DIRS:
                        continue
                    dirs.append(entry.name)
                else:
                    files.append(entry.name)

        # The boards folder is `charts` at the canonical root, wherever it is.
        if under == "." and boards_dir != CHARTS_SUBDIR:
            if (self.root / boards_dir).is_dir():
                dirs.append(CHARTS_SUBDIR)

        for names, handle in ((dirs, ProjectDirectory), (files, ProjectPath)):
            for name in sorted(names, key=str.lower):
                yield handle(self, name if under == "." else f"{under}/{name}")

    def write_text(self, relpath: str, content: str) -> None:
        """Write *content* under the project root, creating parent directories.

        Resolves symlinks and refuses any path that escapes the root — a
        stronger containment check than the read methods' ``assert_relpath``,
        which collapses ``..`` but does not follow symlinks. Raises ValueError
        for an escaping path, OSError on a write failure. A ``dbt_packages/``
        path is dbt-managed and refused outright — ``dbt deps`` is the only
        writer.

        The package-relpath check runs against the RESOLVED path's
        project-relative form, not the raw *relpath* string — checking the
        raw string only inspects its first path component, so a
        ``..``-escape (``charts/../dbt_packages/pkg/charts/x.yml``) passed
        that guard while ``resolve()`` still collapsed it back onto the
        package file underneath.
        """
        candidate = Path(self._stored_for_write(relpath))
        candidate = candidate if candidate.is_absolute() else self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path {relpath!r} escapes the project root")
        if is_package_relpath(posix_relpath(resolved, self.root)):
            raise ValueError(f"path {relpath!r} is a package board — read-only")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def delete_text(self, relpath: str) -> None:
        """Delete the file at *relpath*, resolving symlinks and refusing any path
        that escapes the root — the same containment check as ``write_text``.

        Raises ValueError for an escaping path, FileNotFoundError if the file
        does not exist (``Path.unlink``'s default — no silent no-op). A
        ``dbt_packages/`` path is refused outright — see ``write_text`` for why
        the check runs against the resolved path, not the raw *relpath*.
        """
        candidate = Path(self._stored_for_write(relpath))
        candidate = candidate if candidate.is_absolute() else self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path {relpath!r} escapes the project root")
        if is_package_relpath(posix_relpath(resolved, self.root)):
            raise ValueError(f"path {relpath!r} is a package board — read-only")
        resolved.unlink()

    @cached_property
    def sources(self) -> ProjectSourcesConfig:
        # Lazy import avoids a cycle: compile/config.py imports Project at top-level.
        from dbt_charts.core.compile.config import load_project_sources

        return load_project_sources(self)

    @cached_property
    def declared_sources(self) -> ProjectSourcesConfig:
        from dbt_charts.core.compile.config import load_declared_sources

        return load_declared_sources(self)
