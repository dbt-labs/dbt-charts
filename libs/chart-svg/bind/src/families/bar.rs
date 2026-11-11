//! Bar: one vertical bar per row on a band x and a quantitative y, from the zero baseline.
//! Production's laws composed: the band order (`plot::band`), the zero-anchored value domain
//! (`plot::zero`, `plot::domain`), the axis and tooltip formats (`formats`) and the zero-rule
//! gate. A row with a null y keeps its category's band and draws no bar. A nominal `color:` splits
//! the rows into one series per value, drawn grouped, which is production's default arrangement:
//! every bar grows from the baseline (`y0` is 0) and takes the sub-band of its series index
//! within its category's band. A single-series bar takes `type: line` layers on its shared y: the
//! bars first, then one line per layer, each its own series, over one value domain that spans bars
//! and layers. What the bind does not model yet refuses by name.
//!
//! Binds a band x and one value y to a `Family::Cartesian` of vertical bars (`Mark::Bars`).
//! - A nominal `color:` seats one series per value in production's sorted order, from the
//!   board-wide plan, with a `Legend`; non-negative bars are painted before negative ones.
//! - A colorless bar takes `type: line` layers on its shared y: one `Mark::Lines` layer and
//!   series per layer after the bars, in authored order. The value domain spans bars and
//!   layers and is rounded as a shared scale; each unauthored layer takes the first palette
//!   color not yet painted; one `Legend` covers the bars and layers.
//!
//! `tests/it/bar.rs` replays `bar_production.json`, recorded by `record_bar_bind.py`, and
//! `tests/it/bar_layers.rs` replays `bar_layers_production.json`, recorded by
//! `record_bar_layers_bind.py`.

use std::collections::{HashMap, HashSet};

use board_model::board::Board;
use board_model::chart::layer::{CartesianLayer, LineLayer};
use board_model::chart::{BarChart, ColorColumn, Columns};
use board_model::ids::ChartId;
use board_model::style::bar::{Orientation, StackMode};
use board_model::style::marks::LabelVisible;
use diagnostic::{codes, diag, py_str_repr, refuse, Diagnostic};
use model_core::{Engine, SetLeaves, Tri};

use crate::channel_type::{classify, y_is_numeric, ChannelType};
use crate::category_colors::CategoryColorScale;
use crate::decode::decode;
use crate::derived::{DerivedChart, Placed};
use crate::families::cartesian::{self, axis_format, hex, no_data, plain_text, single_series, threshold_rules, unsupported, value, Prelude};
use crate::formats::locale::active_number_locale;
use crate::legend::fold_symbol_limit;
use crate::bound::{self,
    AuthoredLegend, AxisStyles, Baselines,  Datum, Failure, Key, Legend, LegendEntry, Mark, MarkLayer, MarkStyle, Naming, Orient, Paint,
    Panels, Pos, RowRef, Scale, ScaleKind, Series, SeriesKey, Tooltip, TooltipEntry,
};
use crate::plot::band::band_domain;
use crate::plot::domain;
use crate::plot::zero::{self, Family};
use crate::effective_palette;
use crate::run::RunResult;

/// What a bar chart's laws bind: the two scales, the series and a datum per row.
#[derive(Debug)]
pub struct Bars {
    pub x: Scale,
    pub y: Scale,
    /// The one measure series, or one per `color:` value in production's sorted order.
    pub series: Vec<Series>,
    /// Naming the series of a `color:`.
    pub legend: Option<Legend>,
    /// One per row, in the order production paints them. A row with a null y is `defined: false`.
    pub datums: Vec<Datum>,
    /// Each line layer's datums in authored layer order: one per row, in row order, a null y
    /// `defined: false`. The layer's series follows the bars' in `series`.
    pub lines: Vec<Vec<Datum>>,
    pub baselines: Baselines,
    pub axis_styles: AxisStyles,
    /// The frame, fill and legend the placement derives, after the chart's data has passed its refusals.
    pub derived: DerivedChart,
    /// The chart's own axis keys the other tiers already answer.
    pub diagnostics: Vec<Diagnostic>,
}

fn internal(invalid: crate::bound::Invalid) -> Diagnostic {
    diag::err_internal(format!("bar is invalid: {invalid:?}"))
}

