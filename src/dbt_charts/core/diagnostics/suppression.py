"""Warning suppression: partition a list of Diagnostics into active vs suppressed.

Three ignore layers form a union — a warning is dropped if its code appears in ANY:
  1. cli_codes: caller-provided set (e.g. --ignore-warning flags).
  2. project_codes: global ignore list from dbt_charts.yml `warnings.ignore`.
  3. per_chart_codes: per-chart ignore map, keyed by chart id. Only warnings whose
     `chart` field matches the chart id are suppressed (board-level warnings with
     chart=None are never suppressed by per_chart_codes).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable

from dbt_charts.core.diagnostics.codes_retired import RETIRED_WARNING_CODES
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.diagnostics.registry import REGISTRY


def warn_retired_codes(codes: Iterable[str], *, source: str) -> None:
    """Warn once per retired code: it can no longer fire, so it suppresses nothing."""
    for code in codes:
        if code in RETIRED_WARNING_CODES:
            warnings.warn(
                f"{source}: {code} no longer exists and suppresses nothing; "
                "remove it from the ignore list.",
                stacklevel=2,
            )


def validate_suppression_codes(
    codes: Iterable[str],
    *,
    source: str,
) -> None:
    """Raise if any code isn't a registered WARN-* code — typo guard.

    A typo'd suppression entry (``warnings_ignore: [WARN-PIE-DOMINANT-SEGMEN]``,
    missing the trailing T) matches nothing in ``partition()``'s plain set
    lookup — it silently suppresses no warnings, and the author believes the
    warning is off. Call this at every point suppression codes come in from
    YAML outside the typed board fields (project ``dbt_charts.yml
    warnings.ignore``, meta.yml ``lint.ignore`` / ``lint.ignore_queries``) so
    the typo is a loud compile error instead. A board's per-chart
    ``warnings_ignore:`` and per-query ``ignore:`` are typed by their Literal
    code lists.

    Not covered: inline SQL ``-- dct:ignore <CODE>`` comments. Those are
    parsed at query-validation time (``query_validator.parse_inline_
    suppressions``), a runtime surface with a different error-handling
    contract; a typo there still silently no-ops.

    Args:
        codes: Candidate suppression codes to check.
        source: Human-readable location for the error message (e.g. "chart 'revenue'").
    """
    warning_codes = REGISTRY.codes(level="warning")
    error_codes = REGISTRY.codes(level="error")
    for code in codes:
        if code in RETIRED_WARNING_CODES:
            warn_retired_codes([code], source=source)
            continue
        if code in warning_codes:
            continue
        if code in error_codes:
            raise ValueError(
                f"{source}: cannot suppress {code!r} — it is an error code, "
                "not a warning code."
            )
        raise ValueError(
            f"{source}: unknown warning code {code!r}. "
            f"Registered warning codes: {', '.join(sorted(warning_codes))}"
        )


def partition(
    warnings: list[Diagnostic],
    cli_codes: set[str],
    project_codes: set[str],
    per_chart_codes: dict[str, set[str]],
) -> tuple[list[Diagnostic], list[Diagnostic]]:
    """Partition warnings into (active, suppressed).

    A warning is suppressed when its code is in cli_codes, project_codes, or
    (warning.chart is not None AND code is in per_chart_codes[warning.chart]).

    Args:
        warnings: Full pre-suppression list from run_all().
        cli_codes: Caller-supplied ignore set (CLI --ignore-warning flags).
        project_codes: Project-global ignore set from dbt_charts.yml.
        per_chart_codes: Chart-id → set of codes to suppress for that chart only.

    Returns:
        (active, suppressed) — both lists preserve original order.
    """
    active: list[Diagnostic] = []
    suppressed: list[Diagnostic] = []
    global_codes = cli_codes | project_codes
    for w in warnings:
        if w.code in global_codes or (
            w.chart is not None and w.code in per_chart_codes.get(w.chart, set())
        ):
            suppressed.append(w)
        else:
            active.append(w)
    return active, suppressed
