"""Normalized board types — output representation.

Stage: NORMALIZE (Output) / EXECUTE and RENDER (Input)
Purpose: Layout, Board, and core document types.
"""

from typing import Any, Literal, TypeGuard

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.primitives import HtmlPolicy
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)

# ============================================================================
# TYPE ALIASES
# ============================================================================


# Resolved variable values (not Variable objects)
# Used for Jinja template resolution - values can be any Python type
VariableValues = dict[str, Any]


# ============================================================================
# UNIFIED LAYOUT
# ============================================================================


class LayoutItem(BaseModel):
    """A single item in a layout.

    Represents either a chart or a nested board in the layout.
    The unified structure makes iteration simple:

        for item in layout.items:
            if item.type == "chart":
                render_chart(item.chart)
            else:
                render_board(item.board)

    Attributes:
        type: Either "chart" or "board"
        chart: Chart if type == "chart"
        board: Board if type == "board"

    Grid-specific (optional):
        row, col: Grid position (0-based)
        row_span, col_span: Grid span (in grid units)

    Calculated dimensions (set by sizing module):
        width_fraction: What fraction of parent width (0.0-1.0)
        width: Pixel width
        height: Pixel height
        x: X position in pixels
        y: Y position in pixels
    """

    type: Literal["chart", "board"] = Field(
        description="Item type: 'chart' or 'board'."
    )
    chart: "Chart | None" = Field(
        default=None, description="Compiled chart, present when type == 'chart'."
    )
    board: "Board | None" = Field(
        default=None, description="Compiled board, present when type == 'board'."
    )
    source_path: str = Field(
        default="",
        description=(
            "Absolute dotted YAML path to this layout item, e.g. rows.0 or "
            "rows.0.cols.1 — the same spelling as a source-map key, so a "
            "diagnostic naming it resolves to a line. Empty when the item has "
            "no authored coordinates in this file (a foreach expansion, or a "
            "subtree imported from another board file)."
        ),
    )

    # Grid positioning (optional) - 0-based, in grid units
    row: int | None = Field(
        default=None, description="Grid row position (0-based; grid layouts only)."
    )
    col: int | None = Field(
        default=None, description="Grid column position (0-based; grid layouts only)."
    )
    row_span: int | None = Field(
        default=None, description="Number of rows to span (grid layouts only)."
    )
    col_span: int | None = Field(
        default=None, description="Number of columns to span (grid layouts only)."
    )

    # Authored dimensions (from YAML, e.g. "30%" or "200px").
    # user_width: width fraction/px authored on a cols item.
    # layout_height: slot height authored on a rows/wrapper item; resolved to
    #   LayoutItem.height (float px) by the sizing pass.
    user_width: str | None = Field(
        default=None,
        description="User-specified width from YAML (e.g., '30%' or '200px').",
    )
    layout_height: str | None = Field(
        default=None,
        description="Layout-wrapper height from YAML (e.g., '30%' or '200px'); "
        "drives slot allocation in the sizing pass.",
    )

    # Calculated dimensions (set by sizing)
    width_fraction: float = Field(
        default=1.0, description="Fraction of parent width (0.0–1.0); set by sizing."
    )
    width: float = Field(default=0.0, description="Pixel width; set by sizing.")
    height: float = Field(default=0.0, description="Pixel height; set by sizing.")
    x: float = Field(default=0.0, description="X position in pixels; set by sizing.")
    y: float = Field(default=0.0, description="Y position in pixels; set by sizing.")

    # Layout visibility — evaluated at render time; None means always visible.
    # Accepts: bool (static), str (variable name or Jinja expression),
    # or SingleRowBoolProbe (single-row boolean probe).
    visible: bool | str | SingleRowBoolProbe | None = Field(
        default=None,
        description=(
            "Visibility condition for this layout item. "
            "None/True: always shown. False: always hidden. "
            "str: variable name (truthy check) or Jinja boolean expression. "
            "{query, column}: single-row boolean probe (same contract as variables.disabled)."
        ),
    )

    # Details (collapsible section) metadata
    details_variable: str | None = Field(
        default=None,
        description="Variable name controlling expanded state (collapsible sections).",
    )
    details_summary: str | None = Field(
        default=None,
        description="Summary text shown when collapsed (collapsible sections).",
    )
    details_expanded_summary: str | None = Field(
        default=None,
        description="Summary text shown when expanded (collapsible sections).",
    )
    notes: str | None = Field(
        default=None,
        description=(
            "Optional metadata for AI search. Emitted into the SVG DOM as a "
            "data-layout-notes attribute; never painted as visible pixels."
        ),
    )

    # Render-ready sizing (calculated during normalization/sizing)
    calculated_width: float | None = Field(
        default=None,
        description="Calculated pixel width based on 1200px default; set by sizing.",
    )
    calculated_height: float | None = Field(
        default=None, description="Calculated pixel height; set by sizing."
    )
    aspect_ratio: float | None = Field(
        default=None,
        description="Width/height ratio for responsive rendering; set by sizing.",
    )

    model_config = ConfigDict(extra="forbid")


