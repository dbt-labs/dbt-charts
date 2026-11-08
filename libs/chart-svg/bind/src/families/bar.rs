//! Bar: one vertical bar per row on a band x and a quantitative y, from the zero baseline.
//! Production's laws composed: the band order (`plot::band`), the zero-anchored value domain
//! (`plot::zero`, `plot::domain`), the axis and tooltip formats (`formats`) and the zero-rule
//! gate. A row with a null y keeps its category's band and draws no bar. What the bind does not
//! model yet refuses by name.

use std::collections::HashMap;

use board_model::board::{Board, BoardBody};
use board_model::chart::{BarChart, Columns};
use board_model::ids::ChartId;
use board_model::style::bar::{Orientation, StackMode};
use board_model::style::marks::LabelVisible;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::Engine;

use crate::channel_type::{classify, ChannelType};
use crate::decode::decode;
use crate::derived::DerivedChart;
use crate::families::cartesian::{self, axis_format, count, hex, plain_text, threshold_rules, unsupported, value};
use crate::formats::locale::active_number_locale;
use crate::formats::{column_format, AuthoredFormat, Formats, Kind, ResolvedFormat, Tiers};
use crate::model::{
    AuthoredLegend, AxisStyles, Baselines, Body, BoundChart, Datum, Key, Mark, MarkLayer, MarkStyle, Naming, Orient, Paint,
    Panels, Plot, PlotParts, Pos, RowRef, Scale, ScaleKind, Series, SeriesKey, Tooltip, TooltipEntry, ValueAxis, Zero,
};
use crate::plot::band::band_domain;
use crate::plot::domain;
use crate::plot::zero::{self, Family};
use crate::run::RunResult;
use crate::style::axis::{AxisLadder, AxisName};
use crate::style::{Tier, Tiers as StyleTiers};

/// What a bar chart's laws bind: the two scales, the one series and a datum per row.
#[derive(Debug)]
pub struct Bars {
    pub x: Scale,
    pub y: Scale,
    pub series: Series,
    /// One per row, in row order. A row with a null y is `defined: false`.
    pub datums: Vec<Datum>,
    pub baselines: Baselines,
}

