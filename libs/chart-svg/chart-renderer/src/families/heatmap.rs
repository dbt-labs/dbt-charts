//! Heatmap family — band×band grid with quantitative color ramp.
//!
//! The resolved-chart model and the helpers the facet composite and other families share live
//! here with `render`; [`bind`] holds what the rows decide and [`layout`] every pixel (the
//! autosize-fit negotiation, the axes, the legend). `render` sends a `multiples` payload to
//! the facet composite first.

pub mod bind;
pub mod layout;

use crate::codes;
use crate::contract::HeatmapValue;
use crate::diagnostic::{warn, Diagnostic};
use crate::constants::axis::GRADIENT_NICE_TICK_COUNT;
use crate::layout::Rect;
use std::collections::HashMap;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    ScaleTarget, row_num, row_str, AxisStyle, ChartSort, Font, FormatSlot, StyleChannel, TitleStyle,
};
use crate::families::cartesian::BandScale;
use crate::families::color_scale::{continuous_gradient_def, ContinuousColor};
use scene::{Attrs, Chars, Escape, Node, TextBlock};
use print::print;
use crate::families::heatmap_facet::first_appearance;
use crate::families::legend::{
    gradient_labeled_values, gradient_length, gradient_tick_count, unit_position, LegendConfig, LegendEntry,
    LegendSymbol, GRADIENT_STOP_TICKS,
};
use crate::families::temporal::{infer_vega_type, VegaType};
use measure::{FontBook, Measure};
use crate::vega_fit;
use crate::Payload;

/// `utils.py::_BREATHING_ROOM_PX` — the gap production leaves between an
/// own-side label's near edge and the plot it grows toward.
pub(crate) const BREATHING_ROOM_PX: f64 = 4.0;
/// Vega's `config.axis.translate`: every axis group is drawn half a pixel in
/// from its anchor so 1px strokes land on a pixel boundary.
pub(crate) const AXIS_TRANSLATE: f64 = 0.5;

/// The tick count of a symbol legend over a continuous scale (Vega's `legend.tickCount` default).
pub(crate) const SYMBOL_LEGEND_TICK_COUNT: usize = 5;

#[derive(Deserialize)]
pub(crate) struct HeatmapResolved {
    /// The chart's own id (`ResolvedChart.id`, always present on a real
    /// payload) -- namespaces the one id this family mints, its gradient
    /// `<linearGradient>` (`contract::safe_svg_id`, `converters/chart.py`'s
    /// own suffix law).
    pub(crate) id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) subtitle: Option<String>,
    pub(crate) title_style: Option<TitleStyle>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    pub(crate) background: String,
    pub(crate) x: String,
    pub(crate) y: String,
    #[serde(default, deserialize_with = "null_as_empty")]
    pub(crate) color: String,
    /// Set by the multi-measure fold: each cell is described by Vega's default `field: value` pairs, the x
    /// column and the measure the cell belongs to.
    #[serde(default)]
    pub(crate) wide_native_aria: bool,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// as an `<a>` around each cell.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    #[serde(default)]
    link_open: crate::href::LinkOpen,
    /// The host's board URL scheme, from the payload; rewrites each datum href.
    #[serde(skip)]
    link_context: Option<crate::href::LinkContext>,
    /// Authored axis titles (`x_label`/`y_label`); the field's own name
    /// stands in for an absent one (`resolve_xy_titles`).
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) x_label: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub(crate) y_label: Option<String>,
    pub(crate) field_labels: crate::common::FieldLabels,
    /// Authored `chart.sort` — reaches BOTH dimensions identically
    /// (`emitters/heatmap.py::_write_dimension_sort`), reordered by
    /// `common::dimension_sort_domain`.
    #[serde(default)]
    pub(crate) sort: Option<ChartSort>,
    pub(crate) multiples: Option<crate::families::multiples::MultiplesConfig>,
    /// Baked small-multiples partition dimensions (`resolved.panel_axes`).
    #[serde(default)]
    pub(crate) panel_axes: Vec<crate::families::multiples::PartitionAxis>,
    #[serde(default)]
    pub(crate) resolved_channels: HashMap<String, StyleChannel>,
    pub(crate) legend: LegendConfig,
    pub(crate) style: HeatmapStyle,
    /// The theme's category range, handed out in domain order to a nominal color.
    #[serde(default)]
    pub(crate) palette: Vec<String>,
    /// The palette a series color reads: the chart's own, else the board's, else Vega's default.
    #[serde(skip)]
    pub(crate) series_palette: Vec<String>,
    /// Board-wide value→slot bindings; one bound to the color field refuses.
    #[serde(default)]
    pub(crate) category_colors: Vec<crate::common::CategoryColorScale>,
}

/// Vega-Lite filters a row whose quantitative or temporal color is invalid out
/// of the data before any scale reads it, so a band only such rows fill leaves
/// the grid.
pub(crate) fn drop_invalid_color_rows(
    rows: Vec<serde_json::Map<String, Value>>,
    field: &str,
    kind: ColorKind,
) -> Vec<serde_json::Map<String, Value>> {
    let valid = |row: &serde_json::Map<String, Value>| match kind {
        ColorKind::Categories => true,
        ColorKind::Dates => row.get(field).and_then(date_cell_day).is_some(),
        ColorKind::Gradient => row_num(row, field).is_some_and(f64::is_finite),
    };
    rows.into_iter().filter(|row| valid(row)).collect()
}

/// What a heatmap's color channel is, decided once from its data.
#[derive(Clone, Copy, PartialEq)]
pub(crate) enum ColorKind {
    Gradient,
    Categories,
    Dates,
}

#[derive(Deserialize)]
pub(crate) struct ColorGradient {
    /// Pre-computed hex color stops from lightest to darkest.
    #[serde(default)]
    pub(crate) resolved_stops: Vec<String>,
    /// Author-supplied domain override; null means use the data range.
    #[serde(default)]
    pub(crate) min: Option<f64>,
    #[serde(default)]
    pub(crate) max: Option<f64>,
    /// Author opt-in to the nice-widened tick ladder (themed true).
    pub(crate) nice: bool,
    #[serde(default)]
    palette: Value,
    #[serde(default)]
    hinge: Option<Value>,
    #[serde(default)]
    arm_mode: String,
}

fn null_as_empty<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<String, D::Error> {
    Ok(Option::<String>::deserialize(deserializer)?.unwrap_or_default())
}

/// The column a colorless heatmap's cells share: one category, so every cell takes the
/// palette's first color and no legend or hover value is drawn.
pub(crate) const FLAT_COLOR: &str = "\u{0}flat";

pub(crate) fn with_flat_column(rows: Vec<serde_json::Map<String, Value>>) -> Vec<serde_json::Map<String, Value>> {
    rows.into_iter()
        .map(|mut row| {
            row.insert(FLAT_COLOR.to_string(), Value::String(String::new()));
            row
        })
        .collect()
}

impl HeatmapResolved {
    /// A heatmap authored with no `color:` paints every cell in one fill.
    pub(crate) fn flatten_color(&mut self, rows: Vec<serde_json::Map<String, Value>>) -> Vec<serde_json::Map<String, Value>> {
        if !self.color.is_empty() {
            return rows;
        }
        self.color = FLAT_COLOR.to_string();
        self.legend.visible = false;
        with_flat_column(rows)
    }

    pub(crate) fn is_flat(&self) -> bool {
        self.color == FLAT_COLOR
    }

    /// The per-cell link; a date column prints through `timeFormat`.
    pub(crate) fn datum_link(&self, (x_date, y_date, color_date): (bool, bool, bool)) -> Option<crate::href::DatumLink<'_>> {
        use crate::href::{LinkField, LinkText};
        let text = |date: bool| if date { LinkText::Date } else { LinkText::Raw };
        Some(crate::href::DatumLink {
            template: self.link.as_deref()?,
            open: self.link_open,
            x: LinkField { name: &self.x, text: text(x_date) },
            y: Some(LinkField { name: &self.y, text: text(y_date) }),
            color: Some(&self.color).filter(|c| !c.is_empty()).map(|c| LinkField { name: c, text: text(color_date) }),
            context: self.link_context.as_ref(),
        })
    }
}

impl ColorGradient {
    /// The color domain, its legend ticks, and the scale an authored palette
    /// paints (resolve bakes `resolved_stops` for the theme's default palette
    /// only). A diverging scale spans its pivot-extended bounds and labels the
    /// pivot beside the ticks.
    pub(crate) fn ladder(
        &self,
        rows: &[serde_json::Map<String, Value>],
        field: &str,
    ) -> Result<(f64, f64, Vec<f64>, Option<ContinuousColor>), Diagnostic> {
        let (min, max, ticks) = gradient_ladder(rows, field, self)?;
        if self.resolved_stops.len() >= 2 {
            return Ok((min, max, ticks, None));
        }
        let all = rows
            .iter()
            .filter_map(|row| row_num(row, field))
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
        let target = ScaleTarget {
            palette: self.palette.clone(),
            resolved_stops: None,
            min: self.min,
            max: self.max,
            nice: self.nice,
            null_color: None,
            hinge: self.hinge.clone(),
            arm_mode: self.arm_mode.clone(),
        };
        let scale = ContinuousColor::gradient(&target, Some(all), (min, max)).map_err(|e| format!("heatmap: {e}"))?;
        let Some(pivot) = scale.pivot else {
            return Ok((min, max, ticks, Some(scale)));
        };
        let mut ticks = if ticks.len() == 2 {
            vec![scale.lo, scale.hi]
        } else {
            d3rs::ticks::d3_ticks(scale.lo, scale.hi, GRADIENT_NICE_TICK_COUNT)
        };
        if !ticks.contains(&pivot) {
            ticks.push(pivot);
            ticks.sort_by(f64::total_cmp);
        }
        Ok((scale.lo, scale.hi, ticks, Some(scale)))
    }
}

