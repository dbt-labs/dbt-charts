"""Regression coverage for the forward-ref bootstrap order in
compile/models/style/authored/_base.py.

`_base` injects authored types into `style.theme`'s module globals and
rebuilds the theme chart-style classes so forward refs (e.g.
PaginationConfig) resolve, before any per-family submodule
(area/bar/kpi/line/pie/...) runs its own `build_patch_model(FooChartStyle)`.
If that ordering regresses, the per-family patch model's nested field
annotation is built from the *unresolved* ForwardRef instead of the real
type — this pins the correct, resolved shape.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.style.authored import (
    PaginationConfig,
    TableChartStylePatch,
)


def test_pagination_forward_ref_is_resolved() -> None:
    field = TableChartStylePatch.model_fields["pagination"]
    resolved_types = set(getattr(field.annotation, "__args__", (field.annotation,)))
    assert PaginationConfig in resolved_types, (
        f"TableChartStylePatch.pagination did not resolve to PaginationConfig "
        f"(got {field.annotation!r}) — the _base forward-ref bootstrap likely ran "
        "after this family's build_patch_model()."
    )
