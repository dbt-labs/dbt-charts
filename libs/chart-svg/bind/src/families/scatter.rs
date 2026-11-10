//! Scatter: one point per row on a quantitative x and y. Production's laws composed: the
//! smart-zero verdicts (`plot::zero`), the linear domain (`plot::domain`), the axis and
//! tooltip formats (`formats`) and the zero-rule gates. A row with a null x draws nothing,
//! yet its y joins the y domain, as Vega-Lite's invalid filter keeps it there. What the
//! bind does not model yet refuses by name.

use std::collections::HashMap;

use board_model::board::{Board, BoardBody};
use board_model::chart::{Columns, ScatterChart};
use board_model::ids::ChartId;
use board_model::style::font::SymbolShape;
use board_model::style::marks::LabelVisible;
use model_core::Engine;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};

use crate::channel_type::{classify, ChannelType};
use crate::decode::decode;
use crate::derived::Placed;
use crate::families::cartesian::{self, axis_format, count, hex, plain_text, threshold_rules, unsupported, value};
use crate::formats::locale::{active_number_locale, NumberLocale};
use crate::formats::{column_format, AuthoredFormat, Formats, Kind, ResolvedFormat, Tiers};
use crate::model::{
    AuthoredLegend, AxisStyles, Baselines, Body, BoundChart, Bounds, BoundsReason, Extent, Datum, Mark, MarkLayer, MarkStyle, Naming, Orient,
    FixedLadder, Ladder, Paint, Panels, Plot, PlotParts, Pos, RowRef, Rung, Scale, ScaleKind, Series, SeriesKey, Shape,
    PointStyle, Tooltip, TooltipEntry, ValueAxis, ValueDomain, Zero,
};
use crate::plot::domain::{self, Count};
use crate::plot::zero::{self, Family};
use crate::run::RunResult;
use crate::style::axis::{AxisLadder, AxisName};
use crate::style::{Tier, Tiers as StyleTiers};

fn internal(context: &str) -> impl Fn(crate::model::Invalid) -> Diagnostic + '_ {
    move |invalid| diag::err_internal(format!("scatter {context} is invalid: {invalid:?}"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &ScatterChart) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    let unported = [
        ("`color:`", fields.color.is_some()),
        ("`size:`", chart.size.is_some()),
        ("`shape:`", chart.shape.is_some()),
        ("`layers:`", !chart.layers.is_empty()),
        ("`annotations:`", !chart.annotations.is_empty()),
        ("`multiples:`", fields.multiples.is_some()),
        ("`tooltip:` columns", !chart.tooltip.tooltip.is_empty()),
        ("a list `y:`", matches!(fields.y, Some(Columns::Many(_)))),
        ("`x_label:` or `y_label:`", fields.x_label.is_some() || fields.y_label.is_some()),
        ("`sort:`", fields.sort.is_some()),
        ("`link:`", fields.shared.base.link.is_some()),
    ];
    match unported.into_iter().find_map(|(field, set)| set.then_some(field)) {
        Some(field) => Err(unsupported(refuse!("a scatter with {field} is not supported"))),
        None => Ok(()),
    }
}

/// The point mark as production draws it: the chart's total `marks.point`. Only a constant size
/// in a filled circle of the series color is ported; an `auto` stroke width draws no outline,
/// as production's `stroke_width.unwrap_or(0.0)`.
fn point_style(chart: &ScatterChart) -> Result<PointStyle, Diagnostic> {
    let point = &chart.style.marks.point;
    let Engine::Value(size) = point.size else {
        return Err(unsupported(refuse!("a scatter point mark with an `auto` size is not supported")));
    };
    let unported = [
        ("a `shape`", !matches!(point.shape, Engine::Rule(_) | Engine::Value(SymbolShape::Circle))),
        ("a `color`", !matches!(point.color, Engine::Rule(_))),
        ("`filled: false`", !point.filled),
        ("value labels", !matches!(point.labels.base.visible, LabelVisible::Off)),
    ];
    if let Some(aspect) = unported.into_iter().find_map(|(aspect, set)| set.then_some(aspect)) {
        return Err(unsupported(refuse!("a scatter point mark with {aspect} is not supported")));
    }
    let stroke_width = match point.stroke_width {
        Engine::Value(width) => width,
        Engine::Rule(_) => 0.0,
    };
    Ok(PointStyle { size, opacity: point.opacity, stroke_width })
}

