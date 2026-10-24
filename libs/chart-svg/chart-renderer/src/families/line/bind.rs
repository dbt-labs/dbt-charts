//! Line and area bind: what the resolved chart and its rows decide before any pixel. The wide
//! fold, the labeled-bucket rewrite and the calendar gap fill give the rows layout reads, and each
//! temporal x is parsed once into its instant and the domain seated by it. Nothing here measures text.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use serde_json::Value;

use super::{validate_line_layers, LineResolved};
use crate::common::{is_year_shaped, row_str, row_str_ref};
use crate::diagnostic::Diagnostic;
use crate::families::layers::OverlayLayer;
use crate::families::multiples;
use crate::families::temporal::{
    days_from_civil, parse_iso_date, TimePart, TimeUnit, VegaType,
};
use crate::families::x_type::XResolution;
use crate::Payload;

/// A line or area chart bound to its rows.
pub struct Bound<'a> {
    /// The rows layout reads: the payload's own, or, after a wide fold, a labeled-bucket rewrite or
    /// the calendar gap fill, the payload over the rewritten rows.
    pub(super) payload: Cow<'a, Payload>,
    pub(super) r: LineResolved,
    pub(super) overlays: Vec<OverlayLayer>,
    /// An area chart draws its fills; a line chart its strokes alone.
    pub(super) draw_areas: bool,
    /// A wide chart's series, sorted and complete over the authored measures.
    pub(super) wide_series: Vec<String>,
    pub(super) wide_labels: HashMap<String, String>,
    pub(super) x: XAxis,
}

/// The x axis as the rows decide it.
pub(super) struct XAxis {
    pub(super) law: XResolution,
    /// A null x on a date axis is no bucket.
    pub(super) is_dates: bool,
    /// Nominal or ordinal after the x-type law.
    pub(super) is_band: bool,
    /// The labeled buckets and years the law rewrote to their ISO start.
    pub(super) rows_rewritten: bool,
    /// Every distinct x parsed once; layout adds the values a layer brings.
    pub(super) parsed: XParsed,
    /// The distinct x values: first-appearance order, but for a temporal axis, where each bucket
    /// seats by its instant whatever order the query returned.
    pub(super) domain: Vec<String>,
}

pub fn bind(payload: &Payload, draw_areas: bool) -> Result<Bound<'_>, Diagnostic> {
    let mut r = LineResolved::from_payload(payload)?;
    let faceted = r.multiples.is_some();

    let overlays = validate_line_layers(&r, payload)?;
    // A wide (`y: [a, b]`) chart unfolds into long rows once, here, before
    // any other law runs: the resolver already bakes `y` as
    // `WIDE_VALUE_FIELD` (`bake_wide_measures_kwargs`), so the only
    // substitution left is the rows themselves and the synthetic color
    // field — every downstream law (rail, legend, palette, domains) then
    // runs exactly as it would for an authored `color:` chart, since it's
    // already generic over `color_field`/`payload.rows`. `wide_series` is
    // the SORTED, authored-measure-COMPLETE color domain
    // (`wide_series_names`, computed off the pre-fold rows/dimension,
    // before either is overwritten below) — production threads this exact
    // list into the palette index, never a scan of the folded rows, so a
    // measure null on every row still owns its domain/palette slot
    // (`_wide.py::fold_wide_measures`). Seeds `spatial_domain` in layout.
    let wide = unfold_wide(&mut r, payload)?;
    let mut prepared: Cow<Payload> = Cow::Borrowed(payload);
    if let Some(folded) = wide.payload {
        prepared = Cow::Owned(folded);
    }
    r.use_numeric_color(&prepared.rows);
    // A facet composite keys its quarterly dates as they are.
    if !faceted {
        if let Some(rekeyed) = rekey_to_enclosing_buckets(&r, &prepared, draw_areas)? {
            prepared = Cow::Owned(rekeyed);
        }
    }
    if let Some(filled) = fill_calendar_buckets(&r, &prepared, draw_areas)? {
        prepared = Cow::Owned(filled);
    }
    let x = x_axis(&r, &prepared, draw_areas)?;
    Ok(Bound { payload: prepared, r, overlays, draw_areas, wide_series: wide.series, wide_labels: wide.labels, x })
}

