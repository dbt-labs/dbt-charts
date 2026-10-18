//! Pie/donut family: ResolvedPieChart payload → SVG.
//! Evaluates the same math the pie emitter writes as Vega-Lite expressions —
//! mid-angle labels, dark-companion inks, radius = min(w,h)/2·outer_fraction.
//! The standalone wheel lays out through `vega_fit` (label ink pushes the
//! plot margins, as `autosize: fit` does); the attached-table composition
//! keeps its own direct layout, mirroring production's Python SVG composer.
//!
//! `link` (chart-root `ResolvedChart.link`) paints one `<a href>` around each
//! wedge, slice label and center-total text inside its `.role-mark` group —
//! every layer inheriting the shared `href` encoding — with
//! `{{ color }}`/`{{ theta }}` substituted per row (`crate::href`); the total
//! texts take row 0's. `{{ x }}`/`{{ y }}` refuse with production's own error.
//!
//! Unsupported → clear `Err`:
//! - resolved_channels.color.mode == "conditional" present (pie's authored
//!   surface can no longer produce this from a real board; guards a
//!   hand-written or stale resolved-JSON fixture instead — covers both
//!   paint sites the deleted implementation had, slice fill and label ink)

use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use d3rs::arc::Arc;
use crate::number_locale::d3_format;
use d3rs::pie::pie;

use std::collections::HashMap;

use crate::label_template::{Scope, Template};
use crate::common::{
    category_color, category_ink, category_scale_for, row_str, FormatSlot, Font, Stroke, StyleChannel, TitleStyle,
};
use crate::families::legend::{
    emit_vega_legend, legend_from_widths, legend_slide, legend_title_text, resolve_legend_values,
    legend_fit_bounds, legend_origin, limit_legend_text, LegendConfig, LegendEntry, LegendSymbol, VegaLegend,
};
use crate::families::table::is_date_like_cell;
use crate::frame::{ChartFrame, FrameTitle};
use crate::svg::{num_full, py_float_str, vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::Payload;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    theta: String,
    field_labels: crate::common::FieldLabels,
    identity_field: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    title_style: Option<TitleStyle>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    background: String,
    palette: Vec<String>,
    dark_companion_stops: Vec<String>,
    /// `(pinned color, ink)` pairs for the `category_colors` pins outside the
    /// palette (`common::category_ink`).
    pin_companions: Vec<(String, String)>,
    /// (#7167) the board's plan for a `series`-mode field when one binds
    /// it; `literal` stays on the palette-by-index status quo (no data
    /// field to bind). `mode == "conditional"` is checked once, at
    /// `render`'s own entry gate, and refused — pie's authored surface can
    /// no longer produce it from a real board.
    #[serde(default)]
    resolved_channels: HashMap<String, StyleChannel>,
    /// Board-wide value→slot bindings for this chart's categorical fields
    /// (#7167) — see `common::category_scale_for`/`category_color`.
    #[serde(default)]
    category_colors: Vec<crate::common::CategoryColorScale>,
    outer_fraction: f64,
    slice_label_indices: Vec<usize>,
    style: PieStyle,
    #[serde(default)]
    total: Option<TotalConfig>,
    /// The resolved legend (`ResolvedLegendStyle`), always carried. Production
    /// hides it outside "direct" label mode (`suppress_legend`), so a visible
    /// one is drawn by `draw_pie_fit`'s legend arm.
    legend: LegendConfig,
    #[serde(default)]
    attached_table: Option<Value>,
    #[serde(default)]
    attached_table_placement: Option<String>,
    #[serde(default)]
    attached_table_align: Option<String>,
    #[serde(default)]
    attached_table_width: Option<f64>,
    #[serde(default)]
    attached_table_gap: Option<f64>,
    #[serde(default)]
    hybrid_heading_gap: Option<f64>,
    #[serde(default)]
    wheel_width: Option<f64>,
    #[serde(default)]
    attached_heading: Option<String>,
    #[serde(default)]
    attached_heading_font: Option<Font>,
    #[serde(default)]
    attached_row_indices: Vec<usize>,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// once per wedge (`click_interactivity.py`); an empty string is no link,
    /// production's `if not chart.link`.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    /// How `link` opens (`ResolvedChart.link_open`).
    link_open: crate::href::LinkOpen,
}

// ── Attached table ────────────────────────────────────────────────────────────
// The legend-table beside/below the pie is an ordinary table over the rows
// production's `project_pie_table_rows` synthesizes (swatch, share, name,
// value); `table::render` paints it.

/// The attached table's rows: swatch, share, name, value per frozen row index —
/// production's `project_pie_table_rows`. The share is the slice label's own
/// `.0%` spec.
#[allow(clippy::too_many_arguments)]
fn project_pie_table_rows(
    payload: &Payload,
    row_indices: &[usize],
    palette: &[String],
    identity_field: Option<&String>,
    theta_field: &str,
    thetas: &[f64],
    total: f64,
    category_scale: Option<&crate::common::CategoryColorScale>,
) -> Result<Vec<serde_json::Map<String, Value>>, String> {
    let rows = &payload.rows;
    let scalar = |row: &serde_json::Map<String, Value>, field: &str| match row.get(field) {
        None | Some(Value::Null) => Value::String(String::new()),
        Some(v) => v.clone(),
    };
    let mut table_rows = Vec::with_capacity(row_indices.len());
    for &i in row_indices {
        let swatch = match (category_scale, identity_field) {
            (Some(scale), Some(field)) if !matches!(rows[i].get(field.as_str()), None | Some(Value::Null)) => {
                category_color(scale, &row_str(&rows[i], field), palette)?
            }
            _ => palette[i % palette.len()].clone(),
        };
        let mut row = serde_json::Map::new();
        row.insert("swatch".into(), Value::String(swatch));
        let share = d3_format(".0%", thetas[i] / total)
            .map_err(|e| format!("pie: attached_table share format: {e}"))?;
        row.insert("share".into(), Value::String(share));
        row.insert(
            "name".into(),
            identity_field.map_or(Value::String(String::new()), |f| scalar(&rows[i], f)),
        );
        row.insert("value".into(), scalar(&rows[i], theta_field));
        table_rows.push(row);
    }
    Ok(table_rows)
}

/// Render the pie's attached table at `table_width`, unbounded in height.
/// Returns the SVG and its height.
#[allow(clippy::too_many_arguments)]
fn render_attached_table_svg(
    payload: &Payload,
    attached: &Value,
    row_indices: &[usize],
    palette: &[String],
    identity_field: Option<&String>,
    theta_field: &str,
    thetas: &[f64],
    total: f64,
    table_width: f64,
    fonts: &FontBook,
    category_scale: Option<&crate::common::CategoryColorScale>,
) -> Result<(String, f64), String> {
    let rows = &payload.rows;
    if row_indices.is_empty() {
        return Err("pie: attached_table requires at least one row in attached_row_indices".into());
    }
    if let Some(&bad) = row_indices.iter().find(|&&i| i >= rows.len()) {
        return Err(format!(
            "pie: attached_table row index {bad} out of range — {} data rows",
            rows.len()
        ));
    }
    let table_rows = project_pie_table_rows(
        payload,
        row_indices,
        palette,
        identity_field,
        theta_field,
        thetas,
        total,
        category_scale,
    )?;
    let table_payload = Payload {
        cell_text: Vec::new(),
        max_template_output_bytes: None,
        resolved: attached.clone(),
        rows: table_rows,
        width: table_width,
        height: 0.0,
        background: payload.background.clone(),
        fonts_dir: payload.fonts_dir.clone(),
        padding: None,
        board_style: payload.board_style.clone(),
        language: None,
        variables: payload.variables.clone(),
        filter_variables: payload.filter_variables.clone(),
        interactive_controls: payload.interactive_controls,
        link_context: payload.link_context.clone(),
        datasets: None,
        measuring: false,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    let svg = crate::families::table::render(&table_payload, fonts).map_err(|e| e.to_string())?;
    let height = crate::board::extract_svg_height(&svg)?;
    Ok((svg, height))
}

#[derive(Deserialize)]
struct TotalConfig {
    visible: bool,
    label: Option<String>,
    /// Retired key, present only to be refused.
    format: Option<Value>,
}

#[derive(Deserialize)]
struct TotalStyle {
    value: TotalValueStyle,
    label: TotalSlotStyle,
}

/// The center value's resolved style. Its d3 `format` lives here and nowhere
/// else: `ChartTotal` has no `format` key.
#[derive(Deserialize)]
struct TotalValueStyle {
    font: Font,
    #[serde(default)]
    format: Option<FormatSlot>,
}

#[derive(Deserialize)]
struct TotalSlotStyle {
    font: Font,
}

#[derive(Deserialize)]
struct PieStyle {
    inner_radius: f64,
    slice_mark: SliceMark,
    total_style: TotalStyle,
    /// Width-resolved title font (production's `chart_title_spec` output) —
    /// always preferred over the un-tiered `title_style.font`.
    #[serde(default)]
    title_font: Option<Font>,
    /// The format of the tooltip's value and `Σ` rows -- absent (older
    /// fixtures) or empty means no tooltip aria-labels for this chart, same
    /// gate as bar's own `tooltip_labels` (`chart.style.tooltip_format`).
    #[serde(default)]
    tooltip_format: FormatSlot,
}

#[derive(Deserialize)]
struct SliceMark {
    /// Arc mark opacity, painted on every wedge. A legend symbol would carry
    /// it too (`encode.ts` `symbols` forwards it), so a visible legend refuses it.
    opacity: Option<f64>,
    gap: Option<f64>,
    corner_radius: Option<f64>,
    stroke: Option<Stroke>,
    labels: Option<Labels>,
}

#[derive(Deserialize)]
struct Labels {
    offset: f64,
    line_height: f64,
    font: Font,
    template: String,
}

/// `legend.title.font.case`. The theme cascade guarantees every resolved
/// chart's `legend.title.font` is populated (`core/AGENTS.md`'s "Theme YAML
/// completeness"), so a caller reaching this with `tooltip_labels` true and
/// no case is a genuine resolved-payload defect, not a shape to default
/// around.
fn pie_legend_title_case(legend: &LegendConfig) -> Result<&str, String> {
    legend
        .title
        .font
        .case
        .as_deref()
        .ok_or_else(|| "pie: tooltip value title requires legend.title.font.case".to_string())
}

/// One row's structured-tooltip aria-label -- shared by the arc loop, the
/// center-total texts (always row 0, see `draw_pie_fit`'s doc comment at
/// its call site), and the wedge slice-label texts (each its own row).
fn pie_row_aria(
    r: &Resolved,
    payload_rows: &[serde_json::Map<String, Value>],
    thetas: &[f64],
    total: f64,
    value_title: &str,
    idx: usize,
) -> Result<String, String> {
    let header = r.identity_field.as_ref().map(|f| identity_string(&payload_rows[idx], Some(f)));
    let value = thetas[idx];
    let tt = crate::contract::PieTooltip {
        header: header.as_deref(),
        share: value / total,
        value_title,
        value_format: &r.style.tooltip_format,
        value,
        total,
    };
    crate::contract::pie_aria_label(&tt)
}

/// The href of row `idx`'s datum: `r.link` with its channel placeholders
/// substituted from that row, `None` on a chart with no `link`.
fn row_href(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    idx: usize,
) -> Result<Option<String>, String> {
    r.link
        .as_deref()
        .map(|template| {
            crate::href::resolve_channel_href(template, |channel| {
                pie_link_value(r, &rows[idx], channel)
            })
        })
        .transpose()
}

/// `_channel_field` for a pie (`click_interactivity.py:20-58`): `color` is the
/// color channel's `data_field`, `theta` the measure. A pie has no `x`/`y`
/// encoding, where production raises the same error at spec build.
fn pie_link_value(
    r: &Resolved,
    row: &serde_json::Map<String, Value>,
    channel: &str,
) -> Result<String, String> {
    let field = match channel {
        "color" => r.resolved_channels.get("color").and_then(|c| c.data_field.as_deref()),
        "theta" => Some(r.theta.as_str()),
        _ => None,
    };
    let field = field.ok_or_else(|| {
        format!(
            "link template references channel '{{{{ {channel} }}}}' but chart has no \
             '{channel}' encoding assigned"
        )
    })?;
    crate::href::js_string(row.get(field))
}

fn identity_string(row: &serde_json::Map<String, Value>, field: Option<&String>) -> String {
    field
        .and_then(|f| row.get(f))
        .map(|v| match v {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        })
        .unwrap_or_default()
}

/// The color channel's data field, `None` when the chart authors no
/// `color:`. Without it there is neither a color scale nor a legend.
fn color_field_of(r: &Resolved) -> Option<&str> {
    r.resolved_channels
        .get("color")
        .and_then(|ch| ch.data_field.as_deref())
        .filter(|f| !f.is_empty())
}

/// Whether a legend is drawn at all: visible and a color field to key it.
fn legend_is_drawn(r: &Resolved) -> bool {
    r.legend.visible && color_field_of(r).is_some()
}

// Vega's own default for the most symbol entries a legend draws (vega-parser's
// default `config.legend`, which Vega-Lite leaves in force when the theme does
// not author it).
const VEGA_LEGEND_SYMBOL_LIMIT: u32 = 30;

/// A pie's color legend as Vega lays it out: `(label, fill)` per entry plus
/// the shared grid layout, and the slice stroke color the symbols carry.
struct PieLegend {
    entries: Vec<(String, String)>,
    layout: VegaLegend,
    symbol_stroke: Option<String>,
}

impl PieLegend {
    fn entries(&self) -> Vec<LegendEntry<'_>> {
        self.entries.iter().map(|(label, fill)| LegendEntry {
            label,
            fill,
            symbol: None,
            series_key: None,
        }).collect()
    }

    /// A filled `arc` mark's legend symbol: Vega's default `circle` in the
    /// scale's fill, stroked in the mark's own constant `stroke`
    /// (`legend/encode.ts` `symbols`: the mark `stroke` flows, its width and
    /// join do not), unstroked when the mark has none.
    fn symbol(&self) -> LegendSymbol<'_> {
        match &self.symbol_stroke {
            Some(stroke) => LegendSymbol::StrokedCircle(stroke),
            None => LegendSymbol::FilledCircle,
        }
    }
}

