//! `style/theme/table.py`: the table chart family and the attached support table,
//! with the authored column types it embeds (`style/authored/table.py`):
//! `PaginationConfig`, `TableColumnDefaultsConfig`, `TableColumnConfig` and what
//! they hold. A theme style and its authored form are one concept (D-02), so
//! there is one model each.
//!
//! - `SupportTableFontStyle` is [`FontStyle`]: its `color` also takes `auto`, which
//!   already passes the shape-only `Color` facet.
//! - A `None` that only meant "not set" is a total field (`columns`, `column_defaults`,
//!   `neutral_color`, the geo and table `border`); one that is a state is
//!   `Option<T>` with `absent`.
//! - `link` and `spark` take a value or a block ([`LinkSpec`], [`SparkSpec`]), `format` a
//!   [`FormatSpec`], and `pagination` its mapping form only: the shorthand `pagination: 25`
//!   waits for struct alternates in `accept`. The retired `spark.type`
//!   values (`progress`, `bars`, `histogram`) are an unknown-variant error here.
//! - Python defaults (`row_numbers`, `transpose`, `enabled`, `type: line`) are the
//!   built-in theme's to supply; the spine type is total.
//! - `SparkConfig.thresholds` keys are number text (D-24); they stay strings here, and
//!   no thresholds is the empty map.
//! - `PixelWidth` is a pixel count or a CSS width.

use crate::facet::{Color, ColorOrColumn, DisplayText, FontFamily, FontWeightValue};
use crate::chart::conditional_formatting::Predicate;
use crate::primitives::{
    Tone, BorderStyle, ColumnPalette, CornerStyle, DividerStyle, FormatSpec, GlyphText, LinkSpec,
    ScaleTargetRest, SpacingValues, GradientColorStyle, StrokeStyle,
};
use crate::rule::Auto;
use crate::string_enum;
use crate::style::board::PaddingStyle;
use crate::style::bar::misplaced_defaults;
use crate::style::chart_base::TitledChartStyleBase;
use crate::style::font::{FontSlant, FontStyle, LetterCase, TextDecoration};
use crate::unit::{Fraction, Px};
use indexmap::IndexMap;
use model_core::{
    Cx, Engine, FromNode, Leaf, Merge, Node, Patch, ReadScalar, Scalar, Spell, TypeExpr,
};
use diagnostic::{diag, Diagnostic};
use model_core::{Authored, Tri};
use model_derive::Model;
use serde_json::Value;

string_enum!(
    /// What happens to header text too wide for its column.
    HeaderOverflow { Clip = "clip", Truncate = "truncate", WrapTwo = "wrap-two", Wrap = "wrap" }
);
string_enum!(
    /// Text alignment in a table cell.
    CellAlign { Left = "left", Center = "center", Right = "right" }
);
string_enum!(
    /// Alignment of the row-number column.
    RowNumberAlign { Left = "left", Right = "right" }
);
string_enum!(
    /// Where currency-prefix and magnitude/unit suffix symbols show in a numeric column.
    SymbolMode { All = "all", Anchors = "anchors" }
);
string_enum!(
    /// What a table does with columns wider than its slot.
    ColumnOverflow { Scroll = "scroll", Fit = "fit" }
);
string_enum!(
    /// How a table reaches rows past its first page.
    PaginationMode { Pages = "pages", Scroll = "scroll" }
);
string_enum!(
    /// Strip placement of a support table.
    SupportTablePosition { Top = "top", Bottom = "bottom", Left = "left", Right = "right" }
);
string_enum!(
    /// The inline sparkline kinds.
    SparkType {
        Line = "line",
        Area = "area",
        Bar = "bar",
        BarNormalize = "bar-normalize",
        Column = "column",
        Columns = "columns",
    }
);

/// A column's `spark:`: a chart type alone, or a block. A layer replaces it whole.
#[derive(Debug, Clone, PartialEq)]
pub enum SparkSpec {
    Type(SparkType),
    Block(<SparkConfig as Leaf>::Whole),
}

/// A block's colors are its `color` and the plain-string colors beside it: the `thresholds` values
/// and the `background`. Each resolves as a token slot does; a string that is no token stays.
impl model_core::TokenBlock for SparkSpec {
    fn resolve_block(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        let Self::Block(block) = self else {
            return Ok(());
        };
        model_core::TokenSlot::resolve_tokens(&mut block.color, f)?;
        let mut plain: Vec<&mut String> = block.thresholds.values_mut().flatten().collect();
        if let model_core::Tri::Value(background) = &mut block.background {
            plain.push(background);
        }
        for text in plain {
            if let Some(resolved) = f(text)? {
                *text = resolved;
            }
        }
        Ok(())
    }
}

impl Spell for SparkSpec {
    fn spell(&self) -> Value {
        match self {
            Self::Type(t) => t.spell(),
            Self::Block(b) => b.spell(),
        }
    }
}

impl ReadScalar for SparkSpec {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, model_core::Kind::Map(_)) {
            return SparkConfig::whole(node, cx).map(Self::Block);
        }
        SparkType::read(node, cx).map(Self::Type)
    }
}

