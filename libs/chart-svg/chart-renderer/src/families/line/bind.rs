//! Line and area bind: what the resolved chart and its rows decide before any pixel. The wide
//! fold, the labeled-bucket rewrite and the calendar gap fill give the rows layout reads, and each
//! temporal x is parsed once into its instant and the domain seated by it. Nothing here measures text.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use serde_json::Value;

use super::{validate_line_layers, LineResolved};
use ::bind::rows::{is_year_shaped, row_str};
use diagnostic::Diagnostic;
use crate::families::layers::OverlayLayer;
use ::bind::temporal::{VegaType};
use ::bind::temporal::{TimePart, TimeUnit};
use ::bind::x_type::XResolution;
use ::bind::time_axis::{Grain, TimeAxis};
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
    let mut x = x_axis(&r, &prepared, &overlays, draw_areas)?;
    if let Some(order) = &r.connect {
        let payload = prepared.to_mut();
        order_rows(&r.id, order, &mut payload.rows)?;
        x.domain = key_revisits(&r, &overlays, payload)?;
    }
    r.legend_view = crate::legend_view::LegendView::of(
        &prepared,
        &crate::legend_view::ViewFacts {
            family: crate::legend_view::Family::Line,
            color: r.color.as_deref(),
            panel_fields: r.panel_axes.iter().map(|axis| axis.field.as_str()).collect(),
            wide: !r.wide_measures.is_empty(),
            normalized: r.stack.as_deref() == Some("normalize"),
            layered: !overlays.is_empty() || r.support_table.is_some(),
            continuous_color: r.numeric_color.is_some(),
        },
    )?;
    // The value axis fits what a legend view draws; resolve baked its bounds and ladder off every row.
    if let Some(narrowed) = r.legend_view.clone().and_then(|view| view.narrowed(&prepared, r.color.as_deref().expect("a legend view needs a color field"))) {
        if narrowed.refits_axes() {
            let shown: Vec<f64> = narrowed.payload.rows.iter().filter_map(|row| ::bind::rows::row_num(row, &r.y)).collect();
            if r.stack.is_some() {
                crate::legend_view::unbake(&mut r.style.axis_y, true);
            } else {
                crate::legend_view::refit(&mut r.style.axis_y, &shown, true);
            }
        }
        r.unviewed = r.legend_view.as_ref().map(|view| view.unviewed(payload));
        r.whole_rows = Some(narrowed.whole);
        prepared = Cow::Owned(narrowed.payload);
    }
    Ok(Bound { payload: prepared, r, overlays, draw_areas, wide_series: wide.series, wide_labels: wide.labels, x })
}

/// A `connect:` cell: the order column holds all numbers, or all text (ISO dates sort as text).
enum OrderKey {
    Number(f64),
    Text(String),
}

/// `connect: <column>` draws the path in that column's ascending order. The sort is stable, so
/// rows that tie keep the order the query returned them in.
fn order_rows(id: &str, order: &str, rows: &mut Vec<serde_json::Map<String, Value>>) -> Result<(), Diagnostic> {
    let mut keyed = Vec::with_capacity(rows.len());
    for (i, row) in rows.drain(..).enumerate() {
        let key = match row.get(order) {
            None | Some(Value::Null) => return Err(format!("line {id:?}: connect column {order:?} is empty in row {}", i + 1).into()),
            Some(Value::String(s)) => OrderKey::Text(s.clone()),
            Some(other) => match other.as_f64() {
                Some(n) => OrderKey::Number(n),
                None => return Err(format!("line {id:?}: connect column {order:?} holds {other} in row {}; it takes numbers or text", i + 1).into()),
            },
        };
        keyed.push((key, row));
    }
    if keyed.windows(2).any(|w| matches!((&w[0].0, &w[1].0), (OrderKey::Number(_), OrderKey::Text(_)) | (OrderKey::Text(_), OrderKey::Number(_)))) {
        return Err(format!("line {id:?}: connect column {order:?} mixes numbers and text").into());
    }
    keyed.sort_by(|(a, _), (b, _)| match (a, b) {
        (OrderKey::Number(a), OrderKey::Number(b)) => a.total_cmp(b),
        (OrderKey::Text(a), OrderKey::Text(b)) => a.cmp(b),
        (OrderKey::Number(_), OrderKey::Text(_)) => std::cmp::Ordering::Less,
        (OrderKey::Text(_), OrderKey::Number(_)) => std::cmp::Ordering::Greater,
    });
    rows.extend(keyed.into_iter().map(|(_, row)| row));
    Ok(())
}

/// The marker a revisited x carries after its value: `3\u{1}1` is the second time a series is at
/// x = 3. Only a numeric x is keyed (`key_revisits` checks), so no real value contains it. It never
/// leaves layout; [`x_number`] reads the value back.
const REVISIT: char = '\u{1}';

/// The x value a key stands for, as the reader sees it.
pub(super) fn x_text(key: &str) -> &str {
    key.split(REVISIT).next().unwrap_or(key)
}

/// The number an x key stands for: its value, a revisit's marker dropped.
pub(crate) fn x_number(key: &str) -> Option<f64> {
    x_text(key).parse().ok()
}

