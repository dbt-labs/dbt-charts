"""The files one board file names: the edges of the import closure.

A pure function of the file's bytes and path, which is what lets a host memoize
it by content (``Project.import_refs``). Resolving a ref to the file it names
needs the file's folder and the project tree, so it stays with the walk
(``agent_api.import_closure``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.file_refs import RefKind
from dbt_charts.core.compile.models.refs import (
    CHART_REF_RE,
    QUERY_REF_RE,
    VAR_REF_RE,
)
from dbt_charts.core.compile.parse.parser import load_yaml_text
from dbt_charts.core.utils import YAML_LOADER

_DOTTED_REF_RES: tuple[tuple[re.Pattern[str], RefKind], ...] = (
    (QUERY_REF_RE, "queries"),
    (CHART_REF_RE, "charts"),
    (VAR_REF_RE, "variables"),
)


@dataclass(frozen=True)
class ImportRefs:
    #: ``extends:`` entries naming a file.
    extends: tuple[str, ...] = ()
    #: Every other file ref, with how it names its file.
    imports: tuple[tuple[str, RefKind], ...] = ()
    #: ``template:`` names of ``type: custom`` charts.
    template_names: tuple[str, ...] = ()
    #: ``text:`` values that call a ``glob_`` listing function.
    listing_texts: tuple[str, ...] = ()


def parse_import_refs(content: str, relpath: str) -> ImportRefs:
    """The refs of a board file's text.

    Handles ``.md`` boards via their frontmatter YAML (not the raw markdown text,
    which contains two YAML documents and raises ``ComposerError``).
    A YAML syntax error raises ``ERR-YAML-SYNTAX``; a markdown board's
    frontmatter failure raises a plain ``CompilationError``, so callers see one
    declared exception type. A document that is not a mapping names nothing.
    """
    if relpath.endswith((".md",)):
        from dbt_charts.core.compile.parse.markdown import parse_markdown_board

        try:
            content, _metadata = parse_markdown_board(content)
        except (ValueError, yaml.YAMLError) as exc:
            raise CompilationError(f"Parse error in '{relpath}': {exc}") from exc
    data = load_yaml_text(content, source=relpath, loader=YAML_LOADER)
    if not isinstance(data, dict):
        return ImportRefs()
    try:
        return ImportRefs(
            extends=_extends(data),
            imports=tuple(_extract_imports(data)),
            template_names=tuple(_custom_template_names(data)),
            listing_texts=tuple(_listing_texts(data)),
        )
    except RecursionError as exc:  # a self-referential anchor
        raise CompilationError(f"Parse error in '{relpath}': recursive YAML") from exc


def _custom_template_names(
    data: dict[str, Any],  # type-state: explicit_any — parsed YAML
) -> list[str]:
    """The ``template:`` names of every ``type: custom`` chart in the file.

    A template is read at compile, so an edit to it changes the render the
    way a board edit does; the closure is what a host keys its cache on. A
    chart sits under ``charts:``, inline in a layout list, or in a nested
    board's own ``charts:``, so the whole document is walked.
    """
    names: list[str] = []

    def walk(obj: Any) -> None:  # type-state: explicit_any — parsed YAML
        if isinstance(obj, dict):
            if obj.get("type") == "custom" and isinstance(obj.get("template"), str):
                names.append(obj["template"])
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return names


def _listing_texts(
    data: Any,  # type-state: explicit_any — parsed YAML
) -> list[str]:
    """Every ``text:`` value in the document that may call a listing function."""
    if isinstance(data, dict):
        own = data.get("text")
        texts = [own] if isinstance(own, str) and "glob_" in own else []
        return texts + [t for v in data.values() for t in _listing_texts(v)]
    if isinstance(data, list):
        return [t for v in data for t in _listing_texts(v)]
    return []


def _extract_imports(
    data: dict[str, Any],  # type-state: explicit_any — parsed YAML
) -> list[tuple[str, RefKind]]:
    """Collect every file ref but ``extends:`` (and how it names its file) in a
    board mapping."""
    refs: list[tuple[str, RefKind]] = []
    _scan_dict({k: v for k, v in data.items() if k != "extends"}, refs)
    return refs


def _extends(
    data: dict[str, Any],  # type-state: explicit_any — parsed YAML
) -> tuple[str, ...]:
    """The .yml/.yaml file refs of the top-level extends: key.

    extends: can be a scalar or a list. Built-in theme names (cream, editorial,
    etc.) do not end in .yml/.yaml and are skipped automatically.
    """
    extends = data.get("extends")
    entries = extends if isinstance(extends, list) else [extends]
    return tuple(
        entry
        for entry in entries
        if isinstance(entry, str) and entry.endswith((".yml", ".yaml"))
    )


def _dotted_ref(value: str, refs: list[tuple[str, RefKind]]) -> None:
    """Anchored grammar (models/refs.py): prose like 'style.charts.callout' is
    not a ref, and misreading it disables invalidation for the board."""
    for regex, section in _DOTTED_REF_RES:
        if regex.fullmatch(value):
            refs.append((value, section))
            return


def _scan_dict(
    obj: dict[str, Any],  # type-state: explicit_any — parsed YAML
    refs: list[tuple[str, RefKind]],
) -> None:
    ref = obj.get("ref")
    if isinstance(ref, str):
        _dotted_ref(ref, refs)
        return  # ref dict is terminal — don't recurse into its siblings
    for v in obj.values():
        if isinstance(v, str):
            # Bare-string cross-file refs: the authored form per refs.py is a
            # plain string like "_shared.yml.queries.rev", coerced to {ref: ...}
            # at Pydantic parse time. We read authored YAML, so detect the
            # bare-string form here.
            _dotted_ref(v, refs)
        elif isinstance(v, list):
            _scan_list(v, refs)
        elif isinstance(v, dict):
            _scan_dict(v, refs)


def _scan_list(
    items: list[Any],  # type-state: explicit_any — parsed YAML
    refs: list[tuple[str, RefKind]],
) -> None:
    for item in items:
        if isinstance(item, str) and item.endswith((".yml", ".yaml")):
            refs.append((item, "file"))
        elif isinstance(item, dict):
            _scan_dict(item, refs)