impl FromNode for SparkSpec {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for SparkSpec {
    type Whole = SparkSpec;
    type Authored = Option<SparkSpec>;
    type Patch = Option<SparkSpec>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Ref("SparkConfig", <SparkConfig as model_core::Model>::info),
        &TypeExpr::OneOf(SparkType::VALUES),
    );
}
model_core::build_leaf!(SparkSpec);

/// A column width: a pixel count, or a CSS width such as `10%`.
#[derive(Debug, Clone, PartialEq)]
pub enum PixelWidth {
    Pixels(u32),
    Css(String),
}

impl ReadScalar for PixelWidth {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match node.to_json() {
            Value::String(s) => Some(Self::Css(s)),
            Value::Number(n) => match n.as_i64() {
                Some(v) if v > 0 => {
                    let px = u32::try_from(v).ok().map(Self::Pixels);
                    if px.is_none() {
                        cx.invalid(node.span, "Input should be less than 4294967296");
                    }
                    px
                }
                Some(_) => {
                    cx.invalid(node.span, "Input should be greater than 0");
                    None
                }
                None => {
                    cx.mismatch(node, "an integer or a CSS width string");
                    None
                }
            },
            _ => {
                cx.mismatch(node, "an integer or a CSS width string");
                None
            }
        }
    }
}

impl Spell for PixelWidth {
    fn spell(&self) -> Value {
        match self {
            Self::Pixels(n) => n.spell(),
            Self::Css(s) => s.spell(),
        }
    }
}

impl FromNode for PixelWidth {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for PixelWidth {
    type Whole = PixelWidth;
    type Authored = Option<PixelWidth>;
    type Patch = Option<PixelWidth>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Keyed(
        &model_core::Keywords::bounded(model_core::Bounds {
            gt: Some(0.0),
            ..model_core::Bounds::NONE
        }),
        &TypeExpr::Either(
            &TypeExpr::Scalar(Scalar::Int),
            &TypeExpr::Scalar(Scalar::Str),
        ),
    );
}
model_core::build_leaf!(PixelWidth);

/// A table divider that also chooses how far the line runs.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableDividerStyle {
    #[flatten]
    pub divider: DividerStyle,
    /// Draw one unbroken line across the full table width (true) or only under each column (false).
    pub continuous: bool,
}

/// Column width budget and cell padding.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(freeze, ge = 0)))]
pub struct TableColumnsStyle {
    /// Default column width in pixels.
    pub default_width: f64,
    /// Horizontal padding inside table cells in pixels.
    pub cell_padding: f64,
    /// Auto-width columns whose min/max ratio >= this threshold are snapped to a
    /// shared width before budget allocation. 1.0 disables clustering; 0.0 forces
    /// all auto-columns to equal width.
    pub width_similarity_threshold: Fraction,
    /// Fractional breathing room added above the raw p95 column demand when
    /// pinning compact columns in mixed (compact + text) tables. 0.10 means each
    /// compact column is pinned at 10 % above its measured demand; the extra
    /// width is funded by the text-column budget. Has no effect on all-compact
    /// tables (proportional scaling already fills the budget). 0.0 disables headroom.
    pub content_headroom: Fraction,
    /// Whether columns past the slot scroll sideways or squeeze to fit.
    pub overflow: ColumnOverflow,
    /// Number of leading columns pinned while the table scrolls sideways.
    pub freeze: u32,
}

/// The header row.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableHeaderStyle {
    /// Show the header row (column labels + divider). Set to False on series-keyed
    /// tables where column meanings are obvious from context (e.g. donut-attached
    /// tables: swatch / share / name / value).
    pub visible: bool,
    /// Header row height in pixels.
    pub height: f64,
    /// Header font style overrides.
    #[model(inherit_slot = "Style.charts.table.font")]
    pub font: FontStyle,
    /// Compact-tier font overrides applied when body size ≤11px.
    #[model(absent = "no compact override", local)]
    pub font_compact: Option<Patch<FontStyle>>,
    /// Header background color. Themes may set a fill (cream, dark) when
    /// paper-warmth or contrast demands it.
    #[model(absent = "no fill")]
    pub background: Option<Color>,
    /// What happens to header text too wide for its column (clip, truncate, wrap-two, wrap).
    pub overflow: HeaderOverflow,
    /// Line between the header row and the body.
    pub divider: TableDividerStyle,
}

/// One body-row role (summary or total).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableRowRoleStyle {
    /// Line above rows with this role.
    pub divider: DividerStyle,
    /// Per-role font style override.
    #[model(absent = "the default row font", local)]
    pub font: Option<Patch<FontStyle>>,
    /// Per-role row background color.
    #[model(absent = "no fill")]
    pub background: Option<Color>,
}

/// Per-role style overrides for summary and total rows.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableRowRolesStyle {
    /// Style for summary role rows.
    pub summary: TableRowRoleStyle,
    /// Style for total role rows.
    pub total: TableRowRoleStyle,
}

/// Alternating row stripe style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableRowStripeStyle {
    /// Alternating stripe background color.
    #[model(absent = "no fill")]
    pub color: Option<Color>,
}

