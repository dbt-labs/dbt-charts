//! Scatter: one point per row on a quantitative x and y. Production's laws composed: the
//! smart-zero verdicts (`plot::zero`), the linear domain (`plot::domain`), the axis and
//! tooltip formats (`formats`) and the zero-rule gates. A row with a null x draws nothing,
//! yet its y joins the y domain, as Vega-Lite's invalid filter keeps it there. What the
//! bind does not model yet refuses by name.
//!
//! A `shape:` column (`ShapeScale`) gives each category, in ascending order, a symbol of Vega's default
//! range and draws a legend of its own (`ShapeScale::title` is the raw field); the point mark's authored
//! `shape` is the one symbol of every point (`Shape::Symbol`) and `filled: false` rings each point over the
//! mark's interior (`Unfilled`). A shape on the `color:` field (production merges the two legends), under
//! `multiples:`, with a null category or a row that draws no point refuses by name.
//!
//! A `size:` column scales each point's area (`bound::SizeScale`) with its legend hidden; a visible
//! size legend, and a size under `multiples:`, refuse by name.
//!
//! A nominal `color:` splits the points into series painted from the board's category plan
//! (`cartesian::color_series`), heads each hover with the series name, and names them in a side
//! legend whose entries are buttons (`bound::Toggle`) where the theme shows a hover emphasis. A
//! legend with `values:` or past its `symbol_limit:`, and a row that draws no point (its series
//! would still name a legend entry in production), refuse by name.
//!
//! `multiples:` partitions the rows into panels (`plot::facets`) on shared scales: the domains and
//! ladders span every row, each panel holds its drawn datums in query order, and a panel's hover
//! reads x then y where a single plot's reads y then x (production's facet marks carry Vega-Lite's
//! own description). Independent scales refuse by name, and a panel with no drawn point does too.
//! `tests/it/scatter_facets.rs` replays `scatter_facets_production.json`, recorded by
//! `record_scatter_facets.py`.
//!
//! A date or datetime x is a time scale (`plot::time::x_domain`, the line's own): each point stands at its
//! value's instant, floored to the grain the x-type law settles on, and its hover names the date
//! (`temporal::tooltip_header_date`) or, over timed rows within two days, the time of day. A row with a
//! null or empty x draws nothing, as a number x's does.
//!
//! A text x or y is a point scale (`plot::band::ascending_domain`, `ScaleKind::Point`): each point stands at its
//! category's band center, the categories in Vega's ascending order with a null one a band of its own, over the
//! x axis' `scale.padding` (the theme's 0) or a y axis' fixed half step. With a `color:` the one category axis
//! and one number axis are a dot plot, whose hover names the category, then the series, then the value
//! (`_dot_plot_roles`). `multiples:` over a point scale refuses by name.
//!
//! Binds a quantitative, temporal or text x and a quantitative or text y to a `Family::Cartesian`; a year-shaped
//! x, a text on both axes beside a time x and any other y type are refused by name.
//! `tests/it/scatter.rs` replays `scatter_production.json`, production's final SVG of each YAML
//! twin, recorded by `record_scatter_bind.py`.

use std::collections::HashMap;

use board_model::board::Board;
use board_model::facet::FontFamily;
use board_model::chart::{ColorColumn, Columns, MultiplesConfig, MultiplesScale, ScatterChart};
use board_model::ids::ChartId;
use board_model::style::marks::labels_drawn;
use d3rs::vega_symbol::VegaSymbol;
use color::Rgba;
use column_types::Dataset;
use model_core::Engine;
use diagnostic::{diag, refuse, Diagnostic};

use crate::column_kind::{channel_type, field_kind, ChannelType, ColumnKind, Role};
use crate::plot::band::ascending_domain;
use crate::decode::{decode, Decoded};
use crate::rows::{color_domain_of, is_year_shaped};
use crate::temporal::tooltip_header_date;
use crate::time_axis::Instant;
use crate::plot::time;
use crate::derived::Placed;
use crate::category_colors::CategoryColorScale;
use crate::families::cartesian::{self, axis_format, count, hex, no_data, plain_text, single_series, threshold_rules, unsupported, value, Prelude};
use crate::legend::fold_symbol_limit;
use crate::effective_palette;
use crate::formats::locale::active_number_locale;
use crate::formats::ResolvedFormat;
use crate::bound::{self, SizeScale,
    AuthoredLegend, Baselines,  Bounds, BoundsReason, Extent, Datum, Key, Mark, MarkLayer, MarkStyle, Naming, Orient,
    FixedLadder, Ladder, PlaneParts, Sharing, Pos, RowRef, Rung, Scale, ScaleKind, Shape,
    PointStyle, ShapeScale, Toggle, Tooltip, Unfilled, TooltipEntry, TooltipHeader, ValueAxis, ValueDomain, Zero, ValueScale,
};
use crate::plot::domain::{self, Count, Side};
use crate::plot::facets::{self, FacetAxis, Facets};
use crate::plot::zero::{self, Family};
use crate::run::RunResult;
use crate::style::axis::AxisLadder;

