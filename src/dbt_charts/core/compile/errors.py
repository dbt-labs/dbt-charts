"""Compilation error types.

Stage: COMPILE
Purpose: Define error types for all compilation failures.

These errors are raised during:
- YAML parsing (ParseError, YAMLError)
- Schema validation (ValidationError)
- Reference resolution (ReferenceError)
- Jinja template rendering (JinjaError)

All errors inherit from CompilationError for easy catching.

Enhanced error messages include:
- Line numbers where errors occur
- YAML context showing the problematic snippet
- Helpful suggestions ("Did you mean 'bar'?")

Refs #94
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from dbt_charts.core.diagnostics.base import DbtChartsError

if TYPE_CHECKING:
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.diagnostics.diagnostic import Diagnostic


class CompilationError(DbtChartsError):
    """Base error for all compilation failures.

    This is the parent class for all compilation-related errors.
    Catch this to handle any compilation error.

    Attributes:
        message: Human-readable error description
        location: Optional location in source (line number, path, etc.)
        line: Optional line number in the YAML file
        column: Optional column number in the YAML file
        field_path: Optional dotted path to the field that failed
        context: Optional YAML snippet showing the error location
        suggestion: Optional helpful suggestion for fixing the error
    """

    # `line` / `column` / `field_path` class-level defaults live on
    # DbtChartsError itself (shared by every error hierarchy); only the
    # compile-specific fields need declaring here.
    location: str | None = None
    context: str | None = None
    suggestion: str | None = None

    def __init__(
        self,
        message: str,
        location: str | None = None,
        line: int | None = None,
        column: int | None = None,
        field_path: str = "",
        context: str | None = None,
        suggestion: str | None = None,
    ):
        self.message = message
        self.location = location
        self.line = line
        self.column = column
        self.field_path = field_path
        self.context = context
        self.suggestion = suggestion
        self.fields: dict[str, Any] = {}
        super().__init__(self.message)
        if self.code is None:
            from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL

            self.code = ERR_INTERNAL


class ParseError(CompilationError):
    """Error during YAML parsing.

    Raised when:
    - YAML syntax is invalid
    - YAML structure cannot be parsed

    Attributes:
        line: Line number where the error occurred
        context: YAML snippet showing the error location
        suggestion: Helpful fix suggestion

    Example:
        >>> try:
        ...     compile("invalid: yaml: content")
        ... except ParseError as e:
        ...     print(f"Parse failed at line {e.line}: {e}")
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        context: str | None = None,
        suggestion: str | None = None,
    ):
        super().__init__(
            message,
            line=line,
            column=column,
            context=context,
            suggestion=suggestion,
        )


class ValidationError(CompilationError):
    """Error during schema validation.

    Raised when:
    - Required fields are missing
    - Field values are invalid type
    - Enum values are not recognized
    - Schema constraints are violated

    Attributes:
        field_path: Path to the field that failed validation
        invalid_value: The value that caused the error
        valid_values: List of valid values (if applicable)
        line: Line number where the error occurred
        context: YAML snippet showing the error location
        suggestion: Helpful fix suggestion

    Example:
        >>> try:
        ...     compile("charts:\\n  my_chart:\\n    type: invalid_type")
        ... except ValidationError as e:
        ...     print(f"Validation failed at line {e.line}: {e}")
    """

    invalid_value: str | None = None
    valid_values: list[str] | None = None

    def __init__(
        self,
        message: str,
        location: str | None = None,
        line: int | None = None,
        context: str | None = None,
        suggestion: str | None = None,
        field_path: str | None = None,
        invalid_value: str | None = None,
        valid_values: list[str] | None = None,
    ):
        self.field_path = field_path if field_path is not None else ""
        self.invalid_value = invalid_value
        self.valid_values = valid_values

        super().__init__(
            message,
            location=location if not context else None,
            line=line,
            field_path=field_path if field_path is not None else "",
            context=context,
            suggestion=suggestion,
        )


