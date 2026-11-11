//! Line: one path through the rows on a time x and a quantitative y. Production's laws composed:
//! the x-type law (`x_type`), the time domain (`plot::time`), the smart-zero verdict
//! (`plot::zero`), the linear domain (`plot::domain`), the axis and tooltip formats (`formats`)
//! and the zero-rule gate. A row with a null y keeps its place in x order and breaks the path.
//! What the bind does not model yet refuses by name.
//!
//! Binds a date x and one value y to a `Family::Cartesian` of one `Mark::Lines` series on a time x;
//! a quantitative x is refused by name. A y that production's sampled rule
//! (`channel_type::y_is_numeric`: the first 20 non-null cells, numeric-looking text counting as
//! a number, no cell never tripping it) calls non-numeric is the chart's
//! `ERR-LINE-Y-NOT-NUMERIC` error card (a bar's is `ERR-BAR-Y-NOT-NUMERIC`), named by its public
//! id. A text-declared y the rule does not call non-numeric is refused by name.
//! `tests/it/line.rs` replays `line_production.json`, recorded by `record_line_bind.py`.

use std::collections::HashSet;

use board_model::board::Board;
use board_model::chart::{Columns, LineChart};
use board_model::ids::ChartId;
use board_model::primitives::Curve;
use board_model::style::axis::FillMode;
use board_model::style::marks::LabelVisible;
use diagnostic::{codes, diag, py_str_repr, refuse, Diagnostic};
use model_core::Engine;

use crate::channel_type::{classify, y_is_numeric, ChannelType};
use crate::decode::decode;
use crate::derived::{DerivedChart, Placed};
use crate::families::cartesian::{self, axis_format, measure_axis, no_data, plain_text, single_series, unsupported, value, zero_rule, Prelude};
use crate::formats::locale::active_number_locale;
use crate::bound::{self,
    AuthoredLegend, AxisStyles, Baselines,  Datum, Failure, GrainDrop, Key, Mark, MarkLayer, MarkStyle, Naming, Orient,
    Panels, Pos, RowRef, Scale, ScaleKind, Series, Tooltip, TooltipEntry,
};
use crate::plot::domain;
use crate::plot::time::{time_domain, TimeInput, TimeUnitSetting};
use crate::plot::zero::{self, Family};
use crate::rows::is_year_shaped;
use crate::run::RunResult;
use crate::style::Tiers as StyleTiers;
use crate::temporal::VegaType;
use crate::x_type::{resolve_cartesian_x_type, Grain, XAxis};

/// `core/constants/axis.py::MAX_ORDINAL_BUCKETS`: the most empty buckets a calendar grain may
/// span before the x-type law drops it.
const MAX_ORDINAL_BUCKETS: usize = 60;

/// What a line chart's laws bind: the two scales, the one series and a datum per row.
#[derive(Debug)]
pub struct Lines {
    pub x: Scale,
    pub y: Scale,
    pub series: Series,
    /// One per row, in x order. A row with a null y is `defined: false`.
    pub datums: Vec<Datum>,
    pub baselines: Baselines,
    pub axis_styles: AxisStyles,
    /// The frame, fill and legend the placement derives, after the chart's data has passed its refusals.
    pub derived: DerivedChart,
    /// The chart's own axis keys the other tiers already answer.
    pub diagnostics: Vec<Diagnostic>,
}

fn internal(invalid: crate::bound::Invalid) -> Diagnostic {
    diag::err_internal(format!("line is invalid: {invalid:?}"))
}

/// The scope this bind does not model yet, refused by name before anything is read.
fn refuse_unported(chart: &LineChart) -> Result<(), Diagnostic> {
    let fields = &chart.cartesian;
    let marks = &chart.style.marks;
    let stroke = &marks.line.stroke;
    let unported = [
        ("`color:`", fields.color.is_some()),
        ("a list `y:`", matches!(fields.y, Some(Columns::Many(_)))),
        ("`layers:`", !chart.layers.is_empty()),
        ("`annotations:`", !chart.annotations.is_empty()),
        ("`multiples:`", fields.multiples.is_some()),
        ("`tooltip:` columns", !chart.tooltip.tooltip.is_empty()),
        ("`sort:`", fields.sort.is_some()),
        ("`connect:`", chart.connect.is_some()),
        ("`link:`", fields.shared.base.link.is_some()),
        ("`x_label:` or `y_label:`", fields.x_label.is_some() || fields.y_label.is_some()),
        ("`support_table:`", fields.support_table.is_some()),
        (
            "value labels",
            marks.line.labels.base.visible != LabelVisible::Off || marks.point.labels.base.visible != LabelVisible::Off,
        ),
        ("a `marks.point.size`", !matches!(marks.point.size, Engine::Rule(_))),
    ];
    if let Some((field, _)) = unported.into_iter().find(|(_, set)| *set) {
        return Err(unsupported(refuse!("a line with {field} is not supported")));
    }
    if marks.line.curve != Curve::Linear {
        return Err(unsupported(refuse!("a line whose curve is not `linear` is not supported")));
    }
    let stroked = !matches!(stroke.color, Engine::Rule(_))
        || !matches!(stroke.width, Engine::Rule(_))
        || stroke.dasharray.is_some();
    if stroked {
        return Err(unsupported(refuse!("a line with a `marks.line.stroke` is not supported")));
    }
    Ok(())
}

