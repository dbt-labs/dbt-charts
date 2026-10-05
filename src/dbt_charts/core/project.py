from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from functools import cached_property
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from posixpath import normpath
from typing import TYPE_CHECKING, Any, NamedTuple

import yaml

from dbt_charts.core.utils import IMAGE_SUFFIXES, UniqueKeyLoader

if TYPE_CHECKING:
    from dbt_charts.core.compile.config import ProjectSourcesConfig
    from dbt_charts.core.compile.models.config import ProjectCacheConfig

# The conventional subtree under the project root that holds board files.
CHARTS_SUBDIR = "charts"

# The pre-rename spelling hosts still answer READS from when a project has no
# charts/ tree. Never written, never advertised.
FACES_FALLBACK_SUBDIR = "faces"

# Cascade fragments are project inputs, not standalone dashboards.
META_FILENAMES: frozenset[str] = frozenset({"meta.yaml", "meta.yml"})

# What `dct init` creates, what `dct` reads, and what the IDE and completion
# schemas decorate.
PROJECT_CONFIG_NAME = "dbt_charts.yml"


def assert_relpath(ref: str, anchor: str = ".") -> str:
    """Return *ref* joined onto *anchor* and normalized; raise ValueError unless
    it is a relative path contained under the root.

    Rejects empty/blank refs, absolute paths, and refs that would resolve above
    the project root once joined to *anchor* (a normalized project-relative
    directory, "." for the root itself). Callers that only guard ignore the
    result.

    File identity is a POSIX relpath, so the guard is host-independent: it must
    not depend on the OS ``dct`` runs on, because refs reach it from untrusted
    input (authored board paths, Cloud form fields). A backslash, drive letter,
    or leading root is POSIX-relative but on Windows makes ``root / ref`` drop
    ``root`` (``pathlib`` discards the left operand for a rooted/drived right
    one), so all are rejected — which also keeps the escape check below sound,
    since after it every separator is ``/`` and ``posixpath.normpath`` applies.
    """
    if not ref or not ref.strip():
        raise ValueError(f"file ref must not be empty, got {ref!r}")
    if "\\" in ref or is_absolute_any_os(ref):
        raise ValueError(f"file ref must be relative, got {ref!r}")

    # normpath collapses ".."/"." segments; an escaping path then starts with ".."
    joined = normpath(ref if anchor == "." else f"{anchor}/{ref}")
    if joined.startswith(".."):
        raise ValueError(f"file ref {ref!r} would escape above the project root")
    return joined


def posix_relpath(path: PurePath, root: PurePath) -> str:
    """Project-relative POSIX identity for *path* under *root*.

    ``str(Path.relative_to(...))`` emits the OS-native separator — a no-op on
    POSIX, but backslashes on Windows, which ``assert_relpath`` then rejects.
    File identity is always a POSIX relpath, so normalize with ``as_posix()``.
    *path* and *root* are typed ``PurePath`` (not ``PurePosixPath``) because
    real callers pass OS-native ``Path`` objects from disk.
    """
    return path.relative_to(root).as_posix()


def is_absolute_any_os(s: str) -> bool:
    """True if *s* is absolute under either POSIX or Windows path rules.

    ``Path(s).is_absolute()`` only recognizes the running host's own
    convention — a real absolute Windows path (``C:\\...``) has no leading
    ``/``, so ``PurePosixPath`` judges it relative, and a POSIX-absolute
    path has no drive letter, so ``PureWindowsPath.is_absolute()`` judges it
    relative. Host-independent classification of an arbitrary config- or
    user-supplied path string needs both. ``drive or root`` (rather than
    ``PureWindowsPath.is_absolute()``, which requires both) also classifies
    a driveless-rooted path (``\\srv\\share``) and a rootless drive-relative
    one (``C:x``) as absolute.
    """
    windows = PureWindowsPath(s)
    return bool(PurePosixPath(s).is_absolute() or windows.drive or windows.root)


# Directories skipped when walking the project tree for files.
# A host that overrides Project's file-access methods (e.g. CloudManagedProject's
# git-blob reader) applies the same filter without duplicating the set.
SKIP_SCAN_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "build",
        "dist",
        "node_modules",
        # dbt generated dirs — a dbt project root is a valid dbt charts root
        # (dbt_project.yml is a root marker), and these are full of .yml/.md
        # files that would pass the board-candidate suffix test. Direct-path
        # reads (target/manifest.json, target/super_schema.json) are
        # unaffected: they use exists/read_text, not iteration.
        "target",
        "dbt_packages",
        "logs",
    }
)

