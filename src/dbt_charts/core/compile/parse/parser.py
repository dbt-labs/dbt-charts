"""YAML parsing module.

Stage: COMPILE (Step 1 of 4)
Purpose: Parse YAML strings into AuthoredBoard input types.

Entry Points:
    - parse_yaml(content: str) -> AuthoredBoard

Inputs:
    - YAML string (board definition)

Outputs:
    - AuthoredBoard (input type with optional fields)

Dependencies:
    - yaml (PyYAML)
    - .types (AuthoredBoard)

Errors:
    - ParseError: Invalid YAML syntax (with line numbers, context, suggestions)

See also:
    - compile/validate/dispatch.py for the next step

Refs #94
"""

import re
from typing import Any

import yaml

from dbt_charts.core.compile.errors import ParseError
from dbt_charts.core.compile.models.board.authored import (
    AUTHORED_BOARD_ADAPTER,
    AuthoredBoard,
)
from dbt_charts.core.utils import UniqueKeyLoader


def parse_yaml(content: str) -> AuthoredBoard:
    """Parse YAML content into a AuthoredBoard object.

    Stage: COMPILE (Step 1 of 4: Parsing)

    This is the first step of compilation. It converts a raw YAML string
    into a structured AuthoredBoard object. Only basic syntax validation happens
    here - schema validation is the next step.

    Enhanced error messages include:
    - Line numbers where errors occur
    - YAML context showing the problematic snippet
    - Helpful suggestions ("Did you mean?")

    Args:
        content: Raw YAML string to parse

    Returns:
        AuthoredBoard object with parsed structure

    Raises:
        ParseError: If YAML syntax is invalid or parsing fails

    Example:
        >>> yaml_content = '''
        ... title: My dbt charts
        ... queries:
        ...   sales: SELECT * FROM sales
        ... charts:
        ...   revenue:
        ...     query: sales
        ...     type: line
        ... rows:
        ...   - revenue
        ... '''
        >>> board = parse_yaml(yaml_content)
        >>> board.title
        'My dbt charts'
    """
    # Step 1a–1b: YAML string → mapping
    parsed_data = load_yaml_mapping(content)
    # Step 1c–1d: mapping → AuthoredBoard
    return parse_mapping(parsed_data, content)


def load_yaml_mapping(content: str) -> dict[str, Any]:
    """Parse a YAML string into a top-level mapping.

    Raises ``ParseError`` (with line context) on syntax errors, empty
    documents, or a non-mapping top-level value.
    """
    return mapping_from_node(compose_yaml(content), content)


def compose_yaml(content: str) -> yaml.Node:
    """Scan ``content`` once into its node tree.

    The one scan a board pays. The mapping (``mapping_from_node``) and the
    source map (``build_source_index_from_node``) are both walks over the tree
    this returns, so a caller that needs both composes here and hands the node
    to each rather than scanning the text twice.

    Raises ``ParseError`` (with line context) on a syntax error, or on an
    empty document — there is no tree to hand back.
    """
    # The loader is driven directly: this is the mapping's own scan, not a
    # second `yaml.compose` of the same text beside the source map's.
    try:
        node = UniqueKeyLoader(content).get_single_node()
    except yaml.YAMLError as e:
        raise _parse_error(e, content) from e
    if node is None:
        raise _empty_document_error()
    return node


def mapping_from_node(
    node: yaml.Node, content: str
) -> dict[str, Any]:  # type-state: explicit_any — raw YAML mapping
    """Construct the top-level mapping from a composed node tree.

    Construction is where ``UniqueKeyLoader`` refuses a duplicate key, so the
    check holds for a tree composed elsewhere exactly as it does for
    ``load_yaml_mapping``'s own. ``content`` only enriches the error with its
    line context.
    """
    # `deep=True` builds every nested node before returning, which is where
    # `construct_mapping`'s duplicate-key check runs for each of them. It
    # also flattens merge keys into the tree in place, so a caller that
    # walks the node for positions does so before this.
    try:
        parsed_data = UniqueKeyLoader("").construct_object(node, deep=True)
    except yaml.YAMLError as e:
        raise _parse_error(e, content) from e

    if parsed_data is None:
        raise _empty_document_error()
    if not isinstance(parsed_data, dict):
        raise ParseError(f"YAML must be a mapping, got {type(parsed_data).__name__}")

    return parsed_data


