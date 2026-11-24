"""Detector: WARN_QUERY_RESULT_TRUNCATED — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rules:
  chart_id in ctx.chart_truncations  (attributed — chart owns the query)
  query_name in ctx.unattributed_truncations  (board-level — upstream queries
      demand-executed by cache-ref composition; no chart owns them directly)

Both dicts are sparse — only truncated queries appear.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_QUERY_RESULT_TRUNCATED, Diagnostic
from dbt_charts.core.execute.cache_backend import TruncationInfo
from dbt_charts.core.render.warnings.base import WarningContext

# A result built from {{ queries.X.cache }} upstreams keeps a row count that says
# nothing about the cut (a count(*) keeps one row), and a warm hit cannot tell
# whether the cut was its own or an upstream's, so this names what it reads.
_COMPOSED_MESSAGE_TEMPLATE = (
    "{subject}: query {query_name!r} is built from {upstreams}, and its data "
    "was cut by the {reason} limit, so the values shown are incomplete."
)


def _message(subject: str, truncation: TruncationInfo) -> str:
    if truncation.upstreams:
        return _COMPOSED_MESSAGE_TEMPLATE.format(
            subject=subject,
            query_name=truncation.query_name,
            upstreams=", ".join(repr(name) for name in truncation.upstreams),
            reason=truncation.reason,
        )
    return WARN_QUERY_RESULT_TRUNCATED.message_template.format(
        subject=subject,
        reason=truncation.reason,
        kept_row_count=truncation.kept_row_count,
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per truncated query (chart-attributed or board-level)."""
    warnings: list[Diagnostic] = []

    for chart_id, truncation in ctx.chart_truncations.items():
        warnings.append(
            Diagnostic.from_code(
                WARN_QUERY_RESULT_TRUNCATED,
                chart=chart_id,
                path=f"charts.{chart_id}.query",
                field=None,
                message=_message(f"Chart {chart_id!r}", truncation),
                fix=WARN_QUERY_RESULT_TRUNCATED.fix_template,
            )
        )

    for query_name, truncation in ctx.unattributed_truncations.items():
        # No chart owns this query directly (e.g. it was demand-executed as a
        # cache-ref upstream). Emit with chart=None so the author can identify
        # which upstream query was affected.
        warnings.append(
            Diagnostic.from_code(
                WARN_QUERY_RESULT_TRUNCATED,
                chart=None,
                path=f"queries.{query_name}",
                field=None,
                message=_message(f"Query {query_name!r}", truncation),
                fix=WARN_QUERY_RESULT_TRUNCATED.fix_template,
            )
        )

    return warnings
