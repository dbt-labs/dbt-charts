"""Typed global config contract for compiled runtime defaults.

``Config`` is the authoritative required runtime config object.
Engine knobs only — no presentation fields (style, theme).

Dynamic sections remain open-ended but are backed by Pydantic
mapping-like nodes that still support mapping access.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.cache import CachePatch
from dbt_charts.core.compile.models.primitives import HtmlPolicy
from dbt_charts.core.compile.models.vega_lite.config import VegaLiteConfig
from dbt_charts.core.compile.vega_lite import VEGA_LITE_SCHEMA_URL

# The shape `published_to:` must take. Cloud mounts a project at
# `/<org>/<project>/`, and the trailing slash is what makes the recorded URL
# a page rather than a 404. `cloud_client.published_to.EXPECTED_FORM` states
# the same rule for the writing side -- tach forbids that package importing
# this one, so a parity test holds the two together.
PUBLISHED_TO_FORM = "https://<host>/<org>/<project>/"


def _normalize_node_value(value: object) -> object:
    """Recursively normalize nested mapping values into ConfigNode objects."""
    if isinstance(value, BaseModel):
        return value
    if isinstance(value, Mapping):
        return ConfigNode.model_validate(dict(value))
    if isinstance(value, list):
        return [_normalize_node_value(item) for item in value]
    return value


class ConfigMappingBase(BaseModel, Mapping[str, Any]):
    """Pydantic model with mapping-like helpers for config consumers."""

    model_config = ConfigDict(extra="forbid")

    # Mapping access is a migration shim for dynamic config sections; cache the
    # assembled top-level view and invalidate when this model's own fields change.
    _mapping_cache: dict[str, Any] | None = PrivateAttr(default=None)

    def _mapping_data(self) -> dict[str, Any]:
        mapping_cache = object.__getattribute__(self, "__pydantic_private__").get(
            "_mapping_cache"
        )
        if mapping_cache is not None:
            return mapping_cache

        data: dict[str, Any] = {}
        for key, field in type(self).model_fields.items():
            value = object.__getattribute__(self, key)
            data[key] = value
            if field.alias and field.alias != key:
                data[field.alias] = value
        extra = object.__getattribute__(self, "__pydantic_extra__") or {}
        for key, value in extra.items():
            data[key] = value
        self._mapping_cache = data
        return data

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name != "_mapping_cache":
            self._mapping_cache = None

    def __delattr__(self, item: str) -> None:
        super().__delattr__(item)
        if item != "_mapping_cache":
            self._mapping_cache = None

    def __getitem__(self, key: str) -> Any:
        data = self._mapping_data()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self._mapping_data())

    def __len__(self) -> int:
        return len(self._mapping_data())

    def __bool__(self) -> bool:
        return bool(self._mapping_data())

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping_data().get(key, default)

    def items(self) -> ItemsView[str, Any]:
        return self._mapping_data().items()

    def keys(self) -> KeysView[str]:
        return self._mapping_data().keys()

    def values(self) -> ValuesView[Any]:
        return self._mapping_data().values()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = self._mapping_data()
        if name in data:
            return data[name]
        raise AttributeError(name)

    def to_plain_dict(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """Dump config models to plain nested Python dicts."""
        return self.model_dump(mode="python", exclude_none=exclude_none, by_alias=True)


class ConfigNode(ConfigMappingBase):
    """Open-ended config section that still supports attribute and mapping access."""

    # Accepts arbitrary keys — the config tree is open-ended by design.
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, value: object) -> object:
        if isinstance(value, ConfigNode):
            return value
        if isinstance(value, Mapping):
            return {key: _normalize_node_value(item) for key, item in value.items()}
        return value


class ChartRenderingConfig(ConfigNode):
    class AxisConfig(ConfigNode):
        label_gap_spaces: int = Field(
            description="Word-space separation required between axis labels."
        )
        label_gap_spaces_numeric: int = Field(
            description="Word-space separation required between numeric axis labels."
        )
        sparse_ceiling_px: float = Field(
            description="Maximum pixel gap allowed between visible temporal axis "
            "labels before the label cadence stops coarsening — an absolute pixel "
            "value, not a fraction of plot width, since the same relative density "
            "at different card widths can need opposite cadences."
        )

    class PieConfig(ConfigNode):
        wedge_label_min_share: float
        invisible_slice_share: float
        wheel_dominance_min_ratio: float
        label_reach_coefficient: float
        outer_fraction: float
        attached_table_gap_px: float
        hybrid_heading_gap_px: float
        right_placement_min_width_fraction: float
        right_placement_min_width_px: float
        right_placement_max_wheel_px: float

    class BarConfig(ConfigNode):
        grouped_bar_padding_inner: float
        grouped_bar_padding_outer: float
        # A bar/stacked-total only gets the baseline-anchored hover band when
        # its own value is under this fraction of the chart's own largest
        # value -- separate from hover_band_extend_fraction (how big the band
        # itself is) so a merely-small-but-visible bar doesn't get one.
        hover_band_trigger_fraction: float
        # Fraction of the plot's pixel extent the hover band extends by, once
        # triggered.
        hover_band_extend_fraction: float
        # Vertical room one value label needs inside its bar segment, as a
        # multiple of the label font size: the glyph box plus a little air.
        # Drives the fit test in render/chart/features/value_labels.py, which
        # drops a label whose segment provably cannot hold it.
        label_fit_line_height_multiplier: float = Field(gt=0)
        # Minimum gap between two adjacent horizontal-stacked-bar top-rail
        # labels, in word-spaces at the rail's own font size — same unit
        # convention as AxisConfig.label_gap_spaces. Used by
        # _horizontal_rail_labels_would_collide (compile/resolve/chart/bar.py)
        # to steer a crowded rail back to a legend before it ever renders.
        top_rail_label_gap_spaces: int = Field(ge=0)
        # Height a single-row (`row`) top legend costs, measured as baseline
        # plot height vs. legend-off plot height. Flat: this legend flows
        # horizontally in one row, so its height does not track series count
        # -- measured 31px at 2, 5, 16 and 25 series, on 400px and 640px
        # cards alike. Bar-only: the multi-column (`compact`) legend's height
        # is charged via `legend_wrap_marginal_height_px` instead (see
        # `PlotHeightFloorConfig.compact_legend_row_px` below) -- a marginal
        # per-row cost, deliberately not the same calculation as this field.
        plot_height_floor_row_legend_total_px: float = Field(ge=0)

    class PlotHeightFloorConfig(ConfigNode):
        """Protect-the-plot floor (see resolve/chart/plot_height_floor.py).

        General plot chrome, shared by every cartesian family that estimates
        a plot-height floor -- not legend-specific (a candidate legend's own
        height is passed in by the caller as ``legend_height_px``, computed
        from family- and layout-specific inputs the caller alone knows).
        """

        # Below this fraction of the card's own height the plot is starved
        # and the author is told. Calibrated against a grouped-bar render
        # sweep; 0.30 sits just under the ~32% the compact-legend fold
        # settles at, and above the ~22% "squashed sliver" confirmed by eye
        # at a 300px card. Retune by re-running that sweep, not casually.
        ratio: float = Field(gt=0, lt=1)
        # Fixed chrome the plot never gets (object title + x-axis band and
        # ticks). Card padding is NOT folded in here — it is subtracted
        # separately from style.frame.card_padding, so a theme that changes
        # card padding does not silently invalidate this number.
        irreducible_height_px: float = Field(ge=0)
        # Height the horizontal rail's axis title costs when visible. The
        # other axis title is rotated and costs width, not height, so only
        # one of the two is ever charged here.
        axis_titles_height_px: float = Field(ge=0)
        # Height the subtitle line costs when present. Flat, single-line —
        # a wrapped multi-line subtitle under-counts here.
        subtitle_height_px: float = Field(ge=0)
        # Marginal height ONE row of a wrapped, multi-column top legend adds
        # to the floor's own charge -- flat per row, no fixed chrome term of
        # its own. Deliberately NOT `legend.chrome_height_px` /
        # `legend.row_height_px` (`legend_wrap_required_height_px`,
        # `_axes.py`): that pair is the legend's *total* footprint at
        # collapse, correct only where nothing else is subtracted alongside
        # it (`_stack_legend_should_yield`'s `required > plot_height`). Here
        # the legend's height is charged inside `estimate_plot_height`,
        # which already subtracts its own fixed chrome
        # (`irreducible_height_px`, `axis_titles_height_px`) -- adding the
        # legend's *total* on top double-counts that fixed chrome a second
        # time. This field is the marginal quantity instead, sized to play
        # correctly alongside those two. See `legend_wrap_marginal_height_px`
        # (`_axes.py`).
        #
        # KNOWN BIAS. Measured across both stack modes, the estimate runs
        # optimistic by a mean of +11.8px at 1 series down to +1.8px at 10 --
        # the irreducible term is charged a little too lightly, most visibly
        # where there are fewest legend rows. It is what leaves the check
        # short on a handful of marginal cards. Correcting it needs a sweep
        # across more chart shapes than the one board this was measured on.
        compact_legend_row_px: float = Field(ge=0)

    class TypeInferenceConfig(ConfigNode):
        max_ordinal_buckets: int

    class FrameConfig(ConfigNode):
        footer_rule_gap_px: int
        footer_timestamp_gap_px: int
        # Constrained to the weights with a static font face in
        # fonts.WEIGHT_FACE_ALIASES: any other value renders correctly in a
        # browser and collapses to Regular in every rasterized export.
        footer_brand_weight: Literal[500, 600]

    class SupportTableConfig(ConfigNode):
        divider_gap: float
        chart_support_table_max_x_ticks: int
        # Protect-the-plot floor for the column block, read directly off this
        # getter in render/layout_sizing.py's width-correction re-render:
        # below this fraction of the card's own width, the plot has been
        # squeezed away by the column block's reserved width plus whatever
        # axis/legend overhead the render measures on top of it. Mirrors
        # plot_height_floor.ratio's shape; unlike that one, breaching
        # this floor raises rather than warns -- a width floor breach means
        # the plot's own width clamps toward zero, which is a missing chart,
        # not a squeezed one.
        plot_width_floor_ratio: float = Field(gt=0, lt=1)
        # Softer, earlier signal than the hard floor above: warns when the
        # column block's own reserved width already exceeds this fraction of
        # the pre-axis-chrome footprint it shares with the plot
        # (block_width / (block_width + plot_width)), even while the plot
        # still clears plot_width_floor_ratio by a comfortable margin.
        column_block_share_warn_ratio: float = Field(gt=0, lt=1)

    class StrokeConfig(ConfigNode):
        min_width: float
        max_width: float

    class PointConfig(ConfigNode):
        diameter_ratio: float = Field(gt=0)
        min_px_per_point: float = Field(gt=0)

    class EndpointLabelsConfig(ConfigNode):
        # 0 collapses to the "no explicit pane width" sentinel and ≥1 restores the
        # rail-wider-than-canvas crash this cap exists to prevent.
        max_width_fraction: float = Field(gt=0, lt=1)
        line_height_multiplier: float = Field(gt=0)

    class GradientConfig(ConfigNode):
        nice_tick_count: int

    class LegendConfig(ConfigNode):
        # A single-row top legend's fixed, non-text chrome per entry: the
        # swatch box (constant regardless of mark size or count -- see
        # legend_row_fits' docstring) plus the gap to the next entry. The
        # entry's OWN label text is never estimated here -- it is measured
        # with the engine's real font metrics (font_measure.get_font_measurer)
        # against the resolved legend label font, at the point of use.
        row_swatch_width_px: float
        row_entry_gap_px: float
        # Fixed horizontal chrome a single-row top legend's own left edge
        # loses before it can begin (legend_row_fits' width bound) --
        # min_reserve_px is the near-universal floor (generic card/frame
        # chrome; every family here defaults its measure axis to the right,
        # so nothing axis-driven normally sits to the legend's left).
        # dimension_axis_reserve_chrome_px is added on top of a horizontal
        # bar's own measured dimension-label width -- the one shape that
        # puts a real, content-driven axis on the left. See
        # estimate_left_axis_reserve_px and default_config.yml.
        min_reserve_px: float
        dimension_axis_reserve_chrome_px: float
        # Calibrated estimate of one wrapped legend row's height (label line
        # height plus row padding) and the fixed title/padding chrome above
        # the rows -- the legend's *total* footprint at collapse
        # (legend_wrap_required_height_px), used by bar.py's own
        # stacked-legend-yield classifier. The plot-height floor's own
        # compact-legend charge and the fallback ladder's rung-2 check
        # (legend_wrap_fits_height_budget) want the marginal quantity
        # instead -- see PlotHeightFloorConfig.compact_legend_row_px above.
        row_height_px: float
        chrome_height_px: float

    class HoverEmphasisConfig(ConfigNode):
        # Strength for hover emphasis. Engine config, not a theme value:
        # there is no wide range of settings that read well, so we tune it
        # rather than the author. Themes keep only the on/off switch
        # (style.charts.hover_emphasis.visible) -- one feature, one noun, on
        # both surfaces.
        dimmed_opacity: float = Field(gt=0, lt=1)

    class ColorVariantsConfig(ConfigNode):
        """Constants for ``variant()``/``label_ink()``'s color derivation.

        Engine config, not a theme value -- same reason as
        HoverEmphasisConfig.dimmed_opacity: there is no wide range of
        settings that read well, so we tune it rather than the author.
        One rule shape for all four tiers: ``L' = L + (pole - L) * k``, hue
        held, chroma scaled, gamut-clipped (compile/resolve/style/palette.py).

        ``frozen=True`` (``ConfigNode`` is not frozen by default) blocks
        mutation; pydantic auto-generates a matching ``__hash__`` at runtime,
        but only when the class doesn't already define one -- and pyright
        can't see that dynamic step (dataclass_transform here only tracks a
        `frozen=True` class-keyword argument, and `ConfigNode` itself isn't
        frozen, so that spelling is unavailable). Declaring ``__hash__``
        explicitly, the same field-tuple hash pydantic would have generated,
        keeps this statically ``Hashable`` for the ``lru_cache`` the two
        functions key on.
        """

        model_config = ConfigDict(frozen=True)

        def __hash__(self) -> int:
            return hash(tuple(self.__dict__.values()))

        # Fraction of the remaining distance to white the light tier moves.
        light_k: float = Field(gt=0, le=1)
        # Chroma multiplier applied to the light tier.
        light_chroma: float = Field(gt=0, le=1)
        # Minimum lightness gap light keeps above its base, even when the
        # pale cap below would otherwise pull it closer -- above roughly
        # base L 0.80 this floor wins over that cap (see palette.py).
        light_min_gap: float = Field(ge=0, lt=1)
        # Gap light targets keeping under pale's flat band, when the
        # light_min_gap floor above doesn't win instead.
        light_pale_gap: float = Field(ge=0, lt=1)
        # Lightness pole the dark tier moves toward, and the pole label ink
        # steps toward on a light canvas.
        dark_pole: float = Field(gt=0, lt=1)
        # Fraction of the remaining distance to dark_pole the dark tier --
        # and label ink's pre-floor step -- moves.
        dark_k: float = Field(gt=0, le=1)
        # Flat lightness the pale tier is set to -- the band itself is the
        # pole (its own move fraction is fixed at 1).
        pale_l: float = Field(gt=0, lt=1)
        # Chroma multiplier applied to the pale tier.
        pale_chroma: float = Field(gt=0, le=1)
        # Lightness pole the deep tier moves toward.
        deep_pole: float = Field(gt=0, lt=1)
        # Fraction of the remaining distance to deep_pole the deep tier
        # moves.
        deep_k: float = Field(gt=0, le=1)
        # WCAG contrast floor label ink guarantees against its canvas,
        # applied after the dark-tier step. No upper bound here -- see the
        # default_config.yml comment for the practical ceiling.
        label_ink_min_contrast: float = Field(ge=1)

    pie: PieConfig
    bar: BarConfig
    plot_height_floor: PlotHeightFloorConfig
    type_inference: TypeInferenceConfig
    frame: FrameConfig
    support_table: SupportTableConfig
    stroke: StrokeConfig
    point: PointConfig
    axis: AxisConfig
    endpoint_labels: EndpointLabelsConfig
    gradient: GradientConfig
    legend: LegendConfig
    hover_emphasis: HoverEmphasisConfig
    color_variants: ColorVariantsConfig


class InspectorConfig(ConfigNode):
    tree_max_depth: int


class RenderingConfig(ConfigNode):
    class PngRenderingConfig(ConfigNode):
        scale: float

    png: PngRenderingConfig


class VegaRuntimeConfig(ConfigNode):
    """Vega-Lite renderer runtime config.

    Presentation fields (default_theme, default_palette) have moved to the
    Board/style cascade. Only the renderer runtime config and schema URL remain.
    """

    config: VegaLiteConfig = Field(default_factory=VegaLiteConfig)

    @property
    def schema(self) -> str:  # type: ignore[override]
        return VEGA_LITE_SCHEMA_URL


class ExecutionConfig(ConfigNode):
    # DuckDB-backed executors serialize access via _DUCKDB_EXECUTE_LOCK.
    max_workers: int = Field(
        description="Maximum parallel query workers for a render. DuckDB "
        "serializes access regardless, so this only moves external warehouses.",
    )
    # Safety ceiling on how long a single query may run, enforced as a server-side
    # statement timeout on network warehouses (never a client-side abandon — see
    # execute/adapters/sql_adapter.py). DuckDB and SQLite are local file databases
    # with no server to enforce a timeout, so this has no effect on them.
    # Overridable per source via sources.<name>.max_query_duration_seconds.
    max_query_duration_seconds: int = Field(
        gt=0, description="Maximum seconds a single query may run (must be > 0)."
    )
    # Hard cap on files matched per glob pattern in file sources. Error is raised
    # before any file is read so a runaway glob fails fast. Override in dbt_charts.yml.
    max_glob_file_count: int = Field(
        gt=0, description="Maximum files a single glob may match (must be > 0)."
    )
    # Hard cap on table entries in a single files: map. A deployment ceiling
    # (DCT_FILE_SOURCE_MAX_TABLES_CEILING) can only lower it, never raise it.
    file_source_max_tables: int = Field(
        gt=0,
        description="Max tables in a files: map (must be > 0).",
    )
    # Safety ceiling on a file-source table's uncompressed bytes: the file's
    # own bytes for CSV/JSON, and for Parquet the uncompressed total its
    # footer records, so one cap means one thing across formats. Checked while
    # reading, before the cache backend is written, so a runaway relation
    # fails fast. A deployment ceiling (DCT_FILE_SOURCE_MAX_BYTES_CEILING) can
    # only lower it, never raise it — so a message about this limit must not
    # tell the caller to raise it.
    file_source_max_bytes: int = Field(
        gt=0,
        description="Max uncompressed bytes per file-source table (must be > 0).",
    )
    # Safety ceiling on rows returned by a single query, enforced by bounding the
    # driver's own fetch (fetchmany()/execute(limit=...)) rather than rewriting
    # SQL. Exceeding it truncates the result and emits WARN_QUERY_RESULT_TRUNCATED
    # rather than failing the query. A Cloud deployment ceiling
    # (DCT_MAX_ROWS_CEILING) can only lower this, never raise it. Override in
    # dbt_charts.yml under execution.max_rows.
    max_rows: int = Field(
        gt=0, description="Maximum rows a single query may return (must be > 0)."
    )
    # Safety ceiling on the serialized byte size of a single query result,
    # checked incrementally during row accumulation so an oversized result is
    # never fully serialized to measure it. Exceeding it truncates the result
    # and emits WARN_QUERY_RESULT_TRUNCATED rather than failing the query. A
    # Cloud deployment ceiling (DCT_MAX_RESULT_BYTES_CEILING) can only lower
    # this, never raise it. Override in dbt_charts.yml under
    # execution.max_result_bytes.
    max_result_bytes: int = Field(
        gt=0,
        description="Maximum serialized byte size of a single query result "
        "(must be > 0).",
    )
    # Safety ceiling on the cumulative Jinja-emitted output of one board
    # render — summed across every templated field in that render (queries,
    # titles, markdown, …), not per field. Exceeding it is a hard error
    # (ERR-TEMPLATE-OUTPUT-TOO-LARGE), never a truncation — why, in
    # compile/template/output_budget.py's module docstring. A Cloud
    # deployment ceiling (DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING) can only
    # lower this, never raise it. Override in dbt_charts.yml under
    # execution.max_template_output_bytes.
    max_template_output_bytes: int = Field(
        gt=0,
        description="Maximum cumulative bytes of Jinja-emitted template "
        "output for a single board render (must be > 0).",
    )
    # sqlglot uses different dialect names than dbt charts' public-facing dialect strings.
    dialect_aliases: dict[str, str] = Field(
        description="Maps a dbt charts dialect name to its sqlglot equivalent "
        "before parsing.",
    )


class ServerConfig(ConfigNode):
    model_config = ConfigDict(extra="forbid")

    debug: bool
    nav: bool
    port: int | None = None  # None = use deterministic project-path port
    # When True, non-board frontmatter keys in .md files are rendered as a
    # metadata table at the top of the page.  Off by default so AGENTS.md,
    # README, and other prose files don't suddenly acquire header tables.
    markdown_metadata_table: bool


class ProjectCacheConfig(CachePatch):
    """Project cache root (``cache:`` in dbt_charts.yml): the cascade root every
    source/board/query inherits, plus the backend location.

    Deliberately the **same shape as every other scope** (``ttl`` — inherited
    from CachePatch), so ``cache: 4h`` means the same thing everywhere;
    ``path`` is the one project-only field, reachable via the block form
    (``cache: {ttl: 4h, path: .dct-cache.duckdb}``). There is no separate
    backend on/off switch — the store is provisioned lazily when any resolved
    query policy is enabled. The shipped root lives in
    ``defaults/default_config.yml``.

    One scalar is missing here that every other scope accepts: ``cache: true``.
    See the validator below.
    """

    @model_validator(mode="after")
    def _root_must_state_a_ttl(self) -> ProjectCacheConfig:
        """Caching on at the root with no ttl means forever — say so out loud.

        Reachable only by writing ``cache: true``: the scalar replaces the
        shipped block whole (``deep_merge_dict`` recurses only between
        mappings), and unlike every other scope the root has nothing above it
        to take a ttl from. So the one spelling that promises nothing about
        duration would quietly pick the longest one there is.
        """
        if self.enabled and self.ttl is None:
            raise ValueError(
                "the project cache root has to say how long to keep results — "
                "write cache: <duration> (e.g. cache: 24h) or cache: forever. "
                "(cache: true means 'keep the ttl from the scope above', and "
                "the project root has no scope above it.)"
            )
        return self

    # None = the zero-config in-memory store, a documented outcome rather than
    # a fallback (see the field description). Defaulted, not required, so the
    # project scope accepts the same bare `cache: 4h` scalar as every other
    # scope — a required project-only key would make that spelling unauthorable.
    path: str | None = Field(
        default=None,
        description=(
            "Persistent cache file location. None = ephemeral in-memory cache "
            "auto-provisioned by the engine; a string path selects a persistent "
            "cache file (created if absent). Both are documented outcomes, not "
            "a fallback."
        ),
    )


class Config(ConfigMappingBase):
    """Authoritative required runtime settings model.

    Engine knobs and definition registries only. Presentation (style, board
    layout) lives in the Board cascade via charts/meta.yml — not here.

    Closed top-level fields define the supported global settings surface; any
    stray key in dbt_charts.yml (e.g. ``style:``, ``board:``, ``theme:``) raises a
    pydantic ValidationError (``Extra inputs are not permitted``). The default
    theme is an engine-level fallback (``DCT_DEFAULT_THEME`` env var →
    ``SHIPPED_DEFAULT_THEME_NAME``), not a dbt_charts.yml key.
    """

    model_config = ConfigDict(extra="forbid")

    cache: ProjectCacheConfig
    chart_rendering: ChartRenderingConfig
    execution: ExecutionConfig
    geo_sources: ConfigNode
    # Deployment wins (DCT_HTML_POLICY_CEILING), then this project ceiling, then the board field.
    # Default (trusted-raw) is permissive — no ceiling — for local development.
    # Cloud pins DCT_HTML_POLICY_CEILING=safe-subset at the deployment level.
    html_policy_ceiling: HtmlPolicy
    inspector: InspectorConfig
    rendering: RenderingConfig
    server: ServerConfig
    terminal: ConfigNode
    palettes: ConfigNode
    vega: VegaRuntimeConfig
    dbt_grays: ConfigNode
    dbt_creams: ConfigNode
    strict: bool | None = None  # None = strict mode not configured; defaults to off
    # Nothing reads this: a project's registry is read per-project by
    # load_project_sources, never off the global. It must stay declared anyway —
    # Config is extra="forbid" and validates dbt_charts.yml, so dropping the
    # field would fail load_config (and `dct serve` startup) for every project
    # that declares sources:. None = no explicit sources section in config.
    sources: ConfigNode | None = None
    # Canonical public URL for dct render exports (e.g. "https://dashboards.example.com").
    # When set, rendered links are fully-qualified. Empty string means root-relative (default).
    public_url: str
    # Where this project is published in dbt charts Cloud, e.g.
    # "https://dbtcharts.com/acme-data/analytics/" -- written by
    # `dct cloud project connect`, read by context resolution
    # (dbt_charts.cloud_client.context) before it falls back to matching the
    # git remote. None = not connected, or not recorded yet.
    published_to: str | None = None
    # Nothing reads this field: resolve_dbt_project_dir (core/project_roots.py)
    # reads dbt_charts.yml raw off disk before a Config exists (same reason
    # sources: above is unread here). It must stay declared anyway -- Config
    # is extra="forbid" and validates the whole file, so dropping the field
    # would fail load_config (and `dct serve` startup) for every project that
    # links an external dbt project. None = sibling default (no key set).
    dbt_project_dir: str | None = None

    @field_validator("published_to")
    @classmethod
    def _validate_published_to(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or len(segments) != 2
        ):
            raise ValueError(
                "published_to must be an absolute URL of the form "
                f"{PUBLISHED_TO_FORM}, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _validate_palette_contract(self) -> Config:
        category = self.palettes.get("vivid-10")
        if not isinstance(category, list) or not category:
            raise ValueError("palettes.vivid-10 must be present and non-empty")
        return self


def as_plain_mapping(value: ConfigMappingBase | Mapping[str, Any]) -> dict[str, Any]:
    """Project a compiled config model or raw mapping into a plain dict."""
    if isinstance(value, ConfigMappingBase):
        return value.to_plain_dict(exclude_none=False)
    return dict(value)


def is_mapping_like(value: object) -> bool:
    """Return whether a value can be consumed through mapping helpers."""
    return isinstance(value, ConfigMappingBase | Mapping)
