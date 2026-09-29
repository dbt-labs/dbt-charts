"""Shared path helpers for AI tools and CLI render setup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

# tach-ignore(agent_api->cli: runtime host-type guard in resolve_board_or_error / _relpath_for_fs_location, and direct construction in compile_editor_buffer; the local-filesystem check needs a non-cli signal on Project — deferred)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics import (
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    ERR_META_SCHEMA,
    ERR_PROJECT_CONFIG_SCHEMA,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES as BOARD_CANDIDATE_SUFFIXES,
    CHARTS_SUBDIR as CHARTS_SUBDIR,
    META_FILENAMES,
    PROJECT_CONFIG_NAME,
    BoardFile,
    InMemoryBoard,
    Project,
    ProjectPath,
    assert_relpath,
    posix_relpath,
)
from dbt_charts.core.project_roots import (
    DCT_ROOT_MARKERS as DCT_ROOT_MARKERS,
    find_dct_root as find_dct_root,
    find_repo_root as find_repo_root,
    resolve_dbt_project_dir as resolve_dbt_project_dir,
)

if TYPE_CHECKING:
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.diagnostics.registry import ErrorCode


def is_patch_fragment(resolved: ProjectPath) -> bool:
    """True if *resolved* validates as a BoardPatch fragment (meta.yml or a
    private YAML partial) instead of a standalone board.

    Shared by ``agent_api.validate`` and ``compile_editor_buffer``.
    """
    return resolved.is_meta or (resolved.is_private and resolved.is_yaml)


def _pydantic_diagnostics(
    exc: PydanticValidationError, code: ErrorCode, relpath: str
) -> list[Diagnostic]:
    """One ``pydantic.ValidationError`` -> one ``Diagnostic`` per sub-error,
    dot-joining a multi-segment ``loc``. Shared by both fragment kinds below.
    """
    return [
        DbtChartsError.from_code(
            code,
            message=(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                if e["loc"]
                else e["msg"]
            ),
        ).to_diagnostic(file=relpath)
        for e in exc.errors()
    ]


def _patch_diagnostics(patch_data: dict[str, Any], relpath: str) -> list[Diagnostic]:
    """Validate a parsed meta.yml or private-partial dict as a BoardPatch."""
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER

    try:
        BOARD_PATCH_ADAPTER.validate_python(patch_data)
    except PydanticValidationError as exc:
        return _pydantic_diagnostics(exc, ERR_META_SCHEMA, relpath)
    return []


def patch_file_diagnostics(resolved: ProjectPath) -> list[Diagnostic]:
    """Validate *resolved* off disk as a BoardPatch fragment (meta.yml or a
    private ``_``-prefixed partial). Returns ``[]`` on success.

    Used by ``agent_api.validate``, validating the file as saved. See
    ``patch_content_diagnostics`` for the unsaved-editor-buffer counterpart,
    used by ``compile_editor_buffer``.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.parse.meta import load_meta_file

    relpath = resolved.relpath
    try:
        patch_data, _ = load_meta_file(resolved)
    except CompilationError as exc:
        return [
            DbtChartsError.from_code(ERR_META_SCHEMA, message=str(exc)).to_diagnostic(
                file=relpath
            )
        ]
    return _patch_diagnostics(patch_data, relpath)