/// The gap fill and the fiscal year move the x-type law's verdict; no tier of the time axis
/// may set either to other than the default.
fn refuse_time_axis_leaves(tiers: &StyleTiers<'_>) -> Result<(), Diagnostic> {
    let board = Some(&tiers.charts.axis_x);
    let moved = [tiers.own.axis_x, tiers.family.axis_x, board].into_iter().flatten().any(|axis| {
        axis.fill.is_some_and(|fill| fill != FillMode::Null) || axis.fiscal_year_start_month.is_some_and(|month| month != 1)
    });
    if moved {
        return Err(unsupported(refuse!("a line time axis that sets `fill` or `fiscal_year_start_month` is not supported")));
    }
    Ok(())
}

/// The laws of a line chart over its rows; `None` when the query returned no rows.
pub fn lines(
    id: &ChartId,
    chart: &LineChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed<'_>,
) -> Result<Option<Lines>, Diagnostic> {
    let p = Prelude::of(&chart.cartesian, ChannelType::Temporal, placed)?;
    let (x_field, y_field) = (p.x_field, p.y_field);
    refuse_time_axis_leaves(&p.tiers)?;
    if p.x_ladder.labels_format().is_some() {
        return Err(unsupported(refuse!("a line time axis with a label format is not supported")));
    }
    if p.x_ladder.ticks_count().is_some() {
        return Err(unsupported(refuse!("a line time axis with `ticks.count` is not supported")));
    }
    let y_format = axis_format(&p.y_ladder, p.formats)?;
    let dataset = cartesian::dataset(id, &chart.cartesian, board, run, &[x_field, y_field])?;
    if dataset.rows.is_empty() {
        return Ok(None);
    }
    let x_type = classify(&dataset.schema, &dataset.rows, Some(x_field), true)?;
    if !y_is_numeric(&dataset.schema, &dataset.rows, y_field)? {
        let error = diag::err_line_y_not_numeric(placed.public_id, y_field);
        return Err(match x_type {
            ChannelType::Quantitative => error.with_hint(format!("Column {} looks numeric — did you mean to swap x and y?", py_str_repr(x_field))),
            _ => error,
        });
    }
    match x_type {
        ChannelType::Temporal => {}
        ChannelType::Nominal => return Err(unsupported(refuse!("a line on a nominal x is not supported"))),
        ChannelType::Quantitative => return Err(unsupported(refuse!("a line on a quantitative x is not supported"))),
    }
    if is_year_shaped(&dataset.schema, &dataset.rows, x_field).map_err(Diagnostic::refused)? {
        return Err(unsupported(refuse!("a line on a year-shaped x is not supported")));
    }
    let decoded = decode(dataset)?;
    let (xs, ys) = (decoded.keys(x_field)?, decoded.keys(y_field)?);
    let texts = xs
        .iter()
        .map(|x| match x {
            Key::Text(text) | Key::Date { text, .. } => Ok(text.as_str()),
            Key::Null => Err(unsupported(refuse!("a line row with a null x is not supported"))),
            other => Err(diag::err_internal(format!("a temporal x column decoded to {other:?}"))),
        })
        .collect::<Result<Vec<_>, _>>()?;

    let authored_none = p.x_ladder.time_unit_none();
    let law_axis = XAxis {
        time_unit: authored_none.then_some("none"),
        authored_type: None,
        raw_type: None,
        fill: Some("null"),
        fiscal_year_start_month: 1,
        max_ordinal_buckets: MAX_ORDINAL_BUCKETS,
    };
    let law = resolve_cartesian_x_type(&dataset.schema, &dataset.rows, x_field, &law_axis, "line", false, &[])
        .map_err(Diagnostic::refused)?;
    if law.vl_type != VegaType::Temporal {
        return Err(unsupported(refuse!("a line on an x the x-type law does not read as a time scale is not supported")));
    }
    let time_unit = match law.grain {
        Grain::Calendar(unit) => TimeUnitSetting::Unit(unit),
        Grain::None => TimeUnitSetting::Absent,
        Grain::Part(_) => return Err(diag::err_internal("no time part is authored on a line")),
    };
    let mut seen = HashSet::new();
    let distinct: Vec<String> = texts.iter().filter(|text| seen.insert(**text)).map(|text| text.to_string()).collect();
    // The grain is still read into the domain (`unit`); the reason says why production labels the
    // axis without it.
    let grain_drop = (law.grain == Grain::None).then_some(if authored_none { GrainDrop::AuthoredNone } else { GrainDrop::ScaffoldBudget });
    let input = TimeInput { chart_id: id.as_str(), family: "line", x_field, time_unit, grain_drop, fill_gaps: false, fiscal_start: None };
    let domain = time_domain(&distinct, &input)?.ok_or_else(|| diag::err_internal("a line with rows has an x to span"))?;
    let instants = texts
        .iter()
        .map(|text| domain.axis.get(text).ok_or_else(|| diag::err_internal(format!("the time axis read every x, but not {text:?}"))))
        .collect::<Result<Vec<_>, _>>()?;
    if instants.iter().any(|instant| instant.timed || instant.nan) {
        return Err(unsupported(refuse!("a line on a timestamp or a year below 100 is not supported")));
    }
    let mut order: Vec<usize> = (0..texts.len()).collect();
    order.sort_by(|a, b| instants[*a].at.total_cmp(&instants[*b].at));
    if order.windows(2).any(|pair| instants[pair[0]].pos == instants[pair[1]].pos) {
        return Err(unsupported(refuse!("a line with an x in more than one row is not supported")));
    }
    let y_values = ys.iter().map(value).collect::<Result<Vec<_>, _>>()?;
    let drawn: Vec<f64> = y_values.iter().flatten().copied().collect();
    if drawn.is_empty() {
        return Err(unsupported(refuse!("a line whose rows hold no value is not supported")));
    }

    let y_zero = zero::verdict(Family::Line, p.y_ladder.zero_setting(), &drawn);
    // A single series is production's `apply_nice == false` arm: its baked headroom bounds stand
    // unrounded (`resolve_measure_domain`; only a rail or layers re-nice).
    let y_axis = measure_axis(&p.y_ladder, y_zero, &drawn, y_format, domain::Mark::Plotted, Some(false))?;
    let (axis_styles, [x_shown, y_shown]) = cartesian::axes((&p.x_ladder, p.x_title.clone(), false), (&p.y_ladder, p.y_title.clone(), false), &y_axis.domain, placed.scope, "line", false, false)?;

    let tooltip_format = p.tooltip_format(&drawn)?;
    let locale = active_number_locale();
    let datums = order
        .into_iter()
        .map(|row| {
            let y = y_values[row];
            let entries = match y {
                Some(y) => vec![TooltipEntry { title: p.y_title.clone(), value: tooltip_format.paint(&locale, y)? }],
                None => Vec::new(),
            };
            Ok(Datum {
                x: Pos::Time(instants[row].clone()),
                y0: 0.0,
                y1: y.unwrap_or(0.0),
                series: 0,
                row: RowRef(row),
                label: None,
                tooltip: Tooltip { header: None, entries },
                href: None,
                defined: y.is_some(),
            })
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;

    let baselines = Baselines { zero: zero_rule(&p.y_ladder, y_zero, &drawn)?, unity: false, x_threshold: false };
    let derived = placed.derive()?;
    let series = single_series(placed, y_field, &p.y_title, &derived)?;
    Ok(Some(Lines {
        x: Scale { kind: ScaleKind::Time(domain), title: x_shown },
        y: Scale { kind: ScaleKind::Linear(y_axis), title: y_shown },
        series,
        datums,
        baselines,
        axis_styles,
        derived,
        diagnostics: crate::restated::axes(&p.tiers, [&p.x_ladder, &p.y_ladder], placed.public_id),
    }))
}

/// `chart` bound over its run, in the style of the board body that places it. No rows is
/// [`bound::Cartesian::NoData`] in the chart's frame, once the chart's own style has passed the refusals.
pub fn bind(
    id: &ChartId,
    chart: &LineChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed<'_>,
) -> Result<bound::Chart, Diagnostic> {
    refuse_unported(chart)?;
    let title = plain_text(chart.cartesian.shared.title.as_ref())?;
    let subtitle = plain_text(chart.cartesian.shared.subtitle.as_ref())?;
    let lines = match lines(id, chart, board, run, placed) {
        Err(error) if error.code == codes::ERR_LINE_Y_NOT_NUMERIC.id => {
            let family = Err(Failure { chart: id.clone(), diagnostic: error });
            return Ok(bound::Chart { title: None, subtitle: None, frame: None, family });
        }
        other => other?,
    };
    let Some(lines) = lines else {
        let frame = Some(placed.derive()?.frame);
        return Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(no_data(placed, "line")?)) });
    };
    let frame = Some(lines.derived.frame.clone());
    let plot = bound::Plane::new(bound::PlaneParts {
        orient: Orient::Vertical,
        x: lines.x,
        y: vec![lines.y],
        panels: Panels::Single,
        series: vec![lines.series],
        layers: vec![MarkLayer { mark: Mark::Lines, y_scale: 0, panels: vec![lines.datums] }],
        naming: Naming {
            rail: None,
            entries: Vec::new(),
            asks_top_strip: false,
            authored: AuthoredLegend::Side(lines.derived.legend.clone()),
        },
        legends: Vec::new(),
        support: None,
        baselines: lines.baselines,
        axis_styles: lines.axis_styles,
        diagnostics: lines.diagnostics,
        mark_style: MarkStyle { points: None, bins: None },
    })
    .map_err(internal)?;
    Ok(bound::Chart { title, subtitle, frame, family: Ok(bound::Family::Cartesian(bound::Cartesian::Plane(Box::new(plot)))) })
}
