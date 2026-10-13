//! Heatmap family — band×band grid with quantitative color ramp.
//!
//! The chrome is Vega's `autosize: fit` negotiation (`vega_fit::fit_plot`)
//! over the plot rect, both axes' label ink and the right gradient legend's
//! box; the cell colors and the legend's labeled ticks come off ONE
//! production tick ladder (`emitters/_channels.py`).
//!
//! Unsupported → clear Err:
//! - resolved_channels.color.mode == "conditional" present (heatmap's
//!   authored surface can no longer produce this from a real board;
//!   the guard exists only for a hand-written or stale resolved-JSON
//!   fixture, same as bar/pie/scatter)
//! - non-empty layers
//! - a vertical gradient legend above or below the plot, or a right-hand y
//!   axis — neither has a probed geometry, and no corpus board draws one

use std::collections::HashMap;
use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    dimension_sort_domain, row_num, row_str, AxisStyle, ChartSort, Font, FormatSlot, SortAggregate,
    StyleChannel, TitleStyle,
};
use crate::families::cartesian::BandScale;
use crate::families::heatmap_facet::{
    first_appearance, horizontal_gradient_length, refuse_unmodeled_x_labels, AxisLabel, GradientLegend, HeatmapXAxis,
};
use crate::families::legend::{
    emit_vega_legend, legend_fit_bounds, legend_origin, legend_slide, legend_title_text, refuse_slide_blocker,
    vega_legend, LegendConfig, LegendEntry, LegendSymbol,
    VEGA_LEGEND_TITLE_PADDING,
};
use crate::svg::{xml_escape, xml_escape_attr, vega_escape_attr};
use crate::families::temporal::{
    detect_time_unit, is_time_format, parse_iso_date, resolve_vertical_temporal_labels,
    x_values_are_temporal_candidates,
};
use crate::text::FontBook;
use crate::vega_fit::{self, Bounds};
use crate::Payload;

/// Vega-Lite's `gradientThickness` default: the color bar's width.
pub(crate) const VEGA_GRADIENT_THICKNESS: f64 = 16.0;
/// Vega-Lite's `gradientLength` default is `clamp(plot_height, 64, 200)`
/// (probed: spec heights 700 / 200 / 140 give bars of 200 / 116 / 64).
const VEGA_GRADIENT_LENGTH_MIN: f64 = 64.0;
const VEGA_GRADIENT_LENGTH_MAX: f64 = 200.0;
/// `utils.py::_BREATHING_ROOM_PX` — the gap production leaves between an
/// own-side label's near edge and the plot it grows toward.
pub(crate) const BREATHING_ROOM_PX: f64 = 4.0;
/// `defaults/default_config.yml`'s `gradient.nice_tick_count`. The resolved
/// payload carries no tick count, so the config default is the contract.
const GRADIENT_NICE_TICK_COUNT: usize = 6;
/// Vega's `config.axis.translate`: every axis group is drawn half a pixel in
/// from its anchor so 1px strokes land on a pixel boundary.
pub(crate) const AXIS_TRANSLATE: f64 = 0.5;

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
    /// Only checked for `mode == "conditional"`, at the entry gate. Heatmap's
    /// authored surface can no longer produce one from a real board
    /// (`conditional_formatting` retired), so this guards only a
    /// hand-written or stale resolved-JSON fixture, same as bar/pie/
    /// scatter's.
    #[serde(default)]
    pub(crate) resolved_channels: HashMap<String, StyleChannel>,
    #[serde(default)]
    pub(crate) layers: Vec<Value>,
    pub(crate) legend: LegendConfig,
    pub(crate) style: HeatmapStyle,
    /// The theme's category range, handed out in domain order to a nominal color.
    #[serde(default)]
    pub(crate) palette: Vec<String>,
    /// Board-wide value→slot bindings; one bound to the color field refuses.
    #[serde(default)]
    pub(crate) category_colors: Vec<crate::common::CategoryColorScale>,
}

#[derive(Deserialize)]
pub(crate) struct ColorGradient {
    /// Pre-computed hex color stops from lightest to darkest.
    pub(crate) resolved_stops: Vec<String>,
    /// Author-supplied domain override; null means use the data range.
    #[serde(default)]
    pub(crate) min: Option<f64>,
    #[serde(default)]
    pub(crate) max: Option<f64>,
    /// Author opt-in to the nice-widened tick ladder (themed true).
    pub(crate) nice: bool,
}

