"""Resolve ``glob_boards`` / ``glob_folders`` calls against the project.

The data provider answers text listings from here at render;
``listing_dependencies`` names the files a listing reads, for the import closure.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from dbt_charts.core.compile.parse.board_text import BoardText, parse_board_text
from dbt_charts.core.compile.template.listings import (
    GLOB_BOARDS,
    BoardListing,
    FolderListing,
    ListingCall,
    TextListings,
    extract_listing_calls,
    listing_pattern_error,
)
from dbt_charts.core.project import (
    INDEX_CANDIDATE_NAMES,
    BoardRoot,
    ProjectDirectory,
    ProjectPath,
    assert_relpath,
    board_root_for_relpath,
    board_roots,
    board_slug,
    glob_to_regex,
    is_board_candidate,
)


def _scope(directory: ProjectDirectory, call: ListingCall) -> tuple[str, BoardRoot]:
    root = board_root_for_relpath(directory.relpath, board_roots(directory.project))
    if root is None:
        raise listing_pattern_error(call, "the board is not under charts/")
    try:
        full = assert_relpath(call[1], directory.relpath)
    except ValueError as e:
        raise listing_pattern_error(call, str(e)) from e
    if not full.startswith(f"{root.relpath}/"):
        raise listing_pattern_error(call, f"must stay under {root.relpath}/")
    return full, root


def _is_listable(path: ProjectPath, root: BoardRoot) -> bool:
    inner = PurePosixPath(path.relpath).relative_to(root.relpath).parts
    return (
        is_board_candidate(path.relpath)
        and not path.is_meta
        and not any(part.startswith("_") for part in inner)
    )


def _listed_boards(directory: ProjectDirectory, call: ListingCall) -> list[ProjectPath]:
    full, root = _scope(directory, call)
    return [
        p
        for p in directory.project.files.glob(full)
        if _is_listable(p, root) and p.name not in INDEX_CANDIDATE_NAMES
    ]


def _listed_folders(
    directory: ProjectDirectory, call: ListingCall
) -> dict[str, list[ProjectPath]]:
    """Each matched folder with every listable board under it, by folder relpath."""
    full, root = _scope(directory, call)
    regex = glob_to_regex(full)
    folders: dict[str, list[ProjectPath]] = {}
    for p in directory.project.files.glob(f"{full}/**"):
        if not _is_listable(p, root):
            continue
        for parent in PurePosixPath(p.relpath).parents:
            if regex.match(str(parent)):
                folders.setdefault(str(parent), []).append(p)
    return dict(sorted(folders.items()))


def _parse(path: ProjectPath) -> BoardText:
    return parse_board_text(
        board_slug(path.relpath), path.read_text(), is_markdown=path.is_markdown
    )


def _board_listing(path: ProjectPath) -> BoardListing:
    parsed = _parse(path)
    return BoardListing(
        path=f"/{board_slug(path.relpath)}",
        title=parsed.title,
        notes=parsed.notes,
        tags=list(parsed.tags),
    )


def _folder_listing(relpath: str, boards: list[ProjectPath]) -> FolderListing:
    name = PurePosixPath(relpath).name
    index = next(
        (
            b
            for n in INDEX_CANDIDATE_NAMES
            for b in boards
            if b.relpath == f"{relpath}/{n}"
        ),
        None,
    )
    title = name if index is None else _parse(index).title
    return FolderListing(path=f"/{board_slug(relpath)}/", name=name, title=title)


def resolve_listings(
    calls: frozenset[ListingCall], directory: ProjectDirectory
) -> TextListings:
    """What *calls*, written in a board under *directory*, return."""
    listings = TextListings()
    for call in sorted(calls):
        function, pattern = call
        if function == GLOB_BOARDS:
            listings.boards[pattern] = [
                _board_listing(p) for p in _listed_boards(directory, call)
            ]
        else:
            listings.folders[pattern] = [
                _folder_listing(relpath, boards)
                for relpath, boards in _listed_folders(directory, call).items()
            ]
    return listings


def listing_dependencies(text: str, directory: ProjectDirectory) -> list[ProjectPath]:
    """Every file whose content or presence changes what *text* lists."""
    files: dict[str, ProjectPath] = {}
    for call in sorted(extract_listing_calls(text)):
        if call[0] == GLOB_BOARDS:
            found = _listed_boards(directory, call)
        else:
            found = [b for bs in _listed_folders(directory, call).values() for b in bs]
        files.update((p.relpath, p) for p in found)
    return [files[k] for k in sorted(files)]
