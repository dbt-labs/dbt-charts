//! What a panel derives from its rows before it draws: the value domain and ticks, the stacks, the category
//! order and the data map.

use crate::common::{AxisStyle, ChartSort, SortAggregate, color_domain_of, dimension_sort_domain, row_num, row_str};
use crate::families::cartesian::vl_default_tick_count;
use crate::families::span::RangedBars;

use serde_json::Value;
use std::collections::{HashMap, HashSet};

use super::facet::color_1to1_with_cat;
use super::labels::label_position;
use super::{BarResolved, BarStyle, CHROME_PX, NORMALIZE_QUARTILE_TICKS, continuous_max_size};

/// The order a categorical band domain renders in.
///
/// Mirrors what production's bar emitter pins on the categorical encoding
/// (`emitters/bar.py`, `_emit_horizontal` and `_emit_vertical`):
///
/// | orientation | authored sort | color channel | production pins           | order       |
/// |-------------|---------------|----------------|---------------------------|-------------|
/// | any         | present       | any            | `{field, order}`          | `Field`     |
/// | horizontal  | none          | none           | `{field, order: desc}`    | `ValueDesc` |
/// | horizontal  | none          | present        | `sort: null` (#8214)      | `Query`     |
/// | horizontal  | none          | any, `y_start` | `sort: null`              | `Query`     |
/// | vertical    | none          | any            | `sort: null`              | `Query`     |
///
/// `Field` is `x_domain_order` (`core/utils.py`) folded with `bar_sort_op`'s
/// verdict (`SortAggregate::Sum` for a stacked bar sorted by its own measure,
/// `Min` for every other sort), and a category with no value for the sort
/// field keeps its first-occurrence position after the ranked ones. One deliberate
/// divergence: under `Min` a sort value that is present but not a JSON number
/// is refused, where production compares strings and dates natively; under
/// `Sum` a numeric string parses and any other non-number is refused, where
/// production reads it as no value — the same refusal the `ValueDesc` arm
/// applies to the value field. The
/// vertical arm's chronological override for an ISO-date-looking domain
/// still runs after `Field`, so an authored sort on such a domain is
/// clobbered there — pre-existing, no corpus case authors one.
///
/// The unauthored cells but one are `sort: null`, which Vega-Lite reads as
/// "keep first-occurrence row order" — `_emit_vertical` spreads
/// `chart_sort_to_vl(chart.sort)` into the encoding, and that is `None`
/// whenever nothing is authored; `_emit_horizontal` sets the literal for a
/// colored bar since #8214. There is no alphabetical cell: VL's ascending
/// default only applies when the key is ABSENT, and production never leaves
/// it absent. Wide horizontal bars also pin `sort: null` in production; this
/// renderer refuses a wide payload outright before either arm (see the
/// `wide_measures` check in `render`), so no wide domain reaches here.
#[derive(Clone, Copy)]
pub(super) enum CatOrder<'a> {
    Query,
    ValueDesc,
    Field(&'a ChartSort, SortAggregate),
}

pub(super) fn cat_order<'a>(
    sort: Option<&'a ChartSort>,
    sorts_by_value_by_default: bool,
    measure_field: &str,
    stack: &str,
) -> CatOrder<'a> {
    match sort {
        Some(sort) => {
            let op = if stack != "none" && sort.by == measure_field {
                SortAggregate::Sum
            } else {
                SortAggregate::Min
            };
            CatOrder::Field(sort, op)
        }
        None if sorts_by_value_by_default => CatOrder::ValueDesc,
        None => CatOrder::Query,
    }
}

/// Build the category domain in `order` (see `CatOrder`).
///
/// `CatOrder::Field`'s rank-and-trail shape (stable sort, unranked
/// categories trailing in both directions) is `common::dimension_sort_domain`
/// — the same law line/area/heatmap share for their own dimension axis, here
/// called with the aggregate `cat_order` chose. `CatOrder::ValueDesc` folds a
/// SUM (`vl_sort_op`'s unpinned fallback; a facet repeats a category across
/// its panels), not a sort field, and keeps its own loop.
pub(super) fn build_cat_domain(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    val_field: &str,
    order: CatOrder,
) -> Result<Vec<String>, String> {
    let mut seen = HashSet::new();
    let domain: Vec<String> = rows
        .iter()
        .map(|row| row_str(row, cat_field))
        .filter(|c| seen.insert(c.clone()))
        .collect();
    match order {
        CatOrder::Query => Ok(domain),
        CatOrder::ValueDesc => {
            let mut by_cat: HashMap<String, f64> = HashMap::new();
            for row in rows {
                // Vega's `sum` skips a null: a category of null rows sums to 0.
                *by_cat.entry(row_str(row, cat_field)).or_insert(0.0) += row_num(row, val_field).unwrap_or(0.0);
            }
            // Every `domain` entry is a key of `by_cat` by construction, so
            // there is no unranked category to trail. A plain stable sort
            // suffices; ties keep query order.
            let mut d = domain;
            d.sort_by(|a, b| {
                by_cat[b].partial_cmp(&by_cat[a]).unwrap_or(std::cmp::Ordering::Equal)
            });
            Ok(d)
        }
        // Bar's emitter only writes `sort: {field, op, order}`; Vega orders the
        // domain itself, so a `min` key compares as JavaScript does.
        CatOrder::Field(sort, SortAggregate::Min) => {
            let refs: Vec<&serde_json::Map<String, Value>> = rows.iter().collect();
            crate::common::min_ranked_domain(domain, &refs, |row| row_str(row, cat_field), sort).map_err(|e| format!("bar: {e}"))
        }
        CatOrder::Field(sort, SortAggregate::Sum) => {
            dimension_sort_domain(rows, cat_field, domain, Some(sort), SortAggregate::Sum, "bar")
        }
    }
}

/// The rendered value domain of an endpoint-rail concat, and the baked
/// ladder re-filtered against it. The rail specs carry `domainMax` with
/// VL's default `nice: true`, and nice runs LAST over the overridden end
/// (the established configureDomain order) — so the rendered domain is the
/// d3-nice ceiling of the headroomed stacked max (chart-lab: 181440 →
/// 200000, its "$200K" rung flush at the plot top; bar-charts_7: 130235 →
/// 140000, its 150K rung dropped). An authored `nice: false` reaches Vega
/// and suppresses the rounding; authored true or the unauthored default
/// nices. `value_domain_and_ticks` filtered the ladder against the un-niced
/// max, so a rung the nice admits comes back.
pub(super) fn niced_rail_domain(ax_y: &AxisStyle, domain_max: f64) -> (f64, Vec<f64>) {
    let max = if ax_y.scale.get("nice").and_then(Value::as_bool) != Some(false) {
        crate::families::cartesian::d3_nice_domain(0.0, domain_max, 10).1
    } else {
        domain_max
    };
    if ax_y.tick_values.is_empty() {
        // No baked ladder: an authored count draws that many d3 ticks; an
        // uncounted axis takes this provisional ladder until the arm
        // re-derives it at the fitted extent (`unbaked_rungs`).
        let count = ax_y.ticks.count.map(|c| c as usize).unwrap_or(10);
        return (max, crate::families::cartesian::unbaked_ticks(ax_y, 0.0, max, count));
    }
    let eps = max * 1e-9;
    (max, ax_y.tick_values.iter().copied().filter(|&tv| tv <= max + eps).collect())
}

