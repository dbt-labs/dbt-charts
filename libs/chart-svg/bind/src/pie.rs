//! A pie or donut's data: each wedge's value, fill, tooltip text and label, and a donut's center
//! total, and the attached tables a pie lists its slices in when the wheel cannot label them all.
//! `chart-renderer/src/families/pie/bind.rs` (`bind_wheel`, `bind_slices`, `bind_labels`,
//! `bind_center`, `pie_row_aria`, `bind_attached`) and `compile/resolve/chart/pie.py` (`_resolve_pie`,
//! `labeled_mode`, `attachment_for`) decide the same facts; what this slice does not model is refused
//! by name.
//!
//! Read off `charts.pie` with the family tier's background and padding. The data is each
//! wedge's theta, the compensated shares the label floor reads, the default label template with
//! the tooltip's voted format, wedge fills by row position, companion ink, tooltip text and a
//! donut's center total with its auto caption. A donut reads `charts.donut` over it for the hole
//! and the total format only. The chart's own `style:`, a shared color field
//! and every field the slice does not draw are refused by name. `engine`'s `tests/it/board.rs`
//! draws the pie cases of `board_inputs.json`.

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::PieChart;
use board_model::ids::ChartId;
use board_model::primitives::{FormatSpec, LinkOpen, PaletteChoice};
use board_model::rule::Auto;
use board_model::style::key::KeyType;
use board_model::style::legend::LegendEdge;
use board_model::style::pie::PieChartStyle;
use board_model::style::style::Style;
use column_types::ColumnType;
use d3rs::py_float::py_float_sum;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use model_core::{Engine, SetLeaves, Tri};
use serde_json::{Map, Value};

use crate::derived::{board_ink, Placed};
use crate::formats::locale::{active_number_locale, d3_format};
use crate::formats::{column_format, resolve_format_parts, AuthoredFormat, Formats, Kind, ResolvedFormat, Tiers};
use crate::kpi::declared_column_format;
use crate::label_template::{Scope, Template, Where};
use crate::bound::{TablePosition, CategoryExcess, Empty, Failure, Family, Href, Listed, Pie, PieCenter, PieEmpty, PieKeyEdge, PieLabel, PieLabeled, PieSlice, PieTable, Table, TableCell, TableColumn};
use crate::table::{number_columns, swatch_cell, table_cell, NumberInput, Role};
use board_model::style::table::{CellAlign, SymbolMode, TableChartStyle};
use display_text::slug_to_text;
use crate::run::RunResult;
use crate::category_colors::{self, CategoryColorScale};

/// `WEDGE_LABEL_MIN_SHARE` in `core/constants/pie.py`: a slice labels only past this share.
const WEDGE_LABEL_MIN_SHARE: f64 = 0.08;
/// `INVISIBLE_SLICE_SHARE` in `core/constants/pie.py`: a slice under it is too small to draw, and
/// the pie falls back to listing its small slices in a table.
const INVISIBLE_SLICE_SHARE: f64 = 0.02;

/// `_READABLE_CATEGORIES` in `render/warnings/too_many_color_categories.py`.
const READABLE_CATEGORIES: usize = 8;

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
    ColorNotText,
    ThetaNotNumber,
    NoColor,
    NoTheta,
    NoTotal,
}

impl Data {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Data::ColorNotText => "a color column that is not text",
            Data::ThetaNotNumber => "a theta column that is not a plain number",
            Data::NoColor => "a slice with no color value",
            Data::NoTheta => "a slice with no theta value",
            Data::NoTotal => "a theta that does not sum to a positive total",
        };
        Diagnostic::refused(refuse!("a pie over {what} is not supported"))
    }
}

/// A slice label's template, `where` or value format that this engine does not evaluate.
enum Label {
    Template(String),
    Where(String),
    Affix,
    Quote,
}