/// Body rows.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableRowStyle {
    /// Body row height in pixels.
    pub height: f64,
    /// Alternating row stripe style.
    #[model(absent = "no stripe")]
    pub stripe: Option<TableRowStripeStyle>,
    /// Line under each body row.
    pub divider: TableDividerStyle,
    /// Per-role style overrides for summary and total rows.
    pub roles: TableRowRolesStyle,
    /// Rules that restyle a row by any column's value, hidden ones included. Keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the background and font it gives every visible cell, or `columns` to style named cells (also glyph, glyph_color and tone). Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. A cell's own `conditional` rules beat these; `scale` paints over these on its own cells. Two columns cannot patch the same property.
    #[model(fill = fill_empty, local)]
    pub conditional: IndexMap<String, Vec<TableRowStyleConditional>>,
}

impl TableRowStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<TableRowStyle>) -> Vec<Diagnostic> {
        style
            .conditional
            .iter()
            .flat_map(|(column, rules)| {
                let rules = rules.as_deref().unwrap_or_default();
                let defaults: Vec<bool> = rules.iter().map(|r| matches!(r.predicate.default, Tri::Value(_))).collect();
                misplaced_defaults(column, &defaults)
            })
            .collect()
    }
}

/// The style a row rule applies to one named cell: the part of [`TableColumnConfig`] a
/// conditional rule may set, with no condition of its own.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableCellPatch {
    /// Semantic tone (positive|negative|warning|info) that colors the glyph via the theme's tone palette; the theme-adaptive alternative to a raw glyph_color. Requires a glyph. An explicit glyph_color wins.
    #[model(absent = "no tone")]
    pub tone: Option<Tone>,
    /// Cell background color (hex string, or 'transparent'/'none'), or a column
    /// ID: a value matching a query column name uses that row's value in the named
    /// column instead of the literal string.
    #[model(absent = "the column's own background")]
    pub background: Option<ColorOrColumn>,
    /// Cell font style overrides. `color` and `weight` resolve column-ID-first, the
    /// same as `background`: a value matching a query column name uses that row's
    /// value in the named column.
    #[model(absent = "the column's own font")]
    pub font: Option<ColumnFontStyleConditional>,
    /// Text shown before every cell value; fills the same slot as format.prefix and wins over it.
    #[model(absent = "the column's own glyph")]
    pub glyph: Option<GlyphText>,
    /// Color for the glyph. Requires glyph to be set.
    #[model(absent = "the column's own glyph color")]
    pub glyph_color: Option<Color>,
}

/// The part of [`TableRowStyle`] a conditional rule may set: one predicate plus the paint of
/// every visible cell in the row, or of named cells.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator"
    )),
    constraint(at_most_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator, got: {set}"
    )),
    constraint(at_least_one_of(background, font, columns,
        message = "A conditional rule requires at least one style to apply"
    ))
)]
pub struct TableRowStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Background of every visible cell in the row (hex string, or 'transparent'/'none'), or a column ID: a value matching a query column name uses that row's value in the named column.
    #[model(absent = "the cells' own background")]
    pub background: Option<ColorOrColumn>,
    /// Font of every visible cell in the row. `color` and `weight` resolve column-ID-first, the same as `background`.
    #[model(absent = "the cells' own font")]
    pub font: Option<ColumnFontStyleConditional>,
    /// Style for named cells of the row, keyed by a visible query column. Beats the row-level style on that cell.
    #[model(fill = fill_empty)]
    pub columns: IndexMap<String, TableCellPatch>,
}

/// The title block above the header row.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableTitleStyle {
    /// Minimum title-block height in pixels. Applies as a floor only when a
    /// subtitle is present; a title-only block sizes to its natural content
    /// height instead.
    pub height: f64,
    /// Table title font style overrides.
    #[model(inherit_slot = "Style.charts.table.font")]
    pub font: FontStyle,
}

/// `more_rows` or `empty_state` edge-case UI.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableEdgeStyle {
    /// Edge-case UI (more_rows / empty_state) font style overrides.
    #[model(inherit_slot = "Style.charts.table.font")]
    pub font: FontStyle,
}

/// The placeholder for an empty sparkline.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(requires("stroke.color",
        message = "spark.empty.stroke.color is required (renderer reads color/width/dasharray)"
    )),
    constraint(requires("stroke.width",
        message = "spark.empty.stroke.width is required (renderer reads color/width/dasharray)"
    )),
    constraint(requires("stroke.dasharray",
        message = "spark.empty.stroke.dasharray is required (renderer reads color/width/dasharray)"
    ))
)]
pub struct SparkEmptyStyle {
    /// Horizontal inset for the empty sparkline placeholder in pixels.
    pub inset_x: f64,
    /// Empty-state placeholder stroke style.
    pub stroke: StrokeStyle,
}

/// The single-data-point sparkline.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkSingleValueStyle {
    /// Horizontal inset for the single-value sparkline in pixels.
    pub inset_x: f64,
    /// Radius of the single-value marker circle in pixels.
    pub marker_radius: f64,
}

/// Inline `spark.type: columns` (multi-value vertical bars) defaults.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkColumnsStyle {
    /// Gap between column bars in pixels.
    pub gap: f64,
    /// Horizontal outer padding of the columns sparkline in pixels.
    pub padding: f64,
    /// Minimum rendered bar height in pixels.
    pub min_bar_height: f64,
    /// Column bar corner rounding.
    pub border: CornerStyle,
}

