"""Table family resolved style slice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import (
    FormatAliases,
    ResolvedFontStyle,
    ResolvedFormat,
    ResolvedScaleTarget,
)
from dbt_charts.core.compile.models.style.authored import PaginationConfig
from dbt_charts.core.compile.models.style.authored.table import (
    ColumnScaleConfig,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.style.theme import TableChartStyle, TitleStyle
from dbt_charts.core.text.format_d3 import Notation
from dbt_charts.core.text.numeral_scale import SuffixMode


class ResolvedTableStyle(BaseModel):
    """Table family style slice projected from the style cascade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: TableChartStyle = Field(
        description="Resolved table chart style from the cascade.",
    )
    title: TitleStyle = Field(
        description="Resolved chart title style from the cascade.",
    )
    formats: FormatAliases | None = Field(
        description="Resolved format aliases available to table cells.",
    )
    title_font: ResolvedFontStyle = Field(
        description="Baked title font (size/weight/family/…) from the width tier.",
    )
    pagination: PaginationConfig | None = Field(
        description=(
            "Board-level pagination, chart-local-override-aware (a chart-local "
            "top-level `style.pagination` patch updates this but not "
            "`table.pagination`, which stays theme-shaped) — the same value "
            "`build_chart_style_context(board_style, chart).pagination` produces. "
            "Sizing code reads this instead of re-running the cascade."
        ),
    )


class ResolvedColumnScaleConfig(ColumnScaleConfig):
    """ColumnScaleConfig with resolved scale targets.

    Constructed by _with_resolved_scale_stops in compile/resolve/chart/_table.py
    after the WCAG-safe palette bake runs. background/color carry
    ResolvedScaleTarget so resolved_stops survives pydantic serialization
    (board artifact round-trip). The base ColumnScaleConfig is used for
    pre-bake authored input; this resolved variant is only constructed at
    resolve time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    background: ResolvedScaleTarget | None = Field(
        default=None, description="Resolved background color mapping for this column."
    )
    color: ResolvedScaleTarget | None = Field(
        default=None, description="Resolved text color mapping for this column."
    )


class ResolvedColumnSharedScale(BaseModel):
    """The baked "this table column ships a shared SI/compact magnitude" decision.

    Mirrors ``ResolvedRulerAxis``'s field shape and rationale (see that
    dataclass's docstring in ``resolved/_base.py``) -- a table column with a
    resolved shared magnitude behaves like an axis ruler's tick ladder: one
    exponent, one suffix mode, one digit spec, read by every consumer instead
    of re-derived. ``None`` on ``ResolvedTableColumnConfig.shared_scale`` (not
    a bool-plus-fields shape) means the column doesn't compact under a shared
    magnitude.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    exponent: int = Field(
        description="SI magnitude tier (10**exponent) this column's numbers are scaled by."
    )
    mode: SuffixMode = Field(
        description=(
            "Whether the magnitude suffix appears once, on the anchor row, "
            "or on every row."
        )
    )
    digit_spec: str = Field(
        description="The fixed-point number format the scaled values render through."
    )

    @property
    def register(self) -> Notation:
        """The register this scale speaks: analytic for ANCHOR, narrative for REPEAT."""
        return "analytic" if self.mode is SuffixMode.ANCHOR else "narrative"


class ResolvedTableColumnConfig(TableColumnConfig):
    """TableColumnConfig with a resolved scale field.

    Holds a ResolvedColumnScaleConfig so the gradient resolved_stops survive
    pydantic serialization. Constructed by _with_resolved_scale_stops and
    fill_table_column_defaults during the table resolve pass.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: ResolvedFormat | None = Field(
        default=None,
        description="Resolved value format; None when the column authors none.",
    )
    scale: ResolvedColumnScaleConfig | None = Field(
        default=None,
        description="Resolved continuous color mapping configuration for this column.",
    )
    # Indexed by fractional depth (0..precision+1); baked at resolve time from
    # the column's trim-enabled fixed-point format and the numeric cell font.
    # Empty for non-~f specs, precision=0, or columns whose actual data rows
    # do not show mixed fractional depth (no alignment needed).
    decimal_pad_table: tuple[str, ...] = Field(
        default=(),
        description=(
            "Trailing-pad strings for decimal-point alignment. "
            "Non-empty only for trim-enabled fixed-point columns with mixed fractional depth."
        ),
    )
    shared_scale: ResolvedColumnSharedScale | None = Field(
        default=None,
        description=(
            "Resolved shared SI/compact magnitude for this column, or None "
            "when its values don't share a common magnitude."
        ),
    )


__all__ = [
    "ResolvedColumnScaleConfig",
    "ResolvedColumnSharedScale",
    "ResolvedTableColumnConfig",
    "ResolvedTableStyle",
]
