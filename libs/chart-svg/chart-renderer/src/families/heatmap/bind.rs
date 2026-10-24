//! Heatmap bind: what the rows and the authored style decide before any pixel is placed.
//! The band domains, the color scale and its ladder, every label's text, and each cell's
//! fill, description and link. It measures nothing and holds no size but the slot height
//! the payload carries.

use serde::Deserialize;
use serde_json::Value;

use super::{
    bind_cells, color_categories, date_color, date_color_extent, date_legend_ticks,
    drop_invalid_color_rows, legend_date_label, legend_labels, tooltip_value_title, Cell, CellFill, CellMarks, CellPaint,
    CategoryScale, ColorKind, GradientColors, HeaderFmt, HeatmapResolved, SYMBOL_LEGEND_TICK_COUNT,
};
use crate::common::{dimension_sort_domain, row_str, MirrorGhost, SortAggregate};
use crate::diagnostic::Diagnostic;
use crate::families::color_scale::ContinuousColor;
use crate::families::heatmap_facet::{date_dimension, first_appearance};
use crate::families::temporal::{infer_vega_type, TimeUnit, VegaType};
use crate::{BoardStyle, Padding, Payload};

/// A bound heatmap: the resolved chart as deserialized, with what the data derives from it.
pub struct Bound {
    pub(crate) r: HeatmapResolved,
    /// The card's chrome, which `ChartFrame` reads at layout.
    pub(crate) padding: Option<Padding>,
    pub(crate) board_style: Option<BoardStyle>,
    /// The height the payload's slot gives the chart. A heatmap has no height of its own: it
    /// fills what sizing hands it.
    pub(crate) height: f64,
    pub(crate) x: Dimension,
    pub(crate) y: Dimension,
    pub(crate) coloring: Coloring,
    /// `axis_y.mirror`: the far edge repeats the y labels.
    pub(crate) ghost: Option<MirrorGhost>,
    /// The ghost's own text per band, when its format relabels them.
    pub(crate) ghost_texts: Option<Vec<String>>,
    /// The id of the legend bar's gradient, namespaced to the chart.
    pub(crate) gradient_id: String,
    /// The gradient legend's tick labels.
    pub(crate) gradient_labels: Vec<String>,
    pub(crate) legend_title: String,
    /// A numeric color's symbol legend (a mirrored chart draws one): each rung's label and fill.
    pub(crate) ladder: Vec<(String, String)>,
    pub(crate) cells: Vec<Cell>,
    pub(crate) marks: CellMarks,
}

/// One band dimension.
pub(crate) struct Dimension {
    /// The bands in drawn order: first appearance, or the authored `chart.sort`'s.
    pub(crate) domain: Vec<String>,
    /// The bands in query order, which a date axis's calendar ladder reads.
    pub(crate) first_seen: Vec<String>,
    /// The calendar grain of a date column.
    pub(crate) grain: Option<TimeUnit>,
    /// A category axis's label per band, after an authored `labels.expr`; empty on a date axis.
    pub(crate) texts: Vec<String>,
    /// The axis title before it wraps.
    pub(crate) title: String,
}

/// What the color channel paints, owned: a gradient over a measure, or one palette slot per
/// category.
pub(crate) enum Coloring {
    Gradient { min: f64, max: f64, ticks: Vec<f64>, scale: Option<ContinuousColor> },
    /// A date-like color: Vega's time scale over its `heatmap` range.
    DateGradient { min: f64, max: f64 },
    Categories(CategoryScale),
}

/// The chart a payload describes, read before the plot or the facet composite takes it.
pub(crate) struct Prelude {
    pub(crate) r: HeatmapResolved,
    pub(crate) rows: Vec<serde_json::Map<String, Value>>,
    pub(crate) x_grain: Option<TimeUnit>,
    pub(crate) y_grain: Option<TimeUnit>,
    pub(crate) kind: ColorKind,
}

impl Prelude {
    pub(crate) fn of(payload: &Payload) -> Result<Self, Diagnostic> {
        let mut r = HeatmapResolved::from_payload(payload)?;
        // `_flag_quantitative_color` stamps every resolved color channel, so a
        // series color without the flag is a payload no resolver produced.
        let categorical = match r.resolved_channels.get("color") {
            Some(ch) if ch.mode == "series" => {
                ch.categorical
                    || !ch.quantitative_data.ok_or(
                        "heatmap: a series color channel carries no quantitative_data flag (resolver required)",
                    )?
            }
            _ => false,
        };
        let (rows, x_grain) = date_dimension(&payload.rows, &r.x, &r.style.axis_x, true)?;
        let (rows, y_grain) = date_dimension(&rows, &r.y, &r.style.axis_y, false)?;
        let rows = r.flatten_color(rows);
        let categorical = categorical || r.is_flat();
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
        Ok(Prelude { r, rows, x_grain, y_grain, kind })
    }
}

pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    let folded = fold_wide_y(payload)?;
    let payload = folded.as_ref().unwrap_or(payload);
    let Prelude { r, rows, x_grain, y_grain, kind } = Prelude::of(payload)?;
    if r.multiples.is_some() {
        return Err(Diagnostic::refused("heatmap: a multiples chart is the facet composite's, not a plot to bind"));
    }
    let rows = drop_invalid_color_rows(rows, &r.color, kind);

    // ── Band domains: first appearance over the rows, or an authored
    // `chart.sort`'s per-category MIN order. `_write_dimension_sort`
    // (`emitters/heatmap.py:70-75`) pins `sort: null` on both dimensions when
    // nothing is authored: Vega-Lite's `domainSort` (`compile/scale/domain.ts`
    // `// sort == null`) emits no domain sort, and Vega's `valuesRef`
    // (`parsers/scale.js` `singularDomain`) then keeps the aggregate's encounter
    // order. `_write_dimension_sort` reaches BOTH dimensions identically, so
    // `r.sort` is read once rather than per-axis.
    let dimension_domain = |field: &str| -> Result<Vec<String>, String> {
        dimension_sort_domain(&rows, field, first_appearance(&rows, field), r.sort.as_ref(), SortAggregate::Min, "heatmap")
    };
    let x_domain = dimension_domain(&r.x)?;
    let y_domain = dimension_domain(&r.y)?;

    let coloring = if kind == ColorKind::Dates {
        let (min, max) = date_color_extent(&rows, &r.color)?;
        Coloring::DateGradient { min, max }
    } else if kind == ColorKind::Categories {
        Coloring::Categories(color_categories(&r, &rows)?)
    } else {
        let (min, max, ticks, scale) = r.style.color_gradient.ladder(&rows, &r.color)?;
        Coloring::Gradient { min, max, ticks, scale }
    };

    // Production keeps the nominal band scale and borrows a date ladder's labels
    // (`build_cartesian_x_encoding`); it paints from query order.
    let x_texts = match x_grain {
        Some(_) => Vec::new(),
        None => crate::labels_expr::band_texts("x", &x_domain, &r.style.axis_x.labels)
            .map_err(|e| format!("heatmap: {e}"))?,
    };
    let ghost = r.style.axis_y.mirror_ghost("heatmap")?;
    let y_texts = match y_grain {
        Some(_) => Vec::new(),
        None => crate::labels_expr::band_texts("y", &y_domain, &r.style.axis_y.labels)
            .map_err(|e| format!("heatmap: {e}"))?,
    };
    let ghost_texts = match &ghost {
        // The ghost's own format reads each category as the number d3 sees (`gate_label_format`).
        Some(ghost) if ghost.format.is_some() || ghost.ruler.is_some() => {
            let values: Vec<f64> = y_domain.iter().map(|cat| crate::common::js_number(cat)).collect();
            Some(ghost.texts(&values, &y_domain)?)
        }
        _ => None,
    };

    // A title is drawn unless the style hides it (`axis_to_vl` sets
    // `title: null` only for an explicit `visible: false`); the text is the
    // authored label, else the field's own name.
    let x_title = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    let y_title = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;

    let gradient_labels = match &coloring {
        Coloring::Gradient { ticks, scale, .. } => {
            legend_labels(ticks, r.style.tooltip_format.as_ref().map(|f| f.spec.as_str()), scale.as_ref())?
        }
        Coloring::Categories(_) | Coloring::DateGradient { .. } => Vec::new(),
    };
    let legend_title = crate::families::legend::legend_title_text(&r.color, &r.legend, &r.field_labels)?;
    // The mirror's ghost rule layer strokes the same color, which turns a numeric
    // gradient into a symbol legend too: a stroked circle per rung.
    let ladder = if ghost.is_some() { symbol_ladder(&r, &coloring, &gradient_labels)? } else { Vec::new() };

    let (x_first_seen, y_first_seen) = (first_appearance(&rows, &r.x), first_appearance(&rows, &r.y));
    let (ax_x, ax_y) = (&r.style.axis_x, &r.style.axis_y);
    let paint = CellPaint {
        x_field: &r.x,
        y_field: &r.y,
        color_field: &r.color,
        fill: match &coloring {
            Coloring::Gradient { min, max, scale, .. } => CellFill::Gradient {
                colors: GradientColors::new(&r.style.color_gradient, scale.as_ref()),
                min: *min,
                max: *max,
            },
            Coloring::Categories(scale) => CellFill::Categories(scale),
            Coloring::DateGradient { min, max } => CellFill::Dates { min: *min, max: *max },
        },
        tooltip: tooltip_value_title(&r)?.zip(r.style.tooltip_format.as_ref()).filter(|_| !r.wide_native_aria),
        native_aria: r.wide_native_aria,
        x_header: HeaderFmt::of(&rows, &r.x, ax_x)?,
        y_header: HeaderFmt::of(&rows, &r.y, ax_y)?,
        link: r.datum_link((x_grain.is_some(), y_grain.is_some(), kind == ColorKind::Dates)),
        mirror: ghost.is_some(),
        ghost_titles: None,
    };
    let cells = bind_cells(&rows, &x_domain, &y_domain, &paint)?;
    let marks = CellMarks::of(&r.style.rect_mark)?;

    Ok(Bound {
        padding: payload.padding,
        board_style: payload.board_style.clone(),
        height: payload.height,
        x: Dimension { domain: x_domain, first_seen: x_first_seen, grain: x_grain, texts: x_texts, title: x_title },
        y: Dimension { domain: y_domain, first_seen: y_first_seen, grain: y_grain, texts: y_texts, title: y_title },
        coloring,
        ghost,
        ghost_texts,
        gradient_id: format!("heat_grad-{}", crate::contract::safe_svg_id(&r.id)),
        gradient_labels,
        legend_title,
        ladder,
        cells,
        marks,
        r,
    })
}

