"""Core validation utilities for dbt charts.

This module provides core validation functions that can be used by any
dbt charts client (CLI, playground, API, etc.) to validate board YAML
before execution.

All validation logic is in core - clients just provide their adapter registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile import compile as dbt_charts_compile
from dbt_charts.core.utils import (
    format_error_summary,
    normalize_data_for_json as normalize_data_for_json,  # re-exported
)

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory


def validate_yaml(
    yaml_content: str,
    base_dir: ProjectDirectory | None = None,
) -> dict[str, Any]:
    """Validate YAML content and return errors/warnings.

    This is core validation logic - it only checks compilation, not execution.

    Args:
        yaml_content: YAML string to validate
        base_dir: ProjectDirectory anchor for the board file (resolves cross-file refs).

    Returns:
        Dict with:
            - success: bool - whether validation passed
            - errors: List[str] - compilation errors
            - warnings: List[dict] - compilation warnings (Diagnostic model_dump)
            - error_summary: str - human-readable error summary
            - has_board: bool - whether a board was successfully created
    """
    try:
        result = dbt_charts_compile(yaml_content, base_dir=base_dir)

        errors = [e.message for e in result.errors] if result.errors else []
        warnings = [w.model_dump() for w in result.warnings]

        error_summary = format_error_summary(errors) if errors else ""

        return {
            "success": result.success,
            "errors": errors,
            "warnings": warnings,
            "error_summary": error_summary,
            "has_board": result.success,
        }
    except Exception as e:  # noqa: BLE001 — boundary: any compile exception → dict
        return {
            "success": False,
            "errors": [f"Validation failed: {str(e)}"],
            "warnings": [],
            "error_summary": f"Validation exception: {str(e)}",
            "has_board": False,
        }
