"""Regression for Issue 6: path helpers walk up to find the project root.

dct serve has always done this. dct validate/render/query did not.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from dbt_charts.agent_api._paths import (
    partition_defaults_files,
    resolve_board_path,
    resolve_board_relpath,
    resolve_dbt_project_dir,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.inspect.cache_factory import build_resolver
from dbt_charts.core.project import Project

_VALID_BOARD = """
queries:
  revenue:
    sql: SELECT 1 AS n
    source: analytics
charts:
  rev:
    query: revenue
    type: bar
    x: n
    y: n
rows:
  - rev
"""


def test_resolve_board_path_dotdot_resolves_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a `..`-leading path resolves against cwd (shell-nav feel),
    then relativizes against the project root — no longer 'escapes base directory'."""
    project = tmp_path / "myproject"
    (project / "models").mkdir(parents=True)
    (project / "charts").mkdir()
    (project / "dbt_charts.yml").write_text("# project marker\n")
    (project / "charts" / "hello.yml").write_text(_VALID_BOARD)
    monkeypatch.chdir(project / "models")  # cwd is models/; board is ../charts/

    resolved = resolve_board_path(
        Path("../charts/hello.yml"), FilesystemProject(project.resolve())
    )
    assert resolved.relpath == "charts/hello.yml"


def test_resolve_board_path_bare_relative_already_prefixed(tmp_path: Path) -> None:
    """A bare relative path already under charts/ resolves as-is — no rewrite needed."""
    project = tmp_path / "myproject"
    project.mkdir()

    resolved = resolve_board_path(Path("charts/hello.yml"), FilesystemProject(project))
    assert resolved.relpath == "charts/hello.yml"


def test_resolve_board_path_absolute_path_relativizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute path inside the project relativizes to its project-relative string."""
    monkeypatch.chdir(tmp_path)
    board = tmp_path / "hello.yml"
    board.write_text(_VALID_BOARD)

    resolved = resolve_board_path(board, FilesystemProject(tmp_path))
    assert resolved.relpath == "hello.yml"


def test_resolve_board_path_absolute_within_project_dir_accepted(
    tmp_path: Path,
) -> None:
    """Absolute path inside project_dir is accepted (containment check passes)."""
    project = tmp_path / "myproject"
    (project / "charts").mkdir(parents=True)
    board = project / "charts" / "hello.yml"
    board.write_text(_VALID_BOARD)

    resolved = resolve_board_path(board, FilesystemProject(project))
    assert resolved.relpath == "charts/hello.yml"


def test_resolve_board_path_absolute_outside_project_dir_raises(
    tmp_path: Path,
) -> None:
    """Absolute path outside project_dir raises ValueError — the absolute-escape
    regression: caught here at the agent_api boundary, never inside the core
    resolver (which only ever sees a relpath)."""
    project = tmp_path / "myproject"
    project.mkdir()
    outside = tmp_path / "other" / "sneaky.yml"

    with pytest.raises(ValueError, match="outside project root"):
        resolve_board_path(outside, FilesystemProject(project))


def test_resolve_board_path_absolute_through_project_symlink_accepted(
    tmp_path: Path,
) -> None:
    """An absolute path through an intentional symlink under charts/ (which
    resolves outside the project root on disk) is still accepted — preserves
    the pre-refactor symlink-acceptance behavior via `path_for_fspath`'s
    resolve()-then-normpath fallback."""
    project = tmp_path / "myproject"
    (project / "charts").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "linked.yml").write_text(_VALID_BOARD)
    (project / "charts" / "linked").symlink_to(external, target_is_directory=True)

    board_through_symlink = project / "charts" / "linked" / "linked.yml"
    resolved = resolve_board_path(board_through_symlink, FilesystemProject(project))
    assert resolved.relpath == "charts/linked/linked.yml"


# ---------------------------------------------------------------------------
# resolve_board_path: explicit (absolute / ..) paths must NOT get the
# boards-first retry — only bare relative names do (reviewer-flagged bug).
# ---------------------------------------------------------------------------


def test_resolve_board_path_absolute_nonexistent_does_not_retry_under_boards(
    tmp_path: Path,
) -> None:
    """`dct render /abs/proj/looker/x.yaml` where that exact file is ABSENT but
    `charts/looker/x.yaml` EXISTS must resolve to the absent exact path, not
    silently rewrite to the charts/ counterpart and render the wrong file."""
    project = tmp_path / "myproject"
    boards = project / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)

    absolute_missing = project / "looker" / "x.yaml"  # does not exist

    resolved = resolve_board_path(absolute_missing, FilesystemProject(project))

    assert resolved.relpath == "looker/x.yaml"
    assert not resolved.exists()


def test_resolve_board_path_dotdot_nonexistent_does_not_retry_under_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same bug, `..`-path form: `../looker/x.yaml` from a project subdir must not
    be rewritten to charts/looker/x.yaml just because that counterpart exists."""
    project = tmp_path / "myproject"
    boards = project / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)
    (project / "models").mkdir()
    monkeypatch.chdir(project / "models")  # cwd is models/; ../looker/x.yaml

    resolved = resolve_board_path(Path("../looker/x.yaml"), FilesystemProject(project))

    assert resolved.relpath == "looker/x.yaml"
    assert not resolved.exists()


