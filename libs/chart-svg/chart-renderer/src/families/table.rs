//! Table family: ResolvedTableChart payload → SVG.
//! Renders title, header row, N data rows with alternating stripes,
//! text columns left-aligned, numeric columns center-aligned on a common midpoint.
//!
//! Conditional formatting (chart-level `conditional_formatting` when-rules,
//! and a column's `scale.background`) is ported — see the `WhenRule`/
//! `resolve_conditional_styles`/`interpolate_scale_color` cluster below.
//! A column's per-cell `link:` (verbatim column name or a `{{ col }}` /
//! `{{ col | urlencode }}` template) is ported — see `resolve_cell_link`.
//! chart-root `link` (`links.auto` row detail) paints one `<a href aria-label>`
//! around a transparent `dbt-table-row-link` rect per value row, before that
//! row's cells (summary/total rows get none); the href is `resolve_cell_link`'s.
//! In a linked table the body row rules, and in a banded row every cell
//! background, spark group and link-less cell text, carry `dbt-pointer-inert` /
//! `dbt-table-cell-inert` so a click reaches the band. Refuses: a pivot table.
//! Every family but bar, pie and table refuses a chart-root `link` by name
//! (`lib.rs::render`).
//! Returns Err for: an unknown link template filter, a column's
//! a `tone` rule with no board `tones`,
//! an `in` rule whose candidate list holds a non-scalar, a rule with no
//! active predicate (unreachable on a validated payload), spark types
//! `area`/`column`, spark `value_visible`/`negative_color`, a spark `columns`
//! cell whose value isn't an array, a `columns` array containing a
//! non-numeric element, a `bar` spark column with any negative detail-row
//! value (midline layout not ported), a header
//! that wraps under a content-sized band, a column pairing `scale` with a
//! static `background`/`font.color`, a timestamp in a date lane under an
//! authored `format:`, and a strftime directive that depends on the host locale or time zone.

use crate::BoardStyle;
use crate::Padding;
use crate::Payload;
use crate::TitleShift;
use crate::TonesStyle;
use crate::codes;
use crate::common::CellRow;
use crate::common::Divider;
use crate::common::Font;
use crate::common::FormatSlot;
use crate::common::ScaleTarget;
use crate::diagnostic::Diagnostic;
use crate::diagnostic::warn;
use crate::layout::Rect;
use measure::FontBook;
use measure::Measure;
use print::print;
use serde::Deserialize;
use serde_json::Value;
use serde_json::json;
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::collections::BTreeSet;

pub mod bind;
pub mod layout;
mod text_format;
pub mod width;

#[cfg(test)]
mod coded_error_tests;

// House null sentinel for a numeric/date cell with no value — matches
// format_d3.py's NULL_DISPLAY (an em-dash, never a blank cell).
pub(crate) use d3rs::native::NULL_DISPLAY;

/// Port of `_svg_font_family`'s quote-stripping half only:
/// SVG's `font-family` presentation attribute treats a single/double quote
/// as a LITERAL character, not a CSS string delimiter, so a multi-word name
/// baked quoted for CSS use (`'Inter Variable'`) fails to match any real
/// font once dropped into this attribute verbatim and silently falls back —
/// every glyph in the table then renders in a different font, which is
/// pixel-visible everywhere text paints. A Source Serif family also gains the
/// `Source Serif 4 Web` alias so browsers prefer the bundled woff2, and both
/// names stay quoted: a name with a digit token is not a CSS identifier, so
/// unquoted it falls back to Times in a rasterized render.
fn svg_font_family(family: &str) -> String {
    let normalized = family.replace(['\'', '"'], "");
    if normalized.contains("Source Serif 4") && !normalized.contains("Source Serif 4 Web") {
        return normalized.replace("Source Serif 4", "'Source Serif 4 Web', 'Source Serif 4'");
    }
    normalized
}

// ── Deserialization types (only fields we consume) ────────────────────────────

fn columns_or_none<'de, D>(d: D) -> Result<serde_json::Map<String, Value>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    Ok(Option::deserialize(d)?.unwrap_or_default())
}

#[derive(Deserialize)]
struct Resolved {
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    id: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TableTitleStyle>,
    // Chart-root `ResolvedChart.link` (`_base.py:85`) — `links.auto`/FK-drill's
    // own row-detail template (`automatic_link.py::_table_row_detail_link`,
    // `row_detail=True`), keyed by column name (never a channel placeholder —
    // that form is the non-table chart-mark path, ported separately). Renders
    // as one whole-row band per value row, not a
    // per-cell anchor — `ColConfig.link` above is the unrelated per-column
    // form. `de_nonempty_string` matches production's own `if chart_root_link:`
    // truthiness gate — an authored empty string is no link.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    link_open: crate::href::LinkOpen,
    /// `None` when resolve had nothing to materialize (`_materialize_table_columns`):
    /// the same as no column config, so the query's own columns.
    #[serde(deserialize_with = "columns_or_none")]
    columns: serde_json::Map<String, Value>,
    style: ResolvedStyle,
    #[serde(default)]
    rows: Option<Vec<String>>,
    #[serde(default)]
    pivot_columns: Option<Vec<String>>,
    #[serde(default)]
    values: Option<Vec<String>>,
    // Chart-level `conditional_formatting: {col: {when: [rule, ...]}}` —
    // Keyed by the AUTHORED column
    // name; a pivot leaf looks itself up the same way `ColMeta` looks up
    // its own `ColConfig` (`config_key`, the leaf's measure/dimension
    // suffix) — production's own measure-keyed pivot remap
    // is a narrower special case of that same lookup,
    // not ported separately since no corpus row combines pivot + CF.
    #[serde(default)]
    conditional_formatting: Option<BTreeMap<String, FieldConditionalFormatting>>,
    #[serde(default)]
    column_defaults: Option<ColumnDefaults>,
    // Chart-level header overflow, promoted from the authored patch
    // (`ResolvedChart.header_overflow`).
    #[serde(default)]
    header_overflow: Option<String>,
    #[serde(default)]
    spanners: Option<Vec<Spanner>>,
}

#[derive(Deserialize)]
struct Spanner {
    label: String,
    columns: Vec<String>,
}

/// Table-level defaults applied to a pivot leaf that has no `style.columns`
/// entry of its own (no leaf-keyed entry, and — single-measure pivots only —
/// no measure-keyed fallback either). Mirrors `TableColumnDefaultsConfig`.
#[derive(Deserialize, Default)]
struct ColumnDefaults {
    #[serde(default, deserialize_with = "column_format")]
    format: Option<FormatSlot>,
    #[serde(default)]
    width: Option<WidthHint>,
    #[serde(default)]
    align: Option<String>,
    #[serde(default)]
    background: Option<String>,
    #[serde(default)]
    font: Option<ColumnFont>,
}

// Table-local title style: `common::TitleStyle` doesn't carry `subtitle` (no
// other family renders one), so table.rs deserializes its own superset
// rather than growing the shared type for one consumer.
#[derive(Deserialize)]
struct TableTitleStyle {
    font: Font,
    #[serde(default)]
    overflow: Option<String>,
    #[serde(default)]
    subtitle: Option<TableSubtitleStyle>,
}

#[derive(Deserialize)]
struct TableSubtitleStyle {
    font: Font,
    #[serde(default)]
    overflow: Option<String>,
}

#[derive(Deserialize)]
struct ResolvedStyle {
    table: TableTheme,
    // The width-tiered title font baked at resolve time (`ResolvedTableStyle
    // .title_font` — "Baked title font (size/weight/family/…) from the width
    // tier"). Size/weight/family for the rendered title come from here, not
    // from `title_style.font` (the untiered base) — a narrow table renders
    // its title at a smaller tier, which also feeds the subtitle-band math
    // below. Title *color* still comes from `title_style.font.color`,
    // which the width tier does not touch.
    #[serde(default)]
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct TableTheme {
    // `TableChartStyle.background`: "None inherits
    // from theme" — omits the background rect entirely
    // when unset (or the literal string "transparent"), leaving whatever
    // sits behind the table (the card chrome) to show through. NOT
    // `payload.background` (the card/page canvas color, always present) —
    // painting that unconditionally would opaque over card chrome no
    // authored table ever asked to cover.
    #[serde(default)]
    background: Option<String>,
    font: Font,
    header: HeaderTheme,
    row: RowTheme,
    column_layout: ColLayout,
    outer_padding: f64,
    pagination: PaginationCfg,
    #[serde(default)]
    bottom_padding: f64,
    paginator: PaginatorTheme,
    // Floor for the title+subtitle band, but only when a subtitle is
    // present
    // ("the floor only applies when a subtitle is present; without one the
    // natural content height is correct and the floor creates a visible
    // dead gap"). Defaults to 0.0 so tables without a title_row block in
    // their theme (every pre-existing test fixture) are unaffected.
    #[serde(default)]
    title_row: TitleRowTheme,
    // Whitespace between the title's descent and the subtitle's ascent —
    // The gap between title and subtitle (theme: 3.3). Defaults to 0.0;
    // only consulted when a subtitle is present.
    #[serde(default)]
    title_subtitle_gap: f64,
    /// `table.color`: a static text color replaces the font's.
    #[serde(default)]
    color: Option<TableColor>,
    /// `table.row_numbers`: a leading index column.
    #[serde(default)]
    row_numbers: RowNumbersTheme,
    /// `TableChartStyle.transpose`: rows become columns.
    /// Only the natural-width measure reads it, to refuse.
    #[serde(default)]
    transpose: bool,
    /// `TableChartStyle.header_overflow`, the board-level override of
    /// `header.overflow`.
    #[serde(default)]
    header_overflow: Option<String>,
    text_baseline_offset: f64,
    // "all" or "anchors" — `symbol_mode`, a required field with
    // no default. "anchors" limits the
    // currency prefix / unit suffix on numeric cells to the first row and
    // any summary/total row; every other row shows the bare number.
    symbol_mode: String,
    // `style.wrap` (theme default `true`);
    // false clips long text cells to a single line instead of wrapping.
    #[serde(default = "default_wrap")]
    wrap: bool,
    // The zero-row text (`table.empty_state`); read only by the empty render.
    #[serde(default)]
    empty_state: Option<EmptyState>,
    /// `table.more_rows`: the "+N" note of a truncated table.
    #[serde(default)]
    more_rows: Option<EmptyState>,
    // `style.charts.table.spark` — theme tokens for in-cell spark marks.
    // No `#[serde(default)]`: every shipped theme carries the full block (added
    // alongside every other structural default in `_base.yaml`), so an
    // absent block is a stale fixture, not a legitimate "spark unused"
    // state — deserialization fails loudly rather than silently drawing
    // with a made-up default.
    spark: SparkTheme,
}

/// `style.charts.table.spark` — theme tokens the spark marks read
/// (`resolved_style.spark` (bar/columns/line geometry; the `area` and `column`
/// marks are not ported, so their theme sub-objects are not deserialized here).
#[derive(Deserialize)]
struct SparkTheme {
    // `columns`/`line`/`area` fall back to this when a column omits
    // `spark.color`.
    color: String,
    // Insets around a `line` mark's plot (`SparkStyle.padding`).
    padding: SparkPaddingTheme,
    // The dashed placeholder a zero-value `line`/`columns` cell draws.
    empty: SparkEmptyTheme,
    // The dot-on-a-rule a one-value `line` cell draws.
    single_value: SparkSingleValueTheme,
    columns: SparkColumnsTheme,
    column: SparkColumnTheme,
    area: SparkAreaTheme,
    bar: SparkBarTheme,
}

#[derive(Deserialize)]
struct SparkPaddingTheme {
    top: f64,
    right: f64,
    bottom: f64,
    left: f64,
}

#[derive(Deserialize)]
struct SparkEmptyTheme {
    inset_x: f64,
    stroke: SparkEmptyStroke,
}

#[derive(Deserialize)]
struct SparkEmptyStroke {
    color: String,
    width: f64,
    dasharray: String,
}

#[derive(Deserialize)]
struct SparkSingleValueTheme {
    inset_x: f64,
    marker_radius: f64,
}

#[derive(Deserialize)]
struct SparkColumnsTheme {
    gap: f64,
    padding: f64,
    min_bar_height: f64,
    border: SparkRadiusTheme,
}

#[derive(Deserialize)]
struct SparkBarTheme {
    background: String,
    color: String,
    default_max: f64,
    border: SparkRadiusTheme,
    label: SparkBarLabelTheme,
}

/// The value label a `bar` draws when `value_visible` (`spark.bar.label`).
#[derive(Deserialize)]
struct SparkBarLabelTheme {
    inset_x: f64,
    fill: String,
    fill_opacity: f64,
}

/// The narrow vertical mark's own size (`spark.column`).
#[derive(Deserialize)]
struct SparkColumnTheme {
    width: f64,
}

#[derive(Deserialize)]
struct SparkAreaTheme {
    fill_opacity: f64,
}

/// Only the `radius` sub-field of the theme's `CornerStyle`-shaped border
/// object: `render_spark_bar`/`render_spark_columns` read nothing else off
/// it (no stroke width/color — a spark mark's border is fill-only).
#[derive(Deserialize)]
struct SparkRadiusTheme {
    radius: f64,
}

/// Typed `style.columns.<name>.spark`, narrowed to the four supported marks (`line`, `bar`,
/// `bar-normalize`, `columns`); `value_visible` and `negative_color` are read
/// only to gate the refusal for them in `render()`, never consumed by the
/// renderers below. A field a mark does not read is ignored, as production
/// ignores it (`render_spark` hands each mark only its own options).
#[derive(Deserialize, Clone)]
struct SparkConfig {
    #[serde(rename = "type")]
    spark_type: String,
    color: Option<String>,
    height: Option<f64>,
    width: Option<f64>,
    max: Option<f64>,
    thresholds: Option<Value>,
    background: Option<String>,
    border_radius: Option<f64>,
    value_visible: Option<bool>,
    value_suffix: Option<String>,
    fill_opacity: Option<f64>,
    negative_color: Option<bool>,
    last_visible: Option<bool>,
    min_max_visible: Option<bool>,
}

fn default_width_similarity_threshold() -> f64 {
    0.8
}

fn default_header_visible() -> bool {
    true
}

fn default_wrap() -> bool {
    true
}

#[derive(Deserialize, Default)]
struct TitleRowTheme {
    #[serde(default)]
    height: f64,
}

#[derive(Deserialize)]
struct HeaderTheme {
    /// `table.header.visible`: a hidden header collapses its band and the
    /// header/body gap.
    #[serde(default = "default_header_visible")]
    visible: bool,
    height: f64,
    font: Font,
    /// `table.header.overflow`: the theme's overflow mode for a label that
    /// does not fit its column.
    #[serde(default)]
    overflow: Option<String>,
    /// The line between the header and the body — `TableChartStyle.header.divider`.
    divider: TableDivider,
    /// `table.header.font_compact`: its `weight` replaces the header's when
    /// the body starts at 11px or less.
    #[serde(default)]
    font_compact: Option<Font>,
    /// `table.header.background`: the fill behind the header and pivot group
    /// rows. The zero-row render refuses the band it would paint.
    #[serde(default)]
    background: Option<String>,
}

#[derive(Deserialize)]
struct TableColor {
    #[serde(rename = "static")]
    static_color: Option<String>,
}

/// `colors.sanitize_color`: a hex color, `transparent` or `none` (as
/// `transparent`); anything else is an authored configuration error.
fn sanitize_color(color: Option<&str>) -> Result<Option<&str>, Diagnostic> {
    let Some(color) = color.filter(|c| !c.is_empty()) else {
        return Ok(None);
    };
    let hex = color.strip_prefix('#').is_some_and(|h| {
        matches!(h.len(), 3 | 6) && h.bytes().all(|b| b.is_ascii_hexdigit())
    });
    if hex {
        Ok(Some(color))
    } else if color.eq_ignore_ascii_case("transparent") || color.eq_ignore_ascii_case("none") {
        Ok(Some("transparent"))
    } else {
        Err(Diagnostic::new(
            &codes::ERR_CSS_COLOR_INVALID_AT_RENDER,
            [
                ("value", json!(color)),
                ("detail", json!("expected a hex color, transparent or none")),
            ],
        ))
    }
}

#[derive(Deserialize)]
struct RowNumbersTheme {
    #[serde(default)]
    visible: bool,
    /// The leading column's header text.
    #[serde(default = "default_row_numbers_header")]
    header: String,
    /// `right` or `left`: where the index and its header sit in the column.
    #[serde(default = "default_row_numbers_align")]
    align: String,
    /// The index text's font; read only while `visible`, which refuses its
    /// color's absence by name.
    #[serde(default)]
    font: Option<Font>,
}

impl Default for RowNumbersTheme {
    fn default() -> Self {
        Self {
            visible: false,
            header: default_row_numbers_header(),
            align: default_row_numbers_align(),
            font: None,
        }
    }
}

fn default_row_numbers_header() -> String {
    "#".to_string()
}

fn default_row_numbers_align() -> String {
    "right".to_string()
}

/// `TableDividerStyle`: a divider that also chooses how far
/// the line runs — one unbroken line across the table (`continuous`) or one
/// under each column.
#[derive(Deserialize)]
struct TableDivider {
    #[serde(flatten)]
    line: Divider,
    continuous: bool,
}

#[derive(Deserialize)]
struct RowTheme {
    height: f64,
    // `TableRowStripeStyle | None` in production (`compile/models/style/
    // `style.row.stripe`: `None` means no
    // alternating row fill, same outcome as `Some(StripeTheme { color:
    // None })`. A board that never authors a stripe resolves the bare
    // `null`, not the object.
    stripe: Option<StripeTheme>,
    // Query column carrying the per-row role marker (e.g. "total"/"summary").
    // Never a display column, even when style.columns doesn't list it.
    role: Option<String>,
    /// The line under each body row — `TableChartStyle.row.divider`.
    divider: TableDivider,
    /// Per-role style (divider, background, font weight) for summary/total
    /// rows.
    roles: RowRolesTheme,
}

#[derive(Deserialize)]
struct StripeTheme {
    color: Option<String>,
}

#[derive(Deserialize)]
struct RowRolesTheme {
    summary: RowRoleTheme,
    total: RowRoleTheme,
}

/// `TableRowRoleStyle`: the divider above rows with this
/// role, plus its font weight and background. The summary and total dividers
/// are independent: each carries its own `visible`, color, width and dash.
#[derive(Deserialize)]
struct RowRoleTheme {
    divider: Divider,
    #[serde(default)]
    font: Option<Font>,
    #[serde(default)]
    background: Option<String>,
}

#[derive(Deserialize)]
struct ColLayout {
    cell_padding: f64,
    #[serde(default = "default_width_similarity_threshold")]
    width_similarity_threshold: f64,
    #[serde(default)]
    content_headroom: f64,
}

#[derive(Deserialize)]
struct PaginationCfg {
    enabled: bool,
    page_rows: Option<usize>,
}

// Grow-by-2 ( `_PAGINATION_GROW_CAP`): an overflow past page_rows
// within this cap draws every row on one page instead of paginating —
// always when unbounded, in a slot only when the rows fit.
const PAGINATION_GROW_CAP: usize = 2;
// Anti-dangle squeeze: in a slot, rather than
// strand a 1-2 row tail on its own page, squeeze the row height ≤15%
// (floored at 20px) and show every row on one page. Production only fires
// this when `row_heights is None` — i.e. wrap is off — so the wrap-on path
// never squeezes.
const ANTI_DANGLE_MAX_OVERFLOW: usize = 2;
const ANTI_DANGLE_MAX_SQUEEZE: f64 = 0.15;
const ANTI_DANGLE_MIN_ROW_H: f64 = 20.0;
// Caps the displayed page count in the control bar
// A static export never draws a "… 30 ›"
// tail past this, even when the row count implies more pages.
const STATIC_MULTI_PAGE_MAX_PAGES: usize = 20;
// MUI-style sibling/boundary window sizing
// `_paginator_window` defaults.
const PAGINATOR_SIBLING_COUNT: i64 = 1;
const PAGINATOR_BOUNDARY_COUNT: i64 = 1;
// The pager's prev/next and ellipsis slots, a share of the item width.
const PAGINATOR_AUX_SLOT_RATIO: f64 = 0.66;
const PAGINATOR_PREV_CHEVRON: &str = "\u{2039}";
const PAGINATOR_NEXT_CHEVRON: &str = "\u{203a}";
const PAGINATOR_ELLIPSIS: &str = "\u{2026}";

#[derive(Deserialize)]
struct PaginatorTheme {
    color_active: String,
    color_inactive: String,
    color_disabled: String,
    #[serde(default)]
    font: Font,
    weight_active: i64,
    weight_inactive: i64,
    weight_chevron: i64,
    item_width: f64,
}

#[derive(Deserialize, Default)]
struct ColConfig {
    label: Option<String>,
    #[serde(default)]
    decimal_pad_table: Vec<String>,
    shared_scale: Option<SharedScale>,
    // Per-cell link — `TableColumnConfig.link` (`str | None`): a bare
    // column name substitutes that column's own row value, a
    // `{{ col }}` / `{{ col | urlencode }}` template substitutes the
    // named column(s) — see `resolve_cell_link`, called per row at paint.
    link: Option<String>,
    /// How `link` opens (`TableColumnConfig.link_open`).
    link_open: crate::href::LinkOpen,
    /// `TableColumnConfig.header_link`: the anchor around the column's header.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    header_link: Option<String>,
    header_link_open: crate::href::LinkOpen,
    spark: Option<SparkConfig>,
    // Continuous background color mapping (`style.columns.<col>.scale`,
    // `ResolvedColumnScaleConfig`) — `background` and `color` targets.
    #[serde(default)]
    scale: Option<ColumnScale>,
    // Static column glyph/tint (`glyph`/`glyph_color`), the fallback `resolve_cell_glyph_from_overrides`
    // reads when no `when` rule matched (or none is authored for this
    // column).
    #[serde(default)]
    glyph: Option<String>,
    #[serde(default)]
    glyph_color: Option<String>,
    // Named engine format (e.g. "currency_whole"), an inline d3 spec, or the
    // `{spec, prefix, suffix, notation}` object form (`column_format`). With a
    // baked decimal_pad_table, a column that HAS a format paints at the
    // spec's fixed precision plus the pad; a column with none keeps every
    // row's own per-row SI spec (`format_numeric_auto`) and shares only the
    // pad (#8444's `_unscaled_decimal_pad_table`). Without a pad and without
    // shared_scale, the format alone decides (precision-0 specs, pivot leaf
    // columns — see `_as_resolved_table_column`'s docstring in table.py).
    #[serde(default, deserialize_with = "column_format")]
    format: Option<FormatSlot>,
    // Authored/resolved column width hint — table.py's `TableColumnConfig.width`
    // (int px, or a string that is either a bare px number or a "NN%" of the
    // available data-column budget). Resolved through `parse_column_width`.
    #[serde(default)]
    width: Option<WidthHint>,
    // `width`/`max_width` are one sizing slot on the authored surface: a column
    // claiming either is never filled from `column_defaults` on the other.
    // This renderer has no cap-sizing pass of its own; `max_width` is
    // deserialized only to gate the `column_defaults.width` fill below, not
    // consumed for layout (a genuine, narrower gap than production's — see
    // AGENTS.md).
    #[serde(default)]
    max_width: Option<WidthHint>,
    // `false` hides the column entirely (the `visible is not False` filter) — the sole mechanism that removes a
    // column from the rendered set. `None`/`true` are both visible.
    #[serde(default)]
    visible: Option<bool>,
    // `style.columns.<col>.swatch`: a color-chip column.
    #[serde(default)]
    swatch: Option<bool>,
    // Per-column header overflow mode (`TableColumnConfig.header_overflow`).
    #[serde(default)]
    header_overflow: Option<String>,
    // Resolved column alignment ("left"/"right"/"center"), baked once at
    // resolve time (`classify_date_column_align` / explicit author value).
    // Only "right" is consulted here, to gate the date lane (invariant #11).
    #[serde(default)]
    align: Option<String>,
    // Static cell fill — `TableColumnConfig.background`: a hex color,
    // `transparent`/`none`, or the name of a row field carrying one
    #[serde(default)]
    background: Option<String>,
    // Static cell ink — the `color` and `weight` of `TableColumnConfig.font`,
    // the only two fields the paint site reads.
    #[serde(default)]
    font: Option<ColumnFont>,
}

#[derive(Deserialize)]
struct ColumnFont {
    color: Option<String>,
    weight: Option<Value>,
}

/// `numeral_scale.py`'s `SuffixMode`. It fixes the register
/// (`SharedScale.register`: analytic for Anchor, narrative for Repeat) and
/// which rows paint the magnitude word: `format_kpi_parts` emits it iff
/// `value != 0 and (mode is REPEAT or is_anchor)` (`format_value.py:308`), and
/// `table.py:2657-2663` keeps a magnitude token on every row even under
/// `symbol_mode: anchors`.
#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
enum SuffixMode {
    Anchor,
    Repeat,
}

#[derive(Deserialize)]
struct SharedScale {
    exponent: i32,
    // The baked `ResolvedColumnSharedScale.mode`; an unknown value fails
    // deserialization by name.
    mode: SuffixMode,
    // Fixed-point d3 spec the scaled value renders through (e.g. ",.2~f").
    // Mirrors ResolvedColumnSharedScale.digit_spec — the precision authority
    // for this column, not decimal_pad_table (which is empty for most
    // shared-scale columns; see ColConfig::format's doc comment).
    digit_spec: String,
}

thread_local! {
    /// The board's `style.formats` alias table for the render in progress, like
    /// Python's board-scoped `formats`.
    static BOARD_FORMATS: RefCell<(serde_json::Map<String, Value>, Option<BTreeMap<String, String>>)> =
        RefCell::new(Default::default());
}

/// Scopes a board's format aliases over one table render or measure.
pub(crate) struct BoardFormats(serde_json::Map<String, Value>, Option<BTreeMap<String, String>>);

impl BoardFormats {
    pub(crate) fn enter(
        formats: Option<&serde_json::Map<String, Value>>,
        sub_unit_specs: Option<&BTreeMap<String, String>>,
    ) -> Self {
        let previous = BOARD_FORMATS.with(|slot| {
            slot.replace((formats.cloned().unwrap_or_default(), sub_unit_specs.cloned()))
        });
        BoardFormats(previous.0, previous.1)
    }
}

impl Drop for BoardFormats {
    fn drop(&mut self) {
        BOARD_FORMATS.with(|slot| *slot.borrow_mut() = (std::mem::take(&mut self.0), self.1.take()));
    }
}

/// `TableColumnConfig.format` (`FormatAlias | str | FormatConfig`) as the
/// resolved `ResolvedFormat` slot: a bare string or an object, read by the one
/// `FormatSlot` parser. The table paints `spec`, `prefix`, `suffix` and
/// `notation` (the register an SI letter is spelled in) in their own lanes
/// (`format_kpi_parts`); `raw` names the predefined format the sub-unit floor
/// and the house register key on; `repeat` overrides the table's `symbol_mode`
/// for this column ([`column_symbol_mode`]); `sign_placement` refuses, as
/// production's resolve does (`compile/resolve/chart/_table.py:614-616`).
fn column_format<'de, D>(d: D) -> Result<Option<FormatSlot>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let Some(mut raw) = Option::<serde_json::Value>::deserialize(d)? else {
        return Ok(None);
    };
    // An affix-only slot (`{prefix: "$"}`) formats as `number`: production
    // resolves it at render (`compile/format.py` `_merge_tiers`,
    // `_DEFAULT_NAME`), so the payload carries it as authored.
    if let Some(obj) = raw.as_object_mut() {
        if obj.get("spec").is_none_or(serde_json::Value::is_null) {
            obj.insert("spec".into(), "number".into());
        }
    }
    let slot = FormatSlot::deserialize(raw).map_err(serde::de::Error::custom)?;
    if slot.sign_placement.is_some() {
        return Err(serde::de::Error::custom(
            "table column format: sign_placement is not painted here — a column keeps its sign \
             with the digits (production: ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED)",
        ));
    }
    Ok(Some(slot))
}

// ── Conditional formatting: chart-level when-rules + per-column scale ─────────
// Conditional-style resolution: the last-match-wins predicate merge (with a
// trailing `default: true` fallback) and the scale layer (`compute_scale_domain`,
// `resolve_hinge`, `interpolate_scale_color`). One evaluation path feeds
// both cell-paint sites (the numeric lane and the text cell) and every
// measurement site that needs to know a cell's glyph (the numeric lane's
// prefix-width demand and the text-wrap content width).

/// `style.columns.<col>.scale` (`ColumnScaleConfig`) — `background` is
/// ported; a non-null `color` (a font-color scale) refuses at the
/// column-building pass, since no corpus row scales font color.
#[derive(Deserialize, Clone)]
struct ColumnScale {
    #[serde(default)]
    background: Option<ScaleTarget>,
    #[serde(default)]
    color: Option<ScaleTarget>,
}

/// `FieldConditionalFormatting` — a column's ordered `when:` rule list.
#[derive(Deserialize, Clone, Default)]
struct FieldConditionalFormatting {
    #[serde(default)]
    when: Vec<WhenRule>,
}

/// A single `when` rule — `ConditionalRule`
/// (`compile/models/chart/authored/_conditional_formatting.py`). Every
/// predicate/output field always serializes (Pydantic's `model_dump`), so
/// every field here is a plain `Option`, no `#[serde(default)]` needed —
/// but it's kept anyway so a hand-built test payload may omit a key.
/// Read directly off `conditional_formatting` (table) and `support.rules`
/// (kpi), with the full rule-output set (font/glyph/tone included); both
/// families share this struct and `resolve_conditional_styles`'s merge law.
#[derive(Deserialize, Clone, Default)]
pub(crate) struct WhenRule {
    #[serde(default)]
    eq: Option<Value>,
    #[serde(default)]
    ne: Option<Value>,
    #[serde(default)]
    lt: Option<f64>,
    #[serde(default)]
    lte: Option<f64>,
    #[serde(default)]
    gt: Option<f64>,
    #[serde(default)]
    gte: Option<f64>,
    #[serde(default)]
    between: Option<(f64, f64)>,
    #[serde(default, rename = "in")]
    in_: Option<Vec<Value>>,
    #[serde(default)]
    is_null: Option<bool>,
    #[serde(default)]
    default: Option<bool>,
    #[serde(default)]
    background: Option<String>,
    #[serde(default)]
    font: Option<WhenRuleFont>,
    #[serde(default)]
    glyph: Option<String>,
    #[serde(default)]
    glyph_color: Option<String>,
    #[serde(default)]
    tone: Option<String>,
}

/// `ConditionalRule.font` (`FontStyle`), narrowed to the four attributes
/// `_apply_rule_outputs` reads.
#[derive(Deserialize, Clone, Default)]
struct WhenRuleFont {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    weight: Option<Value>,
    #[serde(default)]
    style: Option<String>,
    #[serde(default)]
    decoration: Option<String>,
}

/// The style keys a matched (or default) rule contributes —
/// `_apply_rule_outputs`'s `result` dict, typed. `None` on any field means
/// "no rule set this key", same as the key being absent from production's
/// dict; a caller falls through to whatever layer sits beneath (scale,
/// then the column's own static glyph).
#[derive(Debug, Default)]
pub(crate) struct RuleOverrides {
    pub(crate) background: Option<String>,
    pub(crate) color: Option<String>,
    weight: Option<String>,
    style: Option<String>,
    decoration: Option<String>,
    pub(crate) glyph: Option<String>,
    pub(crate) glyph_color: Option<String>,
    // Set when a matched (or default) rule's `tone` needed board tones to
    // resolve `glyph_color` and none were passed. `apply_rule_outputs`
    // never errors on this itself — production's own `tones is None` is a
    // reachable, by-design skip, true at EVERY
    // call site, not just the two production names ("the cell background/
    // color path and resolve_cell_glyph"). Only `resolve_cell_style`, the
    // one caller that actually paints `glyph_color`, turns this into a
    // refusal — `resolve_cell_glyph` (glyph STRING only, tones always
    // `None` by design) ignores it entirely.
    pub(crate) tone_needs_board_tones: bool,
}

/// board.py:36-38's `VALID_FONT_WEIGHTS` — a conditional rule's `font.weight`
/// must be gated through this set before it reaches the SVG.
pub(crate) const VALID_FONT_WEIGHTS: &[&str] = &[
    "normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900",
];

#[cfg(test)]
mod resolve_cell_link_tests {
    use super::*;
    use super::bind::resolve_cell_link;
    use serde_json::json;

    fn row(v: Value) -> serde_json::Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn a_link_naming_a_data_column_verbatim_substitutes_that_columns_own_value() {
        let cols = ["id".to_string(), "url".to_string()];
        let r = row(json!({"id": "1", "url": "/accounts/42"}));
        assert_eq!(resolve_cell_link("url", &r, &cols).unwrap().as_deref(), Some("/accounts/42"));
    }

    #[test]
    fn a_verbatim_column_link_with_a_null_value_has_no_link() {
        let cols = ["id".to_string(), "url".to_string()];
        let r = row(json!({"id": "1", "url": null}));
        assert_eq!(resolve_cell_link("url", &r, &cols).unwrap(), None);
    }

    #[test]
    fn a_bare_template_placeholder_substitutes_the_columns_string_value() {
        let cols = ["company".to_string()];
        let r = row(json!({"company": "Nucleus"}));
        assert_eq!(
            resolve_cell_link("/accounts/{{ company }}", &r, &cols).unwrap().as_deref(),
            Some("/accounts/Nucleus")
        );
    }

    #[test]
    fn a_urlencode_filter_matches_quote_plus_space_and_ampersand() {
        let cols = ["company".to_string()];
        let r = row(json!({"company": "A B&C"}));
        assert_eq!(
            resolve_cell_link("/accounts/{{ company | urlencode }}", &r, &cols)
                .unwrap()
                .as_deref(),
            Some("/accounts/A+B%26C")
        );
    }

    #[test]
    fn an_unknown_template_filter_is_an_error_not_a_silent_substitution() {
        let cols = ["company".to_string()];
        let r = row(json!({"company": "Nucleus"}));
        let err = resolve_cell_link("/accounts/{{ company | upper }}", &r, &cols).unwrap_err();
        assert_eq!(err.code, "ERR-TABLE-LINK-FILTER-UNKNOWN");
        assert_eq!(err.params["filter_name"], "upper");
    }

    #[test]
    fn a_null_placeholder_value_yields_no_link_for_the_row() {
        let cols = ["company".to_string()];
        let r = row(json!({"company": null}));
        assert_eq!(resolve_cell_link("/accounts/{{ company }}", &r, &cols).unwrap(), None);
    }

    #[test]
    fn a_missing_placeholder_column_yields_no_link_for_the_row() {
        let cols = ["id".to_string()];
        let r = row(json!({"id": "1"}));
        assert_eq!(resolve_cell_link("/accounts/{{ ghost }}", &r, &cols).unwrap(), None);
    }

    /// `"" or None`: every placeholder resolves but
    /// the whole substituted string is empty.
    #[test]
    fn an_all_empty_substitution_yields_no_link() {
        let cols = ["company".to_string()];
        let r = row(json!({"company": ""}));
        assert_eq!(resolve_cell_link("{{ company }}", &r, &cols).unwrap(), None);
    }

    /// `str(float)` is CPython's repr: exponent form from `1e+16` up and
    /// below `1e-04`, `-0.0` keeps its sign, an exact tie rounds half-even.
    #[test]
    fn a_float_placeholder_prints_as_python_str_does() {
        let cols = ["v".to_string()];
        for (v, want) in [
            (1e16, "/v/1e+16"),
            (1.5e16, "/v/1.5e+16"),
            (9999999999999998.0, "/v/9999999999999998.0"),
            (0.0001, "/v/0.0001"),
            (0.00001, "/v/1e-05"),
            (-0.0, "/v/-0.0"),
            (718102875569663.25, "/v/718102875569663.2"),
        ] {
            let r = row(json!({"v": v}));
            assert_eq!(resolve_cell_link("/v/{{ v }}", &r, &cols).unwrap().as_deref(), Some(want), "{v:e}");
        }
    }

    /// `_TEMPLATE_RE`'s `\w` is `str.isalnum()` or `_` over Unicode; its `\s`
    /// is `str.isspace()`, which includes `\x1c`-`\x1f`.
    #[test]
    fn a_non_ascii_placeholder_name_is_a_placeholder() {
        let cols = ["café".to_string()];
        let r = row(json!({"café": "Nucleus", "²x": "sq"}));
        assert_eq!(resolve_cell_link("/a/{{ café }}", &r, &cols).unwrap().as_deref(), Some("/a/Nucleus"));
        assert_eq!(resolve_cell_link("/a/{{ ²x }}", &r, &cols).unwrap().as_deref(), Some("/a/sq"));
        assert_eq!(resolve_cell_link("/a/{{\x1ccafé\x1f}}", &r, &cols).unwrap().as_deref(), Some("/a/Nucleus"));
        let err = resolve_cell_link("/a/{{ café | ünicode }}", &r, &cols).unwrap_err();
        assert_eq!(err.params["filter_name"], "ünicode");
    }

    /// A combining mark (`Mn`/`Mc`) is not `\w`, even where Rust's
    /// `char::is_alphanumeric` counts it (`Other_Alphabetic`), so the span
    /// stays literal text.
    #[test]
    fn a_placeholder_name_with_a_combining_mark_is_literal_text() {
        let cols = ["x".to_string()];
        let r = row(json!({"x": "v", "cafe\u{301}": "v", "x\u{345}": "v", "x\u{903}": "v"}));
        for link in ["/{{ cafe\u{301} }}", "/{{ x\u{345} }}", "/{{ x\u{903} }}"] {
            assert_eq!(resolve_cell_link(link, &r, &cols).unwrap().as_deref(), Some(link));
        }
    }

    #[test]
    fn a_template_free_string_is_the_href_verbatim() {
        let cols = ["id".to_string()];
        let r = row(json!({"id": "1"}));
        assert_eq!(
            resolve_cell_link("https://example.com/fixed", &r, &cols).unwrap().as_deref(),
            Some("https://example.com/fixed")
        );
    }
}

// ── Number formatting ─────────────────────────────────────────────────────────

/// Format `v` to at most `max_dp` decimal places, trimming trailing zeros.
// ── Column allocation — port of production calculate_column_layout
// (+) and its helpers. Demands/floors/headers arrive
// padded (content + cell_pad*2), matching production's inputs.

/// A column whose padded demand fits under this
/// is compact (pinned to demand); above it, text (competes for budget).
const COMPACT_DEMAND_CEILING: f64 = 220.0;
/// No single column may claim more than this fraction
/// of the budget in proportional allocation.
const PROPORTIONAL_CAP: f64 = 0.6;


// ── Column analysis ───────────────────────────────────────────────────────────

struct ColMeta {
    name: String,
    header: String,
    cfg: ColConfig,
    is_numeric: bool,
    // A text column that is right-aligned AND carries date-like content —
    // the "date lane" (invariant #11): rendered like a numeric column
    // (header centered, cell end-anchored, tabular-numeric font) but through
    // its own formatter, never format_numeric_col.
    is_date: bool,
    // This column's chart-level `conditional_formatting` when-rules, empty
    // when none are authored.
    when_rules: Vec<WhenRule>,
}

/// Per-row semantic role from `style.row.role`, collapsed to the two bits
/// table.rs needs (summary, total).
#[derive(Clone, Copy, PartialEq, Eq)]
enum RowRole {
    Value,
    Summary,
    Total,
}

/// Formatted cell value: the number/text plus optional prefix and suffix
/// lanes. The three-lane split: a currency prefix ("$")
/// and a unit/magnitude suffix ("%", "K", "M", …) each render as their own
/// end-/start-anchored tspan at a column-fixed x, not folded into `text`.
#[derive(Default)]
struct Cell {
    text: String,            // number (or raw text for non-numeric columns)
    prefix: Option<String>,  // "$" — only when the anchor gate allows it
    suffix: Option<String>,  // "%", "K", "M", … only when the anchor gate allows it
    // The same affixes UNGATED — what production's measure_column_demands
    // sees (it formats every cell as its own anchor); the paint fields
    // above carry the declare-once gate, these carry the full display.
    full_prefix: Option<String>,
    full_suffix: Option<String>,
    // A shared-scale cell's lanes as the fit cascade measures them
    // (`_has_overflow` passes `anchored`), where the demand pass reads the
    // locale's own currency placement.
    fit_affixes: Option<(String, String)>,
    pad: String,
    // Raw value was null. Production's per-cell `_is_numeric_cell` gate
    // drops a null off the numeric lane path unless the
    // column carries an explicit `format:` — the paint loop reads this to
    // route the em-dash onto the text path for unformatted columns.
    is_null: bool,
}

/// A coordinate as Python's `str()` writes it: an `Int` prints bare (`81`), a
/// `Float` always carries its `.0` (`81.0`). Production's `_divider_segment`
/// interpolates whatever number its caller computed, so the same line is `81`
/// from `int(...)` arithmetic and `81.0` from a float sum.
#[derive(Clone, Copy)]
enum PyNum {
    Int(f64),
    Float(f64),
}

/// Minimum gap between adjacent per-column header rules (`_RULE_GAP`).
const HEADER_RULE_GAP: f64 = 4.0;

// ── Column width allocation ───────────────────────────────────────────────────

/// Authored/resolved `width:` hint — an int px value or a string (bare px
/// number or "NN%"). Mirrors `TableColumnConfig.width`'s `int | str | None`
/// authored type.
#[derive(Deserialize, Clone)]
#[serde(untagged)]
enum WidthHint {
    Number(f64),
    Text(String),
}


// ── Pivot reshape ──────────────────────────────────────────────────────────────

// Not valid in a SQL alias — the only producer of a key carrying it is this
// module's own leaf-key construction.
const PIVOT_LEAF_SEP: &str = "\u{1e}";

/// One column-dimension group-header span: `(label, first_leaf_idx, n_leaves)`,
/// `first_leaf_idx` 0-indexed into the leaf portion of the final column list.
type PivotLevel = (String, usize, usize);

struct PivotResult {
    rows: Vec<serde_json::Map<String, Value>>,
    /// Final visible column order: row-dim fields, then leaf columns.
    col_order: Vec<String>,
    n_row_dims: usize,
    /// Group-header rows drawn above the leaf header row. Empty for the
    /// single-dim single-measure shape (no group row needed:
    /// no levels).
    levels: Vec<Vec<PivotLevel>>,
    /// The single measure name, when this pivot has exactly one `values`
    /// entry — a measure-keyed `style.columns` entry fans out to every leaf
    /// that has no leaf-keyed entry of its own (the `elif pivot_columns and data` branch; `None` for the multi-measure
    /// shape, which already keys leaves by measure name directly).
    single_measure: Option<String>,
    /// The effective measures: the authored `values`, else the inferred ones.
    values: Vec<String>,
}

/// Painted swatch square size;
/// `SWATCH_CELL_DEMAND` adds 4px of breathing room to the square.
const SWATCH_SIZE: f64 = 14.0;
const SWATCH_CELL_DEMAND: f64 = 18.0;

/// How one pivot dimension's values order: chronological, ascending, or not at
/// all (first-seen).
enum DimOrder {
    Instant(std::collections::HashMap<String, (u32, u32, u32, u32, u32, u32, u32)>),
    Number(std::collections::HashMap<String, f64>),
}

/// A row-dimension cell as a Python dict key: `None`, a
/// number under Python's cross-type equality (`True == 1 == 1.0`, an int
/// and a float equal only when exactly equal), or a string.

// ── Static pagination control bar ──────────────────────────────────────────────

enum PageItem {
    Page(i64),
    Ellipsis,
}

/// What a `bar`'s optional value label needs beyond the mark itself.
struct BarLabel<'a> {
    text: String,
    font_size: f64,
    family: &'a str,
}

/// `render_spark_line`'s `stroke_width: float = 1.5` default;
/// `render_spark` (:623-680) never passes another.
const SPARK_LINE_STROKE_WIDTH: &str = "1.5";

/// What a spark cell reads from beyond its own config: the column's signed
/// layout, the negative tone, and the table font a `bar`'s label inherits.
struct SparkLayout<'a> {
    has_negative: bool,
    negative_tone: Option<&'a str>,
    font_size: f64,
    body_family: &'a str,
}

/// One table's resolved body — everything `render_data_rows` paints a
/// page from, built once per table. `rows`/`row_roles`/`row_heights`/
/// `wrapped_lines` are the shared, GLOBAL per-table arrays (sized
/// `n_rows`, computed once upstream for the whole dataset); a page's
/// `page_offset`/`page_n_shown` select its own range into them.
#[derive(Clone, Copy)]
struct TableBody<'a> {
    rows: &'a [serde_json::Map<String, Value>],
    row_roles: &'a [RowRole],
    row_heights: &'a [f64],
    summary_gap_unit: f64,
    rows_start_y: f64,
    th: &'a TableTheme,
    cols: &'a [ColMeta],
    col_x: &'a [f64],
    col_ws: &'a [f64],
    content_w: f64,
    /// The row-number column's width, zero when it is off; `Some` ink paints its indices.
    rn_w: f64,
    row_numbers: &'a RowNumbersTheme,
    row_number_ink: Option<&'a str>,
    wrapped_lines: &'a [Vec<Option<Vec<String>>>],
    prefix_xs: &'a [f64],
    number_xs: &'a [f64],
    suffix_xs: &'a [f64],
    outer: f64,
    pad: f64,
    row_h: f64,
    font_size: f64,
    body_family: &'a str,
    tab_family: &'a str,
    body_color: &'a str,
    /// A linked cell's ink: the board's own text color.
    link_color: Option<&'a str>,
    bar_auto_max: &'a BTreeMap<String, f64>,
    /// Spark `bar`/`column` columns holding a negative detail value: drawn from a midline.
    signed_columns: &'a BTreeSet<String>,
    tones: Option<&'a TonesStyle>,
    // Chart-root row-detail link template (`Resolved.link`) — `Some` only
    // when `links.auto`/FK-drill synthesized one; drives the whole-row band
    // below.
    link: Option<&'a str>,
    link_open: crate::href::LinkOpen,
    /// Declared board variable names: a linkless cell in such a column links
    /// to `?col=value` (`resolve_filter_cell_link`).
    filter_variables: &'a [String],
    /// The host's board URL space: authored links are rewritten through it.
    link_context: Option<&'a crate::href::LinkContext>,
    fonts: &'a FontBook,
    /// `get_font_measurer(cell_font.family)`: the family a cell is cut in.
    measure_family: &'a str,
    chart_id: &'a str,
    /// Cut elements per column (header and cells alike) in first-cut order,
    /// flushed once into WARN-TABLE-TEXT-TRUNCATED.
    truncations: &'a RefCell<Vec<(String, usize)>>,
}

/// The title and subtitle band above a table's header, and the drawing that
/// fills it — shared by a table with rows and one over none, which paint the
/// same band.
struct TitleBand {
    has_title: bool,
    cased_title: Option<String>,
    authored_title: Option<String>,
    subtitle: Option<String>,
    has_subtitle: bool,
    font_size: f64,
    family: String,
    color: String,
    weight: String,
    text_y: f64,
    /// The title as drawn: one entry per tspan.
    title_lines: Vec<String>,
    subtitle_lines: Vec<String>,
    /// Whether the pre-pass cut the title / subtitle (`WARN-CHART-TITLE-TRUNCATED`).
    title_truncated: bool,
    subtitle_truncated: bool,
    subtitle_font_size: f64,
    subtitle_family: String,
    subtitle_color: String,
    subtitle_text_y: f64,
    /// Height the band pushes the header down by (`title_height`).
    area_h: f64,
}

/// The title fields of a table's resolved chart: what `title_band` reads, so a
/// row's sizing pass asks the band the renderer paints from instead of
/// measuring the title a second way.
#[derive(Deserialize)]
struct TitleOnly {
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TableTitleStyle>,
    style: ResolvedStyle,
}

/// The slice of a table's resolved model a zero-row render reads. A query with
/// no rows leaves `rows`, `columns` and `column_defaults` null, so the full
/// [`Resolved`] cannot deserialize it.
#[derive(Deserialize)]
pub(crate) struct EmptyResolved {
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    id: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TableTitleStyle>,
    #[serde(default)]
    columns: Option<BTreeMap<String, Value>>,
    style: ResolvedStyle,
}

#[derive(Deserialize)]
struct EmptyState {
    font: Font,
}

/// One column's header as `resolve_wrapped_headers` lays it out.
struct WrappedHeader {
    lines: Vec<String>,
    /// Text was cut (wrap-two past two lines): the full label rides in a
    /// `<title>`.
    truncated: bool,
}

/// The two families a table's text is measured in: production's
/// `get_font_measurer` for the body and for the header label.
struct MeasureFonts<'a> {
    body: &'a str,
    header: &'a str,
}

/// Per-column demands in px, keyed by column name.
struct ColumnInputs {
    /// p95 of the formatted cell text, plus both cell paddings.
    demands: BTreeMap<String, f64>,
    /// The header label's one-line width.
    header_demands: BTreeMap<String, f64>,
    /// The widest unbreakable token, plus both cell paddings.
    word_floors: BTreeMap<String, f64>,
}

/// What the size-independent work derives from a table's chart and rows:
/// formatted cells, resolved columns and the per-column scans.
struct Chart {
    resolved: Resolved,
    /// The pivot's reshaped rows, else the query's.
    rows: Vec<serde_json::Map<String, Value>>,
    cols: Vec<ColMeta>,
    /// Whether a pivot reshaped the rows and drew the header groups.
    pivoted: bool,
    pivot_n_row_dims: usize,
    pivot_levels_visible: Vec<Vec<PivotLevel>>,
    row_roles: Vec<RowRole>,
    /// Every numeric/date-lane cell, formatted over the full dataset: this feeds
    /// measurement, which reads only the anchor-independent text and affixes.
    cell_data: Vec<Vec<Cell>>,
    /// Spark `bar` auto-max and midline scan, over the full dataset minus
    /// summary rows (a grand-total value would otherwise re-anchor every detail
    /// row's bar scale).
    bar_auto_max: BTreeMap<String, f64>,
    signed_columns: BTreeSet<String>,
    /// Spark columns own their width regardless of measured demand.
    force_compact: BTreeSet<String>,
    formats: Option<serde_json::Map<String, Value>>,
    sub_unit_specs: Option<BTreeMap<String, String>>,
}

/// The inputs `bind_chart` reads: a chart's resolved model, its rows and the
/// board's format tables.
#[derive(Clone, Copy)]
struct Source<'a> {
    resolved: &'a Value,
    rows: &'a [serde_json::Map<String, Value>],
    cell_text: &'a [CellRow],
    formats: Option<&'a serde_json::Map<String, Value>>,
    sub_unit_specs: Option<&'a BTreeMap<String, String>>,
}

/// A table's type and color, read once off its theme and the board.
struct Look {
    font_size: f64,
    body_family: String,
    /// `style.fonts.numeral` as authored: the family numerals measure in, and
    /// paint in (a Source Serif family gains the bundled-web alias).
    numeral_family: String,
    body_color: String,
    /// A linked cell's ink: the board's own text color.
    link_color: Option<String>,
    header_size: f64,
    header_family: String,
    header_color: String,
    header_weight: String,
    pad: f64,
    outer: f64,
    row_h: f64,
}

/// What a drawn table reads of its payload beyond the chart: the board it sits
/// in, its card padding, its variables and the title shift its row asks for.
struct Drawn {
    look: Look,
    /// The group-header rows above the leaf header: the pivot's, else the
    /// authored spanners'.
    header_levels: Vec<Vec<PivotLevel>>,
    board_style: Option<BoardStyle>,
    padding: Option<Padding>,
    variables: serde_json::Map<String, Value>,
    filter_variables: Vec<String>,
    interactive_controls: bool,
    link_context: Option<crate::href::LinkContext>,
    title_shift: TitleShift,
    pagination_control_height: f64,
}

/// A table after `bind`: the rows it draws, or a result with nothing to draw.
pub enum Bound {
    Rows(Rows),
    /// A query with no rows and no columns to head: the title band over an em dash.
    Empty(Blank),
}

pub struct Rows {
    chart: Chart,
    drawn: Drawn,
}

/// What an empty table draws from: the slice of its resolved model, its card
/// padding and its row's title shift.
pub struct Blank {
    resolved: EmptyResolved,
    padding: Option<Padding>,
    title_shift: TitleShift,
}

impl Bound {
    fn title_shift(&self) -> TitleShift {
        match self {
            Bound::Rows(rows) => rows.drawn.title_shift,
            Bound::Empty(blank) => blank.title_shift,
        }
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind::bind(payload)?;
    let measure = Measure { fonts };
    let laid_out = if payload.height > 0.0 {
        // The slot grows by the title band's shift so no row is squeezed out.
        let cell = Rect {
            x: 0.0,
            y: 0.0,
            width: payload.width,
            height: payload.height + bound.title_shift().body_dy,
        };
        layout::layout(&bound, cell, &measure)?
    } else {
        layout::lay_out(&bound, payload.width, None, &measure)?
    };
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
pub(crate) mod tests {
    /// A resolved column as the Python seam sends it: every column carries its link modes.
    pub(crate) fn col_config(mut column: Value) -> serde_json::Result<ColConfig> {
        let fields = column.as_object_mut().expect("a column is an object");
        fields.entry("link_open").or_insert(json!("page"));
        fields.entry("header_link_open").or_insert(json!("page"));
        serde_json::from_value(column)
    }

    /// Renders and returns the SVG plus the codes of the warnings it emitted.
    pub(crate) fn render_with_warnings(payload: &Payload) -> (String, Vec<&'static str>) {
        let (svg, warnings) = crate::diagnostic::collect(|| render(payload, &test_fonts()));
        (svg.expect("renders"), warnings.iter().map(|w| w.code).collect())
    }
    use super::*;
    use crate::tree::Tree;
    use super::layout::title_geometry;
    use super::layout::strip_js_comments;
    use super::layout::signed_fraction;
    use super::layout::render_spark_columns;
    use super::layout::render_spark_cell;
    use super::layout::render_spark_bar;
    use super::layout::paginator_window;
    use super::layout::numeric_lane_positions;
    use super::layout::max_page_sum;
    use super::layout::largest_safe_page_rows;
    use super::layout::header_rule_span;
    use super::layout::embed_table_pagination_script;
    use super::bind::resolve_row_roles;
    use super::bind::format_numeric_col;
    use super::bind::coerce_numeric_cell;
    use d3rs::py_float::float_repr;
    use crate::common::TAB_FONT;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// A `DividerStyle` payload: every slot is complete in every theme.
    pub(super) fn divider(visible: bool, color: &str, width: f64) -> Value {
        json!({"visible": visible, "color": color, "width": width, "dasharray": null})
    }

    /// A `TableDividerStyle` payload: a divider plus how far it runs.
    pub(super) fn table_divider(visible: bool, color: &str, width: f64) -> Value {
        let mut value = divider(visible, color, width);
        value["continuous"] = json!(true);
        value
    }

    /// The title slots a production resolve always bakes: `style.title_font`
    /// (the width-tiered font) and `title_style` with its subtitle font. Sizes,
    /// inks and weights are `fixtures/table_basic.json`'s; the families are the
    /// generic `serif`/`sans-serif` the fixtures already measured with.
    /// The scope a table render opens, for a test that calls a cell formatter
    /// directly: no board aliases, the en-US plain-digit siblings.
    pub(crate) fn en_us_scope() -> BoardFormats {
        BoardFormats::enter(None, Some(&en_us_siblings()))
    }

    pub(crate) fn en_us_siblings() -> BTreeMap<String, String> {
        BTreeMap::from([
            ("currency".to_string(), "$,.2f".to_string()),
            ("number".to_string(), ",.2f".to_string()),
        ])
    }

    pub(crate) fn resolved_title_font() -> Value {
        json!({"size": 18.0, "family": "serif", "weight": 500.0, "color": "#494A4C"})
    }

    pub(crate) fn resolved_title_style() -> Value {
        json!({
            "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": 500.0},
            "subtitle": {
                "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
            }
        })
    }

    pub(crate) fn base_table_theme() -> Value {
        json!({
            "font": {"family": "Inter, sans-serif", "size": 11.0},
            "header": {
                "height": 39.0,
                "overflow": "wrap-two",
                "font": {"family": "Inter, sans-serif", "size": 11.0, "weight": "600", "case": "title"},
                "divider": table_divider(true, "#222222", 1.0)
            },
            "row": {
                "height": 32.0,
                "stripe": {"color": null},
                "role": null,
                "divider": table_divider(false, "#222222", 1.0),
                "roles": {
                    "summary": {"divider": divider(true, "#222222", 1.0)},
                    "total": {"divider": divider(true, "#222222", 1.0)}
                }
            },
            "column_layout": {"cell_padding": 8.0},
            "outer_padding": 12.0,
            "pagination": {"enabled": true, "page_rows": 20},
            "bottom_padding": 0.0,
            "text_baseline_offset": 4.0,
            "symbol_mode": "all",
            "spark": {
                "color": "#23467f",
                "padding": {"top": 4.0, "right": 2.0, "bottom": 4.0, "left": 2.0},
                "empty": {"inset_x": 4.0, "stroke": {"color": "#d1d5db", "width": 1.0, "dasharray": "4 2"}},
                "single_value": {"inset_x": 4.0, "marker_radius": 2.5},
                "columns": {"gap": 1.0, "padding": 2.0, "min_bar_height": 1.0, "border": {"radius": 1.0}},
                "column": {"width": 16.0, "height": 24.0},
                "area": {"fill_opacity": 0.3},
                "bar": {
                    "background": "#e5e7eb", "color": "#23467f", "default_max": 100.0, "border": {"radius": 0.0},
                    "label": {"inset_x": 4.0, "fill": "#FFFFFF", "fill_opacity": 0.9, "min_size": 9.0, "height_offset": 5.0}
                }
            },
            "paginator": {
                "color_active": "#222222",
                "color_inactive": "#8a8c8f",
                "color_disabled": "#d0d3d9",
                "font": {"family": "Inter, sans-serif", "size": 11.0},
                "weight_active": 600,
                "weight_inactive": 400,
                "weight_chevron": 700,
                "item_width": 20.0
            }
        })
    }

    /// A one-table payload at `width`×`height` over `theme` — the shape every
    /// divider test builds by hand.
    fn plain_payload(theme: Value, columns: Value, rows: Vec<Value>, width: f64, height: f64) -> Payload {
        crate::test_payload(json!({
            "resolved": {
                "link_open": "page",
                "title": "Test Table",
                "title_style": resolved_title_style(),
                "columns": columns,
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": rows,
            "width": width,
            "height": height,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }))
    }

    /// A narrow table can wrap headers under a width shortfall, which sizes
    /// its WARN-TABLE-CRAMPED fix from the board's width.
    pub(super) fn on_a_board(mut payload: Payload) -> Payload {
        payload.board_style =
            crate::test_board_style(json!({"board_width": 600.0}));
        payload
    }

    /// The payload as a live host renders it: one page, controls that commit the variable.
    pub(super) fn live(mut payload: Payload) -> Payload {
        payload.interactive_controls = true;
        payload
    }

    fn make_payload(
        columns: Value,
        rows: Vec<Value>,
        row_role: Option<&str>,
        page_rows: usize,
    ) -> Payload {
        make_payload_ext(columns, rows, row_role, page_rows, "all")
    }

    fn make_payload_ext(
        columns: Value,
        rows: Vec<Value>,
        row_role: Option<&str>,
        page_rows: usize,
        symbol_mode: &str,
    ) -> Payload {
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(row_role);
        theme["pagination"]["page_rows"] = json!(page_rows);
        theme["symbol_mode"] = json!(symbol_mode);
        let resolved = json!({
            "link_open": "page",
            "id": "t1",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        crate::test_payload(payload_json)
    }

    #[test]
    fn explicit_columns_hide_unlisted_row_keys() {
        // 'nrr' is present in every row but not authored in style.columns —
        // production never shows it as a column (fill_table_column_defaults /
        // _materialize_table_columns only materializes explicit keys when
        // style.columns is authored). The renderer must not error on it.
        let rows = vec![
            json!({"company": "Acme", "revenue": 100.0, "nrr": 1.1}),
            json!({"company": "Globex", "revenue": 200.0, "nrr": 1.2}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders without config-entry error");
        assert!(svg.contains("Company"));
        assert!(!svg.contains(">1.1<"), "nrr column must not render");
    }

    /// The resolver bakes a `decimal_pad_table` on a column with NO explicit `format:` and
    /// no `shared_scale` too — every row keeps its own per-row SI spec
    /// (`format_numeric_auto`'s law), only the trailing fractional-digit pad
    /// is shared. `examples/nested-layouts_0/monthly-details`'s own
    /// "Documents Created" column: 1037/2307/5795 -> "1.04"/"2.31" (frac 2,
    /// no pad) / "5.8"+pad (frac 1 < the column's max depth 2), each "K".
    #[test]
    fn unscaled_column_with_a_baked_pad_table_keeps_its_own_per_row_si_spec() {
        let rows = vec![
            json!({"month": "Oct", "docs": 1037.0}),
            json!({"month": "Nov", "docs": 5795.0}),
        ];
        let mut columns = json!({
            "month": {"label": "Month"},
            "docs": {"label": "Docs"},
        });
        columns["docs"]["decimal_pad_table"] =
            json!(["", "\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}\u{2008}\u{200b}"]);
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(">1.04<"), "frac == the table's own precision paints with no pad:\n{svg}");
        assert!(
            svg.contains(">5.8\u{2007}\u{200b}<"),
            "a shallower row takes the pad, still its own SI depth (not the pad table's fixed precision):\n{svg}"
        );
        assert_eq!(svg.matches(">K<").count(), 2, "the per-row magnitude suffix stays its own lane:\n{svg}");
    }

    /// `align: left` on a numeric column anchors the digits at the cell's left
    /// padding: no end-anchored lane tspans and no decimal pad, so a shallower
    /// row is not padded to line its decimal mark up with the deeper ones. The
    /// header moves left with its digits.
    #[test]
    fn a_left_aligned_numeric_column_anchors_its_digits_left_with_no_decimal_lane() {
        let rows = vec![
            json!({"month": "Oct", "docs": 1037.0}),
            json!({"month": "Nov", "docs": 5795.0}),
        ];
        let mut columns = json!({
            "month": {"label": "Month"},
            "docs": {"label": "Docs", "align": "left"},
        });
        columns["docs"]["decimal_pad_table"] =
            json!(["", "\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}\u{2008}\u{200b}"]);
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains("\u{2007}"), "no decimal pad on a left-anchored column:\n{svg}");
        assert!(!svg.contains("text-anchor=\"end\""), "no end-anchored lane tspan:\n{svg}");
        assert_eq!(
            svg.matches("<text x=\"320.3962290553462\" text-anchor=\"start\"").count(),
            2,
            "each body run starts at the cell's left padding:\n{svg}"
        );
        assert!(
            svg.contains("<text x=\"320.3962290553462\" y=\"66.0\" font-size=\"11\" font-weight=\"600\" fill=\"#222222\" text-anchor=\"start\" font-family=\"Inter, sans-serif\">Docs</text>"),
            "the header takes the same left x, start-anchored:\n{svg}"
        );
        assert!(
            svg.contains("<tspan>1.04</tspan><tspan dx=\"3.0\">K</tspan>")
                && svg.contains("<tspan>5.8</tspan><tspan dx=\"3.0\">K</tspan>"),
            "digits and the magnitude suffix flow from the start of the run:\n{svg}"
        );
    }

    /// `align: left` on a numeric column whose rows format to different
    /// widths (here "1.04"K vs "5.8"K) never lines the digits up, no matter
    /// how much slack the column has — WARN-TABLE-NUMERIC-ALIGN-LEFT.
    #[test]
    fn ragged_width_left_aligned_numeric_column_warns_numeric_align_left() {
        let rows = vec![
            json!({"month": "Oct", "docs": 1037.0}),
            json!({"month": "Nov", "docs": 5795.0}),
        ];
        let columns = json!({
            "month": {"label": "Month"},
            "docs": {"label": "Docs", "align": "left"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let (_svg, codes) = render_with_warnings(&payload);
        assert!(
            codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id),
            "{codes:?}"
        );
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
    }

    /// Equal-width values (same digit shape every row) in a column with
    /// slack beyond its content demand: left-anchoring shifts the block left
    /// but the digits still stack, so neither alignment warning fires.
    #[test]
    fn equal_width_left_aligned_column_with_slack_is_silent() {
        let rows = vec![
            json!({"month": "Oct", "docs": 10.0}),
            json!({"month": "Nov", "docs": 20.0}),
        ];
        let columns = json!({
            "month": {"label": "Month"},
            "docs": {"label": "Docs", "align": "left"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let (svg, codes) = render_with_warnings(&payload);
        assert!(svg.contains(">10<") && svg.contains(">20<"), "{svg}");
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id),
            "{codes:?}"
        );
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
    }

    /// Left edge of the "docs" digits with and without `align: left`, from the
    /// painted SVG: `(decimal_lane_left_x, left_run_x, warning_codes)`.
    fn docs_digit_left_edges(label: &str, docs_extra: Value, digits: &str) -> (f64, f64, Vec<&'static str>) {
        let rows = vec![
            json!({"month": "Oct", "docs": 10.0}),
            json!({"month": "Nov", "docs": 20.0}),
        ];
        let docs = |align: bool| {
            let mut c = json!({"label": label});
            if align {
                c["align"] = json!("left");
            }
            for (k, v) in docs_extra.as_object().unwrap() {
                c[k] = v.clone();
            }
            json!({"month": {"label": "Month"}, "docs": c})
        };
        let fonts = test_fonts();
        let decimal = render(&make_payload(docs(false), rows.clone(), None, 20), &fonts).expect("renders");
        let (left, codes) = render_with_warnings(&make_payload(docs(true), rows, None, 20));
        let end_x: f64 = decimal
            .split(&format!("\" text-anchor=\"end\">{digits}<"))
            .next()
            .unwrap()
            .rsplit('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        let left_x: f64 = left
            .split("<text x=\"")
            .skip(1)
            .find(|t| t.contains("text-anchor=\"start\"") && t.contains(&format!("<tspan>{digits}</tspan>")))
            .and_then(|t| t.split('"').next())
            .unwrap()
            .parse()
            .unwrap();
        (end_x - fonts.measure(digits, TAB_FONT, 11.0).unwrap(), left_x, codes)
    }

    /// The digits' x positions coincide only when the decimal lane already
    /// starts at the cell's left padding: a column pinned to exactly its digit
    /// width plus padding. `align: left` then paints nothing differently —
    /// WARN-TABLE-ALIGN-NO-VISIBLE-EFFECT, the silent no-op this code exists
    /// to end.
    #[test]
    fn equal_width_left_aligned_column_whose_digits_do_not_move_warns_no_visible_effect() {
        let digit_w = test_fonts().measure("10", TAB_FONT, 11.0).unwrap();
        let (lane_left, left_x, codes) =
            docs_digit_left_edges("N", json!({"width": digit_w + 12.0}), "10");
        assert!((lane_left - left_x).abs() < 1e-6, "{lane_left} vs {left_x}");
        assert!(
            codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id),
            "{codes:?}"
        );
    }

    /// A header wider than the digits centers the decimal lane inside the
    /// column, so `align: left` moves the digits left: no warning.
    #[test]
    fn equal_width_left_aligned_column_under_a_wide_header_moves_its_digits_and_is_silent() {
        let header = "Documents published";
        let header_w = test_fonts().measure(header, "Inter", 11.0).unwrap();
        let (lane_left, left_x, codes) =
            docs_digit_left_edges(header, json!({"width": header_w + 12.0}), "10");
        assert!(left_x < lane_left - 1.0, "{lane_left} vs {left_x}");
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
    }

    /// A column pinned below its content demand overflows both sides of the
    /// lane: the decimal run straddles the midpoint while the left run starts
    /// at the padding, so the digits move and nothing warns.
    #[test]
    fn equal_width_left_aligned_column_pinned_below_its_digits_moves_them_and_is_silent() {
        let (lane_left, left_x, codes) = docs_digit_left_edges("N", json!({"width": 20}), "10");
        assert!((lane_left - left_x).abs() > 1.0, "{lane_left} vs {left_x}");
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
    }

    /// `align: left` on a date column is none of the numeric paint path's
    /// business: no cell becomes a start-anchored lane run.
    #[test]
    fn a_date_column_authored_align_left_is_painted_as_before() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = vec![
            json!({"event": "Launch", "date": "2024-03-15"}),
            json!({"event": "Close", "date": "2024-06-01"}),
        ];
        let columns = |align: &str| {
            json!({"event": {"label": "Event"}, "date": {"label": "Date", "align": align}})
        };
        let fonts = test_fonts();
        let left = render(&make_payload(columns("left"), rows.clone(), None, 20), &fonts).expect("renders");
        let right = render(&make_payload(columns("right"), rows, None, 20), &fonts).expect("renders");
        assert!(left.contains(">15 Mar 2024</text>"), "{left}");
        assert!(!left.contains("text-anchor=\"end\""), "{left}");
        assert!(right.contains("text-anchor=\"end\""), "{right}");
    }

    /// `align: left` on a TEXT column is not this ladder's concern (the
    /// three-lane numeric paint path never runs for it) — neither alignment
    /// warning ever fires there, ragged or not.
    #[test]
    fn text_column_with_align_left_warns_neither_alignment_code() {
        let rows = vec![
            json!({"company": "A", "docs": 10.0}),
            json!({"company": "Globex Corporation", "docs": 20.0}),
        ];
        let columns = json!({
            "company": {"label": "Company", "align": "left"},
            "docs": {"label": "Docs"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let (_svg, codes) = render_with_warnings(&payload);
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id),
            "{codes:?}"
        );
        assert!(
            !codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id),
            "{codes:?}"
        );
    }

    // `currency`/`number` must carry d3's explicit `.3` precision
    // (predefined_formats.py:102,111) — a bare `~s` defaults to 6
    // significant digits, so 50752.9 would render "50.7529 K"/"50.7529k"
    // instead of the "$50.8K"/"50.8k" production emits. This is the same
    // shared table kpi.rs consumes (`common::predefined_number_spec`).
    #[test]
    fn currency_and_number_formats_use_three_significant_digits() {
        let rows = vec![json!({"company": "Acme", "revenue": 50752.9})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // "$" and the number are separate tspans (invariant #11's symbol lane).
        assert!(svg.contains(">$</tspan>") && svg.contains(">50.8"), "currency rounds to 3 significant digits: {svg}");
        assert!(!svg.contains("50.7529"), "must not fall back to d3's 6-sig-digit default: {svg}");

        let rows = vec![json!({"company": "Acme", "revenue": 50752.9})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "number"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("50.8"), "number rounds to 3 significant digits: {svg}");
        assert!(!svg.contains("50.7529"), "must not fall back to d3's 6-sig-digit default: {svg}");
    }

    #[test]
    fn row_role_column_is_excluded_even_when_unauthored() {
        // row.role names the query column carrying the per-row role marker;
        // it is never a display column even though style.columns is empty
        // (the fallback-to-query-columns branch, `table.rs`'s `else if let
        // Some(first) = rows.first()`) — this is the ONLY branch that ever
        // reads row.role, so an empty `columns` map is required to exercise
        // it (a non-empty map takes the other branch and never looks at
        // row.role at all).
        let rows = vec![
            json!({"company": "Acme", "revenue": 100.0, "row_role": "value"}),
            json!({"company": "Globex", "revenue": 200.0, "row_role": "total"}),
        ];
        let payload = make_payload(json!({}), rows, Some("row_role"), 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders without config-entry error");
        assert!(!svg.contains("Row Role"), "row_role's display header must not render");
        assert!(!svg.contains(">value<") && !svg.contains(">total<"), "row_role's cell values must not render");
        assert!(svg.contains("Company"), "a sibling query column still renders");
    }

    #[test]
    fn overflowing_table_renders_first_page_and_control_bar() {
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let payload = live(make_payload(columns, rows, None, 20));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("static pagination renders instead of erroring");
        assert!(svg.contains("Row 0"), "first page row present");
        assert!(!svg.contains("Row 20"), "second page row must not be painted");
        assert!(svg.contains("dbt-paginator"), "pagination control bar present");
    }

    #[test]
    fn paginator_uses_table_body_family_not_its_own() {
        // Production passes the table body family to the paginator and reads
        // paginator.font only for its size. A distinct
        // paginator family in the theme must NOT reach the emitted SVG.
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut theme = base_table_theme();
        theme["pagination"]["page_rows"] = json!(20);
        theme["paginator"]["font"]["family"] = json!("PaginatorOnlySans");
        let resolved = json!({
            "link_open": "page",
            "id": "t1",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload = live(crate::test_payload(payload_json));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("paginated table renders");
        assert!(
            !svg.contains("PaginatorOnlySans"),
            "paginator family must not be emitted:\n{svg}"
        );
    }

    fn make_pivot_payload(
        columns: Value,
        rows: Vec<Value>,
        pivot_rows: Vec<&str>,
        pivot_columns: Vec<&str>,
        pivot_values: Vec<&str>,
    ) -> Payload {
        let theme = base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "Test Pivot",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "rows": pivot_rows,
            "pivot_columns": pivot_columns,
            "values": pivot_values,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        crate::test_payload(payload_json)
    }

    // playground/pivot-tables' `revenue_cross_tab` shape: single pivot
    // dimension, single measure — leaf columns are the bare `category`
    // values, no group-header row.
    #[test]
    fn pivot_single_dim_single_measure_renders_leaf_columns() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0}),
            json!({"region": "West", "category": "Electronics", "revenue": 21000.0}),
            json!({"region": "West", "category": "Tools", "revenue": 9000.0}),
        ];
        let columns = json!({
            "region": {"label": "Region"},
            "Electronics": {"format": "currency_whole"},
            "Tools": {"format": "currency_whole"},
        });
        let payload =
            make_pivot_payload(columns, rows, vec!["region"], vec!["category"], vec!["revenue"]);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("pivot renders");
        assert!(svg.contains("Region"), "row-dim header renders:\n{svg}");
        assert!(svg.contains("Electronics"), "leaf header from bare col value:\n{svg}");
        assert!(svg.contains("Tools"), "second leaf header:\n{svg}");
        assert!(svg.contains(">$</tspan>") && svg.contains(">58,396<"), "currency_whole format applied to leaf cell:\n{svg}");
        assert!(!svg.contains(">category<"), "raw pivot-dim field never renders as its own column");
        assert!(svg.contains("East") && svg.contains("West"), "row values render");
    }

    // `revenue_units_cross_tab` shape: single pivot dimension, two measures —
    // leaf columns fan the measure-keyed style.columns config, and a group
    // header row labels each category span.
    #[test]
    fn pivot_multi_measure_renders_group_header_and_fanned_config() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
            json!({"region": "West", "category": "Electronics", "revenue": 21000.0, "units": 900.0}),
            json!({"region": "West", "category": "Tools", "revenue": 9000.0, "units": 300.0}),
        ];
        let columns = json!({
            "revenue": {"label": "Revenue", "format": "currency_whole"},
            "units": {"label": "Units", "format": "integer"},
        });
        let payload = make_pivot_payload(
            columns,
            rows,
            vec!["region"],
            vec!["category"],
            vec!["revenue", "units"],
        );
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("multi-measure pivot renders");
        assert!(svg.contains("Electronics"), "category group label renders:\n{svg}");
        assert!(svg.contains("Revenue") && svg.contains("Units"), "fanned measure labels render");
        assert!(svg.contains(">$</tspan>") && svg.contains(">58,396<"), "currency_whole applies to the revenue leaf");
        assert!(svg.contains("2,108"), "integer applies to the units leaf");
    }

    /// A multi-measure pivot leaf reaches `is_fanned_leaf` through `levels_nonempty`, not
    /// `fan_out_via_measure` — its `config_key` ("revenue"/"units") resolves directly against
    /// `r.columns`, so `fan_out_via_measure` (`raw_cfg.is_some() &&
    /// r.columns.get(config_key).is_none()`) is false here. Every other multi-measure pivot test in
    /// this file authors `label`/`format` only, so none of them can catch a regression on the
    /// `levels_nonempty` half of the gate — this is the commonest per-measure authoring shape
    /// (`style.columns.revenue.width`), and it is where the gate once broke.
    /// `revenue_xs[i]`/`units_xs[i]` are numeric leaf header centers (`mid = cx + cw/2`), so their
    /// difference is `col_ws[revenue]/2 + col_ws[units]/2` — asserted here against each leaf's own
    /// independently-computed natural `col_content_width` (mirroring
    /// `column_width_allocation_reserves_room_for_the_prefix_lane`'s pattern), not merely "not
    /// 200px apart": `table_w` is set to the exact sum of the five leaf columns' natural widths so
    /// surplus is 0 and `col_ws == natural` for every column, making the equality exact rather than
    /// approximate.
    #[test]
    fn pivot_multi_measure_leaf_columns_never_inherit_the_measures_width_pin() {
        // (see the single-measure variant: width chosen off the pin multiple)
        let fonts = test_fonts();
        // The allocator's demand semantics (production measure_column_demands):
        // whole rendered strings (prefix+number), measured with the Inter
        // stand-in; the header label folds in through the word floor. Header
        // and body are both 11px here, so one size serves both.
        let pad = 8.0_f64;
        let m = |s: &str| fonts.measure(s, "Inter", 11.0).expect("measures");
        // Floors tokenize what a cell paints ("2,108"), never the raw value
        // ("2108.0"), so the painted cell and the header label are the widest
        // units.
        let region_w = m("Region").max(m("East")).max(m("West")) + 2.0 * pad;
        let rev_elec_w = m("Revenue").max(m("$58,396")).max(m("$21,000")) + 2.0 * pad;
        let rev_tools_w = m("Revenue").max(m("$14,824")).max(m("$9,000")) + 2.0 * pad;
        let units_elec_w = m("Units").max(m("2,108")).max(m("900")) + 2.0 * pad;
        let units_tools_w = m("Units").max(m("449")).max(m("300")) + 2.0 * pad;
        let total_min = region_w + rev_elec_w + units_elec_w + rev_tools_w + units_tools_w;
        let outer = 12.0_f64;

        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
            json!({"region": "West", "category": "Electronics", "revenue": 21000.0, "units": 900.0}),
            json!({"region": "West", "category": "Tools", "revenue": 9000.0, "units": 300.0}),
        ];
        let columns = json!({
            "revenue": {"label": "Revenue", "format": "currency_whole", "width": 200},
            "units": {"label": "Units", "format": "integer", "width": 200},
        });
        let theme = base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "Test Pivot",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "rows": ["region"],
            "pivot_columns": ["category"],
            "values": ["revenue", "units"],
            "conditional_formatting": null,
        });
        // table_w set to the exact natural-width sum (surplus = 0), so
        // col_ws == natural for every leaf and the equality below is exact.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": total_min + 2.0 * outer,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &fonts).expect("multi-measure pivot renders");

        let xs_for = |label: &str| -> Vec<f64> {
            svg.match_indices(&format!(">{label}<"))
                .map(|(idx, _)| {
                    svg[..idx]
                        .rsplit("<text x=\"")
                        .next()
                        .and_then(|s| s.split('"').next())
                        .and_then(|s| s.parse().ok())
                        .unwrap_or_else(|| panic!("{label} header text x parses:\n{svg}"))
                })
                .collect::<Vec<f64>>()
        };
        let revenue_xs = xs_for("Revenue");
        let units_xs = xs_for("Units");
        assert_eq!(revenue_xs.len(), 2, "one Revenue leaf header per category:\n{svg}");
        assert_eq!(units_xs.len(), 2, "one Units leaf header per category:\n{svg}");
        let expected_diffs = [rev_elec_w / 2.0 + units_elec_w / 2.0, rev_tools_w / 2.0 + units_tools_w / 2.0];
        for (i, ((&rx, &ux), &expected)) in
            revenue_xs.iter().zip(units_xs.iter()).zip(expected_diffs.iter()).enumerate()
        {
            assert!(
                (ux - rx - expected).abs() < 0.15,
                "leaf {i}: revenue/units leaves must sit at their own computed \
                 natural half-width sum ({expected}), not the measure's 200px \
                 pin (rx={rx}, ux={ux}):\n{svg}"
            );
        }
    }

    /// A fanned leaf's reset nulls BOTH `width` and `max_width` before `fill_table_column_defaults`
    /// runs, so a leaf whose own measure config claims `max_width` alone still receives
    /// `column_defaults.width`. `revenue` claims `max_width: 120`; `column_defaults.width: 200`
    /// must still pin every revenue (and units) leaf to 200px.
    #[test]
    fn fanned_leaf_reset_clears_max_width_so_column_defaults_width_still_fills() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
        ];
        let columns = json!({
            "revenue": {"label": "Revenue", "format": "currency_whole", "max_width": 120},
            "units": {"label": "Units", "format": "integer"},
        });
        let theme = base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "Test Pivot",
            "title_style": resolved_title_style(),
            "columns": columns,
            "column_defaults": {"width": 200},
            "style": {"table": theme, "title_font": resolved_title_font()},
            "rows": ["region"],
            "pivot_columns": ["category"],
            "values": ["revenue", "units"],
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 1100.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("multi-measure pivot renders");

        let xs_for = |label: &str| -> Vec<f64> {
            svg.match_indices(&format!(">{label}<"))
                .map(|(idx, _)| {
                    svg[..idx]
                        .rsplit("<text x=\"")
                        .next()
                        .and_then(|s| s.split('"').next())
                        .and_then(|s| s.parse().ok())
                        .unwrap_or_else(|| panic!("{label} header text x parses:\n{svg}"))
                })
                .collect::<Vec<f64>>()
        };
        let revenue_xs = xs_for("Revenue");
        let units_xs = xs_for("Units");
        assert_eq!(revenue_xs.len(), 2, "one Revenue leaf header per category:\n{svg}");
        assert_eq!(units_xs.len(), 2, "one Units leaf header per category:\n{svg}");
        for (i, (&rx, &ux)) in revenue_xs.iter().zip(units_xs.iter()).enumerate() {
            assert!(
                (ux - rx - 200.0).abs() < 0.15,
                "leaf {i}: column_defaults.width: 200 must pin every leaf even \
                 though revenue claims max_width: 120 (rx={rx}, ux={ux}):\n{svg}"
            );
        }
    }

    /// A pivot whose every leaf is hidden keeps no group-header level: the empty
    /// level paints no blank band, so the table is as tall as the same row
    /// dimension drawn on its own.
    #[test]
    fn a_pivot_with_every_leaf_hidden_paints_no_blank_header_band() {
        let theme = base_table_theme();
        let height_of = |resolved_extra: Value, rows: Vec<Value>| -> String {
            let mut resolved = json!({
                "link_open": "page",
                "title": null,
                "title_style": resolved_title_style(),
                "style": {"table": theme.clone(), "title_font": resolved_title_font()},
                "conditional_formatting": null,
            });
            for (k, v) in resolved_extra.as_object().unwrap() {
                resolved[k] = v.clone();
            }
            let payload = crate::test_payload(json!({
                "resolved": resolved,
                "rows": rows,
                "width": 600.0,
                "height": 400.0,
                "background": "#ffffff",
                "fonts_dir": null,
                "padding": null,
            }));
            let svg = render(&payload, &test_fonts()).expect("renders");
            svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        let pivoted = height_of(
            json!({
                "columns": {
                    "revenue": {"visible": false},
                    "units": {"visible": false},
                },
                "rows": ["region"],
                "pivot_columns": ["category"],
                "values": ["revenue", "units"],
            }),
            vec![
                json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
                json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
                json!({"region": "West", "category": "Electronics", "revenue": 21000.0, "units": 900.0}),
                json!({"region": "West", "category": "Tools", "revenue": 9000.0, "units": 300.0}),
            ],
        );
        let alone = height_of(
            json!({"columns": {"region": {"label": "Region"}}, "pivot_columns": null}),
            vec![json!({"region": "East"}), json!({"region": "West"})],
        );
        assert_eq!(pivoted, alone);
    }

    // Regression: `visible: false` on a pivot leaf must narrow that leaf's
    // group-header span (and every later span shifts left), not leave a
    // hidden column's header/cells rendering because the pivot shape bypassed
    // the visible-column filter entirely.
    #[test]
    fn pivot_visible_false_hides_a_measure_leaf_and_narrows_its_group_span() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
        ];
        let columns = json!({
            "revenue": {"label": "Revenue", "format": "currency_whole"},
            "units": {"label": "Units", "format": "integer", "visible": false},
        });
        let payload = make_pivot_payload(
            columns,
            rows,
            vec!["region"],
            vec!["category"],
            vec!["revenue", "units"],
        );
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("pivot renders");
        assert!(
            !svg.contains(">Units<") && !svg.contains("2,108") && !svg.contains("449"),
            "hidden units leaf must not render at all:\n{svg}"
        );
        assert!(
            svg.contains(">58,396<") && svg.contains(">14,824<") && svg.matches(">$</tspan>").count() == 2,
            "revenue leaf still renders:\n{svg}"
        );
        // Each category's group-header span narrows to its single surviving
        // leaf (Revenue) instead of still covering the hidden Units column —
        // exactly one "Revenue" leaf header per category, no orphaned gap.
        assert_eq!(svg.matches(">Revenue<").count(), 2, "one leaf header per category, span narrowed:\n{svg}");
    }

    // `nested_columns` shape: two pivot dimensions, single measure — the
    // leaf header shows the innermost dim value, with a group header row for
    // the outer dim.
    #[test]
    fn pivot_multi_dim_single_measure_renders_nested_group_header() {
        // Two categories, each spanning both periods, so the outer-dim group
        // header actually exercises `build_pivot_levels`' run-merging (a
        // single shared category value here would pass even if the group
        // span collapsed to one leaf per row instead of merging the run).
        let rows = vec![
            json!({"region": "East", "category": "Accessories", "period": "Early", "revenue": 22864.0}),
            json!({"region": "East", "category": "Accessories", "period": "Late", "revenue": 19000.0}),
            json!({"region": "East", "category": "Tools", "period": "Early", "revenue": 8000.0}),
            json!({"region": "East", "category": "Tools", "period": "Late", "revenue": 6000.0}),
            json!({"region": "West", "category": "Accessories", "period": "Early", "revenue": 15000.0}),
            json!({"region": "West", "category": "Accessories", "period": "Late", "revenue": 12000.0}),
            json!({"region": "West", "category": "Tools", "period": "Early", "revenue": 5000.0}),
            json!({"region": "West", "category": "Tools", "period": "Late", "revenue": 4000.0}),
        ];
        let payload = make_pivot_payload(
            json!({}),
            rows,
            vec!["region"],
            vec!["category", "period"],
            vec!["revenue"],
        );
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("nested pivot renders");
        assert!(svg.contains("Accessories"), "outer dim group label renders:\n{svg}");
        assert!(svg.contains("Tools"), "second outer dim group label renders:\n{svg}");
        assert!(svg.contains("Early") && svg.contains("Late"), "inner dim leaf headers render");
    }

    // Single-dim single-measure pivot: a measure-keyed style.columns entry
    // (no leaf-keyed entry) must fan its format out to every leaf, not just
    // apply to a leaf literally named "revenue" (which never exists — leaf
    // keys are the pivoted category values). Deleting the measure fallback
    // in `pivot_table_data`'s caller makes this fail: the leaf's config_key
    // ("Electronics") never matches "revenue" in style.columns, so the cell
    // falls through to the un-styled `.3~s` auto-format (58.4k) instead of
    // the currency_whole $58,396 production renders.
    #[test]
    fn pivot_single_measure_fans_measure_keyed_config_to_every_leaf() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0}),
        ];
        let columns = json!({
            "revenue": {"format": "currency_whole"},
        });
        let payload =
            make_pivot_payload(columns, rows, vec!["region"], vec!["category"], vec!["revenue"]);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("pivot renders");
        assert!(svg.contains(">58,396<") && svg.contains(">14,824<") && svg.matches(">$</tspan>").count() == 2, "measure-keyed format fans to both leaves:\n{svg}");
        assert!(svg.contains("Electronics"), "leaf header stays the pivoted value, not the measure name");
    }

    /// A measure-keyed `width:` pin never fans onto pivot leaves — production's
    /// `_fanned_leaf_config` unconditionally clears `width`/`max_width` on
    /// every fanned leaf: "a per-column width multiplied
    /// across N leaves would inflate the table". Three leaves fanning a
    /// `revenue: {width: 200}` measure config must NOT each pin to 200px —
    /// consecutive leaf headers must not sit exactly 200px apart.
    #[test]
    fn pivot_leaf_columns_never_inherit_the_measures_width_pin() {
        let rows = vec![
            json!({"region": "East", "category": "A", "revenue": 10.0}),
            json!({"region": "East", "category": "B", "revenue": 10.0}),
            json!({"region": "East", "category": "C", "revenue": 10.0}),
        ];
        let columns = json!({
            "region": {"label": "R"},
            "revenue": {"width": 200},
        });
        let mut payload =
            make_pivot_payload(columns, rows, vec!["region"], vec!["category"], vec!["revenue"]);
        // Wide enough that the surplus equal-spread lands nowhere near the
        // 200px pin — inheritance would still show as exactly-200 gaps.
        payload.width = 1100.0;
        payload.height = 300.0;
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("pivot renders");

        let header_x = |needle: &str| -> f64 {
            svg.split(needle)
                .next()
                .and_then(|head| head.rsplit("<text x=\"").next())
                .and_then(|s| s.split('"').next())
                .and_then(|s| s.parse().ok())
                .unwrap_or_else(|| panic!("{needle} header text x parses:\n{svg}"))
        };
        let x_a = header_x(">A</text>");
        let x_b = header_x(">B</text>");
        let x_c = header_x(">C</text>");
        assert!(
            (x_b - x_a - 200.0).abs() > 1.0 && (x_c - x_b - 200.0).abs() > 1.0,
            "leaf columns must not each pin to the measure's 200px width \
             (x_a={x_a}, x_b={x_b}, x_c={x_c}):\n{svg}"
        );
    }

    // The multi-dim single-measure shape: leaves carry no measure suffix, so
    // the measure-keyed entry must fan onto them explicitly
    // (a `config_key == name` gate would miss it).
    #[test]
    fn pivot_multi_dim_single_measure_fans_measure_config_to_nested_leaves() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "quarter": "Q1", "revenue": 58396.0}),
            json!({"region": "East", "category": "Electronics", "quarter": "Q2", "revenue": 14824.0}),
        ];
        let columns = json!({
            "revenue": {"format": "currency_whole"},
        });
        let payload = make_pivot_payload(
            columns,
            rows,
            vec!["region"],
            vec!["category", "quarter"],
            vec!["revenue"],
        );
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("multi-dim pivot renders");
        assert!(
            svg.contains(">58,396<") && svg.contains(">14,824<") && svg.matches(">$</tspan>").count() == 2,
            "measure format reaches nested leaves (not the .3~s auto path):\n{svg}"
        );
    }

    // `_render_header_group_rows` fills each group row, then the leaf row is
    // filled: a pivot header with one group level paints two bands.
    #[test]
    fn a_header_background_fills_the_pivot_group_row_and_leaf_row() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "quarter": "Q1", "revenue": 1.0}),
        ];
        let mut payload = make_pivot_payload(
            json!({}),
            rows,
            vec!["region"],
            vec!["category", "quarter"],
            vec!["revenue"],
        );
        payload.resolved["style"]["table"]["header"]["background"] = json!("#f5f5f5");
        let svg = render(&payload, &test_fonts()).expect("pivot renders");
        assert_eq!(svg.matches("fill=\"#f5f5f5\"/>").count(), 2, "{svg}");
    }

    /// The leaf columns a pivot header draws, in order.
    fn pivot_header_order(month_values: &[Value]) -> Vec<String> {
        let rows: Vec<Value> = month_values
            .iter()
            .map(|m| json!({"region": "East", "month": m, "revenue": 1.0}))
            .collect();
        // Explicit labels keep the headers off the slug law (`-` splits there).
        let labels: serde_json::Map<String, Value> = month_values
            .iter()
            .map(|m| {
                let text = m.as_str().map_or_else(|| m.to_string(), str::to_string);
                (text.clone(), json!({"label": text}))
            })
            .collect();
        let payload =
            make_pivot_payload(Value::Object(labels), rows, vec!["region"], vec!["month"], vec!["revenue"]);
        let svg = render(&payload, &test_fonts()).expect("pivot renders");
        let head = svg.split("data-col").next().unwrap_or(&svg);
        let mut found: Vec<(usize, String)> = month_values
            .iter()
            .filter_map(|m| {
                let label = match m {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                head.find(&format!(">{label}</text>")).map(|at| (at, label))
            })
            .collect();
        found.sort();
        found.into_iter().map(|(_, label)| label).collect()
    }

    // A pivot dimension of calendar instants reads chronologically, of numbers
    // ascending; plain strings keep the query's order (`_order_col_tuples`).
    #[test]
    fn pivot_columns_follow_a_dimensions_canonical_order() {
        let temporal = [json!("2026-03-01"), json!("2026-01-01"), json!("2026-02-01")];
        assert_eq!(pivot_header_order(&temporal), ["2026-01-01", "2026-02-01", "2026-03-01"]);
        let buckets = [json!("Mar 2026"), json!("Jan 2026"), json!("Feb 2026")];
        assert_eq!(pivot_header_order(&buckets), ["Jan 2026", "Feb 2026", "Mar 2026"]);
        let numbers = [json!(10), json!(9), json!(100)];
        assert_eq!(pivot_header_order(&numbers), ["9", "10", "100"]);
        let plain = [json!("Total"), json!("B"), json!("A")];
        assert_eq!(pivot_header_order(&plain), ["Total", "B", "A"]);
        let mixed = [json!("2026-03-01"), json!("Zed"), json!("2026-01-01")];
        assert_eq!(pivot_header_order(&mixed), ["2026-03-01", "Zed", "2026-01-01"]);
    }

    /// The paginator shows page 1, the last page and one sibling either side of
    /// the current page; any gap collapses to a single ellipsis, however small.
    #[test]
    fn the_paginator_window_collapses_every_gap_to_one_ellipsis() {
        let shape = |page: i64, total: i64| -> String {
            paginator_window(page, total)
                .iter()
                .map(|item| match item {
                    PageItem::Page(p) => p.to_string(),
                    PageItem::Ellipsis => "\u{2026}".to_string(),
                })
                .collect::<Vec<_>>()
                .join(" ")
        };
        assert_eq!(shape(2, 3), "1 2 3");
        assert_eq!(shape(3, 5), "1 2 3 4 5");
        assert_eq!(shape(1, 8), "1 2 \u{2026} 8");
        assert_eq!(shape(4, 8), "1 \u{2026} 3 4 5 \u{2026} 8");
        assert_eq!(shape(8, 8), "1 \u{2026} 7 8");
        assert_eq!(shape(5, 12), "1 \u{2026} 4 5 6 \u{2026} 12");
        assert_eq!(shape(25, 50), "1 \u{2026} 24 25 26 \u{2026} 50");
    }

    /// `row.role` names a row column holding each row's role, or is itself the
    /// role every row takes when no column has that name; an unknown value is
    /// an ordinary row.
    #[test]
    fn a_row_role_is_a_column_first_and_a_literal_otherwise() {
        let row = |v: Value| v.as_object().unwrap().clone();
        let rows = vec![
            row(json!({"kind": " Total "})),
            row(json!({"kind": "summary"})),
            row(json!({"kind": "weird"})),
            row(json!({"kind": null})),
        ];
        let by_column = resolve_row_roles(&rows, Some("kind"));
        assert!(matches!(by_column.as_slice(), [RowRole::Total, RowRole::Summary, RowRole::Value, RowRole::Value]));
        let literal = resolve_row_roles(&rows, Some("total"));
        assert!(literal.iter().all(|r| matches!(r, RowRole::Total)));
        assert!(resolve_row_roles(&rows, Some("nonsense")).iter().all(|r| matches!(r, RowRole::Value)));
    }

    // column_defaults.format fills only columns that set no format of their own.
    #[test]
    fn column_defaults_format_fills_only_columns_without_one() {
        let rows = vec![json!({"amt": 58396.0, "qty": 3400.0})];
        let columns = json!({
            "amt": {"format": "currency_whole"},
            "qty": {"label": "Quantity"},
        });
        let mut theme = base_table_theme();
        theme["pagination"]["page_rows"] = json!(20);
        let payload_json = json!({
            "resolved": {
                "link_open": "page",
                "title": "Test Table",
                "title_style": resolved_title_style(),
                "columns": columns,
                "column_defaults": {"format": "integer"},
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(">$</tspan>") && svg.contains(">58,396<"), "amt keeps its own format over the default:\n{svg}");
        assert!(svg.contains("Quantity"), "qty keeps its own label:\n{svg}");
        assert!(
            svg.contains(">3,400<"),
            "qty (no format) takes the default integer format, not .3~s's 3.4k:\n{svg}"
        );
    }

    /// `width`/`max_width` are one sizing slot: a column claiming `max_width` must not also be
    /// filled from `column_defaults.width`. `a` claims neither and gets the `column_defaults.width:
    /// 200` pin; `b` claims `max_width` alone and must NOT also get pinned to 200px.
    #[test]
    fn column_defaults_width_never_fills_a_column_that_claims_max_width() {
        let rows = vec![json!({"a": "x", "b": "y", "c": "z"})];
        let columns = json!({
            "a": {"label": "A"},
            "b": {"label": "B", "max_width": 80},
            "c": {"label": "C"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let payload_json = json!({
            "resolved": {
                "link_open": "page",
                "title": "Test Table",
                "title_style": resolved_title_style(),
                "columns": columns,
                "column_defaults": {"width": 200},
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": rows,
            "width": 900.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let header_x = |needle: &str| -> f64 {
            svg.split(needle)
                .next()
                .and_then(|head| head.rsplit("<text x=\"").next())
                .and_then(|s| s.split('"').next())
                .and_then(|s| s.parse().ok())
                .unwrap_or_else(|| panic!("{needle} header text x parses:\n{svg}"))
        };
        let x_a = header_x(">A</text>");
        let x_b = header_x(">B</text>");
        let x_c = header_x(">C</text>");
        assert!(
            (x_b - x_a - 200.0).abs() < 0.15,
            "a (no sizing claim) must still be pinned to the 200px column_defaults.width \
             (x_a={x_a}, x_b={x_b}):\n{svg}"
        );
        assert!(
            (x_c - x_b - 200.0).abs() > 1.0,
            "b (claims max_width) must not also be filled from column_defaults.width \
             (x_b={x_b}, x_c={x_c}):\n{svg}"
        );
    }

    // A leaf-keyed entry still wins over the measure-keyed fallback
    // — the two must be able to disagree.
    #[test]
    fn pivot_single_measure_leaf_keyed_config_wins_over_measure_fallback() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0}),
        ];
        let columns = json!({
            "revenue": {"format": "currency_whole"},
            "Electronics": {"format": "integer"},
        });
        let payload =
            make_pivot_payload(columns, rows, vec!["region"], vec!["category"], vec!["revenue"]);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("pivot renders");
        assert!(svg.contains(">58,396<"), "leaf-keyed integer format wins (number lane):\n{svg}");
        assert!(svg.contains(">14,824<"), "measure-keyed format still fans to the un-overridden leaf:\n{svg}");
        assert_eq!(
            svg.matches(">$</tspan>").count(),
            1,
            "exactly one dollar prefix — the un-overridden leaf, not the leaf-keyed integer one:\n{svg}"
        );
    }

    #[test]
    fn pivot_without_rows_and_values_is_ambiguous_and_without_rows_infers_them() {
        let rows = vec![json!({"region": "East", "category": "Electronics", "revenue": 1.0})];
        let mut payload = make_pivot_payload(json!({}), rows, vec![], vec!["category"], vec![]);
        // Blank out `rows`/`values` entirely (not just empty lists): an empty
        // list is a valid (if unusual) authored value, `null` is the unauthored one.
        payload.resolved["rows"] = Value::Null;
        payload.resolved["values"] = Value::Null;
        let fonts = test_fonts();
        let err = render(&payload, &fonts).expect_err("neither rows nor values is ambiguous");
        assert_eq!(err.code, "ERR-TABLE-PIVOT-DIMENSIONS-AMBIGUOUS");

        payload.resolved["values"] = json!(["revenue"]);
        let svg = render(&payload, &fonts).expect("rows are inferred from the unclaimed columns");
        assert!(svg.contains(">Region</text>"), "the inferred row dimension heads a column:\n{svg}");
    }

    // Static export beyond the 20-page cap: the pager keeps the true page
    // count, and the pages past the cap are not clickable.
    #[test]
    fn static_pagination_over_cap_keeps_true_page_count_with_capped_pages_disabled() {
        let rows: Vec<Value> = (0..500)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut payload = make_payload(columns, rows, None, 20);
        // Tall enough that the slot fits the full 20-row page (plus the
        // control band) — otherwise the slot, not page_rows, decides the
        // page size and the note's totals shift.
        payload.height = 800.0;
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("capped pagination renders instead of erroring");
        assert!(!svg.contains("Showing pages"), "no English cap note:\n{svg}");
        assert!(svg.contains(">25</text>"), "pager keeps the true last page:\n{svg}");
        assert!(
            !svg.contains("data-dbt-page-target=\"25\""),
            "a page past the cap is not clickable:\n{svg}"
        );
        assert!(
            svg.contains("data-dbt-page-target=\"2\""),
            "a rendered page stays clickable:\n{svg}"
        );
    }

    // ── table_pagination.js embedding ──────────────────────────────────────

    #[test]
    fn strip_js_comments_removes_the_fenced_engineering_note() {
        let source = "before/*{# a note\nspanning lines #}*/after";
        assert_eq!(strip_js_comments(source), "before after");
    }

    #[test]
    fn strip_js_comments_leaves_plain_comments_untouched() {
        let source = "// a plain comment\nconst x = 1; /* also plain */";
        assert_eq!(strip_js_comments(source), source);
    }

    #[test]
    fn embed_table_pagination_script_strips_wraps_and_escapes() {
        let mut tree = Tree::new();
        embed_table_pagination_script(&mut tree);
        let embedded = print(&tree.finish());
        assert!(
            embedded.starts_with("<script type=\"text/javascript\">\n<![CDATA[\n"),
            "opens with the CDATA wrapper: {embedded}"
        );
        assert!(embedded.trim_end().ends_with("]]>\n</script>"), "closes the wrapper: {embedded}");
        assert!(
            !embedded.contains("/*{#"),
            "the fenced engineering note never ships inline: {embedded}"
        );
        assert!(
            embedded.contains("data-dbt-table-page"),
            "the real script body is present, not just the wrapper: {embedded}"
        );
    }

    // ── static_multi_page ─────────────────

    /// A payload with a real chart id — `static_multi_page` gates on
    /// `bool(chart_id)`, and `make_payload`'s own fixtures never set one, so
    /// every pre-existing test exercises only the single-page branch.
    fn make_paginated_payload_with_id(
        id: &str,
        columns: Value,
        rows: Vec<Value>,
        page_rows: usize,
        height: f64,
    ) -> Payload {
        let mut theme = base_table_theme();
        theme["pagination"]["page_rows"] = json!(page_rows);
        let resolved = json!({
            "link_open": "page",
            "id": id,
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": height,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        crate::test_payload(payload_json)
    }

    /// A host that commits `data-dbt-page-var` draws only the page its
    /// variable names, and every clickable control names that variable.
    #[test]
    fn an_interactive_host_draws_one_page_and_its_controls_name_the_variable() {
        let rows: Vec<Value> = (0..7)
            .map(|i| json!({"product": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({"product": {"label": "Product"}, "revenue": {"label": "Revenue"}});
        let mut payload = make_paginated_payload_with_id("t1", columns, rows, 3, 800.0);
        payload.interactive_controls = true;
        payload.variables = json!({"t1_page": 2}).as_object().unwrap().clone();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(">Row 3<") && svg.contains(">Row 5<"), "page 2 rows:\n{svg}");
        assert!(!svg.contains(">Row 0<") && !svg.contains(">Row 6<"), "only page 2:\n{svg}");
        assert!(!svg.contains("dbt-table-page") && !svg.contains("<script"), "{svg}");
        assert!(svg.contains("data-dbt-page-var=\"t1_page\" data-dbt-page-target=\"1\""), "{svg}");
        assert!(svg.contains("4\u{2013}6 / 7"), "{svg}");
    }

    #[test]
    fn static_multi_page_wraps_each_page_in_its_own_toggle_group() {
        // 7 rows at page_rows=3: overflow (4) exceeds PAGINATION_GROW_CAP
        // (2), so the grow-by-2 short-circuit (every row on one page) never
        // fires — a real 3-page table (3, 3, 1).
        let rows: Vec<Value> = (0..7)
            .map(|i| json!({"product": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({"product": {"label": "Product"}, "revenue": {"label": "Revenue"}});
        let payload = make_paginated_payload_with_id("t1", columns, rows, 3, 800.0);
        let svg = render(&payload, &test_fonts()).expect("3-page table renders");

        assert!(
            svg.contains("<g class=\"dbt-table-page\" data-dbt-table-page=\"t1\" data-page=\"1\" style=\"display:\">"),
            "page 1 is visible by default: {svg}"
        );
        assert!(
            svg.contains("<g class=\"dbt-table-page\" data-dbt-table-page=\"t1\" data-page=\"2\" style=\"display:none\">"),
            "page 2 starts hidden: {svg}"
        );
        assert!(
            svg.contains("<g class=\"dbt-table-page\" data-dbt-table-page=\"t1\" data-page=\"3\" style=\"display:none\">"),
            "page 3 starts hidden: {svg}"
        );
        // Page 1 paints rows 0-2, page 2 paints rows 3-5, page 3 paints row
        // 6 — never repeated, never missing.
        let page1 = svg.split("data-page=\"1\"").nth(1).unwrap().split("data-page=\"2\"").next().unwrap();
        let page2 = svg.split("data-page=\"2\"").nth(1).unwrap().split("data-page=\"3\"").next().unwrap();
        let page3 = svg.split("data-page=\"3\"").nth(1).unwrap();
        for i in 0..3 {
            assert!(page1.contains(&format!(">Row {i}<")), "page 1 paints row {i}: {page1}");
        }
        for i in 3..6 {
            assert!(page2.contains(&format!(">Row {i}<")), "page 2 paints row {i}: {page2}");
        }
        assert!(page3.contains(">Row 6<"), "page 3 paints row 6: {page3}");
        assert!(!page1.contains(">Row 3<"), "page 1 does not repaint page 2's rows: {page1}");

        // Click targets and the embedded toggle script are present.
        assert!(svg.contains("data-dbt-page-target=\"2\""), "next-page click target: {svg}");
        assert!(
            svg.contains("<script type=\"text/javascript\">"),
            "the pagination toggle script is embedded: {svg}"
        );
    }

    /// `{chart_id}_page` picks the visible toggle group: a page past the pre-rendered ones
    /// falls back to the last, and a missing or unreadable value is page 1.
    #[test]
    fn page_variable_picks_the_visible_toggle_group() {
        let rows: Vec<Value> = (0..7)
            .map(|i| json!({"product": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({"product": {"label": "Product"}, "revenue": {"label": "Revenue"}});
        let visible_pages = |value: Value| -> Vec<usize> {
            let mut payload =
                make_paginated_payload_with_id("t1", columns.clone(), rows.clone(), 3, 800.0);
            payload.variables = json!({"t1_page": value}).as_object().unwrap().clone();
            let svg = render(&payload, &test_fonts()).expect("3-page table renders");
            (1..=3)
                .filter(|n| svg.contains(&format!("data-page=\"{n}\" style=\"display:\">")))
                .collect()
        };
        assert_eq!(visible_pages(json!(2)), [2]);
        assert_eq!(visible_pages(json!("3")), [3]);
        assert_eq!(visible_pages(json!(9)), [3]);
        assert_eq!(visible_pages(json!(0)), [1]);
        assert_eq!(visible_pages(json!("x")), [1]);
        assert_eq!(visible_pages(json!(null)), [1]);
    }

    /// The edge case the extraction into `render_data_rows` exists to get
    /// right: a page that starts mid-run of summary rows carries no gap for
    /// its own first row, since there is no previous row in ITS OWN slice.
    #[test]
    fn static_multi_page_page_starting_on_a_summary_row_carries_no_gap() {
        let mut rows: Vec<Value> = (0..6)
            .map(|i| json!({"product": format!("Row {i}"), "revenue": i as f64, "row_role": "value"}))
            .collect();
        rows.push(json!({"product": "Subtotal A", "revenue": 15.0, "row_role": "summary"}));
        rows.push(json!({"product": "Subtotal B", "revenue": 0.0, "row_role": "summary"}));
        rows.push(json!({"product": "Total", "revenue": 15.0, "row_role": "total"}));
        let columns = json!({"product": {"label": "Product"}, "revenue": {"label": "Revenue"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["pagination"]["page_rows"] = json!(3);
        let resolved = json!({
            "link_open": "page",
            "id": "t2",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 800.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("3-page table renders");

        let page3 = svg.split("data-page=\"3\"").nth(1).unwrap();
        for name in ["Subtotal A", "Subtotal B", "Total"] {
            assert!(page3.contains(&format!(">{name}<")), "page 3 paints {name}: {page3}");
        }
        // Page 3's first row ("Subtotal A") starts at rows_start_y itself —
        // no summary_gap_unit added, since it is local row 0 on its own page.
        let rows_start_y = text_y_before(page3, ">Subtotal A<");
        let page1 = svg.split("data-page=\"1\"").nth(1).unwrap().split("data-page=\"2\"").next().unwrap();
        let page1_row0_y = text_y_before(page1, ">Row 0<");
        assert_eq!(
            rows_start_y, page1_row0_y,
            "page 3's own row 0 (a summary row) starts at the same y page 1's row 0 does — no carried-over gap:\n{page3}"
        );
    }

    /// Regression: `format_numeric_col`'s `is_symbol_anchor`/`anchor_idx`
    /// used to run ONCE over the whole (unpaged) dataset, so `symbol_mode:
    /// anchors`' currency prefix and shared_scale magnitude suffix only
    /// ever anchored on the WHOLE TABLE's row 0 — pages 2+ lost the `$`/`M`
    /// entirely. Production's `_render_data_rows` computes
    /// `shared_scale_anchor_row`/`row_idx == 0` on `page_visible_data`, one
    /// page at a time; this pins
    /// the port's per-page `build_cell_data` recompute in
    /// `static_multi_page` does the same. 7 rows at page_rows=3: a real
    /// 3-page table (3, 3, 1), every row a 7-digit dollar value so the
    /// whole column shares a millions-tier `shared_scale` (exponent 6).
    #[test]
    fn static_multi_page_anchors_currency_and_magnitude_per_page_not_globally() {
        let rows: Vec<Value> = [1_200_000.0, 2_400_000.0, 3_100_000.0, 1_800_000.0, 2_900_000.0, 4_200_000.0, 5_600_000.0]
            .iter()
            .enumerate()
            .map(|(i, v)| json!({"week": format!("Week {i}"), "revenue": v}))
            .collect();
        let columns = json!({
            "week": {"label": "Week"},
            "revenue": {
                "format": "currency",
                "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
            },
        });
        let mut theme = base_table_theme();
        theme["pagination"]["page_rows"] = json!(3);
        theme["symbol_mode"] = json!("anchors");
        let resolved = json!({
            "link_open": "page",
            "id": "t3",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 800.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("3-page table renders");

        let page1 = svg.split("data-page=\"1\"").nth(1).unwrap().split("data-page=\"2\"").next().unwrap();
        let page2 = svg.split("data-page=\"2\"").nth(1).unwrap().split("data-page=\"3\"").next().unwrap();
        let page3 = svg.split("data-page=\"3\"").nth(1).unwrap();
        for (name, page) in [("page 1", page1), ("page 2", page2), ("page 3", page3)] {
            assert!(page.contains('$'), "{name}'s own row 0 anchors the $ prefix:\n{page}");
            assert!(page.contains('M'), "{name}'s own row 0 anchors the M magnitude suffix:\n{page}");
        }
    }

    /// The y attribute immediately preceding `marker`'s first occurrence —
    /// finds the enclosing `<text ... y="Y">marker`.
    fn text_y_before<'a>(svg: &'a str, marker: &str) -> &'a str {
        let marker_pos = svg.find(marker).unwrap_or_else(|| panic!("{marker:?} not found in:\n{svg}"));
        let head = &svg[..marker_pos];
        // A leading space distinguishes the `y="..."` attribute from the
        // `y` inside `font-family="..."`, which also ends in `y="`.
        let y_attr = head.rfind(" y=\"").unwrap_or_else(|| panic!("no y=\" before {marker:?} in:\n{svg}"));
        let after = &head[y_attr + 4..];
        &after[..after.find('"').unwrap()]
    }

    /// Edge-to-edge gaps between consecutive pager glyphs of a live render,
    /// measured in the family and size the glyphs were painted with.
    fn pager_edge_gaps(page_count: usize) -> Vec<(String, String, f64)> {
        let rows: Vec<Value> = (0..page_count * 3)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({"company": {"label": "Company"}, "revenue": {"label": "Revenue"}});
        let mut payload = make_paginated_payload_with_id("t1", columns, rows, 3, 800.0);
        payload.interactive_controls = true;
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        let attr = |t: &str, name: &str| -> String {
            format!(" {t}").split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        let glyphs: Vec<(String, f64, f64)> = svg
            .split("<text ")
            .filter(|t| t.contains("dbt-paginator-glyph"))
            .map(|t| {
                let glyph = t.split('>').nth(1).unwrap().split("</text").next().unwrap();
                let w = fonts
                    .measure(
                        glyph,
                        measure::stand_in_family(&attr(t, "font-family")),
                        attr(t, "font-size").parse().unwrap(),
                    )
                    .unwrap();
                (glyph.to_string(), attr(t, "x").parse().unwrap(), w)
            })
            .collect();
        glyphs
            .windows(2)
            .map(|w| (w[0].0.clone(), w[1].0.clone(), w[1].1 - w[0].1 - w[0].2 / 2.0 - w[1].2 / 2.0))
            .collect()
    }

    // Smallest edge-to-edge gap of a two-digit pager (`… 30`),
    // measured in the painted family and size.
    const MAIN_MIN_EDGE_GAP: f64 = 4.98;

    // A three-digit page count keeps every gap at least the tightest a
    // two-digit pager has, and the ellipsis sits evenly between its
    // neighbors (x is printed to 0.1, hence the tolerance).
    #[test]
    fn pager_gaps_survive_a_three_digit_page_count() {
        for page_count in [30, 300] {
            let gaps = pager_edge_gaps(page_count);
            for (a, b, gap) in &gaps {
                assert!(*gap >= MAIN_MIN_EDGE_GAP - 0.15, "{page_count} pages, {a} {b}: {gap}");
            }
            let before = gaps.iter().find(|g| g.1 == "\u{2026}").unwrap().2;
            let after = gaps.iter().find(|g| g.0 == "\u{2026}").unwrap().2;
            assert!((before - after).abs() < 0.15, "{page_count} pages: {before} vs {after}");
        }
    }

    // Overflow with no pager (no chart id to name the page variable): the rows that did not fit collapse to "+N".
    #[test]
    fn truncated_unpaginated_table_paints_a_bare_plus_count() {
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({"company": {"label": "Company"}, "revenue": {"label": "Revenue"}});
        let mut payload = make_payload(columns, rows, None, 20);
        payload.resolved["id"] = json!("");
        payload.resolved["style"]["table"]["more_rows"] =
            json!({"font": {"size": 11.0, "color": "#626366", "style": "italic"}});
        payload.height = 300.0;
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains("font-style=\"italic\""), "overflow note painted:\n{svg}");
        assert!(svg.contains("fill=\"#626366\""), "the note takes its own slot's ink:\n{svg}");
        assert!(!svg.contains("more rows"), "no English overflow note:\n{svg}");
        assert!(svg.contains(">+25</text>"), "bare +N:\n{svg}");
    }

    // Unbounded grow-by-2 (production's no-height path): an
    // overflow of at most `PAGINATION_GROW_CAP` rows past page_rows draws
    // every row at its natural height, no squeeze and no paginator; one more
    // row paginates at page_rows.
    #[test]
    fn unbounded_small_overflow_grows_instead_of_paginating() {
        let render_n = |n: usize| {
            let rows: Vec<Value> = (0..n)
                .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
                .collect();
            let columns = json!({
                "company": {"label": "Company"},
                "revenue": {"label": "Revenue"},
            });
            let mut payload = live(make_payload(columns, rows, None, 20));
            payload.height = 0.0;
            if let Value::Object(th) = &mut payload.resolved["style"]["table"] {
                th.insert("wrap".into(), json!(false));
            }
            render(&payload, &test_fonts()).expect("renders")
        };
        let grown = render_n(22);
        assert!(grown.contains("Row 21"), "every row renders:\n{grown}");
        assert!(!grown.contains("dbt-paginator"), "no paginator within the cap");
        // title 31 + header 39 + gap floor(32*0.25)=8 + 22 natural 32px rows.
        assert!(grown.contains("height=\"782\""), "natural row pitch:\n{grown}");

        let paged = render_n(23);
        assert!(paged.contains("dbt-paginator"), "past the cap paginates");
        assert!(!paged.contains(">Row 20</text>"), "page 1 holds page_rows rows");
    }

    // `page_rows: null` is unlimited: unbounded, every row draws; in a slot,
    // the slot alone decides the page.
    #[test]
    fn null_page_rows_lets_the_slot_decide_the_page() {
        let rows: Vec<Value> = (0..40)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut payload = live(make_payload(columns, rows, None, 20));
        payload.resolved["style"]["table"]["pagination"]["page_rows"] = Value::Null;
        payload.height = 0.0;
        let unbounded = render(&payload, &test_fonts()).expect("renders");
        assert!(unbounded.contains(">Row 39</text>"), "every row draws:\n{unbounded}");
        assert!(!unbounded.contains("dbt-paginator"), "no paginator unbounded");

        payload.height = 400.0;
        let slotted = render(&payload, &test_fonts()).expect("renders");
        assert!(slotted.contains("dbt-paginator"), "the slot paginates");
        assert!(slotted.contains(">Row 0</text>"), "page 1 draws:\n{slotted}");
        assert!(!slotted.contains(">Row 20</text>"), "past page_rows' old 20 is cut");
    }

    // Bounded wrap-on pagination sizes the canvas for the TALLEST page
    // (production's `_max_page_sum`): every page shares one
    // canvas, so two renders of the same row multiset (tall row on page 1 vs
    // page 2) must come out the same height.
    #[test]
    fn bounded_pagination_anchors_controls_below_the_tallest_page() {
        let tall = json!({"company": "Apex Technologies", "revenue": 24_730_000.12});
        let short = |n: usize| json!({"company": format!("R{n}"), "revenue": 8_460_000.0});
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency"},
        });
        let render_with = |rows: Vec<Value>| -> String {
            let mut payload = on_a_board(make_payload(columns.clone(), rows, None, 3));
            // Narrow enough that "Apex Technologies" wraps (same shape as
            // long_text_cell_wraps_and_grows_its_row); 6 rows > page_rows+2
            // keeps the grow-cap short-circuit off, so the slot paginates.
            payload.width = 190.0;
            payload.height = 400.0;
            render(&payload, &test_fonts()).expect("bounded wrap-on pagination renders")
        };
        let tall_on_page_1 = render_with(vec![
            tall.clone(), short(1), short(2), short(3), short(4), short(5),
        ]);
        assert!(
            tall_on_page_1.contains(">Apex</tspan>"),
            "the page-1 tall row must actually wrap for this pin to apply:\n{tall_on_page_1}"
        );
        let tall_on_page_2 = render_with(vec![
            short(0), short(1), short(2), short(3), tall.clone(), short(5),
        ]);
        assert!(
            tall_on_page_2.contains("dbt-paginator") && tall_on_page_1.contains("dbt-paginator"),
            "both renders paginate"
        );
        let canvas_height = |svg: &str| -> f64 {
            svg.split(" height=\"").nth(1).and_then(|s| s.split('"').next()).and_then(|s| s.parse().ok()).expect("height")
        };
        let (h1, h2) = (canvas_height(&tall_on_page_1), canvas_height(&tall_on_page_2));
        assert!(
            (h1 - h2).abs() < 0.01,
            "the canvas fits the tallest page in both renders (tall-on-page-1 h={h1}, tall-on-page-2 h={h2})"
        );
    }

    // `_largest_safe_page_rows` probes EVERY page-sized window — sizing off
    // the leading rows alone would let a short page 1 pick a size that
    // overflows a later, taller page (the bug a shared page budget causes).
    #[test]
    fn largest_safe_page_rows_checks_every_window_not_just_the_leading_rows() {
        let heights = vec![20.0, 20.0, 30.0, 30.0];
        // Size 2's first window (40) fits 50, but window [30, 30] = 60 does
        // not — only size 1 is safe.
        assert_eq!(largest_safe_page_rows(&heights, 50.0, 4), 1);
        // At 60 both size-2 windows fit; size 3's leading window (70) does not.
        assert_eq!(largest_safe_page_rows(&heights, 60.0, 4), 2);
    }

    // The control bar anchors below the TALLEST page's rows.
    #[test]
    fn max_page_sum_returns_the_tallest_window() {
        let heights = vec![20.0, 20.0, 30.0, 30.0];
        assert_eq!(max_page_sum(&heights, 2), 60.0);
        assert_eq!(max_page_sum(&heights, 3), 70.0);
    }

    // Bounded slot, data overflows it (production `_resolve_visible_rows`'s
    // height branch): the SVG height pins to the slot
    // (`table_height = height` — the slot already budgeted the control
    // band), page 1 slices at page_rows, and the control bar renders.
    // 30 rows × 32px = 960 > available 917, so the grow-by-2 short-circuit
    // must not fire (30 > page_rows + 2).
    #[test]
    fn bounded_slot_paginates_and_pins_svg_height_to_the_slot() {
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut payload = live(make_payload(columns, rows, None, 20));
        payload.height = 1000.0;
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("bounded pagination renders");
        assert!(
            svg.contains("height=\"1000\""),
            "svg height must echo the slot, not shrink to content:\n{svg}"
        );
        assert!(svg.contains("Row 0") && svg.contains("Row 19"), "page 1 renders in full");
        assert!(!svg.contains("Row 20"), "page 2 rows must not paint");
        assert!(svg.contains("dbt-paginator"), "control bar present");
    }

    // Grow-by-2 short-circuit (`_PAGINATION_GROW_CAP`):
    // when every row fits the slot at its natural height AND the overflow
    // past page_rows is ≤ 2, all rows render on one page at NATURAL height
    // (no anti-dangle squeeze — wrap is on, so production never squeezes
    // here) and the SVG shrinks to content, not the slot. Content height:
    // title 31 + header 32 + gap 8 + 22×32 = 775.
    #[test]
    fn bounded_grow_cap_overflow_renders_all_rows_at_content_height() {
        let rows: Vec<Value> = (0..22)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut payload = make_payload(columns, rows, None, 20);
        payload.height = 1000.0;
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("grow-cap renders one page");
        assert!(svg.contains("Row 21"), "every row (incl. the 22nd) renders:\n{svg}");
        assert!(!svg.contains("dbt-paginator"), "no paginator inside the grow cap");
        assert!(
            svg.contains("height=\"782\""),
            "rows keep natural 32px height (no squeeze) and the canvas shrinks \
             to content (title 31 + header 39 + gap 8 + 22 rows), not the 1000px \
             slot:\n{svg}"
        );
    }

    // The slot, not page_rows, decides the page size:
    // a 400px slot fits 8 natural-height rows after reserving the 28px
    // control band (available 310 − 28 = 282; 8×32 = 256 < 282 < 9×32), so
    // page 1 shows 8 rows — not the theme's 20 — and the page count grows to
    // match.
    #[test]
    fn bounded_short_slot_slices_rows_to_fit_with_control_reservation() {
        let rows: Vec<Value> = (0..25)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let payload = live(make_payload(columns, rows, None, 20));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("short-slot pagination renders");
        assert!(svg.contains("height=\"400\""), "svg height echoes the slot:\n{svg}");
        assert!(svg.contains("Row 7"), "8th row still on page 1");
        assert!(!svg.contains("Row 8"), "9th row must fall to page 2:\n{svg}");
        assert!(svg.contains("dbt-paginator"), "control bar present");
    }

    // Subtitle geometry: the subtitle band's height
    // floors on `style.table.title_row.height`, and the header shifts down
    // by the resulting title+subtitle band plus the header/body gap
    // (`int(row_height * 0.25)`) is whitespace after the header, before the
    // first data row — the header itself is never shifted by it. At
    // row.height=32 the subtitle-bottom term (43.5) wins the max and IS the
    // band; the rule then sits at band + header_h - rule_width. Deleting the
    // subtitle-height term collapses the band to the title-only height
    // (31.0 = font_size 18 + 13) — this test fails.
    #[test]
    fn subtitle_reserves_band_below_title_and_shifts_header() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut theme = base_table_theme();
        theme["title_row"] = json!({"height": 20.0});
        theme["title_subtitle_gap"] = json!(3.3);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "subtitle": "A subtitle line",
            "title_style": {
                "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload =
            crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders with subtitle");

        assert!(svg.contains("A subtitle line"), "subtitle text must render:\n{svg}");

        let rule_y = svg
            .split("shape-rendering=\"crispEdges\"")
            .next()
            .and_then(|s| s.rsplit("y=\"").next())
            .and_then(|s| s.split('"').next())
            .expect("header rule y attribute present");
        let rule_y: f64 = rule_y.parse().expect("numeric rule y");
        // title_area_h(43: the subtitle-bottom arm 43.5 winning over
        // title_row.height=20.0 and the title-only 31.0, then truncated to
        // int — production's `int(subtitle_bottom - padding + 8)` at
        // truncates every arm of the max) + header_h(39.0)
        // - rule_width(1.0), production's own placement.
        assert!(
            (rule_y - 81.0).abs() < 0.01,
            "expected header rule at 81.0 (title+subtitle band = int(43.5) = 43), got {rule_y}:\n{svg}"
        );
    }

    /// `compute_table_title_block_layout`: a title or subtitle wider than the
    /// table minus both paddings wraps by its overflow mode, one tspan per
    /// line `font_size + 2` apart, and the band grows by the extra lines. The
    /// full text rides in an inner `<title>` when text was cut.
    #[test]
    fn a_long_title_and_subtitle_wrap_by_their_overflow_modes() {
        let long_title = "Quarterly revenue by region and product family for every market";
        let long_sub = "A subtitle that keeps going and going well past the width of this small table so wrap-two must cut it with an ellipsis at the end of line two";
        let rows = vec![json!({"company": "Acme"})];
        let mut theme = base_table_theme();
        theme["title_row"] = json!({"height": 20.0});
        let payload = |title_overflow: &str| -> Payload {
            crate::test_payload(json!({
                "resolved": {
                    "link_open": "page",
                    "title": long_title,
                    "subtitle": long_sub,
                    "title_style": {
                        "font": {"family": "Inter", "size": 18.0, "color": "#494A4C", "weight": "500"},
                        "overflow": title_overflow,
                        "subtitle": {
                            "font": {"family": "Inter", "size": 11.0, "color": "#8A8C8F"},
                            "overflow": "wrap-two",
                        },
                    },
                    "columns": {"company": {"label": "Company"}},
                    "style": {"table": theme, "title_font": resolved_title_font()},
                    "pivot_columns": null,
                    "conditional_formatting": null,
                },
                "rows": rows, "width": 300.0, "height": 400.0,
                "background": "#ffffff", "fonts_dir": null, "padding": null,
            }))
        };
        let fonts = test_fonts();
        let svg = render(&payload("wrap-two"), &fonts).expect("renders");
        let texts: Vec<&str> = svg.split("<text ").skip(1).collect();
        let title = texts[0];
        let sub = texts[1];
        assert_eq!(title.matches("<tspan").count(), 2, "{svg}");
        // The inner <title> carries the authored text, never the cased one.
        assert!(title.contains(&format!("<title>{long_title}</title>")), "{svg}");
        let ys = |t: &str| -> Vec<f64> {
            t.split("<tspan").skip(1).map(|s| s.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()).collect()
        };
        let ty = ys(title);
        assert_eq!(ty[1] - ty[0], 20.0, "title line height is int(size) + 2");
        let sy = ys(sub);
        assert_eq!(sy.len(), 2, "{svg}");
        assert_eq!(sy[1] - sy[0], 13.0, "subtitle line height is size + 2");
        assert!(sub.contains("\u{2026}</tspan>"), "wrap-two ends in an ellipsis:\n{sub}");
        let title_tspans = |mode: &str| {
            let svg = render(&payload(mode), &fonts).expect("renders");
            let title = svg.split("<text ").nth(1).unwrap().to_string();
            (title.matches("<tspan").count(), svg)
        };
        let (n, truncated) = title_tspans("truncate");
        assert_eq!(n, 1);
        assert!(truncated.contains("\u{2026}</tspan>"));
        let (n, clipped) = title_tspans("clip");
        assert_eq!(n, 1);
        assert!(!clipped.split("<text ").nth(1).unwrap().contains('\u{2026}'), "clip never adds an ellipsis");
        let (n, wrapped) = title_tspans("wrap");
        assert!(n >= 2, "wrap never cuts: {wrapped}");
        assert!(!wrapped.split("<text ").nth(1).unwrap().contains('\u{2026}'));
    }

    // Production routes a NULL cell through the per-CELL gate
    // `_is_numeric_cell(value, col_config)`: a formatless
    // None fails it and takes the non-lane branch, which places it by the
    // column's RESOLVED `align` field — None for a
    // plain numeric column, so its dash paints start-anchored at content
    // left in the BODY font; only an align-right column's null lands on
    // the lane (pinned separately for the date lane below). A null in a
    // column WITH an explicit `format:` stays on the tabular lane path.
    #[test]
    fn null_cells_follow_productions_per_cell_gate() {
        let rows = vec![
            json!({"company": "Acme", "plain": 12.0, "formatted": null, "void": null}),
            json!({"company": "Globex", "plain": null, "formatted": 8.0, "void": null}),
        ];
        let columns = json!({
            "company": {},
            "plain": {},
            "formatted": {"format": "currency_whole"},
            "void": {},
        });
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // Unformatted lane column with NO authored align: production's
        // non-lane branch reads the resolved `align` field, which
        // fill_table_column_defaults leaves None for a plain numeric
        // column — so the dash paints START-anchored at content left in
        // the body font (verified against a live production render: the
        // column's number at x=266.2, its null at x=202.5 start-anchored).
        assert!(
            svg.contains("text-anchor=\"start\" font-family=\"Inter, sans-serif\" data-col=\"1\">\u{2014}</text>"),
            "the plain column's null paints start-anchored, body font:\n{svg}"
        );
        assert!(
            !svg.contains("text-anchor=\"end\" font-family=\"Inter, sans-serif\" data-col=\"1\">\u{2014}</text>"),
            "no body-font null END-anchors here — none of these columns is align: right:\n{svg}"
        );
        // Formatted column: the null stays on the tabular lane path.
        assert!(
            svg.contains("text-anchor=\"end\">\u{2014}</tspan>"),
            "the formatted column's null stays on the tabular lane:\n{svg}"
        );
    }

    // A null in a DATE column follows the same non-lane branch: the date
    // lane requires align "right" ( sends the dash to the
    // lane's number_x, end-anchored, body font) — it must never drop to the
    // column's left edge.
    #[test]
    fn null_date_cell_anchors_on_the_date_lane() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = vec![
            json!({"company": "Acme", "when": "2024-03-15"}),
            json!({"company": "Globex", "when": null}),
        ];
        let columns = json!({
            "company": {},
            "when": {"align": "right"},
        });
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("15 Mar 2024"), "date row formats:\n{svg}");
        assert!(
            svg.contains("text-anchor=\"end\" font-family=\"Inter, sans-serif\" data-col=\"1\">\u{2014}</text>"),
            "the date column's null end-anchors on the lane in the body font:\n{svg}"
        );
        assert!(
            !svg.contains("text-anchor=\"start\" font-family=\"Inter, sans-serif\" data-col=\"1\">\u{2014}</text>"),
            "the date column's null must not fall to the column's left edge:\n{svg}"
        );
    }

    // An unformatted numeric cell below 1 paints plain digits in one run,
    // never d3's milli prefix (`sub_unit_spec_for_value`).
    #[test]
    fn a_fraction_paints_plain_digits_in_one_run() {
        let rows = vec![
            json!({"country": "Pakistan", "growth": 0.94}),
            json!({"country": "China", "growth": 2.24}),
        ];
        let columns = json!({
            "country": {},
            "growth": {},
        });
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains(">0.94<"),
            "a fraction paints as plain digits:\n{svg}"
        );
        assert!(
            !svg.contains("940m"),
            "no milli prefix below 1:\n{svg}"
        );
    }

    // Default header labels are production's two-step pipeline: slug_to_text
    // (tokenize, abbreviation table, unit suffix — NO case transform) then
    // apply_case(text, header.font.case) — so the shipped `case: title`
    // keeps minor words lowercase ("Avg Days to Complete", the exact header
    // examples/interactive_0's documents_table paints) and the abbreviation
    // table matches production's (`avg` → "Avg" comes from the table, not
    // from title-casing).
    #[test]
    fn default_header_labels_case_like_production() {
        let rows = vec![json!({"avg_days_to_complete": 3.4, "nps": 42.0})];
        let columns = json!({
            "avg_days_to_complete": {},
            "nps": {},
        });
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains("Avg Days to Complete"),
            "title-cased default header keeps the minor word lowercase:\n{svg}"
        );
        assert!(
            svg.contains("NPS"),
            "production's abbreviation table maps nps -> NPS:\n{svg}"
        );
    }

    // The AUTHORED label is cased too — production's `display_name = (label
    // or slug_to_text(col))` runs through the same apply_case
    // An authored lowercase label is not exempt.
    #[test]
    fn authored_header_label_is_cased_like_a_default_one() {
        let rows = vec![json!({"a": 1.0})];
        let columns = json!({
            "a": {"label": "days to close"},
        });
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains("Days to Close"),
            "authored lowercase label takes the header font case:\n{svg}"
        );
    }

    // Production cases the table title through `apply_case(title,
    // title_style.font.case)` BEFORE measuring/painting
    // (compute_table_title_block_layout) — the shipped
    // themes set `case: title`, so an authored lowercase title paints
    // title-cased with minor words kept lowercase ("Revenue by Region").
    #[test]
    fn table_title_applies_title_style_font_case() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let resolved = json!({
            "link_open": "page",
            "title": "revenue by region",
            "title_style": {
                "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500", "case": "title"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("cased title renders");
        assert!(
            svg.contains("Revenue by Region"),
            "title must paint title-cased with the minor word kept lowercase:\n{svg}"
        );
        assert!(
            !svg.contains("revenue by region"),
            "authored lowercase title must not paint verbatim:\n{svg}"
        );
    }

    // The subtitle is deliberately NOT cased: production's
    // compute_table_title_block_layout applies apply_case to chart_title
    // only — the subtitle goes through prepare_title_text
    // verbatim, whatever `title_style.font.case` says.
    #[test]
    fn table_subtitle_stays_authored_when_title_case_is_set() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let resolved = json!({
            "link_open": "page",
            "title": "revenue by region",
            "subtitle": "a lowercase subtitle line",
            "title_style": {
                "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500", "case": "title"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("subtitled cased-title table renders");
        assert!(svg.contains("Revenue by Region"), "title is cased:\n{svg}");
        assert!(
            svg.contains("a lowercase subtitle line"),
            "subtitle must paint verbatim, never cased:\n{svg}"
        );
    }

    /// Pins the header-label baseline itself: y = title band + header_h -
    /// text_baseline_offset. The theme supplies a
    /// non-default offset (6.0) so reverting to any fixed constant (the old
    /// -10.0, or the default 4.0) moves the label and fails this.
    #[test]
    fn header_text_baseline_derives_from_text_baseline_offset() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut theme = base_table_theme();
        theme["text_baseline_offset"] = json!(6.0);
        theme["header"]["height"] = json!(50.0);
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // No title: title band = 0, header_h = 50.0 (theme, above the 41.0
        // one-line floor), so the header label baseline sits at 50.0 - 6.0 =
        // 44.0.
        let header_y = svg
            .split(">Company<")
            .next()
            .and_then(|f| f.rsplit(" y=\"").next())
            .and_then(|f| f.split('"').next())
            .and_then(|v| v.parse::<f64>().ok())
            .expect("header label y");
        assert!(
            (header_y - 44.0).abs() < 0.01,
            "header baseline must be header_h - text_baseline_offset = 44.0, got {header_y}:\n{svg}"
        );
    }

    // T1: production writes every SVG coordinate as Python's own
    // `str(float)`: a whole number keeps `.0`, float noise prints in
    // exponent form, an exact tie rounds half-even. A `{:.1}` emission — table.rs's pre-fix behavior throughout — rounds
    // every coordinate to 1dp; the reference-ablation in the research ledger
    // measured that alone as most of three CLOSE table rows (0.525 /
    // 0.199 / 0.482 pp of `precision_right` / `type_compact` / `conversion`).
    #[test]
    fn a_float_coordinate_prints_as_python_str() {
        let cases = [
            (61.0, "61.0"),
            (177.56114144070807, "177.56114144070807"),
            (236.79999999999998, "236.79999999999998"),
            (0.1 + 0.2 - 0.3, "5.551115123125783e-17"),
            (718102875569663.25, "718102875569663.2"),
        ];
        for (v, expected) in cases {
            assert_eq!(PyNum::Float(v).text(), expected, "value {v:e}");
        }
    }

    // T3: title ascent ratio — title baseline =
    // `round(font_size * 0.8)`, fitted against Vega-Lite's own title
    // baseline at all three width-tiered sizes production can pick
    // (11/14/18px). table.rs used an empirical `font_size * 0.78` instead
    // (exact only at 18px; 0.1px off at 14px, 0.4px off at 11px).
    fn title_svg_at_font_size(size: f64) -> String {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut theme = base_table_theme();
        theme["outer_padding"] = json!(0.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "subtitle": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": {"size": size, "family": "serif", "weight": "500"}},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        render(&payload, &fonts).expect("renders")
    }

    #[test]
    fn title_baseline_is_round_of_font_size_times_point_eight() {
        let svg = title_svg_at_font_size(14.0);
        // round(14 * 0.8) = round(11.2) = 11 -> "11.0" (a genuine float,
        // always carrying its decimal point per T1).
        assert!(
            svg.contains("y=\"11.0\""),
            "title baseline must be round(font_size * 0.8) = 11.0, not the \
             empirical 0.78 ratio's 10.9:\n{svg}"
        );
        assert!(!svg.contains("y=\"10.9\""), "must not fall back to the 0.78 empirical ratio:\n{svg}");
    }

    // 11px is where the two ratios diverge most (0.4px, vs 0.1px at 14px).
    #[test]
    fn title_baseline_is_round_of_font_size_times_point_eight_at_11px() {
        let svg = title_svg_at_font_size(11.0);
        // round(11 * 0.8) = round(8.8) = 9 -> "9.0".
        assert!(
            svg.contains("y=\"9.0\""),
            "title baseline must be round(font_size * 0.8) = 9.0, not the \
             empirical 0.78 ratio's 8.58 (\"8.6\"):\n{svg}"
        );
        assert!(!svg.contains("y=\"8.6\""), "must not fall back to the 0.78 empirical ratio:\n{svg}");
    }

    // `title_band.py:116-132`: ascent = int(outer_padding) + round(0.8 * size) and the block
    // runs floor(baseline + size / 2 + 8) from the band's top. At 24px with outer padding 12:
    // baseline round(19.2) = 19, block floor(19 + 12 + 8) = 39, so (12 + 19, 39 - 19).
    #[test]
    fn title_geometry_is_the_bands_ascent_and_the_blocks_remainder() {
        let mut theme = base_table_theme();
        theme["outer_padding"] = json!(12.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "subtitle": null,
            "title_style": resolved_title_style(),
            "style": {"table": theme, "title_font": {"size": 24.0, "family": "serif", "weight": "500"}},
        });
        assert_eq!(title_geometry(&resolved, 600.0, &test_fonts()).unwrap(), (31.0, 20.0));
    }

    // The title band reads only theme-supplied slots: production asserts or
    // requires each (`table.py:352-355`, `:4384-4393`, `:4443-4447`), so a
    // payload missing one refuses by name rather than painting a guessed font.
    fn themed_resolved() -> Value {
        let mut theme = base_table_theme();
        theme["title_row"] = json!({"height": 20.0});
        theme["title_subtitle_gap"] = json!(3.3);
        json!({
            "title": "Test Table",
            "subtitle": "A subtitle line",
            "title_style": {
                "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": {"company": {"label": "Company"}, "revenue": {"label": "Revenue"}},
            "style": {
                "table": theme,
                "title_font": {"size": 18.0, "family": "serif", "weight": "500"}
            },
            "pivot_columns": null,
            "conditional_formatting": null,
        })
    }

    fn render_themed(resolved: Value) -> Result<String, Diagnostic> {
        let payload = crate::test_payload(json!({
            "resolved": resolved,
            "rows": [{"company": "Acme", "revenue": 100.0}],
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }));
        render(&payload, &test_fonts())
    }

    fn assert_theme_refusal(edit: impl FnOnce(&mut Value), expected: &str) {
        let mut resolved = themed_resolved();
        edit(&mut resolved);
        let message = render_themed(resolved).unwrap_err().to_string();
        assert_eq!(message, format!("table: {expected} absent (theme required)"));
    }

    #[test]
    fn a_fully_themed_title_band_renders() {
        let svg = render_themed(themed_resolved()).expect("renders");
        assert!(svg.contains("fill=\"#494A4C\""), "title ink:\n{svg}");
        assert!(svg.contains("fill=\"#8A8C8F\""), "subtitle ink:\n{svg}");
    }

    #[test]
    fn a_cut_title_or_subtitle_warns_title_truncated() {
        let cut = |field: &str, text: &str| {
            crate::diagnostic::Diagnostic::new(
                &crate::codes::WARN_CHART_TITLE_TRUNCATED,
                [
                    ("authored_field", json!(field)),
                    ("authored_text", json!(text)),
                    ("chart_id", json!("t")),
                ],
            )
        };
        let long = "word ".repeat(200);
        let warned = |title: &str, subtitle: &str| {
            let mut resolved = themed_resolved();
            resolved["id"] = json!("t");
            resolved["title"] = json!(title);
            resolved["subtitle"] = json!(subtitle);
            resolved["title_style"]["overflow"] = json!("truncate");
            crate::diagnostic::collect(|| render_themed(resolved)).1
        };
        assert_eq!(warned(&long, "short"), vec![cut("title", &long)]);
        assert_eq!(warned("Short", &long), vec![cut("subtitle", &long)]);
        assert!(warned("Short", "short").is_empty());
    }

    #[test]
    fn an_absent_title_font_refuses_by_name() {
        assert_theme_refusal(
            |r| { r["style"].as_object_mut().unwrap().remove("title_font"); },
            "style.title_font",
        );
    }

    #[test]
    fn an_absent_title_font_slot_refuses_by_name() {
        for slot in ["size", "family", "weight"] {
            assert_theme_refusal(
                |r| { r["style"]["title_font"].as_object_mut().unwrap().remove(slot); },
                &format!("style.title_font.{slot}"),
            );
        }
    }

    #[test]
    fn an_absent_title_ink_refuses_by_name() {
        assert_theme_refusal(
            |r| { r["title_style"]["font"].as_object_mut().unwrap().remove("color"); },
            "title_style.font.color",
        );
        assert_theme_refusal(|r| r["title_style"] = Value::Null, "title_style");
    }

    #[test]
    fn an_absent_subtitle_style_slot_refuses_by_name() {
        assert_theme_refusal(
            |r| {
                r["title_style"]["subtitle"]["font"].as_object_mut().unwrap().remove("color");
            },
            "title_style.subtitle.font.color",
        );
        let mut resolved = themed_resolved();
        resolved["title_style"]["subtitle"]["font"].as_object_mut().unwrap().remove("size");
        assert_eq!(
            render_themed(resolved).unwrap_err().to_string(),
            "table: theme must supply title.subtitle.font.size"
        );
        assert_theme_refusal(
            |r| r["title_style"]["subtitle"] = Value::Null,
            "title_style.subtitle",
        );
    }

    // `table.py:340-355`: the subtitle size is asserted only when a subtitle
    // paints, so a title-only table never reads it.
    #[test]
    fn the_subtitle_size_is_read_only_when_a_subtitle_paints() {
        let mut resolved = themed_resolved();
        resolved["subtitle"] = Value::Null;
        resolved["title_style"]["subtitle"]["font"].as_object_mut().unwrap().remove("size");
        render_themed(resolved).expect("renders");
    }

    #[test]
    fn title_geometry_refuses_an_absent_title_font_size() {
        let mut resolved = themed_resolved();
        resolved["style"]["title_font"].as_object_mut().unwrap().remove("size");
        let message = title_geometry(&resolved, 600.0, &test_fonts()).unwrap_err();
        assert_eq!(message, "table: style.title_font.size absent (theme required)");
    }

    // T2: production measures the numeric lane's "unsigned" text with
    // `num_str.removeprefix("-")` — ASCII hyphen-minus only.
    // Every numeric formatter in this repo (`_with_minus`, d3-format, d3rs's
    // MINUS) emits U+2212 MINUS SIGN for negatives, so that removeprefix
    // never actually matches real cell text: it's a no-op, and production's
    // measurement still counts the minus glyph's width. table.rs stripped
    // both `-` and U+2212, narrowing the lane and shifting the number left
    // of production's position (`type_compact`'s first numeric column sat
    // 0.21px left of the reference).
    #[test]
    fn numeric_lane_keeps_the_unicode_minus_in_the_unsigned_measurement() {
        let rows = vec![json!({"value": -8.55})];
        let columns = json!({"value": {"label": "Value", "format": ",.2f", "width": 50}});
        let theme = base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let outer = 12.0_f64; // base_table_theme's outer_padding
        let pad = 8.0_f64; // base_table_theme's column_layout.cell_padding
        let font_size = 11.0_f64;
        let with_minus = fonts.measure("\u{2212}8.55", TAB_FONT, font_size).unwrap();
        let without_minus = fonts.measure("8.55", TAB_FONT, font_size).unwrap();
        assert!(with_minus > without_minus, "the minus glyph must have nonzero width in this font");

        let (_, right_x, _) = numeric_lane_positions(outer, 50.0, pad, with_minus, 0.0, 0.0, false);
        let (_, wrong_x, _) = numeric_lane_positions(outer, 50.0, pad, without_minus, 0.0, 0.0, false);
        let right_s = float_repr(right_x);
        let wrong_s = float_repr(wrong_x);
        assert!(
            svg.contains(&format!("x=\"{right_s}\" text-anchor=\"end\"")),
            "number lane must measure the unsigned text WITH the U+2212 minus \
             still attached (production's removeprefix(\"-\") never matches it):\n{svg}"
        );
        assert!(
            !svg.contains(&format!("x=\"{wrong_s}\" text-anchor=\"end\"")),
            "number lane must not narrow to the minus-stripped width:\n{svg}"
        );
    }

    // T4: draws a muted "{a}–{b} / {N}" label at
    // `padding` in the pager control band whenever it fits left of the
    // right-anchored page sequence — `conversion`'s reference carries
    // `<text x="0.0" y="563.0">1–20 / 240</text>`. table.rs drew the
    // page sequence and no label at all.
    #[test]
    fn paginator_draws_the_row_range_label_when_it_fits() {
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"company": format!("Row {i}"), "revenue": i as f64}))
            .collect();
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let theme = base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "id": "t1",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        // Tall enough that the slot never constrains the page size below
        // `pagination.page_rows` (20) — the label reads the full page.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 1200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload = live(crate::test_payload(payload_json));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains("1\u{2013}20 / 30"),
            "a wide table must draw the row-range label left of the pager sequence:\n{svg}"
        );
    }

    #[test]
    fn paginator_drops_the_row_range_label_when_it_would_collide_with_the_sequence() {
        let rows: Vec<Value> = (0..30)
            .map(|i| json!({"a": format!("Row {i}")}))
            .collect();
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["pagination"]["page_rows"] = json!(20);
        let resolved = json!({
            "link_open": "page",
            "id": "t1",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        // A narrow table leaves little room left of the right-anchored
        // pager sequence for "1–20 / 30" to fit.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 130.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload = live(crate::test_payload(payload_json));
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("dbt-paginator"), "pager sequence still renders:\n{svg}");
        // " / 30", not the literal "1–20 / 30": the 400px slot caps this page
        // at 10 rows, so a deleted fit gate would draw "1–10 / 30", a
        // different string the narrower assertion would miss entirely.
        assert!(
            !svg.contains(" / 30"),
            "the row-range label must be dropped entirely when it would \
             collide with the pager sequence, not shrunk or overlapped:\n{svg}"
        );
    }

    // The label's fit gate measures with `get_font_measurer(font_family)`, which
    // is Inter for every non-serif family: a body painted in a wider font
    // keeps the label exactly where an Inter-measured label fits.
    #[test]
    fn paginator_label_fit_is_measured_in_inter_whatever_the_body_family() {
        let rows: Vec<Value> = (0..30).map(|i| json!({"a": format!("Row {i}")})).collect();
        let fonts = test_fonts();
        let label = "1\u{2013}20 / 30";
        let inter_w = fonts.measure(label, "Inter", 11.0).expect("measures");
        let code_w = fonts.measure(label, "Source Code Pro", 11.0).expect("measures");
        assert!(code_w > inter_w + 2.0, "premise: the two fonts must measure apart");
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["font"]["family"] = json!("Source Code Pro");
        let aux_slot = 20.0 * PAGINATOR_AUX_SLOT_RATIO;
        let pager_w = 2.0 * aux_slot + 2.0 * 20.0;
        // The label starts at the 12px outer padding and ends 1px short of the
        // pager under Inter's advances, 1px or more past it under the code font.
        let width = 12.0 + inter_w + pager_w + 1.0;
        let payload_json = json!({
            "resolved": {
                "link_open": "page",
                "id": "t1",
                "title": null,
            "title_style": resolved_title_style(),
                "columns": {"a": {"label": "A"}},
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": rows,
            "width": width,
            "height": 1200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload = live(crate::test_payload(payload_json));
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(label), "the label fits under Inter's advances:\n{svg}");
    }

    // Title size/weight/family come from the width-tiered `style.title_font`
    // (production: `ResolvedTableStyle.title_font`, "baked title font from
    // the width tier"), not the untiered `title_style.font` — a narrow table
    // picks a smaller title tier than its theme base. Reading the wrong
    // source here also throws off the subtitle-band math above, since the
    // subtitle baseline derives from the title's own font size.
    #[test]
    fn title_font_size_comes_from_width_tiered_style_title_font_not_title_style() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let theme = base_table_theme();
        // Width-tiered field lives on `style`, a sibling of `style.table` —
        // set it to a value distinct from title_style.font.size (18.0) so a
        // read of the wrong source is visible in the rendered baseline.
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "subtitle": null,
            "title_style": {
                "font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": columns,
            "style": {"table": theme, "title_font": {"size": 11.0, "family": "serif", "weight": "500"}},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload =
            crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(
            svg.contains("font-size=\"11\""),
            "title must render at the width-tiered 11px size, not title_style's 18px:\n{svg}"
        );
        assert!(
            !svg.contains("font-size=\"18\""),
            "title must not fall back to the untiered title_style.font.size:\n{svg}"
        );
    }

    /// Regression: `title_row.height` is a real winning arm of the `max()`,
    /// not a corner case — pin a case where it dominates both the natural
    /// title-only height and the subtitle-bottom term. Deleting the
    /// `th.title_row.height` term from the max() must fail this test by
    /// shrinking the band back down to the subtitle-bottom raw term.
    #[test]
    fn title_row_height_floor_wins_the_max_when_it_dominates() {
        let rows = vec![json!({"company": "Acme", "revenue": 100.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let mut theme = base_table_theme();
        theme["title_row"] = json!({"height": 100.0});
        theme["title_subtitle_gap"] = json!(3.3);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "subtitle": "A subtitle line",
            "title_style": {
                "font": {"family": "serif", "size": 11.0, "color": "#494A4C", "weight": "500"},
                "subtitle": {
                    "font": {"family": "sans-serif", "size": 11.0, "color": "#8A8C8F"}
                }
            },
            "columns": columns,
            "style": {"table": theme, "title_font": {"size": 11.0, "family": "serif", "weight": "500"}},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload =
            crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders with dominant title_row.height");

        let rule_y = svg
            .split("shape-rendering=\"crispEdges\"")
            .next()
            .and_then(|s| s.rsplit("y=\"").next())
            .and_then(|s| s.split('"').next())
            .expect("header rule y attribute present");
        let rule_y: f64 = rule_y.parse().expect("numeric rule y");
        // title_area_h(100.0 = title_row.height, the winning arm over the
        // natural 24.0 and subtitle-bottom 36.5) + header_h(39.0) -
        // rule_width(1.0), production's own placement.
        assert!(
            (rule_y - 138.0).abs() < 0.01,
            "expected header rule at 138.0 (title_row.height-dominated band = 100.0), \
             got {rule_y}:\n{svg}"
        );
    }

    // ── visible: false ──────────────────────────────────────────────────────

    #[test]
    fn visible_false_hides_an_authored_column_entirely() {
        // Distinct from `explicit_columns_hide_unlisted_row_keys` above: here
        // the column IS authored in style.columns, just with visible: false —
        // the `visible is not False` filter, never wired up
        // in this renderer before.
        let rows = vec![
            json!({"company": "Acme", "revenue": 100.0, "nrr": 1.1}),
            json!({"company": "Globex", "revenue": 200.0, "nrr": 1.2}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
            "nrr": {"visible": false},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(!svg.contains(">NRR<") && !svg.contains(">Nrr<"), "hidden column's header must not render:\n{svg}");
        assert!(!svg.contains(">1.1<") && !svg.contains(">1.2<"), "hidden column's cells must not render:\n{svg}");
    }

    // ── shared_scale digit_spec + symbol_mode: anchors ──────────────────────

    #[test]
    fn shared_scale_uses_digit_spec_precision_not_decimal_pad_table() {
        // With an empty decimal_pad_table (production's usual
        // shape for a shared-scale column), the renderer used to derive
        // precision from pad_table.len() (== 0), rounding 24_730_000 scaled
        // to 24.73 down to "25" instead of respecting the column's own
        // digit_spec (",.2~f").
        let rows = vec![json!({"company": "Apex", "revenue": 24_730_000.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {
                "format": "currency",
                "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
            },
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("24.73"), "digit_spec precision must survive an empty pad table:\n{svg}");
        assert!(!svg.contains(">25<"), "must not fall back to zero-precision rounding:\n{svg}");
        assert!(svg.contains('$'), "currency's $ prefix must render on the anchor row:\n{svg}");
        assert!(svg.contains('M'), "shared_scale exponent 6 must render the M magnitude suffix:\n{svg}");
    }

    #[test]
    fn symbol_mode_anchors_limits_prefix_and_suffix_to_first_and_summary_rows() {
        let rows = vec![
            json!({"company": "Apex", "revenue": 24_730_000.0, "growth": 0.125, "row_role": "value"}),
            json!({"company": "Bright", "revenue": 8_460_000.0, "growth": 0.089, "row_role": "value"}),
            json!({"company": "Total", "revenue": 33_190_000.0, "growth": null, "row_role": "total"}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {
                "format": "currency",
                "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
            },
            "growth": {"format": "percent"},
        });
        let payload = make_payload_ext(columns, rows, Some("row_role"), 20, "anchors");
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // Row 0 (anchor) and the total row keep the $ / M / % symbols;
        // row 1 shows the bare number.
        assert!(svg.contains("24.73") && svg.contains('$') && svg.contains('M'), "anchor row keeps its symbols:\n{svg}");
        assert!(svg.contains("8.46"), "non-anchor row still shows its scaled number:\n{svg}");
        assert!(svg.contains("12.5") && svg.contains('%'), "anchor row's percent keeps its % suffix:\n{svg}");
        assert!(svg.contains("8.9"), "non-anchor row still shows its number:\n{svg}");
        assert!(svg.contains("33.19"), "total row (summary role) keeps its scaled number:\n{svg}");
        // The % suffix is its own tspan (`>8.9</tspan><tspan …>%</tspan>`), so
        // `"8.9%"` is never a substring regardless of suppression — count the
        // suffix tspans directly: only the anchor row's growth cell (12.5)
        // keeps one. The total row's growth is null (no suffix to keep or
        // drop), so exactly one `%` tspan must survive.
        assert_eq!(
            svg.matches(">%</tspan>").count(),
            1,
            "only the anchor row's % suffix should survive symbol_mode: anchors:\n{svg}"
        );
    }

    // A column format's `repeat` overrides the table's symbol_mode
    // (`column_symbol_mode`): `every` paints the symbol on every row under
    // `anchors`, `anchor` limits it to the first row under `all`.
    #[test]
    fn a_column_format_repeat_overrides_the_table_symbol_mode() {
        let rows = vec![
            json!({"company": "Apex", "revenue": 24.0}),
            json!({"company": "Bright", "revenue": 8.0}),
        ];
        let dollars = |table_mode: &str, repeat: &str| {
            let columns = json!({
                "company": {"label": "Company"},
                "revenue": {"format": {"spec": "currency", "repeat": repeat}},
            });
            let payload = make_payload_ext(columns, rows.clone(), None, 20, table_mode);
            let svg = render(&payload, &test_fonts()).expect("renders");
            svg.matches(">$</tspan>").count()
        };
        assert_eq!(dollars("anchors", "every"), 2);
        assert_eq!(dollars("all", "anchor"), 1);
    }

    /// An SI spec's magnitude letter is its own suffix and stays on every row
    /// under `anchors`; only the prefix is anchored.
    #[test]
    fn an_si_format_keeps_its_magnitude_suffix_on_every_row_under_anchors() {
        let rows = vec![
            json!({"company": "A", "revenue": 1_500_000.0}),
            json!({"company": "B", "revenue": 2_500_000.0}),
            json!({"company": "C", "revenue": 3_500_000.0}),
        ];
        let columns = json!({"company": {"label": "Company"}, "revenue": {"format": ".3~s"}});
        let payload = make_payload_ext(columns, rows, None, 20, "anchors");
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(svg.matches(">M</tspan>").count(), 3, "{svg}");
    }

    /// A REPEAT shared scale declares its magnitude on every non-zero row, in
    /// the narrative register.
    #[test]
    fn a_repeat_shared_scale_repeats_a_narrative_magnitude_on_every_nonzero_row() {
        let rows = vec![
            json!({"company": "A", "revenue": 1_500_000.0}),
            json!({"company": "B", "revenue": 0.0}),
            json!({"company": "C", "revenue": 3_500_000.0}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"shared_scale": {"exponent": 6, "digit_spec": ",.1~f", "mode": "repeat"}},
        });
        let payload = make_payload_ext(columns, rows, None, 20, "anchors");
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(svg.matches(">mn</tspan>").count(), 2, "{svg}");
    }

    #[test]
    fn symbol_mode_all_shows_symbols_on_every_row() {
        let rows = vec![
            json!({"company": "Apex", "revenue": 24_730_000.0}),
            json!({"company": "Bright", "revenue": 8_460_000.0}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {
                "format": "currency",
                "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
            },
        });
        let payload = make_payload_ext(columns, rows, None, 20, "all");
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("24.73") && svg.contains('$'), "row 0 shows the number with its currency prefix:\n{svg}");
        assert!(svg.matches('M').count() >= 2, "symbol_mode: all shows the M suffix on every row, not just the anchor:\n{svg}");
    }

    // Invariant #11's split-tspan symbol lanes: a currency prefix and a
    // percent suffix each render as their own tspan (never folded into the
    // number's text), and a magnitude suffix (K/M/B) sits `SUFFIX_GAP` off
    // the number while a unit suffix ("%") touches it directly — mirrors
    // The suffix_gap/prefix_gap split.
    #[test]
    fn currency_prefix_and_percent_suffix_render_as_split_tspans_with_correct_gaps() {
        let rows = vec![json!({"company": "Acme", "revenue": 24730.0, "growth": 0.125})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency_whole"},
            "growth": {"format": "percent"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Currency prefix: its own end-anchored tspan, never folded into the
        // number text (no "$24,730" substring — only split tspans).
        assert!(!svg.contains("$24,730"), "prefix must not fold into the number's text:\n{svg}");
        assert!(svg.contains(">$</tspan><tspan"), "prefix is its own leading tspan:\n{svg}");
        assert!(svg.contains(">24,730</tspan>"), "number renders as a bare tspan (no prefix/suffix folded in):\n{svg}");

        // Percent suffix: its own start-anchored tspan sitting flush against
        // the number (suffix_gap == 0 for a non-magnitude unit suffix).
        let re_pct = regex_extract_suffix_x(&svg, "12.5");
        let re_num_end = regex_extract_number_end_x(&svg, "12.5");
        assert_eq!(re_pct, re_num_end, "percent suffix touches the number (no gap):\n{svg}");
    }


    // Authored column width: an explicit `width:` pin must be honored
    // exactly, even when the shrink/grow pass would otherwise widen the
    // column to absorb surplus. `a` pins to 50px; `b` is the sole auto
    // column, so it absorbs 100% of `available_for_auto` (single-auto-column
    // surplus branch collapses to `w + surplus == available_for_auto`
    // regardless of `b`'s own content demand) — `col_x[b] == outer + 50`
    // is deterministic and independent of font metrics.
    #[test]
    fn authored_column_width_pins_exact_pixel_width() {
        let rows = vec![json!({"a": "x", "b": "y"})];
        let columns = json!({
            "a": {"label": "A", "width": 50},
            "b": {"label": "B"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // outer_padding=12.0 (base theme); column b starts exactly at
        // outer + pinned_width(a)=50, its header text at + cell_padding(8).
        let expected_b_text_x = 12.0 + 50.0 + 8.0;
        let b_text_x: f64 = svg
            .split(">B</text>")
            .next()
            .and_then(|head| head.rsplit("<text x=\"").next())
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("B header text x parses");
        assert!(
            (b_text_x - expected_b_text_x).abs() < 0.15,
            "column b must start immediately after a's pinned 50px width \
             (expected {expected_b_text_x}, got {b_text_x}):\n{svg}"
        );
    }

    /// A `"NN%"` width pins to `pct/100 * available_width`. Single-auto-column absorption
    /// (see the sibling pixel-pin test) makes the auto column's start
    /// exactly `outer + 0.4*table_w`, independent of content.
    #[test]
    fn authored_column_width_percent_pins_relative_width() {
        let rows = vec![json!({"a": "x", "b": "y"})];
        let columns = json!({
            "a": {"label": "A", "width": "40%"},
            "b": {"label": "B"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let outer = 12.0_f64;
        let table_w = 300.0 - 2.0 * outer;
        let expected_b_text_x = outer + 0.4 * table_w + 8.0;
        let b_text_x: f64 = svg
            .split(">B</text>")
            .next()
            .and_then(|head| head.rsplit("<text x=\"").next())
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("B header text x parses");
        assert!(
            (b_text_x - expected_b_text_x).abs() < 0.15,
            "column b must start at outer + 40% of table_w \
             (expected {expected_b_text_x}, got {b_text_x}):\n{svg}"
        );
    }

    /// An unparseable width string is a hard error (validate-and-error-fast:
    /// a malformed authored width is a bug to surface, not paper over).
    #[test]
    fn malformed_column_width_is_a_hard_error() {
        let rows = vec![json!({"a": "x"})];
        let columns = json!({"a": {"label": "A", "width": "not-a-width"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let err = render(&payload, &fonts).expect_err("malformed width must error, not silently ignore").to_string();
        assert!(err.contains("width"), "error names the width problem: {err}");
    }

    /// A pixel `width:` pin exceeding the table's own available width keeps
    /// its pinned width — this is the ALL-COLUMNS-PINNED shape
    /// (`all_columns_pinned` in `render()`) — and the canvas stays at the
    /// slot with the body clipped to it, so the table never paints over a
    /// neighbor. 300px table, outer_padding=12 (base theme) -> table_w=276;
    /// a single `width: 500` column exceeds it.
    #[test]
    fn column_width_pin_exceeding_table_width_clips_to_the_slot() {
        let rows = vec![json!({"a": "x"})];
        let columns = json!({"a": {"label": "A", "width": 500}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("all-pinned overflow clips, not errors");
        assert!(svg.starts_with("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"300\""), "{svg}");
        assert!(svg.contains("clip-path=\"url(#table-clip-"), "{svg}");
    }

    /// Same contract, percent pins: three `"50%"` columns sum to 150% of
    /// the table width, still all-pinned (a `%` width is still an explicit
    /// pin, just resolved against `table_w` — `parse_column_width`), so the
    /// canvas stays at the slot and the body clips to it.
    #[test]
    fn column_width_percent_pins_summing_past_table_width_clips_to_the_slot() {
        let rows = vec![json!({"a": "x", "b": "y", "c": "z"})];
        let columns = json!({
            "a": {"label": "A", "width": "50%"},
            "b": {"label": "B", "width": "50%"},
            "c": {"label": "C", "width": "50%"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("all-pinned overflow clips, not errors");
        assert!(svg.starts_with("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"300\""), "{svg}");
        assert!(svg.contains("clip-path=\"url(#table-clip-"), "{svg}");
    }

    /// Production folds word floors into demands, classifies both columns
    /// compact (demand <= 220), and the all-compact over-subscription branch
    /// scales EVERY column below its own floor to fit — a deliberate
    /// production choice (mid-word clipping over canvas growth). Equal
    /// demands split the width equally; the render succeeds.
    #[test]
    fn two_unbreakable_word_floors_scale_down_like_production() {
        let long_token = "Supercalifragilisticexpialidociousmore"; // 39 chars, under the 40-char floor-token cap
        let rows = vec![json!({"a": long_token, "b": long_token})];
        let columns = json!({
            "a": {"label": "A"},
            "b": {"label": "B"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("over-subscribed table scales down and renders");
        // Equal demands -> equal halves of the content width; B's header
        // start sits exactly one column width after A's.
        let x_of = |needle: &str| -> f64 {
            let idx = svg.find(needle).expect(needle);
            svg[..idx]
                .rsplit("<text x=\"")
                .next()
                .and_then(|t| t.split('"').next())
                .and_then(|t| t.parse().ok())
                .expect("x parses")
        };
        let (xa, xb) = (x_of(">A</text>"), x_of(">B</text>"));
        let table_w = 300.0 - 2.0 * 12.0;
        assert!(
            (xb - xa - table_w / 2.0).abs() < 0.15,
            "equal over-subscribed columns split the width equally: xa={xa} xb={xb}"
        );
    }

    /// The word-floor token-length cap counts CHARACTERS (production's
    /// Table_support.py:519), not bytes. A 35-character accented token is 70
    /// UTF-8 bytes — over the 40-byte cap if measured by `.len()`, so a byte-counting bug excludes
    /// it from the floor scan (letting the shrink pass wrap it mid-word); the correct 35-character
    /// count keeps it under the cap and sets a floor. Two such unbreakable tokens, one per column,
    /// exceed the 300px table once the floor is honored.
    #[test]
    fn word_floor_token_cap_counts_characters_not_bytes() {
        let long_token = "É".repeat(35); // 35 chars, 70 UTF-8 bytes
        let rows = vec![json!({"a": long_token.clone(), "b": long_token})];
        let columns = json!({
            "a": {"label": "A"},
            "b": {"label": "B"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let (_, codes) = render_with_warnings(&payload);
        assert!(
            codes.contains(&"WARN-TABLE-COLUMNS-OVERFLOW"),
            "two unbreakable accented-character word floors summing past table_w overflow: {codes:?}"
        );
    }

    /// A header label's word floor must be measured at
    /// `style.table.header.font.size`, and `col_content_width`'s header measurement must use that
    /// same size — production passes `header_font_size` to both because "a theme that sets
    /// `table.header.font.size` above the body size would otherwise get a floor narrower than the
    /// header word it exists to keep whole" AND because an inconsistent pair (floor at header size,
    /// natural width at body size) can let the floor exceed the column's own natural width, which
    /// the redistribution loop then can only clamp *up* to, spuriously refusing a table that fits.
    /// Both columns author a two-word header ("A LongWordHereX", "B Column") so the natural width
    /// (the whole string) always exceeds the floor (the widest single token) once both are measured
    /// at the same size, leaving column `a` real headroom to shrink down to — but never below — its
    /// header-sized floor. `table_w` is derived from `fonts.measure` (not hardcoded) at the
    /// midpoint between "every column fully floor-clamped" and "no shrink needed" so the
    /// deficit-redistribution loop is exercised without pinning a font-metric-fragile boundary.
    #[test]
    fn header_word_floor_measures_at_the_header_font_size() {
        // Floors measure header tokens at the HEADER font size (60px here,
        // body 11px): both columns' floor-inflated demands overflow the
        // width, production grows the table, and this renderer clips it to
        // the slot. The warning proves the 60px measurement: at body
        // size the columns would fit and render.
        let fonts = test_fonts();
        let header_size = 60.0;
        let rows = vec![json!({"a": "x", "b": "y"})];
        let columns = json!({
            "a": {"label": "A LongWordHereX"},
            "b": {"label": "B Column"},
        });
        let mut theme = base_table_theme();
        theme["header"]["font"]["size"] = json!(header_size);
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 500.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let (_, codes) = render_with_warnings(&payload);
        assert!(
            codes.contains(&"WARN-TABLE-COLUMNS-OVERFLOW"),
            "60px header floors overflow the width: {codes:?}"
        );

        // Same table at body-size headers fits and renders — the refusal
        // above is the header-size measurement, not the content.
        let mut theme11 = base_table_theme();
        theme11["row"]["role"] = json!(null);
        let resolved11 = json!({
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": {
                "a": {"label": "A LongWordHereX"},
                "b": {"label": "B Column"},
            },
            "style": {"table": theme11, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload11_json = json!({
            "resolved": resolved11,
            "rows": [{"a": "x", "b": "y"}],
            "width": 500.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload11: Payload = crate::test_payload(payload11_json);
        render(&payload11, &fonts).expect("body-size headers fit");
    }



    /// A URL token must not set a min-word floor — it is excluded from the widest-word scan exactly
    /// like production's `_candidate_tokens`, "opaque IDs and base64 chunks would force a floor no
    /// column could reach". A narrow table with a URL-only cell must still shrink to
    /// `TEXT_COL_MIN_W` and fit, not error.
    #[test]
    fn url_token_does_not_set_an_unreachable_word_floor() {
        let url = "https://averyveryveryverylongexampledomainusedtoproveexclusion.example.com/path";
        let rows = vec![json!({"id": "1", "link": url})];
        let columns = json!({
            "id": {"label": "ID"},
            "link": {"label": "Link"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 120.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        render(&payload, &fonts)
            .expect("a URL-only cell must not set an unreachable word floor");
    }

    // Body row dividers (`style.row.divider`): a rule paints below
    // every row except the last-on-page and any row immediately before a
    // summary row, and its reserved height is carved out of THAT row's own
    // fill (stripe/background), not added to the row's height — the table
    // does not grow.
    #[test]
    fn body_row_rule_paints_between_rows_and_shrinks_that_rows_fill() {
        let rows: Vec<Value> = (0..4).map(|i| json!({"a": format!("row{i}")})).collect();
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["row"]["divider"] = table_divider(true, "#222222", 1.0);
        theme["row"]["stripe"] = json!({"color": "#eeeeee"});
        theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let row_rule_count = svg
            .matches("height=\"1\" fill=\"#222222\" shape-rendering=\"crispEdges\"")
            .count();
        assert_eq!(
            row_rule_count, 3,
            "one rule below each of the first 3 rows, none below the last row:\n{svg}"
        );

        // Row 1 (odd, striped) is not the last row: its stripe fill loses
        // 1px (row_h=32 -> 31) to the rule reservation. Row 3 (odd, last)
        // keeps its full 32px stripe fill since no rule draws below it.
        assert!(
            svg.contains("height=\"31\" fill=\"#eeeeee\""),
            "row 1's stripe fill must shrink by the 1px rule reservation:\n{svg}"
        );
        assert!(
            svg.contains("height=\"32\" fill=\"#eeeeee\""),
            "row 3 (last) keeps its full stripe height (no rule reserved):\n{svg}"
        );
    }

    /// `style.row.stripe` is nullable: `None` means no alternating row fill, the SAME outcome as an
    /// explicit `{"color": null}`, but a real board can resolve either shape
    /// (`playground/dundersign-support-operations`'s `open_queue_table` emits the bare `null`).
    #[test]
    fn row_stripe_null_means_no_alternating_fill_same_as_an_explicit_null_color() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"a": format!("row{i}")})).collect();
        let columns = json!({"a": {"label": "A"}});
        let make_svg = |stripe: Value| -> String {
            let mut theme = base_table_theme();
            theme["row"]["stripe"] = stripe;
            let resolved = json!({
                "link_open": "page",
                "title": "Test Table",
                "title_style": resolved_title_style(),
                "columns": columns,
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            });
            let payload_json = json!({
                "resolved": resolved,
                "rows": rows,
                "width": 300.0,
                "height": 400.0,
                "background": "#ffffff",
                "fonts_dir": null,
                "padding": null,
            });
            let payload: Payload =
                crate::test_payload(payload_json);
            render(&payload, &test_fonts()).expect("a null row.stripe must not refuse to render")
        };
        let bare_null = make_svg(Value::Null);
        let explicit_null_color = make_svg(json!({"color": null}));
        assert_eq!(
            bare_null, explicit_null_color,
            "a bare null stripe and an explicit {{color: null}} stripe must render identically"
        );
        assert!(
            !bare_null.contains("fill=\"#eeeeee\""),
            "no stripe color was authored, so no stripe fill should paint:\n{bare_null}"
        );
    }

    /// A `transparent` stripe paints nothing, same as no stripe.
    #[test]
    fn transparent_row_stripe_emits_no_rect() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"a": format!("row{i}")})).collect();
        let make_svg = |stripe: Value| -> String {
            let mut theme = base_table_theme();
            theme["row"]["stripe"] = stripe;
            let payload: Payload = crate::test_payload(json!({
                "resolved": {
                    "link_open": "page",
                    "title": "T", "title_style": resolved_title_style(),
                    "columns": {"a": {"label": "A"}},
                    "style": {"table": theme, "title_font": resolved_title_font()},
                    "pivot_columns": null, "conditional_formatting": null,
                },
                "rows": rows, "width": 300.0, "height": 400.0,
                "background": "#ffffff", "fonts_dir": null, "padding": null,
            }));
            render(&payload, &test_fonts()).expect("renders")
        };
        assert_eq!(
            make_svg(json!({"color": "transparent"})),
            make_svg(json!({"color": null}))
        );
    }

    /// A rule never draws immediately before a summary row even when `row_rule_width > 0` and the
    /// row isn't last-on-page.
    #[test]
    fn body_row_rule_skips_before_a_summary_row() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "value"}),
            json!({"a": "3", "row_role": "summary"}),
        ];
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["row"]["divider"] = table_divider(true, "#222222", 1.0);
        theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let row_rule_count = svg
            .matches("height=\"1\" fill=\"#222222\" shape-rendering=\"crispEdges\"")
            .count();
        // 1 row-level rule (row 0) + row 2's own unconditional summary-row
        // overhead rule, which production also formats as a bare int height
        // (`max(1, int(summary_divider.width))`) — indistinguishable
        // from the row rule's own bare `rule_reserve_px` by this substring.
        assert_eq!(
            row_rule_count, 2,
            "only row 0 gets its own row rule: row 1 is suppressed (next row \
             is summary), row 2 is last-on-page; row 2 (summary) still draws \
             its own overhead rule above itself:\n{svg}"
        );
    }

    /// A summary row never draws a rule below itself, even when neither of
    /// the other two suppression conditions (`is_last_row`, `next_is_summary`)
    /// applies — the standalone `!is_summary` clause in `will_draw_row_rule`
    /// (survives no other assertion: `body_row_rule_skips_before_a_summary_row`'s
    /// 3-row fixture always has a summary row that is ALSO last-on-page or
    /// followed by nothing, so deleting `!is_summary` there wouldn't change
    /// its count). `value, summary, value, value`: row 1 (summary) is
    /// neither last nor followed by another summary row, so only `!is_summary`
    /// suppresses its rule.
    #[test]
    fn body_row_rule_never_draws_below_a_summary_row_itself() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "summary"}),
            json!({"a": "3", "row_role": "value"}),
            json!({"a": "4", "row_role": "value"}),
        ];
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["row"]["divider"] = table_divider(true, "#222222", 1.0);
        theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let row_rule_count = svg
            .matches("height=\"1\" fill=\"#222222\" shape-rendering=\"crispEdges\"")
            .count();
        // 1 row-level rule (row 2) + row 1's own unconditional summary-row
        // overhead rule (see the sibling test above for why both share this
        // bare-int substring).
        assert_eq!(
            row_rule_count, 2,
            "row 0 is suppressed (next is summary); row 1 (summary) is suppressed \
             by `!is_summary` alone but still draws its own overhead rule; \
             row 2 draws its rule; row 3 is last-on-page:\n{svg}"
        );
    }

    /// A row rule spans the cell-content edge (`Σcol_ws`), not the table's
    /// outer slot (`table_w`) — production's `_compute_content_span`
    /// Used for both row and summary/total rules. An
    /// all-pinned table whose pins sum well under `table_w` must not paint
    /// its rule past the last column's own content edge.
    #[test]
    fn row_rule_spans_the_content_edge_not_the_table_slot() {
        let rows = vec![
            json!({"a": "1"}),
            json!({"a": "2"}),
        ];
        let columns = json!({"a": {"label": "A", "width": 50}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["row"]["divider"] = table_divider(true, "#222222", 1.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        // outer_padding=12, table_w = 300 - 24 = 276; a single 50px pin
        // leaves `Σcol_ws=50` far short of `table_w`. The header divider keeps
        // its theme color (`#222222`) too, so the negative assertion below
        // covers both dividers.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Content edge: outer(12) + pad(8) .. outer + col_ws(50) - pad(8) = 20..54, width 34.
        let expected_w = 50.0 - 2.0 * 8.0;
        let expected_w_s = float_repr(expected_w);
        assert!(
            svg.contains(&format!("width=\"{expected_w_s}\" height=\"1\" fill=\"#222222\"")),
            "row rule must span the pinned column's own content edge (34px), \
             not the 260px table slot:\n{svg}"
        );
        assert!(
            !svg.contains("width=\"260.0\" height=\"1\" fill=\"#222222\""),
            "row rule must not span the table's outer slot:\n{svg}"
        );
        // The header divider shares the same content span and color
        // (`#222222`) — it formats its height as a genuine float (`"1.0"`),
        // unlike the row divider's bare int (`"1"`), so it needs its own
        // negative assertion.
        assert!(
            !svg.contains("width=\"260.0\" height=\"1.0\" fill=\"#222222\""),
            "header rule must not span the table's outer slot either:\n{svg}"
        );
    }

    /// Row stripes and per-role backgrounds span the
    /// content edge (`_compute_content_span(..., cell_pad=0)`,
    /// Not `x=outer, width=svg_w` — the same defect
    /// already fixed for the header/row/summary rules above. An all-pinned
    /// table whose pins sum well under `table_w` must not paint the odd-row
    /// stripe or a summary-role background past the last column's own
    /// content edge. Deletion counterfactual (verified RED reverting to
    /// `width=svg_w`): both rects paint the full 300px canvas slot instead
    /// of the pinned 50px content span.
    #[test]
    fn row_stripe_and_role_background_span_the_content_edge_not_the_table_slot() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "value"}),
            json!({"a": "3", "row_role": "summary"}),
        ];
        let columns = json!({"a": {"label": "A", "width": 50}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["row"]["stripe"] = json!({"color": "#eeeeee"});
        theme["row"]["roles"]["summary"]["background"] = json!("#dddddd");
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        // outer_padding=12, table_w = 300 - 24 = 276; a single 50px pin
        // leaves `Σcol_ws=50` far short of `table_w`.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(
            svg.contains("width=\"50.0\" height="),
            "stripe/role-bg rects must span the pinned column's content edge (50px):\n{svg}"
        );
        assert!(
            !svg.contains("width=\"300.0\" height="),
            "stripe/role-bg rects must not span the canvas's full width (300px):\n{svg}"
        );
        assert!(
            svg.contains("fill=\"#eeeeee\"") && svg.contains("fill=\"#dddddd\""),
            "both the stripe and role background must actually render:\n{svg}"
        );
    }

    /// The row-rule reserve rounds like Python's `round()` (half-to-even),
    /// not Rust's `f64::round` (half-away-from-zero) — production's
    /// `max(1, int(round(row_rule_width)))`. At `width: 2.5`
    /// production reserves 2px; Rust's `.round()` would reserve 3px.
    #[test]
    fn row_rule_reserve_uses_bankers_rounding() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"a": format!("row{i}")})).collect();
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["row"]["divider"] = table_divider(true, "#222222", 2.5);
        theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 300.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains("height=\"2\" fill=\"#222222\" shape-rendering=\"crispEdges\""),
            "width: 2.5 must round to 2 (banker's rounding), not 3:\n{svg}"
        );
        assert!(
            !svg.contains("height=\"3\" fill=\"#222222\" shape-rendering=\"crispEdges\""),
            "must not reserve 3px (half-away-from-zero rounding):\n{svg}"
        );
    }

    /// A divider paints its own color: there is no table-level override tier and
    /// no fallback to the body text color (`_divider_segment` reads `divider.color`).
    #[test]
    fn row_divider_paints_its_own_color() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"a": format!("row{i}")})).collect();
        let columns = json!({"a": {"label": "A"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["row"]["divider"] = table_divider(true, "#cc0000", 1.0);
        theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
        let payload = plain_payload(theme, columns, rows, 300.0, 400.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(
            svg.matches("fill=\"#cc0000\" shape-rendering=\"crispEdges\"").count(),
            2,
            "a row divider under each of the first two rows, in its own color:\n{svg}"
        );
        assert!(!svg.contains("fill=\"#222222\" shape-rendering"), "no body-color line:\n{svg}");
    }

    /// Each divider reads its own slot: the header's, the body row's, the
    /// summary row's and the total row's (`header.divider`, `row.divider`,
    /// `row.roles.summary.divider`, `row.roles.total.divider`) — no shared
    /// color cascade between them.
    #[test]
    fn each_table_divider_paints_its_own_color() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "value"}),
            json!({"a": "3", "row_role": "summary"}),
            json!({"a": "4", "row_role": "total"}),
        ];
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["header"]["divider"] = table_divider(true, "#111111", 1.0);
        theme["row"]["divider"] = table_divider(true, "#222222", 1.0);
        theme["row"]["roles"]["summary"]["divider"] = divider(true, "#333333", 1.0);
        theme["row"]["roles"]["total"]["divider"] = divider(true, "#444444", 1.0);
        let payload = plain_payload(theme, json!({"a": {"label": "A"}}), rows, 300.0, 400.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let count = |color: &str| {
            svg.matches(&format!("fill=\"{color}\" shape-rendering=\"crispEdges\"")).count()
        };
        assert_eq!(count("#111111"), 1, "header divider:\n{svg}");
        assert_eq!(count("#222222"), 1, "one row divider, under row 0 (row 1 precedes the summary):\n{svg}");
        assert_eq!(count("#333333"), 1, "one line above the summary row:\n{svg}");
        assert_eq!(count("#444444"), 2, "a double line above the total row:\n{svg}");
    }

    /// The summary and total dividers are independent switches: hiding the
    /// summary one leaves the total row's double line, and vice versa.
    #[test]
    fn the_total_divider_is_independent_of_the_summary_divider() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "summary"}),
            json!({"a": "3", "row_role": "total"}),
        ];
        let lines = |summary_visible: bool, total_visible: bool| {
            let mut theme = base_table_theme();
            theme["row"]["role"] = json!("row_role");
            theme["header"]["divider"] = table_divider(false, "#222222", 1.0);
            theme["row"]["roles"]["summary"]["divider"] = divider(summary_visible, "#333333", 1.0);
            theme["row"]["roles"]["total"]["divider"] = divider(total_visible, "#444444", 1.0);
            let payload =
                plain_payload(theme, json!({"a": {"label": "A"}}), rows.clone(), 300.0, 400.0);
            let svg = render(&payload, &test_fonts()).expect("renders");
            let count = |color: &str| svg.matches(&format!("fill=\"{color}\"")).count();
            (count("#333333"), count("#444444"))
        };
        assert_eq!(lines(true, true), (1, 2));
        assert_eq!(lines(false, true), (0, 2), "hiding the summary line leaves the total's two");
        assert_eq!(lines(true, false), (1, 0), "hiding the total line leaves the summary's one");
    }

    /// A dashed divider is a stroked `<line>` centered on
    /// the band a solid one would fill — header (an int `y`), body row (int
    /// `y` and reserve) and summary/total (int `y`) all use the one function.
    #[test]
    fn a_dashed_table_divider_is_a_stroked_line_centered_on_its_band() {
        let rows = vec![
            json!({"a": "1", "row_role": "value"}),
            json!({"a": "2", "row_role": "value"}),
            json!({"a": "3", "row_role": "summary"}),
        ];
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        let mut dashed = table_divider(true, "#0077cc", 2.0);
        dashed["dasharray"] = json!("6 3");
        theme["header"]["divider"] = dashed.clone();
        theme["row"]["divider"] = dashed.clone();
        theme["row"]["roles"]["summary"]["divider"] = {
            let mut d = dashed;
            d.as_object_mut().unwrap().remove("continuous");
            d
        };
        let payload = plain_payload(theme, json!({"a": {"label": "A"}}), rows, 300.0, 400.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let lines: Vec<&str> = svg
            .match_indices("<line ")
            .map(|(i, _)| &svg[i..svg[i..].find("/>").unwrap() + i + 2])
            .filter(|l| l.contains("stroke=\"#0077cc\""))
            .collect();
        assert_eq!(lines.len(), 3, "header, one row line, one summary line:\n{svg}");
        // Header thickness is `header_rule_width` (a float, `2.0`); the row and
        // summary thicknesses are `int(...)` (`2`).
        for (line, width) in lines.iter().zip(["2.0", "2", "2"]) {
            let stroke = format!("stroke-width=\"{width}\" stroke-dasharray=\"6 3\"/>");
            assert!(line.contains(&stroke), "{line}");
            assert!(line.contains("x1=\"20.0\"") && line.contains("x2=\"280.0\""), "{line}");
        }
        // Header: band top `int(81 - 2)`-style integer, midpoint a float.
        let ys: Vec<&str> =
            lines.iter().map(|l| l.split("y1=\"").nth(1).unwrap().split('"').next().unwrap()).collect();
        assert!(ys.iter().all(|y| y.ends_with(".0")), "midpoints are floats: {ys:?}");
        assert!(!svg.contains("fill=\"#0077cc\""), "no solid rect for a dashed divider:\n{svg}");
    }

    /// `visible` gates a divider; width never does. A hidden divider keeps its
    /// width and paints nothing, and a visible zero-width-free divider paints.
    #[test]
    fn a_hidden_table_divider_paints_nothing_whatever_its_width() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"a": format!("row{i}")})).collect();
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["header"]["divider"] = table_divider(false, "#222222", 3.0);
        theme["row"]["divider"] = table_divider(false, "#222222", 3.0);
        let payload = plain_payload(theme, json!({"a": {"label": "A"}}), rows, 300.0, 400.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains("crispEdges"), "no divider rect at all:\n{svg}");
        assert!(!svg.contains("<line "), "no divider line either:\n{svg}");
    }

    /// The per-column rule span: a numeric column
    /// centers on its cell midpoint at `max(label_w, lane width)`.
    #[test]
    fn a_numeric_column_rule_centers_on_the_cell_at_the_wider_of_label_and_lane() {
        // cell 100..220 (midpoint 160), bounds 104..216.
        let lane_wider = header_rule_span(100.0, 120.0, 8.0, 40.0, Some((130.0, 190.0)));
        assert_eq!(lane_wider, Some((130.0, 190.0)), "lane 60 > label 40");
        let label_wider = header_rule_span(100.0, 120.0, 8.0, 80.0, Some((140.0, 170.0)));
        assert_eq!(label_wider, Some((120.0, 200.0)), "label 80 > lane 30");
    }

    /// A text column's rule starts at `cell_x + pad` and spans
    /// `max(label_w, cw - 2*pad)`.
    #[test]
    fn a_text_column_rule_starts_at_the_padded_left_edge() {
        assert_eq!(header_rule_span(0.0, 100.0, 8.0, 30.0, None), Some((8.0, 92.0)));
        // A label wider than the content area extends the rule to its width.
        assert_eq!(header_rule_span(0.0, 130.0, 8.0, 117.0, None), Some((8.0, 125.0)));
    }

    /// Every span clamps to `cell_x + 4 .. cell_x + cw - 4` so adjacent
    /// columns' rules never touch.
    #[test]
    fn a_column_rule_clamps_to_four_pixels_inside_its_cell() {
        // Text: x2 = 8 + 120 = 128 > 96; padding 2 puts x1 = 2 < 4.
        assert_eq!(header_rule_span(0.0, 100.0, 8.0, 120.0, None), Some((8.0, 96.0)));
        assert_eq!(header_rule_span(0.0, 100.0, 2.0, 10.0, None), Some((4.0, 96.0)));
        // Numeric: centered 200 wide on a 100 cell overflows both sides.
        assert_eq!(
            header_rule_span(0.0, 100.0, 8.0, 200.0, Some((30.0, 70.0))),
            Some((4.0, 96.0))
        );
    }

    /// `if x2 > x1`: a cell too narrow for its 4px bounds
    /// paints no rule.
    #[test]
    fn a_column_rule_that_clamps_to_nothing_paints_nothing() {
        assert_eq!(header_rule_span(0.0, 8.0, 0.0, 5.0, None), None);
        assert_eq!(header_rule_span(0.0, 6.0, 0.0, 5.0, None), None);
        assert_eq!(header_rule_span(0.0, 8.0, 0.0, 5.0, Some((1.0, 7.0))), None);
    }

    /// Two pinned columns (no surplus to spread): a text column at x 12..132
    /// and a numeric one at 132..232, so the cell geometry is known exactly.
    fn per_column_payload(label_a: &str, label_n: &str, dasharray: Option<&str>) -> Payload {
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["header"]["divider"]["continuous"] = json!(false);
        theme["header"]["divider"]["dasharray"] = json!(dasharray);
        let columns = json!({
            "a": {"label": label_a, "width": 120},
            "n": {"label": label_n, "width": 100},
        });
        let rows = vec![
            json!({"a": "West", "n": 812}),
            json!({"a": "East", "n": 654}),
            json!({"a": "Central", "n": 431}),
        ];
        plain_payload(theme, columns, rows, 244.0, 400.0)
    }

    /// `(x, width)` of every solid divider rect in `svg`, in paint order.
    fn divider_rects(svg: &str) -> Vec<(f64, f64)> {
        let attr = |tag: &str, name: &str| -> f64 {
            let at = tag.find(&format!("{name}=\"")).unwrap() + name.len() + 2;
            tag[at..].split('"').next().unwrap().parse().unwrap()
        };
        svg.match_indices("<rect ")
            .map(|(i, _)| &svg[i..svg[i..].find("/>").unwrap() + i])
            .filter(|tag| tag.contains("fill=\"#222222\"") && tag.contains("crispEdges"))
            .map(|tag| (attr(tag, " x"), attr(tag, "width")))
            .collect()
    }

    /// One rule under each column: the text column spans its padded content
    /// area, the numeric one centers on its cell at the wider of label and
    /// lane — here the lane ("812"), since the label is one letter.
    #[test]
    fn a_non_continuous_header_divider_paints_one_rule_under_each_column() {
        let fonts = test_fonts();
        let svg = render(&per_column_payload("Region", "Q", None), &fonts).expect("renders");
        let lane_w = fonts.measure("812", TAB_FONT, 11.0).unwrap();
        let rects = divider_rects(&svg);
        assert_eq!(rects.len(), 2, "one rule per column:\n{svg}");
        assert_eq!(rects[0], (20.0, 104.0), "text: cell_x 12 + pad 8, width 120 - 16");
        let (x, w) = rects[1];
        assert!((w - lane_w).abs() < 1e-9, "numeric rule is the lane width {lane_w}, got {w}");
        assert!((x + w / 2.0 - 182.0).abs() < 1e-9, "centered on the cell midpoint 182, got {x}");
    }

    /// A left-aligned numeric column has no lane to center its header rule on:
    /// the rule starts at the padded left edge like a text column's.
    #[test]
    fn a_left_aligned_numeric_columns_header_rule_starts_at_its_left_padding() {
        let mut payload = per_column_payload("Region", "Q", None);
        payload.resolved["columns"]["n"]["align"] = json!("left");
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(divider_rects(&svg)[1].0, 140.0, "cell_x 132 + pad 8:\n{svg}");
    }

    /// A label wider than the lane sets the numeric rule's width, measured at
    /// the header size with the header family, after the header's letter case
    /// (`title`) has been applied — the rule covers what is painted.
    #[test]
    fn a_wide_header_label_sets_the_numeric_rule_width() {
        let fonts = test_fonts();
        let svg = render(&per_column_payload("Region", "Revenue Total", None), &fonts)
            .expect("renders");
        let label_w = fonts.measure("Revenue Total", "Inter", 11.0).unwrap();
        let (x, w) = divider_rects(&svg)[1];
        assert!((w - label_w).abs() < 1e-9, "rule is the label width {label_w}, got {w}");
        assert!((x + w / 2.0 - 182.0).abs() < 1e-9, "centered on the cell midpoint, got {x}");
    }

    /// A wrapped header's rule is sized by `max(lines, key=len)`
    /// The line with the most CHARACTERS, not the widest
    /// painted one — here the 11-character narrow line beats the 7-character
    /// wide one.
    #[test]
    fn a_wrapped_header_rule_is_sized_by_its_longest_line_in_characters() {
        let fonts = test_fonts();
        let svg = render(&per_column_payload("Region", "MMMMMMM Illllllllll", None), &fonts)
            .expect("renders");
        assert!(svg.contains(">MMMMMMM</tspan>") && svg.contains(">Illllllllll</tspan>"), "{svg}");
        let longest = fonts.measure("Illllllllll", "Inter", 11.0).unwrap();
        assert!(longest < fonts.measure("MMMMMMM", "Inter", 11.0).unwrap());
        let (_, w) = divider_rects(&svg)[1];
        assert!((w - longest).abs() < 1e-9, "rule {w} vs longest line {longest}");
    }

    /// `max(lines, key=len)` returns the FIRST of equally long lines
    /// Two 7-character lines are sized by the first, even
    /// though the second is the narrower one painted.
    #[test]
    fn a_wrapped_header_rule_takes_the_first_of_equally_long_lines() {
        let fonts = test_fonts();
        let svg = render(&per_column_payload("Region", "MMMMMMM IIIIIII", None), &fonts)
            .expect("renders");
        assert!(svg.contains(">MMMMMMM</tspan>") && svg.contains(">IIIIIII</tspan>"), "{svg}");
        let first = fonts.measure("MMMMMMM", "Inter", 11.0).unwrap();
        assert!(first > fonts.measure("IIIIIII", "Inter", 11.0).unwrap());
        let (_, w) = divider_rects(&svg)[1];
        assert!((w - first).abs() < 1e-9, "rule {w} vs first line {first}");
    }

    /// The segment form is `_divider_segment`'s: a dashed per-column rule is a
    /// stroked `<line>` over the same x span as the solid rect.
    #[test]
    fn a_dashed_per_column_rule_is_a_stroked_line_over_the_rect_span() {
        let fonts = test_fonts();
        let solid = divider_rects(
            &render(&per_column_payload("Region", "Q", None), &fonts).expect("renders"),
        );
        let svg = render(&per_column_payload("Region", "Q", Some("4 2")), &fonts).expect("renders");
        assert!(divider_rects(&svg).is_empty(), "no solid rect for a dashed divider:\n{svg}");
        let lines: Vec<&str> = svg
            .match_indices("<line ")
            .map(|(i, _)| &svg[i..svg[i..].find("/>").unwrap() + i])
            .collect();
        assert_eq!(lines.len(), solid.len(), "{svg}");
        for (line, (x, w)) in lines.iter().zip(&solid) {
            assert!(line.contains("stroke-dasharray=\"4 2\""), "{line}");
            assert!(line.contains(&format!("x1=\"{}\"", float_repr(*x))), "{line}");
            assert!(line.contains(&format!("x2=\"{}\"", float_repr(x + w))), "{line}");
        }
    }

    /// `header_divider.visible` gates the whole header rule, per-column or not.
    #[test]
    fn a_hidden_non_continuous_header_divider_paints_nothing() {
        let mut payload = per_column_payload("Region", "Q", None);
        payload.resolved["style"]["table"]["header"]["divider"]["visible"] = json!(false);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(divider_rects(&svg).is_empty() && !svg.contains("<line "), "{svg}");
    }

    /// The rule measures the CASED label (`header.font.case` applies before
    /// `resolve_wrapped_headers` wraps and measures), never the authored one.
    #[test]
    fn a_per_column_rule_measures_the_cased_label() {
        let fonts = test_fonts();
        let svg = render(&per_column_payload("Region", "days to close", None), &fonts).expect("renders");
        let family = measure::stand_in_family("Inter, sans-serif");
        let cased = fonts.measure("Days to Close", family, 11.0).unwrap();
        let raw = fonts.measure("days to close", family, 11.0).unwrap();
        assert!((cased - raw).abs() > 0.01, "the probe needs cased and raw widths to differ");
        let (_, w) = divider_rects(&svg)[1];
        assert!((w - cased).abs() < 1e-9, "rule width {w} vs cased {cased}:\n{svg}");
    }

    /// Production measures the row-number column's rule from its internal key,
    /// which this engine does not draw: visible `row_numbers` under a
    /// per-column rule refuses by name instead of dropping the column.
    #[test]
    fn a_per_column_header_divider_over_row_numbers_refuses_by_name() {
        let mut payload = per_column_payload("Region", "Q", None);
        payload.resolved["style"]["table"]["row_numbers"] = json!({"visible": true});
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("header.divider.continuous: false"), "{err}");
        assert!(err.contains("row_numbers"), "{err}");
    }

    fn spanner_payload(header_height: f64, hidden: &[&str]) -> Payload {
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        theme["header"]["height"] = json!(header_height);
        let mut columns = json!({"a": {"label": "A"}, "b": {"label": "B"}, "c": {"label": "C"}});
        for h in hidden {
            columns[*h]["visible"] = json!(false);
        }
        let mut payload = plain_payload(
            theme,
            columns,
            vec![json!({"a": "x", "b": 1.0, "c": 2.0})],
            400.0,
            400.0,
        );
        payload.resolved["spanners"] = json!([{"label": "Group", "columns": ["b", "c"]}]);
        payload
    }

    /// An authored spanner is one group-header row above the leaf headers,
    /// the label centered over its columns on `row_y + h/2 + centered offset`
    /// (Inter 11px: 4.00146484375), the 1px rule along the row's bottom.
    #[test]
    fn an_authored_spanner_draws_a_centered_label_row_above_the_headers() {
        let svg = render(&spanner_payload(40.0, &[]), &test_fonts()).expect("renders");
        let label = svg.split(">Group</text>").next().unwrap();
        let label = &label[label.rfind("<text ").unwrap()..];
        assert!(label.contains("text-anchor=\"middle\""), "{label}");
        let y: f64 = label.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        // Title band 31 + half a 40px group row + the centered offset.
        assert_eq!(y, 31.0 + 20.0 + 4.00146484375, "{svg}");
        assert!(svg.contains("y=\"70.0\" width="), "rule on the row bottom edge:\n{svg}");
    }

    fn spanner_rule_width(svg: &str) -> f64 {
        let rule = svg.split("<rect ").find(|r| r.contains("y=\"70.0\"")).expect("span rule");
        rule.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    /// Hiding one spanned column narrows the span; hiding both drops it.
    #[test]
    fn a_hidden_spanned_column_narrows_the_authored_spanner() {
        let full = spanner_rule_width(&render(&spanner_payload(40.0, &[]), &test_fonts()).unwrap());
        let svg = render(&spanner_payload(40.0, &["c"]), &test_fonts()).expect("renders");
        assert!(svg.contains(">Group</text>"), "{svg}");
        assert!(spanner_rule_width(&svg) < full, "the span covers one column, not two:\n{svg}");
        let svg = render(&spanner_payload(40.0, &["b", "c"]), &test_fonts()).expect("renders");
        assert!(!svg.contains(">Group</text>"), "{svg}");
    }

    fn svg_height(svg: &str) -> f64 {
        svg.split(" height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    /// A swatch cell is a 14px chip of its own color (`none` paints
    /// `transparent`), never the hex string, and the column is sized to the chip.
    #[test]
    fn a_swatch_column_paints_a_chip_instead_of_text() {
        let mut theme = base_table_theme();
        theme["outer_padding"] = json!(0.0);
        let columns = json!({"c": {"swatch": true, "width": 24}, "n": {"label": "N", "width": 80}});
        let rows = vec![json!({"c": "#3164a3", "n": "x"}), json!({"c": "none", "n": "y"})];
        let svg = render(&plain_payload(theme, columns, rows, 104.0, 0.0), &test_fonts()).expect("renders");
        assert!(svg.contains("<g transform=\"translate(4, 87.0)\"><rect width=\"14\" height=\"14\" rx=\"3\" fill=\"#3164a3\"/></g>"), "{svg}");
        assert!(svg.contains("fill=\"transparent\"/></g>"), "{svg}");
        assert!(!svg.contains("#3164a3<"), "the hex is paint, not text:\n{svg}");
    }

    /// A hidden header gives its band and the header/body gap back to the rows.
    #[test]
    fn a_hidden_header_collapses_its_band_and_gap() {
        let columns = json!({"n": {"label": "Name"}});
        let rows = vec![json!({"n": "x"})];
        let shown = render(&plain_payload(base_table_theme(), columns.clone(), rows.clone(), 300.0, 0.0), &test_fonts()).unwrap();
        let mut theme = base_table_theme();
        theme["header"]["visible"] = json!(false);
        let hidden = render(&plain_payload(theme, columns, rows, 300.0, 0.0), &test_fonts()).unwrap();
        // The 39px header band and the `int(32 * 0.25)` gap.
        assert_eq!(svg_height(&hidden), svg_height(&shown) - 47.0, "{hidden}");
        assert!(shown.contains(">Name</text>") && !hidden.contains(">Name</text>"), "{hidden}");
    }

    /// The fit cascade samples the first 50 rows only.
    #[test]
    fn the_fit_cascade_samples_only_the_first_fifty_rows() {
        let height = |last: f64| {
            let mut rows: Vec<Value> = (0..50).map(|_| json!({"v": 100.0})).collect();
            rows.push(json!({"v": last}));
            let columns = json!({"v": {"label": "V", "width": 52, "align": "right"}});
            let mut theme = base_table_theme();
            theme["outer_padding"] = json!(0.0);
            theme["pagination"]["enabled"] = json!(false);
            svg_height(&render(&plain_payload(theme, columns, rows, 52.0, 0.0), &test_fonts()).unwrap())
        };
        assert_eq!(height(2615.0), height(100.0));
    }

    /// The decimal pad sizes a column but never drives the fit cascade: the
    /// cascade measures the text without it.
    #[test]
    fn the_decimal_pad_does_not_drive_the_fit_cascade() {
        let rows = vec![json!({"v": 12100.0}), json!({"v": 7800.0})];
        let columns = |pad: Value| json!({"v": {"label": "V", "width": 52, "align": "right", "decimal_pad_table": pad}});
        let mut theme = base_table_theme();
        theme["outer_padding"] = json!(0.0);
        let padded = columns(json!(["", "\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2007}\u{200b}\u{2008}\u{200b}"]));
        let with_pad = render(&plain_payload(theme.clone(), padded, rows.clone(), 52.0, 0.0), &test_fonts()).unwrap();
        let without = render(&plain_payload(theme, columns(json!([])), rows, 52.0, 0.0), &test_fonts()).unwrap();
        assert_eq!(svg_height(&with_pad), svg_height(&without), "{with_pad}");
    }

    /// Under `wrap: false` an overflowing text cell drives the fit cascade like
    /// a numeric one: padding steps down to 6 before the cell is cut.
    #[test]
    fn an_unwrapped_text_cell_that_overflows_steps_the_padding_down() {
        let rows = vec![json!({"name": "A very long name that cannot fit", "value": 1.0})];
        let columns = json!({"name": {"label": "Name", "width": 60}, "value": {"label": "Value"}});
        let mut theme = base_table_theme();
        theme["wrap"] = json!(false);
        let payload = on_a_board(plain_payload(theme.clone(), columns.clone(), rows.clone(), 300.0, 400.0));
        let svg = render(&payload, &test_fonts()).expect("renders");
        // The header rule starts at outer + the effective padding.
        assert!(svg.contains("<rect x=\"18.0\""), "padding stepped down before the cut:\n{svg}");
        assert!(svg.contains('\u{2026}'), "{svg}");
        theme["wrap"] = json!(true);
        let wrapped = render(&on_a_board(plain_payload(theme, columns, rows, 300.0, 400.0)), &test_fonts()).unwrap();
        assert!(wrapped.contains("<rect x=\"20.0\""), "a wrapping table's text cells never drive the cascade:\n{wrapped}");
    }

    /// A title-less table needs no subtitle family.
    #[test]
    fn a_table_without_a_subtitle_needs_no_table_font_family() {
        let mut theme = base_table_theme();
        theme["font"].as_object_mut().unwrap().remove("family");
        theme["header"]["font"]["family"] = json!("Inter, sans-serif");
        let payload = plain_payload(theme, json!({"a": {"label": "A"}}), vec![json!({"a": "x"})], 300.0, 400.0);
        let err = render(&payload, &test_fonts()).err().map(|d| d.to_string()).unwrap_or_default();
        assert!(!err.contains("a subtitle needs"), "{err}");
    }

    /// A payload whose table style lacks a divider is stale: it refuses by the
    /// missing field's name, never a guessed line.
    #[test]
    fn a_table_style_without_dividers_refuses_by_field_name() {
        let mut theme = base_table_theme();
        theme["header"].as_object_mut().unwrap().remove("divider");
        let payload_json = json!({
            "resolved": {"title": null, "title_style": resolved_title_style(), "columns": {"a": {"label": "A"}},
                         "style": {"table": theme, "title_font": resolved_title_font()}, "pivot_columns": null,
                         "conditional_formatting": null},
            "rows": [{"a": "1"}], "width": 300.0, "height": 400.0,
            "background": "#ffffff", "fonts_dir": null, "padding": null,
        });
        let err = serde_json::from_value::<Payload>(payload_json).map(|p| render(&p, &test_fonts()));
        let message = match err {
            Err(e) => e.to_string(),
            Ok(r) => r.unwrap_err().to_string(),
        };
        assert!(message.contains("divider"), "{message}");
    }

    /// The pivot group-header span rule is sourced from the
    /// header FONT color (`_render_pivot_group_header`'s own
    /// `rule_color = colors.get("label_color") or colors["color"]`,
    /// Where `colors["label_color"] = tc.header.font.color`,
    /// Not the header divider's color.
    /// `style.table.header.font.color` is set and `header.divider.color` is a
    /// different color; the span rule must paint the header font color.
    /// Deletion counterfactual (verified RED before sourcing the span rule
    /// from `header_color`): the span rule painted the divider's color.
    #[test]
    fn pivot_group_span_rule_uses_header_font_color_not_the_header_divider_color() {
        let rows = vec![
            json!({"region": "East", "category": "Electronics", "revenue": 58396.0, "units": 2108.0}),
            json!({"region": "East", "category": "Tools", "revenue": 14824.0, "units": 449.0}),
        ];
        let columns = json!({
            "revenue": {"label": "Revenue", "format": "currency_whole"},
            "units": {"label": "Units", "format": "integer"},
        });
        for (visible, width) in [(true, 1.0), (false, 1.0), (true, 3.0), (false, 3.0)] {
        let mut theme = base_table_theme();
        theme["header"]["font"]["color"] = json!("#0055aa");
        theme["header"]["divider"] = table_divider(visible, "#cc0000", width);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Pivot",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "rows": ["region"],
            "pivot_columns": ["category"],
            "values": ["revenue", "units"],
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("multi-measure pivot renders");
        // The header divider legitimately paints its own color (`#cc0000`),
        // so the negative check must target the group span
        // rects specifically, not every `shape-rendering="crispEdges"` rect.
        let span_rects = svg.matches("height=\"1.0\" fill=\"#0055aa\" shape-rendering=\"crispEdges\"").count();
        assert_eq!(
            span_rects, 2,
            "both group span rules (Electronics, Tools) paint the 1px header font color whatever the divider (visible={visible}, width={width}):\n{svg}"
        );
        }
    }

    // row.roles.summary/total styling (rule, background, font weight) —
    // previously unread; summary/total rows rendered as plain rows plus the
    // gap. Per-role background rect, heavier font, and
    // the summary/total rule.
    #[test]
    fn row_roles_summary_and_total_style_the_row() {
        let rows = vec![
            json!({"company": "Acme", "revenue": 100.0, "row_role": "value"}),
            json!({"company": "Total", "revenue": 300.0, "row_role": "total"}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency_whole"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!("row_role");
        theme["row"]["roles"] = json!({
            "summary": {"divider": divider(true, "#222222", 1.0)},
            "total": {"divider": divider(true, "#222222", 1.0),
                      "background": "#f0f0f0", "font": {"weight": "700"}},
        });
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("fill=\"#f0f0f0\""), "total row gets its role background rect:\n{svg}");
        assert!(svg.contains("font-weight=\"700\""), "total row gets its role font weight:\n{svg}");
        // Double rule for "total": two crisp-edged rects above the row,
        // 1px apart (line_h=1, gap=1).
        assert!(
            svg.matches("shape-rendering=\"crispEdges\"").count() >= 3,
            "header rule plus the total row's double rule:\n{svg}"
        );
    }

    // Text wrapping: a text cell too
    // wide for its column wraps onto extra lines instead of clipping, and
    // the row grows to fit the tallest wrapped cell.
    #[test]
    fn long_text_cell_wraps_and_grows_its_row() {
        let rows = vec![
            json!({"company": "Apex Technologies", "revenue": 24_730_000.12}),
            json!({"company": "Bright", "revenue": 8_460_000.0}),
        ];
        // A narrow table with a wide, high-precision currency column starves
        // `company` of width (mirrors precision_high's Apex Technologies
        // wrap in the production golden). `width: 224` (table_w=200) keeps
        // `company` narrower than its full "Apex Technologies" content
        // (still forcing the 2-line wrap) while staying comfortably above
        // the word-floor clamp boundary (~192.5px) so the allocation
        // fits under the post-allocation overflow guard.
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency_full"},
        });
        let build = |wrap: bool| -> Payload {
            let mut theme = base_table_theme();
            theme["wrap"] = json!(wrap);
            let resolved = json!({
                "link_open": "page",
                "title": null,
                "title_style": resolved_title_style(),
                "columns": columns,
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            });
            let payload_json = json!({
                "resolved": resolved,
                "rows": rows,
                "width": 224.0,
                "height": 400.0,
                "background": "#ffffff",
                "fonts_dir": null,
                "padding": null,
            });
            crate::test_payload(payload_json)
        };
        let fonts = test_fonts();
        let svg = render(&build(true), &fonts).expect("renders");
        assert!(
            svg.contains(">Apex</tspan>") && svg.contains(">Technologies</tspan>"),
            "long company name wraps onto two lines:\n{svg}"
        );

        // With wrap disabled, the same board's first row stays single-line —
        // the second row's y must sit strictly higher than in the wrapped
        // render, proving the wrapped row actually grew.
        let svg_unwrapped = render(&build(false), &fonts).expect("renders");
        assert!(!svg_unwrapped.contains(">Apex</tspan>"), "wrap: false keeps the cell single-line:\n{svg_unwrapped}");

        let second_row_y = |s: &str| -> f64 {
            let before = s.split("Bright</text>").next().expect("Bright cell found");
            let head = before.rsplit("<text x=\"").next().expect("text open tag found");
            head.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(
            second_row_y(&svg) > second_row_y(&svg_unwrapped),
            "wrapped row must grow, pushing the next row further down:\nwrapped:\n{svg}\nunwrapped:\n{svg_unwrapped}"
        );
    }

    /// Regression: `row_text_baseline` used `row_heights[ri] - 8.0`, which only equals the real
    /// formula (`per_row_height / 2 + text_baseline_offset`) at the shipped `row.height: 24.0` /
    /// `text_baseline_offset: 4.0` combination — the first thing that breaks it is a wrap-grown
    /// row. Single row, one company cell forced to wrap onto 2 lines: with `font_size: 12.0` and
    /// `text_baseline_offset: 4.0`, `this_row_h` grows to `2*16 - 4 + 16 = 44.0`. The wrapped cell
    /// centers itself correctly via its own (unrelated) `first_y` formula; the numeric lane in the
    /// SAME row must land at the SAME vertical center — production baseline is `44/2 + 4 = 26` from
    /// row top, `first_y + 8` in terms of the wrapped block's own first line. The old formula put
    /// it at `44 - 8 = 36` from row top (`first_y + 18`), 10px low.
    #[test]
    fn wrap_grown_row_centers_non_wrapped_cells_at_the_correct_baseline() {
        let rows = vec![json!({"company": "Apex Technologies", "revenue": 5.0})];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"format": "currency_whole"},
        });
        let mut theme = base_table_theme();
        theme["font"]["size"] = json!(12.0);
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            // Narrow enough that "Apex Technologies" wraps even after the
            // fit cascade steps the font down and the allocator rebalances.
            "width": 150.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"board_width": 600.0, "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()},
        });
        let payload: Payload = crate::test_payload(payload_json);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains(">Apex</tspan>") && svg.contains(">Technologies</tspan>"),
            "company cell must wrap onto two lines for this pin to apply:\n{svg}"
        );

        // The wrapped cell's own first tspan y (`first_y`): row_y + (this_row_h
        // - total_text_h)/2 + font_size — a formula this test does not touch.
        let first_y: f64 = svg
            .split(">Apex</tspan>")
            .next()
            .and_then(|head| head.rsplit("y=\"").next())
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("wrapped first-line y parses");

        // The numeric lane's own text y, in the same (only) row.
        let number_y: f64 = svg
            .split("<text y=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("numeric cell y parses");

        let expected = first_y + 8.0;
        let old_buggy = first_y + 18.0;
        assert!(
            (number_y - expected).abs() < 0.15,
            "numeric lane must center on the grown row (expected {expected}, got {number_y}, \
             old broken formula would give {old_buggy}):\n{svg}"
        );
    }

    /// Test helper: the x of the suffix tspan whose text is `number` immediately preceding it.
    fn regex_extract_suffix_x(svg: &str, number: &str) -> String {
        let marker = format!(">{number}</tspan><tspan x=\"");
        let after = svg.split(&marker).nth(1).expect("number+suffix tspan pair found");
        after.split('"').next().unwrap().to_string()
    }

    /// Test helper: the x attribute of the number tspan whose text is `number`.
    fn regex_extract_number_end_x(svg: &str, number: &str) -> String {
        let marker = format!(">{number}</tspan>");
        let before = svg.split(&marker).next().expect("number tspan found");
        let head = before.rsplit("<tspan x=\"").next().expect("tspan open tag found");
        head.split('"').next().unwrap().to_string()
    }

    // ── Date lane default formatting ─────────────────────────────────────────

    #[test]
    fn right_aligned_iso_date_column_formats_with_the_engine_default() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = vec![
            json!({"event": "Launch", "date": "2024-03-15"}),
            json!({"event": "Close", "date": "2024-06-01"}),
        ];
        let columns = json!({
            "event": {"label": "Event"},
            "date": {"label": "Date", "align": "right"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("15 Mar 2024"), "ISO date must format as 'D Mon YYYY':\n{svg}");
        assert!(svg.contains("1 Jun 2024"), "day is not zero-padded:\n{svg}");
        assert!(!svg.contains("2024-03-15"), "raw ISO string must not leak through:\n{svg}");
    }

    /// `_compute_lane_positions` clamps a date cell's measured width to the
    /// column's content area, so a pinned column narrower than its dates still
    /// anchors the lane's right edge at the cell's content edge.
    #[test]
    fn a_date_lane_wider_than_its_pinned_column_clamps_to_the_content_area() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = vec![json!({"event": "Launch", "date": "2024-03-15"})];
        let columns = json!({
            "event": {"label": "Event"},
            "date": {"label": "Date", "align": "right", "width": 50},
        });
        let svg = render(&make_payload(columns, rows, None, 20), &test_fonts()).expect("renders");
        let lane_tspan: f64 = svg
            .split("<text x=\"")
            .skip(1)
            .find(|t| t.contains("text-anchor=\"end\""))
            .and_then(|t| t.split('"').next())
            .unwrap()
            .parse()
            .unwrap();
        // The clamped lane ends on the content edge, where the header rule ends.
        assert_eq!(lane_tspan, 582.0, "{svg}");
        // Production cuts the too-wide date instead of drawing it whole.
        assert!(!svg.contains("15 Mar 2024"), "the date is cut, not spilled:\n{svg}");
        assert!(svg.contains("\u{2026}</text>"), "{svg}");
    }

    #[test]
    fn left_aligned_date_like_text_column_is_not_promoted_to_the_date_lane() {
        let _locale = crate::time_locale::en_us_scope();
        // Invariant #11: the date lane is gated on the resolved `align`
        // verdict, not re-derived from content alone — a column the
        // resolver didn't right-align stays a plain text column even when
        // every cell happens to look like a date.
        let rows = vec![json!({"event": "Launch", "date": "2024-03-15"})];
        let columns = json!({
            "event": {"label": "Event"},
            "date": {"label": "Date"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains(">15 Mar 2024</text>"),
            "a non-right-aligned column paints the formatted date as a plain text cell:\n{svg}"
        );
    }

    #[test]
    fn numeric_cell_mixed_into_a_date_lane_column_renders_the_number_not_the_null_glyph() {
        let _locale = crate::time_locale::en_us_scope();
        // Invariant #11: a right-aligned column mixing ISO dates and numbers
        // still enters the date lane (is_date fires when *any* cell is
        // date-like), but a Value::Number cell in that lane must render its
        // number, not fall through to the em-dash null sentinel — a present
        // value silently becoming "no value" is a wrong result that looks
        // right.
        let rows = vec![
            json!({"event": "Launch", "date": "2024-03-15"}),
            json!({"event": "Fiscal year", "date": 777}),
        ];
        let columns = json!({
            "event": {"label": "Event"},
            "date": {"label": "Date", "align": "right"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains("15 Mar 2024"), "the real date still formats:\n{svg}");
        assert!(svg.contains("777"), "the numeric cell must render its value, not the null glyph:\n{svg}");
        assert!(!svg.contains('\u{2014}'), "no cell here should fall back to the em-dash null sentinel:\n{svg}");
    }

    #[test]
    fn null_numeric_cell_renders_the_house_null_glyph_not_a_blank_cell() {
        let rows = vec![
            json!({"company": "Acme", "growth": 0.1}),
            json!({"company": "Total", "growth": null}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "growth": {"format": "percent"},
        });
        let payload = make_payload(columns, rows, None, 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains('\u{2014}'), "a null numeric cell must show the em-dash null glyph, not a blank cell:\n{svg}");
    }

    // ── Summary/total row gap ────────────────────────────────────────────────

    #[test]
    fn summary_role_row_gets_extra_gap_before_it() {
        let rows = vec![
            json!({"company": "Acme", "revenue": 100.0, "row_role": "value"}),
            json!({"company": "Globex", "revenue": 200.0, "row_role": "value"}),
            json!({"company": "Total", "revenue": 300.0, "row_role": "total"}),
        ];
        let columns = json!({
            "company": {"label": "Company"},
            "revenue": {"label": "Revenue"},
        });
        let payload = make_payload(columns, rows, Some("row_role"), 20);
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        let height: f64 = svg
            .split("height=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .expect("svg height attribute")
            .parse()
            .expect("numeric height");
        // base_table_theme's row.height is 32.0 -> gap = int(32*0.4) = 12.
        // Without the gap the three 32px rows would put the bottom at a
        // height 12px lower than with it.
        let rows_no_gap = make_payload(
            json!({"company": {"label": "Company"}, "revenue": {"label": "Revenue"}}),
            vec![
                json!({"company": "Acme", "revenue": 100.0}),
                json!({"company": "Globex", "revenue": 200.0}),
                json!({"company": "Total", "revenue": 300.0}),
            ],
            None,
            20,
        );
        let svg_no_gap = render(&rows_no_gap, &fonts).expect("renders");
        let height_no_gap: f64 = svg_no_gap
            .split("height=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .expect("svg height attribute")
            .parse()
            .expect("numeric height");
        assert!(
            (height - height_no_gap - 12.0).abs() < 0.01,
            "a total row must add exactly one 12px summary gap: with={height} without={height_no_gap}"
        );
    }

    #[test]
    fn format_numeric_col_currency_below_one_falls_back() {
        let _scope = en_us_scope();
        // A `format: currency` column with no baked pad table and no
        // shared_scale takes this function's spec-only branch.
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            json!({"amount": 0.67}).as_object().unwrap().clone(),
            json!({"amount": 0.0}).as_object().unwrap().clone(),
            json!({"amount": 12.99}).as_object().unwrap().clone(),
            json!({"amount": 0.0023}).as_object().unwrap().clone(),
        ];
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot::from("currency")),
                ..Default::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let row_roles = [
            RowRole::Value,
            RowRole::Value,
            RowRole::Value,
            RowRole::Value,
        ];
        let cells = format_numeric_col(&rows, &col, "all", &row_roles).expect("formats");

        assert_eq!(cells[0].prefix.as_deref(), Some("$"));
        assert_eq!(cells[0].text, "0.67", "67 cents must not read as SI milli");

        // Zero is exactly representable, not "below the minor unit" -- must
        // not take the two-decimal fallback either.
        assert_eq!(cells[1].text, "0");

        // At or above $1: unchanged, ordinary SI rendering.
        assert_eq!(cells[2].text, "13");

        // Below half a cent the $,.2f fallback cannot represent the value --
        // it would paint "0.00", indistinguishable from the genuine zero two
        // rows up. Significant digits instead, matching format_kpi_parts.
        assert_eq!(cells[3].text, "0.0023", "sub-cent money must not render as 0.00");
    }

    // ── In-cell spark marks: bar / bar-normalize / columns ──────────────────

    fn unsigned() -> SparkLayout<'static> {
        SparkLayout { has_negative: false, negative_tone: None, font_size: 11.0, body_family: "Inter" }
    }

    fn test_spark_theme() -> SparkTheme {
        SparkTheme {
            color: "#6366f1".to_string(),
            padding: SparkPaddingTheme { top: 4.0, right: 2.0, bottom: 4.0, left: 2.0 },
            empty: SparkEmptyTheme {
                inset_x: 4.0,
                stroke: SparkEmptyStroke {
                    color: "#d1d5db".to_string(),
                    width: 1.0,
                    dasharray: "4 2".to_string(),
                },
            },
            single_value: SparkSingleValueTheme { inset_x: 4.0, marker_radius: 2.5 },
            columns: SparkColumnsTheme {
                gap: 1.0,
                padding: 2.0,
                min_bar_height: 1.0,
                border: SparkRadiusTheme { radius: 1.0 },
            },
            bar: SparkBarTheme {
                background: "#e5e7eb".to_string(),
                color: "#23467f".to_string(),
                default_max: 100.0,
                border: SparkRadiusTheme { radius: 0.0 },
                label: SparkBarLabelTheme { inset_x: 4.0, fill: "#FFFFFF".to_string(), fill_opacity: 0.9 },
            },
            column: SparkColumnTheme { width: 16.0 },
            area: SparkAreaTheme { fill_opacity: 0.3 },
        }
    }

    fn spark_cfg(kind: &str) -> SparkConfig {
        SparkConfig {
            spark_type: kind.to_string(),
            color: None,
            height: None,
            width: None,
            max: None,
            thresholds: None,
            background: None,
            border_radius: None,
            value_visible: None,
            value_suffix: None,
            fill_opacity: None,
            negative_color: None,
            last_visible: None,
            min_max_visible: None,
        }
    }

    #[test]
    fn spark_bar_no_track_scales_against_an_explicit_max() {
        // Ground truth: production's render_spark_bar(650, max_value=812,
        // width=200, height=16, color="#222222") — `bar` (not
        // bar-normalize): no background track, fill_x bare "0", fill_width
        // genuine `:.1f`, height bare (a Python int), rx `py_float` of the
        // theme's 0.0 border radius.
        let got = print(&render_spark_bar(650.0, 812.0, 200.0, 16.0, "#222222", false, "#e5e7eb", 0.0, false, None));
        assert_eq!(
            got,
            "<rect x=\"0\" y=\"0\" width=\"160.1\" height=\"16\" fill=\"#222222\" rx=\"0.0\"/>"
        );
    }

    #[test]
    fn spark_bar_normalize_draws_background_track_then_scaled_fill() {
        // Ground truth: render_spark_bar(61, max_value=100, width=214,
        // height=16, normalize=True) — track first (fill="e5e7eb"), then
        // the fill rect on top.
        let got = print(&render_spark_bar(61.0, 100.0, 214.0, 16.0, "#23467f", true, "#e5e7eb", 0.0, false, None));
        assert_eq!(
            got,
            "<rect x=\"0\" y=\"0\" width=\"214\" height=\"16\" fill=\"#e5e7eb\" rx=\"0.0\"/>\
             <rect x=\"0\" y=\"0\" width=\"130.5\" height=\"16\" fill=\"#23467f\" rx=\"0.0\"/>"
        );
    }

    #[test]
    fn spark_bar_zero_fill_draws_the_track_but_no_fill_rect() {
        // The `fill_width > 0` gate: a zero value draws
        // the normalize track alone.
        let got = print(&render_spark_bar(0.0, 100.0, 214.0, 16.0, "#23467f", true, "#e5e7eb", 0.0, false, None));
        assert_eq!(
            got,
            "<rect x=\"0\" y=\"0\" width=\"214\" height=\"16\" fill=\"#e5e7eb\" rx=\"0.0\"/>"
        );
    }

    #[test]
    fn spark_columns_min_rebased_layout_matches_production() {
        // Ground truth: render_spark_columns([271,284,133,50,112,101,596,61],
        // width=228.8, height=16) — no negative, baseline at the array's own
        // minimum (61 -> the shortest bar, at `min_bar_height`).
        let values = [271.0, 284.0, 133.0, 50.0, 112.0, 101.0, 596.0, 61.0];
        let theme = test_spark_theme();
        let got = print(&render_spark_columns(&values, 228.8, 16.0, "#6366f1", None, &theme.columns));
        assert_eq!(
            got,
            "<rect x=\"0.0\" y=\"9.1\" width=\"27.7\" height=\"4.9\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"28.7\" y=\"8.9\" width=\"27.7\" height=\"5.1\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"57.5\" y=\"12.2\" width=\"27.7\" height=\"1.8\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"86.2\" y=\"13.0\" width=\"27.7\" height=\"1.0\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"114.9\" y=\"12.6\" width=\"27.7\" height=\"1.4\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"143.6\" y=\"12.9\" width=\"27.7\" height=\"1.1\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"172.4\" y=\"2.0\" width=\"27.7\" height=\"12.0\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"201.1\" y=\"13.0\" width=\"27.7\" height=\"1.0\" fill=\"#6366f1\" rx=\"1.0\"/>"
        );
    }

    #[test]
    fn spark_columns_switches_to_midline_layout_when_the_array_has_a_negative() {
        // Ground truth: render_spark_columns([-40,-30,20,10], width=100,
        // height=24) — one negative flips the whole cell to a zero-midline
        // layout scaled against the array's own max-abs (40), grown up for
        // positive / down for negative.
        let values = [-40.0, -30.0, 20.0, 10.0];
        let theme = test_spark_theme();
        let got = print(&render_spark_columns(&values, 100.0, 24.0, "#6366f1", None, &theme.columns));
        assert_eq!(
            got,
            "<rect x=\"0.0\" y=\"12.0\" width=\"24.2\" height=\"10.0\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"25.2\" y=\"12.0\" width=\"24.2\" height=\"7.5\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"50.5\" y=\"7.0\" width=\"24.2\" height=\"5.0\" fill=\"#6366f1\" rx=\"1.0\"/>\
             <rect x=\"75.8\" y=\"9.5\" width=\"24.2\" height=\"2.5\" fill=\"#6366f1\" rx=\"1.0\"/>"
        );
    }

    /// A non-finite element keeps its slot, draws no bar and does not poison
    /// the domain: -5 and 5 keep their real heights, scaled against 5.
    #[test]
    fn a_nan_element_in_spark_columns_does_not_erase_the_real_bars() {
        let theme = test_spark_theme();
        let signed = print(&render_spark_columns(&[f64::NAN, -5.0, 5.0], 100.0, 24.0, "#6366f1", None, &theme.columns));
        assert_eq!(signed.matches("<rect").count(), 2);
        assert!(!signed.contains("height=\"1.0\""), "{signed}");
        let positive =
            print(&render_spark_columns(&[f64::NAN, 10.0, 20.0, 30.0], 100.0, 24.0, "#6366f1", None, &theme.columns));
        assert_eq!(positive.matches("<rect").count(), 3);
        let infinite = print(&render_spark_columns(&[f64::NEG_INFINITY, 10.0], 100.0, 24.0, "#6366f1", None, &theme.columns));
        assert_eq!(infinite.matches("<rect").count(), 1);
    }

    #[test]
    fn signed_fraction_unsigned_clamps_to_zero_and_scales() {
        assert_eq!(signed_fraction(650.0, 812.0, false), (650.0 / 812.0, false));
        assert_eq!(signed_fraction(-10.0, 812.0, false), (0.0, false), "negatives clamp to zero");
    }

    #[test]
    fn signed_fraction_signed_reports_which_side_of_zero() {
        let (fraction, is_negative) = signed_fraction(-40.0, 40.0, true);
        assert_eq!(fraction, 1.0);
        assert!(is_negative);
        let (fraction, is_negative) = signed_fraction(20.0, 40.0, true);
        assert_eq!(fraction, 0.5);
        assert!(!is_negative);
    }

    #[test]
    fn spark_cell_sizing_truncates_the_default_width_and_height_like_pythons_int() {
        // Spark cell sizing: padding=8, spark_width defaults
        // to `int(cell_width - 16)`, spark_height to `int(row_height - 8)`
        // — `int()` truncates, matching `f64::trunc()` for these positive
        // extents.
        let spark = spark_cfg("bar");
        let theme = test_spark_theme();
        let (_, w, h) = render_spark_cell("col", &json!(650.0), &spark, 216.0, 24.0, &theme, Some(812.0), &unsigned())
            .expect("no error")
            .expect("bar renders");
        assert_eq!(w, 200.0, "int(216 - 16) = 200");
        assert_eq!(h, 16.0, "int(24 - 8) = 16");
    }

    #[test]
    fn spark_cell_sizing_caps_an_authored_width_and_height() {
        // The cap (`int(cell_width - padding)` / `int(row_height - 4)`)
        // applies even to an authored override — a `min(...)`
        // runs unconditionally after the default-or-authored branch.
        let mut spark = spark_cfg("bar");
        spark.width = Some(300.0);
        spark.height = Some(300.0);
        let theme = test_spark_theme();
        let (_, w, h) = render_spark_cell("col", &json!(50.0), &spark, 100.0, 24.0, &theme, Some(100.0), &unsigned())
            .expect("no error")
            .expect("bar renders");
        assert_eq!(w, 92.0, "int(100 - 8) cap wins over the authored 300");
        assert_eq!(h, 20.0, "int(24 - 4) cap wins over the authored 300");
    }

    #[test]
    fn spark_cell_bar_with_a_non_numeric_value_draws_nothing() {
        // `_render_spark_cell`'s `coerce_numeric_cell(value) is None` gate
        // A bar/bar-normalize cell whose value isn't
        // numeric renders nothing (falls through to the ordinary cell
        // paint at the caller), never a zero-width bar.
        let spark = spark_cfg("bar");
        let theme = test_spark_theme();
        let result = render_spark_cell("col", &json!("Zed"), &spark, 100.0, 24.0, &theme, Some(100.0), &unsigned())
            .expect("no error");
        assert!(result.is_none());
    }

    #[test]
    fn spark_cell_with_an_unmatched_type_refuses_instead_of_aborting() {
        // render_spark_cell is the only production-reachable caller (guarded
        // by render()'s own column-config parse-time gate), but the function
        // itself returns `Result<_, String>` — a drift between that gate and
        // this match should refuse, not `unreachable!()`-abort the process.
        let spark = spark_cfg("sparkle");
        let theme = test_spark_theme();
        let err = render_spark_cell("col", &json!(5.0), &spark, 100.0, 24.0, &theme, None, &unsigned()).unwrap_err();
        assert!(err.contains("sparkle"), "{err}");
    }

    #[test]
    fn spark_cell_bar_with_no_authored_max_uses_the_columns_auto_max() {
        // The spark dispatcher: only `bar` (never
        // `bar-normalize`) reads the table-column-wide auto-max when no
        // `max:` is authored.
        let spark = spark_cfg("bar");
        let theme = test_spark_theme();
        let (content, ..) = render_spark_cell("col", &json!(650.0), &spark, 216.0, 24.0, &theme, Some(812.0), &unsigned())
            .expect("no error")
            .expect("renders");
        let content = print(&content);
        assert!(content.contains("160.1"), "auto-max 812 must scale the fill: {content}");
    }

    #[test]
    fn spark_cell_bar_normalize_ignores_the_columns_auto_max() {
        let mut spark = spark_cfg("bar-normalize");
        spark.max = Some(100.0);
        let theme = test_spark_theme();
        let (content, ..) =
            render_spark_cell("col", &json!(61.0), &spark, 230.0, 24.0, &theme, Some(9999.0), &unsigned())
                .expect("no error")
                .expect("renders");
        let content = print(&content);
        assert!(content.contains("130.5"), "must scale against its own max, not the auto-max: {content}");
    }

    #[test]
    fn spark_cell_translate_uses_an_integer_x_and_a_bare_float_y() {
        // Paint site: `spark_x = px(cell_x + 4)` (an int),
        // `spark_y = row_y + (per_row_height - spark_height) / 2` (a
        // genuine float, T1-law bare interpolation).
        let rows = vec![json!({"c": "Alpha", "v": 42})];
        let columns =
            json!({"c": {"label": "C"}, "v": {"label": "V", "spark": {"type": "bar", "max": 100.0}}});
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let marker = "<g transform=\"translate(";
        let g_start = svg.find(marker).expect("spark g emitted");
        assert_eq!(
            &svg[g_start..g_start + marker.len() + "364, 82.0)".len()],
            "<g transform=\"translate(364, 82.0)",
            "exact geometry, not just the int-x/float-y format — a cell_x + 8 \
             or row_h-centering mutant must fail this:\n{svg}"
        );
    }

    #[test]
    fn spark_cell_centers_on_the_grown_row_height_not_the_nominal_one() {
        // Distinct from the previous test's unwrapped row (where
        // `this_row_h == row_h`, so a `row_h`-centering mutant is inert): a
        // wrapped text cell in the SAME row grows `this_row_h` past the
        // nominal `row_h`, and the spark mark must center on the grown band.
        let rows = vec![json!({"c": "Alpha Beta Gamma Delta", "v": 42})];
        let columns = json!({
            "c": {"label": "C", "width": 80},
            "v": {"label": "V", "spark": {"type": "bar", "max": 100.0}},
        });
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let marker = "<g transform=\"translate(";
        let g_start = svg.find(marker).expect("spark g emitted");
        let rest = &svg[g_start + marker.len()..];
        let coords = rest.split(')').next().expect("closing paren");
        let y: f64 = coords.split(',').nth(1).unwrap().trim().parse().unwrap();
        // Nominal row_h (base_table_theme) is 32; header ends around y=62,
        // so an unwrapped row would center near y=75 (see the previous
        // test). A wrapped row's true center sits measurably lower.
        assert!(y > 80.0, "spark must center on the row's own grown height, not the nominal one: y={y}\n{svg}");
    }

    #[test]
    fn spark_columns_cell_classifies_non_numeric_and_never_paints_the_item_count_text() {
        let rows = vec![json!({"c": "Alpha", "v": [1, 2, 3]}), json!({"c": "Beta", "v": [4, 5]})];
        let columns = json!({"c": {"label": "C"}, "v": {"label": "V", "spark": {"type": "columns"}}});
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        // Header centers only numeric/date columns (invariant #11); a spark
        // `columns` cell's array value classifies non-numeric (same as any
        // other array-valued column), so its header stays start-anchored.
        let header_idx = svg.find(">V<").expect("header V");
        let tag_start = svg[..header_idx].rfind("<text").expect("text tag");
        assert!(
            svg[tag_start..header_idx].contains("text-anchor=\"start\""),
            "spark columns header must stay left-anchored (non-numeric classification):\n{svg}"
        );
        assert!(!svg.contains("items]"), "measurement-only text must never paint:\n{svg}");
        let marker = "<g transform=\"translate(";
        let rect_counts: Vec<usize> = svg
            .match_indices(marker)
            .map(|(i, _)| svg[i..].split("</g>").next().unwrap().matches("<rect").count())
            .collect();
        assert_eq!(
            rect_counts,
            vec![3, 2],
            "one rect per array element, per row's own [1,2,3] / [4,5]:\n{svg}"
        );
    }

    #[test]
    fn spark_cell_with_a_null_value_draws_no_mark_and_falls_through() {
        let rows = vec![json!({"c": "Alpha", "v": null})];
        let columns =
            json!({"c": {"label": "C"}, "v": {"label": "V", "spark": {"type": "bar", "max": 100.0}}});
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            !svg.contains("<g transform=\"translate("),
            "a null spark cell draws no mark:\n{svg}"
        );
        assert!(svg.contains(NULL_DISPLAY), "falls through to the ordinary null glyph:\n{svg}");
    }

    #[test]
    fn spark_bar_cell_with_a_clamped_to_zero_value_falls_through_to_the_cell_text() {
        // The `if spark_content:` truthiness check: a plain
        // `bar` (no track) whose fill_width clamps to <= 0
        // returns EMPTY inner content, so production takes the falsy branch
        // and paints the ordinary formatted cell text instead — a `0` cell
        // shows "0", never a blank mark.
        let rows = vec![json!({"c": "Alpha", "v": 0})];
        let columns =
            json!({"c": {"label": "C"}, "v": {"label": "V", "spark": {"type": "bar", "max": 100.0}}});
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            !svg.contains("<g transform=\"translate("),
            "an empty-content bar mark must not draw an empty <g>:\n{svg}"
        );
        assert!(svg.contains(">0<"), "falls through to painting the cell's own text:\n{svg}");
    }

    #[test]
    fn spark_bar_normalize_cell_with_a_zero_value_still_draws_its_track() {
        // Symmetric check: bar-normalize's background track makes the
        // returned content non-empty even at value 0 (only the fill rect is
        // gated on fill_width > 0), so production DOES draw the mark there.
        let rows = vec![json!({"c": "Alpha", "v": 0})];
        let columns = json!({
            "c": {"label": "C"},
            "v": {"label": "V", "spark": {"type": "bar-normalize", "max": 100.0}},
        });
        let payload = make_payload(columns, rows, None, 20);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("<g transform=\"translate("),
            "bar-normalize still draws its track at value 0:\n{svg}"
        );
    }

    #[test]
    fn coerce_numeric_cell_rejects_a_non_finite_string() {
        // utils.coerce_numeric_cell's non-finite guard: a string that
        // parses to NaN/±Infinity still isn't a numeric cell.
        assert_eq!(coerce_numeric_cell(Some(&json!("inf"))), None);
        assert_eq!(coerce_numeric_cell(Some(&json!("-inf"))), None);
        assert_eq!(coerce_numeric_cell(Some(&json!("NaN"))), None);
        assert_eq!(coerce_numeric_cell(Some(&json!("1e999"))), None);
        assert_eq!(coerce_numeric_cell(Some(&json!("5"))), Some(5.0));
    }


    fn spark_payload(spark_json: Value, value: Value) -> Payload {
        let rows = vec![json!({"c": "A", "v": value})];
        let columns = json!({
            "c": {"label": "C"},
            "v": {"label": "V", "spark": spark_json},
        });
        make_payload(columns, rows, None, 20)
    }

    #[test]
    fn spark_area_draws_a_polygon_under_its_polyline() {
        let payload = spark_payload(json!({"type": "area"}), json!([1, 2, 3]));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<polygon points=\"") && svg.contains("fill-opacity=\"0.3\"/><polyline"), "{svg}");
    }

    #[test]
    fn a_nan_element_in_a_spark_line_or_area_is_skipped() {
        let nan = json!({"non_finite": "nan", "text": "nan"});
        for kind in ["line", "area"] {
            let with_nan = spark_payload(json!({"type": kind}), json!([1, nan, 3, 2]));
            let without = spark_payload(json!({"type": kind}), json!([1, 3, 2]));
            let points = |payload: &Payload| {
                let svg = render(payload, &test_fonts()).unwrap();
                let at = svg.find("<polyline points=\"").expect("a polyline") + 18;
                svg[at..at + svg[at..].find('"').unwrap()].to_string()
            };
            assert_eq!(points(&with_nan), points(&without), "{kind}");
        }
    }

    #[test]
    fn a_non_numeric_spark_element_is_refused_without_echoing_the_cell() {
        let payload = spark_payload(json!({"type": "line"}), json!([1, "x", 3]));
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("not a number") && !err.contains("non_finite") && !err.contains('"'), "{err}");
    }

    #[test]
    fn spark_column_is_a_narrow_bottom_anchored_bar() {
        let payload = spark_payload(json!({"type": "column"}), json!(50));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("width=\"16\" height=\"12.0\""), "{svg}");
    }

    #[test]
    fn spark_value_visible_labels_the_bar() {
        let payload = spark_payload(json!({"type": "bar", "value_visible": true}), json!(20));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("dominant-baseline=\"central\"") && svg.contains(">20</text>"), "{svg}");
    }

    /// A column holding a negative detail value draws every bar from its
    /// midline, and `negative_color` paints the negative ones in the tone.
    #[test]
    fn a_signed_spark_bar_column_grows_from_its_midline_in_the_negative_tone() {
        let rows = vec![json!({"c": "A", "v": 10}), json!({"c": "B", "v": -10})];
        let columns = json!({
            "c": {"label": "C"},
            "v": {"label": "V", "spark": {"type": "bar", "negative_color": true}},
        });
        let mut payload = make_payload(columns, rows, None, 20);
        payload.board_style = crate::test_board_style(json!({
            "tones": {"positive": "#00aa00", "negative": "#cc0000", "warning": "#cc9900", "info": "#0000cc"}
        }));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("fill=\"#cc0000\""), "negative bar takes the tone:\n{svg}");
        assert!(svg.matches("<rect x=\"").count() >= 2, "{svg}");
    }
}

#[cfg(test)]
mod conditional_formatting_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::*;
    use super::layout::column_demand;
    use super::bind::resolve_palette_stops;
    use super::bind::resolve_hinge;
    use super::bind::resolve_conditional_styles;
    use super::bind::resolve_cell_glyph;
    use super::bind::match_predicate;
    use super::bind::interpolate_scale_color;
    use super::bind::font_weight_as_css;
    use super::bind::compute_scale_domain;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn tones() -> TonesStyle {
        serde_json::from_value(json!({
            "positive": "#00884d",
            "negative": "#94001e",
            "warning": "#e69812",
            "info": "#008cdd"
        }))
        .unwrap()
    }

    fn rule(json: Value) -> WhenRule {
        serde_json::from_value(json).unwrap()
    }

    // ── match_predicate ────────────────────────────────────────────────

    #[test]
    fn eq_matches_a_string_value() {
        let r = rule(json!({"eq": "urgent", "glyph": "x"}));
        assert!(match_predicate(&r, Some(&json!("urgent"))).unwrap());
        assert!(!match_predicate(&r, Some(&json!("normal"))).unwrap());
    }

    #[test]
    fn eq_bool_type_guard_rejects_a_cross_type_match() {
        // isinstance(value, bool) != isinstance(rule.eq, bool) -> False,
        // even though 1 == True in Python.
        let r = rule(json!({"eq": true, "glyph": "x"}));
        assert!(!match_predicate(&r, Some(&json!(1))).unwrap());
        assert!(match_predicate(&r, Some(&json!(true))).unwrap());
    }

    #[test]
    fn ne_bool_type_guard_matches_on_a_cross_type_mismatch() {
        // A cross-type pair against `ne` always satisfies it (return True),
        // the mirror image of eq's guard.
        let r = rule(json!({"ne": true, "glyph": "x"}));
        assert!(match_predicate(&r, Some(&json!(1))).unwrap());
        assert!(!match_predicate(&r, Some(&json!(true))).unwrap());
    }

    #[test]
    fn numeric_operators_coerce_a_numeric_string() {
        let lte = rule(json!({"lte": 30, "glyph": "x"}));
        assert!(match_predicate(&lte, Some(&json!("29.5"))).unwrap());
        assert!(!match_predicate(&lte, Some(&json!("30.5"))).unwrap());
        let gt = rule(json!({"gt": 90, "glyph": "x"}));
        assert!(match_predicate(&gt, Some(&json!(91))).unwrap());
        let between = rule(json!({"between": [10, 20], "glyph": "x"}));
        assert!(match_predicate(&between, Some(&json!(15))).unwrap());
        assert!(!match_predicate(&between, Some(&json!(21))).unwrap());
    }

    #[test]
    fn numeric_operator_on_a_non_numeric_value_does_not_match() {
        let r = rule(json!({"gt": 1, "glyph": "x"}));
        assert!(!match_predicate(&r, Some(&json!("Zed"))).unwrap());
        assert!(!match_predicate(&r, None).unwrap());
    }

    #[test]
    fn is_null_matches_a_missing_or_explicit_null_value() {
        let r = rule(json!({"is_null": true, "glyph": "x"}));
        assert!(match_predicate(&r, None).unwrap());
        assert!(!match_predicate(&r, Some(&json!(5))).unwrap());
    }

    #[test]
    fn in_matches_any_scalar_candidate() {
        let r = rule(json!({"in": ["a", "b"], "glyph": "x"}));
        assert!(match_predicate(&r, Some(&json!("b"))).unwrap());
        assert!(!match_predicate(&r, Some(&json!("c"))).unwrap());
    }

    /// `ConditionalRule.model_dump` writes the predicate as `in`.
    #[test]
    fn in_reads_the_key_the_python_model_dumps() {
        let rules: Vec<WhenRule> =
            serde_json::from_value(json!([{"in": ["a"], "glyph": "x"}])).unwrap();
        assert!(match_predicate(&rules[0], Some(&json!("a"))).unwrap());
    }

    #[test]
    fn in_with_a_non_scalar_candidate_is_not_supported() {
        let r = rule(json!({"in": [["nested"]], "glyph": "x"}));
        let err = match_predicate(&r, Some(&json!("nested"))).unwrap_err();
        assert!(err.contains("non-scalar"), "{err}");
    }

    #[test]
    fn a_rule_with_no_active_predicate_is_not_supported() {
        let r = rule(json!({"glyph": "x"}));
        let err = match_predicate(&r, Some(&json!(1))).unwrap_err();
        assert!(err.contains("no active predicate"), "{err}");
    }

    #[test]
    fn a_shared_evaluator_refusal_carries_the_table_prefix() {
        let rules = vec![rule(json!({"in": [["nested"]], "glyph": "x"}))];
        let err = resolve_cell_glyph(&ColConfig::default(), &rules, Some(&json!("nested")))
            .unwrap_err();
        assert!(
            err.starts_with("table: conditional rule 'in' with a non-scalar"),
            "{err}"
        );
    }

    // ── resolve_conditional_styles ─────────────────────────────────────

    #[test]
    fn later_matching_rules_win_over_earlier_ones_per_key() {
        let rules = vec![
            rule(json!({"gt": 0, "background": "#111111", "glyph": "a"})),
            rule(json!({"gt": 10, "background": "#222222"})),
        ];
        let out = resolve_conditional_styles(&rules, Some(&json!(20)), None).unwrap();
        assert_eq!(out.background.as_deref(), Some("#222222"));
        // The second rule set no glyph, so the first rule's glyph survives
        // — only an explicit glyph on a LATER rule clears the color, and
        // only a later rule that ITSELF sets glyph overwrites glyph too.
        assert_eq!(out.glyph.as_deref(), Some("a"));
    }

    #[test]
    fn default_rule_only_fires_when_nothing_else_matched() {
        let rules = vec![
            rule(json!({"gt": 100, "background": "#111111"})),
            rule(json!({"default": true, "background": "#999999"})),
        ];
        let matched = resolve_conditional_styles(&rules, Some(&json!(200)), None).unwrap();
        assert_eq!(matched.background.as_deref(), Some("#111111"));
        let fell_through = resolve_conditional_styles(&rules, Some(&json!(5)), None).unwrap();
        assert_eq!(fell_through.background.as_deref(), Some("#999999"));
    }

    #[test]
    fn a_later_glyph_rule_pops_an_earlier_rules_glyph_color() {
        let rules = vec![
            rule(json!({"gt": 0, "glyph": "a", "glyph_color": "#ff0000"})),
            rule(json!({"gt": 10, "glyph": "b"})),
        ];
        let out = resolve_conditional_styles(&rules, Some(&json!(20)), None).unwrap();
        assert_eq!(out.glyph.as_deref(), Some("b"));
        assert_eq!(out.glyph_color, None, "later glyph clears the earlier rule's color");
    }

    #[test]
    fn tone_resolves_a_glyph_color_from_board_tones() {
        let t = tones();
        let rules = vec![rule(json!({"gt": 90, "glyph": "\u{25d0}", "tone": "warning"}))];
        let out = resolve_conditional_styles(&rules, Some(&json!(95)), Some(&t)).unwrap();
        assert_eq!(out.glyph_color.as_deref(), Some("#e69812"));
    }

    #[test]
    fn explicit_glyph_color_wins_over_tone() {
        let t = tones();
        let rules =
            vec![rule(json!({"gt": 90, "glyph": "x", "glyph_color": "#abcdef", "tone": "warning"}))];
        let out = resolve_conditional_styles(&rules, Some(&json!(95)), Some(&t)).unwrap();
        assert_eq!(out.glyph_color.as_deref(), Some("#abcdef"));
    }

    #[test]
    fn a_tone_rule_with_no_tones_sets_the_needs_board_tones_flag_not_an_error() {
        // apply_rule_outputs itself never errors on a missing `tones` —
        // production's own `tones is None` is a by-design skip at every
        // call site, true for the glyph-only
        // callers this crate has too (`resolve_cell_glyph`). Only
        // `resolve_cell_style`, which actually paints `glyph_color`, turns
        // the flag into a refusal — see
        // `a_tone_rule_with_no_board_tones_refuses_at_render` and
        // `a_tone_rule_renders_and_paints_when_board_tones_are_present`.
        let rules = vec![rule(json!({"gt": 90, "glyph": "x", "tone": "warning"}))];
        let overrides = resolve_conditional_styles(&rules, Some(&json!(95)), None).unwrap();
        assert!(overrides.tone_needs_board_tones);
        assert_eq!(overrides.glyph_color, None);
    }

    #[test]
    fn font_weight_style_and_decoration_rule_outputs_are_ported() {
        let rules = vec![rule(json!({
            "gt": 0,
            "font": {"color": "#333333", "weight": 700, "style": "italic", "decoration": "underline"}
        }))];
        let out = resolve_conditional_styles(&rules, Some(&json!(1)), None).unwrap();
        assert_eq!(out.color.as_deref(), Some("#333333"));
        assert_eq!(out.weight.as_deref(), Some("700"));
        assert_eq!(out.style.as_deref(), Some("italic"));
        assert_eq!(out.decoration.as_deref(), Some("underline"));
    }

    #[test]
    fn font_weight_as_css_prints_a_whole_float_as_a_bare_integer() {
        assert_eq!(font_weight_as_css(&json!(700.0)), "700");
        assert_eq!(font_weight_as_css(&json!(400)), "400");
        assert_eq!(font_weight_as_css(&json!("bold")), "bold");
    }

    // ── scale color interpolation ─────────────────────────────────────────

    #[test]
    fn sequential_interpolation_on_bookings_share() {
        // playground/dundersign-commercial-finance/accounts_table's
        // bookings_share column: domain [0.03504673019051552,
        // 0.08099688589572906], dbt-seq-amber stops. Values computed by
        // the interpolation directly.
        let stops: Vec<String> = [
            "#f6edde", "#f0e3cb", "#ead8b8", "#e4cea7", "#ddc495", "#d6b985", "#cfaf75", "#c8a565",
            "#c09b56", "#b89146", "#b08837",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let lo = 0.03504673019051552;
        let hi = 0.08099688589572906;
        assert_eq!(interpolate_scale_color(lo, lo, hi, &stops, None, "asymmetric").unwrap(), "#f6edde");
        assert_eq!(interpolate_scale_color(hi, lo, hi, &stops, None, "asymmetric").unwrap(), "#b08837");
        assert_eq!(
            interpolate_scale_color(0.059968847781419754, lo, hi, &stops, None, "asymmetric")
                .unwrap(),
            "#d3b57e"
        );
    }

    #[test]
    fn diverging_interpolation_on_win_rate() {
        // win_rate: domain [0.3448275923728943, 0.6129032373428345],
        // hinge 0.4, dbt-div-crimson-green stops, asymmetric arm.
        let stops: Vec<String> = [
            "#c07995", "#cb8fa6", "#d5a6b7", "#dfbebd", "#e0d8d1", "#deddd5", "#c4d1b9", "#a1c6a7",
            "#88b891", "#6eaa7b", "#539d64",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let lo = 0.3448275923728943;
        let hi = 0.6129032373428345;
        let hinge = Some(0.4);
        assert_eq!(
            interpolate_scale_color(0.4888888895511627, lo, hi, &stops, hinge, "asymmetric").unwrap(),
            "#9fc5a5"
        );
        assert_eq!(interpolate_scale_color(0.4, lo, hi, &stops, hinge, "asymmetric").unwrap(), "#deddd5");
        assert_eq!(interpolate_scale_color(lo, lo, hi, &stops, hinge, "asymmetric").unwrap(), "#e0d0cb");
        assert_eq!(interpolate_scale_color(hi, lo, hi, &stops, hinge, "asymmetric").unwrap(), "#539d64");

        // `symmetric` arm mode: each arm normalizes against its OWN width
        // (not the longer of the two), so the domain-min value reaches the
        // negative arm's full extreme (the palette's own first stop) rather
        // than the asymmetric case's partial "#e0d0cb".
        assert_eq!(interpolate_scale_color(lo, lo, hi, &stops, hinge, "symmetric").unwrap(), "#c07995");
    }

    #[test]
    fn sequential_with_a_degenerate_domain_returns_the_middle_stop() {
        // `palette[len(palette) // 2]` — for an odd-length palette that's
        // the true center stop.
        let stops: Vec<String> =
            ["#000000", "#808080", "#ffffff"].iter().map(|s| s.to_string()).collect();
        assert_eq!(interpolate_scale_color(5.0, 5.0, 5.0, &stops, None, "asymmetric").unwrap(), "#808080");
    }

    #[test]
    fn diverging_requires_an_odd_stop_count_of_at_least_three() {
        let stops: Vec<String> = ["#000000", "#ffffff"].iter().map(|s| s.to_string()).collect();
        let err = interpolate_scale_color(0.5, 0.0, 1.0, &stops, Some(0.5), "asymmetric").unwrap_err();
        assert!(err.contains("odd number"), "{err}");
    }

    // ── resolve_hinge, resolve_palette_stops, compute_scale_domain ─────

    #[test]
    fn resolve_hinge_explicit_float_short_circuits_the_decision_tree() {
        assert_eq!(resolve_hinge(Some(&json!(0.4)), -100.0, 100.0, None), Some(0.4));
    }

    #[test]
    fn resolve_hinge_degenerate_domain_falls_back_to_sequential() {
        assert_eq!(resolve_hinge(Some(&json!("auto")), 5.0, 5.0, None), None);
    }

    #[test]
    fn resolve_hinge_auto_zero_crossing_pivots_at_zero() {
        assert_eq!(resolve_hinge(Some(&json!("auto")), -5.0, 10.0, None), Some(0.0));
        assert_eq!(resolve_hinge(Some(&json!("auto")), -5.0, 0.0, None), Some(0.0));
    }

    #[test]
    fn resolve_hinge_auto_percent_format_crossing_one_pivots_at_one() {
        assert_eq!(resolve_hinge(Some(&json!("auto")), 0.5, 2.0, Some(".0%")), Some(1.0));
    }

    #[test]
    fn resolve_hinge_auto_falls_back_to_the_domain_midpoint() {
        assert_eq!(resolve_hinge(Some(&json!("auto")), 2.0, 8.0, None), Some(5.0));
    }

    #[test]
    fn resolve_palette_stops_uses_resolved_stops_for_a_named_palette() {
        let target: ScaleTarget = serde_json::from_value(json!({
            "palette": "dbt-seq-amber",
            "resolved_stops": ["#111111", "#222222"],
            "arm_mode": "asymmetric"
        }))
        .unwrap();
        assert_eq!(resolve_palette_stops(&target).unwrap(), vec!["#111111", "#222222"]);
    }

    #[test]
    fn resolve_palette_stops_uses_an_inline_literal_list_directly() {
        let target: ScaleTarget = serde_json::from_value(json!({
            "palette": ["#aaaaaa", "#bbbbbb"],
            "arm_mode": "asymmetric"
        }))
        .unwrap();
        assert_eq!(resolve_palette_stops(&target).unwrap(), vec!["#aaaaaa", "#bbbbbb"]);
    }

    #[test]
    fn resolve_palette_stops_without_resolved_stops_is_not_supported() {
        let target: ScaleTarget = serde_json::from_value(json!({
            "palette": "dbt-seq-amber",
            "arm_mode": "asymmetric"
        }))
        .unwrap();
        let err = resolve_palette_stops(&target).unwrap_err();
        assert!(err.contains("resolved stops"), "{err}");
    }

    /// An empty inline `palette: []` must refuse, not silently return `Ok(vec![])` — an empty palette
    /// later panics `interpolate_scale_color`'s `palette.len() - 1`.
    #[test]
    fn resolve_palette_stops_empty_inline_list_is_not_supported() {
        let target: ScaleTarget = serde_json::from_value(json!({
            "palette": [],
            "arm_mode": "asymmetric"
        }))
        .unwrap();
        let err = resolve_palette_stops(&target).unwrap_err();
        assert!(err.contains("empty"), "{err}");
    }

    #[test]
    fn compute_scale_domain_infers_from_detail_rows_when_unset() {
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            json!({"v": 3.0}).as_object().unwrap().clone(),
            json!({"v": 9.0}).as_object().unwrap().clone(),
        ];
        let refs: Vec<&serde_json::Map<String, Value>> = rows.iter().collect();
        assert_eq!(compute_scale_domain(&refs, "v", None, None), (3.0, 9.0));
    }

    #[test]
    fn compute_scale_domain_with_no_numerics_falls_back_to_zero_one() {
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![json!({"v": "n/a"}).as_object().unwrap().clone()];
        let refs: Vec<&serde_json::Map<String, Value>> = rows.iter().collect();
        assert_eq!(compute_scale_domain(&refs, "v", None, None), (0.0, 1.0));
    }

    #[test]
    fn compute_scale_domain_explicit_min_max_wins_outright() {
        let refs: Vec<&serde_json::Map<String, Value>> = Vec::new();
        assert_eq!(compute_scale_domain(&refs, "v", Some(-1.0), Some(1.0)), (-1.0, 1.0));
    }

    // ── full render(): paint-site strings + measurement-site width ─────

    fn cf_theme() -> Value {
        let mut theme = super::tests::base_table_theme();
        theme["wrap"] = json!(true);
        theme
    }

    fn cf_payload(columns: Value, rows: Vec<Value>, conditional_formatting: Value) -> Payload {
        let theme = cf_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "CF Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": conditional_formatting,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire(), "tones": {
                "positive": "#00884d", "negative": "#94001e",
                "warning": "#e69812", "info": "#008cdd"
            }},
        });
        crate::test_payload(payload_json)
    }

    fn align_left_codes(rows: Vec<Value>, cf: Value) -> Vec<&'static str> {
        let columns = json!({"days": {"label": "N", "align": "left"}});
        super::tests::render_with_warnings(&cf_payload(columns, rows, cf)).1
    }

    /// A glyph on only some rows: those rows' digits start after the glyph
    /// while the rest start at the padding, so the digits do not stack. The
    /// auto-sized lane under a short header lands exactly on the padding, so a
    /// digits-only check calls the key a no-op.
    #[test]
    fn align_left_with_a_glyph_on_only_some_rows_warns_ragged_not_no_effect() {
        let rows = vec![json!({"days": 10}), json!({"days": 20})];
        let cf = json!({"days": {"when": [{"lte": 10, "glyph": "\u{25b2}"}]}});
        let codes = align_left_codes(rows, cf);
        assert!(codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id), "{codes:?}");
        assert!(!codes.contains(&crate::codes::WARN_TABLE_ALIGN_NO_VISIBLE_EFFECT.id), "{codes:?}");
    }

    /// The minus is painted inline in the left run, so `-10` beside `20`
    /// puts the digits at different x even though their widths match.
    #[test]
    fn align_left_with_mixed_signs_warns_ragged() {
        let rows = vec![json!({"days": -10}), json!({"days": 20})];
        let codes = align_left_codes(rows, json!({}));
        assert!(codes.contains(&crate::codes::WARN_TABLE_NUMERIC_ALIGN_LEFT.id), "{codes:?}");
    }

    #[test]
    fn numeric_lane_glyph_replaces_the_prefix_and_carries_its_own_fill() {
        let rows = vec![json!({"days": 10}), json!({"days": 120})];
        let columns = json!({"days": {"label": "Days"}});
        let cf = json!({"days": {"when": [
            {"lte": 30, "glyph": "A", "glyph_color": "#608470"},
            {"gt": 90, "glyph": "B", "glyph_color": "#d49656"}
        ]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("text-anchor=\"end\" fill=\"#608470\">A</tspan>"),
            "glyph tspan with its own fill missing:\n{svg}"
        );
        assert!(
            svg.contains("text-anchor=\"end\" fill=\"#d49656\">B</tspan>"),
            "second row's glyph tspan missing:\n{svg}"
        );
    }

    /// A `tone` rule on an AUTO-width
    /// numeric column reaches the numeric-lane prefix-width measurement
    /// site (`resolve_cell_glyph`, tones=None by design) before it ever reaches the paint site (`resolve_cell_style`,
    /// real board tones). `apply_rule_outputs` must not refuse just because
    /// the glyph-only measurement call passed no tones — the column must
    /// still render, and paint the tone color, when the PAYLOAD actually
    /// carries board tones (`cf_payload`'s `board_style.tones`).
    #[test]
    fn a_tone_rule_renders_and_paints_when_board_tones_are_present() {
        let rows = vec![json!({"days": 10}), json!({"days": 120})];
        // No `width:` pin — an auto column reaches the demand/word-floor/
        // wrap measurement sites, unlike the pinned columns every other
        // render-level glyph test in this module uses.
        let columns = json!({"days": {"label": "Days"}});
        let cf = json!({"days": {"when": [
            {"lte": 30, "glyph": "A", "tone": "info"},
            {"gt": 90, "glyph": "B", "tone": "warning"}
        ]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).expect("board tones are present; must render");
        assert!(
            svg.contains("text-anchor=\"end\" fill=\"#008cdd\">A</tspan>"),
            "tone-resolved glyph fill missing:\n{svg}"
        );
        assert!(
            svg.contains("text-anchor=\"end\" fill=\"#e69812\">B</tspan>"),
            "second row's tone-resolved glyph fill missing:\n{svg}"
        );
    }

    #[test]
    fn text_cell_glyph_paints_a_leading_tspan_with_its_own_fill() {
        let rows = vec![json!({"priority": "urgent"})];
        let columns = json!({"priority": {"label": "Priority", "width": 200}});
        let cf = json!({"priority": {"when": [
            {"eq": "urgent", "glyph": "\u{25a0}", "glyph_color": "#a8423a"}
        ]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains(&format!("<tspan fill=\"#a8423a\">{} </tspan>", "\u{25a0}")),
            "leading glyph tspan missing:\n{svg}"
        );
    }

    #[test]
    fn a_background_rect_paints_before_the_cell_text_from_a_when_rule() {
        let rows = vec![json!({"days": 120})];
        let columns = json!({"days": {"label": "Days", "width": 100}});
        let cf = json!({"days": {"when": [{"gt": 90, "background": "#ffe0e0", "glyph": "x"}]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("fill=\"#ffe0e0\""), "when-rule background rect missing:\n{svg}");
        let rect_pos = svg.find("fill=\"#ffe0e0\"").unwrap();
        // A single-column, single-row table's only remaining `<text>` after
        // the header is the cell itself — the header's own `<text>` sorts
        // earlier, so the row's text is the LAST one in the document.
        let text_pos = svg.rfind("<text").unwrap();
        assert!(rect_pos < text_pos, "background rect must paint before the row's text");
    }

    /// Regression: `resolve_hinge`'s percent-crossing check
    /// (`col_format.contains('%')`) must run against the RESOLVED d3 spec,
    /// not the raw format name — "percent" itself contains no '%', only
    /// the resolved ".0%" spec does. Domain [0.5, 2.0] crosses the percent
    /// hinge at 1.0; a row whose own value is exactly 1.0 must land on the
    /// diverging palette's exact middle stop (distance-from-hinge zero) —
    /// the broken raw-string check instead falls back to the domain
    /// midpoint (1.25), which is not the row's value, so it never lands
    /// exactly on the middle stop.
    #[test]
    fn resolve_hinge_detects_percent_from_the_resolved_format_spec() {
        let rows = vec![json!({"v": 0.5}), json!({"v": 1.0}), json!({"v": 2.0})];
        let columns = json!({"v": {"label": "V", "format": "percent", "scale": {
            "background": {
                "palette": "seq",
                "domain": "data",
                "hinge": "auto",
                "arm_mode": "asymmetric",
                "resolved_stops": ["#000000", "#808080", "#ffffff"]
            }
        }}});
        let payload = cf_payload(columns, rows, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("fill=\"#808080\""),
            "the percent-detected hinge must land row v=1.0 exactly on the middle stop:\n{svg}"
        );
    }

    #[test]
    fn a_scale_background_paints_the_interpolated_color() {
        let rows: Vec<Value> = (0..3).map(|i| json!({"v": i as f64 * 5.0})).collect();
        let columns = json!({"v": {"label": "V", "scale": {
            "background": {
                "palette": "seq",
                "domain": "data",
                "arm_mode": "asymmetric",
                "resolved_stops": ["#000000", "#ffffff"]
            }
        }}});
        let payload = cf_payload(columns, rows, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        // min value (0) -> first stop, max value (10) -> last stop.
        assert!(svg.contains("fill=\"#000000\""), "min-value scale background missing:\n{svg}");
        assert!(svg.contains("fill=\"#ffffff\""), "max-value scale background missing:\n{svg}");
    }

    /// A table column paints no legend, so `nice` unset keeps the exact extent (12 is the first stop);
    /// an authored `nice: true` widens an unbounded domain to round ends (12 sits at 12%, #1f1f1f).
    #[test]
    fn a_scale_background_widens_only_on_an_authored_nice() {
        let paint = |nice: Value| {
            let rows: Vec<Value> = [12.0, 50.0, 88.0].iter().map(|v| json!({"v": v})).collect();
            let columns = json!({"v": {"label": "V", "scale": {
                "background": {
                    "palette": "seq",
                    "domain": "data",
                    "nice": nice,
                    "arm_mode": "asymmetric",
                    "resolved_stops": ["#000000", "#ffffff"]
                }
            }}});
            render(&cf_payload(columns, rows, Value::Null), &test_fonts()).unwrap()
        };
        assert!(paint(json!(true)).contains("fill=\"#1f1f1f\""));
        assert!(paint(Value::Null).contains("fill=\"#000000\""));
        assert!(paint(json!(false)).contains("fill=\"#000000\""));
    }

    /// The scale domain must come from the rows
    /// production actually shows (`scale_rows`, fed
    /// `page_visible_data`/`visible_data` by its two callers), not the
    /// full dataset. 10 rows, page_rows=3 (no slot height, so `n_shown =
    /// page_rows`): the three VISIBLE rows are `[0, 2, 4]`; the seven
    /// off-page rows are all `1000`. A domain drawn from the full dataset
    /// would put the last visible row (4) near the LOW end of `[0, 1000]`
    /// (nearly black); the correct, visible-only domain (`[0, 4]`) puts it
    /// at the scale's own maximum (t=1.0, white — the last stop exactly).
    #[test]
    fn scale_domain_is_computed_from_the_visible_rows_only() {
        let mut rows: Vec<Value> = vec![
            json!({"v": 0.0}),
            json!({"v": 2.0}),
            json!({"v": 4.0}),
        ];
        rows.extend((0..7).map(|_| json!({"v": 1000.0})));
        let columns = json!({"v": {"label": "V", "scale": {
            "background": {
                "palette": "seq",
                "domain": "data",
                "arm_mode": "asymmetric",
                "resolved_stops": ["#000000", "#ffffff"]
            }
        }}});
        let mut theme = cf_theme();
        theme["pagination"] = json!({"enabled": true, "page_rows": 3});
        let resolved = json!({
            "link_open": "page",
            "id": "t1",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": Value::Null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
        });
        let payload = super::tests::live(crate::test_payload(payload_json));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("fill=\"#ffffff\""),
            "the last VISIBLE row (value 4, the domain's own max) must hit \
             the scale's top stop, not a color crushed toward black by the \
             off-page rows' far larger values:\n{svg}"
        );
    }

    #[test]
    fn a_summary_row_does_not_paint_a_scale_background() {
        // Explicit min/max (not "data"-derived) isolates the row-role skip
        // from domain computation: a value row at the domain midpoint
        // (5, of [0, 10]) paints the mid-gray stop; a total row at 999
        // would clamp to the white extreme stop IF the scale applied to
        // it — asserting that color is absent proves the skip, not a
        // coincidence of domain exclusion.
        let rows = vec![json!({"v": 5.0, "role": "value"}), json!({"v": 999.0, "role": "total"})];
        let columns = json!({"v": {"label": "V", "scale": {
            "background": {
                "palette": "seq",
                "domain": "data",
                "min": 0.0,
                "max": 10.0,
                "arm_mode": "asymmetric",
                "resolved_stops": ["#000000", "#ffffff"]
            }
        }}});
        let mut theme = cf_theme();
        theme["row"]["role"] = json!("role");
        let resolved = json!({
            "link_open": "page",
            "title": "CF Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": Value::Null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("fill=\"#808080\""), "value row's own scale color missing:\n{svg}");
        assert!(!svg.contains("fill=\"#ffffff\""), "scale must skip summary/total rows:\n{svg}");
    }

    /// Round 2 review: distinct from the test above — this pins that a
    /// summary/total row is excluded from the DOMAIN computation, not just
    /// from being painted itself (`is_summary_role(role) → false` in the
    /// `filter` this row's domain slice above, mirroring production's
    /// `scale_rows` construction). Mutating that filter to keep every role
    /// left the test above green (a total row unconditionally never
    /// paints, whatever the domain), while this one goes red.
    #[test]
    fn scale_domain_excludes_the_total_rows_own_value() {
        let rows = vec![
            json!({"v": 0.0, "role": "value"}),
            json!({"v": 2.0, "role": "value"}),
            json!({"v": 4.0, "role": "value"}),
            json!({"v": 1000.0, "role": "total"}),
        ];
        let columns = json!({"v": {"label": "V", "scale": {
            "background": {
                "palette": "seq",
                "domain": "data",
                "arm_mode": "asymmetric",
                "resolved_stops": ["#000000", "#ffffff"]
            }
        }}});
        let mut theme = cf_theme();
        theme["row"]["role"] = json!("role");
        let resolved = json!({
            "link_open": "page",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": Value::Null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("fill=\"#ffffff\""),
            "the top detail value (4) must hit the scale's own max — a \
             domain drawn from the total row's 1000 would crush it toward \
             black instead:\n{svg}"
        );
    }

    #[test]
    fn a_glyph_narrows_the_wrap_content_width() {
        // The same cell text, at a width chosen so it fits on one line
        // (0 `<tspan>`s — the single-line body-text path never emits one)
        // with no glyph, but the glyph run eats into the wrap content width
        // just enough to force exactly one extra wrapped line.
        let text = "A somewhat long label";
        let rows = vec![json!({"label": text})];
        let columns = json!({"label": {"label": "Label", "width": 160}});
        // Each wrapped line gets its own outer `<tspan x="20.0" y="...">` —
        // that x is the cell's own text origin, distinct from the title's
        // (x="12") and from the glyph's own nested, x-less inline tspan.
        let payload_plain = cf_payload(columns.clone(), rows.clone(), Value::Null);
        let svg_plain = render(&payload_plain, &test_fonts()).unwrap();
        let plain_lines = svg_plain.matches("<tspan x=\"20.0\" y=\"").count();
        assert_eq!(plain_lines, 0, "the label must fit on one line with no glyph:\n{svg_plain}");

        let cf = json!({"label": {"when": [{"is_null": false, "glyph": "\u{25cf}\u{25cf}\u{25cf}\u{25cf}\u{25cf}"}]}});
        let payload_glyph = cf_payload(columns, rows, cf);
        let svg_glyph = render(&payload_glyph, &test_fonts()).unwrap();
        let glyph_lines = svg_glyph.matches("<tspan x=\"20.0\" y=\"").count();
        assert_eq!(
            glyph_lines, 2,
            "the glyph run must narrow the wrap content width enough to force a second line:\n{svg_glyph}"
        );
    }

    /// The numeric lane's prefix width must be measured on
    /// whichever the cell will actually render — the glyph when one is
    /// set, the format's own prefix otherwise
    /// (`effective_prefix = cell_glyph or _p`). A currency column's narrow
    /// "$" prefix vs. a wide glyph run must widen the prefix lane and push
    /// the number tspan's x to the right — if the measurement site ignores
    /// the glyph, the lane stays sized for "$" and the wide glyph visually
    /// collides with the number.
    #[test]
    fn a_glyph_widens_the_numeric_prefix_lane_over_the_format_prefix() {
        let rows = vec![json!({"amt": 5})];
        let columns = json!({"amt": {"label": "Amt", "format": "currency", "width": 60}});

        let payload_plain = cf_payload(columns.clone(), rows.clone(), Value::Null);
        let svg_plain = render(&payload_plain, &test_fonts()).unwrap();
        let plain_number_x = number_tspan_x(&svg_plain, "5");

        let cf = json!({"amt": {"when": [{"is_null": false, "glyph": "\u{25cf}\u{25cf}\u{25cf}\u{25cf}\u{25cf}"}]}});
        let payload_glyph = cf_payload(columns, rows, cf);
        let svg_glyph = render(&payload_glyph, &test_fonts()).unwrap();
        let glyph_number_x = number_tspan_x(&svg_glyph, "5");

        assert!(
            glyph_number_x > plain_number_x + 10.0,
            "a wide glyph prefix must shift the number lane right of the '$' baseline: plain {plain_number_x}, glyph {glyph_number_x}"
        );
    }

    /// Extracts the `x` attribute of the numeric lane's own value tspan —
    /// `<tspan x="...".text-anchor="end">{value}</tspan>` — the prefix
    /// tspan shares the same `text-anchor="end"` marker but a different
    /// (glyph or format-prefix) text body, so anchoring on the known
    /// numeric value text disambiguates the two.
    fn number_tspan_x(svg: &str, value: &str) -> f64 {
        let needle = format!("text-anchor=\"end\">{value}</tspan>");
        let tag_end = svg.find(&needle).expect("number tspan present");
        let x_start = svg[..tag_end].rfind("<tspan x=\"").expect("tspan open tag present") + "<tspan x=\"".len();
        let x_end = x_start + svg[x_start..].find('"').expect("x attr closing quote present");
        svg[x_start..x_end].parse().expect("x is a float")
    }

    /// Regression: `font.weight` from a matched rule must be gated through
    /// `VALID_FONT_WEIGHTS` before it lands in the SVG — an invalid weight string is
    /// never interpolated as-is.
    #[test]
    fn an_invalid_rule_font_weight_never_reaches_the_svg() {
        let rows = vec![json!({"label": "x"})];
        let columns = json!({"label": {"label": "Label"}});
        let cf = json!({"label": {"when": [{"is_null": false, "font": {"weight": "999"}}]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("font-weight=\"999\""), "invalid weight must not reach the SVG:\n{svg}");
    }

    #[test]
    fn a_valid_rule_font_weight_reaches_the_svg() {
        let rows = vec![json!({"label": "x"})];
        let columns = json!({"label": {"label": "Label"}});
        let cf = json!({"label": {"when": [{"is_null": false, "font": {"weight": 700}}]}});
        let payload = cf_payload(columns, rows, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("font-weight=\"700\""), "valid weight must reach the SVG:\n{svg}");
    }

    fn scale_target(null_color: Option<&str>) -> Value {
        json!({
            "palette": "seq", "domain": "data", "arm_mode": "asymmetric",
            "resolved_stops": ["#000000", "#ffffff"], "min": 0, "max": 100,
            "null_color": null_color,
        })
    }

    /// The painted `fill` of the text cell showing `text`.
    fn text_fill(svg: &str, text: &str) -> String {
        let end = svg.find(&format!(">{text}<")).unwrap_or_else(|| panic!("{text} missing:\n{svg}"));
        let tag_start = svg[..end].rfind("<text").unwrap();
        let tag = &svg[tag_start..end];
        let at = tag.find("fill=\"").unwrap() + 6;
        tag[at..].split('"').next().unwrap().to_string()
    }

    /// `scale.color` maps a numeric cell to a text color, a null to its
    /// `null_color`; a summary row is skipped.
    #[test]
    fn a_color_scale_paints_the_text_of_a_numeric_cell() {
        let rows = vec![json!({"v": 100.0}), json!({"v": 0.0}), json!({"v": null})];
        let columns = json!({"v": {"label": "V", "scale": {"color": scale_target(Some("#ff0000"))}}});
        let svg = render(&cf_payload(columns, rows, Value::Null), &test_fonts()).unwrap();
        assert_eq!(text_fill(&svg, "100"), "#ffffff");
        assert_eq!(text_fill(&svg, "0"), "#000000");
        assert_eq!(text_fill(&svg, "\u{2014}"), "#ff0000");
    }

    #[test]
    fn a_color_scale_skips_a_summary_row() {
        let rows = vec![
            json!({"v": 100.0, "kind": "value"}),
            json!({"v": 100.0, "kind": "summary"}),
        ];
        let columns = json!({"v": {"label": "V", "scale": {"color": scale_target(None)}}});
        let mut payload = cf_payload(columns, rows, Value::Null);
        payload.resolved["style"]["table"]["row"]["role"] = json!("kind");
        let svg = render(&payload, &test_fonts()).unwrap();
        let fills: Vec<String> = svg
            .match_indices(">100<")
            .map(|(end, _)| {
                let tag = &svg[svg[..end].rfind("<text").unwrap()..end];
                let at = tag.find("fill=\"").unwrap() + 6;
                tag[at..].split('"').next().unwrap().to_string()
            })
            .collect();
        assert_eq!(fills[0], "#ffffff");
        assert_ne!(fills[1], "#ffffff", "{fills:?}");
    }

    /// A scale paints over the column's static background; a cell the scale
    /// cannot map (not a number) keeps the static one. The static font color
    /// stays when only the background is scaled.
    #[test]
    fn a_scale_and_a_static_background_and_font_color_combine() {
        let rows = vec![json!({"v": 100.0}), json!({"v": "n/a"})];
        let columns = json!({"v": {
            "label": "V", "background": "#eeeeee", "font": {"color": "#112233"},
            "scale": {"background": scale_target(None)},
        }});
        let svg = render(&cf_payload(columns, rows, Value::Null), &test_fonts()).unwrap();
        assert!(svg.contains("fill=\"#ffffff\""), "scaled fill missing:\n{svg}");
        assert!(svg.contains("fill=\"#eeeeee\""), "static fill missing:\n{svg}");
        assert_eq!(text_fill(&svg, "100"), "#112233");
    }

    #[test]
    fn a_tone_rule_with_no_board_tones_refuses_at_render() {
        let rows = vec![json!({"days": 120})];
        let columns = json!({"days": {"label": "Days", "width": 100}});
        let cf = json!({"days": {"when": [{"gt": 90, "glyph": "\u{25d0}", "tone": "warning"}]}});
        let theme = cf_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "CF Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": cf,
        });
        // No board_style at all — tones absent.
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("no board tones"), "{err}");
    }

    // ── object-shaped `format:` ({spec, prefix, suffix, notation}) ─────
    // Both target corpus rows carry a `format: {spec: ",.0f", suffix: " d"}`
    // column (open_queue_table's `age_days`, accounts_table's
    // `days_since_close`) — this is not a conditional-formatting law, but
    // is a prerequisite: `ColConfig.format` was `Option<String>` only, so
    // any column authoring the object form failed to deserialize at all
    // Ported minimally: `spec` drives the d3 format the
    // same as the string form already did; `prefix`/`suffix` fold onto the
    // d3-embedded ones with production's exact algebra (`format_kpi_parts(...,
    // keep_affix_gaps=True)`: `prefix = (explicit_prefix + d3_prefix).lstrip()`
    // / `suffix = (d3_suffix + explicit_suffix).rstrip()`,
    // `compile/format_value.py:379-382` — no magnitude suffix on this
    // branch, that's the `~s`/SI spec path, untouched); `notation` has no
    // corpus row and refuses.

    #[test]
    fn object_format_spec_with_explicit_suffix_renders() {
        let rows = vec![json!({"days": 5}), json!({"days": 120})];
        let columns = json!({"days": {
            "label": "Days",
            "format": {"spec": ",.0f", "prefix": null, "suffix": " d", "notation": null}
        }});
        let mut theme = cf_theme();
        theme["symbol_mode"] = json!("anchors");
        let resolved = json!({
            "link_open": "page",
            "title": "CF Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": Value::Null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).unwrap();
        // symbol_mode "anchors": the suffix paints on the anchor row
        // (index 0) only, with its authored leading space (`format_kpi_parts(45,
        // {spec: ",.0f", suffix: " d"}, keep_affix_gaps=True)` is `("", "45", " d")`);
        // "d" is not a magnitude token, so SUFFIX_GAP does not apply.
        assert!(svg.contains("> d</tspan>"), "anchor row's suffix tspan missing:\n{svg}");
        assert_eq!(
            svg.matches("> d</tspan>").count(),
            1,
            "non-anchor rows must not repaint the suffix:\n{svg}"
        );
        assert!(!svg.contains(">d</tspan>"), "the authored leading space was trimmed:\n{svg}");
    }

    #[test]
    fn object_format_notation_must_name_a_register() {
        let rows = vec![json!({"v": 5})];
        let columns =
            json!({"v": {"label": "V", "format": {"spec": ",.0f", "notation": "scientific"}}});
        let payload = cf_payload(columns, rows, Value::Null);
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("notation"), "{err}");
    }

    // ── The other three glyph-aware measurement
    // sites — measure_column_demands, measure_column_word_floors, and the
    // fit cascade's _has_overflow — were unported; only the numeric-lane
    // prefix width and the wrap-layout content-width subtraction were.

    #[test]
    fn column_demand_widens_when_the_column_can_paint_a_glyph() {
        let fonts = test_fonts();
        let cells = vec![Cell { text: "5".into(), ..Default::default() }];
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![json!({"v": 5}).as_object().unwrap().clone()];
        let plain_col = ColMeta {
            name: "v".to_string(),
            header: "V".to_string(),
            cfg: ColConfig::default(),
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let glyph_col = ColMeta {
            name: "v".to_string(),
            header: "V".to_string(),
            cfg: ColConfig { glyph: Some("MMMMMMMMMM".to_string()), ..Default::default() },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let plain = column_demand(&cells, &rows, &plain_col, &fonts, "Inter", 11.0, 8.0).unwrap();
        let widened =
            column_demand(&cells, &rows, &glyph_col, &fonts, "Inter", 11.0, 8.0).unwrap();
        assert!(widened > plain, "glyph must widen the demand: plain {plain}, widened {widened}");
    }

    #[test]
    fn a_wide_glyph_widens_the_word_floor() {
        // 50 rows (this file's per-column sample cap): only row 0's "a"
        // value ("x") matches the glyph rule, the other 49 don't ("z"). p95
        // demand (idx 47 of 50 sorted ascending) EXCLUDES that one outlier,
        // so demand alone stays small — isolating the word FLOOR, which
        // takes the MAX over every sampled row and so still catches it.
        // Ample table width (800px) keeps both columns on the compact PIN
        // path (`d.max(floor_bounded)`) rather than the all-compact
        // scale-down path, which would swallow any floor difference by
        // scaling every column down uniformly regardless of it.
        fn col_b_x(has_glyph: bool) -> f64 {
            let mut rows = vec![json!({"a": "x", "b": "y"})];
            rows.extend((0..49).map(|_| json!({"a": "z", "b": "y"})));
            let columns = json!({"a": {"label": "A", "link_open": "page", "header_link_open": "page"}, "b": {"label": "B", "link_open": "page", "header_link_open": "page"}});
            let cf = if has_glyph {
                json!({"a": {"when": [{"eq": "x", "glyph": "MMMMMMMMMMMMMMMMMMMM"}]}})
            } else {
                Value::Null
            };
            let mut theme = cf_theme();
            theme["outer_padding"] = json!(2.0);
            theme["column_layout"]["cell_padding"] = json!(2.0);
            theme["pagination"]["page_rows"] = json!(50);
            let resolved = json!({
                "link_open": "page",
                "title": "T",
                "title_style": resolved_title_style(),
                "columns": columns,
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": cf,
            });
            let payload_json = json!({
                "resolved": resolved,
                "rows": rows,
                "width": 800.0,
                "height": 2000.0,
                "background": "#eeeeee",
                "fonts_dir": null,
                "padding": null,
                "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire(), "tones": {
                    "positive": "#00884d", "negative": "#94001e",
                    "warning": "#e69812", "info": "#008cdd"
                }},
            });
            let payload: Payload = crate::test_payload(payload_json);
            let svg = render(&payload, &test_fonts()).unwrap();
            // Every row paints "y" in column b at the same x — the first
            // `<text ...>y</text>` (a single-char cell never wraps) is
            // column b's left content edge.
            let marker = ">y</text>";
            let idx = svg.find(marker).expect("column b's cell present");
            let tag_start = svg[..idx].rfind("<text").unwrap();
            let tag = &svg[tag_start..idx];
            let x_start = tag.find("x=\"").unwrap() + 3;
            tag[x_start..].split('"').next().unwrap().parse().unwrap()
        }

        let without_glyph = col_b_x(false);
        let with_glyph = col_b_x(true);
        assert!(
            with_glyph > without_glyph,
            "a wide glyph must widen column a's floor, pushing b right: \
             without {without_glyph}, with {with_glyph}"
        );
    }

    #[test]
    fn a_glyph_trips_the_fit_cascade_that_a_bare_value_would_not() {
        // A single numeric column sized just wide enough for its bare
        // digit string at the theme's font size, but not for glyph + digit
        // string — without the fit-cascade glyph term, this renders at the
        // theme's own font size; with it, `_has_overflow` fires and the
        // cascade steps the body font down to 11px.
        let rows = vec![json!({"v": 5})];
        let columns = json!({"v": {"label": "V", "width": 40}});
        let cf = json!({"v": {"when": [{"eq": 5, "glyph": "MMMMMMMMMM"}]}});
        let mut theme = cf_theme();
        theme["font"]["size"] = json!(18.0);
        theme["outer_padding"] = json!(0.0);
        theme["column_layout"]["cell_padding"] = json!(2.0);
        let resolved = json!({
            "link_open": "page",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": cf,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 40.0,
            "height": 200.0,
            "background": "#eeeeee",
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire(), "tones": {
                "positive": "#00884d", "negative": "#94001e",
                "warning": "#e69812", "info": "#008cdd"
            }},
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).unwrap();
        // The header's own font-size is 11px unconditionally (the base
        // theme's own explicit header size), so a bare `contains("11")`
        // would pass regardless of the cascade — the BODY cell's own
        // tabular-numeric `<text>` tag is the one the cascade actually
        // steps.
        let body_tag_start = svg.find(&format!("font-family=\"{}", quick_xml::escape::escape("'dbt Sans Tabular'"))).unwrap();
        let tag = &svg[..body_tag_start];
        let fs_start = tag.rfind("font-size=\"").unwrap() + "font-size=\"".len();
        let body_font_size = &tag[fs_start..].split('"').next().unwrap();
        assert_eq!(
            *body_font_size, "11",
            "the glyph must trip the fit cascade down to 11px body font:\n{svg}"
        );
    }
}

/// The "no auto columns" branch of the column layout: when EVERY column carries an explicit `width:` pin, the pins
/// are returned exactly as authored regardless of the slot, and
/// `actual_content_width` is their bare sum. A sum past the slot keeps the
/// canvas at the slot and clips the body to it, the same as any other
/// `TABLE_COLUMNS_OVERFLOW`, so the table never paints over a neighbor.
#[cfg(test)]
mod all_pinned_overflow_growth_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::*;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    #[test]
    fn all_columns_pinned_past_the_slot_clips_to_the_slot() {
        let rows = vec![json!({"a": "x", "b": "y"})];
        let columns = json!({
            "a": {"label": "A", "width": 300},
            "b": {"label": "B", "width": 300},
        });
        let mut theme = super::tests::base_table_theme();
        theme["outer_padding"] = json!(10.0);
        let resolved = json!({
            "link_open": "page",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 500.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("all-pinned overflow clips, not errors");
        assert!(svg.starts_with("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"500\""), "{svg}");
        assert!(svg.contains("<rect x=\"0\" y=\"0\" width=\"500\""), "clip rect is the slot:\n{svg}");
        assert!(svg.contains("clip-path=\"url(#table-clip-"), "{svg}");
    }

    #[test]
    fn a_mixed_pin_and_auto_overflow_clips_and_warns() {
        // Not every column is pinned here (`b` is auto): the columns still
        // sum past the slot, so the table clips to it and says so.
        let rows = vec![json!({"a": "x", "b": "a very much longer cell value here"})];
        let columns = json!({
            "a": {"label": "A", "width": 490},
            "b": {"label": "B"},
        });
        let theme = super::tests::base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 500.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let (svg, codes) = super::tests::render_with_warnings(&payload);
        assert!(codes.contains(&"WARN-TABLE-COLUMNS-OVERFLOW"), "{codes:?}");
        assert!(svg.starts_with("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"500\""), "{svg}");
        assert!(svg.contains("clip-path=\"url(#table-clip-"), "the body clips to the slot:\n{svg}");
    }
    fn pinned_payload(width: f64, pin: f64) -> Payload {
        let resolved = json!({
            "link_open": "page",
            "id": "same_id",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": {"a": {"label": "A", "width": pin}},
            "style": {"table": super::tests::base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        crate::test_payload(json!({
            "resolved": resolved,
            "rows": [{"a": "x"}],
            "width": width,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }))
    }

    #[test]
    fn tables_sharing_an_id_get_distinct_clips_for_distinct_slots() {
        let fonts = test_fonts();
        let narrow = render(&pinned_payload(300.0, 900.0), &fonts).unwrap();
        let wide = render(&pinned_payload(400.0, 900.0), &fonts).unwrap();
        let clip_id = |svg: &str| {
            let start = svg.find("<clipPath id=\"").unwrap() + 14;
            svg[start..start + svg[start..].find('"').unwrap()].to_string()
        };
        assert_ne!(clip_id(&narrow), clip_id(&wide));
        assert!(narrow.contains("<rect x=\"0\" y=\"0\" width=\"300\" height="), "{narrow}");
    }

    #[test]
    fn a_table_that_fits_its_slot_emits_no_clip() {
        let svg = render(&pinned_payload(300.0, 100.0), &test_fonts()).unwrap();
        assert!(!svg.contains("clipPath"), "{svg}");
    }
}

#[cfg(test)]
mod allocator_tests {
    use super::*;
    use super::layout::proportional_allocate;
    use super::layout::grow_toward_header_width;
    use super::layout::cluster_and_equalize_widths;
    use super::layout::calculate_column_layout_port;
    use super::layout::allocate_text_columns;
    use std::collections::BTreeMap;

    #[test]
    fn force_compact_pins_a_high_demand_spark_column_despite_the_ceiling() {
        // "v" demands 300px (> COMPACT_DEMAND_CEILING) but carries a spark
        // config pins it compact regardless of measured demand, same as a
        // swatch column. Without `force_compact` it would compete as text
        // instead of pinning to its own 300px demand.
        let mut force_compact = BTreeSet::new();
        force_compact.insert("v".to_string());
        let got = calculate_column_layout_port(
            &["name".to_string(), "v".to_string()],
            &force_compact,
            &BTreeMap::new(),
            &BTreeMap::new(),
            500.0,
            &m(&[("name", 100.0), ("v", 300.0)]),
            &m(&[("name", 50.0), ("v", 40.0)]),
            &m(&[("name", 100.0), ("v", 50.0)]),
            0.8,
            0.0,
            0.0,
        );
        assert_close(&got, &[("name", 125.0), ("v", 375.0)]);
    }

    fn m(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    fn assert_close(got: &BTreeMap<String, f64>, want: &[(&str, f64)]) {
        assert_eq!(got.len(), want.len(), "{got:?} vs {want:?}");
        for (k, v) in want {
            let g = got[*k];
            assert!((g - v).abs() < 1e-6, "{k}: got {g}, want {v}");
        }
    }

    #[test]
    fn proportional_allocate_caps_at_sixty_percent_and_redistributes() {
        let got = proportional_allocate(&m(&[("a", 300.0), ("b", 100.0)]), 200.0);
        assert_close(&got, &[("a", 120.0), ("b", 80.0)]);
        let plain = proportional_allocate(&m(&[("a", 60.0), ("b", 40.0)]), 100.0);
        assert_close(&plain, &[("a", 60.0), ("b", 40.0)]);
    }

    #[test]
    fn cluster_equalize_uses_cluster_extremes_not_adjacent_pairs() {
        // 100..150 in steps of 10: adjacent ratios all >= 0.8, but the walk
        // splits when cluster_min/new < 0.8 — two clusters, snapped + rescaled.
        let got = cluster_and_equalize_widths(
            &m(&[("a", 100.0), ("b", 110.0), ("c", 120.0), ("d", 130.0), ("e", 140.0), ("f", 150.0)]),
            750.0,
            0.8,
        );
        assert_close(
            &got,
            &[
                ("a", 111.11111111111111), ("b", 111.11111111111111), ("c", 111.11111111111111),
                ("d", 138.88888888888889), ("e", 138.88888888888889), ("f", 138.88888888888889),
            ],
        );
        let snap = cluster_and_equalize_widths(&m(&[("a", 100.0), ("b", 104.0), ("c", 200.0)]), 404.0, 0.8);
        assert_close(
            &snap,
            &[("a", 102.98039215686275), ("b", 102.98039215686275), ("c", 198.03921568627453)],
        );
    }

    #[test]
    fn text_allocation_single_column_and_cap_redistribution() {
        let mut caps: BTreeMap<String, Option<f64>> = BTreeMap::new();
        caps.insert("t".into(), None);
        let single = allocate_text_columns(&m(&[("t", 300.0)]), 250.0, &caps, &m(&[("t", 120.0)]), 0.8);
        assert_close(&single, &[("t", 250.0)]);

        let mut caps2: BTreeMap<String, Option<f64>> = BTreeMap::new();
        caps2.insert("t".into(), Some(150.0));
        caps2.insert("u".into(), None);
        let capped =
            allocate_text_columns(&m(&[("t", 300.0), ("u", 300.0)]), 400.0, &caps2, &BTreeMap::new(), 0.8);
        assert_close(&capped, &[("t", 150.0), ("u", 250.0)]);
    }

    #[test]
    fn grow_toward_header_covers_gaps_then_spreads_residual() {
        let mut w = m(&[("a", 50.0), ("b", 80.0)]);
        grow_toward_header_width(&mut w, &m(&[("a", 70.0), ("b", 60.0)]), 30.0);
        assert_close(&w, &[("a", 74.66666666666667), ("b", 85.33333333333333)]);
        let mut w2 = m(&[("a", 50.0), ("b", 80.0)]);
        grow_toward_header_width(&mut w2, &m(&[("a", 70.0), ("b", 60.0)]), 10.0);
        assert_close(&w2, &[("a", 60.0), ("b", 80.0)]);
    }

    #[test]
    fn layout_all_compact_over_subscription_scales_every_column() {
        // precision_high's exact inputs: every demand <= 220 -> all compact,
        // over-subscribed -> uniform scale. Production result verified.
        let got = calculate_column_layout_port(
            &["company", "quarterly_revenue", "growth_rate", "margin"].map(String::from),
            &BTreeSet::new(),
            &BTreeMap::new(),
            &BTreeMap::new(),
            344.0,
            &m(&[
                ("company", 116.12255859375),
                ("quarterly_revenue", 101.22314453125),
                ("growth_rate", 71.62841796875),
                ("margin", 71.1396484375),
            ]),
            &m(&[
                ("company", 49.85986328125),
                ("quarterly_revenue", 45.50390625),
                ("growth_rate", 38.048828125),
                ("margin", 36.16357421875),
            ]),
            &m(&[
                ("company", 86.2861328125),
                ("quarterly_revenue", 70.77978515625),
                ("growth_rate", 54.048828125),
                ("margin", 52.16357421875),
            ]),
            0.8,
            0.0,
            0.0,
        );
        assert_close(
            &got,
            &[
                ("company", 110.92650027863915),
                ("quarterly_revenue", 96.69378031302499),
                ("growth_rate", 68.42330914844891),
                ("margin", 67.95641025988694),
            ],
        );
    }

    #[test]
    fn layout_mixed_pins_compact_and_grows_toward_header() {
        // name demand 300 > 220 -> text; qty compact at 80. The budget (400)
        // covers both natural widths (name 300, qty at its 90px header), so
        // each takes its natural width and the 10px left over goes to the
        // text column: production yields name 310 / qty 90.
        let got = calculate_column_layout_port(
            &["name", "qty"].map(String::from),
            &BTreeSet::new(),
            &BTreeMap::new(),
            &BTreeMap::new(),
            400.0,
            &m(&[("name", 300.0), ("qty", 80.0)]),
            &m(&[("name", 40.0), ("qty", 90.0)]),
            &m(&[("name", 100.0), ("qty", 50.0)]),
            0.8,
            0.0,
            0.0,
        );
        assert_close(&got, &[("name", 310.0), ("qty", 90.0)]);
    }
}

#[cfg(test)]
mod floor_source_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::tests::base_table_theme;
    use super::*;
    use serde_json::json;

    /// Floors tokenize what a cell paints, not the raw value
    /// (): a currency column whose
    /// painted "$24.7M" is narrow floors at its painted token, never at its
    /// raw "24730000.0" width, so a budget that covers the painted natural
    /// widths covers the floors too and each column lands at its painted
    /// width. Under raw floors the revenue column's natural width would be
    /// the raw token's, the budget would not cover it, and revenue would land
    /// elsewhere.
    #[test]
    fn word_floors_measure_formatted_text_not_raw_values() {
        let _scope = super::tests::en_us_scope();
        let fonts = {
            let manifest = env!("CARGO_MANIFEST_DIR");
            let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
            FontBook::load_dir(&dir).expect("test fonts load")
        };
        let pad = 8.0_f64;
        let outer = 12.0_f64;
        let rows = vec![
            json!({"name": "Apex", "revenue": 24730000.0}),
            json!({"name": "Bright", "revenue": 8460000.0}),
        ];
        let name_col = json!({"name": {"label": "Name", "link_open": "page", "header_link_open": "page"}});
        let revenue_col = json!({"revenue": {"label": "Rev", "format": "currency", "link_open": "page", "header_link_open": "page"}});
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        // The columns' natural widths alone, through the measure the board hug
        // reads (`width::measure_natural_width`).
        let natural_of = |columns: Value| -> f64 {
            let payload = width::MeasurePayload {
                resolved: json!({
                    "link_open": "page",
                    "title": null,
                    "title_style": resolved_title_style(),
                    "columns": columns,
                    "style": {"table": theme.clone(), "title_font": resolved_title_font()},
                    "pivot_columns": null,
                    "conditional_formatting": null,
                }),
                rows: rows.iter().map(|r| r.as_object().unwrap().clone()).collect(),
                fonts_dir: None,
                format_locale: crate::number_locale::NumberLocale::en_us(),
                time_locale: crate::time_locale::en_us(),
                language: Default::default(),
                formats: None,
                sub_unit_specs: Some(super::tests::en_us_siblings()),
                cell_text: Vec::new(),
            };
            width::measure_natural_width(&payload, &fonts).unwrap().unwrap() - 2.0 * outer
        };
        let name_w = natural_of(name_col.clone());
        let revenue_w = natural_of(revenue_col);
        let raw_floor = fonts.measure("24730000.0", "Inter", 11.0).expect("measures") + 2.0 * pad;
        assert!(
            raw_floor > revenue_w + 10.0,
            "premise: the raw token must beat the painted width ({raw_floor} vs {revenue_w})"
        );
        let mut columns = name_col.as_object().unwrap().clone();
        columns.extend(json!({"revenue": {"label": "Rev", "format": "currency"}}).as_object().unwrap().clone());
        let payload_json = json!({
            "resolved": {
                "link_open": "page",
                "title": null,
                "title_style": resolved_title_style(),
                "columns": columns,
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": rows,
            "width": name_w + revenue_w + 2.0 * outer,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let payload: Payload = crate::test_payload(payload_json);
        let svg = render(&payload, &fonts).expect("renders");
        let rev_x = {
            let idx = svg.find(">Rev</text>").expect("header");
            svg[..idx]
                .rsplit("<text x=\"")
                .next()
                .and_then(|t| t.split('"').next())
                .and_then(|t| t.parse::<f64>().ok())
                .expect("x parses")
        };
        let expected_center = outer + name_w + revenue_w / 2.0;
        assert!(
            (rev_x - expected_center).abs() < 0.15,
            "revenue column lands at its painted width: center={rev_x}, expected {expected_center}"
        );
    }
}

#[cfg(test)]
mod cascade_and_lane_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::tests::{base_table_theme, on_a_board};
    use super::*;
    use crate::common::TAB_FONT;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn payload_for(columns: Value, rows: Vec<Value>, width: f64) -> Payload {
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        crate::test_payload(json!({
            "resolved": resolved,
            "rows": rows,
            "width": width,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }))
    }

    fn first_x_before(svg: &str, needle: &str) -> f64 {
        let idx = svg.find(needle).unwrap_or_else(|| panic!("{needle} in:\n{svg}"));
        svg[..idx]
            .rsplit("<text x=\"")
            .next()
            .and_then(|t| t.split('"').next())
            .and_then(|t| t.parse().ok())
            .unwrap_or_else(|| panic!("x before {needle} parses"))
    }

    /// Fit cascade: a numeric column scaled
    /// below its demand trips `overflows` at the theme's 8px padding, and
    /// the whole table re-insets at the stepped max(pad/2, 6) = 6 — pinned
    /// via the first text column's body-cell x (outer + 6, not outer + 8).
    #[test]
    fn fit_cascade_steps_cell_padding_down_on_numeric_overflow() {
        let fonts = test_fonts();
        let rows = vec![
            json!({"name": "Apex", "value": 91234567.89}),
            json!({"name": "Bright", "value": 81234567.11}),
        ];
        let columns = json!({
            "name": {"label": "Name"},
            "value": {"label": "Value"},
        });
        // Narrow enough that the all-compact over-subscription scale puts
        // the value column below its own content demand -> overflow.
        let payload = on_a_board(payload_for(columns, rows, 110.0));
        let svg = render(&payload, &fonts).unwrap();
        let apex_x = first_x_before(&svg, ">Apex</text>");
        assert!(
            (apex_x - (12.0 + 6.0)).abs() < 0.01,
            "cascade steps padding 8 -> 6, so the first text cell insets at \
             outer+6: got {apex_x}"
        );
    }

    /// Production measures the fit cascade against the string the cell
    /// DISPLAYS (`_has_overflow` → `format_table_cell_value`, table.py:1360),
    /// in which a magnitude word carries its own gap
    /// (`text/magnitude_words.py:40`): en's analytic `K` is glued to its
    /// number, so a column with room for "2.62K" but not "2.62 K" does not
    /// overflow.
    #[test]
    fn the_fit_cascade_measures_a_glued_magnitude_word_glued() {
        let fonts = test_fonts();
        let glued = fonts.measure("2.62K", "Inter", 11.0).unwrap();
        let spaced = fonts.measure("2.62 K", "Inter", 11.0).unwrap();
        assert!(glued < spaced);
        // Content area between the two. Width = content area + 2·pad + _FIT_BREATH.
        let pin = (glued + spaced) / 2.0 + 2.0 * 8.0 + 4.0;
        let rows = vec![json!({"name": "Apex", "value": 2615.0})];
        let columns = json!({
            "name": {"label": "Name"},
            "value": {"label": "V", "width": pin},
        });
        let locale = crate::number_locale::en_us_with_words(["K", "M", "B", "T"], ["k", "mn", "bn", "trn"]);
        let svg = crate::number_locale::with_number_locale(&locale, || {
            render(&payload_for(columns, rows, 500.0), &fonts).unwrap()
        });
        assert!(
            (first_x_before(&svg, ">Apex</text>") - (12.0 + 8.0)).abs() < 0.01,
            "the glued string fits, so the theme's padding stands:\n{svg}"
        );
    }

    /// A word that carries a no-break gap (de's analytic `\u{a0}k`) is measured
    /// with it: the gap is a whole glyph of width, so a column sized between
    /// "2.62k" and "2.62\u{a0}k" overflows and the cascade steps padding down.
    #[test]
    fn the_fit_cascade_measures_a_gapped_magnitude_word_with_its_gap() {
        let fonts = test_fonts();
        let glued = fonts.measure("2.62k", "Inter", 11.0).unwrap();
        let gapped = fonts.measure("2.62\u{a0}k", "Inter", 11.0).unwrap();
        assert!(glued < gapped);
        let pin = (glued + gapped) / 2.0 + 2.0 * 8.0 + 4.0;
        let rows = vec![json!({"name": "Apex", "value": 2615.0})];
        let columns = json!({
            "name": {"label": "Name"},
            "value": {"label": "V", "width": pin},
        });
        let locale = crate::number_locale::en_us_with_words(["\u{a0}k", "\u{a0}M", "\u{a0}Mrd", "\u{a0}Bio"], ["k", "mn", "bn", "trn"]);
        let svg = crate::number_locale::with_number_locale(&locale, || {
            render(&payload_for(columns, rows, 500.0), &fonts).unwrap()
        });
        assert!(
            (first_x_before(&svg, ">Apex</text>") - (12.0 + 6.0)).abs() < 0.01,
            "the gap tips the cascade, so the table re-insets at outer+6:\n{svg}"
        );
    }

    /// The shared-scale carve-out at the OVERFLOW site (the demand site is
    /// pinned by `a_shared_scale_columns_magnitude_suffix_is_measured_glued_to_its_number`):
    /// production's `_has_overflow` branches to `format_kpi_parts` for a
    /// shared-scale column and measures `prefix + number + suffix` glued, so
    /// a column with room for "24.73M" but not "24.73 M" must NOT trip the
    /// cascade.
    #[test]
    fn a_shared_scale_column_does_not_trip_the_cascade_on_a_space_it_never_paints() {
        let fonts = test_fonts();
        let glued = fonts.measure("24.73M", "Inter", 11.0).unwrap();
        let spaced = fonts.measure("24.73 M", "Inter", 11.0).unwrap();
        let pin = (glued + spaced) / 2.0 + 2.0 * 8.0 + 4.0;
        let rows = vec![json!({"name": "Apex", "amount": 24_730_000.0})];
        let columns = json!({
            "name": {"label": "Name"},
            "amount": {
                "label": "A",
                "width": pin,
                "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
            },
        });
        let svg = render(&payload_for(columns, rows, 500.0), &fonts).unwrap();
        assert!(
            (first_x_before(&svg, ">Apex</text>") - (12.0 + 8.0)).abs() < 0.01,
            "the scaled digit string fits, so the theme's padding stands:\n{svg}"
        );
    }

    /// Both measurement passes format every sampled cell as its OWN anchor
    /// (`_has_overflow` calls `format_table_cell_value`, which knows nothing
    /// about `symbol_mode`), so the declare-once gate that strips a repeated
    /// "$" at PAINT must not narrow what the fit cascade measures: a column
    /// too tight for `$1,234.56` overflows even though its non-anchor row
    /// paints a bare `1,234.56`.
    #[test]
    fn the_fit_cascade_measures_a_non_anchor_rows_stripped_prefix() {
        let fonts = test_fonts();
        let bare = fonts.measure("1,234.56", "Inter", 11.0).unwrap();
        let with_prefix = fonts.measure("$1,234.56", "Inter", 11.0).unwrap();
        let pin = (bare + with_prefix) / 2.0 + 2.0 * 8.0 + 4.0;
        let rows = vec![
            json!({"name": "Apex", "value": 1.0}),
            json!({"name": "Bright", "value": 1234.56}),
        ];
        let columns = json!({
            "name": {"label": "Name"},
            "value": {"label": "V", "format": "currency_full", "width": pin},
        });
        let mut payload = payload_for(columns, rows, 500.0);
        payload.resolved["style"]["table"]["symbol_mode"] = json!("anchors");
        let svg = render(&payload, &fonts).unwrap();
        assert!(
            svg.contains(">1,234.56</tspan>") && !svg.contains(">$1,234.56<"),
            "the non-anchor row paints its number without the prefix:\n{svg}"
        );
        assert!(
            (first_x_before(&svg, ">Apex</text>") - (12.0 + 6.0)).abs() < 0.01,
            "…and the cascade still measures the prefix, so padding steps to 6:\n{svg}"
        );
    }

    /// Lane band clamp (production _compute_lane_positions): a `$`-prefixed
    /// number centered in a column with just enough width shifts the whole
    /// band right by min(left overflow, right headroom) so the prefix never
    /// pokes past the content area's left edge.
    #[test]
    fn lane_band_shifts_right_when_the_prefix_overhangs_left() {
        let fonts = test_fonts();
        let num_w = fonts.measure("1,234,567.89", TAB_FONT, 11.0).unwrap();
        let prefix_w = fonts.measure("$", TAB_FONT, 11.0).unwrap();
        // Content area = num_w + 12: 6px margin per side under centering —
        // less than the prefix + 3px gap needs, more than zero, so the
        // clamp's overflow-left arm fires with right headroom available.
        let pin = num_w + 12.0 + 16.0;
        let rows = vec![json!({"label": "A", "value": 1234567.89})];
        let columns = json!({
            "label": {"label": "L"},
            "value": {"label": "V", "format": "currency_full", "width": pin},
        });
        let payload = payload_for(columns, rows, 500.0);
        let svg = render(&payload, &fonts).unwrap();
        // The value column is last: its content area ends at 500-12-8 and
        // begins at (500-12-pin)+8. The clamped band's left edge (prefix_x
        // - prefix_w) must sit at that content-left edge, not before it.
        let col_x = 500.0 - 12.0 - pin;
        let content_left = col_x + 8.0;
        let prefix_x = {
            let idx = svg.find(">$</tspan>").expect("prefix tspan");
            svg[..idx]
                .rsplit("<tspan x=\"")
                .next()
                .and_then(|t| t.split('"').next())
                .and_then(|t| t.parse::<f64>().ok())
                .expect("prefix tspan x parses")
        };
        assert!(
            prefix_x - prefix_w >= content_left - 0.01,
            "band shifted right so the prefix stays inside the content area: \
             prefix_x={prefix_x} prefix_w={prefix_w} content_left={content_left}"
        );
        assert!(
            prefix_x - prefix_w <= content_left + 0.5,
            "band shifted by exactly the overflow, not clamped harder: \
             prefix_x={prefix_x}"
        );
    }

    /// Degenerate slot guard: a payload whose width is consumed entirely by
    /// outer padding refuses with the named error instead of emitting a
    /// 0-width SVG (the harness's unsized nested-board items).
    #[test]
    fn zero_width_table_refuses_with_a_named_error() {
        let fonts = test_fonts();
        let rows = vec![json!({"a": "x"})];
        let payload = payload_for(json!({"a": {"label": "A"}}), rows, 24.0);
        let err = render(&payload, &fonts).expect_err("0-width refuses").to_string();
        assert!(
            err.contains("too small to allocate columns"),
            "named degenerate-width error: {err}"
        );
    }
}

#[cfg(test)]
mod date_like_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::tests::base_table_theme;
    use super::*;
    use super::bind::is_date_like_str;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }
    use serde_json::json;

    // Expectations from production's _DATE_RE (core/utils.py:29) — the
    // seven recognized shapes, pattern-based rather than semantic (an
    // impossible calendar date that fits a pattern still matches).
    #[test]
    fn date_like_matches_production_patterns() {
        for s in [
            "2024-03", "2024-03-15", "03/15/2024", "Q1 2024", "Mar 15",
            "Mar 15, 2024", "Mar 15 2024", "15 Mar 2024", "2024", "99/99/2024",
        ] {
            assert!(is_date_like_str(s), "{s} is date-like");
        }
        for s in [
            "2024-3", "Q5 2024", "March 15", "15 March 2024", "24", "20244",
            "abc", "", "2024-03-15T10:00", "1,234",
        ] {
            assert!(!is_date_like_str(s), "{s} is not date-like");
        }
    }

    #[test]
    fn non_iso_date_like_cells_render_verbatim_in_a_date_lane() {
        // Production formats only true temporal values (full ISO) to the
        // default date display; US/short/quarter/year-month shapes pass
        // through verbatim (format_table_cell_value's str fallthrough).
        let rows = vec![
            json!({"event": "Launch", "us": "03/15/2024", "ym": "2024-03", "q": "Q1 2024"}),
        ];
        let columns = json!({
            "event": {"label": "Event"},
            "us": {"label": "US", "align": "right"},
            "ym": {"label": "YM", "align": "right"},
            "q": {"label": "Qtr", "align": "right"},
        });
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload: Payload = crate::test_payload(json!({
            "resolved": resolved,
            "rows": rows,
            "width": 500.0,
            "height": 200.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }));
        let svg = render(&payload, &test_fonts()).expect("renders");
        for cell in ["03/15/2024", "2024-03", "Q1 2024"] {
            assert!(svg.contains(cell), "{cell} rendered verbatim:\n{svg}");
        }
        // Date lanes render like numeric columns: centered header,
        // end-anchored cells (production invariant #11).
        let us_header_at = svg.find(">US</text>").expect("US header");
        let before = &svg[..us_header_at];
        let anchor = before.rsplit("text-anchor=\"").next().unwrap();
        assert!(
            anchor.starts_with("middle"),
            "a recognized date lane centers its header:\n{svg}"
        );
    }
}

#[cfg(test)]
mod demand_affix_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::tests::base_table_theme;
    use super::*;
    use super::layout::column_demand;
    use super::bind::format_numeric_col;
    use super::bind::format_date_col;
    use super::bind::build_cell_data;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// A number mixed into a date lane (invariant #11) measures with its
    /// SI magnitude like any numeric cell — production's oracle demand for
    /// a "Mar 1"/1240.0 column is the width of format_table_cell_value(1240,
    /// None) == "1.24K" (the en analytic words are glued) in Inter @11 + 2*8
    /// pad. Without the date-lane number arm recording full_suffix, the
    /// demand under-measures by the "K" width. Pinned at the demand level directly — in a
    /// rendered table the raw-token word floor ("1240.0") can exceed and
    /// mask this demand, which is exactly why the width-level sweep never
    /// caught it.
    #[test]
    fn date_lane_mixed_number_demand_includes_its_magnitude_suffix() {
        let _scope = super::tests::en_us_scope();
        let fonts = test_fonts();
        let col = ColMeta {
            name: "when".to_string(),
            header: "When".to_string(),
            cfg: ColConfig::default(),
            is_numeric: false,
            is_date: true,
            when_rules: Vec::new(),
        };
        let rows = vec![
            serde_json::json!({"when": "Mar 1"}),
            serde_json::json!({"when": 1240.0}),
        ];
        let maps: Vec<serde_json::Map<String, Value>> =
            rows.into_iter().map(|r| r.as_object().unwrap().clone()).collect();
        let cells = format_date_col(&maps, &col).expect("date lane formats");
        let demand = column_demand(&cells, &maps, &col, &fonts, "Inter", 11.0, 8.0).unwrap();
        let oracle = fonts.measure("1.24K", "Inter", 11.0).unwrap() + 16.0;
        assert!(
            (demand - oracle).abs() < 1e-9,
            "mixed-number demand carries its magnitude suffix: {demand} vs oracle {oracle}"
        );
    }

    /// An object-form `format:`'s explicit suffix is measured with its authored space, not stripped
    /// to the bare token: the demand path appends the raw suffix directly (`"45" + " d" = "45 d"`),
    /// distinct from the paint path, which strips it for its lane-merge algebra.
    #[test]
    fn an_object_format_suffix_is_measured_with_its_authored_space() {
        let fonts = test_fonts();
        let col = ColMeta {
            name: "days_since_close".to_string(),
            header: "Days".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot { suffix: " d".into(), ..FormatSlot::from(",.0f") }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps: Vec<serde_json::Map<String, Value>> = vec![json!({"days_since_close": 45.0})]
            .into_iter()
            .map(|r| r.as_object().unwrap().clone())
            .collect();
        let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
        let demand = column_demand(&cells, &maps, &col, &fonts, "Inter", 11.0, 8.0).unwrap();
        let oracle = fonts.measure("45 d", "Inter", 11.0).unwrap() + 16.0;
        assert!(
            (demand - oracle).abs() < 1e-9,
            "object-format suffix must be measured with its authored space: {demand} vs oracle {oracle}"
        );
    }

    /// The explicit PREFIX is measured raw too, not trimmed like the paint lanes' prefix: `45` with
    /// `{spec: ',.0f', prefix: '~ '}` measures as `'~ 45'`.
    #[test]
    fn an_object_format_prefix_is_measured_with_its_authored_space() {
        let fonts = test_fonts();
        let col = ColMeta {
            name: "amt".to_string(),
            header: "Amt".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot { prefix: "~ ".into(), ..FormatSlot::from(",.0f") }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps: Vec<serde_json::Map<String, Value>> = vec![json!({"amt": 45.0})]
            .into_iter()
            .map(|r| r.as_object().unwrap().clone())
            .collect();
        let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
        let demand = column_demand(&cells, &maps, &col, &fonts, "Inter", 11.0, 8.0).unwrap();
        let oracle = fonts.measure("~ 45", "Inter", 11.0).unwrap() + 16.0;
        assert!(
            (demand - oracle).abs() < 1e-9,
            "object-format prefix must be measured with its authored space: {demand} vs oracle {oracle}"
        );
    }

    /// `format_kpi_parts` strips only the far edge of `explicit_prefix +
    /// d3_prefix`: `{spec: "$,.0f", prefix: "~ "}` on 45 paints `("~ $",
    /// "45", "")`, the inner space kept. Trimming the authored prefix
    /// before the merge paints `"~$"` and narrows the prefix lane.
    #[test]
    fn an_object_format_prefix_keeps_its_inner_space_against_the_d3_prefix() {
        let col = ColMeta {
            name: "amt".to_string(),
            header: "Amt".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot { prefix: "~ ".into(), ..FormatSlot::from("$,.0f") }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps: Vec<serde_json::Map<String, Value>> = vec![json!({"amt": 45.0})]
            .into_iter()
            .map(|r| r.as_object().unwrap().clone())
            .collect();
        let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
        assert_eq!(cells[0].prefix.as_deref(), Some("~ $"));
        assert_eq!(cells[0].full_prefix.as_deref(), Some("~ $"));
    }

    fn affix_cells(
        spec: &str,
        prefix: &str,
        suffix: &str,
        symbol_mode: &str,
        values: &[f64],
    ) -> Vec<Cell> {
        let col = ColMeta {
            name: "v".to_string(),
            header: "V".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot {
                    prefix: prefix.into(),
                    suffix: suffix.into(),
                    ..FormatSlot::from(spec)
                }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps: Vec<serde_json::Map<String, Value>> =
            values.iter().map(|v| json!({"v": v}).as_object().unwrap().clone()).collect();
        let roles = vec![RowRole::Value; values.len()];
        format_numeric_col(&maps, &col, symbol_mode, &roles).expect("formats")
    }

    /// Oracle: `format_kpi_parts(45, {spec: ",.0f", prefix: "CHF "}, None,
    /// default_number=True, is_anchor=True, keep_affix_gaps=True)` is
    /// `("CHF ", "45", "")`: the table keeps the authored gap against the number
    /// (`table.py:2626`).
    #[test]
    fn an_object_format_prefix_keeps_its_trailing_space() {
        let cells = affix_cells(",.0f", "CHF ", "", "all", &[45.0]);
        assert_eq!(cells[0].prefix.as_deref(), Some("CHF "));
        assert_eq!(cells[0].full_prefix.as_deref(), Some("CHF "));
    }

    /// Oracle: `{spec: ",.0f", prefix: " ~ "}` gives `("~ ", "45", "")` and
    /// `{spec: "$,.0f", prefix: " ~ "}` gives `("~ $", "45", "")`: only the
    /// outer-left edge is stripped (`(explicit_prefix + d3_prefix).lstrip()`).
    #[test]
    fn an_object_format_prefix_drops_only_its_leading_space() {
        assert_eq!(affix_cells(",.0f", " ~ ", "", "all", &[45.0])[0].prefix.as_deref(), Some("~ "));
        assert_eq!(affix_cells("$,.0f", " ~ ", "", "all", &[45.0])[0].prefix.as_deref(), Some("~ $"));
    }

    /// Oracle: `{spec: ",.0f", suffix: " d "}` gives `("", "45", " d")`
    /// and `{spec: ".0%", suffix: " pts"}` on 0.45 gives `("", "45", "% pts")`:
    /// the suffix lane is `(d3_suffix + explicit_suffix).rstrip()`, the measured
    /// string keeps the raw authored text.
    #[test]
    fn an_object_format_suffix_drops_only_its_trailing_space() {
        let cells = affix_cells(",.0f", "", " d ", "all", &[45.0]);
        assert_eq!(cells[0].suffix.as_deref(), Some(" d"));
        assert_eq!(cells[0].full_suffix.as_deref(), Some(" d "));
        let cells = affix_cells(".0%", "", " pts", "all", &[0.45]);
        assert_eq!(cells[0].suffix.as_deref(), Some("% pts"));
        assert_eq!(cells[0].full_suffix.as_deref(), Some("% pts"));
    }

    /// `symbol_mode: anchors` drops the whole prefix and suffix lane on a
    /// non-anchor row (`table.py:2657-2663`, whose `removesuffix` takes the
    /// column suffix `.rstrip()`ed), while measurement keeps the ungated lanes.
    #[test]
    fn a_non_anchor_row_drops_its_affix_lanes_but_measures_them() {
        let cells = affix_cells(",.0f", "CHF ", " d", "anchors", &[45.0, 120.0]);
        assert_eq!((cells[0].prefix.as_deref(), cells[0].suffix.as_deref()), (Some("CHF "), Some(" d")));
        assert_eq!((cells[1].prefix.as_deref(), cells[1].suffix.as_deref()), (None, None));
        assert_eq!((cells[1].full_prefix.as_deref(), cells[1].full_suffix.as_deref()), (Some("CHF "), Some(" d")));
    }

    /// Oracle: `format_kpi_parts(4.5, {spec: "percent_number", prefix: " ~ ",
    /// suffix: " YoY "}, None, default_number=True, is_anchor=True,
    /// keep_affix_gaps=True)` is `("~ ", "4.5", "% YoY")` (`format_value.py:
    /// 271-275`) and `format_table_cell_value` measures `" ~ 4.5% YoY "`.
    #[test]
    fn a_native_format_carries_its_authored_affixes() {
        let cells = affix_cells("percent_number", " ~ ", " YoY ", "anchors", &[4.5, 6.0]);
        assert_eq!(cells[0].text, "4.5");
        assert_eq!((cells[0].prefix.as_deref(), cells[0].suffix.as_deref()), (Some("~ "), Some("% YoY")));
        assert_eq!((cells[0].full_prefix.as_deref(), cells[0].full_suffix.as_deref()), (Some(" ~ "), Some("% YoY ")));
        assert_eq!((cells[1].prefix.as_deref(), cells[1].suffix.as_deref()), (None, None));
    }

    /// A shared-scale column is the one place production does NOT measure
    /// `format_table_cell_value`'s string: both measurement sites format its
    /// scaled digit string through `format_kpi_parts` and concatenate
    /// `prefix + number + suffix` with no gap, because that is the text the
    /// cell paints. Measuring "24.73 M" instead would over-size the column by
    /// a space.
    #[test]
    fn a_shared_scale_columns_magnitude_suffix_is_measured_glued_to_its_number() {
        let fonts = test_fonts();
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg: ColConfig {
                shared_scale: Some(SharedScale { exponent: 6, mode: SuffixMode::Anchor, digit_spec: ",.2~f".into() }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps: Vec<serde_json::Map<String, Value>> = vec![json!({"amount": 24_730_000.0})]
            .into_iter()
            .map(|r| r.as_object().unwrap().clone())
            .collect();
        let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
        let demand = column_demand(&cells, &maps, &col, &fonts, "Inter", 11.0, 8.0).unwrap();
        let oracle = fonts.measure("24.73M", "Inter", 11.0).unwrap() + 16.0;
        assert!(
            (demand - oracle).abs() < 1e-9,
            "scaled digit string is measured glued: {demand} vs oracle {oracle}"
        );
    }

    /// Demands measure each cell with its OWN ungated affixes (production
    /// formats every cell as its own anchor): an auto-SI column mixing
    /// plain values with one K-suffixed outlier must size to the widest
    /// single cell, never to plain values with the outlier's suffix folded
    /// on. Pinned at exact-fit width, where widths == demands and the
    /// centered header sits at the column's midpoint.
    #[test]
    fn si_column_demand_uses_each_cells_own_suffix() {
        let fonts = test_fonts();
        let m = |s: &str| fonts.measure(s, "Inter", 11.0).unwrap();
        // format_numeric_auto: -8.55 -> "−8.55"; 1500 -> "1.5K" (the suffix lane); 2.24 -> "2.24".
        let name_w = m("Name").max(m("China")).max(m("India")) + 16.0;
        let rate_w = m("Rate").max(m("\u{2212}8.55")).max(m("1.5K")).max(m("2.24")) + 16.0;
        let outer = 12.0;
        let width = name_w + rate_w + 2.0 * outer;

        let rows = vec![
            json!({"name": "China", "rate": 2.24}),
            json!({"name": "India", "rate": -8.55}),
            json!({"name": "China", "rate": 1500.0}),
        ];
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": {"name": {"label": "Name"}, "rate": {"label": "Rate"}},
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload: Payload = crate::test_payload(json!({
            "resolved": resolved,
            "rows": rows,
            "width": width,
            "height": 300.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let idx = svg.find(">Rate</text>").expect("Rate header");
        let x: f64 = svg[..idx]
            .rsplit("<text x=\"")
            .next()
            .and_then(|t| t.split('"').next())
            .and_then(|t| t.parse().ok())
            .expect("x parses");
        let expected_mid = outer + name_w + rate_w / 2.0;
        assert!(
            (x - expected_mid).abs() < 0.05,
            "rate column width equals its own-affix demand (header mid {x} vs {expected_mid}); \
             folding the K suffix onto every cell would widen the column"
        );
    }

    /// The column format is read by the one `FormatSlot` parser: `repeat`
    /// (the table's `symbol_mode`) and `raw` parse, a key the slot does not
    /// know refuses by name instead of vanishing, and so does
    /// `sign_placement`, which a column never paints: production raises
    /// `ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED` at resolve
    /// (`compile/resolve/chart/_table.py:614-616`) because the prefix lane paints
    /// apart from the signed digits.
    #[test]
    fn a_column_format_reads_every_key_through_the_slot_parser() {
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({"format": {
            "spec": ",.0f", "prefix": "US$", "repeat": "every", "raw": "integer"
        }}))
        .expect("parses");
        let slot = cfg.format.expect("a format");
        assert_eq!((slot.prefix.as_str(), slot.repeat.as_deref(), slot.raw.as_deref()), ("US$", Some("every"), Some("integer")));
        let err = |format| serde_json::from_value::<ColConfig>(json!({ "format": format })).err().unwrap().to_string();
        assert!(err(json!({"spec": ",.0f", "bogus": 1})).contains("unknown format key \"bogus\""));
        let placed =
            err(json!({"spec": ",.0f", "prefix": "US$", "sign_placement": "after_prefix"}));
        assert!(placed.contains("sign_placement") && placed.contains("not painted"), "{placed}");
        let notated = crate::families::table::tests::col_config(json!({"format": {"spec": ",.0f", "notation": "narrative"}}))
            .expect("parses");
        assert_eq!(notated.format.expect("a format").notation.as_deref(), Some("narrative"));
    }

    /// Oracle: `format_kpi_parts(0.67, ResolvedFormat(spec="$.3~s",
    /// raw="currency"), None, default_number=True, is_anchor=True)` is
    /// `('$', '0.67', '')`; without `raw` it is `('$', '670m', '')`. The
    /// authored name selects the sub-unit fallback the spec alone cannot.
    #[test]
    fn a_predefined_raw_name_keeps_its_sub_unit_fallback() {
        let _scope = super::tests::en_us_scope();
        let col = ColMeta {
            name: "amt".to_string(),
            header: "Amt".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot { raw: Some("currency".into()), ..FormatSlot::from("$.3~s") }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let maps = vec![json!({"amt": 0.67}).as_object().unwrap().clone()];
        let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
        assert_eq!((cells[0].prefix.as_deref(), cells[0].text.as_str()), (Some("$"), "0.67"));
    }

    fn col_with(name: &'static str, config: Value) -> ColMeta {
        ColMeta {
            name: name.to_string(),
            header: name.to_string(),
            cfg: crate::families::table::tests::col_config(config).expect("a column config"),
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        }
    }

    /// Oracle: `format_kpi_parts(1234567.0, resolve_format_parts(name),
    /// default_number=True, shared_scale=ResolvedColumnSharedScale(exponent=6,
    /// mode=REPEAT, digit_spec=",.2~f"), keep_affix_gaps=True)` is `('', '1.23',
    /// 'M')` for `compact` and `('$', '1.23', 'M')` for `currency_compact`, but
    /// `('', '1.23', 'mn')` for `number`: the analytic notation the two-figure
    /// names bake in wins over the repeat mode's narrative register
    /// (`format_value.py:319-323`).
    #[test]
    fn a_compact_column_spells_its_authored_register_on_a_repeat_scale() {
        let suffix = |format: Value| {
            let col = col_with(
                "amt",
                json!({"format": format, "shared_scale": {"exponent": 6, "mode": "repeat", "digit_spec": ",.2~f"}}),
            );
            let maps = vec![json!({"amt": 1234567.0}).as_object().unwrap().clone()];
            let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
            (cells[0].prefix.clone(), cells[0].text.clone(), cells[0].suffix.clone())
        };
        let compact = json!({"spec": ".2~s", "raw": "compact", "notation": "analytic"});
        assert_eq!(suffix(compact), (None, "1.23".into(), Some("M".into())));
        let currency_compact = json!({"spec": "$.2~s", "raw": "currency_compact", "notation": "analytic"});
        assert_eq!(suffix(currency_compact), (Some("$".into()), "1.23".into(), Some("M".into())));
        assert_eq!(suffix(json!({"spec": ".3~s", "raw": "number"})), (None, "1.23".into(), Some("mn".into())));
    }

    /// Oracle: `format_kpi_parts(v, resolve_format_parts("compact"),
    /// default_number=True, keep_affix_gaps=True)` is `('', '45', 'K')` for
    /// `45123.0`, `('', '12', '')` for `12.0` and `('', '0.50', '')` for `0.5`
    /// (the `number_full` sibling); `currency_compact` leads `45` with `$`.
    #[test]
    fn an_unscaled_compact_column_splits_two_figures_and_an_analytic_word() {
        let lanes = |format: Value, v: f64| {
            let col = col_with("amt", json!({ "format": format }));
            let maps = vec![json!({"amt": v}).as_object().unwrap().clone()];
            let cells = format_numeric_col(&maps, &col, "all", &[RowRole::Value]).expect("formats");
            (cells[0].prefix.clone(), cells[0].text.clone(), cells[0].suffix.clone())
        };
        let compact = || json!({"spec": ".2~s", "raw": "compact", "notation": "analytic"});
        assert_eq!(lanes(compact(), 45123.0), (None, "45".into(), Some("K".into())));
        assert_eq!(lanes(compact(), 12.0), (None, "12".into(), None));
        assert_eq!(lanes(compact(), 0.5), (None, "0.50".into(), None));
        let currency_compact = json!({"spec": "$.2~s", "raw": "currency_compact", "notation": "analytic"});
        assert_eq!(lanes(currency_compact, 45123.0), (Some("$".into()), "45".into(), Some("K".into())));
    }

    /// Oracle: production's `table_columns` reference (`number-format-affixes`,
    /// table `symbol_mode: anchors`) paints `US$` on the first row of `balance`
    /// (`US$`, no `repeat`) and on all three rows of `budget` (`repeat:
    /// every`): a column's `repeat` overrides the table's mode
    /// (`column_symbol_mode`, `compile/format.py:488`).
    #[test]
    fn a_column_repeat_overrides_the_tables_symbol_mode() {
        let col = |name: &str, repeat: Option<&str>| ColMeta {
            name: name.to_string(),
            header: name.to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot {
                    prefix: "US$".into(),
                    repeat: repeat.map(str::to_string),
                    ..FormatSlot::from(",.0f")
                }),
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let cols = [col("balance", None), col("budget", Some("every"))];
        let rows: Vec<serde_json::Map<String, Value>> = [(168000.0, 150000.0), (-41500.0, -30000.0), (-12000.0, -10000.0)]
            .iter()
            .map(|(b, g)| json!({"balance": b, "budget": g}).as_object().unwrap().clone())
            .collect();
        let roles = [RowRole::Value, RowRole::Value, RowRole::Value];
        let cells = build_cell_data(&rows, &cols, "anchors", &roles).expect("formats");
        let prefixes = |column: usize| cells[column].iter().map(|c| c.prefix.as_deref()).collect::<Vec<_>>();
        assert_eq!(prefixes(0), [Some("US$"), None, None]);
        assert_eq!(prefixes(1), [Some("US$"), Some("US$"), Some("US$")]);
        let anchored = |repeat: &str| {
            let cols = [col("balance", Some(repeat))];
            let cells = build_cell_data(&rows, &cols, "all", &roles).expect("formats");
            cells[0].iter().map(|c| c.prefix.is_some()).collect::<Vec<_>>()
        };
        assert_eq!(anchored("anchor"), [true, false, false], "an anchored column overrides an `all` table");
    }
}

#[cfg(test)]
mod link_paint_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::*;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// The last `<text ...>` open tag in the SVG — the single data cell in
    /// these single-row, single-column fixtures, isolated from the title's
    /// own (unrelated) weight/color attributes.
    fn last_text_tag(svg: &str) -> &str {
        let start = svg.rfind("<text").expect("a <text> tag exists");
        let end = svg[start..].find('>').expect("tag closes") + start;
        &svg[start..=end]
    }

    /// A distinctive body/link color (not production's own `#2a2725`, to
    /// prove this renderer reads the theme rather than a hardcoded literal)
    /// plus an optional `row.role`/`conditional_formatting` for the tests
    /// that exercise the cascade's higher-precedence steps.
    fn link_payload(
        columns: Value,
        rows: Vec<Value>,
        row_role: Option<&str>,
        conditional_formatting: Value,
    ) -> Payload {
        let mut theme = super::tests::base_table_theme();
        theme["font"]["color"] = json!("#123456");
        theme["row"]["role"] = json!(row_role);
        let resolved = json!({
            "link_open": "page",
            "title": "Test Table",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": conditional_formatting,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 400.0,
            "height": 300.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"font_color": "#123456"},
        });
        crate::test_payload(payload_json)
    }

    /// A column authoring `link:` used to refuse the whole
    /// table (`"table: per-cell links on column 'company' not supported"`).
    /// A linked cell now wraps in the anchor/group and paints at the
    /// board/theme font color with the medium (500) weight —
    /// — while an unlinked sibling column stays
    /// plain text at the row's own (unbumped) weight.
    #[test]
    fn a_linked_value_cell_wraps_in_an_anchor_at_the_body_color_and_500_weight() {
        let rows = vec![json!({"company": "Nucleus", "region": "West"})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
            "region": {"label": "Region"},
        });
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).expect("a linked column must render, not refuse");
        assert!(
            svg.contains(
                "<a href=\"/accounts/Nucleus\"><g class=\"dbt-table-link\" style=\"color: #123456\">"
            ),
            "anchor + group wrapper missing:\n{svg}"
        );
        // Cell-scoped: the title paints 500 too, so a whole-SVG
        // `contains` would pass with the link default deleted.
        let link_idx = svg.find("<text class=\"dbt-table-link-text\"").expect("link text tag");
        let link_tag = &svg[link_idx..link_idx + svg[link_idx..].find('>').unwrap()];
        assert!(
            link_tag.contains("fill=\"#123456\"") && link_tag.contains("font-weight=\"500\""),
            "linked text fill/weight missing on its own tag:\n{link_tag}"
        );
        assert!(svg.contains("</g></a>"), "anchor/group close missing:\n{svg}");
        let region_idx = svg.find(">West<").expect("region cell renders");
        let region_text_start = svg[..region_idx].rfind("<text").unwrap();
        assert!(
            !svg[region_text_start..region_idx].contains("font-weight"),
            "unlinked sibling column must not pick up the 500 weight:\n{svg}"
        );
    }

    /// Deleting the `cell_style.weight` precedence (falling straight to the link default) turns
    /// this red — 500, not 700.
    #[test]
    fn a_conditional_format_weight_wins_over_the_linked_default() {
        let rows = vec![json!({"company": "Nucleus"})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
        });
        let cf = json!({"company": {"when": [
            {"eq": "Nucleus", "font": {"weight": "700"}}
        ]}});
        let payload = link_payload(columns, rows, None, cf);
        let svg = render(&payload, &test_fonts()).unwrap();
        let cell_tag = last_text_tag(&svg);
        assert!(cell_tag.contains("font-weight=\"700\""), "conditional weight missing:\n{cell_tag}");
        assert!(
            !cell_tag.contains("font-weight=\"500\""),
            "linked 500 default must not win:\n{cell_tag}"
        );
    }

    #[test]
    fn a_linked_summary_row_keeps_its_role_weight_over_the_linked_default() {
        let rows = vec![json!({"company": "Total", "row_role": "summary"})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
        });
        let mut payload = link_payload(columns, rows, Some("row_role"), Value::Null);
        // Bump the summary role's own weight past the linked 500 default so
        // the two are distinguishable.
        payload.resolved["style"]["table"]["row"]["roles"]["summary"]["font"] =
            json!({"weight": "600"});
        let svg = render(&payload, &test_fonts()).unwrap();
        let cell_tag = last_text_tag(&svg);
        assert!(cell_tag.contains("font-weight=\"600\""), "summary role weight missing:\n{cell_tag}");
        assert!(
            !cell_tag.contains("font-weight=\"500\""),
            "linked 500 default must not win:\n{cell_tag}"
        );
    }

    ///a `None` template placeholder yields no
    /// link for that row — the cell paints as plain text, no anchor.
    #[test]
    fn a_null_placeholder_row_paints_plain_text_with_no_anchor() {
        let rows = vec![json!({"company": null})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
        });
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href="), "no href expected for a null placeholder:\n{svg}");
        let cell_tag = last_text_tag(&svg);
        assert!(!cell_tag.contains("font-weight"), "no weight bump for an unlinked cell:\n{cell_tag}");
        assert!(
            !cell_tag.contains("dbt-table-link-text"),
            "no link class for a null-placeholder cell:\n{cell_tag}"
        );
    }

    /// html.escape(quote=True) escapes both quotes as well as `&` — a row
    /// value carrying `"` must not close the href attribute and turn the
    /// rest of the value into markup.
    #[test]
    fn a_quote_in_the_href_is_escaped_and_cannot_close_the_attribute() {
        let rows = vec![json!({"company": "A\" onclick=\"alert(1)"})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
        });
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("href=\"/accounts/A&quot; onclick=&quot;alert(1)\""),
            "quotes must be escaped in the href:\n{svg}"
        );
        let a_idx = svg.find("<a ").expect("anchor");
        let a_tag = &svg[a_idx..a_idx + svg[a_idx..].find('>').unwrap()];
        assert_eq!(
            a_tag, "<a href=\"/accounts/A&quot; onclick=&quot;alert(1)\"",
            "the anchor must carry exactly one attribute; data must not become a second one"
        );
    }

    /// `if link_raw:` — an empty `link:` is falsy in production, so the
    /// cell paints as plain text with no anchor and no 500 weight.
    #[test]
    fn an_empty_link_string_paints_a_plain_cell() {
        let rows = vec![json!({"company": "Nucleus"})];
        let columns = json!({"company": {"label": "Company", "link": ""}});
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href="), "empty link must not paint an anchor:\n{svg}");
        assert!(!last_text_tag(&svg).contains("font-weight=\"500\""), "no link weight:\n{svg}");
    }

    /// A verbatim-column link whose cell value is the empty string is no
    /// link either: production returns `""`, which `if cell_link:` drops.
    #[test]
    fn a_verbatim_column_link_with_an_empty_value_paints_a_plain_cell() {
        let rows = vec![json!({"company": "Nucleus", "url": ""})];
        let columns = json!({"company": {"label": "Company", "link": "url"}, "url": {"label": "URL"}});
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href="), "empty value must not paint an anchor:\n{svg}");
    }

    /// html.escape(quote=True) escapes `&` in the href — this renderer
    /// uses `xml_escape_attr`, which escapes `&` and both quotes.
    #[test]
    fn an_ampersand_in_the_href_is_escaped() {
        let rows = vec![json!({"company": "A&B"})];
        let columns = json!({
            "company": {"label": "Company", "link": "/accounts/{{ company }}"},
        });
        let payload = link_payload(columns, rows, None, Value::Null);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("href=\"/accounts/A&amp;B\""), "ampersand must be escaped:\n{svg}");
    }
}

/// `ResolvedChart.link` — the whole-row detail link `links.auto`/FK-drill
/// synthesizes on a table chart's own row grain (`automatic_link.py::
/// _table_row_detail_link`, `resolve_column_set_link(row_detail=True)`).
/// Distinct from `link_paint_tests` above, which covers a per-COLUMN
/// `style.columns.<col>.link` — this is the chart-ROOT `link:`, rendered as
/// one clickable band per value row, never a per-cell
/// anchor. The band's anchor/rect markup and the pointer-inert classes on the
/// row's other content are pinned by `tests/it/table_row_link.rs`; this module
/// pins the href resolution and the band's aria-label.
#[cfg(test)]
mod row_link_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::*;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn row_link_payload(link: Option<&str>, rows: Vec<Value>) -> Payload {
        let theme = super::tests::base_table_theme();
        let resolved = json!({
            "link_open": "page",
            "title": "Orders",
            "title_style": resolved_title_style(),
            "link": link,
            "columns": {"id": {"label": "Id"}, "status": {"label": "Status"}},
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": rows,
            "width": 400.0,
            "height": 300.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        });
        let mut payload: Payload = crate::test_payload(payload_json);
        payload.board_style = crate::test_board_style(json!({"font_color": "#123456"}));
        payload
    }

    /// The row band's `--dbt-link` is the board's link color, proven to be one
    /// color before it reaches a style attribute.
    #[test]
    fn a_row_link_paints_the_board_link_color_and_refuses_a_hostile_one() {
        let rows = vec![json!({"id": 1, "status": "open"})];
        let link = Some("/d/?id={{ id }}");
        let svg = render(&row_link_payload(link, rows.clone()), &test_fonts()).unwrap();
        assert!(svg.contains("style=\"--dbt-link: #123456\""), "{svg}");
        let mut hostile = row_link_payload(link, rows.clone());
        hostile.board_style = crate::test_board_style(json!({"font_color": "red\" onmouseover=\"x"}));
        let err = render(&hostile, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("font_color") || err.contains("color"), "{err}");
        let mut absent = row_link_payload(link, rows);
        absent.board_style = crate::test_board_style(json!({}));
        let err = render(&absent, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("font_color"), "{err}");
    }

    /// `automatic_link.py::_table_row_detail_link`'s own URL shape: one row-band
    /// anchor per value row, keyed by the row's own `id` column.
    #[test]
    fn a_row_detail_link_emits_one_anchor_per_row() {
        let rows = vec![
            json!({"id": 1, "status": "open"}),
            json!({"id": 2, "status": "closed"}),
        ];
        let payload = row_link_payload(
            Some("/data/dw/main/orders/detail/?id={{ id | urlencode }}"),
            rows,
        );
        let svg = render(&payload, &test_fonts()).expect("a chart-root link must render");
        assert!(
            svg.contains("<a href=\"/data/dw/main/orders/detail/?id=1\""),
            "row 1's own detail href missing:\n{svg}"
        );
        assert!(
            svg.contains("<a href=\"/data/dw/main/orders/detail/?id=2\""),
            "row 2's own detail href missing:\n{svg}"
        );
    }

    fn link_open_payload() -> Payload {
        let rows = vec![json!({"id": 1, "status": "open"}), json!({"id": 2, "status": "closed"})];
        row_link_payload(Some("/d/?id={{ id }}"), rows)
    }

    fn stamped(svg: &str) -> usize {
        svg.matches("data-dbt-link-open=\"modal\"").count()
    }

    /// A page-mode (or unauthored) link adds nothing: the SVG is byte-identical.
    #[test]
    fn a_page_mode_link_renders_byte_identical_to_an_unmoded_one() {
        let unmoded = render(&link_open_payload(), &test_fonts()).unwrap();
        let mut page = link_open_payload();
        page.resolved["link_open"] = json!("page");
        page.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        page.resolved["columns"]["status"]["link_open"] = json!("page");
        page.resolved["columns"]["status"]["header_link"] = json!("/h");
        page.resolved["columns"]["status"]["header_link_open"] = json!("page");
        let mut bare = link_open_payload();
        bare.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        bare.resolved["columns"]["status"]["header_link"] = json!("/h");
        assert_eq!(render(&page, &test_fonts()).unwrap(), render(&bare, &test_fonts()).unwrap());
        assert_eq!(stamped(&unmoded), 0);
    }

    /// A modal chart link stamps each row band anchor; the column's own
    /// page-mode link and header link stay unstamped.
    #[test]
    fn a_modal_row_link_stamps_only_the_row_bands() {
        let mut payload = link_open_payload();
        payload.resolved["link_open"] = json!("modal");
        payload.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        payload.resolved["columns"]["status"]["header_link"] = json!("/h");
        let svg = render(&payload, &test_fonts()).unwrap();
        assert_eq!(stamped(&svg), 2, "{svg}");
        assert!(svg.contains("<a href=\"/d/?id=1\" aria-label=\"1\" data-dbt-link-open=\"modal\">"), "{svg}");
        assert!(svg.contains("<a href=\"/s/open\"><g class=\"dbt-table-link\""), "{svg}");
        assert!(svg.contains("<a href=\"/h\"><text"), "{svg}");
    }

    /// A column's `link_open` and `header_link_open` each govern only their own anchor.
    #[test]
    fn column_modes_are_independent_of_the_chart_and_each_other() {
        let mut payload = link_open_payload();
        payload.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        payload.resolved["columns"]["status"]["link_open"] = json!("modal");
        payload.resolved["columns"]["status"]["header_link"] = json!("/h");
        let svg = render(&payload, &test_fonts()).unwrap();
        assert_eq!(stamped(&svg), 2, "{svg}");
        assert!(svg.contains("<a href=\"/s/open\" data-dbt-link-open=\"modal\"><g class=\"dbt-table-link\""), "{svg}");
        assert!(svg.contains("<a href=\"/d/?id=1\" aria-label=\"1\">"), "{svg}");
        assert!(svg.contains("<a href=\"/h\"><text"), "{svg}");

        let mut header = link_open_payload();
        header.resolved["columns"]["status"]["header_link"] = json!("/h");
        header.resolved["columns"]["status"]["header_link_open"] = json!("modal");
        let svg = render(&header, &test_fonts()).unwrap();
        assert_eq!(stamped(&svg), 1, "{svg}");
        assert!(svg.contains("<a href=\"/h\" data-dbt-link-open=\"modal\"><text"), "{svg}");
    }

    /// A new-tab row, cell or header link targets a new tab on its own anchor.
    #[test]
    fn a_new_tab_link_targets_a_new_tab() {
        let mut payload = link_open_payload();
        payload.resolved["link_open"] = json!("new_tab");
        payload.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        payload.resolved["columns"]["status"]["link_open"] = json!("new_tab");
        payload.resolved["columns"]["status"]["header_link"] = json!("/h");
        payload.resolved["columns"]["status"]["header_link_open"] = json!("new_tab");
        let svg = render(&payload, &test_fonts()).unwrap();
        let tab = "data-dbt-link-open=\"new_tab\" target=\"_blank\"";
        assert!(svg.contains(&format!("<a href=\"/d/?id=1\" aria-label=\"1\" {tab}>")), "{svg}");
        assert!(svg.contains(&format!("<a href=\"/s/open\" {tab}><g class=\"dbt-table-link\"")), "{svg}");
        assert!(svg.contains(&format!("<a href=\"/h\" {tab}><text")), "{svg}");
    }

    /// The implied `?col=value` filter link is always page, whatever the column says.
    #[test]
    fn a_filter_variable_cell_link_is_never_modal() {
        let mut payload = link_open_payload();
        payload.resolved["link"] = Value::Null;
        payload.resolved["link_open"] = json!("modal");
        payload.resolved["columns"]["status"]["link_open"] = json!("modal");
        payload.filter_variables = vec!["status".to_string()];
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"?status=open\">"), "{svg}");
        assert_eq!(stamped(&svg), 0, "{svg}");
    }

    /// An unknown mode is a deserialization error, never a silent page.
    #[test]
    fn an_unknown_link_mode_is_refused() {
        for path in ["chart", "column", "header"] {
            let mut payload = link_open_payload();
            match path {
                "chart" => payload.resolved["link_open"] = json!("popup"),
                "column" => payload.resolved["columns"]["status"]["link_open"] = json!("popup"),
                _ => payload.resolved["columns"]["status"]["header_link_open"] = json!("popup"),
            }
            let err = render(&payload, &test_fonts()).unwrap_err().to_string();
            assert!(err.contains("popup") || err.contains("link_open"), "{path}: {err}");
        }
    }

    /// A pivot reshapes rows into leaf columns, so the row-detail template's
    /// base columns are gone — refuse rather than silently drop every link.
    #[test]
    fn a_pivot_table_with_a_row_link_refuses_by_name() {
        let rows = vec![json!({"id": 1, "status": "open", "amount": 5.0})];
        let mut payload = row_link_payload(Some("/d/?id={{ id | urlencode }}"), rows);
        payload.resolved["pivot_columns"] = json!(["status"]);
        payload.resolved["rows"] = json!(["id"]);
        payload.resolved["values"] = json!(["amount"]);
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("chart-root link"), "names the unsupported shape: {err}");
    }

    /// A chart with no `link:` spec at all emits no anchor — the row band
    /// is opt-in, never a default.
    #[test]
    fn no_link_spec_emits_no_wrapper() {
        let rows = vec![json!({"id": 1, "status": "open"})];
        let payload = row_link_payload(None, rows);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href="), "no href expected with no link spec:\n{svg}");
    }

    ///a `None` template placeholder yields no
    /// link for that row — same "has_none" short-circuit the per-cell path
    /// already pins, exercised here on the chart-root path instead.
    #[test]
    fn a_null_placeholder_row_emits_no_wrapper() {
        let rows = vec![json!({"id": null, "status": "open"})];
        let payload = row_link_payload(
            Some("/data/dw/main/orders/detail/?id={{ id | urlencode }}"),
            rows,
        );
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href="), "no href expected for a null id:\n{svg}");
    }

    fn text_format_payload(format: bool, rows: Vec<Value>) -> Payload {
        let theme = super::tests::base_table_theme();
        let column = |link: &str, spec: &str| {
            let mut column = json!({"link": link});
            if format {
                column["format"] = json!({"spec": spec, "raw": spec});
            }
            column
        };
        let resolved = json!({
            "link_open": "page",
            "title": "People",
            "title_style": resolved_title_style(),
            "link": null,
            "columns": {
                "mail": column("mailto:{{ mail | urlencode }}", "email"),
                "site": column("site", "url"),
            },
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let mut payload: Payload = crate::test_payload(json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 300.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }));
        payload.board_style = crate::test_board_style(json!({"font_color": "#123456"}));
        payload
    }

    /// An `email` / `url` column prints as a formatless text column whose blank
    /// cells are null: the same SVG as the column with its format dropped.
    #[test]
    fn a_text_format_column_prints_its_value_and_links_only_the_cells_with_one() {
        let rows = |blank: Value| {
            vec![
                json!({"mail": "ann+x@example.com", "site": "https://a.example/p?q=1"}),
                json!({"mail": blank.clone(), "site": blank}),
            ]
        };
        let svg = render(&text_format_payload(true, rows(json!(""))), &test_fonts()).unwrap();
        let lowered = render(&text_format_payload(false, rows(Value::Null)), &test_fonts()).unwrap();
        assert_eq!(svg, lowered);
        assert!(svg.contains("<a href=\"mailto:ann%2Bx%40example.com\""), "{svg}");
        assert!(svg.contains("<a href=\"https://a.example/p?q=1\""), "{svg}");
        assert_eq!(svg.matches("<a href=").count(), 2, "{svg}");
        assert!(svg.contains("ann+x@example.com") && svg.contains(NULL_DISPLAY), "{svg}");
    }

    /// The row-detail path's `urlencode` filter is `quote_plus` (a space is
    /// `+`, where the chart-mark path's `_vega_encode_field` writes `%20`).
    #[test]
    fn a_special_character_id_is_urlencoded() {
        let rows = vec![json!({"id": "A&B C", "status": "open"})];
        let payload = row_link_payload(
            Some("/data/dw/main/orders/detail/?id={{ id | urlencode }}"),
            rows,
        );
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("<a href=\"/data/dw/main/orders/detail/?id=A%26B+C\""),
            "urlencoded href missing:\n{svg}"
        );
    }
    /// `str(row[label_col])` on a null cell is `"None"` — a non-empty label,
    /// so the destination URL is never substituted for it.
    #[test]
    fn a_null_label_cell_names_the_band_none() {
        let rows = vec![json!({"id": null, "status": "open"})];
        let payload = row_link_payload(Some("/d/?s={{ status }}"), rows);
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"/d/?s=open\" aria-label=\"None\">"), "{svg}");
    }

    /// A cell that carries its own link keeps `dbt-table-link-text`; only
    /// cells with no link of their own turn pointer-inert in a banded row.
    #[test]
    fn a_cell_with_its_own_link_is_not_inert_in_a_banded_row() {
        let rows = vec![json!({"id": 1, "status": "open"})];
        let mut payload = row_link_payload(Some("/d/?id={{ id }}"), rows);
        payload.resolved["columns"]["status"]["link"] = json!("/s/{{ status }}");
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<text class=\"dbt-table-link-text\""), "{svg}");
        assert_eq!(svg.matches("class=\"dbt-table-cell-inert\"").count(), 1, "only the id cell: {svg}");
    }
}

/// Two gaps this task's sweep-driven debugging surfaced on `accounts_table`
/// once its per-cell `link` refusal no longer masked the row entirely —
/// both pre-existing, both unrelated to the link feature itself (a table's
/// own `link` markup matches production byte-for-byte; see
/// `link_paint_tests` above), both required to get the sweep comparing real
/// pixels instead of ~40% canvas-wide noise.
#[cfg(test)]
mod background_and_font_family_tests {
    use super::tests::{resolved_title_font, resolved_title_style};
    use super::*;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir =
            format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn payload_with(table_extra: Value) -> Payload {
        let mut theme = super::tests::base_table_theme();
        // The fixture's own header font carries an explicit `family`,
        // shadowing body's for any test that overrides only the body font —
        // clear it so `header_family` falls back to (normalized) body,
        // matching production's actual law: header and body cell text
        // share ONE font family ( — `table_font_family`
        // reused for both, header.font.family is never itself the paint
        // source).
        theme["header"]["font"]["family"] = Value::Null;
        if let Value::Object(extra) = table_extra {
            for (k, v) in extra {
                theme[k] = v;
            }
        }
        let resolved = json!({
            "link_open": "page",
            "title": null,
            "title_style": resolved_title_style(),
            "columns": {"a": {"label": "A"}},
            "style": {"table": theme, "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        });
        let payload_json = json!({
            "resolved": resolved,
            "rows": [{"a": "x"}],
            "width": 200.0,
            "height": 100.0,
            // The card/page canvas color — production never paints this
            // into a table's own SVG ( reads the
            // TABLE's own `background`, not this).
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},"font_color": "#222222"},
        });
        crate::test_payload(payload_json)
    }

    /// Omits the background rect entirely when `style.table.background` is unset —
    /// production leaves whatever sits behind the table (card chrome) showing through. This
    /// renderer used to paint `payload.background` (the card canvas) unconditionally instead,
    /// opaquing over chrome no authored table asked to cover, and on a full-canvas rect that alone
    /// moved a swept table row's diff_frac from ~0% to double digits.
    // `rx="4"` is the background rect's own signature — no
    // other rect in this renderer sets a corner radius, so its absence is a
    // clean proxy for "no background rect painted" alongside the header/row
    // rule rects this fixture always draws.
    #[test]
    fn no_authored_table_background_paints_no_rect() {
        let payload = payload_with(json!({}));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("rx=\"4\""), "no background rect expected:\n{svg}");
        assert!(!svg.contains("#ffffff"), "the card canvas color must not leak in:\n{svg}");
    }

    #[test]
    fn an_authored_table_background_paints_the_rounded_rect() {
        let payload = payload_with(json!({"background": "#112233"}));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("><rect x=\"0\" y=\"0\" width=\"200\" height=\"")
                && svg.contains("fill=\"#112233\" rx=\"4\"/>"),
            "authored background rect missing:\n{svg}"
        );
    }

    /// Regression: `svg_utils.card_box` (`render/svg_utils.py:173`) grows the background rect back
    /// out over the card padding `board_composer::slot_dims` shrank this table's render box by, so
    /// the fill reaches the same outer box a Vega chart's spec-baked background does
    /// (`playground/card-background-parity-lab`'s `detail` table). This renderer used to paint the
    /// rect at the inner box only (`x="0" y="0"`), reading no inset at all.
    #[test]
    fn an_authored_background_reaches_back_over_the_card_padding() {
        let mut payload = payload_with(json!({"background": "#112233"}));
        payload.padding =
            Some(crate::Padding { left: 16.0, right: 16.0, top: 16.0, bottom: 16.0 });
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("<rect x=\"-16\" y=\"-16\" width=\"232\" height=\"")
                && svg.contains("fill=\"#112233\" rx=\"4\"/>"),
            "card-padding-reaching background rect missing:\n{svg}"
        );
    }

    /// `_render_header_section` fills the header band across the content span
    /// before its rule; a transparent or unset fill paints nothing.
    #[test]
    fn an_authored_header_background_fills_the_header_band() {
        let with_header_fill = |fill: &str| {
            let mut payload = payload_with(json!({}));
            payload.resolved["style"]["table"]["header"]["background"] = json!(fill);
            render(&payload, &test_fonts()).unwrap()
        };
        let svg = with_header_fill("#f5f5f5");
        assert!(svg.contains("fill=\"#f5f5f5\"/>"), "header band fill missing:\n{svg}");
        let transparent = with_header_fill("transparent");
        assert!(!transparent.contains("transparent"), "{transparent}");
    }

    /// A column with no `link:` that names a declared board variable links
    /// each cell to `?col=value` (`resolve_filter_cell_link`); a column that
    /// is not a variable stays text.
    #[test]
    fn a_declared_filter_variable_column_links_its_cells() {
        let mut payload = payload_with(json!({}));
        payload.rows = vec![json!({"a": "x y"}).as_object().unwrap().clone()];
        payload.filter_variables = vec!["a".to_string()];
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"?a=x+y\">"), "filter link missing:\n{svg}");
        payload.filter_variables = vec!["other".to_string()];
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("<a href"), "{svg}");
    }

    /// `_compute_lane_positions` measures numeric cells only: a null cell's
    /// placeholder never widens the lane, so a lone narrow number sits where it
    /// would in a column with no null.
    #[test]
    fn a_null_cell_does_not_widen_the_numeric_lane() {
        let number_x = |rows: Vec<Value>| {
            let mut payload = payload_with(json!({}));
            payload.resolved["style"]["table"]["pagination"]["enabled"] = json!(false);
            payload.rows = rows.iter().map(|r| r.as_object().unwrap().clone()).collect();
            let svg = render(&payload, &test_fonts()).unwrap();
            let tail = svg.split("<tspan x=\"").nth(1).unwrap_or_else(|| panic!("{svg}"));
            tail.split('"').next().unwrap().to_string()
        };
        assert_eq!(
            number_x(vec![json!({"a": null}), json!({"a": 5.0})]),
            number_x(vec![json!({"a": 5.0})])
        );
    }

    /// Numerals take `style.fonts.numeral` whatever the body family: a serif
    /// body keeps its serif on text cells only. A Source Serif numeral family paints
    /// with the bundled-web alias.
    #[test]
    fn table_numerals_follow_the_board_numeral_family_not_the_body() {
        let render_with = |numeral: Option<&str>| {
            let mut payload = payload_with(json!({}));
            payload.rows = vec![json!({"a": "x", "b": 95}).as_object().unwrap().clone()];
            payload.resolved["columns"] = json!({"a": {"label": "A", "link_open": "page", "header_link_open": "page"}, "b": {"label": "B", "link_open": "page", "header_link_open": "page"}});
            let table = &mut payload.resolved["style"]["table"];
            table["font"]["family"] = json!("'Source Serif 4', Georgia, serif");
            table["header"]["font"]["family"] = json!("'Inter Variable', Inter, sans-serif");
            if let Some(numeral) = numeral {
                payload.board_style = crate::test_board_style(json!({"numeral_family": numeral}));
            }
            render(&payload, &test_fonts()).unwrap()
        };
        let serif = "font-family=\"&apos;Source Serif 4 Web&apos;, &apos;Source Serif 4&apos;, Georgia, serif\"";
        let tabular = format!("font-family=\"{}\"", crate::common::TAB_FONT.replace('\'', "&apos;"));

        let default = render_with(None);
        assert_eq!(default.matches(serif).count(), 1, "text cell only:\n{default}");
        assert_eq!(default.matches(tabular.as_str()).count(), 1, "numeral:\n{default}");
        assert!(default.contains("font-family=\"Inter Variable, Inter, sans-serif\">A</text>"), "{default}");

        let serif_numeral = render_with(Some("'Source Serif 4', Georgia, serif"));
        assert_eq!(serif_numeral.matches(serif).count(), 2, "text cell and numeral:\n{serif_numeral}");
    }

    /// A body at or under 11px takes the header's `font_compact.weight`.
    #[test]
    fn a_compact_body_takes_the_compact_header_weight() {
        let weight_of_header = |size: f64| {
            let mut payload = payload_with(json!({}));
            let table = &mut payload.resolved["style"]["table"];
            table["font"]["size"] = json!(size);
            table["header"]["font"]["weight"] = json!("600");
            table["header"]["font_compact"] = json!({"weight": "500"});
            let svg = render(&payload, &test_fonts()).unwrap();
            let head = svg.split(">A</text>").next().unwrap().to_string();
            head.rsplit("font-weight=\"").next().unwrap().split('"').next().unwrap().to_string()
        };
        assert_eq!(weight_of_header(11.0), "500");
        assert_eq!(weight_of_header(14.0), "600");
    }

    /// Zero rows over resolved columns: the header band and a centered
    /// em dash.
    #[test]
    fn an_empty_result_keeps_the_header_and_paints_an_em_dash() {
        let mut payload = payload_with(json!({}));
        payload.rows = vec![];
        payload.resolved["style"]["table"]["empty_state"] =
            json!({"font": {"size": 14.0, "color": "#626366"}});
        let svg = crate::families::empty::render("table", &payload, &test_fonts()).unwrap();
        assert!(svg.contains(">A</text>"), "header label missing:\n{svg}");
        assert!(svg.contains(">\u{2014}</text>"), "{svg}");
        assert!(!svg.contains("No data"), "{svg}");
    }

    /// The y of the `<text>` element whose content is `label`.
    fn text_y(svg: &str, label: &str) -> f64 {
        let open = svg.find(&format!(">{label}</text>")).unwrap();
        let tag = &svg[svg[..open].rfind("<text").unwrap()..open];
        let y = tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap();
        y.parse().unwrap()
    }

    /// The em dash sits in a body region below the header band and
    /// inside the canvas, never over the column headers.
    #[test]
    fn the_empty_mark_sits_below_the_header_band_inside_the_canvas() {
        let mut payload = payload_with(json!({}));
        payload.rows = vec![];
        payload.resolved["style"]["table"]["empty_state"] =
            json!({"font": {"size": 14.0, "color": "#626366"}});
        let svg = crate::families::empty::render("table", &payload, &test_fonts()).unwrap();
        let canvas_h: f64 =
            svg.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let no_data = text_y(&svg, "\u{2014}");
        let rule = svg.split("<rect").find(|r| r.contains("crispEdges")).unwrap();
        let header_rule_y: f64 = rule.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        assert!(no_data > header_rule_y, "overlaps the header:\n{svg}");
        assert!(no_data < canvas_h, "outside the canvas:\n{svg}");
    }

    /// `style.row_numbers`: a leading `#` column of 1-based indices, blank on a
    /// summary row, that takes its width off the data columns.
    #[test]
    fn row_numbers_lead_every_data_row_and_skip_summary_rows() {
        let mut payload = payload_with(json!({}));
        payload.resolved["style"]["table"]["row_numbers"] =
            json!({"visible": true, "header": "#", "align": "right", "font": {"color": "#626366"}});
        payload.resolved["style"]["table"]["row"]["role"] = json!("kind");
        payload.resolved["style"]["table"]["pagination"]["enabled"] = json!(false);
        payload.rows = vec![
            json!({"a": "x", "kind": "value"}),
            json!({"a": "y", "kind": "value"}),
            json!({"a": "Total", "kind": "total"}),
        ]
        .iter()
        .map(|r| r.as_object().unwrap().clone())
        .collect();
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains(">#</text>"), "{svg}");
        assert!(svg.contains("data-col=\"0\">1</text>") && svg.contains("data-col=\"0\">2</text>"), "{svg}");
        assert!(!svg.contains("data-col=\"0\">3</text>"), "the total row has no index:\n{svg}");
        assert!(svg.contains("fill=\"#626366\""), "indices take their slot's ink:\n{svg}");
    }

    /// A row-number or empty-state slot with no color is refused by name; no
    /// board-level ink stands in.
    #[test]
    fn a_secondary_text_slot_without_a_color_is_refused_by_name() {
        let mut payload = payload_with(json!({}));
        payload.resolved["style"]["table"]["row_numbers"] = json!({"visible": true, "font": {}});
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("style.table.row_numbers.font.color"), "{err}");

        let mut payload = payload_with(json!({}));
        payload.rows = vec![];
        payload.resolved["style"]["table"]["empty_state"] = json!({"font": {"size": 14.0}});
        let err = crate::families::empty::render("table", &payload, &test_fonts()).unwrap_err().to_string();
        assert!(err.contains("style.table.empty_state.font.color"), "{err}");
    }

    /// The title and subtitle text runs name which authored key they are
    /// (`data-authored-kind`), the inline-edit hook.
    #[test]
    fn the_title_and_subtitle_carry_their_authored_kind() {
        let mut payload = payload_with(json!({}));
        payload.resolved["title"] = json!("T");
        payload.resolved["subtitle"] = json!("S");
        payload.resolved["title_style"] = json!({"font": {"family": "serif", "size": 18.0, "color": "#494A4C", "weight": "500"}, "subtitle": {"font": {"family": "serif", "size": 11.0, "color": "#626366"}}});
        payload.resolved["style"]["title_font"] = json!({"size": 18.0, "family": "serif", "weight": "500"});
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("data-authored-kind=\"title\">"), "{svg}");
        assert!(svg.contains("data-authored-kind=\"subtitle\">"), "{svg}");
    }

    /// A column's `header_link` wraps its header in an anchor; every href
    /// sink refuses an unsafe scheme.
    #[test]
    fn a_header_link_anchors_the_header_and_refuses_an_unsafe_scheme() {
        let with_link = |href: &str| {
            let mut payload = payload_with(json!({}));
            payload.resolved["columns"]["a"]["header_link"] = json!(href);
            render(&payload, &test_fonts())
        };
        let svg = with_link("/inspect/?a=1&b=2").unwrap();
        assert!(svg.contains("<a href=\"/inspect/?a=1&amp;b=2\"><text"), "{svg}");
        assert!(svg.contains(">A</text></a>"), "{svg}");
        let err = with_link("javascript:alert(1)").unwrap_err();
        assert_eq!(err.code, "ERR-LINK-SCHEME-UNSAFE-AT-RENDER");
    }

    /// `style.table.transpose`: the one row becomes `(label, value)` rows under
    /// "Metric" and "Value" headers; any other row count refuses.
    #[test]
    fn a_transposed_table_draws_one_metric_row_per_column() {
        let mut payload = payload_with(json!({}));
        payload.height = 400.0;
        payload.resolved["style"]["table"]["transpose"] = json!(true);
        payload.resolved["columns"] = json!({"col_a": {"label": "Alpha", "link_open": "page", "header_link_open": "page"}, "col_b": {"link_open": "page", "header_link_open": "page"}});
        payload.rows = vec![json!({"col_a": 10, "col_b": 20}).as_object().unwrap().clone()];
        let svg = render(&payload, &test_fonts()).unwrap();
        for text in [">Metric</text>", ">Value</text>", ">Alpha</text>", ">Col b</text>"] {
            assert!(svg.contains(text), "{text} missing:\n{svg}");
        }
        payload.rows.push(payload.rows[0].clone());
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-TABLE-TRANSPOSE-ROW-COUNT");
    }

    /// A sentinel string among magnitudes stays text beside a numeric lane that
    /// still compacts its numbers (`_is_numeric_cell` is a per-cell verdict).
    #[test]
    fn a_text_sentinel_among_numbers_does_not_demote_the_column() {
        let mut payload = payload_with(json!({}));
        payload.height = 400.0;
        payload.resolved["columns"] = json!({"a": {"label": "A", "link_open": "page", "header_link_open": "page"}});
        payload.rows = vec![
            json!({"a": 10}).as_object().unwrap().clone(),
            json!({"a": "N/A"}).as_object().unwrap().clone(),
            json!({"a": 2500}).as_object().unwrap().clone(),
        ];
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains(">2.5</tspan><tspan x=") && svg.contains(">K</tspan>"), "{svg}");
        assert!(svg.contains("data-col=\"0\">N/A</text>"), "{svg}");
    }

    /// A static `color` replaces the body ink; an authored color that is not a
    /// hex color, `transparent` or `none` is an error, never painted.
    #[test]
    fn a_static_table_color_inks_the_body_and_an_invalid_color_errors() {
        let mut payload = payload_with(json!({}));
        payload.resolved["style"]["table"]["color"] = json!({"static": "#dd00dd"});
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("fill=\"#dd00dd\""), "{svg}");
        payload.resolved["style"]["table"]["header"]["background"] = json!("not-a-color");
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-CSS-COLOR-INVALID-AT-RENDER");
    }

    /// A linked cell takes the board's text color, not the table's.
    #[test]
    fn a_linked_cell_takes_the_board_font_color() {
        let mut payload = payload_with(json!({}));
        payload.resolved["columns"]["a"]["link"] = json!("/x/{{ a }}");
        payload.board_style = crate::test_board_style(json!({"font_color": "#123456"}));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("style=\"color: #123456\""), "{svg}");
    }

    /// A baked decimal pad table aligns decimals under the format's own digits,
    /// thousands grouping included.
    #[test]
    fn a_decimal_pad_table_pads_the_formatted_number_not_a_bare_trim() {
        let mut payload = payload_with(json!({}));
        payload.height = 400.0;
        payload.resolved["columns"] = json!({"a": {
            "link_open": "page", "header_link_open": "page",
            "format": {"spec": ",.1~f"},
            "decimal_pad_table": ["", "\u{2007}\u{200b}", "\u{2007}\u{200b}\u{2008}\u{200b}"],
        }});
        payload.rows = vec![
            json!({"a": 1000.0}).as_object().unwrap().clone(),
            json!({"a": 0.5}).as_object().unwrap().clone(),
        ];
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("1,000\u{2007}\u{200b}\u{2008}\u{200b}</tspan>"), "{svg}");
    }

    /// A cell link names a board path: the host's link context rewrites it
    /// (`resolve_cell_link_with_board`), query-only filter links excepted.
    #[test]
    fn a_cell_link_is_rewritten_through_the_host_link_context() {
        let mut payload = payload_with(json!({}));
        payload.resolved["columns"]["a"]["link"] = json!("/accounts/{{ a }}");
        payload.link_context = Some(
            serde_json::from_value(json!({"root": "/org/proj/d", "current_board_slug": "a/b", "origin": ""})).unwrap(),
        );
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"/org/proj/d/accounts/x\">"), "{svg}");
    }

    /// A `transparent` background paints no rect.
    #[test]
    fn a_literal_transparent_table_background_paints_no_rect() {
        let payload = payload_with(json!({"background": "transparent"}));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("rx=\"4\""), "no background rect expected:\n{svg}");
    }

    /// SVG's `font-family` presentation attribute treats a quote character as LITERAL,
    /// not a CSS string delimiter — a multi-word name baked quoted for CSS use (`'Inter Variable'`)
    /// fails to resolve as a real font once painted verbatim and silently falls back, which is
    /// pixel-visible on every glyph. The painter strips the quotes.
    #[test]
    fn a_quoted_multi_word_font_family_is_unquoted_for_painting() {
        let payload =
            payload_with(json!({"font": {"family": "'Inter Variable', 'Noto Emoji', Inter"}}));
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(
            svg.contains("font-family=\"Inter Variable, Noto Emoji, Inter\""),
            "quotes must be stripped from the painted font-family:\n{svg}"
        );
        assert!(!svg.contains('\''), "no quote character should reach the SVG at all:\n{svg}");
    }
}

#[cfg(test)]
mod decimal_pad_for_tests {
    use super::bind::decimal_pad_for;

    fn sample_pad_table() -> Vec<String> {
        // precision = 4 - 2 = 2: distinct sentinels per index so a wrong
        // selection reads as a wrong string, not a coincidentally-empty one.
        vec!["IDX0".into(), "IDX1".into(), "IDX2".into(), "IDX3".into()]
    }

    #[test]
    fn whole_number_selects_precision_plus_one() {
        assert_eq!(decimal_pad_for(&sample_pad_table(), "197").unwrap(), "IDX3");
    }

    #[test]
    fn partial_fraction_selects_precision_minus_frac() {
        // frac 1 of precision 2 -> index 2 - 1 = 1.
        assert_eq!(decimal_pad_for(&sample_pad_table(), "93.3").unwrap(), "IDX1");
        // frac == precision (2) -> index 2 - 2 = 0.
        assert_eq!(decimal_pad_for(&sample_pad_table(), "93.30").unwrap(), "IDX0");
    }

    #[test]
    fn over_deep_value_errs_named() {
        let err = decimal_pad_for(&sample_pad_table(), "93.301").unwrap_err();
        assert!(err.contains("decimal_pad_for"), "{err}");
        assert!(err.contains('2'), "names the table's precision: {err}");
        assert!(err.contains('3'), "names the value's fractional depth: {err}");
    }

    #[test]
    fn empty_table_is_no_pad() {
        assert_eq!(decimal_pad_for(&[], "93.3").unwrap(), "");
    }
}

#[cfg(test)]
mod header_font_tests {
    use super::*;
    use super::layout::column_inputs;
    use serde_json::json;

    fn fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        FontBook::load_dir(&format!("{manifest}/../../../src/dbt_charts/core/render/fonts"))
            .expect("test fonts load")
    }

    /// A header paints in its own family, so its demand and word floor are
    /// measured there: Inter cells under a Source Serif 4 header (the wider)
    /// size the column for the serif label.
    #[test]
    fn a_headers_demand_and_floor_are_measured_in_the_header_font() {
        let fonts = fonts();
        let col = ColMeta {
            name: "quarterly".to_string(),
            header: "quarterly".to_string(),
            cfg: ColConfig::default(),
            is_numeric: false,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows = vec![json!({"quarterly": "Al"}).as_object().unwrap().clone()];
        let cells = vec![vec![Cell { text: "Al".into(), ..Default::default() }]];
        let inputs = column_inputs(
            std::slice::from_ref(&col),
            &cells,
            &rows,
            &fonts,
            &MeasureFonts { body: "Inter", header: "Source Serif 4" },
            11.0,
            11.0,
            true,
            6.0,
        )
        .unwrap();
        let serif = fonts.measure("quarterly", "Source Serif 4", 11.0).unwrap();
        assert!(serif > fonts.measure("quarterly", "Inter", 11.0).unwrap());
        assert!((inputs.header_demands["quarterly"] - serif).abs() < 1e-9);
        assert!((inputs.word_floors["quarterly"] - (serif + 12.0)).abs() < 1e-9);
        let cell_demand = fonts.measure("Al", "Inter", 11.0).unwrap() + 12.0;
        assert!((inputs.demands["quarterly"] - cell_demand).abs() < 1e-9);
    }
}

#[cfg(test)]
mod padded_column_tests {
    use super::*;
    use super::bind::format_numeric_col;
    use serde_json::json;

    fn padded_col() -> ColMeta {
        ColMeta {
            name: "amt".to_string(),
            header: "Amt".to_string(),
            cfg: ColConfig {
                format: Some(FormatSlot::from(",.2~f")),
                decimal_pad_table: vec!["\u{2007}\u{2007}".into(), "\u{2007}".into(), String::new(), "\u{2007}\u{2007}\u{2007}".into()],
                ..ColConfig::default()
            },
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        }
    }

    fn cells(values: Vec<Value>) -> Vec<Cell> {
        let rows: Vec<_> =
            values.into_iter().map(|v| json!({"amt": v}).as_object().unwrap().clone()).collect();
        let roles = vec![RowRole::Value; rows.len()];
        format_numeric_col(&rows, &padded_col(), "all", &roles)
            .expect("formats")
    }

    /// A cell that is not a number owes nothing to the column's decimal lane:
    /// its fractional depth may exceed the pad table's, and it paints verbatim.
    #[test]
    fn an_unparseable_cell_in_a_padded_column_paints_verbatim_without_a_pad() {
        let cells = cells(vec![json!(1.5), json!("12.3456789012345678 kg")]);
        assert_eq!(cells[1].text, "12.3456789012345678 kg");
        assert_eq!(cells[1].pad, "");
    }

    /// A bool is not a magnitude: it prints as text, never padded.
    #[test]
    fn a_bool_cell_in_a_padded_column_is_not_padded() {
        let cells = cells(vec![json!(1.5), json!(true)]);
        assert_eq!(cells[1].text, "True");
        assert_eq!(cells[1].pad, "");
    }

    /// A number lands on the pad its own fractional depth selects.
    #[test]
    fn a_number_takes_the_pad_of_its_fractional_depth() {
        let cells = cells(vec![json!(1.5), json!(2)]);
        assert_eq!((cells[0].pad.as_str(), cells[1].pad.as_str()), ("\u{2007}", "\u{2007}\u{2007}\u{2007}"));
    }
}

#[cfg(test)]
mod pivot_shape_tests {
    use super::*;
    use super::bind::pivot_table_data;
    use super::bind::pivot_leaf_keys;
    use serde_json::json;

    fn rows(values: Vec<Value>) -> Vec<serde_json::Map<String, Value>> {
        values.into_iter().map(|v| v.as_object().unwrap().clone()).collect()
    }

    fn strings(names: &[&str]) -> Vec<String> {
        names.iter().map(|s| s.to_string()).collect()
    }

    fn leaves(pivot: &PivotResult) -> Vec<&str> {
        pivot.col_order[pivot.n_row_dims..].iter().map(String::as_str).collect()
    }

    fn quarters() -> Vec<serde_json::Map<String, Value>> {
        rows(vec![
            json!({"region": "US", "year": "2024", "quarter": "Q1", "revenue": 100}),
            json!({"region": "US", "year": "2024", "quarter": "Q2", "revenue": 200}),
            json!({"region": "EU", "year": "2024", "quarter": "Q1", "revenue": 90}),
            json!({"region": "US", "year": "2025", "quarter": "Q1", "revenue": 110}),
            json!({"region": "EU", "year": "2025", "quarter": "Q1", "revenue": 95}),
        ])
    }

    /// A leaf exists only for a column tuple the query observed, and the outer
    /// dimension's span counts exactly its own leaves.
    #[test]
    fn a_sparse_multi_dimension_pivot_has_no_phantom_columns() {
        let pivot = pivot_table_data(
            &quarters(),
            &[],
            &strings(&["region"]),
            &strings(&["year", "quarter"]),
            &strings(&["revenue"]),
            None,
        )
        .unwrap();
        assert_eq!(
            leaves(&pivot),
            [
                pivot_leaf_keys(&strings(&["2024", "Q1"]), &["revenue"]),
                pivot_leaf_keys(&strings(&["2024", "Q2"]), &["revenue"]),
                pivot_leaf_keys(&strings(&["2025", "Q1"]), &["revenue"]),
            ]
            .concat()
        );
        assert_eq!(
            pivot.levels,
            vec![vec![("2024".to_string(), 0, 2), ("2025".to_string(), 2, 1)]]
        );
        assert!(pivot.single_measure.is_some());
    }

    /// A cell lands under its own leaf and a combination the query never
    /// returned stays null.
    #[test]
    fn a_pivot_cell_lands_under_its_leaf_and_a_missing_one_is_null() {
        let pivot = pivot_table_data(
            &quarters(),
            &[],
            &strings(&["region"]),
            &strings(&["year", "quarter"]),
            &strings(&["revenue"]),
            None,
        )
        .unwrap();
        let eu = pivot.rows.iter().find(|r| r["region"] == json!("EU")).unwrap();
        let leaf = |year: &str, quarter: &str| {
            pivot_leaf_keys(&strings(&[year, quarter]), &["revenue"]).remove(0)
        };
        assert_eq!(eu[&leaf("2024", "Q1")], json!(90));
        assert_eq!(eu[&leaf("2025", "Q1")], json!(95));
        assert!(eu.get(&leaf("2024", "Q2")).is_none_or(Value::is_null));
        assert_eq!(pivot.rows.len(), 2);
    }

    /// Several measures fan each column tuple into one leaf per measure, under
    /// one group level per column dimension.
    #[test]
    fn several_measures_fan_each_column_into_a_leaf_per_measure() {
        let data = rows(vec![
            json!({"region": "US", "quarter": "Q1", "revenue": 100, "units": 4}),
            json!({"region": "US", "quarter": "Q2", "revenue": 200, "units": 5}),
        ]);
        let pivot = pivot_table_data(
            &data,
            &[],
            &strings(&["region"]),
            &strings(&["quarter"]),
            &strings(&["revenue", "units"]),
            None,
        )
        .unwrap();
        assert_eq!(leaves(&pivot).len(), 4);
        assert_eq!(
            pivot.levels,
            vec![vec![("Q1".to_string(), 0, 2), ("Q2".to_string(), 2, 2)]]
        );
        assert!(pivot.single_measure.is_none());
    }

    /// A total row's tidy rows share their row-dimension tuple, so they merge
    /// into one wide row at their first appearance: the renderer never moves
    /// totals — an `ORDER BY` places them.
    #[test]
    fn total_rows_merge_into_one_row_in_query_order() {
        let data = rows(vec![
            json!({"region": "US", "quarter": "Q1", "revenue": 100, "role": "value"}),
            json!({"region": "US", "quarter": "Q2", "revenue": 200, "role": "value"}),
            json!({"region": "Total", "quarter": "Q1", "revenue": 190, "role": "total"}),
            json!({"region": "EU", "quarter": "Q1", "revenue": 90, "role": "value"}),
            json!({"region": "EU", "quarter": "Q2", "revenue": 180, "role": "value"}),
            json!({"region": "Total", "quarter": "Q2", "revenue": 380, "role": "total"}),
        ]);
        let pivot = pivot_table_data(
            &data,
            &[],
            &strings(&["region"]),
            &strings(&["quarter"]),
            &strings(&["revenue"]),
            Some("role"),
        )
        .unwrap();
        let regions: Vec<_> = pivot.rows.iter().map(|r| r["region"].clone()).collect();
        assert_eq!(regions, [json!("US"), json!("Total"), json!("EU")]);
        let total = &pivot.rows[1];
        assert_eq!((&total["Q1"], &total["Q2"]), (&json!(190), &json!(380)));
        assert_eq!(total["role"], json!("total"));
        assert_eq!(pivot.rows[0]["role"], json!("value"));
    }

    fn leaf_columns(dims: &[&str], rows: Vec<Value>) -> Vec<String> {
        let dims = strings(dims);
        let pivot = pivot_table_data(&rows_of(rows), &[], &strings(&["r"]), &dims, &strings(&["v"]), None).unwrap();
        leaves(&pivot).into_iter().map(|leaf| leaf.replace(PIVOT_LEAF_SEP, "/")).collect()
    }

    fn rows_of(values: Vec<Value>) -> Vec<serde_json::Map<String, Value>> {
        rows(values)
    }

    /// A tagged pivot dimension value keeps its `str()` text: a Decimal labels
    /// its column with it, a non-finite value is the text itself, and the
    /// unsortable mix keeps first-seen order.
    #[test]
    fn a_tagged_pivot_dimension_is_labeled_with_its_str_in_first_seen_order() {
        let mut raw = rows_of(vec![
            json!({"r": "x", "c": {"decimal": "2"}, "v": 1}),
            json!({"r": "x", "c": {"non_finite": "nan", "text": "NaN"}, "v": 1}),
            json!({"r": "x", "c": {"decimal": "1"}, "v": 1}),
        ]);
        let cell_text = crate::common::decode_cells(&mut raw).unwrap();
        let dims = strings(&["c"]);
        let pivot = pivot_table_data(&raw, &cell_text, &strings(&["r"]), &dims, &strings(&["v"]), None).unwrap();
        assert_eq!(leaves(&pivot), ["2", "NaN", "1"]);
    }

    /// A Decimal dimension value labels its column with the Decimal's own text
    /// and orders numerically.
    #[test]
    fn decimal_pivot_dimension_values_label_by_their_text_and_sort_numerically() {
        let mut raw = rows_of(vec![
            json!({"r": "x", "c": {"decimal": "2024"}, "v": 1}),
            json!({"r": "x", "c": {"decimal": "2023"}, "v": 2}),
            json!({"r": "y", "c": {"decimal": "2.50"}, "v": 3}),
        ]);
        let cell_text = crate::common::decode_cells(&mut raw).unwrap();
        let dims = strings(&["c"]);
        let pivot = pivot_table_data(&raw, &cell_text, &strings(&["r"]), &dims, &strings(&["v"]), None).unwrap();
        assert_eq!(leaves(&pivot), ["2.50", "2023", "2024"]);
    }

    /// A temporal dimension orders chronologically with its null last.
    #[test]
    fn a_temporal_pivot_dimension_sorts_chronologically_with_null_last() {
        let got = leaf_columns(
            &["m"],
            vec![
                json!({"r": "x", "m": "2024-03-01", "v": 1}),
                json!({"r": "x", "m": null, "v": 2}),
                json!({"r": "x", "m": "2024-01-01", "v": 3}),
            ],
        );
        assert_eq!(got, ["2024-01-01", "2024-03-01", "None"]);
    }

    /// A sorted outer dimension orders its groups; the unsorted inner one keeps
    /// first-seen order inside each.
    #[test]
    fn a_sorted_outer_dimension_keeps_the_inner_first_seen() {
        let got = leaf_columns(
            &["year", "team"],
            vec![
                json!({"r": "x", "year": 2025, "team": "zeta", "v": 1}),
                json!({"r": "x", "year": 2024, "team": "mu", "v": 2}),
                json!({"r": "x", "year": 2025, "team": "alpha", "v": 3}),
                json!({"r": "x", "year": 2024, "team": "beta", "v": 4}),
            ],
        );
        assert_eq!(got, ["2024/mu", "2024/beta", "2025/zeta", "2025/alpha"]);
    }

    /// An outer dimension that cannot be sorted keeps first-seen order with its
    /// groups contiguous.
    #[test]
    fn an_unsortable_outer_dimension_keeps_its_groups_contiguous_in_first_seen_order() {
        let got = leaf_columns(
            &["region", "year"],
            vec![
                json!({"r": "x", "region": "west", "year": 2025, "v": 1}),
                json!({"r": "x", "region": "east", "year": 2024, "v": 2}),
                json!({"r": "x", "region": "west", "year": 2024, "v": 3}),
                json!({"r": "x", "region": "east", "year": 2025, "v": 4}),
            ],
        );
        assert_eq!(got, ["west/2024", "west/2025", "east/2024", "east/2025"]);
    }

    /// Booleans are not orderable: first-seen order.
    #[test]
    fn a_boolean_pivot_dimension_keeps_first_seen_order() {
        let got = leaf_columns(
            &["flag"],
            vec![
                json!({"r": "x", "flag": true, "v": 1}),
                json!({"r": "x", "flag": false, "v": 2}),
            ],
        );
        assert_eq!(got, ["True", "False"]);
    }

    /// Several measures keep their group levels in the sorted dimension's order.
    #[test]
    fn multi_measure_group_levels_follow_the_sorted_order() {
        let data = rows(vec![
            json!({"r": "x", "year": 2025, "a": 1, "b": 2}),
            json!({"r": "x", "year": 2024, "a": 3, "b": 4}),
        ]);
        let pivot = pivot_table_data(
            &data,
            &[],
            &strings(&["r"]),
            &strings(&["year"]),
            &strings(&["a", "b"]),
            None,
        )
        .unwrap();
        assert_eq!(
            pivot.levels,
            vec![vec![("2024".to_string(), 0, 2), ("2025".to_string(), 2, 2)]]
        );
    }

    /// Distinct total labels stay distinct rows.
    #[test]
    fn distinct_total_labels_stay_separate_rows() {
        let data = rows(vec![
            json!({"region": "US", "quarter": "Q1", "revenue": 1, "role": "value"}),
            json!({"region": "US Total", "quarter": "Q1", "revenue": 1, "role": "total"}),
            json!({"region": "EU", "quarter": "Q1", "revenue": 2, "role": "value"}),
            json!({"region": "EU Total", "quarter": "Q1", "revenue": 2, "role": "total"}),
        ]);
        let pivot = pivot_table_data(
            &data,
            &[],
            &strings(&["region"]),
            &strings(&["quarter"]),
            &strings(&["revenue"]),
            Some("role"),
        )
        .unwrap();
        let regions: Vec<_> = pivot.rows.iter().map(|r| r["region"].clone()).collect();
        assert_eq!(
            regions,
            [json!("US"), json!("US Total"), json!("EU"), json!("EU Total")]
        );
    }
}


#[cfg(test)]
mod board_format_tests {
    use super::*;
    use super::bind::format_numeric_col;
    use serde_json::json;

    fn fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        FontBook::load_dir(&format!("{manifest}/../../../src/dbt_charts/core/render/fonts"))
            .expect("test fonts load")
    }

    fn board_payload(rows: Vec<Value>, columns: Value, board_style: Value) -> Payload {
        let resolved = json!({
            "link_open": "page",
            "title": null, "title_style": super::tests::resolved_title_style(), "columns": columns,
            "style": {"table": super::tests::base_table_theme(), "title_font": super::tests::resolved_title_font()},
            "pivot_columns": null, "conditional_formatting": null,
        });
        let payload = json!({
            "resolved": resolved, "rows": rows, "width": 600.0, "height": 0.0,
            "background": "#ffffff", "fonts_dir": null, "padding": null,
            "board_style": board_style,
        });
        crate::test_payload(payload)
    }

    /// A board that redefines `date_short` paints every default date through it.
    #[test]
    fn a_board_redefining_date_short_reaches_a_date_cell() {
        let rows = vec![json!({"d": "2024-03-05"})];
        let style = json!({"formats": {"date_short": "%Y/%m/%d"}});
        let svg = render(&board_payload(rows, json!({"d": {}}), style), &fonts()).unwrap();
        assert!(svg.contains("2024/03/05"), "{svg}");
    }

    /// The same board redefining `number` reaches a column with no format, and
    /// its object form carries a suffix.
    #[test]
    fn a_board_redefining_number_reaches_an_unformatted_numeric_column() {
        let rows = vec![json!({"v": 1234567})];
        let style = json!({"formats": {"number": {"spec": ",.0f", "suffix": " widgets"}}});
        let svg = render(&board_payload(rows, json!({"v": {}}), style), &fonts()).unwrap();
        assert!(svg.contains(">1,234,567<") && svg.contains(" widgets"), "{svg}");
    }

    /// An alias the engine cannot read is an error naming it, never a silent
    /// fall back to the default format.
    #[test]
    fn an_unreadable_board_alias_is_refused_by_name() {
        let rows = vec![json!({"v": 5})];
        let style = json!({"formats": {"number": {"spec": 5}}});
        let err = render(&board_payload(rows, json!({"v": {}}), style), &fonts())
            .unwrap_err()
            .to_string();
        assert!(err.contains("board format \"number\""), "{err}");
    }

    /// A board alias carries its notation to the unformatted columns that print
    /// through it.
    #[test]
    fn a_board_alias_notation_reaches_an_unformatted_numeric_column() {
        let rows = vec![json!({"v": 1234567})];
        let alias = json!({"spec": ".3~s", "notation": "narrative"});
        let style = json!({"formats": {"number": alias}});
        let svg = render(&board_payload(rows, json!({"v": {}}), style), &fonts()).unwrap();
        assert!(svg.contains(">mn</tspan>"), "{svg}");
    }

    /// A sub-one value prints the board's own plain-digit sibling, not a
    /// hard-coded two decimals.
    #[test]
    fn a_sub_unit_value_prints_the_boards_sibling_spec() {
        let rows = vec![json!({"v": 0.6712})];
        let style = json!({"sub_unit_specs": {"currency": "$,.3f", "number": ",.3f"}});
        let svg = render(&board_payload(rows.clone(), json!({"v": {}}), style), &fonts()).unwrap();
        assert!(svg.contains(">0.671<"), "{svg}");
        let columns = json!({"v": {"format": {"spec": "$.3~s", "raw": "currency"}}});
        let style = json!({"sub_unit_specs": {"currency": "$,.1f", "number": ",.3f"}});
        let svg = render(&board_payload(rows, columns, style), &fonts()).unwrap();
        assert!(svg.contains(">0.7<"), "{svg}");
    }

    /// A suffix with digits in it never changes which decimal pad a number takes,
    /// and does not fail the pad lookup.
    #[test]
    fn a_digit_bearing_suffix_does_not_change_the_selected_pad() {
        let pad = |suffix: &str| {
            let col = ColMeta {
                name: "v".to_string(),
                header: "V".into(),
                cfg: ColConfig {
                    format: Some(FormatSlot { suffix: suffix.into(), ..FormatSlot::from(",.2~f") }),
                    decimal_pad_table: vec!["".into(), "\u{2007}".into(), "\u{2007}\u{2007}".into(), "\u{2007}\u{2007}\u{2007}".into()],
                    ..ColConfig::default()
                },
                is_numeric: true,
                is_date: false,
                when_rules: Vec::new(),
            };
            let rows = vec![json!({"v": 1.5}).as_object().unwrap().clone()];
            format_numeric_col(&rows, &col, "all", &[RowRole::Value])
                .expect("a digit-bearing suffix formats")[0]
                .pad
                .clone()
        };
        assert_eq!(pad(" m2"), pad(" d"));
        assert_eq!(pad(" 2024.75 kg"), pad(" d"));
    }
}

#[cfg(test)]
mod magnitude_suffix_tests {
    use super::bind::is_magnitude_suffix;

    // Oracle: `format_value(2540, "number")` under en-US is '2.54K' (the
    // analytic words are glued, `text/magnitude_words.py:40`); the lane is 'K'.
    #[test]
    fn en_us_analytic_word_is_printed_glued() {
        let en = crate::number_locale::en_us_with_words(["K", "M", "B", "T"], ["k", "mn", "bn", "trn"]);
        crate::number_locale::with_number_locale(&en, || {
            let lanes = crate::common::format_numeric_auto(2540.0).unwrap();
            assert_eq!((lanes.number.as_str(), lanes.suffix.as_str(), lanes.measured_suffix.as_str()), ("2.54", "K", "K"));
        });
    }

    // Oracle: `magnitude_suffixes()` under en-US is d3's letters and
    // `K M B T`, `k mn bn trn`, bare; `m` (milli) and `%` are not in it.
    #[test]
    fn en_us_words_gate_the_gap() {
        for word in ["k", "M", "K", "B", "T", "mn", "bn", "trn", "P", " K"] {
            assert!(is_magnitude_suffix(word), "{word:?}");
        }
        for word in ["m", "%", "Mrd", "百万", ""] {
            assert!(!is_magnitude_suffix(word), "{word:?}");
        }
    }

    // Oracle: de-DE analytic words are `k M Mrd Bio`, ja-JP's `千 百万 十億 兆`, each behind
    // their own gap; the table lane trims it and the 3px gap follows the word.
    #[test]
    fn the_active_locales_words_gate_the_gap() {
        let de = crate::number_locale::en_us_with_words(["\u{a0}k", "\u{a0}M", "\u{a0}Mrd", "\u{a0}Bio"], ["\u{a0}Tsd.", "\u{a0}Mio.", "\u{a0}Mrd.", "\u{a0}Bio."]);
        crate::number_locale::with_number_locale(&de, || {
            for word in ["Mrd", "\u{a0}Mrd", "Tsd.", "Bio", "Mio."] {
                assert!(is_magnitude_suffix(word), "{word:?}");
            }
            assert!(!is_magnitude_suffix("bn"), "an English narrative word is not de-DE's");
            let lanes = crate::common::format_numeric_auto(1.5e9).unwrap();
            assert_eq!((lanes.number.as_str(), lanes.suffix.as_str(), lanes.measured_suffix.as_str()), ("1.5", "Mrd", "\u{a0}Mrd"));
        });
        let ja = crate::number_locale::en_us_with_words(["千", "百万", "十億", "兆"], ["千", "百万", "十億", "兆"]);
        crate::number_locale::with_number_locale(&ja, || {
            assert!(is_magnitude_suffix("百万"));
            let lanes = crate::common::format_numeric_auto(2.5e6).unwrap();
            assert_eq!((lanes.number.as_str(), lanes.suffix.as_str(), lanes.measured_suffix.as_str()), ("2.5", "百万", "百万"));
            assert!(!is_magnitude_suffix("mn"));
        });
    }
}

#[cfg(test)]
mod shared_scale_register_tests {
    use super::*;
    use super::bind::unpadded_cell_text;
    use super::bind::measured_cell_text;
    use super::bind::format_numeric_col;
    use super::bind::decimal_pad_for;
    use serde_json::json;

    fn suffixes_of(
        mode: &str,
        exponent: i32,
        symbol_mode: &str,
        values: &[f64],
    ) -> Result<Vec<Option<String>>, String> {
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({
            "shared_scale": {"exponent": exponent, "mode": mode, "digit_spec": ",.2~f"},
        }))
        .map_err(|e| e.to_string())?;
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows: Vec<serde_json::Map<String, Value>> = values
            .iter()
            .map(|v| json!({"amount": v}).as_object().unwrap().clone())
            .collect();
        let roles = vec![RowRole::Value; values.len()];
        let cells = format_numeric_col(&rows, &col, symbol_mode, &roles).map_err(|e| e.to_string())?;
        Ok(cells.into_iter().map(|c| c.suffix).collect())
    }

    fn suffix_of(mode: &str, exponent: i32, value: f64) -> Result<Option<String>, String> {
        Ok(suffixes_of(mode, exponent, "all", &[value])?.remove(0))
    }

    // Production gates the word on `value != 0 and (mode is REPEAT or
    // is_anchor)` (format_value.py:308); table.py:2657-2663 keeps a magnitude
    // token on every row even under symbol_mode "anchors".
    #[test]
    fn a_repeat_scale_paints_its_word_on_every_nonzero_row_under_anchors() {
        let got = suffixes_of("repeat", 6, "anchors", &[24.7e6, 8.4e6, 3.1e6]).unwrap();
        assert_eq!(got, vec![Some("mn".to_string()); 3]);
    }

    #[test]
    fn an_anchor_scale_paints_its_word_on_the_anchor_row_alone_under_anchors() {
        let got = suffixes_of("anchor", 6, "anchors", &[24.7e6, 8.4e6, 3.1e6]).unwrap();
        assert_eq!(got, vec![Some("M".to_string()), None, None]);
    }

    #[test]
    fn a_zero_row_carries_no_word_in_either_mode() {
        for mode in ["anchor", "repeat"] {
            let got = suffixes_of(mode, 6, "all", &[24.7e6, 0.0]).unwrap();
            assert!(got[0].is_some(), "{mode}");
            assert_eq!(got[1], None, "{mode}");
        }
    }

    // Oracles: production `format_kpi_parts(value, "number", shared_scale=...,
    // is_anchor=True)` suffix lane for each (mode, exponent).
    #[test]
    fn a_repeat_scale_speaks_the_narrative_register() {
        assert_eq!(suffix_of("repeat", 3, 12_340.0).unwrap().as_deref(), Some("k"));
        assert_eq!(suffix_of("repeat", 6, 24_730_000.0).unwrap().as_deref(), Some("mn"));
        assert_eq!(suffix_of("repeat", 9, 3.2e9).unwrap().as_deref(), Some("bn"));
        assert_eq!(suffix_of("repeat", 12, 2.5e12).unwrap().as_deref(), Some("trn"));
    }

    #[test]
    fn an_anchor_scale_speaks_the_analytic_register_through_the_trillion_tier() {
        assert_eq!(suffix_of("anchor", 3, 12_340.0).unwrap().as_deref(), Some("K"));
        assert_eq!(suffix_of("anchor", 6, 24_730_000.0).unwrap().as_deref(), Some("M"));
        assert_eq!(suffix_of("anchor", 12, 2.5e12).unwrap().as_deref(), Some("T"));
    }

    fn locale_with(edit: impl FnOnce(&mut Value)) -> crate::number_locale::NumberLocale {
        let mut wire = crate::number_locale::en_us_wire();
        edit(&mut wire);
        serde_json::from_value(wire).expect("locale parses")
    }

    // Oracle: `suffix_at_register(exponent, register)` under
    // `Regional(locale="ja-JP").format_locale`, whose words are `千 百万 十億 兆` in
    // both registers.
    #[test]
    fn the_words_are_the_active_locales() {
        let ja = locale_with(|w| {
            w["analytic"] = json!(["千", "百万", "十億", "兆"]);
            w["narrative"] = json!(["千", "百万", "十億", "兆"]);
        });
        crate::number_locale::with_number_locale(&ja, || {
            assert_eq!(suffix_of("anchor", 6, 24_730_000.0).unwrap().as_deref(), Some("百万"));
            assert_eq!(suffix_of("repeat", 9, 3.2e9).unwrap().as_deref(), Some("十億"));
        });
    }

    type Lanes = (Option<String>, String, Option<String>);

    fn lanes_of(format: Value, symbol_mode: &str, values: &[f64]) -> Vec<Lanes> {
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({"format": format})).expect("column config parses");
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows: Vec<serde_json::Map<String, Value>> = values
            .iter()
            .map(|v| json!({"amount": v}).as_object().unwrap().clone())
            .collect();
        let roles = vec![RowRole::Value; values.len()];
        format_numeric_col(&rows, &col, symbol_mode, &roles)
            .expect("formats")
            .into_iter()
            .map(|c| (c.prefix, c.text, c.suffix))
            .collect()
    }

    fn lane(prefix: Option<&str>, number: &str, suffix: Option<&str>) -> Lanes {
        (prefix.map(str::to_string), number.to_string(), suffix.map(str::to_string))
    }

    // Oracles: production's `format_kpi_parts(v, "currency_whole",
    // default_number=True, keep_affix_gaps=True[, anchored=True])` under
    // `Regional(locale="de-DE").format_locale` (`format_value.py:284-288`):
    // ('', '1.234', '\xa0€'), anchored ('€', '1.234', ''), -1234 ('', '−1.234',
    // '\xa0€'). The suffix keeps the locale's gap; the anchors lane leads.
    #[test]
    fn a_dollar_column_takes_the_locales_currency_after_the_digits() {
        let de = crate::number_locale::de_de();
        crate::number_locale::with_number_locale(&de, || {
            assert_eq!(
                lanes_of(json!("currency_whole"), "all", &[1234.0, -1234.0]),
                [lane(None, "1.234", Some("\u{a0}€")), lane(None, "\u{2212}1.234", Some("\u{a0}€"))]
            );
            assert_eq!(
                lanes_of(json!("currency_whole"), "anchors", &[1234.0, 99.0]),
                [lane(Some("€"), "1.234", None), lane(None, "99", None)]
            );
        });
    }

    // Oracle: `format_kpi_parts(0.5, "percent_whole", ...)` under fr-FR is
    // ('', '50', '\u202f%'), and `(0.4231, "percent")` under de-DE ('', '42,3',
    // '%'): the percent sign is the locale's, gap included, in the suffix lane.
    #[test]
    fn a_percent_column_takes_the_locales_percent_sign() {
        crate::number_locale::with_number_locale(&crate::number_locale::fr_fr(), || {
            assert_eq!(lanes_of(json!("percent_whole"), "all", &[0.5]), [lane(None, "50", Some("\u{202f}%"))]);
        });
        crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            assert_eq!(lanes_of(json!("percent"), "all", &[0.4231]), [lane(None, "42,3", Some("%"))]);
        });
    }

    // Oracle: production's `format_kpi_parts(27.6, "percent", default_number=True,
    // keep_affix_gaps=True)` under fr-FR raises `ERR-PERCENT-RANGE`
    // (format_spec '.1%', value 27.6), as does 10.0 under `percent_whole`;
    // `percent_delta` (`+.1%`), `percent_number` and 9.99 under `percent` paint.
    // The refusal quotes the cell as `repr` of the type the table kept it: an
    // integer cell `27`, a float `27.5`, a string cell `float()`'s `27.0`.
    #[test]
    fn a_percent_column_refuses_a_value_shaped_0_to_100() {
        crate::number_locale::with_number_locale(&crate::number_locale::fr_fr(), || {
            let refusal = |format: &str, value: Value| {
                let cfg: ColConfig = crate::families::table::tests::col_config(json!({"format": format}))
                    .expect("column config parses");
                let col = ColMeta {
                    name: "amount".to_string(),
                    header: "Amount".to_string(),
                    cfg,
                    is_numeric: true,
                    is_date: false,
                    when_rules: Vec::new(),
                };
                let rows = vec![json!({"amount": value}).as_object().unwrap().clone()];
                format_numeric_col(&rows, &col, "all", &[RowRole::Value]).map(|_| ()).unwrap_err().to_string()
            };
            assert_eq!(refusal("percent", json!(27.6)), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=27.6"#);
            assert_eq!(refusal("percent_whole", json!(10.0)), r#"ERR-PERCENT-RANGE: format_spec=".0%", value=10.0"#);
            assert_eq!(refusal("percent", json!(27)), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=27"#);
            assert_eq!(refusal("percent", json!(-27)), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=-27"#);
            assert_eq!(refusal("percent", json!(27.5)), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=27.5"#);
            assert_eq!(refusal("percent", json!("27")), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=27.0"#);
            assert_eq!(lanes_of(json!("percent"), "all", &[9.99]), [lane(None, "999,0", Some("\u{202f}%"))]);
            assert_eq!(
                lanes_of(json!("percent_delta"), "all", &[27.6]),
                [lane(None, "+2760,0", Some("\u{202f}%"))]
            );
        });
    }

    // Oracle: `format_kpi_parts(4820000.0, "currency", ...)` under de-DE is
    // ('', '4,82', 'M\xa0€'), anchored ('€', '4,82', 'M'): the magnitude word
    // ends the digits, ahead of a currency the locale places after them.
    #[test]
    fn a_magnitude_word_precedes_the_suffix_currency() {
        crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            assert_eq!(
                lanes_of(json!("currency"), "all", &[4_820_000.0]),
                [lane(None, "4,82", Some("M\u{a0}€"))]
            );
            assert_eq!(
                lanes_of(json!("currency"), "anchors", &[4_820_000.0]),
                [lane(Some("€"), "4,82", Some("M"))]
            );
        });
    }

    // Oracle: production's `format_kpi_parts(24730000.0, "currency",
    // shared_scale=<anchor, 6, ",.2~f">, is_anchor=True, keep_affix_gaps=True)`
    // under de-DE is ('', '24,73', 'M\xa0€') as `_rendered_cell_text` reads it
    // for the demand, and with `anchored=True`, as `_has_overflow` reads it for
    // the fit cascade, ('€', '24,73', 'M').
    #[test]
    fn a_shared_scale_cell_is_measured_in_each_sites_own_currency_placement() {
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({
            "format": "currency",
            "shared_scale": {"exponent": 6, "mode": "anchor", "digit_spec": ",.2~f"},
        }))
        .expect("column config parses");
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows = vec![json!({"amount": 24_730_000.0}).as_object().unwrap().clone()];
        let cell = crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            format_numeric_col(&rows, &col, "anchors", &[RowRole::Value])
        })
        .expect("formats")
        .remove(0);
        assert_eq!((cell.prefix.as_deref(), cell.text.as_str(), cell.suffix.as_deref()), (Some("€"), "24,73", Some("M")));
        assert_eq!((cell.full_prefix.as_deref(), cell.full_suffix.as_deref()), (None, Some("M\u{a0}€")));
        assert_eq!(cell.fit_affixes, Some(("€".to_string(), "M".to_string())));
    }

    fn authored_affix_cells(
        affix: Value,
        mode: &str,
        symbol_mode: &str,
        values: &[f64],
    ) -> Vec<Cell> {
        let mut format = json!({"spec": "$,.2~s"});
        format.as_object_mut().unwrap().extend(affix.as_object().unwrap().clone());
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({
            "format": format,
            "shared_scale": {"exponent": 6, "mode": mode, "digit_spec": ",.2~f"},
        }))
        .expect("column config parses");
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows: Vec<serde_json::Map<String, Value>> = values
            .iter()
            .map(|v| json!({"amount": v}).as_object().unwrap().clone())
            .collect();
        let roles = vec![RowRole::Value; values.len()];
        format_numeric_col(&rows, &col, symbol_mode, &roles).expect("formats")
    }

    fn painted(cell: &Cell) -> (Option<&str>, &str, Option<&str>) {
        (cell.prefix.as_deref(), cell.text.as_str(), cell.suffix.as_deref())
    }

    fn measured(cell: &Cell) -> (Option<&str>, Option<&str>) {
        (cell.full_prefix.as_deref(), cell.full_suffix.as_deref())
    }

    // Oracle: production's `format_kpi_parts(24730000, {spec: "$,.2~s", prefix:
    // "US "}, shared_scale=<anchor, 6, ",.2~f">, is_anchor=True,
    // keep_affix_gaps=True)` is ('US $', '24.73', 'M'): the shared-scale branch
    // falls through to the common tail (`format_value.py:379-390`), where the
    // authored prefix leads the `$`. A non-anchor row under `anchors` (second
    // row, 8.4e6) is ('US $', '8.4', '') before `table.py:2657-2663` blanks the
    // prefix; the demand reads every row as an anchor ('US $', '8.4', 'M').
    #[test]
    fn a_shared_scale_column_paints_an_authored_prefix_ahead_of_the_currency() {
        let cells = authored_affix_cells(json!({"prefix": "US "}), "anchor", "all", &[24_730_000.0]);
        assert_eq!(painted(&cells[0]), (Some("US $"), "24.73", Some("M")));
        assert_eq!(measured(&cells[0]), (Some("US $"), Some("M")));

        let cells = authored_affix_cells(json!({"prefix": "US "}), "anchor", "anchors", &[24_730_000.0, 8_400_000.0]);
        assert_eq!(painted(&cells[0]), (Some("US $"), "24.73", Some("M")));
        assert_eq!(painted(&cells[1]), (None, "8.4", None));
        assert_eq!(measured(&cells[1]), (Some("US $"), Some("M")));
        assert_eq!(cells[1].fit_affixes, Some(("US $".to_string(), "M".to_string())));
    }

    // Oracle: production's `format_kpi_parts(24730000, {spec: "$,.2~s", suffix:
    // " gross"}, shared_scale=<anchor, 6, ",.2~f">, is_anchor=True,
    // keep_affix_gaps=True)` is ('$', '24.73', 'M gross'): the magnitude word
    // leads the authored suffix and its gap survives. Non-anchor, the word is
    // gone and the authored text stays in the lane ('$', '8.4', ' gross'), which
    // `table.py:2657-2663` then blanks under `anchors`; a `repeat` scale keeps
    // its word on every row ('$', '8.4', 'mn gross'), and that suffix survives
    // the same strip because it still opens with a magnitude word.
    #[test]
    fn a_shared_scale_column_paints_an_authored_suffix_behind_the_magnitude_word() {
        let cells = authored_affix_cells(json!({"suffix": " gross"}), "anchor", "all", &[24_730_000.0]);
        assert_eq!(painted(&cells[0]), (Some("$"), "24.73", Some("M gross")));
        assert_eq!(measured(&cells[0]), (Some("$"), Some("M gross")));

        let cells = authored_affix_cells(json!({"suffix": " gross"}), "anchor", "anchors", &[24_730_000.0, 8_400_000.0]);
        assert_eq!(painted(&cells[0]), (Some("$"), "24.73", Some("M gross")));
        assert_eq!(painted(&cells[1]), (None, "8.4", None));
        assert_eq!(measured(&cells[1]), (Some("$"), Some("M gross")));

        let cells = authored_affix_cells(json!({"suffix": " gross"}), "repeat", "anchors", &[24_730_000.0, 8_400_000.0]);
        assert_eq!(painted(&cells[0]), (Some("$"), "24.73", Some("mn gross")));
        assert_eq!(painted(&cells[1]), (None, "8.4", Some("mn gross")));
        assert_eq!(measured(&cells[1]), (Some("$"), Some("mn gross")));
    }

    // Oracle: `format_value(4820000.0, "currency")` under de-DE, what a
    // demand measures, is '4,82\xa0M\xa0€': the word carries its own gap.
    #[test]
    fn a_magnitude_word_before_a_suffix_currency_measures_as_production_does() {
        let cfg: ColConfig = crate::families::table::tests::col_config(json!({"format": "currency"})).expect("column config parses");
        let col = ColMeta {
            name: "amount".to_string(),
            header: "Amount".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows = vec![json!({"amount": 4_820_000.0}).as_object().unwrap().clone()];
        let cell = crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            format_numeric_col(&rows, &col, "all", &[RowRole::Value])
        })
        .expect("formats")
        .remove(0);
        assert_eq!(measured_cell_text(&cell), "4,82\u{a0}M\u{a0}€");
        assert_eq!(unpadded_cell_text(&cell), "4,82\u{a0}M\u{a0}€");
    }

    // Oracle: `decimal_pad_for(("P0", "P1", "P2", "P3"), n)` under de-DE selects
    // `P1` for '93,3', `P3` for '197' and `P0` for '93,30' (the fraction is what
    // follows the locale's comma), and `P3` for '1.234', whose point is a
    // thousands separator.
    #[test]
    fn the_decimal_pad_counts_the_digits_after_the_locales_decimal_mark() {
        let table: Vec<String> = ["P0", "P1", "P2", "P3"].map(String::from).to_vec();
        crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            assert_eq!(decimal_pad_for(&table, "93,3").unwrap(), "P1");
            assert_eq!(decimal_pad_for(&table, "197").unwrap(), "P3");
            assert_eq!(decimal_pad_for(&table, "93,30").unwrap(), "P0");
            assert_eq!(decimal_pad_for(&table, "1.234").unwrap(), "P3");
        });
    }

    #[test]
    fn an_exponent_outside_the_four_tiers_is_refused_by_name() {
        let err = suffix_of("anchor", 4, 12_340.0).unwrap_err();
        assert!(err.contains("exponent 4"), "{err}");
    }

    #[test]
    fn a_shared_scale_without_mode_fails_to_deserialize() {
        let err = serde_json::from_value::<SharedScale>(
            json!({"exponent": 3, "digit_spec": ",.2~f"}),
        )
        .err()
        .expect("missing mode must fail")
        .to_string();
        assert!(err.contains("mode"), "{err}");
    }

    #[test]
    fn an_unknown_mode_is_refused_by_name() {
        let err = suffix_of("sideways", 3, 12_340.0).unwrap_err();
        assert!(err.contains("sideways"), "{err}");
    }
}

/// Expected strings come from running production's `format_kpi_parts(...,
/// keep_affix_gaps=True)` (the lanes a table paints) and
/// `format_table_cell_value` (the string it measures) on the same inputs, under
/// `Regional(locale=...)`.
#[cfg(test)]
mod notation_and_shared_scale_lane_tests {
    use super::*;
    use super::bind::measured_cell_text;
    use super::bind::format_numeric_col;
    use serde_json::json;

    type Lanes = (String, String, String, String);

    fn lanes_of(column: Value, value: f64, symbol_mode: &str) -> Lanes {
        let cfg: ColConfig = crate::families::table::tests::col_config(column).expect("column config parses");
        let col = ColMeta {
            name: "v".to_string(),
            header: "V".to_string(),
            cfg,
            is_numeric: true,
            is_date: false,
            when_rules: Vec::new(),
        };
        let rows = vec![json!({"v": value}).as_object().unwrap().clone()];
        let _scope = super::tests::en_us_scope();
        let cell = format_numeric_col(&rows, &col, symbol_mode, &[RowRole::Value]).expect("formats").remove(0);
        let shared = col.cfg.shared_scale.is_some();
        (
            cell.prefix.clone().unwrap_or_default(),
            cell.text.clone(),
            cell.suffix.clone().unwrap_or_default(),
            measured_cell_text(&cell),
        )
    }

    fn lanes(column: Value, value: f64) -> Lanes {
        lanes_of(column, value, "all")
    }

    fn de(column: Value, value: f64, symbol_mode: &str) -> Lanes {
        crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            lanes_of(column, value, symbol_mode)
        })
    }

    fn lane(prefix: &str, text: &str, suffix: &str, measured: &str) -> Lanes {
        (prefix.into(), text.into(), suffix.into(), measured.into())
    }

    fn si(notation: &str) -> Value {
        json!({"format": {"spec": ".3~s", "notation": notation}})
    }

    #[test]
    fn an_authored_notation_picks_the_register_of_an_inline_si_spec_in_en_us() {
        assert_eq!(lanes(si("analytic"), 1_234_567.0), lane("", "1.23", "M", "1.23M"));
        assert_eq!(lanes(si("analytic"), 1500.0), lane("", "1.5", "K", "1.5K"));
        assert_eq!(lanes(si("narrative"), 1_234_567.0), lane("", "1.23", "mn", "1.23mn"));
        assert_eq!(lanes(si("narrative"), 1500.0), lane("", "1.5", "k", "1.5k"));
    }

    #[test]
    fn an_authored_notation_picks_the_register_of_an_inline_si_spec_in_de_de() {
        assert_eq!(de(si("analytic"), 1_234_567.0, "all"), lane("", "1,23", "M", "1,23\u{a0}M"));
        assert_eq!(de(si("analytic"), 1500.0, "all"), lane("", "1,5", "k", "1,5\u{a0}k"));
        assert_eq!(de(si("narrative"), 1_234_567.0, "all"), lane("", "1,23", "Mio.", "1,23\u{a0}Mio."));
        assert_eq!(de(si("narrative"), 1500.0, "all"), lane("", "1,5", "Tsd.", "1,5\u{a0}Tsd."));
    }

    #[test]
    fn a_house_format_defaults_to_analytic_and_obeys_an_authored_notation() {
        let house = json!({"format": "number"});
        let narrative = json!({"format": {"spec": "number", "notation": "narrative"}});
        assert_eq!(lanes(house.clone(), 1_234_567.0), lane("", "1.23", "M", "1.23M"));
        assert_eq!(lanes(narrative.clone(), 1_234_567.0), lane("", "1.23", "mn", "1.23mn"));
        assert_eq!(de(house, 1500.0, "all"), lane("", "1,5", "k", "1,5\u{a0}k"));
        assert_eq!(de(narrative, 1500.0, "all"), lane("", "1,5", "Tsd.", "1,5\u{a0}Tsd."));
    }

    #[test]
    fn a_currency_notation_keeps_the_locales_currency_after_the_word() {
        let currency = json!({"format": {"spec": "currency", "notation": "narrative"}});
        assert_eq!(lanes(currency.clone(), 1_234_567.0), lane("$", "1.23", "mn", "$1.23mn"));
        assert_eq!(
            de(currency, 1_234_567.0, "all"),
            lane("", "1,23", "Mio.\u{a0}\u{20ac}", "1,23\u{a0}Mio.\u{a0}\u{20ac}")
        );
    }

    fn shared(format: Value, mode: &str) -> Value {
        json!({"format": format, "shared_scale": {"exponent": 6, "mode": mode, "digit_spec": ",.2~f"}})
    }

    #[test]
    fn a_shared_scale_dollar_column_paints_en_us_lanes() {
        let column = shared(json!("$,.0f"), "anchor");
        assert_eq!(lanes_of(column.clone(), 24_730_000.0, "all").0, "$");
        let (prefix, text, suffix, _) = lanes(column, 24_730_000.0);
        assert_eq!((prefix.as_str(), text.as_str(), suffix.as_str()), ("$", "24.73", "M"));
        let affixed = shared(json!({"spec": "$,.0f", "prefix": "~ ", "suffix": " net"}), "anchor");
        let (prefix, _, suffix, _) = lanes(affixed, 24_730_000.0);
        assert_eq!((prefix.as_str(), suffix.as_str()), ("~ $", "M net"));
    }

    #[test]
    fn a_shared_scale_dollar_column_paints_a_suffix_currency_locales_template() {
        let column = shared(json!("$,.0f"), "anchor");
        let lanes_all = de(column.clone(), 24_730_000.0, "all");
        assert_eq!((lanes_all.0.as_str(), lanes_all.1.as_str(), lanes_all.2.as_str()), ("", "24,73", "M\u{a0}\u{20ac}"));
        let anchored = de(column, 24_730_000.0, "anchors");
        assert_eq!((anchored.0.as_str(), anchored.1.as_str(), anchored.2.as_str()), ("\u{20ac}", "24,73", "M"));
        let affixed = shared(json!({"spec": "$,.0f", "prefix": "~ ", "suffix": " net"}), "anchor");
        let (prefix, _, suffix, _) = de(affixed.clone(), 24_730_000.0, "all");
        assert_eq!((prefix.as_str(), suffix.as_str()), ("~ ", "M\u{a0}\u{20ac} net"));
        let (prefix, _, suffix, _) = de(affixed, 24_730_000.0, "anchors");
        assert_eq!((prefix.as_str(), suffix.as_str()), ("~ \u{20ac}", "M net"));
    }

    #[test]
    fn a_shared_scale_authored_notation_wins_over_the_modes_register() {
        let narrative = shared(json!({"spec": "$,.0f", "notation": "narrative"}), "anchor");
        assert_eq!(lanes(narrative, 24_730_000.0).2, "mn");
        let analytic = shared(json!({"spec": ",.0f", "notation": "analytic"}), "repeat");
        assert_eq!(lanes(analytic, 24_730_000.0).2, "M");
        let narrative_de = shared(json!({"spec": "$,.0f", "notation": "narrative"}), "anchor");
        assert_eq!(de(narrative_de.clone(), 24_730_000.0, "all").2, "Mio.\u{a0}\u{20ac}");
        assert_eq!(de(narrative_de, 24_730_000.0, "anchors").2, "Mio.");
        let repeat = shared(json!("$,.0f"), "repeat");
        assert_eq!(de(repeat, 24_730_000.0, "all").2, "Mio.\u{a0}\u{20ac}");
    }
}

#[cfg(test)]
mod contract_tests {
    use super::tests::{base_table_theme, resolved_title_font, resolved_title_style};
    use super::*;
    use serde_json::json;

    fn fonts() -> FontBook {
        let dir = format!(
            "{}/../../../src/dbt_charts/core/render/fonts",
            env!("CARGO_MANIFEST_DIR")
        );
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn payload_at(width: f64) -> Payload {
        payload_pinned(width, json!(null))
    }

    fn payload_pinned(width: f64, pin: Value) -> Payload {
        let mut theme = base_table_theme();
        theme["row"]["role"] = json!(null);
        // A bounded slot paginates; the unbounded measure does not, so the
        // layout-at-natural-height laws below hold only without it.
        theme["pagination"]["enabled"] = json!(false);
        crate::test_payload(json!({
            "resolved": {
                "link_open": "page",
                "title": null,
                "title_style": resolved_title_style(),
                "columns": {
                    "name": {"label": "Name", "width": pin},
                    "value": {"label": "Value", "format": ",.0f"},
                },
                "style": {"table": theme, "title_font": resolved_title_font()},
                "pivot_columns": null,
                "conditional_formatting": null,
            },
            "rows": [
                {"name": "Apex", "value": 1234567.0},
                {"name": "Bright", "value": 89.0},
            ],
            "width": width,
            "height": 0.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": null,
        }))
    }

    /// `bind` holds what the data decides and nothing a size decides: the cells
    /// read the same on a narrow card and a wide one.
    #[test]
    fn bind_formats_the_cells_whatever_the_card_width() {
        let _scope = super::tests::en_us_scope();
        let cells = |width: f64| -> Vec<Vec<String>> {
            let Bound::Rows(rows) = bind::bind(&payload_at(width)).expect("binds") else {
                panic!("a table with rows binds its rows");
            };
            rows.chart
                .cell_data
                .iter()
                .map(|column| column.iter().map(|cell| cell.text.clone()).collect())
                .collect()
        };
        assert_eq!(cells(120.0), cells(900.0));
        assert_eq!(cells(120.0)[1], ["1,234,567", "89"]);
    }

    /// The height a board reserves is the height `layout` draws: one law.
    #[test]
    fn height_for_width_is_the_extent_layout_draws_at_that_width() {
        let _scope = super::tests::en_us_scope();
        let fonts = fonts();
        let measure = Measure { fonts: &fonts };
        let bound = bind::bind(&payload_at(300.0)).expect("binds");
        let height = layout::height_for_width(&bound, 300.0, &measure).expect("measures");
        let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height };
        let laid_out = layout::layout(&bound, cell, &measure).expect("lays out");
        let extent = laid_out.insets.top + laid_out.data_box.height + laid_out.insets.bottom;
        assert!((height - extent).abs() < 1e-9, "{height} vs {extent}");
        let svg = print(&laid_out.nodes);
        assert!(svg.contains(&format!("height=\"{height}\"")), "{height} in {svg}");
    }

    #[test]
    fn intrinsic_is_narrowest_no_wider_than_preferred() {
        let _scope = super::tests::en_us_scope();
        let fonts = fonts();
        let bound = bind::bind(&payload_at(300.0)).expect("binds");
        let width = layout::intrinsic(&bound, &Measure { fonts: &fonts })
            .expect("measures")
            .expect("a table of auto columns has a natural width");
        assert!(0.0 < width.min && width.min <= width.preferred, "{width:?}");
    }

    /// A pixel decision's WARN comes back with the layout; the sink hears
    /// nothing until `render` forwards it.
    #[test]
    fn the_overflow_warning_is_returned_by_layout_not_sunk() {
        let _scope = super::tests::en_us_scope();
        let fonts = fonts();
        let measure = Measure { fonts: &fonts };
        let bound = bind::bind(&payload_pinned(200.0, json!(500))).expect("binds");
        let cell = Rect { x: 0.0, y: 0.0, width: 200.0, height: 400.0 };
        let (laid_out, sunk) =
            crate::diagnostic::collect(|| layout::layout(&bound, cell, &measure).expect("lays out"));
        assert!(sunk.is_empty(), "{sunk:?}");
        let codes: Vec<&str> = laid_out.diagnostics.iter().map(|d| d.code).collect();
        assert_eq!(codes, [crate::codes::WARN_TABLE_COLUMNS_OVERFLOW.id]);
    }

    #[test]
    fn layout_refuses_a_cell_with_no_height() {
        let _scope = super::tests::en_us_scope();
        let fonts = fonts();
        let bound = bind::bind(&payload_at(300.0)).expect("binds");
        let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height: 0.0 };
        let err = layout::layout(&bound, cell, &Measure { fonts: &fonts }).err().expect("refused");
        assert!(err.to_string().contains("taller than 0"), "{err}");
    }

    fn intrinsic_of(payload: &Payload) -> crate::layout::IntrinsicWidth {
        let fonts = fonts();
        let bound = bind::bind(payload).expect("binds");
        layout::intrinsic(&bound, &Measure { fonts: &fonts })
            .expect("measures")
            .expect("has a natural width")
    }

    /// A text column of several words can break between them, so the table
    /// narrows well below the width that shows every word on one line.
    #[test]
    fn a_multi_word_column_can_narrow_below_its_preferred_width() {
        let _scope = super::tests::en_us_scope();
        let mut payload = payload_at(300.0);
        payload.rows[0].insert("name".into(), json!("Apex Bright Cobalt Delta Echo"));
        let width = intrinsic_of(&payload);
        assert!(width.min + 40.0 < width.preferred, "{width:?}");
    }

    /// One hug solve serves both ends, so no pin or fixture puts the minimum
    /// above the preferred width.
    #[test]
    fn the_minimum_never_exceeds_the_preferred_width() {
        let _scope = super::tests::en_us_scope();
        let mut multi_word = payload_at(300.0);
        multi_word.rows[0].insert("name".into(), json!("Apex Bright Cobalt Delta Echo"));
        let mut numbered = payload_at(300.0);
        numbered.resolved["style"]["table"]["row_numbers"] =
            json!({"visible": true, "header": "#", "align": "right"});
        let payloads = [
            payload_at(300.0),
            multi_word,
            numbered,
            payload_pinned(300.0, json!(120)),
            payload_pinned(300.0, json!(500)),
            payload_pinned(300.0, json!("40%")),
        ];
        for payload in &payloads {
            let width = intrinsic_of(payload);
            assert!(width.min <= width.preferred, "{width:?}");
        }
    }

    /// Row numbers take the same width at any breadth, so both ends carry it.
    #[test]
    fn row_numbers_widen_the_minimum_and_the_preferred_width_alike() {
        let _scope = super::tests::en_us_scope();
        let plain = intrinsic_of(&payload_at(300.0));
        let mut numbered = payload_at(300.0);
        numbered.resolved["style"]["table"]["row_numbers"] =
            json!({"visible": true, "header": "#", "align": "right"});
        let numbered = intrinsic_of(&numbered);
        let (min_delta, preferred_delta) =
            (numbered.min - plain.min, numbered.preferred - plain.preferred);
        assert!(min_delta > 0.0, "{plain:?} vs {numbered:?}");
        assert!((min_delta - preferred_delta).abs() < 1e-9, "{plain:?} vs {numbered:?}");
    }
}
