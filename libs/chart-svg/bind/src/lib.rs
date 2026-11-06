//! The bind stage: a normalized board plus its datasets to a bound board.
//! Everything data-derived and size-independent; no text is measured and no
//! node is built, which is why this crate depends on neither `measure` nor `scene`.
//!
//! Deserializing a real `board_types::Board` recurses deeply (its untagged unions
//! overflow an 8 MiB stack): deserialize on a thread with a large stack.
//! `bind_board` itself only walks the typed tree.

pub mod axis;
pub mod category_colors;
pub mod channel_type;
pub mod channels;
pub mod column_kind;
pub mod decode;
pub mod formats;
pub mod js_date;
pub mod label_template;
pub mod layers;
pub mod legend;
pub mod model;
pub mod multiples;
pub mod pattern;
pub mod plot;
pub mod rows;
pub mod stack;
pub mod temporal;
pub mod time_axis;
pub mod time_locale;
pub mod time_zone;
pub mod walk;
pub mod wide;
pub mod x_type;

use std::collections::HashSet;

use board_types::{Board, BarChartType, LayoutItemChart};
use column_types::QueryDatasets;
use channels::{bind_channels, style_gradient, Channels};
use category_colors::{
    observe, plan_category_colors, CategoryColorScale, ChartFieldValues, Observed, Pins,
};
use diagnostic::Diagnostic;
use indexmap::IndexMap;

/// What `bind` decided about the whole board.
#[derive(Debug)]
pub struct BoundBoard {
    /// One scale per field bound across charts, in planning order.
    pub category_colors: Vec<CategoryColorScale>,
    /// Each placed chart's bound channels, by chart id. A chart whose query returned
    /// no dataset is absent. A chart whose channels cannot bind carries its own error
    /// and leaves the board bound, as a chart that fails to resolve draws an error tile.
    pub channels: IndexMap<String, Result<Channels, Diagnostic>>,
}

/// Binds a normalized board to the rows its queries returned and their declared
/// column types. A chart's color column that a dataset does not declare is an error.
pub fn bind_board(board: &Board, data: &QueryDatasets) -> Result<BoundBoard, Diagnostic> {
    let pins = authored_pins(board)?;
    let mut observations: Vec<ChartFieldValues> = Vec::new();
    let mut disqualified: HashSet<&str> = HashSet::new();
    let charts = placed_charts(board);
    for chart in charts.iter().filter_map(|(_, chart)| encoding(chart)) {
        let Some(dataset) = chart.query.and_then(|query| data.get(query)) else {
            continue;
        };
        match observe(dataset, chart.field, pins.contains_key(chart.field))? {
            Observed::Disqualified => {
                disqualified.insert(chart.field);
            }
            Observed::Values(values) if !values.is_empty() => {
                observations.push(IndexMap::from([(chart.field.to_string(), values)]));
            }
            Observed::Values(_) => {}
        }
    }
    let palette = &board.resolved_style.chart_defaults.palette;
    let mut scales = plan_category_colors(&observations, &pins, palette, &IndexMap::new())?;
    // A scale that cannot seat what one chart draws cannot safely bind for any chart.
    scales.retain(|field, _| !disqualified.contains(field.as_str()));
    let category_colors: Vec<CategoryColorScale> = scales.into_values().collect();
    let categorical: HashSet<&str> = category_colors.iter().map(|scale| scale.field.as_str()).collect();
    let mut channels = IndexMap::new();
    for (owner, chart) in charts {
        let (id, query) = identity(chart);
        if let Some(dataset) = query.and_then(|query| data.get(query)) {
            // A chart whose style named an unknown color has no merged style, and
            // its resolve reports that error on the chart alone.
            let bound = match owner.chart_styles.get(id) {
                Some(style) => bind_channels(chart, dataset, &categorical, style_gradient(style)),
                None => Err(Diagnostic::refused(format!("chart {id} has no merged style"))),
            };
            channels.insert(id.to_string(), bound);
        }
    }
    Ok(BoundBoard { category_colors, channels })
}

/// `style.charts.category_colors` of the root board and every nested board.
/// A nested board's field keeps its place; the root wins on a conflict.
fn authored_pins(board: &Board) -> Result<Pins, Diagnostic> {
    let mut pins = Pins::new();
    for item in &board.layout.items {
        if let Some(nested) = &item.board {
            pins.extend(authored_pins(nested)?);
        }
    }
    for (field, binding) in &board.chart_tiers.category_color_pins {
        let values = binding.values.iter().map(|(v, c)| (v.clone(), c.to_string()));
        pins.insert(field.clone(), values.collect());
    }
    Ok(pins)
}

/// A chart that can bind a color scale and the field it names a category with.
struct Encoding<'a> {
    query: Option<&'a str>,
    field: &'a str,
}

/// The charts the board renders, in layout order, one per chart, each with the board
/// whose style it merges from: a chart placed twice draws the same values both times,
/// and counting it twice would carry a single-chart board over the two-chart threshold.
fn placed_charts(board: &Board) -> Vec<(&Board, &LayoutItemChart)> {
    fn walk<'a>(board: &'a Board, out: &mut Vec<(&'a Board, &'a LayoutItemChart)>) {
        for item in &board.layout.items {
            if let Some(chart) = &item.chart {
                if !out.iter().any(|(_, seen)| identity(seen).0 == identity(chart).0) {
                    out.push((board, chart));
                }
            }
            if let Some(nested) = &item.board {
                walk(nested, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(board, &mut out);
    out
}

/// A chart's id and the query it reads.
pub fn identity(chart: &LayoutItemChart) -> (&str, Option<&str>) {
    macro_rules! of {
        ($($variant:ident),*) => {
            match chart {
                // A callout reads no query.
                LayoutItemChart::CalloutChart(c) => (c.id.as_str(), None),
                $(LayoutItemChart::$variant(c) => (c.id.as_str(), c.query_name.as_deref()),)*
            }
        };
    }
    of!(
        BarChart, LineChart, AreaChart, ScatterChart, HeatmapChart, PieChart, KpiChart, TableChart,
        PointMapChart, GeoshapeChart, SparkBarChart, CustomChart
    )
}

/// Only an authored `color:` channel names a category, and only on the families
/// whose marks paint from a board scale (a histogram's do not; a choropleth's
/// `value:` fills it, so its color names nothing).
fn encoding(chart: &LayoutItemChart) -> Option<Encoding<'_>> {
    macro_rules! color {
        ($c:expr) => {
            Some(Encoding {
                query: $c.query_name.as_deref(),
                field: $c.color.as_deref().filter(|field| !field.is_empty())?,
            })
        };
    }
    match chart {
        LayoutItemChart::BarChart(c) if c.type_ == BarChartType::Bar => color!(c),
        LayoutItemChart::LineChart(c) => color!(c),
        LayoutItemChart::AreaChart(c) => color!(c),
        LayoutItemChart::ScatterChart(c) => color!(c),
        LayoutItemChart::HeatmapChart(c) => color!(c),
        LayoutItemChart::PieChart(c) => color!(c),
        LayoutItemChart::PointMapChart(c) => color!(c),
        LayoutItemChart::GeoshapeChart(c) if c.value.as_deref().is_none_or(str::is_empty) => {
            color!(c)
        }
        _ => None,
    }
}