#[derive(Deserialize, Default)]
pub(crate) struct RectMark {
    pub(crate) opacity: Option<f64>,
    pub(crate) stroke: Option<String>,
    pub(crate) stroke_width: Option<f64>,
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

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let r: HeatmapResolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("heatmap payload: {e}"))?;
    r.style.axis_x.refuse_unpainted_affix("heatmap", "axis_x", false)?;
    r.style.axis_x.refuse_x_tick_cadence("heatmap")?;
    r.style.axis_x.refuse_top_x_axis("heatmap")?;
    r.style.axis_y.refuse_unpainted_affix("heatmap", "axis_y", false)?;

    if !r.layers.is_empty() {
        return Err("heatmap: layers not supported".into());
    }
    if r.resolved_channels.get("color").is_some_and(|ch| ch.mode == "conditional") {
        return Err("heatmap: conditional color channel not supported".into());
    }
    if r.style.color_gradient.resolved_stops.len() < 2 {
        return Err("heatmap: color_gradient.resolved_stops must have ≥ 2 stops".into());
    }
    // `_flag_quantitative_color` stamps every resolved color channel, so a
    // series color without the flag is a payload no resolver produced.
    let categorical = match r.resolved_channels.get("color") {
        Some(ch) if ch.mode == "series" => !ch.quantitative_data.ok_or(
            "heatmap: a series color channel carries no quantitative_data flag (resolver required)",
        )?,
        _ => false,
    };
    if categorical && r.multiples.is_some() {
        return Err("heatmap: a categorical color on multiples is not supported".into());
    }
    // A vertical gradient's length follows the plot height and its
    // anchor a side; one above or below the plot is not probed.
    if r.multiples.is_none()
        && r.legend.visible
        && !categorical
        && r.legend.direction == "vertical"
        && matches!(r.legend.position.edge.as_str(), "top" | "bottom")
    {
        return Err(format!(
            "heatmap: a vertical gradient legend on the {} edge is not modeled",
            r.legend.position.edge
        ));
    }
    // Vega-Lite's `defaultGradientLength` keys on the legend orient: a
    // corner orient takes the 100px minimum, not the plot-following length a
    // cardinal strip takes (`horizontal_gradient_length`).
    if r.legend.visible && !categorical && r.legend.position.overlay {
        return Err("heatmap: a floating gradient legend is not modeled (its gradient length follows the orient)".into());
    }
    if r.style.axis_y.position.as_deref() == Some("right") {
        return Err("heatmap: axis_y.position right not supported".into());
    }
    // `axis_y.mirror` is line's own fit-arm feature (line.rs); heatmap has
    // no ghost-axis model — refuse rather than silently ignore an authored
    // mirror and render an unmirrored chart.
    if !matches!(&r.style.axis_y.mirror, None | Some(Value::Null) | Some(Value::Bool(false))) {
        return Err("heatmap: axis_y.mirror not supported".into());
    }
    if r.multiples.is_some() {
        return crate::families::heatmap_facet::render_facet(payload, fonts, &r);
    }

    let w = payload.width;
    let h = payload.height;
    let pad = payload.padding.as_ref();
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    // The chart's OWN effective background — see `HeatmapResolved::background`.
    let bg = r.background.as_str();

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
            &payload.rows,
            field,
            first_appearance(&payload.rows, field),
            r.sort.as_ref(),
            SortAggregate::Min,
            "heatmap",
        )
    };
    let x_domain = dimension_domain(x_field)?;
    let y_domain = dimension_domain(y_field)?;

    let coloring = if categorical {
        let (domain, fills) = color_categories(&r, &payload.rows)?;
        Coloring::Categories { domain, fills }
    } else {
        let (min, max, ticks) = gradient_ladder(&payload.rows, color_field, &r.style.color_gradient)?;
        Coloring::Gradient { min, max, ticks }
    };

    // ── X axis ────────────────────────────────────────────────────────────
    // A date x runs bar's bucketed-calendar ladder instead of the raw ordinal
    // band (`build_cartesian_x_encoding` keeps the band scale and borrows the
    // ladder's labels): it paints from query order, so it needs the band
    // domain to BE that order.
    let ax_x = &r.style.axis_x;
    let x_first_seen = first_appearance(&payload.rows, x_field);
    let x_dates = if x_values_are_temporal_candidates(
        &x_first_seen.iter().map(String::as_str).collect::<Vec<_>>(),
    ) {
        const LEAD: &str = "a single chart with";
        refuse_unmodeled_x_labels(ax_x, LEAD)?;
        let usable_ratio = r
            .style
            .label_usable_ratio
            .ok_or("heatmap: label_usable_ratio absent (theme required for the x axis ladder)")?;
        let axis = HeatmapXAxis::dates(&x_first_seen, ax_x, w, usable_ratio, fonts, LEAD)?;
        if x_domain != x_first_seen {
            return Err(
                "heatmap: a single chart with a date x reordered by an authored sort is not supported"
                    .into(),
            );
        }
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
        None => vega_fit::category_label_angle(
            &ax_x.labels,
            &x_domain,
            w,
            r.style.label_usable_ratio,
            fonts,
            x_label_fs,
        )?,
    });
    let (x_align, x_baseline) = if x_angle == 0.0 {
        ("center", "top")
    } else {
        vega_fit::vl_x_label_anchor(x_angle, true)
    };
    let x_label_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_label_ws = measure_all(fonts, &x_domain, x_label_family, x_label_fs, x_label_limit)?;

    // ── Y category axis ───────────────────────────────────────────────────
    let ax_y = &r.style.axis_y;
    let y_label_family = ax_y.labels.font.family_or("sans-serif");
    let y_label_fs = ax_y.labels.font.size_or(11.0);
    let y_tick_visible = ax_y.ticks.visible.unwrap_or(true);
    let y_tick_len = if y_tick_visible { ax_y.ticks.length.unwrap_or(6.0) } else { 0.0 };
    let y_label_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // Production keeps the nominal band scale and borrows the ladder's labels
    // (`build_cartesian_x_encoding`); it paints from query order.
    let y_first_seen = first_appearance(&payload.rows, y_field);
    let y_dates = y_date_labels(&y_first_seen, ax_y, h, r.style.label_usable_ratio, fonts, y_label_family, y_label_fs)?;
    if y_dates.is_some() && y_domain != y_first_seen {
        return Err("heatmap: a date y reordered by an authored sort is not supported".into());
    }
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
    // An own-side `labels.align` grows the row labels back ACROSS the axis
    // line, which Vega-Lite's own fit estimate does not reserve for. Rather
    // than leave them over the plot, production measures the exact nominal
    // row labels and bakes `labelPadding = max_width + 4`, capped at the
    // label limit (`vl_field_maps.py::measure_axis_to_vl`); Vega then
    // left-anchors every label at −labelPadding. An align it cannot measure
    // from — `center`, or an upper/lower `font.case`, which renders through
    // a `labelExpr` the measurement can't see — drops back to Vega-Lite's
    // away-side default (`_fallback_reversed_label_align`). A date y paints a
    // `labelExpr` too, so it always takes the away-side default.
    let y_case = ax_y.labels.font.case.as_deref().unwrap_or("none");
    let y_own_side = ax_y.labels.align.as_deref() == Some("left")
        && !matches!(y_case, "upper" | "lower")
        && y_dates.is_none();
    let y_label_pad = if y_own_side {
        y_label_max_w.min(y_label_limit) + BREATHING_ROOM_PX
    } else {
        ax_y.labels
            .padding
            .ok_or("heatmap: axis_y.labels.padding absent (theme required)")?
    };
    let (y_ink_align, y_text_anchor) = if y_own_side { ("left", "start") } else { ("right", "end") };
    let y_gutter = y_tick_len + y_label_pad;

    // ── Axis titles ───────────────────────────────────────────────────────
    // A title is drawn unless the style hides it (`axis_to_vl` sets
    // `title: null` only for an explicit `visible: false`); the text is the
    // authored label, else the field's own name.
    let x_title_text = crate::common::axis_title_text(x_field, r.x_label.as_deref(), &r.field_labels);
    let y_title_text = crate::common::axis_title_text(y_field, r.y_label.as_deref(), &r.field_labels);
    refuse_wrapped_axis_title("axis_x", &x_title_text, &ax_x.title, w, fonts)?;
    refuse_wrapped_axis_title("axis_y", &y_title_text, &ax_y.title, h, fonts)?;
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_text);
    let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_text);
    // Where the rotated title starts from: the row labels' outer edge, which
    // `axisLayout` reads off the axis bounds (`s = -bounds.x1`, `vega.js:20389`)
    // before `axisTitleLayout` (`vega.js:20422-20432`) sits the title `padding`
    // past it. Own-side labels start at the gutter and grow toward the plot;
    // away-side ones end there and grow left.
    let y_label_edge = y_label_ws.iter().fold(0.0_f64, |edge, lw| {
        edge.min(vega_fit::text_ink(-y_gutter, 0.0, *lw, y_label_fs, y_ink_align, "middle").x1)
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
            reach.max(ink.y2)
        }),
    };

    // ── Legend ────────────────────────────────────────────────────────────
    let has_legend = r.legend.visible;
    let legend_edge = r.legend.position.edge.as_str();
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
        Coloring::Gradient { ticks, .. } => ticks
            .iter()
            .map(|v| format_legend_value(*v, leg_fmt_spec))
            .collect::<Result<Vec<_>, String>>()?,
        Coloring::Categories { .. } => Vec::new(),
    };
    let leg_title_text = legend_title_text(color_field, &r.legend, &r.field_labels)?;
    // Vega sizes a legend box `ceil(max(title, entries))` — the title
    // measured on the DRAWN name, the entries on the gradient bar plus
    // `labelOffset` plus the widest value label. VEGA measures these itself,
    // so they are SHAPED widths (GPOS kerning applied); the y gutter above
    // stays on the raw advance sum, which is what production's own measurer
    // bakes into `labelPadding` and Vega then honors verbatim. On a
    // comma-formatted ladder the two differ by ~0.56px, a whole pixel of box.
    let leg_max_label_w = leg_labels.iter().try_fold(0.0_f64, |acc, s| {
        fonts.measure_shaped(s, leg_label_family, leg_label_size).map(|v| acc.max(v))
    })?;
    let leg_title_w = if leg_title_text.is_empty() {
        0.0
    } else {
        fonts.measure_shaped(&leg_title_text, leg_title_family, leg_title_size)?
    };
    // A nominal color's symbol legend: a fill-only square per category.
    let symbol_entries: Vec<LegendEntry> = match &coloring {
        Coloring::Categories { domain, fills } => domain
            .iter()
            .zip(fills)
            .map(|(label, fill)| LegendEntry {
                label,
                fill,
                symbol: Some(LegendSymbol::FilledSquare),
                series_key: None,
            })
            .collect(),
        Coloring::Gradient { .. } => Vec::new(),
    };
    let symbol_legend = (has_legend && !symbol_entries.is_empty())
        .then(|| vega_legend(&symbol_entries, &leg_title_text, &r.legend, fonts))
        .transpose()?;
    let slide_blocker = (has_legend && symbol_legend.is_none())
        .then_some("is only modeled on a categorical right legend (a gradient legend's block is not probed)");
    refuse_slide_blocker(&r.legend.position, "heatmap", slide_blocker)?;
    let leg_title_band = if leg_title_text.is_empty() {
        0.0
    } else {
        leg_title_size + VEGA_LEGEND_TITLE_PADDING
    };
    let gradient = has_legend && matches!(coloring, Coloring::Gradient { .. });
    let vertical_gradient = gradient && r.legend.direction == "vertical";
    const TOP_LEAD: &str = "a single chart with";
    let horizontal_gradient = match &coloring {
        Coloring::Gradient { min, max, ticks } if gradient && !vertical_gradient => {
            Some(GradientLegend::resolve(&r, grad_id, (*min, *max, ticks), &leg_labels, TOP_LEAD)?)
        }
        _ => None,
    };
    // The legend box `(w, h)` at plot size `(pw, ph)`: a vertical gradient
    // runs `clamp(ph, 64, 200)` beside its labels, a horizontal one
    // `horizontal_gradient_length` above them; either sits under the title
    // band, and the box is `ceil(max(title, entries))` wide.
    let legend_size = |pw: f64, ph: f64| -> Option<(f64, f64)> {
        if let Some(legend) = &symbol_legend {
            Some((legend.box_w, legend.box_h()))
        } else if let Some(legend) = &horizontal_gradient {
            let length = horizontal_gradient_length(legend_edge, pw);
            Some((leg_title_w.max(length).ceil(), leg_title_band + legend.height()))
        } else if vertical_gradient {
            let entries = VEGA_GRADIENT_THICKNESS + leg_label_offset + leg_max_label_w;
            Some((leg_title_w.max(entries).ceil(), leg_title_band + ph.clamp(VEGA_GRADIENT_LENGTH_MIN, VEGA_GRADIENT_LENGTH_MAX)))
        } else {
            None
        }
    };
    // The legend's origin in plot coordinates, set on every fit pass (the
    // last is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));

    // ── Title ─────────────────────────────────────────────────────────────
    let eff_title_style =
        crate::common::effective_title_style(r.style.title_font.as_ref(), r.title_style.as_ref());
    let title_ref = eff_title_style.as_ref().or(r.title_style.as_ref());
    let title_fs = title_ref.map(|t| t.font.size_or(18.0)).unwrap_or(18.0);
    let cased_title = match r.title.as_deref() {
        None => None,
        Some(t) => {
            let case = title_ref.and_then(|s| s.font.case.as_deref()).unwrap_or("none");
            Some(crate::case::apply_case(t, case)?)
        }
    };
    // Heatmap is a plain unit spec here — a `multiples` chart took
    // `render_facet` above — no concat — so `_render_vl_artifact` runs `apply_title_overflow_to_spec`
    // over it like every other non-hconcat family: the compiled spec carries
    // the pass's own `title.limit` (568 on a 600-wide card with 16px padding).
    // Same law, same helper as pie and both line arms.
    let title_block = crate::common::wrap_title_block(
        cased_title.as_deref(),
        r.subtitle.as_deref(),
        title_ref,
        title_fs,
        crate::common::title_measure_font_size(r.title_style.as_ref()),
        w - pad_l - pad_r,
        fonts,
    )?;

    // ── Plot rect: Vega's autosize-fit negotiation ────────────────────────
    let m = vega_fit::fit_plot(
        w,
        h,
        (pad_l, pad_r, pad_t, pad_b),
        |pw, ph, b| {
            // The y axis's own bounds join `xBounds`, the x axis's `yBounds`
            // (`ViewLayout.js:62-65`); the cells are bare rect marks.
            let mut y_axis = Bounds::new(0.0, 0.0, pw, ph);
            let ys = BandScale::new(y_domain.clone(), 0.0, ph, 0.0, 0.0);
            // The axis group sits a crisp half pixel above its labels' own
            // coordinates in the bounds Vega reads (`axis.js:77`), which only
            // shows where a label overhangs the plot top.
            for (cat, lw) in y_label_bands.iter().zip(&y_label_ws) {
                let Some(by) = ys.position(cat) else { continue };
                let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE;
                y_axis.union(&vega_fit::text_ink(-y_gutter, cy, *lw, y_label_fs, y_ink_align, "middle"));
            }
            for label in y_dates.iter().flatten().filter(|l| !l.row2.is_empty()) {
                let Some(by) = ys.position(&y_domain[label.domain_idx]) else { continue };
                let cy = by + ys.bandwidth() / 2.0 - AXIS_TRANSLATE + vega_fit::label_row_advance(y_label_fs);
                y_axis.union(&vega_fit::text_ink(-y_gutter, cy, label.w2.min(y_label_limit), y_label_fs, y_ink_align, "middle"));
            }
            if y_title_extra > 0.0 {
                y_axis.union(&Bounds::new(y_label_edge - y_title_extra, 0.0, 0.0, ph));
            }
            b.union(&y_axis);
            let mut x_axis = Bounds::new(0.0, 0.0, pw, ph);
            let xs = BandScale::new(x_domain.clone(), 0.0, pw, 0.0, 0.0);
            let row_y = ph + x_axis_offset + x_tick_len + x_label_pad;
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
                let label_reach = (b.y2 - ph).max(0.0);
                x_axis.union(&Bounds::new(0.0, 0.0, 0.0, ph + label_reach + x_title_extra));
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
        },
        title_block.ink.as_ref(),
    );
    let (plot_left, plot_top) = (pad_l + m.left, pad_t + m.top);
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    if plot_w <= 0.0 || plot_h <= 0.0 {
        return Err(format!("heatmap: chart box {w}×{h} too small for content"));
    }
    if let Some(legend) = &horizontal_gradient {
        legend.check_labels_clear(fonts, horizontal_gradient_length(legend_edge, plot_w), TOP_LEAD)?;
    }
    let x_scale = BandScale::new(x_domain.clone(), 0.0, plot_w, 0.0, 0.0);
    let y_scale = BandScale::new(y_domain.clone(), 0.0, plot_h, 0.0, 0.0);
    let cell_w = x_scale.bandwidth();
    let cell_h = y_scale.bandwidth();

    // ── SVG emission ──────────────────────────────────────────────────────
    let mut svg = String::with_capacity(32768);
    // Namespaced so two heatmaps on one board never collide
    // (`converters/chart.py::_namespace_svg_ids`'s law, ported at
    // `contract::safe_svg_id`).
    let _ = write!(
        svg,
        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" \
         viewBox=\"0 0 {w} {h}\">"
    );
    let _ = write!(svg, "<rect width=\"{w}\" height=\"{h}\" fill=\"{bg}\"/>", bg = xml_escape_attr(bg));

    if let Some((tx, ty)) = m.title_pos {
        crate::common::emit_title_block_vega(
            &mut svg,
            &title_block,
            title_ref,
            plot_left + tx,
            plot_top + ty,
            title_fs,
        );
    }

    if vertical_gradient {
        emit_gradient_def(&mut svg, grad_id, &r.style.color_gradient.resolved_stops, true);
    }

    // ── Cells ─────────────────────────────────────────────────────────────
    let _ = write!(svg, "<g transform=\"translate({plot_left:.2},{plot_top:.2})\">");
    let stroke = r.style.rect_mark.stroke.as_deref().unwrap_or("none");
    let stroke_w = r.style.rect_mark.stroke_width.unwrap_or(0.0);
    let cells = CellPaint {
        x_field,
        y_field,
        color_field,
        fill: match &coloring {
            Coloring::Gradient { min, max, .. } => CellFill::Gradient {
                stops: &r.style.color_gradient.resolved_stops,
                min: *min,
                max: *max,
            },
            Coloring::Categories { domain, fills } => CellFill::Categories { domain, fills },
        },
        opacity: r.style.rect_mark.opacity.unwrap_or(1.0),
        stroke_attr: if stroke_w > 0.0 && stroke != "none" {
            format!(" stroke=\"{stroke}\" stroke-width=\"{stroke_w}\"", stroke = xml_escape_attr(stroke))
        } else {
            String::new()
        },
        tooltip: tooltip_value_title(&r, &payload.rows)?.zip(r.style.tooltip_format.as_ref()),
    };
    paint_cells(&mut svg, &payload.rows, &x_scale, &y_scale, &cells)?;
    let _ = write!(svg, "</g>");

    // ── X axis ────────────────────────────────────────────────────────────
    // The axis group sits at (0.5, plot_h + offset + 0.5); a label keeps its
    // exact band position inside it, a tick snaps to a whole pixel first
    // (`floor(center) + 0.5` — probed over 64 band centers, widths 600–615).
    let x_axis_top = plot_top + plot_h + x_axis_offset + AXIS_TRANSLATE;
    let x_label_row = x_axis_top + x_tick_len + x_label_pad;
    let x_label_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let x_label_weight = ax_x.labels.font.weight_str();
    let x_tick_color = ax_x.ticks.color.as_deref().unwrap_or("#D0D3D9");
    let x_tick_w_px = ax_x.ticks.width.unwrap_or(1.0);
    if let Some(axis) = &x_dates {
        axis.paint(&mut svg, ax_x, plot_left, plot_w, plot_top + plot_h);
    } else {
        crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::X, (plot_left + AXIS_TRANSLATE, x_axis_top));
    }
    for band_val in x_domain.iter().filter(|_| x_dates.is_none()) {
        let Some(band_x) = x_scale.position(band_val) else { continue };
        let center = band_x + cell_w / 2.0;
        if x_tick_visible {
            let tick_x = plot_left + center.floor() + AXIS_TRANSLATE;
            let tick_y2 = x_axis_top + x_tick_len;
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
            let _ = write!(
                svg,
                "<text text-anchor=\"{anchor}\" transform=\"translate({label_x:.2},\
                 {x_label_row:.2}) rotate({x_angle}) translate(0,{dy})\" \
                 font-family=\"{}\" font-size=\"{x_label_fs}\" \
                 font-weight=\"{x_label_weight}\" fill=\"{x_label_color}\">{}</text>",
                vega_escape_attr(x_label_family),
                xml_escape(band_val), anchor = xml_escape_attr(anchor), x_label_weight = xml_escape_attr(&x_label_weight), x_label_color = xml_escape_attr(x_label_color)
            );
        }
    }

    let x_title_y = x_label_row
        + x_label_reach
        + ax_x.title.padding.unwrap_or(10.0)
        + vega_fit::baseline_shift("top", ax_x.title.font.size_or(11.0));
    crate::common::emit_axis_title_at(&mut svg, &x_title_text, &ax_x.title, plot_left + plot_w / 2.0, x_title_y, None);
    if x_dates.is_none() {
        crate::contract::axis_group_close(&mut svg);
    }

    // ── Y axis ────────────────────────────────────────────────────────────
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let y_tick_color = ax_y.ticks.color.as_deref().unwrap_or("#D0D3D9");
    let y_tick_w_px = ax_y.ticks.width.unwrap_or(1.0);
    let y_axis_x = plot_left + AXIS_TRANSLATE;
    let y_label_x = y_axis_x - y_gutter;
    // One row per drawn label: every band for a category y, only the ladder's
    // openers (and a second context row under them) for a date y.
    crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (y_axis_x, plot_top));
    let y_rows: Vec<(&str, &str)> = match &y_dates {
        Some(labels) => labels.iter().map(|l| (y_domain[l.domain_idx].as_str(), l.row1.as_str())).collect(),
        None => y_domain.iter().map(|d| (d.as_str(), d.as_str())).collect(),
    };
    for (i, &(band_val, row1)) in y_rows.iter().enumerate() {
        let Some(band_y) = y_scale.position(band_val) else { continue };
        let center = band_y + cell_h / 2.0;
        if y_tick_visible {
            let tick_y = plot_top + center.floor() + AXIS_TRANSLATE;
            let _ = write!(
                svg,
                "<line x1=\"{:.2}\" y1=\"{tick_y:.2}\" x2=\"{y_axis_x:.2}\" y2=\"{tick_y:.2}\" \
                 stroke=\"{y_tick_color}\" stroke-width=\"{y_tick_w_px}\"/>",
                y_axis_x - y_tick_len, y_tick_color = xml_escape_attr(y_tick_color)
            );
        }
        let row2 = y_dates.as_ref().map(|labels| labels[i].row2.as_str()).unwrap_or("");
        let row1_y = plot_top + center + vega_fit::baseline_shift("middle", y_label_fs);
        for (text, y) in [(row1, row1_y), (row2, row1_y + vega_fit::label_row_advance(y_label_fs))] {
            if text.is_empty() {
                continue;
            }
            let _ = write!(
                svg,
                "<text x=\"{y_label_x:.2}\" y=\"{y:.2}\" text-anchor=\"{y_text_anchor}\" \
                 font-family=\"{}\" font-size=\"{y_label_fs}\" \
                 font-weight=\"{y_label_weight}\" fill=\"{y_label_color}\">{}</text>",
                vega_escape_attr(y_label_family),
                xml_escape(text), y_text_anchor = xml_escape_attr(y_text_anchor), y_label_weight = xml_escape_attr(&y_label_weight), y_label_color = xml_escape_attr(y_label_color)
            );
        }
    }

    crate::common::emit_axis_title_rotated(
        &mut svg,
        &y_title_text,
        &ax_y.title,
        y_axis_x + y_label_edge,
        plot_top + plot_h / 2.0,
        -1.0,
        None,
    );
    crate::contract::axis_group_close(&mut svg);

    // ── Legend ────────────────────────────────────────────────────────────
    let (leg_x, leg_y) = (plot_left + legend_xy.get().0, plot_top + legend_xy.get().1);
    let paint_title = |svg: &mut String| {
        if leg_title_text.is_empty() {
            return;
        }
        let leg_title_color = leg_title_font.color.as_deref().unwrap_or("#313233");
        let leg_title_weight = leg_title_font.weight_str();
        let _ = write!(
            svg,
            "<text x=\"{leg_x:.2}\" y=\"{:.2}\" text-anchor=\"start\" \
             font-family=\"{}\" font-size=\"{leg_title_size}\" \
             font-weight=\"{leg_title_weight}\" fill=\"{leg_title_color}\">{}</text>",
            leg_y + vega_fit::baseline_shift("top", leg_title_size),
            vega_escape_attr(leg_title_family),
            xml_escape(&leg_title_text), leg_title_weight = xml_escape_attr(&leg_title_weight), leg_title_color = xml_escape_attr(leg_title_color)
        );
    };
    if let Some(legend) = &horizontal_gradient {
        paint_title(&mut svg);
        legend.paint(&mut svg, leg_x, leg_y + leg_title_band, horizontal_gradient_length(legend_edge, plot_w));
    }
    match (&symbol_legend, &coloring) {
        (Some(legend), _) => {
            let (dx, dy) = legend_slide(&r.legend.position, legend, (plot_w, plot_h))?;
            emit_vega_legend(&mut svg, &symbol_entries, legend, &r.legend, LegendSymbol::FilledSquare, leg_x + dx, leg_y + dy, false);
        }
        (None, Coloring::Gradient { min: color_min, max: color_max, ticks: leg_values }) if vertical_gradient => {
            paint_title(&mut svg);

            let bar_y = leg_y + leg_title_band;
            let grad_len = plot_h.clamp(VEGA_GRADIENT_LENGTH_MIN, VEGA_GRADIENT_LENGTH_MAX);
            let _ = write!(
                svg,
                "<rect x=\"{leg_x:.2}\" y=\"{bar_y:.2}\" width=\"{VEGA_GRADIENT_THICKNESS}\" \
                 height=\"{grad_len}\" fill=\"url(#{grad_id})\"/>", grad_id = xml_escape_attr(grad_id)
            );

            let leg_label_color = leg_label_font.color.as_deref().unwrap_or("#626366");
            let leg_label_weight = leg_label_font.weight_str();
            let label_x = leg_x + VEGA_GRADIENT_THICKNESS + leg_label_offset;
            let span = color_max - color_min;
            let last = leg_labels.len() - 1;
            crate::contract::legend_label_open(&mut svg, "");
            for (i, (v, text)) in leg_values.iter().zip(&leg_labels).enumerate() {
                let t = if span == 0.0 { 0.0 } else { (v - color_min) / span };
                // The bar runs min at the bottom to max at the top, and the two
                // end labels tuck inside it: bottom-most `bottom`, top-most
                // `top`, everything between `middle`.
                let baseline = match i {
                    0 => "bottom",
                    _ if i == last => "top",
                    _ => "middle",
                };
                let label_y = bar_y + grad_len * (1.0 - t)
                    + vega_fit::baseline_shift(baseline, leg_label_size);
                let _ = write!(
                    svg,
                    "<text x=\"{label_x:.2}\" y=\"{label_y:.2}\" text-anchor=\"start\" \
                     font-family=\"{}\" font-size=\"{leg_label_size}\" \
                     font-weight=\"{leg_label_weight}\" fill=\"{leg_label_color}\">{}</text>",
                    vega_escape_attr(leg_label_family),
                    xml_escape(text), leg_label_weight = xml_escape_attr(&leg_label_weight), leg_label_color = xml_escape_attr(leg_label_color)
                );
            }
            crate::contract::mark_group_close(&mut svg);
        }
        _ => {}
    }

    svg.push_str("</svg>");
    Ok(svg)
}

