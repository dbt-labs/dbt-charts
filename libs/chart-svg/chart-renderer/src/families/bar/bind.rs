//! Bar bind: what the resolved chart and its rows decide before any pixel. Rows with no numeric
//! color are dropped, a wide chart unfolds into long rows, a date measure becomes day numbers and
//! a calendar-bucketed x keys each row by the bucket holding it; the layers, the stack mode and
//! the support_table strip are validated. Nothing here measures text.

use crate::common::{MarkLabels, row_num, row_str};
use crate::diagnostic::Diagnostic;
use crate::families::layers::{OverlayBarLayer, OverlayLayer, OverlayLineLayer, OverlayScatterLayer, resolve_layer_rows};
use crate::families::cartesian::LinearScale;
use crate::families::span;
use crate::families::support_table_strip::{self, SupportTableConfig};
use crate::Payload;

use serde_json::Value;
use std::borrow::Cow;

use super::labels::{INVISIBLE_BAR_PX, MIN_PLOT_SHARE, painted_extent};
use super::{BarResolved, x_resolution};

/// A bar chart bound to its rows.
pub struct Bound<'a> {
    /// The rows layout reads: the payload's own or, after a drop, a fold, a day-number rewrite or a
    /// rekey, the payload over the rewritten rows.
    pub(super) payload: Cow<'a, Payload>,
    pub(super) r: BarResolved,
    /// A wide chart's series, sorted and complete over the authored measures.
    pub(super) wide_series: Vec<String>,
    pub(super) layers: Vec<OverlayLayer>,
    /// The strip a single-plot chart attaches; a `multiples` chart refuses one in layout.
    pub(super) support_table: Option<SupportTableConfig>,
}

