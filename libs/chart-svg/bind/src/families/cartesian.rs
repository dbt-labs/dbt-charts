//! The laws the cartesian families share: the opening every family writes ([`Prelude`]), the one
//! series of a chart without `color:` ([`single_series`]), plain chart text, number cells, the house axis format,
//! the grid's threshold rules, a ticks count, a chart's dataset, a `color:` field's series and a
//! fixed ladder's rung text.
//! Each refuses what the bind does not model yet by name, for every family alike.
//! `color_series` paints a `color:` field's series from the category plan.

use std::cell::RefCell;
use std::collections::HashMap;

use board_model::board::Board;
use board_model::chart::{CartesianChartFields, ChartQuery, Columns};
use board_model::facet::DisplayText;
use board_model::ids::ChartId;
use board_model::query::Query;
use color::Rgba;
use column_types::Dataset;
use diagnostic::{diag, refuse, Diagnostic};
use board_model::style::style::Style;
use model_core::{Engine, Resolved};

use crate::category_colors::{series_colors, CategoryColorScale};
use crate::column_kind::{field_channel, ChannelType, Role};
use crate::formats::locale::{active_number_locale, d3_format, NumberLocale};
use crate::derived::{DerivedChart, Placed};
use crate::formats::{column_format, resolve_format_parts, AuthoredFormat, Formats, Kind, Repeat, ResolvedFormat, Tiers};
use crate::bound::{self, AxisStyles, Key, Ladder, Legend, LegendEntry, Paint, Series, SeriesKey, ValueAxis, ValueDomain, ValueScale, Zero};
use crate::rows::color_domain_of;
use crate::plot::caps::caps_bottom;
use crate::plot::domain::{self, Count};
use crate::run::RunResult;
use crate::style::axis::{AxisLadder, AxisName};
use crate::style::Tiers as StyleTiers;

/// The title of a count axis, and the key of the one measure it counts.
pub const COUNT_TITLE: &str = "#";

pub fn unsupported(message: String) -> Diagnostic {
    Diagnostic::refused(message)
}

fn total_style<'a>(placed: &'a Placed<'a>) -> Result<&'a Style, Diagnostic> {
    placed.body()?.style.as_deref().ok_or_else(|| diag::err_internal("a normalized board carries its total style"))
}

/// A chart over no rows, in its frame and `derived`'s walked style, as production draws it
/// (`no_results::render`): the headline in the legend label font.
pub fn no_data(derived: DerivedChart, public_id: &str, run: &RunResult, title: Option<String>, subtitle: Option<String>) -> bound::Chart {
    let headline = derived.legend.base.label.element.font.clone();
    let empty = bound::Empty { filters: run.empty_filters_line(public_id) };
    bound::Chart { title, subtitle, frame: Some(derived.frame), family: Ok(bound::Family::Cartesian(bound::Cartesian::NoData { empty, headline })) }
}

/// What every cartesian family reads before its own steps: the board's total style, the tiers and
/// both axes' ladders over them, the two fields and their titles.
pub struct Prelude<'a> {
    pub style: &'a Style,
    pub formats: Formats<'a>,
    pub tiers: StyleTiers<'a>,
    pub x_ladder: AxisLadder<'a>,
    pub y_ladder: AxisLadder<'a>,
    /// The continuous scale type each axis is authored on; a family refuses or draws it, so none
    /// ignores one (linear for an axis that is not a value axis, whose type is refused outright).
    pub x_scale: ValueScale,
    pub y_scale: ValueScale,
    pub x_field: &'a str,
    pub y_field: &'a str,
    /// Each axis's title: the authored `x_label:` / `y_label:` as written (its newlines read as
    /// spaces, a blank one stays blank), else the field's title. Tooltips and the series name read it too.
    pub x_title: String,
    pub y_title: String,
    /// Whether the chart authors a non-empty label for the axis, which shows an `auto` title.
    pub x_labeled: bool,
    pub y_labeled: bool,
}

impl<'a> Prelude<'a> {
    /// `fields`' chart as `placed`, its x axis carrying an `x_type` field and its y axis a `y_type` one.
    /// The refusals are the ones every family shares: a board `style.formats` alias, and an axis leaf no
    /// walk reads.
    pub fn of(fields: &'a CartesianChartFields, x_type: ChannelType, y_type: ChannelType, placed: &'a Placed<'a>) -> Result<Self, Diagnostic> {
        let y_field = match &fields.y {
            Some(Columns::One(y)) => y.as_str(),
            _ => return Err(diag::err_internal("normalize requires a cartesian chart's `y:`")),
        };
        let y_title = axis_title(y_field, fields.y_label.as_deref(), fields)?;
        Self::open(fields, x_type, y_type, y_field, y_title, placed)
    }

