//! Board composition — assembles the full board SVG from the STATIC resolved
//! board and the port's own chart families/sizing (`sizing.rs` computes
//! every item's box; this module never trusts the SIZED board's own numbers).
//!
//! A board shape this module does not model refuses with a named `Err` —
//! never a partial or approximate board — and a chart item whose own family
//! refuses makes the whole board refuse. Modeled today: a `rows`/`cols`/
//! `grid` layout of chart items AND nested `type: board` items (a board
//! item recurses through `render_nested_board`, its own title/text/layout
//! composed exactly like the root — `docs/laws-board.md`'s "Nested boards"
//! section), a board title with an optional title-inline variables band
//! (`title.rs`), single- AND multi-column body text (`prose.rs`), the
//! variables band standalone or title-inline (`variables.rs`) at any
//! nesting level, and the outer frame (root svg identity attributes,
//! generated styles, footer attribution, timestamp, the hover-runtime
//! facts on the root). Everything else (`tabs`, a details section, an
//! item's own `chart_error`) refuses by name — `docs/laws-board.md`
//! carries the full list and counts.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::{Divider, GappedDivider};
use crate::conditions::eval_bool_condition;
use crate::contract;
use crate::codes::ERR_ENGINE_REFUSED;
use crate::diagnostic::Diagnostic;
use crate::prose;
use crate::sizing::{
    self, calculate_cols_boxes, calculate_grid_boxes, calculate_layout_height,
    calculate_rows_boxes, calculate_tabs_boxes, resolve_active_tab_index, ChartDefaultsDoc,
    ContainerBox, ItemAuthoring, ItemBox, LayoutItemInput,
};
use crate::svg::{attr_name, html_escape, num_full, py_float_str, xml_escape_attr};
use crate::text::FontBook;
use crate::title;
use crate::{render_fitted_natural, BoardStyle, Padding, Payload};

mod row_shifts;
pub use row_shifts::{derive_title_shifts, RowShift};

/// The envelope the Python harness dumps for one board render: production's
/// own `ResolvedBoard` (undumped via the same `TypeAdapter` production's
/// `board_artifact.py` uses), every chart's own datasets keyed by chart id
/// then query name, and the handful of engine-config/render-clock/chrome
/// facts the composer has no other way to read (see field docs).
#[derive(Deserialize)]
pub struct BoardPayload {
    pub resolved: Value,
    pub chart_datasets: sizing::ChartDatasets,
    pub background: Option<String>,
    pub fonts_dir: Option<String>,
    /// Production's own `portable_strftime` output for the data-freshness
    /// line, computed once by the harness from the shared executor's cache
    /// timestamps — the composer paints it verbatim rather than re-deriving
    /// a render clock, which would be non-deterministic across two separate
    /// render invocations.
    pub render_timestamp_text: String,
    /// The root svg's `data-rendered-at` value, read back off the harness's
    /// own reference render (`render_board_svg` stamps `datetime.now()`
    /// internally with no seam to inject a clock — this is the only way
    /// both sides can agree on one wall-clock string).
    pub render_time_iso: String,
    /// `generate_svg_styles(...)`'s exact return value — production's
    /// `<style>` chrome block. Called directly by the harness rather than
    /// re-derived here: with `embed_fonts=False` (the harness's only mode)
    /// it is a pure function of `resolved_style.emoji_mode` alone
    /// (`font_selection.py:231-238` — `board_font_face_css`'s painted-markup
    /// arg is unused on that path), so this is a law, not an opaque asset.
    pub defs_styles_block: String,
    /// `hover_runtime_attributes(resolved_style)`'s exact return value — the
    /// board-root data attributes the host-shipped hover runtime reads; same
    /// reasoning: a pure function of `resolved_style`.
    pub hover_attributes: String,
    /// Same flattened envelope `_rust_payload` hands the per-chart sweep —
    /// forwarded verbatim into every chart item's own `Payload`.
    pub board_style: BoardStyle,
    /// resolved_style.chart_defaults — the theme constants
    /// `sizing::get_chart_content_height` reads. A compile-time/theme fact
    /// (not data-aware), so it is identical between the static and sized
    /// resolves; forwarded here because `BoardStyle` above is the per-chart
    /// sweep's own shared envelope and does not carry it.
    pub chart_defaults: ChartDefaultsDoc,
    /// resolved_style.frame.min_height — `calculate_layout_height`'s floor.
    pub frame_min_height: f64,
    /// `_measured_variable_values(root_board, variables)` — the ROOT board's
    /// own `variable_defaults` merged with the request's own overrides;
    /// title.rs/prose.rs Jinja substitution reads these at every nesting
    /// level. Production threads the SAME raw request-level `variables`
    /// dict unchanged through every nested board's own `_measured_variable_
    /// values(nested_board, variables)` call (`render_nested_board`'s own
    /// `variables` parameter is the caller's, never re-merged per level) —
    /// merging the root's own defaults with itself here is a no-op, so this
    /// field equals that raw dict exactly; `effective_variable_values`
    /// re-merges it against a NESTED board's own `variable_defaults` (also
    /// forwarded, per node, on `BoardDoc`) rather than needing a second
    /// payload field per nesting level. The board title's own `title_style`/
    /// `title_fonts`/`text_style`/`text_fonts` mdsvg seam moved onto
    /// `BoardDoc` itself (`resolved.title_style` etc.) so it can be forwarded
    /// per node, not just at the root — see `BoardDoc`'s own doc comment.
    #[serde(default)]
    pub variable_values: serde_json::Map<String, Value>,
    /// `board_sweep.py::_variable_datasets` — every visible variable's own
    /// query-backed option rows, by variable name, flat across the WHOLE
    /// board tree (variables are board-global: one namespace document-wide,
    /// so no per-nesting-level plumbing is needed). The seam `resolve_
    /// option_values`'s query branch reads instead of an executor.
    #[serde(default)]
    pub variable_datasets: HashMap<String, Vec<serde_json::Map<String, Value>>>,
    /// `boards.py`'s own standalone-variables-band gate is `if board.
    /// visible_variables and variables and ...` — truthiness on the RAW
    /// request-level overrides dict, not `variable_values` above (which is
    /// non-empty whenever ANY variable in the document has a `default:`,
    /// regardless of whether this request overrode anything). Threaded once
    /// at the root, same reasoning as `variable_values` itself.
    #[serde(default)]
    pub variables_overrides_present: bool,
    /// `fonts.py::FONT_REGISTRY`, forwarded verbatim by `board_sweep.py`
    /// rather than duplicated as Rust literals (`embed_fonts.rs`'s own doc)
    /// — item (d)'s `--embed-fonts` face roster (HIGH 3).
    #[serde(default)]
    pub font_registry: Vec<crate::embed_fonts::FontRegistryEntry>,
    /// The URL-mode `@font-face` roster this board's own `defs_styles_
    /// block` already carries — `--embed-fonts` locates and REPLACES this
    /// exact slice rather than growing the block additively.
    #[serde(default)]
    pub font_face_css: String,
    /// Per-chart render outcomes, appended to as `render_chart_item` visits
    /// each chart — never read from the payload JSON (`#[serde(skip)]`), and
    /// interior-mutable rather than threaded as an extra parameter through
    /// every `render_rows`/`render_cols`/`render_nested_board`/etc. call:
    /// every one of those already carries `payload: &BoardPayload`, so a
    /// `RefCell` here reaches every nesting level for free. `run_board`
    /// (`main.rs`) drains it after `render_board` returns to build the
    /// render report sidecar (item a).
    #[serde(skip)]
    pub report: RefCell<Vec<ChartStatusEntry>>,
    /// The italic fonts markdown emphasis consumed while painting: a paint site
    /// records into it (`embed_fonts::record_painted_italics`) and `--embed-fonts`
    /// reads it back after `render_board` returns. Never read from the payload
    /// JSON, and empty on a measure-only pass.
    #[serde(skip)]
    pub painted_italics: crate::embed_fonts::PaintedItalics,
}

/// One chart's outcome for the render report — `rendered` when
/// `render_fitted` produced real ink, `refused` when either the chart never
/// resolved (`ItemDoc.chart_error`) or its own family refused at render time,
/// in which case `render_chart_item` paints `render_error_card_svg` into the
/// slot instead of failing the whole board (item b). `code`/`message` are
/// `None` on a `rendered` entry (`skip_serializing_if`, so the report line
/// for the common case carries no dead keys). MEDIUM: this field was
/// `name` — renamed to `code` to match the cutover plan's
/// `{chart_id, code, message}` shape, which PR 9104's `run_engine` will parse.
#[derive(Serialize, Clone)]
pub struct ChartStatusEntry {
    pub chart_id: String,
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<Diagnostic>,
    /// The engine refused this chart (an unported shape or a data error it
    /// detected); a `chart_error` card is production's own and sets nothing.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub engine_refused: bool,
}

#[derive(Deserialize)]
struct BoardDoc {
    title: String,
    text: String,
    // `ResolvedBoard.html_policy` — mirrors `boards.py`'s own
    // `board.html_policy == "trusted-raw"` check (`:1163`). Missing on
    // every hand-built pre-existing test fixture that predates this field;
    // `#[serde(default)]` (empty string) is never "trusted-raw", so those
    // fixtures' behavior is unchanged.
    #[serde(default)]
    html_policy: String,
    // Container width IS static/compile-time-only input (`compile.sizing.
    // board_container_width` is a pure function of chart config — see
    // sizing.rs's module doc): read directly, never recomputed. `height`
    // (the container's data-aware total) is NOT read from the payload —
    // this composer computes it itself via `sizing::calculate_layout_height`
    // so a wrong number is a `boxes`/`container` DIFF, never borrowed.
    width: f64,
    page_padding: Option<f64>,
    card_padding: Option<f64>,
    card_gap: Option<f64>,
    layout: LayoutDoc,
    style: StyleDoc,
    // Document order, not insertion-arbitrary — `resolve_controls`' own
    // contract (a host binds the i-th control to the i-th box).
    #[serde(deserialize_with = "crate::variables::deserialize_ordered")]
    variables: Vec<(String, crate::variables::VariableDoc)>,
    // `ResolvedBoard.variable_defaults` — present at every nesting level.
    // Root's Jinja substitution (title/text) reads `BoardPayload.variable_
    // values` (the request's own raw overrides, forwarded once); a nested
    // board's own effective values are `{**this.variable_defaults,
    // **payload.variable_values}` (`_measured_variable_values`'s own merge,
    // ported at `effective_variable_values` below) — the SAME raw overrides
    // dict threaded unchanged through every nesting level in production
    // (`render_nested_board`'s own `variables` parameter is the caller's,
    // never re-merged per level).
    #[serde(default)]
    variable_defaults: serde_json::Map<String, Value>,
    // A nested board's own mdsvg title/text Style/FontSlots — the same seam
    // `BoardPayload.title_style`/etc. document for the root, built recursively
    // per node by `board_sweep.py` (`_attach_board_style_seams`) since only
    // production's own `title_renderer`/`get_compact_style` can derive them
    // for THIS board's own resolved_style/level/prose. `None` when this
    // board has no title/text, same contract as the root fields.
    #[serde(default)]
    title_style: Option<mdsvg::style::Style>,
    #[serde(default)]
    title_fonts: Option<mdsvg::fonts::FontSlots>,
    #[serde(default)]
    text_style: Option<mdsvg::style::Style>,
    #[serde(default)]
    text_fonts: Option<mdsvg::fonts::FontSlots>,
    // `None` only on a board with no text.
    #[serde(default)]
    prose_plan: Option<crate::prose::ProsePlan>,
}

impl BoardDoc {
    /// `ResolvedBoard.visible_variables` — order preserved (see the field's
    /// own doc), not a `HashMap` filter.
    fn visible_variables(&self) -> Vec<(&str, &crate::variables::VariableDoc)> {
        self.variables
            .iter()
            .filter(|(_, v)| v.visible)
            .map(|(name, v)| (name.as_str(), v))
            .collect()
    }
}

#[derive(Deserialize)]
struct LayoutDoc {
    #[serde(rename = "type")]
    kind: String,
    items: Vec<ItemDoc>,
    // `content_width` is static/given for the same reason `BoardDoc.width`
    // is (compile.sizing.board_container_width minus 2*page_padding — pure
    // config, no data dependency). `content_height` is NOT read — computed
    // by this composer, same reasoning as `BoardDoc.height`.
    content_width: f64,
    // ResolvedLayout.gap is `compile.sizing.get_board_gap(board)`'s own
    // result, likewise pure config — given, not re-derived.
    gap: f64,
    columns: Option<i64>,
    // Tabs-specific — `()`/`None` on every rows/cols/grid layout, matching
    // `ResolvedLayout`'s own defaults (`tab_titles`/`tab_slugs: ()`,
    // `tab_variable: None`, `default_tab: 0`, `tab_position: None`).
    #[serde(default)]
    tab_titles: Vec<String>,
    #[serde(default)]
    tab_slugs: Vec<String>,
    #[serde(default)]
    tab_variable: Option<String>,
    #[serde(default)]
    default_tab: i64,
    #[serde(default)]
    tab_position: Option<String>,
}

#[derive(Deserialize)]
struct ItemDoc {
    #[serde(rename = "type")]
    kind: String,
    chart: Option<Value>,
    chart_error: Option<Value>,
    // `ResolvedLayoutItem.board` — the nested board's own resolved JSON,
    // kept raw (not eagerly typed as `BoardDoc`) the same way `chart` stays
    // raw `Value`: sizing.rs's `LayoutItemInput` is generic over `&Value`
    // for both kinds, and every level re-parses lazily via
    // `serde_json::from_value`, mirroring `size_board`'s own root parse.
    #[serde(default)]
    board: Option<Value>,
    // x/y/width/height are deliberately NOT deserialized here: this
    // composer computes every item's box itself (sizing.rs) rather than
    // trusting the SIZED board's own numbers, which the payload still
    // carries (board_sweep.py reads them back separately as the oracle for
    // the `boxes` differential — never as composer input).
    // `ResolvedLayoutItem.source_path` — a nested board's own authored path,
    // needed for the `data-authored-path`/`data-authored-kind="header"` a
    // nested title/text block carries (`render_nested_board`'s own
    // `header_authored_attrs`). Chart items don't need it: a chart's own
    // wrapper reads `source_path` off the resolved CHART, not the item.
    #[serde(default)]
    source_path: String,
    details_variable: Option<String>,
    details_summary: Option<String>,
    #[serde(default)]
    details_expanded_summary: Option<String>,
    notes: Option<String>,
    #[serde(default)]
    visible: Option<Value>,
    // `ResolvedLayoutItem.title_shift`, the row's title band
    // (`layout_sizing.py::_assign_title_shifts_in_tree`): zero from the compile stage
    // (`NO_TITLE_SHIFT`), production's shift from the sweep harness.
    // `row_shifts::assign` checks a non-zero one against the shift it derives and
    // overwrites this field with the derived value.
    #[serde(default)]
    title_shift: crate::TitleShift,
    // Authoring facts the STATIC resolved board's `ResolvedLayoutItem` does
    // not preserve (LayoutItem's own grid/authoring fields, compile/models/
    // board/normalized.py) — forwarded by board_sweep.py as a harness fact.
    #[serde(default)]
    user_width: Option<String>,
    #[serde(default)]
    layout_height: Option<String>,
    /// The slot height an ancestor's literal `height:` assigned this chart
    /// (the sizing pass's `authored_slot_heights`); absent when none did.
    #[serde(default)]
    authored_height: Option<f64>,
    #[serde(default)]
    row: Option<i64>,
    #[serde(default)]
    col: Option<i64>,
    #[serde(default)]
    row_span: Option<i64>,
    #[serde(default)]
    col_span: Option<i64>,
    // The chart's own authored root `height`/`width` override — dropped by
    // ResolvedChart at resolve, so also forwarded as an authoring fact
    // rather than read off `chart_json` (chart_json carries only the
    // resolved fields; `.height`/`.width` are compile-time authored ones).
    #[serde(default)]
    chart_authored_height: Option<f64>,
    #[serde(default)]
    chart_authored_width: Option<f64>,
    // The compiled chart's own authored `aspect_ratio`/`min_height`/
    // `max_height` — HIGH 1(a)'s `sizing::unresolved_height` reads these
    // for a `chart_error` item, which carries no resolved chart of its own
    // to read them off directly. Same forwarding reasoning as
    // `chart_authored_height`/`_width` above.
    #[serde(default)]
    chart_authored_aspect_ratio: Option<f64>,
    #[serde(default)]
    chart_authored_min_height: Option<f64>,
    #[serde(default)]
    chart_authored_max_height: Option<f64>,
}

impl ItemDoc {
    fn authoring(&self) -> ItemAuthoring {
        ItemAuthoring {
            user_width: self.user_width.clone(),
            layout_height: self.layout_height.clone(),
            row: self.row,
            col: self.col,
            row_span: self.row_span,
            col_span: self.col_span,
            chart_height: self.chart_authored_height,
            chart_width: self.chart_authored_width,
            chart_aspect_ratio: self.chart_authored_aspect_ratio,
            chart_min_height: self.chart_authored_min_height,
            chart_max_height: self.chart_authored_max_height,
        }
    }

    /// `render/chart/rendering.py:116`'s gate: BOTH fields, either kind.
    fn details_present(&self) -> bool {
        self.details_variable.is_some() && self.details_summary.is_some()
    }
}

#[derive(Deserialize)]
struct StyleDoc {
    background: String,
    font: FontFamilyDoc,
    footer: FooterDoc,
    timestamp: TimestampDoc,
    title: TitleStyleDoc,
    text: TextStyleDoc,
    variables: crate::variables::VariablesStyleDoc,
    // Nested-board-only fields below: root sizing/rendering reads
    // `BoardDoc.card_padding`/`page_padding`/`card_gap`/`LayoutDoc.gap`
    // instead (the baked ROOT board-config knobs, `None` on a nested
    // board — see those fields' own doc comments). A nested board has no
    // such baked knobs; `_calculate_nested_board_layout`/`render_nested_
    // board` read these STYLE fields directly instead.
    frame: FrameStyleDoc,
    #[serde(default)]
    gap: Option<f64>,
    #[serde(default)]
    padding: Option<SpacingDoc>,
    #[serde(default)]
    margin: Option<SpacingDoc>,
    border: BorderStyleDoc,
    box_shadow: Option<BoxShadowDoc>,
    layout: LayoutStyleDoc,
    // Only the table header/row-stripe colors: `layouts.py::render_tabs_
    // layout`/`render_details_summary`'s own `_active_fill`/`_inactive_
    // fill` read these two fields off `resolved_style.chart_defaults.
    // table` (an active tab/expanded section borrows the header's
    // background; an inactive/collapsed one borrows the row stripe) — the
    // rest of `chart_defaults` is the harness-forwarded `payload.chart_
    // defaults: sizing::ChartDefaultsDoc` (a narrower, purpose-built
    // whitelist for `get_chart_content_height`), so this struct only reads
    // the two fields that whitelist omits rather than widening it.
    chart_defaults: StyleChartDefaultsDoc,
}

#[derive(Deserialize)]
struct StyleChartDefaultsDoc {
    table: TableDefaultsDoc,
    /// `chart_defaults.border`: the card border every chart type outside
    /// `kpi`/`table`/`geoshape`/`point_map`/`callout` takes
    /// (`rendering.py::_card_border`).
    border: BorderStyleDoc,
    /// The canvas a KPI or callout card's shadow is filled with (neither
    /// carries a composited canvas of its own, `rendering.py::_card_shadow`).
    ink_canvas: String,
}

#[derive(Deserialize)]
struct TableDefaultsDoc {
    header: TableHeaderDefaultsDoc,
    row: TableRowDefaultsDoc,
}

#[derive(Deserialize)]
struct TableHeaderDefaultsDoc {
    background: Option<String>,
}

#[derive(Deserialize)]
struct TableRowDefaultsDoc {
    stripe: Option<TableStripeDoc>,
}

#[derive(Deserialize)]
struct TableStripeDoc {
    color: String,
}

/// `resolved_style.layout` — only the `tabs`/`details` sub-styles this
/// composer reads; `rows`/`cols`/`grid`'s own `LayoutGapStyle`/
/// `GridLayoutStyle` are given directly as `LayoutDoc.gap`/`.columns`
/// instead (see those fields' own doc comments), so this struct omits them.
#[derive(Deserialize)]
struct LayoutStyleDoc {
    tabs: TabsStyleDoc,
    details: DetailsStyleDoc,
}

#[derive(Deserialize)]
struct TabsStyleDoc {
    bar_height: f64,
    border: BorderStyleDoc,
    font: FontSizeOnlyDoc,
    active_weight: String,
    inactive_weight: String,
    title_baseline_offset: f64,
}

#[derive(Deserialize)]
struct DetailsStyleDoc {
    summary_height: f64,
    border: BorderStyleDoc,
    font: FontSizeOnlyDoc,
    arrow: DetailsArrowDoc,
    label_x: f64,
    text_baseline_offset: f64,
    content_y_offset: f64,
}

#[derive(Deserialize)]
struct DetailsArrowDoc {
    x: f64,
    font: FontSizeOnlyDoc,
}

#[derive(Deserialize)]
struct FontSizeOnlyDoc {
    size: f64,
}

#[derive(Deserialize)]
struct FrameStyleDoc {
    card_padding: f64,
}

#[derive(Deserialize, Clone, Copy, Default)]
struct SpacingDoc {
    top: f64,
    right: f64,
    bottom: f64,
    left: f64,
}

impl SpacingDoc {
    fn horizontal(&self) -> f64 {
        self.left + self.right
    }
    fn vertical(&self) -> f64 {
        self.top + self.bottom
    }
}

#[derive(Deserialize, Clone)]
struct BorderStyleDoc {
    width: f64,
    color: String,
    radius: f64,
    #[serde(default)]
    dash_array: Option<Vec<f64>>,
    #[serde(default)]
    line_cap: Option<String>,
    #[serde(default)]
    dash_offset: Option<f64>,
}

/// `compile/models/primitives.py::BoxShadow`: the board's `style.box_shadow`,
/// a drop shadow under every chart card.
#[derive(Deserialize, Clone)]
struct BoxShadowDoc {
    dx: f64,
    dy: f64,
    blur: f64,
    color: String,
}

/// `compile/models/style/resolved/_base.py::effective_padding`: authored
/// padding (zeroed when unauthored) plus the border's own width on every
/// side (half the stroke sits inside the box).
fn effective_padding(style: &StyleDoc) -> SpacingDoc {
    let pad = style.padding.unwrap_or_default();
    let bw = style.border.width;
    SpacingDoc {
        top: pad.top + bw,
        bottom: pad.bottom + bw,
        left: pad.left + bw,
        right: pad.right + bw,
    }
}

#[derive(Deserialize)]
struct TitleStyleDoc {
    min_height: f64,
    font: TitleFontDoc,
}

#[derive(Deserialize)]
struct TitleFontDoc {
    case: Option<String>,
    color: Option<String>,
}

#[derive(Deserialize)]
struct TextStyleDoc {
    column: TextColumnStyleDoc,
}

#[derive(Deserialize)]
struct TextColumnStyleDoc {
    max_number: Option<i64>,
    max_chars: Option<i64>,
    divider: Divider,
}

impl TextStyleDoc {
    fn as_prose_doc(&self) -> crate::prose::TextDoc {
        crate::prose::TextDoc {
            max_number: self.column.max_number,
            max_chars: self.column.max_chars,
            divider: self.column.divider.clone(),
        }
    }
}

#[derive(Deserialize)]
struct FontFamilyDoc {
    family: Option<String>,
}

#[derive(Deserialize)]
struct FontSizeColorDoc {
    family: Option<String>,
    size: Option<f64>,
    color: Option<String>,
}

#[derive(Deserialize)]
struct FooterDoc {
    visible: bool,
    text: String,
    link: Option<String>,
    font: FontSizeColorDoc,
    y_offset: f64,
    /// Weight the brand run is painted and measured at.
    brand_weight: u32,
    divider: GappedDivider,
}

#[derive(Deserialize)]
struct TimestampDoc {
    visible: bool,
    position: String,
    align: String,
    y: f64,
    /// Gap between the footer-positioned timestamp and the attribution.
    gap: f64,
    font: FontSizeColorDoc,
}


/// `SVG_LAYOUT_PADDED_TYPES` (`normalized/_base.py:203`) — chart types
/// whose own hand-drawn SVG renders at the padding-SHRUNK inner size, so
/// this composer must wrap it in a translate before the wrapper carries the
/// full padded outer box (`padded_authoring_content`, called from this
/// branch of `render_chart_item`). A DIFFERENT set from `PAINTS_MARKS`'
/// complement (`is_internal_svg`, below): `callout` never paints a Vega
/// mark either, but renders full-bleed at 0,0 with no shrink to undo —
/// `ink_carries_padding` handles that case on its own else branch, and the
/// root-`<svg>`-stripping decision is `is_internal_svg`'s alone, not this
/// function's. `histogram` genuinely paints marks, like `bar` — it is in
/// neither set. `geoshape`/`point_map` still refuse at the family dispatch
/// itself, so they never reach either branch.
fn is_svg_family(chart_type: &str) -> bool {
    matches!(chart_type, "table" | "kpi" | "spark_bar")
}

/// `PAINTS_MARKS`' complement (`normalized/_base.py:217-229`), restricted
/// to the types this composer's `render_chart_item` can actually reach —
/// `geoshape`/`point_map` refuse at the family dispatch itself, so they
/// never need naming here, same restriction `is_svg_family` above already
/// takes. `chart_type not in PAINTS_MARKS` (`rendering.py:618`, never
/// `is_error_fallback` — the ChartDataError placard path this composer
/// doesn't reach) decides whether a chart's own root `<svg>` gets stripped
/// before it joins the board's `dbt-chart` wrapper `<g>` — a DIFFERENT
/// decision from `is_svg_family`'s translate-wrap inset, so `callout`
/// (internal-SVG, not padding-shrunk) answers `true` here and `false`
/// there.
fn is_internal_svg(chart_type: &str) -> bool {
    matches!(chart_type, "table" | "kpi" | "spark_bar" | "callout")
}

/// Snap a float coordinate to the nearest integer pixel — the port of
/// `svg_utils.px()`: SVG transforms apply before rasterization, so an
/// unrounded ancestor translate lands a 1px structural mark on a fractional
/// row and the rasterizer splits it across two rows at reduced opacity.
/// Round-half-to-even, matching Python's `round()`.
pub(crate) fn px(v: f64) -> f64 {
    let floor = v.floor();
    let diff = v - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    }
}

/// Byte-exact, case-insensitive `dbt charts` search bounded by non-ASCII-word
/// neighbors (a translated footer may run the brand into non-Latin text) — a hand-rolled
/// substitute for a regex crate this one fixed literal doesn't justify
/// adding. Operates on bytes so a multi-byte UTF-8 char elsewhere in `text`
/// can never misalign the returned offsets (the needle and its boundary
/// bytes are pure ASCII, so any matched offset is a valid `str` slice
/// boundary regardless of what else the string contains).
fn find_brand_phrase(text: &str) -> Option<(usize, usize)> {
    let bytes = text.as_bytes();
    let needle = b"dbt charts";
    if bytes.len() < needle.len() {
        return None;
    }
    // The neighbor test is on characters, not bytes, and ASCII-only: the
    // production regex (`FOOTER_BRAND_WORD_RE`) lookarounds on `[A-Za-z0-9_]`.
    let is_word_char = |c: char| c.is_ascii_alphanumeric() || c == '_';
    for start in 0..=(bytes.len() - needle.len()) {
        if bytes[start..start + needle.len()].eq_ignore_ascii_case(needle) {
            let end = start + needle.len();
            let before_ok = text[..start].chars().next_back().is_none_or(|c| !is_word_char(c));
            let after_ok = text[end..].chars().next().is_none_or(|c| !is_word_char(c));
            if before_ok && after_ok {
                return Some((start, end));
            }
        }
    }
    None
}

/// Per-chart slot dims from the item's outer box — the port of
/// `_sweep.py::slot_dims`/production's `additive_padding`: an SVG family
/// (table/kpi/spark_bar) renders at the padding-shrunk inner size, a Vega
/// family renders at the full outer size with padding forwarded in the spec.
fn slot_dims(
    item_w: f64,
    item_h: f64,
    chart_type: &str,
    card_pad: f64,
    layout_padding: &Value,
) -> Result<(f64, f64, Padding), String> {
    let side = |name: &str| -> Result<f64, String> {
        layout_padding
            .get(name)
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("chart layout_padding.{name} missing or not numeric"))
    };
    let padding = Padding {
        left: card_pad + side("left")?,
        right: card_pad + side("right")?,
        top: card_pad + side("top")?,
        bottom: card_pad + side("bottom")?,
    };
    let (w, h) = if is_svg_family(chart_type) {
        (
            (item_w - padding.left - padding.right).max(0.0),
            (item_h - padding.top - padding.bottom).max(0.0),
        )
    } else {
        (item_w, item_h)
    };
    Ok((w, h, padding))
}

