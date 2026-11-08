//! The bind stage: a normalized board plus its datasets to a bound board.
//! Everything data-derived and size-independent; no text is measured and no
//! node is built, which is why this crate depends on neither `measure` nor `scene`.
//!
//! `bind_board` walks the spine's `board_model::Board`.

pub mod board;
pub mod callout;
pub mod category_colors;
pub mod channel_type;
pub mod channels;
pub mod column_kind;
pub mod decode;
pub mod derived;
pub mod display_text;
pub mod failed;
pub mod families;
pub mod font_stack;
pub mod formats;
pub mod kpi;
pub mod label_template;
pub mod layers;
pub mod legend;
pub mod model;
pub mod multiples;
pub mod pattern;
pub mod plot;
pub mod rows;
pub mod run;
pub mod stack;
pub mod style;
pub mod table_dates;
pub mod temporal;
pub mod time_axis;
pub mod time_locale;
pub mod time_zone;
pub mod variables;
pub mod walk;
pub mod wide;
pub mod x_type;

use std::collections::HashSet;

pub use board::bind;

use board_model::board::{items, Board, BoardBody, LayoutItem};
use board_model::chart::{Chart, ChartBase, ChartQuery, ColorColumn};
use board_model::ids::{ChartId, ItemId};
use board_model::primitives::PaletteChoice;
use category_colors::{
    observe, plan_category_colors, CategoryColorScale, ChartFieldValues, Observed, Pins,
};
use channels::{bind_channels, style_gradient, Channels};
use column_types::QueryDatasets;
use diagnostic::{diag, Diagnostic};
use indexmap::IndexMap;

/// What `bind` decided about the whole board.
#[derive(Debug)]
pub struct BoundBoard {
    /// One scale per field bound across charts, in planning order.
    pub category_colors: Vec<CategoryColorScale>,
    /// Each placed chart's bound channels, by chart id. A chart whose query returned
    /// no dataset is absent. A chart whose channels cannot bind carries its own error
    /// and leaves the board bound, as a chart that fails to resolve draws an error tile.
    pub channels: IndexMap<ChartId, Result<Channels, Diagnostic>>,
}

/// The board's categorical palette as colors, off its total style (`board.chart_style_context.palette`).
/// Resolving a palette name to its colors is the cascade's; a name that arrives here is a defect.
fn palette(board: &Board) -> Result<Vec<String>, Diagnostic> {
    let style = board
        .body
        .style
        .as_deref()
        .ok_or_else(|| diag::err_internal("a normalized board carries its total style"))?;
    let categorical = style
        .charts
        .color
        .categorical
        .as_ref()
        .ok_or_else(|| diag::err_internal("the total style carries `charts.color.categorical`"))?;
    match &categorical.palette {
        PaletteChoice::Colors(colors) => Ok(colors.iter().map(|c| c.as_str().to_string()).collect()),
        PaletteChoice::Name(name) => Err(diag::err_internal(format!(
            "the palette {name:?} reached bind unresolved"
        ))),
    }
}

/// A chart that names a category with its `color:`: the query it reads and the field.
pub struct Encoding<'a> {
    pub query: &'a str,
    pub field: &'a str,
}

