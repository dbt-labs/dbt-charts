//! A pie or donut's data: each wedge's value, fill, tooltip text and label, and a donut's center
//! total. `chart-renderer/src/families/pie/bind.rs` (`bind_wheel`, `bind_slices`, `bind_labels`,
//! `bind_center`, `pie_row_aria`) and `compile/resolve/chart/pie.py` (`_resolve_pie`) decide the
//! same facts; what this slice does not model is refused by name.
//!
//! Read off `charts.pie` with the family tier's background and padding. The data is each
//! wedge's theta, the compensated shares the label floor reads, the default label template with
//! the tooltip's voted format, wedge fills by row position, companion ink, tooltip text and a
//! donut's center total with its auto caption. A donut reads `charts.donut` over it for the hole
//! and the total format only. The chart's own `style:`, a visible legend, a shared color field
//! and every field the slice does not draw are refused by name. `engine`'s `tests/it/board.rs`
//! replays the recorded `PIES` cases (`record_cases/pie.py`).

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::PieChart;
use board_model::ids::ChartId;
use board_model::primitives::{FormatSpec, PaletteChoice};
use board_model::style::pie::PieChartStyle;
use board_model::style::style::Style;
use column_types::ColumnType;
use d3rs::py_float::py_float_sum;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::{Engine, SetLeaves, Tri};
use serde_json::Value;

use crate::derived::Placed;
use crate::formats::locale::{active_number_locale, d3_format};
use crate::formats::{column_format, AuthoredFormat, Formats, Kind, Tiers};
use crate::kpi::declared_formats;
use crate::label_template::{Scope, Template};
use crate::bound::{Failure, Family, Pie, PieCenter, PieLabel, PieSlice};
use crate::run::RunResult;
use crate::{authored_pins, color_field, placed_charts};

/// `WEDGE_LABEL_MIN_SHARE` in `core/constants/pie.py`: a slice labels only past this share.
const WEDGE_LABEL_MIN_SHARE: f64 = 0.08;
/// `INVISIBLE_SLICE_SHARE` in `core/constants/pie.py`: a slice under it is too small to draw, and
/// the pie falls back to listing its small slices in a table.
const INVISIBLE_SLICE_SHARE: f64 = 0.02;

/// `_tooltip.py`'s role markers (`ROLE_HEADER_SWATCHED`, `MUTED`, `ROLE_TOTAL`): invisible code
/// points the front end splits a tooltip's rows by.
pub(crate) const ROLE_HEADER_SWATCHED: char = '\u{2064}';
const ROLE_TOTAL: char = '\u{2063}';
const MUTED: char = '\u{2060}';

/// A chart field, style or board fact that changes what the pie says and is not modeled.
fn unmodeled(what: &str) -> Diagnostic {
    Diagnostic::refused(refuse!("a pie with {what} is not supported"))
}

/// Data the chart's rows hold that production draws another way, or fails on.
#[derive(Clone, Copy)]
enum Data {
    NoRows,
    ColorNotText,
    ThetaNotNumber,
    NoColor,
    NoTheta,
    Negative,
    NoTotal,
    NoLabel,
    TooSmall,
}

impl Data {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Data::NoRows => "no rows",
            Data::ColorNotText => "a color column that is not text",
            Data::ThetaNotNumber => "a theta column that is not a plain number",
            Data::NoColor => "a slice with no color value",
            Data::NoTheta => "a slice with no theta value",
            Data::Negative => "a negative theta",
            Data::NoTotal => "a theta that does not sum to a positive total",
            Data::NoLabel => "no slice large enough to label",
            Data::TooSmall => "a slice too small to draw",
        };
        Diagnostic::refused(refuse!("a pie over {what} is not supported"))
    }
}

/// A slice label's template or value format that this engine does not evaluate.
enum Label {
    Template(String),
    Affix,
    Quote,
}

impl Label {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Label::Template(why) => format!("template this engine does not evaluate ({why})"),
            Label::Affix => "value format affix holding `{`".to_string(),
            Label::Quote => "value format spec holding both quote kinds".to_string(),
        };
        Diagnostic::refused(refuse!("a pie label {what} is not supported"))
    }
}

/// `theta:` names a column the query does not return; production raises its own error.
fn missing_theta_column() -> Diagnostic {
    Diagnostic::refused(refuse!("a pie over a theta column the query lacks is not supported"))
}

fn donut_leaf(leaf: &str) -> Diagnostic {
    unmodeled(&format!("a donut style setting `{leaf}`"))
}

fn format_block() -> Diagnostic {
    unmodeled("a `total.value.format` block")
}