/// Height of a rendered chart's own outer `<svg>` — the fact a layout arm
/// reads back (`actual_item_height`) to accumulate its own position, since
/// some families grow past their slot. Prefers `viewBox` over the `height`
/// attribute, matching production's own `extract_svg_dimensions`
/// (`svg_utils.py:215-241`) — a family whose root carries only `viewBox`
/// (every VL family) would otherwise silently read a child element's own
/// `height=` instead of erroring.
pub(crate) fn extract_svg_height(svg: &str) -> Result<f64, String> {
    if let Some(start) = svg.find("viewBox=\"") {
        let rest = &svg[start + "viewBox=\"".len()..];
        let end = rest
            .find('"')
            .ok_or("chart svg viewBox attribute unterminated")?;
        let mut parts = rest[..end].split_whitespace();
        let (_x, _y, _w, h) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts
                .next()
                .ok_or("chart svg viewBox has fewer than 4 numbers")?,
        );
        return h
            .parse::<f64>()
            .map_err(|e| format!("chart svg viewBox height {h:?} not numeric: {e}"));
    }
    let key = "height=\"";
    let start = svg
        .find(key)
        .ok_or("chart svg carries no viewBox or height attribute")?;
    let rest = &svg[start + key.len()..];
    let end = rest
        .find('"')
        .ok_or("chart svg height attribute unterminated")?;
    rest[..end]
        .parse::<f64>()
        .map_err(|e| format!("chart svg height {:?} not numeric: {e}", &rest[..end]))
}

/// Width counterpart to `extract_svg_height`, same `viewBox`-then-`width=`
/// precedence — `render_fitted`'s support_table width correction needs the
/// outer SVG's actual width, not just height, to measure the autosize:pad
/// overhead.
pub(crate) fn extract_svg_width(svg: &str) -> Result<f64, String> {
    if let Some(start) = svg.find("viewBox=\"") {
        let rest = &svg[start + "viewBox=\"".len()..];
        let end = rest
            .find('"')
            .ok_or("chart svg viewBox attribute unterminated")?;
        let mut parts = rest[..end].split_whitespace();
        let (_x, _y, w) = (
            parts.next(),
            parts.next(),
            parts
                .next()
                .ok_or("chart svg viewBox has fewer than 4 numbers")?,
        );
        return w
            .parse::<f64>()
            .map_err(|e| format!("chart svg viewBox width {w:?} not numeric: {e}"));
    }
    let key = "width=\"";
    let start = svg
        .find(key)
        .ok_or("chart svg carries no viewBox or width attribute")?;
    let rest = &svg[start + key.len()..];
    let end = rest
        .find('"')
        .ok_or("chart svg width attribute unterminated")?;
    rest[..end]
        .parse::<f64>()
        .map_err(|e| format!("chart svg width {:?} not numeric: {e}", &rest[..end]))
}

/// The port of `svg_utils.extract_svg_inner_content`: strip the `<svg ...>`
/// wrapper from an SVG-family family's own root, so it can be re-wrapped in
/// the board's `dbt-chart` handle instead of nesting a second `<svg>`
/// viewport.
pub(crate) fn extract_svg_inner_content(svg: &str) -> Result<&str, String> {
    let open_end = svg
        .find('>')
        .ok_or("chart svg malformed: no opening tag close")?
        + 1;
    let close_start = svg
        .rfind("</svg>")
        .ok_or("chart svg malformed: no closing tag")?;
    if close_start < open_end {
        return Err("chart svg malformed: content bounds invalid".to_string());
    }
    Ok(&svg[open_end..close_start])
}