    /// A chart that counts rows (a histogram) has no `y:` column: its y is the count, titled `y_label:`
    /// when that is non-empty, else `#`.
    pub fn counting(fields: &'a CartesianChartFields, x_type: ChannelType, placed: &'a Placed<'a>) -> Result<Self, Diagnostic> {
        let y_title = fields.y_label.as_deref().filter(|label| !label.is_empty()).map_or_else(|| COUNT_TITLE.to_string(), display_text::collapse_title_newlines);
        Self::open(fields, x_type, ChannelType::Quantitative, COUNT_TITLE, y_title, placed)
    }

    fn open(fields: &'a CartesianChartFields, x_type: ChannelType, y_type: ChannelType, y_field: &'a str, y_title: String, placed: &'a Placed<'a>) -> Result<Self, Diagnostic> {
        let style = total_style(placed)?;
        if !style.formats.is_empty() {
            return Err(unsupported(refuse!("a cartesian chart on a board with `style.formats` aliases is not supported")));
        }
        let x_field = fields.x.as_ref().ok_or_else(|| diag::err_internal("normalize requires a cartesian chart's `x:`"))?.as_str();
        let tiers = StyleTiers::of(placed.chart, placed.own, &style.charts)?;
        let x_ladder = AxisLadder::new(AxisName::X, x_type, &tiers, None, false)?;
        let y_ladder = AxisLadder::new(AxisName::Y, y_type, &tiers, None, false)?;
        if let Some(leaf) = x_ladder.unwalked_leaf(x_type == ChannelType::Quantitative).or_else(|| y_ladder.unwalked_leaf(y_type == ChannelType::Quantitative)) {
            return Err(unsupported(refuse!("a cartesian axis that sets `{leaf}` is not supported")));
        }
        let x_title = axis_title(x_field, fields.x_label.as_deref(), fields)?;
        let x_scale = if x_type == ChannelType::Quantitative { x_ladder.value_scale()? } else { ValueScale::Linear };
        let y_scale = if y_type == ChannelType::Quantitative { y_ladder.value_scale()? } else { ValueScale::Linear };
        Ok(Self { style, formats: Formats::new(None), tiers, x_ladder, y_ladder, x_scale, y_scale, x_field, y_field, x_title, y_title, x_labeled: is_labeled(&fields.x_label), y_labeled: is_labeled(&fields.y_label) })
    }

    /// The tooltip's number format, voted by `values`: the chart's own `axis_y.labels.format` over the
    /// board's tooltip format (`_measure_tooltip_format`).
    pub fn tooltip_format(&self, values: &[f64]) -> Result<ResolvedFormat, Diagnostic> {
        let board = AuthoredFormat::named(self.style.charts.tooltip.format.as_str());
        let axis = AxisLadder::own_axis_y_format(&self.tiers);
        column_format(Kind::Number, Tiers { own: Some(axis.as_ref().unwrap_or(&board)), ..Tiers::default() }, self.formats, None, None, values)
    }
}

fn is_labeled(label: &Option<String>) -> bool {
    label.as_deref().is_some_and(|label| !label.is_empty())
}

/// An axis title as production draws it (`axis_title_text`): the authored `label` with its newlines read as spaces, else `field`'s title.
fn axis_title(field: &str, label: Option<&str>, fields: &CartesianChartFields) -> Result<String, Diagnostic> {
    match label {
        Some(label) => Ok(display_text::collapse_title_newlines(label)),
        None => {
            let labels: HashMap<String, String> = fields.labels.field_labels.clone().into_iter().collect();
            display_text::field_title(field, &labels).map_err(diag::err_internal)
        }
    }
}

/// The one series of a chart without `color:`: the measure `y_field`, painted the chart's single
/// fill, its label ink read against the canvas of `derived`.
pub fn single_series(placed: &Placed<'_>, y_field: &str, title: &str, derived: &DerivedChart) -> Result<Series, Diagnostic> {
    let fill = placed.single_series_fill()?;
    let ink = color::label_ink(&hex(fill), &hex(derived.frame.canvas)).map_err(diag::err_internal)?;
    Ok(Series {
        key: SeriesKey::Measure(y_field.to_string()),
        display: title.to_string(),
        paint: Paint::Solid(fill),
        ink: color::parse_css_color(&ink).map_err(diag::err_internal)?,
    })
}