/// The x law over the chart's rows, and each distinct x value parsed once.
fn x_axis(r: &LineResolved, payload: &Payload, draw_areas: bool) -> Result<XAxis, Diagnostic> {
    let x_field = &r.x;
    let domain_rows = &payload.rows;
    let curve_name = if draw_areas {
        r.style.area_mark.as_ref().and_then(|a| a.curve.as_deref())
    } else {
        r.style.line_mark.curve.as_deref()
    };
    let fam = if draw_areas { "area" } else { "line" };
    // The one x-type law (`x_type::resolve_cartesian_x_type`) decides the grain
    // and whether x is a category; the labeled-bucket and year rewrites run
    // ahead of it exactly as production's emitters apply them.
    let law_rows = crate::families::temporal::normalize_labeled_temporal(&payload.rows, x_field)?;
    let law = crate::families::x_type::resolve_cartesian_x_type(
        &law_rows,
        x_field,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        fam,
        // A multi-measure chart's axis is built without the curve.
        curve_name == Some("step") && r.wide_measures.is_empty(),
        &r.panel_axes.iter().map(|a| a.field.as_str()).collect::<Vec<_>>(),
    )?;
    // The law's rewrite of each labeled bucket or year to its ISO start; the raw label stays the join key.
    let rewritten: HashMap<String, String> = law_rows
        .iter()
        .zip(&payload.rows)
        .map(|(normalized, raw)| (row_str(raw, x_field), row_str(normalized, x_field)))
        .filter(|(raw, normalized)| raw != normalized)
        .collect();
    let rows_rewritten = law_rows.iter().zip(&payload.rows).any(|(normalized, raw)| normalized.get(x_field) != raw.get(x_field));
    // A null x on a date axis is no bucket: production's ordinal scaffold places rows
    // by their bucket and drops the rest, though the row still counts toward the y domain.
    let is_dates = is_year_shaped(domain_rows, x_field)
        || law.vl_type == VegaType::Temporal
        || matches!(crate::families::temporal::infer_vega_type(domain_rows, x_field), VegaType::Temporal | VegaType::Ordinal);
    let grain: Option<TimeUnit> = match law.grain {
        crate::families::x_type::Grain::Calendar(tu) => Some(tu),
        _ => None,
    };
    // A time-part axis (`monthofyear`, ...) collapses every date onto the part's own cycle.
    let part: Option<TimePart> = match law.grain {
        crate::families::x_type::Grain::Part(part) => Some(part),
        _ => None,
    };
    // A band x: nominal or ordinal after the x-type law, which a literal `step` flips a
    // calendar grain onto, an authored `axis_x.type: ordinal` forces and a fiscal start
    // flips a year or quarter grain to. `pin_sorted_domain` sorts exactly these; a
    // quantitative or temporal x ignores the sort.
    let is_band = matches!(law.vl_type, VegaType::Nominal | VegaType::Ordinal);
    let mut parsed = XParsed { grain, part, band: is_band, rewritten, parsed: HashMap::new() };
    let mut domain: Vec<String> = {
        let mut seen = HashSet::new();
        let mut domain = Vec::new();
        for row in domain_rows.iter().filter(|row| !is_dates || row.get(x_field).is_some_and(|v| !v.is_null())) {
            let v = row_str(row, x_field);
            if seen.insert(v.clone()) {
                domain.push(v);
            }
        }
        domain
    };
    // First value no date parses from; a time axis refuses it, any other reads its dates only when all parse.
    let unparsed: Option<String> = domain.iter().find(|key| parsed.add(key).is_none()).cloned();
    if law.vl_type == VegaType::Temporal {
        if let Some(value) = &unparsed {
            return Err(x_value_not_a_date(&r.id, x_field, value));
        }
        // A line or area mark draws its vertices along the x it plots, so a temporal axis
        // seats its buckets left to right whatever order the query returned them in.
        parsed.sort(&mut domain);
    }
    Ok(XAxis { law, is_dates, is_band, rows_rewritten, parsed, domain })
}

/// `ERR-X-VALUE-NOT-A-DATE`: a time axis's x value that parses as no date or timestamp.
pub(super) fn x_value_not_a_date(chart_id: &str, x_field: &str, value: &str) -> Diagnostic {
    Diagnostic::new(
        &crate::codes::ERR_X_VALUE_NOT_A_DATE,
        [
            ("chart_id", serde_json::json!(chart_id)),
            ("value", serde_json::json!(value)),
            ("x_field", serde_json::json!(x_field)),
        ],
    )
}

/// A temporal x value, parsed once. Every date computation reads these fields, never the text.
#[derive(Clone, Copy)]
pub(crate) struct XInstant {
    /// The UTC instant in days (offset applied, millisecond precision): the ordering tiebreak and the last x.
    pub at: f64,
    /// Where a vertex sits: the grain's floor of the text's own wall date, the part's cycle position, else `at`.
    pub pos: f64,
    /// Vega's `timeunit` yields NaN for a year below 100 over a calendar grain.
    pub nan: bool,
}