/// What the color channel paints, owned: a gradient over a measure, or one
/// palette slot per category.
enum Coloring {
    Gradient { min: f64, max: f64, ticks: Vec<f64> },
    Categories { domain: Vec<String>, fills: Vec<String> },
}

/// How a cell takes its fill from its color value.
#[derive(Clone, Copy)]
pub(crate) enum CellFill<'a> {
    Gradient { stops: &'a [String], min: f64, max: f64 },
    Categories { domain: &'a [String], fills: &'a [String] },
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
    /// The tooltip's value title and format, or `None` when the chart paints
    /// no tooltip.
    pub(crate) tooltip: Option<(String, &'a FormatSlot)>,
}

/// The calendar labels a date-valued y band axis paints, or `None` for a
/// category y (also a date column with no detectable calendar grain, which
/// production leaves as raw band values). Refuses by name every date-y
/// setting the ladder does not model.
fn y_date_labels(
    domain: &[String],
    ax_y: &AxisStyle,
    chart_h: f64,
    usable_ratio: Option<f64>,
    fonts: &FontBook,
    family: &str,
    font_size: f64,
) -> Result<Option<Vec<AxisLabel>>, String> {
    let refs: Vec<&str> = domain.iter().map(String::as_str).collect();
    if !x_values_are_temporal_candidates(&refs) {
        return Ok(None);
    }
    if refs.iter().any(|d| parse_iso_date(d).is_none()) {
        return Err("heatmap: a date-like y that is not ISO dates is not supported".into());
    }
    if refs.iter().any(|d| d.len() > 10) {
        return Err("heatmap: a date y with a time of day is not supported".into());
    }
    let Some(data_tu) = detect_time_unit(&refs) else {
        return Ok(None);
    };
    crate::families::temporal::refuse_grain_format("heatmap", &ax_y.grain_formats, data_tu)?;
    let labels_cfg = &ax_y.labels;
    if labels_cfg.expr.is_some() {
        return Err("heatmap: a date y with an authored axis_y.labels.expr is not supported".into());
    }
    if labels_cfg.visible == Some(false) {
        return Err("heatmap: a date y with hidden axis_y labels is not supported".into());
    }
    if ax_y.time_unit.as_ref().is_some_and(|v| !v.is_null()) {
        return Err("heatmap: a date y with an authored axis_y.time_unit is not supported".into());
    }
    if labels_cfg.time_unit.as_deref().is_some_and(|u| u != "auto") {
        return Err("heatmap: a date y with an authored axis_y.labels.time_unit is not supported".into());
    }
    if labels_cfg.angle.is_some() {
        return Err("heatmap: a date y with an authored axis_y.labels.angle is not supported".into());
    }
    if labels_cfg.spec().is_some_and(is_time_format) {
        return Err("heatmap: a date y with an authored time axis_y.labels.format is not supported".into());
    }
    if !refs.windows(2).all(|w| w[0] < w[1]) {
        return Err("heatmap: a date y not in ascending query order is not supported".into());
    }
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
    )?;
    if !ladder.fits && overlap.skip {
        return Err("heatmap: a date y whose coarsest cadence still collides (labelOverlap: greedy) is not supported".into());
    }
    ladder
        .labels
        .into_iter()
        .map(|l| AxisLabel::measured(l.domain_idx, l.row1, l.row2, &|t| fonts.measure(t, family, font_size)))
        .collect::<Result<Vec<_>, _>>()
        .map(Some)
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
    for row in rows {
        let xv = row_str(row, c.x_field);
        let yv = row_str(row, c.y_field);
        let Some(cx) = x_scale.position(&xv) else { continue };
        let Some(cy) = y_scale.position(&yv) else { continue };
        // A category's tooltip still formats its value as a number, which a
        // name is not: Vega prints `NaN`.
        let (fill, cv) = match c.fill {
            CellFill::Gradient { stops, min, max } => {
                let Some(cv) = row_num(row, c.color_field) else { continue };
                (interp_color(stops, min, max, cv), cv)
            }
            CellFill::Categories { domain, fills } => {
                let name = row_str(row, c.color_field);
                let slot = domain
                    .iter()
                    .position(|d| *d == name)
                    .ok_or_else(|| format!("heatmap: color {name:?} is not in its own category domain"))?;
                (fills[slot].clone(), f64::NAN)
            }
        };
        let aria = match &c.tooltip {
            Some((value_title, value_format)) => {
                let tt = crate::contract::HeatmapTooltip {
                    x: &xv,
                    y: &yv,
                    value_title,
                    value_format,
                    value: cv,
                };
                Some(crate::contract::heatmap_aria_label(&tt)?)
            }
            None => None,
        };
        let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::RectMark);
        if aria.is_some() {
            crate::contract::mark_group_open(svg, "rect", "marks");
        }
        let _ = write!(
            svg,
            "<rect x=\"{cx:.2}\" y=\"{cy:.2}\" width=\"{cell_w:.2}\" height=\"{cell_h:.2}\" \
             fill=\"{fill}\"{stroke_attr} opacity=\"{opacity}\"{label_attr}/>",
            fill = xml_escape_attr(&fill),
            stroke_attr = c.stroke_attr,
            opacity = c.opacity,
        );
        if aria.is_some() {
            crate::contract::mark_group_close(svg);
        }
    }
    Ok(())
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// A non-numeric color field is a nominal scale. Vega-Lite leaves its domain
/// unpinned, so Vega sorts it ascending (`sort: ascending`, vega-util
/// `ascending`: UTF-16 code-unit order) and the theme's category range hands
/// the palette out in that order. Everything production would bind
/// differently refuses: a board category-color scale, authored
/// `legend.values`, a null category, a date-like field (which types
/// temporal or ordinal), and a domain the palette cannot cover (an ordinal
/// range would wrap).
fn color_categories(
    r: &HeatmapResolved,
    rows: &[serde_json::Map<String, Value>],
) -> Result<(Vec<String>, Vec<String>), String> {
    if crate::common::category_scale_for(&r.category_colors, &r.color).is_some() {
        return Err("heatmap: a categorical color with a board category-color scale is not supported".into());
    }
    if r.legend.values.is_some() {
        return Err("heatmap: a categorical color with authored legend.values is not supported".into());
    }
    if rows.iter().any(|row| row.get(&r.color).is_none_or(Value::is_null)) {
        return Err("heatmap: a categorical color with a null value is not supported".into());
    }
    let mut domain = first_appearance(rows, &r.color);
    if domain.is_empty() {
        return Err("heatmap: a categorical color with no values is not supported".into());
    }
    let as_strs: Vec<&str> = domain.iter().map(String::as_str).collect();
    if x_values_are_temporal_candidates(&as_strs) {
        return Err("heatmap: a date-like categorical color is not supported".into());
    }
    domain.sort_by(|a, b| a.encode_utf16().cmp(b.encode_utf16()));
    if domain.len() > r.palette.len() {
        return Err(format!(
            "heatmap: {} color categories past the {}-swatch palette is not supported",
            domain.len(),
            r.palette.len()
        ));
    }
    let fills = r.palette[..domain.len()].to_vec();
    Ok((domain, fills))
}

