//! Pie bind: what the rows and the authored style decide before any pixel is placed.
//! The slices' fills, descriptions and links, the slice labels' text and ink, the center
//! total's text, the legend's entries and the attached table's rows. It measures nothing.

use serde::Deserialize;
use serde_json::Value;

use super::{LegendConfig, Resolved};
use crate::common::{category_color, category_ink, category_scale_for, row_str, CategoryColorScale};
use crate::diagnostic::Diagnostic;
use crate::families::legend::{
    fold_symbol_limit, legend_title_text, resolve_legend_values, VEGA_LEGEND_SYMBOL_LIMIT,
};
use crate::families::table::bind::is_date_like_cell;
use crate::label_template::{Scope, Template};
use crate::number_locale::d3_format;
use crate::Payload;

/// A bound pie: the resolved chart as deserialized, with what the rows derive from it.
pub struct Bound<'a> {
    pub(crate) payload: &'a Payload,
    pub(crate) r: Resolved,
    /// The measure per row; a missing or non-numeric cell counts as 0.
    pub(crate) thetas: Vec<f64>,
    pub(crate) total: f64,
    pub(crate) wheel: Wheel,
    pub(crate) attached: Option<Attached>,
    /// The drawing fades under the placeholder overlay. Neither an empty result (the dispatch
    /// draws it as the empty chart) nor an attached table (production composes it unfaded) does.
    pub(crate) faded: bool,
}

/// What the wheel paints, per row and per mark set.
pub(crate) struct Wheel {
    /// One per row, in row order.
    pub(crate) slices: Vec<Slice>,
    /// One per labeled row, in row order.
    pub(crate) labels: Vec<SliceLabel>,
    /// The center total's text when the total is visible.
    pub(crate) center: Option<String>,
    pub(crate) legend: Option<LegendPlan>,
}

pub(crate) struct Slice {
    pub(crate) fill: String,
    /// The structured-tooltip description; `None` when the chart authors no tooltip format.
    pub(crate) aria: Option<String>,
    pub(crate) href: Option<String>,
}

pub(crate) struct SliceLabel {
    /// The row the label is built from; its description and link are that slice's.
    pub(crate) idx: usize,
    pub(crate) lines: Vec<String>,
    pub(crate) fill: String,
}

/// The color legend as the rows decide it; layout limits its text and lays it out.
pub(crate) struct LegendPlan {
    /// `(label, fill)` per drawn entry, the folded overflow row included.
    pub(crate) entries: Vec<(String, String)>,
    /// Per entry, the raw color value its slice links by; `None` for the "…N entries" fold row.
    pub(crate) keys: Vec<Option<String>>,
    pub(crate) title: String,
    pub(crate) symbol_stroke: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Placement {
    Top,
    Bottom,
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Align {
    Start,
    Center,
    End,
}

pub(crate) struct Heading {
    pub(crate) text: String,
    pub(crate) size: f64,
    pub(crate) gap: f64,
    pub(crate) family: String,
    pub(crate) weight: String,
    pub(crate) color: String,
}

/// The legend table beside or below the wheel: a table over the rows production's
/// `project_pie_table_rows` synthesizes.
pub(crate) struct Attached {
    pub(crate) placement: Placement,
    pub(crate) align: Align,
    pub(crate) table_width: f64,
    pub(crate) gap: f64,
    pub(crate) wheel_width: f64,
    pub(crate) heading: Option<Heading>,
    /// The theme's view height the wheel renders at.
    pub(crate) view_height: f64,
    pub(crate) table: crate::families::table::Bound,
}

pub fn bind(payload: &Payload) -> Result<Bound<'_>, Diagnostic> {
    let r = resolved_of(payload)?;
    // Pie's authored surface can no longer produce a conditional color channel
    // (`conditional_formatting` is retired from pie/donut), but a hand-written or stale
    // resolved-JSON fixture can: one check covers the slice fill and the label ink.
    if r.resolved_channels.get("color").is_some_and(|ch| ch.mode == "conditional") {
        return Err("pie: conditional color channel not supported".into());
    }
    let thetas: Vec<f64> = payload
        .rows
        .iter()
        .map(|row| row.get(&r.theta).and_then(Value::as_f64).unwrap_or(0.0))
        .collect();
    let total: f64 = thetas.iter().sum();
    if total <= 0.0 {
        return Err("pie total is zero — nothing to draw".into());
    }
    let attached = match r.attached_table.as_ref().filter(|v| !v.is_null()) {
        None => None,
        Some(table) => Some(bind_attached(payload, &r, table, &thetas, total)?),
    };
    let wheel = bind_wheel(&r, &payload.rows, &thetas, total)?;
    let faded = payload.is_placeholder && attached.is_none();
    Ok(Bound { payload, r, thetas, total, wheel, attached, faded })
}

/// A pie over no rows: production compiles the arc and label layers over an empty
/// dataset, so Vega draws the two empty mark groups inside the negotiated frame. Its
/// zero total is the empty dataset's, not a chart of zero-valued slices (that stays
/// refused in [`bind`]).
pub fn bind_empty(payload: &Payload) -> Result<Bound<'_>, Diagnostic> {
    let r = resolved_of(payload)?;
    if r.attached_table.as_ref().is_some_and(|v| !v.is_null()) {
        return Err("pie: empty result set not supported with an attached table".into());
    }
    if legend_is_drawn(&r) {
        return Err("pie: a visible legend over an empty result set is not ported".into());
    }
    let wheel = bind_wheel(&r, &payload.rows, &[], 0.0)?;
    Ok(Bound { payload, r, thetas: Vec::new(), total: 0.0, wheel, attached: None, faded: false })
}