pub fn bind(payload: &Payload) -> Result<Bound<'_>, Diagnostic> {
    let mut probe = BarResolved::from_payload(payload)?;
    probe.use_numeric_color(&payload.rows)?;
    let mut prepared: Cow<Payload> = Cow::Borrowed(payload);
    let mut all_rows = None;
    // A row with no numeric color draws no bar, but its category keeps its place on the axis.
    if let (Some(_), Some(field)) = (&probe.numeric_color, probe.color.as_deref()) {
        let kept: Vec<_> = payload.rows.iter().filter(|row| row_num(row, field).is_some()).cloned().collect();
        if kept.len() < payload.rows.len() {
            prepared = Cow::Owned(payload.with_inputs(payload.resolved.clone(), kept));
            all_rows = Some(payload.rows.clone());
        }
    }
    let mut r = BarResolved::from_payload(&prepared)?;
    r.all_rows = all_rows;
    r.use_numeric_color(&prepared.rows)?;
    // `labels.stacked: false` leaves a stack's segments unlabeled
    // (`_append_bar_segment_label_layer`); the total label is its own layer.
    if r.stack != "none" {
        if let Some(labels) = r.style.mark.labels.as_mut().filter(|l| l.stacked == Some(false)) {
            labels.visible = false;
        }
    }
    let layers = validate_layers(&r, &prepared)?;
    // A wide (`y: [a, b]`) chart unfolds into long rows once, here, before any other law runs.
    // The resolver already bakes `y` as `WIDE_VALUE_FIELD`; the only substitution left is the rows
    // and the synthetic color field, so layout (already generic over `color_field`/`payload.rows`)
    // runs unchanged past this point. `wide_series` is the sorted, authored-measure-COMPLETE color
    // domain (`common::wide_series_names`), computed off the pre-fold rows/dimension before either
    // is overwritten below.
    let mut wide_series: Vec<String> = Vec::new();
    if !r.wide_measures.is_empty() {
        wide_series = crate::common::wide_series_names(&r.wide_measures, r.color.as_deref(), &prepared.rows);
        let folded = prepared.with_inputs(
            prepared.resolved.clone(),
            crate::common::unfold_wide_rows(&prepared.rows, &r.wide_measures, r.color.as_deref()),
        );
        r.series_from_measures = r.color.is_none();
        r.prefold_rows = prepared.rows.clone();
        r.link_color = Some(r.color.clone().unwrap_or_else(|| crate::common::WIDE_KEY_FIELD.to_string()));
        r.link_y = r.wide_measures[0].clone();
        r.color = Some(crate::common::WIDE_LABEL_FIELD.to_string());
        prepared = Cow::Owned(folded);
    }
    // A colorless bar is selected by its category, which a link prints verbatim only off a date axis.
    let x_text: Vec<String> = prepared.rows.iter().map(|row| row_str(row, &r.x)).collect();
    let x_text: Vec<&str> = x_text.iter().map(String::as_str).collect();
    let dated_x = crate::families::temporal::x_values_are_standard_dates(&x_text)
        || crate::families::temporal::has_labeled_bucket(&prepared.rows, &r.x);
    let selection_key = match (r.link_color.as_deref(), dated_x) {
        (Some(color), _) => Some(("color", color)),
        (None, false) => Some(("x", r.x.as_str())),
        (None, true) => None,
    };
    r.selection = crate::selected::Selection::of(
        &prepared,
        r.link.as_deref(),
        &selection_key.into_iter().collect::<Vec<_>>(),
    )?;
    // A date measure draws as day numbers on the numeric arms; a row missing either date refuses in
    // `with_day_numbers`.
    if let (Some("temporal"), Some(start_field)) = (r.measure_type.as_deref(), r.y_start.as_deref()) {
        let base_days = span::with_day_numbers(&prepared, [r.y.as_str(), start_field])?;
        prepared = Cow::Owned(span::with_layer_day_numbers(
            &base_days,
            r.query_name.as_deref(),
            [r.y.as_str(), start_field],
            &r.layers,
        )?);
    }
    // A calendar-bucketed vertical axis keys every row by the bucket that holds it
    // (`complete_ordinal_time_series`), so a month-end or off-anchor yearly date lands in its band
    // rather than matching none.
    if let Some((rekeyed, prefold_rows)) = rekey_to_enclosing_buckets(&r, &prepared)? {
        r.prefold_rows = prefold_rows;
        prepared = Cow::Owned(rekeyed);
    }
    // A centered stack piles magnitudes upward from 0, so a chart of negatives alone bakes a domain
    // top of 0 (the ladder's last rung) and every mark collapses to zero height.
    if r.stack == "center"
        && r.stacked_domain_max.or(r.style.axis_y.tick_values.last().copied()).is_some_and(|top| top <= 0.0)
        && prepared.rows.iter().any(|row| row_num(row, &r.y).is_some_and(|v| v != 0.0))
    {
        return Err(prepared.painted_no_marks());
    }

    // A `multiples` chart is composed panel by panel, whose own guards refuse a strip.
    let mut support_table = None;
    if r.multiples.is_none() {
        if !matches!(r.stack.as_str(), "none" | "zero" | "center" | "normalize") {
            return Err(format!("bar: stack={:?} is none, zero, center or normalize only", r.stack).into());
        }
        support_table = r
            .support_table
            .as_ref()
            .map(|v| support_table_strip::parse_config(v, "bar"))
            .transpose()?
            .map(|config| config.folded_from(&r.prefold_rows, r.wide_measures.len()));
    }
    Ok(Bound { payload: prepared, r, wide_series, layers, support_table })
}

/// Validates a `type: "bar"` layer over a single-series base: a nominal layer
/// color, if any, and the layer's own x column, if authored.
pub(super) fn validate_bar_layer(
    raw: &Value,
    r: &BarResolved,
    payload: &Payload,
) -> Result<OverlayBarLayer, String> {
    let layer = OverlayBarLayer::parse(raw, "bar")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "bar")?;
    Ok(layer)
}

/// Validates a `type: "bar"` layer over a `color`-split base, stacked or
/// grouped: the
/// one modeled shape is the layer sharing the base's OWN `color` field
/// exactly. VL's default per-mark stack transform runs on the layer's own
/// data independently of the base's (`_overlay.py`'s `emit_bar_layer`
/// never disables stacking on a color-encoded bar layer — its
/// `is_stacked=False` note in `_build_layer_label_specs` gates the value-
/// LABEL midpoint path only, not the mark geometry), so the layer draws its
/// own fresh cumulative stack, painted at the SAME band position as the
/// base, sharing the base's own color scale — no new legend entry, since
/// the domain is identical. `fixtures/layered-color-bar-sort-x-order` is
/// the one landed row (probed): a category with no layer rows draws
/// nothing there, matching VL's own "no data, no mark" law.
pub(super) fn validate_bar_layer_over_stacked_base(
    raw: &Value,
    r: &BarResolved,
    payload: &Payload,
) -> Result<OverlayBarLayer, String> {
    let layer = OverlayBarLayer::parse(raw, "bar")?;
    // Three modeled sub-shapes: the layer shares the base's own `color` field
    // exactly (its own fresh stack, see the render-site doc comment), groups by
    // a `color` field of its own (its values claim the shared scale's next
    // colors), or authors NO color at all — a single, un-stacked bar drawn
    // at the band position against the SAME shared value scale
    // (`charts/extensibility_1`'s `bullet`'s own `Actual` layer; the
    // existing single-series bar-layer paint path already handles this —
    // its geometry never reads `r.color`).
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "bar")?;
    Ok(layer)
}