impl Label {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Label::Template(why) => format!("template this engine does not evaluate ({why})"),
            Label::Where(why) => format!("`where` this engine does not evaluate ({why})"),
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
pub fn bind_pie(
    id: &ChartId,
    chart: &PieChart,
    donut: bool,
    board: &Board,
    run: &RunResult,
    placed: &Placed,
    scales: &[CategoryColorScale],
) -> Result<Result<Family, Failure>, Diagnostic> {
    let style = &placed.style.charts.pie;
    let hole = effective_inner_radius(placed, donut)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.radial.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset = run.dataset(query, id)?;
    // `total:` (`resolve/chart/pie.py`): a visible total needs a hole to sit in, which production
    // raises at resolve as the chart's error.
    if chart.radial.total.as_ref().is_some_and(|total| total.visible) && hole <= 0.0 {
        let diagnostic = diag::err_pie_total_without_hole(format!("charts.{}.total", placed.public_id)).with_sink_param("chart_id", placed.public_id.into());
        return Ok(Err(Failure { chart: id.clone(), diagnostic }));
    }
    refuse_unmodeled(chart, donut, placed)?;
    let identity_color = chart.radial.color.as_ref().map(|color| color.as_str());
    if crate::pattern::identity_series(placed.style.palettes.contains_key("fill_category"), None, identity_color, &[], false).is_some() {
        return Err(Diagnostic::refused(refuse!("a pie patterned by `palettes.fill_category` is not supported")));
    }
    let (Some(theta), Some(color)) = (&chart.radial.theta, &chart.radial.color) else {
        return Err(diag::err_internal("a pie without `theta:` and `color:` passed the unmodeled refusals"));
    };
    let (theta, color) = (theta.as_str(), color.as_str());
    let link = crate::links::chart_link(chart.radial.shared.base.link.as_ref(), crate::links::automatic(run, placed.public_id), board)?;
    // A chart that authors no `link:` draws the host's automatic one, already resolved for its page.
    let automatic = chart.radial.shared.base.link.is_none();
    // `?name={{ color }}` sets a board variable from the clicked slice, which drives the board's
    // selection (`selected::Selection`): the recede and the legend's series links.
    if link.is_some_and(|(template, _)| crate::links::sets_variable(template)) {
        return Err(Diagnostic::refused(refuse!("a pie whose link sets a variable from its slices is not supported")));
    }
    let link_open = link.map_or(LinkOpen::Page, |(_, open)| open);
    let rows = dataset.rows.as_slice();
    let scope = placed.scope();
    let canvas = placed.canvas(&style.base.background.clone().map_or(Tri::Unset, Tri::Value), scope)?;
    let ground = placed.canvas(&Tri::Unset, scope)?;
    let background = style.base.background.as_ref().map_or(placed.style.charts.background.as_str(), |c| c.as_str()).to_string();
    let title = chart.radial.shared.title.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    let subtitle = chart.radial.shared.subtitle.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    // `no_results::nothing_to_plot`: a query with no row draws the card's "No results" before any
    // column is read.
    if rows.is_empty() {
        let headline = placed.style.charts.key.legend.base.label.element.font.clone();
        let empty = Some(PieEmpty { empty: Empty { filters: run.empty_filters_line(placed.public_id) }, headline });
        // No slice, so no table: layout draws the card and never reads these.
        let full = Ok(PieTable { heading: None, table: Table { columns: Vec::new(), empty: None, links: None }, value_widths: Vec::new() });
        return Ok(Ok(Family::Pie(Box::new(Pie { canvas, ground, background, title, subtitle, hole, slices: Vec::new(), link_open, total: 0.0, center: None, table_position: table_position(placed, style), labeled: PieLabeled::Direct, full, excess_categories: None, empty }))));
    }
    if dataset.schema.get(color).map_err(diag::err_internal)? != &ColumnType::String {
        return Err(Data::ColorNotText.refusal());
    }
    // `validate_pie_theta` (resolve): a null theta, then a negative one, is the chart's error; a
    // text theta is `refuse_text_measures`' at render.
    let chart_error = |diagnostic: Diagnostic| Ok(Err(Failure { chart: id.clone(), diagnostic }));
    let null_rows = rows.iter().filter(|row| matches!(row.get(theta), Some(Value::Null))).count();
    if null_rows > 0 {
        return chart_error(diag::err_pie_null_theta(placed.public_id, null_rows, theta));
    }
    let negative_rows = rows.iter().filter(|row| row.get(theta).and_then(Value::as_f64).is_some_and(|v| v < 0.0)).count();
    if negative_rows > 0 {
        return chart_error(diag::err_pie_negative_theta(placed.public_id, negative_rows, theta));
    }
    match dataset.schema.get(theta).map_err(|_| missing_theta_column())? {
        ColumnType::Integer | ColumnType::Float => {}
        ColumnType::String => {
            let source_path = format!("charts.{}", placed.public_id);
            return chart_error(diag::err_chart_measure_is_text(theta).with_sink_param("source_path", source_path.into()).with_sink_param("chart_id", placed.public_id.into()));
        }
        _ => return Err(Data::ThetaNotNumber.refusal()),
    }
    let mut thetas = Vec::with_capacity(rows.len());
    let mut numbers = Vec::with_capacity(rows.len());
    let mut names = Vec::with_capacity(rows.len());
    for row in rows {
        match (row.get(theta), row.get(color)) {
            (Some(Value::Number(n)), Some(Value::String(name))) => {
                thetas.push(n.as_f64().ok_or_else(|| diag::err_internal("a JSON number has an f64 form"))?);
                numbers.push(n);
                names.push(name.as_str());
            }
            (Some(Value::Number(_)), _) => return Err(Data::NoColor.refusal()),
            _ => return Err(Data::NoTheta.refusal()),
        }
    }
    // `compute_shares` (resolve) reads the compensated sum; the render stage reads the plain one.
    let shares_total = py_float_sum(&thetas);
    if !shares_total.is_finite() || shares_total <= 0.0 {
        return Err(Data::NoTotal.refusal());
    }
    let total: f64 = thetas.iter().sum();
    let shares: Vec<f64> = thetas.iter().map(|v| v / shares_total).collect();

    let palette = palette(style, scope)?;
    let companions = palette.iter().map(|c| color::mark_ink(c, &canvas, board_ink(placed.style))).collect::<Result<Vec<_>, _>>().map_err(diag::err_internal)?;
    // `category_scale_for`: a field the board's plan seats (shared by charts, or pinned) paints each
    // slice its board slot (`category_paint`); any other paints the row's own palette slot
    // (`palette_slot`).
    let scale = scales.iter().find(|scale| scale.field == color);

    let labels = &style.marks.slice.labels;
    let vote: Vec<f64> = thetas.iter().copied().chain([py_float_sum(&thetas)]).collect();
    let board_tooltip = AuthoredFormat::named(placed.style.charts.tooltip.format.as_str());
    let aliases = crate::formats::aliases(&placed.style.formats);
    let formats = Formats::new(Some(&aliases));
    let declared = declared_column_format(board, query, theta, formats, false)?;
    let tooltip = column_format(Kind::Number, Tiers { column: declared.as_ref(), board: Some(&board_tooltip), ..Tiers::default() }, formats, None, None, &vote)?;
    let locale = active_number_locale();
    let extra = crate::tooltip::resolve(&chart.tooltip.tooltip, &chart.radial.labels.field_labels, board, query, rows, formats)?;
    let template = match &labels.template {
        Some(authored) => authored.clone(),
        None => default_label_template(&labels.default_template.with_color, &tooltip.prefix, &tooltip.spec, &tooltip.suffix)?,
    };
    let template = Template::parse(&template).map_err(|e| Label::Template(e).refusal())?;
    // A donut's `where` is its `charts.donut` leaf over the pie's (`_family_reaches_recipe`).
    // A donut never reaches here with its own `where`: any `charts.donut` leaf but the hole and the
    // total format is refused ([`effective_inner_radius`]), and a board's `charts.pie` reaches it as one.
    let keep = labels.r#where.as_ref().map(|source| Where::parse(source.as_str()).map_err(|e| Label::Where(e).refusal())).transpose()?;
    if tooltip.spec.is_empty() {
        return Err(diag::err_internal("the tooltip format settles a spec"));
    }
    let field_labels = &chart.radial.labels.field_labels;
    let value_title = field_display_text(theta, field_labels, placed.style.charts.key.legend.base.title.element.font.case.as_str())?;
    let key = pie_key(placed, style);
    let mut slices = Vec::with_capacity(rows.len());
    for (idx, row) in rows.iter().enumerate() {
        let slot = idx % palette.len();
        let fill = match scale {
            Some(scale) => category_colors::color_at(scale, names[idx], &palette).map_err(diag::err_internal)?,
            None => palette[slot].clone(),
        };
        let share = thetas[idx] / total;
        let mut aria = vec![
            format!("{ROLE_HEADER_SWATCHED}{}", names[idx]),
            format!("%: {}", d3_format(".0%", share).map_err(diag::err_internal)?),
            format!("{MUTED}{value_title}: {}", tooltip.paint(&locale, thetas[idx])?),
        ];
        aria.extend(extra.rows(row, &dataset.schema, &locale)?);
        aria.push(format!("{MUTED}{ROLE_TOTAL}Σ: {}", tooltip.paint(&locale, total)?));
        let aria = aria.join("; ");
        let scope = Scope { row, theta_field: theta, color_field: Some(color), percent: share, total };
        // `prepare_label_data`: a slice its `where` rejects gets no label.
        let kept = keep.as_ref().map_or(Ok(true), |w| w.holds(&scope)).map_err(|e| Label::Where(e).refusal())?;
        let label = if kept && key.kind != KeyKind::Legend && shares[idx] > WEDGE_LABEL_MIN_SHARE {
            let lines = template.render(&scope).map_err(|e| Label::Template(e).refusal())?;
            let ink = match scale {
                Some(scale) => category_colors::ink_at(scale, names[idx], &companions, &canvas, board_ink(placed.style)).map_err(diag::err_internal)?,
                None => companions[slot].clone(),
            };
            (!lines.is_empty()).then_some(PieLabel { lines, ink })
        } else {
            None
        };
        let href = link.map(|(template, _)| slice_href(template, automatic, row, theta, color, run)).transpose()?;
        slices.push(PieSlice { value: thetas[idx], fill, aria, label, href });
    }
    // `merge_unset(board_pie, primary)`: a pie-typed chart whose own `style:` sets the hole takes the
    // donut's derived caption too.
    let auto = hole > 0.0 && (donut || own_hole(placed).is_some());
    let center = match center_caption(chart, auto, theta, field_labels)? {
        Some(caption) => Some(center(placed, donut, caption, declared.as_ref(), total, &vote, &locale)?),
        None => None,
    };
    let attach = |rows: Vec<usize>, heading: Option<String>| refuse_table_style(&placed.style.charts.table).and_then(|()| attached_table(&rows, &shares, &names, &slices, &numbers, placed.style.charts.table.symbol_mode, formats, declared.as_ref(), heading));
    let labeled = labeled_mode(&shares, key, || {
        let small = (0..shares.len()).filter(|&i| shares[i] <= WEDGE_LABEL_MIN_SHARE).collect();
        attach(small, Some(display_text::language::tr("pie.too_small_to_label", None, &[]).map_err(diag::err_internal)?))
    });
    let full = attach((0..shares.len()).collect(), None);
    // `too_many_color_categories.py`: a reader tells about eight hues apart, more when the palette is longer.
    let (distinct, max_categories) = (names.iter().collect::<std::collections::BTreeSet<_>>().len(), palette.len().max(READABLE_CATEGORIES));
    let excess_categories = (distinct > max_categories).then(|| CategoryExcess { field: color.to_string(), count: distinct, max_categories });
    let background = style.base.background.as_ref().map_or(placed.style.charts.background.as_str(), |c| c.as_str()).to_string();
    let title = chart.radial.shared.title.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    let subtitle = chart.radial.shared.subtitle.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    Ok(Ok(Family::Pie(Box::new(Pie { canvas, ground, background, title, subtitle, hole, slices, link_open, total, center, table_position: table_position(placed, style), labeled, full, excess_categories, empty: None }))))
}

/// `row_href` over `pie_link_value`: the link's `{{ color }}` is the slice's name, `{{ theta }}` its
/// value, and any other name the row's column of that name; a pie has no `x` or `y`. An authored
/// href resolves in the host's link context, as a bar's and a table cell's do (`href::hosted`); the host's automatic link
/// is drawn as given (production applies no `resolve_href` to it). Either has its scheme checked.
fn slice_href(template: &str, automatic: bool, row: &Map<String, Value>, theta: &str, color: &str, run: &RunResult) -> Result<Href, Diagnostic> {
    let raw = crate::links::expand_channels(template, |channel| match channel {
        "color" => crate::links::js_string(row.get(color)),
        "theta" => crate::links::js_string(row.get(theta)),
        "x" | "y" => Err(Diagnostic::refused(format!("link template references channel '{{{{ {channel} }}}}' but chart has no '{channel}' encoding assigned"))),
        column => crate::links::row_column(row, column),
    })?;
    if automatic { Href::new(&raw) } else { crate::links::resolve(&raw, run) }
}

/// What the pie's `style.key` decides (`_resolve_pie`): whether the key is shown at all and its form.
#[derive(Clone, Copy)]
struct KeyForm {
    visible: bool,
    kind: KeyKind,
}

/// `auto` leaves the form to the slices, `legend` is the full attached table with no slice labels,
/// `direct` names the slices on the wedges and lists only the unlabeled ones in a table.
#[derive(Clone, Copy, PartialEq)]
enum KeyKind {
    Auto,
    Legend,
    Direct,
}

/// The pie's key as the pie family's patch over the board's `charts.key`.
fn pie_key(placed: &Placed, style: &PieChartStyle) -> KeyForm {
    let family = style.keyed.key.as_ref();
    let charts = &placed.style.charts.key;
    let kind = match family.and_then(|key| key.r#type.as_ref()).unwrap_or(&charts.r#type) {
        Engine::Value(KeyType::Legend) => KeyKind::Legend,
        Engine::Value(KeyType::Direct) => KeyKind::Direct,
        Engine::Rule(_) => KeyKind::Auto,
    };
    KeyForm { visible: family.and_then(|key| key.visible).unwrap_or(charts.visible), kind }
}

/// `core/text/case.py::field_display_text`: the chart's `field_labels` entry for `field` verbatim,
/// else the field's derived text in `case`.
fn field_display_text(field: &str, field_labels: &IndexMap<String, String>, case: &str) -> Result<String, Diagnostic> {
    match field_labels.get(field) {
        Some(label) => Ok(label.clone()),
        None => display_text::case::apply_case(&display_text::slug_to_text(field).map_err(diag::err_internal)?, case).map_err(diag::err_internal),
    }
}

/// `merge_authored_position`: a leaf the pie's patch sets, else the board's; an auto edge or align is none.
fn table_position(placed: &Placed, style: &PieChartStyle) -> TablePosition {
    let charts = &placed.style.charts;
    let family = style.keyed.key.as_ref().map(|key| &key.legend);
    let edge = match family.and_then(|legend| legend.position.edge.as_ref()).map(authored_edge) {
        Some(edge) => edge,
        None => authored_edge(&charts.key.legend.position.edge),
    };
    let align = match family.and_then(|legend| legend.position.align.as_ref()) {
        Some(align) => align,
        None => &charts.key.legend.position.leaves.align,
    };
    TablePosition {
        edge: edge.map(|edge| match edge {
            LegendEdge::Left => PieKeyEdge::Left,
            LegendEdge::Right => PieKeyEdge::Right,
            LegendEdge::Top => PieKeyEdge::Top,
            LegendEdge::Bottom => PieKeyEdge::Bottom,
        }),
        align: match align {
            Engine::Rule(Auto::Auto) => None,
            Engine::Value(align) => Some(*align),
        },
    }
}

fn authored_edge(edge: &Engine<LegendEdge, Auto>) -> Option<LegendEdge> {
    match edge {
        Engine::Value(edge) => Some(*edge),
        Engine::Rule(Auto::Auto) => None,
    }
}

/// The hole a pie draws: a donut reads `charts.donut` over `charts.pie`, which for any leaf but
/// the hole is the pie's own board tier (`family_reaches_recipe` copies it in); a donut whose patch
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
        // The chart's own hole, folded into `pie`, beats the board's `charts.donut`.
        None if own_hole(placed).is_some() => Ok(pie),
        None => Ok(patch.inner_radius.as_ref().map_or(pie, |hole| hole.get())),
    }
}

