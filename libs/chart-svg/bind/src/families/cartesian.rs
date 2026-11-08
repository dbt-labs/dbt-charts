//! The laws the cartesian families share: plain chart text, number cells, the house axis format,
//! the grid's threshold rules, a ticks count, a chart's dataset, a `color:` field's series and a
//! fixed ladder's rung text.
//! Each refuses what the bind does not model yet by name, for every family alike.

use std::cell::RefCell;

use board_model::board::Board;
use board_model::chart::{CartesianChartFields, ChartQuery};
use board_model::facet::DisplayText;
use board_model::ids::ChartId;
use board_model::query::Query;
use color::Rgba;
use column_types::Dataset;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::Engine;
use serde_json::json;

use crate::category_colors::{series_colors, CategoryColorScale};
use crate::channel_type::{classify, ChannelType};
use crate::formats::locale::d3_format;
use crate::formats::{resolve_format_parts, Formats, ResolvedFormat};
use crate::model::{Key, Legend, LegendEntry, Paint, Series, SeriesKey, ValueAxis, ValueDomain, Zero};
use crate::rows::color_domain_of;
use crate::plot::domain::{self, Count};
use crate::run::RunResult;
use crate::style::axis::AxisLadder;

pub fn unsupported(message: String) -> Diagnostic {
    Diagnostic::refused(message)
}

/// A title or subtitle as drawn: plain text as written. A template needs `queries.<name>` by
/// query id, which no chart text reads yet.
pub fn plain_text(text: Option<&DisplayText>) -> Result<Option<String>, Diagnostic> {
    let Some(text) = text.map(|t| t.as_str()) else { return Ok(None) };
    if text.contains("{{") || text.contains("{%") || text.contains("{#") {
        return Err(unsupported(refuse!("a templated cartesian chart title or subtitle is not supported")));
    }
    Ok(Some(text.to_string()))
}

/// A cell of a declared number column as a value; `None` for a null.
pub fn value(key: &Key) -> Result<Option<f64>, Diagnostic> {
    match key {
        Key::Null => Ok(None),
        Key::Int(i) => Ok(Some(*i as f64)),
        Key::Num { value, .. } => Ok(Some(*value)),
        other => Err(diag::err_internal(format!("a declared number column decoded to {other:?}"))),
    }
}

/// The axis's label format, resolved. Only the house `number` is ported: a percent draws a unity
/// rule and anchors a scatter x at zero, and another format labels its ladder by rules not ported.
pub fn axis_format(ladder: &AxisLadder<'_>, formats: Formats<'_>) -> Result<ResolvedFormat, Diagnostic> {
    let format = match ladder.labels_format() {
        Some(authored) => resolve_format_parts(&authored, formats)?,
        None => ResolvedFormat::default(),
    };
    if format.raw.as_deref() != Some("number") || format.has_affix() {
        return Err(unsupported(refuse!("a cartesian axis format other than the house `number` is not supported")));
    }
    Ok(format)
}

/// Whether the axis's grid draws its threshold rules.
pub fn threshold_rules(ladder: &AxisLadder<'_>) -> Result<bool, Diagnostic> {
    let walked = ladder.grid_visible().zip(ladder.threshold_visible());
    let (grid, threshold) = walked.ok_or_else(|| diag::err_internal("the total axis slot ends every grid walk"))?;
    Ok(grid && threshold)
}

pub fn count(ladder: &AxisLadder<'_>) -> Result<Option<Count>, Diagnostic> {
    match ladder.ticks_count() {
        None => Ok(None),
        Some(Engine::Rule(_)) => Ok(Some(Count::Auto)),
        Some(Engine::Value(n)) => usize::try_from(*n)
            .ok()
            .filter(|n| *n > 0)
            .map(|n| Some(Count::N(n)))
            .ok_or_else(|| diag::err_internal(format!("normalize admits only a positive `ticks.count`, not {n}"))),
    }
}

pub fn hex(color: Rgba) -> String {
    color::rgb01_to_hex(color.0, color.1, color.2)
}