def patch_content_diagnostics(content: str, relpath: str) -> list[Diagnostic]:
    """Validate unsaved editor-buffer *content* as a BoardPatch fragment -
    the live-text counterpart to ``patch_file_diagnostics``, for the file
    currently open in the editor rather than what is on disk.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.parse.meta import parse_meta_content

    try:
        patch_data, _ = parse_meta_content(content, relpath)
    except CompilationError as exc:
        return [
            DbtChartsError.from_code(ERR_META_SCHEMA, message=str(exc)).to_diagnostic(
                file=relpath
            )
        ]
    return _patch_diagnostics(patch_data, relpath)


def _project_config_diagnostics_for_data(data: Any, relpath: str) -> list[Diagnostic]:
    """Validate already-parsed dbt_charts.yml content via the shared
    non-global schema check. Returns ``[]`` on success.

    ``data`` is `Any`: raw YAML output (a caller-supplied editor buffer or
    disk file), not yet known to be a mapping: the same boundary shape
    ``_as_mapping`` inside ``validate_project_config_data`` type-checks.
    """
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.config import validate_project_config_data
    from dbt_charts.core.compile.errors import CompilationError

    try:
        validate_project_config_data(data, filename=relpath)
    except CompilationError as exc:
        # Already carries its own registered code (e.g.
        # ERR-SOURCE-CREDENTIAL-LITERAL, ERR-SOURCE-CONFIG-INVALID) when
        # raised via `.from_code`: preserve it rather than masking it.
        return [exc.to_diagnostic(file=relpath)]
    except PydanticValidationError as exc:
        # Must be caught before the broader (TypeError, ValueError,
        # RecursionError) clause below: pydantic.ValidationError subclasses
        # ValueError, so this arm is dead code (every schema fault collapses
        # into one opaque str(exc) blob) if it sits after that one.
        return _pydantic_diagnostics(exc, ERR_PROJECT_CONFIG_SCHEMA, relpath)
    except (TypeError, ValueError, RecursionError) as exc:
        # `sources.default` and other project-config type mismatches raise a
        # plain TypeError; a `{{ env_var(...) }}` a source's `path`/`file`
        # references but leaves unset raises ValueError out of the dbt Jinja
        # renderer (render_dbt_jinja_in_dict's documented contract): both run
        # before _validate_source_registry gets a chance to wrap them into a
        # coded CompilationError. A YAML anchor cycle in the project config
        # raises RecursionError out of Config.model_validate itself, before
        # sources: is ever extracted. None of the three has a registered
        # code of its own.
        return [
            DbtChartsError.from_code(
                ERR_PROJECT_CONFIG_SCHEMA, message=str(exc)
            ).to_diagnostic(file=relpath)
        ]
    return []


def project_config_diagnostics(resolved: ProjectPath) -> list[Diagnostic]:
    """Validate *resolved* (``dbt_charts.yml``) off disk through the existing
    project-config loading path. Returns ``[]`` on success. Never compiled
    as a board.

    Used by ``agent_api.validate``. See ``project_config_content_diagnostics``
    for the unsaved-editor-buffer counterpart.
    """
    import yaml

    relpath = resolved.relpath
    try:
        data = resolved.read_yaml()
    except (yaml.YAMLError, OSError) as exc:
        # OSError: mirrors load_meta_file's disk-read peer (parse/meta.py) -
        # an existing-but-unreadable file (permissions, vanished mid-read)
        # is a diagnostic, not an unhandled traceback.
        return [
            DbtChartsError.from_code(
                ERR_PROJECT_CONFIG_SCHEMA, message=f"Failed to parse {relpath}: {exc}"
            ).to_diagnostic(file=relpath)
        ]
    return _project_config_diagnostics_for_data(data, relpath)


def project_config_content_diagnostics(content: str, relpath: str) -> list[Diagnostic]:
    """Validate unsaved editor-buffer *content* as dbt_charts.yml: the
    live-text counterpart to ``project_config_diagnostics``.
    """
    import yaml

    from dbt_charts.core.utils import UniqueKeyLoader

    try:
        data = yaml.load(content, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        return [
            DbtChartsError.from_code(
                ERR_PROJECT_CONFIG_SCHEMA, message=f"Failed to parse {relpath}: {exc}"
            ).to_diagnostic(file=relpath)
        ]
    return _project_config_diagnostics_for_data(data, relpath)


def iter_expanded_board_files(project: Project, under: str) -> list[ProjectPath]:
    """Board files under ``under``, sorted — the shared directory-walk filter.

    Excludes private (leading-underscore) files, ``meta.yml``/``meta.yaml``
    cascade fragments, and inspect-manifest-owned directories: none of these
    are standalone boards. Shared by ``validate_paths`` and ``describe_paths``
    so the two verbs' expansion can't drift out of sync again.
    """
    # WHY: dbt_charts.core.inspect.manifest_utils triggers the inspect package
    # __init__, which eagerly imports TableInspector + grain/quality/semantic
    # detectors. Keep this lazy so `dct --help` doesn't pay that startup cost.
    from dbt_charts.core.inspect.manifest_utils import INSPECT_TEMPLATE_MANIFEST

    return sorted(
        pf
        for pf in project.iter_boards(under=under, recursive=True)
        if pf.is_yaml
        and not pf.is_private
        and not pf.is_meta
        and not (pf.parent / INSPECT_TEMPLATE_MANIFEST).exists()
    )


def partition_defaults_files(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split argv-supplied paths into real boards and meta.yml/meta.yaml defaults files.

    A shell glob (``charts/*.yml``) hands every YAML in the directory to a verb
    like ``render`` — including the ``meta.yml`` ``dct init`` scaffolds to hold
    directory-wide cascade defaults. That file is cascade input, never a
    standalone board (the same ``META_FILENAMES`` check ``iter_expanded_board_files``
    already applies during directory expansion), so a caller iterating argv
    paths one at a time needs the same split before treating each as a board.
    Order is preserved within each returned list.
    """
    boards = [p for p in paths if p.name not in META_FILENAMES]
    defaults_files = [p for p in paths if p.name in META_FILENAMES]
    return boards, defaults_files