/// Binds `chart` to its query's rows at the placement `placed`.
pub fn bind_pie(id: &ChartId, chart: &PieChart, donut: bool, board: &Board, run: &RunResult, placed: &Placed) -> Result<Result<Family, Failure>, Diagnostic> {
    let style = &placed.style.charts.pie;
    let hole = effective_inner_radius(placed, donut)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.radial.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset = run.dataset(query, id)?;
    refuse_unmodeled(id, chart, board, run, placed, query)?;
    let (Some(theta), Some(color)) = (&chart.radial.theta, &chart.radial.color) else {
        return Err(diag::err_internal("a pie without `theta:` and `color:` passed the unmodeled refusals"));
    };
    let (theta, color) = (theta.as_str(), color.as_str());
    let rows = dataset.rows.as_slice();
    if rows.is_empty() {
        return Err(Data::NoRows.refusal());
    }
    if dataset.schema.get(color).map_err(diag::err_internal)? != &ColumnType::String {
        return Err(Data::ColorNotText.refusal());
    }
    if !matches!(dataset.schema.get(theta).map_err(|_| missing_theta_column())?, ColumnType::Integer | ColumnType::Float) {
        return Err(Data::ThetaNotNumber.refusal());
    }
    let mut thetas = Vec::with_capacity(rows.len());
    let mut names = Vec::with_capacity(rows.len());
    for row in rows {
        match (row.get(theta), row.get(color)) {
            (Some(Value::Number(n)), Some(Value::String(name))) => {
                thetas.push(n.as_f64().ok_or_else(|| diag::err_internal("a JSON number has an f64 form"))?);
                names.push(name.as_str());
            }
            (Some(Value::Number(_)), _) => return Err(Data::NoColor.refusal()),
            _ => return Err(Data::NoTheta.refusal()),
        }
    }
    if thetas.iter().any(|v| *v < 0.0) {
        return Err(Data::Negative.refusal());
    }
    // `compute_shares` (resolve) reads the compensated sum; the render stage reads the plain one.
    let shares_total = py_float_sum(&thetas);
    if !shares_total.is_finite() || shares_total <= 0.0 {
        return Err(Data::NoTotal.refusal());
    }
    let total: f64 = thetas.iter().sum();
    let shares: Vec<f64> = thetas.iter().map(|v| v / shares_total).collect();
    refuse_unlabeled_mode(&shares)?;

    let scope = placed.scope();
    let canvas = placed.canvas(&style.base.background.clone().map_or(Tri::Unset, Tri::Value), scope)?;
    let palette = palette(style, scope)?;
    let companions = palette.iter().map(|c| color::mark_ink(c, &canvas)).collect::<Result<Vec<_>, _>>().map_err(diag::err_internal)?;

    let labels = &style.marks.slice.labels;
    let vote: Vec<f64> = thetas.iter().copied().chain([py_float_sum(&thetas)]).collect();
    let board_tooltip = AuthoredFormat::named(placed.style.charts.tooltip.format.as_str());
    let formats = Formats::new(None);
    let tooltip = column_format(Kind::Number, Tiers { board: Some(&board_tooltip), ..Tiers::default() }, formats, None, None, &vote)?;
    let locale = active_number_locale();
    let template = match &labels.template {
        Some(authored) => authored.clone(),
        None => default_label_template(&labels.default_template.with_color, &tooltip.prefix, &tooltip.spec, &tooltip.suffix)?,
    };
    let template = Template::parse(&template).map_err(|e| Label::Template(e).refusal())?;
    if tooltip.spec.is_empty() {
        return Err(diag::err_internal("the tooltip format settles a spec"));
    }
    // `field_display_text(theta, labels, legend.title.element.font.case)`: the tooltip's value title.
    let case = placed.style.charts.legend.base.title.element.font.case.as_str();
    let value_title = display_text::case::apply_case(&display_text::slug_to_text(theta).map_err(diag::err_internal)?, case).map_err(diag::err_internal)?;
    let mut slices = Vec::with_capacity(rows.len());
    for (idx, row) in rows.iter().enumerate() {
        let slot = idx % palette.len();
        let share = thetas[idx] / total;
        let aria = [
            format!("{ROLE_HEADER_SWATCHED}{}", names[idx]),
            format!("%: {}", d3_format(".0%", share).map_err(diag::err_internal)?),
            format!("{MUTED}{value_title}: {}", tooltip.paint(&locale, thetas[idx])?),
            format!("{MUTED}{ROLE_TOTAL}Σ: {}", tooltip.paint(&locale, total)?),
        ]
        .join("; ");
        let label = if shares[idx] > WEDGE_LABEL_MIN_SHARE {
            let scope = Scope { row, theta_field: theta, color_field: Some(color), percent: share, total };
            let lines = template.render(&scope).map_err(|e| Label::Template(e).refusal())?;
            // The wedge's board slot would repaint the ink; with no board scale it is the row's palette slot.
            (!lines.is_empty()).then(|| PieLabel { lines, ink: companions[slot].clone() })
        } else {
            None
        };
        slices.push(PieSlice { value: thetas[idx], fill: palette[slot].clone(), aria, label });
    }
    let center = center(placed, style, donut && hole > 0.0, theta, total, &vote, &locale)?;
    let background = style.base.background.as_ref().map_or(placed.style.charts.background.as_str(), |c| c.as_str()).to_string();
    let title = chart.radial.shared.title.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    Ok(Ok(Family::Pie(Pie { canvas, background, title, hole, slices, total, center })))
}

