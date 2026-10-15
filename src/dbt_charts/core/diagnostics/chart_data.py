"""ChartDataError: chart received data that doesn't match its requirements.

Lives in core (not render) so compile-time chart-data validation can raise
it without compile depending on render. Does not subclass render's
``RenderError`` — that would put core back on top of render — but mirrors
its message-formatting and error-code-defaulting behavior directly against
``DbtChartsError``.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.base import DbtChartsError


class ChartDataError(DbtChartsError):
    """Chart received data that doesn't match its requirements.

    Raised when:
    - KPI chart receives more than 1 row
    - Chart references a column not present in the data
    - Data shape doesn't match chart type expectations
    """

    chart_id: str | None = None

    def __init__(self, message: str, chart_id: str | None = None) -> None:
        self.message = message
        self.chart_id = chart_id
        # `fields` is typed on the DbtChartsError base (dict[str, Any]); no
        # redundant local annotation needed for this bare assignment.
        self.fields = {}
        super().__init__(self._format_message())
        if self.code is None:
            from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL

            self.code = ERR_INTERNAL

    def _format_message(self) -> str:
        """Format error message with optional chart_id (mirrors RenderError's element)."""
        if self.chart_id:
            return f"{self.message} (element: {self.chart_id})"
        return self.message
