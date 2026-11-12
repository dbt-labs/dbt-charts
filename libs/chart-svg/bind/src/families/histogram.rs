//! Histogram: one bar per bin of a quantitative x, as tall as the rows that land in it. Production's
//! laws composed: d3's `bin()` at Vega-Lite's `autoMaxBins` (10) over the x (a constant column takes
//! vega-statistics' one-step extent), the x scale ticked at every bin edge, and a count axis from zero to
//! d3's nice of the tallest bin. A row with a null x lands in no bin yet joins the count domain as a group
//! of its own. The count axis never bakes a headroom or a count, and a binned scale ticks every edge whatever
//! `ticks.count` says, as production does. An empty bin draws nothing but keeps its place on the x scale.
//! What the bind does not model yet refuses by name.
//!
//! Binds a quantitative x to a `Family::Cartesian` with one `Mark::Bins` layer, a datum per populated bin
//! at `Pos::Span(start, end)`. `tests/it/histogram.rs` replays `histogram_production/`, production's final
//! SVG of each YAML twin, recorded by `record_histogram_bind.py`.

use board_model::board::Board;
use board_model::chart::BarChart;
use board_model::ids::ChartId;
use d3rs::bin::{bin, vega_constant_extent, Thresholds};
use diagnostic::{diag, refuse, Diagnostic};
use model_core::SetLeaves;

use crate::column_kind::ChannelType;
use crate::bound::{
    self, AuthoredLegend, Baselines, BinStyle, Bounds, BoundsReason, Datum, Extent, FixedLadder, Ladder, Mark, MarkLayer, MarkStyle,
    Naming, Orient, Panels, Pos, RowRef, Rung, Scale, ScaleKind, Side, Tooltip, TooltipEntry, ValueAxis, ValueDomain, Zero,
};
use crate::decode::decode;
use crate::derived::Placed;
use crate::families::cartesian::{self, axis_format, no_data, plain_text, single_series, unsupported, value, Prelude};
use crate::formats::locale::{active_number_locale, d3_format};
use crate::run::RunResult;

/// The thresholds a histogram asks d3's `bin()` for: Vega-Lite's `autoMaxBins("x")`, which a bare `bin: true`
/// takes. The theme's `bin_maxbins` is not read, as production does not.
const MAXBINS: f64 = 10.0;

fn internal(context: &str) -> impl Fn(bound::Invalid) -> Diagnostic + '_ {
    move |invalid| diag::err_internal(format!("histogram {context} is invalid: {invalid:?}"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &BarChart) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    let unported = [
        ("`color:`", fields.color.is_some()),
        ("`layers:`", !chart.layers.is_empty()),
        ("`annotations:`", !chart.annotations.is_empty()),
        ("`multiples:`", fields.multiples.is_some()),
        ("`tooltip:` columns", !chart.tooltip.tooltip.is_empty()),
        ("`link:`", fields.shared.base.link.is_some()),
        ("`sort:`", fields.sort.is_some()),
        ("`support_table:`", fields.support_table.is_some()),
        ("`pattern:`", chart.pattern.pattern.is_some()),
        ("a multi-line `x_label:` or `y_label:`", [&fields.x_label, &fields.y_label].into_iter().flatten().any(|label| label.contains('\n'))),
    ];
    match unported.into_iter().find_map(|(field, set)| set.then_some(field)) {
        Some(field) => Err(unsupported(refuse!("a histogram with {field} is not supported"))),
        None => Ok(()),
    }
}

/// The leaves of the chart's own style the walks read: its axes, legend, title and bar paint. A histogram's
/// own `style:` is written into `charts.bar` (`family_slots!`) while its marks and frame are read from
/// `charts.histogram`, so any other leaf would draw with the theme's value.
const OWN_WALKED: [&str; 5] = ["axis_x", "axis_y", "legend", "title", "marks.bar.color"];