fn internal(context: &str) -> impl Fn(crate::bound::Invalid) -> Diagnostic + '_ {
    move |invalid| diag::err_internal(format!("scatter {context} is invalid: {invalid:?}"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &ScatterChart) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    let unported = [
        ("`layers:`", !chart.layers.is_empty()),
        ("`annotations:`", !chart.annotations.is_empty()),
        ("`tooltip:` columns", !chart.tooltip.tooltip.is_empty()),
        ("a list `y:`", matches!(fields.y, Some(Columns::Many(_)))),
        ("`sort:`", fields.sort.is_some()),
        ("`link:`", fields.shared.base.link.is_some()),
        ("`style.conditional`", !chart.style.conditional.is_empty()),
    ];
    match unported.into_iter().find_map(|(field, set)| set.then_some(field)) {
        Some(field) => Err(unsupported(refuse!("a scatter with {field} is not supported"))),
        None => Ok(()),
    }
}

/// Panels on their own scales are not modeled yet. A mirrored y axis is refused with every scatter's
/// axis leaves.
fn refuse_unported_facets(multiples: &MultiplesConfig) -> Result<(), Diagnostic> {
    match multiples.scale {
        MultiplesScale::Shared => Ok(()),
        MultiplesScale::Independent => Err(unsupported(refuse!("a scatter with `multiples.scale: independent` is not supported"))),
    }
}

/// Vega-Lite's default mark color, the ring of an unfilled point that no color channel or authored
/// color paints (`VL_DEFAULT_MARK_COLOR` in chart-renderer's scatter).
const VL_DEFAULT_MARK_COLOR: &str = "#4c78a8";

/// The point mark as production draws it: the chart's total `marks.point`. Only a constant size is
/// ported; an `auto` stroke width draws no outline, as production's `stroke_width.unwrap_or(0.0)`.
/// `colored` is whether the chart has a `color:` channel, whose series paint an unfilled point's ring
/// (`unfilled_stroke`), the mark's own color otherwise.
fn point_style(chart: &ScatterChart, colored: bool, canvas: Rgba) -> Result<PointStyle, Diagnostic> {
    let point = &chart.style.marks.point;
    let Engine::Value(size) = point.size else {
        return Err(unsupported(refuse!("a scatter point mark with an `auto` size is not supported")));
    };
    let unported = [
        ("a `color`", !matches!(point.color, Engine::Rule(_))),
        ("value labels", labels_drawn(&point.labels.base.visible)),
    ];
    if let Some(aspect) = unported.into_iter().find_map(|(aspect, set)| set.then_some(aspect)) {
        return Err(unsupported(refuse!("a scatter point mark with {aspect} is not supported")));
    }
    let stroke_width = match point.stroke_width {
        Engine::Value(width) => width,
        Engine::Rule(_) => 0.0,
    };
    let unfilled = (!point.filled)
        .then(|| {
            // `theme.background` is the canvas the chart's frame settled on; normalize settles every other token.
            let interior = match point.fill.as_ref() {
                "theme.background" => hex(canvas),
                color => hex(color::parse_css_color(color).map_err(|e| diag::err_internal(format!("a point fill is a color or `theme.background`: {e}")))?),
            };
            Ok::<_, Diagnostic>(Unfilled { interior, stroke: (!colored).then(|| VL_DEFAULT_MARK_COLOR.to_string()) })
        })
        .transpose()?;
    Ok(PointStyle { size, opacity: point.opacity, stroke_width, unfilled })
}

/// The point mark's authored `shape` (`point_glyph`): its Vega symbol, a circle when `auto`.
fn point_symbol(chart: &ScatterChart) -> Result<VegaSymbol, Diagnostic> {
    match chart.style.marks.point.shape {
        Engine::Rule(_) => Ok(VegaSymbol::Circle),
        Engine::Value(shape) => VegaSymbol::from_name(shape.as_str()).ok_or_else(|| diag::err_internal(format!("the point shape {:?} is no Vega symbol", shape.as_str()))),
    }
}

/// How production reads a scatter axis: it bakes the y axis (headroom, count, the step floor) and
/// leaves x unbaked, ticked by Vega at its count with no headroom, so an x headroom refuses.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Baked {
    X,
    Y,
}