impl XInstant {
    fn parse(text: &str, grain: Option<TimeUnit>, part: Option<TimePart>, band: bool) -> Option<Self> {
        let at = crate::families::temporal::date_text_day(text)?;
        let (y, m, d) = parse_iso_date(text)?;
        let floor = grain.map(|tu| crate::families::temporal::floor_day(days_from_civil(y, m, d), tu));
        let pos = match (floor, part) {
            (Some(day), _) => day as f64,
            (None, Some(part)) => part.instant(at),
            (None, None) => at,
        };
        let nan = !band && floor.is_some_and(|day| crate::families::temporal::civil_from_days(day).0 < 100);
        Some(Self { at, pos, nan })
    }
}

/// Each distinct x text parsed once, with the ISO text the date math reads it as (a labeled bucket
/// or year rewritten to its start, a part axis at the instant its cycle maps it to).
#[derive(Clone)]
pub(super) struct XParsed {
    pub(super) grain: Option<TimeUnit>,
    pub(super) part: Option<TimePart>,
    pub(super) band: bool,
    pub(super) rewritten: HashMap<String, String>,
    pub(super) parsed: HashMap<String, (XInstant, String)>,
}

impl XParsed {
    /// Parses `key` unless it is held already; `None` when it is no date.
    pub(super) fn add(&mut self, key: &str) -> Option<()> {
        if self.parsed.contains_key(key) {
            return Some(());
        }
        let text = self.rewritten.get(key).map_or(key, String::as_str);
        let instant = XInstant::parse(text, self.grain, self.part, self.band)?;
        let iso = if self.part.is_some() { crate::families::temporal::instant_iso(instant.pos) } else { text.to_string() };
        self.parsed.insert(key.to_string(), (instant, iso));
        Some(())
    }

    /// Left to right: by vertex position, then by instant. Every key is parsed already.
    pub(super) fn sort(&self, domain: &mut [String]) {
        domain.sort_by(|a, b| {
            let (a, b) = (self.parsed[a].0, self.parsed[b].0);
            a.pos.total_cmp(&b.pos).then(a.at.total_cmp(&b.at))
        });
    }
}

/// The instants of an x domain, index-aligned with it.
#[derive(Clone)]
pub(crate) struct TemporalX {
    pub at: Vec<f64>,
    pub pos: Vec<f64>,
    pub nan: Vec<bool>,
    pub iso: Vec<String>,
}

impl TemporalX {
    /// `None` when a value of `domain` is no date.
    pub(super) fn of(domain: &[String], parsed: &XParsed) -> Option<Self> {
        let entries = domain.iter().map(|key| parsed.parsed.get(key)).collect::<Option<Vec<_>>>()?;
        Some(Self {
            at: entries.iter().map(|(i, _)| i.at).collect(),
            pos: entries.iter().map(|(i, _)| i.pos).collect(),
            nan: entries.iter().map(|(i, _)| i.nan).collect(),
            iso: entries.iter().map(|(_, iso)| iso.clone()).collect(),
        })
    }

    /// The scale's extent over the rows that have a position; `None` for under two or a zero span.
    pub(super) fn extent(&self) -> Option<(f64, f64)> {
        let finite: Vec<f64> = self.pos.iter().zip(&self.nan).filter(|&(_, &nan)| !nan).map(|(&pos, _)| pos).collect();
        crate::families::temporal::extent_of(&finite)
    }

    /// The domain with one more instant at its end (the end of the last bucket a bar occupies).
    pub(super) fn extended(&self, end_day: f64, end_iso: String) -> Self {
        let mut out = self.clone();
        out.at.push(end_day);
        out.pos.push(end_day);
        out.nan.push(false);
        out.iso.push(end_iso);
        out
    }
}

/// A quarterly date a fiscal start places inside another quarter's bucket is keyed by the
/// bucket that encloses it (`_enclosing_bucket`); the link still reads the date it carried.
/// `None` when no row moves.
fn rekey_to_enclosing_buckets(r: &LineResolved, payload: &Payload, draw_areas: bool) -> Result<Option<Payload>, String> {
    let curve = if draw_areas { r.style.area_mark.as_ref().and_then(|a| a.curve.as_deref()) } else { r.style.line_mark.curve.as_deref() };
    if payload.rows.is_empty() {
        return Ok(None);
    }
    let normalized = crate::families::temporal::normalize_labeled_temporal(&payload.rows, &r.x)?;
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let filled = crate::families::x_type::resolve_filled_x(
        &normalized,
        &r.x,
        &crate::families::x_type::XAxis::of(&r.style.axis_x),
        if draw_areas { "area" } else { "line" },
        curve == Some("step"),
        &panel_fields,
    )?;
    let (Some(tu @ TimeUnit::YearQuarter), crate::families::x_type::Grain::Calendar(_)) = (filled.filled, filled.resolution.grain) else {
        return Ok(None);
    };
    let fiscal = r.style.axis_x.fiscal_year_start_month;
    let mut changed = false;
    let rows: Vec<_> = payload
        .rows
        .iter()
        .cloned()
        .map(|mut row| {
            let start = row
                .get(&r.x)
                .and_then(Value::as_str)
                .and_then(|text| crate::families::temporal::off_anchor_bucket_start(text, tu, fiscal));
            if let Some(start) = start {
                if let Some(raw) = row.insert(r.x.clone(), Value::String(start)) {
                    row.insert(crate::common::RAW_X.to_string(), raw);
                }
                changed = true;
            }
            row
        })
        .collect();
    Ok(changed.then(|| payload.with_inputs(payload.resolved.clone(), rows)))
}

