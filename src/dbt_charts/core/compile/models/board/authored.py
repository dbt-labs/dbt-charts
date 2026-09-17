"""Authored board types — YAML input representation.

Stage: COMPILE (Input)
Purpose: Types that map directly to the YAML board schema.

Contains the top-level AuthoredBoard and its layout sub-shapes, plus
LayoutType for board layout variants.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

from dbt_charts.core.aliases import normalize_alias_url
from dbt_charts.core.compile.models.cache import CachePatch
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.models.markers import (
    Content,
    DisplayText,
    Extends,
    Markdown,
    Merge,
    SchemaSugar,
    Strategy,
)
from dbt_charts.core.compile.models.primitives import (
    HtmlPolicy,
    IncrementalValue,
    validate_incremental_value,
)
from dbt_charts.core.compile.models.query.authored import AuthoredQuery
from dbt_charts.core.compile.models.refs import (
    CHART_REF_PATTERN_STR,
    VAR_REF_PATTERN_STR,
    ChartRef,
    QueryRef,
    VariableRef,
    coerce_ref_string,
    normalize_query_value,
    ref_or_inline,
)
from dbt_charts.core.compile.models.schema_names import ThemeName
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)


def _var_discriminator(v: object) -> str:
    return ref_or_inline(v, VariableRef)


def _query_discriminator(v: object) -> str:
    return ref_or_inline(v, QueryRef)


def _chart_discriminator(v: object) -> str:
    return ref_or_inline(v, ChartRef)


# Each "@ref" arm carries its own BeforeValidator + json_schema_input_type: the
# string-to-{"ref": ...} coercion used to live as a model_validator(mode="before")
# on the CrossFileRef subclass itself (refs.py), invisible to JSON Schema.
# Declaring it here, on the annotation, is what schema/introspection.py reads back
# — the migrator's projected schema widens for free instead of needing a
# hand-maintained copy (renderers/json_schema.py, renderers/vscode_schema.py).
VariableOrRef = Annotated[
    Annotated[Variable, Tag("@inline")]
    | Annotated[
        VariableRef,
        BeforeValidator(
            coerce_ref_string,
            json_schema_input_type=VAR_REF_PATTERN_STR | VariableRef,
        ),
        Tag("@ref"),
    ],
    Discriminator(_var_discriminator),
]
# queries: additionally needs normalize_query_value's whole-value coercion (bare
# SQL vs. bare ref string vs. dict-with-inferred-type) ahead of discrimination —
# QueryRef's own "@ref" arm never actually sees a raw string, since
# normalize_query_value already turns a ref-shaped string into {"ref": ...}
# before the union validates.
QueryOrRef = Annotated[
    Annotated[
        Annotated[AuthoredQuery, Tag("@inline")] | Annotated[QueryRef, Tag("@ref")],
        Discriminator(_query_discriminator),
    ],
    BeforeValidator(
        normalize_query_value,
        json_schema_input_type=str | AuthoredQuery | QueryRef,
    ),
]
ChartOrRef = Annotated[
    Annotated[AuthoredChart, Tag("@inline")]
    | Annotated[
        ChartRef,
        BeforeValidator(
            coerce_ref_string,
            json_schema_input_type=CHART_REF_PATTERN_STR | ChartRef,
        ),
        Tag("@ref"),
    ],
    Discriminator(_chart_discriminator),
]

# ============================================================================
# LAYOUT SUB-SHAPES
# ============================================================================

# Both spellings: a pre-rename board still authors `description:` here, and
# the retired form predates the rename either way, so a `notes:` sibling must
# also match -- `v.keys() <= _CHARTREF_KEYS` would otherwise drop out for
# `- chart: c1 / notes: x` and fall through to a confusing "Unknown field
# 'chart'" error instead of this form's clear message.
_CHARTREF_KEYS = frozenset(
    {"chart", "width", "height", "description", "notes", "visible"}
)
_CHARTREF_MSG = (
    "Layout item uses removed `chart:` reference form. "
    "Use a bare chart name (`- chart_name`) or a nested board wrapper:\n"
    "  - height: 600\n"
    "    rows:\n"
    "      - chart_name"
)


def _reject_chartref_dict(v: Any) -> Any:
    if (
        isinstance(v, dict)
        and isinstance(v.get("chart"), str)
        and v.keys() <= _CHARTREF_KEYS
    ):
        raise ValueError(_CHARTREF_MSG)
    return v


def _check_layout_list(v: Any) -> Any:
    if not isinstance(v, list):
        return v
    for item in v:
        _reject_chartref_dict(item)
    return v


def _check_layout_item(v: Any) -> Any:
    return _reject_chartref_dict(v)


class GridItem(BaseModel):
    """Grid layout item with position and span.

    Uses grid terminology (not pixels):
        col: Column position (0-indexed)
        row: Row position (0-indexed)
        col_span: Number of columns to span (default 1)
        row_span: Number of rows to span (default 1)
        width: Alias for col_span (more intuitive)
        height: Alias for row_span (more intuitive)

    Example:
        - item: my_chart
          col: 0
          row: 0
          width: 12      # Takes half of 24-column grid (alias for col_span)
          height: 2      # Spans 2 rows (alias for row_span)
    """

    model_config = ConfigDict(extra="forbid")

    item: Annotated[
        str | AuthoredBoardInput | AuthoredChart | dict[str, AuthoredChart],
        BeforeValidator(_check_layout_item),
    ] = Field(
        description="Chart name or inline chart/board definition to place in this grid cell."
    )
    col: int | None = Field(
        default=None, description="Column position (0-indexed). Auto-placed if omitted."
    )
    row: int | None = Field(
        default=None, description="Row position (0-indexed). Auto-placed if omitted."
    )
    col_span: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Number of columns to span (width in grid units). Zero or a "
            "positive number; 0 is treated as unset and falls back to "
            "width, then to 1."
        ),
    )
    row_span: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Number of rows to span (height in grid units). Zero or a "
            "positive number; 0 is treated as unset and falls back to "
            "height, then to 1."
        ),
    )
    width: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Alias for col_span (more intuitive name). Zero or a positive "
            "number; used only when col_span is unset or 0, and 0 here "
            "also falls back to 1."
        ),
    )
    height: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Alias for row_span (more intuitive name). Zero or a positive "
            "number; used only when row_span is unset or 0, and 0 here "
            "also falls back to 1."
        ),
    )
    notes: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description=(
            "Optional metadata for AI search. Emitted into the SVG DOM as "
            "a data-layout-notes attribute; never painted as visible "
            "pixels."
        ),
    )


class GridLayout(BaseModel):
    """Grid layout configuration."""

    model_config = ConfigDict(extra="forbid")

    columns: int = Field(
        default=24,
        gt=0,
        description="Number of grid columns (default: 24). Positive number only.",
    )
    items: list[GridItem] = Field(
        description="Cells of this grid, each pairing content with its placement."
    )


class TabItem(BaseModel):
    """Tab layout item."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, DisplayText()] = Field(
        description="Tab label displayed in the tab bar."
    )
    icon: str | None = Field(
        default=None,
        description="Optional icon shown in the tab (e.g., emoji or icon name).",
    )
    notes: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description=(
            "Optional metadata for AI search. Emitted into the SVG DOM as "
            "a data-layout-notes attribute; never painted as visible "
            "pixels."
        ),
    )
    text: Annotated[str | None, Markdown()] = Field(
        default=None, description="Markdown text content shown in this tab."
    )
    style: StylePatch | None = Field(
        default=None, description="Appearance overrides for this tab's content area."
    )

    # Layout fields (tab can contain nested layouts)
    rows: Annotated[
        list[str | AuthoredBoardInput | AuthoredChart | dict[str, AuthoredChart]]
        | None,
        BeforeValidator(_check_layout_list),
    ] = Field(default=None, description="Vertical stack layout for this tab's content.")
    cols: Annotated[
        list[str | AuthoredBoardInput | AuthoredChart | dict[str, AuthoredChart]]
        | None,
        BeforeValidator(_check_layout_list),
    ] = Field(default=None, description="Horizontal layout for this tab's content.")
    grid: GridLayout | None = Field(
        default=None, description="CSS-grid layout for this tab's content."
    )
    tabs: TabLayout | None = Field(
        default=None, description="A further set of tabs opening inside this one."
    )