/// A title or subtitle as drawn: plain text as written, none when empty (production tests `not title`).
/// A template needs `queries.<name>` by query id, which no chart text reads yet.
pub fn plain_text(text: Option<&DisplayText>) -> Result<Option<String>, Diagnostic> {
    let Some(text) = text.map(|t| t.as_str()).filter(|t| !t.is_empty()) else { return Ok(None) };
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

/// The axis's label format, resolved. A percent draws a unity rule and anchors a scatter x at zero,
/// which no family draws yet: refused by name.
pub fn axis_format(ladder: &AxisLadder<'_>, formats: Formats<'_>) -> Result<ResolvedFormat, Diagnostic> {
    let format = match ladder.labels_format() {
        Some(authored) => resolve_format_parts(&authored, formats)?,
        None => ResolvedFormat::default(),
    };
    if format.spec.contains('%') {
        return Err(unsupported(refuse!("a cartesian axis percent format is not supported")));
    }
    Ok(format)
}

/// The one law the families call for the parts both axes draw, walked over the placing scope
/// (`ChartStyles.scope`); the title of an axis is `None` where the walked `title.visible` hides it or its text is blank, and
/// an `auto` title shows only where `p` says the chart authors the axis's label. `y_domain` is `None` for
/// a y axis with no value ladder (a point scale).
pub fn axes(
    p: &Prelude<'_>,
    y_domain: Option<&ValueDomain>,
    scope: &Resolved<Style>,
    family: &str,
    x_is_ruler: bool,
    y_auto_right: bool,
) -> Result<(AxisStyles, [Option<String>; 2]), Diagnostic> {
    let grid = p.y_ladder.grid_visible() == Some(true);
    // A point y scale bakes no ladder, so its floor is unknown and a drawn grid is taken to cap the plot.
    let caps = y_domain.map_or(grid, |domain| caps_bottom(domain, grid, false));
    let mut x_style = p.x_ladder.parts(scope, family, x_is_ruler, y_auto_right, caps, p.x_labeled)?;
    let mut y_style = p.y_ladder.parts(scope, family, false, y_auto_right, false, p.y_labeled)?;
    // A blank title draws no text and reserves no band, however the walk shows it.
    for (style, title) in [(&mut x_style, &p.x_title), (&mut y_style, &p.y_title)] {
        if title.is_empty() {
            style.title = None;
        }
    }
    let titles = [x_style.title.is_some().then(|| p.x_title.clone()), y_style.title.is_some().then(|| p.y_title.clone())];
    Ok((AxisStyles { x: x_style, y: vec![y_style] }, titles))
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
    run.dataset(query, id)
}

/// The tick label production bakes for a ladder (`decide_tick_label`), `None` where it bakes none; a
/// ladder that takes a magnitude ruler is refused.
fn tick_label_choice(
    format: &ResolvedFormat,
    column_forming: bool,
    ticks: &[f64],
    drawn: &[f64],
) -> Result<Option<d3rs::numeral_scale::TickLabelChoice>, Diagnostic> {
    // `bytes` and `duration` are natives with no d3 spec: a literal format, never an SI or fixed one.
    let parts = if d3rs::predefined_formats::PREDEFINED_NATIVE.contains(&format.spec.as_str()) {
        None
    } else {
        Some(d3rs::format::parse_parts(&format.spec).map_err(diag::err_internal)?)
    };
    let type_ = parts.as_ref().map(|parts| parts.type_);
    let repeat = format.repeat.map(|repeat| repeat.to_string());
    let inputs = d3rs::numeral_scale::LabelInputs {
        si_format: (type_ == Some('s')).then_some(format.spec.as_str()),
        fixed_decimal_format: (format.is_house() && type_ == Some('f') && parts.as_ref().is_some_and(|parts| parts.precision.is_some_and(|p| p > 0)))
            .then_some(format.spec.as_str()),
        format_is_house: format.is_house(),
        format_affixed: !format.prefix.is_empty() || !format.suffix.is_empty(),
        notation: format.notation.map(|notation| notation.as_str()),
        repeat: repeat.as_deref(),
        label_expr_authored: false,
        column_forming,
        start_anchored: false,
        // Whether the label font is tabular is a measured fact bind has no pixels for: assumed, so the
        // ruler decides alone and a mixed-depth column refuses in `rung_text`.
        tabular: true,
    };
    let ruler = d3rs::numeral_scale::decide_ruler(&inputs, ticks).map_err(|e| match e {
        d3rs::numeral_scale::LabelError::ColumnNeedsTabularFont => unreachable!("the inputs assume a tabular font"),
        d3rs::numeral_scale::LabelError::Other(message) => diag::err_internal(message),
    })?;
    if ruler.is_some() {
        return Err(unsupported(refuse!("a cartesian axis whose ladder takes a magnitude ruler is not supported")));
    }
    d3rs::numeral_scale::decide_tick_label(&inputs, ticks, drawn).map_err(Diagnostic::refused)
}

/// Whether the format's affix anchors on one rung (`_anchors_affix`).
fn anchors_affix(format: &ResolvedFormat) -> bool {
    format.repeat == Some(Repeat::Anchor) && (!format.prefix.is_empty() || !format.suffix.is_empty())
}

/// The axis over `domain` printing through `format`, and whether production composes its labels
/// (`ValueAxis::composed`): a baked ladder (`baked`: a fixed or authored one) with a tick label or an
/// anchored affix, a log ladder with an anchored affix, any other with no ladder baked (Vega ticks
/// it) with an anchored affix or a house SI format.
pub fn value_axis(domain: ValueDomain, format: ResolvedFormat, column_forming: bool, baked: bool) -> Result<ValueAxis, Diagnostic> {
    let rungs = |rungs: &[bound::Rung]| rungs.iter().map(|rung| rung.value).collect::<Vec<_>>();
    let ticks = match &domain.ladder {
        Ladder::Fixed(ladder) if baked => Some(rungs(ladder.rungs())),
        Ladder::Authored(authored) => Some(rungs(authored)),
        _ => None,
    };
    let composed = anchors_affix(&format)
        || match (&ticks, &domain.ladder) {
            (Some(ticks), _) => tick_label_choice(&format, column_forming, ticks, ticks)?.is_some(),
            (None, Ladder::Log(_)) => false,
            (None, _) => format.is_house_si(),
        };
    Ok(ValueAxis { domain, format, composed })
}

/// A fixed ladder's rung text (a column of labels when `column_forming`), as production labels it
/// (`build_resolved_axis`, painted by `format_y_label`): a house format whose ladder does not compact
/// takes the precision its step needs (`decide_tick_label`) with its currency symbol on the anchor
/// rung, any other format prints each rung as written, zero bare, and an anchored affix
/// (`repeat: anchor`) wears the anchor rung alone. A ladder that compacts takes a magnitude ruler,
/// and a column of mixed decimal depths a measured pad table; neither is ported.
pub fn rung_text(format: &ResolvedFormat, column_forming: bool, ticks: &[f64], drawn: &[f64]) -> Result<Vec<String>, Diagnostic> {
    let locale = active_number_locale();
    let Some(choice) = tick_label_choice(format, column_forming, ticks, drawn)? else {
        return affix_text(format, &locale, ticks, drawn);
    };
    let affixed = !format.prefix.is_empty() || !format.suffix.is_empty();
    let anchor_spec = if choice.prefix.is_empty() { choice.digit_spec.clone() } else { d3rs::format::with_symbol(&choice.digit_spec, &choice.prefix) };
    let anchored = choice.anchor_at_start.is_some();
    let texts = ticks
        .iter()
        .map(|tick| {
            let on_anchor = *tick != 0.0
                && match choice.anchor_at_start {
                    Some(true) => Some(tick) == drawn.first(),
                    Some(false) => Some(tick) == drawn.last(),
                    None => true,
                };
            match (affixed, on_anchor) {
                (true, true) => format.paint_anchored(&locale, &anchor_spec, *tick, anchored),
                (true, false) => format.bare_digits(&locale, &choice.digit_spec, *tick),
                (false, true) if !choice.prefix.is_empty() => d3_format(&anchor_spec, *tick).map_err(diag::err_internal),
                (false, _) => d3_format(&choice.digit_spec, *tick).map_err(diag::err_internal),
            }
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    let depth = |text: &String| text.split_once('.').map_or(0, |(_, fraction)| fraction.len());
    if column_forming && texts.iter().any(|text| depth(text) != depth(&texts[0])) {
        return Err(unsupported(refuse!("a cartesian axis whose rungs need a decimal pad table is not supported")));
    }
    Ok(texts)
}

/// The rungs of a ladder no tick label decides (`affix_tick_label`): an anchored affix
/// (`repeat: anchor`) wears the anchor rung alone, any other prints each rung as written.
fn affix_text(format: &ResolvedFormat, locale: &NumberLocale, ticks: &[f64], drawn: &[f64]) -> Result<Vec<String>, Diagnostic> {
    if !anchors_affix(format) {
        return ticks.iter().map(|tick| format.paint_tick(locale, *tick)).collect();
    }
    let anchor = if d3rs::numeral_scale::ladder_anchor_at_start(drawn) { drawn.first() } else { drawn.last() };
    ticks
        .iter()
        .map(|tick| {
            if *tick != 0.0 && Some(tick) == anchor {
                format.paint_anchored(locale, &format.spec, *tick, true)
            } else {
                format.bare_digits(locale, &format.spec, *tick)
            }
        })
        .collect()
}

/// A domain built over an infallible rung printer: the domain laws take one, so the printer's first
/// refusal waits here and wins over the domain's result.
fn printed(
    print: impl Fn(&[f64], &[f64]) -> Result<Vec<String>, Diagnostic>,
    domain: impl FnOnce(domain::RungText) -> Result<Option<ValueDomain>, bound::Invalid>,
    context: &str,
) -> Result<ValueDomain, Diagnostic> {
    let refused = RefCell::new(None);
    let text = |ticks: &[f64], drawn: &[f64]| match print(ticks, drawn) {
        Ok(texts) => texts,
        Err(diagnostic) => {
            refused.borrow_mut().get_or_insert(diagnostic);
            vec![String::new(); ticks.len()]
        }
    };
    let domain = domain(&text);
    if let Some(diagnostic) = refused.into_inner() {
        return Err(diagnostic);
    }
    domain
        .map_err(|invalid| diag::err_internal(format!("cartesian {context} is invalid: {invalid:?}")))?
        .ok_or_else(|| diag::err_internal(format!("cartesian {context} has no value to span")))
}

/// The linear domain of `values` on `axis`, its ladder's rungs printed by [`rung_text`].
pub fn linear_domain(
    axis: &domain::Axis,
    values: &[f64],
    format: &ResolvedFormat,
    column_forming: bool,
    context: &str,
) -> Result<ValueDomain, Diagnostic> {
    printed(|ticks, drawn| rung_text(format, column_forming, ticks, drawn), |text| domain::linear(axis, values, text), context)
}

/// The domain of a log axis over `values` (all positive: [`domain::require_positive`]), its rungs
/// printed as production prints a log tick, each through the axis format (`format_y_label` with no
/// ruler or tick label, which a log axis bakes none of).
pub fn log_domain(
    base: f64,
    count: usize,
    ladder: &AxisLadder<'_>,
    values: &[f64],
    format: &ResolvedFormat,
    context: &str,
) -> Result<ValueDomain, Diagnostic> {
    let locale = active_number_locale();
    printed(
        |ticks, _| ticks.iter().map(|tick| format.paint_tick(&locale, *tick)).collect(),
        |text| domain::log(base, count, values, ladder.domain(), text),
        context,
    )
}

/// The zero verdict of an axis on `scale`: a log axis has no zero to anchor or cross, and so no rule at it.
pub fn zero_on(scale: ValueScale, verdict: Zero) -> Zero {
    if scale.is_log() {
        Zero::Free
    } else {
        verdict
    }
}

/// The ticks Vega draws on a log measure axis that carries no `ticks.count`: d3's own default of 10.
pub const LOG_TICK_COUNT: usize = 10;

/// Refuses a value axis scale type the family does not draw. A bent scale (pow, sqrt, symlog) is
/// production's compile error off a scatter, whose callers do not ask: its line, bar and histogram
/// would draw linear marks under an axis description naming the type. `draws_log` says whether the
/// family draws a log scale; `axis` is the bare axis letter (`x` or `y`); the diagnostic names it as `axis_{axis}`.
pub fn refuse_scale(chart_id: &str, scale: ValueScale, family: &str, axis: &str, draws_log: bool) -> Result<(), Diagnostic> {
    match scale {
        ValueScale::Linear => Ok(()),
        scale if scale.is_log() && draws_log => Ok(()),
        scale if scale.is_log() => Err(unsupported(refuse!("a {family} {axis} axis on a {} scale is not supported", scale.name()))),
        bent => Err(diag::err_scale_type_scatter_only(chart_id, family, format!("axis_{axis}"), bent.name())),
    }
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
        scale: ValueScale::Linear,
        zero,
        headroom: ladder.headroom().unwrap_or(0.0),
        percent: format.spec.contains('%'),
        count: count(ladder)?,
        values: None,
        domain: ladder.domain(),
        min_step: d3rs::numeral_scale::tick_min_step_for_format(&format.spec),
        independent: false,
        nice,
        mark,
    };
    let domain = linear_domain(&axis, values, &format, true, "y domain")?;
    value_axis(domain, format, true, true)
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
        Legend { title: Some(title), entries: entries.collect(), toggle: None, stamps_series: false }
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
    // `Role::Free`, not `Role::Color`: a date or date-shaped text still refuses as temporal here, where
    // a color field reads it as a category.
    if field_channel(&dataset.schema, &dataset.rows, field, Role::Free)? != ChannelType::Nominal {
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
