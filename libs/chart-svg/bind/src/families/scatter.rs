//! Scatter: one point per row on a quantitative x and y. Production's laws composed: the
//! smart-zero verdicts (`plot::zero`), the linear domain (`plot::domain`), the axis and
//! tooltip formats (`formats`) and the zero-rule gates. A row with a null x draws nothing,
//! yet its y joins the y domain, as Vega-Lite's invalid filter keeps it there. What the
//! bind does not model yet refuses by name.
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
//! Binds a quantitative x and y to a `Family::Cartesian`; any other x or y type is refused by name.
//! `tests/it/scatter.rs` replays `scatter_production.json`, production's final SVG of each YAML
//! twin, recorded by `record_scatter_bind.py`.

use std::collections::HashMap;

use board_model::board::Board;
use board_model::chart::{ColorColumn, Columns, MultiplesConfig, MultiplesScale, ScatterChart};
use board_model::ids::ChartId;
use board_model::style::font::SymbolShape;
use board_model::style::marks::LabelVisible;
use model_core::Engine;
use diagnostic::{diag, refuse, Diagnostic};

use crate::channel_type::{classify, ChannelType};
use crate::decode::decode;
use crate::derived::Placed;
use crate::category_colors::CategoryColorScale;
use crate::families::cartesian::{self, axis_format, count, no_data, plain_text, single_series, threshold_rules, unsupported, value, Prelude};
use crate::legend::fold_symbol_limit;
use crate::effective_palette;
use crate::formats::locale::{active_number_locale, NumberLocale};
use crate::formats::ResolvedFormat;
use crate::bound::{self,
    AuthoredLegend, Baselines,  Bounds, BoundsReason, Extent, Datum, Mark, MarkLayer, MarkStyle, Naming, Orient,
    FixedLadder, Ladder, PlaneParts, Sharing, Pos, RowRef, Rung, Scale, ScaleKind, Shape,
    PointStyle, Toggle, Tooltip, TooltipEntry, ValueAxis, ValueDomain, Zero,
};
use crate::plot::domain::{self, Count};
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
        ("`size:`", chart.size.is_some()),
        ("`shape:`", chart.shape.is_some()),
        ("`layers:`", !chart.layers.is_empty()),
        ("`annotations:`", !chart.annotations.is_empty()),
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