/// What colors a gradient cell or legend bar: the theme's resolved stops
/// (sRGB) or an authored palette's scale.
#[derive(Clone, Copy)]
pub(crate) enum GradientColors<'a> {
    Stops(&'a [String]),
    Scale(&'a ContinuousColor),
}

impl<'a> GradientColors<'a> {
    pub(crate) fn new(gradient: &'a ColorGradient, scale: Option<&'a ContinuousColor>) -> Self {
        scale.map_or(Self::Stops(&gradient.resolved_stops), Self::Scale)
    }

    pub(crate) fn at(self, min: f64, max: f64, value: f64) -> Result<String, String> {
        match self {
            Self::Stops(stops) => interp_color(stops, min, max, value),
            Self::Scale(scale) => Ok(scale.fill(value)),
        }
    }
}

#[derive(Deserialize, Default)]
pub(crate) struct RectMark {
    pub(crate) opacity: Option<f64>,
    pub(crate) stroke: Option<crate::common::Stroke>,
}

impl RectMark {
    /// The stroke that paints on a cell: its color and width, `None` without a
    /// color, a positive width, or a visible mark.
    fn painted_stroke(&self) -> Option<(&crate::common::Stroke, &str, f64)> {
        let stroke = self.stroke.as_ref()?;
        let (color, width) = (stroke.color.as_deref()?, stroke.width?);
        (width > 0.0 && color != "none" && self.opacity != Some(0.0)).then_some((stroke, color, width))
    }

    /// How far a painted stroke grows the cells' bounds: Vega's `boundStroke`
    /// expands them by the full stroke width on every side.
    pub(crate) fn stroke_reach(&self) -> f64 {
        self.painted_stroke().map_or(0.0, |(_, _, width)| width)
    }

    /// The cell stroke attributes Vega writes for a stroked rect: nothing
    /// without a painted stroke, then the cap, join and dash.
    pub(crate) fn stroke_attrs(&self) -> Result<Attrs, String> {
        let Some((stroke, color, width)) = self.painted_stroke() else { return Ok(Attrs::new()) };
        let mut attrs = Attrs::new().set("stroke", color).set("stroke-width", width.to_string());
        if let Some(cap) = &stroke.cap {
            attrs = attrs.set("stroke-linecap", cap.as_str());
        }
        if let Some(join) = &stroke.join {
            attrs = attrs.set("stroke-linejoin", join.as_str());
        }
        if let Some(dasharray) = &stroke.dasharray {
            let raw = dasharray.as_str().ok_or("heatmap: a rect mark stroke dasharray is not a string of lengths")?;
            attrs = attrs.set("stroke-dasharray", crate::step_band::dasharray(&crate::step_band::parse("heatmap", raw, false)?, None));
        }
        Ok(attrs)
    }
}

#[derive(Deserialize)]
pub(crate) struct HeatmapStyle {
    pub(crate) axis_x: AxisStyle,
    pub(crate) axis_y: AxisStyle,
    pub(crate) color_gradient: ColorGradient,
    #[serde(default)]
    pub(crate) rect_mark: RectMark,
    /// The format of the tooltip's value row. The gradient legend paints its
    /// spec alone (the color encoding takes `tooltip_format.spec`,
    /// `emitters/heatmap.py:400`) and falls back to comma-integer.
    #[serde(default)]
    pub(crate) tooltip_format: Option<FormatSlot>,
    /// Width-resolved OBJECT title font — see `common::effective_title_style`.
    #[serde(default)]
    pub(crate) title_font: Option<Font>,
    /// Fraction of the slot width the x labels may crowd into.
    #[serde(default)]
    pub(crate) label_usable_ratio: Option<f64>,
}

impl HeatmapResolved {
    pub(crate) fn from_payload(payload: &Payload) -> Result<Self, String> {
        let mut r: Self = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("heatmap payload: {e}"))?;
        r.series_palette = payload.series_palette(&r.palette);
        r.link_context = payload.link_context.clone();
        Ok(r)
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    if payload.resolved.get("multiples").is_some_and(|multiples| !multiples.is_null()) {
        return crate::families::heatmap_facet::render(payload, fonts);
    }
    let bound = bind::bind(payload)?;
    let measure = Measure { fonts };
    let cell = Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
    let laid_out = layout::layout(&bound, cell, &measure)?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
}

/// Whether the row labels sit on their own side of the axis (`left` on a left axis, `right` on a
/// right one): production bakes `labelPadding` from their widest. `center`, an upper/lower
/// `font.case`, a date y and an authored `labels.expr` (all paint through a `labelExpr`) take Vega-Lite's away-side default.
pub(crate) fn y_labels_own_side(ax_y: &crate::common::AxisStyle, y_is_date: bool) -> bool {
    let own_align = if ax_y.position.as_deref() == Some("right") { "right" } else { "left" };
    ax_y.labels.align.as_deref() == Some(own_align)
        && !matches!(ax_y.labels.font.case.as_deref().unwrap_or("none"), "upper" | "lower")
        && !y_is_date
        && ax_y.labels.expr.is_none()
}

/// A nominal or ordinal color's scale: the entries in domain order and the
/// fill each takes.
pub(crate) struct CategoryScale {
    pub(crate) domain: Vec<String>,
    pub(crate) fills: Vec<String>,
    /// Vega keys the domain by each value's text, so a null and the text
    /// `null` are one entry, held by whichever came first. This says it is
    /// the real null; the other kind of cell then matches no entry and
    /// paints no fill.
    pub(crate) null_is_js: bool,
}

/// The legend's entries for `scale`: an authored `legend.values` picks and
/// orders them (an empty list draws a title-only legend), each keeping the
/// palette slot its place in the domain gave it.
pub(crate) fn category_legend_entries<'a>(scale: &'a CategoryScale, authored: Option<&[String]>) -> Vec<LegendEntry<'a>> {
    let order = match authored {
        Some([]) => Vec::new(),
        Some(authored) => {
            let names: Vec<&str> = scale.domain.iter().map(String::as_str).collect();
            crate::families::legend::resolve_legend_values(authored, &names, None)
        }
        None => (0..scale.domain.len()).collect(),
    };
    order
        .into_iter()
        .map(|i| LegendEntry {
            label: &scale.domain[i],
            fill: &scale.fills[i],
            symbol: Some(LegendSymbol::FilledSquare),
            series_key: None,
        })
        .collect()
}

/// How a cell takes its fill from its color value.
#[derive(Clone, Copy)]
pub(crate) enum CellFill<'a> {
    Gradient { colors: GradientColors<'a>, min: f64, max: f64 },
    Categories(&'a CategoryScale),
    /// A date column on a time scale, in day numbers.
    Dates { min: f64, max: f64 },
}

/// How a dimension's value heads the cell's tooltip: a date column prints its
/// bucket (`header_tooltip_field`), anything else its raw text.
pub(crate) struct HeaderFmt<'a> {
    tu: Option<crate::families::temporal::TimeUnit>,
    standard_date: bool,
    format: Option<crate::families::temporal::AuthoredTimeFormat<'a>>,
}

impl<'a> HeaderFmt<'a> {
    pub(crate) fn of(rows: &[serde_json::Map<String, Value>], field: &str, axis: &'a AxisStyle) -> Result<Self, String> {
        let domain = first_appearance(rows, field);
        let strs: Vec<&str> = domain.iter().map(String::as_str).collect();
        let tu = crate::families::temporal::detect_time_unit(&strs);
        Ok(Self {
            tu,
            standard_date: crate::families::temporal::x_values_are_standard_dates(&strs),
            format: crate::families::temporal::AuthoredTimeFormat::parse(
                "heatmap",
                crate::families::temporal::grain_format(&axis.grain_formats, tu),
            )?,
        })
    }

    fn text(&self, cat: &str) -> Result<String, String> {
        crate::contract::tooltip_header_text(cat, self.tu, self.standard_date, self.format)
    }
}

/// What paints one heatmap cell, common to a single chart and to every panel
/// of a facet composite.
pub(crate) struct CellPaint<'a> {
    pub(crate) x_field: &'a str,
    pub(crate) y_field: &'a str,
    pub(crate) color_field: &'a str,
    pub(crate) fill: CellFill<'a>,
    /// The tooltip's value title and format, or `None` when the chart paints
    /// no tooltip.
    pub(crate) tooltip: Option<(String, &'a FormatSlot)>,
    pub(crate) x_header: HeaderFmt<'a>,
    pub(crate) y_header: HeaderFmt<'a>,
    /// Vega's default description of a multi-measure cell: its x column and its measure, each `field: value`.
    pub(crate) native_aria: bool,
    pub(crate) link: Option<crate::href::DatumLink<'a>>,
    /// `axis_y.mirror`: an invisible rule layer carries each cell's label back to the origin.
    pub(crate) mirror: bool,
    /// A facet panel's rules carry the plain channel titles instead of the cell's structured label.
    pub(crate) ghost_titles: Option<(&'a str, &'a str)>,
}

/// How a cell's rect draws: its opacity and stroke, and how far the stroke grows its bounds.
pub(crate) struct CellMarks {
    pub(crate) opacity: f64,
    pub(crate) stroke: Attrs,
    pub(crate) reach: f64,
}

impl CellMarks {
    pub(crate) fn of(mark: &RectMark) -> Result<Self, String> {
        Ok(CellMarks { opacity: mark.opacity.unwrap_or(1.0), stroke: mark.stroke_attrs()?, reach: mark.stroke_reach() })
    }
}

/// The bands of a date axis that hold a date. Production reads the labels from
/// the non-null values (`_axis_label_values`), so a null band paints none and
/// takes no part in the cadence.
pub(crate) fn dated_bands(domain: &[String]) -> Vec<usize> {
    domain.iter().enumerate().filter(|(_, cell)| !cell.is_empty()).map(|(i, _)| i).collect()
}

/// The tooltip value title — `Some` when the chart paints a structured
/// tooltip (front-end contract: `_heatmap_roles`, ported -- both
/// independents (x, y) header as bare `ROLE_HEADER` rows, the color measure
/// is the sole dependent row). The title is `format_display_text(color_field,
/// ..., font=chart.legend.title.font)` UNCONDITIONALLY -- unlike
/// `legend_title_text`, it is never gated on `legend.title.visible` (the
/// tooltip renders with content the legend itself may be hiding).
///
pub(crate) fn tooltip_value_title(r: &HeatmapResolved) -> Result<Option<String>, String> {
    if r.is_flat() || !r.style.tooltip_format.as_ref().is_some_and(|f| !f.spec.is_empty()) {
        return Ok(None);
    }
    // The theme cascade guarantees every resolved chart's
    // `legend.title.font` is populated (`core/AGENTS.md`'s "Theme YAML
    // completeness") -- a caller reaching this
    // with no case is a genuine resolved-payload defect, not a
    // shape to default around (`pie.rs::pie_legend_title_case`'s same
    // argument).
    let case = r
        .legend
        .title
        .font
        .case
        .as_deref()
        .ok_or("heatmap: tooltip value title requires legend.title.font.case")?;
    crate::common::field_display_text(&r.color, &r.field_labels, case).map(Some)
}

/// A cell the data decided: its band indexes, fill and the labels and link it carries.
pub(crate) struct Cell {
    pub(crate) x: usize,
    pub(crate) y: usize,
    pub(crate) fill: String,
    /// The `aria-label` of the cell's rect.
    pub(crate) aria: Option<String>,
    pub(crate) href: Option<(String, crate::href::LinkOpen)>,
    /// What a mirrored chart's ghost rule says of the cell.
    pub(crate) ghost: Option<String>,
}

impl Cell {
    fn anchor(&self) -> Option<crate::href::Anchor<'_>> {
        self.href.as_ref().map(|(url, open)| crate::href::Anchor { url, open: *open })
    }
}

/// One [`Cell`] per row whose bands both sit in the domains, in row order.
pub(crate) fn bind_cells(
    rows: &[serde_json::Map<String, Value>],
    x_domain: &[String],
    y_domain: &[String],
    c: &CellPaint,
) -> Result<Vec<Cell>, String> {
    fn first_index(domain: &[String]) -> HashMap<&str, usize> {
        let mut index = HashMap::new();
        for (i, band) in domain.iter().enumerate() {
            index.entry(band.as_str()).or_insert(i);
        }
        index
    }
    let (x_index, y_index) = (first_index(x_domain), first_index(y_domain));
    let link_open = c.link.as_ref().map(|link| link.open);
    let mut cells = Vec::with_capacity(rows.len());
    for row in rows {
        let xv = row_str(row, c.x_field);
        let yv = row_str(row, c.y_field);
        let (Some(&x), Some(&y)) = (x_index.get(xv.as_str()), y_index.get(yv.as_str())) else { continue };
        let (fill, cv) = match c.fill {
            CellFill::Gradient { colors, min, max } => {
                let Some(cv) = row_num(row, c.color_field) else { continue };
                (colors.at(min, max, cv)?, HeatmapValue::Number(cv))
            }
            CellFill::Dates { min, max } => {
                let Some(day) = row.get(c.color_field).and_then(date_cell_day) else { continue };
                // The tooltip prints the epoch milliseconds as a number.
                (date_color(min, max, day), HeatmapValue::Number(day * MS_PER_DAY))
            }
            CellFill::Categories(scale) => {
                let null_cell = row.get(c.color_field).is_none_or(Value::is_null);
                let name = if null_cell { NULL_CATEGORY.to_string() } else { row_str(row, c.color_field) };
                if name == NULL_CATEGORY && null_cell != scale.null_is_js {
                    ("none".to_string(), HeatmapValue::Category(NULL_CATEGORY))
                } else {
                    let slot = scale
                        .domain
                        .iter()
                        .position(|d| *d == name)
                        .ok_or_else(|| format!("heatmap: color {name:?} is not in its own category domain"))?;
                    (scale.fills[slot].clone(), HeatmapValue::Category(&scale.domain[slot]))
                }
            }
        };
        let mut ghost_label = None;
        let aria = match &c.tooltip {
            Some((value_title, value_format)) => {
                let (x_head, y_head) = (c.x_header.text(&xv)?, c.y_header.text(&yv)?);
                let tt = crate::contract::HeatmapTooltip {
                    x: &x_head,
                    y: &y_head,
                    value_title,
                    value_format,
                    value: cv,
                };
                if let Some((x_title, y_title)) = c.ghost_titles {
                    let value_s = cv.text(value_format).map_err(|e| format!("heatmap: tooltip value {e}"))?;
                    ghost_label = Some(crate::contract::native_label(&[(x_title, x_head.clone()), (y_title, y_head.clone()), (value_title, value_s)]));
                }
                Some(crate::contract::heatmap_aria_label(&tt)?)
            }
            None if c.native_aria => {
                let measure = row_str(row, crate::common::WIDE_KEY_FIELD);
                Some(crate::contract::native_label(&[(c.x_field, xv.clone()), (measure.as_str(), yv.clone())]))
            }
            None => None,
        };
        let href = c.link.as_ref().map(|link| link.href(row)).transpose()?;
        let ghost = if c.mirror { ghost_label.or_else(|| aria.clone()) } else { None };
        cells.push(Cell { x, y, fill, aria, href: href.zip(link_open), ghost });
    }
    Ok(cells)
}