/// The hole the chart's own `style:` sets, which production reads from its own tier alone
/// (`"inner_radius" in normalized.style.model_fields_set`).
fn own_hole(placed: &Placed) -> Option<f64> {
    placed.own.charts.pie.inner_radius.as_ref().map(|hole| hole.get())
}

/// Every chart field and style that changes what the pie says and is not modeled:
/// the chart's fields, and the family styles this slice does not paint.
/// The chart's own `style:` needs no check of its own: production reads it folded over the board's
/// family tier (`merge_unset(board_pie, primary)`), which is `placed.style`, so these checks see
/// it, except the two leaves it reads from the own tier alone, the hole ([`own_hole`]) and the
/// total format ([`center`]).
fn refuse_unmodeled(
    chart: &PieChart,
    donut: bool,
    placed: &Placed,
) -> Result<(), Diagnostic> {
    let style = &placed.style.charts.pie;
    let radial = &chart.radial;
    let checks = [
        (radial.shared.title.as_ref().is_some_and(|t| t.as_str().contains("{{") || t.as_str().contains("{%") || t.as_str().contains("{#")), "a templated `title`"),
        (radial.shared.subtitle.as_ref().is_some_and(|t| t.as_str().contains("{{") || t.as_str().contains("{%") || t.as_str().contains("{#")), "a templated `subtitle`"),
        (radial.total.as_ref().and_then(|t| t.label.as_ref()).is_some_and(|l| l.as_str().contains("{{") || l.as_str().contains("{%") || l.as_str().contains("{#")), "a templated `total.label`"),
        // A pie-typed chart takes no derived caption unless its own `style:` sets the hole, so its
        // visible total over a board's hole draws the value alone.
        (!donut && own_hole(placed).is_none() && radial.total.as_ref().is_some_and(|t| t.visible && t.label.is_none()), "a `total` drawn with no caption"),
        (chart.pattern.pattern.is_some(), "a `pattern`"),
        (radial.theta.is_none() || radial.color.is_none(), "no authored `theta:` and `color:`"),
        (style.titled.title.is_some(), "a `title` style"),
        (!matches!(style.marks.slice.opacity, Engine::Rule(_)), "a slice `opacity`"),
        (!style.conditional.is_empty(), "`style.conditional`"),
    ];
    match checks.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(unmodeled(what)),
        None => Ok(()),
    }
}

