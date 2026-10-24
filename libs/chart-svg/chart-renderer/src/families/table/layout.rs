//! Every pixel decision of a table: column widths, the fit cascade, wrapping, the
//! title band, pagination and the markup of the cells.

use super::bind::build_cell_data;
use super::bind::cell_text_with_glyph;
use super::bind::coerce_numeric_cell;
use super::bind::column_font_lanes;
use super::bind::glyph_possible;
use super::bind::glyph_run;
use super::bind::is_date_like_cell;
use super::bind::is_magnitude_suffix;
use super::bind::is_numeric_cell;
use super::bind::is_plain_date_lane_cell;
use super::bind::is_summary_role;
use super::bind::is_swatch;
use super::bind::measured_cell_text;
use super::bind::numeric_value;
use super::bind::parse_spark_thresholds;
use super::bind::resolve_cell_glyph;
use super::bind::resolve_cell_link;
use super::bind::resolve_cell_style;
use super::bind::spark_series;
use super::bind::spark_threshold_color;
use super::bind::swatch_color;
use super::bind::unpadded_cell_text;
use super::bind::urlencode_plus;
use super::bind::wants_tabular_font;
use super::sanitize_color;
use super::svg_font_family;
use super::BarLabel;
use super::BoardFormats;
use super::Bound;
use super::Cell;
use super::ColMeta;
use super::ColumnInputs;
use super::Blank;
use super::MeasureFonts;
use super::PageItem;
use super::PaginatorTheme;
use super::PivotLevel;
use super::PyNum;
use super::Resolved;
use super::Rows;
use super::RowRole;
use super::SparkBarLabelTheme;
use super::SparkColumnsTheme;
use super::SparkConfig;
use super::SparkEmptyTheme;
use super::SparkLayout;
use super::SparkPaddingTheme;
use super::SparkSingleValueTheme;
use super::SparkTheme;
use super::TableBody;
use super::TableTheme;
use super::TableTitleStyle;
use super::TitleBand;
use super::TitleOnly;
use super::WidthHint;
use super::WrappedHeader;
use super::ANTI_DANGLE_MAX_OVERFLOW;
use super::ANTI_DANGLE_MAX_SQUEEZE;
use super::ANTI_DANGLE_MIN_ROW_H;
use super::COMPACT_DEMAND_CEILING;
use super::HEADER_RULE_GAP;
use super::PAGINATION_GROW_CAP;
use super::PAGINATOR_AUX_SLOT_RATIO;
use super::PAGINATOR_BOUNDARY_COUNT;
use super::PAGINATOR_ELLIPSIS;
use super::PAGINATOR_NEXT_CHEVRON;
use super::PAGINATOR_PREV_CHEVRON;
use super::PAGINATOR_SIBLING_COUNT;
use super::PROPORTIONAL_CAP;
use super::SPARK_LINE_STROKE_WIDTH;
use super::STATIC_MULTI_PAGE_MAX_PAGES;
use super::SWATCH_CELL_DEMAND;
use super::SWATCH_SIZE;
use super::VALID_FONT_WEIGHTS;
use super::width::natural_width;
use crate::codes;
use crate::common::card_box;
use crate::common::css_color;
use crate::common::Divider;
use crate::common::Font;
use crate::common::SUFFIX_GAP;
use crate::common::TAB_FONT;
use crate::common::TAB_STYLE;
use crate::diagnostic::py_str;
use crate::diagnostic::Diagnostic;
use crate::layout::Insets;
use crate::layout::IntrinsicWidth;
use crate::layout::Layout;
use crate::layout::Rect;
use crate::svg::num_full;
use crate::tree::Open;
use crate::tree::Tree;
use crate::TitleShift;
use d3rs::py_float::float_repr;
use d3rs::py_float::py_float_sum;
use measure::FontBook;
use measure::Measure;
use scene::Attrs;
use scene::Extent;
use scene::Node;
use serde::Deserialize;
use serde_json::json;
use serde_json::Value;
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::collections::BTreeSet;

/// Python's `sum(d.values())` over a column map.
pub(super) fn sum_values(map: &BTreeMap<String, f64>) -> f64 {
    py_float_sum(&map.values().copied().collect::<Vec<_>>())
}

/// Proportional shares with an iterative
/// 60%-of-budget cap; capped excess redistributes among the uncapped.
pub(super) fn proportional_allocate(
    demands: &BTreeMap<String, f64>,
    budget: f64,
) -> BTreeMap<String, f64> {
    if demands.len() <= 1 {
        return demands.keys().map(|k| (k.clone(), budget)).collect();
    }
    let cap = budget * PROPORTIONAL_CAP;
    let mut active = demands.clone();
    let mut fixed: BTreeMap<String, f64> = BTreeMap::new();
    let mut remaining = budget;
    while !active.is_empty() {
        let total = sum_values(&active);
        if total <= 0.0 {
            let per_col = remaining / active.len() as f64;
            for k in active.keys() {
                fixed.insert(k.clone(), per_col);
            }
            break;
        }
        let to_fix: Vec<String> = active
            .iter()
            .filter(|(_, &d)| (d / total) * remaining > cap)
            .map(|(k, _)| k.clone())
            .collect();
        if to_fix.is_empty() {
            for (k, &d) in &active {
                fixed.insert(k.clone(), (d / total) * remaining);
            }
            break;
        }
        for k in &to_fix {
            fixed.insert(k.clone(), cap);
            active.remove(k);
        }
        remaining -= py_float_sum(&vec![cap; to_fix.len()]);
    }
    fixed
}

/// Sorted walk clustering on the
/// cluster-extremes ratio (min/new >= threshold), snap each cluster to its
/// max, then one uniform rescale back to budget.
pub(super) fn cluster_and_equalize_widths(
    widths: &BTreeMap<String, f64>,
    budget: f64,
    threshold: f64,
) -> BTreeMap<String, f64> {
    if widths.len() <= 1 {
        return widths.clone();
    }
    let mut sorted: Vec<(&String, f64)> = widths.iter().map(|(k, &v)| (k, v)).collect();
    sorted.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());

    let mut clusters: Vec<Vec<&String>> = Vec::new();
    let mut cluster: Vec<&String> = Vec::new();
    let mut cluster_min = sorted[0].1;
    for (col, w) in &sorted {
        if !cluster.is_empty() && cluster_min / (w + 1e-9) < threshold {
            clusters.push(std::mem::take(&mut cluster));
            cluster_min = *w;
        }
        cluster.push(col);
    }
    clusters.push(cluster);

    let mut snapped: BTreeMap<String, f64> = BTreeMap::new();
    for cl in &clusters {
        let cluster_max = cl.iter().map(|c| widths[*c]).fold(0.0_f64, f64::max);
        for c in cl {
            snapped.insert((*c).clone(), cluster_max);
        }
    }
    let total = sum_values(&snapped);
    if total > 0.0 {
        let scale = budget / total;
        for v in snapped.values_mut() {
            *v *= scale;
        }
    }
    snapped
}

/// Proportional shares, iterative
/// max_width cap redistribution, cluster-equalize the uncapped, floors
/// (clamped at max_width) applied last.
pub(super) fn allocate_text_columns(
    text_demands: &BTreeMap<String, f64>,
    text_budget: f64,
    max_widths: &BTreeMap<String, Option<f64>>,
    word_floors: &BTreeMap<String, f64>,
    width_similarity_threshold: f64,
) -> BTreeMap<String, f64> {
    if text_demands.len() == 1 {
        let (col, _) = text_demands.iter().next().unwrap();
        let mw = max_widths.get(col).copied().flatten();
        let floor = word_floors.get(col).copied().unwrap_or(0.0);
        let effective_floor = mw.map_or(floor, |m| floor.min(m));
        let allocated = mw.map_or(text_budget, |m| text_budget.min(m));
        return BTreeMap::from([(col.clone(), allocated.max(effective_floor))]);
    }

    let mut to_allocate = proportional_allocate(text_demands, text_budget);
    let mut final_text: BTreeMap<String, f64> = BTreeMap::new();
    loop {
        let mut free: BTreeMap<String, f64> = BTreeMap::new();
        let mut leftover = 0.0_f64;
        for (col, &w) in &to_allocate {
            match max_widths.get(col).copied().flatten() {
                Some(mw) if w > mw => {
                    final_text.insert(col.clone(), mw);
                    leftover += w - mw;
                }
                _ => {
                    free.insert(col.clone(), w);
                }
            }
        }
        if leftover == 0.0 || free.is_empty() {
            final_text.extend(free);
            break;
        }
        let total_free = sum_values(&free);
        if total_free > 0.0 {
            let new_total = total_free + leftover;
            for v in free.values_mut() {
                *v = *v / total_free * new_total;
            }
        }
        to_allocate = free;
    }

    let non_capped: BTreeMap<String, f64> = final_text
        .iter()
        .filter(|(c, _)| max_widths.get(*c).copied().flatten().is_none())
        .map(|(c, &v)| (c.clone(), v))
        .collect();
    if non_capped.len() > 1 {
        let equalized = cluster_and_equalize_widths(
            &non_capped,
            sum_values(&non_capped),
            width_similarity_threshold,
        );
        final_text.extend(equalized);
    }

    for col in text_demands.keys() {
        let floor = word_floors.get(col).copied().unwrap_or(0.0);
        let mw = max_widths.get(col).copied().flatten();
        let effective_floor = mw.map_or(floor, |m| floor.min(m));
        let cur = final_text.get_mut(col).unwrap();
        if effective_floor > 0.0 && *cur < effective_floor {
            *cur = effective_floor;
        }
    }
    final_text
}

/// Cover each column's header gap
/// first (proportionally when the surplus can't cover them all), then
/// spread any residual proportionally to current width.
pub(super) fn grow_toward_header_width(
    widths: &mut BTreeMap<String, f64>,
    header_demands: &BTreeMap<String, f64>,
    surplus: f64,
) {
    let gaps: BTreeMap<String, f64> = widths
        .iter()
        .filter_map(|(col, &w)| {
            let target = header_demands.get(col).copied().unwrap_or(0.0);
            (target > w).then(|| (col.clone(), target - w))
        })
        .collect();
    let total_gap = sum_values(&gaps);
    if total_gap > surplus {
        for (col, gap) in &gaps {
            *widths.get_mut(col).unwrap() += surplus * (gap / total_gap);
        }
        return;
    }
    for (col, gap) in &gaps {
        *widths.get_mut(col).unwrap() += gap;
    }
    let residual = surplus - total_gap;
    if residual <= 0.1 {
        return;
    }
    let total = sum_values(widths);
    let keys: Vec<String> = widths.keys().cloned().collect();
    for col in keys {
        let share = widths[&col] / total;
        *widths.get_mut(&col).unwrap() += residual * share;
    }
}

/// An auto column's demand
/// raised to its word floor — folded in, not applied afterwards, because a
/// column narrower than its widest token splits that word mid-glyph. A
/// declared `max_width` is the hard bound; a floor never overrides it.
pub(super) fn auto_column_demand(demand: f64, floor: f64, cap: Option<f64>) -> f64 {
    let floor_bounded = cap.map_or(floor, |m| floor.min(m));
    demand.max(floor_bounded).max(1.0)
}

/// Compact when the demand fits
/// the ceiling OR the column owns a spark config (spark columns own their own
/// width regardless of measured demand).
pub(super) fn is_compact_column(demand: f64, spark: bool) -> bool {
    spark || demand <= COMPACT_DEMAND_CEILING
}

/// The least width that shows
/// an auto column in full — its demand raised to the word floor, compact
/// columns inflated by `content_headroom` when text columns exist, at least
/// the one-line header plus both cell paddings. Only a text column is capped
/// by `max_width`: the allocator pins a compact column at its demand
/// regardless of it.
pub(super) fn natural_auto_width(
    demand: f64,
    is_text: bool,
    text_columns_exist: bool,
    header_demand: f64,
    cap: Option<f64>,
    content_headroom: f64,
    cell_pad: f64,
) -> f64 {
    let factor = if text_columns_exist {
        1.0 + content_headroom
    } else {
        1.0
    };
    let width = if is_text { demand } else { demand * factor };
    let width = width.max(header_demand + 2.0 * cell_pad);
    match cap {
        Some(cap) if is_text => width.min(cap),
        _ => width,
    }
}

/// Port of `calculate_column_layout`'s smart phase (demands present,
/// multiple auto-columns). `pins` are resolved `width:` values; `caps` are
/// resolved `max_width:` values (None = uncapped). Returns final widths for
/// every column, pins included.
#[allow(clippy::too_many_arguments)]
pub(super) fn calculate_column_layout_port(
    columns: &[String],
    force_compact: &BTreeSet<String>,
    pins: &BTreeMap<String, f64>,
    caps: &BTreeMap<String, Option<f64>>,
    available_width: f64,
    demands: &BTreeMap<String, f64>,
    header_demands: &BTreeMap<String, f64>,
    word_floors: &BTreeMap<String, f64>,
    width_similarity_threshold: f64,
    content_headroom: f64,
    cell_pad: f64,
) -> BTreeMap<String, f64> {
    let mut col_widths: BTreeMap<String, f64> = pins.clone();
    let auto_columns: Vec<&String> = columns.iter().filter(|c| !pins.contains_key(*c)).collect();
    let explicit_total = sum_values(pins);
    let available_for_auto = (available_width - explicit_total).max(0.0);

    if auto_columns.is_empty() {
        return col_widths;
    }
    if auto_columns.len() == 1 {
        // Production's fallback branch: single auto-column takes the budget.
        col_widths.insert(auto_columns[0].clone(), available_for_auto);
        return col_widths;
    }

    let mut auto_demands: BTreeMap<String, f64> = BTreeMap::new();
    for col in &auto_columns {
        let floor = word_floors.get(*col).copied().unwrap_or(0.0);
        let cap = caps.get(*col).copied().flatten();
        let d = demands.get(*col).copied().unwrap_or(1.0);
        auto_demands.insert((*col).clone(), auto_column_demand(d, floor, cap));
    }
    let compact_keys: Vec<String> = auto_demands
        .iter()
        .filter(|(k, &d)| is_compact_column(d, force_compact.contains(*k)))
        .map(|(k, _)| k.clone())
        .collect();
    let text_keys: Vec<String> = auto_demands
        .keys()
        .filter(|k| !compact_keys.contains(k))
        .cloned()
        .collect();

    //when the budget covers every auto column's
    // natural width, each is allocated that width first.
    let natural: BTreeMap<String, f64> = auto_demands
        .iter()
        .map(|(col, &d)| {
            let width = natural_auto_width(
                d,
                text_keys.contains(col),
                !text_keys.is_empty(),
                header_demands.get(col).copied().unwrap_or(0.0),
                caps.get(col).copied().flatten(),
                content_headroom,
                cell_pad,
            );
            (col.clone(), width)
        })
        .collect();
    let has_room = available_for_auto >= sum_values(&natural);
    if has_room {
        auto_demands = natural;
    }

    let raw_compact_total = py_float_sum(
        &compact_keys
            .iter()
            .map(|c| auto_demands[c])
            .collect::<Vec<_>>(),
    );
    let headroom_factor = if has_room {
        1.0
    } else if !text_keys.is_empty() && raw_compact_total > 0.0 {
        let requested = 1.0 + content_headroom;
        let max_factor = available_for_auto / raw_compact_total;
        requested.min(max_factor.max(1.0))
    } else {
        1.0
    };
    let mut compact_pinned: BTreeMap<String, f64> = compact_keys
        .iter()
        .map(|c| (c.clone(), auto_demands[c] * headroom_factor))
        .collect();
    let compact_total = sum_values(&compact_pinned);
    let text_budget = (available_for_auto - compact_total).max(0.0);

    if text_keys.is_empty() {
        if compact_total >= available_for_auto {
            let scale = available_for_auto / compact_total;
            for (c, w) in &compact_pinned {
                col_widths.insert(c.clone(), w * scale);
            }
        } else {
            grow_toward_header_width(
                &mut compact_pinned,
                header_demands,
                available_for_auto - compact_total,
            );
            col_widths.extend(compact_pinned);
        }
    } else {
        let text_demands: BTreeMap<String, f64> = text_keys
            .iter()
            .map(|c| (c.clone(), auto_demands[c]))
            .collect();
        let max_widths: BTreeMap<String, Option<f64>> = text_keys
            .iter()
            .map(|c| (c.clone(), caps.get(c).copied().flatten()))
            .collect();
        let text_result = allocate_text_columns(
            &text_demands,
            text_budget,
            &max_widths,
            word_floors,
            width_similarity_threshold,
        );
        let text_used = sum_values(&text_result);
        col_widths.extend(text_result);

        let remainder = available_for_auto - compact_total - text_used;
        if remainder > 0.1 && !compact_pinned.is_empty() {
            grow_toward_header_width(&mut compact_pinned, header_demands, remainder);
        }
        col_widths.extend(compact_pinned);
    }
    col_widths
}

/// Production's `_compute_lane_positions` band arithmetic
/// Shared with `pie.rs`'s attached table: the
/// number tspan is end-anchored so its CENTER sits on the cell's content
/// midpoint (`number_x = midpoint + max_num_w / 2`), the prefix sits
/// `SUFFIX_GAP` left of the number's left edge when any row shows one, and a
/// K/M/B magnitude suffix sits `SUFFIX_GAP` right of the number — a unit
/// suffix ("%") touches it. If the composed band then pokes past ONE side of
/// the content area while the other side has headroom, the whole band shifts
/// by the smaller of the overflow and that headroom.
/// Returns `(prefix_x, number_x, suffix_x)`, each an absolute x.
pub(crate) fn numeric_lane_positions(
    cell_x: f64,
    cell_w: f64,
    cell_pad: f64,
    max_num_w: f64,
    max_prefix_w: f64,
    max_suffix_w: f64,
    is_magnitude: bool,
) -> (f64, f64, f64) {
    let content_area_left = cell_x + cell_pad;
    let content_area_right = cell_x + cell_w - cell_pad;
    let midpoint = (content_area_left + content_area_right) / 2.0;
    let mut number_x = midpoint + max_num_w / 2.0;
    let suffix_gap = if is_magnitude { SUFFIX_GAP } else { 0.0 };
    let prefix_gap = if max_prefix_w > 0.0 { SUFFIX_GAP } else { 0.0 };
    let mut suffix_x = number_x + suffix_gap;
    let mut prefix_x = number_x - max_num_w - prefix_gap;
    let overflow_left = content_area_left - (prefix_x - max_prefix_w);
    let overflow_right = (suffix_x + max_suffix_w) - content_area_right;
    let shift = if overflow_left > 0.0 && overflow_right < 0.0 {
        overflow_left.min(-overflow_right)
    } else if overflow_right > 0.0 && overflow_left < 0.0 {
        -(overflow_right.min(-overflow_left))
    } else {
        0.0
    };
    prefix_x += shift;
    number_x += shift;
    suffix_x += shift;
    (prefix_x, number_x, suffix_x)
}

/// One column's cell demand: p95 of the fully-formatted cell strings +
/// 2*cell_padding (production measure_column_demands), each cell measured
/// as `measured_cell_text` with its glyph run composed in front, when the
/// column can paint one.
pub(super) fn column_demand(
    cells: &[Cell],
    rows: &[serde_json::Map<String, Value>],
    col: &ColMeta,
    fonts: &FontBook,
    measure_family: &str,
    font_size: f64,
    pad: f64,
) -> Result<f64, String> {
    let texts = cells
        .iter()
        .zip(rows)
        .take(measure::SAMPLE_ROWS)
        .map(|(cell, row)| {
            let raw_value = row.get(col.name.as_str()).filter(|v| !v.is_null());
            cell_text_with_glyph(col, raw_value, &measured_cell_text(cell))
        })
        .collect::<Result<Vec<String>, String>>()?;
    Measure { fonts }.column_demand(
        texts.iter().map(String::as_str),
        measure_family,
        font_size,
        pad,
    )
}

/// Round-half-to-even, matching Python's `round()` — used for the row-rule
/// reserve (`max(1, int(round(w)))`), which Rust's `f64::round`
/// (half-away-from-zero) disagrees with at an exact `.5` (e.g. `2.5` rounds to
/// 2 in Python, 3 in Rust).
pub(super) fn python_round(x: f64) -> f64 {
    let floor = x.floor();
    let diff = x - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    }
}

impl PyNum {
    pub(super) fn value(self) -> f64 {
        match self {
            PyNum::Int(v) | PyNum::Float(v) => v,
        }
    }

    pub(super) fn text(self) -> String {
        match self {
            PyNum::Int(v) => format!("{v}"),
            PyNum::Float(v) => float_repr(v),
        }
    }
}