/// Panels on their own scales are not modeled yet. A mirrored y axis is refused with every scatter's
/// axis leaves.
fn refuse_unported_facets(multiples: &MultiplesConfig) -> Result<(), Diagnostic> {
    match multiples.scale {
        MultiplesScale::Shared => Ok(()),
        MultiplesScale::Independent => Err(unsupported(refuse!("a scatter with `multiples.scale: independent` is not supported"))),
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
        domain: ladder.domain(),
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
    let p = Prelude::of(&chart.cartesian, ChannelType::Quantitative, placed)?;
    let (x_field, y_field) = (p.x_field, p.y_field);
    let x_format = axis_format(&p.x_ladder, p.formats)?;
    let y_format = axis_format(&p.y_ladder, p.formats)?;
    let color_field = chart.cartesian.color.as_ref().map(ColorColumn::as_str);
    let fields: Vec<&str> = [x_field, y_field].into_iter().chain(color_field).collect();
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &fields)?;
    if dataset.rows.is_empty() {
        let frame = Some(placed.derive()?.frame);
        return Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(no_data(placed, "scatter")?)) });
    }
    for field in [x_field, y_field] {
        if !dataset.schema.get(field).map_err(Diagnostic::refused)?.is_number() {
            return Err(unsupported(refuse!("a scatter on a column that is not a declared number is not supported")));
        }
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
    if color_field.is_some() && drawn.len() != xs.len() {
        return Err(unsupported(refuse!("a scatter `color:` with a row that draws no point is not supported")));
    }
    // A year-shaped x reads as a time axis, as production's x-type law rules.
    for (field, dimension) in [(x_field, true), (y_field, false)] {
        if classify(&dataset.schema, &dataset.rows, Some(field), dimension)? != ChannelType::Quantitative {
            return Err(unsupported(refuse!("a scatter on a year-shaped x is not supported")));
        }
    }

    let x_zero = zero::scatter_x(p.x_ladder.zero_setting(), p.x_ladder.domain().is_some(), false, &x_values);
    let y_zero = zero::verdict(Family::Scatter, p.y_ladder.zero_setting(), &y_values);
    let x_axis = value_axis(&p.x_ladder, Baked::X, x_zero, &x_values, x_format, "x domain")?;
    let y_axis = value_axis(&p.y_ladder, Baked::Y, y_zero, &y_values, y_format, "y domain")?;
    let (axis_styles, [x_shown, y_shown]) = cartesian::axes((&p.x_ladder, p.x_title.clone(), false), (&p.y_ladder, p.y_title.clone(), false), &y_axis.domain, placed.scope, "scatter", true, true)?;

    let tooltip_values: Vec<f64> = y_values.iter().chain(&x_values).copied().collect();
    let y_tooltip = p.tooltip_format(&tooltip_values)?;
    // Production paints the x row with the tooltip format's spec alone, no affix or register.
    let x_tooltip = ResolvedFormat { spec: y_tooltip.spec.clone(), ..ResolvedFormat::default() };
    let locale = active_number_locale();
    // Production's panel marks carry Vega-Lite's own description, x then y; a single plot's tooltip
    // reads y then x.
    let faceted = chart.cartesian.multiples.is_some();
    let mut panel_of = vec![0; dataset.rows.len()];
    for (panel, rows) in partition.panels.iter().enumerate() {
        for &row in &rows.rows {
            panel_of[row] = panel;
        }
    }
    let mut panels: Vec<Vec<Datum>> = vec![Vec::new(); partition.panels.len()];
    for (row, x, y) in drawn {
        let entries = if faceted {
            [(&p.x_title, &x_tooltip, x), (&p.y_title, &y_tooltip, y)]
        } else {
            [(&p.y_title, &y_tooltip, y), (&p.x_title, &x_tooltip, x)]
        };
        panels[panel_of[row]].push(datum(row, x, y, entries, &locale)?);
    }
    if panels.iter().any(Vec::is_empty) {
        return Err(unsupported(refuse!("a scatter panel whose rows draw no point is not supported")));
    }

    let baselines = Baselines {
        zero: cartesian::zero_rule(&p.y_ladder, y_zero, &y_values)?,
        unity: false,
        x_threshold: threshold_rules(&p.x_ladder)? && x_holds_zero(&x_axis, x_zero, &x_values),
    };
    let derived = placed.derive()?;
    let frame = Some(derived.frame.clone());
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
    let mut panels = panels;
    if let Some(colors) = &colors {
        for datum in panels.iter_mut().flatten() {
            datum.series = colors.of_row[datum.row.0];
            datum.tooltip.header = Some(colors.series[datum.series].display.clone());
        }
    }
    let (series, legends) = match colors {
        Some(colors) => {
            let labels: HashMap<String, String> = chart.cartesian.labels.field_labels.clone().into_iter().collect();
            let title = display_text::field_title(color_field.expect("a color series has its field"), &labels).map_err(diag::err_internal)?;
            let mut legend = colors.legend(title);
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
        x: Scale { kind: ScaleKind::Linear(x_axis), title: x_shown },
        y: vec![Scale { kind: ScaleKind::Linear(y_axis), title: y_shown }],
        panels: partition.layout,
        series,
        layers: vec![MarkLayer { mark: Mark::Points { shape: Shape::Circle, size: None }, y_scale: 0, panels }],
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
        mark_style: MarkStyle { points: Some(point_style(chart)?), bins: None },
    })
    .map_err(internal("plot"))?;
    Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(bound::Cartesian::Plane(Box::new(plot)))) })
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
        tooltip: Tooltip { header: None, entries },
        href: None,
        defined: true,
    })
}