/// The board-wide category color plan over what each encoding's dataset draws. An encoding
/// whose query returned no dataset is skipped; a field that one chart cannot seat is
/// unbound for every chart, since a scale that cannot seat what one chart draws cannot
/// safely bind for any.
pub fn seat(
    encodings: &[Encoding<'_>],
    data: &QueryDatasets,
    pins: &Pins,
    palette: &[String],
) -> Result<Vec<CategoryColorScale>, Diagnostic> {
    let mut observations: Vec<ChartFieldValues> = Vec::new();
    let mut disqualified: HashSet<&str> = HashSet::new();
    for Encoding { query, field } in encodings {
        let Some(dataset) = data.get(*query) else {
            continue;
        };
        match observe(dataset, field, pins.contains_key(*field))? {
            Observed::Disqualified => {
                disqualified.insert(field);
            }
            Observed::Values(values) if !values.is_empty() => {
                observations.push(IndexMap::from([(field.to_string(), values)]));
            }
            Observed::Values(_) => {}
        }
    }
    let mut scales = plan_category_colors(&observations, pins, palette, &IndexMap::new())?;
    scales.retain(|field, _| !disqualified.contains(field.as_str()));
    Ok(scales.into_values().collect())
}

/// Binds a normalized board to the rows its queries returned and their declared
/// column types. A chart's color column that a dataset does not declare is an error.
pub fn bind_board(
    board: &Board,
    data: &QueryDatasets,
) -> Result<BoundBoard, Diagnostic> {
    let charts = placed_charts(board)?;
    let mut encodings = Vec::new();
    for (_, chart) in &charts {
        if let (Some(field), Some(query)) = (color_field(chart), query_name(chart)?) {
            encodings.push(Encoding { query, field });
        }
    }
    let category_colors = seat(&encodings, data, &authored_pins(board), &palette(board)?)?;
    let categorical: HashSet<&str> = category_colors
        .iter()
        .map(|scale| scale.field.as_str())
        .collect();
    let mut channels = IndexMap::new();
    for (id, chart) in charts {
        if let Some(dataset) = query_name(chart)?.and_then(|query| data.get(query)) {
            channels.insert(
                id.clone(),
                bind_channels(chart, dataset, &categorical, style_gradient(chart)),
            );
        }
    }
    Ok(BoundBoard {
        category_colors,
        channels,
    })
}

/// `style.charts.category_colors` of the root board and every nested board. A nested
/// board's field keeps its place; the root wins a conflict, as the outer scope
/// (`_authored_pins`). A body with no style authors none.
pub fn authored_pins(board: &Board) -> Pins {
    fn walk(body: &BoardBody, at: &ItemId, pins: &mut Pins) {
        for placement in items(body, at) {
            if let LayoutItem::Board(nested) = placement.item {
                walk(&nested.body, &placement.id, pins);
            }
        }
        for (field, binding) in body.style.iter().flat_map(|s| &s.charts.category_colors) {
            let values = binding
                .values
                .iter()
                .map(|(v, c)| (v.clone(), c.as_str().to_string()));
            pins.insert(field.clone(), values.collect());
        }
    }
    let mut pins = Pins::new();
    walk(&board.body, &ItemId::root(), &mut pins);
    pins
}

/// The charts the board renders, in layout order, one per chart: a chart placed twice
/// draws the same values both times, and counting it twice would carry a single-chart
/// board over the two-chart threshold.
fn placed_charts(board: &Board) -> Result<Vec<(&ChartId, &Chart)>, Diagnostic> {
    fn walk<'a>(
        board: &'a Board,
        body: &'a BoardBody,
        at: &ItemId,
        out: &mut Vec<(&'a ChartId, &'a Chart)>,
    ) -> Result<(), Diagnostic> {
        for placement in items(body, at) {
            match placement.item {
                LayoutItem::Chart { id, .. } if !out.iter().any(|(seen, _)| *seen == id) => {
                    let chart = board.body.charts.get(id.as_str()).ok_or_else(|| {
                        diag::err_internal(format!(
                            "layout places chart {} but the board defines none",
                            id.as_str()
                        ))
                    })?;
                    out.push((id, chart));
                }
                LayoutItem::Chart { .. } => {}
                LayoutItem::Board(nested) => walk(board, &nested.body, &placement.id, out)?,
            }
        }
        Ok(())
    }
    let mut out = Vec::new();
    walk(board, &board.body, &ItemId::root(), &mut out)?;
    Ok(out)
}

fn base(chart: &Chart) -> Option<&ChartBase> {
    Some(match chart {
        Chart::Bar(c) | Chart::Histogram(c) => &c.cartesian.shared.base,
        Chart::Line(c) => &c.cartesian.shared.base,
        Chart::Area(c) => &c.cartesian.shared.base,
        Chart::Scatter(c) => &c.cartesian.shared.base,
        Chart::Heatmap(c) => &c.cartesian.shared.base,
        Chart::Pie(c) | Chart::Donut(c) => &c.radial.shared.base,
        Chart::SparkBar(c) => &c.shared.base,
        Chart::Table(c) => &c.shared.base,
        Chart::Kpi(c) => &c.base,
        Chart::Geoshape(c) | Chart::Map(c) => &c.geo.shared.base,
        Chart::PointMap(c) | Chart::BubbleMap(c) => &c.geo.shared.base,
        Chart::Custom(c) => &c.shared.base,
        // A callout reads no query.
        Chart::Callout(_) => return None,
    })
}

/// The query a chart reads. Normalize hoists every query; one still in its authored
/// form is an internal error: it breaks normalize's contract.
fn query_name(chart: &Chart) -> Result<Option<&str>, Diagnostic> {
    match base(chart).and_then(|base| base.query.as_ref()) {
        None => Ok(None),
        Some(ChartQuery::Hoisted(id)) => Ok(Some(id.name().as_str())),
        Some(_) => Err(diag::err_internal(
            "a chart query that normalize has not hoisted reached bind",
        )),
    }
}

/// Only an authored `color:` channel names a category, and only on the families
/// whose marks paint from a board scale (a histogram's do not).
fn color_field(chart: &Chart) -> Option<&str> {
    let color: &Option<ColorColumn> = match chart {
        Chart::Bar(c) => &c.cartesian.color,
        Chart::Line(c) => &c.cartesian.color,
        Chart::Area(c) => &c.cartesian.color,
        Chart::Scatter(c) => &c.cartesian.color,
        Chart::Heatmap(c) => &c.cartesian.color,
        Chart::Pie(c) | Chart::Donut(c) => &c.radial.color,
        Chart::PointMap(c) | Chart::BubbleMap(c) => &c.geo.color,
        // A choropleth's `value:` fills it, so its color then names nothing.
        Chart::Geoshape(c) | Chart::Map(c) if c.geo.value.is_none() => &c.geo.color,
        Chart::Histogram(_)
        | Chart::Geoshape(_)
        | Chart::Map(_)
        | Chart::SparkBar(_)
        | Chart::Table(_)
        | Chart::Kpi(_)
        | Chart::Callout(_)
        | Chart::Custom(_) => return None,
    };
    color
        .as_ref()
        .map(ColorColumn::as_str)
        .filter(|field| !field.is_empty())
}