/// One horizontal divider segment. A solid line
/// is a `<rect>` (integer `y` + integer height = exact pixel coverage at any
/// browser scale); a dashed one needs a stroked `<line>` centered on the same
/// band. `class` rides before `shape-rendering` on the rect and last on the
/// line.
pub(super) fn emit_divider_segment(
    tree: &mut Tree,
    x1: f64,
    x2: f64,
    y: PyNum,
    thickness: PyNum,
    divider: &Divider,
    class: Option<&str>,
) {
    if divider.dasharray.is_none() {
        tree.rect(
            Attrs::new()
                .quick("x", float_repr(x1))
                .quick("y", y.text())
                .quick("width", float_repr(x2 - x1))
                .quick("height", thickness.text())
                .quick("fill", divider.color.as_str())
                .quick_some("class", class)
                .quick("shape-rendering", "crispEdges"),
        );
        return;
    }
    let mid = float_repr(y.value() + thickness.value() / 2.0);
    tree.line(
        Attrs::new()
            .quick("x1", float_repr(x1))
            .quick("y1", mid.as_str())
            .quick("x2", float_repr(x2))
            .quick("y2", mid.as_str())
            .quick("stroke", divider.color.as_str())
            .quick("stroke-width", thickness.text())
            .quick_some("stroke-dasharray", divider.dasharray.as_deref())
            .quick_some("class", class),
    );
}

/// One column's header rule span.
/// A column with a lane (`lane` =
/// its `content_left`/`content_right`) centers on the cell
/// midpoint at `max(label_w, lane width)` (`:1194-1197`); any other column
/// starts at `cell_x + pad` and spans `max(label_w, cw - 2*pad)` (`:1201-1202`).
/// The span clamps to `cell_x + 4 .. cell_x + cw - 4` (`:1173-1174`,
/// `:1205-1206`) and paints only when `x2 > x1` (`:1207`).
pub(super) fn header_rule_span(
    cell_x: f64,
    cell_w: f64,
    pad: f64,
    label_w: f64,
    lane: Option<(f64, f64)>,
) -> Option<(f64, f64)> {
    let (x1, x2) = match lane {
        Some((content_left, content_right)) => {
            let rule_w = label_w.max(content_right - content_left);
            let center = cell_x + cell_w / 2.0;
            (center - rule_w / 2.0, center + rule_w / 2.0)
        }
        None => {
            let x1 = cell_x + pad;
            (x1, x1 + label_w.max(cell_w - 2.0 * pad))
        }
    };
    let x1 = x1.max(cell_x + HEADER_RULE_GAP);
    let x2 = x2.min(cell_x + cell_w - HEADER_RULE_GAP);
    (x2 > x1).then_some((x1, x2))
}

/// Resolve a column width hint to pixels: a positive number is used
/// directly; a non-positive number means "no pin" (`None`); a `"NN%"` string
/// resolves to `available_width * pct/100` (non-positive pct likewise `None`);
/// any other string parses as a literal px number.
///
/// A `"NNpx"` string is a CSS width the authored type admits
/// (`TableColumnConfig.width: int | str`) but production's `float()` cannot
/// read: its `except ValueError: return None` leaves the column UNPINNED, and
/// so does this port — same result, so the same painted table. Any other
/// unparseable string (a bad `"NN%"`, a bad literal) is a bug production
/// silently drops; this renderer raises instead of quietly auto-sizing a
/// width the author actually wrote (no-magic, validate-and-error-fast).
pub(super) fn parse_column_width(
    hint: Option<&WidthHint>,
    available_width: f64,
) -> Result<Option<f64>, String> {
    let Some(hint) = hint else { return Ok(None) };
    match hint {
        WidthHint::Number(n) => Ok((*n > 0.0).then_some(*n)),
        WidthHint::Text(s) => {
            let s = s.trim();
            if s.is_empty() {
                return Ok(None);
            }
            if let Some(pct_str) = s.strip_suffix('%') {
                let pct: f64 = pct_str
                    .trim()
                    .parse()
                    .map_err(|_| format!("table: invalid column width {s:?}"))?;
                return Ok((pct > 0.0).then(|| available_width * (pct / 100.0)));
            }
            if s.len() > 2
                && s.is_char_boundary(s.len() - 2)
                && s[s.len() - 2..].eq_ignore_ascii_case("px")
                && s[..s.len() - 2].trim().parse::<f64>().is_ok()
            {
                return Ok(None);
            }
            let px: f64 = s
                .parse()
                .map_err(|_| format!("table: invalid column width {s:?}"))?;
            Ok((px > 0.0).then_some(px))
        }
    }
}

// ── Pagination row-budget probes ───────────────────────────────────────────────

/// The 1-based page a board variable names: absent or unreadable is page 1.
pub(super) fn page_variable(variables: &serde_json::Map<String, Value>, name: &str) -> usize {
    let page = match variables.get(name) {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Some(Value::String(s)) => s.trim().parse::<i64>().ok(),
        Some(Value::Bool(b)) => Some(i64::from(*b)),
        _ => None,
    };
    page.map_or(1, |p| p.max(1) as usize)
}

/// Largest page size whose EVERY contiguous page-sized window of
/// `row_heights` fits `available`.
/// Checking only the leading rows would let a short page 1 pick a size
/// that overflows a later, taller page.
pub(super) fn largest_safe_page_rows(
    row_heights: &[f64],
    available: f64,
    max_page_rows: usize,
) -> usize {
    let n = row_heights.len();
    if n == 0 {
        return 1;
    }
    for size in (1..=max_page_rows.min(n)).rev() {
        let every_window_fits = (0..n)
            .step_by(size)
            .all(|s| row_heights[s..n.min(s + size)].iter().sum::<f64>() <= available);
        if every_window_fits {
            return size;
        }
    }
    1
}

/// Max over every contiguous page-sized window of `row_heights` —
/// A static multi-page export paints every page
/// onto one shared canvas, so the control bar anchors below the TALLEST
/// page's rows, not page 1's.
pub(super) fn max_page_sum(row_heights: &[f64], page_rows: usize) -> f64 {
    if row_heights.is_empty() || page_rows == 0 {
        return 0.0;
    }
    let n = row_heights.len();
    (0..n)
        .step_by(page_rows)
        .map(|s| row_heights[s..n.min(s + page_rows)].iter().sum::<f64>())
        .fold(0.0, f64::max)
}

/// Windowed page sequence: numeric pages with an ellipsis for gaps.
/// The paginator window (sibling=1, boundary=1).
///
/// Only called when `render` has already established `total_pages >= 2`
/// (the static-export branch only draws controls when the table actually
/// overflows one page) — a `total <= 1` guard here would be dead code.
pub(super) fn paginator_window(page: i64, total: i64) -> Vec<PageItem> {
    let mut must_show: Vec<i64> = Vec::new();
    for p in 1..=PAGINATOR_BOUNDARY_COUNT.min(total) {
        must_show.push(p);
    }
    for p in (total - PAGINATOR_BOUNDARY_COUNT + 1).max(1)..=total {
        must_show.push(p);
    }
    for p in (page - PAGINATOR_SIBLING_COUNT).max(1)..=(page + PAGINATOR_SIBLING_COUNT).min(total) {
        must_show.push(p);
    }
    must_show.sort_unstable();
    must_show.dedup();

    let mut items = Vec::new();
    let mut prev = 0;
    for p in must_show {
        if p > prev + 1 {
            items.push(PageItem::Ellipsis);
        }
        items.push(PageItem::Page(p));
        prev = p;
    }
    items
}

/// Remove every `/*{# ... #}*/`-fenced comment from JavaScript source, port
/// of `comment_stripping.strip_js_comments`. Replaces each fenced span with
/// a single space so adjacent tokens never merge — a plain scan (find the
/// literal `/*{#` marker, then its matching `#}*/` close) is exactly as
/// correct as production's own non-greedy regex here: the 4-character
/// marker is not something real code contains by coincidence, so no
/// string/regex-literal awareness is needed either way.
pub(super) fn strip_js_comments(source: &str) -> String {
    let mut out = String::with_capacity(source.len());
    let mut rest = source;
    while let Some(start) = rest.find("/*{#") {
        out.push_str(&rest[..start]);
        let after_marker = &rest[start + 4..];
        match after_marker.find("#}*/") {
            Some(end) => {
                out.push(' ');
                rest = &after_marker[end + 4..];
            }
            None => {
                // Unterminated fence: production's DOTALL regex would never
                // match without a close either, so the marker (and
                // everything after) passes through untouched.
                out.push_str(&rest[start..]);
                rest = "";
                break;
            }
        }
    }
    out.push_str(rest);
    out
}

/// The static-export pagination toggle script, wrapped for inline SVG
/// execution. `table_pagination.js` is vendored
/// under `chart-renderer/assets/` (pinned byte-equal to production's own
/// copy by `tests/test_table_pagination_asset.py`, a chart-svg pytest, not
/// a runtime import — this crate stays self-contained per `libs/README.md`)
/// rather than read from `dbt-charts/src/` at build or run time.
pub(super) fn embed_table_pagination_script(tree: &mut Tree) {
    let stripped = strip_js_comments(include_str!("../../../assets/table_pagination.js"));
    tree.open(Open::Script, Attrs::new().quick("type", "text/javascript"));
    tree.text("\n");
    tree.cdata(&format!("\n{stripped}\n"));
    tree.text("\n");
    tree.close(Open::Script);
}

/// Static-export pagination control bar: right-anchored chevrons + windowed
/// page numbers, each clickable item carrying a `data-dbt-page-target` hit
/// rect `table_pagination.js` reads (`data-dbt-page-var` too on a live host).
#[allow(clippy::too_many_arguments)]
pub(super) fn render_pagination_controls(
    tree: &mut Tree,
    theme: &PaginatorTheme,
    body_family: &str,
    page: i64,
    total_pages: i64,
    last_enabled_page: i64,
    page_var_name: &str,
    table_width: f64,
    y: f64,
    row_start: usize,
    row_end: usize,
    total_rows: usize,
    padding: f64,
    interactive: bool,
    fonts: &FontBook,
) -> Result<(), String> {
    let window = paginator_window(page, total_pages);
    enum Role {
        Prev,
        Next,
        Page,
        Ellipsis,
    }
    impl Role {
        fn as_str(&self) -> &'static str {
            match self {
                Role::Prev => "prev",
                Role::Next => "next",
                Role::Page => "page",
                Role::Ellipsis => "ellipsis",
            }
        }
    }
    let prev_target = if page > 1 { page - 1 } else { 0 };
    let next_target = if page < last_enabled_page {
        page + 1
    } else {
        0
    };

    let mut sequence: Vec<(Role, String, i64)> =
        vec![(Role::Prev, PAGINATOR_PREV_CHEVRON.to_string(), prev_target)];
    for item in window {
        match item {
            PageItem::Ellipsis => {
                sequence.push((Role::Ellipsis, PAGINATOR_ELLIPSIS.to_string(), 0))
            }
            PageItem::Page(p) => sequence.push((
                Role::Page,
                p.to_string(),
                if p <= last_enabled_page { p } else { 0 },
            )),
        }
    }
    sequence.push((Role::Next, PAGINATOR_NEXT_CHEVRON.to_string(), next_target));

    let font_size = theme.font.size_or(11.0) as i64;
    // Production passes the table body family; paginator.font contributes
    // only its size.
    let font_family = body_family;
    let text_y = y + 18.0;
    // Hit rect hugs the glyph tightly so cursor:pointer matches the visible
    // character — adjacent rects never touch, so the gap between items
    // never feels clickable.
    let rect_w = font_size as f64 + 6.0;
    let rect_h = (font_size as f64 * 1.8).max(18.0);
    let rect_y = y + 4.0;
    let aux_slot_w = theme.item_width * PAGINATOR_AUX_SLOT_RATIO;

    let slot_widths = sequence
        .iter()
        .map(|(role, glyph, _)| match role {
            Role::Prev | Role::Next | Role::Ellipsis => Ok(aux_slot_w),
            Role::Page => Measure { fonts }.page_slot(
                glyph,
                measure::stand_in_family(body_family),
                font_size as f64,
                theme.item_width,
            ),
        })
        .collect::<Result<Vec<f64>, String>>()?;
    let total_width: f64 = slot_widths.iter().sum();
    let sequence_left = table_width - total_width;

    // Slot centers, except an ellipsis sits midway between its neighbors' ink
    // edges so the gap on each side is equal whatever the digit counts.
    let measure_family = measure::stand_in_family(body_family);
    let mut centers: Vec<f64> = Vec::with_capacity(sequence.len());
    let mut slot_left = sequence_left;
    for slot_w in &slot_widths {
        centers.push(slot_left + slot_w / 2.0);
        slot_left += slot_w;
    }
    for i in 0..sequence.len() {
        if matches!(sequence[i].0, Role::Ellipsis) {
            let (prev, next) = (&sequence[i - 1].1, &sequence[i + 1].1);
            let prev_edge =
                centers[i - 1] + fonts.measure(prev, measure_family, font_size as f64)? / 2.0;
            let next_edge =
                centers[i + 1] - fonts.measure(next, measure_family, font_size as f64)? / 2.0;
            centers[i] = (prev_edge + next_edge) / 2.0;
        }
    }

    // A muted "{row_start}–{row_end} / {total_rows}" label at
    // `padding`, left of the page sequence —: an
    // unlabeled "‹ 1 2 … 37 ›" reads as "37 pages of dashboards", not "this
    // table has 37 pages of rows". Dropped entirely (never shrunk) if it
    // would collide with the right-anchored sequence — production gates on
    // the sequence's own left edge.
    let label_text = format!("{row_start}\u{2013}{row_end} / {total_rows}");
    // `get_font_measurer(font_family)`: every non-serif
    // family measures as Inter, whatever the label paints in.
    let label_w = fonts.measure(
        &label_text,
        measure::stand_in_family(body_family),
        font_size as f64,
    )?;
    if padding + label_w <= sequence_left {
        tree.text_el(
            Attrs::new()
                .quick("class", "dbt-paginator-label")
                .quick("x", format!("{padding:.1}"))
                .quick("y", format!("{text_y:.1}"))
                .quick("font-size", font_size.to_string())
                .quick("fill", theme.color_inactive.as_str())
                .quick("font-family", font_family)
                .quick("font-weight", theme.weight_inactive.to_string())
                .quick("style", "font-variant-numeric: tabular-nums;"),
            &label_text,
        );
    }

    tree.open(
        Open::Group,
        Attrs::new()
            .quick("class", "dbt-paginator")
            .quick("data-paginator", page_var_name),
    );
    for (i, (role, glyph, target)) in sequence.iter().enumerate() {
        let center_x = centers[i];

        let is_active_page = matches!(role, Role::Page) && *target == page;
        let is_disabled_chevron = matches!(role, Role::Prev | Role::Next) && *target == 0;
        let is_ellipsis = matches!(role, Role::Ellipsis);

        let is_disabled_page = matches!(role, Role::Page) && *target == 0;
        let (color, weight, clickable) = if is_active_page {
            (theme.color_active.as_str(), theme.weight_active, false)
        } else if is_disabled_chevron {
            (theme.color_disabled.as_str(), theme.weight_chevron, false)
        } else if is_disabled_page {
            (theme.color_disabled.as_str(), theme.weight_inactive, false)
        } else if matches!(role, Role::Prev | Role::Next) {
            (theme.color_active.as_str(), theme.weight_chevron, true)
        } else if is_ellipsis {
            (theme.color_inactive.as_str(), theme.weight_inactive, false)
        } else {
            (theme.color_inactive.as_str(), theme.weight_inactive, true)
        };

        if clickable {
            let rect_x = center_x - rect_w / 2.0;
            // A live host commits the variable the control names; an export
            // names only the target its inline script toggles.
            tree.rect(
                Attrs::new()
                    .quick("class", "dbt-page-target")
                    .quick("x", format!("{rect_x:.1}"))
                    .quick("y", format!("{rect_y:.1}"))
                    .quick("width", format!("{rect_w:.1}"))
                    .quick("height", format!("{rect_h:.1}"))
                    .quick("fill", "transparent")
                    .quick_some("data-dbt-page-var", interactive.then_some(page_var_name))
                    .quick("data-dbt-page-target", target.to_string()),
            );
        }

        tree.text_el(
            Attrs::new()
                .quick("x", format!("{center_x:.1}"))
                .quick("y", format!("{text_y:.1}"))
                .quick("font-size", font_size.to_string())
                .quick("fill", color)
                .quick("text-anchor", "middle")
                .quick("font-family", font_family)
                .quick("font-weight", weight.to_string())
                .quick("class", "dbt-paginator-glyph")
                .quick("style", "font-variant-numeric: tabular-nums;")
                .quick("data-paginator-role", role.as_str())
                .quick_some("data-pagination-current", is_active_page.then_some(page_var_name)),
            glyph,
        );
    }
    tree.close(Open::Group);
    Ok(())
}

// ── In-cell spark marks ──────────

/// Clamp `value` against
/// `max_value`, honoring sign only when `signed`. `signed=false` reproduces
/// the plain edge clamp (negatives clamp to zero, matches every `bar`/
/// `bar-normalize` call site in this renderer — a `bar` column with any
/// negative detail value refuses before paint, `bar-normalize` never
/// consults sign at all). `signed=true` clamps symmetrically to
/// `[-max_value, max_value]` and reports which side of zero the value
/// landed on — `render_spark_columns`'s own has_negative branch below.
/// Returns `(fraction in [0, 1], is_negative)`.
pub(super) fn signed_fraction(value: f64, max_value: f64, signed: bool) -> (f64, bool) {
    if !signed {
        let clamped = value.max(0.0).min(max_value);
        let fraction = if max_value > 0.0 {
            clamped / max_value
        } else {
            0.0
        };
        return (fraction, false);
    }
    let clamped = value.max(-max_value).min(max_value);
    let fraction = if max_value > 0.0 {
        clamped.abs() / max_value
    } else {
        0.0
    };
    (fraction, clamped < 0.0)
}