/// Lays the pie's color legend out through the shared Vega layout, over the
/// color scale's domain. `None` when no legend is drawn.
/// Every legend shape this port does not draw refuses by name.
fn plan_legend(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    category_scale: Option<&crate::common::CategoryColorScale>,
    fonts: &FontBook,
) -> Result<Option<PieLegend>, String> {
    let Some(color_field) = color_field_of(r).filter(|_| r.legend.visible) else {
        return Ok(None);
    };
    let cfg = &r.legend;
    let channel = &r.resolved_channels["color"];
    // A non-series channel is a gradient legend; a series over numeric data
    // is a quantitative scale, which draws a gradient bar, not symbols.
    if channel.mode != "series" {
        return Err(format!("pie: a legend over a {:?} color channel (gradient legend) is not ported", channel.mode));
    }
    match channel.quantitative_data {
        Some(false) => {}
        Some(true) => return Err("pie: a legend over a quantitative color field (gradient legend) is not ported".into()),
        None => return Err("pie: a visible legend needs the color channel's quantitative_data flag (resolver required)".into()),
    }
    if cfg.symbol_shape.is_some() {
        return Err("pie: legend.symbol_shape (VL symbolType) is not ported".into());
    }
    if cfg.symbol_fill.is_some() {
        return Err("pie: legend.symbol_fill (VL symbolFillColor) is not ported".into());
    }
    if cfg.values.as_ref().is_some_and(Vec::is_empty) {
        return Err("pie: legend.values: [] (an empty legend) is not ported".into());
    }
    if r.style.slice_mark.opacity.is_some() {
        return Err("pie: slice opacity under a visible legend (the symbols carry it) is not ported".into());
    }
    if !matches!(cfg.direction.as_str(), "vertical" | "horizontal") {
        return Err(format!("pie: legend.direction {:?} is not vertical or horizontal", cfg.direction));
    }
    if !matches!(cfg.position.edge.as_str(), "right" | "left" | "top" | "bottom") {
        return Err(format!("pie: legend.position edge {:?} is not a side of the plot", cfg.position.edge));
    }

    // The scale's domain: the color values in row order, each once; a null
    // draws no entry.
    let mut domain: Vec<&str> = Vec::new();
    for row in rows {
        match row.get(color_field) {
            None | Some(Value::Null) => {}
            Some(value @ Value::String(text)) => {
                if is_date_like_cell(Some(value)) {
                    return Err("pie: a legend over a date-like color field (temporal scale) is not ported".into());
                }
                if !domain.contains(&text.as_str()) {
                    domain.push(text);
                }
            }
            Some(other) => {
                return Err(format!("pie: a legend over the non-string color value {other} is not ported"));
            }
        }
    }
    let fills: Vec<String> = domain
        .iter()
        .enumerate()
        .map(|(idx, value)| match category_scale {
            Some(scale) => category_color(scale, value, &r.palette),
            None => Ok(r.palette[idx % r.palette.len()].clone()),
        })
        .collect::<Result<_, _>>()?;

    if domain.is_empty() {
        return Err("pie: a legend over a color field with no values is not ported".into());
    }
    // The scale's domain stays in row order; an authored `legend.values`
    // (`apply_legend_entry_order`) picks and orders the entries drawn, each
    // keeping its own swatch.
    let order: Vec<usize> = match &cfg.values {
        None => (0..domain.len()).collect(),
        Some(authored) => resolve_legend_values(authored, &domain, None),
    };

    // `symbolLimit`: vega-encode's `LegendEntries` keeps the first
    // `limit - 1` entries and folds the rest into one "…N entries" row that
    // carries the next entry's own swatch.
    let limit = cfg.symbol_limit.unwrap_or(VEGA_LEGEND_SYMBOL_LIMIT) as usize;
    let mut entries: Vec<(String, String)> =
        order.iter().map(|&idx| (domain[idx].to_string(), fills[idx].clone())).collect();
    if limit > 0 && entries.len() > limit {
        let kept = limit - 1;
        let folded = (format!("\u{2026}{} entries", entries.len() - kept), entries[kept].1.clone());
        entries.truncate(kept);
        entries.push(folded);
    }

    let title = legend_title_text(color_field, cfg, &r.field_labels)?;
    let limited = limit_legend_text(entries.iter().map(|(label, _)| label.as_str()), &title, cfg, fonts)?;
    for ((label, _), drawn) in entries.iter_mut().zip(limited.labels) {
        *label = drawn;
    }
    let (label_ws, title, title_w) = (limited.label_ws, limited.title, limited.title_w);
    // An unstroked symbol bounds the bare 10px glyph (no stroke ring), which
    // tightens the row pitch; a stroked one is the grid's default entry.
    let symbol_stroke = r.style.slice_mark.stroke.as_ref().and_then(|s| s.color.clone());
    let entry_symbol = symbol_stroke.is_none().then_some(LegendSymbol::FilledCircle);
    let layout =
        legend_from_widths(&label_ws, &vec![entry_symbol; entries.len()], false, &title, title_w, cfg)?;
    Ok(Some(PieLegend { entries, layout, symbol_stroke }))
}

/// Standalone pie through Vega's own fit-margin algebra: production compiles
/// the wheel as a layer spec under `autosize: fit` where the slice-label
/// text ink pushes the PLOT MARGINS (asymmetric left/right — donut_example's
/// origin is (33, 44)) while the radius stays `min(pw, ph)/2 ×
/// outer_fraction` of the fitted box, re-evaluated per resize pass.
/// `fit_plot`'s passes model that directly: each candidate (pw, ph)
/// re-derives the radius and label anchors and unions their ink. A wheel's
/// label ink moves WITH the plot, so the sequence generally has no fixed
/// point — which is why `fit_plot` runs Vega's own bounded pass count rather
/// than iterating to one (`AUTOSIZE_PADDING_PASSES`). The attached-table
/// composition renders its wheel through this same fit path at
/// `width=wheel_width` and the theme's unsized view height; only the outer
/// wheel+table placement is its own arithmetic.
#[allow(clippy::too_many_arguments)]
fn draw_pie_fit(
    frame: &ChartFrame,
    r: &Resolved,
    payload_rows: &[serde_json::Map<String, Value>],
    thetas: &[f64],
    total: f64,
    fonts: &FontBook,
    title_dx: f64,
) -> Result<(String, f64), String> {
    use crate::vega_fit;

    let (w, h) = (frame.w, frame.h);
    let (pad_l, pad_r, pad_t, pad_b) = frame.pad;

    // Per-visible-slice label geometry, fixed ahead of the fit (only the
    // anchor positions vary with the candidate plot box).
    struct SliceLabel {
        mid: f64,
        lines: Vec<String>,
        max_w: f64,
        fill: String,
        /// The row this label was built from — its own structured-tooltip
        /// aria-label reads `payload_rows`/`thetas` at this index (a real,
        /// per-row data-bound layer, unlike the center-total texts below).
        idx: usize,
    }
    let mut slice_labels: Vec<SliceLabel> = Vec::new();
    // The color channel's `data_field`, empty when the chart authors no
    // `color:`. Without it every wedge paints one constant `palette[0]` —
    // palette-by-index is the color scale's range, so it needs the scale to
    // exist.
    let color_field = color_field_of(r);
    let color_key = color_field.is_some();
    // The board's plan for this field, if it binds one (#7167) — the arc
    // fill defers to it over the local `slice.index % palette.len()`.
    let category_scale = color_field.and_then(|cf| category_scale_for(&r.category_colors, cf));
    let legend = plan_legend(r, payload_rows, category_scale, fonts)?;
    let labels_cfg = r.style.slice_mark.labels.as_ref();
    if let Some(labels) = labels_cfg {
        let template = Template::parse(&labels.template)?;
        let font_size = labels.font.size_or(14.0);
        let family = labels.font.family_or("sans-serif");
        let mut running = 0.0;
        for (idx, theta) in thetas.iter().enumerate() {
            let mid = std::f64::consts::TAU * (running + theta / 2.0) / total;
            running += theta;
            if !r.slice_label_indices.contains(&idx) {
                continue;
            }
            let lines = template.render(&Scope {
                row: &payload_rows[idx],
                theta_field: &r.theta,
                color_field: r.identity_field.as_deref(),
                percent: theta / total,
                total,
            })?;
            // The label's right (or left) edge joins the autosize fit, so it is
            // a width Vega measures: kerning included.
            let mut max_w = 0.0_f64;
            for line in &lines {
                max_w = max_w.max(fonts.measure_shaped(line, family, font_size)?);
            }
            // The board slot when bound — `ink_at`'s pure function of the
            // row's own value, same as the wedge's `color_at`. Production
            // reads `labels.font.color` only when there is no color key: a
            // color channel always inks with the dark companions, by board
            // slot or, unbound, by row position.
            let via_scale = match (category_scale, color_field) {
                (Some(scale), Some(cf))
                    if !matches!(payload_rows[idx].get(cf), None | Some(Value::Null)) =>
                {
                    Some(category_ink(scale, &row_str(&payload_rows[idx], cf), &r.dark_companion_stops, &r.pin_companions)?)
                }
                _ => None,
            };
            let authored = if color_field.is_some() { None } else { labels.font.color.clone() };
            let fill = via_scale.unwrap_or_else(|| {
                authored.unwrap_or_else(|| {
                    r.dark_companion_stops
                        .get(idx % r.dark_companion_stops.len().max(1))
                        .cloned()
                        .unwrap_or_else(|| "#333".into())
                })
            });
            slice_labels.push(SliceLabel { mid, lines, max_w, fill, idx });
        }
    }
    let label_fs = labels_cfg.map(|l| l.font.size_or(14.0)).unwrap_or(14.0);
    let label_lh = labels_cfg.map(|l| l.line_height).unwrap_or(14.0);
    let label_offset = labels_cfg.map(|l| l.offset).unwrap_or(6.0);

    // Title ink (width-resolved font slot). Overflowing text is processed
    // exactly as production's `apply_title_overflow_to_spec` processes it
    // BEFORE the wheel render — the shared `wrap_title_block` law.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    title.warn_truncated(&r.id);

    let arc_stroke_w = r
        .style
        .slice_mark
        .stroke
        .as_ref()
        .and_then(|s| s.width)
        .unwrap_or(0.0);

    if r.total.as_ref().is_some_and(|t| t.format.is_some()) {
        return Err("pie: total.format moved to style.total_style.value.format".to_string());
    }

    // Center-total text marks are real VL marks — their ink joins the fit
    // union (a long total can outgrow the wheel). Formatted once, reused at
    // emission.
    let total_texts: Option<(String, f64, f64, Option<(f64, f64)>)> = match &r.total {
        Some(total_cfg) if total_cfg.visible => {
            let value_font = &r.style.total_style.value.font;
            let value_size = value_font.size_or(14.0);
            let formatted = match &r.style.total_style.value.format {
                Some(fmt) => {
                    fmt.paint(total).map_err(|e| format!("pie: total value format: {e}"))?
                }
                None if total.fract() == 0.0 => format!("{}", total as i64),
                None => format!("{total}"),
            };
            let value_w =
                fonts.measure(&formatted, value_font.family_or("sans-serif"), value_size)?;
            let label_dims = match &total_cfg.label {
                Some(label_text) => {
                    let label_font = &r.style.total_style.label.font;
                    let label_size = label_font.size_or(11.0);
                    let lw = fonts.measure(
                        label_text,
                        label_font.family_or("sans-serif"),
                        label_size,
                    )?;
                    Some((lw, label_size))
                }
                None => None,
            };
            Some((formatted, value_w, value_size, label_dims))
        }
        _ => None,
    };

    // The legend's (x, y) in plot coordinates, recorded by the fit's last
    // (drawn) layout pass.
    let mut legend_xy = (0.0, 0.0);
    let m = vega_fit::fit_plot(
        w,
        h,
        (pad_l, pad_r, pad_t, pad_b),
        |pw, ph, b| {
            let radius = pw.min(ph) / 2.0 * r.outer_fraction;
            let (cx, cy) = (pw / 2.0, ph / 2.0);
            // Wheel ink: arc marks expand by their stroke (boundStroke).
            b.union(
                &vega_fit::Bounds::new(cx - radius, cy - radius, cx + radius, cy + radius)
                    .expand(arc_stroke_w),
            );
            if let Some((_, value_w, value_fs, label_dims)) = &total_texts {
                b.union(&vega_fit::text_ink(cx, cy, *value_w, *value_fs, "center", "bottom"));
                if let Some((label_w, label_fs)) = label_dims {
                    b.union(&vega_fit::text_ink(cx, cy, *label_w, *label_fs, "center", "top"));
                }
            }
            for sl in &slice_labels {
                let top = sl.mid.cos() >= 0.0;
                let right = sl.mid.sin() >= 0.0;
                let x = cx + sl.mid.sin() * (radius + label_offset);
                let y = cy - sl.mid.cos() * (radius + label_offset)
                    + if top { -(sl.lines.len() as f64 * label_lh) } else { 0.0 };
                let mut ti = vega_fit::text_ink(
                    x,
                    y,
                    sl.max_w,
                    label_fs,
                    if right { "left" } else { "right" },
                    "top",
                );
                if sl.lines.len() > 1 {
                    ti.y2 += (sl.lines.len() as f64 - 1.0) * label_lh;
                }
                b.union(&ti);
            }
            // The legend group's own bounds join the view ink on its own
            // axis only: `ViewLayout.js` unions a left/right legend as
            // `F.add(o.x1, 0).add(o.x2, 0)` and a top/bottom one as
            // `F.add(0, o.y1).add(0, o.y2)` under autosize fit. A pie has no
            // axes, so the legend anchors on the plot rect alone: `ceil(x2)`
            // or `ceil(y2)` plus the layout offset past the near edge,
            // `floor(x1)`/`floor(y1)` (0) minus offset minus its own extent
            // before the far one.
            if let Some(lg) = &legend {
                let (box_w, box_h) = (lg.layout.box_w, lg.layout.box_h());
                // A wheel has no axes and its arcs are bare marks: the
                // bounds are the plot rect's own.
                legend_xy = legend_origin(&r.legend.position, (0.0, pw), (0.0, ph), (pw, ph), (box_w, box_h));
                if let Some(reach) = legend_fit_bounds(&r.legend.position, legend_xy, (box_w, box_h)) {
                    b.union(&reach);
                }
            }
        },
        title.ink(),
    );
    if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
        return Err(format!("pie: plot box too small ({}×{})", m.plot_w, m.plot_h));
    }
    let (pw, ph) = (m.plot_w, m.plot_h);
    let radius = pw.min(ph) / 2.0 * r.outer_fraction;
    let inner = radius * r.style.inner_radius;
    let (cx, cy) = (pad_l + m.left + pw / 2.0, pad_t + m.top + ph / 2.0);
    if let Some((formatted, value_w, _, _)) = &total_texts {
        if inner > 0.0 && *value_w > 2.0 * inner {
            crate::diagnostic::warn(crate::diagnostic::Diagnostic::new(
                &crate::codes::WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS,
                [
                    ("chart_id", serde_json::json!(r.id)),
                    ("formatted_value", serde_json::json!(formatted)),
                    ("hole_diameter", serde_json::json!(2.0 * inner)),
                    ("slot_height", serde_json::json!(ph)),
                    ("slot_width", serde_json::json!(pw)),
                    ("text_width", serde_json::json!(value_w)),
                ],
            ));
        }
    }

    // ── Emission ─────────────────────────────────────────────────────────
    let mut svg = String::with_capacity(4096);
    frame.background(&mut svg);
    if let (Some(_), false) = (m.title_pos, title.block().title_lines.is_empty()) {
        // A left-placed table pushes the wheel right, and the title group
        // slides back by that much so it stays on the card's own left edge.
        // Printed `{dx:g}`.
        if title_dx != 0.0 {
            let _ = write!(svg, "<g transform=\"translate({},0)\">", d3rs::py_float::format_g(title_dx));
        }
        title.paint(&mut svg, m.title_pos, frame.origin(&m));
        if title_dx != 0.0 {
            svg.push_str("</g>");
        }
    }

    let gap = r.style.slice_mark.gap.unwrap_or(0.0);
    let corner = r.style.slice_mark.corner_radius.unwrap_or(0.0);
    let slices = pie(thetas, 0.0, std::f64::consts::TAU, gap);
    let stroke_attrs = match &r.style.slice_mark.stroke {
        Some(s) => format!(
            " stroke=\"{}\" stroke-width=\"{}\" stroke-linejoin=\"{}\"",
            xml_escape_attr(s.color.as_deref().unwrap_or("none")),
            s.width.unwrap_or(1.0),
            xml_escape_attr(s.join.as_deref().unwrap_or("miter")),
        ),
        None => String::new(),
    };
    let stroke_attrs = match r.style.slice_mark.opacity {
        Some(opacity) => format!("{stroke_attrs} opacity=\"{opacity}\""),
        None => stroke_attrs,
    };
    // Structured-tooltip content, computed once: `value_title` doesn't vary
    // per slice (`features/structured_tooltip.py::_pie_roles`'s
    // `format_display_text(chart.theta, ..., font=chart.legend.title.font)`
    // -- `slug_to_display` is this port's own two-step mirror of that
    // pipeline). `None` (no `tooltip_format`) matches bar's own
    // `tooltip_labels` gate: an older fixture with no format authored keeps
    // today's unlabeled, unwrapped arc output.
    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();
    let value_title = if tooltip_labels {
        let case = pie_legend_title_case(&r.legend)?;
        Some(crate::common::field_display_text(&r.theta, &r.field_labels, case)?)
    } else {
        None
    };
    let _ = write!(svg, "<g transform=\"translate({cx},{cy})\">");
    for slice in &slices {
        let path = Arc {
            inner_radius: inner,
            outer_radius: radius,
            corner_radius: corner,
            pad_radius: None,
            start_angle: slice.start_angle,
            end_angle: slice.end_angle,
            pad_angle: slice.pad_angle,
        }
        .path();
        let fill: String = if color_key {
            let positional = || r.palette[slice.index % r.palette.len()].clone();
            match (category_scale, color_field) {
                (Some(scale), Some(cf))
                    if !matches!(payload_rows[slice.index].get(cf), None | Some(Value::Null)) =>
                {
                    category_color(scale, &row_str(&payload_rows[slice.index], cf), &r.palette)?
                }
                _ => positional(),
            }
        } else {
            r.palette[0].clone()
        };
        let aria = match &value_title {
            Some(value_title) => Some(pie_row_aria(
                r,
                payload_rows,
                thetas,
                total,
                value_title,
                slice.index,
            )?),
            None => None,
        };
        let label_attr = crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::ArcMark)?;
        let href = row_href(r, payload_rows, slice.index)?;
        if aria.is_some() {
            crate::contract::mark_group_open(&mut svg, "arc", "layer_0_marks")?;
        }
        svg.push_str(&crate::href::anchor_open(r.link_open.anchor(href.as_deref())));
        let _ = write!(
            svg,
            "<path d=\"{path}\" fill=\"{fill}\"{stroke_attrs}{label_attr}/>",
            fill = xml_escape_attr(&fill)
        );
        svg.push_str(crate::href::anchor_close(r.link_open.anchor(href.as_deref())));
        if aria.is_some() {
            crate::contract::mark_group_close(&mut svg);
        }
    }
    let _ = write!(svg, "</g>");

    // Center total — Vega text baselines: value "bottom", caption "top".
    // Both text layers carry one row, so a description inherited from the
    // arc layer would name the FIRST slice whichever slice is meant: each is
    // a hidden mark set.
    if let (Some(total_cfg), Some((formatted, _, _, _))) = (&r.total, &total_texts) {
        let total_href = row_href(r, payload_rows, 0)?;
        {
            let value_font = &r.style.total_style.value.font;
            let value_size = value_font.size_or(14.0);
            let value_family = value_font.family_or("sans-serif");
            let value_weight = value_font.weight_str();
            let value_color = value_font.color.as_deref().unwrap_or("#000");
            let value_y = cy + vega_fit::baseline_shift("bottom", value_size);
            crate::contract::hidden_mark_group_open(&mut svg, "text", "layer_1_marks");
            svg.push_str(&crate::href::anchor_open(r.link_open.anchor(total_href.as_deref())));
            let _ = write!(
                svg,
                "<text x=\"{cx:.2}\" y=\"{value_y:.2}\" text-anchor=\"middle\" \
                 font-family=\"{value_family}\" font-size=\"{value_size}\"{} \
                 font-weight=\"{value_weight}\" fill=\"{value_color}\">{}</text>",
                value_font.style_attr(),
                xml_escape(formatted), value_family = vega_escape_attr(value_family), value_weight = xml_escape_attr(&value_weight), value_color = xml_escape_attr(value_color)
            );
            svg.push_str(crate::href::anchor_close(r.link_open.anchor(total_href.as_deref())));
            crate::contract::mark_group_close(&mut svg);
            if let Some(label_text) = &total_cfg.label {
                let label_font = &r.style.total_style.label.font;
                let label_size = label_font.size_or(11.0);
                let label_family = label_font.family_or("sans-serif");
                let label_weight = label_font.weight_str();
                let label_color = label_font.color.as_deref().unwrap_or("#000");
                let label_y = cy + vega_fit::baseline_shift("top", label_size);
                crate::contract::hidden_mark_group_open(&mut svg, "text", "layer_2_marks");
                svg.push_str(&crate::href::anchor_open(r.link_open.anchor(total_href.as_deref())));
                let _ = write!(
                    svg,
                    "<text x=\"{cx:.2}\" y=\"{label_y:.2}\" text-anchor=\"middle\" \
                     font-family=\"{label_family}\" font-size=\"{label_size}\"{} \
                     font-weight=\"{label_weight}\" fill=\"{label_color}\">{}</text>",
                    label_font.style_attr(),
                    xml_escape(label_text), label_family = vega_escape_attr(label_family), label_weight = xml_escape_attr(&label_weight), label_color = xml_escape_attr(label_color)
                );
                svg.push_str(crate::href::anchor_close(r.link_open.anchor(total_href.as_deref())));
                crate::contract::mark_group_close(&mut svg);
            }
        }
    }

    // Slice labels at the fitted geometry — a real per-row data-bound
    // layer (unlike the center-total texts above), so each label's own
    // aria-label is its OWN row's, never row 0's.
    if let Some(labels) = labels_cfg {
        let family = labels.font.family_or("sans-serif");
        let weight = labels.font.weight_str();
        for sl in &slice_labels {
            let top = sl.mid.cos() >= 0.0;
            let right = sl.mid.sin() >= 0.0;
            let x = num_full(cx + sl.mid.sin() * (radius + label_offset));
            let y_anchored = cy - sl.mid.cos() * (radius + label_offset)
                + if top { -(sl.lines.len() as f64 * label_lh) } else { 0.0 };
            let anchor = if right { "start" } else { "end" };
            let first_dy = vega_fit::baseline_shift("top", label_fs);
            let label_aria: Option<String> = match &value_title {
                Some(value_title) => {
                    Some(pie_row_aria(r, payload_rows, thetas, total, value_title, sl.idx)?)
                }
                None => None,
            };
            let label_attr = crate::contract::aria_attr(label_aria.as_deref(), crate::contract::ItemRole::TextMark)?;
            let label_href = row_href(r, payload_rows, sl.idx)?;
            if label_aria.is_some() {
                crate::contract::mark_group_open(&mut svg, "text", "layer_3_marks")?;
            }
            svg.push_str(&crate::href::anchor_open(r.link_open.anchor(label_href.as_deref())));
            let _ = write!(
                svg,
                "<text x=\"{x}\" y=\"{}\" text-anchor=\"{anchor}\" \
                 font-family=\"{family}\" font-size=\"{label_fs}\"{} \
                 font-weight=\"{weight}\" fill=\"{}\"{label_attr}>",
                num_full(y_anchored + first_dy),
                labels.font.style_attr(),
                xml_escape_attr(&sl.fill), anchor = xml_escape_attr(anchor), family = vega_escape_attr(family), weight = xml_escape_attr(&weight)
            );
            for (i, line) in sl.lines.iter().enumerate() {
                let _ = write!(
                    svg,
                    "<tspan x=\"{x}\" dy=\"{}\">{}</tspan>",
                    if i == 0 { 0.0 } else { label_lh },
                    xml_escape(line)
                );
            }
            let _ = write!(svg, "</text>");
            svg.push_str(crate::href::anchor_close(r.link_open.anchor(label_href.as_deref())));
            if label_aria.is_some() {
                crate::contract::mark_group_close(&mut svg);
            }
        }
    }

    // The legend group paints after every mark layer, slid along its strip
    // for `align` center/end (`legend_align.py`), against the plot rect the
    // scenegraph reports rather than the drawn one.
    if let Some(lg) = &legend {
        let (dx, dy) = legend_slide(&r.id, &r.legend.position, &lg.layout, m.scenegraph_plot).map_err(|d| d.to_string())?;
        emit_vega_legend(
            &mut svg,
            &lg.entries(),
            &lg.layout,
            &r.legend,
            lg.symbol(),
            pad_l + m.left + legend_xy.0 + dx,
            pad_t + m.top + legend_xy.1 + dy,
            false,
        );
    }

    Ok((svg, radius))
}