/// Validates a ranged `type: "bar"` layer (a candlestick's body, open to
/// close) over a ranged base. The base is single-series or colored 1:1 with
/// the category; the layer's `color`, when authored, is either the base's own
/// field (one shared scale) or — over a colorless base — its own grouping,
/// whose values take the next palette slots. One such layer, on the shared
/// scale, with no legend and no value labels: everything else refuses.
pub(super) fn validate_ranged_bar_layer(
    raw: &Value,
    r: &BarResolved,
    payload: &Payload,
) -> Result<OverlayBarLayer, String> {
    let layer = OverlayBarLayer::parse(raw, "bar")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "bar")?;
    Ok(layer)
}

/// Validates a `type: "scatter"` layer against the one modeled shape: the shared
/// scale or a scale of its own. The point IS the whole mark, so its size is
/// required (`OverlayScatterLayer::parse`).
pub(super) fn validate_scatter_layer(raw: &Value, r: &BarResolved, payload: &Payload) -> Result<OverlayScatterLayer, String> {
    let layer = OverlayScatterLayer::parse(raw, "bar")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "bar")?;
    Ok(layer)
}

/// Validates `r.layers` against the modeled shapes: any run of `type: "bar"`,
/// `"line"`, `"area"` and `"scatter"` layers, in authored order — production
/// paints and claims palette slots in authored order, and each layer shares the
/// base's y scale or, once any pins a side, owns one (`YSides`). Each layer's
/// own type-specific validator decides whether it supports the base's current
/// `stack`/`color` shape. `payload` supplies the base's own rows (a same-query
/// layer's color field lives there) and `payload.datasets` (a cross-query
/// layer's own rows).
pub(super) fn validate_layers(r: &BarResolved, payload: &Payload) -> Result<Vec<OverlayLayer>, String> {
    if r.layers.is_empty() {
        return Ok(Vec::new());
    }
    let mut layers = Vec::with_capacity(r.layers.len());
    for raw in &r.layers {
        // Named by its own authored type before a full struct deserialize
        // fails on a missing family-specific field (`line_mark`/`bar_mark`)
        // with a much less useful message.
        match raw.get("type").and_then(Value::as_str).unwrap_or("") {
            "bar" => {
                // A color-split base, stacked or grouped, shares its scale with
                // the layer's own fresh stack (`validate_bar_layer_over_stacked_base`).
                let ranged = raw.get("y_start").is_some_and(|v| !v.is_null());
                let bl = if ranged {
                    validate_ranged_bar_layer(raw, r, payload)?
                } else if r.color.is_some() {
                    validate_bar_layer_over_stacked_base(raw, r, payload)?
                } else {
                    validate_bar_layer(raw, r, payload)?
                };
                layers.push(OverlayLayer::Bar(Box::new(bl)));
            }
            "scatter" => {
                let sl = validate_scatter_layer(raw, r, payload)?;
                layers.push(OverlayLayer::Scatter(Box::new(sl)));
            }
            "line" | "area" => {
                let ll = validate_line_layer(raw, r, payload)?;
                layers.push(OverlayLayer::Line(Box::new(ll)));
            }
            other => {
                return Err(format!("bar: layer type {other:?} is none of bar, line, area or scatter"));
            }
        }
    }
    Ok(layers)
}