/// The value axis over `values`. A log axis (production's `LogAxis`) ticks at d3's default count on y
/// and at the axis's own on x, and has no zero to join; every other scale ticks and rounds as a linear one.
fn value_axis(
    ladder: &AxisLadder<'_>,
    scale: ValueScale,
    baked: Baked,
    zero: Zero,
    values: &[f64],
    format: ResolvedFormat,
    context: &str,
) -> Result<ValueAxis, Diagnostic> {
    let (headroom, axis_count, min_step) = match baked {
        Baked::Y => (
            ladder.headroom().unwrap_or(0.0),
            count(ladder)?,
            d3rs::numeral_scale::tick_min_step_for_format(&format.spec),
        ),
        Baked::X => {
            if ladder.headroom().is_some() {
                return Err(unsupported(refuse!("a scatter x axis with a `scale.headroom` is not supported")));
            }
            (0.0, None, None)
        }
    };
    let column_forming = baked == Baked::Y;
    let mut domain = match scale {
        ValueScale::Log { base } => {
            let ticks = match baked {
                Baked::Y => cartesian::LOG_TICK_COUNT,
                Baked::X => count(ladder)?.unwrap_or(Count::Auto).target(),
            };
            cartesian::log_domain(base, ticks, ladder, values, &format, context)?
        }
        scale => {
            let axis = domain::Axis {
                scale,
                zero,
                headroom,
                percent: format.spec.contains('%'),
                count: axis_count,
                values: None,
                domain: ladder.domain(),
                min_step,
                independent: false,
                nice: None,
                mark: domain::Mark::Points,
            };
            cartesian::linear_domain(&axis, values, &format, column_forming, context)?
        }
    };
    if domain.extent.lo() == domain.extent.hi() {
        return Err(unsupported(refuse!("a scatter axis over a single value is not supported")));
    }
    if baked == Baked::X && !scale.is_log() {
        let target = count(ladder)?.unwrap_or(Count::Auto).target();
        // Production fits an x to its data only clear of zero; a fit across zero stays niced.
        if zero == Zero::Free && domain.authored.is_none() && !cartesian::spans_zero(values) {
            let data = (domain.extent.lo(), domain.extent.hi());
            let (lo, hi) = domain::nice_clear_of_data(data.0, data.1, data, target, None);
            domain.bounds = Bounds { extent: Extent::new(lo, hi).map_err(internal(context))?, reason: BoundsReason::ClearOfData };
        }
        domain.ladder = x_ladder(target, &domain, &format, values)?;
    }
    Ok(ValueAxis { domain, format })
}

/// Production leaves a scatter x unbaked, and Vega ticks its scale at `ticks.count` (6 when
/// unset, `scatter.rs`'s `unwrap_or(6.0)`): d3's ticks over the bounds at Vega's count `target`,
/// each printed as Vega prints an unbaked rung.
fn x_ladder(target: usize, domain: &ValueDomain, format: &ResolvedFormat, values: &[f64]) -> Result<Ladder, Diagnostic> {
    let bounds = domain.bounds.extent;
    let (lo, hi) = (bounds.lo(), bounds.hi());
    let extent_hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let rungs = format
        .unbaked_rungs(&active_number_locale(), (lo, hi), target, None, extent_hi)?
        .into_iter()
        .map(|(value, text)| Rung { value, text })
        .collect();
    Ok(Ladder::Fixed(FixedLadder::new(rungs, bounds).map_err(internal("x ladder"))?))
}

/// Whether zero is on a scatter x: an authored domain decides alone, else a pinned zero or data
/// that reaches it.
fn x_holds_zero(axis: &ValueAxis, zero: Zero, values: &[f64]) -> bool {
    match axis.domain.authored {
        Some(authored) => authored.extent.lo() <= 0.0 && 0.0 <= authored.extent.hi(),
        None => zero == Zero::Pinned(true) || cartesian::spans_zero(values),
    }
}

/// The scale `x` draws on, read off the data: a chart with no rows has none to read, and its frame is
/// drawn over a number x, as is a declared number column with no value, whose rows draw no point.
fn x_channel(id: &ChartId, chart: &ScatterChart, board: &Board, run: &RunResult) -> Result<ChannelType, Diagnostic> {
    let x_field = chart.cartesian.x.as_ref().ok_or_else(|| diag::err_internal("normalize requires a cartesian chart's `x:`"))?.as_str();
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[x_field])?;
    if dataset.rows.is_empty() {
        return Ok(ChannelType::Quantitative);
    }
    let kind = field_kind(&dataset.schema, &dataset.rows, x_field)?;
    if matches!(kind, ColumnKind::Text { numeric: true, .. }) {
        return Err(unsupported(refuse!("a scatter on a text column that reads as numbers is not supported")));
    }
    let channel = match kind {
        ColumnKind::Empty if dataset.schema.get(x_field).map_err(Diagnostic::refused)?.is_number() => ChannelType::Quantitative,
        kind => channel_type(kind, Role::Dimension, x_field)?,
    };
    if channel == ChannelType::Nominal && !matches!(kind, ColumnKind::Text { .. }) {
        return Err(unsupported(refuse!("a scatter on a nominal x that holds no text is not supported")));
    }
    Ok(channel)
}

/// The scale `y` draws on: a declared number, or plain text, which is a point scale. A y of dates, of
/// years or of numbers written as text is production's measure or time scale, which the bind does not
/// model; a chart with no rows has none to read and its frame is drawn over a number y.
fn y_channel(id: &ChartId, chart: &ScatterChart, board: &Board, run: &RunResult) -> Result<ChannelType, Diagnostic> {
    let Some(Columns::One(y_field)) = &chart.cartesian.y else { unreachable!("normalize requires one `y:`, and a list is refused") };
    let y_field = y_field.as_str();
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[y_field])?;
    if dataset.rows.is_empty() {
        return Ok(ChannelType::Quantitative);
    }
    let declared_number = dataset.schema.get(y_field).map_err(Diagnostic::refused)?.is_number();
    match field_kind(&dataset.schema, &dataset.rows, y_field)? {
        ColumnKind::Number { .. } | ColumnKind::Empty if declared_number => Ok(ChannelType::Quantitative),
        ColumnKind::Text { year: false, dated: false, date_like: false, numeric: false, ambiguous_year: false, .. } => Ok(ChannelType::Nominal),
        _ => Err(unsupported(refuse!("a scatter on a y that is neither a declared number nor plain text is not supported"))),
    }
}