/// Inline `spark.type: bar` and `bar-normalize` (single horizontal bar) defaults.
///
/// The variant decides whether the background track is drawn: `bar` paints only
/// the fill; `bar-normalize` paints a track + fill. The sub-keys are shared.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarCellStyle {
    /// Track background color - fill-grade, pinned per theme.
    pub background: Color,
    /// Bar fill color.
    #[model(absent = "seeds from style.charts.color.categorical.single_series_palette[0]")]
    pub color: Option<Color>,
    /// Default maximum value for bar scale when no explicit max is authored.
    pub default_max: f64,
    /// Corner rounding for spark bar cells.
    pub border: CornerStyle,
    /// Spark bar cell font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
}

/// Spark area fill.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkAreaStyle {
    /// Spark area fill opacity (0–1).
    pub fill_opacity: f64,
}

/// Inline `spark.type: column` (single vertical bar) defaults.
///
/// Geometry only. Color, border and default_max are shared with `spark.bar`:
/// `column` is `bar-normalize`'s vertical mirror without the track.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkColumnStyle {
    /// Spark column default width in pixels.
    pub width: f64,
    /// Spark column default height in pixels.
    pub height: f64,
}

/// Inline sparkline defaults (inside table cells).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkStyle {
    /// Sparkline line/point color.
    #[model(absent = "seeds from style.charts.color.categorical.single_series_palette[0]")]
    pub color: Option<Color>,
    /// Cell padding around the sparkline in pixels.
    pub padding: SpacingValues,
    /// Style for empty/no-data sparklines.
    pub empty: SparkEmptyStyle,
    /// Style for single-data-point sparklines.
    pub single_value: SparkSingleValueStyle,
    /// Style for column-type sparklines.
    pub columns: SparkColumnsStyle,
    /// Style for the single vertical `column` spark mark.
    pub column: SparkColumnStyle,
    /// Style for bar/bar-normalize sparklines.
    pub bar: SparkBarCellStyle,
    /// Style for area sparklines.
    pub area: SparkAreaStyle,
}

/// Leading row-number column (`style.table.row_numbers`).
///
/// When visible, the renderer injects a synthetic column at index 0 that
/// displays the absolute 1-based row index in the original unpaginated dataset.
/// Pagination is continuous: page 2 of a 10-per-page table starts at 11. The
/// column is transparent to `style.columns`, to conditional_formatting rules
/// keyed by column name, and to data-pipeline transforms.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableRowNumbersStyle {
    /// Show a leading row-number column.
    #[model(fill = false)]
    pub visible: bool,
    /// Header label for the row-number column.
    #[model(fill = "#")]
    pub header: String,
    /// Text alignment for the row-number column.
    #[model(fill = "right")]
    pub align: RowNumberAlign,
    /// Row-number font style overrides.
    #[model(inherit_slot = "Style.charts.table.font")]
    pub font: FontStyle,
}

/// Visual style for the paginator control (chevrons + page numbers).
///
/// Distinct from [`PaginationConfig`], which controls behavior (enabled,
/// page_rows): the look is the color ramp across the three item states (active,
/// inactive, disabled), font sizing, weight contrast between the current page and
/// its neighbors, and the slot width that drives the hit target.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PaginatorStyle {
    /// Current page and live chevron color (theme body ink).
    pub color_active: Color,
    /// Other pages and ellipsis color (theme secondary text).
    pub color_inactive: Color,
    /// Chevron color when at first/last page (signals disabled by tone).
    pub color_disabled: Color,
    /// Paginator font overrides (size, family).
    #[model(absent = "the enclosing text font applies")]
    pub font: Option<Patch<FontStyle>>,
    /// Font weight for the current (selected) page number.
    pub weight_active: u32,
    /// Font weight for other pages and ellipsis.
    pub weight_inactive: u32,
    /// Font weight for the prev/next chevrons (live and disabled). Usually
    /// heavier than weight_active so the chevrons read as interactive affordances
    /// against the lighter page numbers.
    pub weight_chevron: u32,
    /// Per-item slot width in pixels (drives layout step).
    pub item_width: f64,
}

/// Table pagination: client-side row paging, a presentation concern distinct from
/// the query-level `limit` that bounds how many rows are fetched.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(page_rows, ge = 1)))]
pub struct PaginationConfig {
    /// Enable client-side pagination for table charts.
    #[model(fill = true)]
    pub enabled: bool,
    /// Rows per page, a ceiling bounded by the tile height. The theme defaults it
    /// to 20; unset means unlimited (the tile height alone sizes the page).
    #[model(absent = "unlimited")]
    pub page_rows: Option<u32>,
    /// Scroll rows under a pinned header, or page them with a pager. No effect
    /// with pagination off.
    #[model(fill = "scroll")]
    pub mode: PaginationMode,
}

