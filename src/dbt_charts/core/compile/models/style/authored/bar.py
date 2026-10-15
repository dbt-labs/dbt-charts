"""Bar chart authored style patch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.factories import build_patch_model
from dbt_charts.core.compile.models.style.theme import BarChartStyle

# Per-family chart style patch used as a Pydantic field annotation in authored
# *Chart classes. The TYPE_CHECKING stub gives pyright a proper class identity
# while the runtime uses the dynamically-created patch model from
# build_patch_model().
if TYPE_CHECKING:

    class BarChartStylePatch(BarChartStyle):  # noqa: F811
        pass

else:
    BarChartStylePatch = build_patch_model(BarChartStyle)