/// A row's x cell: a number, the text of a date, or a category.
#[derive(Clone, Copy)]
enum XCell<'a> {
    Num(f64),
    Date(&'a str),
    /// `None` is a null category.
    Category(Option<&'a str>),
}

/// A row's y cell: a number or a category.
#[derive(Clone, Copy)]
enum YCell<'a> {
    Num(f64),
    Category(Option<&'a str>),
}

/// `None` for a null, and for a date cell with no text, which production reads as no value
/// (`scatter.rs`: `x_reading`). A category is a cell whatever it holds: a null one is a band of its own
/// (`kept_reading`).
fn x_cell(key: &Key, x_type: ChannelType) -> Result<Option<XCell<'_>>, Diagnostic> {
    match x_type {
        ChannelType::Quantitative => Ok(value(key)?.map(XCell::Num)),
        ChannelType::Nominal => category(key).map(|category| Some(XCell::Category(category))),
        ChannelType::Temporal => match key {
            Key::Null => Ok(None),
            Key::Text(text) | Key::Date { text, .. } => Ok((!text.is_empty()).then_some(XCell::Date(text))),
            other => Err(diag::err_internal(format!("a temporal x column decoded to {other:?}"))),
        },
    }
}

fn y_cell(key: &Key, y_type: ChannelType) -> Result<Option<YCell<'_>>, Diagnostic> {
    match y_type {
        ChannelType::Nominal => category(key).map(|category| Some(YCell::Category(category))),
        _ => Ok(value(key)?.map(YCell::Num)),
    }
}

/// A category cell: its text, `None` for a null.
fn category(key: &Key) -> Result<Option<&str>, Diagnostic> {
    match key {
        Key::Text(text) => Ok(Some(text)),
        Key::Null => Ok(None),
        other => Err(diag::err_internal(format!("a text axis decoded to {other:?}"))),
    }
}

/// The x scale of the drawn rows, each one's position on it and the x row of its hover.
struct PlacedX {
    kind: ScaleKind,
    /// Whether the x grid's threshold rule draws.
    threshold: bool,
    at: Vec<Pos>,
    text: Vec<String>,
}

/// The y scale of the drawn rows, each one's value on it (a band's index on a point scale) and the y row of
/// its hover.
struct PlacedY {
    kind: ScaleKind,
    at: Vec<f64>,
    text: Vec<String>,
}

/// A point scale over the distinct `categories`, `padding` of a step outside the first and last band, and each
/// cell's band and hover text.
fn point_scale(categories: &[Option<&str>], padding: f64) -> Result<(ScaleKind, Vec<usize>, Vec<String>), Diagnostic> {
    let domain = ascending_domain(categories)?;
    let band_of: HashMap<&Key, usize> = domain.keys().iter().enumerate().map(|(band, key)| (key, band)).collect();
    let key_of = |category: &Option<&str>| category.map_or(Key::Null, |text| Key::Text(text.to_string()));
    let bands = categories.iter().map(|category| band_of[&key_of(category)]).collect();
    let text = categories.iter().map(|category| category.unwrap_or("null").to_string()).collect();
    Ok((ScaleKind::Point { domain, padding }, bands, text))
}

/// The category x of the `drawn` rows: a point scale over the x axis' `scale.padding`, which production reads
/// where the theme leaves none as Vega's own 0.5 (`point_x_padding`).
fn point_x(p: &Prelude<'_>, drawn: &[(usize, XCell<'_>, YCell<'_>)]) -> Result<PlacedX, Diagnostic> {
    let categories: Vec<Option<&str>> = drawn.iter().map(|(_, x, _)| match x {
        XCell::Category(category) => *category,
        _ => unreachable!("a text x holds categories"),
    }).collect();
    let (kind, bands, text) = point_scale(&categories, p.x_ladder.padding().unwrap_or(0.5))?;
    Ok(PlacedX { kind, threshold: false, at: bands.into_iter().map(Pos::Band).collect(), text })
}

/// The category y of the `drawn` rows: a point scale a half step in from each end, whatever the y axis'
/// `scale.padding` (production's `band`, `(0.5 + i) / n`).
fn point_y(drawn: &[(usize, XCell<'_>, YCell<'_>)]) -> Result<PlacedY, Diagnostic> {
    let categories: Vec<Option<&str>> = drawn.iter().map(|(_, _, y)| match y {
        YCell::Category(category) => *category,
        YCell::Num(_) => unreachable!("a text y holds categories"),
    }).collect();
    let (kind, bands, text) = point_scale(&categories, 0.5)?;
    Ok(PlacedY { kind, at: bands.into_iter().map(|band| band as f64).collect(), text })
}