fn refuse_own_style(placed: &Placed<'_>) -> Result<(), Diagnostic> {
    let mut unwalked = None;
    placed.own.charts.bar.set_leaves("", &mut |path| {
        if !OWN_WALKED.iter().any(|walked| path == *walked || path.starts_with(&format!("{walked}."))) {
            unwalked.get_or_insert_with(|| path.to_string());
        }
    });
    match unwalked {
        Some(leaf) => Err(unsupported(refuse!("a histogram that sets `style.{leaf}` is not supported"))),
        None => Ok(()),
    }
}

/// `chart` bound over its run, in the style of the board body that places it. No rows is
/// [`bound::Cartesian::NoData`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(id: &ChartId, chart: &BarChart, board: &Board, run: &RunResult, placed: &Placed<'_>) -> Result<bound::Chart, Diagnostic> {
    refuse_unported(chart)?;
    refuse_own_style(placed)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let p = Prelude::counting(&chart.cartesian, ChannelType::Quantitative, placed)?;
    let x_field = p.x_field;
    let x_title = chart.cartesian.x_label.clone().unwrap_or_else(|| p.x_title.clone());
    let x_format = axis_format(&p.x_ladder, p.formats)?;
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[x_field])?;
    if dataset.rows.is_empty() {
        let frame = Some(placed.derive()?.frame);
        return Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(no_data(placed, "histogram")?)) });
    }
    if !dataset.schema.get(x_field).map_err(Diagnostic::refused)?.is_number() {
        return Err(unsupported(refuse!("a histogram on a column that is not a declared number is not supported")));
    }
    let decoded = decode(dataset)?;
    let xs = decoded.keys(x_field)?.iter().map(value).collect::<Result<Vec<_>, _>>()?;
    // A non-finite value is no number to d3: it sets no extent and lands in no bin.
    let numbers: Vec<f64> = xs.iter().map(|x| x.filter(|x| x.is_finite()).unwrap_or(f64::NAN)).collect();
    let Some(first) = numbers.iter().copied().find(|x| !x.is_nan()) else {
        return Err(unsupported(refuse!("a histogram whose rows hold no number is not supported")));
    };
    // d3's extent of a repeated value is zero wide; Vega widens it to one step.
    let constant = numbers.iter().all(|x| x.is_nan() || *x == first).then(|| vega_constant_extent(first, MAXBINS));
    let bins = bin(&numbers, Thresholds::Count(MAXBINS), constant);
    let (start, stop) = match (bins.first(), bins.last()) {
        (Some(first), Some(last)) => (first.x0, last.x1),
        _ => return Err(diag::err_internal("d3 bins a column with a number into at least one bin")),
    };
    if stop <= start {
        return Err(diag::err_internal("a histogram's bins span a domain with width"));
    }
    // Every row with no number in x forms a group of its own: no bar, but it sets the count domain's top.
    let unbinned = numbers.iter().filter(|x| x.is_nan()).count();
    let tallest = bins.iter().map(|bin| bin.members.len()).max().ok_or_else(|| diag::err_internal("d3 bins a column with a number into at least one bin"))?.max(unbinned);

    let edges: Vec<f64> = bins.iter().map(|bin| bin.x0).chain([stop]).collect();
    // Each edge prints as an unbaked rung does: production's x labels come from the axis slot's format alone.
    let locale = active_number_locale();
    let extent_hi = numbers.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let rungs = edges
        .iter()
        .enumerate()
        .map(|(index, value)| Ok(Rung { value: *value, text: x_format.paint_unbaked_rung(&locale, *value, Some((index, edges.len())), extent_hi)? }))
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let x_extent = Extent::new(start, stop).map_err(internal("x extent"))?;
    let x_domain = ValueDomain {
        extent: x_extent,
        zero: Zero::Free,
        headroom: 0.0,
        authored: None,
        ladder: Ladder::Fixed(FixedLadder::new(rungs, x_extent).map_err(internal("x ladder"))?),
        min: None,
        max: None,
        bounds: Bounds { extent: x_extent, reason: BoundsReason::Fitted },
    };
    let top = d3rs::ticks::d3_nice(0.0, tallest as f64, 10).1;
    let y_extent = Extent::new(0.0, tallest as f64).map_err(internal("y extent"))?;
    let y_domain = ValueDomain {
        extent: y_extent,
        zero: Zero::Anchored,
        headroom: 0.0,
        authored: None,
        ladder: Ladder::Counts,
        min: None,
        max: None,
        bounds: Bounds { extent: Extent::new(0.0, top).map_err(internal("y bounds"))?, reason: BoundsReason::Niced },
    };
    let y_format = axis_format(&p.y_ladder, p.formats)?;
    let (axis_styles, [x_shown, y_shown]) = cartesian::axes((&p.x_ladder, x_title.clone(), chart.cartesian.x_label.as_deref().is_some_and(|label| !label.is_empty())), (&p.y_ladder, p.y_title.clone(), chart.cartesian.y_label.as_deref().is_some_and(|label| !label.is_empty())), &y_domain, placed.scope, "histogram", true, false)?;

    if axis_styles.y[0].side == Side::Right {
        return Err(unsupported(refuse!("a histogram with its count axis on the right is not supported")));
    }
    let counts: Vec<f64> = bins.iter().map(|bin| bin.members.len() as f64).collect();
    if p.tooltip_format(&counts)?.has_affix() {
        return Err(unsupported(refuse!("a histogram on a tooltip format with an affix is not supported")));
    }
    let x_spec = x_format.spec.as_str();
    let datums = bins
        .iter()
        .enumerate()
        .filter(|(_, bin)| !bin.members.is_empty())
        .map(|(index, bin)| {
            let span = format!("{} \u{2013} {}", d3_format(x_spec, bin.x0).map_err(diag::err_internal)?, d3_format(x_spec, bin.x1).map_err(diag::err_internal)?);
            let count = bin.members.len();
            let count_text = d3_format(&y_format.spec, count as f64).map_err(diag::err_internal)?;
            Ok(Datum {
                x: Pos::Span(bin.x0, bin.x1),
                y0: 0.0,
                y1: count as f64,
                series: 0,
                row: RowRef(index),
                label: None,
                tooltip: Tooltip {
                    header: None,
                    entries: vec![
                        TooltipEntry { title: x_title.clone(), value: span },
                        TooltipEntry { title: p.y_title.clone(), value: count_text },
                    ],
                },
                href: None,
                defined: true,
            })
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;

    let derived = placed.derive()?;
    let frame = Some(derived.frame.clone());
    let series = single_series(placed, p.y_field, &p.y_title, &derived)?;
    let bar = &placed.style.charts.histogram.marks.bar;
    let baselines = Baselines { zero: false, unity: false, x_threshold: false };
    let plot = bound::Plane::new(bound::PlaneParts {
        orient: Orient::Vertical,
        x: Scale { kind: ScaleKind::Linear(ValueAxis { domain: x_domain, format: x_format }), title: x_shown },
        y: vec![Scale { kind: ScaleKind::Linear(ValueAxis { domain: y_domain, format: y_format }), title: y_shown }],
        panels: Panels::Single,
        series: vec![series],
        layers: vec![MarkLayer { mark: Mark::Bins, y_scale: 0, panels: vec![datums] }],
        naming: Naming { rail: None, entries: Vec::new(), asks_top_strip: false, authored: AuthoredLegend::Side(derived.legend.clone()) },
        legends: Vec::new(),
        support: None,
        baselines,
        axis_styles,
        diagnostics: crate::restated::axes(&p.tiers, [&p.x_ladder, &p.y_ladder], placed.public_id),
        mark_style: MarkStyle { points: None, lines: None, bars: None, bins: Some(BinStyle { radius: bar.border.radius, band_width: bar.band_width }) },
    })
    .map_err(internal("plot"))?;
    Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(bound::Cartesian::Plane(Box::new(plot)))) })
}
