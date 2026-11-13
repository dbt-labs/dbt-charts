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
use board_model::chart::kpi::{KpiBackground, KpiSupportConfig};
use board_model::chart::{ChartLink, KpiChart};
use board_model::facet::{Color, FormatName};
use board_model::ids::{ChartId, QueryId};
use board_model::primitives::{FormatSpec, LinkOpen, LinkSpec, Tone};
use board_model::query::Query;
use board_model::rule::Auto;
use board_model::style::font::LetterCase;
use board_model::style::kpi::KpiChartStyle;
use board_model::style::style::Style;
use column_types::{CellTag, ColumnType, Dataset};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use model_core::{Engine, Patch, Tri};
use serde_json::{json, Value};

use crate::conditional::{self, card_patch, good_overrides, CardPatch, Signed};
use crate::formats::locale::active_number_locale;
use crate::formats::{column_format, Alias, AuthoredFormat, Formats, Kind, Notation, Tiers, TEXT_NAMES};
use crate::bound::{Family, Failure, Href, Kpi, KpiSupport};
use crate::run::RunResult;
use crate::table_dates;

/// Binds `chart` to its query's one row, with the total `style` of the placement it is bound at and
/// the chart's `own` style alone and the placing `scope`'s (the board tier of the headline's format,
/// which the chart's own layer is not folded into). A row count or a column production fails on is that chart's
/// error card, a query that returned no rows included (`bind` adds the zero-rows warning beside it).
pub fn bind_kpi(id: &ChartId, chart: &KpiChart, style: &Style, own: &Patch<Style>, scope: &Style, board: &Board, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let kpi = &style.charts.kpi;
    // The board's `style.formats`: the names a KPI's format may take beyond the engine's own.
    let aliases = crate::formats::aliases(&scope.formats);
    let formats = Formats::new(Some(&aliases));
    if matches!(kpi.background, Some(KpiBackground::Gradient(_))) {
        return Err(Diagnostic::refused(refuse!("a KPI `style.background` by column and scale is not supported")));
    }
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
    let declared_tier = |column: &str| declared.and_then(|formats| formats.get(column)).map(|name| AuthoredFormat::named(name.as_str()));
    let column_tier = declared_tier(column);
    let support_tier = support_column.and_then(declared_tier);
    let row = match dataset.rows.as_slice() {
        [] => return failure(diag::err_kpi_no_data(id.as_str())),
        [row] => row,
        rows => return failure(diag::err_kpi_multirow(id.as_str(), rows.len())),
    };
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
    let tiers = Tiers { own: value_own.as_ref(), board: value_authored.as_ref(), column: column_tier.as_ref() };
    // `_kpi_text_link`: a headline under an `email` / `url` format is text that links, and a number under
    // one is an error even when the chart links.
    let text_format = column_format(Kind::Number, tiers, formats, None, None, &[])?.spec;
    let text_format = TEXT_NAMES.contains(&text_format.as_str()).then_some(text_format);
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
            let native = [tiers.own, tiers.board].into_iter().flatten().any(|f| f.spec.is_some()) && unnotated.notation.is_none() && !unnotated.is_house_si();
            let lanes = match format.paint_lanes(&active_number_locale(), value, native, false, false) {
                Ok(lanes) => lanes,
                Err(error) => return data_error(error),
            };
            (lanes.prefix, lanes.number, lanes.suffix)
        }
    };
    let title = filled_text(id, chart.label.as_ref().map(|label| label.as_str()), query, board, run, &aliases)?;
    let patch = card_patch(&chart.style.conditional, row)?;
    let support = match &chart.support {
        Some(support) => match bind_support(id, support, &patch, style, row, dataset, (support_tier.as_ref(), formats, filled_text(id, support.label.as_ref().map(|label| label.as_str()), query, board, run, &aliases)?.unwrap_or_default())) {
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
    let (link, link_open) = match (link(chart, board, run)?, &text_format) {
        (Some((href, open)), _) => (Some(href), open),
        (None, Some(name)) => match text_format_link(name, cell, id) {
            Ok(Some(href)) => (Some(Href::new(&href)?), LinkOpen::Page),
            Ok(None) => (None, LinkOpen::Page),
            Err(error) => return data_error(error),
        },
        (None, None) => (None, LinkOpen::Page),
    };
    let label = title.as_ref().map(|text| cased(text, kpi.label.font.case)).transpose()?;
    Ok(Ok(Family::Kpi(Box::new(Kpi { glyph, arrow: drawn_by_good, ink: patch.value_color.clone().or(signed.tone_color), value_weight: patch.value_weight.clone(), label_color: patch.label_color.clone(), label_weight: patch.label_weight.clone(), prefix, number, suffix, label, title, support, fill: card_fill(kpi, scope)?, link, link_open }))))
}

/// `resolve_support_row` (`chart-renderer/src/families/kpi.rs`): the support value as one string
/// (affix gaps kept), its glyph, the explainer and the inks each paints in. `None` when all three
/// are empty.
fn bind_support(id: &ChartId, support: &KpiSupportConfig, patch: &CardPatch, style: &Style, row: &serde_json::Map<String, Value>, dataset: &Dataset, (column_tier, formats, explainer): (Option<&AuthoredFormat>, Formats<'_>, String)) -> Result<Option<KpiSupport>, Diagnostic> {
    let kpi = &style.charts.kpi;
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
    // `resolve_support_row`: the support's own format is used whole when set, else the query's format
    // for the column; the two never merge field by field.
    let column_tier = if format.is_some() { None } else { column_tier };
    let resolved = match (&format, column_tier) {
        (None, None) => None,
        (own, column) => Some(column_format(Kind::Number, Tiers { own: own.as_ref(), column, ..Tiers::default() }, formats, None, None, &numbers)?),
    };
    let value = match cell {
        Value::Null => String::new(),
        Value::String(text) => match table_dates::parse_temporal(text.trim(), declared) {
            Ok(None) => text.clone(),
            parsed => paint_date(id, parsed, text, Tiers { own: format.as_ref(), column: column_tier, ..Tiers::default() }, formats, false)?,
        },
        Value::Number(n) => {
            let number = n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?;
            let lanes = resolved.clone().unwrap_or_default().paint_lanes(&active_number_locale(), number, false, true, false)?;
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

/// `style.value.format` or `style.support.format` as the formats law takes it.
fn authored_format(spec: &FormatSpec) -> AuthoredFormat {
    match spec {
        FormatSpec::Name(name) => AuthoredFormat::named(name.as_str()),
        FormatSpec::Block(block) => crate::formats::authored_whole(block),
    }
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

/// The card's `link`: a board path or URL, resolved in the host's link context when it has one and
/// drawn as authored when it has none (a render with no public URL), and how it opens: the link's own
/// `open`, else the board's `links.open` (a page when the board sets none, the setting's default).
fn link(chart: &KpiChart, board: &Board, run: &RunResult) -> Result<Option<(Href, LinkOpen)>, Diagnostic> {
    let (url, own_open) = match &chart.base.link {
        None | Some(ChartLink::Off) => return Ok(None),
        Some(ChartLink::On(LinkSpec::Url(url))) => (url.as_str(), None),
        Some(ChartLink::On(LinkSpec::Block(block))) => {
            let Some(url) = block.url.as_deref() else {
                return Err(diag::err_internal("a KPI `link` block reaches bind with no `url`"));
            };
            (url, match block.open { Tri::Value(open) => Some(open), Tri::Unset | Tri::Null => None })
        }
    };
    // `split_link`: a query-only `?var=` link updates the board in place, so it opens as a page unless
    // its own block says otherwise.
    let open = match own_open {
        Some(open) => open,
        None if url.starts_with('?') => LinkOpen::Page,
        None => board.links.as_ref().map_or(LinkOpen::Page, |links| links.open),
    };
    let resolved = match &run.env.link_context {
        Some(context) => context.resolve(url),
        None => url.to_string(),
    };
    Ok(Some((Href::new(&resolved)?, open)))
}

/// `SlotFont::cased`: `text` under a slot's letter-case transform.
fn cased(text: &str, case: LetterCase) -> Result<String, Diagnostic> {
    display_text::case::apply_case(text, case.as_str()).map_err(diag::err_internal)
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
/// filled from the run's variables and the results of the queries it reads, which must be the board's own.
fn filled_text(id: &ChartId, text: Option<&str>, query: &QueryId, board: &Board, run: &RunResult, aliases: &IndexMap<String, Alias>) -> Result<Option<String>, Diagnostic> {
    let Some(text) = text else { return Ok(None) };
    if !(text.contains("{{") || text.contains("{%")) {
        return Ok(Some(text.to_string()).filter(|text| !text.is_empty()));
    }
    let info = template::parse(text, template::environment::Kind::Text).map_err(|errors| {
        let message = errors.first().map_or_else(String::new, |e| e.message.clone());
        Diagnostic::refused(refuse!("a KPI text that does not parse is not supported: {message}"))
    })?;
    if info.queries_nonliteral {
        return Err(Diagnostic::refused(refuse!("a KPI text that reads a query by a computed name is not supported")));
    }
    // `queries.<name>` reads the queries of the file that declares the chart. That is the chart's own
    // data query's file only when the board declares both locally (an imported query keeps its full
    // id as the board's key), so any other reading is the planner's `text_queries` and not modeled.
    let local = |name: &str| board.body.queries.contains_key(name);
    if !local(query.name().as_str()) || !info.queries.iter().all(|name| local(name)) {
        return Err(Diagnostic::refused(refuse!("a KPI text that reads a query of another file is not supported")));
    }
    let mut queries = IndexMap::new();
    for name in &info.queries {
        let qid = QueryId::try_from(format!("{}#{name}", query.file())).map_err(|e| diag::err_internal(format!("a KPI text reads query {name}: {e}")))?;
        let dataset = run.datasets.get(&qid).ok_or_else(|| diag::err_engine_needs_data(qid.to_string()).with_sink_param("chart_id", json!(id.as_str())))?;
        queries.insert(name.clone(), dataset.clone());
    }
    crate::display_text::render_text(text, &run.variables, &queries, aliases).map(|words| Some(words).filter(|words| !words.is_empty()))
}