/// The horizontal stacked bar's endpoint-label rail as production composes
/// it: a vconcat (`translate.py` `_wrap_vconcat_label_rail`) of a
/// `endpoint_labels.height`-tall text pane above the chart pane, root
/// `spacing` = `label_offset`, both panes sharing the x scale and shrunk
/// by the probe overshoot (`_correct_concat_overshoot`) — the chart pane
/// gives up the vertical chrome, both give up the horizontal.
///
/// Vega's concat layout places the chart pane at the rail's bottom +
/// spacing − the pane's own top ink, and the rail's text at the pane's
/// mid-height with `baseline: bottom` — drawn at `round(−0.21·fs)` above
/// that anchor (vega-scenegraph's text offset) with ink from
/// `round(0.8·fs)` above the drawn baseline, so a tall enough font
/// overhangs the pane top (probe-pinned: 1px at 11px in a 20px rail, 3px
/// at 12–13px, 4px at 14px; none at ≤10px or in a 24px rail). The rail
/// mark carries `series_label`'s own font props — `translate.py`'s
/// `_wrap_vconcat_label_rail` stamps `label_mark_font_props` onto the
/// vconcat text mark, same as the vertical rail's pane already does — so
/// `fs` here is `series_label.font_size`, not the board's text-mark
/// default (an authored value-label size is gated to the legacy margins
/// in `use_top_rail` and never reaches this arm).
pub(super) struct TopRail {
    pub(super) height: f64,
    pub(super) spacing: f64,
    /// Drawn baseline below the rail's top edge.
    pub(super) text_dy: f64,
    /// Text ink above the rail's top edge, if any.
    pub(super) overhang: f64,
}

impl TopRail {
    pub(super) fn new(style: &BarStyle) -> Self {
        let font_size = style.series_label.font_size;
        let el = &style.endpoint_labels;
        let text_dy = el.height / 2.0 + (-0.21 * font_size).round();
        let overhang = ((0.8 * font_size).round() - text_dy).max(0.0);
        TopRail { height: el.height, spacing: el.label_offset, text_dy, overhang }
    }
}

/// The rungs an unbaked, uncounted value axis draws once the plot extent
/// along it is known: Vega-Lite's default `tickCount = ceil(range/40)`,
/// as d3 ticks over the (niced) domain. `None` when the axis is baked or
/// authors its own count — nothing to re-derive. An independent facet scale
/// is never baked and its spec carries no `tickCount` (the theme's count only
/// seeds the resolver's baked ladder), so it re-derives whatever the count.
pub(super) fn unbaked_rungs(
    ax_y: &AxisStyle,
    normalize: bool,
    independent: bool,
    domain_min: f64,
    domain_max: f64,
    extent: f64,
) -> Option<Vec<f64>> {
    (!normalize && ax_y.tick_values.is_empty() && (independent || ax_y.ticks.count.is_none()))
        .then(|| {
            let count = vl_default_tick_count(extent);
            crate::families::cartesian::unbaked_ticks(ax_y, domain_min, domain_max, count)
        })
}

/// The value scale a base bar shares with an overlay layer. Vega-Lite merges
/// the two encodings into one scale that no longer carries the base's exact
/// `domainMin`/`domainMax` bounds, so its default `nice` turns on and rounds
/// BOTH ends (d3's own fixed count of 10, never the axis's `tickCount`) —
/// see the vertical arm's `column_line_combo_review`/`lollipop` probes. A
/// baked rung the nice now admits comes back (`niced_rail_domain`'s law) and
/// one it leaves outside drops (`validTicks`, see `value_domain_and_ticks`).
/// Returns the niced `(low, high)` and the re-filtered ladder, if baked.
pub(super) fn nice_shared_scale(ax_y: &AxisStyle, floor: f64, max: f64) -> (f64, f64, Option<Vec<f64>>) {
    // d3-scale's `nice` rounds a descending pair as its ascending one, then restores the order.
    let (lo, hi) = d3rs::ticks::d3_nice(floor.min(max), floor.max(max), 10);
    let ticks = (!ax_y.tick_values.is_empty()).then(|| {
        let eps = hi * 1e-9;
        ax_y.tick_values
            .iter()
            .copied()
            .filter(|&tv| tv <= hi + eps && (lo == 0.0 || tv >= lo - eps))
            .collect()
    });
    if floor > max { (hi, lo, ticks) } else { (lo, hi, ticks) }
}