# Markdown document/board suffixes (matched case-insensitively on the filename).
MARKDOWN_SUFFIXES: tuple[str, ...] = (".md", ".markdown")

# File suffixes recognized as candidate board files during enumeration.
BOARD_CANDIDATE_SUFFIXES: tuple[str, ...] = (".yml", ".yaml", *MARKDOWN_SUFFIXES)

# Filenames a directory URL resolves to as its index board, in priority order.
INDEX_CANDIDATE_NAMES: tuple[str, ...] = tuple(
    f"index{s}" for s in BOARD_CANDIDATE_SUFFIXES
)

# The closed set of file suffixes dbt charts serves from a project tree:
#   BOARD_CANDIDATE_SUFFIXES — config + board files (.yml/.yaml) and markdown
#                  boards (.md/.markdown); composed in so this set can never
#                  drift behind a new board suffix.
#   IMAGE_SUFFIXES — images embeddable from board markdown (core/utils.py).
#   .csv/.json/.parquet — file sources (CsvSourceConfig etc.), whose `files:`
#                  paths are required to carry these exact extensions
#                  (models/source.py `_validate_files_mapping`)
# Matched case-insensitively on the filename suffix. Consumed by Cloud's
# subdir-scoped git-blob fetch to skip files it will never serve, without
# reading any blob content (GitEdge.objects.subtree_blob_shas in Cloud).
SERVED_FILE_SUFFIXES: frozenset[str] = (
    frozenset(BOARD_CANDIDATE_SUFFIXES)
    | frozenset(IMAGE_SUFFIXES)
    | {".csv", ".json", ".jsonl", ".parquet"}
)


def is_private_name(relpath: str) -> bool:
    """A leading-underscore basename is a partial/config file, not enumerable.

    Basename-only by design: charts/_drafts/foo.yml is NOT private here — directory
    privacy (e.g. Cloud's _drafts/ slug skip) is host display policy, not this rule.
    """
    return PurePosixPath(relpath).name.startswith("_")


def is_board_candidate(relpath: str) -> bool:
    """True iff relpath is enumerable as a standalone board: board suffix, not private."""
    return PurePosixPath(
        relpath
    ).suffix in BOARD_CANDIDATE_SUFFIXES and not is_private_name(relpath)


# The whole-project glob: matches every file at any depth. grep's default scope.
ALL_FILES_GLOB = "**/*"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a shell glob to a regex over project-relative POSIX paths.

    The single pattern language for every ``ProjectFileQueries`` host: ``*``
    matches within a path segment (does not cross ``/``); ``**`` crosses ``/``
    for recursive matches (e.g. ``charts/**/*.yml``); ``?`` matches one
    non-separator character. Everything else is matched literally.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i : i + 3] == "**/":
            # Globstar segment: zero or more leading path segments, so
            # `charts/**/*.yml` matches both `charts/x.yml` and `charts/sub/x.yml`.
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def is_glob(path: str) -> bool:
    """True when *path* contains a shell glob character (``*`` or ``?``)."""
    return "*" in path or "?" in path


def glob_literal_prefix(pattern: str) -> str:
    """The leading wildcard-free directory segments of a glob pattern.

    The subtree a host may bound its walk to before matching: ``charts/**/*.yml``
    → ``"charts"``; ``*.yml`` → ``"."`` (no prunable prefix). The final segment
    is always a file matcher, never part of the prefix.
    """
    prefix: list[str] = []
    for segment in pattern.split("/")[:-1]:
        if "*" in segment or "?" in segment:
            break
        prefix.append(segment)
    return "/".join(prefix) if prefix else "."


class GrepHit(NamedTuple):
    """One matching line yielded by ``ProjectFileQueries.grep``.

    A result record, not a file handle — file identity is the project-relative
    path string, per the core path-identity convention.
    """

    relpath: str
    line_number: int
    line: str