/// How production reads a scatter axis: it bakes the y axis (headroom, count, the step floor) and
/// leaves x unbaked, ticked by Vega at its count with no headroom, so an x headroom refuses.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Baked {
    X,
    Y,
}

/// The value axis over `values`.
fn value_axis(
    ladder: &AxisLadder<'_>,
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
    let axis = domain::Axis {
        zero,
        headroom,
        count: axis_count,
        values: None,
        domain: None,
        min_step,
        independent: false,
        nice: None,
        mark: domain::Mark::Points,
    };
    let mut domain = cartesian::linear_domain(&axis, values, &format, column_forming, context)?;
    if domain.extent.lo() == domain.extent.hi() {
        return Err(unsupported(refuse!("a scatter axis over a single value is not supported")));
    }
    if baked == Baked::X {
        let target = count(ladder)?.unwrap_or(Count::Auto).target();
        // Production fits an x to its data only clear of zero; a fit across zero stays niced.
        if zero == Zero::Free && !cartesian::spans_zero(values) {
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

/// `chart` bound over its run, in the style of `body`, the board body that places it. No rows is
/// [`Body::Empty`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(
    id: &ChartId,
    chart: &ScatterChart,
    board: &Board,
    body: &BoardBody,
    run: &RunResult,
    placed: &Placed<'_>,
) -> Result<BoundChart, Diagnostic> {
    refuse_unported(chart)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let style: &Style = body
        .style
        .as_deref()
        .ok_or_else(|| diag::err_internal("a normalized board carries its total style"))?;
    if !style.formats.is_empty() {
        return Err(unsupported(refuse!("a scatter on a board with `style.formats` aliases is not supported")));
    }
    let formats = Formats::new(None);
    let x_field = chart
        .cartesian
        .x
        .as_ref()
        .ok_or_else(|| diag::err_internal("normalize requires a scatter's `x:`"))?
        .as_str();
    let y_field = match &chart.cartesian.y {
        Some(Columns::One(y)) => y.as_str(),
        _ => return Err(diag::err_internal("normalize requires a scatter's `y:`")),
    };
    let tiers = StyleTiers { own: Tier::from(&*placed.own.charts.scatter), family: Tier::from(&*style.charts.scatter), charts: &style.charts };
    let x_ladder = AxisLadder::new(AxisName::X, ChannelType::Quantitative, &tiers, None, false)?;
    let y_ladder = AxisLadder::new(AxisName::Y, ChannelType::Quantitative, &tiers, None, false)?;
    if let Some(leaf) = x_ladder.unwalked_leaf().or_else(|| y_ladder.unwalked_leaf()) {
        return Err(unsupported(refuse!("a scatter axis that sets `{leaf}` is not supported")));
    }
    let x_format = axis_format(&x_ladder, formats)?;
    let y_format = axis_format(&y_ladder, formats)?;
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[x_field, y_field])?;
    if dataset.rows.is_empty() {
        let frame = Some(placed.derive()?.frame);
        return Ok(BoundChart { title, subtitle, frame, body: Body::Empty });
    }
    for field in [x_field, y_field] {
        if !dataset.schema.get(field).map_err(Diagnostic::refused)?.is_number() {
            return Err(unsupported(refuse!("a scatter on a column that is not a declared number is not supported")));
        }
    }
    let decoded = decode(dataset)?;
    let (xs, ys) = (decoded.keys(x_field)?, decoded.keys(y_field)?);
    let mut drawn = Vec::new();
    let (mut x_values, mut y_values) = (Vec::new(), Vec::new());
    for (row, (x, y)) in xs.iter().zip(ys).enumerate() {
        match (value(x)?, value(y)?) {
            (Some(x), Some(y)) => {
                drawn.push((row, x, y));
                x_values.push(x);
                y_values.push(y);
            }
            (Some(_), None) => {
                return Err(unsupported(refuse!("a scatter row with a null y beside a value x is not supported")))
            }
            (None, Some(y)) => y_values.push(y),
            (None, None) => {}
        }
    }
    if drawn.is_empty() {
        return Err(unsupported(refuse!("a scatter whose rows draw no point is not supported")));
    }
    // A year-shaped x reads as a time axis, as production's x-type law rules.
    for (field, dimension) in [(x_field, true), (y_field, false)] {
        if classify(&dataset.schema, &dataset.rows, Some(field), dimension)? != ChannelType::Quantitative {
            return Err(unsupported(refuse!("a scatter on a year-shaped x is not supported")));
        }
    }

    let labels: HashMap<String, String> = chart.cartesian.labels.field_labels.clone().into_iter().collect();
    let x_title = display_text::field_title(x_field, &labels).map_err(diag::err_internal)?;
    let y_title = display_text::field_title(y_field, &labels).map_err(diag::err_internal)?;

    let x_zero = zero::scatter_x(None, false, false, &x_values);
    let y_zero = zero::verdict(Family::Scatter, None, &y_values);
    let x_axis = value_axis(&x_ladder, Baked::X, x_zero, &x_values, x_format, "x domain")?;
    let y_axis = value_axis(&y_ladder, Baked::Y, y_zero, &y_values, y_format, "y domain")?;

    let tooltip_values: Vec<f64> = y_values.iter().chain(&x_values).copied().collect();
    let board_tooltip = AuthoredFormat::named(style.charts.tooltip.format.as_str());
    let y_tooltip = column_format(Kind::Number, Tiers { own: Some(&board_tooltip), ..Tiers::default() }, formats, None, None, &tooltip_values)?;
    // Production paints the x row with the tooltip format's spec alone, no affix or register.
    let x_tooltip = ResolvedFormat { spec: y_tooltip.spec.clone(), ..ResolvedFormat::default() };
    let locale = active_number_locale();
    let datums = drawn
        .into_iter()
        .map(|(row, x, y)| datum(row, x, y, [(&y_title, &y_tooltip, y), (&x_title, &x_tooltip, x)], &locale))
        .collect::<Result<Vec<_>, _>>()?;

    let baselines = Baselines {
        zero: cartesian::zero_rule(&y_ladder, y_zero, &y_values)?,
        unity: false,
        x_threshold: threshold_rules(&x_ladder)? && cartesian::spans_zero(&x_values),
    };
    let derived = placed.derive()?;
    let frame = Some(derived.frame.clone());
    let fill = placed.single_series_fill()?;
    let ink = color::label_ink(&hex(fill), &hex(derived.frame.canvas)).map_err(diag::err_internal)?;
    let series = Series {
        key: SeriesKey::Measure(y_field.to_string()),
        display: y_title.clone(),
        paint: Paint::Solid(fill),
        ink: color::parse_css_color(&ink).map_err(diag::err_internal)?,
    };
    let plot = Plot::new(PlotParts {
        orient: Orient::Vertical,
        x: Scale { kind: ScaleKind::Linear(x_axis), title: Some(x_title) },
        y: vec![Scale { kind: ScaleKind::Linear(y_axis), title: Some(y_title) }],
        panels: Panels::Single,
        series: vec![series],
        layers: vec![MarkLayer { mark: Mark::Points { shape: Shape::Circle, size: None }, y_scale: 0, panels: vec![datums] }],
        naming: Naming {
            rail: None,
            entries: Vec::new(),
            asks_top_strip: false,
            authored: AuthoredLegend::Side(derived.legend.clone()),
        },
        legends: Vec::new(),
        support: None,
        baselines,
        axis_styles: AxisStyles {},
        mark_style: MarkStyle { points: Some(point_style(chart)?) },
    })
    .map_err(internal("plot"))?;
    Ok(BoundChart { title, subtitle, frame, body: Body::Plot(Box::new(plot)) })
}

fn datum(
    row: usize,
    x: f64,
    y: f64,
    tooltip: [(&String, &ResolvedFormat, f64); 2],
    locale: &NumberLocale,
) -> Result<Datum, Diagnostic> {
    let entries = tooltip
        .into_iter()
        .map(|(title, format, value)| Ok(TooltipEntry { title: title.clone(), value: format.paint(locale, value)? }))
        .collect::<Result<_, Diagnostic>>()?;
    Ok(Datum {
        x: Pos::Num(x),
        y0: 0.0,
        y1: y,
        series: 0,
        row: RowRef(row),
        label: None,
        tooltip: Tooltip { entries },
        href: None,
        defined: true,
    })
}