class ReferenceError(CompilationError):
    """Error resolving a reference.

    Raised when:
    - Chart references unknown query
    - Layout references unknown chart
    - Remote reference cannot be resolved
    - Partial file not found

    Attributes:
        ref: The reference that could not be resolved
        context: Where the reference was used

    Example:
        >>> try:
        ...     compile("charts:\\n  my_chart:\\n    query: unknown_query")
        ... except ReferenceError as e:
        ...     print(f"Reference '{e.ref}' not found")
    """

    ref: str | None = None
    ref_path: tuple[str, ...] = ()

    def __init__(
        self,
        ref: str,
        context: str | None = None,
        ref_path: Sequence[str] = (),
    ):
        from dbt_charts.core.diagnostics.codes_compile import (
            ERR_UNRESOLVED_REFERENCE,
        )

        self.ref = ref
        self.ref_path = tuple(ref_path)
        ctx_suffix = f" in {context}" if context else ""
        message = ERR_UNRESOLVED_REFERENCE.message_template.format(
            ref=ref, context=ctx_suffix
        )
        super().__init__(message)
        self.code = ERR_UNRESOLVED_REFERENCE
        self.fields = {"ref": ref}
        if context:
            self.fields["context"] = context


class JinjaError(CompilationError):
    """Error during Jinja template resolution.

    Raised when:
    - Jinja syntax is invalid
    - Variable not found in template context
    - Filter not found

    Attributes:
        template: The template that caused the error (if available)

    Example:
        >>> try:
        ...     compile("queries:\\n  q: SELECT * FROM {{ undefined_var }}")
        ... except JinjaError as e:
        ...     print(f"Template error: {e}")
    """

    template: str | None = None

    def __init__(self, message: str, template: str | None = None):
        from dbt_charts.core.diagnostics.codes_compile import ERR_JINJA_ERROR

        self.template = template
        jinja_msg = ERR_JINJA_ERROR.message_template.format(message=message)
        super().__init__(jinja_msg)
        self.code = ERR_JINJA_ERROR
        self.fields = {"message": message}


class TemplateOutputTooLargeError(CompilationError):
    """A board render's cumulative Jinja-emitted output crossed the
    render-scoped `execution.max_template_output_bytes` ceiling.

    Raised by every render call site that streams through
    `compile.template.output_budget.render_with_budget()` when the open
    `template_output_budget()` scope's `TemplateOutputBudgetExceeded` fires.
    A hard error, not a truncation — see `output_budget.py`'s module
    docstring for why.
    """

    def __init__(self, *, emitted_bytes: int, ceiling: int) -> None:
        from dbt_charts.core.diagnostics.codes_compile import (
            ERR_TEMPLATE_OUTPUT_TOO_LARGE,
        )

        message = ERR_TEMPLATE_OUTPUT_TOO_LARGE.message_template.format(
            ceiling=ceiling, emitted_bytes=emitted_bytes
        )
        super().__init__(message)
        self.code = ERR_TEMPLATE_OUTPUT_TOO_LARGE
        self.fields = {"emitted_bytes": emitted_bytes, "ceiling": ceiling}


class FileValidationError(CompilationError):
    """Pydantic validation error from a YAML file other than the board being
    compiled.

    Its diagnostics are already ranged in *that* file: report them as-is, never
    re-stamp them against the compiling board.
    """

    def __init__(self, message: str, diagnostics: list[Diagnostic]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics

    @classmethod
    def from_pydantic(
        cls, message: str, error: PydanticValidationError, content: str, relpath: str
    ) -> FileValidationError:
        from dbt_charts.core.compile.parse.source_map import (
            build_source_index,
            stamp_diagnostics,
        )
        from dbt_charts.core.compile.parse.yaml_error_formatter import (
            format_validation_errors_structured,
        )

        diagnostics = format_validation_errors_structured(error, content)
        stamp_diagnostics(diagnostics, *build_source_index(content, relpath))
        return cls(message, diagnostics)
