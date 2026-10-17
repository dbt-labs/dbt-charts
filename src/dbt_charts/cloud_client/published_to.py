"""Where a project is published in Cloud, recorded in the project's own
``dbt_charts.yml`` -- the one piece of Cloud state that otherwise lives only
in Cloud's database. ``published_to:`` is written by ``dct cloud project
connect`` (``cli/commands/cloud.py``) and read first by context resolution
(``cloud_client.context``), before it falls back to matching the git remote.

``dbt_charts.cloud_client`` may import no other first-party dbt_charts module
(``tach.toml``), so the filesystem walks here -- finding a repository's
``.git``, finding the nearest ``dbt_charts.yml`` -- are self-contained rather
than reused from ``core.project_roots``.

Everything here raises ``ValueError`` and nothing else: every ``dct cloud``
verb resolves through ``resolve_published_to`` now, and a half-edited
``dbt_charts.yml`` is a routine transient state that must reach the user as
this CLI's one failure output rather than as a traceback.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import yaml

DBT_CHARTS_YML = "dbt_charts.yml"
PUBLISHED_TO_KEY = "published_to"
_GIT_MARKER = ".git"
BOM = "\ufeff"
# Kept in step with ``Config.PUBLISHED_TO_FORM`` by the parity test in
# ``tests/cloud_client/test_published_to.py``; tach forbids the shared import.
EXPECTED_FORM = "https://<host>/<org>/<project>/"


def published_to_url(host: str, org: str, project: str) -> str:
    """The project-home URL `published_to:` records for *org*/*project*.

    *host* is an origin with no path and no trailing slash -- what
    `CloudClient.host` normalizes to. The trailing slash is load-bearing:
    Cloud mounts the project at `/<org>/<project>/` and runs with
    `APPEND_SLASH = False`, so the unslashed form is not a page.

    Raises ValueError when the result is not a value `parse_published_to`
    accepts. A host carrying a path (`--host https://example.com/dct`) would
    otherwise write a three-segment record that `Config` rejects, breaking
    `dct render` for the whole project rather than just the cloud verbs.
    """
    url = f"{host}/{org}/{project}/"
    parse_published_to(url)
    return url


def parse_published_to(value: str) -> tuple[str, str, str]:
    """(host, org, project) from a `published_to:` value, or raise ValueError.

    Strict on purpose: this is the one piece of Cloud state that lives in
    git, so a value that doesn't parse is a corrupted record worth failing
    loud over, not a hint to quietly skip. The trailing slash is the one
    liberty taken -- a hand-written value without it names the same three
    things unambiguously, and only a browser cares.
    """
    parsed = urlsplit(value)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or len(segments) != 2
    ):
        raise ValueError(
            f"published_to must be an absolute URL of the form {EXPECTED_FORM}, "
            f"got {value!r}"
        )
    org, project = segments
    return f"{parsed.scheme}://{parsed.netloc}", org, project


@dataclass(frozen=True)
class PublishedTo:
    """A parsed `published_to:`, plus the file it came from."""

    yml_path: Path
    host: str
    org: str
    project: str


def _read_yaml(path: Path) -> object:
    """*path* parsed as YAML, or a ValueError naming it.

    ``yaml.YAMLError`` is not a ``ValueError`` and no caller up the chain
    catches it, so an unquoted bracket in a config file would otherwise
    traceback out of every ``dct cloud`` verb.
    """
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)  # noqa: S506 — CSafeLoader subclass
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} could not be read as YAML: {exc}") from None


class _UniqueKeyLoader(yaml.CSafeLoader):
    """CSafeLoader that refuses a repeated mapping key.

    PyYAML's default is last-wins, which would let a file with two
    `published_to:` keys read back as fine here while the project loader
    (which rejects duplicates) refuses the whole file. Both sides of this
    module read with the same rule so the CLI can never write, or accept, a
    file the renderer cannot load.
    """

    # A faithful copy of core.utils.UniqueKeyLoader: tach forbids importing it.
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                # `<<: *anchor` is flattened later by SafeConstructor; its key
                # node cannot be constructed here and is never a duplicate.
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"duplicate key: {key!r}", key_node.start_mark
                    )
                seen.add(key)
            except TypeError:
                # An unhashable key (a sequence) cannot be a duplicate; PyYAML
                # rejects it itself, as a ConstructorError, in the super call.
                pass
        return super().construct_mapping(node, deep=deep)


_MERGE_TAG = "tag:yaml.org,2002:merge"
# core.project_roots.DCT_ROOT_MARKERS, duplicated per tach like the .git walk:
# a dbt root without dbt_charts.yml is a project of its own, and the walk must
# stop there rather than answer with an ancestor's record.
_ROOT_MARKERS = (DBT_CHARTS_YML, "dbt_project.yml")


def _find_published_to_line(start: Path) -> tuple[Path, object] | None:
    """The nearest dbt_charts.yml above *start*, and its raw `published_to:`
    value if declared. None when no dbt_charts.yml exists above *start*, or
    the nearest one declares no `published_to:` (absent or YAML null) --
    never keeps walking past the first dbt_charts.yml found for a farther
    ancestor that might. Anything else declared, a string or not, is returned
    for the caller to parse: an empty or mistyped value is a half-edited
    record, not an undeclared one.

    Raises ValueError, naming the file, when that file cannot be read.
    """
    current = start.resolve()
    while True:
        candidate = current / DBT_CHARTS_YML
        if candidate.is_file():
            data = _read_yaml(candidate)
            value = data.get(PUBLISHED_TO_KEY) if isinstance(data, dict) else None
            return None if value is None else (candidate, value)
        if any((current / marker).is_file() for marker in _ROOT_MARKERS):
            # A dbt root with no dbt_charts.yml: this is its own project, and
            # an ancestor's record would name a sibling's boards.
            return None
        if current.parent == current:
            return None
        current = current.parent


def resolve_published_to(start: Path) -> PublishedTo | None:
    """The nearest dbt_charts.yml's `published_to:`, parsed -- or None when
    none exists above *start* or the nearest one declares none.

    Raises ValueError, prefixed with the file's path, when the nearest
    dbt_charts.yml cannot be read or declares a `published_to:` that fails
    to parse.
    """
    found = _find_published_to_line(start)
    if found is None:
        return None
    yml_path, raw = found
    if not isinstance(raw, str):
        raise ValueError(
            f"{yml_path}: {PUBLISHED_TO_KEY} must be a string of the form "
            f"{EXPECTED_FORM}, got {raw!r}"
        )
    try:
        host, org, project = parse_published_to(raw)
    except ValueError as exc:
        raise ValueError(f"{yml_path}: {exc}") from None
    return PublishedTo(yml_path=yml_path, host=host, org=org, project=project)


def find_local_project_dir(start: Path, git_subdirectory: str) -> Path | None:
    """The local directory a just-connected project's dbt_charts.yml belongs
    in: *start*'s repository root, joined with *git_subdirectory* -- the
    value Cloud just resolved for this connect, whatever `--root`, an
    auto-detected sole dbt root, or the repo root produced it.

    None when *start* is not inside a git checkout at all (a `connect <host>
    --git-url` run with no local clone) -- there is nowhere on disk to write,
    and the caller falls back to printing instructions instead.

    Raises ValueError when *git_subdirectory* is not a path inside the
    repository. The check is on the declared value, not on where the
    filesystem sends it: `repo/dbt -> /elsewhere/real` is a legitimate
    checkout layout, and resolving through the symlink first would reject it.
    """
    current = start.resolve()
    while not (current / _GIT_MARKER).exists():
        if current.parent == current:
            return None
        current = current.parent
    if not git_subdirectory:
        return current
    # Cloud stores POSIX paths; parsing platform-natively would let "/etc" or
    # "C:foo" through on Windows, where neither is absolute to PureWindowsPath.
    declared = PurePosixPath(git_subdirectory)
    if (
        declared.is_absolute()
        or ".." in declared.parts
        or any(":" in part or "\\" in part for part in declared.parts)
    ):
        raise ValueError(
            f"git_subdirectory {git_subdirectory!r} escapes the repository root "
            f"{current}"
        )
    return current.joinpath(*declared.parts)


def set_published_to(text: str, url: str) -> str:
    """Return *text* with `published_to: "<url>"` set or replaced at the top
    level.

    Textual, not `safe_load` + `safe_dump`: ruamel is not a dependency, and
    reserializing would strip a hand-authored file's comments and key order.
    Verifies the result parses and declares *url* before returning -- but
    that check reads back only the one key, so everything here splits and
    rejoins on `"\\n"` alone and leaves any other byte of the file exactly
    where it was.
    """
    # A file's BOM must survive, and must not hide the key from the match:
    # a second top-level `published_to:` verifies fine (PyYAML is last-wins)
    # and leaves a file the next connect can no longer edit.
    bom = BOM if text.startswith(BOM) else ""
    body = text.removeprefix(BOM)
    quoted = f'{PUBLISHED_TO_KEY}: "{url}"'
    # Plain, double-quoted and single-quoted spellings of the key all name the
    # same entry; the complex-key form (`? published_to`) is left to the
    # duplicate-key check below rather than guessed at.
    pattern = re.compile(
        rf"^(?:{PUBLISHED_TO_KEY}|\"{PUBLISHED_TO_KEY}\"|'{PUBLISHED_TO_KEY}')\s*:"
    )
    lines = body.split("\n")
    match_index = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    if match_index is not None:
        carriage = "\r" if lines[match_index].endswith("\r") else ""
        lines[match_index] = quoted + carriage
        spliced = "\n".join(lines)
    else:
        newline = "\r\n" if "\r\n" in body else "\n"
        prefix = body if body.endswith("\n") or not body else body + newline
        separator = newline if prefix.strip() else ""
        spliced = f"{prefix}{separator}{quoted}{newline}"
    _assert_declares(bom + spliced, url)
    return bom + spliced


def _assert_declares(text: str, url: str) -> None:
    try:
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 — CSafeLoader subclass
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Setting published_to would produce invalid YAML: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or parsed.get(PUBLISHED_TO_KEY) != url:
        raise ValueError(
            "Setting published_to did not produce the expected entry -- "
            "the file's layout is not one this can edit safely."
        )
