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

use crate::board::ChartStyles;
use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::kpi::{KpiBackground, KpiSupportConfig};
use board_model::chart::KpiChart;
use board_model::facet::{Color, FormatName};
use board_model::ids::{ChartId, QueryId};
use board_model::primitives::{FormatSpec, LinkOpen, ScalePalette, Tone};
use board_model::query::Query;
use board_model::rule::Auto;
use board_model::style::font::LetterCase;
use board_model::style::kpi::{KpiChartStyle, KpiChartStyleConditional};
use board_model::style::style::Style;
use column_types::{CellTag, ColumnType, Dataset};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use model_core::{first_set, Engine, Tri};
use serde_json::{json, Value};

use crate::color_scale;
use crate::column_refs;
use crate::conditional::{self, card_patch, good_overrides, CardPatch, Signed};
use crate::formats::locale::active_number_locale;
use crate::formats::{column_format, is_time_format, resolve_format, tier_fields, Alias, AuthoredFormat, Formats, Kind, Notation, Tiers, TEXT_NAMES};
use crate::bound::{Family, Failure, Href, Kpi, KpiSupport};
use crate::derived::Placed;
use crate::links;
use crate::run::RunResult;
use crate::table_dates;

/// Binds `chart` to its query's one row, with the total `style` of the placement it is bound at and
/// the chart's `own` style alone and the placing `scope`'s (the board tier of the headline's format,
/// which the chart's own layer is not folded into). A row count or a column production fails on is that chart's
/// error card, a query that returned no rows included (`bind` adds the zero-rows warning beside it).
pub fn bind_kpi(id: &ChartId, chart: &KpiChart, placed: &Placed, board: &Board, styles: &ChartStyles, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let (style, own, scope) = (placed.style, placed.own, placed.scope());
    let kpi = &style.charts.kpi;
    // The board's `style.formats`: the names a KPI's format may take beyond the engine's own.
    let aliases = crate::formats::aliases(&scope.formats);
    let formats = Formats::new(Some(&aliases));
    let Some(ChartQuery::Hoisted(query)) = &chart.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset: &Dataset = run.dataset(query, id)?;
    let failure = |diagnostic: Diagnostic| {
        Ok(Err(Failure { chart: id.clone(), diagnostic: diagnostic.with_sink_param("source_path", json!(format!("charts.{id}"))) }))
    };
    if let Err(mismatch) = dataset.schema.check_rows(&dataset.rows) {
        // The sink stamps the chart a data error belongs to (`stamp_chart_diagnostic`); the KPI
        // codes carry it in their own template.
        return failure(mismatch.into_diagnostic().with_sink_param("chart_id", json!(id.as_str())));
    }
    // No row, or one row whose value cell is NULL: the card has nothing to draw.
    let empty = match (dataset.rows.as_slice(), &chart.value) {
        ([], _) => true,
        // A NaN or infinity is a null on the wire with a tag, and prints as its text.
        ([row], Some(value)) => {
            row.get(value.as_str()).is_some_and(Value::is_null)
                && !crate::rows::tag_at(&dataset.cell_text, 0, value.as_str()).is_some_and(|tag| tag.non_finite.is_some())
        }
        _ => false,
    };
    if empty {
        let (painted, patch) = match dataset.rows.first() {
            Some(row) => match bound_channels(id, chart, kpi, &dataset.rows, query.name().as_str(), board.body.queries.get(query.name().as_str())) {
                Ok(channels) => (gradient_fill(kpi, channels.background.as_deref(), row, "")?, card_patch(&channels.conditional, row)?),
                Err(error) => return failure(error),
            },
            None => (Painted::default(), CardPatch::default()),
        };
        let title = filled_text(id, chart.label.as_ref().map(|label| label.as_str()), styles, run, &aliases)?;
        return Ok(Ok(empty_card(chart, placed, painted, patch, title, board, run)?));
    }
    let channels = match bound_channels(id, chart, kpi, &dataset.rows, query.name().as_str(), board.body.queries.get(query.name().as_str())) {
        Ok(channels) => channels,
        Err(error) => return failure(error),
    };
    let declared_tier = |column: &str| channels.formats.get(column).map(|name| AuthoredFormat::named(name.as_str()));
    let support_column = channels.support.as_deref();
    let support_tier = support_column.and_then(declared_tier);
    let row = match dataset.rows.as_slice() {
        [] => return Err(diag::err_internal(format!("KPI {id}'s empty result reaches the card's row"))),
        [row] => row,
        rows => return failure(diag::err_kpi_multirow(id.as_str(), rows.len())),
    };
    let column = channels.value.as_deref().ok_or_else(|| diag::err_internal(format!("KPI {id}'s row has no column to bind its value to")))?;
    let column_tier = declared_tier(column);
    let Some(cell) = row.get(column) else {
        return failure(diag::err_kpi_value_column_not_found(id.as_str(), column));
    };
    if let Some(missing) = support_column.filter(|column| !row.contains_key(*column)) {
        return failure(diag::err_kpi_value_column_not_found(id.as_str(), missing));
    }
    // A ratio-shaped value in a percent format, a support date under a number format, a number under a
    // text format or a url that is not absolute is that chart's error card.
    let data_error = |error: Diagnostic| {
        if matches!(error.code, "ERR-PERCENT-RANGE" | "ERR-KPI-FORMAT-KIND-MISMATCH" | "ERR-KPI-TEMPORAL-FORMAT-INVALID" | "ERR-TABLE-FORMAT-KIND-MISMATCH" | "ERR-URL-VALUE-NOT-ABSOLUTE") {
            failure(error.with_sink_param("chart_id", json!(id.as_str())))
        } else {
            Err(error)
        }
    };
    let value_authored = scope.charts.kpi.value.format.as_ref().map(authored_format);
    let value_own = match &own.charts.kpi.value.format {
        Tri::Value(format) => Some(authored_format(format)),
        Tri::Unset | Tri::Null => None,
    };
    let tiers = Tiers { own: value_own.as_ref(), board: value_authored.as_ref(), column: column_tier.as_ref(), inferred: None };
    // `_kpi_text_link`: a headline under an `email` / `url` format is text that links, and a number under
    // one is an error even when the chart links.
    let value_spec = column_format(Kind::Number, tiers, formats, None, None, &[])?.spec;
    let text_format = TEXT_NAMES.contains(&value_spec.as_str()).then(|| value_spec.clone());
    if let Some(name) = &text_format {
        let reads_as_number = match cell {
            Value::Number(_) => true,
            Value::String(text) => crate::display_text::python_float(text)?.is_some_and(f64::is_finite),
            _ => false,
        };
        if reads_as_number {
            return data_error(diag::err_table_format_kind_mismatch(name.as_str(), format!("`{name}` formats text, but this column holds numbers.")));
        }
    }
    let (prefix, number, suffix) = match headline(cell, dataset.schema.get(column).map_err(Diagnostic::from)?, crate::rows::tag_at(&dataset.cell_text, 0, column))? {
        // `kpi_value_text`: any other cell prints as plain text, whatever the format says.
        Headline::Text(text) => {
            // Production's `good` (`_good_rules`) reads a sign off a number and raises a bare `ValueError`
            // on a cell no number reads as, which its renderer reports as `ERR-INTERNAL` on this chart. A
            // null cell matches no rule and a numeric string coerces; neither is ported.
            if chart.good.is_some() {
                let reads_as_number = match cell {
                    Value::Null => true,
                    Value::String(text) => crate::display_text::python_float(text)?.is_some(),
                    _ => false,
                };
                if reads_as_number {
                    return Err(Diagnostic::refused(refuse!("a KPI `good` on a null or numeric-text value is not supported")));
                }
                let error = diag::err_internal(format!("ValueError: KPI '{id}': `good` needs a numeric `{column}` column to read a sign from, got {}.", diagnostic::py_repr(cell)));
                return Ok(Err(Failure { chart: id.clone(), diagnostic: error.with_sink_param("chart_id", json!(id.as_str())) }));
            }
            (String::new(), text, String::new())
        }
        // `kpi_value_text`: a date or timestamp prints through the time format its tiers name.
        Headline::Moment(text) => {
            if chart.good.is_some() {
                return Err(Diagnostic::refused(refuse!("a KPI `good` on a date value is not supported")));
            }
            let parsed = table_dates::parse_temporal(text.trim(), dataset.schema.get(column).map_err(Diagnostic::from)?);
            let painted = match paint_date(id, parsed, &text, tiers, formats, true) {
                Ok(painted) => painted,
                Err(error) => return data_error(error),
            };
            (String::new(), painted, String::new())
        }
        Headline::Number(value) => {
            let compact = &kpi.value.compact;
            let compacted = if value.abs() >= compact.threshold { compact.spec.as_str() } else { "" };
            let format = column_format(Kind::Number, tiers, formats, Some(compacted), Some(Notation::Narrative), &[value])?;
            // `kpi_format_native`: a literal d3 spec a layer wrote, with no notation of its own, prints d3's
            // raw magnitude letter.
            let unnotated = column_format(Kind::Number, tiers, formats, Some(compacted), None, &[value])?;
            // `kpi_format_native`'s `not from_default`: some kept tier resolves to a spec. An alias that sets only
            // an affix names a spec and resolves to none, and a time format on a number is no tier at all.
            let wrote_spec = tier_fields(Kind::Number, tiers, formats)?.iter().any(|tier| !tier.spec.is_empty());
            let native = wrote_spec && unnotated.notation.is_none() && !unnotated.is_house_si();
            let lanes = match format.paint_lanes(&active_number_locale(), value, native, false, false, true) {
                Ok(lanes) => lanes,
                Err(error) => return data_error(error),
            };
            (lanes.prefix, lanes.number, lanes.suffix)
        }
    };
    let title = filled_text(id, chart.label.as_ref().map(|label| label.as_str()), styles, run, &aliases)?;
    let painted = gradient_fill(kpi, channels.background.as_deref(), row, &value_spec)?;
    let patch = card_patch(&channels.conditional, row)?;
    let support = match &chart.support {
        Some(support) => match bind_support(id, support, &patch, style, row, dataset, (support_column, support_tier.as_ref(), formats, filled_text(id, support.label.as_ref().map(|label| label.as_str()), styles, run, &aliases)?.unwrap_or_default())) {
            Ok(support) => support,
            Err(error) => return data_error(error),
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
    // Each text under its slot's case transform (`slots.value.cased` and its siblings).
    let glyph = cased(&glyph, if drawn_by_good { kpi.value.glyph.arrow.font.case } else { kpi.value.glyph.slot.font.case })?;
    let (prefix, suffix) = (cased(&prefix, kpi.value.affix.font.case)?, cased(&suffix, kpi.value.affix.font.case)?);
    let number = cased(&number, kpi.value.font.case)?;
    let (link, link_open) = match (link(chart, placed.public_id, board, run)?, &text_format) {
        (Some((href, open)), _) => (Some(href), open),
        (None, Some(name)) => match text_format_link(name, cell, id) {
            Ok(Some(href)) => (Some(Href::new(&href)?), LinkOpen::Page),
            Ok(None) => (None, LinkOpen::Page),
            Err(error) => return data_error(error),
        },
        (None, None) => (None, LinkOpen::Page),
    };
    let label = title.as_ref().map(|text| cased(text, kpi.label.font.case)).transpose()?;
    Ok(Ok(Family::Kpi(Box::new(Kpi { glyph, arrow: drawn_by_good, ink: patch.value_color.clone().or(signed.tone_color), value_weight: patch.value_weight.clone(), label_color: patch.label_color.clone(), label_weight: patch.label_weight.clone(), prefix, number, suffix, label, title, support, fill: painted.fill.or(card_fill(kpi, scope)?), link, link_open, hinge_mismatch: painted.hinge_mismatch }))))
}

/// `resolve_support_row` (`chart-renderer/src/families/kpi.rs`): the support value as one string
/// (affix gaps kept), its glyph, the explainer and the inks each paints in. `None` when all three
/// are empty.
fn bind_support(id: &ChartId, support: &KpiSupportConfig, patch: &CardPatch, style: &Style, row: &serde_json::Map<String, Value>, dataset: &Dataset, (column, column_tier, formats, explainer): (Option<&str>, Option<&AuthoredFormat>, Formats<'_>, String)) -> Result<Option<KpiSupport>, Diagnostic> {
    let kpi = &style.charts.kpi;
    let secondary = style
        .fonts
        .get("secondary")
        .and_then(|font| font.color.as_ref())
        .map(|color| color.as_str().to_string())
        .ok_or_else(|| diag::err_internal("style.fonts.secondary.color is unset after the theme cascade".to_string()))?;
    let (cell, declared) = match column {
        Some(column) => {
            let cell = row.get(column).ok_or_else(|| diag::err_internal(format!("the support column {column} is not in the row")))?;
            (cell, dataset.schema.get(column).map_err(Diagnostic::from)?)
        }
        None => (&Value::Null, &ColumnType::String),
    };
    let format = kpi.support.format.as_ref().map(authored_format);
    let numbers: Vec<f64> = cell.as_f64().into_iter().collect();
    // `resolve_support_row`: the support's own format is used whole when set, else the query's format
    // for the column; the two never merge field by field.
    let column_tier = if format.is_some() { None } else { column_tier };
    let resolved = match (&format, column_tier) {
        (None, None) => None,
        (own, column) => {
            // The tier production reads (`resolve_support_row`): the own format, else the query's for the column,
            // a time format on a number being none. A tier that sets no spec (an affix alone) prints exact digits
            // behind its affix, as no format does: the support row never takes the board's `number` default.
            let tiers = Tiers { own: own.as_ref(), column, ..Tiers::default() };
            match tier_fields(Kind::Number, tiers, formats)?.into_iter().next() {
                None => None,
                Some(fields) if fields.spec.is_empty() => Some(fields),
                Some(_) => Some(column_format(Kind::Number, tiers, formats, None, None, &numbers)?),
            }
        }
    };
    let value = match cell {
        Value::Null => String::new(),
        Value::String(text) => match table_dates::parse_temporal(text.trim(), declared) {
            Ok(None) => text.clone(),
            parsed => paint_date(id, parsed, text, Tiers { own: format.as_ref(), column: column_tier, ..Tiers::default() }, formats, false)?,
        },
        Value::Number(n) => {
            let number = n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?;
            let lanes = resolved.clone().unwrap_or_default().paint_lanes(&active_number_locale(), number, false, true, false, true)?;
            format!("{}{}{}", lanes.prefix, lanes.number, lanes.suffix)
        }
        // `str(value)` of a boolean; any other cell shape is not a column kind a KPI reads.
        Value::Bool(flag) => if *flag { "True" } else { "False" }.to_string(),
        _ => return Err(Diagnostic::refused(refuse!("a KPI support value that is not a number, text, date or boolean is not supported"))),
    };
    // `good` sees the raw cell (a null cell is none).
    let cell_or_null = Some(cell).filter(|cell| !cell.is_null());
    let signed = match (support.good, support.value.is_some()) {
        (Some(good), true) => good_overrides(good, &kpi.support.glyph.glyphs, &style.tones, cell_or_null)?,
        _ => Signed::default(),
    };
    let glyph = signed.glyph.clone().or_else(|| support.glyph.clone()).unwrap_or_default();
    if value.is_empty() && explainer.is_empty() && glyph.is_empty() {
        return Ok(None);
    }
    // Each text under its slot's case transform (`slots.support.cased`).
    let (value, explainer) = (cased(&value, kpi.support.font.case)?, cased(&explainer, kpi.support.font.case)?);
    let glyph = cased(&glyph, kpi.support.glyph.font.case)?;
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

/// A KPI's column references in its result's spelling (`bind_channels`): `value` (bound by position
/// when the chart names none), `support.value`, a `{column, scale}` background's column, the
/// `style.conditional` columns and its query's `formats:` keys. `value` is `None` only for a result
/// with no rows.
struct Channels {
    value: Option<String>,
    support: Option<String>,
    background: Option<String>,
    conditional: IndexMap<String, Vec<KpiChartStyleConditional>>,
    formats: IndexMap<String, FormatName>,
}

/// `positional.py`'s `POSITIONAL_CHANNELS["kpi"]`: one column binds `value`.
const KPI_SHAPES: &[(usize, &[&str])] = &[(1, &["value"])];

fn bound_channels(id: &ChartId, chart: &KpiChart, kpi: &KpiChartStyle, rows: &[serde_json::Map<String, Value>], name: &str, query: Option<&Query>) -> Result<Channels, Diagnostic> {
    let columns: Vec<&str> = rows.first().map(|row| row.keys().map(String::as_str).collect()).unwrap_or_default();
    let at = |path: &str| format!("chart {} {path}", diagnostic::py_str_repr(id.as_str()));
    let value = match &chart.value {
        Some(value) => Some(column_refs::renamed(value.as_str(), &columns, &at("value"))?),
        None => column_refs::by_position(id.as_str(), "kpi", KPI_SHAPES, &columns)?.and_then(|bound| bound.first().map(|(_, column)| column.to_string())),
    };
    let support = match chart.support.as_ref().and_then(|support| support.value.as_ref()) {
        Some(column) => Some(column_refs::renamed(column.as_str(), &columns, &at("support.value"))?),
        None => None,
    };
    let background = match &kpi.background {
        Some(KpiBackground::Gradient(gradient)) => match gradient.column.as_deref() {
            Some(column) => Some(column_refs::renamed(column, &columns, &at("style.background.column"))?),
            None => None,
        },
        _ => None,
    };
    let mut conditional = IndexMap::new();
    let mut authored: IndexMap<String, &str> = IndexMap::new();
    for (key, rules) in &chart.style.conditional {
        let column = column_refs::renamed(key, &columns, &at(&format!("style.conditional.{key}")))?;
        if let Some(first) = authored.get(&column) {
            return Err(diag::err_column_ref_duplicate(at("style.conditional"), *first, key.as_str(), column.as_str()));
        }
        authored.insert(column.clone(), key);
        conditional.insert(column, rules.clone());
    }
    let formats = match query {
        Some(query) => declared_column_formats(name, query, &columns)?.into_iter().map(|(column, format)| (column.to_string(), format.clone())).collect(),
        None => IndexMap::new(),
    };
    Ok(Channels { value, support, background, conditional, formats })
}

/// `style.value.format` or `style.support.format` as the formats law takes it.
pub(crate) fn authored_format(spec: &FormatSpec) -> AuthoredFormat {
    match spec {
        FormatSpec::Name(name) => AuthoredFormat::named(name.as_str()),
        FormatSpec::Block(block) => crate::formats::authored_whole(block),
    }
}

/// The query's `formats:` per result column (`declared_format`): a key names a column exactly or up to
/// case folding.
pub(crate) fn declared_column_formats<'c, 'q>(query_name: &str, query: &'q Query, columns: &[&'c str]) -> Result<IndexMap<&'c str, &'q FormatName>, Diagnostic> {
    let at = format!("query {} formats", diagnostic::py_str_repr(query_name));
    let mut declared = IndexMap::new();
    for (key, format) in declared_formats(query) {
        if let Some(column) = column_refs::match_column(key, columns, &at)? {
            declared.insert(column, format);
        }
    }
    Ok(declared)
}

/// `declared_column_format(query.formats, column, formats, time=time)`: the format the query's
/// `formats:` declares for `column` when it is of the asked kind. A text name is no number or time
/// format.
pub(crate) fn declared_column_format(board: &Board, query: &QueryId, column: &str, formats: Formats, time: bool) -> Result<Option<AuthoredFormat>, Diagnostic> {
    let name = query.name();
    let Some(declared) = board.body.queries.get(name.as_str()) else {
        return Ok(None);
    };
    let Some(format) = declared_column_formats(name.as_str(), declared, &[column])?.get(column).map(|format| AuthoredFormat::named(format.as_str())) else {
        return Ok(None);
    };
    let resolved = resolve_format(&format, formats)?;
    Ok((!TEXT_NAMES.contains(&resolved.as_str()) && is_time_format(&resolved) == time).then_some(format))
}

pub(crate) fn declared_formats(query: &Query) -> &IndexMap<String, FormatName> {
    match query {
        Query::Sql(q) => &q.base.formats,
        Query::Values(q) => &q.base.formats,
        Query::Schema(q) => &q.base.formats,
    }
}

/// `text_format_link`: the href an `email` / `url` headline implies, `None` for an empty or null cell.
fn text_format_link(format: &str, cell: &Value, id: &ChartId) -> Result<Option<String>, Diagnostic> {
    let text = match cell {
        Value::Null => return Ok(None),
        Value::String(text) => text.trim(),
        Value::Bool(flag) => if *flag { "True" } else { "False" },
        _ => return Err(Diagnostic::refused(refuse!("a KPI text format over a cell that is not text is not supported"))),
    };
    if text.is_empty() {
        return Ok(None);
    }
    if format == "email" {
        let kept = |byte: u8| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-' | b'~' | b'@');
        let quoted: String = text.bytes().map(|byte| if kept(byte) { (byte as char).to_string() } else { format!("%{byte:02X}") }).collect();
        return Ok(Some(format!("mailto:{quoted}")));
    }
    let flat: String = text.chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let scheme_end = flat.find(':').filter(|end| *end > 0 && flat[..*end].starts_with(|c: char| c.is_ascii_alphabetic()) && flat[..*end].chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.')));
    let absolute = scheme_end.is_some_and(|end| {
        let (scheme, rest) = (flat[..end].to_ascii_lowercase(), &flat[end + 1..]);
        let netloc = rest.strip_prefix("//").map(|after| &after[..after.find(['/', '?', '#']).unwrap_or(after.len())]);
        matches!(scheme.as_str(), "http" | "https") && netloc.is_some_and(|netloc| !netloc.is_empty())
    });
    if !absolute {
        return Err(diag::err_url_value_not_absolute(format!("KPI '{id}'"), text));
    }
    Ok(Some(text.to_string()))
}

/// The card's `link`, its href the template as authored.
fn link(chart: &KpiChart, public_id: &str, board: &Board, run: &RunResult) -> Result<Option<(Href, LinkOpen)>, Diagnostic> {
    let Some((template, open)) = links::chart_link(chart.base.link.as_ref(), links::automatic(run, public_id), board)? else { return Ok(None) };
    Ok(Some((links::resolve(template, run)?, open)))
}

/// `SlotFont::cased`: `text` under a slot's letter-case transform.
fn cased(text: &str, case: LetterCase) -> Result<String, Diagnostic> {
    display_text::case::apply_case(text, case.as_str()).map_err(diag::err_internal)
}

/// `bind` with no row, or a NULL value cell (`chart-renderer/src/families/kpi.rs`): the value slot draws a muted
/// dash and the support row the one "No results" line, with the filters that reach the chart. The glyph, the
/// authored support row and the value rules stand down: none has a cell to read; the one row's conditional weights and label color still apply. A gradient still paints its `null_color` over a NULL cell.
fn empty_card(chart: &KpiChart, placed: &Placed, painted: Painted, patch: CardPatch, title: Option<String>, board: &Board, run: &RunResult) -> Result<Family, Diagnostic> {
    let (style, scope) = (placed.style, placed.scope());
    let kpi = &style.charts.kpi;
    let secondary = style
        .fonts
        .get("secondary")
        .and_then(|font| font.color.as_ref())
        .map(|color| color.as_str().to_string())
        .ok_or_else(|| diag::err_internal("style.fonts.secondary.color is unset after the theme cascade".to_string()))?;
    // The dash and the line beneath it share the support text's own ink.
    let ink = match &kpi.support.font.color {
        Engine::Value(color) if !color.as_str().is_empty() => color.as_str().to_string(),
        _ => secondary.clone(),
    };
    let link = link(chart, placed.public_id, board, run)?;
    let support = KpiSupport {
        glyph: String::new(),
        value: cased(&no_results_line(placed.public_id, run)?, kpi.support.font.case)?,
        explainer: String::new(),
        value_fill: ink.clone(),
        glyph_fill: ink.clone(),
        explainer_fill: secondary,
    };
    Ok(Family::Kpi(Box::new(Kpi {
        glyph: String::new(),
        arrow: false,
        ink: Some(ink),
        value_weight: patch.value_weight,
        label_color: patch.label_color,
        label_weight: patch.label_weight,
        prefix: String::new(),
        number: cased(EMPTY_VALUE, kpi.value.font.case)?,
        suffix: String::new(),
        label: title.as_ref().map(|text| cased(text, kpi.label.font.case)).transpose()?,
        title,
        support: Some(support),
        fill: painted.fill.or(card_fill(kpi, scope)?),
        link: link.as_ref().map(|(href, _)| href.clone()),
        link_open: link.map_or(LinkOpen::Page, |(_, open)| open),
        hinge_mismatch: painted.hinge_mismatch,
    })))
}

/// `no_results::kpi_line`: the one wording of an empty result, then the filters that reach this chart.
fn no_results_line(public_id: &str, run: &RunResult) -> Result<String, Diagnostic> {
    let headline = display_text::language::tr("chart.no_results", None, &[]).map_err(diag::err_internal)?;
    Ok(match run.empty_filters_line(public_id) {
        Some(filters) => format!("{headline} \u{b7} {filters}"),
        None => headline,
    })
}

/// What the value slot shows when there is no value to draw.
const EMPTY_VALUE: &str = "\u{2014}";

/// `scale_color` (`chart-renderer/src/families/kpi.rs`): the color a `{column, scale}` background gives
/// this card. The domain is the one row's own value unless the scale bounds it, so an unbounded scale
/// reads the middle of its palette. A cell that is not a finite number takes `null_color`, else the card
/// keeps its own fill.
/// What a `{column, scale}` background gives the card: its color and, when the scale's hinge does not
/// fit its palette or data, what to warn.
#[derive(Default)]
struct Painted {
    fill: Option<String>,
    hinge_mismatch: Option<(String, String)>,
}

fn gradient_fill(kpi: &KpiChartStyle, column: Option<&str>, row: &serde_json::Map<String, Value>, value_spec: &str) -> Result<Painted, Diagnostic> {
    let Some(KpiBackground::Gradient(gradient)) = &kpi.background else { return Ok(Painted::default()) };
    let scale = &gradient.scale.rest;
    let column = column.ok_or_else(|| diag::err_internal("a KPI background gradient reaches bind with no column"))?;
    let palette = gradient.scale.palette.as_ref().ok_or_else(|| diag::err_internal("a KPI background scale reaches bind with no palette"))?;
    let (min, max) = (first_set([scale.min.as_ref()]).map(|limit| limit.get()), first_set([scale.max.as_ref()]).map(|limit| limit.get()));
    let hinge = first_set([scale.hinge.as_ref()]).map(|hinge| match hinge {
        Engine::Value(pivot) => json!(pivot),
        Engine::Rule(Auto::Auto) => json!("auto"),
    });
    let number = row.get(column).and_then(Value::as_f64).filter(|n| n.is_finite());
    let hinge_mismatch = hinge_mismatch(column, palette, hinge.as_ref(), (min, max), number)?;
    let color = match number {
        None => first_set([scale.null_color.as_ref()]).map(|color: &Color| color.as_str().to_string()).filter(|color| !color.is_empty()),
        Some(number) => {
            let raw = color_scale::scale_domain([number], min, max);
            let (lo, hi) = color_scale::nice_domain(min, max, first_set([scale.nice.as_ref()]).copied().unwrap_or(false), raw);
            let format = Some(value_spec).filter(|spec| !spec.is_empty()).map(|spec| d3rs::format::predefined_spec(spec).map_or(spec, |predefined| predefined.d3));
            let hinge = color_scale::resolve_hinge(hinge.as_ref(), raw.0, raw.1, format);
            let stops = scale_stops(palette)?;
            Some(color_scale::interpolate_scale_color(number, lo, hi, &stops, hinge, scale.arm_mode.map_or("asymmetric", |mode| mode.as_str())).map_err(Diagnostic::refused)?)
        }
    };
    let fill = match color {
        Some(color) if !color::is_sanitizable_color(&color) => return Err(Diagnostic::refused(refuse!("a KPI `style.background` that is not a hex color, transparent or none is not supported"))),
        Some(color) if color.starts_with('#') => Some(color),
        Some(_) => Some("transparent".to_string()),
        None => None,
    };
    Ok(Painted { fill, hinge_mismatch })
}

/// `gradient_hinge_mismatch._problem` over a KPI's background channel: a diverging palette with no hinge, a
/// sequential one with a hinge, or `hinge: auto` over a domain with no neutral value to pivot on. The channel
/// is read with no number format, so a percent column's pivot at 1 does not count here.
fn hinge_mismatch(column: &str, palette: &ScalePalette, hinge: Option<&Value>, (min, max): (Option<f64>, Option<f64>), number: Option<f64>) -> Result<Option<(String, String)>, Diagnostic> {
    let family = match palette {
        ScalePalette::Name(name) if d3rs::vega_schemes::scheme(name.as_str()).is_none() => board_model::palette::palette_family(name.as_str()).ok(),
        _ => None,
    };
    let problem = match (family, hinge) {
        (Some("diverging"), None) => Some(format!("uses the diverging palette '{}' with no hinge, so its neutral color falls at the middle of the data, not at a chosen value", palette_name(palette))),
        (Some("sequential"), Some(_)) => Some(format!("sets a hinge on the sequential palette '{}', which has no neutral center to pivot on", palette_name(palette))),
        (_, Some(auto)) if auto.as_str() == Some("auto") => match (min.or(number), max.or(number)) {
            (Some(lo), Some(hi)) if lo != hi && !(lo < 0.0 && 0.0 <= hi) => Some(format!(
                "sets hinge: auto but its domain {} to {} does not cross zero, so the pivot falls back to the midpoint {}",
                d3rs::py_float::format_g(lo),
                d3rs::py_float::format_g(hi),
                d3rs::py_float::format_g((lo + hi) / 2.0)
            )),
            _ => None,
        },
        _ => None,
    };
    Ok(problem.map(|problem| (column.to_string(), problem)))
}

fn palette_name(palette: &ScalePalette) -> &str {
    match palette {
        ScalePalette::Name(name) => name.as_str(),
        _ => "",
    }
}

/// `resolve_palette_stops` over what `bake_scale_target_stops` leaves: a color list passes through, a shipped
/// palette name resolves to its stops, and a Vega scheme name bakes none, which production's render raises on.
fn scale_stops(palette: &ScalePalette) -> Result<Vec<String>, Diagnostic> {
    match palette {
        ScalePalette::Colors(colors) if colors.is_empty() => Err(Diagnostic::refused("table: scale palette must not be empty")),
        ScalePalette::Colors(colors) => Ok(colors.iter().map(|color| color.as_str().to_string()).collect()),
        ScalePalette::Stops(_) => Err(Diagnostic::refused(refuse!("a KPI scale palette of relative stops is not supported"))),
        ScalePalette::Name(name) if d3rs::vega_schemes::scheme(name.as_str()).is_some() => Err(Diagnostic::refused("table: scale palette without resolved stops not supported")),
        ScalePalette::Name(name) => board_model::palette::palette_list(name.as_str()).map_err(|error| {
            if error.is_unknown() {
                Diagnostic::refused(refuse!("a KPI scale palette that is not a shipped palette is not supported"))
            } else {
                diag::err_internal(error.to_string())
            }
        }),
    }
}

/// `explicit_background` (chart-renderer `kpi.rs`): the chart's own background color, which counts
/// only when it differs from the board's, since the board's own propagates to the card unchanged.
/// A color is sanitized as production does: hex stands, `transparent` and `none` are `transparent`,
/// and any other is refused where production errors.
fn card_fill(kpi: &KpiChartStyle, board: &Style) -> Result<Option<String>, Diagnostic> {
    let Some(KpiBackground::Color(color)) = &kpi.background else { return Ok(None) };
    let color = color.as_str();
    if color == board.background.as_str() {
        return Ok(None);
    }
    if !color::is_sanitizable_color(color) {
        return Err(Diagnostic::refused(refuse!("a KPI `style.background` that is not a hex color, transparent or none is not supported")));
    }
    Ok(Some(if color.starts_with('#') { color.to_string() } else { "transparent".to_string() }))
}

/// The headline as the card draws it: a number prints through the format, and any other cell prints
/// as plain text, a null as nothing and a string as it is, even one that reads as a number (the
/// card's own paint reads only a numeric cell as a number). A moment prints through a date format.
enum Headline {
    Number(f64),
    Text(String),
    /// A date or timestamp, as the wire text a date format paints.
    Moment(String),
}

fn headline(cell: &Value, declared: &ColumnType, tag: Option<&CellTag>) -> Result<Headline, Diagnostic> {
    match (cell, declared) {
        // A non-finite float or Decimal reaches the card as `null`, and prints as the text the wire tagged.
        (Value::Null, ColumnType::Float | ColumnType::Decimal) if tag.is_some_and(|tag| tag.non_finite.is_some()) => {
            Ok(Headline::Text(tag.map_or_else(String::new, |tag| tag.text.clone())))
        }
        (Value::Number(n), ColumnType::Integer | ColumnType::Float | ColumnType::Decimal) => {
            n.as_f64().map(Headline::Number).ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))
        }
        (Value::Null, ColumnType::String | ColumnType::Integer | ColumnType::Float | ColumnType::Decimal | ColumnType::Boolean | ColumnType::Date | ColumnType::Datetime | ColumnType::DatetimeTz) => Ok(Headline::Text(String::new())),
        (Value::String(text), ColumnType::String) => {
            // A string shaped like a date is a moment, which production formats as one.
            if !matches!(crate::table_dates::parse_temporal(text.trim(), declared), Ok(None)) {
                return Ok(Headline::Moment(text.clone()));
            }
            Ok(Headline::Text(text.clone()))
        }
        (Value::String(text), ColumnType::Date | ColumnType::Datetime | ColumnType::DatetimeTz) => Ok(Headline::Moment(text.clone())),
        (Value::Bool(flag), ColumnType::Boolean) => Ok(Headline::Text(if *flag { "True" } else { "False" }.to_string())),
        _ => Err(Diagnostic::refused(refuse!("a KPI value that is not a number, text or boolean is not supported"))),
    }
}

/// A date painted through the time format its tiers name, `date_short` by default
/// (`resolve_support_row`, `kpi_value_text`). A number format over a date, an invalid spec or a directive the painter does not
/// reach is not modeled. A number format over a date is production's `ERR-KPI-FORMAT-KIND-MISMATCH` on the support
/// value; on the headline, whose format may be a board-wide default shared by every KPI, it falls back to `date_short`.
fn paint_date(id: &ChartId, parsed: Result<Option<table_dates::Moment>, table_dates::TemporalError>, text: &str, tiers: Tiers, formats: Formats<'_>, headline: bool) -> Result<String, Diagnostic> {
    let mut dated = column_format(Kind::Time, tiers, formats, None, None, &[])?;
    if !table_dates::is_time_format(&dated.spec) {
        if !headline {
            let spec = d3rs::format::predefined_spec(&dated.spec).map_or(dated.spec.as_str(), |predefined| predefined.d3);
            return Err(diag::err_kpi_format_kind_mismatch(id.as_str(), spec));
        }
        dated = column_format(Kind::Time, Tiers::default(), formats, None, None, &[])?;
    }
    match table_dates::paint_cell(parsed, &dated.spec, &dated.prefix, &dated.suffix) {
        Ok(Some(painted)) => Ok(painted),
        Ok(None) => Err(diag::err_internal(format!("the date {text:?} holds no date"))),
        Err(table_dates::TemporalError::Invalid(reason)) => Err(diag::err_kpi_temporal_format_invalid(id.as_str(), text, dated.spec.as_str(), reason)),
        Err(table_dates::TemporalError::Unported(why)) => Err(Diagnostic::refused(refuse!("a KPI timestamp or strftime directive the painter does not reach is not supported: {why}"))),
    }
}

/// A KPI text as drawn (its label or its support label); an empty one is no text. Template syntax is
/// filled from the run's variables and the results of the queries it reads (`text_datasets`).
fn filled_text(id: &ChartId, text: Option<&str>, styles: &ChartStyles, run: &RunResult, aliases: &IndexMap<String, Alias>) -> Result<Option<String>, Diagnostic> {
    let Some(text) = text else { return Ok(None) };
    if !(text.contains("{{") || text.contains("{%")) {
        return Ok(Some(text.to_string()).filter(|text| !text.is_empty()));
    }
    let queries = crate::display_text::text_datasets(text, Some(id), styles, run)?;
    crate::display_text::render_text(text, &run.variables, &queries, aliases).map(|words| Some(words).filter(|words| !words.is_empty()))
}