class ProjectFileQueries(ABC):
    """Multi-file queries over a project — the host-optimized search seam.

    THE CONTRACT (every host implementation must satisfy all of these; the
    contract tests in ``tests/core/test_project.py`` pin them on the dict
    double so no host can drift):

    - ``glob`` yields ``ProjectPath`` handles sorted by relpath; ``grep``
      yields ``GrepHit`` records in relpath order.
    - Pattern language is exactly ``glob_to_regex``'s, identical on every
      host. Patterns are project-relative: absolute or root-escaping patterns
      raise ``ValueError`` (validate via ``assert_relpath``).
    - Both methods operate on PROJECT-relative paths end to end. Host-level
      path translation (e.g. a monorepo ``git_subdirectory``) must be
      invisible here — in particular it is never subject to skip filtering.
    - A pattern whose literal prefix (``glob_literal_prefix``) enters a
      ``SKIP_SCAN_DIRS`` directory yields nothing.
    - ``grep`` is a case-sensitive substring match per line; files that cannot
      be read as text (binary, vanished, over a host size cap) are skipped,
      never raised.
    - Symlink and skip-dir enumeration policy is the host's ``iter_files``
      policy; implementations do not add private per-method rules.

    Both methods are abstract by design: there is no lowest-common-denominator
    default, so a host can never silently fall through to an unoptimized full
    scan. Each host owns its own ``glob``/``grep`` implementation against its
    own storage — there is no shared base class to opt into.
    """

    @abstractmethod
    def glob(self, pattern: str) -> Iterator[ProjectPath]:
        """Yield handles for files matching *pattern*, sorted by relpath."""

    @abstractmethod
    def grep(self, term: str, glob: str = ALL_FILES_GLOB) -> Iterator[GrepHit]:
        """Yield lines containing *term*, in relpath order, scoped by *glob*."""


class ProjectPath:
    """A handle to a project-relative file. NOT os.PathLike — exposes no Path.

    All reads delegate to the owning Project so subclasses (e.g. Cloud) can
    serve files from an in-memory dict or a remote store without disk access.
    """

    __slots__ = ("_project", "_pure", "_relpath")

    def __init__(self, project: Project, relpath: str) -> None:
        self._project = project
        self._relpath = relpath
        self._pure = PurePosixPath(relpath)

    @property
    def relpath(self) -> str:
        return self._relpath

    @property
    def project(self) -> Project:
        return self._project

    @property
    def name(self) -> str:
        """The file's basename, e.g. ``report.yml`` for ``charts/sub/report.yml``."""
        return self._pure.name

    @property
    def stem(self) -> str:
        """The basename without its suffix, e.g. ``report`` for ``report.yml``."""
        return self._pure.stem

    @property
    def is_yaml(self) -> bool:
        """True if this is a YAML document (by the project's file convention)."""
        return self._relpath.endswith((".yml", ".yaml"))

    @property
    def is_markdown(self) -> bool:
        """True if this is a markdown document (by the project's file convention)."""
        return self._relpath.lower().endswith(MARKDOWN_SUFFIXES)

    @property
    def is_private(self) -> bool:
        """True if this is a partial/config file, not a standalone board.

        Leading-underscore names (``_partial.yml``) are
        includes or config, never enumerated as boards in their own right.
        """
        return is_private_name(self._relpath)

    @property
    def is_meta(self) -> bool:
        """True if this is a cascade fragment, not a standalone board.

        ``meta.yml``/``meta.yaml`` carry inherited defaults for their
        directory; they compile as cascade input, never as their own board.
        """
        return self.name in META_FILENAMES

    def exists(self) -> bool:
        return self._project.exists(self._relpath)

    def read_text(self) -> str:
        return self._project.read_text(self._relpath)

    def read_yaml(self) -> Any:
        return self._project.read_yaml(self._relpath)

    def read_board(self) -> BoardFile:
        """Return this path as a store-backed ``BoardFile`` (content read lazily
        from the store; raises on access if the file is absent)."""
        return _StoredBoard(self)

    @property
    def parent(self) -> ProjectDirectory:
        """The directory this file lives in — the anchor relative refs resolve against."""
        return ProjectDirectory(self._project, str(self._pure.parent))

    def __lt__(self, other: ProjectPath) -> bool:
        """Order by POSIX path parts, so ``sorted()`` needs no ``key=``."""
        return self._pure.parts < other._pure.parts


class ProjectDirectory:
    """A handle to a project-relative directory. NOT os.PathLike — exposes no Path.

    Resolves relative refs against itself via ``path(ref)``, delegating reads to
    the owning Project like ProjectPath does.
    """

    __slots__ = ("_project", "_pure", "_relpath")

    def __init__(self, project: Project, relpath: str) -> None:
        self._project = project
        self._relpath = relpath
        self._pure = PurePosixPath(relpath)

    @property
    def relpath(self) -> str:
        return self._relpath

    @property
    def project(self) -> Project:
        return self._project

    @property
    def name(self) -> str:
        """The directory's basename, e.g. ``sub`` for ``charts/sub``."""
        return self._pure.name

    @property
    def parent(self) -> ProjectDirectory:
        """The containing directory; the project root ('.') is its own parent."""
        return ProjectDirectory(self._project, str(self._pure.parent))

    def __truediv__(self, ref: str) -> ProjectPath:
        """Return a ProjectPath for *ref* resolved relative to this directory.

        *ref* must be a relative path. It may traverse into subdirectories but
        must not escape above the project root. Raises ValueError for absolute
        paths, empty/blank refs, or paths that escape the root.
        """
        return ProjectPath(self._project, assert_relpath(ref, self._relpath))