/// The dataset a cartesian chart reads. A query whose declared format names one of `fields` takes
/// the column-format rung of the axis and tooltip walks, not ported here.
pub fn dataset<'a>(
    id: &ChartId,
    chart: &CartesianChartFields,
    board: &Board,
    run: &'a RunResult,
    fields: &[&str],
) -> Result<&'a Dataset, Diagnostic> {
    let Some(ChartQuery::Hoisted(query)) = &chart.shared.base.query else {
        return Err(diag::err_internal("a cartesian chart reaches bind with its query hoisted"));
    };
    let declared = board
        .body
        .queries
        .get(query.name().as_str())
        .ok_or_else(|| diag::err_internal(format!("normalize hoists query {query} into its board")))?;
    let formats = match declared {
        Query::Sql(q) => &q.base.formats,
        Query::Values(q) => &q.base.formats,
        Query::Schema(q) => &q.base.formats,
    };
    if fields.iter().any(|field| formats.contains_key(*field)) {
        return Err(unsupported(refuse!("a cartesian chart whose query declares an axis column format is not supported")));
    }
    run.datasets
        .get(query)
        .ok_or_else(|| diag::err_engine_needs_data(query.to_string()).with_sink_param("chart_id", json!(id.as_str())))
}

/// A fixed ladder's rung text (a column of labels when `column_forming`), as production labels a
/// ladder that does not compact (`decide_tick_label`): each rung in the precision its step needs.
/// A ladder that compacts takes a magnitude ruler, and a column of mixed decimal depths a measured
/// pad table; neither is ported.
pub fn rung_text(format: &ResolvedFormat, column_forming: bool, ticks: &[f64]) -> Result<Vec<String>, Diagnostic> {
    if d3rs::numeral_scale::shared_scale_for_ladder(ticks).is_some() {
        return Err(unsupported(refuse!("a cartesian axis whose ladder takes a magnitude ruler is not supported")));
    }
    let parts = d3rs::format::parse_parts(&format.spec).map_err(diag::err_internal)?;
    let inputs = d3rs::numeral_scale::LabelInputs {
        si_format: (parts.type_ == 's').then_some(format.spec.as_str()),
        fixed_decimal_format: None,
        format_is_house: format.is_house(),
        format_affixed: false,
        notation: None,
        repeat: None,
        label_expr_authored: false,
        column_forming,
        start_anchored: false,
        tabular: false,
    };
    let choice = d3rs::numeral_scale::decide_tick_label(&inputs, ticks, ticks).map_err(diag::err_internal)?;
    let Some(choice) = choice.filter(|choice| choice.anchor_at_start.is_none()) else {
        return Err(unsupported(refuse!("a cartesian ladder that is not labeled at its step is not supported")));
    };
    let texts = ticks
        .iter()
        .map(|tick| Ok(format!("{}{}", choice.prefix, d3_format(&choice.digit_spec, *tick).map_err(diag::err_internal)?)))
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let depth = |text: &String| text.split_once('.').map_or(0, |(_, fraction)| fraction.len());
    if column_forming && texts.iter().any(|text| depth(text) != depth(&texts[0])) {
        return Err(unsupported(refuse!("a cartesian axis whose rungs need a decimal pad table is not supported")));
    }
    Ok(texts)
}

/// The linear domain of `values` on `axis`, its ladder's rungs printed by [`rung_text`]. The
/// domain law takes an infallible printer, so the first refusal waits here and wins over the
/// domain's result.
pub fn linear_domain(
    axis: &domain::Axis,
    values: &[f64],
    format: &ResolvedFormat,
    column_forming: bool,
    context: &str,
) -> Result<ValueDomain, Diagnostic> {
    let refused = RefCell::new(None);
    let text = |ticks: &[f64]| match rung_text(format, column_forming, ticks) {
        Ok(texts) => texts,
        Err(diagnostic) => {
            refused.borrow_mut().get_or_insert(diagnostic);
            vec![String::new(); ticks.len()]
        }
    };
    let domain = domain::linear(axis, values, &text);
    if let Some(diagnostic) = refused.into_inner() {
        return Err(diagnostic);
    }
    domain
        .map_err(|invalid| diag::err_internal(format!("cartesian {context} is invalid: {invalid:?}")))?
        .ok_or_else(|| diag::err_internal(format!("cartesian {context} has no value to span")))
}