def defaults_file_skip_diagnostics(paths: list[Path]) -> list[Diagnostic]:
    """Build a WARN-DEFAULTS-FILE-GIVEN-AS-BOARD Diagnostic per skipped path."""
    from dbt_charts.core.diagnostics.codes_compile import (
        WARN_DEFAULTS_FILE_GIVEN_AS_BOARD,
    )

    return [
        Diagnostic.from_code(
            WARN_DEFAULTS_FILE_GIVEN_AS_BOARD,
            message=WARN_DEFAULTS_FILE_GIVEN_AS_BOARD.message_template.format(
                path=str(p)
            ),
            fix=WARN_DEFAULTS_FILE_GIVEN_AS_BOARD.fix_template,
            path=str(p),
        )
        for p in paths
    ]


def nothing_to_render_diagnostic() -> Diagnostic:
    """Build the ERR-NOTHING-TO-RENDER Diagnostic for an all-defaults-files argv."""
    from dbt_charts.core.diagnostics.codes_compile import ERR_NOTHING_TO_RENDER

    return DbtChartsError.from_code(ERR_NOTHING_TO_RENDER).to_diagnostic()


def resolve_board_relpath(relpath: PurePosixPath, project: Project) -> ProjectPath:
    """Project-relative board identity -> ProjectPath, with the boards-first retry.

    The relpath-native counterpart to ``resolve_board_path``: for callers that
    already hold a project-relative board identity (Cloud's context strings)
    rather than a real filesystem path, this is the single owner of the
    board-relative vs root-relative reconciliation — a bare name like
    ``looker/x.yaml`` retries under ``CHARTS_SUBDIR`` when it doesn't exist at
    root, without producing a ``charts/charts/...`` double prefix when the
    caller already supplies the full ``charts/looker/x.yaml``.

    Existence is checked via ``project.exists(...)`` so it works for all
    ``Project`` implementations (FilesystemProject, CloudManagedProject
    git-blob store, etc.) — not just the local filesystem. Paths that exist
    at root resolve from root — non-boards paths like ``models/schema.sql``
    are not rewritten under charts/. ``str`` is used only at the identity seam
    (``project.path`` / ``project.exists``); everything else is real
    ``PurePosixPath`` operations.
    """
    relpath_str = str(relpath)
    assert_relpath(relpath_str)
    if project.exists(relpath_str):
        return project.path(relpath_str)
    if not relpath.parts or relpath.parts[0] != CHARTS_SUBDIR:
        candidate = PurePosixPath(CHARTS_SUBDIR) / relpath
        candidate_str = str(candidate)
        if project.exists(candidate_str):
            return project.path(candidate_str)
    return project.path(relpath_str)  # caller reports not-found


def _relpath_for_fs_location(path: Path, project: Project) -> str:
    """Relativize an absolute, on-disk board location against a local project root.

    Absolute and ``..``-leading board paths are explicit filesystem locations —
    the one place this boundary genuinely needs a real ``Path`` op. Only
    ``FilesystemProject`` ever receives them: Cloud/MCP always supply
    project-relative identity strings, never a real filesystem path, so any
    other ``Project`` implementation here is a caller bug, not a store to
    fall back through.

    Two-try containment check (mirrors the pre-refactor behavior): ``resolve()``
    follows symlinks (handles e.g. ``/var`` -> ``/private/var`` on macOS); a
    symlink-blind ``normpath`` fallback accepts paths through intentional
    project-internal symlinks (e.g. ``charts/tasks/`` -> ``../../tasks/``).
    Raises ``ValueError`` if neither check places *path* inside the root.
    """
    if not isinstance(project, FilesystemProject):
        raise ValueError(
            "Absolute or '..'-relative board paths require a local filesystem "
            f"project; got {type(project).__name__}."
        )
    root = project.root
    resolved = path.resolve()
    try:
        return posix_relpath(resolved, root)
    except ValueError:
        pass
    normalized = Path(os.path.normpath(path.absolute()))
    try:
        return posix_relpath(normalized, root)
    except ValueError:
        raise ValueError(f"Path {path!r} is outside project root {root!r}") from None