/// The time x of the `drawn` rows: the line's domain over their dates, each point at its instant. The hover
/// names the date at the data's grain, or the time of day over rows of one or two days.
fn time_x(id: &ChartId, dataset: &Dataset, p: &Prelude<'_>, drawn: &[(usize, XCell<'_>, YCell<'_>)]) -> Result<PlacedX, Diagnostic> {
    let texts: Vec<&str> = drawn
        .iter()
        .map(|(_, x, _)| match x {
            XCell::Date(text) => Ok(*text),
            XCell::Num(x) => Err(diag::err_internal(format!("a time x read the number {x}"))),
            XCell::Category(_) => unreachable!("a time x holds dates"),
        })
        .collect::<Result<_, _>>()?;
    let domain = time::x_domain(id.as_str(), "scatter", dataset, p.x_field, p.x_ladder.time_unit_none(), &texts)?;
    let instants = texts
        .iter()
        .map(|text| domain.axis.get(text).ok_or_else(|| diag::err_internal(format!("the time axis read every x, but not {text:?}"))))
        .collect::<Result<Vec<&Instant>, _>>()?;
    if instants.iter().any(|instant| instant.nan) {
        return Err(unsupported(refuse!("a scatter on a year below 100 is not supported")));
    }
    let clock = domain.clock_header()?;
    let text = instants
        .iter()
        .map(|instant| match &clock {
            Some(clock) => clock.text(instant.at),
            None => tooltip_header_date(instant.date, domain.unit).map_err(diag::err_internal),
        })
        .collect::<Result<_, _>>()?;
    let at = instants.into_iter().map(|instant| Pos::Time(instant.clone())).collect();
    Ok(PlacedX { kind: ScaleKind::Time(domain), threshold: false, at, text })
}

/// A text axis' labels name their categories: an authored `labels.format` would reformat them, which no
/// bind models.
fn refuse_label_format(ladder: &AxisLadder<'_>, axis: &str) -> Result<(), Diagnostic> {
    match ladder.labels_format() {
        None => Ok(()),
        Some(_) => Err(unsupported(refuse!("a scatter text {axis} axis with a `labels.format` is not supported"))),
    }
}