/// Compute value-axis domain max and baked ticks.
///
/// `stack` selects the domain source: `"zero"` uses the baked
/// `stacked_domain_max` (the shared per-category stacked total, headroom
/// already applied at resolve); `"normalize"` fixes `[0, 1]` with a quartile
/// tick ladder; anything else (`"none"`, or a stack with no color channel)
/// falls back to the raw single-value max, as before.
/// An authored `scale.continuous.domain` short-circuits every source above
/// (`authored_domain_and_ticks`).
/// Returns `(domain_min, domain_max, ticks)`. `domain_min` is 0.0
/// unless the zero-anchored floor law, a baked `domain_min` (which wins over
/// the ladder's bottom rung, `apply_domain_headroom_bounds`), the stacked
/// negative-floor law below, or an authored domain pins it negative; an
/// authored `scale.nice` then rounds a negative floor with the top.
/// Every drawing loop of
/// both arms (single-series, grouped by sign sub-layer, and stacked
/// cumulative) draws from the zero rule, so a negative `domain_min` reaches
/// their `LinearScale` as the rule's own position (see
/// `render_horizontal`/`render_vertical_panel`).
pub(super) fn value_domain_and_ticks(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    val_field: &str,
    ax_y: &AxisStyle,
    stack: &str,
    stacked_domain_max: Option<f64>,
    // The unauthored `nice` default genuinely differs by orientation for
    // the non-stacked, unbaked (no rung) shape — production's own two
    // emitters disagree on purpose. Vertical's `bar_zero` branch
    // (`emitters/bar.py:1083-1096`) always emits an explicit `{"nice":
    // False, "zero": True}` when `zero_anchor_pinned_floor` finds no rung
    // (empty `tick_values`) — reachable both for an independent-scale
    // facet panel's own per-panel ladder and for a single-chart unbaked
    // right-axis bar whenever `ax_y.ticks.count` is `None`
    // (`compile/resolve/chart/_domain.py` ~405 leaves ticks unbaked
    // there too). Horizontal's own branch (`emitters/bar.py:1517-1533`, its own
    // comment: "No `nice: False` companion here, unlike y_zero_scale and
    // the vertical branch... its domain has always been nice-rounded")
    // never pins a ladder-less floor at all, so Vega-Lite's own "nice a
    // linear scale with no domain bound" default stays live — pinned by
    // `unbaked_value_axis_takes_vega_lite_defaults`
    // (`quick-guide_7/revenue_by_month`'s own real shape).
    vertical: bool,
    // A ranged bar's `y_start` column: its values join the axis's data extent
    // and the resolver's baked floor becomes the domain's low end.
    start_field: Option<&str>,
    // A centered stack with a visible rail has its main pane's domain
    // overwritten with `[0, max total]` (`endpoint_labels.py`), so an authored
    // pair never reaches Vega and the scale is not niced.
    rail_pins_domain: bool,
    // A centered stack's labels sit on the zero stack (`labels_on_zero_stack`).
    zero_stack_labels: bool,
) -> Result<(f64, f64, Vec<f64>), String> {
    // A centered stack shares the zero stack's extent and ladder; only its
    // segments' offset differs, and they pile magnitudes up from 0, so no
    // negative running sum reaches below it.
    let centered = stack == "center";
    let stack = if centered { "zero" } else { stack };
    if stack == "normalize" {
        return Ok((0.0, 1.0, NORMALIZE_QUARTILE_TICKS.to_vec()));
    }
    if rail_pins_domain && ax_y.authored_continuous_domain().is_some() {
        // The pin is `[0, max total]` on a scale an authored domain keeps
        // un-niced: the tallest stack ends flush at the plot top.
        let max = tallest_total(&category_totals(rows, cat_field, val_field));
        let ticks = if ax_y.tick_values.is_empty() {
            let count = ax_y.ticks.count.map(|c| c as usize).unwrap_or(10);
            crate::families::cartesian::unbaked_ticks(ax_y, 0.0, max, count)
        } else {
            ax_y.tick_values.iter().copied().filter(|&tv| tv <= max * (1.0 + 1e-9)).collect()
        };
        return Ok((0.0, max, ticks));
    }
    if let Some((lo, hi)) = ax_y.authored_continuous_domain() {
        let (floor, top, ticks) = authored_domain_and_ticks(ax_y, vertical || start_field.is_some(), lo, hi);
        return Ok((floor, top, ticks));
    }

    let raw_max = rows
        .iter()
        .flat_map(|row| [Some(val_field), start_field].into_iter().flatten().filter_map(|f| row_num(row, f)))
        .fold(if start_field.is_some() { f64::NEG_INFINITY } else { 0.0 }, f64::max);
    // No baked ladder: production emits no `domainMax` for a non-stacked
    // axis (`apply_domain_headroom_bounds` with a `None` domain_max), so
    // Vega's domain is the data max itself — the headroom only ever reaches
    // Vega inside a baked `domain_max`.
    let unbaked = ax_y.tick_values.is_empty();
    let domain_max = if stack == "zero" {
        // `emitters/bar.py`: a stack with no baked total takes its domain top
        // from the baked ladder's last rung.
        stacked_domain_max.or(ax_y.tick_values.last().copied()).ok_or(
            "bar: stack=\"zero\" with no baked stacked_domain_max — \
             resolve() should always supply one for a real chart",
        )?
    } else if let Some(dm) = ax_y.domain_max {
        dm
    } else if unbaked {
        raw_max
    } else {
        let headroom = ax_y.scale.get("headroom").and_then(Value::as_f64).unwrap_or(0.0);
        raw_max * (1.0 + headroom)
    };

    // An authored `scale.nice` reaches Vega as `scale.nice: true`, which
    // nices the domain AFTER the domainMax override — the baked headroomed
    // max rounds up to the d3 tick step ([0, 81436.32] → [0, 90000] on
    // charts/bar-charts_6). Unauthored, the two orientations genuinely
    // disagree for the non-stacked, unbaked (no rung) shape — see
    // `value_domain_and_ticks`'s own `vertical` param doc comment for both
    // citations. A baked axis (`!unbaked`) or a baked `domainMax`
    // (`bound_emitted`) already switches Vega-Lite's own "nice only with no
    // domain bound" default off on BOTH orientations, so the vertical/
    // horizontal split below only changes the answer for the one
    // previously-unreachable cell (`unbaked && !bound_emitted`, vertical).
    let bound_emitted = stack == "zero" || ax_y.domain_max.is_some() || ax_y.domain_min.is_some();
    let nice = match ax_y.scale.get("nice").and_then(Value::as_bool) {
        Some(authored) => authored,
        // `bar_zero` pins `nice: false` on a vertical bar that grows from 0; a ranged bar's
        // `y_zero_scale` leaves Vega-Lite's default.
        None if vertical && start_field.is_none() => false,
        None => unbaked && !bound_emitted,
    };
    // Vega nices both ends of a stacked extent that holds a negative running
    // sum (production paints a −400 floor over a −300 sum).
    let stack_floor = if stack == "zero" && (!centered || zero_stack_labels) {
        stack_reach(rows, cat_field, val_field).0
    } else if stack == "none" && unbaked && start_field.is_none() {
        // An unbaked axis has no ladder to pin a floor from: Vega's `zero: true`
        // extent is the data's own, which a negative value pulls below 0, unless
        // resolve baked a headroomed floor (an all-negative measure).
        ax_y.domain_min.filter(|floor| *floor < 0.0).unwrap_or_else(|| {
            rows.iter().filter_map(|row| row_num(row, val_field)).fold(0.0, f64::min)
        })
    } else {
        0.0
    };
    let un_niced_max = domain_max;
    // A ranged bar with no baked floor fits the axis to both ends' extent, which
    // `scale.nice` then rounds from the fitted floor, not from 0.
    let fitted_floor = start_field
        .filter(|_| stack == "none" && ax_y.domain_min.is_none() && !ax_y.is_zero_anchored())
        .map(|start| {
            rows.iter()
                .flat_map(|row| [val_field, start].into_iter().filter_map(|f| row_num(row, f)))
                .fold(f64::INFINITY, f64::min)
        });
    let (nice_floor, domain_max) = if let (true, Some(floor)) = (nice, fitted_floor) {
        crate::families::cartesian::d3_nice_domain(floor, domain_max, 10)
    } else if nice && stack_floor < 0.0 {
        let (lo, hi) = crate::families::cartesian::d3_nice_domain(stack_floor, domain_max, 10);
        (lo, hi)
    } else if nice {
        (stack_floor, crate::families::cartesian::d3_nice_domain(0.0, domain_max, 10).1)
    } else {
        (stack_floor, domain_max)
    };

    let ticks: Vec<f64> = if !unbaked {
        let eps = domain_max * 1e-9;
        ax_y.tick_values
            .iter()
            .copied()
            .filter(|&tv| tv <= domain_max + eps)
            .collect()
    } else {
        // Vega draws d3 ticks at the axis's `tickCount`: the authored count,
        // else Vega-Lite's `ceil(range/40)`, which needs the fitted plot
        // extent — the arms re-derive the rungs from it (`unbaked_rungs`);
        // this provisional ladder only seeds the first fit pass.
        let count = ax_y.ticks.count.map(|c| c as usize).unwrap_or(10);
        crate::families::cartesian::unbaked_ticks(ax_y, nice_floor, domain_max, count)
    };

    // Zero-anchored negative floor (`tick_values.py::zero_anchor_domain_floor`,
    // `emitters/bar.py`'s horizontal-arm `floor_ticks[0]` bake / the
    // vertical arm's `bar_zero` branch — the identical law on both
    // orientations): the domain floor is the COMPUTED ladder's first rung,
    // never an authored `scale.values` list (a stated set of tick
    // positions, not a domain-bounds guarantee — an authored ladder's
    // first entry could sit above the data minimum and silently clip it).
    // `ticks` here is already that computed ladder whenever the axis
    // carries a baked one; an unbaked axis keeps the legacy 0.0 floor
    // (no corpus case needs a negative floor on an uncounted axis).
    // Production only pins this floor when the scale's `zero` is not
    // `False` (`emitters/bar.py:1297`'s `x_enc["scale"].get("zero") is not
    // False` guard) — a non-stacked `zero: false` (zoomed) axis instead
    // takes the ZOOMED `ay.domain_min` `apply_domain_headroom_bounds`
    // already baked, unconditionally, ahead of the floor pin. No corpus
    // case authors `axis_y.scale.continuous.zero: false` on a horizontal
    // bar; refuse rather than apply the zero-anchored law outside its
    // stated precondition.
    let authored_ladder = ax_y.scale.get("values").is_some_and(|v| !v.is_null());
    let neg_floor = nice_floor;
    let domain_min = if let (Some(_), Some(baked_floor)) = (start_field, ax_y.domain_min) {
        // A ranged bar's axis fits both ends: `apply_domain_headroom_bounds`
        // bakes the headroom-expanded data floor (`y_zero_scale` returns
        // `{"zero": False}` unless the axis is zero-anchored, which bakes
        // no floor and takes the ladder-floor arms below).
        baked_floor
    } else if stack == "none" && (ax_y.is_zero_false() || start_field.is_some() && !ax_y.is_zero_anchored()) {
        match (ax_y.domain_min, start_field) {
            (Some(baked), _) => baked,
            // A ranged bar with no baked floor: Vega fits the axis to the data
            // extent of both ends.
            (None, Some(_)) if nice => nice_floor,
            (None, Some(start)) => rows
                .iter()
                .flat_map(|row| [val_field, start].into_iter().filter_map(|f| row_num(row, f)))
                .fold(f64::INFINITY, f64::min),
            (None, None) => {
                return Err("bar: axis_y.scale.continuous.zero: false on a bar with no y_start is a payload \
                     compile rejects"
                    .to_string())
            }
        }
    } else if (stack == "zero" && (!ax_y.is_zero_anchored() || unbaked)) || (stack == "none" && unbaked && neg_floor < 0.0) {
        // Vega's stackZero law (`vega-transforms/Stack.js`): a stacked axis
        // with no pinned floor fits it to the DATA's own negative extent —
        // the most negative per-category running sum — no headroom, and niced
        // only on an authored `scale.nice` (production emits neither a
        // `domainMin` nor a nice hint for this shape; `y_zero_scale` only pins `domainMin` when
        // `scale.continuous.zero` is literally `true` AND a ladder was baked
        // to take the floor from). An unbaked axis has no ladder to pin from,
        // so `zero: true` only adds 0 to an extent that already holds it.
        neg_floor
    } else if let Some(baked_floor) = ax_y.domain_min.filter(|_| stack == "none" && !unbaked) {
        // `apply_domain_headroom_bounds` lays the baked floor over the
        // ladder-derived `domainMin` (`emitters/bar.py:1204`): the ladder's
        // bottom rung is the nice-rounded far edge, not the domain.
        baked_floor
    } else if !unbaked && !authored_ladder {
        ticks.first().copied().unwrap_or(0.0)
    } else {
        0.0
    };
    // Vega's stackZero law only reaches a floor that holds the data: a
    // zero-anchored ladder whose first rung sits above a category's
    // negative running sum would clamp that segment to the baseline and
    // drop it under the arms' `< 0.5` guard — a chart of different data,
    // drawn confidently. Refuse by name.
    if stack == "zero" && neg_floor < domain_min - domain_max * 1e-9 {
        return Err(format!(
            "bar: stack=\"zero\" cannot render a negative running sum ({neg_floor}) below the \
             zero-anchored ladder floor ({domain_min}) — the segment would clamp to the \
             baseline and vanish"
        ));
    }
    // `scale.nice` runs after `domainMin`/`zero`, so a floor below 0 is niced
    // with the top (`configureDomain`, `vega.js:21556-21559`): the baked ladder
    // is then filtered by the niced pair.
    let (domain_min, domain_max, ticks) = if nice && vertical && stack == "none" && !unbaked && domain_min < 0.0 {
        let (nice_min, nice_max) = crate::families::cartesian::d3_nice_domain(domain_min, un_niced_max, 10);
        let eps = nice_max.abs() * 1e-9;
        let ladder = ax_y.tick_values.iter().copied().filter(|&tv| tv <= nice_max + eps).collect();
        (nice_min, nice_max, ladder)
    } else {
        (domain_min, domain_max, ticks)
    };
    // Vega draws a declared tick only where the scale places it inside the
    // range (`validTicks`, `vega.js:14547-14574`, called from `AxisTicks`,
    // `vega.js:20966`): a baked rung below a floor that came from the data
    // (a stacked negative extent, a ranged bar's fitted axis) is dropped,
    // as one above the domain max is. Domain-space here: exact but for the
    // one-pixel slack that filter's `floor`/`ceil` of the range adds.
    let eps = domain_max * 1e-9;
    let ticks: Vec<f64> = ticks.into_iter().filter(|&tv| tv >= domain_min - eps).collect();

    Ok((domain_min, domain_max, ticks))
}

