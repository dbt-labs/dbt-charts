# ============================================================================
# RESOLVED TYPES (produced by resolve_board() — no lookups needed downstream)
# ============================================================================


from dataclasses import dataclass

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    ProsePlan,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.chart.resolved.callout import (
    ResolvedCalloutChart,
)
from dbt_charts.core.compile.models.chart.resolved.kpi import ResolvedKpiChart
from dbt_charts.core.compile.models.primitives import HtmlPolicy
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)
from dbt_charts.core.diagnostics import Diagnostic

_AnyResolvedChart = ResolvedChart


def _is_magnitude_colored(channels: dict[str, ResolvedStyleChannel]) -> bool:
    """True when this chart's ``color`` channel paints a quantitative value.

    ``"gradient"`` mode is always a continuous sequential/diverging ramp.
    ``"conditional"`` mode is also unsafe when it carries a continuous
    ``fallback_scale`` — a Looker-style "scale with rule override" is still a
    quantitative ramp wherever no threshold rule matches. ``quantitative_data``
    covers bare, undecorated color authoring (``color: <field>``) that IS a
    magnitude ramp by data, even though that authoring never sets mode to
    "gradient" — every cartesian family's own emitter renders exactly this
    shape as a continuous gradient legend (see
    ``ResolvedStyleChannel.quantitative_data``, set generically for every
    family by ``_flag_quantitative_color`` in ``compile/resolve/chart/
    _channels.py``). Plain rule-based conditional colors (no fallback scale),
    a genuinely categorical ``"series"``, and ``"literal"`` all stay
    eligible: rewriting them does not change what any of them mean.
    """
    color = channels.get("color")
    if color is None:
        return False
    if color.mode == "gradient" or color.quantitative_data:
        return True
    return color.mode == "conditional" and color.fallback_scale is not None


@dataclass(frozen=True)
class ChartIdentity:
    """The chart facts a rendered tile's wrapper element is built from.

    A tile's wrapper carries the identity a host addresses it by: the id and
    authored path click-to-source keys on, and the variable dependencies the
    hover-highlight and loading state query. All of it exists on the
    compiled chart, so a chart that failed to resolve can still be drawn
    with its real identity rather than placeholders — the host cannot tell
    which stage failed, and should not have to.
    """

    chart_type: str
    id: str
    source_path: str
    defined_in_other_file: bool
    query_name: str | None
    notes: str
    variable_dependencies: frozenset[str]
    magnitude_colored: bool
    # None where the resolved chart carries no canvas (KPI, callout).
    canvas: str | None

    @classmethod
    def from_resolved(cls, chart: ResolvedChart) -> "ChartIdentity":
        return cls(
            chart_type=chart.chart_type,
            id=chart.id,
            source_path=chart.source_path,
            defined_in_other_file=chart.defined_in_other_file,
            query_name=chart.query_name,
            notes=chart.notes,
            variable_dependencies=chart.variable_dependencies,
            magnitude_colored=_is_magnitude_colored(chart.resolved_channels),
            canvas=None
            if isinstance(chart, ResolvedKpiChart | ResolvedCalloutChart)
            else chart.canvas,
        )

    @classmethod
    def from_normalized(cls, chart: Chart) -> "ChartIdentity":
        """Identity for a chart that never resolved.

        ``chart.type`` is the authored family alias (``histogram`` survives
        normalization where ``donut`` does not), so this is not byte-identical
        to what a successful resolve would have stamped. It is safe for the one
        thing the value decides downstream — the ``dbt-chart-callout`` class —
        because no family alias is ever ``"callout"``.

        There is no resolved channel data on this path — resolution never
        finished — so ``magnitude_colored`` is unconditionally ``False`` and
        ``canvas`` is ``None``: an error placard has no marks to recede in the
        first place.
        """
        return cls(
            chart_type=chart.type,
            id=chart.id,
            source_path=chart.source_path,
            defined_in_other_file=chart.defined_in_other_file,
            query_name=chart.query_name,
            notes=chart.notes,
            variable_dependencies=chart.variable_dependencies,
            magnitude_colored=False,
            canvas=None,
        )


@dataclass(frozen=True)
class ChartResolveFailure:
    """A chart that could not be resolved, plus enough identity to draw it anyway."""

    diagnostic: Diagnostic
    identity: ChartIdentity


@dataclass(frozen=True)
class ResolvedLayoutItem:
    """Layout item with static dimension estimates — no executor, no data access."""

    type: str  # "chart" | "board" | "text" | "markdown" | ...
    chart: ResolvedChart | None
    # nested board is a forward reference — resolved recursively
    board: "ResolvedBoard | None"
    x: float
    y: float
    width: float
    height: float
    source_path: str = ""
    # Carry-through optional fields from LayoutItem
    details_variable: str | None = None
    details_summary: str | None = None
    details_expanded_summary: str | None = None
    notes: str | None = None
    visible: "bool | str | SingleRowBoolProbe | None" = None
    # Set instead of `chart` when the chart failed to resolve. The render walk
    # draws an error tile from it; `chart` stays None exactly as it is for any
    # non-chart item.
    chart_error: ChartResolveFailure | None = None
    # Title and body offsets from the item's cols row; zero outside one.
    title_shift: TitleShift = NO_TITLE_SHIFT


@dataclass(frozen=True)
class ResolvedLayout:
    """Layout tree with static dimension estimates.

    Dimensions come from compile-time static sizing.  The render phase applies
    data-aware sizing (executor-driven row counts and Vega render-first heights)
    on top of these estimates.
    """

    type: str  # "rows" | "cols" | "grid" | "tabs"
    items: tuple["ResolvedLayoutItem", ...]
    width: float
    height: float
    content_width: float
    content_height: float
    # Grid-specific
    columns: int | None = None
    gap: float = 0.0
    # Tabs-specific
    tab_titles: tuple[str, ...] = ()
    tab_slugs: tuple[str, ...] = ()
    tab_variable: str | None = None
    default_tab: int = 0
    tab_position: str | None = None


@dataclass(frozen=True)
class ResolvedBoard:
    """Pure resolved board — deterministic transform from Board + config.

    Produced by build_resolved_board() (data-resolved, charts) or
    build_resolved_board_static() (static, charts with empty data).
    The render layer performs data-aware sizing on top of these static estimates.

    Board-level config fields (page_padding, card_padding, card_gap) are None
    for nested boards — those values only apply to the root renderable board.
    """

    # Identity
    id: str
    title: str
    notes: str
    tags: tuple[str, ...]
    text: str
    html_policy: HtmlPolicy
    level: int  # Heading level (1 = root board, matches H{level})

    # Style (theme + board overrides already merged).
    style: ResolvedStyle

    # Baked board config constants — None for nested boards (root only).
    # config.style.frame.margin / card_padding / card_gap
    page_padding: float | None
    card_padding: float | None
    card_gap: float | None

    # Static dimension estimates (from compile-time layout tree)
    # Render phase applies data-aware sizing on top.
    width: float
    height: float

    # Layout tree with static item estimates
    layout: ResolvedLayout

    charts: dict[str, ResolvedChart]
    variables: dict[str, Variable]
    queries: dict[str, AnyQuery]
    variable_defaults: VariableValues
    # Card grid this board's own text sits on; None on a board with no text.
    prose_plan: ProsePlan | None = None

    @property
    def visible_variables(self) -> "dict[str, Variable]":
        """Variables with visible=True — available for UI controls."""
        return {k: v for k, v in self.variables.items() if v.visible}