/// A pie over no rows: production compiles the arc and label layers over an
/// empty dataset, so Vega draws the two empty mark groups inside the
/// negotiated frame (`draw_pie_fit` with no slices). Its zero total is the
/// empty dataset's, not a chart of zero-valued slices — that stays refused in
/// [`render`].
pub fn render_empty(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("pie payload: {e}"))?;
    if r.attached_table.as_ref().is_some_and(|v| !v.is_null()) {
        return Err("pie: empty result set not supported with an attached table".into());
    }
    if legend_is_drawn(&r) {
        return Err("pie: a visible legend over an empty result set is not ported".into());
    }
    let frame = ChartFrame::of(payload, r.background.as_str());
    let (content, _radius) = draw_pie_fit(
        &frame,
        &r,
        &payload.rows,
        &[],
        0.0,
        fonts,
        0.0,
    )?;
    let mut svg = String::new();
    frame.root(&mut svg);
    Ok(format!("{svg}{content}</svg>"))
}

/// `title_band.py::vega_title_block_height`: a titled chart's title block down
/// to the plot, padding excluded — the title font size, one subtitle line (its
/// font size plus Vega's default 3px subtitle padding) and `title.offset`
/// (Vega's own 4 when unset).
fn vega_title_block_height(r: &Resolved) -> Result<f64, String> {
    let title_size = r
        .style
        .title_font
        .as_ref()
        .and_then(|f| f.size)
        .ok_or("pie: a titled attached_table at the top requires style.title_font.size")?;
    let subtitle = if r.subtitle.as_deref().is_some_and(|s| !s.is_empty()) {
        let size = r
            .title_style
            .as_ref()
            .and_then(|ts| ts.subtitle.as_ref())
            .and_then(|sub| sub.font.size)
            .ok_or("pie: a subtitled attached_table at the top requires title_style.subtitle.font.size")?;
        size + crate::constants::layout::VEGA_SUBTITLE_PADDING
    } else {
        0.0
    };
    // The shared shift law (`title_shift::shift_vega_payload`) has already
    // refused an unset offset before this runs; this read never falls back.
    let offset = r
        .title_style
        .as_ref()
        .and_then(|ts| ts.position.as_ref())
        .and_then(|pos| pos.offset)
        .ok_or("pie: a titled attached_table at the top requires title_style.position.offset")?;
    Ok(title_size + subtitle + offset)
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let r: Resolved =
        serde_json::from_value(payload.resolved.clone()).map_err(|e| format!("pie payload: {e}"))?;

    // Fired here, in the shared entry gate ahead of the attached-table /
    // fit-path dispatch below — pie's authored surface can no longer
    // produce this from a real board (conditional_formatting is retired
    // from pie/donut), but a hand-written or stale resolved-JSON fixture
    // can still hand it a conditional-mode color channel. Covers both the
    // slice fill and the label ink (draw_pie_fit's two paint sites, both
    // keyed off this same channel) with one check.
    if r.resolved_channels.get("color").is_some_and(|ch| ch.mode == "conditional") {
        return Err("pie: conditional color channel not supported".into());
    }

    // The chart's OWN effective background — see `Resolved::background`.
    let frame = ChartFrame::of(payload, r.background.as_str());
    let (w, (pad_l, pad_r, pad_t, pad_b)) = (frame.w, frame.pad);

    let thetas: Vec<f64> = payload
        .rows
        .iter()
        .map(|row| row.get(&r.theta).and_then(Value::as_f64).unwrap_or(0.0))
        .collect();
    let total: f64 = thetas.iter().sum();
    if total <= 0.0 {
        return Err("pie total is zero — nothing to draw".into());
    }

    let mut svg = String::with_capacity(4096);

    match r.attached_table.as_ref().filter(|v| !v.is_null()) {
        None => {
            frame.root(&mut svg);
            let (content, _radius) = draw_pie_fit(
                &frame,
                &r,
                &payload.rows,
                &thetas,
                total,
                fonts,
                0.0,
            )?;
            if payload.is_placeholder {
                // `render_vega_spec`'s placeholder arm: the whole drawing faded
                // under the overlay, in the board's font.
                let style = payload
                    .board_style
                    .as_ref()
                    .and_then(|b| b.placeholder.as_ref())
                    .ok_or("pie: a placeholder chart needs board_style.placeholder")?;
                let family = style
                    .overlay
                    .font
                    .family
                    .as_deref()
                    .ok_or("pie: a placeholder chart needs board_style.placeholder.overlay.font.family")?;
                let _ = write!(svg, "<g opacity=\"{}\">{content}</g>", py_float_str(style.opacity));
                let mut overlay = crate::xml::Xml::new();
                style.overlay(&mut overlay, w, frame.h, family, None).map_err(|e| e.to_string())?;
                svg.push_str(&overlay.finish());
            } else {
                svg.push_str(&content);
            }
        }
        // Production composes the attached-table wheel and table without the
        // placeholder flag, so neither fades.
        Some(attached) => {
            // Production suppresses the legend whenever the key is a table
            // (`suppress_legend=mode != "direct"`); a payload carrying both
            // is not production's.
            if legend_is_drawn(&r) {
                return Err("pie: a visible legend beside an attached table is not ported".into());
            }
            // The wheel and table are two independently-backgrounded children
            // placed side by side or stacked — the outer canvas itself carries
            // no background fill, same as production leaves the gap between
            // them unpainted.
            let placement = r
                .attached_table_placement
                .as_deref()
                .ok_or("pie: attached_table requires attached_table_placement")?;
            let align = r
                .attached_table_align
                .as_deref()
                .ok_or("pie: attached_table requires attached_table_align")?;
            // `LegendEdge` and `LegendAlign` (compile/models/style/theme.py) are the
            // only values `compose_attached_table_svg` indexes; anything else is
            // not a production payload.
            if !matches!(placement, "top" | "bottom" | "left" | "right")
                || !matches!(align, "start" | "center" | "end")
            {
                return Err(format!(
                    "pie: attached_table placement '{placement}' align '{align}' is not supported"
                ));
            }
            let table_width = r
                .attached_table_width
                .ok_or("pie: attached_table requires attached_table_width")?;
            let gap = r.attached_table_gap.ok_or("pie: attached_table requires attached_table_gap")?;
            let wheel_width = r.wheel_width.ok_or("pie: attached_table requires wheel_width")?;
            let heading = r.attached_heading.as_deref().unwrap_or("");

            let table_category_scale = r
                .identity_field
                .as_deref()
                .and_then(|cf| category_scale_for(&r.category_colors, cf));
            let (mut table_content, mut table_h) = render_attached_table_svg(
                payload,
                attached,
                &r.attached_row_indices,
                &r.palette,
                r.identity_field.as_ref(),
                &r.theta,
                &thetas,
                total,
                table_width,
                fonts,
                table_category_scale,
            )?;

            if !heading.is_empty() {
                let heading_font = r
                    .attached_heading_font
                    .as_ref()
                    .ok_or("pie: attached_table heading requires attached_heading_font")?;
                let heading_gap = r
                    .hybrid_heading_gap
                    .ok_or("pie: attached_table heading requires hybrid_heading_gap")?;
                let heading_size = heading_font.size_or(11.0);
                // The heading block: size + 2 + gap.
                let heading_block_h = heading_size + 2.0 + heading_gap;
                let heading_family = heading_font.family_or("sans-serif");
                let heading_weight = heading_font.weight_str();
                let heading_color = heading_font.color.as_deref().unwrap_or("#000");
                let wrapped_w = crate::svg::num(table_width);
                let wrapped_h = crate::svg::num(table_h + heading_block_h);
                let mut wrapped = format!(
                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{wrapped_w}\" height=\"{wrapped_h}\">"
                );
                let _ = write!(
                    wrapped,
                    "<text x=\"0\" y=\"{:.2}\" font-family=\"{heading_family}\" font-size=\"{heading_size}\" \
                     font-weight=\"{heading_weight}\" fill=\"{heading_color}\">{}</text>",
                    heading_size * 0.8,
                    xml_escape(heading), heading_family = xml_escape_attr(heading_family), heading_weight = xml_escape_attr(&heading_weight), heading_color = xml_escape_attr(heading_color)
                );
                let _ = write!(wrapped, "<g transform=\"translate(0, {heading_block_h:.2})\">{table_content}</g>");
                wrapped.push_str("</svg>");
                table_content = wrapped;
                table_h += heading_block_h;
            }

            // The wheel is a full standalone VL render at `width=wheel_width,
            // height=None` — its effective height is the theme's
            // `chart_defaults.view.continuous_height` (VL's
            // `config.view.continuousHeight`), NOT the slot height — and the
            // composed canvas takes its own outer size from the children:
            // `donut_h + gap + table_h` below, `max(donut_h, table_y +
            // table_h)` at a side, the wheel's own height at the top. The slot
            // height plays no part; the canvas legitimately grows past it or
            // shrinks under it (a documented carve-out in the geometry
            // contract).
            let base_h = payload
                .board_style
                .as_ref()
                .and_then(|bs| bs.continuous_height)
                .ok_or("pie: attached_table requires board_style.continuous_height (the wheel renders at the theme's view height)")?;

            // `placement == "top"` (vega_lite.py:605-628): the table sits
            // between the title and the plot, so the wheel's spec gains the
            // table's height (heading included) plus the gap — added to the
            // view height because `autosize: fit` holds the whole view to it,
            // and to the plot's top through `shift_artifact_title` under a
            // title (the same `shift_chart_title` law a row shift runs) or
            // `bump_padding_top` without one. The table lands where the plot
            // would have started: below the card padding and the title block.
            let titled = r.title.as_deref().is_some_and(|t| !t.is_empty());
            let (wheel_h, wheel_pad_t, pushed, top_table_y) = if placement == "top" {
                let room = table_h + gap;
                if titled {
                    let shift = crate::TitleShift { title_dy: 0.0, body_dy: room };
                    let shifted = crate::title_shift::shift_vega_payload(payload, shift)?;
                    let pushed: Resolved = serde_json::from_value(shifted.resolved.clone())
                        .map_err(|e| format!("pie payload: {e}"))?;
                    let pushed_pad_t = shifted
                        .padding
                        .expect("shift_vega_payload refuses a payload without padding")
                        .top;
                    (base_h + room, pushed_pad_t, Some(pushed), pad_t + vega_title_block_height(&r)?)
                } else {
                    (base_h + room, pad_t + room, None, pad_t)
                }
            } else {
                (base_h, pad_t, None, 0.0)
            };
            // A left table pushes the wheel right by its own width and gap.
            let title_dx = if placement == "left" { -(table_width + gap) } else { 0.0 };

            let wheel_frame = ChartFrame::new(
                wheel_width,
                wheel_h,
                (pad_l, pad_r, wheel_pad_t, pad_b),
                r.background.as_str(),
                num_full,
            );
            let (donut_content, radius) = draw_pie_fit(
                &wheel_frame,
                pushed.as_ref().unwrap_or(&r),
                &payload.rows,
                &thetas,
                total,
                fonts,
                title_dx,
            )?;

            let (outer_h, donut_x, table_x, table_y) = match placement {
                "left" | "right" => {
                    // The table aligns down the wheel's disk, not the card,
                    // and never above the disk's top.
                    let diameter = 2.0 * radius;
                    let disk_top = ((wheel_h - diameter) / 2.0).max(0.0);
                    let table_y = disk_top.max(match align {
                        "start" => disk_top,
                        "center" => disk_top + (diameter - table_h) / 2.0,
                        _ => disk_top + diameter - table_h,
                    });
                    let (donut_x, table_x) = if placement == "right" {
                        (0.0, wheel_width + gap)
                    } else {
                        (table_width + gap, pad_l)
                    };
                    (wheel_h.max(table_y + table_h), donut_x, table_x, table_y)
                }
                _ => {
                    // Across the card inside its padding.
                    let across_x = match align {
                        "start" => pad_l,
                        "center" => (w - table_width) / 2.0,
                        _ => w - table_width - pad_r,
                    };
                    let donut_x = (w - wheel_width) / 2.0;
                    if placement == "top" {
                        (wheel_h, donut_x, across_x, top_table_y)
                    } else {
                        (wheel_h + gap + table_h, donut_x, across_x, wheel_h + gap)
                    }
                }
            };

            ChartFrame { h: outer_h, ..frame }.root(&mut svg);
            let _ = write!(svg, "<g transform=\"translate({donut_x:.2}, 0)\">{donut_content}</g>");
            let _ = write!(svg, "<g transform=\"translate({table_x:.2}, {table_y:.2})\">{table_content}</g>");
        }
    }

    svg.push_str("</svg>");
    Ok(svg)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_pie_payload(total: Value) -> Value {
        json!({
            "resolved": {
                "link_open": "page",
                "id": "pie",
                "chart_type": "pie",
                "theta": "revenue",
                "identity_field": "category",
                "title": null,
                "title_style": null,
                "background": "#FAFAFA",
                "palette": ["#3164a3", "#779bc9", "#ad9c7f"],
                "dark_companion_stops": ["#183253", "#3c4d65"], "pin_companions": [],
                "outer_fraction": 0.9,
                "slice_label_indices": [],
                "total": total,
                "attached_table": null,
                "legend": {
                    "visible": false,
                    "position": {"edge": "right", "align": "start", "overlay": false},
                    "direction": "vertical",
                    "label": {"font": {"size": 11.0}},
                    "title": {"font": {"case": "title"}}
                },
                "field_labels": {},
                "style": {
                    "inner_radius": 0.6,
                    "slice_mark": {
                        "gap": 0.02,
                        "corner_radius": 2.0,
                        "stroke": null,
                        "labels": null
                    },
                    "total_style": {
                        "value": {
                            "font": {
                                "family": "Inter",
                                "color": "#222222",
                                "size": 18.0,
                                "weight": "600",
                                "line_height": 1.25
                            }
                        },
                        "label": {
                            "font": {
                                "family": "Inter",
                                "color": "#8A8C8F",
                                "size": 11.0,
                                "weight": "400",
                                "line_height": 1.25
                            }
                        }
                    }
                }
            },
            "rows": [
                {"category": "Electronics", "revenue": 197199.0},
                {"category": "Accessories", "revenue": 152504.0},
                {"category": "Tools", "revenue": 93320.0}
            ],
            "width": 600.0,
            "height": 432.0,
            "background": "#FAFAFA",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    fn make_payload(payload_json: Value) -> Payload {
        crate::test_payload(payload_json)
    }

    /// The `share` cell of each slice value of a pie totaling 100.
    fn share_cells(values: &[f64]) -> Vec<String> {
        let mut payload = make_payload(make_pie_payload(Value::Null));
        payload.rows = values
            .iter()
            .map(|value| {
                let mut row = serde_json::Map::new();
                row.insert("revenue".into(), json!(value));
                row
            })
            .collect();
        let indices: Vec<usize> = (0..values.len()).collect();
        let palette = vec!["#3164a3".to_string()];
        project_pie_table_rows(&payload, &indices, &palette, None, "revenue", values, 100.0, None)
            .expect("rows project")
            .iter()
            .map(|row| row["share"].as_str().expect("share is a string").to_string())
            .collect()
    }

    /// The attached table's `share` cell is the slice label's own `.0%`: d3 rounds the exact
    /// decimal expansion half away from zero, so 0.5 of 100 prints `1%`, never the even `0%`.
    /// Every string is production's `d3_format(".0%")(share)`:
    /// 0.4 `40%`, 0.3 `30%`, 0.2 `20%`, 0.08 `8%`, 0.015 `2%`, 0.005 `1%`,
    /// 0.175 `18%`, 0.025 `3%`, 0.0049 `0%`, 0.0051 `1%`, 0.125 `13%`, 0.375 `38%`.
    /// Each share is the slice value divided by 100 in doubles, as production computes it.
    #[test]
    fn attached_table_share_rounds_like_production_percent() {
        let ladder = [40.0, 30.0, 20.0, 8.0, 1.5, 0.5];
        let neighbors = [
            (17.5, "18%"),
            (2.5, "3%"),
            (0.49, "0%"),
            (0.51, "1%"),
            (12.5, "13%"),
            (37.5, "38%"),
        ];
        let cases: Vec<(f64, &str)> = ladder
            .iter()
            .copied()
            .zip(["40%", "30%", "20%", "8%", "2%", "1%"])
            .chain(neighbors)
            .collect();
        let values: Vec<f64> = cases.iter().map(|(value, _)| *value).collect();
        let shares = share_cells(&values);
        let expected: Vec<&str> = cases.iter().map(|(_, share)| *share).collect();
        assert_eq!(shares, expected);
    }

    /// The share cell prints through the board's number locale, as the slice labels do.
    /// Production under fr-FR (`number_locale_scope` over `house_locale(
    /// number_locale_definition("fr-FR"), ...)`): `d3_format(".0%")(0.005)` is `1\u{202f}%`
    /// and `(0.4)` is `40\u{202f}%`, the locale's percent sign with its narrow no-break space.
    #[test]
    fn attached_table_share_takes_the_board_number_locale() {
        let shares = crate::number_locale::with_number_locale(&crate::number_locale::fr_fr(), || {
            share_cells(&[40.0, 0.5])
        });
        assert_eq!(shares, ["40\u{202f}%", "1\u{202f}%"]);
    }

    /// Same law as `bar.rs`'s canvas-rect test — see
    /// `pie.rs::Resolved::background`.
    #[test]
    fn canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["background"] = json!("#d9a45b");
        p["background"] = json!("#ffffff");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("<rect width=\"600\" height=\"432\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// With no color channel every wedge paints one constant `palette[0]`. Palette-by-index is the
    /// color scale's range — it only exists when a color channel does
    /// (`color_key` is the channel's non-empty `data_field`).
    #[test]
    fn a_pie_without_a_color_channel_paints_every_arc_the_first_palette_stop() {
        let mut p = make_pie_payload(Value::Null);
        let fills = |payload: &Value| -> Vec<String> {
            let svg = render(&make_payload(payload.clone()), &test_fonts()).expect("renders");
            svg.match_indices("<path d=")
                .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string())
                .collect()
        };
        assert_eq!(fills(&p), ["#3164a3", "#3164a3", "#3164a3"], "no color channel → one constant fill");

        // A color channel restores the scale: the range cycles by slice index.
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        assert_eq!(fills(&p), ["#3164a3", "#779bc9", "#ad9c7f"], "color channel → palette by index");

        // A channel whose data_field is empty is production's `color_key = ""`
        // — the same no-encoding branch, not a color scale.
        p["resolved"]["resolved_channels"]["color"]["data_field"] = json!("");
        assert_eq!(fills(&p), ["#3164a3", "#3164a3", "#3164a3"], "empty data_field is no color key");
    }

    /// Front-end contract: `tooltip_format` present wraps every arc in a
    /// `.role-mark` group and stamps its structured aria-label
    /// (`charts/sector-charts_0/revenue_share_hero`'s Electronics slice,
    /// see `contract.rs`'s pie tests for the formula). A pie with NO
    /// `tooltip_format` (an older/synthetic fixture) keeps today's
    /// unwrapped, unlabeled arcs -- the same `tooltip_labels` gate bar uses.
    #[test]
    fn tooltip_format_wraps_every_arc_with_a_structured_aria_label() {
        let legend = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = legend.clone();
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(
            svg.contains("<g class=\"mark-arc role-mark layer_0_marks\""),
            "every arc wraps in a role-mark group"
        );
        assert!(
            svg.contains(
                "aria-label=\"⁤Electronics; %: 45%; \u{2060}Revenue: 197,199; \u{2060}⁣Σ: 443,023\""
            ),
            "{svg}"
        );

        // No tooltip_format: today's unwrapped, unlabeled status quo.
        let mut without_format = make_pie_payload(Value::Null);
        without_format["resolved"]["legend"] = legend;
        let plain = render(&make_payload(without_format), &test_fonts()).expect("renders");
        assert!(!plain.contains("role-mark"));
        assert!(!plain.contains("aria-label="));
    }

    #[test]
    fn a_field_label_titles_the_tooltip_value_row() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let plain = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(plain.contains("\u{2060}Revenue: 197,199"), "{plain}");
        p["resolved"]["field_labels"] = json!({"revenue": "Sales volume"});
        let labeled = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(labeled.contains("\u{2060}Sales volume: 197,199"), "{labeled}");
    }

    #[test]
    fn a_field_label_titles_the_legend() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        let plain = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(plain.contains(">Category<"), "{plain}");
        p["resolved"]["field_labels"] = json!({"category": "Department"});
        let labeled = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(labeled.contains(">Department<") && !labeled.contains(">Category<"), "{labeled}");
    }

    /// `chart_interactivity.js`'s `RECEDE_ROLES` matches `arc`
    /// literally: only an arc stamped so recedes on hover. The center total's
    /// text carries a description too but a different role, so it never
    /// enters the recede set.
    #[test]
    fn every_arc_carries_the_role_the_runtime_recedes() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let arcs = svg.matches("<g class=\"mark-arc role-mark").count();
        assert!(arcs > 1, "{svg}");
        assert_eq!(svg.matches("aria-roledescription=\"arc mark\"").count(), arcs, "{svg}");
        assert!(!svg.contains("aria-roledescription=\"bar\""), "{svg}");
    }

    /// No `total` config (no center-total display) still carries the
    /// identical Total tooltip row -- `charts/sector-charts_1/revenue_
    /// share_minimal`'s own shape, proving the row is unconditional.
    #[test]
    fn tooltip_total_row_is_unconditional_on_the_center_total_display() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("\u{2060}⁣Σ: 443,023"));
    }

    /// The board plans `category_colors` once per board (#7167): a value's
    /// slot wins over this chart's own row-position index. "Electronics"
    /// draws first (row 0, would take palette[0] positionally) but owns
    /// board slot 2; the other two values take slots 0 and 1.
    #[test]
    fn board_slot_wins_the_wedge_fill_over_row_position() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let fills: Vec<&str> = svg
            .match_indices("<path d=")
            .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap())
            .collect();
        assert_eq!(
            fills,
            ["#ad9c7f", "#3164a3", "#779bc9"],
            "Electronics/Accessories/Tools take slots 2/0/1: {svg}"
        );
    }

    /// The board slot governs the slice-label INK too, not just the wedge
    /// fill — `ink_at(scale, v, dark_companion_stops)` is a pure function of
    /// the row's own value, same as the wedge's `color_at`. Electronics'
    /// wedge is slot 2's swatch; its label must be slot 2's companion, not
    /// row 0's positional one.
    #[test]
    fn board_slot_wins_the_slice_label_ink_over_row_position() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["dark_companion_stops"] = json!(["#dark0", "#dark1", "#dark2"]);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": null, "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let label_fill = |name: &str| -> Option<String> {
            let tag = svg.split("<text").find(|t| t.contains(name)).expect(name).split('>').next().unwrap();
            tag.split("fill=\"").nth(1).map(|f| f.split('"').next().unwrap().to_string())
        };
        assert_eq!(label_fill("Electronics"), Some("#dark2".into()), "{svg}");
        assert_eq!(label_fill("Accessories"), Some("#dark0".into()), "{svg}");
        assert_eq!(label_fill("Tools"), Some("#dark1".into()), "{svg}");
    }

    /// A slice pinned to a color outside the palette is labeled in that
    /// color's own ink, not the companion of the slot it does not paint.
    #[test]
    fn a_pinned_slice_label_takes_its_pinned_colors_ink() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["dark_companion_stops"] = json!(["#dark0", "#dark1", "#dark2"]);
        p["resolved"]["pin_companions"] = json!([["#999999", "#pinink"]]);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": null, "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {"Electronics": "#999999"}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let label_fill = |name: &str| -> Option<String> {
            let tag = svg.split("<text").find(|t| t.contains(name)).expect(name).split('>').next().unwrap();
            tag.split("fill=\"").nth(1).map(|f| f.split('"').next().unwrap().to_string())
        };
        assert_eq!(label_fill("Electronics"), Some("#pinink".into()), "{svg}");
        assert_eq!(label_fill("Accessories"), Some("#dark0".into()), "{svg}");
    }

    /// The deleted conditional-fill implementation covered two paint sites
    /// off one shared channel — the slice fill and the label ink — so one
    /// guard fixture with labels visible proves both are unreachable: had
    /// the guard only fired for one site, this payload would still render
    /// (or panic deeper in the deleted label-ink match) instead of
    /// refusing at the entry gate.
    #[test]
    fn a_conditional_color_channel_is_refused_for_both_slice_fill_and_label_ink() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": "#123456", "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] = json!({"color": {
            "channel": "color", "mode": "conditional", "data_field": "revenue",
            "rules": [
                {"gte": 150000, "background": "#f59e0b"},
                {"lt": 150000, "background": "#e5e7eb"}
            ]
        }});
        let err = render(&make_payload(p), &test_fonts()).expect_err("refuses");
        assert!(err.contains("conditional color channel not supported"), "{err}");
    }

    fn empty_fonts() -> FontBook {
        FontBook::empty()
    }

    // Real font family present in InterVariable.ttf — needed for tests that
    // exercise the label-margin fitting pass, which measures real text.
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn make_pie_payload_with_labels(template: &str) -> Value {
        let mut payload_json = make_pie_payload(Value::Null);
        payload_json["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        payload_json["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "offset": 8.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "color": "#333333", "size": 14.0, "weight": "500"},
            "template": template
        });
        payload_json
    }

    /// A color channel inks each label with its wedge's dark companion; an
    /// authored `font.color` applies only to a pie with no color channel.
    #[test]
    fn a_color_channel_label_ignores_an_authored_font_color() {
        let label_fills = |p: &Value| -> Vec<String> {
            let svg = render(&make_payload(p.clone()), &test_fonts()).unwrap();
            svg.split("<text ")
                .skip(1)
                .filter(|t| t.contains("<tspan"))
                .map(|t| t.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string())
                .collect()
        };
        let mut p = make_pie_payload_with_labels("{{ category }}");
        assert_eq!(label_fills(&p), ["#333333", "#333333", "#333333"], "no channel: authored");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        assert_eq!(label_fills(&p), ["#183253", "#3c4d65", "#183253"], "channel: dark companions");
    }

    #[test]
    fn category_only_template_renders_name_without_percent_or_value() {
        let payload = make_payload(make_pie_payload_with_labels("{{ category }}"));
        let svg = render(&payload, &test_fonts()).expect("category-only template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(!svg.contains("197,199"), "no value line for category-only template:\n{svg}");
        assert!(!svg.contains('%'), "no percent line for category-only template:\n{svg}");
    }

    #[test]
    fn a_template_naming_an_absent_column_errors() {
        let payload = make_payload(make_pie_payload_with_labels("{{ segment }}"));
        let err = render(&payload, &test_fonts()).expect_err("absent column rejected");
        assert!(err.contains("variable 'segment' is undefined"), "{err}");
    }

    #[test]
    fn percent_category_template_renders_percent_and_name_only() {
        let payload = make_payload(make_pie_payload_with_labels(
            "{{ '{:.0%}'.format(percent) }} {{ category }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("percent+category template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(!svg.contains("197,199"), "no value line:\n{svg}");
    }

    #[test]
    fn category_value_line_template_renders_currency_value_line() {
        let payload = make_payload(make_pie_payload_with_labels(
            "{{ category }}\n{{ '${:,.0f}'.format(revenue) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("category+currency template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(svg.contains("$197,199"), "currency-formatted value present:\n{svg}");
    }

    #[test]
    fn percent_only_template_renders_share_without_name() {
        let payload = make_payload(make_pie_payload_with_labels("{{ '{:.0%}'.format(percent) }}"));
        let svg = render(&payload, &test_fonts()).expect("percent-only template renders");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(!svg.contains("Electronics"), "no category name:\n{svg}");
    }

    fn make_pie_payload_with_segment_labels(template: &str) -> Value {
        let mut payload_json = make_pie_payload_with_labels(template);
        payload_json["resolved"]["theta"] = json!("value");
        payload_json["resolved"]["identity_field"] = json!("segment");
        payload_json["rows"] = json!([
            {"segment": "Enterprise", "value": 12100.0},
            {"segment": "Mid-Market", "value": 7800.0},
            {"segment": "SMB", "value": 4900.0}
        ]);
        payload_json
    }

    #[test]
    fn segment_colon_value_template_renders_name_and_thousands_value() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ segment }}: {{ '{:,}'.format(value) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("segment-colon-value template renders");
        assert!(svg.contains("Enterprise: 12,100"), "single-line 'name: value':\n{svg}");
    }

    #[test]
    fn segment_newline_value_template_renders_two_lines() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ segment }}\n{{ '{:,}'.format(value) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("segment-newline-value template renders");
        assert!(svg.contains("Enterprise"), "name line present:\n{svg}");
        assert!(svg.contains("12,100"), "thousands-formatted value line present:\n{svg}");
    }

    #[test]
    fn jinja_filter_percent_color_value_template_renders_percent_and_scaled_k_value() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ percent | format('.0%') }} {{ color }}\n${{ (value / 1000) | format('.0f') }}k",
        ));
        let svg = render(&payload, &test_fonts()).expect("jinja-filter template renders");
        assert!(svg.contains("Enterprise"), "color (identity) name present:\n{svg}");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(svg.contains("$12k"), "value scaled to thousands with k suffix:\n{svg}");
    }

    #[test]
    fn template_with_yaml_block_scalar_trailing_newline_still_matches() {
        // A YAML `template: |` block scalar keeps one trailing "\n" that
        // Jinja2's default keep_trailing_newline=False then strips before
        // parsing (labels_env.py doesn't override it) — the resolved
        // payload's `template` field carries that same trailing "\n", and
        // this renderer must strip it the same way before matching.
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ percent | format('.0%') }} {{ color }}\n${{ (value / 1000) | format('.0f') }}k\n",
        ));
        let svg = render(&payload, &test_fonts()).expect("trailing-newline template still renders");
        assert!(svg.contains("$12k"), "value scaled to thousands with k suffix:\n{svg}");
    }

    #[test]
    fn total_visible_renders_summed_value_and_caption_at_center() {
        let payload_json = make_pie_payload(json!({
            "visible": true,
            "label": "Total Revenue"
        }));
        let mut payload_json = payload_json;
        // The format lives only on the resolved style: `ChartTotal` has no
        // `format` key.
        payload_json["resolved"]["style"]["total_style"]["value"]["format"] = json!("$,.0f");
        payload_json["resolved"]["style"]["total_style"]["value"]["font"]["family"] =
            json!(TEST_FONT);
        payload_json["resolved"]["style"]["total_style"]["label"]["font"]["family"] =
            json!(TEST_FONT);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with total overlay");

        // 197199 + 152504 + 93320 = 443023, formatted "$,.0f" → "$443,023".
        assert!(
            svg.contains("$443,023"),
            "expected formatted summed total in output:\n{svg}"
        );
        assert!(
            svg.contains("Total Revenue"),
            "expected total label caption in output:\n{svg}"
        );
    }

    /// A native total format paints its own unit, as `format_d3` does.
    #[test]
    fn a_native_total_format_paints_its_unit() {
        for (name, want) in [("duration", ">5d 3h<"), ("bytes", ">443 kB<")] {
            let mut payload_json = make_pie_payload(json!({"visible": true}));
            payload_json["resolved"]["style"]["total_style"]["value"]["format"] = json!(name);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect(name);
            assert!(svg.contains(want), "{name}:\n{svg}");
        }
    }

    /// Oracle: `format_d3(443023, ".3~s", "CHF ", "", None, "after_prefix")`.
    #[test]
    fn a_total_value_format_paints_its_affix_around_the_digits() {
        let mut payload_json = make_pie_payload(json!({"visible": true, "label": "Total Revenue"}));
        let value = &mut payload_json["resolved"]["style"]["total_style"]["value"];
        value["format"] = json!({"spec": ".3~s", "prefix": "CHF ", "raw": "number"});
        value["font"]["family"] = json!(TEST_FONT);
        payload_json["resolved"]["style"]["total_style"]["label"]["font"]["family"] = json!(TEST_FONT);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">CHF 443k<"), "{svg}");
    }

    /// A donut's center total wider than the hole it sits in is
    /// WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS, measured on the hole the wheel
    /// actually draws (`2 × inner`), not a slot estimate.
    #[test]
    fn a_total_wider_than_the_hole_warns_and_one_that_fits_does_not() {
        let donut = |size: f64| {
            let mut json = make_pie_payload(json!({"visible": true}));
            let font = &mut json["resolved"]["style"]["total_style"]["value"]["font"];
            font["family"] = json!(TEST_FONT);
            font["size"] = json!(size);
            make_payload(json)
        };
        let (svg, warnings) = crate::diagnostic::collect(|| render(&donut(96.0), &test_fonts()));
        svg.expect("renders");
        assert_eq!(warnings.len(), 1, "{warnings:?}");
        let w = &warnings[0];
        assert_eq!(w.code, crate::codes::WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.id);
        assert_eq!(w.params["chart_id"], json!("pie"));
        assert_eq!(w.params["formatted_value"], json!("443023"));
        let text = w.params["text_width"].as_f64().unwrap();
        let hole = w.params["hole_diameter"].as_f64().unwrap();
        assert!(text > hole, "{text} > {hole}");
        let (slot_w, slot_h) =
            (w.params["slot_width"].as_f64().unwrap(), w.params["slot_height"].as_f64().unwrap());
        assert!((hole - slot_w.min(slot_h) * 0.9 * 0.6).abs() < 1e-9, "{hole} {slot_w} {slot_h}");

        let (svg, warnings) = crate::diagnostic::collect(|| render(&donut(18.0), &test_fonts()));
        svg.expect("renders");
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn total_null_renders_no_center_text() {
        let payload_json = make_pie_payload(Value::Null);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &empty_fonts()).expect("renders without total");
        assert!(
            !svg.contains("text-anchor=\"middle\""),
            "no center text layer expected when total is null:\n{svg}"
        );
    }

    #[test]
    fn the_retired_total_format_key_refuses_by_name() {
        let payload = make_payload(make_pie_payload(json!({
            "visible": true,
            "label": "Total Revenue",
            "format": "$,.0f"
        })));
        let err = render(&payload, &empty_fonts()).unwrap_err();
        assert!(err.contains("total.format"), "{err}");
    }

    #[test]
    fn total_invisible_renders_no_center_text() {
        let payload_json = make_pie_payload(json!({
            "visible": false,
            "label": "Total Revenue"
        }));
        let payload = make_payload(payload_json);
        let svg = render(&payload, &empty_fonts()).expect("renders without total");
        assert!(
            !svg.contains("text-anchor=\"middle\""),
            "no center text layer expected when total.visible is false:\n{svg}"
        );
    }

    // ── Attached table ──────────────────────────────────────────────────────

    fn attached_table_json() -> Value {
        let mut theme = crate::families::table::tests::base_table_theme();
        theme["font"] = json!({"family": "Inter", "color": "#222222", "size": 11.0, "weight": "400"});
        theme["row"]["height"] = json!(24.0);
        theme["row"]["stripe"] = json!({"color": "#F7F8FA"});
        theme["header"]["visible"] = json!(false);
        theme["outer_padding"] = json!(0.0);
        theme["pagination"] = json!({"enabled": false});
        json!({
            "link_open": "page",
            "columns": {
                "swatch": {"width": 24, "swatch": true, "link_open": "page", "header_link_open": "page"},
                "share": {"width": 44, "align": "right", "link_open": "page", "header_link_open": "page"},
                "name": {"width": 83, "link_open": "page", "header_link_open": "page"},
                "value": {"width": 61, "align": "right", "link_open": "page", "header_link_open": "page"}
            },
            "title_style": crate::families::table::tests::resolved_title_style(),
            "style": {
                "table": theme,
                "title_font": crate::families::table::tests::resolved_title_font()
            }
        })
    }

    #[test]
    fn an_attached_table_placement_or_align_production_does_not_index_refuses_by_name() {
        for (placement, align) in [("middle", "start"), ("bottom", "stretch"), ("none", "center")] {
            let mut p = make_pie_payload_with_attached_table("bottom", "");
            p["resolved"]["attached_table_placement"] = json!(placement);
            p["resolved"]["attached_table_align"] = json!(align);
            let payload: Payload = serde_json::from_value(p).unwrap();
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                format!("pie: attached_table placement '{placement}' align '{align}' is not supported")
            );
        }
    }

    fn make_pie_payload_with_attached_table(placement: &str, heading: &str) -> Value {
        let mut payload_json = make_pie_payload(Value::Null);
        payload_json["resolved"]["attached_table"] = attached_table_json();
        payload_json["resolved"]["attached_table_placement"] = json!(placement);
        payload_json["resolved"]["attached_table_align"] =
            json!(if placement == "bottom" { "center" } else { "start" });
        payload_json["resolved"]["attached_table_width"] = json!(212.0);
        payload_json["resolved"]["attached_table_gap"] = json!(12.0);
        payload_json["resolved"]["hybrid_heading_gap"] = json!(6.0);
        payload_json["resolved"]["wheel_width"] = json!(300.0);
        payload_json["resolved"]["attached_heading"] = json!(heading);
        payload_json["resolved"]["attached_heading_font"] = json!({
            "family": "Inter", "color": "#222222", "size": 11.0, "weight": "600", "line_height": 1.25
        });
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 2]);
        // The wheel renders at the theme's view height (the sweep envelope's
        // board_style.continuous_height), never at the slot height.
        payload_json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},"continuous_height": 300.0, "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()});
        payload_json
    }

    /// The attached-table wheel child's own `draw_pie_fit` call (see
    /// `Resolved::background`) reads `r.background`, independently of the
    /// standalone-wheel call site — the composed rect is `wheel_width ×
    /// continuous_height` (300×300 for this fixture), not the card size.
    #[test]
    fn attached_table_wheel_child_reads_the_chart_own_background_not_the_board_background() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["background"] = json!("#d9a45b");
        payload_json["background"] = json!("#ffffff");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("<rect width=\"300\" height=\"300\" fill=\"#d9a45b\"/>"),
            "wheel child paints the chart's own background: {svg}"
        );
    }

    #[test]
    fn attached_table_below_composes_at_the_view_height_and_grows_the_canvas() {
        let payload_json = make_pie_payload_with_attached_table("bottom", "");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with attached table below");

        // Table content: swatch chip, name text, share/value cells for each row.
        assert!(svg.contains("Electronics"), "row name present:\n{svg}");
        assert!(svg.contains("rx=\"3\" fill=\"#3164a3\""), "swatch chip present:\n{svg}");
        assert!(svg.contains('%'), "share percent cell present:\n{svg}");
        // compose_attached_table_svg: the outer canvas takes its OWN height
        // from the children — donut_h (the view height 300) + gap 12 +
        // table_h (3 rows × 24 = 72) = 384 — never the 432 slot.
        assert!(svg.contains("width=\"600\" height=\"384\""), "composed canvas size:\n{svg}");

        // Both group transforms pin the composition math directly.
        assert!(svg.contains("translate(150.00, 0)"), "wheel group centered, y=0:\n{svg}");
        assert!(svg.contains("translate(194.00, 312.00)"), "table group below the wheel (donut_h + gap):\n{svg}");
    }

    /// The table is the pie's legend: each row's swatch must be the exact
    /// fill its own wedge paints (`project_pie_table_rows`'s doc comment).
    /// A board slot reverses "Electronics" (row 0) off palette[0] onto
    /// palette[2], and the swatch follows it, not the row position.
    #[test]
    fn attached_table_swatch_follows_the_board_slot_not_row_position() {
        let mut p = make_pie_payload_with_attached_table("bottom", "");
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        // Swatches in row order: Electronics (slot 2), Accessories (slot 0),
        // Tools (slot 1) — palette-by-ROW-POSITION would give 0/1/2 instead.
        let swatches: Vec<&str> = svg
            .match_indices("rx=\"3\" fill=\"")
            .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap())
            .collect();
        assert_eq!(swatches, ["#ad9c7f", "#3164a3", "#779bc9"], "{svg}");
    }

    #[test]
    fn attached_table_right_places_table_beside_pie_at_disk_top() {
        let payload_json = make_pie_payload_with_attached_table("right", "Too small to label");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with attached table on the right");

        assert!(svg.contains("Too small to label"), "heading text present:\n{svg}");
        // Wheel group is untranslated (right placement never shifts the wheel).
        assert!(svg.contains("translate(0.00, 0)"), "wheel group at origin:\n{svg}");
        // Table starts right of the wheel (wheel_width + gap) at the disk's
        // top edge: the wheel box is 300×300, so the fitted disk radius
        // decides disk_top = (300 − 2r)/2; with no label/total ink the
        // radius is min(pw, ph)/2 × outer_fraction — table_y must sit above
        // the vertical middle and the canvas keeps the wheel's 300 height
        // (disk_top + table_h < 300 here).
        assert!(svg.contains("translate(312.00"), "table positioned right of the wheel:\n{svg}");
        assert!(svg.contains("height=\"300\""), "right placement keeps the wheel height:\n{svg}");
    }

    fn attached_with(placement: &str, align: &str) -> Value {
        let mut p = make_pie_payload_with_attached_table(placement, "");
        p["resolved"]["attached_table_align"] = json!(align);
        p
    }

    /// The `(x, y)` of the table's group in a composed canvas: the one
    /// `translate` that precedes the 212px-wide table child.
    fn table_origin(svg: &str) -> (f64, f64) {
        let table_child = ")\"><svg xmlns=\"http://www.w3.org/2000/svg\" width=\"212\"";
        let end = svg.find(table_child).unwrap_or_else(|| panic!("no table child:\n{svg}"));
        let start = svg[..end].rfind("translate(").expect("table translate") + "translate(".len();
        let (x, y) = svg[start..end].split_once(", ").expect("x, y");
        (x.parse().expect("table x"), y.parse().expect("table y"))
    }

    fn canvas_height(svg: &str) -> f64 {
        svg.split("height=\"").nth(1).and_then(|s| s.split('"').next()).unwrap().parse().unwrap()
    }

    /// A table above or below the wheel aligns across the card — `start` at the left
    /// padding, `center` on the card's middle, `end` against the right padding.
    /// Card 600, table 212, padding 16.
    #[test]
    fn attached_table_aligns_across_the_card_inside_its_padding() {
        for (align, x) in [("start", 16.0), ("center", 194.0), ("end", 600.0 - 212.0 - 16.0)] {
            let svg = render(&make_payload(attached_with("bottom", align)), &test_fonts()).expect("renders");
            // y = donut_h 300 + gap 12; canvas = 300 + 12 + 72.
            assert_eq!(table_origin(&svg), (x, 312.0), "bottom / {align}:\n{svg}");
            assert_eq!(canvas_height(&svg), 384.0, "bottom / {align}");
        }
    }

    /// A table beside the wheel aligns down the
    /// wheel's DISK, never above its top. This wheel is 300 tall with no label
    /// ink, so the disk is 2 × 0.9 × (300 − 32)/2 = 241.2 and disk_top 29.4;
    /// `center` is `disk_top + (241.2 − 72)/2` = 114 and `end`
    /// `disk_top + 241.2 − 72` = 198.6. A right table sits at `wheel_width +
    /// gap`; a left one at the left padding, with the wheel at `table_width +
    /// gap`.
    #[test]
    fn attached_table_aligns_down_the_disk_on_a_side() {
        for (placement, x, wheel_x) in
            [("right", 300.0 + 12.0, "translate(0.00, 0)"), ("left", 16.0, "translate(224.00, 0)")]
        {
            for (align, y) in [("start", 29.4), ("center", 114.0), ("end", 198.6)] {
                let svg = render(&make_payload(attached_with(placement, align)), &test_fonts())
                    .expect("renders");
                let (tx, ty) = table_origin(&svg);
                assert_eq!(tx, x, "{placement} / {align}:\n{svg}");
                assert!((ty - y).abs() < 1e-9, "{placement} / {align}: table y {ty}, want {y}");
                assert!(svg.contains(wheel_x), "{placement} / {align} wheel group:\n{svg}");
                assert_eq!(canvas_height(&svg), 300.0, "{placement} / {align}");
            }
        }
    }

    /// A left table pushes the
    /// wheel right by `table_width + gap` and the title back by the same, so it
    /// stays on the card's left edge. Only a left placement slides it.
    #[test]
    fn attached_table_left_slides_the_title_back_by_the_wheel_offset() {
        for (placement, slid) in [("left", true), ("right", false), ("top", false), ("bottom", false)] {
            let mut p = attached_with(placement, "start");
            p["resolved"]["title"] = json!("Revenue");
            p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
            p["resolved"]["title_style"] =
                json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "position": {"offset": 10.0}});
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            assert_eq!(svg.contains("<g transform=\"translate(-224,0)\">"), slid, "{placement}:\n{svg}");
        }
    }

    /// `_slide_title` prints the slide with `{dx:g}`: a fractional natural
    /// table width slides the title by six significant digits, not the full
    /// float. `compose_attached_table_svg(..., table_width=212.123456789,
    /// gap=12.0, placement="left")` in production emits
    /// `translate(-224.123,0)` for the title group.
    #[test]
    fn attached_table_left_slide_prints_python_g_for_a_fractional_table_width() {
        let mut p = attached_with("left", "start");
        p["resolved"]["attached_table_width"] = json!(212.123456789);
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] =
            json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "position": {"offset": 10.0}});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("<g transform=\"translate(-224.123,0)\">"), "{svg}");
    }

    /// vega_lite.py:605-628, untitled: the wheel's view gains the table's
    /// height plus the gap (72 + 12) and its plot is pushed down by it
    /// (`bump_padding_top`), so the 300 + 84 = 384 view holds a plot box
    /// 268 square from y = 16 + 84 (center (150, 234)); the table drops in at
    /// the top padding.
    #[test]
    fn attached_table_top_pushes_the_untitled_wheel_down_by_the_table() {
        let svg = render(&make_payload(attached_with("top", "start")), &test_fonts()).expect("renders");
        assert_eq!(table_origin(&svg), (16.0, 16.0), "{svg}");
        assert_eq!(canvas_height(&svg), 384.0);
        assert!(svg.contains("<rect width=\"300\" height=\"384\""), "wheel child is the pushed view:\n{svg}");
        assert!(svg.contains("translate(150,234)"), "plot pushed below the table:\n{svg}");
    }

    /// vega_lite.py:605-628 + title_band.py `vega_title_block_height`, titled:
    /// the table lands below the title block — padding 16 + title 18 + one
    /// subtitle line 12 + 3 + `title.offset` 10 — while the wheel's title
    /// offset grows by `round(84)` (`shift_chart_title`, title_dy 0) so the
    /// plot clears the table.
    #[test]
    fn attached_table_top_sits_below_the_title_block() {
        let mut p = attached_with("top", "center");
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["subtitle"] = json!("by category");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] = json!({
            "font": {"size": 18.0, "family": "Source Serif 4"},
            "position": {"offset": 10.0},
            "subtitle": {"font": {"size": 12.0, "family": "Inter"}}
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(table_origin(&svg), (194.0, 16.0 + 18.0 + 15.0 + 10.0), "{svg}");
        assert_eq!(canvas_height(&svg), 384.0);
    }

    #[test]
    fn attached_table_top_under_a_title_without_an_offset_refuses() {
        let mut p = attached_with("top", "center");
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] = json!({"font": {"size": 18.0, "family": "Source Serif 4"}});
        let err = render(&make_payload(p), &test_fonts()).unwrap_err();
        assert!(err.contains("title_style.position.offset"), "{err}");
    }


    /// An attached-table pie's wheel is its own pane at `width=chart.wheel_width`,
    /// so `apply_title_overflow_to_spec`'s limit is `compute_title_limit(wheel_width,
    /// padding)`, not the card's — a title that fits the card but overruns the
    /// narrower wheel pane must still clip there, while the same title on a
    /// plain pie of the same card width is untouched.
    #[test]
    fn attached_table_bounds_the_title_to_the_wheel_pane_not_the_card() {
        let fonts = test_fonts();
        // wheel pane basis 300 - 2*16 = 268; card basis 600 - 2*16 = 568.
        // Build a title whose Inter-measured width (wrap_title_block's fixed
        // measure family) lands between the two: over the pane, under the card.
        let mut text = String::new();
        loop {
            text.push('M');
            if fonts.measure(&text, "Inter", 18.0).unwrap() > 400.0 {
                break;
            }
        }
        let title_style = json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "overflow": "clip"});

        let mut attached_json = make_pie_payload_with_attached_table("right", "");
        attached_json["resolved"]["title"] = json!(text);
        attached_json["resolved"]["title_style"] = title_style.clone();
        let attached_svg =
            render(&make_payload(attached_json), &fonts).expect("attached-table pie renders");
        let attached_title = format!(">{text}<");
        assert!(
            !attached_svg.contains(&attached_title),
            "wheel-pane title must clip below the card width:\n{attached_svg}"
        );

        let mut plain_json = make_pie_payload(Value::Null);
        plain_json["resolved"]["title"] = json!(text);
        plain_json["resolved"]["title_style"] = title_style;
        let plain_svg = render(&make_payload(plain_json), &fonts).expect("plain pie renders");
        assert!(
            plain_svg.contains(&attached_title),
            "same title on a same-width plain pie must draw whole:\n{plain_svg}"
        );
    }

    #[test]
    fn attached_table_small_slot_still_composes_at_view_height() {
        // The slot height plays no part in the composition: production's
        // compose grows the canvas past a short slot rather than shrinking
        // the wheel (the old slot-fit arm errored here).
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["height"] = json!(50.0);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("short slot still renders");
        assert!(svg.contains("width=\"600\" height=\"384\""), "canvas grows past the slot:\n{svg}");
    }

    #[test]
    fn attached_table_missing_continuous_height_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},"format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()});
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("no view height to render the wheel at");
        assert!(err.contains("continuous_height"), "{err}");
    }

    #[test]
    fn attached_table_row_subset_uses_data_idx_not_row_idx() {
        // attached_row_indices = [1, 2] skips row 0 (Electronics) — the first
        // RENDERED row must be data row 1 ("Accessories", palette slot 1),
        // not row 0. A bug that used the loop's row_idx as the palette/data
        // lookup key instead of the row_indices value would put Electronics
        // (palette[0]) first instead.
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([1, 2]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with a row subset");

        assert!(!svg.contains("Electronics"), "skipped row must not render:\n{svg}");
        assert!(svg.contains("Accessories"), "first rendered row is data row 1:\n{svg}");
        assert!(svg.contains("Tools"), "second rendered row is data row 2:\n{svg}");

        let accessories_swatch = svg.find("rx=\"3\" fill=\"#779bc9\"").expect("row 1's palette[1] swatch present");
        let tools_swatch = svg.find("rx=\"3\" fill=\"#ad9c7f\"").expect("row 2's palette[2] swatch present");
        assert!(
            accessories_swatch < tools_swatch,
            "Accessories (data_idx=1) renders as the first row, before Tools (data_idx=2)"
        );
    }

    #[test]
    fn attached_table_row_indices_empty_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([]);
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("empty row_indices is rejected");
        assert!(err.contains("attached_row_indices"), "{err}");
    }

    #[test]
    fn attached_table_row_indices_out_of_range_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 99]);
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("out-of-range index is rejected, not a panic");
        assert!(err.contains("out of range"), "{err}");
    }

    #[test]
    fn attached_table_right_taller_than_wheel_grows_the_canvas() {
        // A tall table on the right grows the composed canvas past the
        // wheel's own height: outer_h = max(donut_h, disk_top + table_h) —
        // compose_attached_table_svg's right arm, the s15_donut shape.
        let mut payload_json = make_pie_payload_with_attached_table("right", "");
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("tall right table renders");
        // 15 rows × 24 = 360 table_h; disk_top > 0 ⇒ outer_h > 360 > 300.
        let h: f64 = svg
            .split("height=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("outer height parses");
        assert!(h > 360.0, "canvas grows to disk_top + table_h: {h}\n{svg}");
    }








    /// Fit margins with no title and no labels are exactly the padding: the
    /// wheel centers the padded box and the radius is
    /// `min(pw, ph)/2 × outer_fraction` of it (568×400 → r 180, center
    /// (300, 216)). The legacy model agrees on this unconstrained shape —
    /// this pins the shared geometry; the model switch is discriminated by
    /// `fit_label_ink_pushes_margins_asymmetrically` below.
    #[test]
    fn fit_wheel_centers_padded_box_with_min_dim_radius() {
        let payload = make_payload(make_pie_payload(Value::Null));
        let svg = render(&payload, &test_fonts()).expect("pie renders");
        assert!(svg.contains("translate(300,216)"), "center at padded-box middle: {svg}");
        assert!(svg.contains("A180,"), "outer radius 0.9×min(568,400)/2 = 180: {svg}");
    }

    /// A visible slice label pushes the PLOT MARGIN on its side (asymmetric,
    /// like production's donut_example origin (33, 44)) — the radius shrinks
    /// only through the fitted box, never through a per-label radius min().
    #[test]
    fn fit_title_overflow_truncates_and_wraps_per_mode() {
        // The overflow wiring end to end: a truncate-mode title yields ONE
        // ellipsized title row; a wrap-two subtitle yields TWO rows, the
        // second advanced by sub_fs + 2 (Vega's default text lineHeight).
        let mut json = make_pie_payload(Value::Null);
        json["width"] = json!(220.0);
        json["height"] = json!(300.0);
        json["resolved"]["title"] =
            json!("A title far too long for a two hundred twenty pixel card to hold");
        json["resolved"]["subtitle"] =
            json!("a subtitle that also overflows the card width and wraps onto a second line");
        json["resolved"]["title_style"] = json!({
            // Source Serif paint stack: the wrap must still MEASURE with
            // Inter (WRAP_MEASURE_FAMILY) — reverting to the paint family
            // changes where the ellipsis lands and fails the exact pin.
            "font": {"family": "'Source Serif 4', Georgia, serif", "size": 14.0, "weight": "500"},
            "overflow": "truncate",
            "position": {"offset": 10.0},
            "subtitle": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "400"},
                "overflow": "wrap-two"
            }
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("overflowing titles render");
        let title_rows =
            svg.matches("font-size=\"14\"").count();
        assert_eq!(title_rows, 1, "truncate mode keeps one title row:\n{svg}");
        assert!(svg.contains('\u{2026}'), "the cut title carries the ellipsis:\n{svg}");
        // Exact pin: the cut lands where the INTER-measured wrap puts it —
        // measuring with the serif paint stack instead moves the ellipsis.
        assert!(
            svg.contains(">A title far too long for a tw\u{2026}<"),
            "wrap measured with Inter, not the paint family:\n{svg}"
        );
        // Subtitle rows: two 11px text rows, 13px apart.
        let mut ys: Vec<f64> = Vec::new();
        for (pos, _) in svg.match_indices("font-size=\"11\"") {
            let head = &svg[..pos];
            if let Some(i) = head.rfind(" y=\"") {
                if let Ok(v) = head[i + 4..].split('\"').next().unwrap_or("").parse::<f64>() {
                    ys.push(v);
                }
            }
        }
        assert_eq!(ys.len(), 2, "wrap-two subtitle renders two rows:\n{svg}");
        assert!((ys[1] - ys[0] - 13.0).abs() < 1e-6, "second row advances by sub_fs + 2: {ys:?}");
    }

    /// An authored `font.style` reaches the center total's value and label
    /// and every slice label as `font-style`, as Vega paints VL's `fontStyle`.
    #[test]
    fn an_authored_font_style_reaches_the_total_and_slice_labels() {
        let mut json = make_pie_payload(json!({"visible": true, "label": "Total"}));
        json["resolved"]["style"]["total_style"]["value"]["font"]["style"] = json!("italic");
        json["resolved"]["style"]["total_style"]["label"]["font"]["style"] = json!("oblique");
        json["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        json["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 12.0, "weight": "500", "style": "italic"},
            "template": "{{ category }}"
        });
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let texts: Vec<&str> = svg.split("<text ").skip(1).collect();
        let styled = |needle: &str| texts.iter().filter(|t| t.contains(needle)).count();
        assert_eq!(styled("font-style=\"oblique\""), 1, "total label:\n{svg}");
        assert_eq!(styled("font-style=\"italic\""), 4, "total value + 3 slice labels:\n{svg}");

        let plain = render(&make_payload(make_pie_payload(json!({"visible": true}))), &test_fonts()).unwrap();
        assert!(!plain.contains("font-style"), "no authored style, no attribute:\n{plain}");
    }

    /// An authored slice opacity paints on every wedge.
    #[test]
    fn an_authored_slice_opacity_paints_on_every_wedge() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["style"]["slice_mark"]["opacity"] = json!(0.55);
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let wedges: Vec<&str> = svg.split("<path ").skip(1).collect();
        assert_eq!(wedges.len(), 3, "{svg}");
        assert!(wedges.iter().all(|w| w.contains("opacity=\"0.55\"")), "{svg}");
    }

    /// A placeholder fades the whole drawing and lays the overlay over the
    /// canvas in the board's font, as `render_vega_spec` does.
    #[test]
    fn a_placeholder_fades_the_wheel_and_paints_the_overlay() {
        let mut json = make_pie_payload(Value::Null);
        json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},
            "format_locale": crate::number_locale::en_us_wire(),
            "time_locale": crate::time_locale::en_us_wire(),
            "placeholder": {
                "opacity": 0.4,
                "overlay": {
                    "text": "add data", "background": "rgba(255, 255, 255, 0.5)",
                    "font": {"size": 16.0, "weight": 500.0, "color": "#666666", "family": "Inter"}
                }
            }
        });
        let plain = render(&make_payload(json.clone()), &test_fonts()).unwrap();
        json["is_placeholder"] = json!(true);
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let (open, rest) = plain.split_at(plain.find('>').unwrap() + 1);
        let content = rest.strip_suffix("</svg>").unwrap();
        assert_eq!(
            svg,
            format!(
                "{open}<g opacity=\"0.4\">{content}</g><g class=\"placeholder-overlay\">\
                 <rect class=\"dbt-pointer-inert\" x=\"0\" y=\"0\" width=\"600.0\" height=\"432.0\" \
                 fill=\"rgba(255, 255, 255, 0.5)\"/><text class=\"dbt-pointer-inert\" x=\"300.0\" \
                 y=\"216.0\" text-anchor=\"middle\" dominant-baseline=\"middle\" font-family=\"Inter\" \
                 font-size=\"16.0\" font-weight=\"500.0\" fill=\"#666666\">add data</text></g></svg>"
            )
        );
    }

    /// A pre-pass cut reports `WARN-CHART-TITLE-TRUNCATED` per field, with the
    /// text as cased; a row that fits reports nothing.
    #[test]
    fn a_cut_title_or_subtitle_warns_title_truncated() {
        let mut json = make_pie_payload(Value::Null);
        json["width"] = json!(220.0);
        json["resolved"]["title"] = json!("a title far too long for a narrow card to hold");
        json["resolved"]["subtitle"] = json!("short");
        json["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500", "case": "upper"},
            "overflow": "truncate",
            "position": {"offset": 10.0},
            "subtitle": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "400"},
                "overflow": "truncate"
            }
        });
        let cut = |field: &str, text: &str| {
            crate::diagnostic::Diagnostic::new(
                &crate::codes::WARN_CHART_TITLE_TRUNCATED,
                [
                    ("authored_field", json!(field)),
                    ("authored_text", json!(text)),
                    ("chart_id", json!("pie")),
                ],
            )
        };
        let payload = make_payload(json.clone());
        let (svg, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        svg.expect("renders");
        assert_eq!(warnings, vec![cut("title", "A TITLE FAR TOO LONG FOR A NARROW CARD TO HOLD")]);

        let subtitle = "a subtitle far too long for a narrow card to hold as well";
        json["resolved"]["title"] = json!("Short");
        json["resolved"]["subtitle"] = json!(subtitle);
        let payload = make_payload(json);
        let (_, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert_eq!(warnings, vec![cut("subtitle", subtitle)]);

        let mut wide = make_pie_payload(Value::Null);
        wide["resolved"]["title"] = json!("Fits");
        let payload = make_payload(wide);
        let (_, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn fit_label_ink_pushes_margins_asymmetrically() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["slice_label_indices"] = serde_json::json!([0]);
        json["rows"][0]["category"] =
            serde_json::json!("Electronics and Consumer Appliances Division");
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("labeled pie renders");
        // Slice 0 (48% share) has its mid angle on the right — its label ink
        // moves the center left of the unlabeled 300.
        let cx: f64 = svg
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        assert!(cx < 300.0, "right-side label ink pushes the wheel left: cx={cx}: {svg}");
        assert!(svg.contains("text-anchor=\"start\""), "right-half label left-aligns: {svg}");
        assert!(!svg.contains("A180,"), "radius follows the fitted box: {svg}");
    }

    /// Vega writes a text mark's anchor as `translate(x,y)` with the number's
    /// own JavaScript string (`645.366207199934,100.61781672160188`), never
    /// rounded: the anchor is a measured position, and a two-decimal copy of
    /// it shifts every glyph's anti-aliasing.
    #[test]
    fn a_slice_label_anchor_prints_at_full_precision() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["slice_label_indices"] = serde_json::json!([0]);
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("labeled pie renders");
        let text = &svg[svg.find("<text x=\"").expect("a label text") + 9..];
        let x = text.split('"').next().unwrap();
        let y = text.split(" y=\"").nth(1).unwrap().split('"').next().unwrap();
        for (axis, printed) in [("x", x), ("y", y)] {
            let value: f64 = printed.parse().unwrap();
            assert_eq!(printed, value.to_string(), "{axis} is the double's own string: {svg}");
            assert_ne!(printed, format!("{value:.2}"), "{axis} is not a two-decimal copy: {svg}");
        }
        assert!(svg.contains(&format!("<tspan x=\"{x}\" dy=\"0\">")), "the tspan repeats the anchor: {svg}");
    }

    /// A wheel whose left-hand label ink grows as the plot shrinks pushes the
    /// title left of the padded view, because Vega places the title group at
    /// the FINAL layout pass's ink `x1` while the margins came from the pass
    /// before. Production's `fix_title_alignment` post-pass moves it back to
    /// exactly `padding.left`, and the committed references carry that as the
    /// two-decimal `translate(-17.00,…)` / `translate(-30.00,…)` signature.
    #[test]
    fn a_title_dragged_left_of_the_padding_is_drawn_at_the_padding() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = serde_json::json!("Revenue Wheel");
        json["resolved"]["title_style"] =
            serde_json::json!({"font": {"family": TEST_FONT, "size": 18.0, "weight": "600"}});
        json["resolved"]["slice_label_indices"] = serde_json::json!([1, 2]);
        json["rows"][1]["category"] = serde_json::json!("Accessories and Peripherals Group");
        json["rows"][2]["category"] = serde_json::json!("Tools and Hardware Group");
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let svg = render(&make_payload(json), &test_fonts()).expect("titled wheel renders");
        let before = &svg[..svg.find(">Revenue Wheel<").expect("title drawn")];
        let tag = &before[before.rfind("<text").unwrap()..];
        assert!(tag.contains("x=\"16.00\""), "title clamped to padding.left: {tag}");
    }

    /// The width-resolved `style.title_font` is the drawn title's font slot,
    /// and the title stacks above the plot through the vega title algebra
    /// (emit_title_vega), not the legacy fs+10 band.
    #[test]
    fn fit_title_uses_width_resolved_font() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = serde_json::json!("Revenue Wheel");
        json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": "serif", "size": 18.0, "weight": "600", "color": "#111111"}
        });
        json["resolved"]["style"]["title_font"] =
            serde_json::json!({"family": TEST_FONT, "size": 14.0, "weight": "600", "color": "#222222"});
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("titled pie renders");
        let before = &svg[..svg.find(">Revenue Wheel<").unwrap()];
        let tag = &before[before.rfind("<text").unwrap()..];
        assert!(tag.contains("font-size=\"14\""), "tiered 14px wins: {tag}");
        assert!(!tag.contains("font-family=\"serif\""), "tiered family wins: {tag}");
    }

    fn linked_pie_payload(link: &str) -> Value {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        p["resolved"]["link"] = json!(link);
        p
    }

    fn hrefs(svg: &str) -> Vec<&str> {
        svg.split("<a href=\"").skip(1).map(|s| s.split('"').next().unwrap()).collect()
    }

    /// `_HREF_PLACEHOLDER` matches only `x|y|color|theta`, so an authored
    /// `{{ category }}` passes through verbatim (`charts/sector-charts_2`):
    /// one `<a>` per wedge, opening right inside the arc's `.role-mark` group
    /// and closing before it does.
    #[test]
    fn a_link_paints_one_anchor_per_wedge_inside_its_mark_group() {
        let p = linked_pie_payload("/category/{{ category }}");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(hrefs(&svg), ["/category/{{ category }}"; 3], "{svg}");
        for (i, _) in svg.match_indices("<g class=\"mark-arc role-mark layer_0_marks\"") {
            let open_end = i + svg[i..].find('>').unwrap() + 1;
            assert!(svg[open_end..].starts_with("<a href=\""), "anchor opens the group: {svg}");
            let group_end = open_end + svg[open_end..].find("</g>").unwrap();
            assert!(svg[open_end..group_end].ends_with("</a>"), "anchor closes the group: {svg}");
        }
    }

    /// A modal link stamps every wedge anchor; page and missing stamp nothing.
    #[test]
    fn a_link_mode_stamps_only_modal_wedge_anchors() {
        let render_with = |mode: Option<&str>| {
            let mut p = linked_pie_payload("/c/");
            if let Some(mode) = mode {
                p["resolved"]["link_open"] = json!(mode);
            }
            render(&make_payload(p), &test_fonts())
        };
        let page = render_with(None).unwrap();
        assert_eq!(render_with(Some("page")).unwrap(), page);
        assert!(!page.contains("data-dbt-link-open"), "{page}");
        let modal = render_with(Some("modal")).unwrap();
        assert_eq!(modal.matches("<a href=\"/c/\" data-dbt-link-open=\"modal\">").count(), 3, "{modal}");
        let err = render_with(Some("popup")).unwrap_err();
        assert!(err.contains("popup"), "{err}");
    }

    /// A pie with no `tooltip_format` draws no `.role-mark` group; its link
    /// still wraps each wedge path.
    #[test]
    fn a_link_wraps_each_wedge_when_no_mark_group_is_drawn() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["link"] = json!("/c/");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(svg.matches("<a href=\"/c/\"><path d=").count(), 3, "{svg}");
        assert!(!svg.contains("role-mark"), "{svg}");
    }

    /// `{{ color }}` reads the color channel's `data_field` off the wedge's
    /// own row and `{{ theta }}` its measure, each through `_vega_encode_field`
    /// and JS `'' + value` (`197199.0` prints `197199`).
    #[test]
    fn color_and_theta_placeholders_substitute_per_wedge() {
        let mut p = linked_pie_payload("/c/?k={{ color }}&v={{theta}}");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["rows"][1]["category"] = json!("A&B C");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(
            hrefs(&svg),
            [
                "/c/?k=Electronics&amp;v=197199",
                "/c/?k=A%26B%20C&amp;v=152504",
                "/c/?k=Tools&amp;v=93320"
            ],
            "{svg}"
        );
    }

    /// The center-total value and caption texts and each slice label carry the
    /// chart's link mode like the wedges: 3 wedges + 2 total texts + 3 labels.
    #[test]
    fn a_modal_link_stamps_the_total_and_slice_label_anchors() {
        let mut p = make_pie_payload_with_labels("{{ category }}");
        p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["link"] = json!("/c/");
        let page = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert_eq!(page.matches("<a href=\"/c/\">").count(), 8, "{page}");
        assert!(!page.contains("data-dbt-link-open"), "{page}");
        p["resolved"]["link_open"] = json!("modal");
        let modal = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(modal.matches("<a href=\"/c/\" data-dbt-link-open=\"modal\">").count(), 8, "{modal}");
        assert_eq!(modal.matches("data-dbt-link-open").count(), 8, "{modal}");
    }

    /// A pie has no `x`/`y` encoding: production raises at spec build
    /// (`_build_href_calc_expr`), so the renderer refuses with its message.
    #[test]
    fn x_and_y_placeholders_refuse_with_productions_message() {
        for channel in ["x", "y"] {
            let p = linked_pie_payload(&format!("/c/?v={{{{ {channel} }}}}"));
            let err = render(&make_payload(p), &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                format!(
                    "link template references channel '{{{{ {channel} }}}}' but chart has no \
                     '{channel}' encoding assigned"
                )
            );
        }
    }

    /// No color channel bound to a data field: `{{ color }}` has no column
    /// to read, the same production error.
    #[test]
    fn a_color_placeholder_without_a_color_field_refuses() {
        let p = linked_pie_payload("/c/?k={{ color }}");
        let err = render(&make_payload(p), &test_fonts()).unwrap_err();
        assert!(err.contains("chart has no 'color' encoding assigned"), "{err}");
    }

    /// Every layer that inherits the shared `description` encoding inherits
    /// the shared `href` too: the center-total texts (`row_number === 1`, so
    /// row 0's href) and each slice label (its own row's) are anchored
    /// inside their mark groups, like the wedges.
    #[test]
    fn total_and_slice_label_texts_carry_their_rows_anchor() {
        let mut p = make_pie_payload_with_labels("{{ category }}");
        p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        p["resolved"]["link"] = json!("/c/?k={{ color }}");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(
            hrefs(&svg),
            [
                "/c/?k=Electronics", "/c/?k=Accessories", "/c/?k=Tools",
                "/c/?k=Electronics", "/c/?k=Electronics",
                "/c/?k=Electronics", "/c/?k=Accessories", "/c/?k=Tools",
            ],
            "{svg}"
        );
        for layer in ["layer_1_marks", "layer_2_marks", "layer_3_marks"] {
            for (i, _) in svg.match_indices(&format!("<g class=\"mark-text role-mark {layer}\"")) {
                let open_end = i + svg[i..].find('>').unwrap() + 1;
                assert!(svg[open_end..].starts_with("<a href=\""), "{layer}: {svg}");
                let group_end = open_end + svg[open_end..].find("</g>").unwrap();
                assert!(svg[open_end..group_end].ends_with("</text></a>"), "{layer}: {svg}");
            }
        }
    }

    // ── Visible legend ───────────────────────────────────────────────────

    fn legend_json(edge: &str, align: &str, direction: &str) -> Value {
        json!({
            "visible": true,
            "position": {"edge": edge, "align": align, "overlay": false},
            "direction": direction,
            "columns": 0,
            "label": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 8.0,
                "max_width": 200.0
            },
            "title": {
                "font": {"family": TEST_FONT, "color": "#313233", "size": 12.0, "weight": 600.0, "case": "title"},
                "visible": null
            },
            "symbol_limit": 20,
            "values": null,
            "symbol_shape": null,
            "symbol_fill": null
        })
    }

    fn legend_pie_payload(edge: &str, align: &str, direction: &str) -> Value {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category", "quantitative_data": false}});
        p["resolved"]["style"]["slice_mark"]["stroke"] =
            json!({"color": "#FFFFFF", "width": 3.0, "join": "round"});
        p["resolved"]["legend"] = legend_json(edge, align, direction);
        p
    }

    const LEGEND_LABELS: [&str; 3] = ["Electronics", "Accessories", "Tools"];

    /// The legend box the shared Vega layout gives `labels` under `payload`'s
    /// own legend config: `(box_w, box_h)`.
    fn legend_box(payload: &Value, labels: &[&str]) -> (f64, f64) {
        use crate::families::legend::{legend_title_text, vega_legend, LegendConfig, LegendEntry};
        let cfg: LegendConfig = serde_json::from_value(payload["resolved"]["legend"].clone()).unwrap();
        let entries: Vec<LegendEntry> =
            labels.iter().map(|label| LegendEntry {
                label,
                fill: "#000",
                symbol: None,
                series_key: None,
            }).collect();
        let title = legend_title_text("category", &cfg, &Default::default()).unwrap();
        let legend = vega_legend(&entries, false, &title, &cfg, &test_fonts()).unwrap();
        (legend.box_w, legend.box_h())
    }

    /// The wheel's center, off its `<g transform="translate(cx,cy)">`.
    fn wheel_center(svg: &str) -> (f64, f64) {
        let rest = &svg[svg.find("<g transform=\"translate(").unwrap() + "<g transform=\"translate(".len()..];
        let (cx, cy) = rest.split(')').next().unwrap().split_once(',').unwrap();
        (cx.parse().unwrap(), cy.parse().unwrap())
    }

    /// Vega's autosize fit gives the plot box exactly what the legend takes:
    /// the legend box plus `legend.offset` (18) on the legend's own edge
    /// (`ViewLayout.js` `layoutLegend`: `F.add(o.x1, 0).add(o.x2, 0)` for a
    /// left/right legend, `F.add(0, o.y1).add(0, o.y2)` for a top/bottom one,
    /// the legend anchored at `ceil(x2)/ceil(y2) + offset` of the plot rect,
    /// or `floor(x1)/floor(y1) - offset - box` on the far edges). No title,
    /// no labels, no total: the wheel is the plot's own center.
    #[test]
    fn a_visible_legend_shrinks_the_plot_by_its_box_plus_the_offset_on_each_edge() {
        // (edge, direction, title visible): a top/horizontal legend is baked
        // untitled (`_base_kwargs`: top + horizontal + title.visible None).
        let (avail_w, avail_h) = (568.0, 400.0);
        for (edge, direction, untitled) in
            [("right", "vertical", false), ("left", "vertical", false), ("top", "horizontal", true), ("bottom", "horizontal", false)]
        {
            let mut p = legend_pie_payload(edge, "start", direction);
            if untitled {
                p["resolved"]["legend"]["title"]["visible"] = json!(false);
            }
            let (box_w, box_h) = legend_box(&p, &LEGEND_LABELS);
            let svg = render(&make_payload(p), &test_fonts()).unwrap_or_else(|e| panic!("{edge}: {e}"));
            let (cx, cy) = wheel_center(&svg);
            let (pw, ph, left, top) = match edge {
                "right" => (avail_w - 18.0 - box_w, avail_h, 0.0, 0.0),
                "left" => (avail_w - 18.0 - box_w, avail_h, 18.0 + box_w, 0.0),
                "top" => (avail_w, avail_h - 18.0 - box_h, 0.0, 18.0 + box_h),
                _ => (avail_w, avail_h - 18.0 - box_h, 0.0, 0.0),
            };
            assert_eq!((cx, cy), (16.0 + left + pw / 2.0, 16.0 + top + ph / 2.0), "{edge}: {svg}");
        }
    }

    /// The legend is the outermost ink on its own edge: a right legend's
    /// swatch column starts at `ceil(plot right) + 18`, a bottom one's title
    /// at `ceil(plot bottom) + 18` (`ViewLayout.js` legend anchors).
    #[test]
    fn the_legend_sits_past_the_plot_rect_on_its_own_edge() {
        let p = legend_pie_payload("right", "start", "vertical");
        let (box_w, _) = legend_box(&p, &LEGEND_LABELS);
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let plot_right = 16.0 + (568.0 - 18.0 - box_w);
        // First entry's symbol center: x = legend x + size/2, size = 12.
        assert!(svg.contains(&format!("translate({:.2},", plot_right + 18.0 + 6.0)), "{svg}");
        // The label sits at legend x + 12 + labelOffset 8.
        assert!(svg.contains(&format!("<text x=\"{:.2}\"", plot_right + 18.0 + 20.0)), "{svg}");
    }

    fn legend_swatches(svg: &str) -> Vec<(String, String)> {
        // (fill, label) per entry, in painted order.
        let legend = &svg[svg.find("d=\"M5,0A5,5,0,1,1,-5,0").unwrap() - 80..];
        legend
            .split("<path transform=\"translate(")
            .skip(1)
            .map(|entry| {
                let fill = entry.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
                let label = entry.split("role-legend-label").nth(1).unwrap().split("</text>").next().unwrap();
                (fill, label.rsplit('>').next().unwrap().to_string())
            })
            .collect()
    }

    /// Entries are the color scale's domain in row order, each swatch the scale's own color for that
    /// value, stroked in the slice mark's own stroke color
    /// (`encode.ts` `symbols`: the mark's `stroke` flows to the legend
    /// symbol, never its `strokeWidth` — the 1.5 stays Vega's).
    #[test]
    fn legend_entries_follow_slice_order_and_the_color_scale() {
        let p = legend_pie_payload("right", "start", "vertical");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#3164a3", "Electronics"), ("#779bc9", "Accessories"), ("#ad9c7f", "Tools")]
                .map(|(f, l)| (f.to_string(), l.to_string())),
            "{svg}"
        );
        assert_eq!(svg.matches("stroke=\"#FFFFFF\" stroke-width=\"1.5\"/>").count(), 3, "{svg}");

        let mut bound = legend_pie_payload("right", "start", "vertical");
        bound["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(bound), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#ad9c7f", "Electronics"), ("#3164a3", "Accessories"), ("#779bc9", "Tools")]
                .map(|(f, l)| (f.to_string(), l.to_string())),
            "the board slot wins the swatch, like the wedge: {svg}"
        );
    }

    /// A hidden legend reserves nothing and draws nothing, and a visible one
    /// over a pie with no color channel draws nothing either: there is no
    /// color scale to carry a legend.
    #[test]
    fn a_hidden_or_colorless_legend_draws_nothing_and_reserves_nothing() {
        let mut hidden = legend_pie_payload("right", "start", "vertical");
        hidden["resolved"]["legend"]["visible"] = json!(false);
        let svg = render(&make_payload(hidden), &test_fonts()).unwrap();
        assert!(!svg.contains("role-legend-label"), "{svg}");
        assert_eq!(wheel_center(&svg), (16.0 + 568.0 / 2.0, 16.0 + 400.0 / 2.0), "{svg}");

        let mut colorless = legend_pie_payload("right", "start", "vertical");
        colorless["resolved"]["resolved_channels"] = json!({});
        let svg = render(&make_payload(colorless), &test_fonts()).unwrap();
        assert!(!svg.contains("role-legend-label"), "{svg}");
        assert_eq!(wheel_center(&svg), (16.0 + 568.0 / 2.0, 16.0 + 400.0 / 2.0), "{svg}");
    }

    /// `symbolLimit` (theme 20): vega-encode's `LegendEntries` keeps the
    /// first `limit - 1` entries and folds the rest into one "…N entries"
    /// row (`w.length - (limit - 1)`) that carries the next domain value's
    /// own swatch — here the 20th value's palette slot.
    #[test]
    fn symbol_limit_folds_the_overflow_into_an_entries_row() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["rows"] = json!((0..22).map(|n| json!({"category": format!("C{n}"), "revenue": 10.0})).collect::<Vec<_>>());
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let swatches = legend_swatches(&svg);
        assert_eq!(swatches.len(), 20, "{svg}");
        assert_eq!(swatches[18].1, "C18");
        // 22 values, 19 kept: 3 folded into the last row, which paints the
        // 20th value's color (palette index 19 % 3).
        assert_eq!(swatches[19], ("#779bc9".to_string(), "\u{2026}3 entries".to_string()), "{svg}");

        let mut at_limit = legend_pie_payload("right", "start", "vertical");
        at_limit["rows"] = json!((0..20).map(|n| json!({"category": format!("C{n}"), "revenue": 10.0})).collect::<Vec<_>>());
        let svg = render(&make_payload(at_limit), &test_fonts()).unwrap();
        assert_eq!(legend_swatches(&svg).len(), 20);
        assert!(!svg.contains("entries"), "an exactly-full legend folds nothing: {svg}");
    }

    /// `labelLimit` (theme 200): a wider label is truncated with an
    /// ellipsis by the same Vega `truncate` the axes use, and the legend box
    /// is bounded at the limit.
    #[test]
    fn a_label_wider_than_the_label_limit_truncates_with_an_ellipsis() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["legend"]["label"]["max_width"] = json!(100.0);
        p["rows"][0]["category"] = json!("Enterprise accounts managed by the strategic partnerships team");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let drawn = &legend_swatches(&svg)[0].1;
        assert!(drawn.starts_with("Enterprise ") && drawn.ends_with('\u{2026}'), "{drawn}");
        let width = test_fonts().measure_shaped(drawn, TEST_FONT, 11.0).unwrap();
        assert!(width <= 100.0 && width > 90.0, "{width}");
        // Vega bounds a truncated text at its limit, not at the shorter
        // drawn text: the box is symbol cell + offset (20) + 100.
        let (cx, _) = wheel_center(&svg);
        assert_eq!(cx, 16.0 + (568.0 - 18.0 - 120.0) / 2.0, "{svg}");
    }

    /// `titleLimit` (180): the same law for the legend title — drawn
    /// truncated, bounded at the limit, so the box is as wide as the limit.
    #[test]
    fn a_title_wider_than_the_title_limit_truncates_and_bounds_the_box_at_the_limit() {
        let field = "a_remarkably_long_customer_segment_classification_field_name";
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["resolved_channels"]["color"]["data_field"] = json!(field);
        p["resolved"]["identity_field"] = json!(field);
        for row in p["rows"].as_array_mut().unwrap() {
            let value = row["category"].take();
            row[field] = value;
        }
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert!(svg.contains("Customer \u{2026}</text>"), "{svg}");
        let (cx, _) = wheel_center(&svg);
        assert_eq!(cx, 16.0 + (568.0 - 18.0 - 180.0) / 2.0, "{svg}");
    }

    /// A horizontal legend flows its entries across `columns` (the
    /// resolver's compact wrap, else one row): the box is the shared
    /// grid's, so the plot gives up exactly its height on the bottom edge.
    #[test]
    fn a_wrapped_horizontal_legend_gives_the_plot_its_taller_box() {
        let mut p = legend_pie_payload("bottom", "start", "horizontal");
        p["resolved"]["legend"]["columns"] = json!(2);
        let (_, box_h) = legend_box(&p, &LEGEND_LABELS);
        let (_, one_row) = legend_box(&legend_pie_payload("bottom", "start", "horizontal"), &LEGEND_LABELS);
        assert!(box_h > one_row, "two columns wrap three entries onto two rows");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let (_, cy) = wheel_center(&svg);
        assert_eq!(cy, 16.0 + (400.0 - 18.0 - box_h) / 2.0, "{svg}");
    }

    /// `legend_align.py:100-103`: center is `(span - block) / 2`, end is
    /// `span - block`, against the plot rect the scenegraph reports (a pie
    /// with no label ink converges, so that is the drawn plot).
    #[test]
    fn center_and_end_slide_the_legend_along_its_strip() {
        for (align, share) in [("start", 0.0), ("center", 0.5), ("end", 1.0)] {
            let p = legend_pie_payload("right", align, "vertical");
            let (box_w, box_h) = legend_box(&p, &LEGEND_LABELS);
            let svg = render(&make_payload(p), &test_fonts()).unwrap();
            let title_baseline = 16.0 + share * (400.0 - box_h) + 9.0;
            let x = 16.0 + (568.0 - 18.0 - box_w).ceil() + 18.0;
            assert!(
                svg.contains(&format!("<text x=\"{x:.2}\" y=\"{title_baseline:.2}\"")),
                "{align}: title at ({x}, {title_baseline}): {svg}"
            );
        }
    }

    /// An unstroked slice mark gives an unstroked symbol (`d.stroke` is
    /// never set); the legend keeps Vega's 1.5 width and draws no ring.
    #[test]
    fn an_unstroked_slice_mark_gives_unstroked_legend_symbols() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["style"]["slice_mark"]["stroke"] = Value::Null;
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(svg.matches("fill=\"#3164a3\" stroke-width=\"1.5\"/>").count(), 1, "{svg}");
        assert!(!svg.contains("stroke=\"#FFFFFF\" stroke-width=\"1.5\""), "{svg}");
        // The bare glyph's bound tightens the row pitch from the ringed 16 to
        // 13 (`gridLayout`: `rowPadding + ceil(-y1) + ceil(y2)`: 2 + 0 + 11, not 2 + 1 + 13).
        let ys = |svg: &str| -> Vec<f64> {
            svg.split("<path transform=\"translate(")
                .skip(1)
                .map(|t| t.split(')').next().unwrap().split_once(',').unwrap().1.parse().unwrap())
                .collect()
        };
        let pitch = |svg: &str| ys(svg)[1] - ys(svg)[0];
        assert_eq!(pitch(&svg), 13.0, "{svg}");
        let stroked = render(&make_payload(legend_pie_payload("right", "start", "vertical")), &test_fonts()).unwrap();
        assert_eq!(pitch(&stroked), 16.0, "{stroked}");
    }

    /// Every legend shape this port does not draw refuses by name rather
    /// than drawing a guess.
    #[test]
    fn legend_shapes_the_port_does_not_draw_refuse_by_name() {
        type Edit = fn(&mut Value);
        let cases: [(&str, Edit, &str); 10] = [
            ("symbol_shape", |p| p["resolved"]["legend"]["symbol_shape"] = json!("square"), "legend.symbol_shape"),
            ("symbol_fill", |p| p["resolved"]["legend"]["symbol_fill"] = json!(false), "legend.symbol_fill"),
            ("empty values", |p| p["resolved"]["legend"]["values"] = json!([]), "legend.values: []"),
            ("opacity", |p| p["resolved"]["style"]["slice_mark"]["opacity"] = json!(0.5), "slice opacity"),
            ("quantitative", |p| p["resolved"]["resolved_channels"]["color"]["quantitative_data"] = json!(true), "quantitative color"),
            ("no flag", |p| p["resolved"]["resolved_channels"]["color"]["quantitative_data"] = Value::Null, "quantitative_data flag"),
            ("gradient", |p| p["resolved"]["resolved_channels"]["color"]["mode"] = json!("gradient"), "gradient legend"),
            ("number", |p| p["rows"][0]["category"] = json!(7), "non-string color value"),
            ("date", |p| p["rows"][0]["category"] = json!("2024-01-31"), "date-like color"),
            ("overlay", |p| p["resolved"]["legend"]["position"]["overlay"] = json!(true), "overlay true"),
        ];
        for (name, edit, expected) in cases {
            let mut p = legend_pie_payload("right", "start", "vertical");
            edit(&mut p);
            let err = match serde_json::from_value::<Payload>(p) {
                Ok(payload) => render(&payload, &test_fonts()).unwrap_err(),
                Err(e) => e.to_string(),
            };
            assert!(err.contains(expected), "{name}: {err}");
        }
    }

    /// The legend and an attached table are alternative keys
    /// (`suppress_legend=mode != "direct"`), and an empty result draws no
    /// legend rows: both combinations refuse by name.
    #[test]
    fn a_visible_legend_beside_an_attached_table_or_over_no_rows_refuses() {
        let mut attached = legend_pie_payload("right", "start", "vertical");
        attached["resolved"]["attached_table"] = json!({"columns": {}});
        let err = render(&make_payload(attached), &test_fonts()).unwrap_err();
        assert!(err.contains("beside an attached table"), "{err}");

        let mut empty = legend_pie_payload("right", "start", "vertical");
        empty["rows"] = json!([]);
        let err = render_empty(&make_payload(empty), &test_fonts()).unwrap_err();
        assert!(err.contains("empty result set"), "{err}");
    }

    /// An authored `legend.values` (`apply_legend_entry_order`) orders and
    /// filters the entries drawn; each keeps the swatch its own value has in
    /// the scale, and a token that matches nothing is dropped.
    #[test]
    fn authored_legend_values_order_and_filter_the_entries() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["legend"]["values"] = json!(["tools", "Electronics", "Nothing"]);
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#ad9c7f", "Tools"), ("#3164a3", "Electronics")].map(|(f, l)| (f.to_string(), l.to_string())),
            "{svg}"
        );

        let mut none = legend_pie_payload("right", "start", "vertical");
        none["resolved"]["legend"]["values"] = json!(["x"]);
        let svg = render(&make_payload(none), &test_fonts()).unwrap();
        assert_eq!(legend_swatches(&svg).len(), 3, "nothing resolving leaves the full order: {svg}");
    }

    /// `legend-symbol.js`: a vertical legend with `columns` > 1 fills
    /// column-major (`row = index % ceil(n / columns)`), a horizontal one
    /// row-major (`row = floor(index / columns)`). Three entries over two
    /// columns: vertical gives Electronics/Accessories down the first column
    /// and Tools on top of the second; horizontal gives Electronics and
    /// Accessories across the first row.
    #[test]
    fn a_vertical_legend_over_columns_fills_column_major_and_a_horizontal_one_row_major() {
        let cells = |direction: &str, edge: &str| -> Vec<(String, f64, f64)> {
            let mut p = legend_pie_payload(edge, "start", direction);
            p["resolved"]["legend"]["columns"] = json!(2);
            p["resolved"]["legend"]["title"]["visible"] = json!(false);
            let svg = render(&make_payload(p), &test_fonts()).unwrap();
            let labels: Vec<String> = legend_swatches(&svg).into_iter().map(|(_, label)| label).collect();
            let positions: Vec<(f64, f64)> = svg
                .split("<path transform=\"translate(")
                .skip(1)
                .map(|t| {
                    let (x, y) = t.split(')').next().unwrap().split_once(',').unwrap();
                    (x.parse().unwrap(), y.parse().unwrap())
                })
                .collect();
            labels.into_iter().zip(positions).map(|(label, (x, y))| (label, x, y)).collect()
        };
        let vertical = cells("vertical", "right");
        let order: Vec<&str> = vertical.iter().map(|(label, ..)| label.as_str()).collect();
        assert_eq!(order, ["Electronics", "Tools", "Accessories"], "{vertical:?}");
        let (electronics, tools, accessories) = (&vertical[0], &vertical[1], &vertical[2]);
        assert!(tools.1 > electronics.1 && tools.2 == electronics.2, "Tools heads column two: {vertical:?}");
        assert!(accessories.1 == electronics.1 && accessories.2 > electronics.2, "Accessories under Electronics: {vertical:?}");

        let horizontal = cells("horizontal", "bottom");
        let order: Vec<&str> = horizontal.iter().map(|(label, ..)| label.as_str()).collect();
        assert_eq!(order, ["Electronics", "Accessories", "Tools"], "{horizontal:?}");
        assert!(horizontal[1].1 > horizontal[0].1 && horizontal[1].2 == horizontal[0].2, "{horizontal:?}");
        assert!(horizontal[2].1 == horizontal[0].1 && horizontal[2].2 > horizontal[0].2, "{horizontal:?}");
    }

    /// Both center-total text layers are `aria: False`: hidden mark sets whose
    /// text carries no per-datum label, with or without a tooltip format.
    #[test]
    fn the_center_total_is_a_hidden_mark_set_with_no_per_datum_label() {
        for tooltip_format in [json!(",.2~f"), Value::Null] {
            let mut p = make_pie_payload_with_labels("{{ category }}");
            p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
            p["resolved"]["legend"] = json!({
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "label": {"font": {"size": 11.0}},
                "title": {"font": {"case": "title"}},
            });
            if !tooltip_format.is_null() {
                p["resolved"]["style"]["tooltip_format"] = tooltip_format;
            }
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            for layer in ["layer_1_marks", "layer_2_marks"] {
                let open = format!("<g class=\"mark-text role-mark {layer}\" aria-hidden=\"true\">");
                let start = svg.find(&open).unwrap_or_else(|| panic!("{layer} not hidden: {svg}"));
                let group = &svg[start..start + svg[start..].find("</g>").unwrap()];
                assert!(!group.contains("aria-label") && !group.contains("role="), "{layer}: {group}");
            }
        }
    }

    /// Oracle: `format_d3(197199, ",.2~f", "US$", "", None, placement)` is
    /// `'US$197,199'` and the total `'US$443,023'`; the share keeps `.0%`.
    #[test]
    fn a_tooltip_row_paints_its_affix() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!({"spec": ",.2~f", "prefix": "US$"});
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(
            svg.contains("aria-label=\"⁤Electronics; %: 45%; \u{2060}Revenue: US$197,199; \u{2060}⁣Σ: US$443,023\""),
            "{svg}"
        );
    }
}