/// The measure domain an authored `scale.continuous.domain` pins, and the
/// baked ladder inside it. The authored pair reaches Vega verbatim
/// (`_emit_vertical`'s `authored_domain` branch emits `scale.domain`;
/// `_emit_horizontal` forwards it through `emit_resolved_scale_vl`), and
/// resolve leaves `domain_min`/`domain_max`/headroom unbaked whenever one is
/// authored — `nice` is the one term that still applies, only when the
/// author asked for it (`configureDomain`, `vega.js:21556-21559`, `nice(null)`
/// = d3's count 10). The axis draws the baked ladder's rungs that land inside
/// the domain (`validTicks`, `vega.js:14547-14572`); an axis with no baked
/// ladder takes d3's ticks over the domain at Vega-Lite's `tickCount`
/// (`unbaked_rungs` re-derives them once the extent is known).
///
/// A descending pair keeps its order and inverts the axis: the bars hang from
/// the zero rule at the plot top. A plain horizontal bar axis reaches 0
/// (`zero: true`), so a pair that excludes it is widened; a vertical or ranged one
/// keeps the authored pair (its scale carries `zero: false`) and the zero-based
/// bars run past the plot edge unclipped.
pub(super) fn authored_domain_and_ticks(ax_y: &AxisStyle, keeps_pair: bool, lo: f64, hi: f64) -> (f64, f64, Vec<f64>) {
    let (lo, hi) = crate::families::cartesian::authored_domain_pair(ax_y, lo, hi);
    let (axis_lo, axis_hi) = if keeps_pair {
        (lo, hi)
    } else if lo < hi {
        (lo.min(0.0), hi.max(0.0))
    } else {
        (lo.max(0.0), hi.min(0.0))
    };
    let ticks = if ax_y.tick_values.is_empty() {
        crate::families::cartesian::unbaked_ticks(ax_y, axis_lo, axis_hi, ax_y.ticks.count.map_or(10, |c| c as usize))
    } else {
        crate::families::cartesian::ladder_within_domain(&ax_y.tick_values, lo, hi)
    };
    (axis_lo, axis_hi, ticks)
}