fn resolved_of(payload: &Payload) -> Result<Resolved, Diagnostic> {
    Ok(Resolved::from_payload(payload)?)
}

/// Whether a legend is drawn at all: visible and a color field to key it.
pub(super) fn legend_is_drawn(r: &Resolved) -> bool {
    r.legend.visible && color_field_of(r).is_some()
}

/// The color channel's data field, `None` when the chart authors no `color:`. Without
/// it there is neither a color scale nor a legend.
pub(super) fn color_field_of(r: &Resolved) -> Option<&str> {
    r.resolved_channels
        .get("color")
        .and_then(|ch| ch.data_field.as_deref())
        .filter(|f| !f.is_empty())
}

fn bind_wheel(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    thetas: &[f64],
    total: f64,
) -> Result<Wheel, String> {
    // Without a color channel every wedge paints one constant `palette[0]`:
    // palette-by-index is the color scale's range, so it needs the scale to exist.
    let color_field = color_field_of(r);
    // The board's plan for this field, if it binds one (#7167): the arc fill defers to it
    // over the local `slice.index % palette.len()`.
    let category_scale = color_field.and_then(|cf| category_scale_for(&r.category_colors, cf));
    let legend = plan_legend(r, rows, category_scale)?;
    let labels = bind_labels(r, rows, thetas, total, color_field, category_scale)?;
    let center = bind_center(r, total)?;
    let slices = bind_slices(r, rows, thetas, total, color_field, category_scale)?;
    Ok(Wheel { slices, labels, center, legend })
}

fn bind_center(r: &Resolved, total: f64) -> Result<Option<String>, String> {
    if r.total.as_ref().is_some_and(|t| t.format.is_some()) {
        return Err("pie: total.format moved to style.total_style.value.format".to_string());
    }
    match &r.total {
        Some(total_cfg) if total_cfg.visible => Ok(Some(match &r.style.total_style.value.format {
            Some(fmt) => fmt.paint(total).map_err(|e| format!("pie: total value format: {e}"))?,
            None if total.fract() == 0.0 => format!("{}", total as i64),
            None => format!("{total}"),
        })),
        _ => Ok(None),
    }
}