def test_resolve_board_path_bare_relative_name_still_retries_under_boards(
    tmp_path: Path,
) -> None:
    """A bare relative name (no explicit `charts/` prefix, no `..`, not absolute)
    must still get the boards-first retry — this is the good behavior the bug
    fix must preserve."""
    project = tmp_path / "myproject"
    boards = project / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)

    resolved = resolve_board_path(Path("looker/x.yaml"), FilesystemProject(project))

    assert resolved.relpath == "charts/looker/x.yaml"
    assert resolved.exists()


def test_render_command_passes_path_types_to_render_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_command must build a real BoardFile (not a bare str) for render_dashboard.

    Regression: the pre-PR shape set scoped_base = str(project_root); the new
    shape threads a real BoardFile (project-relative ProjectPath + content), never
    a stringly-typed path.
    """
    from dbt_charts.cli.commands.render import render_command
    from dbt_charts.core.project import BoardFile

    project = tmp_path / "myproject"
    (project / "charts").mkdir(parents=True)
    (project / "dbt_charts.yml").write_text("# project marker\n")
    board = project / "charts" / "hello.yml"
    board.write_text(
        "title: t\nqueries:\n  q:\n    columns: [n]\n    values:\n      - [1]\n"
        "charts:\n  c:\n    query: q\n    type: kpi\n    value: n\nrows:\n  - c\n"
    )

    captured: dict[str, Any] = {}

    import dbt_charts.core.board as _core_dash_mod

    real_render = _core_dash_mod.render_dashboard

    def spy_render(**kwargs):
        captured["board"] = kwargs.get("board")
        captured["project"] = kwargs.get("project")
        return real_render(**kwargs)

    # Patch at the canonical home; ProjectSession.render_board delegates via _core_dashboard.
    monkeypatch.setattr(_core_dash_mod, "render_dashboard", spy_render)

    render_command(board, format="json", project_dir=project)

    assert isinstance(captured.get("board"), BoardFile), (
        f"board should be BoardFile, got {type(captured.get('board'))}"
    )
    assert captured["board"].path.relpath == "charts/hello.yml"

    assert isinstance(captured.get("project"), Project), (
        f"project should be Project, got {type(captured.get('project'))}"
    )


def test_build_board_render_context_outside_project_root_raises(
    tmp_path: Path,
) -> None:
    """Board outside project_dir raises ValueError with a clear message.

    Callers must supply a project_dir that contains the board file — there is no
    silent fallback to an absolute scoped_path (which would later fail inside
    render_dashboard's resolve_scoped_path containment check).
    """
    from dbt_charts.agent_api._paths import build_board_render_context

    project = tmp_path / "myproject"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("# project marker\n")

    # Board lives OUTSIDE the project directory
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    board = outside_dir / "external.yml"
    board.write_text(_VALID_BOARD)

    with pytest.raises(ValueError, match="is outside project_dir"):
        build_board_render_context(board, project)


def test_build_board_render_context_absolute_outside_cwd_project_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Board outside cwd-walked project raises ValueError, not a silent fallback.

    The CLI passes resolve_project_dir(None) (cwd walk); if the board lives
    outside that project, a clear error is raised so the user knows to add
    --project-dir.
    """
    from dbt_charts.agent_api._paths import build_board_render_context

    cwd_project = tmp_path / "cwd_project"
    cwd_project.mkdir()
    (cwd_project / "dbt_charts.yml").write_text("# cwd-walked project marker\n")

    external_dir = tmp_path / "external"
    external_dir.mkdir()
    board = external_dir / "board.yml"
    board.write_text(_VALID_BOARD)

    monkeypatch.chdir(cwd_project)
    with pytest.raises(ValueError, match="is outside project_dir"):
        build_board_render_context(board, cwd_project.resolve())


def test_build_resolver_accepts_path_cache(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """build_resolver must accept cache_path: Path | None (PR 2 migration)."""
    from dbt_charts.core.execute.adapters import build_adapter_registry

    project = tmp_path / "myproject"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("# marker\n")
    registry = build_adapter_registry(local_project(project), read_only=True)
    cache = tmp_path / "cache.json"

    # Path argument must be accepted without raising TypeError
    resolver = build_resolver(registry, cache)
    assert resolver is not None


# ---------------------------------------------------------------------------
# resolve_board_path: boards-first fallback edge cases (merged from the former
# core-level resolver, folded into the single agent_api resolve_board_path)
# ---------------------------------------------------------------------------


def test_resolve_board_path_explicit_boards_prefix_no_double_prefix(
    tmp_path: Path,
) -> None:
    """Explicit 'charts/looker/x.yaml' resolves directly — no charts/charts/... double prefix."""
    project = tmp_path / "myproject"
    boards = project / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)

    resolved = resolve_board_path(
        Path("charts/looker/x.yaml"), FilesystemProject(project)
    )
    assert resolved.relpath == "charts/looker/x.yaml"


def test_resolve_board_path_root_file_resolves_from_root(tmp_path: Path) -> None:
    """Non-boards path that exists at root resolves from root, not rewritten under charts/."""
    project = tmp_path / "myproject"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "charts").mkdir()
    (models / "schema.sql").write_text("SELECT 1\n")

    resolved = resolve_board_path(Path("models/schema.sql"), FilesystemProject(project))
    assert resolved.relpath == "models/schema.sql"