/// An authored pair that pins one value gives the value scale no extent: every
/// bar's two ends land on the same pixel, and production raises
/// `ERR-CHART-PAINTED-NO-MARKS` once a bar has a nonzero value to draw.
pub(super) fn collapsed_authored_domain(ax_y: &AxisStyle, rows: &[serde_json::Map<String, Value>], val_field: &str) -> bool {
    ax_y.authored_continuous_domain().is_some_and(|(lo, hi)| {
        let (lo, hi) = crate::families::cartesian::authored_domain_pair(ax_y, lo, hi);
        lo == hi
    }) && rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v != 0.0))
}

/// The `(lowest, highest)` end a layer's per-category zero stack reaches
/// (`stackZero`: positives pile up from 0, negatives down from it).
pub(super) fn stack_reach(rows: &[serde_json::Map<String, Value>], cat_field: &str, val_field: &str) -> (f64, f64) {
    let mut piles: HashMap<(String, bool), f64> = HashMap::new();
    for row in rows {
        if let Some(v) = row_num(row, val_field) {
            *piles.entry((row_str(row, cat_field), v < 0.0)).or_insert(0.0) += v;
        }
    }
    piles.into_iter().fold((0.0, 0.0), |(lo, hi), ((_, negative), sum)| {
        if negative { (lo.min(sum), hi) } else { (lo, hi.max(sum)) }
    })
}

/// Port of `_stacked_midpoints` (features/endpoint_labels.py), default
/// stack order only (this renderer refuses authored stack orders at the
/// dispatch gate). Each series anchors at its OWN last column (in rendered
/// domain order) carrying a value; the cumulative offset is column-local —
/// a series stacks on whichever series precede it in that same column — so
/// every anchor column is snapshotted independently. A series with no value
/// anywhere anchors zero-height at the domain's last column (its label must
/// still appear: the rail replaces the legend). Normalize divides by the
/// anchor column's OWN total, the share VL renders for that column.
pub(super) fn stacked_rail_anchors(
    cat_domain: &[String],
    stack_order: &[String],
    data_map: &HashMap<(String, String), f64>,
    normalize: bool,
    // The tallest total of a `center` stack, which floats every category to its middle.
    center_max: Option<f64>,
) -> Result<Vec<(String, f64)>, String> {
    // A negative measure under endpoint labels is refused ahead of the render
    // (`ERR_ENDPOINT_LABELS_NEGATIVE_STACK`), so every anchor here is non-negative.
    if cat_domain.is_empty() {
        return Ok(Vec::new());
    }
    Ok(stack_order
        .iter()
        .map(|series| {
            let anchor = cat_domain
                .iter()
                .rposition(|c| data_map.contains_key(&(c.clone(), series.clone())))
                .unwrap_or(cat_domain.len() - 1);
            let cat = &cat_domain[anchor];
            let value_of = |s2: &String| {
                data_map.get(&(cat.clone(), s2.clone())).copied().unwrap_or(0.0)
            };
            let total: f64 = stack_order.iter().map(value_of).sum();
            let mut cum = 0.0_f64;
            let mut own = 0.0_f64;
            for s2 in stack_order {
                let v = value_of(s2);
                if s2 == series {
                    own = v;
                    break;
                }
                cum += v;
            }
            let mut mid = cum + own / 2.0;
            if let Some(max) = center_max {
                mid += crate::common::center_offset(max, total);
            }
            if normalize && total > 0.0 {
                mid /= total;
            }
            (series.clone(), mid)
        })
        .collect())
}

/// A grouped bar's rail anchors (`endpoint_labels.py`'s non-stacked branch): each series at its own last
/// non-null value, in the order the series first appear, and the bounds the cascade packs into: the authored
/// domain, else the raw data extent (`_y_domain`).
pub(super) fn grouped_rail_anchors(
    rows: &[serde_json::Map<String, Value>],
    cat_domain: &[String],
    cat_field: &str,
    val_field: &str,
    color_field: &str,
    authored: Option<(f64, f64)>,
) -> Result<(Vec<(String, f64)>, (f64, f64)), String> {
    // The walk-back reads the axis's own order, not raw x.
    let rank: HashMap<&str, usize> = cat_domain.iter().enumerate().map(|(i, x)| (x.as_str(), i)).collect();
    let ranked: Vec<serde_json::Map<String, Value>> = rows
        .iter()
        .filter_map(|row| {
            let i = *rank.get(row_str(row, cat_field).as_str())?;
            let mut ranked = row.clone();
            ranked.insert(cat_field.to_string(), Value::from(i));
            Some(ranked)
        })
        .collect();
    let last = crate::common::last_nonnull_value_per_series(&ranked, cat_field, val_field, color_field)?;
    let mut seen = HashSet::new();
    let anchors = rows
        .iter()
        .map(|row| row_str(row, color_field))
        .filter(|series| seen.insert(series.clone()))
        .filter_map(|series| last.get(&series).map(|v| (series, *v)))
        .collect();
    let values = rows.iter().filter_map(|row| row_num(row, val_field));
    let extent = values.clone().reduce(f64::min).zip(values.reduce(f64::max));
    let bounds = match (authored, extent) {
        (Some((lo, hi)), _) if hi >= lo => (lo, hi),
        (_, Some(extent)) => extent,
        (_, None) => (0.0, 1.0),
    };
    Ok((anchors, bounds))
}

/// Sum of `val_field` across all series, per category — the stacked total a
/// `stack = "normalize"` segment divides into — the same group total the
/// production tooltip bakes per category.
pub(super) fn category_totals(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    val_field: &str,
) -> HashMap<String, f64> {
    let mut totals: HashMap<String, f64> = HashMap::new();
    for row in rows {
        if let Some(v) = row_num(row, val_field) {
            *totals.entry(row_str(row, cat_field)).or_insert(0.0) += v;
        }
    }
    totals
}

/// `_stacked_y_domain`'s `y_domain_max` for a `zero`/`center` stack: the
/// tallest category total, with no headroom and no nice. The one value the
/// domain pin, the rail midpoints and the centered segments all read.
pub(super) fn tallest_total(totals: &HashMap<String, f64>) -> f64 {
    totals.values().copied().fold(0.0, f64::max)
}