/// Validates a `type: "line"` layer: a shared-scale or dual-axis line over any
/// bar base, a disconnected `curve: "step"` goal marker included
/// (`charts/extensibility_1`'s `bullet`): a two-point plateau per category,
/// inset by `STEP_BAND_DISCONNECTED_WIDTH` of the band (`step_band.py`'s
/// `connect: false` branch). A non-nominal layer color refuses by name.
pub(super) fn validate_line_layer(
    raw: &Value,
    r: &BarResolved,
    payload: &Payload,
) -> Result<OverlayLineLayer, String> {
    let layer = OverlayLineLayer::parse(raw, "bar")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "bar")?;
    Ok(layer)
}

/// The payload with each row of a banded calendar grain keyed by the bucket that
/// encloses its date, and the pre-fold rows a wide strip reads keyed the same way;
/// `None` when no row sits off its bucket's start.
pub(super) fn rekey_to_enclosing_buckets(
    r: &BarResolved,
    payload: &Payload,
) -> Result<Option<(Payload, Vec<serde_json::Map<String, Value>>)>, String> {
    if r.orientation != "vertical" {
        return Ok(None);
    }
    let filled = x_resolution(r, &payload.rows)?;
    let (Some(tu), crate::families::x_type::Grain::Calendar(_)) = (filled.filled, filled.resolution.grain) else {
        return Ok(None);
    };
    let fiscal = r.style.axis_x.fiscal_year_start_month;
    let rekey = |rows: &[serde_json::Map<String, Value>]| {
        let mut changed = false;
        let rekeyed: Vec<_> = rows
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
        (rekeyed, changed)
    };
    let (rows, changed) = rekey(&payload.rows);
    let (prefold_rows, prefold_changed) = rekey(&r.prefold_rows);
    Ok((changed || prefold_changed).then(|| (payload.with_inputs(payload.resolved.clone(), rows), prefold_rows)))
}

/// Production's genuine-zero label (`features/zero_value_label.py`): the text
/// layer a plain single-series bar gets when a row's bar draws no visible mark
/// (a genuine zero, or one painted under `INVISIBLE_BAR_PX`).
/// The layer carries every row and blanks all but the zero ones through a
/// `condition` on `text` (`:129-133`), so it paints through the value-label
/// machinery with `blank` marks — the marks stay, empty, and their ink counts.
///
/// `_zero_label_scope` (`:51`) is the gate: a bar, no stack (`:73`), no
/// layers (`:75`), no authored value labels (`:77` — the value-label layer owns
/// the zero row then) and no color channel (`:79`). A wide chart passes the
/// scope (its resolved `y` is the synthetic `WIDE_VALUE_FIELD`) and falls out
/// at the row test (`:111`): the rows `:106-110` read do not carry that column
/// (probed: a wide bar with a zero row draws no label). The mark is "above"
/// whatever `labels.position` says (`:119`), with no `dx`/`dy`, and takes only
/// the label slot's format (`:143`, plain d3 — no house register) and font
/// (`:127`, `:145-147`).
///
/// `scale` is the value scale across the whole canvas. A row that would paint
/// under `INVISIBLE_BAR_PX` on a plot `MIN_PLOT_SHARE` of the canvas is the
/// cheap pre-test, which runs before the plot is fitted and keeps the layer
/// off every chart whose bars all draw; `zero_label_blank` then blanks the
/// rest at the real plot size.
pub(super) fn zero_label(
    r: &BarResolved,
    rows: &[serde_json::Map<String, Value>],
    scale: &LinearScale,
) -> Result<Option<MarkLabels>, String> {
    let authored = r.style.mark.labels.as_ref();
    let in_scope = r.stack == "none"
        && r.layers.is_empty()
        && r.wide_measures.is_empty()
        && !authored.is_some_and(|l| l.visible)
        && !r.resolved_channels.contains_key("color");
    let px = |v: f64| scale.position(v);
    let range = (scale.range_start, scale.range_end);
    let invisible = |row: &serde_json::Map<String, Value>| {
        row_num(row, &r.y).is_some_and(|v| painted_extent(&px, range, v) * MIN_PLOT_SHARE < INVISIBLE_BAR_PX)
    };
    if !in_scope || !rows.iter().any(invisible) {
        return Ok(None);
    }
    let slot = authored.ok_or("bar: style.mark.labels absent (resolved payload required)")?;
    Ok(Some(MarkLabels {
        visible: true,
        last_only: false,
        field: None,
        format: slot.format.clone(),
        dx: None,
        dy: None,
        font: slot.font.clone(),
        position: Some("above".into()),
        stacked: None,
    }))
}