def test_resolve_board_path_nonexistent_returns_relpath_unchanged(
    tmp_path: Path,
) -> None:
    """Path absent at root and under charts/ returns the relpath as-given (caller handles not-found)."""
    project = tmp_path / "myproject"
    (project / "charts").mkdir(parents=True)

    resolved = resolve_board_path(Path("missing.yaml"), FilesystemProject(project))
    assert resolved.relpath == "missing.yaml"


def test_resolve_board_path_absolute_rejects_non_filesystem_project(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """Absolute paths are an explicit on-disk location — only a real
    ``FilesystemProject`` can relativize them. Resolving one against a
    non-filesystem store (Cloud's git-blob project) must raise, not silently
    succeed via the store's inert ``root`` (a real filesystem Path that
    ``InMemoryProject``/``CloudManagedProject`` never actually read from).

    Regression for D1: the boundary normalization must go through a concrete
    ``FilesystemProject``, never the abstract ``Project.root``/
    ``path_for_fspath`` reach-through.
    """
    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": "title: x\n"}
    )
    absolute_board = tmp_path / "charts" / "looker" / "x.yaml"

    with pytest.raises(ValueError, match="filesystem"):
        resolve_board_path(absolute_board, project)


def test_resolve_board_path_dotdot_rejects_non_filesystem_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """Same as above for the `..`-leading shape: a `..` path resolves against
    cwd, an equally filesystem-only operation, and must raise for a
    non-filesystem project rather than resolving via a coincidental root."""
    (tmp_path / "models").mkdir()
    monkeypatch.chdir(tmp_path / "models")
    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": "title: x\n"}
    )

    with pytest.raises(ValueError, match="filesystem"):
        resolve_board_path(Path("../charts/looker/x.yaml"), project)


