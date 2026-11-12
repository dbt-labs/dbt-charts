//! `style/theme/charts.py`: the per-chart-type style map and the shared chart configuration.
//!
//! `PieChartStylePatch` is not a model: the donut slot is `Patch<PieChartStyle>`. The `title` slot
//! is the total [`ChartTitleStyle`]: Python's sparse patch only meant "copy `Style.title`". The root
//! redeclares `padding` and `background` and takes `legend` as the total model, where a family
//! holds a sparse override, so it lists the painted fields itself instead of flattening them.
//! `_reject_legacy_mark_keys` dissolved into parse: `label` and `map` are unknown keys here,
//! and the migration registry tells their story.

use crate::facet::Color;
use crate::primitives::{BorderStyle, ColorStyle};
use crate::style::area::AreaChartStyle;
use crate::style::axis::{
    AxisXStyle, AxisYStyle, BandAxisStyle, BaseAxisStyle, QuantitativeAxisStyle, TooltipStyle,
};
use crate::style::bar::BarChartStyle;
use crate::style::board::{ChartTitleStyle, PaddingStyle};
use crate::style::callout::CalloutChartStyle;
use crate::style::category_colors::CategoryColorBinding;
use crate::style::font::FontStyle;
use crate::style::geoshape::GeoshapeChartStyle;
use crate::style::heatmap::HeatmapChartStyle;
use crate::style::histogram::HistogramChartStyle;
use crate::style::kpi::KpiChartStyle;
use crate::style::layout::ViewStyle;
use crate::style::legend::LegendStyle;
use crate::style::line::LineChartStyle;
use crate::style::marks::{GlobalMarksStyle, SeriesLabelStyle};
use crate::style::pie::PieChartStyle;
use crate::style::point_map::PointMapChartStyle;
use crate::style::scatter::ScatterChartStyle;
use crate::style::spark_bar::SparkBarChartStyle;
use crate::style::table::{SupportTableStyle, TableChartStyle};
use indexmap::IndexMap;
use model_core::Patch;
use model_derive::Model;

/// Whether a chart visually answers "what am I pointing at", beyond the tooltip.
///
/// Hovering a mark recedes the others so the one under the cursor stands out. Bar, histogram,
/// pie/donut, and heatmap charts recede their other marks; line, area, and scatter instead draw
/// a datum marker plus a drop line down to the axis, since receding a stroke reads as an
/// interruption rather than emphasis. A family that does not yet answer the question ignores the
/// switch rather than half-answering it.
///
/// The line/area drop line's color and width: each theme picks a neutral one scaffold rung past
/// its own measure gridline, at twice that gridline's width, so the line reads as structure
/// rather than as another data series.
///
/// The browser runtime reads `visible` and `recede_contrast`, and the engine reads the same pair
/// to bake the receded look of a mark a board variable does not select, so changing either
/// changes the rendered SVG of a board that holds one. The drop line's paint stays runtime-only.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(recede_contrast, gt = 1)))]
pub struct HoverEmphasisStyle {
    /// Whether hovering a mark visually emphasizes it; theme always provides this.
    pub visible: bool,
    /// WCAG contrast ratio against the chart's canvas that a receded bar/arc is mixed down to while another mark is hovered. A mark already at or below it stays as it is.
    pub recede_contrast: f64,
    /// Color of the vertical drop line from a hovered line/area datum down to its axis.
    pub drop_line_color: Color,
    /// Width, in pixels, of the vertical drop line from a hovered line/area datum down to its axis.
    pub drop_line_width: f64,
}