/// Vega's `center` stack (`stackCenter`): each category's pile of `|v|` floats to
/// the middle of the tallest. A chart holding both signs with a corner radius
/// splits into a `>= 0` and a `< 0` layer (`_layers.py::emit_bar_layer`); each
/// layer is a stack of its own, so each centers on its own tallest pile. A
/// `middle` value label is no bar layer: its stack covers both signs at once.
#[derive(Clone, Copy)]
pub(crate) struct CenterStack {
    pub(super) tallest: [f64; 2],
    pub(super) split: bool,
}

impl CenterStack {
    /// Whether production splits the chart's bars by sign (`sign_layers`).
    pub(super) fn splits(radius: Option<f64>, rows: &[serde_json::Map<String, Value>], val_field: &str) -> bool {
        let signed = |keep: fn(f64) -> bool| rows.iter().any(|row| row_num(row, val_field).is_some_and(keep));
        radius.is_some() && signed(|v| v > 0.0) && signed(|v| v < 0.0)
    }

    pub(super) fn layer(&self, v: f64) -> usize {
        usize::from(self.split && v < 0.0)
    }

    /// The tallest pile per layer across `groups`, each group the rows of one
    /// panel (a facet's stack is computed across every panel).
    pub(super) fn over<'a>(
        groups: impl IntoIterator<Item = &'a [serde_json::Map<String, Value>]>,
        cat_field: &str,
        val_field: &str,
        split: bool,
    ) -> Self {
        let mut stack = Self { tallest: [0.0; 2], split };
        for rows in groups {
            let mut piles: HashMap<(String, usize), f64> = HashMap::new();
            for row in rows {
                if let Some(v) = row_num(row, val_field) {
                    *piles.entry((row_str(row, cat_field), stack.layer(v))).or_insert(0.0) += v.abs();
                }
            }
            for ((_, layer), pile) in piles {
                stack.tallest[layer] = stack.tallest[layer].max(pile);
            }
        }
        stack
    }
}

/// The spans a stack's value labels anchor on when they are not the bars' own.
/// A centered stack's `middle` label sits on the both-signs stack, and a
/// non-`middle` label of a centered or normalized stack sits on the raw zero
/// stack (`labels_on_zero_stack`; over a `[0, 1]` scale that puts it far past
/// the plot).
pub(super) fn stack_label_spans(
    r: &BarResolved,
    // The both-signs stack of every row a `middle` label centers on.
    label_stack: Option<&CenterStack>,
    cat_domain: &[String],
    stack_order: &[String],
    data_map: &HashMap<(String, String), f64>,
    totals: &HashMap<String, f64>,
) -> Option<SegmentSpans> {
    let labels = r.style.mark.labels.as_ref().filter(|l| l.visible)?;
    let middle = label_position(labels, true) == "middle";
    let order = if stack_order.is_empty() { &[String::new()][..] } else { stack_order };
    match r.stack.as_str() {
        "center" => {
            let centered = middle.then_some(label_stack).flatten();
            Some(stack_spans(cat_domain, order, data_map, false, totals, centered))
        }
        "normalize" if !middle => Some(stack_spans(cat_domain, order, data_map, false, totals, None)),
        _ => None,
    }
}

/// Separates a series from the tier of a repeated row: on a time axis two rows of one `(bucket, series)` stack
/// over each other, the first row at tier 0 and each later one a tier up.
pub(super) const TIER_SEP: char = '\u{1d}';

pub(super) fn tier_name(series: &str, tier: usize) -> String {
    if tier == 0 { series.to_string() } else { format!("{series}{TIER_SEP}{tier}") }
}

pub(super) fn is_tier(series: &str) -> bool {
    series.contains(TIER_SEP)
}

/// The series a tier segment belongs to.
pub(super) fn base_series(series: &str) -> &str {
    series.split(TIER_SEP).next().expect("a split yields a part")
}

/// A category's series in the order production paints them: its `>= 0` layer, then its `< 0` one, which a
/// normalized stack lays over the first.
pub(super) fn layered_by_sign<'a>(
    order: &'a [String],
    cat: &'a str,
    data_map: &'a HashMap<(String, String), f64>,
) -> impl Iterator<Item = &'a String> {
    let negative = move |series: &&String| data_map.get(&(cat.to_string(), (*series).clone())).is_some_and(|v| *v < 0.0);
    order.iter().filter(move |s| !negative(s)).chain(order.iter().filter(negative))
}

/// The outermost segment of each sign layer of `cat`'s stack, `(>= 0, < 0)`: the only ones Vega's clip rounds
/// (`cornerRadiusEnd` on the layer's group), so every join inside a stack stays square.
pub(super) fn outer_segments<'a>(
    order: &'a [String],
    cat: &'a str,
    data_map: &'a HashMap<(String, String), f64>,
) -> (Option<&'a String>, Option<&'a String>) {
    let (mut positive, mut negative) = (None, None);
    for series in layered_by_sign(order, cat, data_map).filter(|s| data_map.contains_key(&(cat.to_string(), (*s).clone()))) {
        if data_map[&(cat.to_string(), series.clone())] < 0.0 {
            negative = Some(series);
        } else {
            positive = Some(series);
        }
    }
    (positive, negative)
}

/// How many tiers above the first each series of `data_map` has.
pub(super) fn tier_depths(data_map: &HashMap<(String, String), f64>) -> HashMap<String, usize> {
    let mut depths: HashMap<String, usize> = HashMap::new();
    for (_, series) in data_map.keys().filter(|(_, series)| is_tier(series)) {
        let tier = series.split(TIER_SEP).nth(1).and_then(|t| t.parse().ok()).expect("a tier name carries its tier");
        let depth = depths.entry(base_series(series).to_string()).or_insert(0);
        *depth = (*depth).max(tier);
    }
    depths
}

/// `order` with each series followed by the tiers stacked over it, the order production stacks a bucket's rows in.
pub(super) fn with_tiers(order: &[String], depths: &HashMap<String, usize>) -> Vec<String> {
    order.iter().flat_map(|series| (0..=depths.get(series).copied().unwrap_or(0)).map(|tier| tier_name(series, tier))).collect()
}

/// Each row's `(cat, series)` key, in row order. A repeated key is a row stacked over the earlier ones, kept under
/// its tier's name; a ranged bar (`tiered: false`) never stacks, since Vega-Lite stacks nothing that has a `y2`.
pub(super) fn row_keys(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    color_field: Option<&str>,
    tiered: bool,
) -> Vec<(String, String)> {
    let mut seen: HashMap<(String, String), usize> = HashMap::new();
    rows.iter()
        .map(|row| {
            let cat = row_str(row, cat_field);
            let base = color_field.map(|cf| row_str(row, cf)).unwrap_or_default();
            if !tiered {
                return (cat, base);
            }
            let tier = seen.entry((cat.clone(), base.clone())).or_insert(0);
            let series = tier_name(&base, *tier);
            *tier += 1;
            (cat, series)
        })
        .collect()
}

