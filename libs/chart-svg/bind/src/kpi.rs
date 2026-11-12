//! A KPI card's data: the headline cell of its one row, cut into the lanes it draws in (glyph, prefix,
//! number, suffix), its label and its support row. `chart-renderer/src/families/kpi.rs::bind` and
//! `compile/resolve/chart/simple.py::_resolve_kpi` decide the same facts; what this slice does not
//! model is refused by name.
//!
//! The headline's format is the chart's own `style.value.format` over the placement's (`kpi_format_native`
//! decides whether d3's raw magnitude letter stays). The support value is one string with its affix
//! gaps kept; its color is the row's own ink, else the tone's (a delta format colors by sign), else
//! the board's secondary ink (`resolve_support_row`).
//!
//! The headline cell is cut into its lanes by `formats/lanes.rs` (`ResolvedFormat::paint_lanes`)
//! under the KPI's compaction policy. A row count, value column or cell type production fails on
//! is the chart's error card.

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::kpi::KpiSupportConfig;
use board_model::chart::KpiChart;
use board_model::facet::{Color, FormatName};
use board_model::ids::ChartId;
use board_model::primitives::{FormatSpec, Tone};
use board_model::query::Query;
use board_model::rule::Auto;
use board_model::style::kpi::KpiChartStyle;
use board_model::style::style::Style;
use column_types::{ColumnType, Dataset};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use model_core::{Engine, Patch, Tri};
use serde_json::{json, Value};

use crate::conditional::{self, good_overrides, support_patch, Signed, SupportPatch};
use crate::formats::locale::NumberLocale;
use crate::formats::{column_format, AuthoredFormat, Formats, Kind, Notation, Repeat, SignPlacement, Tiers};
use crate::bound::{Family, Failure, Kpi, KpiSupport};
use crate::run::RunResult;

/// Binds `chart` to its query's one row, with the total `style` of the placement it is bound at and
/// the chart's `own` style alone and the placing `scope`'s (the board tier of the headline's format,
/// which the chart's own layer is not folded into). A row count or a column production fails on is that chart's
/// error card; a query that returned no rows is refused, since production also reports a zero-rows
/// warning this stage does not raise.
pub fn bind_kpi(id: &ChartId, chart: &KpiChart, style: &Style, own: &Patch<Style>, scope: &Style, board: &Board, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let kpi = &style.charts.kpi;
    refuse_unmodeled(chart, board)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset: &Dataset = run.dataset(query, id)?;
    let Some(value) = &chart.value else {
        return Err(Diagnostic::refused(refuse!("a KPI that names no `value` is not supported")));
    };
    let column = value.as_str();
    let failure = |diagnostic: Diagnostic| {
        Ok(Err(Failure { chart: id.clone(), diagnostic: diagnostic.with_sink_param("source_path", json!(format!("charts.{id}"))) }))
    };
    if let Err(mismatch) = dataset.schema.check_rows(&dataset.rows) {
        // The sink stamps the chart a data error belongs to (`stamp_chart_diagnostic`); the KPI
        // codes carry it in their own template.
        return failure(mismatch.into_diagnostic().with_sink_param("chart_id", json!(id.as_str())));
    }
    let support_column = chart.support.as_ref().and_then(|support| support.value.as_ref()).map(|value| value.as_str());
    let declared = board.body.queries.get(query.name().as_str()).map(declared_formats);
    if declared.is_some_and(|formats| formats.contains_key(column) || support_column.is_some_and(|c| formats.contains_key(c))) {
        return Err(Diagnostic::refused(refuse!("a query's `formats:` for a KPI value is not supported")));
    }
    let row = match dataset.rows.as_slice() {
        [] => return Err(Diagnostic::refused(refuse!("a KPI whose query returned no rows is not supported"))),
        [row] => row,
        rows => return failure(diag::err_kpi_multirow(id.as_str(), rows.len())),
    };
    let Some(cell) = row.get(column) else {
        return failure(diag::err_kpi_value_column_not_found(id.as_str(), column));
    };
    if let Some(missing) = support_column.filter(|column| !row.contains_key(*column)) {
        return failure(diag::err_kpi_value_column_not_found(id.as_str(), missing));
    }
    let value = number(cell, dataset.schema.get(column).map_err(Diagnostic::from)?)?;
    let authored = scope.charts.kpi.value.format.as_ref().map(authored_format);
    let own_format = match &own.charts.kpi.value.format {
        Tri::Value(format) => Some(authored_format(format)),
        Tri::Unset | Tri::Null => None,
    };
    let tiers = Tiers { own: own_format.as_ref(), board: authored.as_ref(), column: None };
    let compact = &kpi.value.compact;
    let compacted = if value.abs() >= compact.threshold { compact.spec.as_str() } else { "" };
    let formats = Formats::new(None);
    let format = column_format(Kind::Number, tiers, formats, Some(compacted), Some(Notation::Narrative), &[value])?;
    // `kpi_format_native`: a literal d3 spec a layer wrote, with no notation of its own, prints d3's
    // raw magnitude letter.
    let unnotated = column_format(Kind::Number, tiers, formats, Some(compacted), None, &[value])?;
    let native = [tiers.own, tiers.board].into_iter().flatten().any(|f| f.spec.is_some()) && unnotated.notation.is_none() && !unnotated.is_house_si();
    // A ratio-shaped value in a percent format is that chart's error card, as the headline's or the
    // support row's.
    let percent_range = |error: Diagnostic| {
        if error.code == "ERR-PERCENT-RANGE" {
            failure(error.with_sink_param("chart_id", json!(id.as_str())))
        } else {
            Err(error)
        }
    };
    let lanes = match format.paint_lanes(&NumberLocale::en_us(), value, native, false) {
        Ok(lanes) => lanes,
        Err(error) => return percent_range(error),
    };
    let patch = support_patch(&chart.style.conditional, row)?;
    let support = match &chart.support {
        Some(support) => match bind_support(support, &patch, kpi, style, row, dataset) {
            Ok(support) => support,
            Err(error) => return percent_range(error),
        },
        None => None,
    };
    // `good` draws the glyph and tone of the headline's sign: the glyph in the arrow face, the whole
    // run in the tone's color.
    let signed = match chart.good {
        Some(good) => good_overrides(good, &kpi.value.glyph.glyphs, &style.tones, Some(cell))?,
        None => Signed::default(),
    };
    let drawn_by_good = signed.glyph.is_some();
    let glyph = signed.glyph.or_else(|| chart.glyph.clone()).unwrap_or_default();
    Ok(Ok(Family::Kpi(Kpi { glyph, arrow: drawn_by_good, ink: signed.tone_color, prefix: lanes.prefix, number: lanes.number, suffix: lanes.suffix, label: label(chart)?, support })))
}

