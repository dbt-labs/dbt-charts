from __future__ import annotations

import hashlib
import re
import string
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
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

# The dbt-managed subtree holding installed packages. `dbt deps` is the only
# writer; a package's own `charts/` directory is a board root (see
# `board_roots`). dbt's `packages-install-path` override is not honored —
# discovery assumes the default location (convention only, documented in the
# published boards-and-packages guide).
DBT_PACKAGES_SUBDIR = "dbt_packages"


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
        # unaffected: they use exists/read_text, not iteration. dbt_packages
        # itself is pruned from ordinary recursive walks (glob/grep/`iter_dir(".")`)
        # — package boards are reached only through `board_roots`, which calls
        # `iter_dir(DBT_PACKAGES_SUBDIR)` directly rather than walking into it.
        "target",
        DBT_PACKAGES_SUBDIR,
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

# Jinja template suffix: `type: custom` charts read `templates/<name>.svg.j2`
# through the project, so Cloud has to fetch these blobs into its store.
TEMPLATE_SUFFIXES: tuple[str, ...] = (".j2",)

# The closed set of file suffixes dbt charts serves from a project tree:
#   BOARD_CANDIDATE_SUFFIXES — config + board files (.yml/.yaml) and markdown
#                  boards (.md/.markdown); composed in so this set can never
#                  drift behind a new board suffix.
#   IMAGE_SUFFIXES — images embeddable from board markdown (core/utils.py).
#   TEMPLATE_SUFFIXES — custom-chart SVG templates (see above).
#   .csv/.json/.parquet — file sources (CsvSourceConfig etc.), whose `files:`
#                  paths are required to carry these exact extensions
#                  (models/source.py `_validate_files_mapping`)
# Matched case-insensitively on the filename suffix. Consumed by Cloud's
# subdir-scoped git-blob fetch to skip files it will never serve, without
# reading any blob content (GitEdge.objects.subtree_blob_shas in Cloud).
SERVED_FILE_SUFFIXES: frozenset[str] = (
    frozenset(BOARD_CANDIDATE_SUFFIXES)
    | frozenset(IMAGE_SUFFIXES)
    | frozenset(TEMPLATE_SUFFIXES)
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


def is_package_relpath(relpath: str) -> bool:
    """True iff relpath sits under ``dbt_packages/`` — read-only, dbt-managed."""
    return PurePosixPath(relpath).parts[:1] == (DBT_PACKAGES_SUBDIR,)


class BoardRoot(NamedTuple):
    """One entry in the board-root table: a subtree that holds board files.

    ``relpath`` is the project-relative directory (``"charts"`` for the host,
    ``"dbt_packages/<pkg>/charts"`` for an installed package). ``namespace``
    is the URL/slug prefix a board under this root carries — ``""`` for the
    host root, the package directory name otherwise (the same namespace dbt
    already uses for that package's models, e.g. ``ref('zendesk', ...)``).
    """

    relpath: str
    namespace: str


def package_board_roots(project: Project) -> list[BoardRoot]:
    """The board root for every installed package that ships a ``charts/`` dir.

    Probes ``Project.exists(DBT_PACKAGES_SUBDIR)`` first and returns early
    when it is absent -- a Cloud host never has a ``dbt_packages/`` entry
    (gitignored, never committed), so this keeps discovery off any path that
    needs more than the minimal ``Project`` surface (``exists``/``iter_dir``)
    every implementation already provides. Only once the directory is known
    to exist does this read ``Project.iter_dir(DBT_PACKAGES_SUBDIR)`` -- one
    directory listing, not a recursive walk — so it shares ``iter_dir``'s
    symlink-escape exclusion (a `local:`-installed package is a symlink
    outside the project root and is silently excluded, the same guard
    ``FilesystemProject.iter_dir`` already applies) and its
    dotfile/SKIP_SCAN_DIRS filtering for free. A package with no ``charts/``
    subdirectory contributes no root.
    """
    if not project.exists(DBT_PACKAGES_SUBDIR):
        return []
    roots: list[BoardRoot] = []
    for pkg_entry in project.iter_dir(DBT_PACKAGES_SUBDIR):
        if not isinstance(pkg_entry, ProjectDirectory):
            continue
        for child in project.iter_dir(pkg_entry.relpath):
            if isinstance(child, ProjectDirectory) and child.name == CHARTS_SUBDIR:
                roots.append(BoardRoot(child.relpath, pkg_entry.name))
                break
    return roots


def _assert_no_root_namespace_collision(
    project: Project, roots: Sequence[BoardRoot]
) -> None:
    """Raise if a host ``charts/<pkg>`` entry claims a package's own namespace.

    A host ``charts/zendesk/`` directory or ``charts/zendesk.{yml,md}`` file
    alongside an installed ``zendesk`` package is the same bug class as an
    alias colliding with a real board file — resolved the same way, as a
    hard build-time error. Enforced HERE, inside ``board_roots`` itself,
    rather than by one caller (``dct serve``'s ``AliasIndex.build``) alone —
    every consumer of the root table (``iter_boards``, ``resolve_board_relpath``,
    search, the CLI move path, ...) must reject the ambiguous state the same
    way, not silently pick the host board.
    """
    package_namespaces = {root.namespace for root in roots if root.namespace}
    if not package_namespaces:
        return
    for child in project.iter_dir(CHARTS_SUBDIR):
        if isinstance(child, ProjectDirectory):
            candidate_name = child.name
        elif child.is_yaml or child.is_markdown:
            candidate_name = child.stem
        else:
            continue
        if candidate_name in package_namespaces:
            raise ValueError(
                f"Board root collision: host 'charts/{candidate_name}' and the "
                f"installed package {candidate_name!r} both claim the "
                f"{candidate_name!r} namespace."
            )


def board_roots(project: Project) -> list[BoardRoot]:
    """The full board-root table: the host root plus every package root.

    The host root is always first and always present (even when ``charts/``
    doesn't exist yet — enumeration over a missing root simply yields
    nothing), so it wins any ordering tie against a same-named package root.

    Raises ValueError if a package namespace collides with a host
    ``charts/<namespace>`` directory or board file (see
    ``_assert_no_root_namespace_collision``).
    """
    roots = [BoardRoot(CHARTS_SUBDIR, "")] + package_board_roots(project)
    _assert_no_root_namespace_collision(project, roots)
    return roots


def _split_board_root(relpath: str) -> tuple[str, tuple[str, ...]]:
    """Split *relpath* into (namespace, tail parts) by matching a board-root shape.

    Pure and lexical — the two recognized shapes (``charts/...`` and
    ``dbt_packages/<pkg>/charts/...``) are exactly what ``board_roots`` ever
    produces, so no project I/O is needed to recognize them. Raises
    ValueError if *relpath* is not under either shape.

    Splits on ``/`` rather than normalizing, so a malformed segment (``.``,
    ``..``, ``//``) survives into the slug for path validation to reject
    instead of being silently collapsed into a valid-looking one.
    """
    parts = tuple(relpath.split("/"))
    if parts[:1] == (CHARTS_SUBDIR,):
        return "", parts[1:]
    if (
        len(parts) >= 3
        and parts[0] == DBT_PACKAGES_SUBDIR
        and parts[2] == CHARTS_SUBDIR
    ):
        return parts[1], parts[3:]
    raise ValueError(f"{relpath!r} is not under a board root")


def board_namespace_for_root(root_relpath: str) -> str:
    """The namespace a board root's own relpath carries (see ``BoardRoot``)."""
    namespace, tail = _split_board_root(root_relpath)
    if tail:
        raise ValueError(f"{root_relpath!r} is not itself a board root")
    return namespace


def board_slug(relpath: str) -> str:
    """Author-facing file-identity slug for a board-root-relative path (file or
    directory).

    ``charts/finance/rev.yml`` -> ``finance/rev``; a package board
    ``dbt_packages/pkg_a/charts/overview.yml`` -> ``pkg_a/overview`` — the
    same namespace dbt already uses for that package's models. An ``index``
    leaf is kept (``charts/reports/index.yml`` -> ``reports/index``,
    ``charts/index.yml`` -> ``index``): this is a *file*-identity slug, so
    every board file must map to a distinct value — collapsing ``index``
    would make a directory and its own index board indistinguishable, and
    would corrupt relative-link resolution (which derives a board's
    directory from its slug). Collapsing ``index`` into a clean URL is a
    display concern for the URL layer alone (``alias_index._url_for_file``).
    Works for a directory relpath too (no suffix to strip), so the same
    function serves both board-file identity and directory-listing URLs.

    Raises ValueError if relpath is not under any board root.
    """
    namespace, tail_parts = _split_board_root(relpath)
    suffix = PurePosixPath(relpath).suffix
    if suffix in BOARD_CANDIDATE_SUFFIXES and tail_parts:
        tail_parts = (*tail_parts[:-1], PurePosixPath(tail_parts[-1]).stem)
    parts = ((namespace,) if namespace else ()) + tail_parts
    return "/".join(parts)


def board_relpath_candidates(slug: str, roots: Sequence[BoardRoot]) -> list[str]:
    """Ordered candidate relpaths for *slug* across every board root in *roots*.

    Mirrors the per-suffix + bare-extension candidate ordering used for a
    single root (a URL that already carries its own extension matches the
    bare join), scoped per matching root: the host root (namespace ``""``)
    matches every slug; a package root matches only when the slug's leading
    segment equals its namespace, or the slug equals the namespace exactly
    (the package's own index).
    """
    candidates: list[str] = []
    for root in roots:
        if root.namespace:
            if slug == root.namespace:
                rest = "index"
            elif slug.startswith(f"{root.namespace}/"):
                rest = slug[len(root.namespace) + 1 :]
            else:
                continue
        else:
            rest = slug if slug else "index"
        base = f"{root.relpath}/{rest}"
        candidates.extend(f"{base}{s}" for s in BOARD_CANDIDATE_SUFFIXES)
        candidates.append(base)
    return candidates


def board_directory_candidates(
    clean_path: str, roots: Sequence[BoardRoot]
) -> list[tuple[BoardRoot, str]]:
    """Every board root *clean_path* could resolve under, paired with the
    path remaining inside that root ("" for the root's own directory).

    The directory-URL counterpart of ``board_relpath_candidates`` (which
    resolves a *file* URL): the host root always matches (namespace ``""``);
    a package root matches only when the leading segment of *clean_path*
    equals its namespace, or *clean_path* equals the namespace exactly (the
    package's own root directory). Shared by the alias index's
    directory-listing check and ``dct serve``'s directory router, so
    package-name matching is written once instead of copied per caller.
    """
    candidates: list[tuple[BoardRoot, str]] = []
    for root in roots:
        if root.namespace:
            if clean_path == root.namespace:
                rest = ""
            elif clean_path.startswith(f"{root.namespace}/"):
                rest = clean_path[len(root.namespace) + 1 :]
            else:
                continue
        else:
            rest = clean_path
        candidates.append((root, rest))
    return candidates


def board_root_for_relpath(
    relpath: str, roots: Sequence[BoardRoot]
) -> BoardRoot | None:
    """The board root whose subtree contains *relpath*, or None."""
    for root in roots:
        if relpath == root.relpath or relpath.startswith(f"{root.relpath}/"):
            return root
    return None


# The whole-project glob: matches every file at any depth. grep's default scope.
ALL_FILES_GLOB = "**/*"

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def ascii_lower(text: str) -> str:
    """*text* with ASCII letters lowercased and every other character untouched.

    The one case fold ``grep(..., ignore_case=True)`` means on every host, so a
    store that matches in SQL over bytes agrees with one that matches in Python.
    """
    return text.translate(_ASCII_LOWER)


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


class BoardLabel(NamedTuple):
    """What a listing prints for one board file."""

    file_path: str
    #: Slug plus suffix — what ``render_board(path=...)`` takes back.
    board_path: str
    title: str
    notes: str
    #: Lowercased ``tags:`` of the board.
    tags: tuple[str, ...]


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
    - ``grep`` is a substring match per line, case-sensitive unless
      ``ignore_case`` (which folds ASCII letters only, ``ascii_lower``); files
      that cannot be read as text (binary, vanished, over a host size cap) are
      skipped, never raised.
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
    def grep(
        self, term: str, glob: str = ALL_FILES_GLOB, ignore_case: bool = False
    ) -> Iterator[GrepHit]:
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

    @property
    def is_package(self) -> bool:
        """True if this file sits under an installed package's board root.

        A package board is read-only and ships no ``source:`` of its own --
        used by the meta.yml cascade (``compile.parse.meta.find_meta_files``)
        to prepend the host's outermost meta layer without that module
        needing its own runtime import of ``core.project``.
        """
        return is_package_relpath(self._relpath)

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
    def iter_files(
        self,
        under: str,
        *,
        recursive: bool,
        onerror: Callable[[str, OSError], None] | None = None,
    ) -> Iterator[str]:
        """Yield project-relative paths of ALL files under *under*, sorted.

        *under* is a project-relative subtree (``"."`` means the whole project).
        Format-neutral: no suffix filter — callers own that. Yields nothing
        when the subtree does not exist. Paths are yielded in lexicographic
        order; deterministic diagnostics and stable build outputs rely on this.

        ``onerror``, when given, is called with a project-relative path and
        the ``OSError`` for a directory this implementation could not read
        (a real-filesystem host only -- a git-blob-backed host has no such
        failure mode and ignores it).
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
        Cloud deletes the git-blob-store board via the projects service's
        ``delete_board``). Raises ``FileNotFoundError`` if the path does not
        exist — no silent no-op.
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
        self,
        *,
        under: str | None = None,
        recursive: bool = True,
        onerror: Callable[[str, OSError], None] | None = None,
    ) -> Iterator[ProjectPath]:
        """Yield ProjectPath handles for candidate board files under *under*.

        *under* ``None`` (the default) scans every board root — the host
        ``charts/`` plus every installed package's ``charts/`` (see
        ``board_roots``). Pass an explicit *under* (typically ``CHARTS_SUBDIR``)
        to scope the scan to one subtree, e.g. a write path that must stay
        host-only.

        Applies board-suffix (BOARD_CANDIDATE_SUFFIXES) and underscore-prefix
        filters so callers only see plausible board files. Skips traversal-noise
        dirs via ``iter_files``. Yields nothing when a subtree does not exist.

        ``onerror`` is forwarded to ``iter_files``.
        """
        roots = [under] if under is not None else [r.relpath for r in board_roots(self)]
        for root in roots:
            for relpath in self.iter_files(root, recursive=recursive, onerror=onerror):
                if not is_board_candidate(relpath):
                    continue
                yield ProjectPath(self, relpath)

    def board_labels(self) -> list[BoardLabel]:
        """Title and notes of every board file under every board root, by path.

        Parses each board. A host that can answer from stored metadata
        overrides this so a listing costs no per-board read; it must keep this
        contract: every ``iter_boards`` file except ``meta.yml`` fragments,
        markdown boards included, an unparseable or unreadable board named by its slug.
        """
        from dbt_charts.core.compile.parse.board_text import parse_board_text
        from dbt_charts.core.diagnostics.execution import ExecutionError

        labels: list[BoardLabel] = []
        for pf in sorted(self.iter_boards(), key=lambda p: p.relpath):
            if pf.is_meta:
                continue
            slug = board_slug(pf.relpath)
            try:
                content = pf.read_text()
            except (OSError, UnicodeDecodeError, ExecutionError):
                content = ""  # not readable as text: listed by its slug, as Cloud does
            text = parse_board_text(slug, content, is_markdown=pf.is_markdown)
            labels.append(
                BoardLabel(
                    pf.relpath,
                    f"{slug}{PurePosixPath(pf.relpath).suffix}",
                    text.title,
                    text.notes,
                    text.tags,
                )
            )
        return labels

    def path(self, relpath: str) -> ProjectPath:
        """Return a ProjectPath handle for a project-relative path."""
        return ProjectPath(self, relpath)

    def directory(self, relpath: str = ".") -> ProjectDirectory:
        """Return a ProjectDirectory handle. No argument anchors at the project root."""
        return ProjectDirectory(self, relpath)

    def host_charts_directory(self) -> ProjectDirectory:
        """The host's own ``charts/`` directory.

        The outermost meta.yml cascade layer for a package board (see
        ``compile.parse.meta.find_meta_files``): a package ships no
        ``source:`` of its own, so the host names one for it here -- via the
        board's own ``project`` handle, not the caller-supplied walk bound,
        so this is correct regardless of what upper bound the caller passed.
        """
        return self.directory(CHARTS_SUBDIR)

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