def _parse_error(e: yaml.YAMLError, content: str) -> ParseError:
    # problem_mark is the actual error position (e.g. where an unterminated
    # flow sequence was found to be missing its close); it's only present
    # on yaml.error.MarkedYAMLError, not the plain yaml.YAMLError base, so
    # this is a genuine type-narrowing check on a third-party exception —
    # not a guaranteed field we're being defensive about.
    problem_mark = e.problem_mark if isinstance(e, yaml.error.MarkedYAMLError) else None
    line_num = problem_mark.line + 1 if problem_mark is not None else None
    column_num = problem_mark.column + 1 if problem_mark is not None else None
    context = _get_yaml_context_for_error(content, line_num) if line_num else None
    return ParseError(
        f"Invalid YAML syntax: {e}",
        line=line_num,
        column=column_num,
        context=context,
        suggestion=_get_yaml_parse_suggestion(str(e)),
    )


def _empty_document_error() -> ParseError:
    """The one ``ParseError`` raised for a comment-only/blank/null document.

    Stamped with its own registered code, not left to default to ERR-INTERNAL
    (``compile/errors.py``'s fallback for an unmigrated raise site) — this one
    *is* migrated, it just can't name the file yet: ``compiler.py``'s
    ``_parse_error_to_diagnostics`` rebuilds the message with the file name
    once it knows one (``parse_yaml``/``load_yaml_mapping`` only ever see raw
    content, never a board's identity).
    """
    from dbt_charts.core.diagnostics.codes_compile import ERR_EMPTY_YAML_DOCUMENT

    err = ParseError("Empty YAML document")
    err.code = ERR_EMPTY_YAML_DOCUMENT
    return err


def parse_mapping(parsed_data: dict[str, Any], content: str = "") -> AuthoredBoard:
    """Convert an already-parsed YAML mapping into an ``AuthoredBoard``.

    Shared by ``parse_yaml`` (string entry) and the meta-merge path in
    ``compile_file`` (which builds the mapping by deep-merging meta under the
    board, so there is no source string to re-parse). ``content`` is only used to
    enrich validation errors with line context; pass the original board text when
    available, else leave empty.

    Raises ``ParseError`` on schema-validation failure.
    """
    from dbt_charts.core.compile.migrations import prepare_board_mapping

    parsed_data = prepare_board_mapping(parsed_data)

    from pydantic import ValidationError as PydanticValidationError

    try:
        return AUTHORED_BOARD_ADAPTER.validate_python(parsed_data)
    except PydanticValidationError as e:
        # compiler._parse_error_to_diagnostics re-reads e.__cause__ as a
        # PydanticValidationError and routes through format_validation_errors_structured
        # for the compiler path. Some callers (dct migrate, registered_views/expander.py)
        # stringify this ParseError directly, so build the message from e.errors()
        # rather than str(e) -- the latter's header names the internal
        # BeforeValidator-wrapped adapter type, not AuthoredBoard.
        error_summary = "; ".join(err["msg"] for err in e.errors())
        raise ParseError(f"Board schema validation failed: {error_summary}") from e
    except (TypeError, ValueError) as e:
        raise ParseError(f"Failed to parse board structure: {e}") from e


def _get_yaml_context_for_error(
    content: str,
    line_num: int,
    context_lines: int = 2,
) -> str:
    """Get YAML context around an error line.

    Args:
        content: Full YAML content
        line_num: Line number of the error (1-indexed)
        context_lines: Number of lines to show before/after

    Returns:
        Formatted context string
    """
    from dbt_charts.core.compile.parse.yaml_error_formatter import get_yaml_context

    return get_yaml_context(content, line_num, context_lines)


def _get_yaml_parse_suggestion(error_msg: str) -> str | None:
    """Get a helpful suggestion for a YAML parse error.

    Args:
        error_msg: The error message

    Returns:
        Suggestion string or None
    """
    error_lower = error_msg.lower()

    if "indent" in error_lower:
        return "💡 Check your indentation - YAML requires consistent spacing (typically 2 spaces)"
    elif "expected" in error_lower and "block" in error_lower:
        return "💡 This often happens with incorrect indentation or missing colons"
    elif "mapping" in error_lower:
        return "💡 Check for missing colons after key names or incorrect nesting"
    elif "duplicate" in error_lower:
        return "💡 You have duplicate keys - each key name must be unique at the same level"
    elif "found character" in error_lower:
        return (
            "💡 Check for special characters that need quoting, or invalid YAML syntax"
        )

    return None


_SQL_PREFIX_RE = re.compile(
    r"^\s*(SELECT|WITH|PRAGMA|INSERT|UPDATE|DELETE|CREATE)\b",
    re.IGNORECASE,
)


def looks_like_sql(s: str) -> bool:
    """Return True when a string starts like a raw SQL statement."""
    return bool(_SQL_PREFIX_RE.match(s))
