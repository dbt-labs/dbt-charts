//! Scatter bind: what the resolved chart and its rows decide before any pixel. The wide fold and the
//! labeled-bucket rewrite give the rows layout reads, the channels' kinds classify each axis, and
//! the authored mark shapes that cannot draw refuse here. Nothing here measures text.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use serde_json::Value;

use super::{x_time_axis, color_extent, drawn_at, quantitative_color, rows_at, size_scale_domain, validate_scatter_layers, ScatterResolved, Shape};
use crate::common::color_domain_of;
use crate::common::row_str;
use crate::diagnostic::Diagnostic;
use crate::families::layers::OverlayLayer;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::time_axis::TimeAxis;
use crate::Payload;

/// A scatter chart bound to its rows.
pub struct Bound<'a> {
    /// The rows layout reads: the payload's own, or, after a wide fold or a labeled-bucket
    /// rewrite, the payload over the rewritten rows.
    pub(super) payload: Cow<'a, Payload>,
    pub(super) r: ScatterResolved,
    pub(super) overlays: Vec<OverlayLayer>,
    /// A wide chart's series, in the folded rows' sorted measure order, as the legend names them.
    pub(super) wide_series: Vec<String>,
    pub(super) shape: Shape,
    /// Every x date of the rows and the layers, read once; empty without a date x.
    pub(super) time: TimeAxis,
    /// The rows Vega keeps (`kept_reading`), as indices into `payload.rows`: the rows every color,
    /// shape and size scale reads, whichever facet panel draws them.
    pub(super) drawn: Vec<usize>,
    /// The color legend's categories in domain order; empty without a categorical color.
    pub(super) color_domain: Vec<String>,
    /// A quantitative color's `(min, max)` over the drawn rows.
    pub(super) color_range: Option<(f64, f64)>,
    /// A size channel's zero-anchored `(min, max)` over the drawn rows.
    pub(super) size_domain: Option<(f64, f64)>,
}

