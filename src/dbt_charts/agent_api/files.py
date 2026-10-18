"""General project-file tools for the chat agent.

Claude-Code-style primitives — read, write, edit, move, delete — that let an
agent author and save boards directly, rather than via a domain-specific
"write a dashboard" verb. The agent composes these with the typed dct tools
(``render_board``, ``schema``, ``execute_sql``).

Every path is resolved against and confined to the project root. A path that
escapes the root (``..`` traversal, absolute path outside, symlink out) is
refused — these tools never touch the wider filesystem.
"""

from __future__ import annotations

from collections.abc import Collection

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api._link_scan import LinkHit, board_slug, rewrite_links
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.project import Project

# Errors a backing-store read can raise for a single file that should be skipped
# rather than aborting a whole read: not-a-text-file, gone, or unreadable. Single-file
# reads only — search skips unreadable files inside ProjectFileQueries itself.
_UNREADABLE_FILE = (UnicodeDecodeError, OSError, ExecutionError)


class ReadFileArgs(BaseModel):
    """Read a UTF-8 text file from the project. Path is root-relative (includes
    the charts/ prefix for a dashboard, e.g. 'charts/looker/x.yaml') — not the
    board-relative form. If the path came from search_files, use the hit's
    file_path, not board_path."""

    path: str = Field(description="File path relative to the project root.")


class WriteFileArgs(BaseModel):
    """Write a UTF-8 text file in the project, creating parent directories.

    Overwriting an existing file is refused unless you have already read_file'd
    it — you cannot preserve what you have not seen. To change
    part of an existing file, prefer edit_file. Path is root-relative (includes the
    charts/ prefix for a dashboard) and may not escape it. If the path came
    from search_files, use the hit's file_path, not board_path.
    """

    path: str = Field(description="File path relative to the project root.")
    content: str = Field(description="Full file contents to write.")


class EditFileArgs(BaseModel):
    """Replace an exact string in a project file. old_string must occur exactly once.

    Fails (writing nothing) if old_string is absent or appears more than once —
    include enough surrounding context to make it unique. Path is
    root-relative (includes the charts/ prefix for a dashboard) and may not
    escape it. If the path came from search_files, use the hit's
    file_path, not board_path.
    """

    path: str = Field(description="File path relative to the project root.")
    old_string: str = Field(description="Exact text to replace (must be unique).")
    new_string: str = Field(description="Replacement text.")


class MoveFileArgs(BaseModel):
    """Move (rename) a project file. Fails if the destination already exists
    or the source does not. Both paths are relative to the project root and
    may not escape it.

    When both paths are boards (under charts/), exact-match ``link:``
    chart fields and markdown links pointing at the old slug are rewritten to
    the new one in the same operation; any other reference to the old slug's
    basename is reported as a fuzzy hit, never rewritten.
    """

    source_path: str = Field(
        description="Current file path relative to the project root."
    )
    destination_path: str = Field(
        description="New file path relative to the project root."
    )


class DeleteFileArgs(BaseModel):
    """Delete a project file. Fails if it does not exist. Path is relative to
    the project root and may not escape it."""

    path: str = Field(description="File path relative to the project root.")


class ReadFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str
    content: str | None = None
    error: str | None = None


class WriteFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str
    bytes_written: int = 0
    error: str | None = None


class EditFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str
    replacements: int = 0
    error: str | None = None


class MoveFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    source_path: str
    destination_path: str
    links_rewritten: int = 0
    fuzzy_links: list[LinkHit] = Field(default_factory=list)
    error: str | None = None


class DeleteFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str
    error: str | None = None


def read_file(path: str, project: Project) -> ReadFileResult:
    try:
        content = project.read_text(path)
    except FileNotFoundError:
        return ReadFileResult(success=False, path=path, error=f"file not found: {path}")
    except (ValueError, IsADirectoryError, *_UNREADABLE_FILE) as exc:
        return ReadFileResult(success=False, path=path, error=str(exc))
    return ReadFileResult(success=True, path=path, content=content)