/// The line layers of `chart`, in authored order; every other layer type refuses by name.
fn line_layers(chart: &BarChart) -> Result<Vec<&LineLayer>, Diagnostic> {
    chart
        .layers
        .iter()
        .map(|layer| match layer {
            CartesianLayer::Line(line) => Ok(line),
            CartesianLayer::Bar(_) => Err("bar"),
            CartesianLayer::Area(_) => Err("area"),
            CartesianLayer::Scatter(_) => Err("scatter"),
        })
        .collect::<Result<_, _>>()
        .map_err(|kind| unsupported(refuse!("a bar with a layer of type `{kind}` is not supported")))
}

/// What a bar's line layer may author: its own `y:` on the shared axis, a `label:`, and no paint.
fn refuse_layers(chart: &BarChart) -> Result<(), Diagnostic> {
    let layers = line_layers(chart)?;
    if !layers.is_empty() && chart.cartesian.color.is_some() {
        return Err(unsupported(refuse!("a bar with `color:` and `layers:` is not supported")));
    }
    for layer in layers {
        let base = &layer.base;
        if base.axis_y.is_some() {
            return Err(unsupported(refuse!("a bar line layer with `axis_y:` is not supported")));
        }
        let unported = [
            ("no `y:`", base.y.is_none()),
            ("`query:`", base.query.is_some()),
            ("`x:`", base.x.is_some()),
            ("`color:`", base.color.is_some()),
            ("`legend: false`", !base.legend),
        ];
        if let Some((field, _)) = unported.into_iter().find(|(_, set)| *set) {
            return Err(unsupported(refuse!("a bar line layer with {field} is not supported")));
        }
        let mut leaf = None;
        if let Some(style) = &layer.style {
            style.set_leaves("", &mut |path| {
                leaf.get_or_insert_with(|| path.to_string());
            });
        }
        if let Some(leaf) = leaf {
            return Err(unsupported(refuse!("a bar line layer that sets `{leaf}` is not supported")));
        }
    }
    Ok(())
}

