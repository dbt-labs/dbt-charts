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
from dbt_charts.core.utils import BoardLoader, UniqueKeyLoader

# This module only sees raw content, so the file is unknown here; the compiler
# rebuilds the error with the real name, as it does for an empty document.
UNNAMED_YAML_SOURCE = "the given YAML content"

# The breaks libyaml counts a line on; a mark's ``line`` indexes this split.
_YAML_BREAK_RE = re.compile(r"\r\n|[\n\r\x85\u2028\u2029]")


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
        raise yaml_syntax_error(e, content, source=UNNAMED_YAML_SOURCE) from e
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
        raise yaml_syntax_error(e, content, source=UNNAMED_YAML_SOURCE) from e

    if parsed_data is None:
        raise _empty_document_error()
    if not isinstance(parsed_data, dict):
        raise ParseError(f"YAML must be a mapping, got {type(parsed_data).__name__}")

    return parsed_data


def yaml_syntax_error(
    e: yaml.YAMLError, content: str, *, source: str, note: str = ""
) -> ParseError:
    """The ``ERR-YAML-SYNTAX`` error for a PyYAML failure in ``content``.

    ``source`` names the file in the message and ``note`` adds a closing
    sentence after the quoted line. The position is read from
    ``problem_mark`` (the actual error position, e.g. where an unterminated flow
    sequence was found to be missing its close). It is only present on
    ``yaml.error.MarkedYAMLError``, not the plain ``yaml.YAMLError`` base, so
    this is a genuine type-narrowing check on a third-party exception.
    """
    from dbt_charts.core.diagnostics.codes_compile import ERR_YAML_SYNTAX

    if not (isinstance(e, yaml.error.MarkedYAMLError) and e.problem_mark is not None):
        return ParseError.from_code(
            ERR_YAML_SYNTAX, source=source, where="", problem=str(e), quoted_line=""
        )
    line = e.problem_mark.line + 1
    column = e.problem_mark.column + 1
    lines = _YAML_BREAK_RE.split(content)
    offending = lines[line - 1].strip() if line <= len(lines) else ""
    return ParseError.from_code(
        ERR_YAML_SYNTAX,
        source=source,
        where=f" at line {line}, column {column}",
        problem=": ".join(part for part in (e.context, e.problem) if part),
        quoted_line=(f"\n    {offending}" if offending else "")
        + (f"\n{note}" if note else ""),
        line=line,
        column=column,
    )


_MAPPING_VALUES_PROBLEM = "mapping values are not allowed in this context"
_MAX_QUOTED_LINES = 4
# `<indent>[- ]<key>: <value>`, the one line shape a quoted copy can repair.
_KEY_VALUE_LINE_RE = re.compile(r"^(\s*(?:-\s+)*[A-Za-z_][\w-]*:[ \t]+)(\S.*)$")
_COLON_SPACE_RE = re.compile(r":(?:\s|$)")


def quote_colon_typos(content: str) -> tuple[str, list[ParseError]] | None:
    """A copy of ``content`` with unquoted colon-space values double-quoted.

    The typo is a plain scalar holding ``: `` (``title: Largest in 1950:
    rank``), which YAML reads as a nested mapping. Returns the copy plus one
    ``ERR-YAML-SYNTAX`` per quoted line (at most ``_MAX_QUOTED_LINES``), or
    ``None`` when any failure is not such a line or the copy still fails. The
    caller validates the copy for its diagnostics only; the authored text is
    never rewritten.
    """
    # Every break is rewritten as "\n" in the copy: line numbers and columns
    # are unchanged, and the repaired line never carries a stray "\r".
    lines = _YAML_BREAK_RE.split(content)
    errors: list[ParseError] = []
    while True:
        text = "\n".join(lines)
        try:
            UniqueKeyLoader(text).get_single_node()
        except yaml.YAMLError as e:
            # A positionless error (a control character's ReaderError) is not
            # the typo; the caller reports its plain syntax error.
            if (
                not isinstance(e, yaml.error.MarkedYAMLError)
                or e.problem != _MAPPING_VALUES_PROBLEM
                or e.problem_mark is None
                or len(errors) == _MAX_QUOTED_LINES
            ):
                return None
            index = e.problem_mark.line
            match = _KEY_VALUE_LINE_RE.match(lines[index])
            if match is None or not _COLON_SPACE_RE.search(match[2]):
                return None
            note = (
                "The rest of this report comes from the file with line "
                f"{index + 1} quoted."
            )
            errors.append(
                yaml_syntax_error(e, text, source=UNNAMED_YAML_SOURCE, note=note)
            )
            escaped = match[2].replace("\\", "\\\\").replace('"', '\\"')
            lines[index] = f'{match[1]}"{escaped}"'
        else:
            return (text, errors) if errors else None


def load_yaml_text(
    content: str,
    *,
    source: str,
    loader: type[BoardLoader] = UniqueKeyLoader,
) -> Any:  # type-state: explicit_any — raw parsed YAML of any shape
    """Parse a non-board authored file (meta, ``extends:`` fragment, import).

    Raises ``ParseError`` (``ERR-YAML-SYNTAX``) naming ``source``. The position
    stays in the message only: the diagnostic is stamped against the board being
    compiled, and a line number in another file would underline the wrong line.
    """
    try:
        return yaml.load(content, Loader=loader)  # noqa: S506 — repo loaders only
    except yaml.YAMLError as e:
        err = yaml_syntax_error(e, content, source=source)
        err.line = err.column = None
        raise err from e


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


_SQL_PREFIX_RE = re.compile(
    r"^\s*(SELECT|WITH|PRAGMA|INSERT|UPDATE|DELETE|CREATE)\b",
    re.IGNORECASE,
)


def looks_like_sql(s: str) -> bool:
    """Return True when a string starts like a raw SQL statement."""
    return bool(_SQL_PREFIX_RE.match(s))
