"""Type stub for the PyO3 extension `dbt_charts._engine` (crate at libs/chart-svg/python)."""

from typing import Any, TypedDict

class RenderDiagnostic(TypedDict):
    code: str
    params: dict[str, Any]

class RenderDiagnosticError(ValueError):
    code: str
    params: dict[str, Any]

def render_chart(
    payload_json: str,
) -> tuple[str, list[RenderDiagnostic], float | None]: ...
def measure_table_natural_width(payload_json: str) -> float | None: ...
def table_title_geometry(payload_json: str) -> tuple[float, float]: ...
def font_loads() -> int: ...
def check_template(source: str) -> None: ...
def custom_child() -> None: ...
