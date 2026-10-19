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

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api._link_scan import LinkHit, board_slug, rewrite_links
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    DBT_PACKAGES_SUBDIR,
    Project,
    is_board_candidate,
)

# Errors a backing-store read can raise for a single file that should be skipped
# rather than aborting a whole read: not-a-text-file, gone, or unreadable. Single-file
# reads only — search skips unreadable files inside ProjectFileQueries itself.
_UNREADABLE_FILE = (UnicodeDecodeError, OSError, ExecutionError)


class ReadFileArgs(BaseModel):
    """Read a UTF-8 text file from the project. Path is root-relative (includes
    the charts/ prefix for a dashboard, e.g. 'charts/looker/x.yaml') — not the
    board-relative form. If the path came from search_files, use the hit's
    file_path, not board_path."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path relative to the project root.")


class WriteFileArgs(BaseModel):
    """Write a UTF-8 text file in the project, creating parent directories.

    Overwriting an existing file is refused unless you have already read_file'd
    it — you cannot preserve what you have not seen. To change
    part of an existing file, prefer apply_patch. Path is root-relative (includes the
    charts/ prefix for a dashboard) and may not escape it. If the path came
    from search_files, use the hit's file_path, not board_path.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path relative to the project root.")
    content: str = Field(description="Full file contents to write.")


class MoveFileArgs(BaseModel):
    """Move (rename) a project file. Fails if the destination already exists
    or the source does not. Both paths are relative to the project root and
    may not escape it.

    When both paths are boards (under charts/), exact-match ``link:``
    chart fields and markdown links pointing at the old slug are rewritten to
    the new one in the same operation; any other reference to the old slug's
    basename is reported as a fuzzy hit, never rewritten.
    """

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(
        description="Current file path relative to the project root."
    )
    destination_path: str = Field(
        description="New file path relative to the project root."
    )


class DeleteFileArgs(BaseModel):
    """Delete a project file. Fails if it does not exist. Path is relative to
    the project root and may not escape it."""

    model_config = ConfigDict(extra="forbid")

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
        content = project.read_text(copy_on_write_paths(path, project)[0])
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
        # A package board path writes its host copy, so that is what is overwritten.
        _, path = copy_on_write_paths(path, project)
        if path in files_seen or not project.exists(path):
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
            "apply_patch to change part of it."
        ),
    )


def copy_on_write_paths(path: str, project: Project) -> tuple[str, str]:
    """``(read_from, write_to)`` for *path*, the one place copy-on-write is decided.

    A package board (``dbt_packages/<pkg>/charts/<rest>``, a board candidate) is
    dbt-managed, so a write saves ``charts/<pkg>/<rest>`` instead; that host
    board shadows the package's (see ``Project.iter_boards``). Reads come from
    the host copy once it exists, else from the package file. Any other path
    reads and writes itself.
    """
    parts = path.split("/")
    if not (
        len(parts) > 3
        and parts[0] == DBT_PACKAGES_SUBDIR
        and parts[2] == CHARTS_SUBDIR
        and is_board_candidate(path)
    ):
        return path, path
    target = "/".join([CHARTS_SUBDIR, parts[1], *parts[3:]])
    return (target if project.exists(target) else path), target


def write_file(path: str, content: str, project: Project) -> WriteFileResult:
    """Write *content* to *path*; a package board path saves a host copy.

    The result's ``path`` is where the content was actually written.
    """
    _, path = copy_on_write_paths(path, project)
    try:
        project.write_text(path, content)
    except (ValueError, OSError) as exc:
        return WriteFileResult(success=False, path=path, error=str(exc))
    return WriteFileResult(
        success=True, path=path, bytes_written=len(content.encode("utf-8"))
    )


def _closest_lines(text: str, old_string: str) -> str | None:
    """The run of lines in *text*, as long as *old_string*, that is most like it."""
    lines = text.splitlines(keepends=True)
    width = max(1, len(old_string.splitlines()))
    windows = ["".join(lines[i : i + width]) for i in range(len(lines) - width + 1)]
    if not windows:
        return None
    # Lines in common first: a recalled block most often has the right lines in
    # the wrong order or with one value changed.
    wanted = {line.strip() for line in old_string.splitlines()}

    def score(window: str) -> tuple[int, float]:
        shared = len(wanted & {line.strip() for line in window.splitlines()})
        return shared, SequenceMatcher(None, window, old_string).ratio()

    return max(windows, key=score)


# -- apply_patch: the Responses API built-in tool, Codex semantics -----------------