/// `chart` bound over its run, in the style of `body`, the board body that places it. No rows is
/// [`bound::Cartesian::NoData`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(
    id: &ChartId,
    chart: &ScatterChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed<'_>,
    scales: &[CategoryColorScale],
) -> Result<bound::Chart, Diagnostic> {
    refuse_unported(chart)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let (x_type, y_type) = (x_channel(id, chart, board, run)?, y_channel(id, chart, board, run)?);
    let (temporal, x_nominal, y_nominal) = (x_type == ChannelType::Temporal, x_type == ChannelType::Nominal, y_type == ChannelType::Nominal);
    if temporal && y_nominal {
        return Err(unsupported(refuse!("a scatter on a time x with a text y is not supported")));
    }
    if (x_nominal || y_nominal) && chart.cartesian.multiples.is_some() {
        return Err(unsupported(refuse!("a scatter on a nominal x or y with `multiples:` is not supported")));
    }
    let p = Prelude::of(&chart.cartesian, x_type, y_type, placed)?;
    let (x_field, y_field) = (p.x_field, p.y_field);
    if temporal {
        time::refuse_unmodeled_leaves(&p.tiers, &p.x_ladder, "scatter")?;
    }
    if x_nominal {
        refuse_label_format(&p.x_ladder, "x")?;
    }
    if y_nominal {
        refuse_label_format(&p.y_ladder, "y")?;
    }
    let x_format = if x_type == ChannelType::Quantitative { axis_format(&p.x_ladder, p.formats)? } else { ResolvedFormat::default() };
    let y_format = if y_nominal { ResolvedFormat::default() } else { axis_format(&p.y_ladder, p.formats)? };
    let color_field = chart.cartesian.color.as_ref().map(ColorColumn::as_str);
    let fields: Vec<&str> = [x_field, y_field].into_iter().chain(color_field).collect();
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &fields)?;
    if dataset.rows.is_empty() {
        return Ok(no_data(placed.derive()?, placed.public_id, run, title, subtitle));
    }
    if temporal && is_year_shaped(&dataset.schema, &dataset.rows, x_field).map_err(Diagnostic::refused)? {
        return Err(unsupported(refuse!("a scatter on a year-shaped x is not supported")));
    }
    if temporal && chart.cartesian.multiples.is_some() {
        return Err(unsupported(refuse!("a scatter on a time x with `multiples:` is not supported")));
    }
    let decoded = decode(dataset)?;
    let (xs, ys) = (decoded.keys(x_field)?, decoded.keys(y_field)?);
    let partition = match &chart.cartesian.multiples {
        Some(multiples) => {
            refuse_unported_facets(multiples)?;
            let facets = facet_fields(multiples, &decoded, dataset)?;
            facets::partition(&dataset.rows, Some(&facets))?
        }
        None => facets::partition(&dataset.rows, None)?,
    };
    let mut drawn = Vec::new();
    let (mut x_values, mut y_values) = (Vec::new(), Vec::new());
    for (row, (x, y)) in xs.iter().zip(ys).enumerate() {
        match (x_cell(x, x_type)?, y_cell(y, y_type)?) {
            (Some(x), Some(y)) => {
                drawn.push((row, x, y));
                if let XCell::Num(x) = x {
                    x_values.push(x);
                }
                if let YCell::Num(y) = y {
                    y_values.push(y);
                }
            }
            (Some(_), None) => {
                return Err(unsupported(refuse!("a scatter row with a null y beside a value x is not supported")))
            }
            (None, Some(YCell::Num(y))) => y_values.push(y),
            (None, Some(YCell::Category(_)) | None) => {}
        }
    }
    if drawn.is_empty() {
        return Err(unsupported(refuse!("a scatter whose rows draw no point is not supported")));
    }
    if color_field.is_some() && drawn.len() != xs.len() {
        return Err(unsupported(refuse!("a scatter `color:` with a row that draws no point is not supported")));
    }

    let size = chart.size.as_ref().map(|channel| size_scale(channel.as_str(), dataset, &decoded, &drawn)).transpose()?;
    for (scale, side, field, values) in [(p.y_scale, Side::Y, y_field, &y_values), (p.x_scale, Side::X, x_field, &x_values)] {
        if scale.is_log() {
            domain::require_positive(placed.public_id, side, field, values)?;
        }
    }
    if p.y_scale.is_log() {
        domain::refuse_log(placed.public_id, domain::LogUse::Other, count(&p.y_ladder)?)?;
    }
    let tooltip_values: Vec<f64> = y_values.iter().chain(&x_values).copied().collect();
    let y_tooltip = p.tooltip_format(&tooltip_values)?;
    // Production paints the x row with the tooltip format's spec alone, no affix or register.
    let x_tooltip = ResolvedFormat { spec: y_tooltip.spec.clone(), ..ResolvedFormat::default() };
    let locale = active_number_locale();
    let y_zero = cartesian::zero_on(p.y_scale, zero::verdict(Family::Scatter, p.y_ladder.zero_setting(), &y_values));
    let y = if y_nominal {
        point_y(&drawn)?
    } else {
        let axis = value_axis(&p.y_ladder, p.y_scale, Baked::Y, y_zero, &y_values, y_format, "y domain")?;
        let at = drawn
            .iter()
            .map(|(_, _, y)| match y {
                YCell::Num(y) => Ok(*y),
                YCell::Category(_) => unreachable!("a number y holds numbers"),
            })
            .collect::<Result<Vec<_>, Diagnostic>>()?;
        let text = at.iter().map(|y| y_tooltip.paint(&locale, *y)).collect::<Result<_, _>>()?;
        PlacedY { kind: ScaleKind::Linear(axis), at, text }
    };
    let y_domain = match &y.kind {
        ScaleKind::Linear(axis) => Some(&axis.domain),
        _ => None,
    };
    let (axis_styles, [x_shown, y_shown]) = cartesian::axes(&p, y_domain, placed.scope, "scatter", x_type == ChannelType::Quantitative, true)?;
    let x = match x_type {
        ChannelType::Temporal => time_x(id, dataset, &p, &drawn)?,
        ChannelType::Nominal => point_x(&p, &drawn)?,
        ChannelType::Quantitative => {
            let x_zero = cartesian::zero_on(p.x_scale, zero::scatter_x(p.x_ladder.zero_setting(), p.x_ladder.domain().is_some(), false, &x_values));
            let axis = value_axis(&p.x_ladder, p.x_scale, Baked::X, x_zero, &x_values, x_format, "x domain")?;
            let threshold = threshold_rules(&p.x_ladder)? && x_holds_zero(&axis, x_zero, &x_values);
            let (at, text) = drawn
                .iter()
                .map(|(_, x, _)| match x {
                    XCell::Num(x) => Ok((Pos::Num(*x), x_tooltip.paint(&locale, *x)?)),
                    XCell::Date(text) => Err(diag::err_internal(format!("a number x read the date {text:?}"))),
                    XCell::Category(_) => unreachable!("a number x holds numbers"),
                })
                .collect::<Result<(Vec<_>, Vec<_>), Diagnostic>>()?;
            PlacedX { kind: ScaleKind::Linear(axis), threshold, at, text }
        }
    };

    let derived = placed.derive()?;
    let frame = Some(derived.frame.clone());
    if chart.size.is_some() && derived.legend.base.visible != Engine::Value(false) {
        return Err(unsupported(refuse!("a scatter `size:` with a visible legend is not supported")));
    }
    if chart.size.is_some() && chart.cartesian.multiples.is_some() {
        return Err(unsupported(refuse!("a scatter `size:` with `multiples:` is not supported")));
    }
    if color_field.is_some() && chart.cartesian.multiples.is_some() {
        return Err(unsupported(refuse!("a scatter `color:` with `multiples:` is not supported")));
    }
    if color_field.is_some() && derived.legend.base.values.is_some() {
        return Err(unsupported(refuse!("a scatter legend with `values:` is not supported")));
    }
    let palette = effective_palette(&placed.own.charts.scatter.cartesian.painted.color.categorical, |own| own.palette.as_ref(), p.style)?;
    let colors = color_field
        .map(|field| cartesian::color_series(field, dataset, decoded.keys(field)?, scales, &palette, derived.frame.canvas))
        .transpose()?;
    if let (Some(colors), Some(limit)) = (&colors, derived.legend.base.symbol_limit) {
        if fold_symbol_limit(colors.series.len(), limit as usize).is_some() {
            return Err(unsupported(refuse!("a scatter legend with more entries than its `symbol_limit:` is not supported")));
        }
    }

    let shape = match &chart.shape {
        Some(channel) => Shape::Channel(shape_scale(channel.as_str(), color_field, chart.cartesian.multiples.is_some(), dataset, &decoded, &drawn, &p.style.font.family)?),
        None => Shape::Symbol(point_symbol(chart)?),
    };

    // Production's panel marks carry Vega-Lite's own description, x then y; a single plot's tooltip
    // reads y then x.
    let faceted = chart.cartesian.multiples.is_some();
    let mut panel_of = vec![0; dataset.rows.len()];
    for (panel, rows) in partition.panels.iter().enumerate() {
        for &row in &rows.rows {
            panel_of[row] = panel;
        }
    }
    // One category axis, one number axis and a color split is a dot plot (`_dot_plot_roles`): the hover names the
    // category, the series (ranked when the board plans the color field's scale) and the number, which the full
    // tooltip format paints.
    let dot_plot = colors.is_some() && x_nominal != y_nominal;
    let ranked = color_field.is_some_and(|field| scales.iter().any(|scale| scale.field == field));
    let mut panels: Vec<Vec<Datum>> = vec![Vec::new(); partition.panels.len()];
    for (index, (row, cell, y_cell)) in drawn.iter().enumerate() {
        let (x_at, y_at) = (x.at[index].clone(), y.at[index]);
        let (x_text, y_text) = (&x.text[index], &y.text[index]);
        let tooltip = match (&colors, dot_plot) {
            (Some(colors), true) => {
                let series = colors.of_row[*row];
                // Production's dot-plot header is the cell's own text, which reads a null as empty.
                let (category, measure) = if x_nominal {
                    let XCell::Category(category) = cell else { unreachable!("a dot plot's x is the category here") };
                    (category.unwrap_or_default().to_string(), TooltipEntry { title: p.y_title.clone(), value: y_text.clone() })
                } else {
                    let XCell::Num(x) = cell else { unreachable!("a dot plot's y is the category, so its x is a number") };
                    let YCell::Category(category) = y_cell else { unreachable!("a dot plot's y is the category") };
                    (category.unwrap_or_default().to_string(), TooltipEntry { title: p.x_title.clone(), value: y_tooltip.paint(&locale, *x)? })
                };
                let mut heads = vec![TooltipHeader::plain(category), TooltipHeader::series(colors.series[series].display.clone())];
                if ranked {
                    heads.push(TooltipHeader::rank(series));
                }
                Tooltip { heads, entries: vec![measure] }
            }
            _ => {
                let x_entry = TooltipEntry { title: p.x_title.clone(), value: x_text.clone() };
                let y_entry = TooltipEntry { title: p.y_title.clone(), value: y_text.clone() };
                let entries = if faceted { [x_entry, y_entry] } else { [y_entry, x_entry] };
                let heads = colors.iter().map(|colors| TooltipHeader::swatched(colors.series[colors.of_row[*row]].display.clone())).collect();
                Tooltip { heads, entries: entries.into() }
            }
        };
        let series = colors.as_ref().map_or(0, |colors| colors.of_row[*row]);
        panels[panel_of[*row]].push(datum(*row, x_at, y_at, series, tooltip));
    }
    if panels.iter().any(Vec::is_empty) {
        return Err(unsupported(refuse!("a scatter panel whose rows draw no point is not supported")));
    }

    let baselines = Baselines {
        zero: !y_nominal && cartesian::zero_rule(&p.y_ladder, y_zero, &y_values)?,
        unity: false,
        x_threshold: x.threshold,
    };
    let (series, legends) = match colors {
        Some(colors) => {
            let labels: HashMap<String, String> = chart.cartesian.labels.field_labels.clone().into_iter().collect();
            let title = display_text::field_title(color_field.expect("a color series has its field"), &labels).map_err(diag::err_internal)?;
            let mut legend = colors.legend(title);
            legend.stamps_series = dot_plot && !ranked;
            // A click hides series only where the theme shows a hover emphasis, since nothing else
            // shows an entry hidden (`LegendView::of`); a single series has nothing to narrow to.
            if p.style.charts.hover_emphasis.visible && colors.series.len() > 1 {
                let mut domain: Vec<String> = Vec::new();
                for &index in &colors.of_row {
                    let name = &colors.series[index].display;
                    if !domain.contains(name) {
                        domain.push(name.clone());
                    }
                }
                legend.toggle = Some(Toggle {
                    param: format!("_dct_legend_{}", placed.public_id),
                    domain,
                    keys: colors.series.iter().map(|series| series.display.clone()).collect(),
                });
            }
            (colors.series, vec![legend])
        }
        None => (vec![single_series(placed, y_field, &p.y_title, &derived)?], Vec::new()),
    };
    let plot = bound::Plane::new(PlaneParts {
        orient: Orient::Vertical,
        x: Scale { kind: x.kind, title: x_shown },
        y: vec![Scale { kind: y.kind, title: y_shown }],
        panels: partition.layout,
        series,
        layers: vec![MarkLayer { mark: Mark::Points { shape, size }, y_scale: 0, panels }],
        naming: Naming {
            rail: None,
            entries: Vec::new(),
            asks_top_strip: false,
            authored: AuthoredLegend::Side(derived.legend.clone()),
        },
        legends,
        support: None,
        baselines,
        axis_styles,
        diagnostics: crate::restated::axes(&p.tiers, [&p.x_ladder, &p.y_ladder], placed.public_id),
        mark_style: MarkStyle { points: Some(point_style(chart, color_field.is_some(), derived.frame.canvas)?), lines: None, bars: None, bins: None },
    })
    .map_err(internal("plot"))?;
    Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(bound::Cartesian::Plane(Box::new(plot)))) })
}