class BoardFile:
    """A board's content bound to its (optional) project location.

    The compiler's and renderer's input. ``path`` — a ``ProjectPath`` or
    ``None`` — anchors the ``charts/meta.yml`` cascade, relative refs, and the
    error/link identity when the board is a real stored file. It is ``None`` for
    in-memory content with no location (AI output, scratch), which then compiles
    with no meta.yml cascade beyond the inherited default source and never
    leaks a fabricated leaf into errors or preview URLs. ``content`` is a
    property, so a stored board reads lazily while in-memory content supplies a
    buffer.
    """

    path: ProjectPath | None = None

    @property
    def content(self) -> str:
        raise NotImplementedError


class _StoredBoard(BoardFile):
    """A board read from the store via ``ProjectPath.read_board`` — its content is
    the file's text, read lazily on access."""

    path: ProjectPath

    def __init__(self, path: ProjectPath) -> None:
        self.path = path

    @property
    def content(self) -> str:
        return self.path.read_text()


class InMemoryBoard(BoardFile):
    """In-memory board content, optionally anchored at a real ``path`` so the
    cascade + relative refs resolve (an editor buffer of a stored board, or a
    loaded gallery example). ``path`` is required and keyword-only so every
    call site states its intent explicitly — pass ``path=None`` for genuinely
    locationless generated content (AI output, scratch); passing an
    unintended ``None`` silently opts out of the cascade."""

    def __init__(self, content: str, *, path: ProjectPath | None) -> None:
        self.path = path
        self._content = content

    @property
    def content(self) -> str:
        return self._content


