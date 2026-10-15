"""Rendering error types.

Stage: RENDER
Purpose: Define error types for rendering failures.

These errors are raised during:
- General rendering failures (RenderError)
- Format conversion (FormatError)
- Pre-render required-variable validation (MissingRequiredVariablesError)

All errors inherit from RenderError → DbtChartsError for easy catching.

Note: Many render errors are displayed IN the output rather than thrown,
so users see helpful error messages in the rendered board.
"""

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.diagnostics.base import DbtChartsError


class RenderError(DbtChartsError):
    """Base error for all rendering failures.

    This is the parent class for all rendering-related errors.
    Catch this to handle any rendering error.

    Attributes:
        message: Human-readable error description
        element: Element that failed to render (if applicable)
    """

    # Class-level None default so `from_code`-constructed instances (which
    # bypass __init__) still satisfy `e.element` access.
    element: str | None = None

    def __init__(self, message: str, element: str | None = None) -> None:
        self.message = message
        self.element = element
        self.fields: dict[str, Any] = {}
        super().__init__(self._format_message())
        if self.code is None:
            from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL

            self.code = ERR_INTERNAL

    def _format_message(self) -> str:
        """Format error message with optional element."""
        if self.element:
            return f"{self.message} (element: {self.element})"
        return self.message


class FormatError(RenderError):
    """Error during format conversion.

    Raised when:
    - Unknown format requested
    - SVG to PNG/PDF conversion fails
    - HTML template error

    Always construct via `FormatError.from_code(...)` with a registered
    diagnostic code. There is no dedicated plain-message constructor; a bare
    `FormatError("...")` falls back to the inherited `RenderError.__init__`,
    which stamps `ERR_INTERNAL` and leaves `.format` at its `None` default.

    Example:
        >>> try:
        ...     render(board, executor, format="unknown")
        ... except FormatError as e:
        ...     print(f"Format error: {e}")
    """

    format: str | None = None


@dataclass
class MissingVariable:
    """Metadata for a single required variable that was not provided at render time."""

    key: str
    label: str | None
    notes: str | None
    input_type: str | None


class MissingRequiredVariablesError(RenderError):
    """Raised before any query executes when required variables have no value.

    Carries a structured list so callers can inspect which variables were
    missing without parsing the error string.
    """

    def __init__(self, missing: list[MissingVariable]) -> None:
        from dbt_charts.core.diagnostics import ERR_INPUT_INVALID

        self.missing = missing
        self.code = ERR_INPUT_INVALID
        descriptions = ", ".join(
            f"{mv.key} ({mv.label})" if mv.label else mv.key for mv in missing
        )
        super().__init__(f"Missing required variables: {descriptions}")
        query_example = "&".join(f"{mv.key}=..." for mv in missing)
        self.hint = (
            "Open the dashboard with the required query params in the URL "
            f"(for example `?{query_example}`), or set `default:` on the "
            "variable in the board YAML."
        )
        # Populate fields so to_diagnostic() carries per-variable metadata.
        self.fields = {
            "missing": [
                {
                    "key": mv.key,
                    "label": mv.label,
                    "notes": mv.notes,
                    "input_type": mv.input_type,
                }
                for mv in missing
            ]
        }