/// `resolve_support_row` (`chart-renderer/src/families/kpi.rs`): the support value as one string
/// (affix gaps kept), its glyph, the explainer and the inks each paints in. `None` when all three
/// are empty.
fn bind_support(support: &KpiSupportConfig, patch: &SupportPatch, kpi: &KpiChartStyle, style: &Style, row: &serde_json::Map<String, Value>, dataset: &Dataset) -> Result<Option<KpiSupport>, Diagnostic> {
    let secondary = style
        .fonts
        .get("secondary")
        .and_then(|font| font.color.as_ref())
        .map(|color| color.as_str().to_string())
        .ok_or_else(|| diag::err_internal("style.fonts.secondary.color is unset after the theme cascade".to_string()))?;
    let (cell, declared) = match &support.value {
        Some(column) => {
            let column = column.as_str();
            let cell = row.get(column).ok_or_else(|| diag::err_internal(format!("the support column {column} is not in the row")))?;
            (cell, dataset.schema.get(column).map_err(Diagnostic::from)?)
        }
        None => (&Value::Null, &ColumnType::String),
    };
    let format = kpi.support.format.as_ref().map(authored_format);
    let numbers: Vec<f64> = cell.as_f64().into_iter().collect();
    let resolved = match &format {
        Some(format) => Some(column_format(Kind::Number, Tiers { own: Some(format), ..Tiers::default() }, Formats::new(None), None, None, &numbers)?),
        None => None,
    };
    let value = match cell {
        Value::Null => String::new(),
        Value::String(_) if matches!(declared, ColumnType::Date | ColumnType::Datetime | ColumnType::DatetimeTz) => return Err(Diagnostic::refused(refuse!("a KPI support value that is a date is not supported"))),
        Value::String(text) => text.clone(),
        Value::Number(n) => {
            let number = n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?;
            let lanes = resolved.clone().unwrap_or_default().paint_lanes(&NumberLocale::en_us(), number, false, true)?;
            format!("{}{}{}", lanes.prefix, lanes.number, lanes.suffix)
        }
        _ => return Err(Diagnostic::refused(refuse!("a KPI support value that is not a number or text is not supported"))),
    };
    // `good` sees the raw cell (a null cell is none).
    let cell_or_null = Some(cell).filter(|cell| !cell.is_null());
    let signed = match (support.good, support.value.is_some()) {
        (Some(good), true) => good_overrides(good, &kpi.support.glyph.glyphs, &style.tones, cell_or_null)?,
        _ => Signed::default(),
    };
    let glyph = signed.glyph.clone().or_else(|| support.glyph.clone()).unwrap_or_default();
    let explainer = match &support.label {
        Some(label) => plain_text(label.as_str(), "a KPI support label with template syntax is not supported")?.unwrap_or_default(),
        None => String::new(),
    };
    if value.is_empty() && explainer.is_empty() && glyph.is_empty() {
        return Ok(None);
    }
    let tone_color = |tone: Tone| conditional::tone_color(&style.tones, tone).to_string();
    let is_delta = resolved.as_ref().and_then(|format| format.raw.as_deref()).is_some_and(|raw| DELTA_FORMATS.contains(&raw));
    // A delta value is colored by its sign and `tone` is the direction (positive, the default: up is
    // good; negative: up is bad); zero and a non-number stay un-toned. Any other tone is a fixed color.
    let authored_tone = patch.tone.or(kpi.support.tone);
    let tone = match (authored_tone, is_delta) {
        (None, false) => None,
        (None | Some(Tone::Positive | Tone::Negative), true) => {
            let up_is_good = authored_tone != Some(Tone::Negative);
            match cell.as_f64() {
                Some(v) if v != 0.0 => Some(tone_color(if (v > 0.0) == up_is_good { Tone::Positive } else { Tone::Negative })),
                _ => None,
            }
        }
        (Some(tone), _) => Some(tone_color(tone)),
    };
    let ink = |color: &Engine<Color, Auto>| match color {
        Engine::Value(color) => Some(color.as_str().to_string()).filter(|c| !c.is_empty()),
        Engine::Rule(_) => None,
    };
    let (row_color, glyph_ink) = (ink(&kpi.support.font.color), ink(&kpi.support.glyph.font.color));
    // `good`'s tone colors the value and glyph unless a matched rule sets the tone itself; a matched
    // rule's `font.color` outranks both.
    let good_color = if patch.tone.is_some() { None } else { signed.tone_color };
    let rule_color = patch.color.clone().filter(|color| !color.is_empty()).or(good_color);
    let value_fill = rule_color.clone().or_else(|| row_color.clone()).or_else(|| tone.clone()).unwrap_or_else(|| secondary.clone());
    let glyph_fill = rule_color.or(glyph_ink).or(tone).or(row_color).unwrap_or_else(|| secondary.clone());
    Ok(Some(KpiSupport { glyph, value, explainer, value_fill, glyph_fill, explainer_fill: secondary }))
}

