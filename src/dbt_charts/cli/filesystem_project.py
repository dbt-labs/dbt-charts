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
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dbt_charts.core.project import (
    ALL_FILES_GLOB,
    CHARTS_SUBDIR,
    FACES_FALLBACK_SUBDIR,
    PROJECT_CONFIG_NAME,
    SKIP_SCAN_DIRS,
    GrepHit,
    Project,
    ProjectDirectory,
    ProjectFileQueries,
    ProjectPath,
    assert_relpath,
    glob_literal_prefix,
    glob_to_regex,
    is_package_relpath,
    package_board_roots,
    posix_relpath,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dbt_charts.core.compile.config import ProjectSourcesConfig


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

    def grep(self, term: str, glob: str = ALL_FILES_GLOB) -> Iterator[GrepHit]:
        needle = term.encode("utf-8")
        for pf in self.glob(glob):
            try:
                data = self._project.read_bytes(pf.relpath)
            except OSError:
                continue
            if needle not in data:
                continue  # one C-speed scan; no decode for the common no-match file
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # not valid text -> whole file skipped, never raised
            for i, line in enumerate(text.splitlines(), start=1):
                if term in line:
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
    def charts_dir(self) -> Path:
        return self.root / self._boards_physical_subdir()

    def package_charts_dirs(self) -> list[Path]:
        """Absolute paths of every installed package's ``charts/`` directory.

        Filesystem-only edge currency (mirrors ``charts_dir``) — probed per
        call, not cached, so ``dct serve``'s alias-index invalidation watch
        notices a package added or removed by a ``dbt deps`` re-run.
        """
        return [self.root / root.relpath for root in package_board_roots(self)]

    def _boards_physical_subdir(self) -> str:
        """The on-disk directory that answers canonical ``charts/`` paths.

        Pre-rename projects still carry ``faces/``; they keep working through a
        store-level translation — consumers always speak ``charts/`` and never
        see the physical name. ``charts/`` wins outright when both exist (the
        two are never merged), and nothing here ever creates ``faces/``.
        Probed per call, not cached: a live ``dct serve`` must notice when the
        user renames the directory. Written surfaces (``dct init``, schemas,
        docs) stay ``charts/``-only.
        """
        if (self.root / CHARTS_SUBDIR).is_dir():
            return CHARTS_SUBDIR
        if (self.root / FACES_FALLBACK_SUBDIR).is_dir():
            return FACES_FALLBACK_SUBDIR
        return CHARTS_SUBDIR

    def _physical(self, relpath: str) -> str:
        """Map a canonical relpath to its physical location under the fallback."""
        physical = self._boards_physical_subdir()
        if physical == CHARTS_SUBDIR:
            return relpath
        if relpath == CHARTS_SUBDIR:
            return physical
        if relpath.startswith(f"{CHARTS_SUBDIR}/"):
            return f"{physical}{relpath[len(CHARTS_SUBDIR) :]}"
        return relpath

    def _canonical(self, relpath: str) -> str:
        """Inverse of ``_physical`` for paths yielded by listings."""
        physical = self._boards_physical_subdir()
        if physical == CHARTS_SUBDIR:
            return relpath
        if relpath == physical:
            return CHARTS_SUBDIR
        if relpath.startswith(f"{physical}/"):
            return f"{CHARTS_SUBDIR}{relpath[len(physical) :]}"
        return relpath

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

        Not part of the pluggable file-content interface
        (``read_text``/``read_bytes``/``exists``/``iter_files``) — filesystem
        only, which is why it lives here rather than on ``Project``.
        """
        return (self.root / relpath).resolve()

    def exists(self, relpath: str) -> bool:
        """Raises ValueError if *relpath* is absolute, empty/blank, or escapes
        above the project root."""
        assert_relpath(relpath)
        return (self.root / self._physical(relpath)).exists()

    def read_text(self, relpath: str) -> str:
        """Raises FileNotFoundError if the path does not exist, or ValueError
        if *relpath* is absolute, empty/blank, or escapes above the project
        root."""
        assert_relpath(relpath)
        return (self.root / self._physical(relpath)).read_text(encoding="utf-8")

    def read_bytes(self, relpath: str) -> bytes:
        """Raises FileNotFoundError if the path does not exist, or ValueError
        if *relpath* is absolute, empty/blank, or escapes above the project
        root."""
        assert_relpath(relpath)
        return (self.root / self._physical(relpath)).read_bytes()

    def file_version(self, relpath: str) -> str:
        """Stat signature (``mtime_ns:size``) — no file read on the common path.

        This is exactly git's racy-git change detection: mtime + size is a
        reliable "did this file change?" signal for the local edit-refresh loop,
        for tracked, dirty, and untracked files alike, and costs one ``stat``.
        """
        assert_relpath(relpath)
        st = (self.root / self._physical(relpath)).stat()
        return f"{st.st_mtime_ns}:{st.st_size}"

    def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
        """Skips traversal-noise directories (``SKIP_SCAN_DIRS``)."""
        under = self._physical(under)
        subtree = self.root if under == "." else self.root / under
        if not subtree.is_dir():
            return

        if recursive:
            candidates = []
            for root_str, dirs, files in os.walk(subtree, topdown=True):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_SCAN_DIRS)
                root_path = Path(root_str)
                for name in files:
                    candidates.append(posix_relpath(root_path / name, self.root))
            yield from (self._canonical(c) for c in sorted(candidates))
        else:
            for p in sorted(subtree.iterdir()):
                if p.is_file():
                    yield self._canonical(posix_relpath(p, self.root))

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        """``os.scandir`` over *under*, filtered/sorted per ``Project.iter_dir``.

        The symlink-escape check mirrors the historical ``list_dir_entries``
        sandbox guard: an entry whose resolved target lands outside the
        project root is excluded (e.g. a ``charts/tasks -> ../../tasks``
        symlink), even though ``os.scandir`` itself would happily follow it.
        """
        under = self._physical(under)
        subtree = self.root if under == "." else self.root / under
        if not subtree.is_dir():
            return

        dirs: list[os.DirEntry[str]] = []
        files: list[os.DirEntry[str]] = []
        with os.scandir(subtree) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    Path(entry.path).resolve().relative_to(self.root)
                except ValueError:
                    continue
                if entry.is_dir():
                    if entry.name in SKIP_SCAN_DIRS:
                        continue
                    dirs.append(entry)
                else:
                    files.append(entry)

        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())
        for entry in dirs:
            child = entry.name if under == "." else f"{under}/{entry.name}"
            yield ProjectDirectory(self, self._canonical(child))
        for entry in files:
            child = entry.name if under == "." else f"{under}/{entry.name}"
            yield ProjectPath(self, self._canonical(child))

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
        candidate = Path(self._physical(relpath))
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
        candidate = Path(self._physical(relpath))
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