/// Look of the scrollbars a scrolling table draws over its body. Drawn inside
/// the SVG, so a static export shows how much of the table is out of view.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(size, gt = 0), range(inset, ge = 0)))]
pub struct TableScrollbarStyle {
    /// Scrollbar thickness in pixels.
    pub size: f64,
    /// Gap between the scrollbar and the table edge in pixels.
    pub inset: f64,
    /// Scrollbar thumb color.
    pub thumb_color: Color,
    /// Hairline between frozen and scrolling columns.
    pub edge_color: Color,
    /// Scrollbar track color; unset draws the thumb alone.
    #[model(absent = "no track")]
    pub track_color: Option<Color>,
}

/// A table column's font. `color` may name a query column, so it is not a color field.
///
/// Its own model, not a flatten of [`FontStyle`]: `color` differs, and every field
/// is unset unless the column says so.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(size, gt = 0)), shorthand = "extends")]
pub struct ColumnFontStyle {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    #[model(absent = "the table font's family")]
    pub family: Option<FontFamily>,
    /// Text color (CSS color string), or a column ID: a value matching a query
    /// column name uses that row's value in the named column.
    #[model(absent = "the table font's color")]
    pub color: Option<ColorOrColumn>,
    /// Font size in pixels.
    #[model(absent = "the table font's size")]
    pub size: Option<f64>,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    #[model(absent = "the table font's weight")]
    pub weight: Option<FontWeightValue>,
    /// Upright or slanted type (normal or italic).
    #[model(absent = "the table font's style")]
    pub style: Option<FontSlant>,
    /// Line drawn on the text (underline, line-through, or none).
    #[model(absent = "the table font's decoration")]
    pub decoration: Option<TextDecoration>,
    /// Letter-case transform applied at render time.
    #[model(absent = "the table font's case")]
    pub case: Option<LetterCase>,
    /// Line height as a unitless multiple of font size.
    #[model(absent = "the table font's line height")]
    pub line_height: Option<f64>,
}

/// The part of [`ColumnFontStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends")]
pub struct ColumnFontStyleConditional {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Text color (CSS color string), or a column ID: a value matching a query
    /// column name uses that row's value in the named column.
    #[model(absent = "the table font's color")]
    pub color: Option<ColorOrColumn>,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    #[model(absent = "the table font's weight")]
    pub weight: Option<FontWeightValue>,
    /// Upright or slanted type (normal or italic).
    #[model(absent = "the table font's style")]
    pub style: Option<FontSlant>,
    /// Line drawn on the text (underline, line-through, or none).
    #[model(absent = "the table font's decoration")]
    pub decoration: Option<TextDecoration>,
}

/// The part of [`TableColumnConfig`] a conditional rule may set: one predicate plus the cell
/// style that varies per row.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator"
    )),
    constraint(at_most_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator, got: {set}"
    )),
    constraint(at_least_one_of(tone, background, font, glyph, glyph_color,
        message = "A conditional rule requires at least one style to apply"
    ))
)]
pub struct TableColumnConfigConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Semantic tone (positive|negative|warning|info) that colors the glyph via the theme's tone palette; the theme-adaptive alternative to a raw glyph_color. Requires a glyph. An explicit glyph_color wins.
    #[model(absent = "no tone")]
    pub tone: Option<Tone>,
    /// Cell background color (hex string, or 'transparent'/'none'), or a column
    /// ID: a value matching a query column name uses that row's value in the named
    /// column instead of the literal string.
    #[model(absent = "the column's own background")]
    pub background: Option<ColorOrColumn>,
    /// Cell font style overrides. `color` and `weight` resolve column-ID-first, the
    /// same as `background`: a value matching a query column name uses that row's
    /// value in the named column.
    #[model(absent = "the column's own font")]
    pub font: Option<ColumnFontStyleConditional>,
    /// Text shown before every cell value; fills the same slot as format.prefix and wins over it.
    #[model(absent = "the column's own glyph")]
    pub glyph: Option<GlyphText>,
    /// Color for the glyph. Requires glyph to be set.
    #[model(absent = "the column's own glyph color")]
    pub glyph_color: Option<Color>,
}

/// Table-level defaults applied to every column unless overridden per column.
///
/// Only fields that make sense as uniform defaults: data-dependent ones (link,
/// header_link, spark, scale, glyph, glyph_color, header_overflow) depend on a
/// column's data shape or interaction intent.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableColumnDefaultsConfig {
    /// Override column width in pixels (integer) or a CSS width string.
    #[model(absent = "the measured width")]
    pub width: Option<PixelWidth>,
    /// Override cell text alignment (left, center, or right).
    #[model(absent = "the column's own alignment")]
    pub align: Option<CellAlign>,
    /// How values are written in every column, unless a column sets its own.
    #[model(absent = "the value's own format")]
    pub format: Option<FormatSpec>,
    /// Override cell background color (CSS color string), or a column ID: a
    /// value matching a query column name uses that row's value.
    #[model(absent = "no background")]
    pub background: Option<ColorOrColumn>,
    /// Override cell font style (size, weight, color, family).
    #[model(absent = "the table font")]
    pub font: Option<ColumnFontStyle>,
}