class Layout(BaseModel):
    """Unified layout structure.

    Stage: COMPILE output

    All layout types (rows, cols, grid, tabs) are represented with this
    unified structure, making downstream processing simpler.

    The type field indicates the original layout type for rendering
    (e.g., rows render vertically, cols horizontally).

    Attributes:
        type: Layout type (rows, cols, grid, tabs)
        items: List of LayoutItem (charts or nested boards)

    Grid-specific:
        columns: Number of columns in grid

    Tabs-specific:
        tab_titles: Titles for each tab
        default_tab: Index of default active tab

    Calculated:
        width, height: Total dimensions (set by sizing)
        content_width, content_height: Inner dimensions after padding/margin

    Example:
        >>> layout = compiled_board.layout
        >>> print(f"Layout type: {layout.type}")
        >>> for item in layout.items:
        ...     print(f"  - {item.type}: {item.chart.id if item.chart else item.board.id}")
    """

    type: LayoutType = Field(description="Layout type (rows, cols, grid, tabs).")
    items: list[LayoutItem] = Field(
        default_factory=list,
        description="List of layout items (charts or nested boards).",
    )

    # Grid-specific
    columns: int | None = Field(
        default=None, description="Number of columns for grid layouts."
    )

    # Tabs-specific
    tab_titles: list[str] | None = Field(
        default=None, description="Titles for each tab (tabs layout only)."
    )
    tab_slugs: list[str] | None = Field(
        default=None,
        description="Slugified titles used as URL values (tabs layout only).",
    )
    tab_variable: str | None = Field(
        default=None,
        description="Variable name controlling the active tab (tabs layout only).",
    )
    default_tab: int | None = Field(
        default=None, description="Index of the default active tab (tabs layout only)."
    )
    tab_position: Literal["top", "left"] | None = Field(
        default=None,
        description="Tab bar position: 'top' or 'left' (tabs layout only).",
    )

    # Calculated dimensions (outer dimensions including padding/margin)
    width: float = Field(
        default=0.0, description="Total calculated pixel width; set by sizing."
    )
    height: float = Field(
        default=0.0, description="Total calculated pixel height; set by sizing."
    )

    # Calculated content dimensions (inner dimensions after padding/margin)
    content_width: float = Field(
        default=0.0,
        description="Inner pixel width after padding/margin; set by sizing.",
    )
    content_height: float = Field(
        default=0.0,
        description="Inner pixel height after padding/margin; set by sizing.",
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# COMPILED BOARD
# ============================================================================


class Board(BaseModel):
    """Compiled board ready for execution and rendering.

    Stage: COMPILE output / EXECUTE and RENDER input

    This is the main output of compilation. It contains:
    - Resolved definitions (variables, queries, charts)
    - Unified layout structure
    - Calculated dimensions
    - Applied defaults and metadata

    Guarantees:
        - id: Always set
        - title: Always set (empty string if not provided)
        - layout: Always present (unified structure)
        - All charts resolved to Chart objects
        - All queries resolved to query objects

    Attributes:
        id: Unique identifier for this board
        title: Display title
        notes: Optional metadata for AI search (not painted; Cloud shows it
            in the dashboard-card hover overlay)
        variables: Variable definitions (Dict[str, Variable])
        queries: Compiled queries (Dict[str, AnyQuery])
        charts: Compiled charts (Dict[str, Chart])
        layout: Unified layout structure
        variable_defaults: Resolved default values for variables
        style: Board styling
    Example:
        >>> result = compile(yaml_content)
        >>> board = result.board
        >>> print(board.id)        # "sales-board"
        >>> print(board.title)     # "Sales Board"
        >>> print(board.layout.type)  # "rows"
        >>> for item in board.layout.items:
        ...     chart = item.chart
        ...     print(f"Chart: {chart.id}, Query: {chart.query_name}")
    """

    id: str = Field(description="Unique identifier for this board.")
    title: str = Field(
        default="", description="Display title. Empty string if not provided."
    )
    notes: str = Field(
        default="",
        description=(
            "Optional metadata for AI search/context. Empty string if not provided. "
            "Never appears in the rendered board, but Cloud shows it in the "
            "dashboard-card hover overlay on the project/home listing."
        ),
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags for categorization and search."
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Additional URLs that redirect to this board's canonical file-path URL. "
            "Each entry is absolute (leading /), trailing-slash-canonical, "
            "percent-decoded."
        ),
    )
    text: str = Field(
        default="", description="Markdown text content for text-only boards."
    )
    html_policy: HtmlPolicy = Field(
        default="none",
        description=(
            "HTML rendering policy for the board's body text. "
            "See docs/contributing/markdown-html-policy.md for tier definitions."
        ),
    )

    # Definitions
    variables: dict[str, Variable] = Field(
        default_factory=dict,
        description="Local variable definitions for UI controls.",
    )
    queries: dict[str, AnyQuery] = Field(
        default_factory=dict,
        description="Compiled query objects by name.",
    )
    charts: dict[str, Chart] = Field(
        default_factory=dict,
        description="Compiled chart objects by chart id.",
    )

    # Source configurations (for resolving named source references)
    sources: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Source configurations for resolving named source references (e.g., {'profiles': {'type': 'duckdb'}}).",
    )

    # Unified layout
    layout: Layout = Field(
        description="Unified layout structure containing all charts and nested boards.",
    )

    # Pre-computed variable defaults from variable_registry
    variable_defaults: VariableValues = Field(
        default_factory=dict,
        description="Pre-computed default values for all variables; only set on root board.",
    )

    # Global variable registry (only set on root board, contains all variables from entire tree)
    variable_registry: dict[str, Variable] | None = Field(
        default=None,
        description="All variables from the entire board tree; only set on root board.",
    )

    # Vega-Lite theme (e.g., "clarity", "stark", "neon")
    theme: str | None = Field(
        default=None,
        description="Theme name (e.g., 'clarity', 'paper', 'vivid'). Inherited by nested boards.",
    )
    # Authored style patch — None when the board has no style: block.
    authored_style: StylePatch | None = Field(
        default=None,
        description="Authored board style patch (background, padding, border, etc.).",
    )
    # Board-scoped resolved style — authoritative, produced at compile time.
    resolved_style: ResolvedStyle = Field(
        description="Board-scoped resolved style; produced at compile time. Render reads this instead of re-merging patches.",
    )
    # Board-scoped chart style cascade context — compiler working state
    # (sparse axis overlays, patch sentinels, palette/role token bindings, the
    # pre-inherit tree), produced alongside resolved_style by the same
    # cascade. Used only by runtime chart resolution (execute orchestration,
    # sizing, the support_table-attachment axis-offset step) — never by
    # ResolvedBoard or any mechanical render API.
    chart_style_context: ChartStyleContext = Field(
        description="Board-scoped chart style cascade context for runtime chart resolution.",
    )

    def placed_charts(self) -> list[Chart]:
        """Every chart the layout tree places, in layout order."""
        charts: list[Chart] = []
        for item in self.layout.items:
            if item.chart is not None:
                charts.append(item.chart)
            elif item.board is not None:
                charts.extend(item.board.placed_charts())
        return charts

    @property
    def visible_variables(self) -> "dict[str, Variable]":
        """Return variables with visible=True (available for UI controls)."""
        return {k: v for k, v in self.variables.items() if v.visible}

    # Card gap toggle (from board YAML)
    card_gap: bool = Field(
        default=False,
        description="When True, adds gap between cards and adjusts page margin.",
    )

    # Auto-link: synthesize detail-page links for table charts with no explicit link:
    auto_link: bool = Field(
        default=False,
        description=(
            "When True, table charts with no explicit link: automatically link each "
            "row to its canonical /data/<source>/<schema>/<table>/detail/ page."
        ),
    )

    # Semantic heading level: count of titled ancestors (not structural nesting depth).
    # A titled root board is level=1; a titled board nested under a bare wrapper is
    # still level=2 (only one titled ancestor). Bare wrappers without a title do not
    # advance the counter. Required — every Board is constructed by the normalizer (or
    # by a call site that derives level from parent_level), so a missing value is a
    # bug, not a default. Can be overridden via style.title.level.
    level: int = Field(
        description=(
            "Heading level for this board's title (matches H{level}; root board = 1 "
            "when titled, 0 when untitled). Computed from titled-ancestor count; can "
            "be overridden via style.title.level."
        ),
    )

    # Compilation metadata
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Compilation metadata (warnings, diagnostics, source info) populated by the normalizer.",
    )

    def set_theme(self, theme: str | None) -> None:
        """Change this board's theme and re-cascade resolved_style in-place.

        Every nested board is also re-cascaded because any field this board
        explicitly authored propagates down to its children (and, called on
        the root, any field an ancestor of a nested board authored keeps
        propagating through it too).

        Always go through this method to mutate ``board.theme`` — callers
        that write ``board.theme = …`` directly will leave ``resolved_style``
        stale, and the render layer trusts ``resolved_style`` without
        re-checking.
        """
        # Lazy import to avoid a compile/normalize ↔ compile/models cycle.
        from dbt_charts.core.compile.normalize.dispatch import sync_board_resolved_style

        self.theme = theme
        sync_board_resolved_style(self)

    model_config = ConfigDict(extra="forbid")


# Resolve forward references (Chart and Board are now in scope)
LayoutItem.model_rebuild()
Layout.model_rebuild()
Board.model_rebuild()


# ============================================================================
# TYPE GUARD
# ============================================================================


def is_board(item: Any) -> TypeGuard[Board]:
    """Type guard for Board.

    After compilation, all boards are Board objects.
    Use this for type narrowing in conditional blocks.

    Args:
        item: Item to check

    Returns:
        True if item is a Board

    Example:
        >>> for item in layout.items:
        ...     if item.board and is_board(item.board):
        ...         # Type checker knows item.board is Board
        ...         print(item.board.layout.type)
    """
    return isinstance(item, Board)