/// The nodes of `cells` over the band scales: a rect per cell, in its `marks` group when it
/// is described, and a mirrored chart's ghost rules after them.
pub(crate) fn cell_nodes(
    cells: &[Cell],
    x_scale: &BandScale,
    y_scale: &BandScale,
    marks: &CellMarks,
) -> Result<Vec<Node>, String> {
    use crate::contract::{aria_attrs, ghost_layer, mark_group, GhostRule, GhostRun, ItemRole};
    let (cell_w, cell_h) = (x_scale.bandwidth(), y_scale.bandwidth());
    let mut nodes = Vec::with_capacity(cells.len() + 1);
    let mut rules = Vec::new();
    for cell in cells {
        let (cx, cy) = (x_scale.position_at(cell.x), y_scale.position_at(cell.y));
        let rect = Node::Rect(
            Attrs::new()
                .set("x", format!("{cx:.2}"))
                .set("y", format!("{cy:.2}"))
                .set("width", format!("{cell_w:.2}"))
                .set("height", format!("{cell_h:.2}"))
                .set("fill", cell.fill.as_str())
                .extend(marks.stroke.clone())
                .set("opacity", marks.opacity.to_string())
                .extend(aria_attrs(cell.aria.as_deref(), ItemRole::RectMark)?),
        );
        let mark = crate::href::linked(cell.anchor(), rect);
        nodes.push(if cell.aria.is_some() { mark_group("rect", "marks", vec![mark])? } else { mark });
        if let Some(ghost) = &cell.ghost {
            rules.push(GhostRule {
                aria: Some(ghost),
                x: cx + cell_w / 2.0,
                y: cy + cell_h / 2.0,
                stroke: &cell.fill,
                anchor: cell.anchor(),
            });
        }
    }
    if !rules.is_empty() {
        nodes.push(ghost_layer(&rules, GhostRun::ToOrigin)?);
    }
    Ok(nodes)
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// A non-numeric color field is a nominal scale. Vega-Lite leaves its domain
/// unpinned, so Vega sorts it ascending (`sort: ascending`, vega-util
/// `ascending`: UTF-16 code-unit order) and the theme's category range hands
/// the palette out in that order, wrapping past its last swatch; a board
/// category-color scale binds its own slots and a null category sorts first as
/// the text `null`. A date-like field that is not a date (typed ordinal) takes
/// Vega's `ordinal` range instead: the `blues` ramp sampled at `(i + 1) / (n + 1)`.
pub(crate) fn color_categories(
    r: &HeatmapResolved,
    rows: &[serde_json::Map<String, Value>],
) -> Result<CategoryScale, String> {
    let mut null_is_js = None;
    let mut seen = std::collections::HashSet::new();
    let mut domain: Vec<String> = Vec::new();
    for row in rows {
        let null_cell = row.get(&r.color).is_none_or(Value::is_null);
        let name = if null_cell { NULL_CATEGORY.to_string() } else { row_str(row, &r.color) };
        if name == NULL_CATEGORY {
            if null_is_js.is_none() {
                null_is_js = Some(null_cell);
            }
        } else if seen.insert(name.clone()) {
            domain.push(name);
        }
    }
    if null_is_js == Some(false) {
        domain.push(NULL_CATEGORY.to_string());
    }
    domain.sort_by(|a, b| a.encode_utf16().cmp(b.encode_utf16()));
    // Vega's `ascending` ranks a real null before every value; its legend and
    // tooltip print the JS string.
    if null_is_js == Some(true) {
        domain.insert(0, NULL_CATEGORY.to_string());
    }
    let fills = if infer_vega_type(rows, &r.color) == VegaType::Ordinal {
        let n = domain.len() as f64;
        (1..=domain.len())
            .map(|i| {
                let (red, green, blue) = d3rs::color::interpolate_rgb_piecewise(&d3rs::vega_schemes::BLUES, i as f64 / (n + 1.0));
                format!("rgb({red}, {green}, {blue})")
            })
            .collect()
    } else {
        // The theme's category range wraps past its last swatch, as Vega's
        // ordinal range does.
        let scale = crate::common::category_scale_for(&r.category_colors, &r.color);
        domain
            .iter()
            .map(|name| crate::common::series_color(name, &domain, &r.series_palette, scale))
            .collect::<Result<Vec<_>, _>>()?
    };
    Ok(CategoryScale { domain, fills, null_is_js: null_is_js == Some(true) })
}

/// How Vega prints a null category.
const NULL_CATEGORY: &str = "null";

/// A rotated tick label: translate to the anchor, rotate, then the baseline shift (Vega's SVG
/// text transform for a rotated item). `at` is `(x, anchor_y, angle, baseline dy)`; `face` sets
/// the anchor and font; `(text, row2)` are its rows, and `hidden` writes `opacity="0"`.
pub(crate) fn rotated_label(
    (x, anchor_y, angle, dy): (f64, f64, f64, f64),
    face: &LabelFace,
    (text, row2): (&str, &str),
    hidden: bool,
) -> Node {
    let attrs = Attrs::new()
        .set("text-anchor", face.anchor)
        .set("transform", format!("translate({x:.p$},{anchor_y:.p$}) rotate({angle}) translate(0,{dy})", p = face.places))
        .extend(face.font())
        .set_if(hidden, "opacity", "0");
    // A second row rides the same rotated block, one line advance below.
    if row2.is_empty() {
        return Node::text(attrs, text);
    }
    let run = |attrs: Attrs, text: &str| Node::Tspan(TextBlock { attrs, text: Chars::new(text, Escape::Attr) });
    let second = Attrs::new().set("x", "0").set("dy", vega_fit::label_row_advance(face.size).to_string());
    Node::TextRuns { attrs, children: vec![run(Attrs::new(), text), run(second, row2)] }
}

/// How a tick label is set: its anchor and the face Vega writes on every `<text>`, and the
/// decimals its coordinates print with.
pub(crate) struct LabelFace<'a> {
    pub(crate) places: usize,
    pub(crate) anchor: &'a str,
    pub(crate) family: &'a str,
    pub(crate) size: f64,
    pub(crate) weight: &'a str,
    pub(crate) fill: &'a str,
}

impl LabelFace<'_> {
    fn font(&self) -> Attrs {
        Attrs::new()
            .vega("font-family", self.family)
            .set("font-size", self.size.to_string())
            .set("font-weight", self.weight)
            .set("fill", self.fill)
    }
}

/// An unrotated tick label with its baseline at `(x, y)`.
pub(crate) fn flat_label((x, y): (f64, f64), face: &LabelFace, hidden: bool, text: &str) -> Node {
    Node::text(
        Attrs::new()
            .set("x", format!("{x:.p$}", p = face.places))
            .set("y", format!("{y:.p$}", p = face.places))
            .set("text-anchor", face.anchor)
            .extend(face.font())
            .set_if(hidden, "opacity", "0"),
        text,
    )
}

/// An axis tick from `(x1, y1)` to `(x2, y2)`, its coordinates printed to `places` decimals.
pub(crate) fn tick_line(
    places: usize,
    (x1, y1): (f64, f64),
    (x2, y2): (f64, f64),
    color: &str,
    width: f64,
) -> Node {
    Node::Line(
        Attrs::new()
            .set("x1", format!("{x1:.places$}"))
            .set("y1", format!("{y1:.places$}"))
            .set("x2", format!("{x2:.places$}"))
            .set("y2", format!("{y2:.places$}"))
            .set("stroke", color)
            .set("stroke-width", width.to_string()),
    )
}

/// SVG `text-anchor` for a Vega `align`.
pub(crate) fn svg_text_anchor(align: &str) -> &'static str {
    match align {
        "left" => "start",
        "right" => "end",
        _ => "middle",
    }
}

/// The gradient's color domain and its legend's labeled ticks — ONE list
/// feeds both, so a painted cell can never disagree with the value printed
/// beside it (`emitters/_channels.py`: `_nice_domain_ticks`,
/// `gradient_scale_to_vl`, `apply_gradient_legend_endpoint_labels`).
///
/// An authored `min` AND `max` win outright. A single-sided bound is honored
/// as that exact edge with the raw data extent for the free one — Vega
/// compiles it to `domainMin`/`domainMax` and nices neither side (probed on
/// the compiled scale). `nice: false` is the author's opt-out and keeps the
/// raw extent with two endpoint labels. Otherwise the domain widens to
/// `nice_tick_values(min, max, nice_tick_count)` and EVERY rung is labeled.
pub(crate) fn gradient_ladder(
    rows: &[serde_json::Map<String, Value>],
    field: &str,
    gradient: &ColorGradient,
) -> Result<(f64, f64, Vec<f64>), Diagnostic> {
    // `is_vega_numeric_value`: a number or a boolean; a numeric string is nominal.
    let vega_numeric = |row: &serde_json::Map<String, Value>| match row.get(field)? {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    };
    let (data_lo, data_hi) =
        rows.iter().filter_map(vega_numeric).fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
    if !data_lo.is_finite() {
        // `emitters/heatmap.py`: a quantitative color column with no number to span.
        return Err(Diagnostic::new(&codes::ERR_HEATMAP_COLOR_NO_VALUES, [("field", Value::from(field))]));
    }
    if let (Some(lo), Some(hi)) = (gradient.min, gradient.max) {
        return Ok((lo, hi, vec![lo, hi]));
    }
    if gradient.nice && gradient.min.is_none() && gradient.max.is_none() {
        let ticks = d3rs::ticks::nice_tick_values(data_lo, data_hi, GRADIENT_NICE_TICK_COUNT, None);
        return Ok((ticks[0], ticks[ticks.len() - 1], ticks));
    }
    let lo = gradient.min.unwrap_or(data_lo);
    let hi = gradient.max.unwrap_or(data_hi);
    Ok((lo, hi, vec![lo, hi]))
}

const MS_PER_DAY: f64 = 86_400_000.0;

/// A date cell's day number. `None` for a cell Vega-Lite filters out of a
/// grid: null, or an invalid date. A number is epoch milliseconds, as `new Date(n)` reads it.
pub(crate) fn date_cell_day(cell: &Value) -> Option<f64> {
    match cell {
        Value::String(text) => crate::js_date::parse_days(text),
        Value::Number(ms) => ms.as_f64().filter(|ms| ms.abs() <= 8.64e15).map(|ms| ms / MS_PER_DAY),
        _ => None,
    }
}

/// A time scale's domain is the raw date extent (it is never niced); a column
/// that is all one date has a zero-width domain.
pub(crate) fn date_color_extent(rows: &[serde_json::Map<String, Value>], field: &str) -> Result<(f64, f64), String> {
    let days = rows.iter().filter_map(|r| date_cell_day(r.get(field)?));
    let (min, max) = days.fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), d| (lo.min(d), hi.max(d)));
    if min > max {
        return Err(format!("heatmap: the date color {field:?} holds no date"));
    }
    Ok((min, max))
}

/// Vega's `heatmap` color range (`yellowgreenblue`) on a two-value domain: the
/// scheme sampled at `1/3` and `2/3` (`quantize(scheme, 2)`), then mixed in HCL.
/// A zero-width domain maps every date to the middle.
pub(crate) fn date_scale(min: f64, max: f64) -> ContinuousColor {
    ContinuousColor::dates(&d3rs::vega_schemes::YELLOWGREENBLUE, min, max)
}

/// A date cell's fill on [`date_scale`].
pub(crate) fn date_color(min: f64, max: f64, day: f64) -> String {
    date_scale(min, max).fill(day)
}

/// The scale's ticks over `[min, max]` for `count` target ticks, in days. A
/// zero-width domain has its one date as the only tick.
fn date_ticks(min: f64, max: f64, count: usize) -> Result<Vec<f64>, String> {
    if min == max {
        return Ok(vec![min]);
    }
    Ok(crate::families::temporal::utc_instants(min, max, count))
}

/// The labeled dates of a time-scale legend over a gradient `length` px long:
/// an authored `legend.values` (a date JS cannot parse is `NaN`, drawn as
/// `Date.parse` leaves it), else `max(2, 2 * floor(length / 100))` target
/// ticks, or the two ends when fewer than three (`LegendEntries`).
pub(crate) fn date_legend_values(min: f64, max: f64, plot_h: f64, authored: Option<&[String]>) -> Result<Vec<f64>, String> {
    date_legend_ticks(min, max, gradient_tick_count(gradient_length(plot_h)), authored)
}

/// A date legend's labeled days for `count` target ticks.
pub(crate) fn date_legend_ticks(min: f64, max: f64, count: usize, authored: Option<&[String]>) -> Result<Vec<f64>, String> {
    match authored {
        Some(values) => Ok(values.iter().map(|v| crate::js_date::parse_days(v).unwrap_or(f64::NAN)).collect()),
        None if min == max => Ok(vec![min]),
        None => Ok(gradient_labeled_values(date_ticks(min, max, count)?, min, max)),
    }
}

