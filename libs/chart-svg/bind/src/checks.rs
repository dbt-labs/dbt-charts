//! The render-time warnings production reads off a chart's spec and rows after resolve:
//! `WARN-REDUNDANT-ENCODING`, `WARN-TREND-ON-BARS` and `WARN-SCATTER-STACKED-IN-COLUMNS`, and the
//! `ERR-SCATTER-MULTI-Y-NOT-NUMERIC` and scatter `ERR-ANNOTATION-INVALID` refusals resolve raises.
//! They read the authored chart and its rows, never a family's bound plot, so every family shares
//! them. Each returns the code, params and path production's detector did.

use board_model::chart::annotation::Annotation;
use board_model::chart::layer::CartesianLayer;
use board_model::chart::{CartesianChartFields, Chart, Columns, ScatterChart};
use diagnostic::{diag, py_repr, Diagnostic, ModelPath};
use serde_json::{Map, Value};

use crate::column_kind::{field_channel, y_is_numeric, ChannelType, Role};
use column_types::{ColumnTypes, Dataset};

/// Production's `_MAX_DISTINCT` and `_MIN_POINTS_PER_VALUE`.
const MAX_DISTINCT: usize = 5;
const MIN_POINTS_PER_VALUE: usize = 4;

const LEGEND_CHANNELS: [&str; 3] = ["color", "size", "shape"];
const FACET_CHANNELS: [&str; 2] = ["multiples.rows", "multiples.columns"];

fn at(id: &str, leaf: &str) -> ModelPath {
    ModelPath(vec!["charts".into(), id.into(), leaf.into()])
}

fn cartesian(chart: &Chart) -> Option<&CartesianChartFields> {
    match chart {
        Chart::Scatter(c) => Some(&c.cartesian),
        Chart::Bar(c) | Chart::Histogram(c) => Some(&c.cartesian),
        Chart::Line(c) => Some(&c.cartesian),
        Chart::Area(c) => Some(&c.cartesian),
        Chart::Heatmap(c) => Some(&c.cartesian),
        _ => None,
    }
}

/// `redundant_encoding.py`: one warning per field bound to two or more of a chart's channels.
pub fn redundant_encoding(id: &str, chart: &Chart) -> Vec<Diagnostic> {
    let mut bound: Vec<(&str, Vec<&'static str>)> = Vec::new();
    fn bind<'a>(bound: &mut Vec<(&'a str, Vec<&'static str>)>, field: &'a str, channel: &'static str) {
        match bound.iter_mut().find(|(f, _)| *f == field) {
            Some((_, channels)) => channels.push(channel),
            None => bound.push((field, vec![channel])),
        }
    }
    if let Some(c) = cartesian(chart) {
        if let Some(x) = &c.x {
            bind(&mut bound, x.as_str(), "x");
        }
        if let Some(Columns::One(y)) = &c.y {
            bind(&mut bound, y.as_str(), "y");
        }
        if let Some(m) = &c.multiples {
            if let Some(rows) = &m.rows {
                bind(&mut bound, rows, "multiples.rows");
            }
            if let Some(columns) = &m.columns {
                bind(&mut bound, columns, "multiples.columns");
            }
        }
    }
    if let Chart::Scatter(c) = chart {
        if let Some(size) = &c.size {
            bind(&mut bound, size.as_str(), "size");
        }
        if let Some(shape) = &c.shape {
            bind(&mut bound, shape.as_str(), "shape");
        }
    }
    if let Chart::Pie(c) | Chart::Donut(c) = chart {
        if let Some(theta) = &c.radial.theta {
            bind(&mut bound, theta.as_str(), "theta");
        }
    }
    let color = match chart {
        Chart::Pie(c) | Chart::Donut(c) => c.radial.color.as_ref(),
        other => cartesian(other).and_then(|c| c.color.as_ref()),
    };
    if let Some(color) = color {
        bind(&mut bound, color.as_str(), "color");
    }
    let is_bar = matches!(chart, Chart::Bar(_));
    bound
        .into_iter()
        .filter(|(_, channels)| channels.len() >= 2)
        .filter(|(_, channels)| !(is_bar && channels.len() == 2 && channels.contains(&"x") && channels.contains(&"color")))
        .filter(|(_, channels)| {
            let faceted = channels.iter().any(|c| FACET_CHANNELS.contains(c));
            !(faceted && channels.iter().all(|c| FACET_CHANNELS.contains(c) || LEGEND_CHANNELS.contains(c)))
        })
        .map(|(field, mut channels)| {
            channels.sort_unstable();
            let first = channels.remove(0);
            let shown = py_repr(&Value::String(field.into()));
            let mut warning = diag::warn_redundant_encoding(id, field, [first].iter().chain(&channels).copied().collect::<Vec<_>>().join(", "))
                .at_path(at(id, first));
            for channel in channels {
                warning = warning.relating_path(at(id, channel), format!("also bound to {shown} — this is the redundant binding"));
            }
            warning
        })
        .collect()
}