class TabLayout(BaseModel):
    """Tab layout configuration.

    The `id` field controls the variable name and URL param for tab selection.
    If not provided, auto-generates as 'tab', 'tab_1', etc.

    Example YAML:
        tabs:
          id: view              # → URL param ?view=overview
          default: overview
          items:
            - title: Overview
              rows: [kpi_row]
            - title: Details
              rows: [detail_table]
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None,
        description="Variable name and URL param base for tab selection (auto-generated if omitted).",
    )
    position: Literal["top", "left"] = Field(
        default="top", description="Which edge the tab bar sits on (top or left)."
    )
    default: str | None = Field(
        default=None,
        description="Title of the tab opened on load; the first tab if omitted.",
    )
    items: list[TabItem] = Field(description="Tabs in display order.")


# ============================================================================
# BOARD (TOP-LEVEL)
# ============================================================================


class BoardDetails(BaseModel):
    """Collapsible section metadata for a board.

    Authored as a block or string shorthand:

        details: "Show more"                        # str shorthand
        details:
          summary: "Show more"
          expanded_title: "Hide"
          expanded: false
    """

    model_config = ConfigDict(extra="forbid")

    summary: Annotated[str, DisplayText()] = Field(
        description="Label shown when the section is collapsed."
    )
    expanded_title: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Label shown when the section is expanded. Defaults to summary.",
    )
    expanded: bool = Field(
        default=False, description="Whether the section is open by default."
    )


def _coerce_details(v: Any) -> Any:
    """Coerce str → BoardDetails dict for the BeforeValidator on AuthoredBoard.details."""
    if isinstance(v, str):
        return {"summary": v}
    return v


def desugar_theme(
    data: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    """Desugar ``theme: X`` → ``extends: X`` (single-write: theme removed).

    ``theme:`` is authoring sugar for ``extends:``. Having both is an error.

    Sugar at *parse* time, which is downstream of schema migration — so
    ``theme:`` is a key the migration recognizer sees like any other, not a
    spelling of ``extends:`` that it can look through.

    Declared on the model's own ``Annotated`` input wrapper (``AuthoredBoardInput``,
    below; ``BoardPatchInput`` in ``patch.py``). Both wrappers share this one
    function so the two input surfaces (a whole board, a board fragment) can
    never desugar ``theme:`` differently.

    Note: ``normalized.Board`` still carries a ``theme`` field + ``set_theme``
    method — a live parallel theme-name channel that re-cascades
    ``resolved_style`` when the theme is switched after compile (see
    ``normalize/dispatch.py``; read at serve time in ``core/serve/server.py``).  The
    Phase-3 cutover to ``resolve_board`` deletes the ``theme`` field, folding it
    into the single ``extends`` path.
    """
    if not isinstance(data, dict) or "theme" not in data:
        return data
    theme_val = data.get("theme")
    extends_val = data.get("extends")
    if extends_val is not None and theme_val is not None:
        raise ValueError(
            "Cannot specify both 'theme:' and 'extends:'. "
            "'theme:' is sugar for 'extends:'; use one or the other."
        )
    data = dict(data)
    if theme_val is not None:
        data["extends"] = theme_val
    del data["theme"]  # always strip theme: (even null — it is not a field)
    return data


class AuthoredBoard(BaseModel):
    """AuthoredBoard definition from YAML.

    This is the top-level input type representing a complete board.
    A board contains definitions (variables, queries, charts) and a layout.

    Example YAML:
        title: Sales Board
        notes: Overview of sales metrics

        source: my_postgres  # Default source for all queries

        variables:
          date_range:
            input: daterange
            default: ["2024-01-01", "2024-12-31"]

        queries:
          sales: SELECT * FROM sales WHERE date BETWEEN ...

        charts:
          revenue:
            query: sales
            type: line
            x: date
            y: amount

        rows:
          - revenue

    Layout:
        Exactly one layout type should be present (rows, cols, grid, or tabs).
        Layout items can be:
        - Chart name (string reference)
        - Inline chart definition (dict with query and type)
        - Nested board (dict with layout keys)

    Source:
        Optional source shorthand: ``source: my_db`` sets the default connection
        for all queries. Inheritable via meta.yml cascade.
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Merge(Strategy.OVERRIDE), DisplayText()] = Field(
        default=None, description="Heading shown at the top of the board."
    )
    notes: Annotated[str | None, Merge(Strategy.OVERRIDE), DisplayText()] = Field(
        default=None,
        description="Prose summary of what this board covers; read by AI search and board listings.",
    )
    tags: Annotated[list[str] | None, Merge(Strategy.APPEND, nested=Strategy.CHILD)] = (
        Field(default=None, description="Keywords for grouping and searching boards.")
    )
    aliases: list[str] | None = (
        Field(  # no Merge marker -- identity field, validate-absent in extends/meta lane
            default=None,
            description=(
                "Additional URLs that redirect to this board's canonical file-path URL. "
                "Each entry must be absolute (leading /). Requests to these URLs are "
                "redirected (302) to the board's real path, query string preserved. "
                "Valid on .yml, .yaml, .md, and folder index.* boards."
            ),
        )
    )
    # alias="_schema_version" so YAML authors see a leading underscore marking
    # this as dct migrate-written, not hand-authored (Python code uses
    # schema_version; Pydantic rejects a leading underscore on a field name).
    schema_version: str | None = (
        Field(  # no Merge marker -- identity field, validate-absent in extends/meta lane
            default=None,
            alias="_schema_version",
            description=(
                "The latest released dbt charts YAML schema version this file was "
                "last migrated to, written by `dct migrate` only -- never hand-author "
                "this. Informational: nothing reads it back when your board loads, "
                "and it is not a validated guarantee about the file's actual grammar "
                "(a hand-edit after migration can make it stale). YAML key: "
                "_schema_version."
            ),
        )
    )

    text: Annotated[str | None, Merge(Strategy.OVERRIDE), Content(), Markdown()] = (
        Field(default=None, description="Markdown text content for text-only sections.")
    )
    html_policy: Annotated[HtmlPolicy, Merge(Strategy.OVERRIDE)] = Field(
        default="none",
        description=(
            "HTML rendering policy for the board's body text. One of: "
            '"none" (default): HTML is escaped and rendered as plain markdown; '
            '"safe-subset": reserved for a parser-checked allowlist (not yet '
            "enforced; currently renders as none); "
            '"trusted-raw": raw HTML via foreignObject. TRUSTED-CONTENT ONLY: '
            "this is NOT a security sandbox. <script>/event-handlers are stripped "
            "as a best-effort guard, not a guarantee. Enable only on first-party "
            "boards you fully control."
        ),
    )

    # Default source name for all queries; inheritable via meta.yml cascade.
    source: Annotated[
        str | None,
        Merge(Strategy.OVERRIDE),
    ] = Field(
        default=None,
        description="Default source name for all queries in this board. Inheritable via meta.yml cascade.",
    )

    # The dashboard's own cache layer, between source and query in the cascade:
    # every query this board declares inherits it and may refine it. Not
    # Optional — a CachePatch with no fields set already means "authors
    # nothing, inherit everything", so a None state would be a second spelling
    # of the same thing (cascade placeholder, per models/AGENTS.md).
    #
    # No Merge marker: DEEP by type inference, matching `merge_cache_layers`
    # field-for-field. Overriding the meta.yml block whole would make
    # `cache: true` — the one spelling that sets nothing but the switch — drop
    # the directory's ttl for the project root's.
    cache: CachePatch = Field(
        default_factory=CachePatch,
        description=(
            "Cache policy for every query in this dashboard, e.g. cache: 1h: "
            "queries inherit it and may refine it; cache: false opts the whole "
            "dashboard out. Inheritable via the meta.yml cascade."
        ),
    )
    incremental: Annotated[IncrementalValue, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "Default watermark column for incremental refresh: queries in "
            "this board fetch only new rows since the last run and merge "
            "them with the cached result, keyed on this column. Queries "
            "inherit this value and may override it with their own "
            "incremental: setting. Set to false on a nested board to opt out "
            "of a parent's incremental setting."
        ),
    )

    # Scoped definitions
    variables: Annotated[
        dict[str, VariableOrRef] | None, Merge(Strategy.BY_KEY, nested=Strategy.CHILD)
    ] = Field(
        default_factory=dict,
        description="Named inputs that parameterize queries; each renders as a control unless it sets visible: false.",
    )
    queries: Annotated[
        dict[str, QueryOrRef] | None, Merge(Strategy.BY_KEY), Content()
    ] = Field(
        default_factory=dict,
        description="Named result sets the charts draw from (SQL, CSV, HTTP, and more).",
    )
    charts: Annotated[
        dict[str, ChartOrRef] | None, Merge(Strategy.BY_KEY), Content()
    ] = Field(
        default_factory=dict,
        description=(
            "Named chart definitions. When no explicit layout is present, "
            "charts render as an implicit row layout in authored order."
        ),
    )

    # Layout (exactly one should be present)
    rows: Annotated[
        list[str | AuthoredBoardInput | AuthoredChart | dict[str, AuthoredChart]]
        | None,
        BeforeValidator(_check_layout_list),
        Merge(Strategy.APPEND, nested=Strategy.CHILD),
        Content(),
    ] = Field(
        default=None,
        description="Vertical stack layout: list of chart names or inline chart/board definitions.",
    )
    cols: Annotated[
        list[str | AuthoredBoardInput | AuthoredChart | dict[str, AuthoredChart]]
        | None,
        BeforeValidator(_check_layout_list),
        Merge(Strategy.APPEND, nested=Strategy.CHILD),
        Content(),
    ] = Field(
        default=None,
        description="Horizontal layout: list of chart names or inline chart/board definitions.",
    )
    grid: Annotated[
        GridLayout | None, Merge(Strategy.DEEP, nested=Strategy.CHILD), Content()
    ] = Field(
        default=None,
        description="CSS-grid style layout with explicit row/column placement.",
    )
    tabs: Annotated[
        TabLayout | None, Merge(Strategy.DEEP, nested=Strategy.CHILD), Content()
    ] = Field(
        default=None,
        description="Tabbed navigation layout where each tab contains its own layout.",
    )

    # Card gap toggle: when true, adds gap between cards.
    card_gap: Annotated[bool, Merge(Strategy.OVERRIDE)] = Field(
        default=False,
        description="When True, adds gap between cards. Default: cards are edge-to-edge (0 gap).",
    )

    # Focus mode
    chart_focus: Annotated[str | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description="Render only this named chart with its dependent variables (useful for embedding or SVG export).",
    )

    # Collapsible section (details)
    details: Annotated[
        BoardDetails | None, BeforeValidator(_coerce_details), Merge(Strategy.OVERRIDE)
    ] = Field(
        default=None,
        description=(
            "Collapsible section metadata. "
            "String shorthand: details: 'text' → BoardDetails(summary='text'). "
            "Block form: details: {summary: ..., expanded_title: ..., expanded: false}."
        ),
    )

    # Styling & dimensions (when nested)
    id: str | None = (
        Field(  # no Merge marker -- identity field, validate-absent in extends/meta lane
            default=None,
            description="Explicit ID for this board. Auto-generated from filename if omitted.",
        )
    )
    style: Annotated[StylePatch | None, Merge(Strategy.DEEP)] = Field(
        default=None,
        description="Appearance overrides for this board (background, border, and more). Most fields this board or an ancestor board explicitly authors cascade to nested child boards. Per-board fields (frame, layout, gap, margin, padding): a nested board that authors any style of its own resolves these against its own theme, never an ancestor's. Root-board-only fields (footer, timestamp): a nested board never draws its own footer or timestamp line, so these never reach it either.",
    )
    width: Annotated[str | int | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description="Width when nested (e.g., '50%', '400px', or an integer in pixels). "
        "On the root board there is no parent to place it into, so it instead sets "
        "the board's own width (equivalent to 'style.frame.width'); percentages "
        "are rejected there since there's nothing to size relative to.",
    )
    height: Annotated[str | int | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description="Height when nested (e.g., '300px' or an integer in pixels).",
    )
    visible: Annotated[
        bool | str | SingleRowBoolProbe | None, Merge(Strategy.OVERRIDE)
    ] = Field(
        default=None,
        description=(
            "Controls whether this layout item is rendered. "
            "Accepts a bool, variable name, Jinja expression, "
            "or {query, column} probe."
        ),
    )

    # Inheritance chain — names and/or relative paths of boards this board extends.
    # The resolution engine folds the chain (low→high priority) before merging.
    # `override` here: a child's extends replaces the parent's; the parent's own
    # extends is already folded in during chain resolution, never re-merged.
    extends: Annotated[
        ThemeName | str | list[str] | None, Merge(Strategy.OVERRIDE), Extends()
    ] = Field(
        default=None,
        description="Board name(s) or relative path(s) this board inherits from, low to high priority. A built-in theme name resolves it directly.",
    )

    # Auto-link: synthesize a detail-page link for table charts when no explicit
    # link: is set. Default off — opt in at the board level (per-board override).
    # Explicit link: always wins; link: false suppresses per chart (an explicit
    # null is indistinguishable from omission, so only false opts out).
    # dct serve-scoped for Phase 1; project-level opt-in is deferred.
    auto_link: bool = Field(
        default=False,
        description=(
            "When True, table charts with no explicit link: automatically link each "
            "row to its canonical /data/<source>/<schema>/<table>/detail/ page. "
            "Default off. An explicit link: always wins; set link: false on a "
            "chart to suppress its automatic link."
        ),
    )

    @field_validator("incremental", mode="before")
    @classmethod
    def _validate_incremental(
        cls,
        v: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
    ) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
        return validate_incremental_value(v)

    @model_validator(mode="before")
    @classmethod
    def reject_board_key(cls, data: Any) -> Any:
        """Reject 'board' as a top-level key."""
        if isinstance(data, dict) and "board" in data:
            raise ValueError(
                "'board:' is not valid. Board properties (title, rows, queries, etc.) "
                "should be at the top level of the YAML file, not nested under 'board:'."
            )
        return data

    @model_validator(mode="after")
    def validate_aliases(self) -> AuthoredBoard:
        """Every alias must be absolute, per ``aliases``' own description.

        Checked through ``normalize_alias_url`` so the authored surface and the
        redirect index cannot disagree about what a well-formed alias is; the
        normalized value is discarded, since the board stores what the author
        wrote.
        """
        if self.aliases is None:
            return self
        for alias in self.aliases:
            normalize_alias_url(alias)
        return self

    @model_validator(mode="after")
    def validate_layout(self) -> AuthoredBoard:
        """Ensure at least one layout type or content is defined."""
        layouts = [self.rows, self.cols, self.grid, self.tabs]
        defined = [layout for layout in layouts if layout is not None]
        if len(defined) > 1:
            raise ValueError(
                "Board can only have one layout type (rows, cols, grid, or tabs)"
            )
        if (
            len(defined) == 0
            and self.text is None
            and not self.title
            and not self.notes
            and not self.charts
        ):
            raise ValueError(
                "Board must have at least one layout type, text, title, notes, or chart"
            )
        return self

    def get_default_source(self) -> str | None:
        """Return the default source name, or None."""
        return self.source