/// Vega's date legend label, `%b %d, %Y`; a `NaN` date prints as d3 formats
/// an invalid one.
pub(crate) fn legend_date_label(day: f64) -> Result<String, String> {
    use crate::families::temporal::{civil_from_days, vl_default_format, TimeUnit};
    if day.is_nan() {
        return Ok("NaN, 0NaN".to_string());
    }
    let (y, m, d) = civil_from_days(day.floor() as i64);
    vl_default_format(y, m, d, TimeUnit::YearMonthDate)
}

/// The gradient of a time-scale legend over `[min, max]` days.
pub(crate) fn date_gradient_def(id: &str, min: f64, max: f64) -> Result<Node, String> {
    let ticks = date_ticks(min, max, GRADIENT_STOP_TICKS)?;
    Ok(continuous_gradient_def(id, &date_scale(min, max), &ticks, true))
}

/// The resolved stops interpolated piecewise in sRGB (`d3.interpolateRgb`),
/// printed the way d3 formats a color.
pub(crate) fn interp_color(stops: &[String], domain_min: f64, domain_max: f64, value: f64) -> Result<String, String> {
    let colors = stops
        .iter()
        .map(|stop| d3rs::color::parse_hex(stop).ok_or_else(|| format!("heatmap: gradient stop {stop:?} is not a #rrggbb color")))
        .collect::<Result<Vec<_>, _>>()?;
    let t = unit_position(value, domain_min, domain_max).clamp(0.0, 1.0);
    Ok(d3rs::color::rgb_text(d3rs::color::interpolate_rgb_piecewise(&colors, t)))
}

/// The gradient legend's labels. Vega labels a diverging pivot that is no tick
/// at the ladder's tick precision, whatever the tooltip format.
pub(crate) fn legend_labels(ticks: &[f64], spec: Option<&str>, scale: Option<&ContinuousColor>) -> Result<Vec<String>, String> {
    ticks
        .iter()
        .map(|&v| match scale {
            Some(scale) if scale.pivot == Some(v) && !d3rs::ticks::d3_ticks(scale.lo, scale.hi, GRADIENT_NICE_TICK_COUNT).contains(&v) => {
                let step = d3rs::ticks::d3_tick_step(scale.lo, scale.hi, GRADIENT_NICE_TICK_COUNT);
                crate::number_locale::d3_format(&crate::number_locale::vega_tick_spec(step), v)
            }
            _ => format_legend_value(v, spec),
        })
        .collect()
}

/// Format a legend value using the payload's d3 spec, or fall back to comma-integer.
pub(crate) fn format_legend_value(v: f64, spec: Option<&str>) -> Result<String, String> {
    match spec {
        Some(s) => crate::number_locale::d3_format(s, v)
            .map_err(|e| format!("heatmap legend format {s:?}: {e}")),
        None => crate::number_locale::d3_format(",d", v)
            .map_err(|e| format!("heatmap legend format ',d': {e}")),
    }
}


#[cfg(test)]
mod tests {
    use super::{render, row_str, RectMark};
    use crate::families::legend::unit_position;
    use crate::families::temporal::TimeUnit;

    #[test]
    fn a_zero_width_color_domain_maps_to_the_middle() {
        assert_eq!(unit_position(5.0, 5.0, 5.0), 0.5);
        assert_eq!(unit_position(5.0, 0.0, 10.0), 0.5);
        assert_eq!(unit_position(0.0, 0.0, 10.0), 0.0);
    }

    fn stroke_attr(mark: &RectMark) -> String {
        print::attributes(&mark.stroke_attrs().unwrap())
    }

    #[test]
    fn a_rect_stroke_object_paints_its_color_and_width() {
        let mark: RectMark = serde_json::from_value(serde_json::json!({"stroke": {"color": "#000000", "width": 1.0}})).unwrap();
        assert_eq!(stroke_attr(&mark), " stroke=\"#000000\" stroke-width=\"1\"");
        assert_eq!(mark.stroke_reach(), 1.0);
        assert_eq!(stroke_attr(&RectMark::default()), "");
        assert_eq!(RectMark::default().stroke_reach(), 0.0);
    }

    /// Vega writes the cap, join and dash after the width, the dash as
    /// comma-joined numbers; none of them paints without a stroke color.
    #[test]
    fn a_rect_stroke_paints_its_cap_join_and_dash() {
        let styled: RectMark = serde_json::from_value(serde_json::json!({
            "stroke": {"color": "#000", "width": 2.0, "cap": "round", "join": "bevel", "dasharray": "4 2.5"}
        }))
        .unwrap();
        assert_eq!(
            stroke_attr(&styled),
            " stroke=\"#000\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"bevel\" stroke-dasharray=\"4,2.5\""
        );
        let colorless: RectMark = serde_json::from_value(serde_json::json!({"stroke": {"width": 2.0, "dasharray": "4 2"}})).unwrap();
        assert_eq!(stroke_attr(&colorless), "");
        let hidden: RectMark = serde_json::from_value(serde_json::json!({
            "opacity": 0.0, "stroke": {"color": "#000", "width": 2.0}
        }))
        .unwrap();
        assert_eq!((stroke_attr(&hidden).as_str(), hidden.stroke_reach()), ("", 0.0));
    }

    use measure::FontBook;
    use crate::Payload;
    use super::{GradientColors, HeatmapResolved};
    use crate::families::heatmap_facet::GradientLegend;
    use serde_json::{json, Value};

    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    /// `wrap_axis_title` measures the title at its painted weight
    /// (`_cartesian.py:610`; 600 at the theme default), so a title that fits its
    /// budget at 400 can still wrap.
    #[test]
    fn a_title_that_fits_regular_but_wraps_at_its_painted_weight() {
        let fonts = test_fonts();
        let text = "Net revenue retention by customer segment and sales region";
        let width_at = |weight: f32| fonts.measure_at_weight(text, "Inter", 11.0, Some(weight)).unwrap();
        let (regular, painted) = (width_at(400.0), width_at(600.0));
        assert!(painted - regular > 2.0, "{regular} vs {painted}");
        let extent = crate::families::bar::CHROME_PX + ((regular + painted) / 2.0).floor();
        let lines = |weight: Value| {
            let font = serde_json::from_value(json!({"family": TEST_FONT, "size": 11.0, "weight": weight}))
                .expect("font parses");
            crate::common::wrap_axis_title(text, extent, 1.0, &font, &fonts).expect("wraps")
        };
        assert_eq!(lines(json!("400")), (vec![text.to_string()], false));
        let (wrapped, truncated) = lines(json!(600.0));
        assert_eq!(wrapped.len(), 2, "{wrapped:?}");
        assert!(!truncated);
        assert_eq!(lines(json!("semibold")).0.len(), 1, "an unparseable weight paints at the default");
    }

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn measure(s: &str, size: f64) -> f64 {
        test_fonts().measure(s, TEST_FONT, size).expect("measures")
    }

    fn max_measure(labels: &[&str], size: f64) -> f64 {
        labels.iter().fold(0.0_f64, |acc, s| acc.max(measure(s, size)))
    }

    /// What Vega measures for itself — the legend box, never the axis gutter.
    fn measure_shaped(s: &str, size: f64) -> f64 {
        test_fonts().measure_shaped(s, TEST_FONT, size).expect("measures")
    }

    fn max_measure_shaped(labels: &[&str], size: f64) -> f64 {
        labels.iter().fold(0.0_f64, |acc, s| acc.max(measure_shaped(s, size)))
    }