/// The mark a trend annotation fits: the one plotting its `y_column`, else the chart's own. A
/// column no mark plots is resolve's `ERR-ANNOTATION-INVALID`, not this warning.
fn is_bar_trend(chart: &Chart, y_column: Option<&str>) -> bool {
    let (family_bar, y, layers) = match chart {
        Chart::Bar(c) | Chart::Histogram(c) => (true, &c.cartesian.y, &c.layers),
        Chart::Line(c) => (false, &c.cartesian.y, &c.layers),
        Chart::Area(c) => (false, &c.cartesian.y, &c.layers),
        Chart::Scatter(c) => (false, &c.cartesian.y, &c.layers),
        _ => return false,
    };
    let Some(column) = y_column else { return family_bar };
    let plotted = match y {
        Some(Columns::One(y)) => vec![y.as_str()],
        Some(Columns::Many(ys)) => ys.iter().map(|y| y.as_str()).collect(),
        None => Vec::new(),
    };
    if plotted.contains(&column) {
        return family_bar;
    }
    fn layer_y(layer: &CartesianLayer) -> (bool, Option<&str>) {
        match layer {
            CartesianLayer::Bar(l) => (true, l.base.y.as_deref()),
            CartesianLayer::Line(l) => (false, l.base.y.as_deref()),
            CartesianLayer::Area(l) => (false, l.base.y.as_deref()),
            CartesianLayer::Scatter(l) => (false, l.base.y.as_deref()),
        }
    }
    layers.iter().map(layer_y).find(|(_, y)| *y == Some(column)).is_some_and(|(bar, _)| bar)
}

/// `trend_on_bars.py`: one warning per `trend` annotation that fits a bar mark.
pub fn trend_on_bars(id: &str, chart: &Chart) -> Vec<Diagnostic> {
    let annotations: &[Annotation] = match chart {
        Chart::Bar(c) | Chart::Histogram(c) => &c.annotations,
        Chart::Line(c) => &c.annotations,
        Chart::Area(c) => &c.annotations,
        Chart::Scatter(c) => &c.annotations,
        _ => return Vec::new(),
    };
    annotations
        .iter()
        .enumerate()
        .filter_map(|(index, a)| match a {
            Annotation::Trend(t) => Some((index, t)),
            _ => None,
        })
        .filter(|(_, t)| is_bar_trend(chart, t.base.y_column.as_ref().map(|c| c.as_str())))
        .map(|(index, _)| diag::warn_trend_on_bars(id, index))
        .collect()
}

/// `scatter_stacked_in_columns.py`: a numeric x or y of at most five distinct values with four
/// or more points each draws columns of dots.
pub fn stacked_in_columns(id: &str, chart: &ScatterChart, rows: &[Map<String, Value>]) -> Vec<Diagnostic> {
    let y = match &chart.cartesian.y {
        Some(Columns::One(y)) => Some(y.as_str()),
        _ => None,
    };
    [("x", chart.cartesian.x.as_ref().map(|x| x.as_str())), ("y", y)]
        .into_iter()
        .filter_map(|(channel, field)| Some((channel, field?)))
        .filter_map(|(channel, field)| {
            let mut values: Vec<f64> = rows
                .iter()
                .filter_map(|row| row.get(field)?.as_f64())
                .map(|v| if v == 0.0 { 0.0 } else { v })
                .collect();
            let points = values.len();
            values.sort_by(f64::total_cmp);
            values.dedup();
            let distinct = values.len();
            let stacked = 0 < distinct && distinct <= MAX_DISTINCT && points >= MIN_POINTS_PER_VALUE * distinct;
            stacked.then(|| {
                diag::warn_scatter_stacked_in_columns(channel, distinct, points)
                    .at_path(at(id, channel))
                    .with_sink_param("chart_id", id.into())
                    .with_sink_param("field", field.into())
            })
        })
        .collect()
}

/// `resolve/chart/scatter.py`'s refusals: a non-numeric measure in a list `y`, and an annotation
/// on a temporal `y`. `Ok(Some(_))` is production's error for the chart; `Err` is a column the
/// engine cannot classify.
pub fn scatter_invalid(id: &str, chart: &ScatterChart, types: &ColumnTypes, rows: &[Map<String, Value>]) -> Result<Option<Diagnostic>, Diagnostic> {
    match &chart.cartesian.y {
        Some(Columns::Many(ys)) => {
            for y in ys {
                if !y_is_numeric(types, rows, y.as_str())? {
                    return Ok(Some(diag::err_scatter_multi_y_not_numeric(id, y.as_str())));
                }
            }
        }
        Some(Columns::One(y)) if !chart.annotations.is_empty() && field_channel(types, rows, y.as_str(), Role::Free)? == ChannelType::Temporal => {
            return Ok(Some(diag::err_annotation_invalid(id, 0, "annotations on a scatter need a numeric or category y")));
        }
        _ => {}
    }
    Ok(None)
}

/// Every warning production's render-time detectors raise for the chart `chart`, named `id`,
/// over the `dataset` it reads, if it reads one.
pub fn of(id: &str, chart: &Chart, dataset: Option<&Dataset>) -> Vec<Diagnostic> {
    let mut out = redundant_encoding(id, chart);
    out.extend(trend_on_bars(id, chart));
    if let (Chart::Scatter(scatter), Some(dataset)) = (chart, dataset) {
        out.extend(stacked_in_columns(id, scatter, &dataset.rows));
    }
    out
}