/// The predefined formats that write a signed change.
const DELTA_FORMATS: [&str; 4] = ["delta", "percent_delta", "percent_number_delta", "percentage_points_delta"];

/// `style.value.format` or `style.support.format` as the formats law takes it.
fn authored_format(spec: &FormatSpec) -> AuthoredFormat {
    match spec {
        FormatSpec::Name(name) => AuthoredFormat::named(name.as_str()),
        FormatSpec::Block(block) => {
            let set = |field: &Tri<String>| match field {
                Tri::Value(value) => Some(value.clone()),
                Tri::Unset | Tri::Null => None,
            };
            AuthoredFormat {
                spec: set(&block.spec).filter(|spec| !spec.is_empty()),
                prefix: set(&block.prefix),
                suffix: set(&block.suffix),
                notation: match block.notation {
                    Tri::Value(board_model::primitives::Notation::Analytic) => Some(Notation::Analytic),
                    Tri::Value(board_model::primitives::Notation::Narrative) => Some(Notation::Narrative),
                    _ => None,
                },
                sign_placement: match block.sign_placement {
                    Tri::Value(board_model::primitives::SignPlacement::AfterPrefix) => Some(SignPlacement::AfterPrefix),
                    Tri::Value(board_model::primitives::SignPlacement::BeforePrefix) => Some(SignPlacement::BeforePrefix),
                    _ => None,
                },
                repeat: match block.repeat {
                    Tri::Value(board_model::primitives::AffixRepeat::Anchor) => Some(Repeat::Anchor),
                    Tri::Value(board_model::primitives::AffixRepeat::Every) => Some(Repeat::Every),
                    _ => None,
                },
            }
        }
    }
}

pub(crate) fn declared_formats(query: &Query) -> &IndexMap<String, FormatName> {
    match query {
        Query::Sql(q) => &q.base.formats,
        Query::Values(q) => &q.base.formats,
        Query::Schema(q) => &q.base.formats,
    }
}

/// Every chart field, style and board fact that changes what the card says and is not modeled.
fn refuse_unmodeled(chart: &KpiChart, board: &Board) -> Result<(), Diagnostic> {
    let unmodeled = [
        (chart.style.background.is_some(), "a KPI `style.background`"),
        (chart.base.link.is_some(), "a KPI `link`"),
        (board.regional.is_some(), "a number locale other than en-US"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("{what} is not supported"))),
        None => Ok(()),
    }
}

/// The headline as a number: production reads any other cell (text, a moment, a null) another way.
fn number(cell: &Value, declared: &ColumnType) -> Result<f64, Diagnostic> {
    match (cell, declared) {
        (Value::Number(n), ColumnType::Integer | ColumnType::Float) => {
            n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))
        }
        _ => Err(Diagnostic::refused(refuse!("a KPI value that is not an integer or float number is not supported"))),
    }
}

/// The label as drawn; an empty one is no label. Template syntax needs the board's variables and
/// queries.
fn label(chart: &KpiChart) -> Result<Option<String>, Diagnostic> {
    match chart.label.as_ref() {
        Some(label) => plain_text(label.as_str(), "a KPI label with template syntax is not supported"),
        None => Ok(None),
    }
}

fn plain_text(text: &str, refusal: &'static str) -> Result<Option<String>, Diagnostic> {
    if text.is_empty() {
        return Ok(None);
    }
    if text.contains("{{") || text.contains("{%") {
        return Err(Diagnostic::refused(refuse!("{refusal}")));
    }
    Ok(Some(text.to_string()))
}
