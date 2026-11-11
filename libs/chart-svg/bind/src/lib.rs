//! The bind stage: a normalized board plus its datasets to a bound board.
//! Everything data-derived and size-independent; no text is measured and no
//! node is built, which is why this crate depends on neither `measure` nor `scene`.
//!
//! `bind_board` walks the spine's `board_model::Board`: the category plan and channels over
//! its `Board`, with the palette read off the board's total style
//! (`charts.color.categorical.palette`, already resolved to colors). `seat` is the plan over a
//! list of encodings; `plan_colors` is the plan over a board's placed charts that
//! `board::bind` seats once and hands each chart. The datasets are `column-types`'
//! `{rows, schema}` per query, the engine's payload shape. Data-derived facts only, with no
//! direct dependency on `measure` or `scene` (the transitive `scene` edge through `d3rs` goes
//! away with the d3rs task). No `bind` code builds a node.
//!
//! Passes and laws: `decode` is the first board-wide pass; `category_colors` the board-wide
//! value-to-slot plan; `channels` a chart's `color`/`background` series channels;
//! `channel_type` a field's quantitative/temporal/nominal verdict from its declared column
//! type; `formats/` the formats law (resolution, tier merge, painter, number locale); `walk` and
//! `style/` the declared priority-list walk and the slots' lists over `board-model`;
//! `font_stack` the emoji-family law.
//!
//! It also owns the data-only halves of the shared modules `chart-renderer` imports
//! (`::bind::...`): `time_axis`; `temporal` (the time unit and its date arithmetic, bucket
//! detection and completion, labeled-bucket normalizing); `x_type` (the x-type law); `rows` (row
//! readers and the color domain); `wide` (the wide fold); `stack` (the stack grammar); `bound`
//! (the bound model, spec § 5.2: `Chart.family`, `Family`, `Cartesian`, `Plane`, `Kpi`, `Callout`, its domains and `Key`, the
//! decoded-cell value; only the engine calls it); `multiples` (the small-multiples partition and
//! `regroup`); `layers` (a layer's row scope and color grouping); `legend` (`legend.values`
//! resolution and the symbol-limit fold); `callout`, `pie`, `table`, `kpi`, `variables` and
//! `failed` (each chart family's or card's data and text); `time_locale`, `time_zone` and
//! `label_template`. `data/` holds the en-US number-locale JSON; the de-DE locale is a
//! test-support builder in `formats::locale`.
//!
//! `feature = "test-support"` exposes the scope guards and locale builders engine tests use.
//! Tests replay recorded production answers (`tests/it/*_production.json`) and Python's
//! recorded style cascade (`tests/oracle/walks.rs`, from `chart_svg_dev/_bind_walks.py`;
//! `tests/oracle/walks.json` from `tests/test_bind_walks_fixture.py`).

pub mod board;
pub mod callout;
pub mod category_colors;
pub mod channel_type;
pub mod channels;
pub mod conditional;
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
pub mod bound;
pub mod multiples;
pub mod pattern;
pub mod pie;
pub mod restated;
pub mod plot;
pub mod rows;
pub mod run;
pub mod stack;
pub mod style;
pub mod table;
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

use board::ChartStyles;
use board_model::board::{items, Board, BoardBody, LayoutItem};
use board_model::chart::{Chart, ChartBase, ChartQuery, ColorColumn};
use board_model::ids::{ChartId, ItemId, QueryId};
use board_model::primitives::PaletteChoice;
use board_model::style::style::Style;
use category_colors::{
    observe, plan_category_colors, CategoryColorScale, ChartFieldValues, Observed, Pins,
};
use channels::{bind_channels, style_gradient, Channels};
use column_types::{Dataset, QueryDatasets};
use diagnostic::{diag, Diagnostic};
use indexmap::IndexMap;
use model_core::Tri;

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

/// A palette as colors. Resolving a palette name to its colors is the cascade's; a name that
/// arrives here is a defect.
pub fn colors_of(palette: &PaletteChoice) -> Result<Vec<String>, Diagnostic> {
    match palette {
        PaletteChoice::Colors(colors) => Ok(colors.iter().map(|c| c.as_str().to_string()).collect()),
        PaletteChoice::Name(name) => Err(diag::err_internal(format!(
            "the palette {name:?} reached bind unresolved"
        ))),
    }
}

/// The categorical palette of a scope's total style as colors (`charts.color.categorical`).
pub fn palette_of(style: &Style) -> Result<Vec<String>, Diagnostic> {
    let categorical = style
        .charts
        .color
        .categorical
        .as_ref()
        .ok_or_else(|| diag::err_internal("the total style carries `charts.color.categorical`"))?;
    colors_of(&categorical.palette)
}