/// A sparkline column's miniature chart.
#[derive(Debug, Clone, PartialEq, Model)]
// One `range` names one field, so two bounds are two constraints.
#[allow(clippy::duplicated_attributes)]
#[model(constraint(range(height, gt = 0)), constraint(range(width, gt = 0)))]
pub struct SparkConfig {
    /// Spark chart type (line, area, bar, bar-normalize, column, columns).
    #[model(fill = "line")]
    pub r#type: SparkType,
    /// Color for the spark mark.
    #[model(absent = "the table's spark color")]
    pub color: Option<Color>,
    /// Spark chart height in pixels.
    #[model(absent = "the table's spark height")]
    pub height: Option<u32>,
    /// Spark chart width in pixels.
    #[model(absent = "the table's spark width")]
    pub width: Option<u32>,
    /// Highlight the last data point (line/area spark charts).
    #[model(absent = "not highlighted")]
    pub last_visible: Option<bool>,
    /// Annotate the min and max data points (line spark charts only).
    #[model(absent = "not annotated")]
    pub min_max_visible: Option<bool>,
    /// Fill opacity for area spark charts (0–1).
    #[model(absent = "the table's area opacity")]
    pub fill_opacity: Option<Fraction>,
    /// Scaling ceiling: bar-normalize and column clamp the value to it; bar uses
    /// it in place of the column's data max.
    #[model(absent = "the column's data max")]
    pub max: Option<f64>,
    /// Color thresholds for bar / bar-normalize / column: {value: CSS color string}.
    #[model(default_empty)]
    pub thresholds: IndexMap<String, String>,
    /// Background track color for bar-normalize chart.
    #[model(absent = "the table's track color")]
    pub background: Option<String>,
    /// Corner radius in pixels for bar, bar-normalize, and column bars, and for the bar-normalize track.
    #[model(absent = "the table's corner radius")]
    pub border_radius: Option<f64>,
    /// Paint negative values (bar / column / columns) with the theme's
    /// tones.negative color instead of the shared spark color. Has no effect on
    /// columns with no negative values.
    #[model(fill = false)]
    pub negative_color: bool,
}

/// A table column's scale target: CSS color strings only, no relative stops.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ColumnScaleTargetConfig {
    /// Which colors the scale draws from: a built-in palette name or Vega scheme, or a CSS color list.
    pub palette: ColumnPalette,
    #[flatten]
    pub rest: ScaleTargetRest,
}

/// Scale-based continuous color mapping for a table column.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ColumnScaleConfig {
    /// Continuous background color mapping for this column.
    #[model(absent = "no background scale")]
    pub background: Option<ColumnScaleTargetConfig>,
    /// Continuous text color mapping for this column.
    #[model(absent = "no text scale")]
    pub color: Option<ColumnScaleTargetConfig>,
}

/// Configuration for a single table column.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(requires(glyph, when(set(glyph_color)),
        message = "TableColumnConfig glyph_color requires glyph to be set"
    )),
    constraint(at_most_one_of(width, max_width,
        message = "TableColumnConfig: 'width' and 'max_width' are mutually exclusive. Use 'width' for a hard pin, or 'max_width' to cap auto-sizing."
    )),
    constraint(forbids(conditional, when(is_in(visible, false)),
        message = "TableColumnConfig: 'conditional' rules on a column with 'visible: false' paint nothing. Remove the rules, show the column, or key a rule on it in style.row.conditional."
    ))
)]
pub struct TableColumnConfig {
    /// Whether this column renders. Defaults to true: style.columns is styling
    /// only, so naming a column here never hides it or any other column. Set false
    /// to hide it while keeping its values available to link: templates and
    /// style-input references. A column consumed as a style input (another
    /// column's background / font.color / font.weight names it) is hidden
    /// automatically unless it has its own style.columns entry; an explicit entry
    /// is a display signal and the column renders.
    #[model(absent = "renders")]
    pub visible: Option<bool>,
    /// Header, default the prettified column name (`first_year` reads "First
    /// year"). Alias in SQL instead.
    #[model(absent = "the prettified column name")]
    pub label: Option<DisplayText>,
    /// How the number is written: a D3 spec or a preset name.
    #[model(absent = "the table's column default")]
    pub format: Option<FormatSpec>,
    /// Miniature chart drawn inside each cell.
    #[model(absent = "no sparkline")]
    pub spark: Option<SparkSpec>,
    /// Render this column's cells as small rounded color squares instead of text.
    /// Cell value must be a CSS color string (e.g. '#3164a3'). Useful for
    /// series-keyed tables, e.g. a 'Series' column where each row is identified by
    /// its color in the parent chart's palette.
    #[model(absent = "text cells")]
    pub swatch: Option<bool>,
    /// Column width (integer pixels or CSS string like '10%').
    #[model(absent = "the measured width")]
    pub width: Option<PixelWidth>,
    /// Maximum column width for auto-sized text columns (integer pixels or CSS
    /// string like '30%'). Cannot be set together with width:.
    #[model(absent = "no cap")]
    pub max_width: Option<PixelWidth>,
    /// Text alignment in cells (left, center, right).
    #[model(absent = "the column's default alignment")]
    pub align: Option<CellAlign>,
    /// What happens to header text too wide for its column (clip, truncate, wrap-two, wrap).
    #[model(absent = "the table's header overflow")]
    pub header_overflow: Option<HeaderOverflow>,
    /// URL template that makes the column header clickable.
    #[model(absent = "no header link")]
    pub header_link: Option<LinkSpec>,
    /// URL template for cell values, with the row's fields available.
    #[model(absent = "no cell link")]
    pub link: Option<LinkSpec>,
    /// Cell background color (hex string, or 'transparent'/'none'), or a column
    /// ID: a value matching a query column name uses that row's value in the named
    /// column instead of the literal string.
    #[model(absent = "the table's column default")]
    pub background: Option<ColorOrColumn>,
    /// Cell font style overrides. `color` and `weight` resolve column-ID-first, the
    /// same as `background`: a value matching a query column name uses that row's
    /// value in the named column.
    #[model(absent = "the table's column default")]
    pub font: Option<ColumnFontStyle>,
    /// Continuous color mapping configuration for this column.
    #[model(absent = "no color scale")]
    pub scale: Option<ColumnScaleConfig>,
    /// Text shown before every cell value; fills the same slot as format.prefix and wins over it.
    #[model(absent = "no glyph")]
    pub glyph: Option<GlyphText>,
    /// Color for the glyph. Requires glyph to be set.
    #[model(absent = "the cell's ink")]
    pub glyph_color: Option<Color>,
    /// Rules that restyle this column's cells by the cell's own value. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the cell style it applies: background, font color/weight/style/decoration, glyph, glyph_color and tone. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched.
    #[model(fill = fill_empty, nullable, min_items = 1)]
    pub conditional: Vec<TableColumnConfigConditional>,
}