/// The ink an unauthored layer takes on the chart's shared color scale: the first palette color
/// the bars and the layers before it have not painted, else the palette's color at the number of
/// colors painted so far, wrapping.
fn free_ink(painted: &[String], palette: &[String]) -> Result<String, Diagnostic> {
    let taken: Vec<String> = painted.iter().map(|ink| ink.to_lowercase()).collect();
    let free = palette.iter().find(|ink| !taken.contains(&ink.to_lowercase()));
    free.or_else(|| palette.get(painted.len().checked_rem(palette.len())?))
        .cloned()
        .ok_or_else(|| diag::err_internal("a layered bar read an empty palette"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &BarChart, placed: &Placed<'_>) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    if fields.color.is_some() && matches!(placed.own.charts.bar.cartesian.painted.color.base.gradient, Tri::Value(_)) {
        return Err(unsupported(refuse!("a cartesian `color:` with a `style.color` gradient is not supported")));
    }
    if matches!(fields.y, Some(Columns::Many(_))) {
        return Err(unsupported(refuse!("a bar with a list `y:` is not supported")));
    }
    if chart.style.stack != StackMode::None {
        return Err(unsupported(refuse!("a stacked bar is not supported")));
    }
    refuse_layers(chart)?;
    if !chart.annotations.is_empty() {
        return Err(unsupported(refuse!("a bar with `annotations:` is not supported")));
    }
    if fields.multiples.is_some() {
        return Err(unsupported(refuse!("a bar with `multiples:` is not supported")));
    }
    if !chart.tooltip.tooltip.is_empty() {
        return Err(unsupported(refuse!("a bar with `tooltip:` columns is not supported")));
    }
    if fields.sort.is_some() {
        return Err(unsupported(refuse!("a bar with `sort:` is not supported")));
    }
    if chart.style.marks.bar.labels.labels.base.visible != LabelVisible::Off {
        return Err(unsupported(refuse!("a bar with value labels is not supported")));
    }
    if chart.style.orientation != Some(Engine::Value(Orientation::Vertical)) {
        return Err(unsupported(refuse!("a bar whose orientation is not authored `vertical` is not supported")));
    }
    if fields.shared.base.link.is_some() {
        return Err(unsupported(refuse!("a bar with `link:` is not supported")));
    }
    if fields.x_label.is_some() || fields.y_label.is_some() {
        return Err(unsupported(refuse!("a bar with `x_label:` or `y_label:` is not supported")));
    }
    if chart.y_start.is_some() {
        return Err(unsupported(refuse!("a bar with `y_start:` is not supported")));
    }
    if chart.pattern.pattern.is_some() {
        return Err(unsupported(refuse!("a bar with `pattern:` is not supported")));
    }
    if fields.support_table.is_some() {
        return Err(unsupported(refuse!("a bar with `support_table:` is not supported")));
    }
    Ok(())
}

/// The legend a derived placement walks, refused where the bind does not model it: `values:` names
/// the entries of a `color:` legend.
fn refuse_legend_values(chart: &BarChart, derived: &DerivedChart) -> Result<(), Diagnostic> {
    if (chart.cartesian.color.is_some() || !chart.layers.is_empty()) && derived.legend.base.values.is_some() {
        return Err(unsupported(refuse!("a bar legend with `values:` is not supported")));
    }
    Ok(())
}

/// The laws of a bar chart over its rows; `None` when the query returned no rows. `scales` is the
/// board-wide category color plan a `color:` series paints from.
pub fn bars(
    id: &ChartId,
    chart: &BarChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed<'_>,
    scales: &[CategoryColorScale],
) -> Result<Option<Bars>, Diagnostic> {
    let p = Prelude::of(&chart.cartesian, ChannelType::Nominal, placed)?;
    let (x_field, y_field) = (p.x_field, p.y_field);
    if p.x_ladder.labels_format().is_some() {
        return Err(unsupported(refuse!("a bar band axis with a label format is not supported")));
    }
    let y_format = axis_format(&p.y_ladder, p.formats)?;
    let color_field = chart.cartesian.color.as_ref().map(ColorColumn::as_str);
    let layers = line_layers(chart)?;
    let layer_fields = layers
        .iter()
        .map(|layer| layer.base.y.as_deref().ok_or_else(|| diag::err_internal("a bar line layer reaches the bind without its own `y:`")))
        .collect::<Result<Vec<_>, _>>()?;
    let fields: Vec<&str> = [x_field, y_field].into_iter().chain(color_field).chain(layer_fields.iter().copied()).collect();
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &fields)?;
    if dataset.rows.is_empty() {
        return Ok(None);
    }
    let x_type = classify(&dataset.schema, &dataset.rows, Some(x_field), true)?;
    if !y_is_numeric(&dataset.schema, &dataset.rows, y_field)? {
        let error = diag::err_bar_y_not_numeric(placed.public_id, y_field);
        return Err(match x_type {
            ChannelType::Quantitative => error.with_hint(format!("Column {} looks numeric — did you mean to swap x and y?", py_str_repr(x_field))),
            _ => error,
        });
    }
    for field in &layer_fields {
        if !dataset.schema.get(field).map_err(Diagnostic::refused)?.is_number() {
            return Err(unsupported(format!("bar: y column {field:?} is not numeric")));
        }
    }
    match x_type {
        ChannelType::Nominal => {}
        ChannelType::Temporal => return Err(unsupported(refuse!("a bar on a temporal or year-shaped x is not supported"))),
        ChannelType::Quantitative => return Err(unsupported(refuse!("a bar on a quantitative x is not supported"))),
    }
    let decoded = decode(dataset)?;
    let (xs, ys) = (decoded.keys(x_field)?, decoded.keys(y_field)?);
    if xs.contains(&Key::Null) {
        return Err(unsupported(refuse!("a bar row with a null x is not supported")));
    }
    if xs.iter().any(|x| !matches!(x, Key::Text(_))) {
        return Err(unsupported(refuse!("a bar on an x that is not text is not supported")));
    }
    let categories = band_domain(&dataset.rows, xs, None, |key| match key {
        Key::Text(text) => text.clone(),
        other => unreachable!("every x key is text: {other:?}"),
    })?;
    let derived = placed.derive()?;
    if (color_field.is_some() || !chart.layers.is_empty()) && derived.legend.base.values.is_some() {
        return Err(unsupported(refuse!("a bar legend with `values:` is not supported")));
    }
    let palette = effective_palette(&placed.own.charts.bar.cartesian.painted.color.categorical, |own| own.palette.as_ref(), p.style)?;
    let derived = placed.derive()?;
    refuse_legend_values(chart, &derived)?;
    let colors = color_field
        .map(|field| cartesian::color_series(field, dataset, decoded.keys(field)?, scales, &palette, derived.frame.canvas))
        .transpose()?;
    let entry_count = match &colors {
        Some(colors) => Some(colors.series.len()),
        None => (!layers.is_empty()).then_some(1 + layers.len()),
    };
    if let (Some(entry_count), Some(limit)) = (entry_count, derived.legend.base.symbol_limit) {
        if fold_symbol_limit(entry_count, limit as usize).is_some() {
            return Err(unsupported(refuse!("a bar legend with more entries than its `symbol_limit:` is not supported")));
        }
    }
    match &colors {
        None if categories.keys().len() != xs.len() => {
            return Err(unsupported(refuse!("a bar with a category in more than one row is not supported")));
        }
        Some(colors) => {
            let mut cells = HashSet::new();
            if !xs.iter().zip(&colors.of_row).all(|cell| cells.insert(cell)) {
                return Err(unsupported(refuse!("a bar with a category and `color:` value in more than one row is not supported")));
            }
        }
        None => {}
    }
    let y_values = ys.iter().map(value).collect::<Result<Vec<_>, _>>()?;
    let drawn: Vec<f64> = y_values.iter().flatten().copied().collect();
    if drawn.is_empty() {
        return Err(unsupported(refuse!("a bar whose rows hold no value is not supported")));
    }
    let layer_values = layer_fields
        .iter()
        .map(|field| decoded.keys(field)?.iter().map(value).collect::<Result<Vec<_>, _>>())
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    if layer_values.iter().any(|values| values.iter().all(Option::is_none)) {
        return Err(unsupported(refuse!("a bar line layer whose rows hold no value is not supported")));
    }
    // The bars and the layers share one scale, so it spans them all.
    let spanned: Vec<f64> = drawn.iter().copied().chain(layer_values.iter().flatten().flatten().copied()).collect();

    let labels: HashMap<String, String> = chart.cartesian.labels.field_labels.clone().into_iter().collect();
    let y_zero = zero::verdict(Family::Bar { stacked: false, y_start: false, date_span: false }, p.y_ladder.zero_setting(), &spanned);
    // A layer joining the bars' scale makes it round like any shared scale (`nice_shared_scale`).
    let nice = (!layers.is_empty()).then_some(true);
    let y_axis = cartesian::measure_axis(&p.y_ladder, y_zero, &spanned, y_format, domain::Mark::VerticalBar, nice)?;
    let (axis_styles, [x_shown, y_shown]) = cartesian::axes((&p.x_ladder, p.x_title.clone(), false), (&p.y_ladder, p.y_title.clone(), false), &y_axis.domain, placed.scope, "bar", false, false)?;

    let tooltip_format = p.tooltip_format(&drawn)?;
    let locale = active_number_locale();
    let band_of: HashMap<&Key, usize> = categories.keys().iter().enumerate().map(|(band, key)| (key, band)).collect();
    // A layer's value prints in the bars' format, voted by the bars' values alone.
    let datums_of = |values: &[Option<f64>], title: &str, series_of: &dyn Fn(usize) -> usize| {
        values
            .iter()
            .enumerate()
            .map(|(row, y)| {
                let entries = match y {
                    Some(y) => vec![TooltipEntry { title: title.to_string(), value: tooltip_format.paint(&locale, *y)? }],
                    None => Vec::new(),
                };
                Ok(Datum {
                    x: Pos::Band(band_of[&xs[row]]),
                    y0: 0.0,
                    y1: y.unwrap_or(0.0),
                    series: series_of(row),
                    row: RowRef(row),
                    label: None,
                    tooltip: Tooltip { header: None, entries },
                    href: None,
                    defined: y.is_some(),
                })
            })
            .collect::<Result<Vec<_>, Diagnostic>>()
    };
    let mut datums = datums_of(&y_values, &p.y_title, &|row| colors.as_ref().map_or(0, |colors| colors.of_row[row]))?;
    // A color-split chart holding both signs paints its non-negative bars first, then the negative
    // ones: production lays the two signs down as two layers so each rounds its own tip.
    if colors.is_some() && drawn.iter().any(|y| *y > 0.0) && drawn.iter().any(|y| *y < 0.0) {
        datums.sort_by_key(|datum| datum.y1 < 0.0);
    }

    // Bars grow from a zero-anchored axis, so the rule at zero draws whenever the grid's does.
    let baselines = Baselines { zero: threshold_rules(&p.y_ladder)?, unity: false, x_threshold: false };
    let (mut series, mut legend) = match colors {
        Some(colors) => {
            let title = display_text::field_title(color_field.expect("a color series has its field"), &labels).map_err(diag::err_internal)?;
            let legend = colors.legend(title);
            (colors.series, Some(legend))
        }
        None => {
            (vec![single_series(placed, y_field, &p.y_title, &derived)?], None)
        }
    };
    let mut lines = Vec::new();
    if !layers.is_empty() {
        let canvas = hex(derived.frame.canvas);
        let mut painted = vec![hex(placed.single_series_fill()?)];
        let mut names = vec![p.y_title.clone()];
        for (layer, (field, values)) in layers.iter().zip(layer_fields.iter().zip(&layer_values)) {
            let name = match &layer.base.label {
                Some(label) => label.clone(),
                None => display_text::field_title(field, &labels).map_err(diag::err_internal)?,
            };
            if names.contains(&name) {
                return Err(unsupported(refuse!("a bar line layer named like an earlier series is not supported")));
            }
            let ink = free_ink(&painted, &palette)?;
            let stroke = color::parse_css_color(&ink).map_err(diag::err_internal)?;
            let label_ink = color::label_ink(&hex(stroke), &canvas).map_err(diag::err_internal)?;
            let index = series.len();
            lines.push(datums_of(values, &name, &|_| index)?);
            series.push(Series {
                key: SeriesKey::Layer(name.clone()),
                display: name.clone(),
                paint: Paint::Solid(stroke),
                ink: color::parse_css_color(&label_ink).map_err(diag::err_internal)?,
            });
            painted.push(ink);
            names.push(name);
        }
        let entries = series.iter().enumerate().map(|(series, s)| LegendEntry { series, label: s.display.clone() });
        legend = Some(Legend { title: None, entries: entries.collect(), toggle: None });
    }
    Ok(Some(Bars {
        x: Scale { kind: ScaleKind::Band(categories), title: x_shown },
        y: Scale { kind: ScaleKind::Linear(y_axis), title: y_shown },
        series,
        legend,
        datums,
        lines,
        baselines,
        axis_styles,
        derived,
        diagnostics: crate::restated::axes(&p.tiers, [&p.x_ladder, &p.y_ladder], placed.public_id),
    }))
}