/// The measure axis over `values`, baked as production bakes a vertical cartesian measure axis:
/// headroom, count and the step floor of its format. `mark` is what draws it, which decides when
/// the scale rounds unless `nice` forces it.
pub fn measure_axis(
    ladder: &AxisLadder<'_>,
    zero: Zero,
    values: &[f64],
    format: ResolvedFormat,
    mark: domain::Mark,
    nice: Option<bool>,
) -> Result<ValueAxis, Diagnostic> {
    let axis = domain::Axis {
        zero,
        headroom: ladder.headroom().unwrap_or(0.0),
        count: count(ladder)?,
        values: None,
        domain: None,
        min_step: d3rs::numeral_scale::tick_min_step_for_format(&format.spec),
        independent: false,
        nice,
        mark,
    };
    let domain = linear_domain(&axis, values, &format, true, "y domain")?;
    Ok(ValueAxis { domain, format })
}

/// Whether the rule at zero draws: the grid's threshold rules do, and zero is in the domain,
/// whether the zero verdict anchors it there or the data crosses it.
pub fn zero_rule(ladder: &AxisLadder<'_>, zero: Zero, values: &[f64]) -> Result<bool, Diagnostic> {
    let in_domain = matches!(zero, Zero::Anchored | Zero::Pinned(true)) || spans_zero(values);
    Ok(threshold_rules(ladder)? && in_domain)
}

/// Whether the data reaches zero from both sides or touches it.
pub fn spans_zero(values: &[f64]) -> bool {
    let lo = values.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    lo <= 0.0 && 0.0 <= hi
}

/// The series of a `color:` field: one per value, and the series each row belongs to.
#[derive(Debug)]
pub struct ColorSeries {
    pub series: Vec<Series>,
    /// Each row's index into `series`.
    pub of_row: Vec<usize>,
}

impl ColorSeries {
    /// The legend naming every series, in series order, under `title`.
    pub fn legend(&self, title: String) -> Legend {
        let entries = self.series.iter().enumerate().map(|(series, s)| LegendEntry { series, label: s.display.clone() });
        Legend { title: Some(title), entries: entries.collect() }
    }
}

/// The series of the nominal `color:` field `field`, whose cell in each row is `keys`. The series
/// run in the order production sorts a color domain (`color_domain_of`), and each paints what
/// [`series_colors`] gives it over `palette`, the palette of the scope that draws the chart: the
/// plan's seat for its value, or its position when the board binds no scale for the field. `canvas` is what the label ink
/// reads against.
pub fn color_series(
    field: &str,
    dataset: &Dataset,
    keys: &[Key],
    scales: &[CategoryColorScale],
    palette: &[String],
    canvas: Rgba,
) -> Result<ColorSeries, Diagnostic> {
    if classify(&dataset.schema, &dataset.rows, Some(field), false)? != ChannelType::Nominal {
        return Err(unsupported(refuse!("a cartesian `color:` that is quantitative or temporal is not supported")));
    }
    let texts = keys
        .iter()
        .map(|key| match key {
            Key::Text(text) => Ok(text),
            Key::Null => Err(unsupported(refuse!("a cartesian row with a null `color:` is not supported"))),
            _ => Err(unsupported(refuse!("a cartesian `color:` that is not text is not supported"))),
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let domain = color_domain_of(&dataset.rows, field);
    let fills = series_colors(&domain, palette, scales.iter().find(|scale| scale.field == field))?;
    let series = domain
        .iter()
        .zip(&fills)
        .map(|(value, literal)| {
            let fill = color::parse_css_color(literal).map_err(diag::err_internal)?;
            let ink = color::label_ink(&hex(fill), &hex(canvas)).map_err(diag::err_internal)?;
            Ok(Series {
                key: SeriesKey::Value(Key::Text(value.clone())),
                display: value.clone(),
                paint: Paint::Solid(fill),
                ink: color::parse_css_color(&ink).map_err(diag::err_internal)?,
            })
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let of_row = texts
        .into_iter()
        .map(|text| domain.binary_search(text).expect("the domain holds every row's value"))
        .collect();
    Ok(ColorSeries { series, of_row })
}