pub fn bind(payload: &Payload) -> Result<Bound<'_>, Diagnostic> {
    let mut r = ScatterResolved::from_payload(payload)?;

    let overlays = validate_scatter_layers(&r, payload)?;
    if r.style.point_mark.shape.as_ref().is_some_and(|v| !v.is_string()) {
        return Err("scatter: an authored point_mark shape must be a symbol name".into());
    }
    if r.style.point_mark.filled == Some(false) {
        if r.style.point_mark.fill.is_none() {
            return Err("scatter: unfilled point marks need style.marks.point.fill".into());
        }
    }
    // A wide (`y: [a, b]`) chart unfolds into long rows once, here, before
    // any other law runs — same seam as bar.rs/line.rs (`common::
    // unfold_wide_rows`'s own doc comment). Scatter's fold has no
    // dimension/stack-order complexity to carry: `_wide_scatter_fold`
    // (`emitters/scatter.py:65-95`) passes `fold_order=raw_wide_series_
    // names(measures, dimension, data)`, and `raw_wide_series_names`
    // (`compile/resolve/chart/_wide_fields.py:142-161`) is `sorted(
    // measures)` with no dimension — production paints in SORTED measure
    // order, never the authored `wide_measures` array order — so this seam
    // sorts before folding; a `color:` dimension composes `value - measure`
    // labels like bar/line. Unlike bar/line, the row's own `WIDE_LABEL_FIELD` is overwritten
    // to the HUMANIZED text directly (not the raw measure name) —
    // production's real VL `calculate` expression (`_label_expression`)
    // bakes the humanized label straight onto the row, and scatter's
    // `color_domain`/`series_of` both read that field verbatim with no
    // further humanization step of their own, so this is the one
    // substitution that keeps them correct with no new plumbing.
    let mut prepared: Cow<Payload> = Cow::Borrowed(payload);
    let mut wide_series: Vec<String> = Vec::new();
    if !r.wide_measures.is_empty() {
        let wide_labels = display_text::wide_measure_labels_for(&r.wide_measures, &r.field_labels)?;
        let mut fold_order = r.wide_measures.clone();
        fold_order.sort();
        let dimension = r.color.clone();
        let raw_names = crate::common::wide_series_names(&r.wide_measures, dimension.as_deref(), &payload.rows);
        // Display text per raw series name: the measure part humanized, a
        // `color:` dimension value kept verbatim ahead of it.
        let mut shown: HashMap<String, String> = HashMap::new();
        for measure in &r.wide_measures {
            match dimension.as_deref() {
                None => {
                    shown.insert(measure.clone(), wide_labels[measure].clone());
                }
                Some(dim) => {
                    for v in crate::common::wide_dimension_values(&payload.rows, dim) {
                        shown.insert(
                            format!("{v}{}{measure}", crate::common::WIDE_SERIES_SEPARATOR),
                            format!("{v}{}{}", crate::common::WIDE_SERIES_SEPARATOR, wide_labels[measure]),
                        );
                    }
                }
            }
        }
        wide_series = raw_names.iter().map(|raw| shown[raw].clone()).collect();
        let folded = crate::common::unfold_wide_rows(&payload.rows, &fold_order, dimension.as_deref())
            .into_iter()
            .map(|mut row| {
                if let Some(Value::String(raw)) = row.remove(crate::common::WIDE_LABEL_FIELD) {
                    row.insert(crate::common::WIDE_LABEL_FIELD.to_string(), Value::String(shown[&raw].clone()));
                }
                row
            })
            .collect();
        r.link_color = Some(r.color.clone().unwrap_or_else(|| crate::common::WIDE_KEY_FIELD.to_string()));
        r.link_y = r.wide_measures[0].clone();
        r.color = Some(crate::common::WIDE_LABEL_FIELD.to_string());
        prepared = Cow::Owned(payload.with_inputs(payload.resolved.clone(), folded));
    }
    // A labeled bucket or a year-shaped x reads as its ISO start, as line rewrites the column: a year is a
    // time axis, never a number line joined to zero.
    if r.link_x_labeled || crate::common::is_year_shaped(&prepared.rows, &r.x) {
        let rows = crate::families::temporal::normalize_labeled_temporal(&prepared.rows, &r.x)?.into_owned();
        prepared = Cow::Owned(prepared.with_inputs(prepared.resolved.clone(), rows));
    }

    // A column types the way production's `infer_vega_type_from_data` reads
    // it: its first ten cells decide, so a quantitative column drops a later
    // string row (`kept_reading`) and anything else is a nominal band, whose
    // domain order comes from `common::vega_ascending_sort`.
    let column_is_measure = |field: &str| infer_vega_type(&prepared.rows, field) == VegaType::Quantitative;
    let x_numeric = column_is_measure(&r.x);
    let y_numeric = column_is_measure(&r.y);
    let x_nominal = !x_numeric;
    // A column of ISO dates types `temporal` (a UTC time scale), not a
    // category band (`type_inference.py`'s strict `all_temporal` gate). A
    // date-LIKE column (`2024-01`) that `detect_time_unit` buckets is also
    // `temporal` on a scatter (`resolve_cartesian_x_type`), which this arm
    // does not draw; any other date-like column stays a band.
    let (x_dates, x_nominal) = if x_nominal {
        let mut seen = HashSet::new();
        let distinct: Vec<String> = prepared
            .rows
            .iter()
            .map(|row| row_str(row, &r.x))
            .filter(|v| !v.is_empty() && seen.insert(v.clone()))
            .collect();
        let refs: Vec<&str> = distinct.iter().map(String::as_str).collect();
        if crate::families::temporal::x_values_are_standard_dates(&refs)
            || crate::families::temporal::x_values_are_temporal_candidates(&refs)
                && crate::families::temporal::detect_time_unit(&refs).is_some()
        {
            (true, false)
        } else {
            (false, true)
        }
    } else {
        (false, false)
    };
    let time = x_time_axis(&r, &prepared, &overlays, x_dates)?;
    let y_nominal = !y_numeric;
    // A time spec on a categorical y paints through a UTC labelExpr over date
    // bands (`emitters/scatter.py`); over any other value `gate_label_format`
    // raises instead of painting "Invalid Date".
    if let Some(spec) = r.style.axis_y.labels.spec().filter(|s| y_nominal && crate::families::temporal::is_time_format(s)) {
        crate::families::cartesian_x_errors::time_format(spec, "axis_y.labels.format", &r.y, &prepared.rows, None)?;
    }
    let y_log = r.style.axis_y.is_log_scale();
    // A log axis is never baked (`_resolve_cartesian_ticks` returns no
    // ladder for it): its ticks come from the scale itself.
    // An independent facet scale is never baked: each panel draws Vega's own ticks.
    let independent_facet = r.multiples.as_ref().is_some_and(|m| m.scale == "independent");
    let y_measure = y_numeric && (!r.style.axis_y.tick_values.is_empty() || y_log || independent_facet);
    let drawn = drawn_at(&prepared.rows, &r, x_nominal, x_dates, y_nominal);
    let drawn_rows = rows_at(&prepared.rows, &drawn);
    // `wide_series` (the sorted, authored-measure-COMPLETE domain, `common::wide_series_names` +
    // humanization) feeds a wide chart's domain instead of a row scan — a measure null on every row
    // still owns a domain/palette slot, same law bar.rs's own `color_domain` split applies
    // (`build_chart_domains`).
    let color_domain = if !wide_series.is_empty() {
        wide_series.clone()
    } else if quantitative_color(&r) {
        Vec::new()
    } else {
        r.color.as_deref().map_or_else(Vec::new, |field| color_domain_of(drawn_rows.iter().copied(), field))
    };
    let color_range = r
        .color
        .as_deref()
        .filter(|_| quantitative_color(&r))
        .map(|field| color_extent(drawn_rows.iter().copied(), field));
    let size_domain = size_scale_domain(&r, &drawn_rows, (x_nominal, x_dates, y_nominal))?;
    Ok(Bound {
        payload: prepared,
        r,
        overlays,
        wide_series,
        shape: Shape { x_dates, x_nominal, y_nominal, y_measure },
        time,
        drawn,
        color_domain,
        color_range,
        size_domain,
    })
}
