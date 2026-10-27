//! Line and area bind: what the resolved chart and its rows decide before any pixel. The wide
//! fold, the labeled-bucket rewrite and the calendar gap fill give the rows layout reads, and each
//! temporal x is parsed once into its instant and the domain seated by it. Nothing here measures text.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use serde_json::Value;

use super::{validate_line_layers, LineResolved};
use crate::common::{is_year_shaped, row_str};
use crate::diagnostic::Diagnostic;
use crate::families::layers::OverlayLayer;
use crate::families::multiples;
use crate::families::temporal::{TimePart, TimeUnit, VegaType};
use crate::families::x_type::XResolution;
use crate::time_axis::{Grain, TimeAxis};
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
    /// Every distinct x of the rows and of the layers' own columns, read once.
    pub(super) time: TimeAxis,
    /// The first x value no date parses from, on an axis that is not a time scale (a time scale refuses it).
    pub(super) unparsed: Option<String>,
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
    let x = x_axis(&r, &prepared, &overlays, draw_areas)?;
    Ok(Bound { payload: prepared, r, overlays, draw_areas, wide_series: wide.series, wide_labels: wide.labels, x })
}

/// The x law over the chart's rows, and each distinct x value parsed once.
fn x_axis(r: &LineResolved, payload: &Payload, overlays: &[OverlayLayer], draw_areas: bool) -> Result<XAxis, Diagnostic> {
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
    let unit: Option<TimeUnit> = match law.grain {
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
    let mut time = TimeAxis::new(Grain { unit, part, band: is_band }, rewritten);
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
    // A layer's own x column joins the shared scale, at its place in time whichever order the layer lists it.
    let mut layer_keys: Vec<(String, &str)> = Vec::new();
    for overlay in overlays {
        let layer_x = overlay.x().unwrap_or(x_field.as_str());
        let layer_rows = crate::families::layers::resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), fam)?;
        let normalized = crate::families::temporal::normalize_labeled_temporal(layer_rows, layer_x)?;
        time.rewrite(
            layer_rows
                .iter()
                .zip(normalized.iter())
                .map(|(raw, normalized)| (row_str(raw, layer_x), row_str(normalized, layer_x)))
                .filter(|(raw, normalized)| raw != normalized),
        );
        layer_keys.extend(layer_rows.iter().filter(|row| row.get(layer_x).is_some_and(|v| !v.is_null())).map(|row| (row_str(row, layer_x), layer_x)));
    }
    // First value no date parses from; a time axis refuses it, any other reads its dates only when all parse.
    let mut unparsed: Option<String> = None;
    for (key, field) in domain.iter().map(|key| (key, x_field.as_str())).chain(layer_keys.iter().map(|(key, field)| (key, *field))) {
        match time.add(key) {
            Ok(()) => {}
            Err(_) if law.vl_type != VegaType::Temporal => {
                unparsed.get_or_insert_with(|| key.clone());
            }
            Err(unreadable) => return Err(unreadable.diagnostic(&r.id, field, fam)),
        }
    }
    if law.vl_type == VegaType::Temporal {
        // A line or area mark draws its vertices along the x it plots, so a temporal axis
        // seats its buckets left to right whatever order the query returned them in.
        time.sort(&mut domain);
    }
    Ok(XAxis { law, is_dates, is_band, rows_rewritten, time, unparsed, domain })
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
fn fill_calendar_buckets(r: &LineResolved, payload: &Payload, draw_areas: bool) -> Result<Option<Payload>, Diagnostic> {
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
    let mut instants = TimeAxis::new(Grain::default(), HashMap::new());
    for row in &rows {
        instants.add(&row_str(row, &r.x)).map_err(|e| e.diagnostic(&r.id, &r.x, mark))?;
    }
    rows.sort_by(|a, b| instants.day(&row_str(a, &r.x)).total_cmp(&instants.day(&row_str(b, &r.x))));
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