/// The size channel `field`: its values must all be declared numbers. The scale's domain is zero-anchored
/// over the rows that draw a point, as production folds it from `(0, 0)` over the rows it keeps; a row
/// with no x draws none and does not widen it.
fn size_scale(field: &str, dataset: &Dataset, decoded: &Decoded, drawn: &[(usize, XCell<'_>, YCell<'_>)]) -> Result<SizeScale, Diagnostic> {
    if !dataset.schema.get(field).map_err(Diagnostic::refused)?.is_number() {
        return Err(unsupported(refuse!("a scatter `size:` on a column that is not a declared number is not supported")));
    }
    let by_row = decoded
        .keys(field)?
        .iter()
        .map(|key| value(key)?.ok_or_else(|| unsupported(refuse!("a scatter `size:` with a null value is not supported"))))
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let domain = drawn.iter().map(|(row, ..)| by_row[*row]).fold((0.0_f64, 0.0_f64), |(lo, hi), v| (lo.min(v), hi.max(v)));
    Ok(SizeScale { domain, by_row })
}

/// The shape channel `field`: a column of text over rows that all draw a point, whose categories in
/// ascending order take the symbols of Vega's range (`ShapeScale`). `color` is the chart's own color field,
/// whose legend would carry the shapes when it is the same column, which production merges into one
/// legend and this port does not.
fn shape_scale(
    field: &str,
    color: Option<&str>,
    faceted: bool,
    dataset: &Dataset,
    decoded: &Decoded,
    drawn: &[(usize, XCell<'_>, YCell<'_>)],
    font: &FontFamily,
) -> Result<ShapeScale, Diagnostic> {
    if faceted {
        return Err(unsupported(refuse!("a scatter `shape:` with `multiples:` is not supported")));
    }
    if color == Some(field) {
        return Err(unsupported(refuse!("a scatter `shape:` on its `color:` field is not supported")));
    }
    let keys = decoded.keys(field)?;
    if drawn.len() != keys.len() {
        return Err(unsupported(refuse!("a scatter `shape:` with a row that draws no point is not supported")));
    }
    let domain = color_domain_of(&dataset.rows, field);
    let by_row = keys
        .iter()
        .map(|key| match key {
            Key::Text(text) => domain.binary_search(text).map_err(|_| diag::err_internal(format!("the shape {text:?} is outside its domain"))),
            Key::Null => Err(unsupported(refuse!("a scatter row with a null `shape:` is not supported"))),
            _ => Err(unsupported(refuse!("a scatter `shape:` that is not text is not supported"))),
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    Ok(ShapeScale { title: field.to_string(), domain, by_row, font: font.as_ref().to_string() })
}

/// `multiples:` as the partition reads it: each axis's field with the keys decoded per row. A field
/// the query lacks is production's `ERR-MULTIPLES-FIELD-NOT-FOUND`, found by the partition's own scan.
fn facet_fields<'a>(
    multiples: &'a MultiplesConfig,
    decoded: &'a crate::decode::Decoded,
    dataset: &column_types::Dataset,
) -> Result<Facets<'a>, Diagnostic> {
    if multiples.rows.is_some() && multiples.rows == multiples.columns {
        return Err(diag::err_multiples_self_crossed(multiples.rows.clone()));
    }
    let axis = |field: &'a Option<String>| -> Result<Option<FacetAxis<'a>>, Diagnostic> {
        let Some(field) = field else { return Ok(None) };
        let keys = if dataset.schema.get(field).is_ok() { decoded.keys(field)? } else { &[] };
        Ok(Some(FacetAxis { field, keys }))
    };
    Ok(Facets { rows: axis(&multiples.rows)?, columns: axis(&multiples.columns)?, x: Sharing::Shared, y: Sharing::Shared, mirror: false })
}

fn datum(row: usize, x: Pos, y: f64, series: usize, tooltip: Tooltip) -> Datum {
    Datum { x, y0: 0.0, y1: y, series, row: RowRef(row), label: None, tooltip, href: None, defined: true }
}