fn internal(invalid: crate::model::Invalid) -> Diagnostic {
    diag::err_internal(format!("bar is invalid: {invalid:?}"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &BarChart) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    if fields.color.is_some() {
        return Err(unsupported(refuse!("a bar with `color:` is not supported")));
    }
    if matches!(fields.y, Some(Columns::Many(_))) {
        return Err(unsupported(refuse!("a bar with a list `y:` is not supported")));
    }
    if chart.style.stack != StackMode::None {
        return Err(unsupported(refuse!("a stacked bar is not supported")));
    }
    if !chart.layers.is_empty() {
        return Err(unsupported(refuse!("a bar with `layers:` is not supported")));
    }
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

/// The value axis over `values`, baked as production bakes a vertical bar's y: headroom, count
/// and the step floor of its format.
fn value_axis(ladder: &AxisLadder<'_>, zero: Zero, values: &[f64], format: ResolvedFormat) -> Result<ValueAxis, Diagnostic> {
    let axis = domain::Axis {
        zero,
        headroom: ladder.headroom().unwrap_or(0.0),
        count: count(ladder)?,
        values: None,
        domain: None,
        min_step: d3rs::numeral_scale::tick_min_step_for_format(&format.spec),
        independent: false,
        nice: None,
        mark: domain::Mark::VerticalBar,
    };
    let domain = cartesian::linear_domain(&axis, values, &format, true, "y domain")?;
    Ok(ValueAxis { domain, format })
}

/// The laws of a bar chart over its rows; `None` when the query returned no rows.
pub fn bars(
    id: &ChartId,
    chart: &BarChart,
    board: &Board,
    body: &BoardBody,
    run: &RunResult,
    derived: &DerivedChart,
) -> Result<Option<Bars>, Diagnostic> {
    let style: &Style = body
        .style
        .as_deref()
        .ok_or_else(|| diag::err_internal("a normalized board carries its total style"))?;
    if !style.formats.is_empty() {
        return Err(unsupported(refuse!("a bar on a board with `style.formats` aliases is not supported")));
    }
    let formats = Formats::new(None);
    let x_field = chart
        .cartesian
        .x
        .as_ref()
        .ok_or_else(|| diag::err_internal("normalize requires a bar's `x:`"))?
        .as_str();
    let y_field = match &chart.cartesian.y {
        Some(Columns::One(y)) => y.as_str(),
        _ => return Err(diag::err_internal("normalize requires a bar's `y:`")),
    };
    let tiers = StyleTiers { own: Tier::from(&chart.style), family: Tier::from(&*style.charts.bar), charts: &style.charts };
    let x_ladder = AxisLadder::new(AxisName::X, ChannelType::Nominal, &tiers, None, false)?;
    let y_ladder = AxisLadder::new(AxisName::Y, ChannelType::Quantitative, &tiers, None, false)?;
    if let Some(leaf) = x_ladder.unwalked_leaf().or_else(|| y_ladder.unwalked_leaf()) {
        return Err(unsupported(refuse!("a bar axis that sets `{leaf}` is not supported")));
    }
    if x_ladder.labels_format().is_some() {
        return Err(unsupported(refuse!("a bar band axis with a label format is not supported")));
    }
    let y_format = axis_format(&y_ladder, formats)?;
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[x_field, y_field])?;
    if dataset.rows.is_empty() {
        return Ok(None);
    }
    if !dataset.schema.get(y_field).map_err(Diagnostic::refused)?.is_number() {
        return Err(unsupported(format!("bar: y column {y_field:?} is not numeric")));
    }
    match classify(&dataset.schema, &dataset.rows, Some(x_field), true)? {
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
    if categories.keys().len() != xs.len() {
        return Err(unsupported(refuse!("a bar with a category in more than one row is not supported")));
    }
    let y_values = ys.iter().map(value).collect::<Result<Vec<_>, _>>()?;
    let drawn: Vec<f64> = y_values.iter().flatten().copied().collect();
    if drawn.is_empty() {
        return Err(unsupported(refuse!("a bar whose rows hold no value is not supported")));
    }

    let labels: HashMap<String, String> = chart.cartesian.labels.field_labels.clone().into_iter().collect();
    let x_title = display_text::field_title(x_field, &labels).map_err(diag::err_internal)?;
    let y_title = display_text::field_title(y_field, &labels).map_err(diag::err_internal)?;

    let y_zero = zero::verdict(Family::Bar { stacked: false, y_start: false, date_span: false }, None, &drawn);
    let y_axis = value_axis(&y_ladder, y_zero, &drawn, y_format)?;

    let board_tooltip = AuthoredFormat::named(style.charts.tooltip.format.as_str());
    let tooltip_format = column_format(Kind::Number, Tiers { own: Some(&board_tooltip), ..Tiers::default() }, formats, None, None, &drawn)?;
    let locale = active_number_locale();
    // Every category is in one row, and the band domain keeps them in row order: a row's band is
    // its row.
    let datums = y_values
        .iter()
        .enumerate()
        .map(|(row, y)| {
            let entries = match y {
                Some(y) => vec![TooltipEntry { title: y_title.clone(), value: tooltip_format.paint(&locale, *y)? }],
                None => Vec::new(),
            };
            Ok(Datum {
                x: Pos::Band(row),
                y0: 0.0,
                y1: y.unwrap_or(0.0),
                series: 0,
                row: RowRef(row),
                label: None,
                tooltip: Tooltip { entries },
                href: None,
                defined: y.is_some(),
            })
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;

    // Bars grow from a zero-anchored axis, so the rule at zero draws whenever the grid's does.
    let baselines = Baselines { zero: threshold_rules(&y_ladder)?, unity: false, x_threshold: false };
    let fill = derived.single_series_fill;
    let ink = color::label_ink(&hex(fill), &hex(derived.frame.canvas)).map_err(diag::err_internal)?;
    let series = Series {
        key: SeriesKey::Measure(y_field.to_string()),
        display: y_title.clone(),
        paint: Paint::Solid(fill),
        ink: color::parse_css_color(&ink).map_err(diag::err_internal)?,
    };
    Ok(Some(Bars {
        x: Scale { kind: ScaleKind::Band(categories), title: Some(x_title) },
        y: Scale { kind: ScaleKind::Linear(y_axis), title: Some(y_title) },
        series,
        datums,
        baselines,
    }))
}

/// `chart` bound over its run, in the style of `body`, the board body that places it. No rows is
/// [`Body::Empty`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(
    id: &ChartId,
    chart: &BarChart,
    board: &Board,
    body: &BoardBody,
    run: &RunResult,
    derived: &DerivedChart,
) -> Result<BoundChart, Diagnostic> {
    refuse_unported(chart)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let frame = Some(derived.frame.clone());
    let Some(bars) = bars(id, chart, board, body, run, derived)? else {
        return Ok(BoundChart { title, subtitle, frame, body: Body::Empty });
    };
    let plot = Plot::new(PlotParts {
        orient: Orient::Vertical,
        x: bars.x,
        y: vec![bars.y],
        panels: Panels::Single,
        series: vec![bars.series],
        layers: vec![MarkLayer { mark: Mark::Bars, y_scale: 0, panels: vec![bars.datums] }],
        naming: Naming {
            rail: None,
            entries: Vec::new(),
            asks_top_strip: false,
            authored: AuthoredLegend::Side(derived.legend.clone()),
        },
        legends: Vec::new(),
        support: None,
        baselines: bars.baselines,
        axis_styles: AxisStyles {},
        mark_style: MarkStyle { points: None },
    })
    .map_err(internal)?;
    Ok(BoundChart { title, subtitle, frame, body: Body::Plot(Box::new(plot)) })
}