/// Production wraps an axis title that outgrows `axis_title_budget`
/// (`extent - 72`, `emitters/_cartesian.py:568-580`) onto a second line and
/// ellipsizes past that (`wrap_axis_title`, `_cartesian.py:583`); this arm draws
/// one line, so a title past the budget refuses instead of drawing the wrong
/// text. `extent` is the box size along the axis the title runs. The title is
/// measured at its painted weight (`wrap_axis_title`, `_cartesian.py:610`),
/// which is 600 at the theme default, so a title that fits at 400 can still wrap.
fn refuse_wrapped_axis_title(
    name: &str,
    text: &str,
    title: &crate::common::AxisTitle,
    extent: f64,
    fonts: &FontBook,
) -> Result<(), String> {
    if crate::common::axis_title_band(title, text) == 0.0 {
        return Ok(());
    }
    let budget = (extent - crate::families::bar::CHROME_PX).floor().max(1.0);
    let width = title.font.measure_weighted(fonts, text, title.font.size_or(11.0))?;
    if width > budget {
        return Err(format!(
            "heatmap: {name} title {text:?} is {width:.0}px wide, past its {budget}px wrap budget \
             (wrapped axis titles are not supported)"
        ));
    }
    Ok(())
}

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
fn svg_text_anchor(align: &str) -> &'static str {
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
    if let (Some(lo), Some(hi)) = (gradient.min, gradient.max) {
        return Ok((lo, hi, vec![lo, hi]));
    }
    let (data_lo, data_hi) = rows
        .iter()
        .filter_map(|row| row_num(row, field))
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
    if !data_lo.is_finite() {
        return Err(format!("heatmap: color field {field:?} has no numeric values"));
    }
    if gradient.nice && gradient.min.is_none() && gradient.max.is_none() {
        let ticks = d3rs::ticks::nice_tick_values(data_lo, data_hi, GRADIENT_NICE_TICK_COUNT, None);
        return Ok((ticks[0], ticks[ticks.len() - 1], ticks));
    }
    let lo = gradient.min.unwrap_or(data_lo);
    let hi = gradient.max.unwrap_or(data_hi);
    Ok((lo, hi, vec![lo, hi]))
}