/// Python's `f"{v:g}"`: six significant digits, trailing zeros dropped,
/// exponent form past `1e6` and under `1e-4`.
pub(super) fn py_g(v: f64) -> String {
    if v == 0.0 {
        return "0".to_string();
    }
    let exp = v.abs().log10().floor() as i32;
    if !(-4..6).contains(&exp) {
        let text = format!("{v:.5e}");
        let (mantissa, e) = text.split_once('e').expect("exponent form");
        let mantissa = if mantissa.contains('.') {
            mantissa.trim_end_matches('0').trim_end_matches('.')
        } else {
            mantissa
        };
        let e: i32 = e.parse().expect("exponent");
        return format!("{mantissa}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    let text = format!("{v:.*}", (5 - exp).max(0) as usize);
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        text
    }
}

/// A signed column grows each bar from
/// its midline (`has_negative`); `width`/`height` print bare — production's
/// `int(...)`-computed size is a genuine Python int, and this renderer's
/// `spark_width`/`spark_height` mirror that with `f64::trunc()`.
/// `border_radius` is a theme float, so it takes `py_float`.
#[allow(clippy::too_many_arguments)]
pub(super) fn render_spark_bar(
    value: f64,
    max_value: f64,
    width: f64,
    height: f64,
    color: &str,
    normalize: bool,
    background: &str,
    border_radius: f64,
    has_negative: bool,
    label: Option<(&BarLabel, &SparkBarLabelTheme)>,
) -> Vec<Node> {
    let (fraction, is_negative) = signed_fraction(value, max_value, has_negative);
    let (fill_x, fill_width) = if has_negative {
        let half_width = width / 2.0;
        let fill_width = fraction * half_width;
        (
            format!(
                "{:.1}",
                if is_negative {
                    half_width - fill_width
                } else {
                    half_width
                }
            ),
            fill_width,
        )
    } else {
        ("0".to_string(), fraction * width)
    };
    let radius_s = float_repr(border_radius);
    let mut tree = Tree::new();
    if normalize {
        tree.rect(
            Attrs::new()
                .quick("x", "0")
                .quick("y", "0")
                .quick("width", width.to_string())
                .quick("height", height.to_string())
                .quick("fill", background)
                .quick("rx", radius_s.as_str()),
        );
    }
    if fill_width > 0.0 {
        tree.rect(
            Attrs::new()
                .quick("x", fill_x)
                .quick("y", "0")
                .quick("width", format!("{fill_width:.1}"))
                .quick("height", height.to_string())
                .quick("fill", color)
                .quick("rx", radius_s.as_str()),
        );
    }
    if let Some((label, theme)) = label {
        tree.text_el(
            Attrs::new()
                .quick("x", float_repr(width - theme.inset_x))
                .quick("y", float_repr(height / 2.0))
                .quick("font-size", float_repr(label.font_size))
                .quick("fill", theme.fill.as_str())
                .quick("fill-opacity", float_repr(theme.fill_opacity))
                .quick("text-anchor", "end")
                .quick("dominant-baseline", "central")
                .quick("font-family", label.family)
                .quick("style", "font-variant-numeric: tabular-nums lining-nums;"),
            &label.text,
        );
    }
    tree.finish()
}

/// `spark.type: column`: one
/// bottom-anchored vertical bar, or midline-anchored in a signed column.
#[allow(clippy::too_many_arguments)]
pub(super) fn render_spark_column(
    value: f64,
    max_value: f64,
    width: f64,
    height: f64,
    color: &str,
    border_radius: f64,
    has_negative: bool,
) -> Vec<Node> {
    let (fraction, is_negative) = signed_fraction(value, max_value, has_negative);
    let (fill_y, fill_height) = if has_negative {
        let half_height = height / 2.0;
        let fill_height = fraction * half_height;
        (
            if is_negative {
                half_height
            } else {
                half_height - fill_height
            },
            fill_height,
        )
    } else {
        let fill_height = fraction * height;
        (height - fill_height, fill_height)
    };
    let mut tree = Tree::new();
    if fill_height > 0.0 {
        tree.rect(
            Attrs::new()
                .quick("x", "0")
                .quick("y", format!("{fill_y:.1}"))
                .quick("width", width.to_string())
                .quick("height", format!("{fill_height:.1}"))
                .quick("fill", color)
                .quick("rx", float_repr(border_radius)),
        );
    }
    tree.finish()
}

/// Per-cell layout decided from
/// the array's OWN values — min-rebased when every value is non-negative
/// (baseline at the array's own minimum), zero-midline the moment any value
/// is negative (min-rebasing would erase the negative: `(v-min)/range`
/// scales `-40 -30 -20 -10` identically to `10 20 30 40`). `x`/`y`/`width`/
/// `height` are genuine `:.1f` rounds (not the bare-float T1 law); `rx` is
/// the theme's bare `columns.border.radius` float, so `py_float`.
/// `negative_fill` is the tone a negative bar takes when `negative_color` is on.
pub(super) fn render_spark_columns(
    values: &[f64],
    width: f64,
    height: f64,
    color: &str,
    negative_fill: Option<&str>,
    theme: &SparkColumnsTheme,
) -> Vec<Node> {
    let plot_height = height - 2.0 * theme.padding;
    let num_bars = values.len() as f64;
    let total_gap = theme.gap * (num_bars - 1.0);
    let bar_width = (width - total_gap) / num_bars;
    let radius_s = float_repr(theme.border.radius);
    // A non-finite value keeps its x-slot but draws no bar and adds nothing to
    // the domain (the null rule of `coerce_numeric_cell`).
    let finite: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    let has_negative = finite.iter().any(|v| *v < 0.0);
    let mut tree = Tree::new();
    let mut bar = |x: f64, y: f64, bar_height: f64, fill: &str| {
        tree.rect(
            Attrs::new()
                .quick("x", format!("{x:.1}"))
                .quick("y", format!("{y:.1}"))
                .quick("width", format!("{bar_width:.1}"))
                .quick("height", format!("{bar_height:.1}"))
                .quick("fill", fill.to_string())
                .quick("rx", radius_s.as_str()),
        );
    };
    if has_negative {
        let max_abs = finite.iter().fold(0.0_f64, |acc, v| acc.max(v.abs()));
        let half = plot_height / 2.0;
        let mid_y = theme.padding + half;
        for (i, &val) in values.iter().enumerate() {
            if !val.is_finite() {
                continue;
            }
            let x = i as f64 * (bar_width + theme.gap);
            let (fraction, is_negative) = signed_fraction(val, max_abs, true);
            let bar_height = theme.min_bar_height.max(fraction * half);
            let y = if is_negative {
                mid_y
            } else {
                mid_y - bar_height
            };
            let fill = if is_negative {
                negative_fill.unwrap_or(color)
            } else {
                color
            };
            bar(x, y, bar_height, fill);
        }
    } else {
        let min_val = finite.iter().copied().fold(f64::INFINITY, f64::min);
        let max_val = finite.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let value_range = max_val - min_val;
        for (i, &val) in values.iter().enumerate() {
            if !val.is_finite() {
                continue;
            }
            let x = i as f64 * (bar_width + theme.gap);
            let bar_height = if value_range > 0.0 {
                theme
                    .min_bar_height
                    .max(((val - min_val) / value_range) * plot_height)
            } else {
                plot_height / 2.0
            };
            let y = height - theme.padding - bar_height;
            bar(x, y, bar_height, color);
        }
    }
    tree.finish()
}

/// A dashed rule across the vertical
/// middle, drawn for a zero-value `line` or `columns` cell.
pub(super) fn render_empty_spark(width: f64, height: f64, theme: &SparkEmptyTheme) -> Vec<Node> {
    let y = float_repr(height / 2.0);
    let mut tree = Tree::new();
    tree.line(
        Attrs::new()
            .quick("x1", float_repr(theme.inset_x))
            .quick("y1", y.as_str())
            .quick("x2", float_repr(width - theme.inset_x))
            .quick("y2", y.as_str())
            .quick("stroke", theme.stroke.color.as_str())
            .quick("stroke-width", float_repr(theme.stroke.width))
            .quick("stroke-dasharray", theme.stroke.dasharray.as_str()),
    );
    tree.finish()
}

/// A rule inset `inset_x` from
/// each side at mid-height, and a dot of `marker_radius` at the width midpoint,
/// drawn for a one-value `line` cell.
pub(super) fn render_single_value_line(
    width: f64,
    height: f64,
    color: &str,
    theme: &SparkSingleValueTheme,
) -> Vec<Node> {
    let y = float_repr(height / 2.0);
    let mut tree = Tree::new();
    tree.line(
        Attrs::new()
            .quick("x1", float_repr(theme.inset_x))
            .quick("y1", y.as_str())
            .quick("x2", float_repr(width - theme.inset_x))
            .quick("y2", y.as_str())
            .quick("stroke", color)
            .quick("stroke-width", SPARK_LINE_STROKE_WIDTH),
    );
    tree.circle(
        Attrs::new()
            .quick("cx", float_repr(width / 2.0))
            .quick("cy", y.as_str())
            .quick("r", float_repr(theme.marker_radius))
            .quick("fill", color),
    );
    tree.finish()
}

/// `render_spark_area`: the `line`'s polyline under a filled
/// polygon closed along the plot's bottom edge.
#[allow(clippy::too_many_arguments)]
pub(super) fn render_spark_area(
    values: &[f64],
    width: f64,
    height: f64,
    color: &str,
    fill_opacity: f64,
    last_visible: bool,
    padding: &SparkPaddingTheme,
) -> Vec<Node> {
    let points = spark_points(values, width, height, padding);
    let bottom_y = height - padding.bottom;
    let first_x = padding.left;
    let last_x = padding.left + (width - (padding.left + padding.right));
    let mut tree = Tree::new();
    tree.polygon(
        Attrs::new()
            .quick(
                "points",
                format!(
                    "{} {} {}",
                    py_float_str_pair(first_x, bottom_y),
                    joined_points(&points),
                    py_float_str_pair(last_x, bottom_y)
                ),
            )
            .quick("fill", color)
            .quick("fill-opacity", float_repr(fill_opacity)),
    );
    emit_spark_line(&mut tree, values, &points, color, last_visible, false);
    tree.finish()
}

/// Python's `f"{x},{y}"` for two floats.
pub(super) fn py_float_str_pair(x: f64, y: f64) -> String {
    format!("{},{}", float_repr(x), float_repr(y))
}

/// `_normalize_points` for a
/// series of two or more values: x is `padding.left + i/(n-1) * plot_width`,
/// y maps `[min, max]` onto the plot height (a flat series sits on
/// `padding.top + plot_height/2`), both printed `:.1f`.
pub(super) fn spark_points(
    values: &[f64],
    width: f64,
    height: f64,
    padding: &SparkPaddingTheme,
) -> Vec<(String, String)> {
    let min_val = values.iter().cloned().fold(f64::INFINITY, f64::min);
    let max_val = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let value_range = max_val - min_val;
    let plot_width = width - (padding.left + padding.right);
    let plot_height = height - (padding.top + padding.bottom);
    let last_index = (values.len() - 1) as f64;
    values
        .iter()
        .enumerate()
        .map(|(i, &val)| {
            let x = padding.left + (i as f64 / last_index) * plot_width;
            let y = if value_range > 0.0 {
                padding.top + (1.0 - (val - min_val) / value_range) * plot_height
            } else {
                padding.top + plot_height / 2.0
            };
            (format!("{x:.1}"), format!("{y:.1}"))
        })
        .collect()
}

pub(super) fn joined_points(points: &[(String, String)]) -> String {
    points
        .iter()
        .map(|(x, y)| format!("{x},{y}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// The polyline through `points`. The optional dots follow it: the last point
/// with `r="2.5"`, then the first minimum and first maximum with `r="2"`
/// (the same index twice for a flat series).
pub(super) fn emit_spark_line(
    tree: &mut Tree,
    values: &[f64],
    points: &[(String, String)],
    color: &str,
    last_visible: bool,
    min_max_visible: bool,
) {
    tree.polyline(
        Attrs::new()
            .quick("points", joined_points(points))
            .quick("fill", "none")
            .quick("stroke", color)
            .quick("stroke-width", SPARK_LINE_STROKE_WIDTH)
            .quick("stroke-linecap", "round")
            .quick("stroke-linejoin", "round"),
    );
    let mut dot = |index: usize, radius: &str| {
        let (x, y) = &points[index];
        tree.circle(
            Attrs::new()
                .quick("cx", x.as_str())
                .quick("cy", y.as_str())
                .quick("r", radius)
                .quick("fill", color),
        );
    };
    if last_visible {
        dot(points.len() - 1, "2.5");
    }
    if min_max_visible {
        let min_val = values.iter().cloned().fold(f64::INFINITY, f64::min);
        let max_val = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let first_of = |target: f64| {
            values
                .iter()
                .position(|&v| v == target)
                .expect("min and max come from the values")
        };
        dot(first_of(min_val), "2");
        dot(first_of(max_val), "2");
    }
}

pub(super) fn render_spark_line(
    values: &[f64],
    width: f64,
    height: f64,
    color: &str,
    last_visible: bool,
    min_max_visible: bool,
    padding: &SparkPaddingTheme,
) -> Vec<Node> {
    let points = spark_points(values, width, height, padding);
    let mut tree = Tree::new();
    emit_spark_line(
        &mut tree,
        values,
        &points,
        color,
        last_visible,
        min_max_visible,
    );
    tree.finish()
}

/// A spark cell's sizing: `padding = 8`;
/// `spark_width`/`spark_height` default to `int(cell_width - 2*padding)` /
/// `int(row_height - padding)` (production's `int()` truncates, matching
/// `f64::trunc()` for the positive extents every corpus cell carries), then
/// capped at `int(cell_width - padding)` / `int(row_height - 4)`. Returns
/// `None` (draw nothing — the caller falls through to the ordinary cell
/// paint) when a `bar`/`bar-normalize` cell has no numeric value to show, or
/// `Err` naming a cell the mark cannot draw: a `columns` cell that is not an
/// array or holds a non-number, or a multi-point `line` series holding a
/// non-number (production raises there). `line` reads its series the way
/// `render_spark` does (`spark_series`).
pub(super) fn render_spark_cell<'a>(
    column: &str,
    value: &Value,
    spark: &SparkConfig,
    cell_width: f64,
    row_height: f64,
    theme: &SparkTheme,
    column_max: Option<f64>,
    layout: &SparkLayout<'a>,
) -> Result<Option<(Vec<Node>, f64, f64)>, String> {
    let padding = 8.0;
    let default_width = if spark.spark_type == "column" {
        theme.column.width.trunc()
    } else {
        (cell_width - padding * 2.0).trunc()
    };
    let default_height = (row_height - padding).trunc();
    let spark_width = spark
        .width
        .unwrap_or(default_width)
        .min((cell_width - padding).trunc());
    let spark_height = spark
        .height
        .unwrap_or(default_height)
        .min((row_height - 4.0).trunc());
    // A bar's fill: an authored color, else the bucket its (clamped) value
    // reaches; a negative value under `negative_color` takes the tone instead.
    let bar_color = |v: f64, max_value: f64, is_negative: bool| -> Result<String, String> {
        if is_negative && spark.negative_color == Some(true) {
            if let Some(tone) = layout.negative_tone {
                return Ok(tone.to_string());
            }
        }
        let thresholds = match (&spark.color, &spark.thresholds) {
            (None, Some(t)) => parse_spark_thresholds(column, t)?,
            _ => Vec::new(),
        };
        Ok(match &spark.color {
            Some(color) => color.clone(),
            None => spark_threshold_color(v.max(0.0).min(max_value), &thresholds, &theme.bar.color)
                .to_string(),
        })
    };

    match spark.spark_type.as_str() {
        "bar" | "bar-normalize" => {
            let Some(v) = coerce_numeric_cell(Some(value)) else {
                return Ok(None);
            };
            let normalize = spark.spark_type == "bar-normalize";
            let max_value = spark
                .max
                .or(if normalize { None } else { column_max })
                .unwrap_or(theme.bar.default_max);
            let signed = layout.has_negative && !normalize;
            let (_, is_negative) = signed_fraction(v, max_value, signed);
            let color = bar_color(v, max_value, is_negative)?;
            let border_radius = spark.border_radius.unwrap_or(theme.bar.border.radius);
            let background = spark.background.as_deref().unwrap_or(&theme.bar.background);
            let label = (spark.value_visible == Some(true)).then(|| {
                let mut text = if v.fract() == 0.0 {
                    py_g(v)
                } else {
                    format!("{v:.1}")
                };
                if let Some(suffix) = &spark.value_suffix {
                    text.push_str(suffix);
                }
                BarLabel {
                    text,
                    font_size: layout.font_size,
                    family: layout.body_family,
                }
            });
            let content = render_spark_bar(
                v,
                max_value,
                spark_width,
                spark_height,
                &color,
                normalize,
                background,
                border_radius,
                signed,
                label.as_ref().map(|l| (l, &theme.bar.label)),
            );
            Ok(Some((content, spark_width, spark_height)))
        }
        "column" => {
            let Some(v) = coerce_numeric_cell(Some(value)) else {
                return Ok(None);
            };
            let max_value = spark.max.unwrap_or(theme.bar.default_max);
            let (_, is_negative) = signed_fraction(v, max_value, layout.has_negative);
            let color = bar_color(v, max_value, is_negative)?;
            let border_radius = spark.border_radius.unwrap_or(theme.bar.border.radius);
            let content = render_spark_column(
                v,
                max_value,
                spark_width,
                spark_height,
                &color,
                border_radius,
                layout.has_negative,
            );
            Ok(Some((content, spark_width, spark_height)))
        }
        "columns" => {
            let Value::Array(items) = value else {
                return Err("table: spark columns cell is not an array".into());
            };
            if items.is_empty() {
                return Ok(Some((
                    render_empty_spark(spark_width, spark_height, &theme.empty),
                    spark_width,
                    spark_height,
                )));
            }
            // A missing element (a null, or a NaN the wire decoded to one) leaves
            // its slot empty.
            let values: Vec<f64> = items
                .iter()
                .map(|v| match v {
                    Value::Null => Ok(f64::NAN),
                    other => other.as_f64().ok_or_else(|| {
                        "table: spark columns array element is not a number".to_string()
                    }),
                })
                .collect::<Result<_, _>>()?;
            let color = spark.color.as_deref().unwrap_or(&theme.color);
            let negative_fill = (spark.negative_color == Some(true))
                .then_some(layout.negative_tone)
                .flatten();
            let content = render_spark_columns(
                &values,
                spark_width,
                spark_height,
                color,
                negative_fill,
                &theme.columns,
            );
            Ok(Some((content, spark_width, spark_height)))
        }
        "line" | "area" => {
            let color = spark.color.as_deref().unwrap_or(&theme.color);
            let is_area = spark.spark_type == "area";
            // A missing element (a null, or a NaN the wire decoded to one) is
            // skipped; the line runs through its neighbors.
            let values: Vec<f64> = spark_series(value)
                .iter()
                .filter(|v| !v.is_null())
                .map(|v| {
                    v.as_f64().ok_or_else(|| {
                        format!(
                            "table: spark {} column '{column}' array element is not a number",
                            spark.spark_type
                        )
                    })
                })
                .collect::<Result<_, _>>()?;
            let content = match values.as_slice() {
                [] => render_empty_spark(spark_width, spark_height, &theme.empty),
                [_] => {
                    render_single_value_line(spark_width, spark_height, color, &theme.single_value)
                }
                many => {
                    let values = many;
                    if is_area {
                        render_spark_area(
                            &values,
                            spark_width,
                            spark_height,
                            color,
                            spark.fill_opacity.unwrap_or(theme.area.fill_opacity),
                            spark.last_visible == Some(true),
                            &theme.padding,
                        )
                    } else {
                        render_spark_line(
                            &values,
                            spark_width,
                            spark_height,
                            color,
                            spark.last_visible == Some(true),
                            spark.min_max_visible == Some(true),
                            &theme.padding,
                        )
                    }
                }
            };
            Ok(Some((content, spark_width, spark_height)))
        }
        other => Err(format!("table: spark type '{other}' not supported")),
    }
}

/// Count one cut element for column `name`, keeping columns in first-cut
/// order: production's warning lists them in the order its sink recorded them.
pub(super) fn count_cut(truncations: &RefCell<Vec<(String, usize)>>, name: &str) {
    let mut t = truncations.borrow_mut();
    match t.iter_mut().find(|(n, _)| n == name) {
        Some((_, count)) => *count += 1,
        None => t.push((name.to_string(), 1)),
    }
}

impl TableBody<'_> {
    /// Cut `text` to column `ci`'s content area less the glyph run, counting
    /// a cut into WARN-TABLE-TEXT-TRUNCATED. Whitespace normalization alone
    /// counts as a cut, as in production's `display_value != pre_trunc`.
    fn cut_cell(&self, ci: usize, text: &str, glyph: Option<&str>) -> Result<String, Diagnostic> {
        let glyph_w = self
            .fonts
            .measure(&glyph_run(glyph), self.measure_family, self.font_size)?;
        let measurer = self.fonts.measurer(self.measure_family)?;
        let cut = measure::fonts::truncate_text_precise(
            text,
            self.col_ws[ci] - 2.0 * self.pad - glyph_w,
            self.font_size,
            &measurer,
            true,
            true,
        );
        if cut != text && !self.chart_id.is_empty() {
            count_cut(self.truncations, self.cols[ci].name.as_str());
        }
        Ok(cut)
    }

    /// Paint one page's worth of data rows (and their deferred row rules)
    /// into `svg`, called once per page.
    /// `cell_data` is sized to THIS CALL's own page (`page_n_shown`, indexed
    /// by the LOCAL row index `local_ri`, never the global `ri`) — the
    /// non-paginated caller passes the one global `build_cell_data` result
    /// at `page_offset == 0`, where local and global indices coincide; the
    /// paginated `static_multi_page` loop recomputes its own
    /// `build_cell_data` call per page so each page's `symbol_mode: anchors`
    /// prefix/suffix anchors at ITS OWN row 0, not the whole table's (see
    /// `build_cell_data`'s own doc). `scale_rows`, the stripe alternation,
    /// and the summary-gap accumulator are likewise recomputed FRESH from
    /// this page's own slice every call, mirroring production's
    /// `enumerate(rows)`-local semantics — never sliced
    /// out of a precomputed global array.
    fn render_data_rows(
        &self,
        tree: &mut Tree,
        page_offset: usize,
        page_n_shown: usize,
        cell_data: &[Vec<Cell>],
    ) -> Result<(), Diagnostic> {
        let Self {
            rows,
            row_roles,
            row_heights,
            summary_gap_unit,
            rows_start_y,
            th,
            cols,
            col_x,
            col_ws,
            content_w,
            rn_w,
            row_numbers,
            row_number_ink,
            wrapped_lines,
            prefix_xs,
            number_xs,
            suffix_xs,
            outer,
            pad,
            row_h,
            font_size,
            body_family,
            tab_family,
            body_color,
            link_color,
            bar_auto_max,
            signed_columns,
            tones,
            link,
            link_open,
            filter_variables,
            link_context,
            ..
        } = *self;
        let row_text_baseline = |ri: usize| row_heights[ri] / 2.0 + th.text_baseline_offset;
        let all_data_columns: Vec<String> = rows
            .get(page_offset)
            .map(|r| r.keys().cloned().collect())
            .unwrap_or_default();
        // Row-band anchor name: the first column whose
        // config carries neither `spark` nor `swatch`. Falls back to the first
        // column when every column is one of them.
        let row_link_label_col: Option<&str> = link.and_then(|_| {
            cols.iter()
                .find(|c| c.cfg.spark.is_none() && !is_swatch(c))
                .or_else(|| cols.first())
                .map(|c| c.name.as_str())
        });
        // Cumulative row-top offset and summary-gap accumulator, computed
        // LOCALLY from this page's own row slice — mirrors production's own
        // per-call `enumerate(rows)` accumulation exactly:
        // a page that starts mid-run of summary rows carries NO gap for its own
        // first row, since there is no previous row in ITS OWN slice to compare
        // against (`local_ri > 0` guards it below, same as production's
        // `row_idx > 0`).
        let mut page_row_y_offset = vec![0.0_f64; page_n_shown];
        let mut page_summary_gap_at = vec![0.0_f64; page_n_shown];
        {
            let mut acc = 0.0_f64;
            let mut gap_acc = 0.0_f64;
            for local_ri in 0..page_n_shown {
                let ri = page_offset + local_ri;
                if local_ri > 0
                    && is_summary_role(row_roles[ri])
                    && !is_summary_role(row_roles[ri - 1])
                {
                    gap_acc += summary_gap_unit;
                }
                page_row_y_offset[local_ri] = acc;
                page_summary_gap_at[local_ri] = gap_acc;
                acc += row_heights[ri];
            }
        }

        // Scale domain input — the VISIBLE (page-1) detail rows only
        // (production's `scale_rows`, built inside
        // `_render_data_rows` from whichever slice its two callers pass —
        // `page_visible_data`/`visible_data`, never the full dataset): a
        // grand-total cell is often 10-100x any single detail value, which
        // would crush the detail-row gradient toward one end of the palette,
        // and a row past the page/slot the table never draws must not skew the
        // domain the rows it DOES draw interpolate against either.
        let scale_rows: Vec<&serde_json::Map<String, Value>> = rows
            [page_offset..page_offset + page_n_shown]
            .iter()
            .zip(row_roles[page_offset..page_offset + page_n_shown].iter())
            .filter(|(_, role)| !is_summary_role(**role))
            .map(|(row, _)| row)
            .collect();

        // Body row dividers: `style.row.divider`. Never
        // drawn below the last row on the page or the row right before a
        // summary/total row; its reserved height comes out of THAT row's own
        // fill (stripe/role background), not extra row height — the line sits
        // inside the existing row band, so the table never grows.
        let row_divider = &th.row.divider.line;
        let row_rule_width = row_divider.width;
        let mut row_rules = Tree::new();
        let outer_s = float_repr(outer);
        let content_w_s = float_repr(content_w);
        // The row link covers the data columns, never the index gutter.
        let (band_x_s, band_w_s) = (float_repr(outer + rn_w), float_repr(content_w - rn_w));

        // Data rows.
        for local_ri in 0..page_n_shown {
            let ri = page_offset + local_ri;
            let this_row_h = row_heights[ri];
            let row_y = rows_start_y + page_row_y_offset[local_ri] + page_summary_gap_at[local_ri];
            let text_y = row_y + row_text_baseline(ri);
            let text_y_s = float_repr(text_y);
            let role = row_roles[ri];
            let is_summary = is_summary_role(role);

            let is_last_row = local_ri == page_n_shown - 1;
            let next_is_summary = local_ri + 1 < page_n_shown && is_summary_role(row_roles[ri + 1]);
            let will_draw_row_rule =
                row_divider.visible && !is_last_row && !next_is_summary && !is_summary;
            let rule_reserve_px = if will_draw_row_rule {
                python_round(row_rule_width).max(1.0)
            } else {
                0.0
            };
            let fill_height = this_row_h - rule_reserve_px;

            // Alternating stripe on odd rows (1, 3, …) — suppressed on
            // summary/total rows, which stand out against a clean background
            // (a summary row takes no stripe).
            if local_ri % 2 == 1 && !is_summary {
                if let Some(stripe_color) = th
                    .row
                    .stripe
                    .as_ref()
                    .and_then(|s| s.color.as_ref())
                    .filter(|c| !c.eq_ignore_ascii_case("transparent"))
                {
                    // Content span (`_compute_content_span(..., cell_pad=0)`,
                    // Not `svg_w` — an all-pinned table
                    // summing under `table_w` must not paint its stripe past
                    // its own last column, same defect the header rule fix
                    // above already addressed for the rule sites.
                    tree.rect(
                        Attrs::new()
                            .quick("x", outer_s.as_str())
                            .quick("y", row_y.to_string())
                            .quick("width", content_w_s.as_str())
                            .quick("height", fill_height.to_string())
                            .quick("fill", stripe_color.as_str()),
                    );
                }
            }

            // Per-role background fill and divider from row.roles.summary /
            // row.roles.total. Single line above a summary
            // row; double line (1px gap) above a total row.
            let role_theme = match role {
                RowRole::Total => Some(&th.row.roles.total),
                RowRole::Summary => Some(&th.row.roles.summary),
                RowRole::Value => None,
            };
            if let Some(rt) = role_theme {
                if let Some(bg) = sanitize_color(rt.background.as_deref())? {
                    // Content span, not `svg_w` — same
                    // fix as the row stripe above.
                    tree.rect(
                        Attrs::new()
                            .quick("x", outer_s.as_str())
                            .quick("y", row_y.to_string())
                            .quick("width", content_w_s.as_str())
                            .quick("height", fill_height.to_string())
                            .quick("fill", bg),
                    );
                }
                // The line above a summary row; a total row draws two with a
                // 1px gap, from its own divider, never
                // the summary's.
                let summary_divider = &rt.divider;
                if summary_divider.visible {
                    // `line_h` is `max(1, int(width))` — it truncates, distinct
                    // from the row line's `python_round` below — and `row_y` is
                    // `int(round(...))`, so
                    // `y_upper`/`y_lower`/`y_single` are production ints too:
                    // all stay bare, never `py_float`-wrapped like the
                    // genuinely-float row line's `y` below.
                    let line_h = summary_divider.width.trunc().max(1.0);
                    let (rule_x1, rule_x2) = (outer + pad, outer + content_w - pad);
                    let thickness = PyNum::Int(line_h);
                    if matches!(role, RowRole::Total) {
                        let gap = 1.0;
                        let y_upper = row_y - line_h - gap - line_h;
                        let y_lower = row_y - line_h;
                        for y_line in [y_upper, y_lower] {
                            emit_divider_segment(
                                tree,
                                rule_x1,
                                rule_x2,
                                PyNum::Int(y_line),
                                thickness,
                                summary_divider,
                                None,
                            );
                        }
                    } else {
                        emit_divider_segment(
                            tree,
                            rule_x1,
                            rule_x2,
                            PyNum::Int(row_y - line_h),
                            thickness,
                            summary_divider,
                            None,
                        );
                    }
                }
            }

            // Summary/total rows: heavier font weight. A
            // per-role font.weight takes precedence, and a TOTAL row falls back
            // to the SUMMARY role's weight before the medium 500 default (no
            // fallback to a flat `summary_font_weight`; this renderer doesn't
            // model one, only the per-role override).
            let row_font_weight: Option<String> = if is_summary {
                let own_weight = || -> Option<String> {
                    let f = role_theme?.font.as_ref()?;
                    f.weight
                        .is_some()
                        .then(|| f.weight_str())
                        .filter(|w| VALID_FONT_WEIGHTS.contains(&w.as_str()))
                };
                let summary_weight = || -> Option<String> {
                    let f = th.row.roles.summary.font.as_ref()?;
                    f.weight
                        .is_some()
                        .then(|| f.weight_str())
                        .filter(|w| VALID_FONT_WEIGHTS.contains(&w.as_str()))
                };
                let w = own_weight().or_else(|| {
                    matches!(role, RowRole::Total)
                        .then(summary_weight)
                        .flatten()
                });
                Some(w.unwrap_or_else(|| "500".to_string()))
            } else {
                None
            };

            // Row-scoped link band: one `<a>` per value
            // row wrapping a transparent hover rect behind the whole row —
            // never a per-cell fallthrough (a plain dimension cell stays
            // text; a cell's OWN `link:`, painted below, is a separate
            // anchor on top). Summary/total rows never get one. Emitted
            // BEFORE the per-column loop so document order matches
            // production's own paint order. Geometry mirrors the row
            // stripe/role rects just above (`outer_s`/`content_w_s`): this
            // renderer has no row-number gutter to exclude, so
            // `_compute_content_span`'s own gutter-excluding span collapses
            // to that same one already computed for every other horizontal
            // row element.
            let mut row_has_band = false;
            if let (Some(link_tpl), false) = (link, is_summary) {
                if let Some(row_href) = resolve_cell_link(link_tpl, &rows[ri], &all_data_columns)?
                    .map(|href| {
                        link_context
                            .map_or(href.clone(), |ctx| crate::href::resolve_href(&href, ctx))
                    })
                {
                    row_has_band = true;
                    let link_color =
                        css_color(link_color.ok_or("table: a link needs board_style.font_color")?)?;
                    let row_href_checked = crate::href::checked_href_diagnostic(&row_href)?;
                    let row_label = row_link_label_col
                        .and_then(|c| rows[ri].get(c))
                        .map(py_str)
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| row_href.clone());
                    // The row's hover scope: the band tints while the pointer
                    // is anywhere in the row, a cell's own link included.
                    tree.open(Open::Group, Attrs::new().quick("class", "dbt-table-row"));
                    tree.open(
                        Open::Link,
                        Attrs::new()
                            .quick("href", row_href_checked)
                            .quick("aria-label", row_label)
                            .quick_some("data-dbt-link-open", link_open.open_attr())
                            .quick_some("target", link_open.target()),
                    );
                    tree.rect(
                        Attrs::new()
                            .quick("class", "dbt-table-row-link")
                            .quick("fill", "transparent")
                            .quick("style", format!("--dbt-link: {link_color}"))
                            .quick("x", band_x_s.as_str())
                            .quick("y", row_y.to_string())
                            .quick("width", band_w_s.as_str())
                            .quick("height", this_row_h.to_string()),
                    );
                    tree.close(Open::Link);
                }
            }
            // In a banded row, content that is not itself a link must not
            // swallow the band's pointer events. The host
            // stylesheet keeps `.dbt-chart text` pointer-active, so this is a
            // class its `.dbt-pointer-inert` / `text.dbt-table-cell-inert`
            // rules read, never a `pointer-events` attribute they would outrank.
            let cell_pe_class = row_has_band.then_some("dbt-pointer-inert");

            // The index counts data rows across pages; a summary or total row
            // leaves it blank.
            if let (Some(ink), false) = (row_number_ink, is_summary) {
                let (x, anchor) = if row_numbers.align == "right" {
                    (outer + rn_w - pad, "end")
                } else {
                    (outer + pad, "start")
                };
                tree.text_el(
                    Attrs::new()
                        .quick("x", float_repr(x))
                        .quick("y", text_y_s.as_str())
                        .quick("font-size", font_size.to_string())
                        .quick("fill", ink)
                        .quick("text-anchor", anchor)
                        .quick("font-family", TAB_FONT)
                        .quick("data-col", "0"),
                    &(ri + 1).to_string(),
                );
            }
            for (ci, col) in cols.iter().enumerate() {
                let cx = col_x[ci];
                let cell = &cell_data[ci][local_ri];
                // The index in the drawn column list, the row-number column first.
                let data_col = ci + usize::from(row_numbers.visible);

                if is_swatch(col) {
                    let value = rows[ri].get(col.name.as_str()).unwrap_or(&Value::Null);
                    let Some(color) = swatch_color(value) else {
                        return Err(Diagnostic::new(
                            &codes::ERR_TABLE_SWATCH_NOT_COLOR,
                            [
                                ("chart_id", json!(self.chart_id)),
                                ("swatch_column", json!(col.name.as_str())),
                                ("row_index", json!(ri)),
                                ("value", value.clone()),
                            ],
                        ));
                    };
                    let swatch_x = python_round(cx + 4.0) as i64;
                    let swatch_y_s = float_repr(row_y + (this_row_h - SWATCH_SIZE) / 2.0);
                    tree.open(
                        Open::Group,
                        Attrs::new()
                            .quick("transform", format!("translate({swatch_x}, {swatch_y_s})"))
                            .quick_some("class", cell_pe_class),
                    );
                    tree.rect(
                        Attrs::new()
                            .quick("width", SWATCH_SIZE.to_string())
                            .quick("height", SWATCH_SIZE.to_string())
                            .quick("rx", "3")
                            .quick("fill", color),
                    );
                    tree.close(Open::Group);
                    continue;
                }

                // Spark cell placement law: a
                // `None`/absent cell value, or a cell whose spark content comes
                // back empty (a plain `bar` clamped to <= 0 — see the
                // `content.is_empty()` check below), draws no mark and falls
                // through to the ordinary cell paint below (only the empty-
                // content case is reached by this corpus). `spark_x = px(cell_x
                // + 4)` — an int, bare; `spark_y = row_y + (this_row_h -
                // spark_height) / 2` centers the mark in the row's own
                // (possibly grown) band — `this_row_h` is production's
                // `per_row_height` — and is a genuine float, so
                // `py_float`. Only the SIZING call below reads the NOMINAL row
                // height (`row_h`, not `this_row_h`): the mark's visual size
                // stays pinned to it so a wrapped row growing taller doesn't
                // grow its spark, but the mark still centers in the row's
                // actual (grown) band.
                if let Some(spark) = &col.cfg.spark {
                    if let Some(value) = rows[ri].get(col.name.as_str()) {
                        if !value.is_null() {
                            // `column` is the only ported-elsewhere mark whose
                            // width feeds its own centered alignment; the three
                            // marks this renderer supports all fill the cell
                            // width from the left-edge anchor, so the reported
                            // width is unused here.
                            // `if spark_content:` is a
                            // truthiness check, not a `None` check: a plain
                            // `bar` cell whose fill clamps to <= 0 returns
                            // `Some(("", w, h))` (no track's
                            // `fill_width > 0` gate) — falsy in Python, so
                            // production falls through to the ordinary cell
                            // paint below rather than drawing an empty mark.
                            let spark_layout = SparkLayout {
                                has_negative: signed_columns.contains(col.name.as_str()),
                                negative_tone: tones.map(|t| t.negative.as_str()),
                                font_size,
                                body_family,
                            };
                            if let Some((content, spark_w, spark_h)) = render_spark_cell(
                                col.name.as_str(),
                                value,
                                spark,
                                col_ws[ci],
                                row_h,
                                &th.spark,
                                bar_auto_max.get(col.name.as_str()).copied(),
                                &spark_layout,
                            )? {
                                if !content.is_empty() {
                                    // The narrow `column` mark centers under its header
                                    // on a lane column, else sits at the cell padding;
                                    // every other mark fills the cell from `cx + 4`.
                                    let spark_x = if spark.spark_type == "column" {
                                        if col.is_numeric || col.is_date {
                                            python_round(cx + (col_ws[ci] - spark_w) / 2.0) as i64
                                        } else {
                                            python_round(cx + pad) as i64
                                        }
                                    } else {
                                        python_round(cx + 4.0) as i64
                                    };
                                    let spark_y_s =
                                        float_repr(row_y + (this_row_h - spark_h) / 2.0);
                                    tree.open(
                                        Open::Group,
                                        Attrs::new()
                                            .quick(
                                                "transform",
                                                format!("translate({spark_x}, {spark_y_s})"),
                                            )
                                            .quick_some("class", cell_pe_class),
                                    );
                                    tree.extend(content);
                                    tree.close(Open::Group);
                                    continue;
                                }
                            }
                        }
                    }
                }

                // Overlay scale + when conditional formatting: computed once per cell, ahead of every render branch
                // below, since production paints the background rect before
                // deciding which text shape to draw.
                let raw_value = rows[ri].get(col.name.as_str()).filter(|v| !v.is_null());
                let cell_style =
                    resolve_cell_style(col, &rows[ri], raw_value, &scale_rows, role, tones)?;
                let (column_font_color, column_font_weight) = column_font_lanes(col, &rows[ri]);
                if let Some(bg) = &cell_style.background {
                    let cx_s = float_repr(cx);
                    let cw_s = float_repr(col_ws[ci]);
                    tree.rect(
                        Attrs::new()
                            .quick("x", cx_s)
                            .quick("y", row_y.to_string())
                            .quick("width", cw_s)
                            .quick("height", fill_height.to_string())
                            .quick("fill", bg.as_str())
                            .quick_some("class", cell_pe_class),
                    );
                }
                // Per-cell link: a column `link:` is
                // resolved per row, ahead of the color/weight cascade below —
                // production's own `cell_link` local plays the same role.
                // `if link_raw:` — an empty `link:` string is falsy in
                // production and never reaches the resolver.
                let cell_link: Option<String> = match &col.cfg.link {
                    Some(link_tpl) if !link_tpl.is_empty() => {
                        resolve_cell_link(link_tpl, &rows[ri], &all_data_columns)?.map(|href| {
                            link_context
                                .map_or(href.clone(), |ctx| crate::href::resolve_href(&href, ctx))
                        })
                    }
                    _ => rows[ri]
                        .get(col.name.as_str())
                        .filter(|v| {
                            !v.is_null() && filter_variables.iter().any(|f| f == col.name.as_str())
                        })
                        .map(|v| {
                            format!(
                                "?{}={}",
                                urlencode_plus(col.name.as_str()),
                                urlencode_plus(&py_str(v))
                            )
                        }),
                };

                // Color cascade: conditional → column font.color → link color
                // → body color. The link color is the BOARD's own font color
                // (`board_style.font_color`), required when a link exists.
                // An authored column color that cannot be used still stands in
                // for the link color: the cell then takes the body color.
                let column_color_authored = col
                    .cfg
                    .font
                    .as_ref()
                    .and_then(|f| f.color.as_deref())
                    .is_some_and(|c| !c.is_empty());
                let fill_color = cell_style
                    .color
                    .as_deref()
                    .or(column_font_color.as_deref())
                    .unwrap_or(if cell_link.is_some() && !column_color_authored {
                        link_color.ok_or("table: a link needs board_style.font_color")?
                    } else {
                        body_color
                    });
                // Weight cascade: conditional → column
                // font.weight → summary/total role weight
                // (`row_font_weight_attr`) → 500 for a linked cell → nothing.
                let cell_font_weight: Option<String> = cell_style
                    .weight
                    .clone()
                    .filter(|w| VALID_FONT_WEIGHTS.contains(&w.as_str()))
                    .or_else(|| {
                        column_font_weight
                            .clone()
                            .filter(|w| VALID_FONT_WEIGHTS.contains(&w.as_str()))
                    })
                    .or_else(|| row_font_weight.clone())
                    .or_else(|| cell_link.is_some().then(|| "500".to_string()));
                let font_style = cell_style.style.as_ref();
                let font_decoration = cell_style.decoration.as_ref();

                // A formatless null fails production's per-cell
                // `_is_numeric_cell` gate and takes the
                // non-lane branch, which places it by the column's RESOLVED
                // `align` field — None for a plain
                // numeric column (fill_table_column_defaults leaves it unset;
                // the three-lane invariant is unconditional on `align` for
                // numeric-VALUED cells only), "right" for a date lane. So a
                // plain column's null paints START-anchored at content left,
                // an align-right column's null END-anchors at the lane's own
                // number_x — both in the BODY font with no tabular styling
                // (`_wants_tabular_font(None, no-format)` is False,
                // A null in a column WITH an explicit
                // format: stays on the tabular lane path unchanged. This
                // branch pre-dates conditional formatting and does not (yet)
                // honor a rule's color/glyph — no corpus row has a null cell in
                // a CF-active numeric column (see AGENTS.md).
                // Anchor wrapper (2712): opens before every
                // shape branch below since production paints the background
                // rect outside the anchor but the cell's own text inside it,
                // and closes after all of them share one `</g></a>` — a linked
                // cell's `<text>` also carries the `dbt-table-link-text` class
                // production stamps on it.
                let link_href = cell_link
                    .as_deref()
                    .map(crate::href::checked_href_diagnostic)
                    .transpose()?;
                if let Some(href) = link_href {
                    // Only the column's own `link:` follows its mode; the
                    // `?col=value` filter link is implied, so it is page.
                    let open = if col.cfg.link.as_deref().is_some_and(|l| !l.is_empty()) {
                        col.cfg.link_open
                    } else {
                        crate::href::LinkOpen::Page
                    };
                    tree.open(
                        Open::Link,
                        Attrs::new()
                            .quick("href", href)
                            .quick_some("data-dbt-link-open", open.open_attr())
                            .quick_some("target", open.target()),
                    );
                    tree.open(
                        Open::Group,
                        Attrs::new()
                            .quick("class", "dbt-table-link")
                            .quick("style", format!("color: {}", css_color(fill_color)?)),
                    );
                }
                // The link-text class for a wired cell; else, in a banded row,
                // the inert-cell class.
                let cell_link_class = if cell_link.is_some() {
                    Some("dbt-table-link-text")
                } else if row_has_band {
                    Some("dbt-table-cell-inert")
                } else {
                    None
                };

                let cell_raw = rows[ri].get(col.name.as_str());
                let plain_in_date_lane = is_plain_date_lane_cell(col, cell_raw);
                let tabular_text = wants_tabular_font(col, cell_raw);
                let null_body_font = cell.is_null && col.cfg.format.is_none();
                if (col.is_numeric || col.is_date) && null_body_font {
                    let (x, anchor) = match col.cfg.align.as_deref() {
                        Some("center") => (cx + col_ws[ci] / 2.0, "middle"),
                        Some("right") => (number_xs[ci], "end"),
                        _ => (cx + pad, "start"),
                    };
                    tree.text_el(
                        Attrs::new()
                            .quick_some("class", cell_link_class)
                            .quick("x", float_repr(x))
                            .quick("y", text_y_s.as_str())
                            .quick("font-size", font_size.to_string())
                            .quick("fill", fill_color)
                            .quick("text-anchor", anchor)
                            .quick("font-family", body_family)
                            .quick_some("font-weight", row_font_weight.as_deref())
                            .quick("data-col", data_col.to_string()),
                        &cell.text,
                    );
                } else if (col.is_numeric || col.is_date)
                    && !plain_in_date_lane
                    && !is_date_like_cell(cell_raw)
                    && is_numeric_cell(col, cell_raw)
                {
                    // The thumbnail redraw reads the raw number off the cell.
                    let lane_value = coerce_numeric_cell(rows[ri].get(col.name.as_str()))
                        .filter(|v| v.is_finite())
                        .map(|v| match rows[ri].get(col.name.as_str()) {
                            Some(Value::Number(n)) if !n.is_f64() => n.to_string(),
                            _ => format!("{v:?}"),
                        });
                    tree.open(
                        Open::Text,
                        Attrs::new()
                            .quick_some("class", cell_link_class)
                            .quick("y", text_y_s.as_str())
                            .quick("font-size", font_size.to_string())
                            .quick("fill", fill_color)
                            .quick("font-family", tab_family)
                            .quick("style", TAB_STYLE)
                            .quick_some("font-weight", cell_font_weight.as_deref())
                            .quick_some("font-style", font_style)
                            .quick_some("text-decoration", font_decoration)
                            .quick("data-col", data_col.to_string())
                            .quick_some("data-value", lane_value),
                    );
                    // Prefix tspan: end-anchored at prefix_x (left of the
                    // number), its own tspan — invariant #11's symbol lane. A
                    // glyph replaces the format prefix entirely and carries its own
                    // `fill` only when BOTH a glyph and a glyph_color are set —
                    // otherwise it inherits the enclosing `<text>`'s fill.
                    let full_prefix = cell_style.glyph.as_deref().or(cell.prefix.as_deref());
                    if let Some(prefix) = full_prefix {
                        tree.tspan(
                            Attrs::new()
                                .quick("x", float_repr(prefix_xs[ci]))
                                .quick("text-anchor", "end")
                                .quick_some(
                                    "fill",
                                    cell_style
                                        .glyph
                                        .as_ref()
                                        .and(cell_style.glyph_color.as_deref()),
                                ),
                            prefix,
                        );
                    }
                    // A date-like cell leaves the lane branch for the cutting text
                    // branch, so a date wider than its column is cut, not spilled.
                    let number_text = if col.is_date
                        && !(is_numeric_cell(col, cell_raw) && !is_date_like_cell(cell_raw))
                    {
                        self.cut_cell(ci, &cell.text, cell_style.glyph.as_deref())?
                    } else {
                        cell.text.clone()
                    };
                    tree.tspan(
                        Attrs::new()
                            .quick("x", float_repr(number_xs[ci]))
                            .quick("text-anchor", "end"),
                        &number_text,
                    );
                    if let Some(suffix) = &cell.suffix {
                        tree.tspan(
                            Attrs::new()
                                .quick("x", float_repr(suffix_xs[ci]))
                                .quick("text-anchor", "start"),
                            suffix,
                        );
                    }
                    tree.close(Open::Text);
                } else {
                    // Column-level `align`: center is the
                    // cell midpoint, right the padded right edge; a text cell
                    // has no lane, so nothing else moves it.
                    // A right-aligned or date-shaped cell of a lane column sits
                    // on the lane's number edge.
                    let in_lane = col.is_numeric || col.is_date;
                    let (tx, anchor) = match col.cfg.align.as_deref() {
                        Some("center") => (cx + col_ws[ci] / 2.0, "middle"),
                        align
                            if in_lane
                                && (align == Some("right") || is_date_like_cell(cell_raw)) =>
                        {
                            (number_xs[ci], "end")
                        }
                        Some("right") => (cx + col_ws[ci] - pad, "end"),
                        _ => (cx + pad, "start"),
                    };
                    let (cell_family, tabular_style) = if tabular_text {
                        (tab_family, Some(TAB_STYLE))
                    } else {
                        (body_family, None)
                    };
                    let tx_s = float_repr(tx);
                    // Glyph for non-three-lane cells (text/date/null): a leading
                    // colored tspan, first line only for a wrapped cell.
                    let glyph_prefix = cell_style.glyph.as_deref();
                    let emit_glyph_prefix = |tree: &mut Tree| {
                        if let Some(glyph) = glyph_prefix {
                            tree.tspan(
                                Attrs::new().quick_some("fill", cell_style.glyph_color.as_deref()),
                                &format!("{glyph} "),
                            );
                        }
                    };
                    match &wrapped_lines[ci][ri] {
                        Some(lines) => {
                            // Vertically center the wrapped block in the (grown)
                            // row: `first_y = row_y + (per_row_height
                            // - total_text_h) / 2 + font_size`.
                            let line_height = font_size + th.text_baseline_offset;
                            let total_text_h = lines.len() as f64 * line_height;
                            let first_y = row_y + (this_row_h - total_text_h) / 2.0 + font_size;
                            tree.open(
                                Open::Text,
                                Attrs::new()
                                    .quick_some("class", cell_link_class)
                                    .quick("x", tx_s.as_str())
                                    .quick("font-size", font_size.to_string())
                                    .quick("fill", fill_color)
                                    .quick("text-anchor", anchor)
                                    .quick("font-family", cell_family)
                                    .quick_some("font-weight", cell_font_weight.as_deref())
                                    .quick_some("font-style", font_style)
                                    .quick_some("text-decoration", font_decoration)
                                    .quick("data-col", data_col.to_string()),
                            );
                            for (li, line) in lines.iter().enumerate() {
                                let ly = first_y + li as f64 * line_height;
                                tree.open(
                                    Open::Tspan,
                                    Attrs::new()
                                        .quick("x", tx_s.as_str())
                                        .quick("y", float_repr(ly)),
                                );
                                if li == 0 {
                                    emit_glyph_prefix(tree);
                                }
                                tree.text(line);
                                tree.close(Open::Tspan);
                            }
                            tree.close(Open::Text);
                        }
                        None => {
                            // The non-wrapping cell branch: a
                            // numeric/date-like cell, or any cell of a `wrap: false`
                            // table, is cut to its content area less the glyph.
                            let text = if !th.wrap || tabular_text {
                                self.cut_cell(ci, &cell.text, cell_style.glyph.as_deref())?
                            } else {
                                cell.text.clone()
                            };
                            tree.open(
                                Open::Text,
                                Attrs::new()
                                    .quick_some("class", cell_link_class)
                                    .quick("x", tx_s.as_str())
                                    .quick("y", text_y_s.as_str())
                                    .quick("font-size", font_size.to_string())
                                    .quick("fill", fill_color)
                                    .quick("text-anchor", anchor)
                                    .quick("font-family", cell_family)
                                    .quick_some("style", tabular_style)
                                    .quick_some("font-weight", cell_font_weight.as_deref())
                                    .quick_some("font-style", font_style)
                                    .quick_some("text-decoration", font_decoration)
                                    .quick("data-col", data_col.to_string()),
                            );
                            emit_glyph_prefix(tree);
                            tree.text(&text);
                            tree.close(Open::Text);
                        }
                    }
                }
                if cell_link.is_some() {
                    tree.close(Open::Group);
                    tree.close(Open::Link);
                }
            }
            if row_has_band {
                tree.close(Open::Group);
            }

            if will_draw_row_rule {
                // `rule_y = row_y + per_row_height - rule_reserve_px`
                // is an int in production: `row_y` is `int(round(...))` and
                // every per-row height is an int, so it prints bare like the
                // summary/total lines' `y`.
                let rule_y = row_y + this_row_h - rule_reserve_px;
                // On a linked table the divider must not catch the pointer,
                // or it reopens a dead strip in the band beneath it
                // Unlinked tables stay byte-identical.
                let rule_class = link.is_some().then_some("dbt-pointer-inert");
                emit_divider_segment(
                    &mut row_rules,
                    outer + pad,
                    outer + content_w - pad,
                    PyNum::Int(rule_y),
                    PyNum::Int(rule_reserve_px),
                    row_divider,
                    rule_class,
                );
            }
        }
        // Row rules paint after every row's own fill/text, on top of them —
        // Deferred row rules flush after the rows.
        tree.extend(row_rules.finish());
        Ok(())
    }
}

/// The lines a title or subtitle draws in `limit` px, and whether the pre-pass
/// cut it; no limit leaves the text whole, and an empty result falls back to
/// the text (`splitlines() or [text]`).
pub(super) fn wrap_title(
    text: &str,
    mode: Option<&str>,
    limit: Option<f64>,
    font_size: f64,
    family: &str,
    fonts: &FontBook,
) -> Result<(Vec<String>, bool), String> {
    let Some(limit) = limit.filter(|_| !text.is_empty()) else {
        return Ok((vec![text.to_string()], false));
    };
    let (lines, truncated) =
        crate::common::prepare_title_lines(text, mode, limit, font_size, family, fonts)?;
    Ok((
        if lines.is_empty() {
            vec![text.to_string()]
        } else {
            lines
        },
        truncated,
    ))
}

pub(super) fn title_band(
    title: Option<&str>,
    subtitle: Option<&str>,
    title_style: Option<&TableTitleStyle>,
    title_font: Option<&Font>,
    th: &TableTheme,
    table_width: f64,
    shift: TitleShift,
    fonts: &FontBook,
) -> Result<TitleBand, String> {
    // Size/weight/family come from the width-tiered `style.title_font`
    // (production's `table_style.title_font`), not `title_style.font` (the
    // untiered base) — a narrow table renders a smaller title tier, which
    // also feeds the subtitle-band math below. Color still comes from
    // `title_style.font.color` (table.py's `colors["title_color"]`), which
    // the width tier does not touch. Production reads the font and asserts
    // both inks for every table, titled or not (`table.py:4384-4393`,
    // `:4443-4447`), so an absent slot refuses by name.
    let title_font = title_font.ok_or("table: style.title_font absent (theme required)")?;
    let title_font_size = title_font
        .size
        .ok_or("table: style.title_font.size absent (theme required)")?;
    let title_family = title_font
        .family
        .as_deref()
        .ok_or("table: style.title_font.family absent (theme required)")?;
    if title_font.weight.is_none() {
        return Err("table: style.title_font.weight absent (theme required)".into());
    }
    let title_weight = title_font.weight_str();
    let title_style = title_style.ok_or("table: title_style absent (theme required)")?;
    let title_color = title_style
        .font
        .color
        .as_deref()
        .ok_or("table: title_style.font.color absent (theme required)")?;
    let subtitle_style = title_style
        .subtitle
        .as_ref()
        .ok_or("table: title_style.subtitle absent (theme required)")?;
    let subtitle_color = subtitle_style
        .font
        .color
        .as_deref()
        .ok_or("table: title_style.subtitle.font.color absent (theme required)")?;

    let has_title = title.is_some_and(|t| !t.is_empty());
    // Production cases the title before measuring/painting
    // (compute_table_title_block_layout); the subtitle is
    // NOT cased there — prepare_title_text runs on the authored subtitle.
    let cased_title: Option<String> = match title {
        Some(t) => match title_style.font.case.as_deref() {
            Some(c) if c != "none" => Some(display_text::case::apply_case(t, c)?),
            _ => Some(t.to_string()),
        },
        None => None,
    };
    // Title baseline = round(font_size * 0.8), the title ascent ratio, fitted against Vega-Lite's own title baseline
    // at all three width-tiered sizes (11/14/18px) production can pick.
    let title_text_y = crate::sizing::title_ascent(title_font_size);
    // `int(title_bottom - padding + 8)` with
    // `title_bottom = baseline + font_size * 0.5` (the first two terms are the
    // padding-free `title_text_y`), which is `font_size + 13` only at 18px.
    // `compute_title_limit(table_width, outer_padding)`: `int(width - 2*pad)`.
    let limit = {
        let l = (table_width - 2.0 * th.outer_padding.trunc()).trunc();
        (table_width > 0.0 && l > 0.0).then_some(l.max(1.0))
    };
    let title_line_h = title_font_size.trunc() + 2.0;
    let (title_lines, title_truncated) = match cased_title.as_deref().filter(|_| has_title) {
        Some(t) => wrap_title(
            t,
            title_style.overflow.as_deref(),
            limit,
            title_font_size.trunc(),
            measure::stand_in_family(title_family),
            fonts,
        )?,
        None => (Vec::new(), false),
    };
    let last_title_text_y = title_text_y + (title_lines.len().max(1) - 1) as f64 * title_line_h;
    let natural_title_area_h_raw = if has_title {
        (last_title_text_y + title_font_size * 0.5 + 8.0).floor()
    } else {
        0.0
    };

    let has_subtitle = has_title && subtitle.is_some();
    // `table.py:340-355`: the size is asserted only when a subtitle paints;
    // production's own unpainted value is 0.0.
    let subtitle_font_size = if has_subtitle {
        subtitle_style
            .font
            .size
            .ok_or("table: theme must supply title.subtitle.font.size")?
    } else {
        0.0
    };
    let subtitle_family = if has_subtitle {
        svg_font_family(
            th.font
                .family
                .as_deref()
                .ok_or("table: a subtitle needs style.table.font.family")?,
        )
    } else {
        String::new()
    };
    // Mirrors table.py's `_subtitle_baseline_below_title`: title_subtitle_gap
    // is pure whitespace between the title's descent and the subtitle's
    // ascent, not a baseline-to-baseline distance — both descent and ascent
    // are derived from their own font size via the same 0.8 ascent ratio the
    // title baseline itself approximates (title_text_y above).
    let (subtitle_lines, subtitle_truncated) = match subtitle.filter(|_| has_subtitle) {
        Some(sub) => wrap_title(
            sub,
            subtitle_style.overflow.as_deref(),
            limit,
            subtitle_font_size,
            measure::stand_in_family(&subtitle_family),
            fonts,
        )?,
        None => (Vec::new(), false),
    };
    let subtitle_baseline_below = |title_baseline: f64| {
        python_round(
            title_baseline
                + title_font_size * 0.2
                + th.title_subtitle_gap
                + subtitle_font_size * 0.8,
        )
    };
    let subtitle_text_y = subtitle_baseline_below(last_title_text_y);
    // `title.py`'s title_bottom/subtitle_bottom tail term (+8) plus the
    // subtitle's own descent (font_size * 0.5). Raw/gap-free, same scale as
    // `natural_title_area_h_raw` and `th.title_row.height` below.
    let subtitle_area_h_raw = subtitle_text_y
        + (subtitle_lines.len().max(1) - 1) as f64 * (subtitle_font_size + 2.0)
        + subtitle_font_size * 0.5
        + 8.0;

    // The title_row.height floor only fires with a subtitle present — see
    // TitleRowTheme's doc comment. Without one, natural_title_area_h_raw is
    // unchanged from before this feature existed. All three arms are raw
    // (gap-free); the gap is folded in once after the max(), never inside
    // an individual arm — otherwise whichever new term (subtitle_area_h_raw,
    // title_row.height) wins the max silently drops the compensation every
    // other arm relies on.
    // Production truncates each arm of the max to int (`int(title_row.height)`, `int(subtitle_bottom - padding + 8)`):
    // the subtitle arm lands on x.5 at the shipped 11px subtitle size, and
    // painting the band un-truncated shifts every header/row/rule 0.5px
    // below production.
    let title_area_h = if has_subtitle {
        natural_title_area_h_raw
            .max(th.title_row.height.floor())
            .max(subtitle_area_h_raw.floor())
    } else {
        natural_title_area_h_raw
    };

    // The row's title shift (`:4455`): the baselines move down
    // `title_dy` — the subtitle's rounded afresh from the moved title baseline,
    // as `_subtitle_baseline_below_title` does — and the header and rows move
    // down `body_dy`. The band's own natural height above stays the unshifted
    // `compute_table_title_block_layout` height.
    let subtitle_drawn_y = subtitle_baseline_below(last_title_text_y + shift.title_dy);
    Ok(TitleBand {
        has_title,
        cased_title,
        authored_title: title.map(str::to_string),
        subtitle: subtitle.map(str::to_string),
        has_subtitle,
        font_size: title_font_size,
        family: title_family.to_string(),
        color: title_color.to_string(),
        weight: title_weight,
        text_y: title_text_y + shift.title_dy,
        title_lines,
        subtitle_lines,
        title_truncated,
        subtitle_truncated,
        subtitle_font_size,
        subtitle_family,
        subtitle_color: subtitle_color.to_string(),
        subtitle_text_y: subtitle_drawn_y,
        area_h: title_area_h + shift.body_dy,
    })
}

/// A titled table's `(ascent, tail)` in the row's title band, measured from the
/// table's own top edge (`title_band.py:116-132`): the baseline sits
/// `int(outer_padding)` plus `title_ascent` below it, and the body starts where
/// `compute_table_title_block_layout`'s block ends. `width` is the table's slot
/// width, which wraps the title.
pub fn title_geometry(
    resolved: &Value,
    width: f64,
    fonts: &FontBook,
) -> Result<(f64, f64), String> {
    let r = TitleOnly::deserialize(resolved).map_err(|e| format!("table title: {e}"))?;
    let th = &r.style.table;
    let band = title_band(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.title_style.as_ref(),
        r.style.title_font.as_ref(),
        th,
        width,
        TitleShift::default(),
        fonts,
    )?;
    Ok((
        th.outer_padding.trunc() + band.text_y,
        band.area_h - band.text_y,
    ))
}

impl TitleBand {
    fn truncations(&self, chart_id: &str) -> Vec<Diagnostic> {
        crate::common::title_truncations(
            chart_id,
            (self.cased_title.as_deref(), self.title_truncated),
            (self.subtitle.as_deref(), self.subtitle_truncated),
        )
    }
}

pub(super) fn emit_title_band(tree: &mut Tree, band: &TitleBand, outer: f64) {
    let (title_font_size, title_weight) = (band.font_size, &band.weight);
    let (title_color, title_family) = (&band.color, &band.family);
    // The band starts at `current_y = padding`, so both
    // baselines sit `outer_padding` below the band's own origin.
    let (title_text_y, subtitle_text_y) = (outer + band.text_y, outer + band.subtitle_text_y);
    let (subtitle_font_size, subtitle_color, subtitle_family) = (
        band.subtitle_font_size,
        &band.subtitle_color,
        &band.subtitle_family,
    );
    if let (true, Some(title_text)) = (band.has_title, band.cased_title.as_deref()) {
        tree.open(
            Open::Text,
            Attrs::new()
                .quick("x", outer.to_string())
                .quick("y", float_repr(title_text_y))
                .quick("font-size", title_font_size.to_string())
                .quick("font-weight", title_weight.as_str())
                .quick("fill", title_color.as_str())
                .quick("font-family", title_family.as_str())
                .quick("data-authored-kind", "title"),
        );
        // The full text rides along when the drawn lines differ from it.
        if band.title_lines.join("\n") != title_text {
            tree.title(band.authored_title.as_deref().unwrap_or(title_text));
        }
        let title_line_h = title_font_size.trunc() + 2.0;
        for (i, line) in band.title_lines.iter().enumerate() {
            tree.tspan(
                Attrs::new()
                    .quick("x", outer.to_string())
                    .quick("y", float_repr(title_text_y + i as f64 * title_line_h)),
                line,
            );
        }
        tree.close(Open::Text);
        if let (true, Some(subtitle_text)) = (band.has_subtitle, band.subtitle.as_deref()) {
            tree.open(
                Open::Text,
                Attrs::new()
                    .quick("x", outer.to_string())
                    .quick("font-size", float_repr(subtitle_font_size))
                    .quick("fill", subtitle_color.as_str())
                    .quick("font-family", subtitle_family.as_str())
                    .quick("data-authored-kind", "subtitle"),
            );
            if band.subtitle_lines.join(" ") != subtitle_text {
                tree.title(subtitle_text);
            }
            for (i, line) in band.subtitle_lines.iter().enumerate() {
                tree.tspan(
                    Attrs::new().quick("x", outer.to_string()).quick(
                        "y",
                        float_repr(subtitle_text_y + i as f64 * (subtitle_font_size + 2.0)),
                    ),
                    line,
                );
            }
            tree.close(Open::Text);
        }
    }
}

/// A table over no rows: an em dash centered in a canvas that holds
/// the title band and the header band. The canvas
/// height is `_resolve_visible_rows`' content height with `rows_height == 0` —
/// the no-slot path and the slot path's grow-by-2
/// shortcut agree on it — plus nothing for the pager (one
/// page, `pagination_active` false) or a summary gap (no
/// rows). A slot shorter than that content takes the
/// pagination-reserve branch instead, which is not ported.
fn lay_out_empty(
    blank: &Blank,
    width: f64,
    slot: Option<f64>,
    measure: &Measure,
) -> Result<Layout, Diagnostic> {
    let Blank {
        resolved: r,
        padding,
        title_shift,
    } = blank;
    let th = &r.style.table;
    let band = title_band(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.title_style.as_ref(),
        r.style.title_font.as_ref(),
        th,
        width,
        *title_shift,
        measure.fonts,
    )?;
    let mut diagnostics = Vec::new();
    if let Some(id) = r.id.as_deref() {
        diagnostics.extend(band.truncations(id));
    }
    let outer = th.outer_padding.trunc();
    // `:655`: a hidden header collapses both the band and the
    // header/body gap.
    let (header_height, header_body_gap) = if th.header.visible {
        (th.header.height.trunc(), (th.row.height * 0.25).trunc())
    } else {
        (0.0, 0.0)
    };
    let body_bottom = band.area_h + header_height + header_body_gap;
    let table_height = body_bottom + outer + th.bottom_padding.trunc();
    if let Some(slot_h) = slot.filter(|&h| h < table_height) {
        return Err(format!(
            "table: empty result set in a {slot_h}px slot shorter than its {table_height}px content \
             not supported"
        )
        .into());
    }
    let empty_state = th
        .empty_state
        .as_ref()
        .ok_or("table: empty result set needs style.table.empty_state")?;
    let ink = empty_state
        .font
        .color
        .as_deref()
        .ok_or("table: empty result set needs style.table.empty_state.font.color")?;
    let size = empty_state
        .font
        .size
        .ok_or("table: empty result set needs style.table.empty_state.font.size")?;
    let family = svg_font_family(
        th.font
            .family
            .as_deref()
            .ok_or("table: empty result set needs style.table.font.family")?,
    );
    let mut tree = Tree::new();
    tree.open_svg(Extent { width, height: table_height });
    // The card rect only when the table authors a background.
    if let Some(bg) = th
        .background
        .as_deref()
        .filter(|b| !b.is_empty() && !b.eq_ignore_ascii_case("transparent"))
    {
        let (bx, by, bw, bh) = card_box(width, table_height, padding.as_ref());
        tree.rect(
            Attrs::new()
                .quick("x", num_full(bx))
                .quick("y", num_full(by))
                .quick("width", num_full(bw))
                .quick("height", num_full(bh))
                .quick("fill", bg)
                .quick("rx", "4"),
        );
    }
    emit_title_band(&mut tree, &band, outer);
    tree.text_el(
        Attrs::new()
            .quick("x", float_repr(width / 2.0))
            .quick("y", float_repr(table_height / 2.0))
            .quick("font-size", float_repr(size))
            .quick("fill", ink)
            .quick("text-anchor", "middle")
            .quick("font-family", family.as_str()),
        "\u{2014}",
    );
    tree.close(Open::Svg);
    Ok(Layout {
        data_box: Rect {
            x: outer,
            y: band.area_h,
            width: width - 2.0 * outer,
            height: body_bottom - band.area_h,
        },
        insets: Insets {
            top: band.area_h,
            right: outer,
            bottom: table_height - body_bottom,
            left: outer,
        },
        nodes: tree.finish(),
        diagnostics,
        title_pos: None,
    })
}

/// The effective header overflow mode for `col`: the column's own, else the
/// chart's promoted one, else the table style's, else the theme's
/// (`resolve_header_overflow`).
pub(super) fn header_overflow_mode<'a>(
    r: &'a Resolved,
    th: &'a TableTheme,
    col: &'a ColMeta,
) -> Result<&'a str, String> {
    col.cfg
        .header_overflow
        .as_deref()
        .or(r.header_overflow.as_deref())
        .or(th.header_overflow.as_deref())
        .or(th.header.overflow.as_deref())
        .ok_or_else(|| {
            format!(
                "table payload: no header overflow mode for column '{}'",
                col.name.as_str()
            )
        })
}