/// The hole a pie draws: a donut reads `charts.donut` over `charts.pie`, which for any leaf but
/// the hole is the pie's own board tier (`pie_reaches_donut` copies it in); a donut whose patch
/// sets any other leaf is refused.
fn effective_inner_radius(placed: &Placed, donut: bool) -> Result<f64, Diagnostic> {
    let pie = placed.style.charts.pie.inner_radius.get();
    let Some(patch) = placed.style.charts.donut.as_ref().filter(|_| donut) else { return Ok(pie) };
    let mut other = None;
    patch.set_leaves("", &mut |path| {
        if !matches!(path, "inner_radius" | "total.value.format") {
            other.get_or_insert_with(|| path.to_string());
        }
    });
    match other {
        Some(leaf) => Err(donut_leaf(&leaf)),
        None => Ok(patch.inner_radius.as_ref().map_or(pie, |hole| hole.get())),
    }
}

/// Every chart field, style and board fact that changes what the pie says and is not modeled:
/// the chart's fields, its own tier (the one production reads some leaves from alone), the family
/// styles this slice does not paint, and the board's category plan.
fn refuse_unmodeled(
    id: &ChartId,
    chart: &PieChart,
    board: &Board,
    run: &RunResult,
    placed: &Placed,
    query: &board_model::ids::QueryId,
) -> Result<(), Diagnostic> {
    let style = &placed.style.charts.pie;
    let color = chart.radial.color.as_ref().map(|c| c.as_str());
    let (radial, base) = (&chart.radial, &chart.radial.shared.base);
    let mut own = false;
    placed.own.set_leaves("", &mut |_| own = true);
    let query_formats = board.body.queries.get(query.name().as_str()).is_some_and(|q| !declared_formats(q).is_empty());
    let color_plan = placed_charts(board)?.into_iter().any(|(other, _, c)| other != id && color.is_some() && color_field(c) == color)
        || color.is_some_and(|color| authored_pins(board).contains_key(color));
    let checks = [
        (radial.shared.title.as_ref().is_some_and(|t| t.as_str().contains("{{") || t.as_str().contains("{%") || t.as_str().contains("{#")), "a templated `title`"),
        (radial.shared.subtitle.is_some(), "a `subtitle`"),
        (base.notes.is_some(), "`notes`"),
        (base.link.is_some(), "a `link`"),
        (!chart.tooltip.tooltip.is_empty(), "`tooltip:` columns"),
        (!radial.labels.field_labels.is_empty(), "`field_labels`"),
        (radial.total.is_some(), "a `total`"),
        (radial.height.is_some() || radial.width.is_some(), "a `height` or `width`"),
        (chart.pattern.pattern.is_some(), "a `pattern`"),
        (radial.theta.is_none() || radial.color.is_none(), "no authored `theta:` and `color:`"),
        (board.regional.is_some() || run.env.locale != "en-US", "a locale other than en-US"),
        (own, "its own `style:`"),
        (style.legended.legend.is_some(), "a `legend` style"),
        (placed.style.charts.legend.base.visible == Engine::Value(true), "a visible legend"),
        (style.titled.title.is_some(), "a `title` style"),
        (!matches!(style.marks.slice.opacity, Engine::Rule(_)), "a slice `opacity`"),
        (style.marks.slice.labels.r#where.is_some(), "`labels.where`"),
        (query_formats, "a query that declares column formats"),
        (color_plan, "a color field shared with another chart or pinned"),
    ];
    match checks.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(unmodeled(what)),
        None => Ok(()),
    }
}

/// `labeled_mode` (resolve): a slice too small to draw makes the hybrid mode (a table lists the
/// small slices) and no slice big enough to label the table-only one. The size stage may still
/// choose the table for a direct pie, which layout refuses.
fn refuse_unlabeled_mode(shares: &[f64]) -> Result<(), Diagnostic> {
    if !shares.iter().any(|&share| share > WEDGE_LABEL_MIN_SHARE) {
        return Err(Data::NoLabel.refusal());
    }
    if shares.iter().copied().fold(f64::INFINITY, f64::min) < INVISIBLE_SLICE_SHARE {
        return Err(Data::TooSmall.refusal());
    }
    Ok(())
}