/// The port of `svg_utils.selection_boxes`: two non-painting rects marking
/// the tagged group's own outer/inner boundary, `.dbt-box-outer`
/// padding-included, `.dbt-box-inner` padding-excluded. `mark` is `(y,
/// height)` for the outer rect alone — a block (a heading) that reserved
/// more height than its text fills, so the mark should trace the text's own
/// span rather than the whole reservation; `None` (every chart wrapper's own
/// call) traces the full outer box.
/// `svg_utils.translate_group`: wrap `content` in a translate `<g>` unless
/// the translate is a no-op AND there is nothing else to carry, in which
/// case the content is returned unwrapped — a bare group still counts
/// against a PDF writer's nesting budget. `x`/`y` arrive already
/// pixel-snapped and formatted. `extra_attrs`, like `authored_kind_attr`'s
/// return value, must carry its own leading space when non-empty.
pub(crate) fn translate_group(x: &str, y: &str, content: &str, extra_attrs: &str) -> String {
    if x == "0" && y == "0" && extra_attrs.is_empty() {
        return content.to_string();
    }
    format!(r#"<g transform="translate({x}, {y})"{extra_attrs}>{content}</g>"#)
}

/// `boards.py::_enclosing_svg_key`: a stable id for the `<svg>` element that
/// most tightly encloses byte offset `pos` in `svg` — that element's own
/// opening tag's byte offset (`-1` when `pos` sits outside every `<svg>`
/// element). Two offsets share this key only when they sit inside the
/// exact same `<svg>` element, never merely a shared ancestor.
fn enclosing_svg_key(svg: &str, pos: usize) -> i64 {
    let mut stack: Vec<i64> = Vec::new();
    let mut i = 0usize;
    while i < pos {
        if svg.is_char_boundary(i) && svg[i..].starts_with("</svg>") {
            stack.pop();
            i += "</svg>".len();
            continue;
        }
        // `<svg\b` — Python's regex boundary: "<svg" followed by a
        // non-word character or the end of the string, never `<svgFoo`.
        if svg.is_char_boundary(i) && svg[i..].starts_with("<svg") {
            let after = i + 4;
            let is_boundary = after >= svg.len()
                || (svg.is_char_boundary(after)
                    && !svg[after..]
                        .chars()
                        .next()
                        .is_some_and(|c| c.is_alphanumeric() || c == '_'));
            if is_boundary {
                stack.push(i as i64);
                i = after;
                continue;
            }
        }
        // Advance by one char, never a raw byte, to stay on a UTF-8
        // boundary — board text (titles, footers) is not ASCII-only.
        let ch_len = svg[i..].chars().next().map(char::len_utf8).unwrap_or(1);
        i += ch_len;
    }
    *stack.last().unwrap_or(&-1)
}

/// `boards.py::_DEDUPE_ELIGIBLE_RULE_RE`: `^\s*\.md-[0-9a-f]+-[a-z]+\s*\{`.
fn is_dedupe_eligible_rule(line: &str) -> bool {
    let Some(rest) = line.trim_start().strip_prefix(".md-") else {
        return false;
    };
    let hex_end = rest
        .find(|c: char| !c.is_ascii_hexdigit())
        .unwrap_or(rest.len());
    if hex_end == 0 {
        return false;
    }
    let Some(rest) = rest[hex_end..].strip_prefix('-') else {
        return false;
    };
    let alpha_end = rest
        .find(|c: char| !c.is_ascii_lowercase())
        .unwrap_or(rest.len());
    if alpha_end == 0 {
        return false;
    }
    rest[alpha_end..].trim_start().starts_with('{')
}

/// `boards.py::_dedupe_repeated_style_rules`: collapse byte-identical CSS
/// rule lines repeated across `  <style>\n...\n  </style>` blocks that sit
/// inside the same `<svg>` element, in a fully assembled board SVG. Applied
/// only once every chart/nested board has already rendered its own
/// complete, self-sufficient CSS (called once, at the very end of
/// `render_board`), so this can only ever remove a line already provably
/// present elsewhere in the same scope — see `enclosing_svg_key` for why
/// that scope is "the same enclosing `<svg>`", not "the whole document".
fn dedupe_repeated_style_rules(svg: &str) -> String {
    const OPEN: &str = "  <style>\n";
    const CLOSE: &str = "\n  </style>";
    let mut result = String::with_capacity(svg.len());
    let mut seen: std::collections::HashSet<(i64, &str)> = std::collections::HashSet::new();
    let mut cursor = 0usize;
    loop {
        let Some(open_rel) = svg[cursor..].find(OPEN) else {
            result.push_str(&svg[cursor..]);
            break;
        };
        let open_pos = cursor + open_rel;
        let has_leading_newline = open_pos > 0 && svg.as_bytes()[open_pos - 1] == b'\n';
        let match_start = if has_leading_newline {
            open_pos - 1
        } else {
            open_pos
        };
        result.push_str(&svg[cursor..match_start]);

        let body_start = open_pos + OPEN.len();
        let Some(close_rel) = svg[body_start..].find(CLOSE) else {
            // No closing tag reachable — not a real style block; emit the
            // rest verbatim rather than mis-parse it as one.
            result.push_str(&svg[match_start..]);
            break;
        };
        let body_end = body_start + close_rel;
        let match_end = body_end + CLOSE.len();
        let body = &svg[body_start..body_end];
        let scope = enclosing_svg_key(svg, match_start);

        let mut kept_lines: Vec<&str> = Vec::new();
        for line in body.split('\n') {
            if is_dedupe_eligible_rule(line) {
                let key = (scope, line);
                if seen.contains(&key) {
                    continue;
                }
                seen.insert(key);
            }
            kept_lines.push(line);
        }
        // A fully-emptied block consumes its leading newline too, leaving
        // no blank line behind at its old position.
        if !kept_lines.is_empty() {
            if has_leading_newline {
                result.push('\n');
            }
            result.push_str("  <style>\n");
            result.push_str(&kept_lines.join("\n"));
            result.push_str("\n  </style>");
        }
        cursor = match_end;
    }
    result
}

pub(crate) fn selection_boxes(
    outer_width: f64,
    outer_height: f64,
    padding: &Padding,
    mark: Option<(f64, f64)>,
) -> String {
    let inner_width = (outer_width - padding.left - padding.right).max(0.0);
    let inner_height = (outer_height - padding.top - padding.bottom).max(0.0);
    let (mark_y, mark_height) = match mark {
        Some((y, h)) => (y + padding.top, h),
        None => (0.0, outer_height),
    };
    format!(
        r#"<rect class="dbt-box-outer" x="0" y="{}" width="{}" height="{}" fill="transparent"/><rect class="dbt-box-inner" x="{}" y="{}" width="{}" height="{}" fill="transparent"/>"#,
        num_full(px(mark_y)),
        num_full(px(outer_width)),
        num_full(px(mark_height)),
        num_full(px(padding.left)),
        num_full(px(padding.top)),
        num_full(px(inner_width)),
        num_full(px(inner_height)),
    )
}

/// The port of `svg_utils.padded_authoring_content`: the selection boxes
/// plus `content` translated inward by `padding`, for an SVG family
/// rendered at its un-padded inner size. `mark`: see `selection_boxes`.
pub(crate) fn padded_authoring_content(
    content: &str,
    inner_width: f64,
    inner_height: f64,
    padding: &Padding,
    mark: Option<(f64, f64)>,
) -> String {
    let outer_width = inner_width + padding.left + padding.right;
    let outer_height = inner_height + padding.top + padding.bottom;
    format!(
        "{}{}",
        selection_boxes(outer_width, outer_height, padding, mark),
        translate_group(&num_full(px(padding.left)), &num_full(px(padding.top)), content, "")
    )
}

/// `_ink_carries_padding` (`chart/rendering.py:585-602`): `chart.chart_type
/// in PAINTS_MARKS`, false for a pie with an attached table (the composed
/// canvas carries the padding on its wheel child, not as a whole) and false
/// for `callout` — its card `render_svg_family` returns full-bleed at 0,0
/// with the padding never applied, so claiming an inset here puts the
/// block's pointer target on its own ink, same as the pie case. `table`/
/// `kpi`/`spark_bar` are the rest of `PAINTS_MARKS`'s complement, but never
/// reach this function — `is_svg_family` intercepts them first, above this
/// branch. Every OTHER type reaching here paints marks (bar/line/area/
/// scatter/heatmap/pie/histogram; `geoshape`/`point_map` already refused at
/// `render()` itself), so this function names only the two exceptions.
fn ink_carries_padding(chart_json: &Value, chart_type: &str) -> bool {
    if chart_type == "pie" {
        let has_attached_table = chart_json
            .get("attached_table")
            .is_some_and(|v| !v.is_null());
        return !has_attached_table;
    }
    chart_type != "callout"
}

/// `chart.label if chart.chart_type == "kpi" else chart.title`, falling back
/// to the chart's own id when empty — `chart/rendering.py:564-569`. A title/
/// label containing Jinja syntax refuses: this composer has no template
/// engine, and a plain string with no `{{`/`{%` is exactly what
/// `resolve_jinja_template(text, vars, strict=False)` returns unchanged (no
/// substitution possible without template syntax), so this is a real
/// no-op-when-plain law, not an approximation.
fn chart_title_text(chart_json: &Value, chart_type: &str, id: &str) -> Result<String, String> {
    let source = if chart_type == "kpi" {
        chart_json.get("label").and_then(Value::as_str)
    } else {
        chart_json.get("title").and_then(Value::as_str)
    };
    match source {
        Some(s) if !s.is_empty() => {
            if s.contains("{{") || s.contains("{%") {
                return Err(format!("chart {id} title templating not modeled"));
            }
            Ok(s.to_string())
        }
        _ => Ok(id.to_string()),
    }
}

/// The port of `chart/rendering.py:628-748` (`_wrap_rendered_chart_svg`):
/// the `<g class="dbt-chart" ...>` handle every chart item carries in
/// production, with its selection boxes and (for an SVG family) the nested
/// `<svg>` viewport replaced by `extract_svg_inner_content`'s law. Returns
/// (wrapped_svg, actual_height).
/// `compile/models/board/resolved.py::_is_magnitude_colored`: true when
/// this chart's `color` channel paints a quantitative value — a `gradient`
/// mode ramp, bare `color: <field>` authoring flagged `quantitative_data`
/// by `_flag_quantitative_color`, or a `conditional` mode carrying a
/// continuous `fallback_scale`. Read straight off the chart's own
/// `resolved_channels.color` (already present on every chart item's JSON —
/// the same data every family's own emitter reads to paint the legend).
fn is_magnitude_colored(chart_json: &Value) -> bool {
    let Some(color) = chart_json
        .get("resolved_channels")
        .and_then(|c| c.get("color"))
    else {
        return false;
    };
    let mode = color.get("mode").and_then(Value::as_str);
    if mode == Some("gradient") || color.get("quantitative_data") == Some(&Value::Bool(true)) {
        return true;
    }
    mode == Some("conditional") && !color.get("fallback_scale").is_none_or(Value::is_null)
}

/// `svg_utils.border_rect`: a stroke-only rect tracing a `width` x `height`
/// box, inset by half the stroke so the border stays inside the box; `""`
/// when it draws nothing. Every border production strokes around a box (a
/// chart card, a nested board) goes through this one function.
fn border_rect(width: f64, height: f64, border: &BorderStyleDoc, css_class: &str) -> String {
    let stroke = border.width;
    if stroke <= 0.0 || border.color.is_empty() {
        return String::new();
    }
    let inset = stroke / 2.0;
    let class_attr = if css_class.is_empty() {
        String::new()
    } else {
        format!(r#" class="{}""#, xml_escape_attr(css_class))
    };
    format!(
        concat!(
            r#"<rect{} x="{}" y="{}" width="{}" height="{}" fill="none" "#,
            r#"stroke="{}" stroke-width="{}"{} rx="{}"/>"#
        ),
        class_attr,
        py_float_str(inset),
        py_float_str(inset),
        py_max_zero(width - stroke),
        py_max_zero(height - stroke),
        xml_escape_attr(&border.color),
        py_float_str(stroke),
        crate::common::border_dash_attrs(
            border.dash_array.as_deref(),
            border.line_cap.as_deref(),
            border.dash_offset,
        ),
        py_max_zero(border.radius - inset),
    )
}

/// `svg_utils.round_clipped`: `content` clipped to a rounded box. The clip id
/// is the geometry itself, so a page holding several boards cannot make one
/// clip stand in for another.
fn round_clipped(content: &str, width: f64, height: f64, radius: f64) -> String {
    let clip_id = format!(
        "dbt-card-clip-{}-{}-{}",
        num_full(width),
        num_full(height),
        num_full(radius)
    );
    format!(
        concat!(
            r#"<clipPath id="{id}"><rect width="{w}" height="{h}" rx="{r}"/></clipPath>"#,
            r#"<g clip-path="url(#{id})">{content}</g>"#
        ),
        id = clip_id,
        w = py_float_str(width),
        h = py_float_str(height),
        r = py_float_str(radius),
        content = content,
    )
}

/// `svg_utils.shadow_rect`: the box `fill`ed with the canvas under a
/// `drop-shadow` filter, so the shadow falls outside the box and the card's
/// own paint covers the rest.
fn shadow_rect(
    width: f64,
    height: f64,
    shadow: &BoxShadowDoc,
    fill: &str,
    radius: f64,
) -> Result<String, String> {
    use d3rs::py_float::format_g;
    let color = crate::common::css_color(&shadow.color).map_err(|e| format!("box_shadow: {e}"))?;
    Ok(format!(
        concat!(
            r#"<rect class="dbt-card-shadow" width="{}" height="{}" rx="{}" fill="{}" "#,
            r#"style="filter: drop-shadow({}px {}px {}px {})"/>"#
        ),
        py_float_str(width),
        py_float_str(height),
        py_float_str(radius),
        xml_escape_attr(fill),
        format_g(shadow.dx),
        format_g(shadow.dy),
        format_g(shadow.blur),
        xml_escape_attr(color),
    ))
}

/// `chart/rendering.py::_card_border`: the border framing this chart's card;
/// `None` for a callout, which draws its own. KPI, table, geoshape and
/// point_map carry theirs on the chart's own `style.<type>.border`; every other
/// type takes the board's `chart_defaults.border`.
fn card_border(
    chart_json: &Value,
    chart_type: &str,
    id: &str,
    board_default: &BorderStyleDoc,
) -> Result<Option<BorderStyleDoc>, String> {
    match chart_type {
        "callout" => Ok(None),
        "kpi" | "table" | "geoshape" | "point_map" => {
            let own = chart_json
                .pointer(&format!("/style/{chart_type}/border"))
                .ok_or_else(|| {
                    format!("chart {id:?}: style.{chart_type}.border missing from the chart")
                })?;
            serde_json::from_value(own.clone())
                .map(Some)
                .map_err(|e| format!("chart {id:?}: style.{chart_type}.border: {e}"))
        }
        _ => Ok(Some(board_default.clone())),
    }
}

/// `chart/rendering.py::_wrap_rendered_chart_svg`'s card layers around
/// `boundary`: the shadow rect first, the content clipped to the card's radius
/// when it is above zero, the border rect last. `canvas` is the chart's own
/// composited canvas, `None` for the types that carry none (KPI and callout take
/// `chart_defaults.ink_canvas` for their shadow, `_card_shadow`).
#[allow(clippy::too_many_arguments)]
fn card_layers(
    chart_json: &Value,
    chart_type: &str,
    id: &str,
    canvas: Option<&str>,
    card: &CardChrome,
    boundary: String,
    width: f64,
    height: f64,
) -> Result<String, String> {
    let border = card_border(chart_json, chart_type, id, &card.border)?;
    // A callout's shadow follows its own `style.border.radius`
    // (`_card_shadow`); every other card's follows the border it draws.
    let radius = match (&border, &card.shadow) {
        (Some(border), _) => border.radius,
        (None, Some(_)) => chart_json
            .pointer("/style/border/radius")
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("chart {id:?}: style.border.radius missing from the callout"))?,
        (None, None) => 0.0,
    };
    let mut layers = if radius > 0.0 {
        round_clipped(&boundary, width, height, radius)
    } else {
        boundary
    };
    if let Some(shadow) = &card.shadow {
        let fill = canvas.unwrap_or(&card.ink_canvas);
        layers = shadow_rect(width, height, shadow, fill, radius)? + &layers;
    }
    if let Some(border) = &border {
        layers.push_str(&border_rect(width, height, border, "dbt-card-border"));
    }
    Ok(layers)
}

fn wrap_chart_svg(
    chart_json: &Value,
    chart_type: &str,
    chart_svg: &str,
    outer_width: f64,
    padding: &Padding,
    card: &CardChrome,
) -> Result<(String, f64), String> {
    let id = chart_json
        .get("id")
        .and_then(Value::as_str)
        .ok_or("chart item missing id")?;
    let source_path = chart_json
        .get("source_path")
        .and_then(Value::as_str)
        .unwrap_or("");
    let defined_in_other_file = chart_json
        .get("defined_in_other_file")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let chart_notes = chart_json
        .get("notes")
        .and_then(Value::as_str)
        .unwrap_or("");
    let mut var_deps: Vec<&str> = chart_json
        .get("variable_dependencies")
        .and_then(Value::as_array)
        .map(|arr| arr.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    var_deps.sort_unstable();

    let dims_height = extract_svg_height(chart_svg)?;
    // `rendering.py:618-624`'s own, SEPARATE decision from `is_svg_family`
    // below: `is_internal_svg = chart_type not in PAINTS_MARKS` (never
    // `is_error_fallback` — this composer never reaches the ChartDataError
    // placard path) strips the chart's own root `<svg>` before embedding it
    // in the board's `dbt-chart` wrapper `<g>`, "to avoid a redundant
    // nested `<svg>` viewport" (production's own comment) — independent of
    // whether the content ALSO needs the padding-shrunk translate wrap
    // `is_svg_family` decides just below. `callout` is the one type this
    // composer reaches that is internal-SVG but NOT padding-shrunk
    // (`is_svg_family` false, `is_internal_svg` true): taking only the
    // `is_svg_family` branch here left a composed callout's own root
    // `<svg>` nested inside the wrapper, byte-diverging from production.
    let chart_content =
        if is_internal_svg(chart_type) { extract_svg_inner_content(chart_svg)? } else { chart_svg };
    let (boundary, actual_height) = if is_svg_family(chart_type) {
        let inner_width = outer_width - padding.left - padding.right;
        (
            padded_authoring_content(chart_content, inner_width, dims_height, padding, None),
            dims_height + px(padding.top) + px(padding.bottom),
        )
    } else {
        let ink_inset = if ink_carries_padding(chart_json, chart_type) {
            *padding
        } else {
            Padding {
                left: 0.0,
                right: 0.0,
                top: 0.0,
                bottom: 0.0,
            }
        };
        (
            format!(
                "{}{chart_content}",
                selection_boxes(outer_width, dims_height, &ink_inset, None)
            ),
            dims_height,
        )
    };

    // KPI and callout carry no composited canvas of their own
    // (`ChartIdentity.canvas` is `None`).
    let canvas = if matches!(chart_type, "kpi" | "callout") {
        None
    } else {
        Some(
            chart_json
                .get("canvas")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("chart {id:?} missing canvas"))?,
        )
    };
    let boundary = card_layers(
        chart_json, chart_type, id, canvas, card, boundary, outer_width, actual_height,
    )?;

    let title_text = chart_title_text(chart_json, chart_type, id)?;
    // `rendering.py:677-679`: every `type: callout` chart's wrapper carries a
    // second class, `dbt-chart-callout`; the error placard's wrapper
    // (`wrap_error_card_svg`) carries it separately.
    let class_attr = if chart_type == "callout" {
        r#"class="dbt-chart dbt-chart-callout""#.to_string()
    } else {
        r#"class="dbt-chart""#.to_string()
    };
    let mut attrs = vec![
        class_attr,
        format!(r#"id="chart-{}""#, xml_escape_attr(id)),
        format!(r#"data-chart-id="{}""#, xml_escape_attr(id)),
        format!(r#"data-chart-type="{}""#, xml_escape_attr(chart_type)),
        format!(r#"data-chart-title="{}""#, xml_escape_attr(&title_text)),
        format!(r#"data-chart-width="{}""#, py_float_str(outer_width)),
        format!(r#"data-chart-height="{}""#, py_float_str(actual_height)),
        // `rendering.py:713-716`: `group`, not `img` (an `img` role makes
        // every per-mark `aria-label` presentational).
        r#"role="group""#.to_string(),
        format!(r#"aria-roledescription="{}""#, xml_escape_attr(&display_text::language::tr("aria.chart", None, &[])?)),
        format!(r#"aria-label="{}""#, xml_escape_attr(&title_text)),
    ];
    if !source_path.is_empty() && !defined_in_other_file {
        attrs.push(format!(
            r#"data-authored-path="{}""#,
            xml_escape_attr(source_path)
        ));
        attrs.push(r#"data-authored-kind="chart""#.to_string());
    }
    if !chart_notes.is_empty() {
        attrs.push(format!(
            r#"data-chart-notes="{}""#,
            xml_escape_attr(chart_notes)
        ));
    }
    for v in var_deps {
        attrs.push(format!(r#"data-var-{}="true""#, attr_name(v)?));
    }
    // A JS selection hook, not a visual property: chart_interactivity.js's
    // hover-emphasis reads this off the wrapper instead of sniffing the
    // rendered legend for a gradient node. Absence means safe to recede, so
    // it is only ever emitted, never emitted as "false".
    if is_magnitude_colored(chart_json) {
        attrs.push(r#"data-dbt-magnitude-colored="true""#.to_string());
    }
    if let Some(canvas) = canvas {
        attrs.push(format!(r#"data-dbt-chart-canvas="{}""#, xml_escape_attr(canvas)));
    }
    // `rendering.py:719-724`: the raw subtitle (callout and KPI carry none),
    // written through `escape_attr` even as element text, never a `<title>`
    // (a native hover tooltip) and never an `aria-describedby` (chart ids
    // repeat across boards sharing a page).
    let description = match chart_json.get("subtitle").and_then(Value::as_str) {
        Some(subtitle) if !subtitle.is_empty() => {
            format!("<desc>{}</desc>", xml_escape_attr(subtitle))
        }
        _ => String::new(),
    };
    Ok((
        format!("<g {}>{description}{boundary}</g>", attrs.join(" ")),
        actual_height,
    ))
}

/// `boards.py::_measured_variable_values`, ported: `{**board.variable_
/// defaults, **variables}` — `variables` is the SAME raw request-level
/// overrides dict at every nesting level in production (`render_nested_
/// board`'s own `variables` parameter is the caller's, unchanged), which
/// `BoardPayload.variable_values` already equals for the root (`_title_
/// payload`'s own `{**root.variable_defaults, **root.variable_defaults}`
/// merge, a no-op) — so this needs no extra payload plumbing per node.
fn effective_variable_values(
    board: &BoardDoc,
    payload_vars: &serde_json::Map<String, Value>,
) -> serde_json::Map<String, Value> {
    let mut merged = board.variable_defaults.clone();
    merged.extend(payload_vars.iter().map(|(k, v)| (k.clone(), v.clone())));
    merged
}

/// `boards.py::_nested_variables_path`: empty in, empty out — `source_path`
/// already means "no authored coordinates in this file" for an imported
/// board, and an inline one extends by the single key this band draws.
fn nested_variables_path(source_path: &str) -> String {
    if source_path.is_empty() {
        String::new()
    } else {
        format!("{source_path}.variables")
    }
}

/// `conditions.py::evaluate_visible`: a missing `visible` renders
/// (`if_none=True`), the rest is `eval_bool_condition` over the request's
/// variable values.
fn item_visible(
    visible: &Option<Value>,
    variable_values: &serde_json::Map<String, Value>,
) -> Result<bool, String> {
    eval_bool_condition(
        visible.as_ref().unwrap_or(&Value::Null),
        true,
        variable_values,
        "board item visible",
    )
}

struct RenderedItem {
    svg: String,
    height: f64,
}

/// Chrome facts every render_* arm below needs beyond the boxes
/// themselves: the board's background (painted by both the layout bg rect
/// and each chart's own card), card padding, and card_gap — the one
/// board-config constant threaded UNCHANGED through every nesting level in
/// production (`_calculate_nested_board_layout`'s own `card_gap` parameter
/// is the caller's, never re-read per nested board), so it lives here
/// rather than as a `render_rows`-only parameter now that a board item can
/// recurse.
struct RenderChrome<'a> {
    bg: &'a str,
    /// The layout's own background rect fill: the board's background on the root,
    /// empty on a nested board, whose own `bg_rect` already paints it.
    layout_bg: &'a str,
    card_pad: f64,
    card_gap: f64,
    /// The gap `render_rows` accumulates between stacked items — NOT always
    /// `layout.gap` (the JSON field, `ResolvedLayout.gap`/`compile.sizing.
    /// get_board_gap`'s baked theme value): correct for the ROOT board,
    /// whose sizing pass reads the SAME `board.layout.gap` (`layout_sizing.
    /// py::calculate_data_aware_layout`'s own `gap = get_board_gap(board)`),
    /// but WRONG for a nested board, whose render (`render_nested_board`)
    /// and sizing (`nested_board_sizing_context`) both read `resolved_style.
    /// gap` instead — a real production asymmetry (`_calculate_nested_
    /// board_layout` never reads `layout.gap` at all). Threaded explicitly
    /// so `render_rows` never reads the wrong source for either caller.
    item_gap: f64,
    /// The ROOT board's own `resolved_style.frame.card_padding` —
    /// CONSTANT through the whole tree, unlike `card_pad` above (each
    /// nested board's own). `render/chart/rendering.py::render_layout_item`
    /// reads `card_padding` off whatever `resolved_style` recursion handed
    /// it (the nested board's own, for `additive_padding`/the chart
    /// wrapper's selection boxes — `card_pad` above is exact there), but
    /// `layout_sizing.py::_make_data_aware_height_provider`'s render-first
    /// Vega height closure is built ONCE, over the ROOT's OWN `card_
    /// padding`, and reused unchanged for every chart in the whole
    /// recursive tree (`_vertical_inset`'s own `card_padding` closure
    /// capture) — a genuine, deliberate production asymmetry between the
    /// sizing pass (always root's) and the render pass (each board's own).
    /// `render_chart_item` never reads this field itself (only `slot_dims`'
    /// `card_pad` does); it exists solely to thread the correct value into
    /// each nested `SizingCtx.card_padding` a board item's own recursion
    /// builds (`render_nested_board`/`nested_board_total_height`).
    root_card_padding: f64,
    /// `layouts.py::render_details_summary`'s own theme-constant reads,
    /// resolved once per level from THIS layout's own `resolved_style` —
    /// see `details_chrome_from_style`'s own doc.
    details_style: DetailsChrome,
    /// `layouts.py::render_tabs_layout`'s own theme-constant reads — see
    /// `tabs_chrome_from_style`'s own doc.
    tabs_style: TabsChrome,
    /// The style facts every chart's card wrapper reads off THIS level's
    /// `resolved_style` — see `CardChrome`.
    card_style: CardChrome,
}

/// The board-level style `rendering.py::_card_border`/`_card_shadow` read
/// for a chart's card: `chart_defaults.border`, `box_shadow` and
/// `chart_defaults.ink_canvas`, resolved ONCE per nesting level like
/// `DetailsChrome`.
#[derive(Clone)]
struct CardChrome {
    border: BorderStyleDoc,
    shadow: Option<BoxShadowDoc>,
    ink_canvas: String,
}

fn card_chrome_from_style(style: &StyleDoc) -> CardChrome {
    CardChrome {
        border: style.chart_defaults.border.clone(),
        shadow: style.box_shadow.clone(),
        ink_canvas: style.chart_defaults.ink_canvas.clone(),
    }
}

/// `layouts.py::render_details_summary`'s theme constants, resolved ONCE
/// per `RenderChrome` build (one per nesting level, matching `bg`/
/// `card_pad` above) rather than re-read off `StyleDoc` at every details
/// item — `resolved_style.layout.details`/`.title.font.color`/`.border.
/// color`/`.chart_defaults.table.{header,row}` are all THIS level's own
/// style, matching every render call in production reading its own
/// `resolved_style` parameter.
#[derive(Clone)]
struct DetailsChrome {
    summary_height: f64,
    border_color: String,
    border_width: f64,
    border_radius: f64,
    border_dash_attrs: String,
    arrow_x: f64,
    arrow_font_size: f64,
    label_x: f64,
    font_size: f64,
    text_baseline_offset: f64,
    content_y_offset: f64,
    expanded_fill: Option<String>,
    collapsed_fill: Option<String>,
    /// `resolved_style.title.font.color` — Python interpolates this
    /// directly into an f-string with no `is None` guard, so a theme
    /// leaving it unset would literally paint the string `"None"` (Python's
    /// own `str(None)`); no built-in theme reaches that shape, but this
    /// mirrors the literal behavior rather than silently defaulting.
    title_font_color: String,
}

fn details_chrome_from_style(style: &StyleDoc) -> DetailsChrome {
    let details = &style.layout.details;
    DetailsChrome {
        summary_height: details.summary_height,
        // Stroke COLOR is the board's own top-level `resolved_style.border.
        // color`, not `details.border.color` — width/dash/radius are the
        // details style's own (`layouts.py::render_details_summary`'s
        // literal `stroke="{resolved_style.border.color}" stroke-width=
        // "{details_config.border.width}"` mixed-source read).
        border_color: style.border.color.clone(),
        border_width: details.border.width,
        border_radius: details.border.radius,
        border_dash_attrs: crate::common::border_dash_attrs(
            details.border.dash_array.as_deref(),
            details.border.line_cap.as_deref(),
            details.border.dash_offset,
        ),
        arrow_x: details.arrow.x,
        arrow_font_size: details.arrow.font.size,
        label_x: details.label_x,
        font_size: details.font.size,
        text_baseline_offset: details.text_baseline_offset,
        content_y_offset: details.content_y_offset,
        expanded_fill: style.chart_defaults.table.header.background.clone(),
        collapsed_fill: style.chart_defaults.table.row.stripe.as_ref().map(|s| s.color.clone()),
        title_font_color: style
            .title
            .font
            .color
            .clone()
            .unwrap_or_else(|| "None".to_string()),
    }
}

/// `layouts.py::render_tabs_layout`'s theme constants — see
/// `details_chrome_from_style`'s own doc for why these are resolved once
/// per level rather than per tab item.
#[derive(Clone)]
struct TabsChrome {
    bar_height: f64,
    border_color: String,
    border_width: f64,
    border_dash_attrs: String,
    font_size: f64,
    active_weight: String,
    inactive_weight: String,
    title_baseline_offset: f64,
    active_fill: Option<String>,
    inactive_fill: Option<String>,
    /// `resolved_style.title.font.color` (active tab) — same literal-`None`
    /// caveat as `DetailsChrome::title_font_color`.
    active_font_color: String,
    /// `resolved_style.variables.font.color` (inactive tab).
    inactive_font_color: String,
}

fn tabs_chrome_from_style(style: &StyleDoc) -> TabsChrome {
    let tabs = &style.layout.tabs;
    TabsChrome {
        bar_height: tabs.bar_height,
        // Same mixed-source stroke read as `DetailsChrome` — see its own
        // doc. A tab rect never carries a corner radius at all (no `rx=`
        // attribute in production's own `_tab_rect` markup, unlike a
        // details summary rect), so `tabs.border.radius` is never read.
        border_color: style.border.color.clone(),
        border_width: tabs.border.width,
        border_dash_attrs: crate::common::border_dash_attrs(
            tabs.border.dash_array.as_deref(),
            tabs.border.line_cap.as_deref(),
            tabs.border.dash_offset,
        ),
        font_size: tabs.font.size,
        active_weight: tabs.active_weight.clone(),
        inactive_weight: tabs.inactive_weight.clone(),
        title_baseline_offset: tabs.title_baseline_offset,
        active_fill: style.chart_defaults.table.header.background.clone(),
        inactive_fill: style.chart_defaults.table.row.stripe.as_ref().map(|s| s.color.clone()),
        active_font_color: style
            .title
            .font
            .color
            .clone()
            .unwrap_or_else(|| "None".to_string()),
        inactive_font_color: style
            .variables
            .font_color()
            .map(str::to_string)
            .unwrap_or_else(|| "None".to_string()),
    }
}

/// Render one layout item — chart or nested board (`render/chart/
/// rendering.py`'s own item-type-agnostic dispatch). `Ok(None)` means
/// production skips it (and its gap) entirely — `evaluate_visible` false,
/// `chart/rendering.py:107-109` / `layouts.py`'s `if item_svg:` guards at
/// every layout arm.
fn render_layout_item(
    item: &ItemDoc,
    item_box: &ItemBox,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
    fresh_render: &HashSet<String>,
) -> Result<Option<RenderedItem>, String> {
    // chart/rendering.py:107-109 evaluates visible before any other work —
    // an invisible item is free, so this check comes before kind/details.
    if !item_visible(&item.visible, &payload.variable_values)? {
        return Ok(None);
    }
    // chart/rendering.py:116 gates the details branch on BOTH fields; either
    // alone falls through to the chart/board branch below. Applies to
    // either item kind — a details section on a CHART item still refuses
    // (no `render_nested_board` for it to recurse into; production's own
    // `if expanded and item.board:` else-branch degrades to the collapsed
    // summary bar even when expanded, a shape no ladder/corpus row
    // authors and this composer refuses rather than approximate).
    if item.details_present() {
        if item.kind.as_str() != "board" {
            return Err("board details section not modeled".to_string());
        }
        return render_details_item(
            item,
            item_box,
            payload,
            chrome,
            fonts,
            chart_height_overrides,
        )
        .map(Some);
    }
    match item.kind.as_str() {
        "chart" => {
            render_chart_item(item, item_box, payload, chrome, fonts, fresh_render)
                .map(Some)
        }
        "board" => {
            let board_json = item
                .board
                .as_ref()
                .ok_or("board item missing its resolved board")?;
            render_nested_board(
                board_json,
                &item.source_path,
                item_box.width,
                item_box.height,
                chrome.card_gap,
                chrome.root_card_padding,
                payload,
                fonts,
                chart_height_overrides,
                item.title_shift,
            )
            .map(Some)
        }
        other => Err(format!("board item type not modeled: {other}")),
    }
}

/// `chart/rendering.py::render_layout_item`'s details branch, restricted
/// to a `type: board` item (the only kind `render_layout_item` still lets
/// reach here): renders the summary bar (`layouts.py::render_details_
/// summary`) always, then — only when expanded — the nested board's own
/// content beneath it, wrapped in a border rect spanning the whole
/// section. `item_box.height` already has the details chrome subtracted
/// by the CALLER's own sizing pass (`sizing::item_height`'s board-details
/// arm), so the nested board renders at exactly `item_box.height -
/// details_chrome_height`, matching production's own `content_height =
/// height - details_chrome_height(...)`.
fn render_details_item(
    item: &ItemDoc,
    item_box: &ItemBox,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
) -> Result<RenderedItem, String> {
    let details = &chrome.details_style;
    let summary_height = details.summary_height;
    let width = item_box.width;
    let expanded = item_details_expanded(item, payload)?;
    let summary_svg = render_details_summary(item, payload, width, expanded, details)?;

    if !expanded {
        return Ok(RenderedItem {
            svg: summary_svg,
            height: summary_height,
        });
    }

    let board_json = item.board.as_ref().ok_or("board item missing its resolved board")?;
    let chrome_height = summary_height + chrome.item_gap + chrome.card_gap;
    let content_height = (item_box.height - chrome_height).max(0.0);
    let nested = render_nested_board(
        board_json,
        &item.source_path,
        width,
        content_height,
        chrome.card_gap,
        chrome.root_card_padding,
        payload,
        fonts,
        chart_height_overrides,
        // A details section draws its summary bar, not a title, so the row
        // assigns it no shift (`layout_sizing.py::_title_band_target`).
        crate::TitleShift::default(),
    )?;
    let content_y = summary_height + details.content_y_offset;
    // `chart/rendering.py:152-154`: `float(px(summary_height +
    // content_y_offset) + board_actual_height)` -- px() rounds the summary
    // band before adding the nested board's own height, not after.
    let actual_height = px(content_y) + nested.height;
    let section_border = format!(
        r#"<rect x="0" y="0" width="{}" height="{}" fill="none" stroke="{}" stroke-width="{}"{} rx="{}"/>"#,
        py_float_str(width),
        py_float_str(actual_height),
        xml_escape_attr(&details.border_color),
        py_float_str(details.border_width),
        details.border_dash_attrs,
        py_float_str(details.border_radius),
    );
    let svg = format!(
        "{section_border}{summary_svg}{}",
        translate_group("0", &num_full(px(content_y)), &nested.svg, "")
    );
    Ok(RenderedItem {
        svg,
        height: actual_height,
    })
}

/// `render/sizing.py::_is_details_item_expanded`'s "live variable value"
/// branch — the only one this port reaches, matching `sizing::item_
/// details_expanded`'s own doc for why the `board.meta` fallback is not
/// ported. Same lookup, duplicated here rather than shared, since the
/// render side reads `ItemDoc`/`BoardPayload` and the sizing side reads
/// `LayoutItemInput`/`SizingCtx` — two different structs over the same
/// underlying JSON, not two independent laws.
fn item_details_expanded(item: &ItemDoc, payload: &BoardPayload) -> Result<bool, String> {
    let details_variable = item
        .details_variable
        .as_deref()
        .expect("details_present implies details_variable is Some");
    match payload.variable_values.get(details_variable) {
        Some(Value::Bool(b)) => Ok(*b),
        Some(Value::String(s)) => Ok(s.eq_ignore_ascii_case("true")),
        None => Err("board details expanded-state fallback (board.meta) not ported".to_string()),
        Some(other) => Err(format!(
            "board details expanded-state variable of type {other:?} not modeled"
        )),
    }
}

/// `layouts.py::render_details_summary`: the collapsible section's own
/// summary bar (disclosure arrow + label), rendered as a clickable
/// `<a href="...">` — always emitted, collapsed or expanded.
fn render_details_summary(
    item: &ItemDoc,
    payload: &BoardPayload,
    width: f64,
    expanded: bool,
    details: &DetailsChrome,
) -> Result<String, String> {
    let details_variable = item
        .details_variable
        .as_deref()
        .expect("details_present implies details_variable is Some");
    let arrow = if expanded { "\u{25bc}" } else { "\u{25b6}" };
    // `layouts.py:486`: a bare conditional, no fallback -- `normalize/
    // layout.py:494` always fills `details_expanded_summary`, so there is
    // no unauthored case for a fallback to cover.
    let label = if expanded {
        item.details_expanded_summary.as_deref()
    } else {
        item.details_summary.as_deref()
    }
    .unwrap_or("");
    let new_value = if expanded { "false" } else { "true" };
    let href = build_toggle_url(&payload.variable_values, details_variable, new_value);
    let fill = if expanded { &details.expanded_fill } else { &details.collapsed_fill };
    let fill_attr = fill.as_deref().unwrap_or("none");
    let summary_rect = format!(
        r#"<rect x="0" y="0" width="{}" height="{}" fill="{fill_attr}" stroke="{}" stroke-width="{}"{} rx="{}" class="dbt-details-toggle"/>"#,
        py_float_str(width),
        py_float_str(details.summary_height),
        xml_escape_attr(&details.border_color),
        py_float_str(details.border_width),
        details.border_dash_attrs,
        py_float_str(details.border_radius), fill_attr = xml_escape_attr(fill_attr),
    );
    Ok(format!(
        r#"<a href="{}">{summary_rect}<text x="{}" y="{}" font-size="{}" fill="{}">{arrow}</text><text x="{}" y="{}" font-size="{}" fill="{}" font-weight="500">{}</text></a>"#,
        xml_escape_attr(&href),
        py_float_str(details.arrow_x),
        py_float_str(details.summary_height / 2.0 + details.text_baseline_offset),
        py_float_str(details.arrow_font_size),
        xml_escape_attr(&details.title_font_color),
        py_float_str(details.label_x),
        py_float_str(details.summary_height / 2.0 + details.text_baseline_offset),
        py_float_str(details.font_size),
        xml_escape_attr(&details.title_font_color),
        html_escape(label),
    ))
}

/// `layouts.py::_build_toggle_url`: change one URL param while preserving
/// every other current variable value — non-`null` values only, matching
/// `{k: str(v) for k, v in variables.items() if v is not None}`, then
/// `params[param_name] = new_value`. Python dict order is INSERTION order:
/// an existing key is updated in place (position unchanged), a new one is
/// appended last — `variables` is `serde_json::Map` (the crate's
/// `preserve_order` feature backs it with an insertion-ordered map), so
/// iterating it and updating `params` in place the same way reproduces that
/// order exactly. No sort — `urlencode` walks the dict as given, and an
/// alphabetical sort here would silently reorder every multi-variable
/// board's toggle-link query string relative to production's.
fn build_toggle_url(
    variables: &serde_json::Map<String, Value>,
    param_name: &str,
    new_value: &str,
) -> String {
    let mut params: Vec<(String, String)> = variables
        .iter()
        .filter(|(_, v)| !v.is_null())
        .map(|(k, v)| (k.clone(), jinja_query_value_str(v)))
        .collect();
    if let Some(entry) = params.iter_mut().find(|(k, _)| k == param_name) {
        entry.1 = new_value.to_string();
    } else {
        params.push((param_name.to_string(), new_value.to_string()));
    }
    let query = params
        .iter()
        .map(|(k, v)| format!("{}={}", url_encode(k), url_encode(v)))
        .collect::<Vec<_>>()
        .join("&");
    format!("?{query}")
}

/// Python's bare `str(value)` for a URL query param — mirrors `title::
/// jinja_value_str`'s own stringification (identical scalar shapes: a
/// tab/details variable is always a string or bool at compile time), but
/// stays local rather than depending on `title.rs` for one small helper.
fn jinja_query_value_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// `urllib.parse.urlencode`'s per-value percent-encoding (query-string
/// safe set: alphanumerics and `-_.~` pass through unescaped, space
/// becomes `+`, everything else is `%XX`) — the same encoding Python's
/// `urlencode` applies to both keys and values.
fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// `_docs_site.py::DEFAULT_DOCS_SITE_URL`.
const DEFAULT_DOCS_SITE_URL: &str = "https://docs.dbtcharts.com";

/// `_docs_site.py::docs_site_url` — `DCT_DOCS_URL` overrides the shipped
/// default when set to a non-empty value.
fn docs_site_url() -> String {
    let raw = std::env::var("DCT_DOCS_URL").unwrap_or_default();
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        DEFAULT_DOCS_SITE_URL.to_string()
    } else {
        trimmed.trim_end_matches('/').to_string()
    }
}

/// `diagnostics/registry.py::build_doc_url` — a pure function of the code
/// string alone, never the registry, so it applies identically to a real
/// registered diagnostic's code AND to this composer's own invented
/// `ERR-ENGINE-REFUSED` code. This is the code badge's own anchor
/// (`callout.py::render_callout_svg`'s `code_svg`); the separate `doc_url`
/// parameter on `render_error_card_svg` below is the REGISTRY's own
/// `doc_url` (equal to this for a real diagnostic, absent for Rust's own
/// invented one) that feeds the "See the docs" link instead.
fn build_doc_url(code: &str) -> Result<String, String> {
    let page = if code.starts_with("ERR-") {
        "errors"
    } else if code.starts_with("WARN-") {
        "warnings"
    } else {
        return Err(format!(
            "code {code:?} has no recognized ERR-/WARN- prefix; cannot build a doc URL"
        ));
    };
    Ok(format!(
        "{}/reference/{page}/#{}",
        docs_site_url(),
        code.to_lowercase()
    ))
}

/// `diagnostics/diagnostic.py::display_message` — appends `" (query:
/// <name>)"` when the diagnostic names a query and the raw message doesn't
/// already carry that suffix. Every human-facing presentation surface
/// (production's own doc: CLI panels, the SVG error placard) must call
/// this instead of reading `.message` directly.
fn display_message(message: &str, query: Option<&str>) -> String {
    let Some(q) = query.filter(|q| !q.is_empty()) else {
        return message.to_string();
    };
    let suffix = format!(" (query: {q})");
    if message.ends_with(&suffix) {
        message.to_string()
    } else {
        format!("{message}{suffix}")
    }
}

/// `resolved_style.chart_defaults.callout_error` off the ROOT board's own
/// JSON — read fresh per refusal (rare path) rather than threaded through
/// `BoardPayload` as a typed field. Root-level, not the current nesting
/// level's own resolved_style, the same simplification `chart_payload.
/// board_style` above already makes for every nested chart.
fn callout_error_style(payload: &BoardPayload) -> Result<&Value, String> {
    payload
        .resolved
        .get("style")
        .and_then(|s| s.get("chart_defaults"))
        .and_then(|c| c.get("callout_error"))
        .ok_or_else(|| "board payload missing style.chart_defaults.callout_error".to_string())
}

/// What differs between the two refusal kinds' placards: `title`/`hint`/
/// `doc_url` are `None` for a render-time refusal, which carries no
/// registered diagnostic to source them from, and `Some` — the registered
/// title/hint/doc_url — for a `chart_error` resolve failure. The code badge's
/// anchor always resolves via `build_doc_url`, independent of `doc_url`,
/// matching production's own two separate expressions (coincidentally equal
/// for a real diagnostic).
struct ErrorCard<'a> {
    chart_id: &'a str,
    title: Option<&'a str>,
    code: &'a str,
    message: &'a str,
    hint: Option<&'a str>,
    doc_url: Option<&'a str>,
}

/// A refused chart's own placard, drawn by the callout family's
/// `markdown=False` path (`callout.py::render_callout_svg`) — the same card
/// production paints from both `_build_error_callout_svg` sites.
fn render_error_card_svg(
    width: f64,
    height: f64,
    card: &ErrorCard,
    style: &Value,
    fonts_dir: Option<&str>,
    fonts: &FontBook,
) -> Result<String, String> {
    let resolved = serde_json::json!({
        "id": card.chart_id,
        "title": card.title,
        "message": card.message,
        "style": style,
        "placard": {
            "code": card.code,
            "code_url": build_doc_url(card.code)?,
            "hint": card.hint,
            "doc_url": card.doc_url,
        },
    });
    let placard = crate::Payload {
        cell_text: Vec::new(),
        max_template_output_bytes: None,
        resolved,
        rows: Vec::new(),
        width,
        height,
        background: None,
        fonts_dir: fonts_dir.map(str::to_string),
        padding: None,
        board_style: None,
        language: None,
        datasets: None,
        variables: Default::default(),
        filter_variables: Default::default(),
        interactive_controls: Default::default(),
        link_context: None,
        measuring: false,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    crate::families::callout::render(&placard, fonts).map(|(svg, _)| svg)
}

/// Wraps a `render_error_card_svg` output the same way `wrap_chart_svg`
/// wraps a real chart's, restricted to the zero-ink-inset shape production's
/// own `_wrap_rendered_chart_svg` always takes on its `is_error_fallback`
/// path (`inset=None`, `ink_inset=_ZERO_PADDING`) — the card paints full-
/// bleed, so there is no padding to inset selection boxes by. `resolved_
/// title` is unconditionally `chart_id` on both of production's own error
/// call sites (`_render_callout_block`/`_render_chart_resolve_failure` each
/// pass `resolved_title=<...>.id`), so this needs no separate title input.
fn wrap_error_card_svg(
    chart_id: &str,
    chart_type: &str,
    source_path: &str,
    defined_in_other_file: bool,
    notes: &str,
    var_deps: &[&str],
    card_svg: &str,
    outer_width: f64,
) -> Result<(String, f64), String> {
    let dims_height = extract_svg_height(card_svg)?;
    let content = extract_svg_inner_content(card_svg)?;
    let zero = Padding {
        left: 0.0,
        right: 0.0,
        top: 0.0,
        bottom: 0.0,
    };
    let boundary = format!(
        "{}{content}",
        selection_boxes(outer_width, dims_height, &zero, None)
    );
    let mut attrs = vec![
        r#"class="dbt-chart dbt-chart-callout""#.to_string(),
        format!(r#"id="chart-{}""#, xml_escape_attr(chart_id)),
        format!(r#"data-chart-id="{}""#, xml_escape_attr(chart_id)),
        format!(r#"data-chart-type="{}""#, xml_escape_attr(chart_type)),
        format!(r#"data-chart-title="{}""#, xml_escape_attr(chart_id)),
        format!(r#"data-chart-width="{}""#, py_float_str(outer_width)),
        format!(r#"data-chart-height="{}""#, py_float_str(dims_height)),
        // `rendering.py:713-716`: `group`, not `img` (an `img` role makes
        // every per-mark `aria-label` presentational).
        r#"role="group""#.to_string(),
        format!(r#"aria-roledescription="{}""#, xml_escape_attr(&display_text::language::tr("aria.chart", None, &[])?)),
        format!(r#"aria-label="{}""#, xml_escape_attr(chart_id)),
    ];
    if !source_path.is_empty() && !defined_in_other_file {
        attrs.push(format!(
            r#"data-authored-path="{}""#,
            xml_escape_attr(source_path)
        ));
        attrs.push(r#"data-authored-kind="chart""#.to_string());
    }
    if !notes.is_empty() {
        attrs.push(format!(r#"data-chart-notes="{}""#, xml_escape_attr(notes)));
    }
    for v in var_deps {
        attrs.push(format!(r#"data-var-{}="true""#, attr_name(v)?));
    }
    Ok((
        format!("<g {}>{boundary}</g>", attrs.join(" ")),
        dims_height,
    ))
}

/// The chart-item render body — `Ok`, never `Option`: visibility/details
/// are already checked by `render_layout_item` before this is called. A
/// chart that never resolved (`item.chart_error`) or whose own family
/// refuses at render time no longer fails the whole board (item b) —
/// `render_error_card_svg` paints its slot instead, and the outcome is
/// recorded on `payload.report` either way.
fn render_chart_item(
    item: &ItemDoc,
    item_box: &ItemBox,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    fresh_render: &HashSet<String>,
) -> Result<RenderedItem, String> {
    let chart_bg = chrome.bg;
    let card_pad = chrome.card_pad;
    let chart_json = match &item.chart {
        Some(c) => c,
        None if item.chart_error.is_some() => {
            let failure = item
                .chart_error
                .as_ref()
                .expect("checked by the guard above");
            let identity = failure
                .get("identity")
                .ok_or("chart_error missing identity")?;
            let diagnostic = failure
                .get("diagnostic")
                .ok_or("chart_error missing diagnostic")?;
            let chart_id = identity
                .get("id")
                .and_then(Value::as_str)
                .ok_or("chart_error identity missing id")?;
            let code = diagnostic
                .get("code")
                .and_then(Value::as_str)
                .ok_or("chart_error diagnostic missing code")?;
            let message = diagnostic
                .get("message")
                .and_then(Value::as_str)
                .ok_or("chart_error diagnostic missing message")?;
            // `_callout_error_title(code)`/`REGISTRY.get(code).doc_url` — no
            // diagnostics registry exists in Rust to source these from, so
            // `board_sweep.py`'s harness forwards them alongside the rest
            // of `diagnostic` (HIGH 2); read by name, refuse by name when
            // absent rather than painting a placard with no title.
            let title = diagnostic
                .get("title")
                .and_then(Value::as_str)
                .ok_or("chart_error diagnostic missing title")?;
            let doc_url = diagnostic
                .get("doc_url")
                .and_then(Value::as_str)
                .ok_or("chart_error diagnostic missing doc_url")?;
            // `hint`/`query` are already on `Diagnostic` itself — `str | None`,
            // so absence is a normal, un-forwarded-by-name shape, not a
            // missing seam.
            let hint = diagnostic.get("hint").and_then(Value::as_str);
            let query = diagnostic.get("query").and_then(Value::as_str);
            let display_msg = display_message(message, query);
            let source_path = identity
                .get("source_path")
                .and_then(Value::as_str)
                .unwrap_or("");
            let defined_in_other_file = identity
                .get("defined_in_other_file")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let mut var_deps: Vec<&str> = identity
                .get("variable_dependencies")
                .and_then(Value::as_array)
                .map(|arr| arr.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            var_deps.sort_unstable();
            let notes = identity.get("notes").and_then(Value::as_str).unwrap_or("");
            let chart_type = identity
                .get("chart_type")
                .and_then(Value::as_str)
                .ok_or("chart_error identity missing chart_type")?;

            let card = render_error_card_svg(
                item_box.width,
                item_box.height,
                &ErrorCard {
                    chart_id,
                    title: Some(title),
                    code,
                    message: &display_msg,
                    hint,
                    doc_url: Some(doc_url),
                },
                callout_error_style(payload)?,
                payload.fonts_dir.as_deref(),
                fonts,
            )?;
            let (wrapped, actual_height) = wrap_error_card_svg(
                chart_id,
                chart_type,
                source_path,
                defined_in_other_file,
                notes,
                &var_deps,
                &card,
                item_box.width,
            )?;
            payload.report.borrow_mut().push(ChartStatusEntry {
                chart_id: chart_id.to_string(),
                status: "refused",
                code: Some(code.to_string()),
                message: Some(message.to_string()),
                warnings: Vec::new(),
                engine_refused: false,
            });
            return Ok(RenderedItem {
                svg: wrapped,
                height: actual_height,
            });
        }
        None => return Err("chart item missing its resolved chart".to_string()),
    };
    let chart_id = chart_json
        .get("id")
        .and_then(Value::as_str)
        .ok_or("chart item missing id")?;
    let chart_type = chart_json
        .get("chart_type")
        .and_then(Value::as_str)
        .ok_or("chart item missing chart_type")?;
    // `ResolvedCalloutChart.query_name` is always `None` (callout has no
    // query — `render/chart/callout.py`'s docstring) — the only family
    // with no dataset to look up at all, so it skips the query_name/
    // datasets lookup below entirely rather than forwarding an empty
    // placeholder into `payload.chart_datasets`.
    let static_rows: Vec<serde_json::Map<String, Value>> = Vec::new();
    let (rows, datasets) = if chart_type == "callout" {
        (&static_rows, None)
    } else {
        let query_name = chart_json
            .get("query_name")
            .and_then(Value::as_str)
            .ok_or("chart item missing query_name")?;
        let ds = payload
            .chart_datasets
            .get(chart_id)
            .ok_or_else(|| format!("no datasets forwarded for chart {chart_id}"))?;
        let r = ds
            .get(query_name)
            .ok_or_else(|| format!("chart {chart_id}: dataset {query_name} not forwarded"))?;
        (r, Some(ds))
    };
    let layout_padding = chart_json
        .get("layout_padding")
        .ok_or("chart item missing layout_padding")?;
    let (slot_w, slot_h, padding) = slot_dims(
        item_box.width,
        item_box.height,
        chart_type,
        card_pad,
        layout_padding,
    )?;

    // A Vega-family chart's own INK renders at whatever padding production's
    // render-first SIZING pass used, not this item's own wrapper padding:
    // `render_cache` is keyed by `(chart_id, width, height)` alone — no
    // padding component (`chart/rendering.py::_render_chart_item_inner`'s
    // `cache_key`) — so once the sizing-time render is cached (always true
    // for a Vega chart under `render_first=True`, by construction: the
    // final box height IS that render's own `actual_height`), a nested
    // board's own `card_padding` override never reaches the spec — only
    // the wrapper's own selection boxes (built fresh every render, from
    // THIS call's own `padding`) see it. `chrome.root_card_padding`
    // reproduces that cache-reuse exactly: the SAME value `layout_sizing.
    // py::_vertical_inset`'s sizing closure captured once at the root.
    // SVG families are never cached this way (`if not is_svg_family and
    // cache_key in render_cache`), so they always use the wrapper's own.
    let spec_padding = if is_svg_family(chart_type) {
        padding
    } else {
        let (_, _, p) = slot_dims(
            item_box.width,
            item_box.height,
            chart_type,
            chrome.root_card_padding,
            layout_padding,
        )?;
        p
    };

    let chart_payload = Payload {
        cell_text: Vec::new(),
        // The composer sizes no custom chart (`sizing`), so no template runs here.
        max_template_output_bytes: None,
        resolved: chart_json.clone(),
        rows: rows.clone(),
        width: slot_w,
        height: slot_h,
        background: Some(chart_bg.to_string()),
        fonts_dir: payload.fonts_dir.clone(),
        // kpi/table read this to grow their own card-fill rect back out
        // over the padding their render box was shrunk by (`common::
        // card_box`, production's `inset` param — `render_svg_family(...,
        // inset=padding if chart_type in SVG_LAYOUT_PADDED_TYPES else
        // None)`, `vega_lite.py:114`); spark_bar is also `is_svg_family`
        // but reads no `inset` on either side (`session.py` passes none to
        // `render_spark_bar_svg`, and `spark_bar.rs` never reads `payload.
        // padding`) — it has no card-fill rect to grow. A Vega family
        // reads this field to bake the SAME padding into its own spec.
        padding: Some(spec_padding),
        board_style: Some(payload.board_style.clone()),
        language: None,
        datasets: datasets.cloned(),
        // The real paint: `slot_h` is the already-decided box height, not
        // a static estimate to grow from — see `Payload::measuring`. The
        // ONE exception is a cols-row chart `sizing::calculate_cols_boxes`
        // force-realigned away from its own pristine natural: production
        // gives that a FRESH render at the row height (a `render_cache`
        // miss, `_align_cols_heights`), so the probe-wrap law must fire on
        // it again, the same as the sizing probe's own first render.
        variables: payload.variable_values.clone(),
        filter_variables: Default::default(),
        title_shift: item.title_shift,
        interactive_controls: Default::default(),
        link_context: None,
        is_placeholder: false,
        authored_height: item.authored_height,
        measuring: fresh_render.contains(chart_id),
    };
    // Production keeps a chart's sizing-time render while its box still holds
    // that height (a support_table chart is also corrected against an
    // overshoot, `_correct_support_table_height`). The render cache is keyed
    // by title shift too, so a shifted chart renders fresh. Non-aspect-ratio
    // types size by their own law and are not reused.
    let chart_type = chart_json.get("chart_type").and_then(Value::as_str).ok_or("chart missing chart_type")?;
    let natural_payload = if !fresh_render.contains(chart_id)
        && item.title_shift == crate::TitleShift::default()
        && (crate::is_support_table_arm(&chart_payload)?
            || !sizing::is_non_aspect_ratio_type(chart_type)
            || !chart_json["multiples"].is_null())
    {
        let mut natural = chart_payload.clone();
        natural.height = sizing::static_slot_estimate(
            chart_json,
            item.authored_height,
            item_box.width,
            chrome.root_card_padding,
            &payload.chart_defaults,
        )?;
        natural.measuring = true;
        Some(natural)
    } else {
        None
    };
    let chart_svg = match render_fitted_natural(natural_payload.as_ref().unwrap_or(&chart_payload), &chart_payload, fonts) {
        Ok(output) => {
            payload.report.borrow_mut().push(ChartStatusEntry {
                chart_id: chart_id.to_string(),
                status: "rendered",
                code: None,
                message: None,
                warnings: output.warnings,
                engine_refused: false,
            });
            output.svg
        }
        Err(e) => {
            let title = chart_json.get("title").and_then(Value::as_str);
            let source_path = chart_json
                .get("source_path")
                .and_then(Value::as_str)
                .unwrap_or("");
            let defined_in_other_file = chart_json
                .get("defined_in_other_file")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let chart_notes = chart_json
                .get("notes")
                .and_then(Value::as_str)
                .unwrap_or("");
            let mut var_deps: Vec<&str> = chart_json
                .get("variable_dependencies")
                .and_then(Value::as_array)
                .map(|arr| arr.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            var_deps.sort_unstable();

            // Rust carries no message text for a registered code, so the card
            // names the code and its params; a refusal has no registry entry,
            // so no doc link beyond the code badge's own anchor.
            let message = e.to_string();
            let doc_url = (e.code != ERR_ENGINE_REFUSED.id)
                .then(|| build_doc_url(e.code))
                .transpose()?;
            let card = render_error_card_svg(
                item_box.width,
                item_box.height,
                &ErrorCard {
                    chart_id,
                    title,
                    code: e.code,
                    message: &message,
                    hint: None,
                    doc_url: doc_url.as_deref(),
                },
                callout_error_style(payload)?,
                payload.fonts_dir.as_deref(),
                fonts,
            )?;
            let (wrapped, actual_height) = wrap_error_card_svg(
                chart_id,
                chart_type,
                source_path,
                defined_in_other_file,
                chart_notes,
                &var_deps,
                &card,
                item_box.width,
            )?;
            payload.report.borrow_mut().push(ChartStatusEntry {
                chart_id: chart_id.to_string(),
                status: "refused",
                code: Some(e.code.to_string()),
                message: Some(message),
                warnings: Vec::new(),
                engine_refused: true,
            });
            let svg = match item.notes.as_deref() {
                Some(notes) if !notes.is_empty() => format!(
                    r#"<g class="dbt-layout-item" data-layout-notes="{}">{wrapped}</g>"#,
                    xml_escape_attr(notes)
                ),
                _ => wrapped,
            };
            return Ok(RenderedItem {
                svg,
                height: actual_height,
            });
        }
    };
    let (wrapped, actual_height) =
        wrap_chart_svg(
            chart_json,
            chart_type,
            &chart_svg,
            item_box.width,
            &padding,
            &chrome.card_style,
        )?;

    let svg = match item.notes.as_deref() {
        Some(notes) if !notes.is_empty() => format!(
            r#"<g class="dbt-layout-item" data-layout-notes="{}">{wrapped}</g>"#,
            xml_escape_attr(notes)
        ),
        _ => wrapped,
    };
    Ok(RenderedItem {
        svg,
        height: actual_height,
    })
}

/// `layouts.py::_bg_rect(available_width, <accumulated height>, background)`,
/// gated by each layout arm's `if background:` (layouts.py:143/214/287) — an
/// empty `background` renders no rect at all, not a rect with empty fill.
/// Dims are bare Python float interpolation (`py_float_str`), never
/// `format_svg_numeric`.
fn layout_bg_rect(width: f64, height: f64, fill: &str) -> String {
    if fill.is_empty() {
        return String::new();
    }
    format!(
        r#"<rect x="0" y="0" width="{}" height="{}" fill="{}" rx="4"/>"#,
        py_float_str(width),
        py_float_str(height),
        xml_escape_attr(fill)
    )
}

/// `layouts.py`'s own per-item fallback, identical across all three layout
/// arms (`render_rows_layout:118-119`/`render_cols_layout`/`render_grid_
/// layout`): `item.width if item.width > 0 else available_width` (likewise
/// height) — a degenerate ZERO-height/width box (a nested board with no
/// title/text/layout content — see `docs/laws-board.md`'s "Nested boards"
/// section) falls back to the LAYOUT's own available dims, never renders
/// at a literal 0×0 slot.
fn item_slot_box(item_box: &ItemBox, available_width: f64, available_height: f64) -> ItemBox {
    ItemBox {
        x: item_box.x,
        y: item_box.y,
        width: if item_box.width > 0.0 {
            item_box.width
        } else {
            available_width
        },
        height: if item_box.height > 0.0 {
            item_box.height
        } else {
            available_height
        },
    }
}

/// `layouts.py::render_rows_layout`: chart-only items, stacked vertically
/// with `gap + card_gap` between each. Returns (svg, actual_total_height).
/// An empty `items` (a text-only board with nothing in `rows:`) short-
/// circuits before the bg-rect line ever runs (`layouts.py:108`) — no
/// background rect draws even with a non-empty `style.background`.
fn render_rows(
    layout: &LayoutDoc,
    boxes: &[ItemBox],
    available_width: f64,
    available_height: f64,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    fresh_render: &HashSet<String>,
) -> Result<(String, f64), String> {
    if layout.items.is_empty() {
        return Ok((String::new(), 0.0));
    }
    let mut rendered = String::new();
    let mut current_y = 0.0_f64;
    let mut actual_total_height = 0.0_f64;

    for (item, item_box) in layout.items.iter().zip(boxes) {
        let slot = item_slot_box(item_box, available_width, available_height);
        // A `rows` layout aligns nothing among its own items, so it hands
        // no overrides to a child; `fresh_render` carries the ids a parent
        // cols row realigned inside this nested board.
        let Some(r) = render_layout_item(
            item,
            &slot,
            payload,
            chrome,
            fonts,
            &HashMap::new(),
            fresh_render,
        )?
        else {
            continue;
        };
        rendered.push_str(&translate_group("0", &num_full(px(current_y)), &r.svg, ""));
        actual_total_height = current_y + r.height;
        current_y += r.height + chrome.item_gap + chrome.card_gap;
    }

    let bg = layout_bg_rect(layout.content_width, actual_total_height, chrome.layout_bg);
    Ok((format!("{bg}\n{rendered}"), actual_total_height))
}

/// `layouts.py::render_cols_layout`: chart-only items at their own
/// computed `x`, all starting at `y=0` — inter-column gaps are already
/// baked into each item's `x` by `sizing::calculate_cols_boxes`, nothing to
/// add here. Returns (svg, max_actual_item_height).
fn render_cols(
    layout: &LayoutDoc,
    boxes: &[ItemBox],
    available_width: f64,
    available_height: f64,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
    fresh_render: &HashSet<String>,
) -> Result<(String, f64), String> {
    if layout.items.is_empty() {
        return Ok((String::new(), 0.0));
    }
    let mut rendered = String::new();
    let mut max_height = 0.0_f64;

    for (item, item_box) in layout.items.iter().zip(boxes) {
        let slot = item_slot_box(item_box, available_width, available_height);
        let Some(r) = render_layout_item(
            item,
            &slot,
            payload,
            chrome,
            fonts,
            chart_height_overrides,
            fresh_render,
        )?
        else {
            continue;
        };
        rendered.push_str(&translate_group(&num_full(px(item_box.x)), "0", &r.svg, ""));
        max_height = max_height.max(r.height);
    }

    let bg = layout_bg_rect(layout.content_width, max_height, chrome.layout_bg);
    Ok((format!("{bg}\n{rendered}"), max_height))
}

/// `layouts.py::render_grid_layout`: chart-only items at their own computed
/// pixel `(x, y)`. Returns (svg, max_actual_bottom_edge).
fn render_grid(
    layout: &LayoutDoc,
    boxes: &[ItemBox],
    available_width: f64,
    available_height: f64,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
    fresh_render: &HashSet<String>,
) -> Result<(String, f64), String> {
    if layout.items.is_empty() {
        return Ok((String::new(), 0.0));
    }
    let mut rendered = String::new();
    let mut max_bottom_edge = 0.0_f64;

    for (item, item_box) in layout.items.iter().zip(boxes) {
        let slot = item_slot_box(item_box, available_width, available_height);
        let Some(r) = render_layout_item(
            item,
            &slot,
            payload,
            chrome,
            fonts,
            chart_height_overrides,
            fresh_render,
        )?
        else {
            continue;
        };
        rendered.push_str(&translate_group(
            &num_full(px(item_box.x)),
            &num_full(px(item_box.y)),
            &r.svg,
            "",
        ));
        max_bottom_edge = max_bottom_edge.max(item_box.y + r.height);
    }

    let bg = layout_bg_rect(layout.content_width, max_bottom_edge, chrome.layout_bg);
    Ok((format!("{bg}\n{rendered}"), max_bottom_edge))
}

/// `layouts.py::render_tabs_layout`: renders ONLY the active tab's own
/// content (every OTHER tab's box is uniform but never rendered — see
/// `sizing::calculate_tabs_boxes`'s own doc), plus the tab bar (one rect +
/// label per tab, the inactive ones wrapped in a toggle link). `boxes` is
/// `layout.items.len()` copies of the SAME uniform box `calculate_tabs_
/// boxes` assigned; only `boxes[active_index]` is ever read.
fn render_tabs_layout(
    layout: &LayoutDoc,
    boxes: &[ItemBox],
    available_width: f64,
    payload: &BoardPayload,
    chrome: &RenderChrome,
    fonts: &FontBook,
) -> Result<(String, f64), String> {
    if layout.items.is_empty() {
        return Ok((String::new(), 0.0));
    }
    // `tab_position: "left"` renders differently (`content_y = 0.0`,
    // `content_height = available_height` — no reserved top strip) but
    // SIZING never branches on it at all (`_calculate_tabs_dimensions` has
    // no `tab_position` parameter — a real production asymmetry this port
    // reproduces rather than patches): a "left" board would already size
    // wrong in production before this composer ever runs, so refusing here
    // is the honest response rather than rendering a shape whose boxes are
    // already known-wrong upstream. No ladder/corpus row authors it.
    if !matches!(layout.tab_position.as_deref(), None | Some("top")) {
        return Err(format!(
            "board layout tab_position not modeled: {:?}",
            layout.tab_position.as_deref().unwrap_or("top")
        ));
    }
    let tabs = &chrome.tabs_style;
    let active_index = resolve_active_tab_index(
        layout.items.len(),
        layout.tab_variable.as_deref(),
        &layout.tab_slugs,
        layout.default_tab,
        &payload.variable_values,
    )?;
    let content_y = tabs.bar_height;

    // `card_gap=0.0`/`gap=0.0` — production's own call site (`render_tabs_
    // layout`'s own `render_layout_item` call) forces both explicitly,
    // never `chrome`'s own values.
    let tab_chrome = RenderChrome {
        bg: chrome.bg,
        layout_bg: chrome.layout_bg,
        card_pad: chrome.card_pad,
        card_gap: 0.0,
        item_gap: 0.0,
        root_card_padding: chrome.root_card_padding,
        details_style: chrome.details_style.clone(),
        tabs_style: chrome.tabs_style.clone(),
        card_style: chrome.card_style.clone(),
    };
    let active_item = &layout.items[active_index];
    let active_box = &boxes[active_index];
    let rendered = render_layout_item(
        active_item,
        active_box,
        payload,
        &tab_chrome,
        fonts,
        &HashMap::new(),
        &HashSet::new(),
    )?;
    let (item_svg, actual_item_height) = match &rendered {
        Some(r) => (r.svg.as_str(), r.height),
        None => ("", 0.0),
    };

    let n = layout.items.len();
    let titles: Vec<String> = if layout.tab_titles.len() == n {
        layout.tab_titles.clone()
    } else {
        (1..=n)
            .map(|idx| display_text::language::tr("tab.title", None, &[("n", &idx.to_string())]))
            .collect::<Result<_, _>>()?
    };
    let slugs: Vec<String> = if !layout.tab_slugs.is_empty() {
        layout.tab_slugs.clone()
    } else {
        (0..n).map(|idx| format!("tab_{idx}")).collect()
    };
    let tab_width = available_width / titles.len() as f64;

    let mut tab_bar_parts = Vec::with_capacity(n);
    for (idx, (title, slug)) in titles.iter().zip(&slugs).enumerate() {
        let is_active = idx == active_index;
        let x = idx as f64 * tab_width;
        let weight = if is_active { &tabs.active_weight } else { &tabs.inactive_weight };
        let fill = if is_active { &tabs.active_fill } else { &tabs.inactive_fill };
        let fill_attr = fill.as_deref().unwrap_or("none");
        let text_fill = if is_active { &tabs.active_font_color } else { &tabs.inactive_font_color };
        let tab_rect = format!(
            r#"<rect x="{}" y="0" width="{}" height="{}" fill="{fill_attr}" stroke="{}" stroke-width="{}"{}/>"#,
            py_float_str(x),
            py_float_str(tab_width),
            py_float_str(tabs.bar_height),
            xml_escape_attr(&tabs.border_color),
            py_float_str(tabs.border_width),
            tabs.border_dash_attrs, fill_attr = xml_escape_attr(fill_attr),
        );
        let tab_svg = format!(
            r#"{tab_rect}<text x="{}" y="{}" text-anchor="middle" font-size="{}" fill="{text_fill}" font-weight="{weight}">{}</text>"#,
            py_float_str(x + tab_width / 2.0),
            py_float_str(tabs.bar_height / 2.0 + tabs.title_baseline_offset),
            py_float_str(tabs.font_size),
            html_escape(title), text_fill = xml_escape_attr(text_fill), weight = xml_escape_attr(weight),
        );
        if let Some(tab_variable) = layout.tab_variable.as_deref() {
            if !is_active {
                let href = build_toggle_url(&payload.variable_values, tab_variable, slug);
                tab_bar_parts.push(format!(r#"<a href="{}">{tab_svg}</a>"#, xml_escape_attr(&href)));
                continue;
            }
        }
        tab_bar_parts.push(tab_svg);
    }
    let tab_bar_svg = tab_bar_parts.join("\n");

    let content_svg = if item_svg.is_empty() {
        String::new()
    } else {
        translate_group("0", &num_full(px(content_y)), item_svg, "")
    };
    let actual_height = content_y + actual_item_height;
    let bg_rect = layout_bg_rect(available_width, actual_height, chrome.layout_bg);
    Ok((
        format!("{bg_rect}\n{tab_bar_svg}\n{content_svg}"),
        actual_height,
    ))
}

/// `boards.py::_footer_attribution_svg`, ported: separate right-anchored
/// `<text>` runs for the (optional) suffix, the brand phrase — set one
/// weight heavier, wrapped in the footer link when set — and the (optional)
/// prefix, rather than a single `<text>` with `<tspan>` children (rasterizers
/// disagree about mixed content — see the Python docstring this ports).
/// Returns (svg, total_width) — width lets a footer-right timestamp clear
/// the whole run.
fn footer_attribution_svg(
    footer: &FooterDoc,
    font_family: &str,
    x: f64,
    y: f64,
    fonts: &FontBook,
) -> Result<(String, f64), String> {
    let size = footer
        .font
        .size
        .ok_or("footer.font.size must be resolved")?;
    let color = footer
        .font
        .color
        .as_deref()
        .ok_or("footer.font.color must be resolved")?;
    let brand_weight = footer.brand_weight;
    let run = |end_x: f64, content: &str, brand: bool| -> String {
        let weight_attr = if brand {
            format!(r#" font-weight="{brand_weight}""#)
        } else {
            String::new()
        };
        format!(
            r#"<text x="{}" y="{}" text-anchor="end" font-size="{}" fill="{color}" font-family="{font_family}"{weight_attr}>{}</text>"#,
            // Snapped like the Python side (svg_utils.px) — see its comment.
            num_full(px(end_x)),
            num_full(y),
            num_full(size),
            html_escape(content), color = xml_escape_attr(color), font_family = xml_escape_attr(font_family)
        )
    };

    let text = footer.text.clone();
    let Some((match_start, match_end)) = find_brand_phrase(&text) else {
        let w = fonts.measure(&text, font_family, size)?;
        return Ok((run(x, &text, false), w));
    };

    // Measured at 600 because that is the weight it is painted at; the
    // regular instance is 1.41px narrower and the prefix would land on it.
    let prefix = text[..match_start].trim_end().to_string();
    let brand_word = text[match_start..match_end].to_string();
    let suffix = text[match_end..].trim_start().to_string();
    // A space separates the brand from a neighbor only where the text has one
    // (CJK runs the brand straight into its words).
    let space = fonts.measure(" ", font_family, size)?;
    let prefix_gap = if prefix.len() != match_start { space } else { 0.0 };
    let suffix_gap = if suffix.len() != text.len() - match_end { space } else { 0.0 };

    let mut right = x;
    let mut parts: Vec<String> = Vec::new();
    if !suffix.is_empty() {
        parts.push(run(x, &suffix, false));
        right -= fonts.measure(&suffix, font_family, size)? + suffix_gap;
    }

    let brand_inner = run(right, &brand_word, true);
    let brand_run = match &footer.link {
        Some(link) => format!(
            r#"<a class="dbt-footer-link" href="{}" target="_blank" rel="noopener noreferrer">{brand_inner}</a>"#,
            xml_escape_attr(link)
        ),
        None => brand_inner,
    };
    parts.insert(0, brand_run);
    right -= fonts.measure_at_weight(&brand_word, font_family, size, Some(brand_weight as f32))?;

    if !prefix.is_empty() {
        parts.insert(0, run(right - prefix_gap, &prefix, false));
        right -= prefix_gap + fonts.measure(&prefix, font_family, size)?;
    }
    Ok((parts.concat(), x - right))
}

/// One `LayoutItemInput` per `items`/`authorings` pair — shared by the root
/// pass (`size_board`) and every nested-board pass (`nested_board_total_
/// height`/`render_nested_board`) so the field list lives in one place.
fn layout_inputs<'a>(
    items: &'a [ItemDoc],
    authorings: &'a [ItemAuthoring],
    chart_height_overrides: &HashMap<String, f64>,
) -> Vec<LayoutItemInput<'a>> {
    items
        .iter()
        .zip(authorings)
        .map(|(item, authoring)| LayoutItemInput {
            kind: item.kind.as_str(),
            chart: item.chart.as_ref(),
            chart_error: item.chart_error.as_ref(),
            authoring,
            board: item.board.as_ref(),
            details_present: item.details_present(),
            details_variable: item.details_variable.as_deref(),
            chart_height_override: item
                .chart
                .as_ref()
                .and_then(|c| c.get("id"))
                .and_then(Value::as_str)
                .and_then(|id| chart_height_overrides.get(id))
                .copied(),
        })
        .collect()
}

/// `render/sizing.py::calculate_layout_items`'s box-assignment dispatch
/// (`_apply_layout_dimensions`), shared by the root pass and every nested
/// board's own layout. `available_height` is the caller's real value for
/// "cols" and "tabs" (each arm's own doc has the citation for why the real
/// number matters there); `grid`'s own `f64::INFINITY` narrowing is
/// unchanged (root's own pre-existing simplification, reused unchanged for
/// nested rather than fixed fresh here — no evidenced gap forced it yet,
/// unlike cols/tabs). The second element of the return tuple is the
/// `chart_id -> forced_height` map `_align_board_charts` computed for this
/// layout's OWN direct board-item children (empty off a `cols` layout —
/// `rows`/`grid` never align, matching production: `_align_grid_rows` is
/// unported, a named, narrower gap — see its own call site).
fn boxes_for_layout(
    layout: &LayoutDoc,
    inputs: &[LayoutItemInput],
    content_width: f64,
    available_height: f64,
    ctx: &sizing::SizingCtx,
) -> Result<(Vec<ItemBox>, HashMap<String, f64>, HashSet<String>), String> {
    match layout.kind.as_str() {
        "rows" => {
            let boxes = calculate_rows_boxes(inputs, content_width, available_height, ctx)?;
            let fresh_render = sizing::forced_fresh_renders(inputs, &boxes, ctx)?;
            Ok((boxes, HashMap::new(), fresh_render))
        }
        // `_calculate_cols_dimensions`'s own `row_height = min(max_content_
        // height, available_height)` (unless a specified/authored item
        // height already dominates) DOES read the real `available_height`
        // in production — an authored `height:` on the wrapping `rows:`/
        // nested-board item caps a cols row's height the same way it caps
        // a rows item's own. `f64::INFINITY` was fine everywhere no
        // authored ceiling reached this deep — wrong the instant one does
        // (`ladder/nested/11-cols-authored-height-caps-natural`: oracle
        // `260.0` vs the `INFINITY`-clamped `408.0`, `playground/card-
        // background-parity-lab`'s own corpus shape).
        "cols" => calculate_cols_boxes(inputs, content_width, available_height, ctx),
        "grid" => {
            sizing::refuse_forced_rail_charts(inputs)?;
            let columns = layout.columns.unwrap_or(sizing::DEFAULT_GRID_COLUMNS);
            calculate_grid_boxes(inputs, columns, content_width, f64::INFINITY, ctx)
        }
        // `_calculate_tabs_dimensions`'s own `tab_available = available_
        // height - tab_bar_height` then `min(ch, tab_available)`
        // (layout_sizing.py) is NOT a no-op the way an earlier version of
        // this file argued: `available_height` here is itself
        // `nested_board.layout.height` minus a chain of padding/margin/
        // title-text-variables subtractions
        // (`_calculate_nested_board_layout`'s `layout_available_height`,
        // or the board's own `container_height` at the root) — a DIFFERENT
        // float already computed via its own addition/subtraction chain,
        // not the bit-identical output of THIS item's own measure call.
        // `(measured + tab_bar_height) - tab_bar_height` does not always
        // reproduce `measured` bit-for-bit in IEEE 754, so `min()` can
        // still fire and pick the ULP-rounded value even when the two
        // operands are mathematically equal (`ladder/boards/tabs/05-tabs-
        // nested-in-rows-with-title`: oracle `481.54999999999995` vs the
        // `INFINITY`-clamped port's `481.55`). The caller passes the real
        // number now — see each call site's own citation for how it's
        // derived.
        "tabs" => calculate_tabs_boxes(
            inputs,
            content_width,
            available_height,
            layout.tab_variable.as_deref(),
            &layout.tab_slugs,
            layout.default_tab,
            ctx,
        )
        .map(|b| (b, HashMap::new(), HashSet::new())),
        other => Err(format!("board layout not modeled: {other}")),
    }
}

/// `payload.fonts_dir` unwrapped — a real directory string on every real
/// harness run (`board_sweep.py::_board_payload` always sets it); `None`
/// only on a hand-built test fixture with no visible variables to measure
/// (every other test fixture in this crate ships `fonts_dir: null` too,
/// same reason `SizingCtx.fonts_dir` stays `Option`), where this value is
/// never actually read.
fn fonts_dir_str(payload: &BoardPayload) -> &str {
    payload.fonts_dir.as_deref().unwrap_or("")
}

/// A nested board's content width at item width `width` (`_calculate_nested_board_layout`).
fn nested_content_width(nested: &BoardDoc, width: f64) -> f64 {
    let board_margin_horizontal = nested.style.margin.map(|m| m.horizontal()).unwrap_or(0.0);
    width - effective_padding(&nested.style).horizontal() - board_margin_horizontal
}

/// The gap between a nested board's children: `style.gap`, zero when unset.
fn nested_child_gap(nested: &BoardDoc) -> f64 {
    nested.style.gap.unwrap_or(0.0)
}

/// `render/sizing.py::nested_board_sizing_context`. Returns `(content_width,
/// non_layout_height, child_gap)`.
fn nested_board_sizing_context(
    nested: &BoardDoc,
    width: f64,
    card_gap: f64,
    payload: &BoardPayload,
) -> Result<(f64, f64, f64), String> {
    let content_width = nested_content_width(nested, width);
    let child_gap = nested_child_gap(nested);
    let effective_child_gap = child_gap + card_gap;

    let card_pad = nested.style.frame.card_padding;
    let inner = (content_width - 2.0 * card_pad).max(1.0);
    let variable_values = effective_variable_values(nested, &payload.variable_values);
    let fonts_dir = fonts_dir_str(payload);
    let visible_variables = nested.visible_variables();

    let use_title_inline = crate::variables::should_use_title_inline_band(
        &nested.title,
        &visible_variables,
        content_width,
        nested.title_style.as_ref(),
        nested.title_fonts.as_ref(),
        nested.style.title.font.case.as_deref(),
        &nested.style.variables,
        card_pad,
        &variable_values,
        fonts_dir,
    )?;

    let (title_h, variables_h, inline_band_h) = if use_title_inline {
        let style = nested
            .title_style
            .clone()
            .ok_or("nested board has a title but payload carries no title_style")?;
        let title_fonts = nested
            .title_fonts
            .clone()
            .ok_or("nested board has a title but payload carries no title_fonts")?;
        let title_case = nested.style.title.font.case.as_deref();
        let inline_band_h = crate::variables::compute_title_variables_inline_band_height(
            &nested.title,
            &visible_variables,
            content_width,
            &variable_values,
            card_pad,
            nested.style.title.min_height,
            style,
            title_fonts,
            title_case,
            &nested.style.variables,
            fonts_dir,
        )?;
        (0.0, 0.0, inline_band_h)
    } else {
        let title_h = if !nested.title.is_empty() {
            let style = nested
                .title_style
                .clone()
                .ok_or("nested board has a title but payload carries no title_style")?;
            let title_fonts = nested
                .title_fonts
                .clone()
                .ok_or("nested board has a title but payload carries no title_fonts")?;
            let title_case = nested.style.title.font.case.as_deref();
            title::get_title_height(
                &nested.title,
                &variable_values,
                title_case,
                inner,
                style,
                title_fonts,
            )?
            .max(nested.style.title.min_height)
        } else {
            0.0
        };
        let variables_h = if !visible_variables.is_empty() {
            // sizing.py:1930 hands `compute_variable_controls_height` the
            // bare `content_width` here — unlike the render side
            // (`render_nested_board` below, sizing.py:1425 equivalent),
            // sizing never subtracts `card_pad`. A real, if asymmetric,
            // production behavior — not a bug to "fix" by matching render.
            crate::variables::compute_variable_controls_height(
                &visible_variables,
                content_width,
                &variable_values,
                &nested.style.variables,
                fonts_dir,
            )?
        } else {
            0.0
        };
        (title_h, variables_h, 0.0)
    };

    let text_h = if !nested.text.is_empty() {
        let style = nested
            .text_style
            .clone()
            .ok_or("nested board has text but payload carries no text_style")?;
        let text_fonts = nested
            .text_fonts
            .clone()
            .ok_or("nested board has text but payload carries no text_fonts")?;
        let column = nested.style.text.as_prose_doc();
        let plan = nested
            .prose_plan
            .ok_or("nested board has text but payload carries no prose_plan")?;
        let allow_raw_html = nested.html_policy == "trusted-raw";
        prose::get_markdown_text_height(
            &nested.text,
            &variable_values,
            inner,
            &column,
            &plan,
            nested.style.frame.card_padding,
            style,
            text_fonts,
            allow_raw_html,
        )?
    } else {
        0.0
    };

    let content_box = title::compute_board_content_box(
        title_h,
        text_h,
        variables_h,
        inline_band_h,
        effective_child_gap,
        !nested.layout.items.is_empty(),
        card_pad,
    );
    let non_layout_height = content_box.non_layout_height + content_box.gap_before_layout;
    Ok((content_width, non_layout_height, child_gap))
}

/// `render/sizing.py::get_item_content_height`'s board branch, ported — a
/// pure query (never mutates), mirroring production's own STATIC
/// measurement of "how tall would this nested board be at this candidate
/// width", called once per candidate width by the CONTAINER's own box-
/// assignment pass (`sizing::item_height`'s board dispatch, via `SizingCtx.
/// nested_board_height`). `is_item_collapsed_summary`/the details chrome
/// addition are not ported: `sizing::item_height` already refuses a details
/// item before this ever runs, so both are structurally unreachable here.
fn nested_board_total_height(
    nested_json: &Value,
    width: f64,
    card_gap: f64,
    root_card_padding: f64,
    payload: &BoardPayload,
    fonts: &FontBook,
) -> Result<f64, String> {
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;
    let (content_width, non_layout_height, child_gap) =
        nested_board_sizing_context(&nested, width, card_gap, payload)?;

    let mut nested_height = non_layout_height;
    if !nested.layout.items.is_empty() {
        let authorings: Vec<ItemAuthoring> =
            nested.layout.items.iter().map(ItemDoc::authoring).collect();
        let inputs = layout_inputs(&nested.layout.items, &authorings, &HashMap::new());
        let nested_height_fn = |b: &Value, w: f64| {
            nested_board_total_height(b, w, card_gap, root_card_padding, payload, fonts)
        };
        let contribution_fn = |b: &Value, w: f64, rh: f64| {
            board_chart_target_contribution(b, w, rh, card_gap, root_card_padding, payload, fonts)
        };
        let overrides_ctx = BoardRenderCtx {
            card_gap,
            root_card_padding,
            payload,
            fonts,
        };
        let overrides_fn = |b: &Value, w: f64, t: f64, rh: f64| {
            board_alignment_overrides(b, w, t, rh, &overrides_ctx)
        };
        // `SizingCtx.card_padding` is the ROOT's own, not this nested
        // board's `card_pad` — `layout_sizing.py::_make_data_aware_height_
        // provider`'s render-first Vega vertical-inset closure is built
        // ONCE over the root's `card_padding` and reused unchanged for
        // every chart in the tree (see `RenderChrome::root_card_padding`'s
        // own doc for the full citation).
        let ctx = sizing::SizingCtx {
            gap: child_gap,
            card_gap,
            card_padding: root_card_padding,
            chart_datasets: &payload.chart_datasets,
            defaults: &payload.chart_defaults,
            board_style: &payload.board_style,
            chart_bg: &nested.style.background,
            tabs_bar_height: nested.style.layout.tabs.bar_height,
            details_summary_height: nested.style.layout.details.summary_height,
            variable_values: &payload.variable_values,
            fonts_dir: payload.fonts_dir.clone(),
            fonts,
            nested_board_height: &nested_height_fn,
            board_chart_target_contribution: &contribution_fn,
            board_alignment_overrides: &overrides_fn,
        };
        // A pure NATURAL (unconstrained) measurement — `_align_board_
        // charts`'s own overrides never apply here, matching production's
        // `get_item_content_height`'s board branch, which never runs
        // alignment either (that happens later, in a SEPARATE tree walk
        // over the already-resolved board). `available_height: INFINITY`
        // for "tabs" matches `_measure_tabs_layout_height`, which has no
        // `available_height` parameter at all — production's own MEASURE
        // side never clamps.
        nested_height += if nested.layout.kind == "rows" {
            // `calculate_layout_height`'s "rows" arm derives the total from
            // ALREADY-ASSIGNED boxes (`boxes.last().y + boxes.last().
            // height`) — correct in VALUE but a different float grouping
            // than production's own measure function (`_measure_rows_
            // layout_height`'s own doc has the full citation). Call the
            // true measure function directly here so a nested board's own
            // reported height matches production's bits, not the assign
            // pass's differently-grouped equivalent.
            sizing::measure_rows_layout_height(&inputs, content_width, &ctx)?
        } else if nested.layout.kind == "cols" {
            // A cols row measures its charts' pristine naturals; the boxes
            // below are the ALIGNED ones, which a small-multiples chart can
            // push past it.
            sizing::measure_cols_layout_height(&inputs, content_width, f64::INFINITY, &ctx)?
        } else {
            let (boxes, _own_overrides, _own_fresh_render) =
                boxes_for_layout(&nested.layout, &inputs, content_width, f64::INFINITY, &ctx)?;
            calculate_layout_height(nested.layout.kind.as_str(), &boxes, 0.0)
        };
    }
    let ep = effective_padding(&nested.style);
    let margin_vertical = nested.style.margin.map(|m| m.vertical()).unwrap_or(0.0);
    Ok(nested_height + ep.vertical() + margin_vertical)
}

/// Recursively computes a nested board's own inner item boxes, at the
/// item's own FINAL assigned width, applying `chart_height_overrides` (a
/// PARENT cols row's own `_align_board_charts` write, empty when this
/// board sits outside a cols row or does not qualify) to its DIRECT Vega
/// chart children and threading each further-nested board's OWN
/// alignment overrides (computed by `boxes_for_layout`, if this board's
/// own layout is `cols`) down to ITS children in turn — the same numbers
/// production's frozen `ResolvedBoard.layout.items` carries for a nested
/// `type: board` item (`_align_board_charts`'s write DOES reach that frozen
/// tree; see `board_alignment_overrides`'s own citation).
/// Exposed for `main.rs`'s `.boxes.json` sidecar only — `render_board`/
/// `render_nested_board` need the composed SVG, not this JSON shape, and
/// call `boxes_for_layout` directly.
pub fn nested_board_item_boxes(
    nested_json: &Value,
    width: f64,
    available_height: f64,
    card_gap: f64,
    root_card_padding: f64,
    payload: &BoardPayload,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
) -> Result<Vec<Value>, String> {
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;
    if nested.layout.items.is_empty() {
        return Ok(Vec::new());
    }
    let (content_width, non_layout_height, child_gap) =
        nested_board_sizing_context(&nested, width, card_gap, payload)?;
    // `_calculate_nested_board_layout`'s own `layout_available_height`
    // (`board_alignment_overrides`'s doc carries the full citation) —
    // `details_chrome_height` stays 0 for the same reason it does there: a
    // details item refuses before this composer ever reaches it.
    let ep = effective_padding(&nested.style);
    let margin = nested.style.margin.unwrap_or_default();
    let layout_available_height =
        (available_height - ep.vertical() - margin.vertical() - non_layout_height).max(0.0);
    let authorings: Vec<ItemAuthoring> =
        nested.layout.items.iter().map(ItemDoc::authoring).collect();
    let inputs = layout_inputs(&nested.layout.items, &authorings, chart_height_overrides);
    let nested_height_fn = |b: &Value, w: f64| {
        nested_board_total_height(b, w, card_gap, root_card_padding, payload, fonts)
    };
    let contribution_fn = |b: &Value, w: f64, rh: f64| {
        board_chart_target_contribution(b, w, rh, card_gap, root_card_padding, payload, fonts)
    };
    let overrides_ctx = BoardRenderCtx {
        card_gap,
        root_card_padding,
        payload,
        fonts,
    };
    let overrides_fn =
        |b: &Value, w: f64, t: f64, rh: f64| board_alignment_overrides(b, w, t, rh, &overrides_ctx);
    let ctx = sizing::SizingCtx {
        gap: child_gap,
        card_gap,
        card_padding: root_card_padding,
        chart_datasets: &payload.chart_datasets,
        defaults: &payload.chart_defaults,
        board_style: &payload.board_style,
        chart_bg: &nested.style.background,
        tabs_bar_height: nested.style.layout.tabs.bar_height,
        details_summary_height: nested.style.layout.details.summary_height,
        variable_values: &payload.variable_values,
        fonts_dir: payload.fonts_dir.clone(),
        fonts,
        nested_board_height: &nested_height_fn,
        board_chart_target_contribution: &contribution_fn,
        board_alignment_overrides: &overrides_fn,
    };
    let (boxes, own_overrides, _own_fresh_render) = boxes_for_layout(
        &nested.layout,
        &inputs,
        content_width,
        layout_available_height,
        &ctx,
    )?;
    let active_tab = active_tab_index(&nested.layout, payload)?;
    let mut out = Vec::with_capacity(boxes.len());
    for (i, (b, item)) in boxes.iter().zip(nested.layout.items.iter()).enumerate() {
        let mut obj = serde_json::json!({
            "x": b.x, "y": b.y, "width": b.width, "height": b.height,
        });
        if item.kind == "board" {
            let sub_json = item
                .board
                .as_ref()
                .ok_or("board item missing its resolved board")?;
            let sub_boxes = if active_tab.is_some_and(|active| active != i)
                || item_subtree_untouched(item, payload)?
            {
                zeroed_nested_item_boxes(sub_json)?
            } else {
                nested_board_item_boxes(
                    sub_json,
                    b.width,
                    b.height,
                    card_gap,
                    root_card_padding,
                    payload,
                    fonts,
                    &own_overrides,
                )?
            };
            obj["nested"] = Value::Array(sub_boxes);
        }
        out.push(obj);
    }
    Ok(out)
}

/// A COLLAPSED details item's own subtree — the second (alongside an
/// inactive tab's) shape `_calculate_nested_board_layout` never recurses
/// into: `is_item_collapsed_summary(item, variable_values)` short-circuits
/// its very first line (`if is_item_collapsed_summary(...) or not item.
/// board: return`), before `nested_board.layout.width`/`content_width`/
/// `content_height` are ever assigned or `calculate_layout_items` is ever
/// called on the nested board's own layout — so its whole subtree stays at
/// `ResolvedLayoutItem`'s pre-sizing zero default, same as `zeroed_nested_
/// item_boxes`'s own tabs citation.
fn item_subtree_untouched(item: &ItemDoc, payload: &BoardPayload) -> Result<bool, String> {
    if !item.details_present() {
        return Ok(false);
    }
    item_details_expanded(item, payload).map(|expanded| !expanded)
}

/// `Some(active_index)` when `layout.kind == "tabs"`, else `None` — the
/// active-tab gate `active_layout_items`'s own tabs branch applies before
/// `_calculate_nested_board_layout` ever recurses into an item's own
/// children (see `zeroed_nested_item_boxes`'s doc for why every OTHER
/// item's subtree stays untouched).
fn active_tab_index(layout: &LayoutDoc, payload: &BoardPayload) -> Result<Option<usize>, String> {
    if layout.kind != "tabs" || layout.items.is_empty() {
        return Ok(None);
    }
    resolve_active_tab_index(
        layout.items.len(),
        layout.tab_variable.as_deref(),
        &layout.tab_slugs,
        layout.default_tab,
        &payload.variable_values,
    )
    .map(Some)
}

/// Every entry of `nested_json`'s own `layout.items`, zeroed at every
/// level — the shape `render/sizing.py::calculate_layout_items`'s own
/// `active_layout_items(layout, variable_values)` leaves an INACTIVE tab
/// item's whole subtree at: `_calculate_nested_board_layout` is called
/// only for the tab `active_layout_items` returns (`for item in active_
/// layout_items(layout, active_variable_values): _calculate_nested_board_
/// layout(item, ...)`, `calculate_layout_items`), so a tab that is not the
/// active one — and everything nested inside it, at any depth — never has
/// its own `x`/`y`/`width`/`height` assigned by ANY sizing pass and stays
/// at `ResolvedLayoutItem`'s own pre-sizing construction value (0.0 on
/// every field). `board_sweep.py`'s own `_oracle_layout_boxes` recurses
/// into `item.board.layout.items` unconditionally on `item.board is not
/// None` (no tabs-awareness of its own), so the oracle for an inactive
/// tab's whole subtree reads back these same untouched zeros — this
/// function reproduces that shape structurally rather than computing real
/// (and therefore wrong) boxes for a branch production never sized.
fn zeroed_nested_item_boxes(nested_json: &Value) -> Result<Vec<Value>, String> {
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;
    let mut out = Vec::with_capacity(nested.layout.items.len());
    for item in &nested.layout.items {
        let mut obj = serde_json::json!({"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0});
        if item.kind == "board" {
            let sub_json = item.board.as_ref().ok_or("board item missing its resolved board")?;
            obj["nested"] = Value::Array(zeroed_nested_item_boxes(sub_json)?);
        }
        out.push(obj);
    }
    Ok(out)
}

/// `layout_sizing.py::_col_chart_body_target`'s board branch,
/// bound as `SizingCtx.board_chart_target_contribution`. `row_height` is
/// the cols row's own already-computed uniform slot height: production's
/// FIRST sizing pass (`calculate_layout_items`) assigns every item in a
/// resolved cols row — chart or board — that SAME uniform height before
/// any alignment ever runs (`calculate_cols_boxes` does the identical
/// thing here), so `board.layout.content_height`/`item.height` for ANY
/// board item in that row already equals it by the time `_col_chart_body_
/// target` reads them — verified against a live Python trace on
/// `playground/dundersign-commercial-finance` (all three cols siblings —
/// a KPI+donut board, a single-chart board, and the all-Vega-rows board
/// that drives the row — report the identical `content_height`/`item.
/// height`, `574.72`, at the point `_col_chart_body_target` runs, matching
/// this function's own `row_height` parameter exactly).
fn board_chart_target_contribution(
    nested_json: &Value,
    width: f64,
    row_height: f64,
    card_gap: f64,
    root_card_padding: f64,
    payload: &BoardPayload,
    fonts: &FontBook,
) -> Result<f64, String> {
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;
    if is_all_vega_rows_board(&nested) {
        return Ok(row_height);
    }
    // `row_height` stands in for `item.height` here the same way this
    // function's own doc comment already establishes for the `is_all_
    // vega_rows_board` branch above — the cols row's first sizing pass
    // already assigned this nested board that exact uniform height.
    let sub_boxes = nested_board_item_boxes(
        nested_json,
        width,
        row_height,
        card_gap,
        root_card_padding,
        payload,
        fonts,
        &HashMap::new(),
    )?;
    let mut max_h = 0.0_f64;
    for (sub, sub_box) in nested.layout.items.iter().zip(&sub_boxes) {
        let Some(chart) = &sub.chart else { continue };
        let Some(chart_type) = chart.get("chart_type").and_then(Value::as_str) else {
            continue;
        };
        if sizing::is_non_aspect_ratio_type(chart_type) {
            continue;
        }
        if let Some(h) = sub_box.get("height").and_then(Value::as_f64) {
            max_h = max_h.max(h);
        }
    }
    Ok(max_h)
}

/// `layout_sizing.py::_col_chart_body_target`'s own `is_all_vega_rows`
/// local: a `rows` layout of more than one item, every one a
/// Vega chart (`NON_ASPECT_RATIO_TYPES` excluded).
fn is_all_vega_rows_board(nested: &BoardDoc) -> bool {
    nested.layout.kind == "rows"
        && nested.layout.items.len() > 1
        && nested.layout.items.iter().all(|s| {
            s.chart.as_ref().is_some_and(|c| {
                c.get("chart_type")
                    .and_then(Value::as_str)
                    .is_some_and(|t| !sizing::is_non_aspect_ratio_type(t))
            })
        })
}

/// `layout_sizing.py::_align_board_charts`, bound as
/// `SizingCtx.board_alignment_overrides`. `row_height` is the cols row's own
/// uniform slot height (equals `item.height` — see `board_chart_target_
/// contribution`'s own citation), from which `content_h` is derived the
/// same way `_calculate_nested_board_layout` derives `board.layout.
/// content_height` from `item.height` (below) — NOT a direct substitution:
/// round 3 review found a nested board with its own title/text/variables
/// band disagrees (`item.height - content_height` = the band's height,
/// nonzero), which `ladder/nested/10` now pins. Returns the flat `chart_id
/// -> forced_height` map for every DIRECT Vega chart child the alignment
/// condition reaches; empty when it does not apply.
/// The 4 constants every recursive call in `board_alignment_overrides`'s
/// own tree needs, unchanged at every level — bundled so the function stays
/// under clippy's `too_many_arguments` threshold without inconsistent
/// argument order with its sibling functions above (which keep them
/// positional; only this one recurses on itself enough times, 6 call sites,
/// to make bundling worth the indirection).
#[derive(Clone, Copy)]
struct BoardRenderCtx<'a> {
    card_gap: f64,
    root_card_padding: f64,
    payload: &'a BoardPayload,
    fonts: &'a FontBook,
}

fn board_alignment_overrides(
    nested_json: &Value,
    width: f64,
    target_height: f64,
    row_height: f64,
    ctx: &BoardRenderCtx,
) -> Result<HashMap<String, f64>, String> {
    let BoardRenderCtx {
        card_gap,
        root_card_padding,
        payload,
        fonts,
    } = *ctx;
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;
    let vega_count = nested
        .layout
        .items
        .iter()
        .filter(|s| {
            s.chart.as_ref().is_some_and(|c| {
                c.get("chart_type")
                    .and_then(Value::as_str)
                    .is_some_and(|t| !sizing::is_non_aspect_ratio_type(t))
            })
        })
        .count();
    // A nested `cols` layout keeps nothing of this write: `_align_all_cols_in_tree`
    // recurses into it after the parent's alignment (`layout_sizing.py:1705-1706`),
    // recomputes the row's own `_col_chart_body_target` and rewrites every direct
    // Vega item's height from it (`:1684-1697`). `calculate_cols_boxes` applies
    // that row alignment, with its own `fresh_render`, when the board is composed.
    if nested.layout.kind == "cols" || vega_count != 1 {
        return Ok(HashMap::new());
    }
    // `layout_sizing.py::_align_board_charts`'s `content_h = board.layout.content_height`,
    // set by `_calculate_nested_board_layout`'s `layout_available_height =
    // item.height - details_chrome_height - effective_padding.vertical -
    // margin.vertical - non_layout_height`. `row_height` alone stands in
    // for `item.height` correctly (verified: production's first sizing
    // pass assigns every cols-row item that exact uniform height — see
    // `board_chart_target_contribution`'s own citation) but NOT for
    // `content_height` itself, which additionally subtracts the nested
    // board's own title/text/variables band — a board with none of those
    // has `non_layout_height == 0` and the two coincide, which is why the
    // substitution looked like an identity until a titled nested board
    // (round 3 review) exposed the gap. `details_chrome_height` stays 0
    // here for the same reason `render_nested_board` doesn't subtract it:
    // a details item refuses before this composer ever reaches it.
    let ep = effective_padding(&nested.style);
    let margin = nested.style.margin.unwrap_or_default();
    let (_content_width, non_layout_height, _child_gap) =
        nested_board_sizing_context(&nested, width, card_gap, payload)?;
    let content_h = (row_height - ep.vertical() - margin.vertical() - non_layout_height).max(0.0);
    if content_h <= 0.0 {
        return Ok(HashMap::new());
    }
    let sub_boxes = nested_board_item_boxes(
        nested_json,
        width,
        row_height,
        card_gap,
        root_card_padding,
        payload,
        fonts,
        &HashMap::new(),
    )?;
    let non_chart_h: f64 = nested
        .layout
        .items
        .iter()
        .zip(&sub_boxes)
        .filter(|(s, _)| {
            !s.chart.as_ref().is_some_and(|c| {
                c.get("chart_type")
                    .and_then(Value::as_str)
                    .is_some_and(|t| !sizing::is_non_aspect_ratio_type(t))
            })
        })
        .filter_map(|(_, b)| b.get("height").and_then(Value::as_f64))
        .sum();
    let board_chart_max = (content_h - non_chart_h).max(0.0);
    let effective = target_height.min(board_chart_max);

    let mut overrides = HashMap::new();
    for (sub, sub_box) in nested.layout.items.iter().zip(&sub_boxes) {
        let Some(chart) = &sub.chart else { continue };
        let Some(chart_type) = chart.get("chart_type").and_then(Value::as_str) else {
            continue;
        };
        if sizing::is_non_aspect_ratio_type(chart_type) {
            continue;
        }
        let natural = sub_box.get("height").and_then(Value::as_f64);
        let capped = sizing::effective_alignment_target_for_chart(chart_type, natural, effective);
        let Some(id) = chart.get("id").and_then(Value::as_str) else {
            continue;
        };
        overrides.insert(id.to_string(), capped);
    }

    // `_align_cols_heights`'s own board-item branch
    // (`layout_sizing.py:1340-1342`): EVERY
    // `item.board is not None` child recurses into `_align_board_charts`,
    // unconditionally on the PARENT's own layout type (Python's loop has
    // no `if board.layout.type == "cols"` guard — that condition already
    // gated whether THIS function ran at all, at the top). Round 3 review
    // MEDIUM: an earlier version of this recursion wrongly re-added that
    // guard here — no corpus board reaches this second level, so it is
    // transliterated but unverified beyond one.
    for (sub, sub_box) in nested.layout.items.iter().zip(&sub_boxes) {
        let Some(sub_board_json) = sub.board.as_ref() else {
            continue;
        };
        let sub_width = sub_box.get("width").and_then(Value::as_f64).unwrap_or(width);
        let inner = board_alignment_overrides(sub_board_json, sub_width, effective, effective, ctx)?;
        overrides.extend(inner);
    }

    Ok(overrides)
}

/// Compute every layout item's own box plus the board's own container
/// dims, from the STATIC resolved board — the seam this module exists for.
/// Returns `(boxes, container)` where `boxes[i]` corresponds to
/// `layout.items[i]`. Called twice per board render (once here, once by
/// `main.rs`'s sidecar writer so board_sweep.py can diff Rust's own numbers
/// against production's SIZED board without this composer ever reading
/// production's numbers back in) — cheap relative to the chart renders
/// that follow, so the duplication buys a clean public seam rather than a
/// shared-mutable-state one.
/// Every root layout item's own box, the board's own container dims, and
/// the flat `chart_id -> forced_height` map `_align_board_charts`'s cols
/// alignment produced for the root's own DIRECT children (empty at the
/// root, which has no incoming overrides of its own).
pub type SizedBoard =
    (Vec<ItemBox>, ContainerBox, HashMap<String, f64>, HashSet<String>);

pub fn size_board(payload: &BoardPayload, fonts: &FontBook) -> Result<SizedBoard, String> {
    payload.board_style.scoped(|| size_board_in_scope(payload, fonts))
}

fn size_board_in_scope(payload: &BoardPayload, fonts: &FontBook) -> Result<SizedBoard, String> {
    let board: BoardDoc = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("board payload deserialize: {e}"))?;
    let card_padding = board
        .card_padding
        .ok_or("board card_padding missing (nested board?)")?;
    let card_gap = board
        .card_gap
        .ok_or("board card_gap missing (nested board?)")?;

    // ItemAuthoring must live in its own binding — LayoutItemInput borrows it.
    let authorings: Vec<ItemAuthoring> =
        board.layout.items.iter().map(ItemDoc::authoring).collect();
    // The root board never sits inside anyone's cols row — no incoming
    // overrides.
    let inputs = layout_inputs(&board.layout.items, &authorings, &HashMap::new());

    let content_width = board.layout.content_width;
    let nested_height_fn =
        |b: &Value, w: f64| nested_board_total_height(b, w, card_gap, card_padding, payload, fonts);
    let contribution_fn = |b: &Value, w: f64, rh: f64| {
        board_chart_target_contribution(b, w, rh, card_gap, card_padding, payload, fonts)
    };
    let overrides_ctx = BoardRenderCtx {
        card_gap,
        root_card_padding: card_padding,
        payload,
        fonts,
    };
    let overrides_fn =
        |b: &Value, w: f64, t: f64, rh: f64| board_alignment_overrides(b, w, t, rh, &overrides_ctx);
    let ctx = sizing::SizingCtx {
        gap: board.layout.gap,
        card_gap,
        card_padding,
        chart_datasets: &payload.chart_datasets,
        defaults: &payload.chart_defaults,
        board_style: &payload.board_style,
        chart_bg: &board.style.background,
        tabs_bar_height: board.style.layout.tabs.bar_height,
        details_summary_height: board.style.layout.details.summary_height,
        variable_values: &payload.variable_values,
        fonts_dir: payload.fonts_dir.clone(),
        fonts,
        nested_board_height: &nested_height_fn,
        board_chart_target_contribution: &contribution_fn,
        board_alignment_overrides: &overrides_fn,
    };
    // `tabs` here stays `f64::INFINITY` — `ladder/tabs/01`/`02`/`04` DO
    // author `tabs:` as their board's ROOT layout and MATCH today, so this
    // is latent rather than unreached: each one's real `container_height`
    // sits comfortably clear of the tab-bar-height rounding boundary that
    // forced threading the real number into the NESTED case (`ladder/
    // tabs/05`'s own one-ULP gap). The same fix would apply identically at
    // the root if a board's own container height ever landed on that
    // boundary; no corpus board does yet, so it stays unfixed rather than
    // unfixable.
    let (boxes, overrides, fresh_render) =
        boxes_for_layout(&board.layout, &inputs, content_width, f64::INFINITY, &ctx)?;

    let mut content_height = if board.layout.kind == "rows" {
        // `calculate_layout_height`'s "rows" arm derives the total from
        // ALREADY-ASSIGNED boxes — correct in VALUE but a different float
        // grouping than production's own measure function
        // (`sizing::measure_rows_layout_height`'s own doc has the full
        // citation: `playground/time-unit-label-cadence-matrix`'s
        // container-height `box_diff`, oracle `5784.349999999999` vs the
        // derived value `5784.35`). Call the true measure function
        // directly so the board's own reported container height matches
        // production's bits.
        sizing::measure_rows_layout_height(&inputs, content_width, &ctx)?
            .max(payload.frame_min_height)
    } else if board.layout.kind == "cols" {
        // The row keeps its measured height when alignment moves a
        // small-multiples chart past it (`measure_cols_layout_height`).
        sizing::measure_cols_layout_height(&inputs, content_width, f64::INFINITY, &ctx)?
            .max(payload.frame_min_height)
    } else {
        calculate_layout_height(board.layout.kind.as_str(), &boxes, payload.frame_min_height)
    };
    // `layout_sizing.py::calculate_data_aware_layout`'s `if board.title:`
    // branch: the board's own container height also carries its title, on
    // top of the layout-items-only height above.
    if !board.title.is_empty() {
        content_height += board_title_height(payload, &board, content_width, card_padding)?
            + board.layout.gap
            + card_gap;
    }
    let container = ContainerBox {
        width: board.width,
        height: content_height,
        content_width,
        content_height,
    };
    Ok((boxes, container, overrides, fresh_render))
}

/// `size_board`'s root `boxes`, as JSON, with a `"nested"` key attached to
/// every `type: board` root item (its own inner boxes, recursively —
/// `nested_board_item_boxes`) — the shape `main.rs`'s `.boxes.json`
/// sidecar writes for `board_sweep.py`'s recursed oracle comparison.
pub fn size_board_items_json(
    payload: &BoardPayload,
    fonts: &FontBook,
    boxes: &[ItemBox],
    chart_height_overrides: &HashMap<String, f64>,
) -> Result<Vec<Value>, String> {
    payload
        .board_style
        .scoped(|| size_board_items_json_in_scope(payload, fonts, boxes, chart_height_overrides))
}

fn size_board_items_json_in_scope(
    payload: &BoardPayload,
    fonts: &FontBook,
    boxes: &[ItemBox],
    chart_height_overrides: &HashMap<String, f64>,
) -> Result<Vec<Value>, String> {
    let board: BoardDoc = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("board payload deserialize: {e}"))?;
    let card_padding = board.card_padding.ok_or("board card_padding missing")?;
    let card_gap = board.card_gap.ok_or("board card_gap missing")?;
    let active_tab = active_tab_index(&board.layout, payload)?;
    let mut out = Vec::with_capacity(boxes.len());
    for (i, (b, item)) in boxes.iter().zip(board.layout.items.iter()).enumerate() {
        let mut obj = serde_json::json!({
            "x": b.x, "y": b.y, "width": b.width, "height": b.height,
        });
        if item.kind == "board" {
            let sub_json = item
                .board
                .as_ref()
                .ok_or("board item missing its resolved board")?;
            let sub_boxes = if active_tab.is_some_and(|active| active != i)
                || item_subtree_untouched(item, payload)?
            {
                zeroed_nested_item_boxes(sub_json)?
            } else {
                nested_board_item_boxes(
                    sub_json,
                    b.width,
                    b.height,
                    card_gap,
                    card_padding,
                    payload,
                    fonts,
                    chart_height_overrides,
                )?
            };
            obj["nested"] = Value::Array(sub_boxes);
        }
        out.push(obj);
    }
    Ok(out)
}

/// `render/sizing.py::get_title_height`, called at the same
/// `content_width - 2*card_padding` measure width `calculate_data_aware_
/// layout` uses. `board.title_style`/`title_fonts` are `None` only when
/// `board_sweep.py` built the payload for a titleless board — a title-bearing
/// board reaching this point with no forwarded Style/FontSlots is a harness
/// bug, not a board shape to tolerate.
fn board_title_height(
    payload: &BoardPayload,
    board: &BoardDoc,
    content_width: f64,
    card_padding: f64,
) -> Result<f64, String> {
    let style = board
        .title_style
        .clone()
        .ok_or("board has a title but payload carries no title_style")?;
    let fonts = board
        .title_fonts
        .clone()
        .ok_or("board has a title but payload carries no title_fonts")?;
    let title_measure_width = (content_width - 2.0 * card_padding).max(0.0);
    title::get_title_height(
        &board.title,
        &payload.variable_values,
        board.style.title.font.case.as_deref(),
        title_measure_width,
        style,
        fonts,
    )
}

/// Python's `max(x, 0)` where `0` is a literal `int`: when `x < 0` the `int`
/// survives into an f-string as the bare `"0"`, never `"0.0"` — unlike Rust's
/// `f64::max`, which always stays a float. `svg_utils.border_rect` hits this
/// three times (`width`, `height`, `rx`); this is that exact interpolation,
/// not `py_float_str` wrapped in a clamp.
fn py_max_zero(x: f64) -> String {
    if x < 0.0 {
        "0".to_string()
    } else {
        py_float_str(x)
    }
}

/// `boards.py::_build_board_content_items`'s `header_piece` closure, ported
/// as a free function (Rust closures can't easily borrow `content_x`/
/// `header_start`/etc. the way the Python nested closure does, so those
/// become explicit parameters) — restricted to the `header_authored_attrs`
/// branch: `render_nested_board`'s own call into `_build_board_content_
/// items` never passes `title_authored_attrs`/`text_authored_attrs` (both
/// always ""), so `header_piece`'s OTHER two branches (a per-piece
/// `_tagged_authoring_block`, and the bare-translate fallback when neither
/// attrs set) are unreachable from this composer and not ported.
/// `header_authored_attrs` empty (a nested board item with no `source_path`
/// — a foreach expansion or an imported subtree) falls back to a bare
/// absolute translate, matching `header_piece`'s own `if not own_attrs:`
/// arm (`own_attrs` is always "" on this call path — see above).
fn header_piece(
    svg: &str,
    at_x: f64,
    at_y: f64,
    kind: &str,
    content_x: f64,
    header_start: f64,
    header_authored_attrs: &str,
) -> String {
    if header_authored_attrs.is_empty() {
        return translate_group(&num_full(px(at_x)), &num_full(px(at_y)), svg, "");
    }
    // Offset from the ROUNDED wrapper origin, not the unrounded one — px()
    // each separately since the two roundings can disagree. `kind` empty
    // (the inline-band piece — the band's own internal `kind="title"` leaf
    // already tags its title sub-group) emits no attr at all, matching
    // Python's `authored_kind_attr(kind) if kind else ''`.
    let kind_attr = if kind.is_empty() {
        String::new()
    } else {
        contract::authored_kind_attr(kind)
    };
    translate_group(
        &num_full(px(at_x) - px(content_x)),
        &num_full(px(at_y) - px(header_start)),
        svg,
        &kind_attr,
    )
}

/// `render/boards.py::render_nested_board`, ported. Returns the nested
/// board's own SVG fragment plus its actual rendered height — the
/// `RenderedItem` a container's layout arm accumulates against, exactly
/// like a chart item's own wrapped SVG.
#[allow(clippy::too_many_arguments)]
fn render_nested_board(
    nested_json: &Value,
    source_path: &str,
    available_width: f64,
    available_height: f64,
    card_gap: f64,
    root_card_padding: f64,
    payload: &BoardPayload,
    fonts: &FontBook,
    chart_height_overrides: &HashMap<String, f64>,
    title_shift: crate::TitleShift,
) -> Result<RenderedItem, String> {
    let nested: BoardDoc = serde_json::from_value(nested_json.clone())
        .map_err(|e| format!("nested board payload deserialize: {e}"))?;

    let ep = effective_padding(&nested.style);
    let margin = nested.style.margin.unwrap_or_default();

    let (content_width, non_layout_height, child_gap) =
        nested_board_sizing_context(&nested, available_width, card_gap, payload)?;

    let card_pad = nested.style.frame.card_padding;
    let title_text_width = (content_width - 2.0 * card_pad).max(0.0);
    let prose_width = if title_text_width > 0.0 {
        title_text_width
    } else {
        content_width
    };
    let variable_values = effective_variable_values(&nested, &payload.variable_values);
    let fonts_dir = fonts_dir_str(payload);
    let visible_variables = nested.visible_variables();

    let use_title_inline = crate::variables::should_use_title_inline_band(
        &nested.title,
        &visible_variables,
        content_width,
        nested.title_style.as_ref(),
        nested.title_fonts.as_ref(),
        nested.style.title.font.case.as_deref(),
        &nested.style.variables,
        card_pad,
        &variable_values,
        fonts_dir,
    )?;

    let mut inline_header_svg = String::new();
    let mut inline_header_height = 0.0_f64;
    let (title_svg, title_height, nested_title_line_box) = if use_title_inline {
        let style = nested
            .title_style
            .clone()
            .ok_or("nested board has a title but payload carries no title_style")?;
        let title_fonts = nested
            .title_fonts
            .clone()
            .ok_or("nested board has a title but payload carries no title_fonts")?;
        let title_case = nested.style.title.font.case.as_deref();
        let (band, band_h) = crate::variables::render_title_variables_inline_band(
            &nested.title,
            &visible_variables,
            &variable_values,
            content_width,
            card_pad,
            style,
            title_fonts,
            title_case,
            nested.style.title.font.color.as_deref(),
            nested.style.title.min_height,
            "",
            &nested_variables_path(source_path),
            Some(&payload.variable_datasets),
            &nested.style.variables,
            fonts_dir,
            &payload.painted_italics,
        )?;
        inline_header_svg = band;
        inline_header_height = band_h;
        (String::new(), 0.0, None)
    } else if nested.title.is_empty() {
        (String::new(), 0.0, None)
    } else {
        let style = nested
            .title_style
            .clone()
            .ok_or("nested board has a title but payload carries no title_style")?;
        let title_fonts = nested
            .title_fonts
            .clone()
            .ok_or("nested board has a title but payload carries no title_fonts")?;
        let title_case = nested.style.title.font.case.as_deref();
        let mut svg = title::render_title_svg(
            &nested.title,
            &variable_values,
            title_case,
            prose_width,
            style.clone(),
            title_fonts.clone(),
            Some(&payload.painted_italics),
        )?;
        if let Some(color) = &nested.style.title.font.color {
            svg = title::paint_title_svg_fill(&svg, color);
        }
        let measured_title_height = title::get_title_height(
            &nested.title,
            &variable_values,
            title_case,
            prose_width,
            style.clone(),
            title_fonts.clone(),
        )?;
        let height = measured_title_height.max(nested.style.title.min_height);
        let line_box = title::title_line_box(style, title_fonts, measured_title_height)?;
        (svg, height, Some(line_box))
    };

    let (text_svg, text_height) = if nested.text.is_empty() {
        (String::new(), 0.0)
    } else {
        let style = nested
            .text_style
            .clone()
            .ok_or("nested board has text but payload carries no text_style")?;
        let text_fonts = nested
            .text_fonts
            .clone()
            .ok_or("nested board has text but payload carries no text_fonts")?;
        let column = nested.style.text.as_prose_doc();
        let plan = nested
            .prose_plan
            .ok_or("nested board has text but payload carries no prose_plan")?;
        let allow_raw_html = nested.html_policy == "trusted-raw";
        let resolved_md = prose::resolve_markdown_jinja(&nested.text, &variable_values)?;
        // `boards.py::_render_text_svg` returns `(svg, height)` — its OWN
        // real-render height, not `get_markdown_text_height`'s SIZING
        // estimate (never called on this path in production; the two
        // genuinely diverge for whitespace-only text, where mdsvg's measure
        // pass short-circuits to 0 but a real render still emits a
        // nonzero-height paragraph block).
        prose::render_prose_svg(
            &resolved_md,
            prose_width,
            &column,
            &plan,
            nested.style.frame.card_padding,
            style.clone(),
            text_fonts.clone(),
            allow_raw_html,
            Some(&payload.painted_italics),
        )?
    };

    // `boards.py`'s own standalone-band gate: variable controls are inset
    // by `card_pad` on the left (aligned with chart content); the right
    // edge stays where it was.
    let nested_variables_width = (content_width - card_pad).max(0.0);
    let (nested_variables_svg, nested_variables_height) = if !visible_variables.is_empty()
        && payload.variables_overrides_present
        && inline_header_svg.is_empty()
    {
        let label_case = nested
            .style
            .variables
            .label_font_case()
            .ok_or("style.variables.label.font.case must be resolved")?;
        let controls = crate::variables::resolve_controls(
            &visible_variables,
            &variable_values,
            Some(&payload.variable_datasets),
            &nested.style.variables,
            label_case,
        )?;
        crate::variables::render_variables_strip_svg(
            &controls,
            nested_variables_width,
            &nested.style.variables,
            "start",
            &nested_variables_path(source_path),
            fonts_dir,
        )?
    } else {
        (String::new(), 0.0)
    };

    // `details_chrome_height` is not subtracted here: the parent's own
    // `render_layout_item` already refuses a details item before this
    // function is ever reached, so it is always 0 on this rung's shapes.
    // `layout_available_height` mirrors `_calculate_nested_board_layout`'s
    // own `layout_available_height` — becomes `nested_board.layout.
    // content_height`, the SAME value production's `_render_layout` reads
    // as its own `available_height` and every `layouts.py` render arm
    // falls back to for a degenerate (0-height) item (`item.height if
    // item.height > 0 else available_height` — see `item_slot_box` below).
    // Threaded into `boxes_for_layout`'s "rows"/"cols"/"tabs" arms below,
    // where the real number matters (`boxes_for_layout`'s own doc has the
    // full citation). `grid`'s own arm still passes `f64::INFINITY`
    // unconditionally (root's own pre-existing narrowing, reused unchanged
    // for nested — no evidenced gap forced it yet).
    let layout_available_height =
        (available_height - ep.vertical() - margin.vertical() - non_layout_height).max(0.0);

    let authorings: Vec<ItemAuthoring> =
        nested.layout.items.iter().map(ItemDoc::authoring).collect();
    let inputs = layout_inputs(&nested.layout.items, &authorings, chart_height_overrides);
    let nested_height_fn = |b: &Value, w: f64| {
        nested_board_total_height(b, w, card_gap, root_card_padding, payload, fonts)
    };
    let contribution_fn = |b: &Value, w: f64, rh: f64| {
        board_chart_target_contribution(b, w, rh, card_gap, root_card_padding, payload, fonts)
    };
    let overrides_ctx = BoardRenderCtx {
        card_gap,
        root_card_padding,
        payload,
        fonts,
    };
    let overrides_fn =
        |b: &Value, w: f64, t: f64, rh: f64| board_alignment_overrides(b, w, t, rh, &overrides_ctx);
    // `SizingCtx.card_padding` is the ROOT's own — see `nested_board_total_
    // height`'s identical note and `RenderChrome::root_card_padding`'s doc.
    let ctx = sizing::SizingCtx {
        gap: child_gap,
        card_gap,
        card_padding: root_card_padding,
        chart_datasets: &payload.chart_datasets,
        defaults: &payload.chart_defaults,
        board_style: &payload.board_style,
        chart_bg: &nested.style.background,
        tabs_bar_height: nested.style.layout.tabs.bar_height,
        details_summary_height: nested.style.layout.details.summary_height,
        variable_values: &payload.variable_values,
        fonts_dir: payload.fonts_dir.clone(),
        fonts,
        nested_board_height: &nested_height_fn,
        board_chart_target_contribution: &contribution_fn,
        board_alignment_overrides: &overrides_fn,
    };
    let (layout_content, actual_layout_height) = if nested.layout.items.is_empty() {
        (String::new(), 0.0)
    } else {
        let (boxes, own_overrides, own_fresh_render) = boxes_for_layout(
            &nested.layout,
            &inputs,
            content_width,
            layout_available_height,
            &ctx,
        )?;
        let chrome = RenderChrome {
            bg: &nested.style.background,
            layout_bg: "",
            card_pad,
            card_gap,
            item_gap: child_gap,
            root_card_padding,
            details_style: details_chrome_from_style(&nested.style),
            tabs_style: tabs_chrome_from_style(&nested.style),
            card_style: card_chrome_from_style(&nested.style),
        };
        match nested.layout.kind.as_str() {
            "rows" => render_rows(
                &nested.layout,
                &boxes,
                content_width,
                layout_available_height,
                payload,
                &chrome,
                fonts,
                &own_fresh_render,
            )?,
            "cols" => render_cols(
                &nested.layout,
                &boxes,
                content_width,
                layout_available_height,
                payload,
                &chrome,
                fonts,
                &own_overrides,
                &own_fresh_render,
            )?,
            "grid" => render_grid(
                &nested.layout,
                &boxes,
                content_width,
                layout_available_height,
                payload,
                &chrome,
                fonts,
                &own_overrides,
                &own_fresh_render,
            )?,
            "tabs" => render_tabs_layout(
                &nested.layout,
                &boxes,
                content_width,
                payload,
                &chrome,
                fonts,
            )?,
            other => return Err(format!("board layout not modeled: {other}")),
        }
    };

    // `_build_board_content_items`, restricted to this rung's reachable
    // shape: title and/or text and/or the inline band, always one combined
    // `header` handle when `source_path` is non-empty and at least one of
    // title/inline-band/text is present (`own_attrs` — `title_authored_
    // attrs`/`text_authored_attrs` — is always "" on this call path, so the
    // per-piece `_tagged_authoring_block` arm of `header_piece` is
    // unreachable, see `header_piece`'s own doc). Variables is a SEPARATE
    // item, never folded into the combined header handle.
    // `_build_board_content_items` (`boards.py:643-647`, `:730`, `:737`): the
    // row's shift moves the title down `title_dy` and everything after it
    // `body_dy`. An inline title/variables band sizes itself and is never
    // given a shift (`board_title_metrics`).
    let shifted_title_height = if title_svg.is_empty() { title_height } else { title_height + title_shift.body_dy };
    let content_box = title::compute_board_content_box(
        shifted_title_height,
        text_height,
        nested_variables_height,
        inline_header_height,
        child_gap + card_gap,
        !nested.layout.items.is_empty(),
        card_pad,
    );
    let x_offset = ep.left;
    let y_offset = ep.top;
    let content_x = x_offset + card_pad;
    let header_authored_attrs = if !source_path.is_empty()
        && (!title_svg.is_empty() || !inline_header_svg.is_empty() || !text_svg.is_empty())
    {
        title::authored_attrs(source_path, "header")
    } else {
        String::new()
    };

    let header_start = y_offset + content_box.content_top;
    let mut y = header_start;
    let mut header_pieces: Vec<String> = Vec::new();
    let mut header_end = header_start;

    if !inline_header_svg.is_empty() {
        // `at_x` is `x_offset`, NOT `content_x` — the inline band's own
        // internal composition already applies `card_pad` as its own
        // inset for the title/vars sub-groups (see the identical note at
        // `render_board`'s own inline wrapper).
        header_pieces.push(header_piece(
            &inline_header_svg,
            x_offset,
            y,
            "",
            content_x,
            header_start,
            &header_authored_attrs,
        ));
        y += inline_header_height;
        header_end = y;
        if !text_svg.is_empty() {
            y += child_gap + card_gap;
        }
    } else if !title_svg.is_empty() {
        header_pieces.push(header_piece(
            &title_svg,
            content_x,
            y + title_shift.title_dy,
            "title",
            content_x,
            header_start,
            &header_authored_attrs,
        ));
        y += shifted_title_height;
        header_end = y;
    }
    if !text_svg.is_empty() {
        let inner = extract_svg_inner_content(&text_svg)?;
        if !inner.is_empty() {
            header_pieces.push(header_piece(
                inner,
                content_x,
                y,
                "text",
                content_x,
                header_start,
                &header_authored_attrs,
            ));
        }
        y += text_height;
        header_end = y;
        if !nested_variables_svg.is_empty() && inline_header_svg.is_empty() {
            y += child_gap + card_gap;
        }
    }

    let mut items: Vec<String> = Vec::new();
    if !header_authored_attrs.is_empty() && !header_pieces.is_empty() {
        let header_box = match (nested_title_line_box, title_svg.is_empty()) {
            (Some((line_top, line_height)), false) => Some((
                line_top,
                if text_svg.is_empty() {
                    line_height
                } else {
                    header_end - header_start - line_top
                },
            )),
            _ => None,
        };
        items.push(title::tagged_authoring_block(
            &header_pieces.concat(),
            &header_authored_attrs,
            content_x,
            header_start,
            prose_width,
            header_end - header_start,
            card_pad,
            card_pad,
            content_box.content_top,
            header_box,
        ));
    } else {
        items.extend(header_pieces);
    }

    // `_build_board_content_items`'s own standalone variables item: never
    // folded into the combined header handle (no `data-authored-path` on
    // it), placed at the running `y` the header/text pieces left off at.
    if !nested_variables_svg.is_empty() {
        if text_svg.is_empty() && !title_svg.is_empty() {
            y += child_gap + card_gap;
        }
        items.push(translate_group(
            &num_full(px(content_x)),
            &num_full(px(y)),
            &nested_variables_svg,
            "",
        ));
    }

    let y_layout = y_offset + content_box.non_layout_height + content_box.gap_before_layout;
    if !layout_content.is_empty() {
        items.push(translate_group(
            &num_full(px(x_offset)),
            &num_full(px(y_layout)),
            &layout_content,
            "",
        ));
    }

    let inner_items_height = content_box.non_layout_height
        + content_box.gap_before_layout
        + actual_layout_height.max(0.0);

    let natural_board_height = ep.top + inner_items_height + ep.bottom;
    let board_width = available_width - margin.horizontal();
    let allocated_board_height = (available_height - margin.vertical()).max(0.0);
    let board_height = natural_board_height.max(allocated_board_height);
    let total_svg_width = available_width;
    let total_svg_height = board_height + margin.vertical();

    // `authored_bg`/`bg_rect`: `resolved_style.background` read directly and
    // gated on non-empty ONLY, the same law `layout_bg_rect` already applies
    // — NOT the harness-level "transparent" override the ROOT's own bg_rect
    // gates on (see the module doc's "Two background sources" note).
    let authored_bg = &nested.style.background;
    let border_radius = nested.style.border.radius;
    let bg_rect = if !authored_bg.is_empty() {
        format!(
            r#"<rect x="0" y="0" width="{}" height="{}" fill="{}" rx="{}"/>"#,
            py_float_str(board_width),
            py_float_str(board_height),
            xml_escape_attr(authored_bg),
            py_float_str(border_radius)
        )
    } else {
        String::new()
    };

    let border_rect = border_rect(board_width, board_height, &nested.style.border, "");

    let board_group = translate_group(
        &num_full(px(margin.left)),
        &num_full(px(margin.top)),
        &format!("{bg_rect}\n{border_rect}\n{}", items.concat()),
        "",
    );

    let svg = format!(
        "<svg width=\"{}\" height=\"{}\" viewBox=\"0 0 {} {}\">\n{board_group}\n</svg>",
        py_float_str(total_svg_width),
        py_float_str(total_svg_height),
        py_float_str(total_svg_width),
        py_float_str(total_svg_height)
    );

    Ok(RenderedItem {
        svg,
        height: total_svg_height,
    })
}

/// Returns `(svg, painted_markup)` — the composed board and the scoped
/// face-selection input `--embed-fonts` scans (see `painted_markup`'s own
/// doc, below, for what it is and is not).
pub fn render_board(payload: &BoardPayload, fonts: &FontBook) -> Result<(String, String), String> {
    payload.board_style.scoped(|| render_board_in_scope(payload, fonts))
}

fn render_board_in_scope(payload: &BoardPayload, fonts: &FontBook) -> Result<(String, String), String> {
    let mut board: BoardDoc = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("board payload deserialize: {e}"))?;

    let page_padding = board
        .page_padding
        .ok_or("board page_padding missing (nested board?)")?;
    let card_padding = board
        .card_padding
        .ok_or("board card_padding missing (nested board?)")?;
    let card_gap = board
        .card_gap
        .ok_or("board card_gap missing (nested board?)")?;

    let (boxes, container, overrides, fresh_render) = size_board(payload, fonts)?;
    // `layout_sizing.py::calculate_data_aware_layout`: the row title shifts are assigned
    // from the sized boxes, after the sizing renders (which carry none) and before the final one.
    let sized_items = size_board_items_json(payload, fonts, &boxes, &overrides)?;
    row_shifts::assign(&mut board, payload, fonts, &sized_items)?;

    // Layout bg rect and per-chart card bg both paint `style.background`
    // (`_render_layout`'s literal parameter, `boards.py:1123`) — the same
    // field `_rust_payload` hands the per-chart sweep
    // (`golden_sweep.py::_sweep_board`). There is no longer a second,
    // independently-authorable page-canvas source.
    let chrome = RenderChrome {
        bg: &board.style.background,
        layout_bg: &board.style.background,
        card_pad: card_padding,
        card_gap,
        item_gap: board.layout.gap,
        root_card_padding: card_padding,
        details_style: details_chrome_from_style(&board.style),
        tabs_style: tabs_chrome_from_style(&board.style),
        card_style: card_chrome_from_style(&board.style),
    };
    // `boards.py::render_board_svg`'s own `layout_content_height = height -
    // (2 * page_padding)` — `height = board.height`, the SIZED total
    // (`container.height` here); `layout_content_width = board.layout.
    // content_width` (no padding subtraction, already an inner dim).
    let root_layout_available_height = container.height - 2.0 * page_padding;
    let (layout_svg, actual_layout_height) = match board.layout.kind.as_str() {
        "rows" => render_rows(
            &board.layout,
            &boxes,
            container.content_width,
            root_layout_available_height,
            payload,
            &chrome,
            fonts,
            &fresh_render,
        )?,
        "cols" => render_cols(
            &board.layout,
            &boxes,
            container.content_width,
            root_layout_available_height,
            payload,
            &chrome,
            fonts,
            &overrides,
            &fresh_render,
        )?,
        "grid" => render_grid(
            &board.layout,
            &boxes,
            container.content_width,
            root_layout_available_height,
            payload,
            &chrome,
            fonts,
            &overrides,
            &fresh_render,
        )?,
        "tabs" => render_tabs_layout(
            &board.layout,
            &boxes,
            container.content_width,
            payload,
            &chrome,
            fonts,
        )?,
        other => return Err(format!("board layout not modeled: {other}")),
    };

    // `prose_width`/`title_text_width`: boards.py:990-993.
    let effective_gap = board.layout.gap + card_gap;
    let layout_content_width = container.content_width;
    let title_text_width = (layout_content_width - 2.0 * card_padding).max(0.0);
    let prose_width = if title_text_width > 0.0 {
        title_text_width
    } else {
        layout_content_width
    };
    let fonts_dir = fonts_dir_str(payload);
    let visible_variables = board.visible_variables();

    let use_title_inline = crate::variables::should_use_title_inline_band(
        &board.title,
        &visible_variables,
        layout_content_width,
        board.title_style.as_ref(),
        board.title_fonts.as_ref(),
        board.style.title.font.case.as_deref(),
        &board.style.variables,
        card_padding,
        &payload.variable_values,
        fonts_dir,
    )?;

    let mut inline_header_svg = String::new();
    let mut inline_header_height = 0.0_f64;
    let (title_svg, title_height, root_title_line_box) = if use_title_inline {
        let style = board
            .title_style
            .clone()
            .ok_or("board has a title but payload carries no title_style")?;
        let title_fonts = board
            .title_fonts
            .clone()
            .ok_or("board has a title but payload carries no title_fonts")?;
        let title_case = board.style.title.font.case.as_deref();
        let (band, band_h) = crate::variables::render_title_variables_inline_band(
            &board.title,
            &visible_variables,
            &payload.variable_values,
            layout_content_width,
            card_padding,
            style,
            title_fonts,
            title_case,
            board.style.title.font.color.as_deref(),
            board.style.title.min_height,
            &title::authored_attrs("title", "title"),
            "variables",
            Some(&payload.variable_datasets),
            &board.style.variables,
            fonts_dir,
            &payload.painted_italics,
        )?;
        inline_header_svg = band;
        inline_header_height = band_h;
        (String::new(), 0.0, None)
    } else if board.title.is_empty() {
        (String::new(), 0.0, None)
    } else {
        let style = board
            .title_style
            .clone()
            .ok_or("board has a title but payload carries no title_style")?;
        let title_fonts = board
            .title_fonts
            .clone()
            .ok_or("board has a title but payload carries no title_fonts")?;
        let title_case = board.style.title.font.case.as_deref();
        let mut svg = title::render_title_svg(
            &board.title,
            &payload.variable_values,
            title_case,
            prose_width,
            style.clone(),
            title_fonts.clone(),
            Some(&payload.painted_italics),
        )?;
        if let Some(color) = &board.style.title.font.color {
            svg = title::paint_title_svg_fill(&svg, color);
        }
        let measured_title_height = title::get_title_height(
            &board.title,
            &payload.variable_values,
            title_case,
            prose_width,
            style.clone(),
            title_fonts.clone(),
        )?;
        let height = measured_title_height.max(board.style.title.min_height);
        let line_box = title::title_line_box(style, title_fonts, measured_title_height)?;
        (svg, height, Some(line_box))
    };
    // `boards.py::render_board_svg`'s own text branch (`_render_text_svg`).
    let (text_svg, text_height) = if board.text.is_empty() {
        (String::new(), 0.0)
    } else {
        let style = board
            .text_style
            .clone()
            .ok_or("board has text but payload carries no text_style")?;
        let text_fonts = board
            .text_fonts
            .clone()
            .ok_or("board has text but payload carries no text_fonts")?;
        let column = board.style.text.as_prose_doc();
        let plan = board
            .prose_plan
            .ok_or("board has text but payload carries no prose_plan")?;
        let allow_raw_html = board.html_policy == "trusted-raw";
        let resolved_md = prose::resolve_markdown_jinja(&board.text, &payload.variable_values)?;
        // `boards.py::_render_text_svg` returns `(svg, height)` — its OWN
        // real-render height (see `render_nested_board`'s identical note).
        prose::render_prose_svg(
            &resolved_md,
            prose_width,
            &column,
            &plan,
            board.style.frame.card_padding,
            style.clone(),
            text_fonts.clone(),
            allow_raw_html,
            Some(&payload.painted_italics),
        )?
    };

    // Variable controls are inset by card_padding on the left (aligned with
    // chart content); reduce width by the same amount so the right edge is
    // unchanged. `boards.py`'s own gate: visible variables, a truthy raw
    // request-level `variables` dict, and no inline band already claiming them.
    let variables_width = (layout_content_width - card_padding).max(0.0);
    let (variables_svg, variables_height) = if !visible_variables.is_empty()
        && payload.variables_overrides_present
        && inline_header_svg.is_empty()
    {
        let label_case = board
            .style
            .variables
            .label_font_case()
            .ok_or("style.variables.label.font.case must be resolved")?;
        let controls = crate::variables::resolve_controls(
            &visible_variables,
            &payload.variable_values,
            Some(&payload.variable_datasets),
            &board.style.variables,
            label_case,
        )?;
        crate::variables::render_variables_strip_svg(
            &controls,
            variables_width,
            &board.style.variables,
            "start",
            "variables",
            fonts_dir,
        )?
    } else {
        (String::new(), 0.0)
    };

    let content_box = title::compute_board_content_box(
        title_height,
        text_height,
        variables_height,
        inline_header_height,
        effective_gap,
        !board.layout.items.is_empty(),
        card_padding,
    );

    let content_x = page_padding + card_padding;
    let mut y = page_padding + content_box.content_top;
    let mut header = String::new();
    let mut header_has_piece = false;
    if !inline_header_svg.is_empty() {
        // `header_piece`'s own `own_attrs=""` branch (bare translate at
        // `x_offset`, NOT `content_x` — the band's own internal composition
        // already applies `card_pad` as its own inset for the title/vars
        // sub-groups, so the outer wrapper must not add it a second time)
        // — the band's OWN title sub-group carries the `title`/`title`
        // handle, tagged inside `render_title_variables_inline_band` itself.
        header.push_str(&translate_group(
            &num_full(px(page_padding)),
            &num_full(px(y)),
            &inline_header_svg,
            "",
        ));
        y += inline_header_height;
        header_has_piece = true;
        if !text_svg.is_empty() {
            y += effective_gap;
        }
    } else if !title_svg.is_empty() {
        header.push_str(&title::tagged_authoring_block(
            &title_svg,
            &title::authored_attrs("title", "title"),
            content_x,
            y,
            prose_width,
            title_height,
            card_padding,
            card_padding,
            content_box.content_top,
            root_title_line_box,
        ));
        y += title_height;
        header_has_piece = true;
    }
    if !text_svg.is_empty() {
        // `boards.py::_build_board_content_items`'s `_render_text_svg` branch
        // strips the `<svg>` wrapper before re-tagging (unlike the title,
        // which keeps its own nested `<svg>` — see `title.rs`'s module doc);
        // an empty inner body (after stripping) contributes height but no
        // element, matching production's own `if md:` guard.
        let inner = extract_svg_inner_content(&text_svg)?;
        if !inner.is_empty() {
            let top_inset = if header_has_piece {
                0.0
            } else {
                content_box.content_top
            };
            header.push_str(&title::tagged_authoring_block(
                inner,
                &title::authored_attrs("text", "text"),
                content_x,
                y,
                prose_width,
                text_height,
                card_padding,
                card_padding,
                top_inset,
                None,
            ));
        }
        y += text_height;
        header_has_piece = true;
        if !variables_svg.is_empty() && inline_header_svg.is_empty() {
            y += effective_gap;
        }
    }
    if !variables_svg.is_empty() {
        if text_svg.is_empty() && header_has_piece {
            y += effective_gap;
        }
        header.push_str(&translate_group(
            &num_full(px(content_x)),
            &num_full(px(y)),
            &variables_svg,
            "",
        ));
    }
    let y_layout = page_padding + content_box.non_layout_height + content_box.gap_before_layout;
    let content_items_height = content_box.non_layout_height
        + content_box.gap_before_layout
        + actual_layout_height.max(0.0);

    let total_width = board
        .width
        .max(container.content_width + 2.0 * page_padding);
    let mut total_height = container
        .height
        .max(content_items_height + 2.0 * page_padding);

    let footer = &board.style.footer;
    if footer.visible {
        let footer_font_size = footer
            .font
            .size
            .ok_or("footer.font.size must be resolved")?;
        let rule_gap = if footer.divider.line.visible {
            footer.divider.gap
        } else {
            0.0
        };
        total_height += footer.y_offset + footer_font_size + rule_gap;
    }

    let font_family = board
        .style
        .font
        .family
        .as_deref()
        .ok_or("style.font.family must be resolved")?;
    let footer_family = footer
        .font
        .family
        .as_deref()
        .ok_or("footer.font.family must be resolved")?;
    let timestamp_family = board
        .style
        .timestamp
        .font
        .family
        .as_deref()
        .ok_or("timestamp.font.family must be resolved")?;

    // Root background rect gates on the harness's OWN `background` param
    // (None when the resolved style is literally "transparent" —
    // `boards.py:1184-1185`), unlike the layout rects above, which always
    // draw from `resolved_style.background` directly regardless of that
    // override.
    let bg_rect = match &payload.background {
        Some(fill) => format!(
            r#"<rect x="0" y="0" width="{}" height="{}" fill="{}"/>"#,
            py_float_str(total_width),
            py_float_str(total_height),
            xml_escape_attr(fill)
        ),
        None => String::new(),
    };

    let footer_x = total_width - page_padding;
    let footer_y = total_height - footer.y_offset;
    let mut footer_element = String::new();
    let mut footer_width = 0.0_f64;
    if footer.visible {
        let (attribution, w) = footer_attribution_svg(
            footer,
            footer_family,
            footer_x,
            footer_y,
            fonts,
        )?;
        footer_width = w;
        let mut footer_parts: Vec<String> = Vec::new();
        if footer.divider.line.visible {
            let rule = &footer.divider.line;
            let footer_font_size = footer
                .font
                .size
                .ok_or("footer.font.size must be resolved")?;
            let rule_y = footer_y - footer_font_size - footer.divider.gap;
            footer_parts.push(format!(
                r#"<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="{}"{}/>"#,
                num_full(page_padding),
                num_full(rule_y),
                num_full(footer_x),
                num_full(rule_y),
                xml_escape_attr(&rule.color),
                num_full(rule.width),
                rule.dash_attr()
            ));
        }
        footer_parts.push(attribution);
        // boards.py:1247 — `"\n".join(footer_parts)`, not a bare concat.
        footer_element = footer_parts.join("\n");
    }

    let timestamp = &board.style.timestamp;
    let mut timestamp_element = String::new();
    if timestamp.visible {
        let ts_size = timestamp
            .font
            .size
            .ok_or("timestamp.font.size must be resolved")?;
        let ts_color = timestamp
            .font
            .color
            .as_deref()
            .ok_or("timestamp.font.color must be resolved")?;
        let ts_y = if timestamp.position == "top" {
            timestamp.y
        } else {
            footer_y
        };
        let (ts_x, anchor) = if timestamp.align == "left" {
            (page_padding, "start")
        } else if timestamp.position == "footer" && footer.visible {
            (
                footer_x - footer_width - timestamp.gap,
                "end",
            )
        } else {
            (total_width - page_padding, "end")
        };
        timestamp_element = format!(
            r#"<text data-role="render-timestamp" x="{}" y="{}" text-anchor="{anchor}" font-size="{}" fill="{ts_color}" font-family="{timestamp_family}" style="font-variant-numeric: tabular-nums lining-nums;">{}</text>"#,
            num_full(ts_x),
            num_full(ts_y),
            num_full(ts_size),
            html_escape(&payload.render_timestamp_text), anchor = xml_escape_attr(anchor), ts_color = xml_escape_attr(ts_color), timestamp_family = xml_escape_attr(timestamp_family)
        );
    }

    // boards.py:960-964: the page title is the board's own (Jinja-resolved,
    // no case transform) when authored, else the literal "dbt Charts".
    let page_title = if board.title.is_empty() {
        "dbt Charts".to_string()
    } else {
        title::resolve_jinja(&board.title, &payload.variable_values)?
    };

    // `boards.py::_build_board_content_items`'s `if layout_content: items.
    // append(...)` — the wrapper `<g>` is only emitted when `_render_layout`
    // returned something, matching `render_nested_board`'s own gate above.
    let layout_group = if layout_svg.is_empty() {
        String::new()
    } else {
        translate_group(
            &num_full(px(page_padding)),
            &num_full(px(y_layout)),
            &layout_svg,
            "",
        )
    };

    // `boards.py:1340-1349`: `painted_markup = "".join(content_items) +
    // font_family + chrome_families` — content_items is title/text/variables/
    // inline-header (`header` here) plus the layout body (`layout_group`),
    // NEVER the background rect, the URL font-face roster, or the footer/
    // timestamp markup itself; those two contribute their font family only
    // while they are visible. This is the face-selection SCAN input
    // `--embed-fonts` needs — distinct from `svg` below, which is what gets
    // the URL roster substring replaced.
    let chrome_families: String = [
        (footer.visible, footer_family),
        (timestamp.visible, timestamp_family),
    ]
    .into_iter()
    .filter(|(visible, _)| *visible)
    .map(|(_, family)| family)
    .collect();
    let painted_markup = format!("{header}{layout_group}{font_family}{chrome_families}");

    // boards.py:1289-1297: `<defs>\n{grid_defs}\n{svg_styles}\n</defs>` — this
    // composer never has a grid pattern, so `grid_defs` is always "", leaving
    // a blank line before `{svg_styles}`. Likewise `{margin_lines}` (also
    // always "" here) leaves a blank line between the layout `<g>` and the
    // timestamp element.
    let svg = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" width="{w}" height="{h}" preserveAspectRatio="xMinYMin meet" style="display: block;" data-rendered-at="{rendered_at}" data-dbt-page-title="{page_title_attr}"{hover_attributes} data-dbt-page-background="{background_attr}" aria-label="{page_title_attr}">

<defs>

{defs}
</defs>
{bg_rect}
{header}{layout_group}

{timestamp_element}
{footer_element}
</svg>"#,
        vb_w = py_float_str(total_width),
        vb_h = py_float_str(total_height),
        w = num_full(total_width),
        h = num_full(total_height),
        rendered_at = xml_escape_attr(&payload.render_time_iso),
        page_title_attr = xml_escape_attr(&page_title),
        background_attr = xml_escape_attr(&board.style.background),
        defs = payload.defs_styles_block,
        layout_group = layout_group,
        hover_attributes = payload.hover_attributes,
    );
    Ok((dedupe_repeated_style_rules(&svg), painted_markup))
}

#[cfg(test)]
mod dedupe_repeated_style_rules_tests {
    use super::dedupe_repeated_style_rules;

    /// Two enclosing-scope-identical style blocks (both direct children of
    /// the same root `<svg>` — no nested `<svg>` between them) with the
    /// SAME class-prefixed rule line: the second occurrence is dropped.
    #[test]
    fn a_repeated_rule_line_in_the_same_svg_scope_is_dropped() {
        let svg = "<svg>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n<g>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n</g>\n</svg>";
        let out = dedupe_repeated_style_rules(svg);
        assert_eq!(out.matches(".md-abc123-text").count(), 1, "{out}");
    }

    /// The same rule line in a DIFFERENT enclosing `<svg>` scope (a nested
    /// board's own `<svg>` element) survives — dedup never crosses an
    /// `<svg>` boundary.
    #[test]
    fn a_repeated_rule_line_in_a_different_svg_scope_survives() {
        let svg = "<svg>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n<svg>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n</svg>\n</svg>";
        let out = dedupe_repeated_style_rules(svg);
        assert_eq!(out.matches(".md-abc123-text").count(), 2, "{out}");
    }

    /// A style block emptied entirely by dedup vanishes, leading newline
    /// included, rather than leaving a blank line behind.
    #[test]
    fn a_fully_deduped_block_leaves_no_blank_line() {
        let svg = "<svg>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n</svg>";
        let out = dedupe_repeated_style_rules(svg);
        assert_eq!(
            out,
            "<svg>\n  <style>\n    .md-abc123-text { fill: red; }\n  </style>\n\n</svg>"
        );
    }

    /// A non-eligible line (no `.md-<hash>-<name> {` shape) is never
    /// deduped, even when byte-identical across two blocks in one scope.
    #[test]
    fn a_non_eligible_line_is_never_deduped() {
        let svg = "<svg>\n  <style>\n    body { margin: 0; }\n  </style>\n  <style>\n    body { margin: 0; }\n  </style>\n</svg>";
        let out = dedupe_repeated_style_rules(svg);
        assert_eq!(out.matches("body { margin: 0; }").count(), 2, "{out}");
    }
}

#[cfg(test)]
mod tests {
    use super::{
        border_rect, find_brand_phrase, ink_carries_padding, is_internal_svg,
        is_magnitude_colored, translate_group, wrap_chart_svg, BorderStyleDoc, CardChrome, Padding,
    };
    use serde_json::json;

    /// A board level whose cards carry `border` (`chart_defaults.border`) and
    /// `shadow` (`style.box_shadow`, `null` for none).
    fn card(border: serde_json::Value, shadow: serde_json::Value) -> CardChrome {
        CardChrome {
            border: serde_json::from_value(border).unwrap(),
            shadow: serde_json::from_value(shadow).unwrap(),
            ink_canvas: "#fafafa".to_string(),
        }
    }

    fn no_border() -> serde_json::Value {
        json!({"border": {"width": 0.0, "color": "transparent", "radius": 0.0}})
    }

    fn no_card() -> CardChrome {
        card(json!({"width": 0.0, "color": "transparent", "radius": 0.0}), json!(null))
    }

    #[test]
    fn dashed_border_prints_dash_pattern_with_format_g() {
        // Expected string is `svg_utils.border_dash_attrs` on the same border.
        let border: BorderStyleDoc = serde_json::from_value(json!({
            "width": 1.0, "color": "#000", "radius": 0.0,
            "dash_array": [3.14159265, 2.0], "line_cap": "round", "dash_offset": 0.00001,
        }))
        .unwrap();
        assert_eq!(
            crate::common::border_dash_attrs(
                border.dash_array.as_deref(),
                border.line_cap.as_deref(),
                border.dash_offset,
            ),
            r#" stroke-dasharray="3.14159,2" stroke-linecap="round" stroke-dashoffset="1e-05""#
        );
    }

    #[test]
    fn is_internal_svg_matches_paints_marks_complement() {
        for t in ["table", "kpi", "spark_bar", "callout"] {
            assert!(is_internal_svg(t), "{t} should be internal-svg");
        }
        for t in ["bar", "line", "area", "scatter", "heatmap", "pie", "histogram"] {
            assert!(!is_internal_svg(t), "{t} paints a mark, should not be internal-svg");
        }
    }

    #[test]
    fn a_composed_callout_strips_its_own_root_svg() {
        // Review round 2 HIGH-R2-A: `is_svg_family` (the translate-wrap
        // inset decision) is false for callout, so the old code took the
        // else branch verbatim and never stripped callout's own root
        // `<svg>` — production strips it regardless of the inset decision
        // (`rendering.py:618-624`'s `is_internal_svg`, a separate check).
        let chart_json = json!({"id": "status"});
        let callout_svg = r##"<svg xmlns="http://www.w3.org/2000/svg" class="dbt-chart-callout" width="320.0" height="66.4" viewBox="0 0 320.0 66.4"><rect fill="#e6f4ea"/></svg>"##;
        let padding = Padding { left: 16.0, right: 16.0, top: 16.0, bottom: 16.0 };
        let (boundary, _height) =
            wrap_chart_svg(&chart_json, "callout", callout_svg, 320.0, &padding, &no_card())
                .unwrap();
        assert!(!boundary.contains("<svg"), "root svg should be stripped: {boundary}");
        assert!(boundary.contains(r##"fill="#e6f4ea""##), "inner content must survive: {boundary}");
    }

    #[test]
    fn the_wrapper_stamps_chart_type_and_the_charts_own_canvas() {
        // `rendering.py::_wrap_rendered_chart_svg`: every wrapper names its
        // chart type; the composited canvas rides along for hover emphasis
        // except on KPI and callout, whose `ChartIdentity.canvas` is None.
        let table_svg = r#"<svg xmlns="http://www.w3.org/2000/svg" width="200" height="90" viewBox="0 0 200 90"><text>t</text></svg>"#;
        let padding = Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 };
        let table = json!({"id": "t", "canvas": "#ffffff", "style": {"table": no_border()}});
        let (wrapped, _) =
            wrap_chart_svg(&table, "table", table_svg, 200.0, &padding, &no_card()).unwrap();
        assert!(wrapped.contains(r#"data-chart-id="t" data-chart-type="table" data-chart-title="#));
        assert!(wrapped.contains(r##"data-dbt-chart-canvas="#ffffff""##));

        let callout = json!({"id": "c", "canvas": "#ffffff"});
        let (wrapped, _) =
            wrap_chart_svg(&callout, "callout", table_svg, 200.0, &padding, &no_card()).unwrap();
        assert!(wrapped.contains(r#"data-chart-type="callout""#));
        assert!(!wrapped.contains("data-dbt-chart-canvas"));
    }

    const CARD_SVG: &str = concat!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" width="200" height="90" "#,
        r#"viewBox="0 0 200 90"><text>t</text></svg>"#
    );
    const ZERO_PADDING: Padding = Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 };
    const CLIP: &str = concat!(
        r#"<clipPath id="dbt-card-clip-200-90-8"><rect width="200.0" height="90.0" rx="8.0"/>"#,
        r#"</clipPath><g clip-path="url(#dbt-card-clip-200-90-8)">"#
    );
    const BORDER_RECT: &str = concat!(
        r##"<rect class="dbt-card-border" x="1.0" y="1.0" width="198.0" height="88.0" "##,
        r##"fill="none" stroke="#7a3fa0" stroke-width="2.0" rx="7.0"/>"##
    );
    const SHADOW_RECT: &str = concat!(
        r##"<rect class="dbt-card-shadow" width="200.0" height="90.0" rx="8.0" "##,
        r##"fill="#f3f0ea" style="filter: drop-shadow(0px 2px 8px #00000040)"/>"##
    );

    fn border_2px() -> serde_json::Value {
        json!({"width": 2.0, "color": "#7a3fa0", "radius": 8.0})
    }

    fn square_border() -> serde_json::Value {
        json!({"width": 0.0, "color": "transparent", "radius": 0.0})
    }

    fn shadow() -> serde_json::Value {
        json!({"dx": 0.0, "dy": 2.0, "blur": 8.0, "color": "#00000040"})
    }

    fn bar() -> serde_json::Value {
        json!({"id": "b", "canvas": "#f3f0ea"})
    }

    fn wrap_card(chart: &serde_json::Value, chart_type: &str, card: &CardChrome) -> String {
        wrap_chart_svg(chart, chart_type, CARD_SVG, 200.0, &ZERO_PADDING, card).unwrap().0
    }

    #[test]
    fn a_bordered_card_is_clipped_to_its_radius_and_stroked_last() {
        // `rendering.py::_wrap_rendered_chart_svg`: `round_clipped` wraps the
        // card when the radius is above zero, `border_rect` follows it.
        let wrapped = wrap_card(&bar(), "bar", &card(border_2px(), json!(null)));
        let (_, body) = wrapped.split_once('>').unwrap();
        assert!(body.starts_with(CLIP), "{body}");
        assert!(body.ends_with(&format!("</g>{BORDER_RECT}</g>")), "{body}");
        assert!(!wrapped.contains("dbt-card-shadow"));
    }

    #[test]
    fn a_shadowed_card_paints_its_shadow_rect_before_the_clip() {
        // `svg_utils.shadow_rect`: filled with the chart's own canvas, radius
        // the card border's, `{:g}` offsets.
        let wrapped = wrap_card(&bar(), "bar", &card(border_2px(), shadow()));
        let (_, body) = wrapped.split_once('>').unwrap();
        assert!(body.starts_with(&format!("{SHADOW_RECT}{CLIP}")), "{body}");
        assert!(body.ends_with(&format!("</g>{BORDER_RECT}</g>")), "{body}");
    }

    #[test]
    fn a_card_with_no_border_and_no_shadow_gets_no_wrapper_layers() {
        let wrapped = wrap_card(&bar(), "bar", &no_card());
        for layer in ["dbt-card-clip", "clip-path", "dbt-card-border", "dbt-card-shadow"] {
            assert!(!wrapped.contains(layer), "{layer}: {wrapped}");
        }
    }

    #[test]
    fn a_borderless_square_card_with_a_shadow_has_no_clip() {
        // The clip needs a radius above zero; the shadow rect still paints.
        let wrapped = wrap_card(&bar(), "bar", &card(square_border(), shadow()));
        assert!(wrapped.contains(concat!(
            r#"<rect class="dbt-card-shadow" width="200.0" height="90.0" rx="0.0""#
        )));
        assert!(!wrapped.contains("clip"), "{wrapped}");
        assert!(!wrapped.contains("dbt-card-border"));
    }

    #[test]
    fn a_dashed_border_prints_its_dash_attributes_between_the_stroke_and_the_radius() {
        let dashed = json!({"width": 2.0, "color": "#0b57d0", "radius": 0.0,
                            "dash_array": [6.0, 3.0], "line_cap": "round", "dash_offset": 1.5});
        let wrapped = wrap_card(&bar(), "bar", &card(dashed, json!(null)));
        // radius 0 minus the half stroke clamps to the int `0`, as `max(.., 0)` does.
        let expected = concat!(
            r##"<rect class="dbt-card-border" x="1.0" y="1.0" width="198.0" height="88.0" "##,
            r##"fill="none" stroke="#0b57d0" stroke-width="2.0" stroke-dasharray="6,3" "##,
            r##"stroke-linecap="round" stroke-dashoffset="1.5" rx="0"/></g>"##
        );
        assert!(wrapped.ends_with(expected), "{wrapped}");
        assert!(!wrapped.contains("clip"));
    }

    #[test]
    fn a_border_that_draws_nothing_is_an_empty_string_at_any_class() {
        // `svg_utils.border_rect`: no stroke width or no color paints nothing.
        for border in [
            json!({"width": 0.0, "color": "#000", "radius": 4.0}),
            json!({"width": 2.0, "color": "", "radius": 4.0}),
        ] {
            let border: BorderStyleDoc = serde_json::from_value(border).unwrap();
            assert_eq!(border_rect(200.0, 90.0, &border, "dbt-card-border"), "");
            assert_eq!(border_rect(200.0, 90.0, &border, ""), "");
        }
    }

    #[test]
    fn kpi_and_table_cards_take_their_own_border_not_the_board_default() {
        // `rendering.py::_card_border`: kpi, table, geoshape and point_map
        // read `chart.style.<type>.border`; every other type, the board's.
        let boardwide = card(border_2px(), json!(null));
        for chart_type in ["kpi", "table"] {
            let own = json!({"id": "c", "canvas": "#fff",
                             "style": {chart_type: {"border": border_2px()}}});
            let wrapped = wrap_card(&own, chart_type, &no_card());
            assert!(wrapped.contains(CLIP), "{chart_type}: {wrapped}");
            let tail = format!("</g>{BORDER_RECT}</g>");
            assert!(wrapped.ends_with(&tail), "{chart_type}: {wrapped}");

            let none = json!({"id": "c", "canvas": "#fff",
                             "style": {chart_type: {"border": square_border()}}});
            assert!(!wrap_card(&none, chart_type, &boardwide).contains("dbt-card-border"));
        }
    }

    #[test]
    fn a_kpi_without_its_resolved_border_is_refused_by_name() {
        let kpi = json!({"id": "k"});
        let err = wrap_chart_svg(&kpi, "kpi", CARD_SVG, 200.0, &ZERO_PADDING, &no_card())
            .unwrap_err();
        assert!(err.contains("chart \"k\"") && err.contains("style.kpi.border"), "{err}");
    }

    #[test]
    fn a_kpi_shadow_is_filled_with_the_ink_canvas_and_a_callout_clips_to_its_own_radius() {
        // KPI and callout carry no composited canvas, so their shadow takes
        // `chart_defaults.ink_canvas`; a callout draws its own border but its
        // shadow and clip follow `chart.style.border.radius`.
        let shadowed = card(square_border(), shadow());
        let kpi = json!({"id": "k", "style": {"kpi": {"border": square_border()}}});
        let wrapped = wrap_card(&kpi, "kpi", &shadowed);
        assert!(wrapped.contains(r##"rx="0.0" fill="#fafafa""##), "{wrapped}");

        let callout = json!({"id": "c", "style": {"border": {"radius": 4.0}}});
        let wrapped = wrap_card(&callout, "callout", &shadowed);
        assert!(wrapped.contains(r##"rx="4.0" fill="#fafafa""##), "{wrapped}");
        assert!(wrapped.contains(r#"<clipPath id="dbt-card-clip-200-90-4">"#), "{wrapped}");
        assert!(!wrapped.contains("dbt-card-border"));

        let plain = wrap_card(&callout, "callout", &no_card());
        assert!(!plain.contains("clip") && !plain.contains("dbt-card-shadow"), "{plain}");
    }

    #[test]
    fn a_shadow_color_that_is_not_one_css_color_is_refused() {
        let hostile =
            json!({"dx": 0.0, "dy": 2.0, "blur": 8.0, "color": "red; background:url(//x)"});
        let card = card(square_border(), hostile);
        let err = wrap_chart_svg(&bar(), "bar", CARD_SVG, 200.0, &ZERO_PADDING, &card)
            .unwrap_err();
        assert!(err.contains("ERR-CSS-COLOR-INVALID-AT-RENDER"), "{err}");
    }

    #[test]
    fn the_wrapper_names_itself_a_chart_group_and_describes_a_subtitled_chart() {
        // `rendering.py::_wrap_rendered_chart_svg`: `role="group"` and
        // `aria-roledescription="chart"` sit between `data-chart-height` and
        // `aria-label`; a non-empty subtitle becomes a leading `<desc>`
        // (escaped like `escape_attr`); a `null` one (callout, KPI) or an
        // empty one emits none.
        let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" width="200" height="90" viewBox="0 0 200 90"><text>t</text></svg>"#;
        let padding = Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 };
        let wrap = |chart: serde_json::Value, chart_type: &str| {
            wrap_chart_svg(&chart, chart_type, svg, 200.0, &padding, &no_card()).unwrap().0
        };
        let subtitled = wrap(
            json!({"id": "t", "canvas": "#fff", "subtitle": "A & \"b\"",
                   "style": {"table": no_border()}}),
            "table",
        );
        assert!(subtitled.contains(
            r#"data-chart-height="90.0" role="group" aria-roledescription="chart" aria-label="t""#
        ));
        let (_, body) = subtitled.split_once('>').unwrap();
        assert!(body.starts_with("<desc>A &amp; &quot;b&quot;</desc><rect"), "{body}");
        for subtitle in [json!(null), json!("")] {
            let plain = wrap(json!({"id": "c", "subtitle": subtitle}), "callout");
            assert!(plain.contains(r#"role="group" aria-roledescription="chart""#));
            assert!(!plain.contains("<desc>"));
        }
    }

    #[test]
    fn callout_ink_never_carries_padding() {
        // `chart/rendering.py:585-602`: a callout's card renders full-bleed
        // at 0,0, so its own box's ink already fills the outer box — unlike
        // every painting-mark family this else-branch also serves, whose
        // Vega spec bakes the padding into its own render. A regression
        // back to the old blanket `true` default makes the selection box's
        // `dbt-box-inner` rect disagree with a real callout's own ink,
        // which `playground/callout-tones` caught via board_sweep.py's
        // `chrome_bytes` structural contract.
        assert!(!ink_carries_padding(&json!({}), "callout"));
    }

    #[test]
    fn bar_ink_still_carries_padding() {
        assert!(ink_carries_padding(&json!({}), "bar"));
    }

    #[test]
    fn the_brand_phrase_needs_a_word_boundary_like_pythons_regex() {
        // Only ASCII letters, digits and `_` glue: a translated footer may run
        // the brand into non-Latin text. Neighbors are classified as
        // characters, not bytes, so a multi-byte letter is one neighbor.
        assert_eq!(find_brand_phrase("cafédbt charts"), Some((5, 15)));
        assert_eq!(find_brand_phrase("dbt Chartsで作成"), Some((0, 10)));
        assert_eq!(find_brand_phrase("made with dbt charts"), Some((10, 20)));
        assert_eq!(find_brand_phrase("made with dbt Charts"), Some((10, 20)));
        assert_eq!(find_brand_phrase("made with dbt chartsX"), None);
    }

    #[test]
    fn translate_group_skips_the_wrapper_only_when_theres_nothing_to_carry() {
        // Regression: `render_nested_board`'s own bg/border/items group used
        // to hand-roll `<g transform="translate(...)">` unconditionally, so
        // a margin-free nested board grew an extra bare `<g>` production's
        // `translate_group` no-ops away — a `chrome_bytes` byte diff with a
        // perfect pixel match (ladder/variables/07-nested-board-variables).
        assert_eq!(translate_group("0", "0", "<rect/>", ""), "<rect/>");
        assert_eq!(
            translate_group("0", "0", "<rect/>", " data-x=\"1\""),
            r#"<g transform="translate(0, 0)" data-x="1"><rect/></g>"#
        );
        assert_eq!(
            translate_group("5", "0", "<rect/>", ""),
            r#"<g transform="translate(5, 0)"><rect/></g>"#
        );
    }

    #[test]
    fn magnitude_colored_reads_the_charts_own_resolved_color_channel() {
        // Regression: `wrap_chart_svg` never emitted `data-dbt-magnitude-
        // colored`, a `chrome_bytes`-only hover-emphasis hint
        // `_is_magnitude_colored` computes from `resolved_channels.color`
        // (charts/more-types_0's heatmap, 0% pixel diff, chrome_bytes DIFF).
        let no_color = serde_json::json!({"resolved_channels": {}});
        assert!(!is_magnitude_colored(&no_color));

        let gradient = serde_json::json!({
            "resolved_channels": {"color": {"mode": "gradient"}}
        });
        assert!(is_magnitude_colored(&gradient));

        let quantitative_series = serde_json::json!({
            "resolved_channels": {"color": {"mode": "series", "quantitative_data": true}}
        });
        assert!(is_magnitude_colored(&quantitative_series));

        let categorical_series = serde_json::json!({
            "resolved_channels": {"color": {"mode": "series", "quantitative_data": false}}
        });
        assert!(!is_magnitude_colored(&categorical_series));

        let conditional_with_fallback = serde_json::json!({
            "resolved_channels": {"color": {"mode": "conditional", "fallback_scale": {}}}
        });
        assert!(is_magnitude_colored(&conditional_with_fallback));

        let conditional_no_fallback = serde_json::json!({
            "resolved_channels": {"color": {"mode": "conditional", "fallback_scale": null}}
        });
        assert!(!is_magnitude_colored(&conditional_no_fallback));
    }
}

/// Item (b): a refused chart's own error card and its wrap.
#[cfg(test)]
mod error_card_tests {
    use super::*;

    fn fonts_dir() -> String {
        format!(
            "{}/../../../src/dbt_charts/core/render/fonts",
            env!("CARGO_MANIFEST_DIR")
        )
    }

    fn style() -> Value {
        let font = |color: &str, size: f64| {
            serde_json::json!({"family": "Inter", "color": color, "size": size, "weight": "400"})
        };
        serde_json::json!({
            "tone": "negative",
            "background": "#fee2e2",
            "border": {"width": 1.0, "color": "#c00000", "radius": 4.0},
            "padding": {"top": 12.0, "right": 12.0, "bottom": 12.0, "left": 12.0},
            "section_gap": 6.0,
            "bold_font_weight": "600",
            "title": {"font": font("#900", 14.0), "y_offset": 0.0},
            "message": {"font": font("#333", 12.0), "y_offset": 0.0},
        })
    }

    fn card_svg(card: &ErrorCard, width: f64, height: f64) -> String {
        let dir = fonts_dir();
        render_error_card_svg(
            width,
            height,
            card,
            &style(),
            Some(&dir),
            &FontBook::empty(),
        )
        .unwrap()
    }

    fn refusal<'a>(title: Option<&'a str>, message: &'a str) -> ErrorCard<'a> {
        ErrorCard {
            chart_id: "c",
            title,
            code: "ERR-ENGINE-REFUSED",
            message,
            hint: None,
            doc_url: None,
        }
    }

    #[test]
    fn card_paints_code_badge_title_and_message_in_a_bordered_box() {
        let svg = card_svg(&refusal(Some("Revenue trend"), "bar: not supported"), 300.0, 80.0);
        assert!(svg.starts_with("<svg"));
        assert!(svg.contains("dbt-chart-callout"));
        assert!(svg.contains("Revenue trend"));
        assert!(svg.contains("bar: not supported"));
        assert!(svg.contains(r##"fill="#fee2e2""##));
        assert!(svg.contains(r##"stroke="#c00000""##));
        assert_eq!(
            svg.matches("<text").count(),
            3,
            "code badge + title line + message line: {svg}"
        );
        // The code badge always resolves its own doc anchor via
        // `build_doc_url`, independent of whether a registry backs it.
        assert!(svg.contains(
            r#"href="https://docs.dbtcharts.com/reference/errors/#err-engine-refused""#
        ));
    }

    #[test]
    fn card_omits_the_title_block_when_none() {
        let svg = card_svg(&refusal(None, "chart type not implemented"), 300.0, 80.0);
        assert_eq!(
            svg.matches("<text").count(),
            2,
            "code badge + message line only: {svg}"
        );
    }

    /// The hint block and the "See the docs" link only paint when their own
    /// inputs are present — the shape a `chart_error`'s own registered
    /// diagnostic carries that an unregistered render-time refusal does not.
    #[test]
    fn card_paints_hint_and_see_the_docs_link_when_both_present() {
        let svg = card_svg(
            &ErrorCard {
                chart_id: "c",
                title: Some("Warehouse rejected an unknown column or table reference"),
                code: "ERR-BINDER-UNKNOWN-COLUMN",
                message: "Warehouse could not resolve an unknown column or table (query: missing_table)",
                hint: Some("Check the column name against your model's schema."),
                doc_url: Some("https://docs.dbtcharts.com/reference/errors/#err-binder-unknown-column"),
            },
            700.0,
            80.0,
        );
        assert!(svg.contains(&*quick_xml::escape::escape("Check the column name against your model's schema.")));
        assert!(svg.contains(r#"font-style="italic""#));
        assert!(svg.contains("See the docs"));
        assert!(svg.contains(
            r#"href="https://docs.dbtcharts.com/reference/errors/#err-binder-unknown-column""#
        ));
    }

    #[test]
    fn wrap_stamps_chart_identity_at_zero_ink_inset() {
        let card = card_svg(&refusal(None, "refused"), 200.0, 100.0);
        let (wrapped, height) =
            wrap_error_card_svg("my_chart", "bar", "charts/x.yml", false, "", &[], &card, 200.0)
                .unwrap();
        assert_eq!(height, 100.0);
        assert!(wrapped.starts_with(r#"<g class="dbt-chart dbt-chart-callout""#));
        assert!(wrapped.contains(r#"data-chart-id="my_chart" data-chart-type="bar""#));
        assert!(wrapped.contains(r#"data-chart-title="my_chart""#));
        // The error card is an `is_error_fallback` wrapper: same role and
        // roledescription, never a description (no subtitle is passed).
        assert!(wrapped.contains(
            r#"data-chart-height="100.0" role="group" aria-roledescription="chart" aria-label="my_chart""#
        ));
        assert!(!wrapped.contains("<desc>"));
        assert!(wrapped.contains(r#"data-authored-path="charts/x.yml""#));
        // Zero ink inset: the outer/inner selection boxes are identical, not
        // shrunk by any padding (production's `_ZERO_PADDING` on its own
        // `is_error_fallback` path).
        assert!(wrapped.contains(r#"class="dbt-box-inner" x="0" y="0" width="200" height="100""#));
    }
}

#[cfg(test)]
mod py_max_zero_tests {
    use super::*;

    /// `max(x, 0)`, Python's own tie rule: the two-arg form returns the
    /// FIRST operand when neither is strictly greater, so `max(0.0, 0)`
    /// is the float `0.0` (formatted `"0.0"`), not the bare int `0` —
    /// only a strictly-negative `x` collapses to `"0"`.
    #[test]
    fn only_strictly_negative_collapses_to_bare_zero() {
        assert_eq!(py_max_zero(-1.0), "0");
        assert_eq!(py_max_zero(0.0), "0.0");
        assert_eq!(py_max_zero(5.5), "5.5");
    }
}

#[cfg(test)]
mod nested_box_sidecar_tests {
    use super::*;
    use crate::text::FontBook;

    /// A minimal, fully-resolved board JSON (own `BoardDoc` shape) — reused
    /// for both the root board and the nested board under test, since a
    /// nested board's own resolved JSON is the SAME shape recursively
    /// (`ResolvedLayoutItem.board: ResolvedBoard`).
    fn minimal_board_json(layout_items: serde_json::Value) -> serde_json::Value {
        let variables_input = serde_json::json!({
            "height": 28.0, "border": {"radius": 4.0}, "background": "#ffffff",
            "padding": {"top": 6.0, "right": 10.0, "bottom": 6.0, "left": 10.0},
            "widths": {"text": 140.0, "number": 100.0, "range": 120.0, "slider_value_min": 30.0, "checkbox": 16.0, "daterange": 160.0},
            "range": {"default_min": 0.0, "default_max": 100.0, "default_step": 1.0}
        });
        let font = serde_json::json!({"size": 11.0, "family": "Inter", "color": "#000000", "weight": "400", "case": "none"});
        let style_variables = serde_json::json!({
            "position": "top", "title_inline_band_bottom_pad": 8.0, "gap": 10.0,
            "title_inline_title_max_width": 0.0,
            "font": font, "label": {"font": font}, "value": {"font": font},
            "placeholder": {"font": {"size": 11.0, "family": "Inter", "color": "#999999", "weight": "400", "case": "none"}},
            "container_height": 32.0,
            "border": {"color": "#cccccc"},
            "control_gap": 8.0,
            "input": variables_input
        });
        serde_json::json!({
            "title": "", "text": "", "width": 800.0, "height": 400.0,
            "page_padding": 16.0, "card_padding": 8.0, "card_gap": 16.0,
            "variables": {},
            "layout": {"type": "cols", "items": layout_items, "content_width": 800.0, "gap": 16.0},
            "style": {
                "background": "#ffffff",
                "font": {"family": "Inter"},
                "footer": {"visible": false, "text": "made with dbt charts", "link": null,
                           "font": {"family": "Inter", "size": 11.0, "color": "#999999"}, "y_offset": 14.0,
                           "brand_weight": 600,
                           "divider": {"visible": false, "color": "#e5e5e5", "width": 0.5, "dasharray": null, "gap": 2.0}},
                "timestamp": {"visible": false, "position": "footer", "align": "left", "y": 14.0, "gap": 12.0,
                              "font": {"family": "Inter", "size": 11.0, "color": "#999999"}},
                "title": {"min_height": 0.0, "font": {"case": null, "color": null}},
                "text": {"column": {"max_number": null, "gap": null, "max_chars": null,
                                    "divider": {"visible": false, "color": "#e0e0e0", "width": 1.0, "dasharray": null}}},
                "variables": style_variables,
                "frame": {"card_padding": 8.0},
                "gap": null, "padding": null, "margin": null,
                "border": {"width": 0.0, "color": "transparent", "radius": 0.0},
                "box_shadow": null,
                "layout": {
                    "tabs": {
                        "bar_height": 40.0,
                        "border": {"width": 0.0, "color": "transparent", "radius": 4.0},
                        "font": {"size": 14.0},
                        "active_weight": "600", "inactive_weight": "400",
                        "title_baseline_offset": 5.0
                    },
                    "details": {
                        "summary_height": 36.0,
                        "border": {"width": 0.0, "color": "transparent", "radius": 4.0},
                        "font": {"size": 14.0},
                        "arrow": {"x": 14.0, "font": {"size": 13.0}},
                        "label_x": 32.0, "text_baseline_offset": 5.0, "content_y_offset": 4.0
                    }
                },
                "chart_defaults": {
                    "table": {
                        "header": {"background": null},
                        "row": {"stripe": {"color": "#F7F8FA"}}
                    },
                    "border": {"width": 0.0, "color": "transparent", "radius": 0.0},
                    "ink_canvas": "#ffffff"
                }
            }
        })
    }

    /// A KPI needs no query data to size — its height is a static theme
    /// constant (`chart_defaults.kpi.default_height`), the simplest chart
    /// shape for a hand-built fixture.
    fn minimal_kpi_chart(id: &str) -> serde_json::Value {
        serde_json::json!({
            "id": id, "chart_type": "kpi", "query_name": "q",
            "layout_padding": {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
        })
    }

    fn payload(resolved: serde_json::Value) -> BoardPayload {
        let envelope = serde_json::json!({
            "resolved": resolved, "chart_datasets": {}, "background": null, "fonts_dir": null,
            "render_timestamp_text": "", "render_time_iso": "2026-01-01T00:00:00Z",
            "defs_styles_block": "", "hover_attributes": "",
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"muted": null, "tones": null, "title_case": null, "continuous_height": null, "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()},
            "chart_defaults": {
                "default_chart_height": 300.0, "default_table_height": 250.0,
                "min_height": 200.0, "max_height": 600.0,
                "aspect_ratio": 1.5, "padding": {"top": 0.0, "bottom": 0.0},
                "kpi": {"default_height": 80.0},
                "spark_bar": {"max_bars": 10, "bar": {"height": 18.0, "padding": 2.0}},
                "bar": {"aspect_ratio": 1.39}, "line": {"aspect_ratio": null},
                "area": {"aspect_ratio": null}, "scatter": {"aspect_ratio": null},
                "heatmap": {"aspect_ratio": null}, "pie": {"aspect_ratio": 1.0},
                "histogram": {"aspect_ratio": null},
                "geoshape": {"aspect_ratio": 1.2}, "point_map": {"aspect_ratio": 1.2}
            },
            "frame_min_height": 0.0
        });
        serde_json::from_value(envelope).expect("board payload deserializes")
    }

    /// A root `cols:` row — a direct KPI beside a nested board holding its
    /// own single KPI — with `size_board_items_json`'s `"nested"` key
    /// reporting the nested item's OWN box, recursively (HIGH 1's box
    /// oracle recursion, `board_sweep.py::_oracle_layout_boxes`'s Rust
    /// counterpart).
    #[test]
    fn nested_board_item_reports_its_own_inner_box_under_a_nested_key() {
        let nested_board = minimal_board_json(serde_json::json!([
            {"type": "chart", "chart": minimal_kpi_chart("nested_kpi")}
        ]));
        let resolved = minimal_board_json(serde_json::json!([
            {"type": "chart", "chart": minimal_kpi_chart("direct_kpi")},
            {"type": "board", "board": nested_board}
        ]));
        let p = payload(resolved);
        let fonts = FontBook::empty();
        let (boxes, _container, overrides, _fresh_render) =
            size_board(&p, &fonts).expect("board sizes");
        let items = size_board_items_json(&p, &fonts, &boxes, &overrides).expect("sidecar builds");

        assert!(
            items[0].get("nested").is_none(),
            "a direct chart item has no nested key"
        );
        let nested = items[1]["nested"]
            .as_array()
            .expect("board item carries nested boxes");
        assert_eq!(nested.len(), 1);
        // 80.0 (chart_defaults.kpi.default_height) + 2 * 8.0 card_padding.
        assert_eq!(
            nested[0]["height"],
            serde_json::json!(96.0),
            "kpi's padded static height"
        );
    }

    fn real_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("real fonts load")
    }

    /// `charts/extensibility_1`'s own `slope` chart, verbatim (the
    /// `resolved`/`rows` pair `board_sweep.py` captured, minus the
    /// duplicate `resolved.query` block) — a `Shape::RailConcat` chart
    /// whose wide (probe) and narrow (final) title/subtitle wraps
    /// disagree by one line at its own natural 376px width (`docs/laws-
    /// board.md`'s "extensibility_1 row-height gap" section).
    const SLOPE_RESOLVED_JSON: &str =
        include_str!("../../fixtures/rail_concat/slope_resolved.json");
    const SLOPE_ROWS_JSON: &str = include_str!("../../fixtures/rail_concat/slope_rows.json");

    /// A rail-concat line chart derived from `slope`'s own resolved shape
    /// (color and endpoint labels kept), with its subtitle blanked (no
    /// wrap to disagree) and
    /// `min_height: 500.0` so its own natural comfortably exceeds
    /// `slope`'s ~295.667 — the cols row's tallest chart, forcing
    /// `slope` to realign UP past its own natural. Its rows are
    /// `SLOPE_ROWS_JSON`, unchanged.
    fn tall_sibling_resolved() -> serde_json::Value {
        let mut chart: serde_json::Value = serde_json::from_str(SLOPE_RESOLVED_JSON).unwrap();
        chart["id"] = serde_json::json!("tall_sibling");
        chart["subtitle"] = serde_json::json!("");
        chart["min_height"] = serde_json::json!(500.0);
        chart
    }


    fn chart_dataset(
        chart_id: &str,
        query_name: &str,
        rows_json: &str,
    ) -> (String, HashMap<String, Vec<serde_json::Map<String, serde_json::Value>>>) {
        let rows: Vec<serde_json::Map<String, serde_json::Value>> =
            serde_json::from_str(rows_json).unwrap();
        let mut inner = HashMap::new();
        inner.insert(query_name.to_string(), rows);
        (chart_id.to_string(), inner)
    }

    /// Extracts the `data-chart-height="..."` value from the wrapper
    /// carrying `data-chart-id="<chart_id>"` — the attribute order
    /// `wrap_chart_svg`/its callout sibling always emit (id, title,
    /// width, height).
    fn chart_height_attr(svg: &str, chart_id: &str) -> f64 {
        let marker = format!("data-chart-id=\"{chart_id}\"");
        let start = svg.find(&marker).unwrap_or_else(|| panic!("no {marker} in {svg}"));
        let window = &svg[start..(start + 400).min(svg.len())];
        let h_marker = "data-chart-height=\"";
        let hi = window.find(h_marker).unwrap() + h_marker.len();
        let rest = &window[hi..];
        let end = rest.find('"').unwrap();
        rest[..end].parse::<f64>().unwrap()
    }

    /// `slope` and `tall_sibling_resolved` as a parent cols row: `slope` inside
    /// a nested board whose layout type is `kind`, 376px wide, beside the tall
    /// sibling at 408px.
    fn slope_in_nested_board_beside_tall_sibling(
        slope: serde_json::Value,
        kind: &str,
    ) -> BoardPayload {
        let mut item = serde_json::json!({"type": "chart", "chart": slope});
        if kind == "grid" {
            item["col"] = serde_json::json!(0);
            item["row"] = serde_json::json!(0);
        }
        let mut nested_board = minimal_board_json(serde_json::json!([item]));
        nested_board["layout"]["type"] = serde_json::json!(kind);
        if kind == "grid" {
            nested_board["layout"]["columns"] = serde_json::json!(1);
        }
        let items = serde_json::json!([
            {"type": "board", "board": nested_board, "user_width": "376"},
            {
                "type": "chart",
                "chart": tall_sibling_resolved(),
                "user_width": "408",
            },
        ]);
        let mut resolved = minimal_board_json(items);
        resolved["card_padding"] = serde_json::json!(16.0);
        resolved["style"]["frame"]["card_padding"] = serde_json::json!(16.0);
        let mut p = payload(resolved);
        p.chart_datasets = HashMap::from([
            chart_dataset("slope", "pairs", SLOPE_ROWS_JSON),
            chart_dataset("tall_sibling", "pairs", SLOPE_ROWS_JSON),
        ]);
        p
    }

    /// A rail-concat chart alone in a nested board's own `cols` row keeps its
    /// own natural height beside a taller sibling: the nested row's alignment
    /// (`layout_sizing.py:1705-1706`) rewrites what the parent row's
    /// `_align_board_charts` wrote, so nothing is forced and nothing is
    /// re-rendered (`ladder/nested/12-cols-rail-line`).
    #[test]
    fn a_rail_chart_in_a_nested_boards_own_cols_row_keeps_its_own_natural() {
        let fonts = real_fonts();
        let slope: serde_json::Value = serde_json::from_str(SLOPE_RESOLVED_JSON).unwrap();
        let p = slope_in_nested_board_beside_tall_sibling(slope, "cols");
        let (svg, _painted) = render_board(&p, &fonts).expect("board renders");

        let slope_h = chart_height_attr(&svg, "slope");
        assert!(
            (slope_h - 295.666_666).abs() < 1e-3,
            "slope keeps its own natural, not the row's aligned height: {slope_h}: {svg}"
        );
    }

    /// A rail-concat chart alone in a nested `rows` board is forced to the
    /// parent row's height and then re-rendered there, so the probe-wrap
    /// disagreement regrows past the aligned height
    /// (`ladder/nested/14-rows-rail-line`).
    #[test]
    fn a_rail_chart_realigned_inside_a_nested_rows_board_regrows_the_disagreement() {
        let fonts = real_fonts();
        let slope: serde_json::Value = serde_json::from_str(SLOPE_RESOLVED_JSON).unwrap();
        let p = slope_in_nested_board_beside_tall_sibling(slope, "rows");
        let (svg, _painted) = render_board(&p, &fonts).expect("board renders");

        let slope_h = chart_height_attr(&svg, "slope");
        let sibling_h = chart_height_attr(&svg, "tall_sibling");
        assert!(
            slope_h - sibling_h >= 12.0,
            "slope grows past the aligned height by the probe-wrap residual: \
             slope={slope_h} sibling={sibling_h}: {svg}"
        );
    }

    /// A nested `grid` board holding a rail-concat chart the parent
    /// row realigns refuses by name: the fresh render is only pinned for a
    /// nested `rows` board. The gate must recognize a RailConcat chart with a
    /// VISIBLE legend (`families::line::shape` picks `Shape::RailConcat` from
    /// `show_rail && !y_on_right && !has_support_table`, whatever the legend says).
    #[test]
    fn a_rail_chart_realigned_inside_a_nested_grid_board_refuses() {
        let fonts = real_fonts();
        let mut slope: serde_json::Value = serde_json::from_str(SLOPE_RESOLVED_JSON).unwrap();
        slope["legend"]["visible"] = serde_json::json!(true);
        slope["legend"]["position"] =
            serde_json::json!({"edge": "bottom", "align": "start", "overlay": false});
        let p = slope_in_nested_board_beside_tall_sibling(slope, "grid");

        let err = render_board(&p, &fonts).expect_err("refuses rather than paint unrefreshed");
        assert!(
            err.contains("rail-concat chart realigned by a parent cols row inside a nested grid"),
            "unexpected error: {err}"
        );
    }

    /// The gate's `y_on_right` check reads the real field path
    /// (`style.axis_y.position`): a chart authored with a right y axis takes
    /// `Shape::Legacy`, never `Shape::RailConcat`, so a nested grid board does
    /// not refuse it.
    #[test]
    fn a_right_axis_chart_is_not_refused_inside_a_nested_grid_board() {
        let fonts = real_fonts();
        let mut slope: serde_json::Value = serde_json::from_str(SLOPE_RESOLVED_JSON).unwrap();
        slope["style"]["axis_y"]["position"] = serde_json::json!("right");
        let p = slope_in_nested_board_beside_tall_sibling(slope, "grid");

        render_board(&p, &fonts)
            .expect("a right-axis chart is Shape::Fit, not RailConcat — no refusal");
    }

    /// `layout_sizing.py::_align_grid_rows` (called from `_align_all_
    /// cols_in_tree`'s "grid" arm) delegates to `_align_cols_heights` for
    /// any `(row, height)` group sharing more than one item, the SAME
    /// fresh-render law the cols-row test above pins — `calculate_grid_
    /// boxes`'s own `row_groups` loop marks a direct chart in such a
    /// group the same way `calculate_cols_boxes` does. Two grid items
    /// sharing `row: 0`, `col: 0`/`1`: `slope` (disagreeing wraps) beside
    /// a taller sibling — the exact cols-row shape above, reproduced on
    /// a `grid` layout instead.
    #[test]
    fn a_rail_chart_realigned_in_a_grid_row_group_also_regrows_the_disagreement() {
        let fonts = real_fonts();
        let mut items = serde_json::json!([
            {
                "type": "chart",
                "chart": serde_json::from_str::<serde_json::Value>(SLOPE_RESOLVED_JSON).unwrap(),
                "col": 0, "row": 0,
            },
            {
                "type": "chart",
                "chart": tall_sibling_resolved(),
                "col": 1, "row": 0,
            },
        ]);
        let mut resolved = minimal_board_json(items.take());
        resolved["layout"]["type"] = serde_json::json!("grid");
        resolved["layout"]["columns"] = serde_json::json!(2);
        // `content_width: 784` with the board's own 16px `gap` + 16px
        // `card_gap` (`effective_gap = 32`) lands `col_width` at exactly
        // 376 — the same pane width `slope`'s own wrap disagreement is
        // exact at (see the cols-row test above).
        resolved["layout"]["content_width"] = serde_json::json!(784.0);
        resolved["card_padding"] = serde_json::json!(16.0);
        resolved["style"]["frame"]["card_padding"] = serde_json::json!(16.0);
        let mut p = payload(resolved);
        p.chart_datasets = HashMap::from([
            chart_dataset("slope", "pairs", SLOPE_ROWS_JSON),
            chart_dataset("tall_sibling", "pairs", SLOPE_ROWS_JSON),
        ]);
        let (svg, _painted) = render_board(&p, &fonts).expect("board renders");

        let slope_h = chart_height_attr(&svg, "slope");
        let sibling_h = chart_height_attr(&svg, "tall_sibling");
        assert!(
            slope_h - sibling_h >= 12.0,
            "slope grows past the grid row's own group height by the \
             probe-wrap residual: slope={slope_h} sibling={sibling_h}: {svg}"
        );
    }

    /// The id-collision shape. `overrides` (nested-board sizing
    /// overrides, `board_alignment_overrides`) and `fresh_render` (the
    /// cols-row fresh-render set) are SEPARATE structures precisely so a
    /// chart id shared between a DIRECT cols-row item and a chart nested
    /// inside a SIBLING board item never cross-contaminates: the nested
    /// board's own internal alignment write for `collide` must not flip
    /// the DIRECT `collide` item's `measuring` to `true` when its own
    /// row alignment never needed it. Root cols row: a direct chart
    /// `collide` (tall enough to BE the row's own chart_target, so its
    /// own alignment is a no-op) beside a nested board whose own sole
    /// chart is ALSO named `collide` — production's own real shape
    /// (`docs/laws-line-area.md`'s own citation: "the corpus places one
    /// chart in several slots"). A single shared `HashMap<String, f64>`
    /// for both concepts would report `collide` as needing a fresh
    /// render purely because the nested board wrote an (unrelated)
    /// override for it.
    #[test]
    fn a_direct_chart_sharing_an_id_with_a_nested_boards_own_chart_does_not_collide() {
        let fonts = real_fonts();
        let mut collide_outer = tall_sibling_resolved();
        collide_outer["id"] = serde_json::json!("collide");
        let mut collide_inner = collide_outer.clone();
        collide_inner["min_height"] = serde_json::Value::Null;

        let mut nested_board = minimal_board_json(serde_json::json!([
            {"type": "chart", "chart": collide_inner},
        ]));
        nested_board["layout"]["type"] = serde_json::json!("rows");
        let items = serde_json::json!([
            {"type": "chart", "chart": collide_outer, "user_width": "392"},
            {"type": "board", "board": nested_board, "user_width": "392"},
        ]);
        let resolved = minimal_board_json(items);
        let mut p = payload(resolved);
        p.chart_datasets =
            HashMap::from([chart_dataset("collide", "pairs", SLOPE_ROWS_JSON)]);

        let (_boxes, _container, overrides, fresh_render) =
            size_board(&p, &fonts).expect("board sizes");
        assert!(
            overrides.contains_key("collide"),
            "the nested board's own alignment writes an override for its \
             own `collide` chart: {overrides:?}"
        );
        assert!(
            !fresh_render.contains("collide"),
            "the DIRECT `collide` item is already its row's own chart_\
             target, so it needs no fresh render — a nested board's \
             unrelated override for the SAME id must not flip it: \
             fresh_render={fresh_render:?}"
        );
    }

    /// The second `Payload::measuring` site (`sizing::calculate_cols_
    /// boxes`'s own `fresh_render` set) is the thing that makes
    /// production's own `_align_cols_heights` fresh re-render reproduce
    /// the rail-concat probe-wrap law on a cols-row chart force-
    /// realigned away from its own natural. `slope` sits beside a much
    /// taller sibling here, so its own row height is NOT its own
    /// 295.667 natural — this pins that the composer paints it a WHOLE
    /// extra subtitle line (`fontSize(12) + 2` = 14.0) past the row
    /// height, not the row height verbatim. Mutation-pinned: hardcoding
    /// `render_chart_item`'s `measuring` to `false` drops the assertion.
    #[test]
    fn a_rail_chart_realigned_past_its_own_natural_in_a_cols_row_regrows_the_disagreement() {
        let fonts = real_fonts();
        // `slope`'s own wrap disagreement is exact at `payload.width ==
        // 376` AND the ORIGINAL corpus board's own 16px card padding
        // (`docs/laws-board.md`'s "extensibility_1 row-height gap") —
        // `title_block_probe` wraps at the RAW `svg_w` (`payload.width`,
        // padding-independent), but the real `title_block` wraps at
        // `target_width - pane1_width - spacing - pad_l - pad_r`, so a
        // smaller card padding widens the narrow basis enough to fit the
        // SAME one line the probe does, erasing the disagreement this
        // test exists to pin. `minimal_board_json`'s own default (8) is
        // overridden here to match.
        let items = serde_json::json!([
            {
                "type": "chart",
                "chart": serde_json::from_str::<serde_json::Value>(SLOPE_RESOLVED_JSON).unwrap(),
                "user_width": "376",
            },
            {
                "type": "chart",
                "chart": tall_sibling_resolved(),
                "user_width": "408",
            },
        ]);
        let mut resolved = minimal_board_json(items);
        resolved["card_padding"] = serde_json::json!(16.0);
        resolved["style"]["frame"]["card_padding"] = serde_json::json!(16.0);
        let mut p = payload(resolved);
        p.chart_datasets = HashMap::from([
            chart_dataset("slope", "pairs", SLOPE_ROWS_JSON),
            chart_dataset("tall_sibling", "pairs", SLOPE_ROWS_JSON),
        ]);
        let (svg, _painted) = render_board(&p, &fonts).expect("board renders");

        let slope_h = chart_height_attr(&svg, "slope");
        let sibling_h = chart_height_attr(&svg, "tall_sibling");
        // The row aligns both charts to the SAME target height first
        // (`calculate_cols_boxes`'s own `item_heights`) — `tall_sibling`
        // paints exactly there (`measuring: false`, no residual of its
        // own). `slope` is realigned past its own 295.667 natural, so it
        // gets a fresh, `measuring: true` render at that SAME target —
        // exactly like production's own `_align_cols_heights` — and its
        // probe/final title wraps still disagree at this pane width, so
        // it grows past the row target by the residual
        // (`fontSize(12) + 2` = 14.0), landing ABOVE `sibling_h`, not
        // level with it.
        assert!(
            slope_h - sibling_h >= 12.0,
            "slope grows past the row height by the probe-wrap residual \
             (fontSize(12) + 2 = 14.0, modulo ceil rounding): \
             slope={slope_h} sibling={sibling_h}: {svg}"
        );
    }
}