impl TableColumnConfig {
    /// A `default: true` rule is allowed only last in a column's `conditional` list: a positional
    /// rule over an element field, which no `constraint` kind tests.
    pub fn default_position(column: &Authored<TableColumnConfig>) -> Vec<Diagnostic> {
        let rules = column.conditional.as_deref().unwrap_or_default();
        rules
            .iter()
            .enumerate()
            .filter(|(i, r)| matches!(r.predicate.default, Tri::Value(_)) && i + 1 != rules.len())
            .map(|(i, _)| {
                diag::err_validation_field(
                    "conditional",
                    format!(
                        "Value error, A rule with default: true must be the last entry in a column's rules (found at position {i} of {})",
                        rules.len()
                    ),
                )
            })
            .collect()
    }
}

/// Table chart style overrides layered on top of shared chart defaults.
///
/// A table uses a fixed sizing contract and paints no legend, so it has no
/// `aspect_ratio`, `min_height`, `max_height` or `legend`. `border` follows
/// `style.charts.border`: a table draws its own card.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableChartStyle {
    #[flatten]
    pub titled: TitledChartStyleBase,
    /// Width the board asks for when sizing to a table before its data is known.
    /// With data, a board with no width measures the table's columns instead. Only
    /// a value set on the chart itself pins the width; a board- or theme-level
    /// value is just the estimate used before data is known.
    #[model(inherit = "Style.charts.preferred_width")]
    pub preferred_width: f64,
    /// Padding as {top, right, bottom, left} in pixels, never one number.
    #[model(inherit_slot = "Style.charts.padding")]
    pub padding: PaddingStyle,
    /// Table background color.
    #[model(absent = "the theme's background")]
    pub background: Option<Color>,
    /// Table chart-level font overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Chart card border style.
    #[model(inherit_slot = "Style.charts.border")]
    pub border: BorderStyle,
    /// Table color: gradient scale only (no categorical arm). Cell text color is `column_defaults.font.color`.
    #[model(default_empty)]
    pub color: GradientColorStyle,
    /// Outer table padding in pixels.
    pub outer_padding: f64,
    /// Extra bottom padding below the last row in pixels.
    pub bottom_padding: f64,
    /// Default column width and cell padding settings.
    pub column_layout: TableColumnsStyle,
    /// Table header row style.
    pub header: TableHeaderStyle,
    /// Table body row style.
    pub row: TableRowStyle,
    /// Leading row-number column configuration.
    pub row_numbers: TableRowNumbersStyle,
    /// Title block rendered above the header row.
    pub title_row: TableTitleStyle,
    /// Allow cell text wrapping; false clips to single line.
    pub wrap: bool,
    /// Hide query columns whose names start with an underscore, such as a `_flag`
    /// computed for a conditional rule or `_fivetran_synced`. A hidden column's values
    /// still feed rules and links, and a column with its own `style.columns` entry
    /// always shows. Set false to show them.
    pub hide_underscore_columns: bool,
    /// Client-side pagination defaults for table charts.
    pub pagination: PaginationConfig,
    /// Table-level column defaults applied to every column.
    #[model(default_empty)]
    pub column_defaults: TableColumnDefaultsConfig,
    /// Per-column display configuration keyed by column name (label, format, width, etc.).
    #[model(default_empty)]
    pub columns: IndexMap<String, TableColumnConfig>,
    /// Table column header text overflow mode.
    #[model(absent = "each header's own overflow")]
    pub header_overflow: Option<HeaderOverflow>,
    /// Visual style for the paginator control (chevrons + page numbers).
    pub paginator: PaginatorStyle,
    /// Visual style for the scrollbars of a table that scrolls.
    pub scrollbar: TableScrollbarStyle,
    /// Where to show currency-prefix and magnitude/unit suffix symbols in a numeric
    /// column. 'all' shows the full formatted value on every row; 'anchors' shows
    /// it only on the first data row and summary/total rows, stripping prefix and
    /// suffix from plain middle rows so the anchors guide the reader at the top and
    /// bottom of the value field.
    pub symbol_mode: SymbolMode,
    /// Style for the 'more rows' edge-case indicator.
    pub more_rows: TableEdgeStyle,
    /// Style for the empty-state (no data) indicator.
    pub empty_state: TableEdgeStyle,
    /// Inline sparkline defaults for table cells.
    pub spark: SparkStyle,
    /// Render a single wide data row as N (label, value) rows, one per column.
    /// Raises ChartDataError when data has more than one row. Used for Looker
    /// single-value summary tiles with multiple measures.
    #[model(fill = false)]
    pub transpose: bool,
    /// Vertical offset to align SVG text baseline with cell grid in pixels.
    pub text_baseline_offset: f64,
    /// Pure whitespace between the title's descent and the subtitle's ascent, in
    /// pixels, not a baseline-to-baseline distance. Combined with the title and
    /// subtitle font sizes to reproduce Vega-Lite's title->subtitle spacing at any
    /// font size. Font size and color for the subtitle itself come from
    /// style.title.subtitle (the same source chart-family titles use).
    pub title_subtitle_gap: f64,
}