/// Parse a hex color "#rrggbb" → (r, g, b) floats.
fn parse_hex(hex: &str) -> (f64, f64, f64) {
    let hex = hex.trim_start_matches('#');
    let parse = |s: &str| u8::from_str_radix(s, 16).unwrap_or(0) as f64;
    if hex.len() == 6 {
        (parse(&hex[0..2]), parse(&hex[2..4]), parse(&hex[4..6]))
    } else {
        (0.0, 0.0, 0.0)
    }
}

/// Linearly interpolate through the resolved stops and return an `rgb(r,g,b)` string.
fn interp_color(stops: &[String], domain_min: f64, domain_max: f64, value: f64) -> String {
    let n = stops.len();
    let t = if domain_max == domain_min {
        0.0
    } else {
        ((value - domain_min) / (domain_max - domain_min)).clamp(0.0, 1.0)
    };
    let float_i = t * (n - 1) as f64;
    let i = (float_i.floor() as usize).min(n - 2);
    let frac = float_i - i as f64;
    let (r0, g0, b0) = parse_hex(&stops[i]);
    let (r1, g1, b1) = parse_hex(&stops[i + 1]);
    let r = (r0 + frac * (r1 - r0)).round() as u8;
    let g = (g0 + frac * (g1 - g0)).round() as u8;
    let b = (b0 + frac * (b1 - b0)).round() as u8;
    format!("rgb({r},{g},{b})")
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
    use super::{refuse_wrapped_axis_title, render};
    use crate::text::FontBook;
    use crate::Payload;
    use serde_json::{json, Value};

    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    /// `wrap_axis_title` measures the title at its painted weight
    /// (`_cartesian.py:610`; 600 at the theme default), so a title that fits its
    /// budget at 400 can still wrap, and the arm that cannot wrap refuses it.
    #[test]
    fn a_title_that_fits_regular_but_wraps_at_its_painted_weight_is_refused() {
        let fonts = test_fonts();
        let text = "Net revenue retention by customer segment and sales region";
        let width_at = |weight: f32| fonts.measure_at_weight(text, "Inter", 11.0, Some(weight)).unwrap();
        let (regular, painted) = (width_at(400.0), width_at(600.0));
        assert!(painted - regular > 2.0, "{regular} vs {painted}");
        let extent = crate::families::bar::CHROME_PX + ((regular + painted) / 2.0).floor();
        let title = |weight: Value| -> crate::common::AxisTitle {
            serde_json::from_value(json!({
                "font": {"family": TEST_FONT, "size": 11.0, "weight": weight},
                "padding": 10.0,
                "visible": true,
            }))
            .expect("axis title parses")
        };
        refuse_wrapped_axis_title("axis_x", text, &title(json!("400")), extent, &fonts)
            .expect("fits at 400");
        let err = refuse_wrapped_axis_title("axis_x", text, &title(json!(600.0)), extent, &fonts)
            .expect_err("wraps at 600");
        assert!(err.contains("wrap budget"), "{err}");
        refuse_wrapped_axis_title("axis_x", text, &title(json!("semibold")), extent, &fonts)
            .expect("an unparseable weight paints at the default");
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

    /// A sort field present but non-numeric on some row refuses by name —
    /// see `common::dimension_sort_domain`'s doc comment.
    #[test]
    fn heatmap_authored_sort_refuses_a_non_numeric_sort_value() {
        let mut json = sort_heatmap_payload(json!({"by": "seq", "order": "asc"}));
        json["rows"] = json!([
            {"month": "Feb", "seq": "second", "series": "alpha", "revenue": 200.0},
            {"month": "Jan", "seq": 1, "series": "alpha", "revenue": 100.0}
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("must refuse");
        assert!(err.contains("seq"), "{err}");
    }

    /// A NUMERIC-LOOKING string ("9", "2", "10") still refuses under
    /// MIN — production's real `min` is d3's own comparator over the raw
    /// JSON value, which orders a JSON string LEXICOGRAPHICALLY, never by
    /// parsing it as a number first.
    #[test]
    fn heatmap_authored_sort_refuses_a_numeric_looking_string_sort_value() {
        let mut json = sort_heatmap_payload(json!({"by": "seq", "order": "asc"}));
        json["rows"] = json!([
            {"month": "Feb", "seq": "9", "series": "alpha", "revenue": 200.0},
            {"month": "Mar", "seq": "2", "series": "alpha", "revenue": 50.0},
            {"month": "Jan", "seq": "10", "series": "alpha", "revenue": 100.0}
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts())
            .expect_err("must refuse — MIN never parses a JSON string as a number");
        assert!(err.contains("seq"), "{err}");
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

    /// The guard is keyed on `resolved_channels.color.mode`, not the
    /// chart-level `conditional_formatting` field this file used to (wrongly)
    /// check: that field is always null for heatmap post-removal, so a
    /// guard still reading it would be permanently inert against exactly
    /// the hand-written/stale resolved-JSON fixture it exists to catch.
    #[test]
    fn conditional_color_channel_refuses() {
        let mut json = heatmap_payload(|_| {});
        json["resolved"]["resolved_channels"] = serde_json::json!({
            "color": {"mode": "conditional", "data_field": "revenue", "rules": []}
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("refuses");
        assert!(err.contains("conditional color channel not supported"), "{err}");
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
    /// one `nice_tick_values(min, max, 6)` list (`default_config.yml`'s
    /// `gradient.nice_tick_count`). At five rungs the same extent gives step
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
        assert!(svg.contains("fill=\"rgb(191,191,191)\""), "wrong domain: {svg}");
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

    /// `axis_y.mirror` is line's own fit-arm feature (line.rs); heatmap has
    /// no ghost-axis model — refuse rather than silently ignore an
    /// authored mirror and render an unmirrored chart.
    #[test]
    fn mirror_refuses() {
        let j = heatmap_payload(|j| j["resolved"]["style"]["axis_y"]["mirror"] = json!(true));
        let p: Payload = serde_json::from_value(j).unwrap();
        let err = render(&p, &test_fonts()).expect_err("heatmap does not model mirror");
        assert!(err.contains("mirror"), "error names the unmodeled field: {err}");
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

    /// `RECEDE_ROLES` matches `rect mark` literally: a heatmap cell recedes
    /// on hover only when stamped so.
    #[test]
    fn every_cell_carries_the_role_the_runtime_recedes() {
        let svg = render_json(heatmap_payload(|_| {}));
        let cells = svg.matches("<g class=\"mark-rect role-mark marks\"").count();
        assert!(cells > 1, "{svg}");
        assert_eq!(svg.matches("aria-roledescription=\"rect mark\"").count(), cells, "{svg}");
    }

    /// Shapes with no probed geometry refuse rather than inventing one.
    #[test]
    fn unmodeled_chrome_placements_refuse() {
        let err = |j: Value| {
            let p: Payload = serde_json::from_value(j).unwrap();
            render(&p, &test_fonts()).expect_err("refuses")
        };
        let vertical_bottom = err(heatmap_payload(|j| {
            j["resolved"]["legend"]["position"] = json!({"edge": "bottom", "align": "start", "overlay": false})
        }));
        assert!(vertical_bottom.contains("vertical gradient legend") && vertical_bottom.contains("bottom"), "{vertical_bottom}");
        assert!(err(heatmap_payload(|j| j["resolved"]["style"]["axis_y"]["position"] = json!("right")))
            .contains("axis_y"));
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

    /// The heatmap paints no affix on either axis: it refuses by name.
    #[test]
    fn an_axis_affix_refuses_by_name() {
        for axis in ["axis_x", "axis_y"] {
            let p: Payload = serde_json::from_value(heatmap_payload(|j| {
                j["resolved"]["style"][axis]["labels"]["format"] = json!({"spec": ",.0f", "prefix": "US$"});
            }))
            .unwrap();
            let err = render(&p, &test_fonts()).expect_err("refuses");
            assert!(err.contains(&format!("heatmap: {axis}.labels.format")) && err.contains("\"prefix\""), "{err}");
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
}
