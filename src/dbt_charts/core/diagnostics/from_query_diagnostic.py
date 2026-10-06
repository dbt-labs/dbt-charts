"""Adapter: QueryDiagnostic → Diagnostic.

``QueryDiagnostic.code`` is already one of the registered WARN-* query
codes (core/diagnostics/codes_query.py) — this adapter builds the message
through that code's own message_template and carries the validator's richer
``severity``/``confidence``/``detail``/``evidence`` as typed fields, rather
than concatenating them into the message string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.registry import REGISTRY, WarningCode

if TYPE_CHECKING:
    from dbt_charts.core.inspect.query_validator import QueryDiagnostic


def from_query_diagnostic(query_name: str, diag: QueryDiagnostic) -> Diagnostic:
    """Convert a query validator diagnostic into a Diagnostic.

    ``message`` carries only the templated head; ``severity``, ``confidence``,
    ``detail``, and ``evidence`` (table names and relationship info for
    fanout/join diagnostics) land in ``fields`` so consumers can render them
    without parsing a concatenated string.
    """
    wc = REGISTRY.get(diag.code)  # also validates diag.code is registered
    # QueryDiagnostic.code is a query code (all WarningCode —
    # see _QUERY_CODES in query_validator.py); narrow to reach fix_template,
    # which lives on WarningCode/ErrorCode independently, not the base.
    if not isinstance(wc, WarningCode):
        raise TypeError(
            f"{diag.code!r} is registered as an error code; query diagnostics "
            "must be warnings"
        )
    message = wc.message_template.format(query_name=query_name, message=diag.message)
    # Explicit if/else, not `diag.recommendation or wc.fix_template` — an `or`
    # would treat an empty-string recommendation the same as a missing one and
    # reads as the banned silent-fallback idiom.
    if diag.recommendation is not None:  # noqa: SIM108 — see comment above
        fix = diag.recommendation
    else:
        fix = wc.fix_template
    fields = {
        "severity": diag.severity,
        "confidence": diag.confidence,
        "detail": diag.detail,
        "evidence": list(diag.evidence),
    }
    # path ends in .sql so _ancestors() resolves to the sql: block-scalar node
    # rather than the outer <name>: container, which would collapse to the key
    # line rather than pointing into the SQL body.
    return Diagnostic.from_code(
        wc, message=message, fix=fix, path=f"queries.{query_name}.sql", fields=fields
    )
