"""The ChartDataProvider seam: Executor satisfies it.

The Protocol's narrowness is a design rule, not a test: asserting which members
it does and does not declare would be introspection ceremony, which this repo
bans. That rule and its rationale live in ``chart_data_provider``'s own module
docstring; the type checkers enforce conformance below.
"""

from __future__ import annotations

from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.execute.executor import Executor


def _executor_is_a_provider(executor: Executor) -> ChartDataProvider:
    """Type-level assertion that ``Executor`` satisfies the Protocol.

    This is enforced by pyright in CI, which is stronger than any runtime
    check available here: ``issubclass`` is unusable on a Protocol with
    non-method members (``cache_hit_ats`` is a property), and ``isinstance``
    only checks attribute presence, not signatures. If ``Executor`` drifts from
    the Protocol, the type checkers fail on this line.
    """
    return executor