/// `resolve_wrapped_headers`: each header on one
/// line when it fits the cell's content area `int(cw - 2*pad)` under real glyph
/// metrics, else wrapped by the column's overflow mode. Only `wrap-two` (the
/// theme default) and `wrap` are drawn; a header that needs `clip` or
/// `truncate` refuses by name.
pub(super) fn wrapped_headers(
    cols: &[ColMeta],
    col_ws: &[f64],
    pad: f64,
    header_font_size: f64,
    header_measure_family: &str,
    r: &Resolved,
    th: &TableTheme,
    fonts: &FontBook,
) -> Result<Vec<WrappedHeader>, String> {
    let mut out = Vec::with_capacity(cols.len());
    for (col, &cw) in cols.iter().zip(col_ws) {
        let content_area = (cw - pad * 2.0).trunc().max(1.0);
        if fonts.measure(&col.header, header_measure_family, header_font_size)? <= content_area {
            out.push(WrappedHeader {
                lines: vec![col.header.clone()],
                truncated: false,
            });
            continue;
        }
        let mode = header_overflow_mode(r, th, col)?;
        let (lines, truncated) = crate::common::prepare_title_lines(
            &col.header,
            Some(mode),
            content_area,
            header_font_size,
            header_measure_family,
            fonts,
        )?;
        let lines = if lines.is_empty() {
            vec![col.header.clone()]
        } else {
            lines
        };
        out.push(WrappedHeader { lines, truncated });
    }
    Ok(out)
}