def resolve_board_path(path: Path, project: Project) -> ProjectPath:
    """User/agent-supplied filesystem-shaped board path -> ProjectPath.

    Absolute and ``..``-leading paths are explicit locations: they relativize
    against a concrete ``FilesystemProject`` root (``_relpath_for_fs_location``,
    a leading ``..`` resolving against cwd for CLI shell-nav feel) and are
    returned as-is — no ``charts/`` retry, even when the exact path doesn't
    exist. A bare relative name (no explicit location) delegates to
    ``resolve_board_relpath`` for the boards-first retry, which works against
    any ``Project`` implementation.
    """
    if path.is_absolute():
        relpath = _relpath_for_fs_location(path, project)
    elif ".." in path.parts:
        relpath = _relpath_for_fs_location(
            Path(os.path.normpath(Path.cwd() / path)), project
        )
    else:
        return resolve_board_relpath(PurePosixPath(path), project)
    return project.path(relpath)


def resolve_board_or_error(path: Path, project: Project) -> BoardFile | Diagnostic:
    """Boards-first resolve a path to a stored ``BoardFile``, or a structured error.

    The one agent_api seam turning a user-supplied path into the compiler's
    located input — shared by the CLI render verb and the AI render tool so the
    boards-first retry and the ``ERR-FILE-NOT-FOUND`` envelope never drift
    between surfaces. Returns a ``Diagnostic`` for an escaping path
    (``ERR-INTERNAL``) or a missing board (``ERR-FILE-NOT-FOUND``).
    """
    try:
        resolved = resolve_board_path(path, project)
    except ValueError as exc:
        return DbtChartsError.from_code(ERR_INTERNAL, message=str(exc)).to_diagnostic()
    if not resolved.exists():
        return DbtChartsError.from_code(
            ERR_FILE_NOT_FOUND,
            path=resolved.relpath,
        ).to_diagnostic(file=resolved.relpath)
    if (
        isinstance(project, FilesystemProject)
        and not (project.root / resolved.relpath).is_file()
    ):
        # A directory (or other non-file) resolves + exists on disk but read_board
        # would raise past the structured envelope. This is a filesystem-only
        # concern — a non-filesystem store's `exists()` already gates real boards.
        return DbtChartsError.from_code(
            ERR_INTERNAL, message=f"Not a file: {resolved.relpath}"
        ).to_diagnostic(file=resolved.relpath)
    return resolved.read_board()


@dataclass(frozen=True)
class BoardRenderContext:
    """Path resolution result from a board path + project root.

    Adapter registry is built by `ProjectSession.open`, not by the context — call sites
    open a `ProjectSession` with `project_root` and read the registry off
    `project.adapter_registry`.
    """

    board_file: Path
    scoped_path: Path
    scoped_base: Path
    project_root: Path
    output_dir: Path


def build_board_render_context(
    board_path: Path,
    project_dir: Path,
) -> BoardRenderContext:
    """Resolve a board path against the given project root.

    ``project_dir`` is authoritative. Callers must resolve their project dir first
    (e.g. via ``resolve_project_dir(raw_dir)`` at the CLI boundary).
    """
    if board_path.is_absolute():
        board_file = board_path.resolve()
    elif ".." in board_path.parts:
        board_file = (Path.cwd() / board_path).resolve()
    else:
        board_file = (project_dir / board_path).resolve()

    project_root = project_dir

    try:
        scoped_path: Path = board_file.relative_to(project_root)
    except ValueError:
        raise ValueError(
            f"Board file {board_file} is outside project_dir {project_root}. "
            f"Pass an explicit --project-dir that contains the board file."
        ) from None

    return BoardRenderContext(
        board_file=board_file,
        scoped_path=scoped_path,
        scoped_base=project_root,
        project_root=project_root,
        output_dir=project_root,
    )