/// `labeled_mode` (resolve): the key decides first (`legend` is the full table alone, a hidden key the bare
/// wheel, `direct` the wedge labels with a table of the slices too small to label); otherwise the slices alone
/// decide whether a pie may label its big slices on the wheel (a slice under `INVISIBLE_SLICE_SHARE` makes it
/// hybrid, with a table listing the small ones) or only a table of every slice labels it (no slice clears
/// `WEDGE_LABEL_MIN_SHARE`). Whether the box lets the labels stand is the size stage's call.
fn labeled_mode(shares: &[f64], key: KeyForm, small: impl FnOnce() -> Listed) -> PieLabeled {
    let smallest = shares.iter().copied().fold(f64::INFINITY, f64::min);
    if !key.visible {
        PieLabeled::Bare
    } else if key.kind == KeyKind::Legend {
        PieLabeled::None
    } else if key.kind == KeyKind::Direct {
        if smallest <= WEDGE_LABEL_MIN_SHARE { PieLabeled::Hybrid(small()) } else { PieLabeled::Direct }
    } else if !shares.iter().any(|&share| share > WEDGE_LABEL_MIN_SHARE) {
        PieLabeled::None
    } else if smallest < INVISIBLE_SLICE_SHARE {
        PieLabeled::Hybrid(small())
    } else {
        PieLabeled::Direct
    }
}

