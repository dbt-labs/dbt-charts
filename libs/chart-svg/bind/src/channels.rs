//! A chart's data-bound style channels: `color:` on the families that take one and
//! a KPI's `background:`. `channel.py::normalize_chart_channels`, `_channels.py::
//! _flag_quantitative_color` / `flag_categorical_color` and `_wide_fields.py::
//! resolve_wide_measure_channels`, over the seam's typed charts.
//!
//! Every channel the seam can carry is a bare column name. It is a series channel, or,
//! when the chart's own `style.color.gradient` is set, a gradient channel carrying that
//! scale (the gradient's stops are baked downstream, where the palette is known). What
//! authors a literal or conditional channel never reaches here as a string and is
//! refused by name; conditional formatting belongs to the table and KPI family binds.

use std::collections::HashSet;

use board_types::{
    AreaChartY, BarChartType, BarChartY, BoardChartStylesValue, KpiChartBackground, LayoutItemChart,
    LineChartY, ScaleTargetConfig, ScatterChartY,
};
use column_types::Dataset;
use diagnostic::Diagnostic;
use serde_json::Value;

use crate::wide::{WIDE_LABEL_FIELD, WIDE_SERIES_SEPARATOR};

/// How many rows decide whether a color column is a magnitude ramp.
const SAMPLE_ROWS: usize = 10;

/// How a channel paints from its field.
#[derive(Debug, Clone)]
pub enum Mode {
    Series,
    /// A continuous scale the chart's `style.color.gradient` authors.
    Gradient(ScaleTargetConfig),
}

/// A channel bound to one data field.
#[derive(Debug, Clone)]
pub struct Channel {
    pub data_field: String,
    pub mode: Mode,
    /// A series channel's sampled values are all numbers: a continuous ramp, not
    /// categories. Only the `color` channel carries it.
    pub quantitative_data: bool,
    /// A board-wide category scale binds this series channel's field.
    pub categorical: bool,
}

#[derive(Debug, Default)]
pub struct Channels {
    pub color: Option<Channel>,
    pub background: Option<Channel>,
}

/// The gradient a chart's own `style.color` sets (`None`: it sets none).
pub fn style_gradient(style: &BoardChartStylesValue) -> Option<&ScaleTargetConfig> {
    use BoardChartStylesValue as S;
    match style {
        S::BarStyle(s) => s.gradient.as_ref(),
        S::HistogramStyle(s) => s.gradient.as_ref(),
        S::LineStyle(s) => s.gradient.as_ref(),
        S::AreaStyle(s) => s.gradient.as_ref(),
        S::ScatterStyle(s) => s.gradient.as_ref(),
        S::HeatmapStyle(s) => s.gradient.as_ref(),
        S::PieStyle(s) => s.gradient.as_ref(),
        S::KpiStyle(s) => s.gradient.as_ref(),
        S::TableStyle(s) => s.gradient.as_ref(),
        S::GeoshapeStyle(s) => s.gradient.as_ref(),
        S::PointMapStyle(s) => s.gradient.as_ref(),
        S::SparkBarStyle(s) => s.gradient.as_ref(),
        S::CalloutStyle(s) => s.gradient.as_ref(),
        S::CustomStyle(s) => s.gradient.as_ref(),
    }
}

/// What a chart authors of its channels.
struct Authored<'a> {
    color: Option<&'a str>,
    /// A wide `y: [...]` on a family that folds it.
    wide_measures: Option<&'a [String]>,
    has_layers: bool,
    background: Option<&'a KpiChartBackground>,
}

fn authored(chart: &LayoutItemChart) -> Authored<'_> {
    macro_rules! cartesian {
        ($c:expr, $wide:ident, $folds:expr) => {
            Authored {
                color: $c.color.as_deref(),
                wide_measures: match &$c.y {
                    $wide::Array(measures) if $folds => Some(measures.as_slice()),
                    _ => None,
                },
                has_layers: !$c.layers.is_empty(),
                background: None,
            }
        };
    }
    macro_rules! colored {
        ($c:expr) => {
            Authored { color: $c.color.as_deref(), wide_measures: None, has_layers: false, background: None }
        };
    }
    match chart {
        LayoutItemChart::BarChart(c) => cartesian!(c, BarChartY, c.type_ == BarChartType::Bar),
        LayoutItemChart::LineChart(c) => cartesian!(c, LineChartY, true),
        LayoutItemChart::AreaChart(c) => cartesian!(c, AreaChartY, true),
        LayoutItemChart::ScatterChart(c) => cartesian!(c, ScatterChartY, true),
        LayoutItemChart::HeatmapChart(c) => colored!(c),
        LayoutItemChart::PieChart(c) => colored!(c),
        LayoutItemChart::PointMapChart(c) => colored!(c),
        LayoutItemChart::GeoshapeChart(c) => colored!(c),
        LayoutItemChart::SparkBarChart(c) => colored!(c),
        LayoutItemChart::KpiChart(c) => Authored {
            color: None,
            wide_measures: None,
            has_layers: false,
            background: Some(&c.background),
        },
        LayoutItemChart::TableChart(_)
        | LayoutItemChart::CalloutChart(_)
        | LayoutItemChart::CustomChart(_) => {
            Authored { color: None, wide_measures: None, has_layers: false, background: None }
        }
    }
}