    fn axis_json(ticks_visible: bool, align: Option<&str>) -> Value {
        json!({
            "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#EDEFF2", "opacity": 1.0},
            "ticks": {"visible": ticks_visible, "count": null, "length": 6.0, "offset": 1.0,
                      "color": "#EDEFF2", "width": 1.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400",
                         "case": "none"},
                "padding": 6.0, "min_gap": 16.0, "format": null, "visible": true,
                "align": align, "angle": null, "max_width": null,
                "overlap": {"skip": true, "tilt": true},
                "tilt_increments": [0.0, -30.0, -45.0, -60.0, -90.0]
            },
            "title": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#8A8C8F"},
                "padding": 10.0, "visible": false
            },
            "position": null,
            "mirror": null, "band_position": null, "scale": {},
            "tick_values": [], "domain_max": null, "domain_min": null,
            "ruler": null, "tick_label": null,
            "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": null, "line": null
        })
    }

    /// The corpus heatmap (`charts/more-types_0/heatmap_example`) in payload
    /// form: 4 regions × 3 categories, revenue 14 824 … 58 396, a right
    /// gradient legend and a Vega title. The color ramp is reduced to a
    /// black→white pair so a cell's fill reads the domain directly.
    fn heatmap_payload(mutate: impl FnOnce(&mut Value)) -> Value {
        let rows = json!([
            {"region": "East",  "category": "Accessories", "revenue": 47368},
            {"region": "East",  "category": "Electronics", "revenue": 58396},
            {"region": "East",  "category": "Tools",       "revenue": 14824},
            {"region": "North", "category": "Accessories", "revenue": 34968},
            {"region": "North", "category": "Electronics", "revenue": 40565},
            {"region": "North", "category": "Tools",       "revenue": 22859},
            {"region": "South", "category": "Accessories", "revenue": 38244},
            {"region": "South", "category": "Electronics", "revenue": 53637},
            {"region": "South", "category": "Tools",       "revenue": 20865},
            {"region": "West",  "category": "Accessories", "revenue": 31924},
            {"region": "West",  "category": "Electronics", "revenue": 44601},
            {"region": "West",  "category": "Tools",       "revenue": 34772}
        ]);
        let mut json = json!({
            "resolved": {
                "chart_type": "heatmap",
                "id": "revenue_chart",
                "title": "Revenue by Region and Category",
                "subtitle": "",
                "title_style": {
                    "font": {"family": TEST_FONT, "size": 18.0, "weight": "500",
                             "color": "#494A4C", "case": "none"},
                    "position": {"anchor": "start", "offset": 10.0},
                    "subtitle": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400",
                                          "color": "#8A8C8F"}}
                },
                "background": "#ffffff",
                "field_labels": {}, "x": "region", "y": "category", "color": "revenue",
                "multiples": null, "resolved_channels": {}, "layers": [],
                "legend": {
                    "visible": true, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                    "title": {"font": {"case": "title", "family": TEST_FONT, "size": 12.0, "weight": "600",
                                       "color": "#313233", "case": "title"}},
                    "label": {"font": {"family": TEST_FONT, "size": 11.0, "weight": "400",
                                       "color": "#626366"}, "padding": 8.0}
                },
                "style": {
                    "axis_x": axis_json(true, None),
                    "axis_y": axis_json(false, Some("left")),
                    "color_gradient": {
                        "resolved_stops": ["#000000", "#ffffff"],
                        "min": null, "max": null, "nice": true
                    },
                    "rect_mark": {},
                    "tooltip_format": ",.2~f",
                    "label_usable_ratio": 0.8
                }
            },
            "rows": rows,
            "width": 600.0,
            "height": 432.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        });
        mutate(&mut json);
        json
    }

    fn render_json(json: Value) -> String {
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("heatmap renders")
    }

    #[test]
    fn a_field_label_titles_the_legend_and_the_tooltip_value_row() {
        let render_with = |labels: Value| {
            render_json(heatmap_payload(|json| {
                json["resolved"]["field_labels"] = labels;
            }))
        };
        let plain = render_with(json!({}));
        let labeled = render_with(json!({"revenue": "Sales volume"}));
        assert!(!plain.contains("Sales volume"));
        assert!(labeled.matches("Sales volume").count() >= 2, "{labeled}");
    }

    fn assert_label_order(svg: &str, names: &[&str]) {
        let pos: Vec<usize> = names
            .iter()
            .map(|n| svg.find(&format!(">{n}<")).unwrap_or_else(|| panic!("{n} label missing: {svg}")))
            .collect();
        assert!(pos.windows(2).all(|w| w[0] < w[1]), "expected {names:?}, positions {pos:?}");
    }

    /// `_write_dimension_sort` reaches BOTH dimensions identically, so the
    /// authored sort here is by `seq` (1:1 with `month`, x); `series` (y)
    /// ties at MIN(seq)=1 for both alpha and gamma and keeps first-
    /// occurrence order (alpha, gamma) regardless of direction — the same
    /// distinguishing shape `fixtures/authored-sort-x-order.yml` uses.
    fn sort_heatmap_payload(sort: Value) -> Value {
        heatmap_payload(|json| {
            json["resolved"]["x"] = json!("month");
            json["resolved"]["y"] = json!("series");
            json["resolved"]["color"] = json!("revenue");
            json["resolved"]["sort"] = sort;
            json["rows"] = json!([
                {"month": "Feb", "seq": 2, "series": "alpha", "revenue": 200.0},
                {"month": "Feb", "seq": 2, "series": "gamma", "revenue": 120.0},
                {"month": "Mar", "seq": 3, "series": "alpha", "revenue": 50.0},
                {"month": "Jan", "seq": 1, "series": "alpha", "revenue": 100.0},
                {"month": "Jan", "seq": 1, "series": "gamma", "revenue": 60.0}
            ]);
        })
    }

    /// No authored sort: `_write_dimension_sort` pins `sort: null` on both
    /// dimensions, so each band domain is the rows' first-appearance order
    /// (Vega-Lite's `domainSort` returns `undefined` for `sort == null`;
    /// `valuesRef` then keeps Vega's aggregate encounter order), never the
    /// lexical order a nominal scale falls back to when `sort` is absent.
    #[test]
    fn heatmap_without_an_authored_sort_keeps_query_row_order_on_both_bands() {
        let svg = render_json(heatmap_payload(|json| {
            json["resolved"]["x"] = json!("day");
            json["resolved"]["y"] = json!("region");
            json["resolved"]["color"] = json!("revenue");
            json["rows"] = json!([
                {"day": "Mon", "region": "West", "revenue": 10.0},
                {"day": "Tue", "region": "West", "revenue": 20.0},
                {"day": "Wed", "region": "North", "revenue": 30.0},
                {"day": "Thu", "region": "North", "revenue": 40.0},
                {"day": "Fri", "region": "East", "revenue": 50.0}
            ]);
        }));
        assert_label_order(&svg, &["Mon", "Tue", "Wed", "Thu", "Fri"]);
        assert_label_order(&svg, &["West", "North", "East"]);
    }

    #[test]
    fn heatmap_honors_an_authored_ascending_sort() {
        let json = sort_heatmap_payload(json!({"by": "seq", "order": "asc"}));
        let svg = render_json(json);
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
        assert_label_order(&svg, &["alpha", "gamma"]);
    }

    #[test]
    fn heatmap_honors_an_authored_descending_sort() {
        let json = sort_heatmap_payload(json!({"by": "seq", "order": "desc"}));
        let svg = render_json(json);
        assert_label_order(&svg, &["Mar", "Feb", "Jan"]);
        assert_label_order(&svg, &["alpha", "gamma"]);
    }

    /// A sort column of numbers and text makes Python's sort raise, which
    /// `x_domain_order` answers with row order — see
    /// `common::dimension_sort_domain`'s doc comment.
    #[test]
    fn heatmap_authored_sort_over_numbers_and_text_keeps_row_order() {
        let mut json = sort_heatmap_payload(json!({"by": "seq", "order": "asc"}));
        json["rows"] = json!([
            {"month": "Feb", "seq": "second", "series": "alpha", "revenue": 200.0},
            {"month": "Jan", "seq": 1, "series": "alpha", "revenue": 100.0}
        ]);
        let svg = render_json(json);
        assert_label_order(&svg, &["Feb", "Jan"]);
    }

    /// A NUMERIC-LOOKING string ("9", "2", "10") is text under MIN —
    /// production's real `min` orders a JSON string LEXICOGRAPHICALLY, never
    /// by parsing it as a number first, so "10" ranks before "2" and "9".
    #[test]
    fn heatmap_authored_sort_ranks_numeric_looking_text_lexicographically() {
        let mut json = sort_heatmap_payload(json!({"by": "seq", "order": "asc"}));
        json["rows"] = json!([
            {"month": "Feb", "seq": "9", "series": "alpha", "revenue": 200.0},
            {"month": "Mar", "seq": "2", "series": "alpha", "revenue": 50.0},
            {"month": "Jan", "seq": "10", "series": "alpha", "revenue": 100.0}
        ]);
        let svg = render_json(json);
        assert_label_order(&svg, &["Jan", "Mar", "Feb"]);
    }

    /// An unranked category (no `sort.by` value on any row) trails
    /// the ranked ones in BOTH directions — production's `x_domain_order`'s
    /// own `ranked + unranked` return, never a direction-dependent
    /// placement. Query order is (Feb, Mar[unranked], Jan).
    fn sort_heatmap_payload_with_one_unranked_category(order: &str) -> Value {
        heatmap_payload(|json| {
            json["resolved"]["x"] = json!("month");
            json["resolved"]["y"] = json!("series");
            json["resolved"]["color"] = json!("revenue");
            json["resolved"]["sort"] = json!({"by": "seq", "order": order});
            json["rows"] = json!([
                {"month": "Feb", "seq": 2, "series": "alpha", "revenue": 200.0},
                {"month": "Mar", "series": "alpha", "revenue": 50.0},
                {"month": "Jan", "seq": 1, "series": "alpha", "revenue": 100.0}
            ]);
        })
    }

    #[test]
    fn heatmap_authored_sort_trails_an_unranked_category_ascending() {
        let svg = render_json(sort_heatmap_payload_with_one_unranked_category("asc"));
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
    }

    #[test]
    fn heatmap_authored_sort_trails_an_unranked_category_descending() {
        let svg = render_json(sort_heatmap_payload_with_one_unranked_category("desc"));
        assert_label_order(&svg, &["Feb", "Jan", "Mar"]);
    }

    fn attr(tag: &str, name: &str) -> f64 {
        let key = format!("{name}=\"");
        let rest = tag.split(&key).nth(1).unwrap_or_else(|| panic!("no {name} in {tag}"));
        rest.split('"').next().unwrap().parse().unwrap_or_else(|_| panic!("{name} in {tag}"))
    }

    /// Every `<text>` tag whose content is exactly `body`.
    fn text_tag(svg: &str, body: &str) -> String {
        let needle = format!(">{body}</text>");
        let end = svg.find(&needle).unwrap_or_else(|| panic!("no text {body:?} in {svg}"));
        let start = svg[..end].rfind("<text").unwrap();
        svg[start..end].to_string()
    }

    /// `(plot_left, plot_top)` — the cell group's translate.
    fn plot_origin(svg: &str) -> (f64, f64) {
        let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
        let inner = t.split(')').next().unwrap();
        let (a, b) = inner.split_once(',').unwrap();
        (a.parse().unwrap(), b.parse().unwrap())
    }

    /// The drawn cell rects, `(x, y, w, h)`.
    /// A cell rect is the only element whose `fill` is `rgb(...)`
    /// (`interp_color`'s own output format) -- distinguishes it from the
    /// legend's gradient bar (`fill="url(#...)"`) or the background rect
    /// (`fill="#..."`) without relying on group nesting depth, which now
    /// varies per rect (wrapped in its own `.role-mark` group when the
    /// structured tooltip is on, unwrapped otherwise).
    fn cells(svg: &str) -> Vec<(f64, f64, f64, f64)> {
        svg.match_indices("<rect ")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|tag| tag.contains("fill=\"rgb("))
            .map(|tag| (attr(tag, "x"), attr(tag, "y"), attr(tag, "width"), attr(tag, "height")))
            .collect()
    }

    /// The measured left gutter the own-side `labels.align: left` law bakes:
    /// `max_label_width + 4` (`utils.py::_BREATHING_ROOM_PX`).
    fn own_side_padding() -> f64 {
        max_measure(&["Accessories", "Electronics", "Tools"], 11.0) + 4.0
    }

    /// `ceil(max(title_width, 16 + labelOffset + max_label_width))`, on the
    /// SHAPED widths Vega measures the box with.
    fn legend_box_w() -> f64 {
        let entries = 16.0 + 8.0
            + max_measure_shaped(&["10,000", "20,000", "30,000", "40,000", "50,000", "60,000"], 11.0);
        measure_shaped("Revenue", 12.0).max(entries).ceil()
    }

    /// Same law as `bar.rs`'s canvas-rect test — see
    /// `HeatmapResolved::background`.
    #[test]
    fn canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut json = heatmap_payload(|_| {});
        json["resolved"]["background"] = serde_json::json!("#d9a45b");
        json["background"] = serde_json::json!("#ffffff");
        let svg = render_json(json);
        assert!(svg.contains("<rect width=\"600\" height=\"432\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// H1 — the color scale's domain and the gradient legend's ladder are
    /// one `nice_tick_values(min, max, 6)` list (`GRADIENT_NICE_TICK_COUNT`). At five rungs the same extent gives step
    /// 20 000 (`0/20,000/40,000/60,000`) and paints every cell differently.
    #[test]
    fn the_color_domain_and_the_legend_ladder_share_the_six_rung_tick_list() {
        let svg = render_json(heatmap_payload(|_| {}));
        for v in ["10,000", "20,000", "30,000", "40,000", "50,000", "60,000"] {
            assert!(svg.contains(&format!(">{v}</text>")), "legend missing {v}: {svg}");
        }
        assert!(!svg.contains(">0</text>"), "five-rung ladder leaked a 0 rung: {svg}");
        // 47,368 over [10 000, 60 000] is t = .74736 → 191 on a black→white
        // ramp; over the five-rung [0, 60 000] it would be .78947 → 201.
        assert!(svg.contains("fill=\"rgb(191, 191, 191)\""), "wrong domain: {svg}");
    }

    /// An authored `min` AND `max` win outright — no nicing, two labels.
    #[test]
    fn an_authored_min_and_max_replace_the_ladder() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["color_gradient"]["min"] = json!(5000.0);
            j["resolved"]["style"]["color_gradient"]["max"] = json!(55000.0);
        }));
        assert!(svg.contains(">5,000</text>") && svg.contains(">55,000</text>"), "{svg}");
        for v in ["10,000", "20,000", "60,000"] {
            assert!(!svg.contains(&format!(">{v}</text>")), "nice rung {v} survived: {svg}");
        }
    }

    /// A single-sided bound bypasses nicing entirely: that edge exactly, the
    /// raw data extent for the free one.
    #[test]
    fn a_single_sided_bound_bypasses_nicing() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["color_gradient"]["min"] = json!(5000.0);
        }));
        assert!(svg.contains(">5,000</text>") && svg.contains(">58,396</text>"), "{svg}");
        assert!(!svg.contains(">60,000</text>"), "nice widening leaked: {svg}");
    }

    /// `nice: false` is the author's opt-out — raw extent, two endpoint
    /// labels.
    #[test]
    fn nice_false_labels_the_raw_data_extent() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["color_gradient"]["nice"] = json!(false);
        }));
        assert!(svg.contains(">14,824</text>") && svg.contains(">58,396</text>"), "{svg}");
        assert!(!svg.contains(">10,000</text>"), "nice ladder survived: {svg}");
    }

    /// H2 + H3 + H4 — the plot rect is `vega_fit`'s negotiation over the
    /// measured y-label gutter, the x-label band and the legend box, not a
    /// hand-rolled margin block. The y labels here are wide enough
    /// (`Accessories` ≈ 64px) that the gutter, not the theme's 6px padding,
    /// decides the left margin.
    #[test]
    fn the_plot_rect_is_the_autosize_fit_negotiation() {
        let svg = render_json(heatmap_payload(|_| {}));
        let left = 16.0 + own_side_padding().ceil();
        let right = 16.0 + 18.0 + legend_box_w();
        let plot_w = 600.0 - left - right;
        // Title 18px + offset 10 above; x band = offset 1 + tick 6 +
        // padding 6 + one 11px label row below.
        let plot_h = 432.0 - 32.0 - 28.0 - 24.0;
        assert_eq!(plot_origin(&svg), (left, 44.0), "{svg}");
        let cells = cells(&svg);
        assert_eq!(cells.len(), 12, "{svg}");
        assert!((cells[0].2 - plot_w / 4.0).abs() < 0.01, "cell w {:?} vs {plot_w}: {svg}", cells[0]);
        assert!((cells[0].3 - plot_h / 3.0).abs() < 0.01, "cell h {:?}: {svg}", cells[0]);
    }

    /// H3 — an own-side `labels.align: left` left-anchors each row label at
    /// `−(tick + max_width + 4)`, growing back toward the plot; the axis
    /// group's own 0.5 translate rides along.
    #[test]
    fn own_side_y_labels_left_anchor_at_the_measured_padding() {
        let svg = render_json(heatmap_payload(|_| {}));
        let (plot_left, plot_top) = plot_origin(&svg);
        let tag = text_tag(&svg, "Accessories");
        assert!(tag.contains("text-anchor=\"start\""), "{tag}");
        assert!((attr(&tag, "x") - (plot_left + 0.5 - own_side_padding())).abs() < 0.01, "{tag}");
        // Band center + Vega's `middle` baseline shift, round(0.30 · 11) = 3.
        let cell_h = cells(&svg)[0].3;
        assert!((attr(&tag, "y") - (plot_top + cell_h / 2.0 + 3.0)).abs() < 0.01, "{tag}");
    }

    /// Without an own-side align production drops the measurement and takes
    /// Vega-Lite's away-side default: labels right-anchored at
    /// `−labelPadding`, the gutter their full width wider.
    #[test]
    fn an_away_side_y_axis_keeps_vega_lite_s_own_gutter() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_y"]["labels"]["align"] = Value::Null;
        }));
        let (plot_left, _) = plot_origin(&svg);
        let max_w = max_measure(&["Accessories", "Electronics", "Tools"], 11.0);
        assert_eq!(plot_left, 16.0 + (6.0 + max_w).ceil(), "{svg}");
        let tag = text_tag(&svg, "Accessories");
        assert!(tag.contains("text-anchor=\"end\""), "{tag}");
        assert!((attr(&tag, "x") - (plot_left + 0.5 - 6.0)).abs() < 0.01, "{tag}");
    }

    /// H6 — the gradient's end labels flip baseline (bottom-most `bottom`,
    /// top-most `top`, interior `middle`); every one sits at
    /// `16 + labelOffset` from the bar.
    #[test]
    fn the_gradient_end_labels_flip_their_baseline() {
        let svg = render_json(heatmap_payload(|_| {}));
        let (plot_left, plot_top) = plot_origin(&svg);
        let cells = cells(&svg);
        let plot_w = cells[0].2 * 4.0;
        let legend_x = plot_left + plot_w.ceil() + 18.0;
        // Title band = titleFontSize + Vega's titlePadding 5.
        let grad_top = plot_top + 12.0 + 5.0;
        let grad_len = 200.0_f64;
        for (body, t, shift) in
            [("10,000", 0.0, -2.0), ("30,000", 0.4, 3.0), ("60,000", 1.0, 9.0)]
        {
            let tag = text_tag(&svg, body);
            assert!(tag.contains("text-anchor=\"start\""), "{tag}");
            assert!((attr(&tag, "x") - (legend_x + 16.0 + 8.0)).abs() < 0.01, "{tag}");
            let want = grad_top + grad_len * (1.0 - t) + shift;
            assert!((attr(&tag, "y") - want).abs() < 0.01, "{body} at {} want {want}: {tag}", attr(&tag, "y"));
        }
    }

    /// The gradient `<linearGradient>` id -- the one id this family mints
    /// -- carries the chart's own namespace suffix, and the legend bar's
    /// `url(#...)` reference resolves to that exact id (never the bare
    /// `heat_grad`, which would collide across two heatmaps on one board).
    #[test]
    fn the_gradient_id_is_namespaced_to_the_chart() {
        let svg = render_json(heatmap_payload(|j| j["resolved"]["id"] = json!("revenue_chart")));
        assert!(svg.contains("id=\"heat_grad-revenue_chart\""), "{svg}");
        assert!(svg.contains("url(#heat_grad-revenue_chart)"), "{svg}");
        assert!(!svg.contains("id=\"heat_grad\""), "bare id must not survive: {svg}");
    }

    /// H5 — the gradient bar is `16 × clamp(plot_h, 64, 200)`: it shrinks
    /// with a short plot and stops at 64 (probed at spec heights 700 → 200,
    /// 200 → 116, 140 → 64).
    #[test]
    fn the_gradient_bar_length_clamps_to_the_plot_height() {
        let bar_h = |h: f64| {
            let svg = render_json(heatmap_payload(|j| j["height"] = json!(h)));
            let i = svg.find("url(#heat_grad").unwrap();
            let tag = svg[..i].rfind("<rect ").map(|s| svg[s..i].to_string()).unwrap();
            attr(&tag, "height")
        };
        assert_eq!(bar_h(432.0), 200.0);
        assert_eq!(bar_h(200.0), 116.0);
        assert_eq!(bar_h(140.0), 64.0);
    }

    /// The legend box joins the fit HORIZONTALLY only. At a plot short
    /// enough that the box overhangs the x-label band the bottom margin
    /// still comes off the labels: probed at spec height 140, where Vega
    /// negotiates a 56px plot under an 81px legend box (17px title band +
    /// the gradient's 64px floor) — a 25px overhang the fit ignores.
    #[test]
    fn the_legend_box_never_deepens_the_bottom_margin() {
        let svg = render_json(heatmap_payload(|j| j["height"] = json!(140.0)));
        // 140 − 32 padding − 28 title band − 24 x-label band.
        assert_eq!(plot_origin(&svg).1, 44.0, "{svg}");
        // The x axis group rides the plot's bottom edge, `+ offset + 0.5`.
        let line = svg.split("<line ").nth(1).unwrap().split("/>").next().unwrap();
        assert_eq!(attr(line, "y1"), 44.0 + 56.0 + 1.0 + 0.5, "{svg}");
    }

    /// H7 — Vega translates each axis group by `config.axis.translate` 0.5:
    /// the x labels' baseline lands at `ph + offset + 0.5 + tick +
    /// padding + round(0.79 · fs)`, and every tick snaps to `floor(center)
    /// + 0.5` (probed over 64 band centers at widths 600–615).
    #[test]
    fn the_x_axis_takes_vega_s_half_pixel_translate() {
        let svg = render_json(heatmap_payload(|_| {}));
        let (plot_left, plot_top) = plot_origin(&svg);
        let cells = cells(&svg);
        let (cell_w, cell_h) = (cells[0].2, cells[0].3);
        let plot_bottom = plot_top + cell_h * 3.0;
        let tag = text_tag(&svg, "East");
        assert!((attr(&tag, "y") - (plot_bottom + 1.0 + 0.5 + 6.0 + 6.0 + 9.0)).abs() < 0.01, "{tag}");
        assert!((attr(&tag, "x") - (plot_left + cell_w / 2.0)).abs() < 0.01, "{tag}");
        // Every tick snaps in PLOT coordinates, before the group translate —
        // `floor`, not `round`: the four centers here are 52.375 / 157.125 /
        // 261.875 / 366.625, and rounding would put the last two a pixel out.
        for (i, chunk) in svg.split("<line ").skip(1).enumerate() {
            let line = chunk.split("/>").next().unwrap();
            assert!((attr(line, "y1") - (plot_bottom + 1.0 + 0.5)).abs() < 0.01, "{line}");
            assert!((attr(line, "y2") - (plot_bottom + 1.0 + 0.5 + 6.0)).abs() < 0.01, "{line}");
            let center = cell_w * (i as f64 + 0.5);
            assert!((attr(line, "x1") - (plot_left + center.floor() + 0.5)).abs() < 0.01, "{line}");
        }
    }

    /// An upper/lower `font.case` renders through a `labelExpr` production
    /// cannot measure against, so the own-side align falls back to the
    /// away-side gutter even though `labels.align` still says `left`; the
    /// gutter holds the cased text that paints.
    #[test]
    fn a_cased_label_falls_back_to_the_away_side_gutter_of_its_painted_text() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_y"]["labels"]["font"]["case"] = json!("upper");
        }));
        let max_w = max_measure(&["ACCESSORIES", "ELECTRONICS", "TOOLS"], 11.0);
        assert_eq!(plot_origin(&svg).0, 16.0 + (6.0 + max_w).ceil(), "{svg}");
        assert!(text_tag(&svg, "ACCESSORIES").contains("text-anchor=\"end\""), "{svg}");
    }

    /// The lift onto `wrap_title_block` makes production's title-overflow
    /// pass reachable here: heatmap is a plain unit spec, so
    /// `_render_vl_artifact` runs `apply_title_overflow_to_spec` over it and
    /// the compiled spec carries that pass's `title.limit`. A title past the
    /// limit wraps to two lines, and the fit reserves the extra
    /// `fontSize + 2` line advance for it.
    #[test]
    fn a_title_past_the_overflow_limit_wraps_to_two_reserved_lines() {
        let long = "Revenue by region and product category across every \
                    reporting quarter of the fiscal year";
        assert!(measure(long, 18.0) > 600.0 - 32.0, "fixture is not over the limit");
        let svg = render_json(heatmap_payload(|j| j["resolved"]["title"] = json!(long)));
        assert_eq!(svg.matches("font-size=\"18\"").count(), 2, "title did not wrap: {svg}");
        // One extra 18px line advances the block by `fontSize + 2`, so the
        // top margin deepens from 28 to 48.
        assert_eq!(plot_origin(&svg).1, 16.0 + 18.0 + 20.0 + 10.0, "{svg}");
    }

    /// H8 — the title is placed by Vega's title layout: left-aligned with
    /// the view ink (the y-label gutter, not the card padding) and stacked
    /// `ink_h + offset` above it, with a subtitle under it.
    #[test]
    fn the_title_anchors_on_the_y_label_ink_and_stacks_a_subtitle() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["subtitle"] = json!("Trailing twelve months");
        }));
        let (plot_left, plot_top) = plot_origin(&svg);
        let tag = text_tag(&svg, "Revenue by Region and Category");
        assert!((attr(&tag, "x") - (plot_left - own_side_padding())).abs() < 0.01, "{tag}");
        // Title row at `plot_top − ink_h − offset`, baseline round(0.79·18).
        let ink_h = 18.0 + 3.0 + 11.0;
        let title_row = plot_top - ink_h - 10.0;
        assert!((attr(&tag, "y") - (title_row + 14.0)).abs() < 0.01, "{tag}");
        let sub = text_tag(&svg, "Trailing twelve months");
        assert!((attr(&sub, "y") - (title_row + 18.0 + 3.0 + 9.0)).abs() < 0.01, "{sub}");
    }

    /// H4 — the legend box is `ceil(max(title, entries))`, and the title is
    /// measured on the DRAWN, title-cased name. A long field name widens the
    /// box past its entries and narrows the plot by the difference.
    #[test]
    fn a_wide_legend_title_sets_the_box_width() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["color"] = json!("revenue_by_reporting_region");
            for row in j["rows"].as_array_mut().unwrap() {
                let v = row["revenue"].clone();
                row["revenue_by_reporting_region"] = v;
            }
        }));
        let title_w = measure_shaped("Revenue By Reporting Region", 12.0);
        let entries = 16.0 + 8.0
            + max_measure_shaped(&["10,000", "20,000", "30,000", "40,000", "50,000", "60,000"], 11.0);
        assert!(title_w > entries, "fixture is not discriminating: {title_w} <= {entries}");
        let plot_w = cells(&svg)[0].2 * 4.0;
        let left = 16.0 + own_side_padding().ceil();
        assert!((plot_w - (600.0 - left - 16.0 - 18.0 - title_w.ceil())).abs() < 0.01, "{svg}");
    }

    /// An untitled vertical legend's box is still `ceil(16 + labelOffset +
    /// widest label)`: Vega rounds the box up whether or not a title widens it.
    #[test]
    fn an_untitled_vertical_legend_box_rounds_up_to_a_whole_pixel() {
        let json = heatmap_payload(|j| j["resolved"]["legend"]["title"]["visible"] = json!(false));
        let payload: Payload = serde_json::from_value(json).unwrap();
        let r = HeatmapResolved::from_payload(&payload).unwrap();
        let labels = ["10,000", "60,000"].map(String::from);
        let values = [10_000.0, 60_000.0];
        let legend =
            GradientLegend::resolve(&r, GradientColors::new(&r.style.color_gradient, None), "g", (10_000.0, 60_000.0, &values), &labels, &test_fonts()).unwrap();
        let width = legend.box_width(100.0);
        assert!(width.fract() == 0.0 && width >= 24.0 + max_measure_shaped(&["10,000", "60,000"], 11.0), "{width}");
    }

    /// H9 — the x categories run bar's ordinal overlap ladder: names too
    /// wide for their band tilt to the first `tilt_increments` rung that
    /// fits, and the rotated ink deepens the bottom margin.
    #[test]
    fn wide_x_categories_take_the_ordinal_tilt_ladder() {
        let svg = render_json(heatmap_payload(|j| {
            for (i, row) in j["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                let long = format!("{} Region Of Operations", ["East", "North", "South", "West"][i / 3]);
                row["region"] = json!(long);
            }
        }));
        // Band = 600 · 0.8 / 4 = 120px; the labels' widest adjacent pair
        // clears it only at the ladder's −45 rung (normalized to 315).
        assert!(svg.contains("rotate(315)"), "wrong tilt rung: {svg}");
        let (_, plot_top) = plot_origin(&svg);
        let plot_h = cells(&svg)[0].3 * 3.0;
        assert!(plot_top + plot_h < 432.0 - 16.0 - 24.0, "bottom band did not deepen: {svg}");
    }

    /// The legend box is measured the way VEGA measures it — SHAPED, with
    /// the font's GPOS kerning — where the y-label gutter stays on the raw
    /// advance sum, because production bakes `labelPadding` with its own raw
    /// measurer and Vega honors that number verbatim. A comma kerns the two
    /// apart by ~0.56px at 11px, which is a whole pixel of legend box and so
    /// a whole pixel of plot.
    #[test]
    fn the_legend_box_measures_shaped_where_the_y_gutter_measures_raw() {
        let fonts = test_fonts();
        let raw = fonts.measure("40,000", TEST_FONT, 11.0).unwrap();
        let shaped = fonts.measure_shaped("40,000", TEST_FONT, 11.0).unwrap();
        let entries = |w: f64| (16.0 + 8.0 + w).ceil();
        assert!(entries(raw) > entries(shaped), "fixture not discriminating: {raw} vs {shaped}");
        let svg = render_json(heatmap_payload(|_| {}));
        let left = 16.0 + own_side_padding().ceil();
        let plot_w = cells(&svg)[0].2 * 4.0;
        assert!(
            (plot_w - (600.0 - left - 16.0 - 18.0 - entries(shaped))).abs() < 0.01,
            "plot_w {plot_w}: {svg}"
        );
        // The gutter is unchanged by the shaped measurement.
        let tag = text_tag(&svg, "Accessories");
        assert!((attr(&tag, "x") - (plot_origin(&svg).0 + 0.5 - own_side_padding())).abs() < 0.01, "{tag}");
    }

    /// A suppressed legend title takes no band and draws nothing, and the
    /// title's own `font.case` decides the drawn text — both via
    /// `legend::legend_title_text`.
    #[test]
    fn a_suppressed_legend_title_gives_up_its_band() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["legend"]["title"]["visible"] = json!(false);
        }));
        assert!(!svg.contains(">Revenue</text>"), "{svg}");
        let (_, plot_top) = plot_origin(&svg);
        let i = svg.find("url(#heat_grad").unwrap();
        let bar = svg[..i].rfind("<rect ").map(|s| svg[s..i].to_string()).unwrap();
        assert_eq!(attr(&bar, "y"), plot_top, "gradient still under a title band: {bar}");
    }

    /// The legend title's own `font.case` is honored, not hardcoded.
    #[test]
    fn the_legend_title_takes_its_own_font_case() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["legend"]["title"]["font"]["case"] = json!("upper");
        }));
        assert!(svg.contains(">REVENUE</text>"), "{svg}");
    }

    /// The theme cascade guarantees `legend.title.font.case` is always
    /// populated on a resolved payload -- a caller reaching the tooltip's
    /// value-title case with it missing is a genuine resolved-payload
    /// defect, not a shape to silently default around (`pie.rs`'s own
    /// `pie_legend_title_case` refuses the identical fact the same way).
    /// Regression coverage for the bug where this defaulted to "title".
    #[test]
    fn missing_legend_title_font_case_refuses_the_tooltip_value_title() {
        let j = heatmap_payload(|j| {
            j["resolved"]["legend"]["title"]["font"]["case"] = json!(null);
        });
        let p: Payload = serde_json::from_value(j).unwrap();
        let err = render(&p, &test_fonts()).expect_err("missing case refuses");
        assert!(err.contains("legend.title.font.case"), "{err}");
    }

    /// A color field with no numeric rows has no domain to paint or label —
    /// it raises production's code rather than inventing one.
    #[test]
    fn a_color_field_with_no_numeric_rows_raises_its_code() {
        let err = {
            let j = heatmap_payload(|j| {
                for row in j["rows"].as_array_mut().unwrap() {
                    row["revenue"] = json!(null);
                }
            });
            let p: Payload = serde_json::from_value(j).unwrap();
            render(&p, &test_fonts()).expect_err("raises")
        };
        assert!(err.contains("ERR-HEATMAP-COLOR-NO-VALUES") && err.contains("revenue"), "{err}");
    }

    /// A date x heads the cell's tooltip with its bucket, as every other cartesian family does.
    #[test]
    fn a_date_like_x_heads_the_tooltip_with_its_bucket() {
        let _en = crate::time_locale::en_us_scope();
        let j = heatmap_payload(|j| {
            j["rows"] = json!([
                {"region": "2024-01-01", "category": "Accessories", "revenue": 100},
                {"region": "2024-02-01", "category": "Accessories", "revenue": 200},
            ]);
        });
        let p: Payload = serde_json::from_value(j).unwrap();
        let svg = render(&p, &test_fonts()).expect("renders");
        assert!(svg.contains("\u{2061}Jan 2024; \u{2061}Accessories; Revenue: 100"), "{svg}");
    }

    /// `axis_y.mirror` repeats the category labels on the right edge, and the
    /// ghost rule layer's stroke turns the gradient legend into a symbol
    /// legend.
    #[test]
    fn mirror_repeats_the_category_labels_on_the_right_edge() {
        let plain = render_json(heatmap_payload(|_| {}));
        let svg = render_json(heatmap_payload(|j| j["resolved"]["style"]["axis_y"]["mirror"] = json!(true)));
        let labels = |svg: &str| svg.matches(">Accessories</text>").count();
        assert_eq!((labels(&plain), labels(&svg)), (1, 2), "{svg}");
        assert!(plain.contains("fill=\"url(#") && !svg.contains("fill=\"url(#"), "the gradient turns into symbols");
    }

    /// Front-end contract: `tooltip_format` (already set in the base
    /// fixture) wraps every cell in a `.role-mark` group and stamps its
    /// structured aria-label (`charts/more-types_0/heatmap_example`'s own
    /// East/Accessories cell, see `contract.rs`'s heatmap tests for the
    /// formula). No `tooltip_format` keeps today's unwrapped, unlabeled
    /// cells — same gate as bar/pie/scatter.
    #[test]
    fn tooltip_format_wraps_every_cell_with_a_structured_aria_label() {
        let svg = render_json(heatmap_payload(|_| {}));
        assert!(
            svg.contains("<g class=\"mark-rect role-mark marks\""),
            "every cell wraps in a role-mark group: {svg}"
        );
        assert!(
            svg.contains("aria-label=\"⁡East; ⁡Accessories; Revenue: 47,368\""),
            "{svg}"
        );

        let plain = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["tooltip_format"] = Value::Null;
        }));
        assert!(!plain.contains("role-mark"));
        assert!(crate::contract::has_no_mark_labels(&plain));
    }

    /// The heatmap fixture's legend at `edge`/`direction`.
    fn edge_payload(edge: &str, direction: &str) -> Value {
        heatmap_payload(|j| {
            j["resolved"]["legend"]["position"] = json!({"edge": edge, "align": "start", "overlay": false});
            j["resolved"]["legend"]["direction"] = json!(direction);
        })
    }

    /// The gradient bar's `(x, y, width, height)`.
    fn gradient_bar(svg: &str) -> (f64, f64, f64, f64) {
        let tag = svg
            .match_indices("<rect ")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .find(|tag| tag.contains("fill=\"url(#"))
            .unwrap_or_else(|| panic!("no gradient bar: {svg}"));
        (attr(tag, "x"), attr(tag, "y"), attr(tag, "width"), attr(tag, "height"))
    }

    /// A left legend anchors at `floor(xBounds.x1) - 18 - box_w`
    /// (`legend.js:41-46`), `xBounds` the plot rect ∪ the y axis (the cells
    /// are bare rect marks): the box is the view's leftmost ink and ends
    /// 18px short of the y axis's widest label.
    #[test]
    fn a_left_gradient_legend_is_the_views_leftmost_ink() {
        let svg = render_json(edge_payload("left", "vertical"));
        let (x, ..) = gradient_bar(&svg);
        assert_eq!(x, 16.0, "{svg}");
        assert_eq!(attr(&text_tag(&svg, "Revenue"), "x"), 16.0, "{svg}");
        let (plot_left, _) = plot_origin(&svg);
        let gap = plot_left - (16.0 + legend_box_w()) - 18.0;
        assert!(gap >= max_measure(&["Accessories", "Electronics", "Tools"], 11.0), "gap {gap}: {svg}");
    }

    /// A horizontal gradient on the top or bottom edge is
    /// `clamp(plot_width, 100, 200)` long, on a side edge the 100px
    /// `gradientHorizontalMinLength` (vega-lite `compile/legend/properties.ts`
    /// `defaultGradientLength`, `legend.ts` `defaultLegendConfig`), under a
    /// `12 + 5` title band; a bottom one is the view's bottom-most ink.
    #[test]
    fn a_horizontal_gradient_legend_takes_its_edges_length_under_its_title() {
        // An authored domain labels its two ends alone, so they never
        // overlap along the bar.
        let two_ends = |edge: &str| {
            let mut p = edge_payload(edge, "horizontal");
            p["resolved"]["style"]["color_gradient"]["min"] = json!(0.0);
            p["resolved"]["style"]["color_gradient"]["max"] = json!(100000.0);
            render_json(p)
        };
        let svg = two_ends("bottom");
        let (_, y, width, height) = gradient_bar(&svg);
        let cells = cells(&svg);
        let plot_w: f64 = cells.iter().filter(|c| c.1 == cells[0].1).map(|c| c.2).sum();
        assert_eq!((width, height), (plot_w.clamp(100.0, 200.0), 16.0), "{svg}");
        assert_eq!(y + 16.0 + 8.0 + 11.0, 432.0 - 16.0, "{svg}");
        let title = text_tag(&svg, "Revenue");
        assert_eq!(attr(&title, "y"), y - 17.0 + measure::baseline_shift("top", 12.0), "{svg}");
        assert_eq!(gradient_bar(&two_ends("right")).2, 100.0);
    }

    /// A categorical heatmap's symbol legend anchors on any edge through
    /// the shared law: on the left its title sits at the padded edge.
    #[test]
    fn a_categorical_left_legend_is_the_views_leftmost_ink() {
        let svg = render_json(heatmap_payload(|j| {
            for (i, row) in j["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["tier"] = json!(["High", "Low", "Mid"][i % 3]);
            }
            j["resolved"]["color"] = json!("tier");
            j["resolved"]["palette"] = json!(["#111111", "#222222", "#333333"]);
            j["resolved"]["resolved_channels"] =
                json!({"color": {"channel": "color", "mode": "series", "data_field": "tier", "quantitative_data": false}});
            j["resolved"]["legend"]["position"] = json!({"edge": "left", "align": "start", "overlay": false});
        }));
        assert_eq!(attr(&text_tag(&svg, "Tier"), "x"), 16.0, "{svg}");
    }

    /// `RECEDE_ROLES` matches `rect` literally: a heatmap cell recedes
    /// on hover only when stamped so.
    #[test]
    fn every_cell_carries_the_role_the_runtime_recedes() {
        let svg = render_json(heatmap_payload(|_| {}));
        let cells = svg.matches("<g class=\"mark-rect role-mark marks\"").count();
        assert!(cells > 1, "{svg}");
        assert_eq!(svg.matches("aria-roledescription=\"rect mark\"").count(), cells, "{svg}");
    }

    /// A right y axis puts its labels on the away side of the cells, and an
    /// own-side (`left`) label alignment pads the axis by the widest label.
    #[test]
    fn a_right_y_axis_renders_away_and_own_side() {
        let away = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_y"]["position"] = json!("right");
        }));
        let own = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_y"]["position"] = json!("right");
            j["resolved"]["style"]["axis_y"]["labels"]["align"] = json!("right");
        }));
        assert!(away.contains("role-axis") && own.contains("role-axis"));
        assert_ne!(away, own);
    }

    /// A vertical gradient below the plot keeps the side length, `clamp(plot
    /// height, 64, 200)`: the legend's orient moves it, not its length.
    #[test]
    fn a_vertical_gradient_below_the_plot_renders() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["legend"]["position"] = json!({"edge": "bottom", "align": "start", "overlay": false});
        }));
        assert!(svg.contains("fill=\"url(#"), "{svg}");
    }

    /// Oracle: `format_d3(47368, ",.2~f", "US$", "", None, placement)` is
    /// `'US$47,368'`. The gradient legend keeps the bare spec: production's
    /// color encoding takes `tooltip_format.spec` alone.
    #[test]
    fn a_tooltip_row_paints_its_affix_and_the_legend_stays_bare() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["tooltip_format"] = json!({"spec": ",.2~f", "prefix": "US$"});
        }));
        assert!(svg.contains("aria-label=\"⁡East; ⁡Accessories; Revenue: US$47,368\""), "{svg}");
        assert!(!svg.contains(">US$"), "no painted text wears the affix: {svg}");
    }

    /// A heatmap axis is nominal, so resolve rejects an authored affix before
    /// render; one beside an authored `labels.expr` still refuses by name.
    #[test]
    fn an_axis_affix_beside_a_labels_expr_is_dropped() {
        for axis in ["axis_x", "axis_y"] {
            let p: Payload = serde_json::from_value(heatmap_payload(|j| {
                j["resolved"]["style"][axis]["labels"]["format"] = json!({"spec": ",.0f", "prefix": "US$"});
                j["resolved"]["style"][axis]["labels"]["expr"] = json!("datum.label");
            }))
            .unwrap();
            let svg = render(&p, &test_fonts()).unwrap_or_else(|e| panic!("{axis}: {e}"));
            assert!(!svg.contains("US$"), "{axis}");
        }
    }

    #[test]
    fn a_date_legend_with_fewer_than_three_ticks_labels_its_two_ends() {
        // 30 days at two ticks is monthly: one tick inside, so the ends label it.
        assert_eq!(super::date_legend_values(19723.0, 19753.0, 100.0, None).unwrap(), vec![19723.0, 19753.0]);
        // At a 200px bar the four ticks are weekly.
        assert_eq!(super::date_legend_values(19723.0, 19753.0, 200.0, None).unwrap().len(), 4);
        // 60 days at two ticks is monthly: Jan 1, Feb 1, Mar 1.
        assert_eq!(super::date_legend_values(19723.0, 19783.0, 100.0, None).unwrap(), vec![19723.0, 19754.0, 19783.0]);
    }

    #[test]
    fn a_two_value_date_range_is_the_scheme_sampled_at_thirds() {
        assert_eq!(super::date_color(0.0, 60.0, 0.0), "rgb(134, 208, 187)");
        assert_eq!(super::date_color(0.0, 60.0, 60.0), "rgb(37, 139, 187)");
        assert_eq!(super::date_color(0.0, 60.0, 31.0), "rgb(68, 175, 188)");
    }

    /// A time-scale legend over one date labels that date once, and an authored
    /// `legend.values` is the labeled set whatever the domain.
    #[test]
    fn a_zero_width_date_legend_labels_its_one_date_and_authored_values_replace_the_ticks() {
        assert_eq!(super::date_legend_values(19723.0, 19723.0, 200.0, None).unwrap(), vec![19723.0]);
        let authored = ["2024-01-01".to_string(), "soon".to_string()];
        let values = super::date_legend_values(19723.0, 19783.0, 200.0, Some(&authored)).unwrap();
        assert_eq!(values[0], 19723.0);
        assert!(values[1].is_nan());
        assert_eq!(super::legend_date_label(values[1]).unwrap(), "NaN, 0NaN");
    }

    /// A zero-width time domain maps every date to the middle of the range.
    #[test]
    fn a_zero_width_date_domain_is_the_middle_of_the_range() {
        assert_eq!(super::date_color(5.0, 5.0, 5.0), super::date_color(0.0, 10.0, 5.0));
    }

    fn dimension_rows(cells: &[&str]) -> Vec<serde_json::Map<String, Value>> {
        cells.iter().map(|c| json!({"d": c}).as_object().unwrap().clone()).collect()
    }

    fn dimension(cells: &[&str], is_x: bool) -> (Vec<String>, Option<TimeUnit>) {
        let axis: crate::common::AxisStyle = serde_json::from_value(axis_json(true, None)).unwrap();
        let (rows, grain) = crate::families::heatmap_facet::date_dimension(&dimension_rows(cells), "d", &axis, is_x).unwrap();
        (rows.iter().map(|r| row_str(r, "d")).collect(), grain)
    }

    /// `canonicalize_cartesian_x_data` under a calendar grain: an x sorts
    /// ascending and a date-time collapses to its date; a y keeps its order.
    #[test]
    fn a_date_dimension_collapses_midnights_and_sorts_only_an_x() {
        let cells = ["2024-03-01T00:00:00", "2024-01-01", "2024-02-01T00:00:00"];
        let (x, grain) = dimension(&cells, true);
        assert_eq!((x, grain), (["2024-01-01", "2024-02-01", "2024-03-01"].map(String::from).to_vec(), Some(TimeUnit::YearMonth)));
        let (y, grain) = dimension(&cells, false);
        assert_eq!((y, grain), (["2024-03-01", "2024-01-01", "2024-02-01"].map(String::from).to_vec(), Some(TimeUnit::YearMonth)));
    }

    /// `canonicalize_and_sort_ordinal_x` sorts on `str(value)`: a null is "None", after every date.
    #[test]
    fn a_date_x_sorts_a_null_after_every_date() {
        let rows: Vec<_> = [json!(null), json!("2024-02-01"), json!("2024-01-01")]
            .iter()
            .map(|c| json!({"d": c}).as_object().unwrap().clone())
            .collect();
        let axis: crate::common::AxisStyle = serde_json::from_value(axis_json(true, None)).unwrap();
        let (rows, grain) = crate::families::heatmap_facet::date_dimension(&rows, "d", &axis, true).unwrap();
        assert_eq!(rows.iter().map(|r| row_str(r, "d")).collect::<Vec<_>>(), ["2024-01-01", "2024-02-01", ""]);
        assert_eq!(grain, Some(TimeUnit::YearMonth));
    }

    /// A labeled bucket is rewritten to its first day; a time of day or a
    /// shape no calendar names has no grain and keeps its rows as they came.
    #[test]
    fn a_date_dimension_without_a_calendar_grain_keeps_its_rows() {
        let (x, grain) = dimension(&["Q2 2024", "Q1 2024"], true);
        assert_eq!((x, grain), (["2024-01-01", "2024-04-01"].map(String::from).to_vec(), Some(TimeUnit::YearQuarter)));
        let clock = ["2024-02-01T10:00:00", "2024-01-01T10:00:00"];
        assert_eq!(dimension(&clock, true), (clock.map(String::from).to_vec(), None));
        let four_weekly = ["2024-02-26", "2024-01-29", "2024-01-01"];
        assert_eq!(dimension(&four_weekly, true), (four_weekly.map(String::from).to_vec(), None));
        let names = ["b", "a"];
        assert_eq!(dimension(&names, true), (names.map(String::from).to_vec(), None));
    }

    /// A color field with no number raises whether or not the author pinned both bounds, and a
    /// boolean counts as the number production's `is_vega_numeric_value` reads it as.
    #[test]
    fn a_gradient_color_with_no_number_raises_even_with_both_bounds_authored() {
        let gradient: super::ColorGradient = serde_json::from_value(serde_json::json!({"nice": true, "min": 0.0, "max": 10.0})).unwrap();
        let rows = |cells: &[serde_json::Value]| -> Vec<serde_json::Map<String, serde_json::Value>> {
            cells.iter().map(|cell| serde_json::json!({"c": cell}).as_object().unwrap().clone()).collect()
        };
        let err = super::gradient_ladder(&rows(&[serde_json::json!("n/a"), serde_json::Value::Null]), "c", &gradient).unwrap_err();
        assert_eq!(err.code, "ERR-HEATMAP-COLOR-NO-VALUES");
        assert_eq!(err.params["field"], serde_json::json!("c"));
        assert!(super::gradient_ladder(&rows(&[serde_json::json!(true)]), "c", &gradient).is_ok());
    }

    /// The stops interpolate piecewise in sRGB and print the way d3 formats a
    /// color; a stop that is not `#rrggbb` is an error, not black.
    #[test]
    fn a_gradient_interpolates_its_stops_piecewise_in_srgb() {
        let stops = ["#000000".to_string(), "#ff0000".to_string(), "#ffffff".to_string()];
        assert_eq!(super::interp_color(&stops, 0.0, 10.0, 0.0).unwrap(), "rgb(0, 0, 0)");
        assert_eq!(super::interp_color(&stops, 0.0, 10.0, 2.5).unwrap(), "rgb(128, 0, 0)");
        assert_eq!(super::interp_color(&stops, 0.0, 10.0, 7.5).unwrap(), "rgb(255, 128, 128)");
        assert_eq!(super::interp_color(&stops, 0.0, 10.0, 99.0).unwrap(), "rgb(255, 255, 255)");
        let err = super::interp_color(&["red".to_string(), "#fff".to_string()], 0.0, 1.0, 0.5).unwrap_err();
        assert!(err.contains("\"red\""), "{err}");
    }

    // ── The family contract ──────────────────────────────────────────────

    fn bound(mutate: impl FnOnce(&mut Value)) -> (Payload, super::bind::Bound) {
        let payload: Payload = serde_json::from_value(heatmap_payload(mutate)).unwrap();
        let bound = super::bind::bind(&payload).expect("heatmap binds");
        (payload, bound)
    }

    /// `bind` reads no font: the bands, the color ladder, every label and each cell's fill are
    /// there before any size is known.
    #[test]
    fn bind_derives_the_bands_the_ladder_and_the_cells() {
        let (_, bound) = bound(|_| {});
        assert_eq!(bound.x.domain, ["East", "North", "South", "West"].map(String::from));
        assert_eq!(bound.y.domain, ["Accessories", "Electronics", "Tools"].map(String::from));
        assert_eq!(bound.x.texts, bound.x.domain);
        assert_eq!(bound.cells.len(), 12);
        let super::bind::Coloring::Gradient { min, max, ticks, .. } = &bound.coloring else { panic!("a measure colors a gradient") };
        assert_eq!((ticks.first(), ticks.last()), (Some(min), Some(max)));
        assert_eq!(bound.gradient_labels.len(), ticks.len());
        assert!(bound.cells.iter().all(|cell| cell.fill.starts_with("rgb(") && cell.aria.is_some()));
    }

    #[test]
    fn a_heatmap_hugs_nothing_and_takes_the_slot_height() {
        let (payload, bound) = bound(|_| {});
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        assert_eq!(super::layout::intrinsic(&bound, &measure).unwrap(), None);
        assert_eq!(super::layout::height_for_width(&bound, 123.0, &measure).unwrap(), payload.height);
    }

    /// `layout` draws the cell it is given and raises its WARNs itself: the title and both axis
    /// titles are cut, in the order the chart reports them, and none reaches the sink.
    #[test]
    fn layout_draws_the_given_cell_and_returns_its_warnings() {
        let long = "An axis title that keeps going and going until no budget holds it, however it wraps";
        let (_, bound) = bound(|json| {
            let resolved = &mut json["resolved"];
            resolved["title"] = json!(long.repeat(3));
            resolved["x_label"] = json!(long.repeat(3));
            resolved["y_label"] = json!(long.repeat(3));
            resolved["style"]["axis_x"]["title"]["visible"] = json!(true);
            resolved["style"]["axis_y"]["title"]["visible"] = json!(true);
        });
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 };
        let (laid, sunk) = crate::diagnostic::collect(|| super::layout::layout(&bound, cell, &measure));
        let laid = laid.expect("heatmap lays out");
        assert!(sunk.is_empty(), "{sunk:?}");
        let fields: Vec<_> = laid
            .diagnostics
            .iter()
            .map(|d| (d.code, d.params["authored_field"].as_str().unwrap().to_string()))
            .collect();
        assert_eq!(
            fields,
            [
                ("WARN-AXIS-TITLE-TRUNCATED", "x_label"),
                ("WARN-AXIS-TITLE-TRUNCATED", "y_label"),
                ("WARN-CHART-TITLE-TRUNCATED", "title"),
            ]
            .map(|(code, field)| (code, field.to_string()))
        );
        let [scene::Node::Svg { extent, .. }] = laid.nodes.as_slice() else { panic!("one root") };
        assert_eq!((extent.width, extent.height), (420.0, 260.0));
        let data = laid.data_box;
        assert!(data.x >= 0.0 && data.y >= 0.0 && data.x + data.width <= 420.0 && data.y + data.height <= 260.0);
        assert_eq!(cell.insets_of(&data), laid.insets);
    }

    /// `render` is `bind`, `layout` into the payload's card, and `print`; the warnings reach the
    /// sink once, from the layout's own list.
    #[test]
    fn render_prints_the_layout_of_the_bound_chart() {
        let (payload, bound) = bound(|json| json["resolved"]["title"] = json!("T ".repeat(300)));
        let fonts = test_fonts();
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
        let laid = super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).unwrap();
        let (svg, sunk) = crate::diagnostic::collect(|| render(&payload, &fonts));
        assert_eq!(svg.unwrap(), print::print(&laid.nodes));
        assert_eq!(sunk, laid.diagnostics);
    }
}