/// `_column_demands` over `measure_column_demands` and
/// `measure_column_word_floors`: every string is
/// the FORMATTED painted cell text (prefix+number+suffix for numeric lanes),
/// measured with the Inter stand-in (get_font_path's non-numeric branch
/// measures every non-serif family as Inter — the same documented deliberate
/// under-measure kpi.rs mirrors) at body size; demand = p95 + 2*pad, floor =
/// widest token across the sampled cells' formatted text AND the header label
/// (header at header size) + 2*pad, applied to every column, numerics
/// included.
#[allow(clippy::too_many_arguments)]
pub(super) fn column_inputs(
    cols: &[ColMeta],
    cell_data: &[Vec<Cell>],
    rows: &[serde_json::Map<String, Value>],
    fonts: &FontBook,
    families: &MeasureFonts,
    font_size: f64,
    header_size: f64,
    header_visible: bool,
    pad: f64,
) -> Result<ColumnInputs, String> {
    let measure = Measure { fonts };
    let mut demands: BTreeMap<String, f64> = BTreeMap::new();
    let mut header_demands: BTreeMap<String, f64> = BTreeMap::new();
    let mut word_floors: BTreeMap<String, f64> = BTreeMap::new();
    for (col, cells) in cols.iter().zip(cell_data.iter()) {
        // A swatch cell holds a color, not text: the painted square sets the
        // width (`SWATCH_CELL_DEMAND`).
        if is_swatch(col) {
            demands.insert(col.name.to_string(), SWATCH_CELL_DEMAND + 2.0 * pad);
            header_demands.insert(col.name.to_string(), SWATCH_CELL_DEMAND);
            word_floors.insert(col.name.to_string(), SWATCH_CELL_DEMAND + 2.0 * pad);
            continue;
        }
        demands.insert(
            col.name.to_string(),
            column_demand(cells, rows, col, fonts, families.body, font_size, pad)?,
        );
        // A hidden header paints no label, so it demands no width and sets
        // no floor.
        header_demands.insert(
            col.name.to_string(),
            if header_visible {
                fonts.measure(&col.header, families.header, header_size)?
            } else {
                0.0
            },
        );

        // Widest unbreakable token: header label's tokens at header size,
        // cell tokens at body size (`_measure_min_word_width`,
        //), with the URL/40-char candidate exclusions.
        // Cells are tokenized by what they paint (`_rendered_cell_text`,
        //): a currency column's floor carries its
        // painted "$24.7M", never the raw "24730000.0".
        let texts: Vec<String> = cells
            .iter()
            .take(measure::SAMPLE_ROWS)
            .map(measured_cell_text)
            .collect();
        let mut token_w = if header_visible {
            measure.widest_token(&col.header, families.header, header_size)?
        } else {
            0.0
        };
        for text in &texts {
            token_w = token_w.max(measure.widest_token(text, families.body, font_size)?);
        }
        // A cell that paints a glyph competes with its own `glyph run +
        // widest token` — the glyph shares a line with the word after it
        // and cannot break away from it.
        // Measured PER CELL and maxed separately from the glyph-free
        // token_w above, then folded in at the end: the widest token and
        // the widest glyph run can belong to different cells (or, for the
        // header, to no painted cell at all), so pairing them column-wide
        // would reserve a first-line unit nothing renders.
        if glyph_possible(col) {
            let mut glyph_term = 0.0_f64;
            for (row, text) in rows.iter().zip(&texts) {
                let raw_value = row.get(col.name.as_str()).filter(|v| !v.is_null());
                let glyph = resolve_cell_glyph(&col.cfg, &col.when_rules, raw_value)?;
                let glyph_w =
                    fonts.measure(&glyph_run(glyph.as_deref()), families.body, font_size)?;
                glyph_term =
                    glyph_term.max(glyph_w + measure.widest_token(text, families.body, font_size)?);
            }
            token_w = token_w.max(glyph_term);
        }
        word_floors.insert(col.name.to_string(), token_w + 2.0 * pad);
    }
    Ok(ColumnInputs {
        demands,
        header_demands,
        word_floors,
    })
}