def test_resolve_board_or_error_absolute_non_filesystem_project_is_enveloped(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """The rejection from `_relpath_for_fs_location` must reach callers as a
    structured Diagnostic, not an uncaught ValueError — `resolve_board_or_error`
    is the shared CLI-render / AI-render seam and both surfaces expect a
    `Diagnostic` on failure, never a raise past the --json / tool-call envelope."""
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/x.yaml": "title: x\n"}
    )
    absolute_board = tmp_path / "charts" / "x.yaml"

    result = resolve_board_or_error(absolute_board, project)

    assert isinstance(result, Diagnostic)
    assert result.code == "ERR-INTERNAL"


def test_resolve_board_path_non_filesystem_store_fallback(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """Boards fallback fires for non-filesystem stores (Cloud git-blob repro).

    resolve_board_path must use `project.exists(...)` (never a raw Path check)
    so it works under any Project implementation.
    """
    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": "title: x\n"}
    )

    resolved = resolve_board_path(Path("looker/x.yaml"), project)
    assert resolved.relpath == "charts/looker/x.yaml"


# ---------------------------------------------------------------------------
# resolve_board_relpath — the PurePosixPath-native entry, used directly by
# string/relpath callers (Cloud) that never hold a real filesystem Path.
# ---------------------------------------------------------------------------


