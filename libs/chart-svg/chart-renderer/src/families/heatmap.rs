//! Heatmap family — band×band grid with quantitative color ramp.
//!
//! The chrome is Vega's `autosize: fit` negotiation (`vega_fit::fit_plot`)
//! over the plot rect, both axes' label ink and the right gradient legend's
//! box; the cell colors and the legend's labeled ticks come off ONE
//! production tick ladder (`emitters/_channels.py`).
//!
//! Unsupported → clear Err:
//! - a vertical gradient legend above or below the plot, or a right-hand y
//!   axis — neither has a probed geometry, and no corpus board draws one

use crate::diagnostic::Diagnostic;
use crate::constants::axis::GRADIENT_NICE_TICK_COUNT;
use std::collections::HashMap;
use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    dimension_sort_domain, ScaleTarget, row_num, row_str, AxisStyle, ChartSort, Font, FormatSlot, SortAggregate,
    StyleChannel, TitleStyle,
};
use crate::families::cartesian::BandScale;
use crate::families::color_scale::{ContinuousColor, GradientAt};
use crate::families::heatmap_facet::{
    date_dimension, first_appearance, AxisLabel, GradientLegend, GradientPaint, HeatmapXAxis,
};
use crate::families::legend::{
    emit_vega_legend, legend_fit_bounds, legend_origin, legend_slide, legend_title_text, slide_block,
    unit_position, vega_legend, LegendConfig, LegendEntry, LegendSymbol, VerticalGradient,
    gradient_length, gradient_labeled_values, horizontal_gradient_length, gradient_tick_count, emit_scale_gradient_def, gradient_legend_box,
    GRADIENT_STOP_TICKS, VEGA_GRADIENT_LENGTH_MAX, VEGA_GRADIENT_LENGTH_MIN,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::svg::{xml_escape, xml_escape_attr, vega_escape_attr};
use crate::families::temporal::{
    axis_time_format, fold_steep, infer_vega_type, parse_iso_date, rewrite_with_expr, resolve_vertical_temporal_labels, AuthoredTimeFormat,
    TimeUnit, VegaType,
};
use crate::text::FontBook;
use crate::vega_fit::{self, Bounds};
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
    pub(crate) color: String,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// as an `<a>` around each cell.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    #[serde(default)]
    link_open: crate::href::LinkOpen,
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

impl HeatmapResolved {
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
    ) -> Result<(f64, f64, Vec<f64>, Option<ContinuousColor>), String> {
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
    pub(crate) fn stroke_attr(&self) -> Result<String, String> {
        let Some((stroke, color, width)) = self.painted_stroke() else { return Ok(String::new()) };
        let mut attrs = format!(" stroke=\"{}\" stroke-width=\"{width}\"", xml_escape_attr(color));
        if let Some(cap) = &stroke.cap {
            let _ = write!(attrs, " stroke-linecap=\"{}\"", xml_escape_attr(cap));
        }
        if let Some(join) = &stroke.join {
            let _ = write!(attrs, " stroke-linejoin=\"{}\"", xml_escape_attr(join));
        }
        if let Some(dasharray) = &stroke.dasharray {
            let raw = dasharray.as_str().ok_or("heatmap: a rect mark stroke dasharray is not a string of lengths")?;
            attrs.push_str(&crate::step_band::attr(&crate::step_band::parse("heatmap", raw, false)?, None));
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
        let mut r: Self = serde_json::from_value(payload.resolved.clone()).map_err(|e| format!("heatmap payload: {e}"))?;
        r.series_palette = payload.series_palette(&r.palette);
        Ok(r)
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let r = HeatmapResolved::from_payload(payload)?;
    r.style.axis_x.refuse_unpainted_affix("heatmap", "axis_x", None)?;
    r.style.axis_x.refuse_x_tick_cadence("heatmap")?;
    if r.multiples.is_some() {
        r.style.axis_x.refuse_top_x_axis("heatmap")?;
    }
    r.style.axis_y.refuse_unpainted_affix("heatmap", "axis_y", None)?;

    // `_flag_quantitative_color` stamps every resolved color channel, so a
    // series color without the flag is a payload no resolver produced.
    let categorical = match r.resolved_channels.get("color") {
        Some(ch) if ch.mode == "series" => !ch.quantitative_data.ok_or(
            "heatmap: a series color channel carries no quantitative_data flag (resolver required)",
        )?,
        _ => false,
    };
    let (rows, x_grain) = date_dimension(&payload.rows, &r.x, &r.style.axis_x, true)?;
    let (rows, y_grain) = date_dimension(&rows, &r.y, &r.style.axis_y, false)?;
    // A date-like column types temporal: a time-scale gradient (`DateGradient`).
    let date_color = categorical && infer_vega_type(&rows, &r.color) == VegaType::Temporal;
    let categorical = categorical && !date_color;
    let kind = if date_color {
        ColorKind::Dates
    } else if categorical {
        ColorKind::Categories
    } else {
        ColorKind::Gradient
    };
    if r.multiples.is_some() {
        return Ok(crate::families::heatmap_facet::render_facet(payload, fonts, &r, kind)?);
    }

    let rows = drop_invalid_color_rows(rows, &r.color, kind);

    // The chart's OWN effective background — see `HeatmapResolved::background`.
    let frame = ChartFrame::of_view(payload, r.background.as_str(), crate::ViewScale::Discrete)?;
    let (w, h) = (frame.w, frame.h);

    let x_field = &r.x;
    let y_field = &r.y;
    let color_field = &r.color;

    // ── Band domains: first appearance over the rows, or an authored
    // `chart.sort`'s per-category MIN order. `_write_dimension_sort`
    // (`emitters/heatmap.py:70-75`) pins `sort: null` on both dimensions when
    // nothing is authored: Vega-Lite's `domainSort` (`compile/scale/domain.ts`
    // `// sort == null`) emits no domain sort, and Vega's `valuesRef`
    // (`parsers/scale.js` `singularDomain`) then keeps the aggregate's encounter
    // order. `_write_dimension_sort` reaches BOTH dimensions identically, so
    // `r.sort` is read once rather than per-axis.
    let dimension_domain = |field: &str| -> Result<Vec<String>, String> {
        dimension_sort_domain(
            &rows,
            field,
            first_appearance(&rows, field),
            r.sort.as_ref(),
            SortAggregate::Min,
            "heatmap",
        )
    };
    let x_domain = dimension_domain(x_field)?;
    let y_domain = dimension_domain(y_field)?;

    let coloring = if date_color {
        let (min, max) = date_color_extent(&rows, color_field)?;
        Coloring::DateGradient { min, max }
    } else if categorical {
        Coloring::Categories(color_categories(&r, &rows)?)
    } else {
        let (min, max, ticks, scale) = r.style.color_gradient.ladder(&rows, color_field)?;
        Coloring::Gradient { min, max, ticks, scale }
    };

    // ── X axis ────────────────────────────────────────────────────────────
    // A date x runs bar's bucketed-calendar ladder instead of the raw ordinal
    // band (`build_cartesian_x_encoding` keeps the band scale and borrows the
    // ladder's labels): the rows are already in date order (`date_dimension`),
    // so the band domain is the ladder's own order.
    let ax_x = &r.style.axis_x;
    let x_first_seen = first_appearance(&rows, x_field);
    let x_dates = if let Some(data_tu) = x_grain {
        let usable_ratio = r
            .style
            .label_usable_ratio
            .ok_or("heatmap: label_usable_ratio absent (theme required for the x axis ladder)")?;
        let axis = HeatmapXAxis::dates(&x_first_seen, data_tu, ax_x, w, usable_ratio, fonts)?;
        Some(axis)
    } else {
        None
    };
    let x_tick_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = if x_tick_visible { ax_x.ticks.length.unwrap_or(6.0) } else { 0.0 };
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_label_pad = ax_x
        .labels
        .padding
        .ok_or("heatmap: axis_x.labels.padding absent (theme required)")?;
    let x_label_fs = ax_x.labels.font.size_or(11.0);
    let x_label_family = ax_x.labels.font.family_or("sans-serif");
    // Heatmap's x is always an ordinal band (`resolve_cartesian_x_type`'s
    // own heatmap branch pins it), so it runs bar's ordinal overlap ladder
    // over the slot width — production's `resolve_axis_x_overlap` with
    // `chart_width=box.width`.
    let x_angle = vega_fit::normalize_angle(match ax_x.labels.angle {
        _ if x_dates.is_some() => 0.0,
        Some(angle) => angle,
        None => vega_fit::category_label_layout(
            &ax_x.labels,
            &x_domain,
            w,
            r.style.label_usable_ratio,
            fonts,
            x_label_fs,
        )?
        .angle,
    });
    let x_edge = ax_x.x_edge();
    let (x_align, x_baseline) = if x_angle == 0.0 {
        ("center", x_edge.baseline())
    } else {
        vega_fit::vl_x_label_anchor(x_angle, x_edge == crate::common::XEdge::Bottom)
    };
    let x_label_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_label_ws = measure_all(fonts, &x_domain, x_label_family, x_label_fs, x_label_limit)?;

    // ── Y category axis ───────────────────────────────────────────────────
    let ax_y = &r.style.axis_y;
    let ghost = ax_y.mirror_ghost("heatmap")?;
    let mirror_active = ghost.is_some();
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_tick_visible = ax_y.ticks.visible.unwrap_or(true);
    let y_tick_len = if y_tick_visible { ax_y.ticks.length.unwrap_or(6.0) } else { 0.0 };
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // Production keeps the nominal band scale and borrows the ladder's labels
    // (`build_cartesian_x_encoding`); it paints from query order.
    let y_first_seen = first_appearance(&rows, y_field);
    let (y_dates, y_greedy_gap) =
        y_date_labels(&y_first_seen, y_grain, ax_y, h, r.style.label_usable_ratio, fonts, y_label_family, y_label_fs)?;
    let y_label_bands: Vec<String> = match &y_dates {
        Some(labels) => labels.iter().map(|l| y_domain[l.domain_idx].clone()).collect(),
        None => y_domain.clone(),
    };
    let y_label_max_w = y_label_bands
        .iter()
        .try_fold(0.0_f64, |acc, s| fonts.measure(s, y_label_family, y_label_fs).map(|v| acc.max(v)))?;
    let y_label_ws = match &y_dates {
        Some(labels) => labels.iter().map(|l| l.w1.max(l.w2).min(y_label_limit)).collect(),
        None => measure_all(fonts, &y_domain, y_label_family, y_label_fs, y_label_limit)?,
    };
    // Each drawn label's width and whether it carries a second row; hidden
    // labels draw none.
    let y_label_specs: Vec<(f64, bool)> = if ax_y.labels.visible == Some(false) {
        Vec::new()
    } else {
        match &y_dates {
            Some(labels) => labels.iter().zip(&y_label_ws).map(|(l, &w)| (w, !l.row2.is_empty())).collect(),
            None => y_label_ws.iter().map(|&w| (w, false)).collect(),
        }
    };
    // An own-side `labels.align` (`left` on a left axis, `right` on a right
    // one) grows the row labels back ACROSS the axis line, which Vega-Lite's
    // own fit estimate does not reserve for. Rather than leave them over the
    // plot, production measures the exact nominal row labels and bakes
    // `labelPadding = max_width + 4`, capped at the label limit
    // (`vl_field_maps.py::measure_axis_to_vl`); Vega then anchors every label
    // at -labelPadding. An align it cannot measure from, `center` or an
    // upper/lower `font.case` (which renders through a `labelExpr` the
    // measurement can't see), drops back to Vega-Lite's away-side default
    // (`_fallback_reversed_label_align`). A date y paints a `labelExpr` too,
    // so it always takes the away-side default.
    let y_right = ax_y.position.as_deref() == Some("right");
    let own_align = if y_right { "right" } else { "left" };
    let y_own_side = y_labels_own_side(ax_y, y_dates.is_some());
    let y_label_pad = if y_own_side {
        y_label_max_w.min(y_label_limit) + BREATHING_ROOM_PX
    } else {
        ax_y.labels
            .padding
            .ok_or("heatmap: axis_y.labels.padding absent (theme required)")?
    };
    // An authored align wins; else Vega-Lite's default for the angle (`right`, the
    // text ending at the axis, when flat).
    let y_angle = vega_fit::normalize_angle(ax_y.labels.angle.unwrap_or(0.0));
    let (vl_y_align, y_baseline) = vega_fit::vl_y_label_anchor(y_angle, !y_right);
    let y_ink_align = if y_own_side { own_align } else { vl_y_align };
    let (ghost_default_align, ghost_baseline) = vega_fit::vl_y_label_anchor(y_angle, y_right);
    let mut y_edges = vec![YEdge { right: y_right, align: y_ink_align, baseline: y_baseline, relabeled: None }];
    if let Some(ghost) = &ghost {
        let align = if y_own_side { own_align } else { ghost_default_align };
        // The ghost's own format reads each category as the number d3 sees (`gate_label_format`).
        let relabeled = if ghost.format.is_some() || ghost.ruler.is_some() {
            let values: Vec<f64> = y_domain.iter().map(|cat| crate::common::js_number(cat)).collect();
            let texts = ghost.texts(&values, &y_domain)?;
            let ws = measure_all(fonts, &texts, y_label_family, y_label_fs, y_label_limit)?;
            Some((texts, ws))
        } else {
            None
        };
        y_edges.push(YEdge { right: !y_right, align, baseline: ghost_baseline, relabeled });
    }
    let y_labels_hidden = ax_y.labels.visible == Some(false);
    let y_ink = |edge: &YEdge, anchor_x: f64, cy: f64, label_w: f64, extra_row: bool| {
        crate::common::rotated_category_label_ink(anchor_x, cy, label_w, y_label_fs, edge.align, edge.baseline, y_angle, extra_row)
    };
    let y_gutter = y_tick_len + y_label_pad;
    // The labels' anchor x past an edge, for a plot `pw` wide.
    let y_anchor_x = |edge: &YEdge, pw: f64| if edge.right { pw + y_gutter } else { -y_gutter };

    // ── Axis titles ───────────────────────────────────────────────────────
    // A title is drawn unless the style hides it (`axis_to_vl` sets
    // `title: null` only for an explicit `visible: false`); the text is the
    // authored label, else the field's own name.
    let x_title_text = crate::common::wrapped_axis_title(
        &r.id,
        "x_label",
        &crate::common::axis_title_text(x_field, r.x_label.as_deref(), &r.field_labels)?,
        &ax_x.title.font,
        w,
        1.0, fonts,
    )?;
    let y_title_text = crate::common::wrapped_axis_title(
        &r.id,
        "y_label",
        &crate::common::axis_title_text(y_field, r.y_label.as_deref(), &r.field_labels)?,
        &ax_y.title.font,
        h,
        1.0, fonts,
    )?;
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_text, fonts)?;
    let y_title_w = crate::common::widest_title_line(&ax_y.title, &y_title_text, fonts)?;
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_text);
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_text);
    // Where the rotated title starts from: the row labels' outer edge, which
    // `axisLayout` reads off the axis bounds (`s = -bounds.x1`, `vega.js:20389`)
    // before `axisTitleLayout` (`vega.js:20422-20432`) sits the title `padding`
    // past it. Own-side labels start at the gutter and grow toward the plot;
    // away-side ones end there and grow left.
    // Hidden labels leave the axis its ticks, which the title clears.
    let y_tick_edge = if y_labels_hidden { y_tick_len } else { 0.0 };
    let y_label_edge = y_label_specs.iter().fold(if y_right { y_tick_edge } else { -y_tick_edge }, |edge, &(lw, extra_row)| {
        let ink = y_ink(&y_edges[0], y_anchor_x(&y_edges[0], 0.0), 0.0, lw, extra_row);
        if y_right { edge.max(ink.x2) } else { edge.min(ink.x1) }
    });
    // How far the x labels reach below their row's anchor: the bottom title
    // sits `padding` past it (`s = bounds.y2`, `vega.js:20403-20405`).
    let x_label_reach = match &x_dates {
        Some(axis) => axis.label_reach(),
        None => x_label_ws.iter().fold(0.0_f64, |reach, lw| {
            let mut ink = vega_fit::text_ink(0.0, 0.0, *lw, x_label_fs, x_align, x_baseline);
            if x_angle != 0.0 {
                ink = ink.rotate(x_angle, 0.0, 0.0);
            }
            reach.max(match x_edge {
                crate::common::XEdge::Bottom => ink.y2,
                crate::common::XEdge::Top => -ink.y1,
            })
        }),
    };

    // ── Legend ────────────────────────────────────────────────────────────
    let has_legend = r.legend.visible;
    let legend_edge = r.legend.position.edge.as_str();
    let legend_floats = r.legend.position.overlay;
    let grad_id = format!("heat_grad-{}", crate::contract::safe_svg_id(&r.id));
    let grad_id = grad_id.as_str();
    let leg_label_font = &r.legend.label.font;
    let leg_label_size = leg_label_font.size_or(11.0);
    let leg_label_family = leg_label_font.family_or("sans-serif");
    let leg_title_font = &r.legend.title.font;
    let leg_title_size = leg_title_font.size_or(12.0);
    let leg_title_family = leg_title_font.family_or("sans-serif");
    let leg_label_offset = r
        .legend
        .label
        .padding
        .ok_or("heatmap: legend.label.padding absent (theme required)")?;
    let leg_fmt_spec = r.style.tooltip_format.as_ref().map(|f| f.spec.as_str());
    let leg_labels = match &coloring {
        Coloring::Gradient { ticks, scale, .. } => legend_labels(ticks, leg_fmt_spec, scale.as_ref())?,
        Coloring::Categories(_) | Coloring::DateGradient { .. } => Vec::new(),
    };
    // A time-scale legend's labels follow its drawn length: two ticks below
    // a 200px bar, four at 200.
    let date_label_widths = match &coloring {
        Coloring::DateGradient { min, max } => {
            let measure = |length: f64| -> Result<Option<f64>, String> {
                date_legend_values(*min, *max, length, r.legend.values.as_deref())?
                    .iter()
                    .try_fold(None, |widest: Option<f64>, d| {
                        let w = fonts.measure_shaped(&legend_date_label(*d)?, leg_label_family, leg_label_size)?;
                        Ok(Some(widest.map_or(w, |widest| widest.max(w))))
                    })
            };
            Some((measure(VEGA_GRADIENT_LENGTH_MIN)?, measure(VEGA_GRADIENT_LENGTH_MAX)?))
        }
        _ => None,
    };
    let leg_title_text = legend_title_text(color_field, &r.legend, &r.field_labels)?;
    let leg_title_w = if leg_title_text.is_empty() {
        0.0
    } else {
        fonts.measure_shaped(&leg_title_text, leg_title_family, leg_title_size)?
    };
    // A nominal color's symbol legend: a fill-only square per category. The
    // mirror's ghost rule layer strokes the same color, which turns a
    // numeric gradient into a symbol legend too: a stroked circle per rung.
    let ladder_symbols = mirror_active && !matches!(coloring, Coloring::Categories(_));
    let ladder: Vec<(String, String)> = match &coloring {
        Coloring::Gradient { min, max, ticks, scale } if ladder_symbols => leg_labels
            .iter()
            .zip(ticks)
            .map(|(label, &tick)| {
                Ok((label.clone(), GradientColors::new(&r.style.color_gradient, scale.as_ref()).at(*min, *max, tick)?))
            })
            .collect::<Result<_, String>>()?,
        Coloring::DateGradient { min, max } if ladder_symbols => {
            date_legend_ticks(*min, *max, SYMBOL_LEGEND_TICK_COUNT, r.legend.values.as_deref())?
                .into_iter()
                .map(|day| Ok((legend_date_label(day)?, self::date_color(*min, *max, day))))
                .collect::<Result<_, String>>()?
        }
        _ => Vec::new(),
    };
    let symbol_entries: Vec<LegendEntry> = match &coloring {
        Coloring::Categories(scale) => category_legend_entries(scale, r.legend.values.as_deref()),
        _ => ladder
            .iter()
            .map(|(label, fill)| LegendEntry { label, fill, symbol: Some(LegendSymbol::Circle), series_key: None })
            .collect(),
    };
    let symbol_legend = (has_legend && (ladder_symbols || matches!(coloring, Coloring::Categories(_))))
        .then(|| vega_legend(&symbol_entries, false, &leg_title_text, &r.legend, fonts))
        .transpose()?;
    let gradient =
        has_legend && !ladder_symbols && matches!(coloring, Coloring::Gradient { .. } | Coloring::DateGradient { .. });
    let vertical_gradient = gradient && r.legend.direction == "vertical";
    let gradient_legend = match &coloring {
        Coloring::Gradient { min, max, ticks, scale } if gradient => {
            let colors = GradientColors::new(&r.style.color_gradient, scale.as_ref());
            Some(GradientLegend::resolve(&r, colors, grad_id, (*min, *max, ticks), &leg_labels, fonts)?.with_title(
                &r.legend,
                leg_title_text.clone(),
                fonts,
            )?)
        }
        // A horizontal date bar's box holds no labels; its ladder is drawn at paint, by length.
        Coloring::DateGradient { min, max } if gradient && !vertical_gradient => Some(
            GradientLegend::over(&r.legend, GradientPaint::Stops(&[]), grad_id, (*min, *max, &[]), &[], fonts)?
                .with_title(&r.legend, leg_title_text.clone(), fonts)?,
        ),
        _ => None,
    };
    // The legend box `(w, h)` at plot size `(pw, ph)`: a vertical gradient
    // runs `clamp(ph, 64, 200)` beside its labels, a horizontal one
    // `horizontal_gradient_length` above them; either sits under the title
    // band, and the box is `ceil(max(title, entries))` wide.
    let legend_size = |pw: f64, ph: f64| -> Option<(f64, f64)> {
        if let Some(legend) = &symbol_legend {
            Some((legend.box_w, legend.box_h()))
        } else if let Some(legend) = &gradient_legend {
            let bar_len = if vertical_gradient {
                legend.bar_len(pw, ph)
            } else {
                horizontal_gradient_length(legend_edge, legend_floats, pw)
            };
            Some((legend.box_width(bar_len), legend.height(bar_len)))
        } else if vertical_gradient {
            let (short, long) = date_label_widths.expect("the numeric gradient is drawn by gradient_legend");
            let label_w = if gradient_length(ph) >= VEGA_GRADIENT_LENGTH_MAX { long } else { short };
            Some(gradient_legend_box(leg_title_w, leg_title_size, label_w, leg_label_offset, gradient_length(ph)))
        } else {
            None
        }
    };
    // The legend's origin in plot coordinates, set on every fit pass (the
    // last is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));

    // ── Title ─────────────────────────────────────────────────────────────
    // Heatmap is a plain unit spec here — a `multiples` chart took
    // `render_facet` above — no concat — so `_render_vl_artifact` runs `apply_title_overflow_to_spec`
    // over it like every other non-hconcat family: the compiled spec carries
    // the pass's own `title.limit` (568 on a 600-wide card with 16px padding).
    // Same law, same helper as pie and both line arms.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    title.warn_truncated(&r.id);

    // Each date label's block (both rows) and whether Vega's greedy overlap
    // pass keeps it, for a plot `pw` x `ph`: ink and visibility both follow
    // the band spacing, so they are read per fit pass.
    let y_date_ink = |edge: &YEdge, pw: f64, ph: f64| -> Vec<Bounds> {
        let ys = BandScale::new(y_domain.clone(), 0.0, ph, 0.0, 0.0);
        y_dates
            .iter()
            .flatten()
            .map(|label| {
                let cy = ys.position(&y_domain[label.domain_idx]).expect("a label names a band of its own domain")
                    + ys.bandwidth() / 2.0
                    - AXIS_TRANSLATE;
                y_ink(edge, y_anchor_x(edge, pw), cy, label.w1.max(label.w2).min(y_label_limit), !label.row2.is_empty())
            })
            .collect()
    };
    let y_date_visible = |pw: f64, ph: f64| -> Vec<bool> {
        let ink = y_date_ink(&y_edges[0], pw, ph);
        match y_greedy_gap {
            Some(gap) => vega_fit::greedy_visible(&ink, gap),
            None => vec![true; ink.len()],
        }
    };

    // ── Plot rect: Vega's autosize-fit negotiation ────────────────────────
    let m = frame.fit(&title, |pw, ph, b| {
        // The y axis's own bounds join `xBounds`, the x axis's `yBounds`
        // (`ViewLayout.js:62-65`); the cells are bare rect marks.
        let mut y_axis = Bounds::new(0.0, 0.0, pw, ph);
        let ys = BandScale::new(y_domain.clone(), 0.0, ph, 0.0, 0.0);
        // The axis group sits a crisp half pixel above its labels' own
        // coordinates in the bounds Vega reads (`axis.js:77`), which only
        // shows where a label overhangs the plot top.
        for edge in &y_edges {
            if y_labels_hidden {
                // Hidden labels leave the axis its ticks alone.
                if y_tick_len > 0.0 {
                    let (x1, x2) = if edge.right { (pw, pw + y_tick_len) } else { (-y_tick_len, 0.0) };
                    for cat in &y_label_bands {
                        let Some(by) = ys.position(cat) else { continue };
                        let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE;
                        y_axis.union(&Bounds::new(x1, cy, x2, cy));
                    }
                }
            } else if y_dates.is_some() {
                let visible = y_date_visible(pw, ph);
                for (ink, _) in y_date_ink(edge, pw, ph).iter().zip(&visible).filter(|(_, v)| **v) {
                    y_axis.union(ink);
                }
            } else {
                let ws = edge.relabeled.as_ref().map_or(&y_label_ws, |(_, ws)| ws);
                for (cat, lw) in y_label_bands.iter().zip(ws) {
                    let Some(by) = ys.position(cat) else { continue };
                    let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE;
                    y_axis.union(&y_ink(edge, y_anchor_x(edge, pw), cy, *lw, false));
                }
            }
        }
        if y_title_extra > 0.0 {
            let x = if y_right { pw + y_label_edge + y_title_extra } else { y_label_edge - y_title_extra };
            y_axis.union(&Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
            if !y_right {
                y_axis.union(&Bounds::new(x, 0.0, 0.0, ph));
            } else {
                y_axis.union(&Bounds::new(pw, 0.0, x, ph));
            }
        }
        b.union(&y_axis);
        let reach = r.style.rect_mark.stroke_reach();
        b.union(&Bounds::new(-reach, -reach, pw + reach, ph + reach));
        let mut x_axis = Bounds::new(0.0, 0.0, pw, ph);
        let xs = BandScale::new(x_domain.clone(), 0.0, pw, 0.0, 0.0);
        let row_y = x_edge.at(ph, x_axis_offset + x_tick_len + x_label_pad);
        if let Some(axis) = &x_dates {
            axis.union_ink(&mut x_axis, pw, ph);
        }
        for (cat, lw) in x_domain.iter().zip(&x_label_ws).filter(|_| x_dates.is_none()) {
            let Some(bx) = xs.position(cat) else { continue };
            let cx = bx + xs.bandwidth() / 2.0;
            let mut ink =
                vega_fit::text_ink(cx, row_y, *lw, x_label_fs, x_align, x_baseline);
            if x_angle != 0.0 {
                // Vega rotates the text box about its anchor
                // (`text.js bound()` → `Bounds.rotate`).
                ink = ink.rotate(x_angle, cx, row_y);
            }
            x_axis.union(&ink);
        }
        b.union(&x_axis);
        if x_title_extra > 0.0 {
            let (y1, y2) = match x_edge {
                crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(0.0) + x_title_extra),
                crate::common::XEdge::Top => (-((-b.y1).max(0.0) + x_title_extra), ph),
            };
            x_axis.union(&Bounds::new(pw / 2.0 - x_title_w / 2.0, y1, pw / 2.0 + x_title_w / 2.0, y2));
            b.union(&x_axis);
        }
        // Only the legend's own axis joins the fit (probed at nine spec
        // heights whose right box overhangs the plot by 1–70px, the
        // bottom margin stayed on the x-label band every time).
        if let Some(size) = legend_size(pw, ph) {
            let origin = legend_origin(&r.legend.position, (y_axis.x1, y_axis.x2), (0.0, x_axis.y2), (pw, ph), size);
            legend_xy.set(origin);
            if let Some(reach) = legend_fit_bounds(&r.legend.position, origin, size) {
                b.union(&reach);
            }
        }
    });
    let (plot_left, plot_top) = frame.origin(&m);
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    let x_scale = BandScale::new(x_domain.clone(), 0.0, plot_w, 0.0, 0.0);
    let y_scale = BandScale::new(y_domain.clone(), 0.0, plot_h, 0.0, 0.0);
    let cell_w = x_scale.bandwidth();
    let cell_h = y_scale.bandwidth();

    // ── SVG emission ──────────────────────────────────────────────────────
    let mut svg = String::with_capacity(32768);
    // Namespaced so two heatmaps on one board never collide
    // (`converters/chart.py::_namespace_svg_ids`'s law, ported at
    // `contract::safe_svg_id`).
    frame.open(&mut svg);
    title.paint(&mut svg, m.title_pos, (plot_left, plot_top));

    if let (true, Coloring::DateGradient { min, max }) = (vertical_gradient, &coloring) {
        emit_date_gradient_def(&mut svg, grad_id, *min, *max)?;
    }

    // ── Cells ─────────────────────────────────────────────────────────────
    let _ = write!(svg, "<g transform=\"translate({plot_left:.2},{plot_top:.2})\">");
    let cells = CellPaint {
        x_field,
        y_field,
        color_field,
        fill: match &coloring {
            Coloring::Gradient { min, max, scale, .. } => CellFill::Gradient {
                colors: GradientColors::new(&r.style.color_gradient, scale.as_ref()),
                min: *min,
                max: *max,
            },
            Coloring::Categories(scale) => CellFill::Categories(scale),
            Coloring::DateGradient { min, max } => CellFill::Dates { min: *min, max: *max },
        },
        opacity: r.style.rect_mark.opacity.unwrap_or(1.0),
        stroke_attr: r.style.rect_mark.stroke_attr()?,
        stroke_reach: r.style.rect_mark.stroke_reach(),
        tooltip: tooltip_value_title(&r, &rows)?.zip(r.style.tooltip_format.as_ref()),
        link: r.datum_link((x_grain.is_some(), y_grain.is_some(), date_color)),
        mirror: mirror_active,
        ghost_titles: None,
    };
    paint_cells(&mut svg, &rows, &x_scale, &y_scale, &cells)?;
    let _ = write!(svg, "</g>");

    // ── X axis ────────────────────────────────────────────────────────────
    // The axis group sits at (0.5, plot_h + offset + 0.5); a label keeps its
    // exact band position inside it, a tick snaps to a whole pixel first
    // (`floor(center) + 0.5` — probed over 64 band centers, widths 600–615).
    let x_sign = x_edge.sign();
    let x_axis_top = plot_top + x_edge.at(plot_h, x_axis_offset) + AXIS_TRANSLATE;
    let x_label_row = x_axis_top + x_sign * (x_tick_len + x_label_pad);
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    let x_tick_color = crate::common::required_ink(ax_x.ticks.color.as_deref(), "heatmap", "axis_x.ticks.color")?;
    let x_tick_w_px = ax_x.ticks.width.unwrap_or(1.0);
    if let Some(axis) = &x_dates {
        axis.paint(&mut svg, ax_x, plot_left, plot_w, plot_top + x_edge.edge_y(plot_h))?;
    } else {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (plot_left + AXIS_TRANSLATE, x_axis_top))?;
    }
    for band_val in x_domain.iter().filter(|_| x_dates.is_none()) {
        let Some(band_x) = x_scale.position(band_val) else { continue };
        let center = band_x + cell_w / 2.0;
        if x_tick_visible {
            let tick_x = plot_left + center.floor() + AXIS_TRANSLATE;
            let tick_y2 = x_axis_top + x_sign * x_tick_len;
            let _ = write!(
                svg,
                "<line x1=\"{tick_x:.2}\" y1=\"{x_axis_top:.2}\" \
                 x2=\"{tick_x:.2}\" y2=\"{tick_y2:.2}\" \
                 stroke=\"{x_tick_color}\" stroke-width=\"{x_tick_w_px}\"/>", x_tick_color = xml_escape_attr(x_tick_color)
            );
        }
        let label_x = plot_left + center;
        let dy = vega_fit::baseline_shift(x_baseline, x_label_fs);
        let anchor = svg_text_anchor(x_align);
        if x_angle == 0.0 {
            let _ = write!(
                svg,
                "<text x=\"{label_x:.2}\" y=\"{:.2}\" text-anchor=\"{anchor}\" \
                 font-family=\"{}\" font-size=\"{x_label_fs}\" \
                 font-weight=\"{x_label_weight}\" fill=\"{x_label_color}\">{}</text>",
                x_label_row + dy,
                vega_escape_attr(x_label_family),
                xml_escape(band_val), anchor = xml_escape_attr(anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
            );
        } else {
            emit_rotated_x_label(
                &mut svg,
                (label_x, x_label_row, x_angle, dy),
                (anchor, x_label_family, x_label_fs, &x_label_weight, x_label_color),
                (band_val, ""),
                "",
            );
        }
    }

    let x_title_y = x_label_row
        + x_sign * (x_label_reach + ax_x.title.padding.unwrap_or(10.0))
        + vega_fit::baseline_shift(x_edge.baseline(), ax_x.title.font.size_or(11.0));
    crate::common::emit_axis_title_at(&mut svg, &x_title_text, &ax_x.title, plot_left + plot_w / 2.0, x_title_y, None);
    if x_dates.is_none() {
        crate::contract::axis_group_close(&mut svg);
    }

    // ── Y axis ────────────────────────────────────────────────────────────
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let y_tick_color = crate::common::required_ink(ax_y.ticks.color.as_deref(), "heatmap", "axis_y.ticks.color")?;
    let y_tick_w_px = ax_y.ticks.width.unwrap_or(1.0);
    let y_visible = y_date_visible(plot_w, plot_h);
    let y_rows: Vec<(&str, &str)> = match &y_dates {
        Some(labels) => labels.iter().map(|l| (y_domain[l.domain_idx].as_str(), l.row1.as_str())).collect(),
        None => y_domain.iter().map(|d| (d.as_str(), d.as_str())).collect(),
    };
    for (edge_idx, edge) in y_edges.iter().enumerate() {
        let y_text_anchor = svg_text_anchor(edge.align);
        let y_axis_x = plot_left + if edge.right { plot_w } else { 0.0 } + AXIS_TRANSLATE;
        let y_label_x = if edge.right { y_axis_x + y_gutter } else { y_axis_x - y_gutter };
        // One row per drawn label: every band for a category y, only the ladder's
        // openers (and a second context row under them) for a date y.
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (y_axis_x, plot_top))?;
        for (i, &(band_val, row1)) in y_rows.iter().enumerate() {
            let row1 = edge.relabeled.as_ref().map_or(row1, |(texts, _)| texts[i].as_str());
            let Some(band_y) = y_scale.position(band_val) else { continue };
            let center = band_y + cell_h / 2.0;
            if y_tick_visible {
                let tick_y = plot_top + center.floor() + AXIS_TRANSLATE;
                let _ = write!(
                    svg,
                    "<line x1=\"{:.2}\" y1=\"{tick_y:.2}\" x2=\"{y_axis_x:.2}\" y2=\"{tick_y:.2}\" \
                     stroke=\"{y_tick_color}\" stroke-width=\"{y_tick_w_px}\"/>",
                    if edge.right { y_axis_x + y_tick_len } else { y_axis_x - y_tick_len },
                    y_tick_color = xml_escape_attr(y_tick_color)
                );
            }
            if y_labels_hidden {
                continue;
            }
            let row2 = y_dates.as_ref().map(|labels| labels[i].row2.as_str()).unwrap_or("");
            let hidden = if y_dates.is_some() && !y_visible[i] { " opacity=\"0\"" } else { "" };
            if y_angle != 0.0 {
                emit_rotated_x_label(
                    &mut svg,
                    (y_label_x, plot_top + center, y_angle, vega_fit::baseline_shift(edge.baseline, y_label_fs)),
                    (y_text_anchor, y_label_family, y_label_fs, &y_label_weight, y_label_color),
                    (row1, row2),
                    hidden,
                );
                continue;
            }
            let row1_y = plot_top + center + vega_fit::baseline_shift("middle", y_label_fs);
            for (text, y) in [(row1, row1_y), (row2, row1_y + vega_fit::label_row_advance(y_label_fs))] {
                if text.is_empty() {
                    continue;
                }
                let _ = write!(
                    svg,
                    "<text x=\"{y_label_x:.2}\" y=\"{y:.2}\" text-anchor=\"{y_text_anchor}\" \
                     font-family=\"{}\" font-size=\"{y_label_fs}\" \
                     font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\"{hidden}>{}</text>",
                    vega_escape_attr(y_label_family),
                    xml_escape(text), y_text_anchor = xml_escape_attr(y_text_anchor), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
                );
            }
        }
        // The ghost axis carries no title (`mirror_axis.py` nulls it).
        if edge_idx == 0 {
            crate::common::emit_axis_title_rotated(
                &mut svg,
                &y_title_text,
                &ax_y.title,
                y_axis_x + y_label_edge,
                plot_top + plot_h / 2.0,
                if y_right { 1.0 } else { -1.0 },
                None,
            );
        }
        crate::contract::axis_group_close(&mut svg);
    }

    // ── Legend ────────────────────────────────────────────────────────────
    // A symbol legend slides inside its own paint arm; a gradient block
    // slides here, over the box `legend_size` measures.
    let (grad_dx, grad_dy) = match (symbol_legend.is_none(), legend_size(plot_w, plot_h)) {
        (true, Some(block)) => slide_block(&r.id, &r.legend.position, block, (plot_w, plot_h))?,
        _ => (0.0, 0.0),
    };
    let (leg_x, leg_y) = (plot_left + legend_xy.get().0 + grad_dx, plot_top + legend_xy.get().1 + grad_dy);
    if let Some(legend) = gradient_legend.as_ref().filter(|_| !vertical_gradient) {
        if let Coloring::DateGradient { min, max } = &coloring {
            let dates = date_scale(*min, *max);
            GradientAt::paint(&r.legend, None, &dates, (grad_id, &leg_title_text), fonts, (plot_w, plot_h), (&mut svg, leg_x, leg_y))?;
        } else {
            legend.paint(&mut svg, leg_x, leg_y, horizontal_gradient_length(legend_edge, legend_floats, plot_w));
        }
    }
    let paint_vertical_gradient = |svg: &mut String, range: (f64, f64), values: &[f64], labels: &[String]| {
        VerticalGradient {
            gradient_id: grad_id,
            title: &leg_title_text,
            range,
            values,
            labels,
            length: gradient_length(plot_h),
        }
        .paint(svg, &r.legend, leg_x, leg_y)
    };
    match (&symbol_legend, &coloring) {
        (Some(legend), _) => {
            let (dx, dy) = legend_slide(&r.id, &r.legend.position, legend, (plot_w, plot_h))?;
            emit_vega_legend(&mut svg, &symbol_entries, legend, &r.legend, LegendSymbol::FilledSquare, leg_x + dx, leg_y + dy, false);
        }
        (None, Coloring::Gradient { .. }) if vertical_gradient => {
            if let Some(legend) = &gradient_legend {
                legend.paint(&mut svg, leg_x, leg_y, legend.bar_len(plot_w, plot_h));
            }
        }
        (None, Coloring::DateGradient { min, max }) if vertical_gradient => {
            let ticks = date_legend_values(*min, *max, plot_h, r.legend.values.as_deref())?;
            let labels = ticks.iter().map(|d| legend_date_label(*d)).collect::<Result<Vec<_>, _>>()?;
            paint_vertical_gradient(&mut svg, (*min, *max), &ticks, &labels)?;
        }
        _ => {}
    }

    svg.push_str("</svg>");
    Ok(svg)
}