/// The table style a pie's table reads and the engine does not model for it: production's `_table`
/// applies `column_defaults`, `transpose` and `row.conditional` to the attached table too.
fn refuse_table_style(table: &TableChartStyle) -> Result<(), Diagnostic> {
    let defaults = &table.column_defaults;
    let what = if defaults.width.is_some() || defaults.align.is_some() || defaults.format.is_some() || defaults.background.is_some() || defaults.font.is_some() {
        "`style.column_defaults`"
    } else if table.transpose {
        "`style.transpose`"
    } else if !table.row.conditional.is_empty() {
        "`style.row.conditional`"
    } else {
        return Ok(());
    };
    Err(unmodeled(&format!("an attached table under {what}")))
}

/// `plan_attachment`'s rows (`project_pie_table_rows`) as a table the table family draws: the wedge's
/// own fill as the swatch, the slice label's `.0%` share, the name and the value. The value column is the
/// table's own numeric column (`number_columns`, each value at its own magnitude: `allow_shared_scale` false) under
/// the format the query declares for the theta, else the type default (`inferred_number_format`).
/// The header is never drawn (`header.visible: false`).
#[allow(clippy::too_many_arguments)]
fn attached_table(
    rows: &[usize],
    shares: &[f64],
    names: &[&str],
    slices: &[PieSlice],
    numbers: &[&serde_json::Number],
    symbols: SymbolMode,
    formats: Formats<'_>,
    declared: Option<&AuthoredFormat>,
    heading: Option<String>,
) -> Result<PieTable, Diagnostic> {
    let text = |name: &str| table_cell(Some(&Value::String(name.to_string())), &ColumnType::String);
    let mut swatch = Vec::with_capacity(rows.len());
    let mut share = Vec::with_capacity(rows.len());
    let mut name = Vec::with_capacity(rows.len());
    let mut value = Vec::with_capacity(rows.len());
    let measured_format = declared.map(|format| resolve_format_parts(format, formats)).transpose()?;
    let mut value_widths = Vec::with_capacity(rows.len());
    for &i in rows {
        swatch.push(TableCell::Text(swatch_cell(Some(&Value::String(slices[i].fill.clone())))?));
        share.push(text(&d3_format(".0%", shares[i]).map_err(diag::err_internal)?)?);
        name.push(text(names[i])?);
        value.push(numbers[i]);
        value_widths.push(value_width_text(slices[i].value, measured_format.as_ref())?);
    }
    let cells: Vec<Option<&serde_json::Number>> = value.iter().map(|n| Some(*n)).collect();
    let input = NumberInput { cells: &cells, name: "value", tiers: Tiers { own: declared, ..Tiers::default() }, at: "a pie's attached table value column", role: Role::Painted };
    let value = number_columns(&[input], formats, symbols, false)?.remove(0);
    let column = |key: &str, cells: Vec<TableCell>, align: CellAlign, swatch: bool| -> Result<TableColumn, Diagnostic> {
        Ok(TableColumn { header: slug_to_text(key).map_err(diag::err_internal)?, cells, align, width: None, swatch, max_width: None, background: None, ink: None, weight: None })
    };
    let columns = vec![
        column("swatch", swatch, CellAlign::Left, true)?,
        column("share", share, CellAlign::Right, false)?,
        column("name", name, CellAlign::Left, false)?,
        column("value", value, CellAlign::Right, false)?,
    ];
    Ok(PieTable { heading, table: Table { columns, empty: None, links: None }, value_widths })
}

