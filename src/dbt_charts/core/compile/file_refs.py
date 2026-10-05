"""The one resolver for authored relative file refs, and the reader of what a
cross-file ref points at.

Every call site passes the directory its ref is anchored at; this module owns
the escape rule, the existence check, the per-kind suffix rule, and the error.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

import yaml

from dbt_charts.core.compile.errors import CompilationError, ReferenceError
from dbt_charts.core.compile.markdown_blocks import image_urls
from dbt_charts.core.diagnostics.codes_compile import ERR_FILE_SOURCE_AMBIGUOUS
from dbt_charts.core.diagnostics.codes_render import ERR_IMAGE_REF_UNRESOLVED
from dbt_charts.core.utils import IMAGE_SUFFIXES

if TYPE_CHECKING:
    from dbt_charts.core.compile.validate.authoring_warnings import YamlValue
    from dbt_charts.core.project import ProjectDirectory, ProjectPath

# Inline `source: data/x.csv` suffix → file-source type.
INLINE_FILE_SOURCE_SUFFIXES: dict[str, str] = {
    ".csv": "csv",
    ".json": "json",
    ".parquet": "parquet",
}


def _anchor(
    ref: str, anchor: ProjectDirectory | None, ref_path: Sequence[str]
) -> ProjectDirectory:
    if anchor is None:
        raise ReferenceError(ref, "a board with no project location", ref_path)
    return anchor


def _join(
    ref: str, file_ref: str, anchor: ProjectDirectory, ref_path: Sequence[str]
) -> ProjectPath:
    try:
        return anchor / file_ref
    except ValueError as e:
        raise ReferenceError(ref, f"{anchor.relpath!r} — {e}", ref_path) from e


def split_external_query_ref(query_ref: str) -> tuple[str, str]:
    """`path/to/file.yml#query_name` → `("path/to/file.yml", "query_name")`.

    The grammar only — which directory the file part is anchored at is the
    caller's business, and the two resolvers deliberately disagree about it
    (`resolve_external_query` in `normalize/queries.py` anchors at the compiling board's
    `base_dir`; the cross-board chart-import walk in `compiler.py` anchors at
    the *source* board's own directory). The parse itself must not diverge with
    them, or the same authored string resolves for a local chart and errors
    for an imported one.
    """
    file_part, _, query_name = query_ref.partition("#")
    if not file_part or not query_name:
        raise CompilationError(
            f"Invalid external query reference: '{query_ref}'. "
            "Expected format: 'path/to/file.yml#query_name'"
        )
    return file_part, query_name


RefKind = Literal["file", "query_file", "queries", "charts", "variables"]


def resolve_ref(
    ref: str,
    anchor: ProjectDirectory | None,
    kind: RefKind,
    *,
    ref_path: Sequence[str] = (),
) -> ProjectPath:
    """Resolve an authored file ref against *anchor*; the file must exist.

    ``"file"`` (layout import, ``extends:`` path) is used as written.
    ``"query_file"`` is a ``file.yml#q`` ref: the file part precedes ``#``. A
    section kind is a dotted ``x.<section>.<name>`` ref: the file part precedes
    the last ``.<section>.``, with ``.yml`` appended when unsuffixed. *ref_path*
    gives the ``ReferenceError`` a diagnostic range.
    """
    directory = _anchor(ref, anchor, ref_path)
    file_ref = ref
    if kind == "query_file":
        file_ref = split_external_query_ref(ref)[0]
    elif kind != "file":
        file_ref = ref.rsplit(f".{kind}.", 1)[0]
        if not file_ref.endswith((".yml", ".yaml")):
            file_ref = f"{file_ref}.yml"
    path = _join(ref, file_ref, directory, ref_path)
    if not path.exists():
        raise ReferenceError(
            ref, f"{directory.relpath!r} — {path.relpath!r} does not exist", ref_path
        )
    return path


def resolve_file_source(
    ref: str, anchor: ProjectDirectory | None, query_name: str
) -> tuple[ProjectPath, str]:
    """Resolve an inline ``source: data/x.csv`` to its file and source type.

    Tried against *anchor* and the project root, so ``data/x.csv`` works from
    any depth while ``../data/x.csv`` keeps working. Both existing is
    ``ERR-FILE-SOURCE-AMBIGUOUS``, never a silent pick; neither returns the
    *anchor* candidate, for execute to report the missing file per chart.
    """
    directory = _anchor(ref, anchor, ())
    file_type = INLINE_FILE_SOURCE_SUFFIXES.get(PurePosixPath(ref).suffix.lower())
    if file_type is None:
        raise ReferenceError(
            ref,
            f"query {query_name!r} — an inline file source needs one of "
            f"{sorted(INLINE_FILE_SOURCE_SUFFIXES)}",
        )
    # Keyed by relpath: from the root both anchors are one candidate. An anchor
    # the ref escapes is absent; only both failing is the author's error.
    candidates: dict[str, tuple[str, ProjectPath]] = {}
    escape: ReferenceError | None = None
    for label, candidate_dir in (
        ("board directory", directory),
        ("project root", directory.project.directory()),
    ):
        try:
            candidate = _join(ref, ref, candidate_dir, ())
        except ReferenceError as e:
            escape = e
            continue
        candidates.setdefault(candidate.relpath, (label, candidate))
    if escape is not None and not candidates:
        raise escape
    existing = [(label, p) for label, p in candidates.values() if p.exists()]
    if len(existing) > 1:
        found = " and ".join(f"{p.relpath!r} ({label})" for label, p in existing)
        raise CompilationError.from_code(
            ERR_FILE_SOURCE_AMBIGUOUS, query_name=query_name, ref=ref, candidates=found
        )
    path = existing[0][1] if existing else next(iter(candidates.values()))[1]
    return path, file_type


# "//host/a.png" names a remote origin even though it carries no scheme.
_NOT_PROJECT_IMAGES = ("http://", "https://", "//", "data:")

# Why a template may pick among images but not name one: a host tells a render
# is stale from the files the board's YAML names.
_TEMPLATED_IMAGE = (
    "a project image path must be written out, not built by a template; to "
    "switch images, put {% if %} around whole images"
)


def is_local_image(ref: str) -> bool:
    """Whether image destination *ref* names a project file, rather than a
    remote URL (``http(s)://``, ``//host``) or an inline ``data:`` URI."""
    return not ref.lower().startswith(_NOT_PROJECT_IMAGES)


def written_image_refs(markdown: str) -> list[str]:
    """The project image paths *markdown* writes out, in document order.

    A path holding Jinja is not one: it names no file until rendered.
    """
    from mdsvg import parse

    return [
        url
        for url in image_urls(list(parse(markdown)))
        if is_local_image(url) and "{{" not in url and "{%" not in url
    ]


def check_images_written_out(authored: str, rendered: str) -> None:
    """Raise ERR-IMAGE-REF-UNRESOLVED if *rendered*, the Jinja rendering of the
    markdown *authored*, names a project image that *authored* does not write
    out (see ``written_image_refs``)."""
    if rendered == authored:
        return
    from mdsvg import parse

    written = set(written_image_refs(authored))
    for url in image_urls(list(parse(rendered))):
        if is_local_image(url) and url not in written:
            raise CompilationError.from_code(
                ERR_IMAGE_REF_UNRESOLVED, ref=url, reason=_TEMPLATED_IMAGE
            )


def _charts_directory(directory: ProjectDirectory) -> ProjectDirectory:
    # tach-ignore(pre-existing compile->project coupling — accepted debt)
    from dbt_charts.core.project import CHARTS_SUBDIR

    return directory.project.directory(CHARTS_SUBDIR)


def image_path(ref: str, written_in: ProjectDirectory | None) -> ProjectPath:
    """The file a markdown image path (``![](img/x.png)``) names, whether or not
    it exists; the path must carry an ``IMAGE_SUFFIXES`` suffix.

    A relative path resolves against *written_in*; a leading ``/`` roots it at
    ``charts/``, the directory every host serves.
    """
    directory = _anchor(ref, written_in, ())
    if PurePosixPath(ref).suffix.lower() not in IMAGE_SUFFIXES:
        raise ReferenceError(
            ref,
            f"{directory.relpath!r} — unsupported image suffix; use one of "
            f"{sorted(IMAGE_SUFFIXES)}",
        )
    if ref.startswith("/"):
        return _join(ref, ref[1:], _charts_directory(directory), ())
    return _join(ref, ref, directory, ())


def resolve_image(ref: str, written_in: ProjectDirectory | None) -> ProjectPath:
    """``image_path``, which must exist.

    A relative path that only exists under ``charts/`` is not taken from
    there: the error names the ``/`` spelling instead.
    """
    path = image_path(ref, written_in)
    if path.exists():
        return path
    directory = _anchor(ref, written_in, ())
    hint = ""
    if not ref.startswith("/"):
        try:
            served = _charts_directory(directory) / ref
        except ValueError:  # the ref climbs out of charts/: no such spelling
            served = None
        if served is not None and served.relpath != path.relpath and served.exists():
            hint = f"; {served.relpath!r} does — write '/{ref}' to use it"
    raise ReferenceError(
        ref, f"{directory.relpath!r} — {path.relpath!r} does not exist{hint}"
    )


def _markdown_keys(obj: dict[str, YamlValue]) -> frozenset[str]:
    if "type" not in obj:
        return frozenset({"title", "text"})
    if obj["type"] == "callout":
        return frozenset({"title", "message"})
    return frozenset()


def markdown_fields(
    obj: dict[str, YamlValue],
) -> Iterator[tuple[dict[str, YamlValue], str, str]]:
    """Each ``(mapping, key, markdown)`` in a board mapping as authored, at any
    depth: a board's or tab's ``title``/``text``, a callout's
    ``title``/``message``. Other charts' titles are plain text, and a
    ``{ref: ...}`` mapping holds none."""
    if isinstance(obj.get("ref"), str):
        return
    keys = _markdown_keys(obj)
    for key, value in obj.items():
        if isinstance(value, str):
            if key in keys:
                yield obj, key, value
        elif isinstance(value, dict):
            yield from markdown_fields(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from markdown_fields(item)


def anchor_images(data: dict[str, YamlValue], written_in: ProjectDirectory) -> None:
    """Respell, in place, each relative project image path in *data*'s markdown
    from ``charts/`` (``/…``), so it names the file it names from *written_in*
    (the directory of the file *data* was read from) wherever that markdown is
    inherited to.

    Raises ``ReferenceError`` for a path ``image_path`` rejects, or one naming
    a file outside ``charts/``, which no ``/`` spelling reaches.
    """
    charts = PurePosixPath(_charts_directory(written_in).relpath)
    for mapping, key, markdown in markdown_fields(data):
        anchored = markdown
        for ref in dict.fromkeys(written_image_refs(markdown)):
            if ref.startswith("/"):
                continue
            path = PurePosixPath(image_path(ref, written_in).relpath)
            if not path.is_relative_to(charts):
                raise ReferenceError(
                    ref,
                    f"{written_in.relpath!r} — {str(path)!r} is outside "
                    f"{str(charts)!r}, so markdown inheriting it cannot name it",
                )
            respelled = f"](/{path.relative_to(charts)}".replace("\\", "\\\\")
            # The parse already found *ref* as an image destination; this only
            # respells it where it is written, up to the space or ")" ending it.
            anchored = re.sub(rf"\]\({re.escape(ref)}(?=[\s)])", respelled, anchored)
        mapping[key] = anchored


def read_section(
    path: ProjectPath, section: str, ref: str, ref_path: Sequence[str] = ()
) -> tuple[dict[str, YamlValue], dict[str, YamlValue]]:
    """The `section:` mapping of the file a cross-file *ref* resolved to, and the
    file's whole mapping (whose board-level `source:` its queries inherit)."""
    try:
        content = path.read_yaml()
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise CompilationError(f"{ref!r}: failed to load {path.relpath}: {e}") from e
    if not isinstance(content, dict):
        raise ReferenceError(ref, f"{path.relpath!r} — not a YAML mapping", ref_path)
    if not isinstance(content.get(section), dict):
        raise ReferenceError(
            ref, f"{path.relpath!r} — that file has no {section}: section", ref_path
        )
    return content[section], content
