"""A project's ``locked_schema_version``: the board grammar it targets.

Validation of the lock itself, the warnings it silences, and the check that
board files still speak the locked release's syntax (``dct validate``, Cloud's
merge gate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import fragment_files
from dbt_charts.core.compile.migrations.migrations import (
    MigrationError,
    UnsupportedSchemaError,
    _board_migration_context,
    collect_migration_notices,
    migrate_yaml_text,
)
from dbt_charts.core.compile.parse.parser import load_yaml_mapping, load_yaml_text

if TYPE_CHECKING:
    from dbt_charts.core.project import Project, ProjectPath


def validate_locked_schema_version(value: str, *, source: str) -> str:
    """*value* when it names a released schema version; otherwise raise."""
    catalog, _ = _board_migration_context()
    released = [e.version for e in reversed(catalog.entries) if e.status == "RELEASED"]
    if value not in released:
        raise ValueError(
            f"{source}: locked_schema_version {value!r} is not a released schema "
            f"version; released versions: {', '.join(released)}"
        )
    return value


def warnings_newer_than_lock(lock: str | None) -> frozenset[str]:
    """Warning codes a project locked to *lock* must not see; none when unlocked."""
    if lock is None:
        return frozenset()
    catalog, _ = _board_migration_context()
    return catalog.warnings_newer_than(lock)


def locked_grammar_rejections(
    yaml_text: str, lock: str, *, ignored_keys: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Why release *lock*'s grammar rejects *yaml_text*, after migrating it up to *lock*.

    ``_schema_version`` is dropped first: frozen snapshots are closed and the
    oldest predate the key. *ignored_keys* are top-level keys another layer
    owns (meta.yml's ``lint:``); they are removed before migration so schema
    recognition never sees them.
    """
    catalog, registry = _board_migration_context()
    if ignored_keys:
        yaml_text = yaml.safe_dump(
            {
                key: value
                for key, value in load_yaml_mapping(yaml_text).items()
                if key not in ignored_keys
            },
            sort_keys=False,
        )
    with collect_migration_notices():
        capped = migrate_yaml_text(
            yaml_text, catalog=catalog, registry=registry, stop_target=lock
        )
    mapping = {
        key: value
        for key, value in load_yaml_mapping(capped).items()
        if key != "_schema_version"
    }
    grammar = catalog.grammar_for(lock)
    errors = [
        _closest_branch_error(error)
        for error in validator_for(grammar)(grammar).iter_errors(mapping)
    ]
    return tuple(
        ".".join(str(part) for part in error.absolute_path) + f": {error.message}"
        if error.absolute_path
        else error.message
        for error in errors
    )


def _closest_branch_error(error: ValidationError) -> ValidationError:
    """Descend anyOf/oneOf into the branch the value nearly matches.

    The branch with the fewest errors is the one the author meant (a bar
    chart's branch, not a KPI's); its deepest error names the failing field.
    jsonschema's ``best_match`` stops at a tie between branches, which every
    discriminated union produces.
    """
    while error.context:
        branches: dict[str | int, list[ValidationError]] = {}
        for child in error.context:
            branches.setdefault(child.relative_schema_path[0], []).append(child)
        branch = min(
            branches.values(),
            key=lambda b: (len(b), -max(len(c.absolute_path) for c in b)),
        )
        error = max(branch, key=lambda c: len(c.absolute_path))
    return error


def locked_schema_errors(
    board_path: ProjectPath, board_text: str, project: Project
) -> list[str]:
    """Messages for every file of the board (itself, its meta.yml and
    ``extends:`` files) the project's lock rejects; empty when unlocked.

    Markdown boards are not checked. The board itself, when it does not parse
    or is not recognizably board YAML, is left to the compile, which reports it.
    """
    lock = project.locked_schema_version
    if lock is None or board_path.is_markdown:
        return []
    try:
        board_mapping = load_yaml_mapping(board_text)
    except CompilationError:
        return []
    files: list[tuple[str, str, frozenset[str]]] = [
        (board_path.relpath, board_text, frozenset())
    ]  # the board first: its own compile reports what the check cannot read
    for fragment in fragment_files(board_path, board_mapping, project.directory(".")):
        files.append(
            (
                fragment.relpath,
                fragment.read_text(),
                frozenset({"lint"}) if fragment.is_meta else frozenset(),
            )
        )
    errors: list[str] = []
    for relpath, text, ignored_keys in files:
        try:
            if load_yaml_text(text, source=relpath) is None:
                continue  # an empty document is a valid empty patch
            rejections = locked_grammar_rejections(
                text, lock, ignored_keys=ignored_keys
            )
        except (UnsupportedSchemaError, CompilationError) as error:
            if relpath != board_path.relpath:
                errors.append(
                    f"{relpath}: cannot check against locked_schema_version "
                    f"{lock}: {error}"
                )
            continue
        except MigrationError as error:
            errors.append(
                f"{relpath}: cannot check against locked_schema_version {lock}: {error}"
            )
            continue
        errors.extend(
            f"{relpath} does not match locked_schema_version {lock}: {rejection}"
            for rejection in rejections
        )
    return errors