/// `_format_value_for_width`: the value column is measured at its value under `,.0f` (or the column
/// format's own `.0%` or `$,.0f`) between the format's affixes, whatever the cell prints.
fn value_width_text(value: f64, format: Option<&ResolvedFormat>) -> Result<String, Diagnostic> {
    let spec = format.and_then(|f| f.raw.as_deref()).filter(|raw| matches!(*raw, ".0%" | "$,.0f")).unwrap_or(",.0f");
    let (prefix, suffix) = format.map_or(("", ""), |f| (f.prefix.as_str(), f.suffix.as_str()));
    Ok(format!("{prefix}{}{suffix}", d3_format(spec, value).map_err(diag::err_internal)?))
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

/// The caption under the center total, or `None` for no center (`_auto_total_label`,
/// `ChartTotal`): a donut's derived caption unless `total:` names one, nothing for a pie or a
/// hidden total, and the authored `total.label` of a visible one.
fn center_caption(chart: &PieChart, auto: bool, theta: &str, field_labels: &IndexMap<String, String>) -> Result<Option<String>, Diagnostic> {
    match &chart.radial.total {
        Some(total) if !total.visible => Ok(None),
        Some(total) => match &total.label {
            Some(label) => Ok(Some(label.as_str().to_string())),
            None if auto => auto_caption(theta, field_labels).map(Some),
            None => Err(diag::err_internal("a pie's captionless total passed the unmodeled refusals")),
        },
        None if auto => auto_caption(theta, field_labels).map(Some),
        None => Ok(None),
    }
}

/// `_auto_total_label`: the theta column's name as a total's caption, its `field_labels` entry when
/// the chart has one (painted as authored), else its derived name lowered inline.
fn auto_caption(theta: &str, field_labels: &IndexMap<String, String>) -> Result<String, Diagnostic> {
    let authored = field_labels.get(theta);
    let label = match authored {
        Some(label) => label.clone(),
        None => display_text::slug_to_text(theta).map_err(diag::err_internal)?,
    };
    let total_word = display_text::language::tr("pie.total_word", None, &[]).map_err(diag::err_internal)?;
    if label.to_lowercase().contains(&total_word.to_lowercase()) {
        return Ok(label);
    }
    let inline = match authored {
        Some(_) => label,
        None => display_text::inline_derived_label(&label).map_err(diag::err_internal)?,
    };
    display_text::language::tr("pie.total", None, &[("label", inline.as_str())]).map_err(diag::err_internal)
}

/// A pie's center total (`bind_center`): the formatted sum over `caption`.
fn center(
    placed: &Placed,
    donut: bool,
    caption: String,
    column: Option<&AuthoredFormat>,
    total: f64,
    vote: &[f64],
    locale: &crate::formats::locale::NumberLocale,
) -> Result<PieCenter, Diagnostic> {
    let authored = |format: Option<&FormatSpec>| match format {
        Some(FormatSpec::Name(name)) => Ok(Some(AuthoredFormat::named(name.as_str()))),
        Some(FormatSpec::Block(_)) => Err(format_block()),
        None => Ok(None),
    };
    // `chart_total_format`: the chart's own `total.value.format`, its strongest layer.
    let own = authored(match &placed.own.charts.pie.total.value.format {
        Tri::Value(format) => Some(format),
        Tri::Unset | Tri::Null => None,
    })?;
    // `board_total_format`: the board's family tier, where a donut reads `charts.donut` over
    // `charts.pie` (the patch holds the board's pie copied in) and a pie reads `charts.pie`.
    let scope = placed.scope();
    let donut = scope.charts.donut.as_ref().filter(|_| donut).and_then(|patch| match patch.total.value.format.as_ref() {
        Tri::Value(format) => Some(format),
        Tri::Unset | Tri::Null => None,
    });
    let board = authored(donut.or(scope.charts.pie.total.value.format.as_ref()))?;
    let aliases = crate::formats::aliases(&placed.style.formats);
    let tiers = Tiers { own: own.as_ref(), column, board: board.as_ref(), ..Tiers::default() };
    let format = column_format(Kind::Number, tiers, Formats::new(Some(&aliases)), Some("integer"), None, vote)?;
    Ok(PieCenter { value: format.paint(locale, total)?, caption })
}

#[cfg(test)]
mod tests {
    use super::*;

    const AUTO: KeyForm = KeyForm { visible: true, kind: KeyKind::Auto };

    #[test]
    fn the_shares_alone_decide_the_labeled_mode() {
        let small = || Ok(PieTable { heading: None, table: Table { columns: Vec::new(), empty: None, links: None }, value_widths: Vec::new() });
        assert_eq!(labeled_mode(&[0.5, 0.3, 0.2], AUTO, small), PieLabeled::Direct);
        assert!(matches!(labeled_mode(&[0.6, 0.25, 0.14, 0.01], AUTO, small), PieLabeled::Hybrid(_)));
        assert_eq!(labeled_mode(&[1.0 / 14.0; 14], AUTO, small), PieLabeled::None);
        // A slice at the label floor is not labeled (`share > WEDGE_LABEL_MIN_SHARE`).
        assert_eq!(labeled_mode(&[0.08, 0.08, 0.84 / 2.0, 0.84 / 2.0], AUTO, small), PieLabeled::Direct);
        assert_eq!(labeled_mode(&[0.08; 12].iter().chain(&[0.04]).copied().collect::<Vec<_>>(), AUTO, small), PieLabeled::None);
    }

    #[test]
    fn a_legend_key_is_the_full_table_alone_whatever_the_shares() {
        let small = || Ok(PieTable { heading: None, table: Table { columns: Vec::new(), empty: None, links: None }, value_widths: Vec::new() });
        let legend = KeyForm { visible: true, kind: KeyKind::Legend };
        assert_eq!(labeled_mode(&[0.5, 0.3, 0.2], legend, small), PieLabeled::None);
        assert_eq!(labeled_mode(&[0.6, 0.25, 0.14, 0.01], legend, small), PieLabeled::None);
    }

    #[test]
    fn a_direct_key_labels_the_wedges_and_never_reaches_the_full_table() {
        let small = || Ok(PieTable { heading: None, table: Table { columns: Vec::new(), empty: None, links: None }, value_widths: Vec::new() });
        let direct = KeyForm { visible: true, kind: KeyKind::Direct };
        assert_eq!(labeled_mode(&[1.0 / 14.0; 14], AUTO, small), PieLabeled::None);
        assert!(matches!(labeled_mode(&[1.0 / 14.0; 14], direct, small), PieLabeled::Hybrid(_)));
        assert!(matches!(labeled_mode(&[0.6, 0.35, 0.05], direct, small), PieLabeled::Hybrid(_)));
        assert_eq!(labeled_mode(&[0.5, 0.3, 0.2], direct, small), PieLabeled::Direct);
    }

    #[test]
    fn a_hidden_key_is_the_bare_wheel_whatever_its_type() {
        let small = || Ok(PieTable { heading: None, table: Table { columns: Vec::new(), empty: None, links: None }, value_widths: Vec::new() });
        for kind in [KeyKind::Auto, KeyKind::Legend, KeyKind::Direct] {
            let hidden = KeyForm { visible: false, kind };
            assert_eq!(labeled_mode(&[1.0 / 14.0; 14], hidden, small), PieLabeled::Bare);
            assert_eq!(labeled_mode(&[0.6, 0.25, 0.14, 0.01], hidden, small), PieLabeled::Bare);
        }
    }

    #[test]
    fn an_attached_table_prints_its_values_under_the_type_default_or_the_declared_format() {
        let (big, small) = (serde_json::Number::from(1500), serde_json::Number::from(40));
        let slices = [PieSlice { value: 1500.0, fill: "#40639c".to_string(), aria: String::new(), label: None, href: None }, PieSlice { value: 40.0, fill: "#779bc9".to_string(), aria: String::new(), label: None, href: None }];
        let table = attached_table(&[0, 1], &[0.9, 0.1], &["A", "B"], &slices, &[&big, &small], SymbolMode::All, Formats::new(None), None, None).unwrap();
        let printed: Vec<String> = table.table.columns[3].cells.iter().map(|cell| cell.text().into_owned()).collect();
        assert_eq!(printed, ["1,500", "40"]);
        // The query's declared theta format is the column's own, so it decides the digits.
        let declared = AuthoredFormat::named("currency");
        let table = attached_table(&[0, 1], &[0.9, 0.1], &["A", "B"], &slices, &[&big, &small], SymbolMode::All, Formats::new(None), Some(&declared), None).unwrap();
        let printed: Vec<String> = table.table.columns[3].cells.iter().map(|cell| cell.text().into_owned()).collect();
        assert_ne!(printed, ["1,500", "40"]);
    }

    #[test]
    fn an_attached_table_holds_a_swatch_share_name_and_value_per_listed_slice() {
        let (a, b) = (serde_json::Number::from(60), serde_json::Number::from(1));
        let slice = |fill: &str| PieSlice { value: 0.0, fill: fill.to_string(), aria: String::new(), label: None, href: None };
        let slices = [slice("#40639c"), slice("#775770")];
        let listed = attached_table(&[1], &[0.99, 0.01], &["Apples", "Figs"], &slices, &[&a, &b], SymbolMode::All, Formats::new(None), None, Some("Too small to label".to_string())).unwrap();
        assert_eq!(listed.heading.as_deref(), Some("Too small to label"));
        let cells: Vec<Vec<String>> = listed.table.columns.iter().map(|c| c.cells.iter().map(|cell| cell.text().into_owned()).collect()).collect();
        assert_eq!(cells, [["#775770"], ["1%"], ["Figs"], ["1"]]);
        assert_eq!(listed.table.columns.iter().map(|c| c.swatch).collect::<Vec<_>>(), [true, false, false, false]);
    }

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