/// One drawn edge of the y category axis: whether it sits on the right, and
/// how its labels anchor.
/// Whether the row labels sit on their own side of the axis (`left` on a left axis, `right` on a
/// right one): production bakes `labelPadding` from their widest. `center`, an upper/lower
/// `font.case` and a date y (both paint through a `labelExpr`) take Vega-Lite's away-side default.
pub(crate) fn y_labels_own_side(ax_y: &crate::common::AxisStyle, y_is_date: bool) -> bool {
    let own_align = if ax_y.position.as_deref() == Some("right") { "right" } else { "left" };
    ax_y.labels.align.as_deref() == Some(own_align)
        && !matches!(ax_y.labels.font.case.as_deref().unwrap_or("none"), "upper" | "lower")
        && !y_is_date
}

struct YEdge {
    right: bool,
    align: &'static str,
    baseline: &'static str,
    /// The mirror ghost's own label text and width per category, when its format relabels them.
    relabeled: Option<(Vec<String>, Vec<f64>)>,
}

/// What the color channel paints, owned: a gradient over a measure, or one
/// palette slot per category.
enum Coloring {
    Gradient { min: f64, max: f64, ticks: Vec<f64>, scale: Option<ContinuousColor> },
    /// A date-like color: Vega's time scale over its `heatmap` range.
    DateGradient { min: f64, max: f64 },
    Categories(CategoryScale),
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

/// What paints one heatmap cell, common to a single chart and to every panel
/// of a facet composite.
pub(crate) struct CellPaint<'a> {
    pub(crate) x_field: &'a str,
    pub(crate) y_field: &'a str,
    pub(crate) color_field: &'a str,
    pub(crate) fill: CellFill<'a>,
    pub(crate) opacity: f64,
    /// Pre-rendered ` stroke="…" stroke-width="…"` attributes, or empty.
    pub(crate) stroke_attr: String,
    /// How far the stroke grows a cell's bounds.
    pub(crate) stroke_reach: f64,
    /// The tooltip's value title and format, or `None` when the chart paints
    /// no tooltip.
    pub(crate) tooltip: Option<(String, &'a FormatSlot)>,
    pub(crate) link: Option<crate::href::DatumLink<'a>>,
    /// `axis_y.mirror`: an invisible rule layer carries each cell's label back to the origin.
    pub(crate) mirror: bool,
    /// A facet panel's rules carry the plain channel titles instead of the cell's structured label.
    pub(crate) ghost_titles: Option<(&'a str, &'a str)>,
}

/// The calendar labels a date-valued y band axis paints, or `None` for a
/// category y (also a date column with no detectable calendar grain, which
/// production leaves as raw band values), and the `labelSeparation` of the
/// Vega greedy overlap pass the leftovers go through when even the coarsest
/// cadence collides. Refuses by name every date-y setting the ladder does
/// not model.
fn y_date_labels(
    domain: &[String],
    grain: Option<TimeUnit>,
    ax_y: &AxisStyle,
    chart_h: f64,
    usable_ratio: Option<f64>,
    fonts: &FontBook,
    family: &str,
    font_size: f64,
) -> Result<(Option<Vec<AxisLabel>>, Option<f64>), String> {
    let Some(data_tu) = grain else {
        return Ok((None, None));
    };
    if let Some(cell) = domain.iter().find(|d| parse_iso_date(d).is_none()) {
        return Err(crate::refuse!("heatmap: a date y holding {cell:?}, which is not an ISO date, is not supported"));
    }
    let labels_cfg = &ax_y.labels;
    let authored = AuthoredTimeFormat::parse(
        "heatmap",
        axis_time_format(labels_cfg.spec(), &ax_y.grain_formats, Some(data_tu)),
    )?;
    let usable_ratio = usable_ratio.ok_or("heatmap: label_usable_ratio absent (theme required for the y axis ladder)")?;
    let overlap = labels_cfg.overlap.as_ref().ok_or("heatmap: axis_y.labels.overlap absent (theme required)")?;
    let min_gap = labels_cfg.min_gap.ok_or("heatmap: axis_y.labels.min_gap absent (theme required)")?;
    let ladder = resolve_vertical_temporal_labels(
        domain,
        data_tu,
        chart_h * usable_ratio,
        ax_y.fiscal_year_start_month,
        overlap.skip,
        font_size,
        min_gap,
        authored,
    )?;
    let greedy_gap = (!ladder.fits && overlap.skip).then_some(min_gap);
    let labels = ladder.labels;
    let labels = match labels_cfg.expr.as_deref() {
        Some(expr) => {
            let n = crate::common::parse_labels_expr_slice_n(expr).map_err(|e| format!("heatmap: {e}"))?;
            rewrite_with_expr(labels, domain, n)
        }
        None => fold_steep(labels, labels_cfg.angle, data_tu),
    };
    let labels = labels
        .into_iter()
        .map(|l| AxisLabel::measured(l.domain_idx, l.row1, l.row2, &|t| fonts.measure(t, family, font_size)))
        .collect::<Result<Vec<_>, _>>()?;
    Ok((Some(labels), greedy_gap))
}

/// The tooltip value title — `Some` when the chart paints a structured
/// tooltip (front-end contract: `_heatmap_roles`, ported -- both
/// independents (x, y) header as bare `ROLE_HEADER` rows, the color measure
/// is the sole dependent row). The title is `format_display_text(color_field,
/// ..., font=chart.legend.title.font)` UNCONDITIONALLY -- unlike
/// `legend_title_text`, it is never gated on `legend.title.visible` (the
/// tooltip renders with content the legend itself may be hiding).
///
/// `header_tooltip_field`'s own type inference would call an all-date-like
/// x/y "temporal" and format it through `tooltip_header_date` (the full
/// bucket, not the raw string) -- this port has no ported detection for that.
/// Same posture as line/area's own unmodeled shapes (a wide-measures fold, a
/// `chart.layers` combo, band-step): gated off silently rather than refused,
/// since the cell geometry already renders correctly today and a new `Err`
/// here would regress an already-passing pixel row for an ordinary shape (a
/// month×category heatmap, say) that just happens to have a date-shaped axis.
pub(crate) fn tooltip_value_title(
    r: &HeatmapResolved,
    rows: &[serde_json::Map<String, Value>],
) -> Result<Option<String>, String> {
    let tooltip_labels = r.style.tooltip_format.as_ref().is_some_and(|f| !f.spec.is_empty())
        && [r.x.as_str(), r.y.as_str()].iter().all(|field| {
            !rows
                .iter()
                .all(|row| crate::families::temporal::parse_iso_date(&row_str(row, field)).is_some())
        });
    if !tooltip_labels {
        return Ok(None);
    }
    // The theme cascade guarantees every resolved chart's
    // `legend.title.font` is populated (`core/AGENTS.md`'s "Theme YAML
    // completeness") -- a caller reaching this with `tooltip_labels`
    // true and no case is a genuine resolved-payload defect, not a
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

/// One `<rect>` per row: the band cell at its x/y band, filled from the
/// gradient (and, with a tooltip, wrapped in its `marks` group).
pub(crate) fn paint_cells(
    svg: &mut String,
    rows: &[serde_json::Map<String, Value>],
    x_scale: &BandScale,
    y_scale: &BandScale,
    c: &CellPaint,
) -> Result<(), String> {
    let (cell_w, cell_h) = (x_scale.bandwidth(), y_scale.bandwidth());
    let mut ghosts: Vec<(String, f64, f64, String, Option<String>)> = Vec::new();
    for row in rows {
        let xv = row_str(row, c.x_field);
        let yv = row_str(row, c.y_field);
        let Some(cx) = x_scale.position(&xv) else { continue };
        let Some(cy) = y_scale.position(&yv) else { continue };
        // A category's tooltip still formats its value as a number, which a
        // name is not: Vega prints `NaN`.
        let (fill, cv) = match c.fill {
            CellFill::Gradient { colors, min, max } => {
                let Some(cv) = row_num(row, c.color_field) else { continue };
                (colors.at(min, max, cv)?, cv)
            }
            CellFill::Dates { min, max } => {
                let Some(day) = row.get(c.color_field).and_then(date_cell_day) else { continue };
                // The tooltip prints the epoch milliseconds as a number.
                (date_color(min, max, day), day * MS_PER_DAY)
            }
            CellFill::Categories(scale) => {
                let null_cell = row.get(c.color_field).is_none_or(Value::is_null);
                let name = if null_cell { NULL_CATEGORY.to_string() } else { row_str(row, c.color_field) };
                if name == NULL_CATEGORY && null_cell != scale.null_is_js {
                    ("none".to_string(), f64::NAN)
                } else {
                    let slot = scale
                        .domain
                        .iter()
                        .position(|d| *d == name)
                        .ok_or_else(|| format!("heatmap: color {name:?} is not in its own category domain"))?;
                    (scale.fills[slot].clone(), f64::NAN)
                }
            }
        };
        let mut ghost_label = None;
        let aria = match &c.tooltip {
            Some((value_title, value_format)) => {
                let tt = crate::contract::HeatmapTooltip {
                    x: &xv,
                    y: &yv,
                    value_title,
                    value_format,
                    value: cv,
                };
                if let Some((x_title, y_title)) = c.ghost_titles {
                    let value_s = value_format.paint(cv).map_err(|e| format!("heatmap: tooltip value {e}"))?;
                    ghost_label = Some(crate::contract::native_label(&[(x_title, xv.clone()), (y_title, yv.clone()), (value_title, value_s)]));
                }
                Some(crate::contract::heatmap_aria_label(&tt)?)
            }
            None => None,
        };
        let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::RectMark)?;
        if aria.is_some() {
            crate::contract::mark_group_open(svg, "rect", "marks")?;
        }
        let href = c.link.as_ref().map(|link| link.href(row)).transpose()?;
        let anchor = c.link.as_ref().and_then(|link| link.open.anchor(href.as_deref()));
        svg.push_str(&crate::href::anchor_open(anchor));
        let _ = write!(
            svg,
            "<rect x=\"{cx:.2}\" y=\"{cy:.2}\" width=\"{cell_w:.2}\" height=\"{cell_h:.2}\" \
             fill=\"{fill}\"{stroke_attr} opacity=\"{opacity}\"{label_attr}/>",
            fill = xml_escape_attr(&fill),
            stroke_attr = c.stroke_attr,
            opacity = c.opacity,
        );
        svg.push_str(crate::href::anchor_close(anchor));
        if aria.is_some() {
            crate::contract::mark_group_close(svg);
        }
        if let (true, Some(aria)) = (c.mirror, ghost_label.or(aria)) {
            ghosts.push((aria, cx + cell_w / 2.0, cy + cell_h / 2.0, fill, href));
        }
    }
    if !ghosts.is_empty() {
        let rules: Vec<crate::contract::GhostRule> = ghosts
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule {
                aria: Some(aria),
                x: *x,
                y: *y,
                stroke,
                anchor: c.link.as_ref().and_then(|link| link.open.anchor(href.as_deref())),
            })
            .collect();
        crate::contract::mirror_ghost_layer(svg, &rules, crate::contract::GhostRun::ToOrigin)?;
    }
    Ok(())
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

/// Label widths in domain order, each capped at what Vega-Lite will actually
/// draw (`labelLimit`).
fn measure_all(
    fonts: &FontBook,
    labels: &[String],
    family: &str,
    size: f64,
    limit: f64,
) -> Result<Vec<f64>, String> {
    labels.iter().map(|s| fonts.measure(s, family, size).map(|w| w.min(limit))).collect()
}

/// SVG `text-anchor` for a Vega `align`.
/// A rotated x label: translate to the anchor, rotate, then the baseline
/// shift (Vega's SVG text transform for a rotated item). `at` is
/// `(x, anchor_y, angle, baseline dy)`, `font` is `(text-anchor, family, size,
/// weight, fill)`.
pub(crate) fn emit_rotated_x_label(
    svg: &mut String,
    (x, anchor_y, angle, dy): (f64, f64, f64, f64),
    (text_anchor, family, size, weight, fill): (&str, &str, f64, &str, &str),
    (text, row2): (&str, &str),
    opacity: &str,
) {
    // A second row rides the same rotated block, one line advance below.
    let rows = if row2.is_empty() {
        xml_escape(text)
    } else {
        format!(
            "<tspan>{}</tspan><tspan x=\"0\" dy=\"{}\">{}</tspan>",
            xml_escape(text),
            vega_fit::label_row_advance(size),
            xml_escape(row2)
        )
    };
    let _ = write!(
        svg,
        "<text text-anchor=\"{}\" transform=\"translate({x:.2},{anchor_y:.2}) rotate({angle}) translate(0,{dy})\" \
         font-family=\"{}\" font-size=\"{size}\" font-weight=\"{}\" fill=\"{}\"{opacity}>{rows}</text>",
        xml_escape_attr(text_anchor),
        vega_escape_attr(family),
        xml_escape_attr(weight),
        xml_escape_attr(fill),
    );
}

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
) -> Result<(f64, f64, Vec<f64>), String> {
    // `is_vega_numeric_value`: a number or a boolean; a numeric string is nominal.
    let vega_numeric = |row: &serde_json::Map<String, Value>| match row.get(field)? {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    };
    let (data_lo, data_hi) =
        rows.iter().filter_map(vega_numeric).fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
    if !data_lo.is_finite() {
        return Err(crate::refuse!("heatmap: a color field {field:?} with no numeric values is not supported"));
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
fn date_legend_values(min: f64, max: f64, plot_h: f64, authored: Option<&[String]>) -> Result<Vec<f64>, String> {
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

/// The gradient of a time-scale legend: a stop at each end and at each of the
/// scale's 15 ticks, each in the scale's own color. A zero-width domain has no
/// span to tick, so Vega samples a unit scale over the same range instead.
pub(crate) fn emit_date_gradient_def(out: &mut String, id: &str, min: f64, max: f64) -> Result<(), String> {
    if min == max {
        crate::families::legend::emit_ramp_gradient_def(out, id, true, |t| date_color(0.0, 1.0, t));
        return Ok(());
    }
    let ticks = date_ticks(min, max, GRADIENT_STOP_TICKS)?;
    emit_scale_gradient_def(out, id, (min, max), &ticks, |day| date_color(min, max, day), true);
    Ok(())
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

/// Emit a `<linearGradient>` into `<defs>`: vertical (y1=1 bottom, y2=0 top)
/// for the right legend, horizontal (x1=0 left, x2=1 right) for a top one.
pub(crate) fn emit_gradient_def(out: &mut String, id: &str, stops: &[String], vertical: bool) {
    let n = stops.len();
    let axis = if vertical { "x1=\"0\" x2=\"0\" y1=\"1\" y2=\"0\"" } else { "x1=\"0\" x2=\"1\" y1=\"0\" y2=\"0\"" };
    let _ = write!(out, "<defs><linearGradient id=\"{id}\" {axis}>", id = xml_escape_attr(id));
    for (i, stop) in stops.iter().enumerate() {
        let offset = if n == 1 {
            0.0
        } else {
            i as f64 / (n - 1) as f64
        };
        let _ = write!(out, "<stop offset=\"{offset:.4}\" stop-color=\"{stop}\"/>", stop = xml_escape_attr(stop));
    }
    let _ = write!(out, "</linearGradient></defs>");
}

/// The gradient legend's labels. Vega labels a diverging pivot that is no tick
/// at the ladder's tick precision, whatever the tooltip format.
pub(crate) fn legend_labels(ticks: &[f64], spec: Option<&str>, scale: Option<&ContinuousColor>) -> Result<Vec<String>, String> {
    ticks
        .iter()
        .map(|&v| match scale {
            Some(scale) if scale.pivot == Some(v) && !d3rs::ticks::d3_ticks(scale.lo, scale.hi, GRADIENT_NICE_TICK_COUNT).contains(&v) => {
                let step = d3rs::ticks::d3_tick_step(scale.lo, scale.hi, GRADIENT_NICE_TICK_COUNT);
                let precision = crate::families::color_scale::tick_precision(step);
                crate::number_locale::d3_format(&format!(",.{precision}f"), v)
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
    use super::{render, row_str, RectMark, TimeUnit};
    use crate::families::legend::unit_position;

    #[test]
    fn a_zero_width_color_domain_maps_to_the_middle() {
        assert_eq!(unit_position(5.0, 5.0, 5.0), 0.5);
        assert_eq!(unit_position(5.0, 0.0, 10.0), 0.5);
        assert_eq!(unit_position(0.0, 0.0, 10.0), 0.0);
    }

    #[test]
    fn a_rect_stroke_object_paints_its_color_and_width() {
        let mark: RectMark = serde_json::from_value(serde_json::json!({"stroke": {"color": "#000000", "width": 1.0}})).unwrap();
        assert_eq!(mark.stroke_attr().unwrap(), " stroke=\"#000000\" stroke-width=\"1\"");
        assert_eq!(mark.stroke_reach(), 1.0);
        assert_eq!(RectMark::default().stroke_attr().unwrap(), "");
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
            styled.stroke_attr().unwrap(),
            " stroke=\"#000\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"bevel\" stroke-dasharray=\"4,2.5\""
        );
        let colorless: RectMark = serde_json::from_value(serde_json::json!({"stroke": {"width": 2.0, "dasharray": "4 2"}})).unwrap();
        assert_eq!(colorless.stroke_attr().unwrap(), "");
        let hidden: RectMark = serde_json::from_value(serde_json::json!({
            "opacity": 0.0, "stroke": {"color": "#000", "width": 2.0}
        }))
        .unwrap();
        assert_eq!((hidden.stroke_attr().unwrap().as_str(), hidden.stroke_reach()), ("", 0.0));
    }

    use crate::text::FontBook;
    use crate::Payload;
    use super::{GradientColors, GradientLegend, HeatmapResolved};
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
                    "title": {"font": {"family": TEST_FONT, "size": 12.0, "weight": "600",
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
    /// away-side gutter even though `labels.align` still says `left`.
    #[test]
    fn an_uncased_measurement_falls_back_to_the_away_side_gutter() {
        let svg = render_json(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_y"]["labels"]["font"]["case"] = json!("upper");
        }));
        let max_w = max_measure(&["Accessories", "Electronics", "Tools"], 11.0);
        assert_eq!(plot_origin(&svg).0, 16.0 + (6.0 + max_w).ceil(), "{svg}");
        assert!(text_tag(&svg, "Accessories").contains("text-anchor=\"end\""), "{svg}");
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
    /// it errors rather than inventing one.
    #[test]
    fn a_color_field_with_no_numeric_rows_refuses() {
        let err = {
            let j = heatmap_payload(|j| {
                for row in j["rows"].as_array_mut().unwrap() {
                    row["revenue"] = json!(null);
                }
            });
            let p: Payload = serde_json::from_value(j).unwrap();
            render(&p, &test_fonts()).expect_err("refuses")
        };
        assert!(err.contains("revenue") && err.contains("numeric"), "{err}");
    }

    /// `header_tooltip_field`'s own type inference would call an
    /// all-date-like x "temporal" and reformat it -- this port has no
    /// ported detection for that, so it skips the tooltip wiring silently
    /// (same posture as line/area's own unmodeled shapes) rather than
    /// refusing the whole chart over a shape that renders correctly today.
    /// No corpus row hits this.
    #[test]
    fn a_date_like_x_skips_the_tooltip_rather_than_mislabel() {
        let j = heatmap_payload(|j| {
            j["rows"] = json!([
                {"region": "2024-01-01", "category": "Accessories", "revenue": 100},
                {"region": "2024-02-01", "category": "Accessories", "revenue": 200},
            ]);
        });
        let p: Payload = serde_json::from_value(j).unwrap();
        let svg = render(&p, &test_fonts()).expect("renders despite the unmodeled date-like x");
        assert!(!svg.contains("role-mark"), "no tooltip wiring for a date-like x: {svg}");
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
        assert_eq!(attr(&title, "y"), y - 17.0 + crate::vega_fit::baseline_shift("top", 12.0), "{svg}");
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
    fn an_axis_affix_beside_a_labels_expr_refuses_by_name() {
        for axis in ["axis_x", "axis_y"] {
            let p: Payload = serde_json::from_value(heatmap_payload(|j| {
                j["resolved"]["style"][axis]["labels"]["format"] = json!({"spec": ",.0f", "prefix": "US$"});
                j["resolved"]["style"][axis]["labels"]["expr"] = json!("datum.label");
            }))
            .unwrap();
            let err = render(&p, &test_fonts()).expect_err("refuses");
            assert!(err.contains(&format!("heatmap: {axis}.labels.format")) && err.contains("labels.expr"), "{err}");
        }
    }

    #[test]
    fn an_authored_x_tick_cadence_refuses_by_name() {
        let p: Payload = serde_json::from_value(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_x"]["ticks"]["time_unit"] = json!("yearmonth");
        }))
        .unwrap();
        let err = render(&p, &test_fonts()).expect_err("refuses");
        assert!(err.contains("heatmap: axis_x.ticks.time_unit \"yearmonth\""), "{err}");
    }

    #[test]
    fn a_bare_x_tick_step_refuses_by_name() {
        let p: Payload = serde_json::from_value(heatmap_payload(|j| {
            j["resolved"]["style"]["axis_x"]["ticks"]["step"] = json!(5.0);
        }))
        .unwrap();
        let err = render(&p, &test_fonts()).expect_err("refuses");
        assert!(err.contains("heatmap: axis_x.ticks.step"), "{err}");
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
        assert!(super::gradient_ladder(&rows(&[serde_json::json!("n/a"), serde_json::Value::Null]), "c", &gradient).is_err());
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
}