/// The palette a pie paints with (`_effective_palette`): the board's categorical one.
fn palette(style: &PieChartStyle, scope: &Style) -> Result<Vec<String>, Diagnostic> {
    let board = style
        .color
        .categorical
        .as_ref()
        .or(scope.charts.color.categorical.as_ref())
        .ok_or_else(|| diag::err_internal("the total style holds `charts.color.categorical`"))?;
    match &board.palette {
        PaletteChoice::Colors(colors) if !colors.is_empty() => Ok(colors.iter().map(|c| c.as_str().to_string()).collect()),
        PaletteChoice::Colors(_) => Err(diag::err_internal("the board palette names no color")),
        PaletteChoice::Name(name) => Err(diag::err_internal(format!("a palette name {name:?} reaches bind unresolved"))),
    }
}

/// `default_slice_label_template`: the theme's default label with `{value_line}` filled by the
/// tooltip's format, its affixes around `{{ value | format(<spec>) }}`. An affix holding `{` would
/// read as template syntax.
fn default_label_template(theme: &str, prefix: &str, spec: &str, suffix: &str) -> Result<String, Diagnostic> {
    if prefix.contains('{') || suffix.contains('{') {
        return Err(Label::Affix.refusal());
    }
    // `jinja_string_literal`: the spec embedded in the quote kind it does not hold.
    let quote = match (spec.contains('\''), spec.contains('"')) {
        (false, _) => '\'',
        (true, false) => '"',
        (true, true) => return Err(Label::Quote.refusal()),
    };
    let value_line = format!("{prefix}{{{{ value | format({quote}{}{quote}) }}}}{suffix}", spec.replace('\\', "\\\\"));
    Ok(theme.replace("{value_line}", &value_line))
}

/// A donut's center total (`bind_center`, `_auto_total_label`): the formatted sum and its caption.
/// A pie draws none, nor does a pie-typed chart with a hole.
fn center(
    placed: &Placed,
    style: &PieChartStyle,
    drawn: bool,
    theta: &str,
    total: f64,
    vote: &[f64],
    locale: &crate::formats::locale::NumberLocale,
) -> Result<Option<PieCenter>, Diagnostic> {
    if !drawn {
        return Ok(None);
    }
    // A donut reads `charts.donut` over `charts.pie`; the patch holds the board's pie copied in.
    let donut = placed.style.charts.donut.as_ref().and_then(|patch| match patch.total.value.format.as_ref() {
        Tri::Value(format) => Some(format),
        Tri::Unset | Tri::Null => None,
    });
    let authored = match donut.or(style.total.value.format.as_ref()) {
        Some(FormatSpec::Name(name)) => Some(AuthoredFormat::named(name.as_str())),
        Some(FormatSpec::Block(_)) => return Err(format_block()),
        None => None,
    };
    let format = column_format(Kind::Number, Tiers { board: authored.as_ref(), ..Tiers::default() }, Formats::new(None), Some("integer"), None, vote)?;
    let label = display_text::slug_to_text(theta).map_err(diag::err_internal)?;
    let total_word = display_text::language::tr("pie.total_word", None, &[]).map_err(diag::err_internal)?;
    let caption = if label.to_lowercase().contains(&total_word.to_lowercase()) {
        label
    } else {
        let inline = display_text::inline_derived_label(&label).map_err(diag::err_internal)?;
        display_text::language::tr("pie.total", None, &[("label", inline.as_str())]).map_err(diag::err_internal)?
    };
    Ok(Some(PieCenter { value: format.paint(locale, total)?, caption }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_spec_holding_both_quote_kinds_is_refused_by_name() {
        let refused = default_label_template("{value_line}", "", "a'b\"c", "").unwrap_err().to_string();
        assert!(refused.contains("a pie label value format spec holding both quote kinds is not supported"), "{refused}");
    }

    #[test]
    fn an_affix_holding_a_brace_is_refused_by_name() {
        let refused = default_label_template("{value_line}", "{", ",.0f", "").unwrap_err().to_string();
        assert!(refused.contains("a pie label value format affix holding `{` is not supported"), "{refused}");
    }

    #[test]
    fn a_spec_is_embedded_in_the_quote_kind_it_does_not_hold() {
        assert_eq!(default_label_template("{value_line}", "$", ",.0f", "!").unwrap(), "${{ value | format(',.0f') }}!");
        assert_eq!(default_label_template("{value_line}", "", "a'b", "").unwrap(), "{{ value | format(\"a'b\") }}");
    }
}