class Project(ABC):
    """The structure of a dbt charts project — the host-substitution seam.

    Answers "which file holds X" for the canonical project files. The five
    file-access methods (``read_text``, ``read_bytes``, ``exists``,
    ``iter_files``, ``write_text``) and ``sources`` are abstract: an embedding
    host implements them against its own backing store. dct core's local-filesystem
    implementation is ``FilesystemProject``; Cloud's git-blob-store
    implementation is ``CloudManagedProject``. Everything downstream reads
    through the handles (``ProjectPath``/``ProjectDirectory``) unchanged
    regardless of which implementation is behind them. The base holds no
    ``Path``: a real filesystem ``root`` (and the members derived from it —
    ``charts_dir``, ``path_for_fspath``, ``directory_for_fspath``) is
    ``FilesystemProject``-only edge currency, not part of the abstract seam.
    """

    @abstractmethod
    def exists(self, relpath: str) -> bool:
        """Return True if a project-relative path exists in the backing store.

        Implementations own their own relpath containment check.
        """

    @abstractmethod
    def read_text(self, relpath: str) -> str:
        """Return the UTF-8 contents of a project-relative file.

        Raises FileNotFoundError if the path does not exist. Implementations
        own their own relpath containment check.
        """

    @abstractmethod
    def read_bytes(self, relpath: str) -> bytes:
        """Return the raw bytes of a project-relative file.

        Raises FileNotFoundError if the path does not exist. Used by
        FileSourceMaterializer to read CSV/JSON/Parquet (Cloud: from git
        blobs). Implementations own their own relpath containment check.
        """

    def read_yaml(self, relpath: str) -> Any:
        """Return the parsed YAML contents of a project-relative file.

        Raises FileNotFoundError if the path does not exist (inherited from
        ``read_text``).
        """
        return yaml.load(self.read_text(relpath), Loader=UniqueKeyLoader)

    def config_document(self) -> tuple[str, Any] | None:
        """Return ``(filename, raw parsed YAML)`` for this host's project
        config file, or ``None`` if this host exposes no project config file
        at all.

        Default: ``None``. A host with no filesystem-shaped config file (e.g.
        Cloud's git-blob store, via ``CloudManagedProject``) inherits this and
        stays inert — it reads no engine config (``cache:``/``warnings:``/
        ``server:``/etc.) from a tenant's committed ``dbt_charts.yml``, matching
        its historical ``config_file = None`` override. ``FilesystemProject``
        overrides this to probe ``dbt_charts.yml``.
        """
        return None

    def manifest_project(self) -> Project:
        """Return the ``Project`` whose file seam holds the linked dbt project
        (``target/manifest.json``, ``dbt_project.yml``, ``profiles.yml``).

        Default: ``self`` (the dbt project lives at this project's own root).
        ``FilesystemProject`` overrides this to return ``self.dbt_project``
        when a ``dbt_project_dir`` links an external directory.
        """
        return self

    @abstractmethod
    def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
        """Yield project-relative paths of ALL files under *under*, sorted.

        *under* is a project-relative subtree (``"."`` means the whole project).
        Format-neutral: no suffix filter — callers own that. Yields nothing
        when the subtree does not exist. Paths are yielded in lexicographic
        order; deterministic diagnostics and stable build outputs rely on this.
        """

    @abstractmethod
    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        """Yield the immediate children of *under*: a ``ProjectPath`` per file, a
        ``ProjectDirectory`` per subdirectory.

        The filter/sort contract lives HERE so every reader (``list_dir_entries``,
        ``lazy_dir_context``, and any future navigation surface) shares one
        definition instead of drifting: directories first, then files, each
        group sorted case-insensitively by name; dotfiles excluded;
        ``SKIP_SCAN_DIRS`` directory names excluded. ``_``-prefixed boards ARE
        included — nav shows partials, unlike ``iter_boards`` which drops them.

        Yields immediate children that exist in the backing store — not a
        promise of cross-host byte-identical output (hosts differ in what
        "exists" means: a git-blob store has no empty-directory entries, so a
        subdirectory surfaces there iff it holds at least one tracked file).

        *under* is a project-relative subtree (``"."`` means the project root).
        Yields nothing when the subtree does not exist. Implementations own
        their own relpath containment check.
        """

    @abstractmethod
    def write_text(self, relpath: str, content: str) -> None:
        """Write UTF-8 *content* to a project-relative path.

        The write counterpart of the read lens: an embedding host implements it
        against its own backing store (``FilesystemProject`` writes to disk;
        Cloud commits to its git-blob store). Implementations own their own
        containment check and may restrict which paths are writable — raise
        ``ValueError`` to refuse (a path that escapes the root, or a store that
        only accepts certain subtrees).
        """

    @abstractmethod
    def delete_text(self, relpath: str) -> None:
        """Delete a project-relative file.

        The delete counterpart of ``write_text``: an embedding host implements it
        against its own backing store (``FilesystemProject`` unlinks the file;
        Cloud deletes the git-blob-store board via ``Branch.delete_board``). Raises
        ``FileNotFoundError`` if the path does not exist — no silent no-op.
        Implementations own their own containment check and may restrict which
        paths are deletable — raise ``ValueError`` to refuse (a path that escapes
        the root, or a store that only accepts certain subtrees).
        """

    def file_version(self, relpath: str) -> str:
        """Return a content-version token for a project-relative file.

        The token changes whenever the file's content changes and is stable
        while it does not. It is the single file-identity used by file-source
        cache keys (both the materializer's internal file-table cache and the
        outer result cache), so a warm dashboard can check the cache without
        re-parsing the data file.

        The base implementation hashes the file bytes through ``read_bytes`` —
        always correct, since it routes through the host's own store (never the
        server filesystem), so the silent-cwd-fallthrough hazard that makes the
        four read primitives ``@abstractmethod`` does not apply here. It is NOT
        cheap, though: it reads the whole file. Hosts SHOULD override with a
        cheap token that needs no full read —

        - ``FilesystemProject``: a stat signature (``mtime_ns:size``) — git's own
          racy-git change-detection primitive.
        - A git-blob-backed host (Cloud): the blob OID, already content-addressed
          and read from the tree entry without inflating the blob.

        Raises FileNotFoundError if the path does not exist.
        """
        return hashlib.sha256(self.read_bytes(relpath)).hexdigest()[:16]

    def iter_boards(
        self, *, under: str = CHARTS_SUBDIR, recursive: bool = True
    ) -> Iterator[ProjectPath]:
        """Yield ProjectPath handles for candidate board files under *under*.

        Applies board-suffix (BOARD_CANDIDATE_SUFFIXES) and underscore-prefix
        filters so callers only see plausible board files. Skips traversal-noise
        dirs via ``iter_files``. Yields nothing when the subtree does not exist.
        """
        for relpath in self.iter_files(under, recursive=recursive):
            if not is_board_candidate(relpath):
                continue
            yield ProjectPath(self, relpath)

    def path(self, relpath: str) -> ProjectPath:
        """Return a ProjectPath handle for a project-relative path."""
        return ProjectPath(self, relpath)

    def directory(self, relpath: str = ".") -> ProjectDirectory:
        """Return a ProjectDirectory handle. No argument anchors at the project root."""
        return ProjectDirectory(self, relpath)

    # cached_property (not property) on the abstract declaration: concrete
    # overrides cache expensive per-instance work (filesystem probe / DB
    # query) via cached_property, and pyright's override check rejects a
    # cached_property overriding a plain abstract property.
    #
    # Unlike the four file primitives above (plain `@abstractmethod`, which
    # ABCMeta enforces at construction time), `functools.cached_property`
    # does not forward `__isabstractmethod__` — only `property` does — so
    # ABCMeta never registers `sources` in `__abstractmethods__` and a
    # subclass that omits it constructs without error. The `raise` below is
    # the substitute enforcement: a missing override fails loudly the first
    # time `.sources` is accessed instead of silently returning `None`.
    @cached_property
    @abstractmethod
    def sources(self) -> ProjectSourcesConfig:
        """Return the project's source registry."""
        raise NotImplementedError("Project subclasses must implement `sources`.")

    # Same cached_property + abstractmethod enforcement shape as `sources`:
    # constructs without an override, fails loudly on first access. Pyright
    # additionally rejects instantiation of subclasses missing the override
    # in analyzed packages.
    @cached_property
    @abstractmethod
    def files(self) -> ProjectFileQueries:
        """Return the host's multi-file query surface (see ProjectFileQueries)."""
        raise NotImplementedError("Project subclasses must implement `files`.")

    # Same cached_property + abstractmethod enforcement shape as `sources`/`files`.
    @cached_property
    @abstractmethod
    def name(self) -> str:
        """Return the project's human-readable display name.

        A host-supplied identity string, not a path computation: each host
        provides the label a user recognizes for the project. Callers that
        need a display label (e.g. the render-layer root breadcrumb) read
        this rather than deriving one from a filesystem path.
        """
        raise NotImplementedError("Project subclasses must implement `name`.")

    @cached_property
    def warnings_ignore(self) -> frozenset[str]:
        # Lazy import avoids a cycle: compile/config.py imports Project at top-level.
        from dbt_charts.core.compile.config import get_project_warnings_ignore

        return get_project_warnings_ignore(self)

    @cached_property
    def cache(self) -> ProjectCacheConfig:
        """The project's `cache:` block — the root of the cache cascade.

        A per-project value, deliberately not the process-global config: one
        Cloud worker compiles for many orgs.
        """
        from dbt_charts.core.compile.config import get_project_cache_root

        return get_project_cache_root(self)