/// A calendar-bucketed x that resolves an ordinal axis (a `curve: step`'s band
/// axis, `step_band.is_band_step`, an authored `axis_x.type: ordinal`, a fiscal
/// start's year or quarter grain) is gap-filled by production before anything
/// else reads the rows: labeled buckets rewritten to their ISO start, then one
/// row per bucket and panel, in chronological order, the missing ones null in
/// every measure (`gap_fill_ordinal_time_per_panel`). `None` when no fill applies.
fn fill_calendar_buckets(r: &LineResolved, payload: &Payload, draw_areas: bool) -> Result<Option<Payload>, String> {
    let curve = if draw_areas { r.style.area_mark.as_ref().and_then(|a| a.curve.as_deref()) } else { r.style.line_mark.curve.as_deref() };
    if payload.rows.is_empty() {
        return Ok(None);
    }
    let axis = &r.style.axis_x;
    let normalized = crate::families::temporal::normalize_labeled_temporal(&payload.rows, &r.x)?;
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let mark = if draw_areas { "area" } else { "line" };
    let filled = crate::families::x_type::resolve_filled_x(
        &normalized,
        &r.x,
        &crate::families::x_type::XAxis::of(axis),
        mark,
        curve == Some("step"),
        &panel_fields,
    )?;
    let Some(tu) = filled.filled.filter(|_| matches!(filled.resolution.vl_type, VegaType::Nominal | VegaType::Ordinal)) else {
        return Ok(None);
    };
    let fill = axis.fill.as_deref().ok_or("x-axis fill must be resolved before ordinal time-series completion")?;
    // A color field that is itself a partition field is constant within a panel:
    // nothing is left to cross-join over (`gap_fill_ordinal_time_per_panel`).
    let dims: Vec<&str> = r.color.as_deref().into_iter().filter(|c| !panel_fields.contains(c)).collect();
    let mut rows = Vec::with_capacity(normalized.len());
    for panel in multiples::regroup(&r.panel_axes, &normalized)? {
        let mut completed =
            crate::families::temporal::complete_ordinal_rows(&panel.rows, &r.x, &dims, tu, fill, axis.fiscal_year_start_month)?;
        // `regroup` strips the partition columns; the pooled rows carry them back.
        for row in &mut completed {
            for (field, value) in panel_fields.iter().zip(&panel.key) {
                row.insert((*field).to_string(), value.clone());
            }
        }
        rows.extend(completed);
    }
    // A stable chronological re-sort of the pooled panels (disjoint ranges concatenate out of order).
    rows.sort_by(|a, b| row_str_ref(a, &r.x).cmp(&row_str_ref(b, &r.x)));
    let cloned = payload.with_inputs(payload.resolved.clone(), rows);
    Ok(Some(cloned))
}

/// A wide (`y: [a, b]`) chart's long form: the rows unfolded once, before any
/// other law runs, the synthetic color field set on `r`, and the sorted,
/// authored-measure-complete series domain with its humanized labels.
struct WideFold {
    payload: Option<Payload>,
    series: Vec<String>,
    labels: HashMap<String, String>,
}

fn unfold_wide(r: &mut LineResolved, payload: &Payload) -> Result<WideFold, String> {
    if r.wide_measures.is_empty() {
        return Ok(WideFold { payload: None, series: Vec::new(), labels: HashMap::new() });
    }
    // A wide chart always renders solid: production's dash encoding lives only
    // in `_apply_line_color_encoding`, which the folded arm never reaches
    // (`emitters/line.py:462`).
    let series = crate::common::wide_series_names(&r.wide_measures, r.color.as_deref(), &payload.rows);
    let cloned = payload.with_inputs(payload.resolved.clone(), crate::common::unfold_wide_rows(&payload.rows, &r.wide_measures, r.color.as_deref()));
    r.series_from_measures = r.color.is_none();
    r.prefold_rows = payload.rows.clone();
    r.link_color = Some(r.color.clone().unwrap_or_else(|| crate::common::WIDE_KEY_FIELD.to_string()));
    r.link_y = r.wide_measures[0].clone();
    r.color = Some(crate::common::WIDE_LABEL_FIELD.to_string());
    let labels = display_text::wide_measure_labels_for(&r.wide_measures, &r.field_labels)?;
    Ok(WideFold { payload: Some(cloned), series, labels })
}