/// Padding inside support_table rows.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct SupportTableRowPaddingStyle {
    /// Vertical (top/bottom) padding inside support_table rows in pixels.
    pub vertical: f64,
    /// Horizontal (left/right) padding inside support_table rows in pixels.
    pub horizontal: f64,
}

/// Support_table row padding and divider.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct SupportTableRowStyle {
    /// Row padding style.
    pub padding: SupportTableRowPaddingStyle,
    /// Line under each strip row.
    pub divider: DividerStyle,
}

/// Row label styling.
///
/// The row label's side (left or right gutter) is a pure function of the chart's
/// resolved `axis_y.orient`: there is no authorable `position` or `align`. With
/// `axis_y.orient == "right"` the label sits at `spec_width + axis_y.labels.padding`
/// and extends rightward; with `"left"` it sits at `-axis_y.labels.padding` and
/// extends leftward.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SupportTableLabelStyle {
    /// Row label font style overrides. An 'auto' color follows the table's own
    /// font color, or `neutral_color` when that is also 'auto'.
    #[model(inherit_slot = "Style.charts.support_table.font")]
    pub font: FontStyle,
}

/// The rule between plot and strip, plus the gap it reserves before the first row.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SupportTableDividerStyle {
    #[flatten]
    pub divider: DividerStyle,
    /// Vertical gap between the divider and the first strip row.
    pub gap: Px,
}

/// Attached support_table style. Lives at `style.charts.support_table.*`, and under
/// the per-chart-type blocks (bar, line, area) for per-chart-type overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SupportTableStyle {
    /// Support_table font style overrides. Its `color` also takes `auto`: a
    /// per-series cell paints in its series' dark ink and every other cell in
    /// `neutral_color`; a color paints every cell.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Ink for cells that belong to no series (totals, source columns, labels)
    /// when `font.color` is 'auto'.
    #[model(inherit = "Style.charts.font.color")]
    pub neutral_color: Color,
    /// Ink for a funnel's ribbon rows, the change between two stage rows. One step
    /// quieter than the stage rows, at the same size.
    pub ribbon_color: Color,
    /// Line at the boundary between the chart plot and the data strip. For
    /// position='bottom': the line sits above the strip (below the axis). For
    /// position='top': the line sits below the strip rows (above the plot top).
    pub divider: SupportTableDividerStyle,
    /// Support_table row padding and divider style.
    pub row: SupportTableRowStyle,
    /// Row label (series name) style.
    pub label: SupportTableLabelStyle,
    /// Padding above the topmost strip row in pixels. For position='bottom': gap
    /// between axis labels and the first row. For position='top': space above the
    /// topmost row (outer edge of strip).
    pub padding_top: f64,
    /// Padding below the last strip row in pixels. For position='bottom': space
    /// below the last row. For position='top': gap between the last row and the
    /// plot top edge.
    pub padding_bottom: f64,
    /// Number of x-axis label lines to reserve in the axis gap (only used for
    /// position='bottom'; ignored for position='top'). Typically 1 or 2. Vega emits
    /// an array label expression at year boundaries, so the strip reserves two
    /// lines even on months that emit one.
    pub label_max_lines: u32,
    /// Strip placement relative to the chart plot. 'top'/'bottom' apply when the
    /// chart's category axis is horizontal (vertical bar, line, area): 'top'
    /// places the strip above the plot, 'bottom' places it below with the x-axis
    /// between plot and strip. 'left'/'right' apply when the category axis is
    /// vertical (a horizontal bar): the strip renders as value columns beside the
    /// plot instead of rows above or below it. 'auto' uses 'top' on a horizontal
    /// category axis and the side the category labels are on on a vertical one.
    #[model(fill = "auto")]
    pub position: Engine<SupportTablePosition, Auto>,
}