/// The palette a chart's `color:` series paint from (`_effective_palette`): the chart's own
/// `color.categorical.palette` when it sets one, else its scope's. `own` is the chart's own
/// `color.categorical` leaf and `palette` reads the palette off it.
pub fn effective_palette<T>(
    own: &Tri<T>,
    palette: impl FnOnce(&T) -> Option<&PaletteChoice>,
    style: &Style,
) -> Result<Vec<String>, Diagnostic> {
    match own {
        Tri::Value(own) => match palette(own) {
            Some(palette) => colors_of(palette),
            None => palette_of(style),
        },
        Tri::Unset | Tri::Null => palette_of(style),
    }
}

/// The root board's palette: the plan seats every chart's values from it, and a nested board
/// repaints a slot from its own palette.
fn palette(board: &Board) -> Result<Vec<String>, Diagnostic> {
    let style = board
        .body
        .style
        .as_deref()
        .ok_or_else(|| diag::err_internal("a normalized board carries its total style"))?;
    palette_of(style)
}

/// A chart that names a category with its `color:`: the dataset it reads and the field.
pub struct Encoding<'a> {
    pub dataset: &'a Dataset,
    pub field: &'a str,
}

/// The board-wide category color plan over what each encoding's dataset draws. A field that
/// one chart cannot seat is unbound for every chart, since a scale that cannot seat what one
/// chart draws cannot safely bind for any.
pub fn seat(
    encodings: &[Encoding<'_>],
    pins: &Pins,
    palette: &[String],
) -> Result<Vec<CategoryColorScale>, Diagnostic> {
    let mut observations: Vec<ChartFieldValues> = Vec::new();
    let mut disqualified: HashSet<&str> = HashSet::new();
    for Encoding { dataset, field } in encodings {
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

/// The board-wide category color plan over every placed chart's `color:` field, each read from
/// the dataset `dataset` returns for its query. A chart whose query returned no dataset is
/// skipped.
pub fn plan_colors<'a>(
    board: &Board,
    dataset: impl Fn(&QueryId) -> Option<&'a Dataset>,
) -> Result<Vec<CategoryColorScale>, Diagnostic> {
    let mut encodings = Vec::new();
    for (_, _, chart) in placed_charts(board)? {
        if let (Some(field), Some(dataset)) = (color_field(chart), query_id(chart)?.and_then(&dataset)) {
            encodings.push(Encoding { dataset, field });
        }
    }
    if encodings.is_empty() {
        return Ok(Vec::new());
    }
    seat(&encodings, &authored_pins(board), &palette(board)?)
}

/// Binds a normalized board to the rows its queries returned and their declared
/// column types. A chart's color column that a dataset does not declare is an error.
pub fn bind_board(
    board: &Board,
    styles: &ChartStyles,
    data: &QueryDatasets,
) -> Result<BoundBoard, Diagnostic> {
    let category_colors = plan_colors(board, |query| data.get(query.name().as_str()))?;
    let categorical: HashSet<&str> = category_colors
        .iter()
        .map(|scale| scale.field.as_str())
        .collect();
    let mut channels = IndexMap::new();
    for (id, at, chart) in placed_charts(board)? {
        if let Some(dataset) = query_id(chart)?.and_then(|query| data.get(query.name().as_str())) {
            let gradient = style_gradient(chart, styles.own_at(&at)?);
            channels.insert(
                id.clone(),
                bind_channels(chart, dataset, &categorical, gradient),
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
pub(crate) fn placed_charts(board: &Board) -> Result<Vec<(&ChartId, ItemId, &Chart)>, Diagnostic> {
    fn walk<'a>(
        board: &'a Board,
        body: &'a BoardBody,
        at: &ItemId,
        out: &mut Vec<(&'a ChartId, ItemId, &'a Chart)>,
    ) -> Result<(), Diagnostic> {
        for placement in items(body, at) {
            match placement.item {
                LayoutItem::Chart { id, .. } if !out.iter().any(|(seen, ..)| *seen == id) => {
                    let chart = board.body.charts.get(id.as_str()).ok_or_else(|| {
                        diag::err_unknown_chart(id.as_str(), board.body.charts.keys().map(String::as_str).collect::<Vec<_>>())
                    })?;
                    out.push((id, placement.id.clone(), chart));
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

/// The name of the query `chart` reads, as `bind_board` looks its dataset up; `None` for a chart
/// that reads none.
pub fn query_name(chart: &Chart) -> Result<Option<&str>, Diagnostic> {
    Ok(query_id(chart)?.map(|id| id.name().as_str()))
}

/// The hoisted query a chart reads; `None` for a callout or a chart that names no query.
/// Normalize hoists every query; one still in its authored form is an internal error: it
/// breaks normalize's contract.
pub fn query_id(chart: &Chart) -> Result<Option<&QueryId>, Diagnostic> {
    match base(chart).and_then(|base| base.query.as_ref()) {
        None => Ok(None),
        Some(ChartQuery::Hoisted(id)) => Ok(Some(id)),
        Some(_) => Err(diag::err_internal(
            "a chart query that normalize has not hoisted reached bind",
        )),
    }
}

/// Only an authored `color:` channel names a category, and only on the families
/// whose marks paint from a board scale (a histogram's do not).
pub(crate) fn color_field(chart: &Chart) -> Option<&str> {
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