class PatchOperation(BaseModel):
    """One ``apply_patch_call`` operation, as the Responses API delivers it."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["create_file", "update_file", "delete_file"]
    path: str
    diff: str | None = None


class PatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str
    error: str | None = None


@dataclass
class _Hunk:
    contexts: list[str] = field(default_factory=list)
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    at_eof: bool = False

    @property
    def has_body(self) -> bool:
        return bool(self.old or self.new or self.at_eof)


_EOF_MARKER = "*** End of File"
_PUNCTUATION = str.maketrans(
    {
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
        **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
        **dict.fromkeys("\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008", " "),
        **dict.fromkeys("\u2009\u200a\u202f\u205f\u3000", " "),
    }
)


def _parse_hunks(diff: str) -> list[_Hunk]:
    """Split a V4A update body into hunks (Codex's ``@@`` chunk grammar).

    A hunk is optional ``@@ context`` headers, then ``' '``/``-``/``+`` lines; an
    empty line is an empty context line. Consecutive headers anchor in turn
    (``@@ class A`` then ``@@ def f``).
    """
    lines = diff.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    hunks: list[_Hunk] = []
    current = _Hunk()
    for number, line in enumerate(lines, 1):
        if line.startswith("@@"):
            if current.has_body:
                hunks.append(current)
                current = _Hunk()
            context = line[2:].strip()
            if context:
                current.contexts.append(context)
        elif line == _EOF_MARKER:
            current.at_eof = True
        elif line == "" or line[0] == " ":
            current.old.append(line[1:])
            current.new.append(line[1:])
        elif line[0] == "-":
            current.old.append(line[1:])
        elif line[0] == "+":
            current.new.append(line[1:])
        else:
            raise ValueError(
                f"diff line {number} must start with ' ', '+', '-' or '@@': {line!r}"
            )
    if current.has_body:
        hunks.append(current)
    if not hunks:
        raise ValueError("diff has no hunks")
    return hunks


def _normalize_punctuation(line: str) -> str:
    return line.strip().translate(_PUNCTUATION)


def _seek_sequence(
    lines: list[str], pattern: list[str], start: int, at_eof: bool
) -> int | None:
    """Codex's ``seek_sequence``: the first index at or after *start* where
    *pattern* matches, trying exact, then trailing-whitespace-insensitive, then
    whitespace-insensitive, then punctuation-normalized. An end-of-file hunk
    searches from the tail first."""
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    tail = len(lines) - len(pattern)
    first = tail if at_eof else start
    keys: tuple[Callable[[str], str], ...] = (
        str,
        str.rstrip,
        str.strip,
        _normalize_punctuation,
    )
    for key in keys:
        wanted = [key(line) for line in pattern]
        for i in range(first, tail + 1):
            if [key(line) for line in lines[i : i + len(pattern)]] == wanted:
                return i
    return None


def _apply_hunks(text: str, hunks: list[_Hunk]) -> str:
    """Codex's ``compute_replacements`` + ``apply_replacements``."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    replacements: list[tuple[int, int, list[str]]] = []
    cursor = 0
    for number, hunk in enumerate(hunks, 1):
        for context in hunk.contexts:
            found = _seek_sequence(lines, [context], cursor, False)
            if found is None:
                raise ValueError(
                    f"hunk {number}: context line not found: @@ {context}\n"
                    f"closest lines in the file:\n{_closest_lines(text, context)}"
                )
            cursor = found + 1
        if not hunk.old:
            replacements.append((len(lines), 0, hunk.new))
            continue
        old, new = hunk.old, hunk.new
        found = _seek_sequence(lines, old, cursor, hunk.at_eof)
        if found is None and old[-1] == "":
            # A diff ending in a blank context line: the file has no such line.
            old, new = old[:-1], new[:-1] if new and new[-1] == "" else new
            found = _seek_sequence(lines, old, cursor, hunk.at_eof)
        if found is None:
            raise ValueError(
                f"hunk {number}: could not find the lines to change, in order, "
                f"after line {cursor}:\n" + "\n".join(hunk.old) + "\n"
                f"closest lines in the file:\n{_closest_lines(text, chr(10).join(hunk.old))}"
            )
        replacements.append((found, len(old), new))
        cursor = found + len(old)
    # Add-only hunks record the original end of file; applying back to front is
    # only right when the replacements are in file order.
    replacements.sort(key=lambda replacement: replacement[0])
    for start, length, new in reversed(replacements):
        lines[start : start + length] = new
    return "\n".join(lines) + "\n"


def apply_patch(operation: PatchOperation, project: Project) -> PatchResult:
    """Apply one ``apply_patch_call`` operation. LOCAL semantics: nothing is
    written unless the whole operation applies, and no exception escapes.

    Like ``write_file``, a create or update of a package board path saves its
    host copy (see ``copy_on_write_paths``), and the result's ``path`` is where
    the content was written. A delete acts on the path as given.
    """
    path = operation.path
    try:
        if operation.type == "delete_file":
            project.delete_text(path)
        elif operation.diff is None:
            raise ValueError(f"{operation.type} needs a diff")
        else:
            source, path = copy_on_write_paths(path, project)
            if operation.type == "create_file":
                if project.exists(path):
                    raise ValueError(f"file already exists: {path}; use update_file")
                body = operation.diff.removesuffix("\n").split("\n")
                if not all(line.startswith("+") for line in body):
                    raise ValueError("create_file diff lines must each start with '+'")
                project.write_text(path, "\n".join(line[1:] for line in body) + "\n")
            else:
                project.write_text(
                    path,
                    _apply_hunks(
                        project.read_text(source), _parse_hunks(operation.diff)
                    ),
                )
    except FileNotFoundError:
        return PatchResult(success=False, path=path, error=f"file not found: {path}")
    except (ValueError, IsADirectoryError, *_UNREADABLE_FILE) as exc:
        return PatchResult(success=False, path=path, error=str(exc))
    return PatchResult(success=True, path=path)


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