@dataclass(frozen=True)
class YamlRenderContext:
    """Path resolution result for rendering inline YAML against a project root.

    Adapter registry is built by `ProjectSession.open`, not by the context.
    """

    project_root: Path
    output_dir: Path


def build_yaml_render_context(
    project_dir: Path,
) -> YamlRenderContext:
    """Resolve the project root for rendering inline YAML.

    ``project_dir`` is authoritative. Callers must resolve their project dir
    first (e.g. via ``resolve_project_dir(raw_dir)`` at the CLI boundary).
    """
    project_root = project_dir.resolve()
    return YamlRenderContext(
        project_root=project_root,
        output_dir=project_root,
    )


@dataclass(frozen=True)
class EditorCompileResult:
    """Result of `compile_editor_buffer`.

    `own_file` is whatever identity string the compile was stamped with —
    `board_path.relpath` on the cascade path, `str(file)` on every fallback
    branch — the value a `Diagnostic`'s `range.file` equals for a finding on
    *this* buffer. A cascade merges other project files (`charts/meta.yml`,
    `extends` targets) into the same compile, and a finding in one of those
    carries that file's own `range.file` instead — the caller compares
    against `own_file` to attribute a foreign-origin finding to the file that
    actually has the problem, rather than squiggling it onto an unrelated
    line of this buffer.
    """

    result: CompileResult
    own_file: str
    config_error: str | None = None


def compile_editor_buffer(content: str, file: Path) -> EditorCompileResult:
    """Compile editor buffer text, applying the meta.yml cascade when ``file``
    sits inside a project.

    Resolves a `Project` from `file`'s ancestry, the same "does a project
    exist here" check the CLI uses (`find_dct_root`), so an editor buffer
    compiles under the same cascade `dct validate`/`dct render` would apply.

    Every fallback branch below drops to the non-cascade `compile()` with no
    `project_sources` — so alongside skipping the folder `meta.yml` cascade
    (`source:`, `extends:`, lint config), a SQL query's dialect is unknown and
    compile stays silent about SQL syntax rather than guessing one (see
    `_sql_parse_warnings`).

    `EditorCompileResult.config_error` is an optional config-load-error
    message for the caller to surface as a *separate* diagnostic — folding a
    broken `dbt_charts.yml` into the compile result would replace every real
    finding on the board with one line-1 error blaming the wrong document.
    """
    # Lazy: core.compile is the heavy stack agent_api's PEP 562 laziness
    # exists to keep out of `import dbt_charts.cli.main` — this module is
    # imported eagerly by cli/_project.py and cli/_workspace_guard.py, so a
    # top-level import here would defeat that (test_lazy_imports.py).
    from dbt_charts.core.compile.compiler import (
        CompileResult,
        compile as dct_compile,
        compile_file,
    )

    own_file = str(file)
    root = find_dct_root(file.parent)
    if root is None:
        return EditorCompileResult(dct_compile(content, file=own_file), own_file)

    project = FilesystemProject(root)
    try:
        board_path = project.path_for_fspath(file)
    except ValueError:
        return EditorCompileResult(
            dct_compile(content, file=own_file),
            own_file,
            config_error=(
                f"{file} is outside project root {root}; folder meta.yml "
                "defaults were not applied"
            ),
        )

    # dbt_charts.yml and a `_`-prefixed private YAML partial are never
    # boards: compiling either through compile_file() misapplies the board
    # schema. Neither needs project.sources/cache loaded below,
    # and both validate the live buffer text, not what is on disk, matching
    # every other branch of this function. Same dispatch as
    # agent_api.validate._validate_resolved.
    if board_path.relpath == PROJECT_CONFIG_NAME:
        errors = project_config_content_diagnostics(content, board_path.relpath)
        return EditorCompileResult(CompileResult(errors=errors), board_path.relpath)
    if is_patch_fragment(board_path):
        errors = patch_content_diagnostics(content, board_path.relpath)
        return EditorCompileResult(CompileResult(errors=errors), board_path.relpath)

    try:
        _ = project.sources
        _ = project.cache
    except Exception as exc:  # noqa: BLE001 — attributed below, not swallowed
        return EditorCompileResult(
            dct_compile(content, file=own_file),
            own_file,
            config_error=f"Project dbt_charts.yml could not be loaded: {exc}",
        )

    result = compile_file(InMemoryBoard(content, path=board_path))
    return EditorCompileResult(result, board_path.relpath)