/// Build (cat, series) → value lookup map. A repeated `(cat, series)` is a row stacked over the earlier ones, kept
/// under its tier's name.
pub(super) fn build_data_map(
    rows: &[serde_json::Map<String, Value>],
    cat_field: &str,
    val_field: &str,
    color_field: Option<&str>,
    tiered: bool,
) -> Result<HashMap<(String, String), f64>, String> {
    let mut data_map: HashMap<(String, String), f64> = HashMap::new();
    for (row, key) in rows.iter().zip(row_keys(rows, cat_field, color_field, tiered)) {
        // A null measure draws no mark: it leaves its category and series in
        // the domains and the cell empty, like a row the query never returned.
        let Some(val) = row_num(row, val_field) else { continue };
        data_map.insert(key, val);
    }
    Ok(data_map)
}

/// The dates a time axis's tooltips name: a row floored to its bucket keeps the date it carried, which
/// production's header formats and whose stack its total sums.
pub(super) struct RawX {
    pub(super) header: HashMap<(String, String), String>,
    pub(super) total: HashMap<String, f64>,
}

impl RawX {
    pub(super) fn of(rows: &[serde_json::Map<String, Value>], cat_field: &str, val_field: &str, color_field: Option<&str>, tiered: bool) -> Self {
        let mut raw = Self { header: HashMap::new(), total: HashMap::new() };
        for (row, key) in rows.iter().zip(row_keys(rows, cat_field, color_field, tiered)) {
            let date = super::time_x::tooltip_date(row, cat_field);
            *raw.total.entry(date.clone()).or_insert(0.0) += row_num(row, val_field).unwrap_or(0.0);
            if date != key.0 {
                raw.header.insert(key, date);
            }
        }
        raw
    }

    /// The date of the datum at `(cat, series)`.
    pub(super) fn date<'a>(&'a self, cat: &'a str, series: &str) -> &'a str {
        self.header.get(&(cat.to_string(), series.to_string())).map_or(cat, String::as_str)
    }

    /// The stack total `Σ` of the datum at `(cat, series)`, over the rows sharing its date.
    pub(super) fn total(&self, cat: &str, series: &str) -> Result<(&'static str, f64), String> {
        let date = self.date(cat, series);
        let t = self.total.get(date).copied().ok_or_else(|| format!("bar: internal error -- no stacked total computed for {date:?}"))?;
        Ok(("Σ", t))
    }
}

/// The bars' value-space extent `(lowest end, highest end)`, shared by both
/// arms' fit closures and the facet ink: every bar grows from the zero rule
/// (zero-seeded), a stack's ends are its sign-split running sums, a normalize
/// stack ends at 1, and a ranged bar spans its two ends with no zero to seed
/// from.
#[allow(clippy::too_many_arguments)]
pub(super) fn bar_value_extent(
    rows: &[serde_json::Map<String, Value>],
    val_field: &str,
    y_start: Option<&str>,
    normalize: bool,
    is_stacked: bool,
    is_grouped: bool,
    data_map: &HashMap<(String, String), f64>,
    cat_domain: &[String],
    stack_order: &[String],
    color_domain: &[String],
    tier_depth: &HashMap<String, usize>,
    center: Option<&CenterStack>,
) -> (f64, f64) {
    if normalize {
        (0.0, 1.0)
    } else if let Some(center) = center {
        (0.0, center.tallest[0].max(center.tallest[1]))
    } else if let Some(start_field) = y_start {
        rows.iter()
            .flat_map(|row| [val_field, start_field].into_iter().filter_map(|f| row_num(row, f)))
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)))
    } else if is_stacked {
        cat_domain.iter().fold((0.0, 0.0), |(lo, hi), cat| {
            let (neg, pos) = stack_order
                .iter()
                .map(|s| data_map.get(&(cat.clone(), s.clone())).copied().unwrap_or(0.0))
                .fold((0.0, 0.0), |(n, p), v| if v < 0.0 { (n + v, p) } else { (n, p + v) });
            (lo.min(neg), hi.max(pos))
        })
    } else {
        let series: Vec<&str> =
            if is_grouped { color_domain.iter().map(String::as_str).collect() } else { vec![""] };
        // Rows of one cell stack over each other: the bar reaches their sum.
        cat_domain
            .iter()
            .flat_map(|cat| series.iter().map(move |s| (cat, *s)))
            .filter_map(|(cat, s)| {
                let tiers = (0..=tier_depth.get(s).copied().unwrap_or(0)).filter_map(|tier| data_map.get(&(cat.clone(), tier_name(s, tier))));
                tiers.copied().reduce(|total, v| total + v)
            })
            .fold((0.0, 0.0), |(lo, hi), v| (f64::min(lo, v), f64::max(hi, v)))
    }
}

// ── Once-per-chart state ─────────────────────────────────────────────────────
//
// Category/color domains, stack order, palette domain and legend
// construction: every one of these is computed from EVERY row this chart
// will ever draw, never per small-multiples panel (a future facet composite
// shares one `BarChartState`/`LegendState` across all its panels, the same
// way resolve's own `panel_axes` bake is shared). Both `render_vertical`
// and `render_horizontal` build them by calling the exact same domain/
// legend helpers (`build_cat_domain`, `color_domain_of`,
// `color_1to1_with_cat`, `stacked_legend_entries`,
// `grouped_legend_domain`, `legend_title_text`) they already called inline
// — this only bundles the calls and their outputs, so a later panel-drawing
// function can take the bundle as an explicit parameter instead of closing
// over a dozen scattered locals.

/// Once-per-chart category/color/stack/palette domains — `BarChartState`.
pub(super) struct BarChartState {
    pub(super) cat_domain: Vec<String>,
    pub(super) color_domain: Vec<String>,
    pub(super) stack_order: Vec<String>,
    pub(super) is_grouped: bool,
    pub(super) is_stacked: bool,
    pub(super) normalize: bool,
    /// Palette slot domain: `stack_order` unless the chart is a stacked bar
    /// whose color IS 1:1 with its category, in which case `color_domain`
    /// (#8118's own alphabetical-vs-baseline-first split — `color_1to1_
    /// with_cat`). `one_to_one`/grouped-band-padding (a SEPARATE, mutually
    /// exclusive predicate keyed off the same underlying check but requiring
    /// `!is_stacked`) stays a local in each render fn's own band-geometry
    /// section, same as before this struct — nothing downstream reads it
    /// off chart-global state yet.
    pub(super) palette_domain: Vec<String>,
}