/// Registry of all chart-type styles plus shared chart configuration.
///
/// Most shared chart fields (padding, aspect_ratio, min_height, max_height, legend) are
/// populated by theme YAML at the global `charts:` level and serve as the cascade source for
/// per-family defaults. `font`/`border` are declared directly here: only families with a
/// hand-drawn per-chart card render surface consume a per-family font/border. `tooltip` lives
/// here as the sole authoritative slot. `height` and `width` are not in the style cascade: they
/// live only at chart root in the authored YAML.
///
/// The `marks:` block holds global mark defaults (tier 1 of the three-tier mark cascade).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartsStyle {
    /// Chart legend style.
    pub legend: LegendStyle,
    /// Preferred chart width in pixels.
    #[model(inherit = "Style.charts.preferred_width")]
    pub preferred_width: f64,
    /// Padding as {top, right, bottom, left} in pixels, never one number.
    #[model(local)]
    pub padding: PaddingStyle,
    /// Chart canvas background; takes the board background while unset.
    #[model(inherit = "Style.background")]
    pub background: String,
    /// Chart aspect ratio (width/height).
    #[model(inherit = "Style.charts.aspect_ratio")]
    pub aspect_ratio: f64,
    /// Minimum chart height in pixels.
    #[model(inherit = "Style.charts.min_height")]
    pub min_height: f64,
    /// Maximum chart height in pixels.
    #[model(inherit = "Style.charts.max_height")]
    pub max_height: f64,
    /// Chart color scales: categorical palette and/or gradient.
    pub color: ColorStyle,
    /// Chart-level font overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Border around every chart card: `{width, color, radius}` or `'1px solid #ccc'`. Omit the key for no border; callout cards keep their own.
    pub border: BorderStyle,
    /// Title style for every chart's title.
    #[model(inherit_slot = "Style.title")]
    pub title: ChartTitleStyle,
    /// Board-wide category colors as `<column>: {values: {<value>: color}}`. Pins a category to one swatch across every chart on the board.
    #[model(default_empty)]
    pub category_colors: IndexMap<String, CategoryColorBinding>,
    /// Board-wide chart tooltip style.
    pub tooltip: TooltipStyle,
    /// Board-wide switch for hover emphasis on charts.
    pub hover_emphasis: HoverEmphasisStyle,
    /// Ordered list of Vega-Lite strokeDash arrays for line-family categorical encoding; None disables dash emission.
    #[model(absent = "solid lines")]
    pub dashes: Option<Vec<Vec<u32>>>,
    /// Fallback chart height in pixels when aspect-ratio sizing is unavailable.
    pub default_chart_height: f64,
    /// Placeholder table height in pixels; replaced by data-aware row-count sizing at render time.
    pub default_table_height: f64,
    /// Fraction of chart width usable for axis labels (0–1); labels are tilted when full labels exceed this width.
    pub label_usable_ratio: f64,
    /// Shared axis style applied to all axes before per-axis overrides.
    pub axis: BaseAxisStyle,
    /// X-axis style overrides applied after the shared axis. Unset fields fall back to style.charts.axis.
    #[model(local)]
    pub axis_x: Patch<AxisXStyle>,
    /// Y-axis style overrides applied after the shared axis. Unset fields fall back to style.charts.axis.
    #[model(local)]
    pub axis_y: Patch<AxisYStyle>,
    /// Quantitative axis style overrides applied after axis_x/axis_y. Unset fields fall back to style.charts.axis.
    #[model(local)]
    pub axis_quantitative: Patch<QuantitativeAxisStyle>,
    /// Band (category) axis style overrides applied after axis_x/axis_y. Unset fields fall back to style.charts.axis.
    #[model(local, default_empty)]
    pub axis_band: Patch<BandAxisStyle>,
    /// Vega-Lite view dimensions and border.
    pub view: ViewStyle,
    /// Global mark defaults (tier-1 of the marks cascade).
    pub marks: GlobalMarksStyle,
    /// Bar chart style; histogram has its own block.
    pub bar: Box<BarChartStyle>,
    /// Line chart style.
    pub line: Box<LineChartStyle>,
    /// Area chart style.
    pub area: Box<AreaChartStyle>,
    /// Scatter chart style.
    pub scatter: Box<ScatterChartStyle>,
    /// Histogram chart style.
    pub histogram: Box<HistogramChartStyle>,
    /// Heatmap chart style.
    pub heatmap: Box<HeatmapChartStyle>,
    /// Geoshape (choropleth) chart style.
    pub geoshape: Box<GeoshapeChartStyle>,
    /// Point map chart style.
    pub point_map: Box<PointMapChartStyle>,
    /// Pie/donut chart style.
    pub pie: Box<PieChartStyle>,
    /// Donut overrides, applied over `pie`: a board's pie or donut style reaches every donut, and `donut` styles donuts only.
    #[model(absent = "no donut overrides: a donut is the plain pie", local)]
    pub donut: Option<Patch<PieChartStyle>>,
    /// Shared series-label typography for endpoint and stack labels.
    pub series_label: SeriesLabelStyle,
    /// KPI card chart style.
    pub kpi: Box<KpiChartStyle>,
    /// Table chart style.
    pub table: Box<TableChartStyle>,
    /// Spark_bar (full-chart horizontal bar) style.
    pub spark_bar: Box<SparkBarChartStyle>,
    /// Attached support_table strip style.
    pub support_table: SupportTableStyle,
    /// Callout chart-family style for type:callout and runtime fallback cards.
    pub callout: Box<CalloutChartStyle>,
}