@pytest.mark.windows
def test_resolve_board_relpath_bare_name_retries_under_boards(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A bare board-relative name gets the boards-first retry, same as resolve_board_path."""
    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)

    resolved = resolve_board_relpath(
        PurePosixPath("looker/x.yaml"), local_project(tmp_path)
    )
    assert resolved.relpath == "charts/looker/x.yaml"


def test_resolve_board_relpath_already_root_relative_no_double_prefix(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """An already root-relative 'charts/...' path resolves as-is — no charts/charts/... prefix."""
    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_VALID_BOARD)

    resolved = resolve_board_relpath(
        PurePosixPath("charts/looker/x.yaml"), local_project(tmp_path)
    )
    assert resolved.relpath == "charts/looker/x.yaml"


def test_resolve_board_relpath_non_boards_path_resolved_from_root(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A non-boards relpath that exists at root resolves from root, unchanged."""
    models = tmp_path / "models"
    models.mkdir(parents=True)
    (tmp_path / "charts").mkdir()
    (models / "schema.sql").write_text("SELECT 1\n")

    resolved = resolve_board_relpath(
        PurePosixPath("models/schema.sql"), local_project(tmp_path)
    )
    assert resolved.relpath == "models/schema.sql"


def test_resolve_board_relpath_non_filesystem_store_fallback(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """Boards fallback fires for non-filesystem stores too — the Cloud repro."""
    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": "title: x\n"}
    )

    resolved = resolve_board_relpath(PurePosixPath("looker/x.yaml"), project)
    assert resolved.relpath == "charts/looker/x.yaml"


@pytest.mark.windows
def test_resolve_board_relpath_escaping_relpath_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """An escaping relpath is rejected via assert_relpath — never silently accepted."""
    with pytest.raises(ValueError, match="escape"):
        resolve_board_relpath(
            PurePosixPath("../../etc/passwd"), local_project(tmp_path)
        )


def test_resolve_board_relpath_empty_relpath_does_not_crash(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """A '.'/empty relpath (``PurePosixPath('').parts == ()``) falls through to an
    inert not-found ``ProjectPath`` — same as the old string implementation's
    ``project.path(".")`` — rather than crashing on ``relpath.parts[0]``.

    Reachable from Cloud: ``_same_board`` builds a ``PurePosixPath`` straight
    from a context string with no non-empty guard, so a client-supplied
    ``current_file_path: "."`` must not raise past ``_same_board``'s
    ``except ValueError``.
    """
    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": "title: x\n"}
    )

    resolved = resolve_board_relpath(PurePosixPath(), project)
    assert resolved.relpath == "."

    resolved_dot = resolve_board_relpath(PurePosixPath(), project)
    assert resolved_dot.relpath == "."


# ---------------------------------------------------------------------------
# assert_relpath — the containment guard resolve_board_path wraps
# ---------------------------------------------------------------------------


def test_assert_relpath_requires_ref_arg() -> None:
    from dbt_charts.core.project import assert_relpath

    with pytest.raises(TypeError):
        assert_relpath()  # type: ignore[call-arg]


@pytest.mark.windows
def test_assert_relpath_accepts_relative_path() -> None:
    from dbt_charts.core.project import assert_relpath

    assert_relpath("charts/hello.yml")


@pytest.mark.windows
def test_assert_relpath_rejects_absolute() -> None:
    from dbt_charts.core.project import assert_relpath

    with pytest.raises(ValueError, match="relative"):
        assert_relpath("/etc/passwd")


@pytest.mark.windows
@pytest.mark.parametrize(
    "ref",
    [
        "C:/Windows/System32/drivers/etc/hosts",  # drive-absolute, forward slash
        "C:\\Windows\\hosts",  # drive-absolute, backslash
        "\\\\host\\share",  # UNC
        "\\Windows\\System32\\drivers\\etc\\hosts",  # driveless-rooted (is_absolute() is False)
        "\\etc\\passwd",
        "foo\\..\\..\\..\\etc\\passwd",  # relative escape via backslash separators
    ],
)
def test_assert_relpath_rejects_windows_paths(ref: str) -> None:
    # These are POSIX-relative but escape root on Windows, where the OS treats
    # backslash as a separator and ``root / ref`` discards ``root`` for a
    # rooted/drived ref — the guard must reject them regardless of host OS.
    from dbt_charts.core.project import assert_relpath

    with pytest.raises(ValueError, match="relative"):
        assert_relpath(ref)


@pytest.mark.windows
def test_assert_relpath_rejects_escape() -> None:
    from dbt_charts.core.project import assert_relpath

    with pytest.raises(ValueError, match="escape"):
        assert_relpath("../../etc/passwd")


# ---------------------------------------------------------------------------
# render_board boards-first path resolution (regression: prod repro)
# ---------------------------------------------------------------------------

_INLINE_BOARD = """\
title: Sales Report
notes: sales metrics for testing
queries:
  q:
    columns: [n]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: n
rows:
  - c
"""


def test_render_board_boards_first_stripped_path(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """resolve_board_or_error('looker/x.yaml') resolves charts/looker/x.yaml — prod repro.

    search_boards returns board_path='looker/x.yaml' (charts/ prefix stripped).
    Before this fix, passing that path to render_board caused
    'ERR-INPUT-INVALID File not found: looker/x.yaml'. The charts/-first
    retry lives in resolve_board_or_error — the shared agent_api seam every
    surface (AI tool, CLI) now resolves a user path through before building a
    BoardFile; core.board.render_dashboard itself takes an already-located
    BoardFile and does no path-string resolution.
    """
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_INLINE_BOARD)
    project = local_project(tmp_path)

    board = resolve_board_or_error(
        Path("looker/x.yaml"), project
    )  # stripped board_path
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", result.validation_errors


def test_render_board_explicit_boards_prefix_still_works(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """resolve_board_or_error('charts/looker/x.yaml') still works — no double charts/charts/... prefix."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_INLINE_BOARD)
    project = local_project(tmp_path)

    board = resolve_board_or_error(Path("charts/looker/x.yaml"), project)
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", result.validation_errors


def test_render_board_absolute_board_path_still_works(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """resolve_board_or_error(<absolute path>) still resolves — the AI render
    tool and CLI render verb both reach a board through this seam, and an
    absolute path is the CLI's native shape (`dct render /abs/proj/x.yaml`)."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    board_file = boards / "x.yaml"
    board_file.write_text(_INLINE_BOARD)
    project = local_project(tmp_path)

    board = resolve_board_or_error(board_file, project)
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", result.validation_errors


def test_render_board_dotdot_board_path_still_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """resolve_board_or_error('../charts/x.yaml') still resolves — the `..`-leading
    shell-nav shape CLI render supports from a project subdir."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    boards = tmp_path / "charts" / "looker"
    boards.mkdir(parents=True)
    (boards / "x.yaml").write_text(_INLINE_BOARD)
    (tmp_path / "models").mkdir()
    monkeypatch.chdir(tmp_path / "models")
    project = local_project(tmp_path)

    board = resolve_board_or_error(Path("../charts/looker/x.yaml"), project)
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", result.validation_errors


def test_resolve_board_or_error_directory_returns_structured_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A directory path resolves + exists but isn't a board — resolve_board_or_error
    returns a structured error instead of crashing past the --json envelope
    (regression: `dct render <dir>` / MCP render of a directory)."""
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    (tmp_path / "charts").mkdir()
    project = local_project(tmp_path)

    result = resolve_board_or_error(Path("charts"), project)
    assert isinstance(result, Diagnostic)
    assert result.code == "ERR-INTERNAL"


def test_search_board_path_renders_as_link(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """search_boards board_path resolves via resolve_board_or_error without error.

    Regression: before the fix, every render after a search failed with
    'File not found: <stripped-path>'.
    """
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.agent_api.search import search_boards
    from dbt_charts.core.diagnostics import Diagnostic

    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "sales.yaml").write_text(_INLINE_BOARD)
    project = local_project(tmp_path)

    results = search_boards(query="sales", project=project)
    assert results.results, "search must find the board"

    board_path = results.results[0].board_path  # "sales.yaml" — charts/ prefix stripped

    board = resolve_board_or_error(Path(board_path), project)
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", f"render failed with: {result.validation_errors}"


def test_render_board_boards_first_non_filesystem_project(
    tmp_path: Path,
    in_memory_project: Callable[[Path, dict[str, str]], object],
) -> None:
    """Cloud / non-filesystem repro: stripped path resolves via Project, not raw Path.exists().

    CloudManagedProject never writes to disk — raw Path.exists() returns False
    for all candidates. Without routing the fallback through project.path_for_fspath,
    every resolve_board_or_error call after search_boards returns
    'ERR-FILE-NOT-FOUND: looker/x.yaml'.
    """
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    project: Project = in_memory_project(  # type: ignore[assignment]
        tmp_path, {"charts/looker/x.yaml": _INLINE_BOARD}
    )

    board = resolve_board_or_error(
        Path("looker/x.yaml"), project
    )  # stripped board_path
    assert not isinstance(board, Diagnostic), board

    result = ProjectSession(project=project).render_board(
        board=board, as_link=True, server_port=8000
    )
    assert result.status == "ok", result.validation_errors


def test_resolve_dbt_project_dir_importable_from_agent_api(tmp_path: Path) -> None:
    """`cli/_project.py` must import this from `agent_api._paths`, never from
    `core.project_roots` directly (module-boundary rule)."""
    assert resolve_dbt_project_dir(tmp_path, None) == tmp_path


def test_partition_defaults_files_splits_meta_yml_from_real_boards() -> None:
    """A shell glob (`charts/*.yml`) hands `dct render` every YAML in the
    directory, including the meta.yml `dct init` scaffolds to hold cascade
    defaults. That file isn't a board."""
    boards, defaults_files = partition_defaults_files(
        [
            Path("charts/rev.yml"),
            Path("charts/meta.yml"),
            Path("charts/sub/meta.yaml"),
            Path("charts/other.yaml"),
        ]
    )
    assert boards == [Path("charts/rev.yml"), Path("charts/other.yaml")]
    assert defaults_files == [Path("charts/meta.yml"), Path("charts/sub/meta.yaml")]


def test_partition_defaults_files_all_boards_returns_empty_defaults() -> None:
    boards, defaults_files = partition_defaults_files([Path("charts/rev.yml")])
    assert boards == [Path("charts/rev.yml")]
    assert defaults_files == []