/// `chart` bound over its run, in the style of the board body that places it. No rows is
/// [`bound::Cartesian::NoData`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(
    id: &ChartId,
    chart: &BarChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed<'_>,
    scales: &[CategoryColorScale],
) -> Result<bound::Chart, Diagnostic> {
    refuse_unported(chart, placed)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let bars = match bars(id, chart, board, run, placed, scales) {
        Err(error) if error.code == codes::ERR_BAR_Y_NOT_NUMERIC.id => {
            let family = Err(Failure { chart: id.clone(), diagnostic: error });
            return Ok(bound::Chart { title: None, subtitle: None, frame: None, family });
        }
        other => other?,
    };
    let Some(bars) = bars else {
        let derived = placed.derive()?;
        refuse_legend_values(chart, &derived)?;
        let frame = Some(derived.frame);
        return Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(no_data(placed, "bar")?)) });
    };
    let frame = Some(bars.derived.frame.clone());
    let plot = bound::Plane::new(bound::PlaneParts {
        orient: Orient::Vertical,
        x: bars.x,
        y: vec![bars.y],
        panels: Panels::Single,
        series: bars.series,
        layers: std::iter::once(bars.datums)
            .chain(bars.lines)
            .enumerate()
            .map(|(layer, datums)| MarkLayer { mark: if layer == 0 { Mark::Bars } else { Mark::Lines }, y_scale: 0, panels: vec![datums] })
            .collect(),
        naming: Naming {
            rail: None,
            entries: Vec::new(),
            asks_top_strip: false,
            authored: AuthoredLegend::Side(bars.derived.legend.clone()),
        },
        legends: bars.legend.into_iter().collect(),
        support: None,
        baselines: bars.baselines,
        axis_styles: bars.axis_styles,
        diagnostics: bars.diagnostics,
        mark_style: MarkStyle { points: None, bins: None },
    })
    .map_err(internal)?;
    Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(bound::Cartesian::Plane(Box::new(plot)))) })
}
