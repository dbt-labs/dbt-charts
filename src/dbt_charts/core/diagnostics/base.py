"""Base error type shared by every dbt charts error hierarchy.

Lives in core (below compile/execute/render) so all three stages can raise
a common ``DbtChartsError`` without any of them depending on another.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

if TYPE_CHECKING:
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic
    from dbt_charts.core.diagnostics.registry import ErrorCode


class DbtChartsError(Exception):
    """Base error for all dbt charts errors."""

    code: ErrorCode | None = None
    fields: dict[str, Any]
    # Class-level defaults so every subclass (CompilationError, RenderError,
    # ExecutionError, ...) satisfies `e.line` / `e.column` / `e.field_path`
    # access without crashing, whether constructed via `__init__` or
    # `from_code` (which bypasses `__init__` entirely). This lets
    # build_diagnostic() read them as plain attributes across the whole
    # heterogeneous error hierarchy instead of duck-typing with getattr.
    # `field_path` is only ever set by the compile family, but the default
    # belongs here for the same reason: "" means "no path resolved".
    line: int | None = None
    column: int | None = None
    field_path: str = ""
    # Foreign text (driver/subprocess output), never put in the authored
    # message. Log-only; see from_code's detail param.
    detail: str | None = None

    @classmethod
    def from_code(
        cls,
        ec: ErrorCode,
        *,
        detail: str | None = None,
        **fields: Any,  # type-state: explicit_any — registered-code fields are runtime-heterogeneous by construction, per this method's own docstring
    ) -> Self:
        """Construct an instance carrying a registry ErrorCode + structured fields.

        Bypasses the subclass __init__ (and its `_format_message` decoration);
        the rendered message is exactly `ec.message_template.format(**fields)`,
        except that a `Sequence[str]` value (a raise site writes
        `available=sorted(...)`, not a joined string — `hint_generator` needs
        the raw candidates) is joined with ", " for display, and an empty one
        renders "none configured" rather than a bare "". Inlined as a
        comprehension rather than a helper function deliberately: `fields`
        holds runtime-heterogeneous values already covered by `**fields: Any`
        above, and a separate function would need its own `Any` parameter
        annotation to type-check — the core type-state gate blocks growing
        that count, so this stays inline where the type is already inferred
        from `fields` rather than newly spelled out.
        Reserved field names (`code`, `message`, `fields`) are skipped so the
        registry-set values can't be clobbered by a caller-supplied field.

        `detail` is foreign text, kept out of `.fields` so a host that
        persists or displays `.fields` (Cloud's chart_errors) never carries it
        along — it's stored only on `.detail`. It is still visible to
        `ec.message_template` under the same `{detail}` key, for a code whose
        authored template embeds it directly.
        """
        display_fields = {
            key: (
                (", ".join(value) if value else "none configured")
                if (
                    isinstance(value, Sequence)
                    and not isinstance(value, str)
                    and all(isinstance(item, str) for item in value)
                )
                else value
            )
            for key, value in fields.items()
        }
        if detail is not None:
            display_fields["detail"] = detail
        message = ec.message_template.format(**display_fields)
        inst: Self = cls.__new__(cls)
        Exception.__init__(inst, message)
        inst.message = message  # type: ignore[attr-defined]
        inst.code = ec
        inst.fields = dict(fields)
        inst.detail = detail
        # Mirror fields onto typed subclass attributes so callers reading
        # e.chart_id / e.chart_type / e.format / e.element get the same value
        # whether the error was raised via legacy __init__ or from_code.
        # Class-level None defaults on every subclass attr make setattr safe.
        _reserved = {"code", "message", "fields"}
        for key, value in fields.items():
            if key in _reserved:
                continue
            setattr(inst, key, value)
        return inst

    def to_diagnostic(self, file: str | None = None) -> Diagnostic:
        from dbt_charts.core.diagnostics.diagnostic import build_diagnostic

        return build_diagnostic(self, file=file)