# The authoring-input entry point for AuthoredBoard: desugars theme: -> extends:
# ahead of validation and declares the sugar key's shape for schema
# introspection (SchemaSugar; see its docstring in markers.py). Must be defined
# here -- after the AuthoredBoard class body, before its model_rebuild() call
# below -- rather than near the VariableOrRef/QueryOrRef/ChartOrRef block
# earlier in this file: `AuthoredBoardInput = Annotated[AuthoredBoard, ...]` is
# a runtime name lookup (unlike those aliases, which wrap types already
# defined), so it would NameError if written before `class AuthoredBoard`
# exists. The 5 recursive field annotations above (GridItem.item,
# TabItem.rows/cols, AuthoredBoard.rows/cols) reference AuthoredBoardInput
# even though they're written earlier in the file -- `from __future__ import
# annotations` makes them lazy strings, resolved only when model_rebuild()
# runs, by which point this name exists.
AuthoredBoardInput = Annotated[
    AuthoredBoard,
    BeforeValidator(desugar_theme),
    SchemaSugar(
        name="theme",
        type_repr="str",
        enum_values=tuple(sorted(get_args(ThemeName))),
        description="Built-in theme name; shorthand for `extends: <name>`.",
    ),
]
AUTHORED_BOARD_ADAPTER: TypeAdapter[AuthoredBoard] = TypeAdapter(AuthoredBoardInput)


# Resolve forward references (GridItem.item and TabItem.rows/cols reference types
# defined later in the file — rebuild after all classes are in scope)
GridItem.model_rebuild()
TabItem.model_rebuild()
AuthoredBoard.model_rebuild()


# ============================================================================
# LAYOUT TYPE
# ============================================================================


class LayoutType(str, Enum):
    """Layout types for organizing charts.

    - rows: Vertical stack of items
    - cols: Horizontal arrangement of items
    - grid: CSS-grid style layout with columns
    - tabs: Tabbed navigation between views
    """

    ROWS = "rows"
    COLS = "cols"
    GRID = "grid"
    TABS = "tabs"