fn bind_slices(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    thetas: &[f64],
    total: f64,
    color_field: Option<&str>,
    category_scale: Option<&CategoryColorScale>,
) -> Result<Vec<Slice>, String> {
    // The tooltip's value title doesn't vary per slice (`features/structured_tooltip.py::
    // _pie_roles`'s `format_display_text(chart.theta, ..., font=chart.legend.title.font)`);
    // no `tooltip_format` means no aria-labels for this chart, same gate as bar's own
    // `tooltip_labels`.
    let value_title = if !r.style.tooltip_format.spec.is_empty() {
        let case = pie_legend_title_case(&r.legend)?;
        Some(crate::common::field_display_text(&r.theta, &r.field_labels, case)?)
    } else {
        None
    };
    let slices = (0..thetas.len())
        .map(|idx| {
            let fill = match (color_field, category_scale) {
                (None, _) => r.palette[0].clone(),
                (Some(cf), Some(scale)) if !matches!(rows[idx].get(cf), None | Some(Value::Null)) => {
                    category_color(scale, &row_str(&rows[idx], cf), &r.palette)?
                }
                _ => r.palette[idx % r.palette.len()].clone(),
            };
            let aria = value_title
                .as_deref()
                .map(|value_title| pie_row_aria(r, rows, thetas, total, value_title, idx))
                .transpose()?;
            Ok(Slice { fill, aria, href: row_href(r, rows, idx)? })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(slices)
}

/// The link of row 0 the visible center total carries. A pie over no rows has no row to fill
/// a channel placeholder, so a link that names one leaves the total unlinked; the channel is
/// still checked against the chart's encodings.
pub(super) fn center_href(r: &Resolved, rows: &[serde_json::Map<String, Value>]) -> Result<Option<String>, String> {
    match &r.total {
        Some(total_cfg) if total_cfg.visible && !rows.is_empty() => row_href(r, rows, 0),
        Some(total_cfg) if total_cfg.visible => {
            let mut names_channel = false;
            let href = r
                .link
                .as_deref()
                .map(|template| {
                    crate::href::resolve_channel_href(template, |channel| {
                        names_channel = true;
                        pie_link_value(r, &serde_json::Map::new(), channel)
                    })
                })
                .transpose()?;
            Ok(href.filter(|_| !names_channel))
        }
        _ => Ok(None),
    }
}

/// The slice labels' text and ink, for the rows `slice_label_indices` names.
fn bind_labels(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    thetas: &[f64],
    total: f64,
    color_field: Option<&str>,
    category_scale: Option<&CategoryColorScale>,
) -> Result<Vec<SliceLabel>, String> {
    let Some(labels) = r.style.slice_mark.labels.as_ref() else { return Ok(Vec::new()) };
    let template = Template::parse(&labels.template)?;
    let mut out = Vec::new();
    for (idx, theta) in thetas.iter().enumerate() {
        if !r.slice_label_indices.contains(&idx) {
            continue;
        }
        let lines = template.render(&Scope {
            row: &rows[idx],
            theta_field: &r.theta,
            color_field: r.identity_field.as_deref(),
            percent: theta / total,
            total,
        })?;
        // The board slot when bound, a pure function of the row's own value, same as the
        // wedge's fill. Production reads `labels.font.color` only when there is no color
        // key: a color channel always inks with the dark companions, by board slot or,
        // unbound, by row position.
        let via_scale = match (category_scale, color_field) {
            (Some(scale), Some(cf)) if !matches!(rows[idx].get(cf), None | Some(Value::Null)) => Some(category_ink(
                scale,
                &row_str(&rows[idx], cf),
                &r.dark_companion_stops,
                &r.pin_companions,
            )?),
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
        out.push(SliceLabel { idx, lines, fill });
    }
    Ok(out)
}

/// `legend.title.font.case`. The theme cascade guarantees every resolved chart's
/// `legend.title.font` is populated (`core/AGENTS.md`'s "Theme YAML completeness"), so a
/// caller reaching this with `tooltip_labels` true and no case is a genuine
/// resolved-payload defect, not a shape to default around.
fn pie_legend_title_case(legend: &LegendConfig) -> Result<&str, String> {
    legend
        .title
        .font
        .case
        .as_deref()
        .ok_or_else(|| "pie: tooltip value title requires legend.title.font.case".to_string())
}

/// One row's structured-tooltip aria-label, shared by the wedge and its slice label.
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
    let extra = crate::families::tooltip_extra::rows_for(&r.tooltip, &payload_rows[idx])?;
    let tt = crate::contract::PieTooltip {
        header: header.as_deref(),
        share: value / total,
        value_title,
        value_format: &r.style.tooltip_format,
        value,
        total,
        extra: &extra,
    };
    crate::contract::pie_aria_label(&tt)
}

/// The href of row `idx`'s datum: `r.link` with its channel placeholders substituted from
/// that row, `None` on a chart with no `link`.
fn row_href(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    idx: usize,
) -> Result<Option<String>, String> {
    r.link
        .as_deref()
        .map(|template| {
            crate::href::resolve_channel_href(template, |channel| pie_link_value(r, &rows[idx], channel))
        })
        .transpose()
}

/// `_channel_field` for a pie (`click_interactivity.py:20-58`): `color` is the color
/// channel's `data_field`, `theta` the measure. A pie has no `x`/`y` encoding, where
/// production raises the same error at spec build.
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

/// The pie's color legend over the color scale's domain, up to the text a layout still
/// has to limit and lay out. `None` when no legend is drawn. Every legend shape this port
/// does not draw refuses by name.
fn plan_legend(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
    category_scale: Option<&CategoryColorScale>,
) -> Result<Option<LegendPlan>, String> {
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

    let limit = cfg.symbol_limit.unwrap_or(VEGA_LEGEND_SYMBOL_LIMIT) as usize;
    let mut entries: Vec<(String, String)> =
        order.iter().map(|&idx| (domain[idx].to_string(), fills[idx].clone())).collect();
    let mut keys: Vec<Option<String>> = order.iter().map(|&idx| Some(domain[idx].to_string())).collect();
    if let Some((kept, label)) = fold_symbol_limit(entries.len(), limit) {
        let folded = (label, entries[kept].1.clone());
        entries.truncate(kept);
        entries.push(folded);
        keys.truncate(kept);
        keys.push(None);
    }
    let title = legend_title_text(color_field, cfg, &r.field_labels)?;
    let symbol_stroke = r.style.slice_mark.stroke.as_ref().and_then(|s| s.color.clone());
    Ok(Some(LegendPlan { entries, keys, title, symbol_stroke }))
}

fn bind_attached(
    payload: &Payload,
    r: &Resolved,
    attached: &Value,
    thetas: &[f64],
    total: f64,
) -> Result<Attached, Diagnostic> {
    // Production suppresses the legend whenever the key is a table
    // (`suppress_legend=mode != "direct"`); a payload carrying both is not production's.
    if legend_is_drawn(r) {
        return Err("pie: a visible legend beside an attached table is not ported".into());
    }
    let placement = r
        .attached_table_placement
        .as_deref()
        .ok_or("pie: attached_table requires attached_table_placement")?;
    let align = r
        .attached_table_align
        .as_deref()
        .ok_or("pie: attached_table requires attached_table_align")?;
    // `LegendEdge` and `LegendAlign` (compile/models/style/theme.py) are the only values
    // `compose_attached_table_svg` indexes; anything else is not a production payload.
    let (placement_kind, align_kind) = match (placement, align) {
        ("top", "start") => (Placement::Top, Align::Start),
        ("top", "center") => (Placement::Top, Align::Center),
        ("top", "end") => (Placement::Top, Align::End),
        ("bottom", "start") => (Placement::Bottom, Align::Start),
        ("bottom", "center") => (Placement::Bottom, Align::Center),
        ("bottom", "end") => (Placement::Bottom, Align::End),
        ("left", "start") => (Placement::Left, Align::Start),
        ("left", "center") => (Placement::Left, Align::Center),
        ("left", "end") => (Placement::Left, Align::End),
        ("right", "start") => (Placement::Right, Align::Start),
        ("right", "center") => (Placement::Right, Align::Center),
        ("right", "end") => (Placement::Right, Align::End),
        _ => {
            return Err(format!(
                "pie: attached_table placement '{placement}' align '{align}' is not supported"
            ).into())
        }
    };
    let table_width = r.attached_table_width.ok_or("pie: attached_table requires attached_table_width")?;
    let gap = r.attached_table_gap.ok_or("pie: attached_table requires attached_table_gap")?;
    let wheel_width = r.wheel_width.ok_or("pie: attached_table requires wheel_width")?;
    let table_category_scale = r
        .identity_field
        .as_deref()
        .and_then(|cf| category_scale_for(&r.category_colors, cf));
    let table = bind_table(
        payload,
        attached,
        &r.attached_row_indices,
        &r.palette,
        r.identity_field.as_ref(),
        &r.theta,
        thetas,
        total,
        table_width,
        table_category_scale,
    )?;

    let heading = r.attached_heading.as_deref().unwrap_or("");
    let heading = if heading.is_empty() {
        None
    } else {
        let font = r
            .attached_heading_font
            .as_ref()
            .ok_or("pie: attached_table heading requires attached_heading_font")?;
        let gap = r
            .hybrid_heading_gap
            .ok_or("pie: attached_table heading requires hybrid_heading_gap")?;
        Some(Heading {
            text: heading.to_string(),
            size: font.size_or(11.0),
            gap,
            family: font.family_or("sans-serif").to_string(),
            weight: font.weight_str(),
            color: font.color.as_deref().unwrap_or("#000").to_string(),
        })
    };

    // The wheel is a full standalone VL render at `width=wheel_width, height=None`: its
    // effective height is the theme's `chart_defaults.view.continuous_height` (VL's
    // `config.view.continuousHeight`), NOT the slot height.
    let view_height = payload
        .board_style
        .as_ref()
        .and_then(|bs| bs.continuous_height)
        .ok_or("pie: attached_table requires board_style.continuous_height (the wheel renders at the theme's view height)")?;
    Ok(Attached {
        placement: placement_kind,
        align: align_kind,
        table_width,
        gap,
        wheel_width,
        heading,
        view_height,
        table,
    })
}

/// The attached table's rows: swatch, share, name, value per frozen row index —
/// production's `project_pie_table_rows`. The share is the slice label's own `.0%` spec.
#[allow(clippy::too_many_arguments)]
pub(super) fn project_pie_table_rows(
    payload: &Payload,
    row_indices: &[usize],
    palette: &[String],
    identity_field: Option<&String>,
    theta_field: &str,
    thetas: &[f64],
    total: f64,
    category_scale: Option<&CategoryColorScale>,
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

/// The pie's attached table bound at `table_width`, unbounded in height.
#[allow(clippy::too_many_arguments)]
fn bind_table(
    payload: &Payload,
    attached: &Value,
    row_indices: &[usize],
    palette: &[String],
    identity_field: Option<&String>,
    theta_field: &str,
    thetas: &[f64],
    total: f64,
    table_width: f64,
    category_scale: Option<&CategoryColorScale>,
) -> Result<crate::families::table::Bound, Diagnostic> {
    let rows = &payload.rows;
    if row_indices.is_empty() {
        return Err("pie: attached_table requires at least one row in attached_row_indices".into());
    }
    if let Some(&bad) = row_indices.iter().find(|&&i| i >= rows.len()) {
        return Err(format!("pie: attached_table row index {bad} out of range — {} data rows", rows.len()).into());
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
        // A legend with a hidden header and no paging: nothing to sort or filter.
        interactive_controls: false,
        link_context: payload.link_context.clone(),
        datasets: None,
        geometries: None,
        measuring: false,
        title_shift: crate::TitleShift::default(),
        is_placeholder: false,
        authored_height: None,
    };
    crate::families::table::bind::bind(&table_payload)
}