/// Build `BarChartState` — same domain helpers `render_vertical`/
/// `render_horizontal` already called inline, now called once from a shared
/// site. `sorts_by_value_by_default` is `BarResolved::sorts_by_value_by_default`
/// (query-order vs value-descending default sort). `rows` are the chart's
/// whole dataset; `cat_rows` is where the category domain comes from (a narrowed
/// facet panel's own rows) and `color_one_to_one` a facet's verdict over every
/// panel (`None` reads it off `rows`).
pub(super) fn build_chart_domains(
    rows: &[serde_json::Map<String, Value>],
    cat_rows: &[serde_json::Map<String, Value>],
    color_one_to_one: Option<bool>,
    cat_field: &str,
    val_field: &str,
    color_field: Option<&str>,
    sort: Option<&ChartSort>,
    sorts_by_value_by_default: bool,
    stack: &str,
    stack_order_mode: Option<&str>,
    wide_measures: &[String],
    wide_series: &[String],
    chart_palette_unset: bool,
) -> Result<BarChartState, String> {
    let is_grouped = color_field.is_some();
    let is_stacked = is_grouped && stack != "none";
    let normalize = stack == "normalize";
    let order = cat_order(sort, sorts_by_value_by_default, val_field, stack);
    let cat_domain = build_cat_domain(cat_rows, cat_field, val_field, order)?;
    let color_domain = if !wide_measures.is_empty() {
        wide_series.to_vec()
    } else {
        color_field.map_or_else(Vec::new, |cf| color_domain_of(rows, cf))
    };
    let stack_order: Vec<String> = if is_stacked {
        crate::common::sorted_series_by_stack_order(
            &color_domain,
            rows,
            color_field.expect("is_stacked implies color"),
            val_field,
            stack_order_mode,
        )?
    } else {
        Vec::new()
    };
    // Palette slots: alphabetical color domain when color is 1:1 with the
    // category (see color_1to1_with_cat), baseline-first stack order otherwise.
    // A chart with no palette of its own sets no color domain: Vega-Lite's default sorts it.
    let palette_domain = if is_stacked
        && (chart_palette_unset
            || color_one_to_one.unwrap_or_else(|| {
                color_1to1_with_cat(rows, cat_field, color_field.expect("is_stacked implies color"))
            }))
    {
        color_domain.clone()
    } else {
        stack_order.clone()
    };
    Ok(BarChartState { cat_domain, color_domain, stack_order, is_grouped, is_stacked, normalize, palette_domain })
}

/// The shortest card that gives each of `n_categories` bands room for its label
/// (`min_height_for_horizontal_bar_categories`).
pub(super) fn horizontal_min_height(r: &BarResolved, n_categories: usize) -> Result<f64, String> {
    let ax_x = &r.style.axis_x;
    let font_size = ax_x.labels.font.size.ok_or("bar: axis_x.labels.font.size absent (theme required)")?;
    let pad = ax_x.labels.padding.ok_or("bar: axis_x.labels.padding absent (theme required)")?;
    let min_gap = ax_x.labels.min_gap.ok_or("bar: axis_x.labels.min_gap absent (theme required)")?;
    let mark_size = r.style.mark.size.unwrap_or_else(|| continuous_max_size(&r.style.mark));
    let band_step = mark_size.max(font_size + 2.0 * pad) + min_gap;
    Ok(CHROME_PX + n_categories as f64 * band_step)
}

/// A bar segment's place on the measure axis, shared by its drawn bar and its
/// value label.
#[derive(Clone, Copy)]
pub(super) struct SegmentSpan {
    /// A grouped bar's series slot within its band; `None` centers on the band.
    pub(super) slot: Option<usize>,
    /// The `[start, end]` in the measure's own units — the stack transform's
    /// bounds (a share under `normalize`), or `[0, value]`.
    pub(super) start: f64,
    pub(super) end: f64,
}

pub(super) type SegmentSpans = HashMap<(String, String), SegmentSpan>;

/// Every stacked segment's `[start, end]` (`stack` transform: a share under
/// `normalize`, floated by `center`), one per `(category, series)` with a
/// datum. A row Vega has no datum for draws nothing and owns no span.
pub(super) fn stack_spans(
    cat_domain: &[String],
    stack_order: &[String],
    data_map: &HashMap<(String, String), f64>,
    normalize: bool,
    totals: &HashMap<String, f64>,
    center: Option<&CenterStack>,
) -> SegmentSpans {
    let mut spans = SegmentSpans::new();
    for cat in cat_domain {
        // A synthesized calendar bucket has no rows, so no total: production's
        // null-measure column draws nothing there.
        if normalize && !totals.contains_key(cat) {
            continue;
        }
        let values = || stack_order.iter().filter_map(|series| data_map.get(&(cat.clone(), series.clone())).copied());
        // Under `normalize` production draws a mixed-sign column as two layers (`datum.v >= 0`, `< 0`), each
        // stacked up from the zero rule over its own sum of magnitudes.
        let (pos_total, neg_total) = if normalize {
            values().fold((0.0_f64, 0.0_f64), |(pos, neg), v| if v < 0.0 { (pos, neg - v) } else { (pos + v, neg) })
        } else {
            (0.0, 0.0)
        };
        let mut piles = [0.0_f64; 2];
        if let Some(center) = center {
            for layer in 0..2 {
                let pile = crate::common::abs_total(values().filter(|&v| center.layer(v) == layer));
                piles[layer] = crate::common::center_offset(center.tallest[layer], pile);
            }
        }
        let mut sums = crate::common::StackZero::new(0.0);
        let mut shares = [0.0_f64; 2];
        for series in stack_order {
            let Some(&raw_val) = data_map.get(&(cat.clone(), series.clone())) else { continue };
            let (start, end) = match center {
                Some(center) => {
                    let layer = center.layer(raw_val);
                    let start = piles[layer];
                    piles[layer] += raw_val.abs();
                    (start, piles[layer])
                }
                None if normalize => {
                    let (layer, total) = if raw_val < 0.0 { (1, neg_total) } else { (0, pos_total) };
                    let start = shares[layer];
                    shares[layer] += raw_val.abs() / total;
                    (start, shares[layer])
                }
                None => sums.push(raw_val),
            };
            spans.insert((cat.clone(), series.clone()), SegmentSpan { slot: None, start, end });
        }
    }
    spans
}

/// Every painted grouped bar's span, at its series slot: `[0, value]`, or a
/// ranged bar's `[start, end]`.
pub(super) fn grouped_spans(
    painted: &[(usize, String, String)],
    data_map: &HashMap<(String, String), f64>,
    ranged: Option<&RangedBars>,
) -> SegmentSpans {
    let mut spans = SegmentSpans::new();
    // A cell's later rows stack over its earlier ones.
    let mut piled: HashMap<(String, String), f64> = HashMap::new();
    for (si, series, cat) in painted {
        let key = (cat.clone(), series.clone());
        let Some(&value) = data_map.get(&key) else { continue };
        let (start, end) = match ranged {
            None => {
                let pile = piled.entry((cat.clone(), base_series(series).to_string())).or_insert(0.0);
                let start = *pile;
                *pile += value;
                (start, *pile)
            }
            Some(rb) => match rb.starts.get(&key) {
                Some(&start) => (start, value),
                None => continue,
            },
        };
        spans.insert(key, SegmentSpan { slot: Some(*si), start, end });
    }
    spans
}