/// The table drawn into `cell`: the root `svg` is the card, so only the cell's
/// size is read. `cell.height` is the slot a bounded table fits (pagination),
/// already grown by its row's title shift.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    if cell.height <= 0.0 {
        return Err(Diagnostic::from(format!(
            "table: layout needs a cell taller than 0, got {}",
            cell.height
        )));
    }
    lay_out(bound, cell.width, Some(cell.height), measure)
}

/// `layout` at `width`; `slot` is the height a bounded table fits, `None` the
/// unbounded measure (a sizer's `height=None`). The two diverge when pagination
/// is on: unbounded clamps to `page_rows` and derives its own height, while a
/// slot pages to fit it and pins the height to it, so `layout` at the natural
/// height is not the natural render. `render` and `height_for_width` take the
/// unbounded path.
pub(super) fn lay_out(
    bound: &Bound,
    width: f64,
    slot: Option<f64>,
    measure: &Measure,
) -> Result<Layout, Diagnostic> {
    let Rows { chart, drawn } = match bound {
        Bound::Rows(rows) => rows,
        Bound::Empty(blank) => return lay_out_empty(blank, width, slot, measure),
    };
    let fonts = measure.fonts;
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let _formats = BoardFormats::enter(chart.formats.as_ref(), chart.sub_unit_specs.as_ref());
    let r = &chart.resolved;
    let th = &r.style.table;
    let rows = &chart.rows[..];
    let cols = &chart.cols;
    let n_rows = rows.len();
    let chart_id = r.id.as_deref().unwrap_or_default();
    let header_visible = th.header.visible;
    let look = &drawn.look;
    let mut font_size = look.font_size;
    let mut header_size = look.header_size;
    let mut row_h = look.row_h;
    let pad = look.pad;
    let outer = look.outer;
    let body_family = look.body_family.clone();
    let header_family = look.header_family.clone();
    let header_weight = look.header_weight.clone();
    let body_color = look.body_color.as_str();
    let header_color = look.header_color.as_str();
    let link_color = look.link_color.as_deref();
    let tab_family_owned = if look.numeral_family.contains("Source Serif") {
        svg_font_family(&look.numeral_family)
    } else {
        look.numeral_family.clone()
    };
    let tab_family: &str = &tab_family_owned;
    let tones = drawn.board_style.as_ref().and_then(|b| b.tones.as_ref());
    let row_roles = &chart.row_roles;
    let cell_data = &chart.cell_data;
    let force_compact = &chart.force_compact;
    let bar_auto_max = &chart.bar_auto_max;
    let signed_columns = &chart.signed_columns;
    let pivot_n_row_dims = chart.pivot_n_row_dims;
    let pivot_levels_visible = &drawn.header_levels;
    let pagination_control_height = drawn.pagination_control_height;

    // Pagination is resolved AFTER the fit cascade, wrap layout, and title/
    // header geometry — production's own order (column widths → fit cascade →
    // `_compute_wrap_layout` → `_resolve_visible_rows`). See the
    // "Visible-rows resolution" block below; only the row-height binding the
    // cascade mutates lives up here.
    // ── Demands / floors / header demands — production's inputs ──
    // Every string is the FORMATTED painted cell text
    // (prefix+number+suffix for numeric lanes), measured with the Inter
    // stand-in (get_font_path's non-numeric branch measures every
    // non-serif family as Inter — the same documented deliberate
    // under-measure kpi.rs mirrors) at body size; demand = p95 + 2*pad,
    // floor = widest token across cells AND the header label (header at
    // header size) + 2*pad, applied to every column, numerics included.
    let table_w = width - 2.0 * outer;
    // Degenerate slot (the harness's unsized nested-board items arrive as
    // 0×0): refuse with a named error rather than allocating every column
    // to zero and emitting an invalid-size SVG.
    if table_w <= 0.0 {
        return Err(format!(
            "table: available width ({table_w:.1}px) too small to allocate columns"
        )
        .into());
    }
    let measure_family = measure::stand_in_family(&body_family);
    let header_measure_family = measure::stand_in_family(&header_family);
    let tab_measure_family = look.numeral_family.as_str();

    // The row-number column takes its width off the top; the data columns
    // allocate into what is left.
    let initial_font_size = font_size;
    let rn = &th.row_numbers;
    let row_number_width = |font_now: f64| -> Result<f64, String> {
        if !rn.visible {
            return Ok(0.0);
        }
        Measure { fonts }.row_number_column(
            &rn.header,
            n_rows,
            measure_family,
            header_measure_family,
            font_now.trunc(),
            pad,
        )
    };
    let mut rn_w = row_number_width(font_size)?;

    let column_names: Vec<String> = cols.iter().map(|c| c.name.to_string()).collect();
    // Phase 1 (explicit `width:` pins and `max_width:` caps), the demands, and
    // the allocation, at the fonts and padding it is asked for.
    let allocate = |font_now: f64,
                    header_now: f64,
                    pad_now: f64,
                    budget: f64|
     -> Result<
        (
            ColumnInputs,
            BTreeMap<String, f64>,
            BTreeMap<String, Option<f64>>,
            Vec<f64>,
        ),
        Diagnostic,
    > {
        let inputs = column_inputs(
            &cols,
            &cell_data,
            rows,
            fonts,
            &MeasureFonts {
                body: measure_family,
                header: header_measure_family,
            },
            font_now,
            header_now,
            th.header.visible,
            pad_now,
        )?;
        let mut pins: BTreeMap<String, f64> = BTreeMap::new();
        let mut caps: BTreeMap<String, Option<f64>> = BTreeMap::new();
        for col in cols {
            if let Some(w) = parse_column_width(col.cfg.width.as_ref(), budget)? {
                pins.insert(col.name.to_string(), w);
            }
            caps.insert(
                col.name.to_string(),
                parse_column_width(col.cfg.max_width.as_ref(), budget)?,
            );
        }
        if std::env::var("TABLE_DEBUG").is_ok() {
            eprintln!("RUST demands: {:?}", inputs.demands);
            eprintln!("RUST word_floors: {:?}", inputs.word_floors);
        }
        let width_map = calculate_column_layout_port(
            &column_names,
            &force_compact,
            &pins,
            &caps,
            budget,
            &inputs.demands,
            &inputs.header_demands,
            &inputs.word_floors,
            th.column_layout.width_similarity_threshold,
            th.column_layout.content_headroom,
            pad_now,
        );
        let col_ws = column_names.iter().map(|n| width_map[n]).collect();
        Ok((inputs, pins, caps, col_ws))
    };
    let (mut inputs, mut pins, mut caps, mut col_ws) =
        allocate(font_size, header_size, pad, table_w - rn_w)?;

    // ── Fit cascade: allocation ran at the theme's cell padding; if any
    // numeric/date cell or short header overflows its content area (text
    // cells too under `wrap: false`; wrapped text cells never overflow),
    // padding steps down to max(pad/2, 6), then the body font to 11px (row
    // height −4, floored at 16; header follows the body when it inherits
    // it). Overflow is measured against `measured_cell_text` — the same
    // string the demand pass measures — with the Inter stand-in, minus the
    // 4px breathing room.
    // A glyph-possible column
    // composes `glyph_run + rendered` before measuring, same as the demand
    // pass.
    let overflows = |pad_now: f64, font_now: f64, header_now: f64| -> Result<bool, String> {
        for ((col, cells), cw) in cols.iter().zip(cell_data.iter()).zip(col_ws.iter()) {
            if is_swatch(col) {
                continue;
            }
            let content_area = cw - 2.0 * pad_now - 4.0;
            if header_visible
                && cols.len() > 1
                && col.header.chars().count() <= 12
                && fonts
                    .measure(&col.header, measure_family, header_now)
                    .unwrap_or(0.0)
                    > content_area
            {
                return Ok(true);
            }
            // Wrapped text cells never overflow; unwrapped ones drive the cascade.
            if th.wrap && !(col.is_numeric || col.is_date) {
                continue;
            }
            for (ri, cell) in cells.iter().enumerate().take(measure::SAMPLE_ROWS) {
                let rendered = unpadded_cell_text(cell);
                let raw_value = rows[ri].get(col.name.as_str()).filter(|v| !v.is_null());
                let w = fonts.measure(
                    &cell_text_with_glyph(col, raw_value, &rendered)?,
                    measure_family,
                    font_now,
                )?;
                if w > content_area {
                    return Ok(true);
                }
            }
        }
        Ok(false)
    };
    let mut eff_pad = pad;
    if overflows(eff_pad, font_size, header_size)? {
        eff_pad = (pad / 2.0).floor().max(6.0);
        if overflows(eff_pad, font_size, header_size)? {
            let header_inherits_body = th.header.font.size.is_none();
            font_size = 11.0;
            row_h = (row_h - 4.0).max(16.0);
            header_size = if header_inherits_body {
                font_size
            } else {
                11.0
            };
        }
    }
    let pad = eff_pad;
    // After the cascade stepped the body font down, the row-number column
    // narrows with it and the data columns are allocated again into the new
    // budget.
    if rn.visible && font_size < initial_font_size {
        rn_w = row_number_width(font_size)?;
        (inputs, pins, caps, col_ws) = allocate(font_size, header_size, pad, table_w - rn_w)?;
    }
    let ColumnInputs {
        demands,
        header_demands,
        ..
    } = inputs;

    // Content edge for horizontal elements that span every column (row
    // stripes/rules, summary/total rules) — production's
    // The cell-content edge from
    // the first column's content start to the last column's content end,
    // NOT the table's outer slot. Identical to `table_w` whenever
    // `Σcol_ws == table_w`, which explicit pins can break — an all-pinned
    // table summing under `table_w` must not paint its rule past its own
    // last column.
    if std::env::var("TABLE_DEBUG").is_ok() {
        let named: Vec<(String, f64)> = cols
            .iter()
            .map(|c| c.name.to_string())
            .zip(col_ws.iter().cloned())
            .collect();
        eprintln!("RUST col_ws: {named:?}");
    }
    let content_w: f64 = rn_w + col_ws.iter().sum::<f64>();

    // Columns whose honored widths sum past the slot record
    // WARN-TABLE-COLUMNS-OVERFLOW, and the body is clipped to the card box
    // (see the emit below) so it never paints over a neighboring slot. Pins
    // alone, word floors and numeric-lane reservations can each push the
    // total over `table_w`.
    // Sub-pixel epsilon: the surplus/deficit branches divide and sum floats
    // per column, so a table that mathematically fits exactly (`Σcol_ws ==
    // table_w`) can land a few ULPs over `table_w` from summation error
    // alone; the `==` boundary must still count as "fits".
    const OVERFLOW_EPSILON: f64 = 1e-6;
    let content_overflows = content_w > table_w + OVERFLOW_EPSILON;
    if content_overflows {
        diagnostics.push(Diagnostic::new(
            &codes::WARN_TABLE_COLUMNS_OVERFLOW,
            [
                ("chart_id", json!(chart_id)),
                ("needed_width", json!(content_w + 2.0 * outer)),
                ("available_width", json!(width)),
            ],
        ));
    }

    // Precompute per-column x offsets and midpoints.
    let col_x: Vec<f64> = {
        let mut xs = Vec::with_capacity(col_ws.len());
        let mut x = outer + rn_w;
        for &w in &col_ws {
            xs.push(x);
            x += w;
        }
        xs
    };

    // Text wrapping: a text
    // column's cell that overflows its content width word-wraps onto extra
    // lines rather than clipping/truncating, and the row grows to fit the
    // tallest wrapped cell. `wrapped_lines[ci][ri]` is `None` for a
    // single-line cell (render path falls back to the plain one-line text).
    let mut row_heights: Vec<f64> = vec![row_h; n_rows];
    let mut wrapped_lines: Vec<Vec<Option<Vec<String>>>> = Vec::with_capacity(cols.len());
    if th.wrap {
        let line_height = font_size + th.text_baseline_offset;
        for (ci, col) in cols.iter().enumerate() {
            if col.is_numeric || col.is_date || is_swatch(col) {
                wrapped_lines.push(vec![None; n_rows]);
                continue;
            }
            let content_w = col_ws[ci] - 2.0 * pad;
            let mut col_lines: Vec<Option<Vec<String>>> = Vec::with_capacity(n_rows);
            for (ri, cell) in cell_data[ci].iter().enumerate() {
                if cell.text.is_empty() || content_w <= 0.0 {
                    col_lines.push(None);
                    continue;
                }
                // The glyph shares the first line with the text it
                // precedes, so the wrap search runs against the width left
                // over after it — subtracted only past the guard above, so
                // a glyph never pushes a cell through it.
                let raw_value = rows[ri].get(col.name.as_str()).filter(|v| !v.is_null());
                let glyph = resolve_cell_glyph(&col.cfg, &col.when_rules, raw_value)?;
                let glyph_w =
                    fonts.measure(&glyph_run(glyph.as_deref()), measure_family, font_size)?;
                let cell_content_w = content_w - glyph_w;
                if fonts.measure(&cell.text, measure_family, font_size)? <= cell_content_w {
                    col_lines.push(None);
                    continue;
                }
                // A date-like or numeric cell is cut with an ellipsis at paint
                // time instead of wrapping.
                if wants_tabular_font(col, rows[ri].get(col.name.as_str())) {
                    col_lines.push(None);
                    continue;
                }
                // `wrap_text_precise`: whitespace words, a
                // word wider than the column split at its last fitting seam.
                let (lines, _) = crate::common::wrap_text_measured(
                    &cell.text,
                    cell_content_w,
                    font_size,
                    measure_family,
                    fonts,
                    None,
                    false,
                )?;
                if lines.len() > 1 {
                    let grown = python_round(
                        lines.len() as f64 * line_height - th.text_baseline_offset + 2.0 * pad,
                    );
                    row_heights[ri] = row_heights[ri].max(grown);
                    col_lines.push(Some(lines));
                } else {
                    col_lines.push(None);
                }
            }
            wrapped_lines.push(col_lines);
        }
    } else {
        wrapped_lines.extend((0..cols.len()).map(|_| vec![None; n_rows]));
    }
    // For numeric/date-lane columns: fixed per-column lane positions
    // (`numeric_lane_positions`, invariant #11) so prefix/number/suffix each
    // render as their own tspan, every column's numbers sharing one rhythm.
    let mut prefix_xs: Vec<f64> = vec![0.0; cols.len()];
    let mut number_xs: Vec<f64> = vec![0.0; cols.len()];
    let mut suffix_xs: Vec<f64> = vec![0.0; cols.len()];
    // `_compute_lane_positions`' `(content_left, content_right)`: the lane's full extent, prefix left edge to suffix right edge. A
    // column earns a lane entry only when it measured content
    // (`max_content_w > 0`).
    let mut lane_extents: Vec<Option<(f64, f64)>> = vec![None; cols.len()];
    for (ci, col) in cols.iter().enumerate() {
        if !col.is_numeric && !col.is_date {
            continue;
        }
        let cells = &cell_data[ci];
        // Production measures the number lane with `num_str.removeprefix
        // ("-")` — ASCII hyphen-minus only. Every numeric
        // formatter in this repo (`_with_minus`, d3-format, d3rs's own
        // MINUS) emits U+2212 MINUS SIGN for negatives, never ASCII "-", so
        // that removeprefix never actually matches real cell text: it's a
        // no-op, and production's "unsigned" measurement still counts the
        // minus glyph's width. Stripping U+2212 here (production's own bug,
        // faithfully NOT fixed) would narrow the lane and shift the number
        // left of production's position — port the no-op, not the intent.
        let mut max_num_w = 0.0_f64;
        let mut max_prefix_w = 0.0_f64;
        let mut max_suffix_w = 0.0_f64;
        for (ri, c) in cells.iter().enumerate() {
            // Production's lane pass skips a null: its placeholder is painted
            // outside the lane and must not widen it.
            if c.is_null {
                continue;
            }
            // Only a magnitude, or a date-shaped cell (every cell of a
            // right-aligned date column), sizes the lane; a sentinel beside
            // magnitudes does not.
            let raw_lane = rows[ri].get(col.name.as_str());
            let is_magnitude = numeric_value(raw_lane).is_some() && is_numeric_cell(col, raw_lane);
            let is_date_cell = is_date_like_cell(raw_lane)
                || (col.is_date
                    && col.cfg.align.as_deref() == Some("right")
                    && matches!(raw_lane, Some(Value::String(s)) if !s.trim().is_empty()));
            if !(is_magnitude || is_date_cell) {
                continue;
            }
            let unsigned = c.text.trim_start_matches('-');
            let lane_font = if is_plain_date_lane_cell(col, rows[ri].get(col.name.as_str())) {
                measure_family
            } else {
                tab_measure_family
            };
            let measured = fonts.measure(unsigned, lane_font, font_size)?;
            // A date cell is wrapped/truncated to its column when painted, so
            // its lane width is clamped to the content area.
            let raw_cell = rows[ri].get(col.name.as_str());
            let numeric_lane_cell = is_numeric_cell(col, raw_cell) && !is_date_like_cell(raw_cell);
            max_num_w = max_num_w.max(if col.is_date && !numeric_lane_cell {
                measured.min(col_ws[ci] - 2.0 * pad)
            } else {
                measured
            });
            // A glyph replaces the format prefix in the prefix lane, so the
            // lane is measured on whichever the cell will actually render
            // (`effective_prefix = cell_glyph or _p`).
            let raw_value = rows[ri].get(col.name.as_str()).filter(|v| !v.is_null());
            let glyph = resolve_cell_glyph(&col.cfg, &col.when_rules, raw_value)?;
            let effective_prefix = glyph.as_deref().or(c.prefix.as_deref());
            if let Some(p) = effective_prefix {
                max_prefix_w = max_prefix_w.max(fonts.measure(p, tab_measure_family, font_size)?);
            }
            if let Some(sfx) = c.suffix.as_deref() {
                max_suffix_w =
                    max_suffix_w.max(fonts.measure(sfx, tab_measure_family, font_size)?);
            }
        }
        // Magnitude gap is date-column-exempt ( hard-zeroes it
        // there — a date column's suffix, if any, is never a K/M/B magnitude).
        let is_magnitude = !col.is_date
            && cells
                .iter()
                .find_map(|c| c.suffix.as_deref())
                .is_some_and(|suffix| is_magnitude_suffix(suffix));
        let (prefix_x, num_x, suffix_x) = numeric_lane_positions(
            col_x[ci],
            col_ws[ci],
            pad,
            max_num_w,
            max_prefix_w,
            max_suffix_w,
            is_magnitude,
        );
        number_xs[ci] = num_x;
        prefix_xs[ci] = prefix_x;
        suffix_xs[ci] = suffix_x;
        if max_num_w > 0.0 {
            lane_extents[ci] = Some((prefix_x - max_prefix_w, suffix_x + max_suffix_w));
        }
    }

    // ── Layout geometry ───────────────────────────────────────────────────────

    let band = title_band(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.title_style.as_ref(),
        r.style.title_font.as_ref(),
        th,
        width,
        drawn.title_shift,
        fonts,
    )?;
    if !chart_id.is_empty() {
        diagnostics.extend(band.truncations(chart_id));
    }
    let title_area_h = band.area_h;
    // `render_table_svg`: the caller grew the slot by the band's `body_dy` so
    // no row is squeezed out to make room.
    let slot_h = slot;

    // The `header_body_gap` (`int(row_height * 0.25)`) is
    // whitespace added AFTER the header row, before the first data row
    // — the header itself is never shifted by it.
    let mut header_body_gap = if header_visible {
        (row_h * 0.25).floor()
    } else {
        0.0
    };
    let header_y = title_area_h;
    // Header band: the
    // theme's `int(header.height)`, floored at `2*int(outer_padding) + lines *
    // (int(header_size) + text_baseline_offset)` — so `header.height: 0` still
    // holds one header line. `resolve_wrapped_headers` sets `lines` to the
    // tallest header: a label that does not fit `int(cw - 2*pad)` wraps (a
    // column sized to its own header never fits it, the truncation losing the
    // fraction).
    let header_font_size = header_size.trunc();
    let header_line_h = header_font_size + th.text_baseline_offset;
    let theme_header_h = th.header.height.trunc();
    let header_lines = if header_visible {
        wrapped_headers(
            &cols,
            &col_ws,
            pad,
            header_font_size,
            header_measure_family,
            &r,
            th,
            fonts,
        )?
    } else {
        Vec::new()
    };
    let truncations: RefCell<Vec<(String, usize)>> = RefCell::new(Vec::new());
    if !chart_id.is_empty() {
        for (col, header) in cols.iter().zip(&header_lines) {
            if header.truncated {
                count_cut(&truncations, col.name.as_str());
            }
        }
    }
    // Cramping: headers wrapped because the columns' demand exceeds the budget
    // they were divided into. A table that also overflows
    // its slot is WARN-TABLE-COLUMNS-OVERFLOW's case.
    let wrapped_count = header_lines.iter().filter(|h| h.lines.len() > 1).count();
    if wrapped_count > 0 && !content_overflows {
        let mut demand = 0.0_f64;
        let mut relative_fraction = 0.0_f64;
        for col in cols {
            if let Some(&pinned) = pins.get(col.name.as_str()) {
                demand += pinned;
                if matches!(&col.cfg.width, Some(WidthHint::Text(s)) if s.trim().ends_with('%')) {
                    relative_fraction += pinned / table_w;
                }
                continue;
            }
            let mut col_demand =
                demands[col.name.as_str()].max(header_demands[col.name.as_str()] + 2.0 * pad);
            let relative_cap =
                matches!(&col.cfg.max_width, Some(WidthHint::Text(s)) if s.trim().ends_with('%'));
            if let (Some(Some(cap)), false) = (caps.get(col.name.as_str()), relative_cap) {
                col_demand = col_demand.min(*cap);
            }
            demand += col_demand;
        }
        let shortfall = demand - table_w;
        // The fix is sized from the root board's width: a render outside a
        // board has none to suggest, so it warns of nothing.
        let board_width = drawn.board_style.as_ref().and_then(|b| b.board_width);
        if let (true, Some(board_width)) =
            (shortfall >= 1.0 && relative_fraction < 1.0, board_width)
        {
            let needed_budget = (demand - relative_fraction * table_w) / (1.0 - relative_fraction);
            let growth = (needed_budget - table_w).max(0.0) * board_width / table_w;
            let suggested_width = ((board_width + growth) / 50.0).ceil() * 50.0;
            diagnostics.push(Diagnostic::new(
                &codes::WARN_TABLE_CRAMPED,
                [
                    ("chart_id", json!(chart_id)),
                    ("wrapped_headers", json!(wrapped_count)),
                    ("column_count", json!(cols.len())),
                    ("needed_width", json!(demand)),
                    ("available_width", json!(table_w)),
                    ("suggested_width", json!(suggested_width)),
                ],
            ));
        }
    }
    let max_header_lines = header_lines
        .iter()
        .map(|h| h.lines.len())
        .max()
        .unwrap_or(1);
    let header_floor = 2.0 * outer.trunc() + max_header_lines as f64 * header_line_h;
    let header_h = if header_visible {
        theme_header_h.max(header_floor).trunc()
    } else {
        0.0
    };
    // A multi-dim or multi-measure pivot draws N group-header rows above the
    // leaf header row, each the same height as the leaf row. Every other table has zero group levels,
    // so `leaf_header_y == header_y` and geometry is unchanged.
    let pivot_levels: &[Vec<PivotLevel>] = if header_visible {
        &pivot_levels_visible
    } else {
        &[]
    };
    let n_group_levels = pivot_levels.len();
    let group_row_height = header_h;
    let leaf_header_y = header_y + group_row_height * n_group_levels as f64;
    // Production's header placement: text baseline at
    // header_h - text_baseline_offset, rule at header_h - rule width, and
    // the header/body gap added once before the first data row.
    let header_text_y = leaf_header_y + header_h - th.text_baseline_offset;
    let header_divider = &th.header.divider;
    // The per-column rule loop walks every column, the synthetic row-number
    // column included, and sizes it from its internal key's
    // slug text — a column this engine does not draw.
    if header_divider.line.visible && !header_divider.continuous && th.row_numbers.visible {
        return Err(
            "table: style.header.divider.continuous: false with style.row_numbers.visible: true \
             is not supported"
                .into(),
        );
    }
    // `int(current_y + header_height - header_rule_width)`.
    let rule_y = (leaf_header_y + header_h - header_divider.line.width).trunc();
    let mut rows_start_y = leaf_header_y + header_h + header_body_gap;

    // ── Visible-rows resolution (visible rows) ──────────
    //
    // Static export: every host that can act on live pagination controls
    // (dct serve, Cloud) ships variables.js; a rasterized SVG never does, so
    // production always takes the `static_multi_page` branch — every page
    // pre-renders into its own toggle group, only the variable's page visible.
    // With a slot height and pagination enabled, the SLOT
    // decides: the grow-by-2 short-circuit renders every row at content
    // height when they all fit; otherwise the page size is however many rows
    // fit the slot after reserving the control band, and the SVG height pins
    // to the slot itself (`table_height = height` — the sizer already
    // budgeted the control band, so it is never added on top).
    // Without a slot height, the canvas derives from the
    // page-clamped rows plus the control band. Production passes
    // `row_heights` only when wrap is on (`wrap_cells and data`).
    // `page_rows: null` is unlimited: the slot alone bounds the page.
    let page_rows = th.pagination.page_rows.unwrap_or(n_rows.max(1));
    let have_heights = th.wrap && n_rows > 0;
    let mut n_shown = n_rows;
    let mut true_total_pages = 1usize;
    // svg height echoes the slot instead of deriving from content
    let mut slot_pinned = false;
    // uniform row-height squeeze fired: rebuild the row-height-derived state
    let mut squeezed: Option<f64> = None;

    if let (true, Some(slot_h)) = (th.pagination.enabled, slot_h) {
        // header_height in production's budget is the post-pivot-bump total.
        let header_total_h = group_row_height * n_group_levels as f64 + header_h;
        // `outer` is in the budget (production subtracts `padding`,
        // But this renderer's canvas formula never spends it
        // vertically — production offsets all content by `padding`
        // where this renderer starts at y=0. Latent: every
        // shipped theme sets `table.outer_padding: 0.0`, so the budget and
        // the paint agree on the corpus; don't "fix" one side alone.
        let available =
            slot_h - title_area_h - header_total_h - header_body_gap - outer - th.bottom_padding;
        let safe_page_rows = |avail: f64| -> usize {
            if have_heights {
                largest_safe_page_rows(&row_heights, avail, page_rows)
            } else {
                // production: min(max(1, int(avail / row_height)), page_rows)
                (((avail / row_h) as i64).max(1) as usize).min(page_rows)
            }
        };
        let total_data_h: f64 = row_heights.iter().sum();
        if total_data_h <= available && n_rows <= page_rows + PAGINATION_GROW_CAP {
            // Grow-by-2 short-circuit: every row fits at natural height and
            // the overflow is within the cap — one page, content height.
        } else {
            slot_pinned = true;
            let probe = safe_page_rows(available);
            let mut effective = probe;
            if n_rows > probe {
                let overflow = n_rows - probe;
                if !have_heights && overflow <= ANTI_DANGLE_MAX_OVERFLOW {
                    let sq = (available / n_rows as f64)
                        .floor()
                        .max(ANTI_DANGLE_MIN_ROW_H);
                    if sq / row_h >= 1.0 - ANTI_DANGLE_MAX_SQUEEZE
                        && sq * n_rows as f64 <= available
                    {
                        squeezed = Some(sq);
                    }
                }
                if squeezed.is_none() {
                    // Controls will render — re-pick with the reduced budget.
                    effective = safe_page_rows(available - pagination_control_height);
                }
            }
            if squeezed.is_none() {
                // The slot, not the table, chose the page size.
                let requested = page_rows.min(n_rows);
                if th.pagination.page_rows.is_some() && effective < requested {
                    diagnostics.push(Diagnostic::new(
                        &codes::WARN_TABLE_PAGE_SQUEEZED,
                        [
                            ("chart_id", json!(chart_id)),
                            ("drawn_rows", json!(effective)),
                            ("page_rows", json!(requested)),
                            ("total_rows", json!(n_rows)),
                        ],
                    ));
                }
                n_shown = effective.min(n_rows);
                true_total_pages = if n_rows > 0 {
                    n_rows.div_ceil(effective)
                } else {
                    1
                };
            }
        }
    } else if th.pagination.enabled && n_rows > page_rows + PAGINATION_GROW_CAP {
        n_shown = page_rows;
        true_total_pages = n_rows.div_ceil(page_rows);
    }

    if let Some(sq) = squeezed {
        // Production returns the squeezed height as `effective_row_height`
        // and the caller rebinds `row_height` to it before painting, so the
        // header/body gap and every row band derive from the squeezed value
        // Wrap is off whenever this fires, so the uniform
        // rebuild loses no wrap-grown heights.
        row_h = sq;
        row_heights = vec![row_h; n_rows];
        header_body_gap = if header_visible {
            (row_h * 0.25).floor()
        } else {
            0.0
        };
        rows_start_y = leaf_header_y + header_h + header_body_gap;
    }

    // True page count (uncapped) vs. the count the static export pre-renders.
    // The pager shows the true count with pages past the cap disabled.
    let paginated = true_total_pages > 1;
    let static_multi_page = paginated && !chart_id.is_empty() && !drawn.interactive_controls;
    let total_pages = true_total_pages.min(STATIC_MULTI_PAGE_MAX_PAGES);

    // Extra breathing room before the first summary/total row
    // (`int(row_height * 0.4)`) so the double rule doesn't
    // crowd the last detail row. Accumulates once per value→summary/total
    // transition; `summary_gap_at[i]` is the total gap already applied by
    // row i (added to every row from the transition onward).
    let summary_gap_unit = (row_h * 0.4) as i64 as f64;
    let mut summary_gap_at = vec![0.0_f64; n_rows];
    {
        let mut acc = 0.0_f64;
        for i in 0..n_rows {
            if i > 0 && is_summary_role(row_roles[i]) && !is_summary_role(row_roles[i - 1]) {
                acc += summary_gap_unit;
            }
            summary_gap_at[i] = acc;
        }
    }

    // Bottom of the last painted row — matches production's `indicator_y`
    // The y the pagination control bar anchors below. When
    // pages have unequal (wrap-grown) heights, the bar anchors below the
    // TALLEST page's rows (production's `_max_page_sum` rows_height) so it
    // sits at one y across every pre-rendered page.
    // Outside a static export a paginated table draws the one page its
    // variable names (`_extract_page_from_variables`), clamped to the last.
    let current_page = if paginated && !static_multi_page {
        page_variable(&drawn.variables, &format!("{chart_id}_page")).min(true_total_pages)
    } else {
        1
    };
    let live_offset = (current_page - 1) * n_shown;
    let live_shown = n_shown.min(n_rows.saturating_sub(live_offset));
    let live_rows = live_offset..live_offset + live_shown;
    let last_gap = if current_page == 1 {
        summary_gap_at
            .get(n_shown.wrapping_sub(1))
            .copied()
            .unwrap_or(0.0)
    } else {
        let transitions = live_rows
            .clone()
            .skip(1)
            .filter(|&i| is_summary_role(row_roles[i]) && !is_summary_role(row_roles[i - 1]))
            .count();
        transitions as f64 * summary_gap_unit
    };
    // The pager sits off the page actually drawn; the canvas is sized for the
    // tallest page so the card does not resize between pages.
    let shown_rows_h: f64 = row_heights[live_rows.clone()].iter().sum();
    let indicator_y = rows_start_y + shown_rows_h + last_gap + th.bottom_padding;
    // A zero-row table holds one row of body for its "No data" label, so the
    // label sits below the header instead of over it.
    let canvas_rows_h: f64 = if n_rows == 0 {
        row_h
    } else if paginated && have_heights {
        max_page_sum(&row_heights, n_shown)
    } else {
        shown_rows_h
    };
    // Every page paints into one canvas, so it holds the gap of whichever
    // page needs the most (`_max_page_summary_gap`).
    let canvas_gap = if static_multi_page {
        (0..n_rows.div_ceil(n_shown.max(1)))
            .map(|page| {
                let window = page * n_shown..((page + 1) * n_shown).min(n_rows);
                window
                    .clone()
                    .skip(1)
                    .filter(|&i| {
                        is_summary_role(row_roles[i]) && !is_summary_role(row_roles[i - 1])
                    })
                    .count() as f64
                    * summary_gap_unit
            })
            .fold(0.0, f64::max)
    } else {
        last_gap
    };
    let canvas_y = rows_start_y + canvas_rows_h + canvas_gap + th.bottom_padding;
    // Slot-pinned (bounded pagination): the canvas is exactly the slot —
    // production echoes `height` back as table_height and never re-adds the
    // control band. Otherwise the canvas derives from
    // content, plus the control band when the paginator renders (an explicit
    // slot height already reserves it via the sizer, so this
    // renderer must not double-add it — mirrors the row/summary-rule
    // reserve's own slot-pinned carve-outs above).
    let svg_h = if let (true, Some(slot_h)) = (slot_pinned, slot_h) {
        slot_h
    } else {
        canvas_y
            + if paginated {
                pagination_control_height
            } else {
                0.0
            }
    };
    let svg_w = width;

    // ── Emit SVG ──────────────────────────────────────────────────────────────

    let mut tree = Tree::new();
    tree.open_svg(Extent { width: svg_w, height: svg_h });
    // Hosts inline this content into the board without the root <svg>'s
    // viewport, so an overflowing table must clip itself: unclipped, its
    // columns paint over whatever sits beside it in a `cols` row. The clip id
    // is the geometry itself: chart ids repeat across a board tree, and two
    // clips sharing an id must share a shape.
    if content_overflows {
        let (bx, by, bw, bh) = card_box(svg_w, svg_h, drawn.padding.as_ref());
        let (bx, by, bw, bh) = (num_full(bx), num_full(by), num_full(bw), num_full(bh));
        let clip_id = format!("table-clip-{bx}-{by}-{bw}-{bh}");
        tree.open(Open::Defs, Attrs::new());
        tree.open(Open::ClipPath, Attrs::new().quick("id", clip_id.as_str()));
        tree.rect(
            Attrs::new()
                .quick("x", bx.as_str())
                .quick("y", by.as_str())
                .quick("width", bw.as_str())
                .quick("height", bh.as_str()),
        );
        tree.close(Open::ClipPath);
        tree.close(Open::Defs);
        tree.open(
            Open::Group,
            Attrs::new().quick("clip-path", format!("url(#{clip_id})")),
        );
    }
    // Omit the rect when the table's OWN background is
    // unset or the literal string "transparent" — never `payload.background`
    // (the card/page canvas), which paints regardless of what the table
    // itself authored and would opaque over card chrome production leaves
    // showing through.
    if let Some(bg) = th
        .background
        .as_deref()
        .filter(|b| !b.eq_ignore_ascii_case("transparent"))
    {
        // `card_box` (`common.rs`, `svg_utils.card_box` — `render/
        // svg_utils.py:173`) reaches the rect back out over `payload.
        // padding`, the same law kpi.rs's own card-fill rect ports —
        // production calls it with `inset=drawn.padding`.
        let (bx, by, bw, bh) = card_box(svg_w, svg_h, drawn.padding.as_ref());
        tree.rect(
            Attrs::new()
                .quick("x", num_full(bx))
                .quick("y", num_full(by))
                .quick("width", num_full(bw))
                .quick("height", num_full(bh))
                .quick("fill", bg)
                .quick("rx", "4"),
        );
    }

    emit_title_band(&mut tree, &band, outer);

    let header_fill = sanitize_color(th.header.background.as_deref())?
        .filter(|b| !b.eq_ignore_ascii_case("transparent"));

    // Pivot group-header rows (multi-dim / multi-measure only). Mirrors
    // The pivot group header: one row per descriptor level,
    // outermost dim on top, each span centered over the leaf columns it
    // covers, with its own separator rule along the span's bottom edge.
    if !pivot_levels.is_empty() {
        let header_measurer = fonts.measurer(measure::stand_in_family(&header_family))?;
        for (level_idx, level) in pivot_levels.iter().enumerate() {
            let row_y = header_y + group_row_height * level_idx as f64;
            if let Some(fill) = header_fill {
                // The full table
                // width less the outer padding on both sides.
                tree.rect(
                    Attrs::new()
                        .quick("x", num_full(outer))
                        .quick("y", num_full(row_y))
                        .quick("width", num_full(table_w - 2.0 * outer))
                        .quick("height", num_full(group_row_height))
                        .quick("fill", fill),
                );
            }
            let label_y = row_y
                + group_row_height / 2.0
                + measure::centered_baseline_offset(&header_measurer, header_size.trunc());
            for (label, first_leaf, n_leaves) in level {
                let abs_first = pivot_n_row_dims + first_leaf;
                let abs_last = abs_first + n_leaves - 1;
                let x_left = col_x[abs_first];
                let x_right = col_x[abs_last] + col_ws[abs_last];
                let center_x = (x_left + x_right) / 2.0;
                if x_right > x_left + pad {
                    // The span rule is `_render_header_group_rows`' own fixed
                    // `_RULE_H = 1.0` line: it follows neither
                    // the header divider's `visible` nor its width. Its color
                    // is the header font color — `colors.get("label_color")
                    // or colors["color"]`, `label_color =
                    // tc.header.font.color`.
                    let rule_w = 1.0;
                    let span_rule_y = row_y + group_row_height - rule_w;
                    let (span_rule_x_s, span_rule_y_s, span_rule_w_s, rule_w_s) = (
                        float_repr(x_left + pad),
                        float_repr(span_rule_y),
                        float_repr(x_right - x_left - 2.0 * pad),
                        float_repr(rule_w),
                    );
                    tree.rect(
                        Attrs::new()
                            .quick("x", span_rule_x_s)
                            .quick("y", span_rule_y_s)
                            .quick("width", span_rule_w_s)
                            .quick("height", rule_w_s)
                            .quick("fill", header_color)
                            .quick("shape-rendering", "crispEdges"),
                    );
                }
                let (center_x_s, label_y_s) = (float_repr(center_x), float_repr(label_y));
                tree.text_el(
                    Attrs::new()
                        .quick("x", center_x_s)
                        .quick("y", label_y_s)
                        .quick("font-size", header_size.to_string())
                        .quick("font-weight", header_weight.as_str())
                        .quick("fill", header_color)
                        .quick("text-anchor", "middle")
                        .quick("font-family", header_family.as_str()),
                    label,
                );
            }
        }
    }

    // `_render_header_section`'s fill: the header band across
    // the cell-content span (`cell_pad=0`), under the rule.
    if let (true, Some(fill)) = (header_visible, header_fill) {
        tree.rect(
            Attrs::new()
                .quick("x", num_full(outer))
                .quick("y", num_full(leaf_header_y))
                .quick("width", num_full(content_w))
                .quick("height", num_full(header_h))
                .quick("fill", fill),
        );
    }

    if header_visible && header_divider.line.visible && header_divider.continuous {
        // Header divider: one unbroken line across the
        // content span, not `table_w` — an all-pinned table summing under
        // `table_w` must not paint it past its own last column.
        emit_divider_segment(
            &mut tree,
            outer + pad,
            outer + content_w - pad,
            PyNum::Int(rule_y),
            PyNum::Float(header_divider.line.width),
            &header_divider.line,
            None,
        );
    } else if header_visible && header_divider.line.visible {
        // One rule under each column, each through the
        // continuous form's own segment. The label is the WIDEST wrapped line
        // by character count (`max(lines, key=len)` — the
        // first of equals), measured at the header size with the header
        // measurer.
        for (ci, header) in header_lines.iter().enumerate() {
            let widest_line = header
                .lines
                .iter()
                .reduce(|widest, line| {
                    if line.chars().count() > widest.chars().count() {
                        line
                    } else {
                        widest
                    }
                })
                .expect("a header has at least one line");
            let label_w = fonts.measure(widest_line, header_measure_family, header_font_size)?;
            let Some((x1, x2)) =
                header_rule_span(col_x[ci], col_ws[ci], pad, label_w, lane_extents[ci])
            else {
                continue;
            };
            emit_divider_segment(
                &mut tree,
                x1,
                x2,
                PyNum::Int(rule_y),
                PyNum::Float(header_divider.line.width),
                &header_divider.line,
                None,
            );
        }
    }

    // Header labels, bottom-aligned: every header's last line sits at the
    // same baseline.
    let header_text_y_s = float_repr(header_text_y);
    if let (true, true) = (rn.visible, header_visible) {
        let (x, anchor) = if rn.align == "right" {
            (outer + rn_w - pad, "end")
        } else {
            (outer + pad, "start")
        };
        tree.text_el(
            Attrs::new()
                .quick("x", float_repr(x))
                .quick("y", header_text_y_s.as_str())
                .quick("font-size", header_size.to_string())
                .quick("font-weight", header_weight.as_str())
                .quick("fill", header_color)
                .quick("text-anchor", anchor)
                .quick("font-family", header_family.as_str()),
            &rn.header,
        );
    }
    for ((ci, col), header) in cols.iter().enumerate().zip(&header_lines) {
        let cx = col_x[ci];
        let cw = col_ws[ci];
        // Center-on-midpoint invariant: a lane column and
        // an `align: center` column center their header on the cell midpoint;
        // every other header, a right-aligned text column included, stays left.
        let (x, anchor) =
            if col.is_numeric || col.is_date || col.cfg.align.as_deref() == Some("center") {
                (cx + cw / 2.0, "middle")
            } else {
                (cx + pad, "start")
            };
        let x_s = float_repr(x);
        let header_attrs = || {
            Attrs::new()
                .quick("font-size", header_size.to_string())
                .quick("font-weight", header_weight.as_str())
                .quick("fill", header_color)
                .quick("text-anchor", anchor)
                .quick("font-family", header_family.as_str())
        };
        if let Some(link) = &col.cfg.header_link {
            tree.open(
                Open::Link,
                Attrs::new()
                    .quick("href", crate::href::checked_href_diagnostic(link)?)
                    .quick_some("data-dbt-link-open", col.cfg.header_link_open.open_attr())
                    .quick_some("target", col.cfg.header_link_open.target()),
            );
        }
        if header.lines.len() > 1 {
            tree.open(
                Open::Text,
                Attrs::new().quick("x", x_s.as_str()).extend(header_attrs()),
            );
            if header.truncated {
                tree.title(&col.header);
            }
            let start_y = header_text_y - (header.lines.len() - 1) as f64 * header_line_h;
            for (li, line) in header.lines.iter().enumerate() {
                let ly = float_repr(start_y + li as f64 * header_line_h);
                tree.tspan(Attrs::new().quick("x", x_s.as_str()).quick("y", ly), line);
            }
            tree.close(Open::Text);
        } else {
            tree.open(
                Open::Text,
                Attrs::new()
                    .quick("x", x_s.as_str())
                    .quick("y", header_text_y_s.as_str())
                    .extend(header_attrs()),
            );
            if header.truncated {
                tree.title(&col.header);
            }
            tree.text(&header.lines[0]);
            tree.close(Open::Text);
        }
        if col.cfg.header_link.is_some() {
            tree.close(Open::Link);
        }
    }

    let body = TableBody {
        rows,
        row_roles: &row_roles,
        row_heights: &row_heights,
        summary_gap_unit,
        rows_start_y,
        th,
        cols: &cols,
        col_x: &col_x,
        col_ws: &col_ws,
        content_w,
        wrapped_lines: &wrapped_lines,
        prefix_xs: &prefix_xs,
        number_xs: &number_xs,
        suffix_xs: &suffix_xs,
        outer,
        pad,
        row_h,
        font_size,
        body_family: &body_family,
        tab_family,
        rn_w,
        row_numbers: rn,
        row_number_ink: if rn.visible {
            Some(
                rn.font
                    .as_ref()
                    .and_then(|f| f.color.as_deref())
                    .ok_or("table: row numbers need style.table.row_numbers.font.color")?,
            )
        } else {
            None
        },
        body_color,
        link_color,
        bar_auto_max,
        signed_columns,
        tones,
        link: r.link.as_deref(),
        link_open: r.link_open,
        filter_variables: &drawn.filter_variables,
        link_context: drawn.link_context.as_ref(),
        fonts,
        measure_family,
        chart_id,
        truncations: &truncations,
    };
    if static_multi_page {
        // Every page's rows and paginator are pre-rendered into their own
        // `<g data-dbt-table-page>` toggle group; `table_pagination.js`
        // (embedded verbatim below) flips which one is visible on click —
        // The static multi-page loop.
        // `rows_per_page` is page 1's own row count (`n_shown`), the SAME
        // for every page except a possibly-shorter last one (squeezed —
        // every row on one page — never coexists with `paginated`).
        let page_var_name = format!("{chart_id}_page");
        let rendered_pages = total_pages;
        let rows_per_page = n_shown;
        // A deep-linked page past what the export pre-renders has nothing to
        // show: the last rendered page stands in rather than hiding every group.
        let initial_page = page_variable(&drawn.variables, &page_var_name).min(rendered_pages);
        for page_n in 1..=rendered_pages {
            let page_offset = (page_n - 1) * rows_per_page;
            let page_n_shown = rows_per_page.min(n_rows - page_offset);
            // This page's own pager y — each pre-rendered toggle group
            // carries its own rows height, not the tallest page's, so the
            // pager sits right below ITS rows when toggled visible.
            let page_rows_h: f64 = row_heights[page_offset..page_offset + page_n_shown]
                .iter()
                .sum();
            let page_indicator_y = rows_start_y + page_rows_h + th.bottom_padding;

            // Recomputed per page, over THIS page's own row slice — never
            // sliced out of the global `cell_data` above, which anchors
            // `symbol_mode: anchors` at the WHOLE table's row 0. Production
            // does the same: `_paint_data_rows`/`_render_data_rows` runs
            // its `row_idx == 0`/`shared_scale_anchor_row` logic on
            // `page_visible_data`, one page at a time (// `build_cell_data`'s own doc above).
            let page_cell_data = build_cell_data(
                &rows[page_offset..page_offset + page_n_shown],
                &cols,
                &th.symbol_mode,
                &row_roles[page_offset..page_offset + page_n_shown],
            )?;
            let display = if page_n == initial_page { "" } else { "none" };
            tree.open(
                Open::Group,
                Attrs::new()
                    .quick("class", "dbt-table-page")
                    .quick("data-dbt-table-page", chart_id)
                    .quick("data-page", page_n.to_string())
                    .quick("style", format!("display:{display}")),
            );
            body.render_data_rows(&mut tree, page_offset, page_n_shown, &page_cell_data)?;
            render_pagination_controls(
                &mut tree,
                &th.paginator,
                &body_family,
                page_n as i64,
                true_total_pages as i64,
                rendered_pages as i64,
                &page_var_name,
                svg_w,
                page_indicator_y,
                page_offset + 1,
                page_offset + page_n_shown,
                n_rows,
                outer,
                false,
                fonts,
            )?;
            tree.close(Open::Group);
        }
        // Every static multi-page render reports how it paginated. The pager
        // itself shows the true page count, so the file never needs a cap
        // note and the user-facing warning stays silent.
        diagnostics.push(
            Diagnostic::new(
                &codes::WARN_STATIC_PAGINATION_CAPPED,
                [
                    ("chart_id", json!(chart_id)),
                    ("page_rows", json!(rows_per_page)),
                    ("total_rows", json!(n_rows)),
                    ("rendered_pages", json!(rendered_pages)),
                    ("total_pages", json!(true_total_pages)),
                ],
            )
            .with_sink_param("cap_note_dropped", json!(false)),
        );
        embed_table_pagination_script(&mut tree);
    } else {
        // The page this render shows: every anchor (`symbol_mode: anchors`,
        // scale domain) is that page's own, as the static loop does.
        let page_cell_data;
        let page_cells = if current_page == 1 {
            &cell_data
        } else {
            page_cell_data = build_cell_data(
                &rows[live_rows.clone()],
                &cols,
                &th.symbol_mode,
                &row_roles[live_rows.clone()],
            )?;
            &page_cell_data
        };
        body.render_data_rows(&mut tree, live_offset, live_shown, page_cells)?;

        if n_rows > live_shown {
            if paginated && !chart_id.is_empty() {
                render_pagination_controls(
                    &mut tree,
                    &th.paginator,
                    &body_family,
                    current_page as i64,
                    true_total_pages as i64,
                    true_total_pages as i64,
                    &format!("{chart_id}_page"),
                    svg_w,
                    indicator_y,
                    live_offset + 1,
                    live_offset + live_shown,
                    n_rows,
                    outer,
                    drawn.interactive_controls,
                    fonts,
                )?;
            } else {
                let more_font = &th
                    .more_rows
                    .as_ref()
                    .ok_or("table: truncated rows need style.table.more_rows")?
                    .font;
                let more_size = more_font
                    .size
                    .ok_or("table: truncated rows need style.table.more_rows.font.size")?;
                let more_ink = more_font
                    .color
                    .as_deref()
                    .ok_or("table: truncated rows need style.table.more_rows.font.color")?;
                let more_style = more_font
                    .style
                    .as_deref()
                    .ok_or("table: truncated rows need style.table.more_rows.font.style")?;
                tree.text_el(
                    Attrs::new()
                        .quick("x", float_repr(svg_w / 2.0))
                        .quick("y", float_repr(indicator_y))
                        .quick("font-size", float_repr(more_size))
                        .quick("fill", more_ink)
                        .quick("text-anchor", "middle")
                        .quick("font-style", more_style)
                        .quick("font-family", body_family.as_str()),
                    &format!("+{}", n_rows - live_shown),
                );
            }
        }
    }

    let truncations = truncations.into_inner();
    if !truncations.is_empty() {
        let fields: Vec<&str> = truncations.iter().map(|(name, _)| name.as_str()).collect();
        let count: usize = truncations.iter().map(|(_, n)| n).sum();
        diagnostics.push(Diagnostic::new(
            &codes::WARN_TABLE_TEXT_TRUNCATED,
            [
                ("chart_id", json!(chart_id)),
                ("authored_field", json!(fields.join(", "))),
                ("truncation_count", json!(count)),
            ],
        ));
    }
    if n_rows == 0 {
        let empty_font = &th
            .empty_state
            .as_ref()
            .ok_or("table: empty result set needs style.table.empty_state")?
            .font;
        let size = empty_font
            .size
            .ok_or("table: empty result set needs style.table.empty_state.font.size")?;
        let ink = empty_font
            .color
            .as_deref()
            .ok_or("table: empty result set needs style.table.empty_state.font.color")?;
        tree.text_el(
            Attrs::new()
                .quick("x", crate::svg::py_float_str(svg_w / 2.0))
                .quick("y", crate::svg::py_float_str(rows_start_y + row_h / 2.0))
                .quick("font-size", crate::svg::py_float_str(size))
                .quick("fill", ink)
                .quick("text-anchor", "middle")
                .quick("font-family", body_family.as_str()),
            "\u{2014}",
        );
    }
    if content_overflows {
        tree.close(Open::Group);
    }
    tree.close(Open::Svg);
    let body_bottom = rows_start_y + canvas_rows_h + canvas_gap;
    Ok(Layout {
        data_box: Rect {
            x: outer,
            y: title_area_h,
            width: svg_w - 2.0 * outer,
            height: body_bottom - title_area_h,
        },
        insets: Insets {
            top: title_area_h,
            right: outer,
            bottom: svg_h - body_bottom,
            left: outer,
        },
        nodes: tree.finish(),
        diagnostics,
        title_pos: None,
    })
}

/// How wide the table can be hugged: `None` with nothing to hug (`%` pins total
/// 100% or more, or no column carries a width of its own).
pub fn intrinsic(bound: &Bound, measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    let Bound::Rows(rows) = bound else {
        return Ok(None);
    };
    Ok(natural_width(&rows.chart, measure.fonts)?)
}

/// The height the table takes at `width` with nothing bounding it: the same
/// `layout`, read back as the root card's height.
pub fn height_for_width(bound: &Bound, width: f64, measure: &Measure) -> Result<f64, Diagnostic> {
    let laid_out = lay_out(bound, width, None, measure)?;
    let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
        return Err("table: layout is one root card".to_string().into());
    };
    Ok(extent.height)
}