def iter_dir_from_relpaths(
    project: Project, under: str, relpaths: Iterable[str]
) -> Iterator[ProjectPath | ProjectDirectory]:
    """Derive ``Project.iter_dir``'s immediate-children result from a flat
    file-relpath iterable.

    Shared by every host whose backing store has no native directory listing
    and instead only enumerates files (Cloud's git-blob tree walk, and the
    in-memory/dict test doubles standing in for it): a name one path segment
    under *under* with more path beneath it is a subdirectory — surfaced iff
    at least one relpath holds it, since these stores have no empty-directory
    concept — and a name with nothing beneath it is a direct file. Applies the
    same filter/sort contract as ``FilesystemProject.iter_dir``: dirs first,
    then files, each group case-insensitive; dotfiles and ``SKIP_SCAN_DIRS``
    names excluded. *relpaths* need not be pre-scoped to *under* — this
    function does its own prefix filtering.
    """
    prefix = "" if under == "." else f"{under}/"
    dir_names: set[str] = set()
    file_names: list[str] = []
    for relpath in relpaths:
        if not relpath.startswith(prefix):
            continue
        rest = relpath[len(prefix) :]
        if not rest:
            continue
        name, sep, _ = rest.partition("/")
        if name.startswith("."):
            continue
        if sep:
            if name not in SKIP_SCAN_DIRS:
                dir_names.add(name)
        else:
            file_names.append(name)

    for name in sorted(dir_names, key=str.lower):
        child = name if under == "." else f"{under}/{name}"
        yield ProjectDirectory(project, child)
    for name in sorted(file_names, key=str.lower):
        child = name if under == "." else f"{under}/{name}"
        yield ProjectPath(project, child)