def refuse_blind_overwrite(
    path: str, project: Project, files_seen: Collection[str]
) -> WriteFileResult | None:
    """The refusal for overwriting a file the caller has never read, or None.

    An agent cannot preserve what it has not seen: given a whole-file write over
    a board it never read, everything in that board it did not think to reproduce
    is simply gone. `files_seen` is the caller's record of paths it has read — hosts scope that
    however their session works, so the refusal says only "has not been read",
    never how long that is remembered.

    A path this cannot resolve is refused, not waved through. "I could not
    check" must never mean "go ahead": ``exists()`` applies ``assert_relpath``
    and ``write_text`` does not, so the two disagree about a backslash-spelled
    ref — on POSIX it becomes a junk file named ``charts\board.yml`` at the
    root, and on Windows it *is* ``charts/board.yml`` and overwrites the very
    board this gate exists to protect.
    """
    if path in files_seen:
        return None
    try:
        if not project.exists(path):
            return None
    except ValueError as exc:
        return WriteFileResult(
            success=False,
            path=path,
            error=(
                f"{path} is not a usable project path ({exc}), so whether it "
                "would overwrite an existing file cannot be checked. Write a "
                "project-root-relative path with forward slashes."
            ),
        )
    return WriteFileResult(
        success=False,
        path=path,
        error=(
            f"{path} already exists and has not been read. Overwriting it would "
            "discard content you have not seen. Call read_file first, or use "
            "edit_file to change part of it."
        ),
    )


def write_file(path: str, content: str, project: Project) -> WriteFileResult:
    try:
        project.write_text(path, content)
    except (ValueError, OSError) as exc:
        return WriteFileResult(success=False, path=path, error=str(exc))
    return WriteFileResult(
        success=True, path=path, bytes_written=len(content.encode("utf-8"))
    )


def edit_file(
    path: str, old_string: str, new_string: str, project: Project
) -> EditFileResult:
    try:
        original = project.read_text(path)
    except FileNotFoundError:
        return EditFileResult(success=False, path=path, error=f"file not found: {path}")
    except (ValueError, IsADirectoryError, *_UNREADABLE_FILE) as exc:
        return EditFileResult(success=False, path=path, error=str(exc))
    count = original.count(old_string)
    if count == 0:
        return EditFileResult(
            success=False, path=path, error="old_string not found in file"
        )
    if count > 1:
        return EditFileResult(
            success=False,
            path=path,
            error=f"old_string is not unique ({count} occurrences) — add surrounding context",
        )
    try:
        project.write_text(path, original.replace(old_string, new_string))
    except (ValueError, OSError) as exc:
        return EditFileResult(success=False, path=path, error=str(exc))
    return EditFileResult(success=True, path=path, replacements=1)


def move_file(
    source_path: str, destination_path: str, project: Project
) -> MoveFileResult:
    """Move *source_path* to *destination_path*, rewriting exact inbound
    dashboard links along the way (see ``MoveFileArgs``). LOCAL semantics: no
    exceptions escape, everything is reported in the typed result."""
    try:
        content = project.read_text(source_path)
    except FileNotFoundError:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=f"file not found: {source_path}",
        )
    except (ValueError, IsADirectoryError, *_UNREADABLE_FILE) as exc:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=str(exc),
        )
    try:
        destination_exists = project.exists(destination_path)
    except ValueError as exc:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=str(exc),
        )
    if destination_exists:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=f"destination already exists: {destination_path}",
        )

    old_slug, new_slug = board_slug(source_path), board_slug(destination_path)
    scan = rewrite_links(project, old_slug, new_slug) if old_slug and new_slug else None

    try:
        project.write_text(destination_path, content)
    except (ValueError, OSError) as exc:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=str(exc),
        )
    try:
        project.delete_text(source_path)
    except (ValueError, OSError, FileNotFoundError) as exc:
        return MoveFileResult(
            success=False,
            source_path=source_path,
            destination_path=destination_path,
            error=f"wrote {destination_path!r} but failed to delete {source_path!r}: {exc}",
        )
    return MoveFileResult(
        success=True,
        source_path=source_path,
        destination_path=destination_path,
        links_rewritten=len(scan.exact) if scan else 0,
        fuzzy_links=scan.fuzzy if scan else [],
    )


def delete_file(path: str, project: Project) -> DeleteFileResult:
    """Delete *path*. LOCAL semantics: no exceptions escape."""
    try:
        project.delete_text(path)
    except FileNotFoundError:
        return DeleteFileResult(
            success=False, path=path, error=f"file not found: {path}"
        )
    except (ValueError, OSError) as exc:
        return DeleteFileResult(success=False, path=path, error=str(exc))
    return DeleteFileResult(success=True, path=path)
