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

use board_model::chart::kpi::KpiBackground;
use board_model::chart::layer::CartesianLayer;
use board_model::chart::{CartesianChartFields, Chart, ColorColumn, Columns};
use board_model::facet::Channel as ColumnName;
use board_model::primitives::ScaleTargetConfig;
use board_model::style::style::Style;
use model_core::Patch;
use column_types::Dataset;
use diagnostic::Diagnostic;
use serde_json::Value;

use crate::style::set;
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

/// The gradient a chart's own `style.color` authors (`None`: it authors none). `own` is the
/// chart's `style:` alone as a board-style layer; the scale is the chart's total one, which
/// folds the authored gradient over the theme's. The theme's family-level gradient alone is not
/// the chart's: a heatmap that authors none binds its `color:` as a series, as Python does. A
/// geo family's gradient paints its `value:`, not a `color:` channel, and a family with no
/// `color:` channel has no color style.
pub fn style_gradient<'a>(chart: &'a Chart, own: &Patch<Style>) -> Option<&'a ScaleTargetConfig> {
    let charts = &own.charts;
    let (authored, total) = match chart {
        Chart::Bar(c) | Chart::Histogram(c) => (&charts.bar.cartesian.painted.color.base.gradient, &c.style.cartesian.painted.color.base.gradient),
        Chart::Line(c) => (&charts.line.cartesian.painted.color.base.gradient, &c.style.cartesian.painted.color.base.gradient),
        Chart::Area(c) => (&charts.area.cartesian.painted.color.base.gradient, &c.style.cartesian.painted.color.base.gradient),
        Chart::Scatter(c) => (&charts.scatter.cartesian.painted.color.base.gradient, &c.style.cartesian.painted.color.base.gradient),
        Chart::Heatmap(c) => (&charts.heatmap.cartesian.painted.color.base.gradient, &c.style.cartesian.painted.color.base.gradient),
        Chart::Pie(c) | Chart::Donut(c) => (&charts.pie.color.base.gradient, &c.style.color.base.gradient),
        Chart::Geoshape(_) | Chart::Map(_) | Chart::PointMap(_) | Chart::BubbleMap(_) | Chart::Kpi(_) | Chart::SparkBar(_) | Chart::Table(_) | Chart::Callout(_) | Chart::Custom(_) => return None,
    };
    set(authored).and(total.as_ref())
}

/// What a chart authors of its channels.
struct Authored<'a> {
    color: Option<&'a str>,
    /// A wide `y: [...]` on a family that folds it.
    wide_measures: Option<Vec<&'a str>>,
    has_layers: bool,
    background: Option<&'a KpiBackground>,
}

fn authored(chart: &Chart) -> Authored<'_> {
    fn cartesian<'a>(
        fields: &'a CartesianChartFields,
        layers: &'a [CartesianLayer],
        folds: bool,
    ) -> Authored<'a> {
        Authored {
            color: fields.color.as_ref().map(ColorColumn::as_str),
            wide_measures: match &fields.y {
                Some(Columns::Many(measures)) if folds => {
                    Some(measures.iter().map(ColumnName::as_str).collect())
                }
                _ => None,
            },
            has_layers: !layers.is_empty(),
            background: None,
        }
    }
    fn colored(color: &Option<ColorColumn>) -> Authored<'_> {
        Authored {
            color: color.as_ref().map(ColorColumn::as_str),
            wide_measures: None,
            has_layers: false,
            background: None,
        }
    }
    match chart {
        Chart::Bar(c) => cartesian(&c.cartesian, &c.layers, true),
        Chart::Histogram(c) => cartesian(&c.cartesian, &c.layers, false),
        Chart::Line(c) => cartesian(&c.cartesian, &c.layers, true),
        Chart::Area(c) => cartesian(&c.cartesian, &c.layers, true),
        Chart::Scatter(c) => cartesian(&c.cartesian, &c.layers, true),
        Chart::Heatmap(c) => colored(&c.cartesian.color),
        Chart::Pie(c) | Chart::Donut(c) => colored(&c.radial.color),
        Chart::Geoshape(c) | Chart::Map(c) => colored(&c.geo.color),
        Chart::PointMap(c) | Chart::BubbleMap(c) => colored(&c.geo.color),
        Chart::Kpi(c) => Authored {
            color: None,
            wide_measures: None,
            has_layers: false,
            background: c.background.as_ref(),
        },
        Chart::SparkBar(_) | Chart::Table(_) | Chart::Callout(_) | Chart::Custom(_) => Authored {
            color: None,
            wide_measures: None,
            has_layers: false,
            background: None,
        },
    }
}

/// `_flag_quantitative_color`'s test (`core/utils.py::vega_infers_quantitative`): the
/// first rows' present, non-null cells are all numbers or booleans. A field absent
/// from the first row is not; an all-null sample is, vacuously.
fn infers_quantitative(dataset: &Dataset, field: &str) -> bool {
    if !dataset
        .rows
        .first()
        .is_some_and(|row| row.contains_key(field))
    {
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
            "Channel '{channel}': '{field}' looks like a literal color, but a channel binds a data column. Set a literal color in the chart's style:\n  style:\n    color:\n      static: \"{field}\""
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
/// .
pub fn bind_channels(
    chart: &Chart,
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
        None => {}
        Some(KpiBackground::Column(field)) => {
            validate_field("background", field, dataset)?;
            channels.background = Some(Channel {
                data_field: field.clone(),
                mode: Mode::Series,
                quantitative_data: false,
                categorical: false,
            });
        }
        Some(KpiBackground::Channel(_)) => {
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