/// `_flag_quantitative_color`'s test (`core/utils.py::vega_infers_quantitative`): the
/// first rows' present, non-null cells are all numbers or booleans. A field absent
/// from the first row is not; an all-null sample is, vacuously.
fn infers_quantitative(dataset: &Dataset, field: &str) -> bool {
    if !dataset.rows.first().is_some_and(|row| row.contains_key(field)) {
        return false;
    }
    dataset
        .rows
        .iter()
        .take(SAMPLE_ROWS)
        .filter_map(|row| row.get(field))
        .filter(|cell| !cell.is_null())
        .all(|cell| matches!(cell, Value::Number(_) | Value::Bool(_)))
}

/// `channel.py::_validate_channel_field` against the declared columns: a name the
/// schema lacks is an error, and a `#`-prefixed one reads as a literal color typo.
fn validate_field(channel: &str, field: &str, dataset: &Dataset) -> Result<(), Diagnostic> {
    if dataset.schema.0.contains_key(field) {
        return Ok(());
    }
    if field.starts_with('#') {
        return Err(Diagnostic::refused(format!(
            "Channel '{channel}': '{field}' looks like a literal color; use {{{channel}: {{value: \"{field}\"}}}}"
        )));
    }
    let mut available: Vec<&str> = dataset.schema.0.keys().map(String::as_str).collect();
    available.sort_unstable();
    Err(Diagnostic::refused(format!(
        "Channel '{channel}' column '{field}' not found. Available: {available:?}"
    )))
}

/// The channels `chart` binds over its `dataset`. `categorical_fields` are the
/// fields the board's category scales bind; `gradient` is the chart's own
/// `style.color.gradient`, which turns its `color:` field into a gradient channel
/// (geo charts ignore it, as the seam carries none for them).
pub fn bind_channels(
    chart: &LayoutItemChart,
    dataset: &Dataset,
    categorical_fields: &HashSet<&str>,
    gradient: Option<&ScaleTargetConfig>,
) -> Result<Channels, Diagnostic> {
    let authored = authored(chart);
    let mut channels = Channels::default();
    let color = authored.color;
    if color.is_none() && gradient.is_some() {
        return Err(Diagnostic::refused(
            "style.color: {gradient: ...} requires chart.color to name a data field",
        ));
    }
    if let Some(field) = color {
        // An empty field names no column: Python skips validation and sampling for it.
        if !field.is_empty() {
            validate_field("color", field, dataset)?;
        }
        channels.color = Some(match gradient {
            Some(scale) => Channel {
                data_field: field.to_string(),
                mode: Mode::Gradient(scale.clone()),
                quantitative_data: false,
                categorical: false,
            },
            None => Channel {
                data_field: field.to_string(),
                mode: Mode::Series,
                quantitative_data: !field.is_empty() && infers_quantitative(dataset, field),
                categorical: categorical_fields.contains(field),
            },
        });
    }
    match authored.background {
        None | Some(KpiChartBackground::Null) => {}
        Some(KpiChartBackground::String(field)) => {
            validate_field("background", field, dataset)?;
            channels.background = Some(Channel {
                data_field: field.clone(),
                mode: Mode::Series,
                quantitative_data: false,
                categorical: false,
            });
        }
        Some(KpiChartBackground::Object(_)) => {
            return Err(Diagnostic::refused(
                "background channel literal/gradient/conditional binding is not bound",
            ));
        }
    }
    if let Some(measures) = authored.wide_measures {
        if gradient.is_some() {
            return Err(Diagnostic::refused(
                "wide y: [...] combined with a gradient color is not bound (ERR_MULTI_Y_COLOR_CONFLICT)",
            ));
        }
        if authored.has_layers {
            return Err(Diagnostic::refused(
                "wide y: [...] combined with chart.layers is not bound (ERR_MULTI_Y_LAYERS_CONFLICT)",
            ));
        }
        if authored.color.is_some() {
            if let Some(measure) = measures.iter().find(|m| m.contains(WIDE_SERIES_SEPARATOR)) {
                return Err(Diagnostic::refused(format!(
                    "measure {measure:?} contains the wide-series separator {WIDE_SERIES_SEPARATOR:?} \
                     (ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR)"
                )));
            }
        }
        channels.color = Some(Channel {
            data_field: WIDE_LABEL_FIELD.to_string(),
            mode: Mode::Series,
            quantitative_data: false,
            categorical: false,
        });
    }
    Ok(channels)
}