/// `connect:` draws one mark per row, so a series that comes back to an x must not share a join
/// key with its earlier visit. Each later visit's x is rewritten to `x`+marker+n in `payload`; the
/// returned domain is the keys in row order.
fn key_revisits(r: &LineResolved, overlays: &[OverlayLayer], payload: &mut Payload) -> Result<Vec<String>, Diagnostic> {
    if r.multiples.is_some() || !overlays.is_empty() {
        return Err(format!("line {:?}: connect does not combine with layers or multiples", r.id).into());
    }
    let mut visits: HashMap<(String, String), usize> = HashMap::new();
    let mut domain = Vec::new();
    let mut seen = HashSet::new();
    for (i, row) in payload.rows.iter_mut().enumerate() {
        let x = match row.get(&r.x) {
            None | Some(Value::Null) => return Err(format!("line {:?}: connect needs an x on every row; row {} has none", r.id, i + 1).into()),
            Some(v) => ::bind::rows::cell_str(Some(v)),
        };
        if x.parse::<f64>().is_err() {
            return Err(format!("line {:?}: connect needs a quantitative x, but row {} has {x:?}", r.id, i + 1).into());
        }
        let series = r.color.as_deref().map(|c| row_str(row, c)).unwrap_or_default();
        let visit = visits.entry((x.clone(), series)).or_default();
        let key = if *visit == 0 { x } else { format!("{x}{REVISIT}{visit}") };
        *visit += 1;
        if seen.insert(key.clone()) {
            domain.push(key.clone());
        }
        // Links and labels read the cell as the query wrote it, not the join key.
        if let Some(raw) = row.insert(r.x.clone(), Value::String(key)) {
            row.insert(::bind::wide::RAW_X.to_string(), raw);
        }
    }
    Ok(domain)
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
    // The one x-type law (`::bind::x_type::resolve_cartesian_x_type`) decides the grain
    // and whether x is a category; the labeled-bucket and year rewrites run
    // ahead of it exactly as production's emitters apply them.
    let law_rows = ::bind::temporal::normalize_labeled_temporal(&payload.schema.rows, &payload.rows, x_field)?;
    let law = ::bind::x_type::resolve_cartesian_x_type(
        &payload.schema.rows,
        &law_rows,
        x_field,
        &r.style.axis_x.x_axis(),
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
    let is_dates = is_year_shaped(&payload.schema.rows, domain_rows, x_field)?
        || law.vl_type == VegaType::Temporal
        || matches!(::bind::temporal::infer_vega_type(&payload.schema.rows, domain_rows, x_field)?, VegaType::Temporal | VegaType::Ordinal);
    let unit: Option<TimeUnit> = match law.grain {
        ::bind::x_type::Grain::Calendar(tu) => Some(tu),
        _ => None,
    };
    // A time-part axis (`monthofyear`, ...) collapses every date onto the part's own cycle.
    let part: Option<TimePart> = match law.grain {
        ::bind::x_type::Grain::Part(part) => Some(part),
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
        let layer_types = crate::families::layers::resolve_layer_types(payload, overlay.query_name(), r.query_name.as_deref(), fam)?;
        let normalized = ::bind::temporal::normalize_labeled_temporal(layer_types, layer_rows, layer_x)?;
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
    let normalized = ::bind::temporal::normalize_labeled_temporal(&payload.schema.rows, &payload.rows, &r.x)?;
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let filled = ::bind::x_type::resolve_filled_x(
        &payload.schema.rows,
        &normalized,
        &r.x,
        &r.style.axis_x.x_axis(),
        if draw_areas { "area" } else { "line" },
        curve == Some("step"),
        &panel_fields,
    )?;
    let (Some(tu @ TimeUnit::YearQuarter), ::bind::x_type::Grain::Calendar(_)) = (filled.filled, filled.resolution.grain) else {
        return Ok(None);
    };
    let fiscal = r.style.axis_x.fiscal_year_start_month;
    let axis = ::bind::time_axis::TimeAxis::over(payload.rows.iter().filter_map(|row| row.get(&r.x).and_then(Value::as_str)));
    let mut changed = false;
    let rows: Vec<_> = payload
        .rows
        .iter()
        .cloned()
        .map(|mut row| {
            let start = row
                .get(&r.x)
                .and_then(Value::as_str)
                .and_then(|text| axis.date(text))
                .and_then(|date| ::bind::temporal::off_anchor_bucket_start(date, tu, fiscal));
            if let Some(start) = start {
                if let Some(raw) = row.insert(r.x.clone(), Value::String(start)) {
                    row.insert(::bind::wide::RAW_X.to_string(), raw);
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
    let normalized = ::bind::temporal::normalize_labeled_temporal(&payload.schema.rows, &payload.rows, &r.x)?;
    let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
    let mark = if draw_areas { "area" } else { "line" };
    let filled = ::bind::x_type::resolve_filled_x(
        &payload.schema.rows,
        &normalized,
        &r.x,
        &axis.x_axis(),
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
    for panel in ::bind::multiples::regroup(&r.panel_axes, &normalized)? {
        let mut completed =
            ::bind::temporal::complete_ordinal_rows(&panel.rows, &r.x, &dims, tu, fill, axis.fiscal_year_start_month)?;
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
    let series = ::bind::wide::wide_series_names(&r.wide_measures, r.color.as_deref(), &payload.rows);
    let cloned = payload
        .with_inputs(payload.resolved.clone(), ::bind::wide::unfold_wide_rows(&payload.rows, &r.wide_measures, r.color.as_deref()))
        .with_row_types(::bind::wide::unfold_wide_types(&payload.schema.rows, &r.wide_measures)?);
    r.series_from_measures = r.color.is_none();
    r.prefold_rows = payload.rows.clone();
    r.link_color = Some(r.color.clone().unwrap_or_else(|| ::bind::wide::WIDE_KEY_FIELD.to_string()));
    r.link_y = r.wide_measures[0].clone();
    r.color = Some(::bind::wide::WIDE_LABEL_FIELD.to_string());
    let labels = display_text::wide_measure_labels(&r.wide_measures, &r.field_labels)?.labels;
    Ok(WideFold { payload: Some(cloned), series, labels })
}