/// The rungs of a symbol legend over a numeric color: each label with its fill.
fn symbol_ladder(r: &HeatmapResolved, coloring: &Coloring, labels: &[String]) -> Result<Vec<(String, String)>, String> {
    match coloring {
        Coloring::Gradient { min, max, ticks, scale } => labels
            .iter()
            .zip(ticks)
            .map(|(label, &tick)| {
                Ok((label.clone(), GradientColors::new(&r.style.color_gradient, scale.as_ref()).at(*min, *max, tick)?))
            })
            .collect(),
        Coloring::DateGradient { min, max } => {
            date_legend_ticks(*min, *max, SYMBOL_LEGEND_TICK_COUNT, r.legend.values.as_deref())?
                .into_iter()
                .map(|day| Ok((legend_date_label(day)?, date_color(*min, *max, day))))
                .collect()
        }
        Coloring::Categories(_) => Ok(Vec::new()),
    }
}

/// A multi-measure heatmap (`y: [a, b]`) draws one rect layer per measure: x is the
/// row's x, the y band is the measure's own VALUE, and the measure's label colors
/// the cell on a shared categorical scale. The same cells fall out of a long fold
/// whose y is the folded value and whose color is the folded label.
pub(crate) fn fold_wide_y(payload: &Payload) -> Result<Option<Payload>, String> {
    let Some(names) = payload.resolved.get("y").and_then(Value::as_array) else { return Ok(None) };
    let measures: Vec<String> = names
        .iter()
        .map(|m| m.as_str().map(str::to_string).ok_or_else(|| format!("heatmap: y names the non-text measure {m}")))
        .collect::<Result<_, _>>()?;
    let field_labels: crate::common::FieldLabels = payload
        .resolved
        .get("field_labels")
        .map(|labels| Deserialize::deserialize(labels).map_err(|e| format!("heatmap payload: field_labels: {e}")))
        .transpose()?
        .unwrap_or_default();
    let labels = display_text::wide_measure_labels_for(&measures, &field_labels)?;
    let mut rows = crate::common::unfold_wide_rows(&payload.rows, &measures, None);
    for row in &mut rows {
        let measure = row_str(row, crate::common::WIDE_KEY_FIELD);
        row.insert(crate::common::WIDE_LABEL_FIELD.to_string(), Value::String(labels[&measure].clone()));
    }
    let mut resolved = payload.resolved.clone();
    let fields = resolved.as_object_mut().ok_or("heatmap payload: resolved is not an object")?;
    if fields.get("y_label").is_none_or(Value::is_null) {
        fields.insert("y_label".into(), Value::String(crate::common::wide_measures_title(&measures, &field_labels)?));
    }
    fields.insert("wide_native_aria".into(), Value::Bool(true));
    fields.insert("y".into(), Value::String(crate::common::WIDE_VALUE_FIELD.into()));
    fields.insert("color".into(), Value::String(crate::common::WIDE_LABEL_FIELD.into()));
    fields.insert(
        "resolved_channels".into(),
        serde_json::json!({"color": {
            "channel": "color", "mode": "series", "data_field": crate::common::WIDE_LABEL_FIELD, "quantitative_data": false
        }}),
    );
    Ok(Some(payload.with_inputs(resolved, rows)))
}
