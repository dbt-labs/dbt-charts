//! Attached `chart.support_table` strip — shared by `bar.rs`/`line.rs`.
//!
//! Ports the subset of `support_table_attachment.py` this renderer supports:
//! `source`/`aggregate` entries (one text row each; a `by_measure: true`
//! `per_series:` entry is one, see `SupportTableEntry`) plus a `per_series:`
//! entry, which expands to one row per series in the caller's `series_order`
//! — see `build_rows_composed`'s per_series branch. A `per_series` entry also refuses on the legacy splice arm (`build_rows`)
//! and when the caller has no color field/`series_order`
//! (`build_rows_composed`). Every other entry becomes one text row of
//! formatted values, one cell per x-domain position, plus a row label.
//!
//! Two layout models coexist, matching the two production shapes:
//!
//! * **Composed** (`build_rows_composed`/`render_rows_composed`/`composed_ink`,
//!   driven by bar.rs's and line.rs's composed arms): production's
//!   `autosize: pad` shape — the plot keeps the payload view verbatim, the
//!   strip floats above it, and the canvas grows around the ink, the strip's
//!   own cells and stubs included. Carries the full declare-once affix
//!   anchoring (`strip_numerals_for_values`/`StripNumerals` ported below —
//!   trimmed and composed-SI rows both) and, off a band scale only, the
//!   centering cell dx. Cells and stubs anchor from the value axis's orient.
//!
//! Row-y convention: `_row_y_pixel` (`support_table_attachment.py`) measures
//! every row/divider/inter-row-rule pixel from the plot edge (`y=0` for
//! `position: top`) — row 0 always closest to the plot. `row_y_base`/`divider_y_base` below reproduce those
//! same formulas with the plot edge pinned at local origin 0 (i.e.
//! `spec_height` fixed to 0.0).

use crate::constants::layout::SUPPORT_TABLE_MAX_X_TICKS;
use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;

use crate::codes;
use crate::diagnostic::Diagnostic;
use crate::common::{row_str, slug_to_display, Divider, FormatSlot, GappedDivider, Font, SignPlacement};
use crate::number_locale::active_number_locale;
use crate::families::temporal::TimeUnit;
use print::{vega_escape_attr, xml_escape, xml_escape_attr};
use measure::FontBook;

// Per-row base height before padding — mirrors `_ROW_HEIGHT_BASE` in
// `compile/support_table.py`.
const ROW_HEIGHT_BASE: f64 = 14.0;
// Row-label gutter cap — mirrors `_LABEL_STUB_LIMIT_PX` in
// `support_table_attachment.py`. Vega-Lite's `labelLimit` truncates with an
// ellipsis at this pixel width; a long `label:` would otherwise overrun the
// gutter the caller reserved for it.
const LABEL_STUB_LIMIT_PX: f64 = 80.0;

/// One `support_table` entry as the payload spells it. A `by_measure: true`
/// `per_series` entry is one row reading that column directly, headed by its
/// `label` or the raw column name: exactly a `source` entry, so it
/// deserializes into one (`SupportTableEntry::from`).
#[derive(Deserialize)]
struct RawSupportTableEntry {
    source: Option<String>,
    aggregate: Option<String>,
    #[serde(default)]
    format: Option<FormatSlot>,
    label: Option<String>,
    per_series: Option<String>,
    #[serde(default)]
    by_measure: bool,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(from = "RawSupportTableEntry")]
pub struct SupportTableEntry {
    pub source: Option<String>,
    pub aggregate: Option<String>,
    /// `None` when production's resolve-time bake left the format unset
    /// (`compile/support_table.py` only bakes a format when `entry.source ==
    /// y_field`) — render unformatted in that case, matching production.
    pub format: Option<FormatSlot>,
    pub label: Option<String>,
    /// Presence signals a `per_series:` entry — the source column each
    /// expanded series row sums (`build_rows_composed`'s per_series branch).
    pub per_series: Option<String>,
    /// A `per_series` entry that reads one column per row: it names no series
    /// yet still retires the series legend.
    pub by_measure: bool,
}

impl From<RawSupportTableEntry> for SupportTableEntry {
    fn from(raw: RawSupportTableEntry) -> Self {
        match raw.per_series {
            Some(column) if raw.by_measure => SupportTableEntry {
                label: Some(raw.label.unwrap_or_else(|| column.clone())),
                source: Some(column),
                aggregate: raw.aggregate,
                format: raw.format,
                per_series: None,
                by_measure: true,
            },
            per_series => SupportTableEntry {
                source: raw.source,
                aggregate: raw.aggregate,
                format: raw.format,
                label: raw.label,
                per_series,
                by_measure: false,
            },
        }
    }
}

#[derive(Deserialize, Debug)]
pub struct SupportTableConfig {
    pub entries: Vec<SupportTableEntry>,
    #[serde(skip)]
    fold: Option<StripFold>,
}

/// A wide chart's strip reads the rows as queried, one per x, and paints each
/// cell once per measure: the fold hands the strip's text layer one datum per
/// measure per row, each carrying the same cell value.
#[derive(Debug)]
struct StripFold {
    rows: Vec<serde_json::Map<String, Value>>,
    measures: usize,
}

impl SupportTableConfig {
    /// Whether a `per_series` entry of either kind is present: production
    /// retires the series legend then, as the strip's rows or column headers
    /// stand for it.
    pub fn retires_series_legend(&self) -> bool {
        self.entries.iter().any(|e| e.per_series.is_some() || e.by_measure)
    }

    /// The rows the strip reads: as queried on a wide chart, else `rows`.
    pub fn source_rows<'a>(&'a self, rows: &'a [serde_json::Map<String, Value>]) -> &'a [serde_json::Map<String, Value>] {
        self.fold.as_ref().map_or(rows, |fold| fold.rows.as_slice())
    }

    /// How many times a cell paints: once per measure on a wide chart.
    pub fn cell_copies(&self) -> usize {
        self.fold.as_ref().map_or(1, |fold| fold.measures)
    }

    pub fn folded_from(mut self, rows: &[serde_json::Map<String, Value>], measures: usize) -> Self {
        if measures > 0 {
            self.fold = Some(StripFold { rows: rows.to_vec(), measures });
        }
        self
    }
}

#[derive(Deserialize, Clone)]
pub struct RowPadding {
    pub vertical: f64,
    // `horizontal` was unread until `width_sampling_step` needed
    // `min_band_px = max_cell_w + 2 * padding.horizontal`
    // (`support_table_attachment.py:4335`) — the payload already carries it
    // (`SupportTableRowPaddingStyle.horizontal` is a required field), this
    // struct just never deserialized it.
    pub horizontal: f64,
}

#[derive(Deserialize, Clone)]
pub struct RowStyle {
    pub padding: RowPadding,
    /// The line under each strip row, and between support-table columns.
    pub divider: Divider,
}

#[derive(Deserialize, Clone)]
pub struct LabelStyle {
    pub font: Font,
}

#[derive(Deserialize, Clone)]
pub struct SupportTableStyle {
    pub font: Font,
    /// The line between the plot and the strip, and the gap it reserves.
    pub divider: GappedDivider,
    pub row: RowStyle,
    pub label: LabelStyle,
    pub padding_top: f64,
    pub padding_bottom: f64,
    /// Lines of horizontal x label the baked axis gap already reserves.
    pub label_max_lines: f64,
    pub position: String,
}

/// Parse `resolved.support_table` (already `Option<Value>` on the family
/// payload) into a `SupportTableConfig`.
pub fn parse_config(value: &Value, family: &str) -> Result<SupportTableConfig, String> {
    let config: SupportTableConfig = Deserialize::deserialize(value)
        .map_err(|e| format!("{family}: support_table payload: {e}"))?;
    if config.entries.iter().any(|e| e.aggregate.is_none() && e.source.is_none() && e.per_series.is_none())
    {
        return Err(format!(
            "{family}: support_table entry with neither source, aggregate, nor per_series"
        ));
    }
    Ok(config)
}

/// The distance between a grouped bar's `n` sub-bands inside a category band
/// (`bandwidth / n`, the `xOffset`/`yOffset` scale the bar marks use). A color
/// 1:1 with the category carries no offset scale: the one bar takes the band.
/// The only place the split is computed: the grouped marks, vertical and
/// horizontal, and the strip's cells all read it.
pub(crate) fn sub_band_step(bandwidth: f64, n: usize, one_to_one: bool) -> f64 {
    if one_to_one {
        bandwidth
    } else if n == 0 {
        0.0
    } else {
        bandwidth / n as f64
    }
}

/// Where sub-band `slot` starts, from its category band's start.
pub(crate) fn sub_band_start(step: f64, slot: usize, one_to_one: bool) -> f64 {
    if one_to_one { 0.0 } else { slot as f64 * step }
}

/// Each of `n` sub-bands' center, from the category band's center.
pub(crate) fn sub_band_center_offsets(bandwidth: f64, n: usize, one_to_one: bool) -> Vec<f64> {
    let step = sub_band_step(bandwidth, n, one_to_one);
    (0..n).map(|i| sub_band_start(step, i, one_to_one) + step / 2.0 - bandwidth / 2.0).collect()
}

/// Total visual rows a config expands to — port of the row-accounting loop
/// in `support_table_strip_height` (support_table_attachment.py:195-220): a
/// `per_series:` entry expands to `series_count` rows (one per series);
/// every other entry (a `by_measure` one included) is one row.
/// `series_count` is the parent chart's color-domain size, required (>0) whenever a per_series entry is present; defaulting it to 1 would
/// silently under-size the strip.
pub fn visual_row_count(config: &SupportTableConfig, series_count: usize) -> Result<usize, String> {
    let mut n = 0usize;
    for e in &config.entries {
        if e.per_series.is_some() {
            if series_count == 0 {
                return Err(
                    "support_table: a `per_series:` entry needs the parent chart's \
                     color-domain size (series_count) > 0 — defaulting to 1 would \
                     silently under-size the strip and the expanded rows would render \
                     below the reserved padding"
                        .to_string(),
                );
            }
            n += series_count;
        } else {
            n += 1;
        }
    }
    Ok(n)
}

/// Total pixel height of the strip. Port of `_strip_height`
/// (`support_table_attachment.py`) — `n_rows` is the caller's own
/// `visual_row_count` result, already accounting for per_series expansion.
pub fn strip_height(style: &SupportTableStyle, n_rows: usize) -> f64 {
    let row_h = ROW_HEIGHT_BASE + style.row.padding.vertical * 2.0;
    let divider = divider_offset(style);
    let inter_row =
        if style.row.divider.visible && n_rows > 1 { style.row.divider.width * (n_rows - 1) as f64 } else { 0.0 };
    style.padding_top + divider + row_h * n_rows as f64 + inter_row + style.padding_bottom
}

/// Divider offset from the plot edge (0.0 when the divider is hidden).
fn divider_offset(style: &SupportTableStyle) -> f64 {
    if style.divider.line.visible { style.divider.line.width + style.divider.gap } else { 0.0 }
}

/// One strip line, as the Vega-Lite `rule` mark production draws it: the
/// divider's own color and width, dashed as Vega serializes `strokeDash`.
pub(crate) fn divider_line(x1: f64, y1: f64, x2: f64, y2: f64, divider: &Divider) -> String {
    format!(
        "<line x1=\"{x1:.2}\" y1=\"{y1:.2}\" x2=\"{x2:.2}\" y2=\"{y2:.2}\" \
         stroke=\"{}\" stroke-width=\"{}\"{}/>",
        xml_escape_attr(&divider.color),
        divider.width,
        divider.vega_dash_attr()
    )
}

pub(crate) fn row_height(style: &SupportTableStyle) -> f64 {
    ROW_HEIGHT_BASE + style.row.padding.vertical * 2.0
}

/// Port of `_row_y_pixel` with `spec_height` fixed at 0.0 — see the module
/// doc for why this is a faithful reframing. `axis_offset_value` is only
/// consulted for `position: bottom`.
fn row_y_base(index: usize, style: &SupportTableStyle, axis_offset_value: f64) -> f64 {
    let row_h = row_height(style);
    let divider_off = divider_offset(style);
    let inter_row = if style.row.divider.visible && index > 0 { style.row.divider.width * index as f64 } else { 0.0 };
    if style.position == "bottom" {
        style.padding_top + axis_offset_value + divider_off + index as f64 * row_h + inter_row + row_h / 2.0
    } else {
        // top: measured from the plot top going upward (negative). Row 0 is
        // closest to the plot; row N is furthest above.
        -(style.padding_bottom + divider_off + index as f64 * row_h + inter_row + row_h / 2.0)
    }
}

/// Port of `_divider_y_pixel` with `spec_height` fixed at 0.0.
fn divider_y_base(style: &SupportTableStyle, axis_offset_value: f64) -> f64 {
    if style.position == "bottom" {
        style.padding_top + axis_offset_value
    } else {
        -style.padding_bottom
    }
}

/// Port of `_inter_row_rule_y_pixel` with `spec_height` fixed at 0.0 — the
/// rule between row `index` and row `index + 1`.
fn inter_row_y_base(index: usize, style: &SupportTableStyle, axis_offset_value: f64) -> f64 {
    let half_gap = row_height(style) / 2.0 + style.row.divider.width / 2.0;
    let row_y = row_y_base(index, style, axis_offset_value);
    if style.position == "bottom" {
        row_y + half_gap
    } else {
        row_y - half_gap
    }
}

/// A resolved cell value before formatting — a bare source column can hold
/// text (production paints it verbatim, `_numeric_column`'s "Vega renders
/// them as labels"), while an aggregate always reduces to a number.
#[derive(Clone, Debug, PartialEq)]
pub enum CellValue {
    Num(f64),
    Text(String),
}

/// Python's `float(raw)`: a JSON number as-is, a bool as 0 or 1, a string
/// parsed with the whitespace and digit-separating underscores `float()`
/// accepts. `None` for anything else, so the caller can tell "no value" (skip)
/// from "value that failed to coerce" (error). `_numeric_column` and
/// `_aggregate_by_x` both call `float()`, so a numeric-looking text cell
/// counts toward a column's measured width.
fn coerce_numeric(v: &Value) -> Option<f64> {
    match v {
        Value::Number(_) => v.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) => {
            let text = s.trim();
            let separated = text.as_bytes().windows(3).all(|w| w[1] != b'_' || (w[0].is_ascii_digit() && w[2].is_ascii_digit()));
            let edge = text.starts_with('_') || text.ends_with('_');
            if edge || !separated {
                return None;
            }
            text.replace('_', "").parse::<f64>().ok()
        }
        _ => None,
    }
}

/// Resolve one entry's per-x-domain values from the query rows.
///
/// `source` entries read the field directly (one row per x — `build_rows`
/// checks the ambiguous-aggregation guard once for the whole config before
/// calling this). A JSON number renders numeric; anything else (a string,
/// bool, etc.) paints verbatim via `row_str` — matching production's
/// `_vl_format_calc` with `format_spec is None`
/// (`isValid(v) && (!isNumber(v) || isFinite(v)) ? datum.<col> : '-'` —
/// Vega-Lite's `isNumber` is a `typeof` test, so a numeric-looking *string*
/// like `"007"` or `"1e3"` is never parsed, only a real JSON number is).
///
/// `aggregate` entries group by `x_field` and reduce with the named op.
/// `count`/`count_distinct` count matching rows/distinct raw values directly
/// (any type, including non-numeric and null) — mirroring Vega-Lite's
/// `count`/`distinct` aggregate ops, which never filter on value type. Every
/// other op requires every matching non-null value to coerce to a number —
/// production's `_aggregate_by_x` deliberately lets `float()` raise for a
/// non-numeric source under a numeric aggregate ("a query authoring error…
/// should surface as ValueError, not silently produce wrong dx"); this
/// mirrors that with a named `Err` rather than silently reducing over the
/// numeric subset.
fn entry_values(
    entry: &SupportTableEntry,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
) -> Result<Vec<Option<CellValue>>, Diagnostic> {
    let field = entry
        .source
        .as_deref()
        .ok_or_else(|| "support_table: aggregate entry missing source".to_string())?;
    if let Some(op) = &entry.aggregate {
        let mut out = Vec::with_capacity(domain.len());
        for x in domain {
            let matching: Vec<&serde_json::Map<String, Value>> = rows
                .iter()
                .filter(|row| row.get(x_field).map(value_to_key) == Some(x.clone()))
                .collect();
            let value = match op.as_str() {
                "count" => matching.len() as f64,
                "count_distinct" => {
                    let mut seen: HashSet<String> = HashSet::new();
                    for row in &matching {
                        if let Some(v) = row.get(field) {
                            if !v.is_null() {
                                seen.insert(value_to_key(v));
                            }
                        }
                    }
                    seen.len() as f64
                }
                _ => {
                    let mut vals = Vec::with_capacity(matching.len());
                    for row in &matching {
                        let Some(raw) = row.get(field) else { continue };
                        if raw.is_null() {
                            continue;
                        }
                        vals.push(coerce_numeric(raw).ok_or_else(|| {
                            format!(
                                "support_table: aggregate={op:?} on source {field:?}: \
                                 non-numeric value {raw:?} at x={x:?} — a numeric \
                                 aggregate over a non-numeric column is a query \
                                 authoring error"
                            )
                        })?);
                    }
                    match reduce(op, &vals)? {
                        Some(v) => v,
                        None => {
                            out.push(None);
                            continue;
                        }
                    }
                }
            };
            out.push(Some(CellValue::Num(value)));
        }
        Ok(out)
    } else {
        // source entry: exactly one row per x (cardinality checked once by
        // `build_rows` before any entry is resolved).
        let mut by_x: std::collections::HashMap<String, CellValue> = std::collections::HashMap::new();
        // `_require_numeric_source`: a baked number format over a source holding
        // anything but a number (a boolean included) paints `NaN`, so production raises.
        if let Some(format) = &entry.format {
            let non_numeric = rows.iter().filter_map(|row| row.get(field)).find(|raw| !raw.is_null() && !raw.is_number());
            if let Some(value) = non_numeric {
                return Err(Diagnostic::new(
                    &codes::ERR_SUPPORT_TABLE_FORMAT_NOT_NUMERIC,
                    [("format_spec", Value::from(format.spec.as_str())), ("source", Value::from(field)), ("value", value.clone())],
                ));
            }
        }
        for row in rows {
            let Some(x) = row.get(x_field) else { continue };
            let Some(raw) = row.get(field) else { continue };
            if raw.is_null() {
                continue;
            }
            let cell = match raw {
                // A real JSON number renders numeric — never a numeric-looking
                // *string* (`"007"`, `"1e3"`): Vega-Lite's `isNumber` is a
                // `typeof` test, so production never parses one either.
                // `as_f64` is total here (`arbitrary_precision` is off, so
                // every `Value::Number` converts); a non-finite value is
                // already handled at `format_cell`.
                Value::Number(n) => CellValue::Num(
                    n.as_f64().expect("Value::Number::as_f64 is total without arbitrary_precision"),
                ),
                // No baked format: paint verbatim, matching production's
                // `_vl_format_calc` with `format_spec is None` — a string is
                // *valid* and painted as-is, never coerced or dashed. A baked
                // format over a non-number raised above.
                _ => CellValue::Text(row_str(row, field)),
            };
            by_x.insert(value_to_key(x), cell);
        }
        Ok(domain.iter().map(|x| by_x.get(x).cloned()).collect())
    }
}

/// Display key for a domain-grouping value — mirrors `row_str`
/// (`common.rs`) exactly, so a `NULL` bucket in the query rows lands on the
/// same domain entry the caller built its `domain` from (both render a
/// `Value::Null` as `""`, never `"null"`).
fn value_to_key(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// One series' per-x SUM of a `per_series:` entry's source column — mirrors
/// the VL transform `_per_series_row_layers` emits for a normal-mode entry
/// (`aggregate: [{op: sum, field, as}], groupby: [x_field, color_field]`
/// plus a per-series filter, support_table_attachment.py:1394-1426) and the
/// width-measurement pool `_aggregate_by_x_and_color` draws from
/// (:2121-2145): skips a row whose x, color, or raw value is null, and
/// requires every remaining matching value to coerce numeric (a
/// `per_series:` column is expected to always be numeric — mirrors
/// `entry_values`'s aggregate-path refusal on a non-numeric source).
fn per_series_values(
    per_series_field: &str,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    color_field: &str,
    series_value: &str,
) -> Result<Vec<Option<CellValue>>, String> {
    let mut out = Vec::with_capacity(domain.len());
    for x in domain {
        let mut sum = 0.0_f64;
        let mut any = false;
        for row in rows {
            let Some(rx) = row.get(x_field) else { continue };
            if value_to_key(rx) != *x {
                continue;
            }
            let Some(rc) = row.get(color_field) else { continue };
            if rc.is_null() || value_to_key(rc) != series_value {
                continue;
            }
            let Some(raw) = row.get(per_series_field) else { continue };
            if raw.is_null() {
                continue;
            }
            let v = coerce_numeric(raw).ok_or_else(|| {
                format!(
                    "support_table: per_series={per_series_field:?}: non-numeric value \
                     {raw:?} at x={x:?}, series={series_value:?} — a per_series column is \
                     expected to always be numeric"
                )
            })?;
            sum += v;
            any = true;
        }
        out.push(if any { Some(CellValue::Num(sum)) } else { None });
    }
    Ok(out)
}

fn reduce(op: &str, vals: &[f64]) -> Result<Option<f64>, String> {
    if vals.is_empty() {
        return Ok(None);
    }
    Ok(Some(match op {
        "sum" => vals.iter().sum(),
        "avg" => vals.iter().sum::<f64>() / vals.len() as f64,
        "min" => vals.iter().cloned().fold(f64::INFINITY, f64::min),
        "max" => vals.iter().cloned().fold(f64::NEG_INFINITY, f64::max),
        "median" => {
            let mut sorted = vals.to_vec();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let mid = sorted.len() / 2;
            if sorted.len() % 2 == 0 {
                (sorted[mid - 1] + sorted[mid]) / 2.0
            } else {
                sorted[mid]
            }
        }
        other => return Err(format!("support_table: unsupported aggregate op {other:?}")),
    }))
}

// ── Declare-once numerals (port of `strip_numerals_for_values` /
//    `StripNumerals`, support_table_attachment.py + numeral_scale.py) ─────

/// How one strip row spells its numbers: the digit spec each cell formats
/// through, plus the affix the anchor cell alone declares. Two spellings:
/// a `s`-type (SI) format is COMPOSED — the row divides by one shared
/// magnitude and formats through a rebuilt fixed-point spec — while every
/// other affix-carrying format is TRIMMED — cells format through the
/// author's own spec and non-anchor cells have the repeated affix removed
/// from the formatted string. An un-anchored row (production's
/// `plain_numerals` / `StripAnchor.nowhere()`) declares nothing: every cell
/// paints the author's spec in full.
pub struct StripNumerals {
    pub(crate) digit_spec: String,
    /// The currency the anchor cell declares, spelled the board's way.
    prefix: String,
    suffix: String,
    divisor: f64,
    suffix_is_magnitude: bool,
    repeat_suffix: bool,
    /// The value axis is on the left (`hang = "right"`): cells align left, so
    /// the anchor's trailing affix extends into its own band.
    hang_right: bool,
    /// A leading `$` paints on every cell while the magnitude suffix still
    /// declares once: on a left-aligned strip the anchor's prefix would push
    /// its digits off the shared edge.
    prefix_repeats: bool,
    /// An affix or notation a d3 spec cannot carry (`StripNumerals.authored`):
    /// its spec is `digit_spec`, painted on the anchor cell alone when
    /// `declares_once`, else on every cell.
    authored: Option<FormatSlot>,
    declares_once: bool,
    /// `repeat: every`: each cell declares the unit, none is bare.
    pub(crate) every: bool,
    /// The trailing pad `decimal_pad_for` selects per cell so mixed fractional
    /// depths line up on the decimal point; empty when nothing aligns.
    decimal_pad: Vec<String>,
}

impl StripNumerals {
    fn plain(spec: &str) -> StripNumerals {
        StripNumerals {
            decimal_pad: Vec::new(),
            digit_spec: spec.to_string(),
            prefix: String::new(),
            suffix: String::new(),
            divisor: 1.0,
            suffix_is_magnitude: false,
            repeat_suffix: false,
            hang_right: false,
            prefix_repeats: false,
            authored: None,
            declares_once: false,
            every: false,
        }
    }

    /// A row that keeps the author's own spec with no unit to declare, aligned
    /// by the deepest fractional depth its cells print (`_unscaled_decimal_pad`).
    fn unscaled(values: &[f64], spec: &str, pad: &DecimalPad) -> Result<StripNumerals, String> {
        Ok(StripNumerals { decimal_pad: pad.unscaled(values, spec)?, ..StripNumerals::plain(spec) })
    }

    /// `_entry_numerals` for a literal d3 spec or alias: it opts out of the
    /// house rules but keeps the decimal alignment of a trim fixed-point spec.
    pub(crate) fn literal(spec: &str, pad: &DecimalPad) -> Result<StripNumerals, String> {
        Ok(StripNumerals { decimal_pad: pad.for_spec(spec, None)?, ..StripNumerals::plain(spec) })
    }

    /// `plain_numerals`: nothing anchors, so every cell paints the author's
    /// spec in full. An SI spec with no trim table aligns on its cells' own
    /// observed depth.
    pub(crate) fn undeclared(values: &[f64], spec: &str, pad: &DecimalPad) -> Result<StripNumerals, String> {
        let literal = StripNumerals::literal(spec, pad)?;
        let is_si = d3rs::format::parse_parts(spec).is_ok_and(|p| p.type_ == 's');
        if literal.decimal_pad.is_empty() && is_si {
            return StripNumerals::unscaled(values, spec, pad);
        }
        Ok(literal)
    }

    /// `_authored_affix_numerals`: the affix declares on the anchor cell when
    /// the strip is `anchored` and the slot asks (`repeat: anchor`, or a house
    /// format's default), on every cell otherwise (`repeat: every`, a
    /// literal d3 spec, a notation alone). A `$` spec and an anchored
    /// `before_prefix` sign refuse by name.
    pub(crate) fn authored(
        slot: &FormatSlot,
        anchored: bool,
        hang_right: bool,
        pad: &DecimalPad,
        field_path: &str,
    ) -> Result<StripNumerals, Diagnostic> {
        let spec = &slot.spec;
        let declares_once = anchored
            && (!slot.prefix.is_empty() || !slot.suffix.is_empty())
            && match slot.repeat.as_deref() {
                Some(repeat) => repeat == "anchor",
                // A left-aligned anchor's leading prefix would push its digits
                // off the shared edge, so a house format with one repeats it.
                None => slot.is_house() && !(hang_right && !slot.prefix.is_empty()),
            };
        if declares_once && !slot.prefix.is_empty() && slot.sign_placement == Some(SignPlacement::BeforePrefix) {
            return Err(Diagnostic::new(
                &codes::ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX,
                [("field_path", serde_json::json!(field_path))],
            ));
        }
        Ok(StripNumerals {
            authored: Some(slot.clone()),
            declares_once,
            hang_right,
            decimal_pad: pad.for_spec(spec, None)?,
            ..StripNumerals::plain(spec)
        })
    }

    /// The trailing affix only the anchor paints on a right-aligned strip
    /// (`StripNumerals.hung_suffix`, support_table_attachment.py:785-796).
    /// Right-aligned text puts a trailing affix on the aligned edge, so the
    /// anchor's digits would sit one affix-width left of every bare cell's;
    /// the affix hangs past the edge instead. A left-aligned row trails it
    /// into the anchor's own band, and a REPEAT row paints it on every cell.
    pub(crate) fn hung_suffix(&self) -> &str {
        if let Some(slot) = &self.authored {
            return if self.declares_once && !self.hang_right { &slot.suffix } else { "" };
        }
        if self.hang_right || self.repeat_suffix {
            ""
        } else {
            &self.suffix
        }
    }

    /// Whether `v` can declare the row's unit (`_drawn_index_transform`'s
    /// CARRIES flag): finite, and non-zero when the suffix is a magnitude —
    /// "0k" is meaningless, while $0 and 0% are real readings.
    pub fn carries(&self, v: f64) -> bool {
        v.is_finite() && !(self.suffix_is_magnitude && v == 0.0)
    }

    fn padded(&self, text: String) -> Result<String, String> {
        let pad = crate::families::table::decimal_pad_for(&self.decimal_pad, &text)?;
        Ok(format!("{text}{pad}"))
    }

    /// The text one cell paints. `is_anchor` marks the single unit-declaring
    /// cell (the first carrying cell in paint order).
    pub fn cell_text(&self, v: f64, is_anchor: bool) -> Result<String, String> {
        let is_anchor = is_anchor || self.every;
        if let Some(slot) = &self.authored {
            // `_compose_affixed_text` / `_bare_authored_text`: the declaring
            // cell leads its sign with the prefix, the rest paint the digits
            // alone.
            let text = if !self.declares_once || is_anchor {
                slot.paint_anchored(&self.digit_spec, v, self.declares_once, &self.decimal_pad)
            } else {
                d3rs::format::without_symbol(&self.digit_spec)
                    .and_then(|bare_spec| slot.bare_digits(&bare_spec, v, &self.decimal_pad))
            };
            return text.map_err(|e| format!("support_table: format {:?}: {e}", self.digit_spec));
        }
        if self.prefix.is_empty() && self.suffix.is_empty() {
            let text = crate::number_locale::d3_format(&self.digit_spec, v)
                .map_err(|e| format!("support_table: format {:?}: {e}", self.digit_spec))?;
            return self.padded(text);
        }
        if !self.suffix_is_magnitude && self.prefix.is_empty() {
            // Trimmed: format through the author's spec, then remove the
            // repeated suffix from the cell — rebuilding the spec is what would
            // silently drop a sign or zero pad. A left-axis (`hang_right`)
            // anchor puts the suffix back after the stripped text, so a padded
            // spec keeps its digits on the shared left edge.
            let full = crate::number_locale::d3_format(&self.digit_spec, v)
                .map_err(|e| format!("support_table: format {:?}: {e}", self.digit_spec))?;
            if is_anchor && !self.hang_right {
                return Ok(full);
            }
            let mut bare = full;
            if !self.suffix.is_empty() {
                bare = bare.replacen(self.suffix.as_str(), "", 1);
            }
            if is_anchor {
                bare.push_str(&self.suffix);
            }
            return Ok(bare);
        }
        // Composed: the sign leads the currency ("−$448"); a magnitude suffix
        // never shows on zero, a unit suffix does.
        let digits = crate::number_locale::d3_format(&self.digit_spec, v.abs() / self.divisor)
            .map_err(|e| format!("support_table: format {:?}: {e}", self.digit_spec))?;
        let digits = self.padded(digits)?;
        let locale = active_number_locale();
        let sign = if v < 0.0 { locale.locale.minus.as_str() } else { "" };
        let prefix = if is_anchor || self.prefix_repeats { self.prefix.as_str() } else { "" };
        let suffix_on = (!self.suffix_is_magnitude || v != 0.0) && (self.repeat_suffix || is_anchor);
        let suffix = if suffix_on { self.suffix.as_str() } else { "" };
        Ok(format!("{sign}{prefix}{digits}{suffix}"))
    }
}

/// SI tiers a shared scale may land on, highest first (`_TIERS`).
const SI_TIERS: [i32; 4] = [12, 9, 6, 3];

fn natural_tier(v: f64) -> Option<i32> {
    let magnitude = v.abs();
    SI_TIERS.iter().copied().find(|&t| magnitude >= 10f64.powi(t))
}

/// Digits before the decimal point in `v`'s plain written-out form
/// (truncating — 999.5 has 3 written integer digits).
fn integer_digit_count(v: f64) -> usize {
    format!("{}", v.abs().floor() as i128).len()
}

/// Like `integer_digit_count` but negative-capable below 1.0
/// (`_decimal_exponent`).
fn decimal_exponent(v: f64) -> i32 {
    if v == 0.0 {
        return 1;
    }
    v.abs().log10().floor() as i32 + 1
}

/// The face `decimal_pad_table_for` composes its padding units from: the
/// support table's own `font.family`. Units are composed when a table is
/// actually built, never for a chart that aligns nothing.
pub(crate) struct DecimalPad<'a> {
    fonts: &'a FontBook,
    family: &'a str,
}

impl<'a> DecimalPad<'a> {
    pub(crate) fn new(fonts: &'a FontBook, style: &'a SupportTableStyle) -> Result<Self, String> {
        let family = style.font.family.as_deref().ok_or("support_table: font.family absent (theme required)")?;
        Ok(DecimalPad { fonts, family })
    }

    fn table(&self, precision: usize) -> Result<Vec<String>, String> {
        let measurer = self.fonts.measurer(self.family)?;
        let (digit_unit, dot_unit) = mdsvg::reservation::compose_decimal_units(&measurer, self.family)?;
        Ok(d3rs::numeral_pad::build_decimal_pad_table(precision, &digit_unit, &dot_unit))
    }

    /// `decimal_pad_table_for`: a table for a trim-enabled fixed-point spec
    /// with decimals, empty for every other spec. A spec d3 cannot parse (a
    /// native name) is empty too, as in production: it fails where it paints.
    fn for_spec(&self, spec: &str, max_precision: Option<usize>) -> Result<Vec<String>, String> {
        let Some(declared) = d3rs::numeral_pad::pad_table_precision(spec) else { return Ok(Vec::new()) };
        match max_precision.map_or(declared, |cap| declared.min(cap)) {
            0 => Ok(Vec::new()),
            precision => self.table(precision),
        }
    }

    /// `_unscaled_decimal_pad`: a row with no shared magnitude aligns on the
    /// deepest fractional depth its own cells print, when they share one unit.
    fn unscaled(&self, values: &[f64], spec: &str) -> Result<Vec<String>, String> {
        let texts: Vec<(f64, String)> = values
            .iter()
            .filter(|v| v.is_finite())
            .map(|&v| {
                crate::number_locale::d3_format(spec, v)
                    .map(|t| (v, t))
                    .map_err(|e| format!("support_table: format {spec:?}: {e}"))
            })
            .collect::<Result<_, _>>()?;
        let decimal = &crate::number_locale::active_number_locale().locale.decimal;
        match d3rs::numeral_pad::unscaled_pad_precision(&texts, decimal) {
            Some(deepest) => self.table(deepest),
            None => Ok(Vec::new()),
        }
    }

    /// `_shared_scale_decimal_pad`: a shared-scale row's digit spec, capped at
    /// the deepest depth its scaled cells actually print.
    fn shared_scale(&self, values: &[f64], digit_spec: &str, magnitude: f64) -> Result<Vec<String>, String> {
        if self.for_spec(digit_spec, None)?.is_empty() {
            return Ok(Vec::new());
        }
        let depths: HashSet<usize> = values
            .iter()
            .filter(|v| v.is_finite())
            .map(|&v| {
                crate::number_locale::d3_format(digit_spec, v / magnitude)
                    .map(|t| crate::families::table::count_decimals(&t))
                    .map_err(|e| format!("support_table: format {digit_spec:?}: {e}"))
            })
            .collect::<Result<_, _>>()?;
        match depths.iter().max() {
            Some(&deepest) if depths.len() > 1 => self.for_spec(digit_spec, Some(deepest)),
            _ => Ok(Vec::new()),
        }
    }
}

/// Port of `strip_numerals_for_values` + `_magnitude_numerals`
/// (support_table_attachment.py) over `shared_scale_for_column`
/// (numeral_scale.py). `anchored` is the caller's anchor decision — false
/// (production's `plain_numerals`: an unpinned sort, quantitative x) paints
/// the author's spec in full on every cell. `hang_right` is the value axis's
/// own orient (`hang = "right"` when `axis_y_orient != "right"`, i.e. a LEFT
/// axis), which production pairs with `prefix_repeats`: a leading `$` on the
/// left-aligned anchor would push its digits off the shared edge, so a
/// trimmed (non-`s`) symbol format cannot anchor there and every cell keeps
/// the author's spec, while a composed (`s`) one repeats the `$` on every cell
/// and still declares its magnitude suffix once. An affix-free format has
/// nothing to hang and anchors normally either way. `vertical` is a column
/// block (`vertical=True`): its magnitude suffix speaks the analytic register
/// while it declares once, narrative once it repeats.
pub(crate) fn strip_numerals_for_values(
    values: &[f64],
    resolved: &str,
    hang_right: bool,
    vertical: bool,
    every: bool,
    pad: &DecimalPad,
) -> Result<StripNumerals, String> {
    let parts = d3rs::format::parse_parts(resolved)
        .map_err(|e| format!("support_table: format {resolved:?}: {e}"))?;
    if parts.type_ != 's' {
        // A left-aligned strip repeats a leading currency, so its anchor
        // cannot carry one: every cell keeps the author's spec.
        if hang_right && parts.symbol {
            return Ok(StripNumerals::plain(resolved));
        }
        let locale = active_number_locale();
        let suffix = if parts.type_ == '%' { locale.locale.percent.clone() } else { String::new() };
        let (lead, trail) = currency_affixes(parts.symbol, every);
        if !trail.is_empty() {
            return Ok(StripNumerals { suffix, hang_right, ..StripNumerals::plain(resolved) });
        }
        let digit_spec = if lead.is_empty() { resolved.to_string() } else { d3rs::format::without_symbol(resolved)? };
        return Ok(StripNumerals { digit_spec, prefix: lead, suffix, hang_right, ..StripNumerals::plain(resolved) });
    }

    // Composed (`s` type): majority-vote tier over non-zero finite values;
    // every refusal below keeps the author's spec (the row simply doesn't
    // compact) — production's own fallback, never a heuristic.
    let voters: Vec<f64> = values.iter().copied().filter(|v| *v != 0.0 && v.is_finite()).collect();
    if voters.is_empty() {
        return StripNumerals::unscaled(values, resolved, pad);
    }
    let mut tally: std::collections::HashMap<Option<i32>, usize> = std::collections::HashMap::new();
    for v in &voters {
        *tally.entry(natural_tier(*v)).or_insert(0) += 1;
    }
    let top = *tally.values().max().expect("voters is non-empty");
    let tied: Vec<Option<i32>> = tally.iter().filter(|(_, &c)| c == top).map(|(t, _)| *t).collect();
    if tied.contains(&None) {
        return StripNumerals::unscaled(values, resolved, pad);
    }
    let majority = tied.iter().filter_map(|t| *t).max().expect("no None in tie");
    let extreme = values.iter().fold(0.0_f64, |a, v| a.max(v.abs()));
    if integer_digit_count(extreme) < 6 {
        return StripNumerals::unscaled(values, resolved, pad);
    }
    let magnitude = 10f64.powi(majority);
    let finest = voters
        .iter()
        .copied()
        .min_by(|a, b| a.abs().partial_cmp(&b.abs()).expect("voters are finite"))
        .expect("voters is non-empty");
    // tier_distance: how many tiers below the majority the finest value's own
    // tier sits; None (below every tier) or > 1 would inflate every sibling's
    // precision — refuse.
    let tiers_ascending = [3, 6, 9, 12];
    let distance = match natural_tier(finest) {
        None => None,
        Some(vt) => Some(
            tiers_ascending.iter().position(|&t| t == majority).expect("majority is a tier")
                as i32
                - tiers_ascending.iter().position(|&t| t == vt).expect("tier from natural_tier")
                    as i32,
        ),
    };
    match distance {
        None => return StripNumerals::unscaled(values, resolved, pad),
        Some(d) if d > 1 => return StripNumerals::unscaled(values, resolved, pad),
        _ => {}
    }
    let sig_figs = parts.precision.unwrap_or(6) as i32;
    let decimals = (sig_figs - decimal_exponent(finest.abs() / magnitude)).max(0);
    let digit_spec = format!(",.{decimals}~f");
    // Zero-print guard: a voter whose scaled form prints no non-zero digit
    // would be indistinguishable from a genuine zero row.
    for v in &voters {
        let printed = crate::number_locale::d3_format(&digit_spec, v / magnitude)
            .map_err(|e| format!("support_table: format {digit_spec:?}: {e}"))?;
        if !printed.chars().any(|c| c.is_ascii_digit() && c != '0') {
            return StripNumerals::unscaled(values, resolved, pad);
        }
    }
    let decimal_pad = pad.shared_scale(values, &digit_spec, magnitude)?;
    let mode_repeat = integer_digit_count(extreme / magnitude) >= 4;
    let pow = 10f64.powi(decimals);
    let printed_extreme = (extreme / magnitude * pow).round() / pow;
    let repeat = mode_repeat || integer_digit_count(printed_extreme) >= 4;
    let (lead, trail) = currency_affixes(parts.symbol, hang_right || every);
    Ok(StripNumerals {
        digit_spec,
        prefix_repeats: hang_right && !lead.is_empty(),
        prefix: lead,
        suffix: format!("{}{trail}", crate::common::suffix_at_register(majority, vertical && !repeat)?),
        divisor: magnitude,
        suffix_is_magnitude: true,
        repeat_suffix: repeat,
        hang_right,
        authored: None,
        declares_once: false,
        every: false,
        decimal_pad,
    })
}

/// `_currency_affixes`: a `$` spec's currency as `(lead, trail)` around the
/// digits and a magnitude suffix. Declared once it leads; on every cell
/// (`repeats`) it keeps the locale's side.
fn currency_affixes(has_symbol: bool, repeats: bool) -> (String, String) {
    if !has_symbol {
        return (String::new(), String::new());
    }
    let locale = active_number_locale();
    let (lead, trail) = locale.composed_currency("$", repeats);
    if lead.is_empty() && trail.is_empty() {
        return (locale.locale.currency[0].clone(), String::new());
    }
    (lead, trail)
}

pub(crate) fn format_cell(v: Option<&CellValue>, spec: Option<&str>) -> Result<String, String> {
    match (v, spec) {
        (None, _) => Ok("-".to_string()),
        (Some(CellValue::Num(v)), _) if !v.is_finite() => Ok("-".to_string()),
        // No baked format (production only bakes one when entry.source ==
        // y_field) — render the plain number, matching production.
        (Some(CellValue::Num(v)), None) => Ok(crate::families::color_scale::js_number_string(*v)),
        (Some(CellValue::Num(v)), Some(spec)) => {
            crate::number_locale::d3_format(spec, *v).map_err(|e| format!("support_table: format {spec:?}: {e}"))
        }
        // Bare source column holding a string: painted verbatim, matching
        // production's `_vl_format_calc` with `format_spec is None`.
        (Some(CellValue::Text(s)), _) => Ok(s.clone()),
    }
}

/// What production's description of a strip cell is built from: the cell's x
/// as the parent chart's own mark would describe it (`week: 2024-01-01`), one
/// per domain entry, and the color field's title for a `per_series:` row's
/// `; Category: Electronics` tail.
pub struct CellAria {
    pub x: Vec<String>,
    pub color_title: Option<String>,
}

impl CellAria {
    pub(crate) fn label(&self, i: usize, series: Option<&str>) -> String {
        match (series, &self.color_title) {
            (Some(s), Some(title)) => format!("{}; {title}: {s}", self.x[i]),
            _ => self.x[i].clone(),
        }
    }
}

impl ComposedRow {
    pub fn describe(&mut self, aria: &CellAria) {
        for (i, cell) in self.cells.iter_mut().enumerate() {
            cell.aria = aria.label(i, self.series.as_deref());
        }
    }
}

/// One composed-layout cell: the painted text at its x on the parent
/// chart's own x encoding (the anchor's hung suffix width already added on a
/// strip row, `build_cells`), with the shaped width Vega's autosize-pad bounds
/// union reads (S6 — an edge cell overhangs the plot by its full width).
/// `hang` is the anchor's hung-suffix width, 0 on every other cell; a column
/// block reads it to place the anchor past its digit edge.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ComposedCell {
    pub x: f64,
    pub text: String,
    pub w: f64,
    pub hang: f64,
    /// Production's description of the cell, set by `describe`; empty until then.
    pub aria: String,
    /// The chart's link around the cell, when it has one (`link_cells`).
    pub href: Option<String>,
}

/// One composed-layout row. The cells' aligned edge sits on the parent's x
/// (a line/area point) offset by `dx` — half the widest *bare* cell
/// (`_compute_support_table_entry_dx`), so a bar's number lane centers on
/// its band while the anchor's affix overhangs into the gutter; production
/// writes no `dx` at all off a band scale, and `build_rows_composed` then
/// leaves it 0. The row label is a stub in the value axis's own gutter,
/// painted in `label_color` when set — a `per_series:` row's series-ink
/// (`_per_series_row_layers`'s `mark["fill"] = dark_fill`,
/// support_table_attachment.py:1473-1474) — else the style's own label ink.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ComposedRow {
    pub label: String,
    pub label_w: f64,
    pub cells: Vec<ComposedCell>,
    pub dx: f64,
    pub label_color: Option<String>,
    /// How far the stub sits from its own axis-side anchor: a grouped bar's
    /// `xOffset` channel moves it by the sub-band its series-less row opens.
    pub stub_dx: f64,
    /// The series a `per_series:` row prints; `None` on every other row.
    pub series: Option<String>,
    /// Width the row's anchor cell WOULD measure if it landed on this
    /// row's own largest-magnitude value, at the row's own affix — never
    /// the actual anchor cell's width, which sits wherever the first
    /// carrying value happens to fall (`support_table_attachment.py:4310-
    /// 4337`'s `band_budget_w` widening loop, `max_abs_val = max(_vals,
    /// key=abs)`). 0.0 on an unanchored row (nothing to widen for).
    pub anchor_worst_case_w: f64,
    /// Each cell paints once per entry, shifted along the category axis: a wide
    /// chart carries one entry per measure, every other row a single `0.0`.
    pub copy_dx: Vec<f64>,
}

/// The links a chart's `link` puts on an entry's cells, one per category of
/// `domain`: a plain source entry reads its category's datum, while an
/// aggregate or per-series entry reads an aggregate row whose link Vega
/// writes as `undefined`. `links` is the chart's hrefs by `(category, series)`,
/// `None` on a chart with no link.
pub(crate) fn link_cells(
    cells: &mut [ComposedCell],
    entry: &SupportTableEntry,
    domain: &[String],
    links: Option<&HashMap<(String, String), String>>,
) {
    let Some(links) = links else { return };
    let aggregated = entry.aggregate.is_some() || entry.per_series.is_some();
    for (cell, cat) in cells.iter_mut().zip(domain) {
        cell.href = Some(if aggregated {
            crate::href::UNDEFINED_HREF.to_string()
        } else {
            links.get(&(cat.clone(), String::new())).cloned().unwrap_or_else(|| crate::href::UNDEFINED_HREF.to_string())
        });
    }
}

/// Cells + the pooled bare-cell width (dx input) for one row's own values —
/// factored out of `build_rows_composed` so the per_series branch can build
/// N rows off the same logic a normal entry uses for one. `hang_along_x` is
/// true for a strip row only, where the cell's aligned edge and band axis
/// coincide: the anchor's `x` then carries its hung-suffix width. A column
/// block's `x` is the band center along the category axis, so it keeps `x`
/// and reads `ComposedCell::hang` to place the anchor along its own edge.
#[allow(clippy::too_many_arguments)]
pub(crate) fn build_cells(
    values: &[Option<CellValue>],
    x_positions: &[f64],
    numerals: &Option<StripNumerals>,
    spec: Option<&str>,
    fonts: &FontBook,
    font_family: &str,
    font_size: f64,
    hang_along_x: bool,
) -> Result<(Vec<ComposedCell>, f64), String> {
    // The anchor is the first carrying cell in paint order
    // (`_drawn_index_transform`: valid + finite, non-zero for a magnitude
    // suffix) — computed per ROW even for a per_series entry's shared
    // numerals ("After the per-series filter, so each series numbers its
    // own drawn cells and anchors its own leftmost one",
    // support_table_attachment.py:1416-1419).
    let anchor_idx = numerals
        .as_ref()
        .and_then(|n| values.iter().position(|v| matches!(v, Some(CellValue::Num(num)) if n.carries(*num))));
    let mut max_bare_w = 0.0_f64;
    let mut cells = Vec::with_capacity(values.len());
    for (i, (v, &x)) in values.iter().zip(x_positions.iter()).enumerate() {
        // The anchor paints on a layer of its own, `dx` grown by its hung
        // suffix's width (`_hung_anchor_layers`, :1197-1226).
        let mut hang = 0.0_f64;
        let text = match (v, numerals) {
            (Some(CellValue::Num(num)), Some(n)) if num.is_finite() => {
                // Bare width feeds dx for every cell — the anchor's affix
                // deliberately overhangs its lane.
                let bare = n.cell_text(*num, false)?;
                max_bare_w = max_bare_w.max(fonts.measure(&bare, font_family, font_size)?);
                let is_anchor = anchor_idx == Some(i);
                if is_anchor {
                    hang = fonts.measure(n.hung_suffix(), font_family, font_size)?;
                }
                n.cell_text(*num, is_anchor)?
            }
            _ => {
                let text = format_cell(v.as_ref(), spec)?;
                if matches!(v, Some(CellValue::Num(_))) {
                    max_bare_w = max_bare_w.max(fonts.measure(&text, font_family, font_size)?);
                }
                text
            }
        };
        let w = fonts.measure_shaped(&text, font_family, font_size)?;
        let x = if hang_along_x { x + hang } else { x };
        cells.push(ComposedCell { x, text, w, hang, aria: String::new(), href: None });
    }
    Ok((cells, max_bare_w))
}

/// Port of `support_table_attachment.py:4318-4337`'s `band_budget_w`
/// widening loop, isolated from `build_cells`: measures the anchor's own
/// affix-decorated text at the row's LARGEST-magnitude value (`max_abs_val
/// = max(_vals, key=abs)`), not at whichever value the drawn-index anchor
/// actually lands on. A CARRIES-skipped anchor (the first cell is zero, so
/// the running sum anchors the next non-zero cell instead) can land on a
/// value smaller than the row's own max — sizing the width-sampling budget
/// to the ACTUAL anchor cell alone would under-measure whenever some other
/// cell in the row prints more digits than the anchor cell does. Returns
/// 0.0 for an unanchored row (`numerals.is_none()`) or a row with no
/// numeric values at all — nothing to widen for. `nums` is the SAME
/// already-extracted numeric list its caller built to construct
/// `numerals` in the first place (`strip_numerals_for_values`'s own input).
fn anchor_worst_case_cell_width(
    nums: &[f64],
    numerals: &Option<StripNumerals>,
    fonts: &FontBook,
    font_family: &str,
    font_size: f64,
) -> Result<f64, String> {
    let Some(n) = numerals else { return Ok(0.0) };
    let max_abs = nums
        .iter()
        .copied()
        .filter(|x| x.is_finite())
        .max_by(|a, b| a.abs().partial_cmp(&b.abs()).unwrap());
    let Some(max_abs) = max_abs else { return Ok(0.0) };
    let text = n.cell_text(max_abs, true)?;
    fonts.measure(&text, font_family, font_size)
}

/// One entry's cells per visual line — a strip row or a column block column —
/// and the declare-once numerals its whole block shares. `lines` holds one
/// value list per series for a normal `per_series:` entry and exactly one
/// otherwise; `nums` pools every line's numbers, the input production's
/// per-ENTRY `entry_values`/`_entry_numerals` see (`support_table_attachment.py`
/// `_entry_values`/`_entry_numerals`), never one decision per line.
pub(crate) struct EntryLines<'a> {
    pub(crate) spec: Option<&'a str>,
    pub(crate) lines: Vec<Vec<Option<CellValue>>>,
    pub(crate) numerals: Option<StripNumerals>,
    pub(crate) nums: Vec<f64>,
}

/// Resolve one entry against the query rows. `anchored` is the caller's
/// production anchor decision and `axis_on_right` its numeral `hang`
/// (`strip_numerals_for_values`); a `per_series:` entry additionally needs the
/// chart's color field and series list, refused by name otherwise (production's
/// own guard, `attach_support_table`, :1910-1921). `vertical` is a column
/// block, whose magnitude suffix speaks the column register
/// (`strip_numerals_for_values`).
#[allow(clippy::too_many_arguments)]
pub(crate) fn entry_lines<'a>(
    entry: &'a SupportTableEntry,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    anchored: bool,
    axis_on_right: bool,
    vertical: bool,
    color_field: Option<&str>,
    series_order: &[String],
    pad: &DecimalPad,
    chart_id: &str,
    entry_index: usize,
) -> Result<EntryLines<'a>, Diagnostic> {
    let spec = entry.format.as_ref().map(|f| f.spec.as_str());
    // `_entry_numerals`: a predefined name declares once; a d3 literal with
    // the same spec keeps per-cell units.
    let predefined = entry.format.as_ref().is_some_and(FormatSlot::is_house);
    let lines: Vec<Vec<Option<CellValue>>> = if let Some(per_series_field) = &entry.per_series {
        let Some(cfield) = color_field else {
            return Err(
                "support_table: a `per_series:` entry requires the chart to carry a \
                 color encoding"
                    .to_string().into(),
            );
        };
        if series_order.is_empty() {
            return Err(
                "support_table: a `per_series:` entry requires series_order — the \
                 parent chart's color-domain series list"
                    .to_string().into(),
            );
        }
        // One SUM(per_series) grouped by (x, color) per series, filtered
        // to that series (`_per_series_row_layers`,
        // support_table_attachment.py:1394-1426).
        series_order
            .iter()
            .map(|s| per_series_values(per_series_field, rows, x_field, domain, cfield, s))
            .collect::<Result<Vec<_>, _>>()?
    } else {
        vec![entry_values(entry, rows, x_field, domain)?]
    };
    let nums: Vec<f64> = lines
        .iter()
        .flatten()
        .filter_map(|v| match v {
            Some(CellValue::Num(n)) => Some(*n),
            Some(CellValue::Text(text)) => coerce_numeric(&Value::String(text.clone())),
            None => None,
        })
        .collect();
    // A percent spec is fed ratios: a value of 10 or more is a forgotten division by 100.
    if let Some(spec) = spec {
        for n in &nums {
            crate::common::check_percent_range(*n, None, spec)?;
        }
    }
    let numerals = match (&entry.format, spec) {
        (Some(slot), _) if slot.has_affix() => {
            Some(StripNumerals::authored(
                slot,
                anchored,
                !axis_on_right,
                pad,
                &format!("charts.{chart_id}.support_table.entries[{entry_index}].format"),
            )?)
        }
        (_, Some(s)) if !anchored => Some(StripNumerals::undeclared(&nums, s, pad)?),
        (_, Some(s)) if !predefined => Some(StripNumerals::literal(s, pad)?),
        (slot, Some(s)) => {
            let every = slot.as_ref().is_some_and(|f| f.repeat.as_deref() == Some("every"));
            let mut numerals = strip_numerals_for_values(&nums, s, !axis_on_right, vertical, every, pad)?;
            numerals.every = every;
            Some(numerals)
        }
        (_, None) => None,
    };
    Ok(EntryLines { spec, lines, numerals, nums })
}

/// Build the composed strip's rows: declare-once numerals per entry
/// (`anchored` per the caller's production anchor decision), band-centering
/// dx from the bare cell widths (measured with the strip's own font — the
/// tabular font production's `numeric=True` measurer picks) when
/// `band_centered`, and the stub label truncated at `LABEL_STUB_LIMIT_PX`.
///
/// `band_centered` is the caller's own scale fact: production computes an
/// entry dx only for a cell sitting on a band (a bar's category), and
/// passes `dx = None` for a line/area point — where the aligned edge sits
/// on the point's x itself. `axis_on_right` is the same value axis orient
/// `composed_ink`/`render_rows_composed` take — a symbol format cannot
/// anchor on a LEFT axis (`hang = "right"` there — see
/// `strip_numerals_for_values`'s doc comment), so every caller pinned to a
/// right axis (both existing callers) is unaffected and the rail-concat
/// arm's own left-axis calls get the refusal for free.
///
/// `color_field`/`series_order`/`series_ink` are the caller's own resolved
/// color domain — `series_order[i]`'s dark-companion ink is
/// `series_ink[i]` (`common::series_ink` on the chart's color scale, the
/// SAME lookup the endpoint rail's own label ink uses). This module never
/// resolves a color scale itself; a `per_series:` entry with no
/// `color_field` or an empty `series_order` refuses by name, mirroring
/// production's own guard (`attach_support_table`, :1910-1921).
#[allow(clippy::too_many_arguments)]
pub fn build_rows_composed(
    chart_id: &str,
    config: &SupportTableConfig,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    x_positions: &[f64],
    fonts: &FontBook,
    style: &SupportTableStyle,
    anchored: bool,
    band_centered: bool,
    axis_on_right: bool,
    color_field: Option<&str>,
    series_order: &[String],
    series_ink: &[String],
    links: Option<&HashMap<(String, String), String>>,
    ordered_x: bool,
) -> Result<Vec<ComposedRow>, Diagnostic> {
    if domain.len() != x_positions.len() {
        return Err("support_table: domain/x_positions length mismatch".into());
    }
    let rows = config.source_rows(rows);
    validate_against_data(config, rows, x_field, domain, ordered_x)?;
    let (font_family, font_size) = cell_font(style)?;
    let (label_family, label_size) = label_font_resolved(style);

    let mut out: Vec<ComposedRow> = Vec::new();
    for (entry_index, entry) in config.entries.iter().enumerate() {
        let EntryLines { spec, lines, numerals, nums } = entry_lines(
            entry,
            rows,
            x_field,
            domain,
            anchored,
            axis_on_right,
            false,
            color_field,
            series_order,
            &DecimalPad::new(fonts, style)?,
            chart_id,
            entry_index,
        )?;
        // The block's own worst-case anchor width, from the SAME pooled
        // values the shared `numerals` above declared its affix from —
        // production's `band_budget_w` widening loop runs per ENTRY, and
        // a per_series block IS one entry.
        let anchor_worst_case_w =
            anchor_worst_case_cell_width(&nums, &numerals, fonts, font_family, font_size)?;

        if entry.per_series.is_some() {
            if series_ink.len() != series_order.len() {
                return Err(
                    "support_table: internal error — series_ink/series_order length \
                     mismatch"
                        .to_string().into(),
                );
            }
            // dx is shared across the block too — `_compute_support_table_
            // entry_dx` is per ENTRY, fed by the pooled sum-per-(x,series)
            // width measurement (`_aggregate_by_x_and_color`,
            // support_table_attachment.py:2121-2145).
            let mut max_bare_w = 0.0_f64;
            let mut cells_by_series: Vec<Vec<ComposedCell>> = Vec::with_capacity(lines.len());
            for values in &lines {
                let (mut cells, row_bare_w) =
                    build_cells(values, x_positions, &numerals, spec, fonts, font_family, font_size, true)?;
                link_cells(&mut cells, entry, domain, links);
                max_bare_w = max_bare_w.max(row_bare_w);
                cells_by_series.push(cells);
            }
            let dx = if band_centered { max_bare_w / 2.0 } else { 0.0 };
            for (i, cells) in cells_by_series.into_iter().enumerate() {
                // A per_series row is always headed by the series name
                // (a `by_measure` entry reads as a `source` one).
                let label = truncate_label(fonts, label_family, label_size, &series_order[i], true)?;
                let label_w = fonts.measure_shaped(&label, label_family, label_size)?;
                out.push(ComposedRow {
                    label,
                    label_w,
                    cells,
                    dx,
                    label_color: Some(series_ink[i].clone()),
                    stub_dx: 0.0,
                    series: Some(series_order[i].clone()),
                    anchor_worst_case_w,
                    copy_dx: vec![0.0],
                });
            }
            continue;
        }

        let (mut cells, max_bare_w) =
            build_cells(&lines[0], x_positions, &numerals, spec, fonts, font_family, font_size, true)?;
        link_cells(&mut cells, entry, domain, links);
        let label = match entry.label.clone() {
            Some(l) => l,
            None => slug_to_display(
                entry.source.as_deref().expect("entry_values already required a source"),
                "none",
            )?,
        };
        let label = truncate_label(fonts, label_family, label_size, &label, true)?;
        let label_w = fonts.measure_shaped(&label, label_family, label_size)?;
        // A `by_measure` entry reads the folded rows directly, one copy per measure, so
        // production applies no band-centering `dx` to it.
        let dx = if band_centered && !entry.by_measure { max_bare_w / 2.0 } else { 0.0 };
        out.push(ComposedRow {
            label,
            label_w,
            cells,
            dx,
            label_color: None,
            stub_dx: 0.0,
            series: None,
            anchor_worst_case_w,
            copy_dx: vec![0.0; config.cell_copies()],
        });
    }
    Ok(out)
}

/// Width-based sampling step for an ordered (bar band) x axis — port of
/// `validate_support_table_against_data`'s cap-overflow `sampling_step`
/// composed with the width-based override that widens it
/// (`support_table_attachment.py:4335-4356`). `min_band_px` is the caller's
/// own `max_cell_w + 2 * row.padding.horizontal` (`max_cell_w` reads
/// straight off the already-built `ComposedCell::w`s — the same shaped
/// measurement `_entry_cell_widths` performs, so no separate width pass is
/// needed here). The cap-overflow step is folded in by `apply_width_sampling`.
/// Only meaningful for a genuinely
/// ordered x (a detected/authored calendar cadence): callers gate this to
/// that case, mirroring production's own `validator_x_type == "temporal"`
/// guard — an unordered nominal/ordinal domain is never sampled (dropping
/// unordered categories is silent data loss, production's fail-closed
/// rule).
pub fn width_sampling_step(n: usize, axis_x_width: f64, min_band_px: f64) -> usize {
    if n == 0 || axis_x_width <= 0.0 || min_band_px <= 0.0 {
        return 1;
    }
    let fit_cols = (axis_x_width / min_band_px).floor().max(1.0);
    ((n as f64 / fit_cols).ceil() as usize).max(1)
}

/// The typeface a strip cell is measured and painted in.
fn cell_font(style: &SupportTableStyle) -> Result<(&str, f64), String> {
    let family = style.font.family.as_deref().ok_or("support_table: font.family absent")?;
    let size = style.font.size.ok_or("support_table: font.size absent")?;
    Ok((family, size))
}

/// The width one strip cell needs of its band: the widest cell plus the row
/// padding on both sides (`min_band_px`).
fn min_band_px(max_cell_w: f64, row_padding_horizontal: f64) -> f64 {
    max_cell_w + 2.0 * row_padding_horizontal
}

/// `WARN-SUPPORT-TABLE-CELL-OVERFLOW`: the widest strip cell needs more than
/// the width one band is drawn at, so neighboring values run together.
pub fn warn_cell_overflow(
    chart_id: &str,
    max_cell_w: f64,
    row_padding_horizontal: f64,
    axis_x_width: f64,
    n_bands: usize,
    sampling_step: usize,
) {
    let min_band_px = min_band_px(max_cell_w, row_padding_horizontal);
    let drawn_band_px = axis_x_width / n_bands.max(1) as f64 * sampling_step as f64;
    if n_bands > 0 && min_band_px > drawn_band_px {
        crate::diagnostic::warn(crate::diagnostic::Diagnostic::new(
            &crate::codes::WARN_SUPPORT_TABLE_CELL_OVERFLOW,
            [
                ("chart_id", serde_json::json!(chart_id)),
                ("detail", serde_json::json!(format!("{min_band_px:.0}px needed, {drawn_band_px:.0}px per band"))),
            ],
        ));
    }
}

/// Keep every `step`-th cell, starting from the first — port of
/// `_sampling_transforms`'s VL window+filter pair (1-indexed row_number
/// ascending by x, `(row_number - 1) % step == 0`); `cells` is already in
/// that same x-ascending paint order, so this is the same predicate
/// 0-indexed. `step <= 1` is a no-op (every cell kept).
fn sample_cell_mask(n: usize, step: usize) -> Vec<bool> {
    if step <= 1 {
        return vec![true; n];
    }
    (0..n).map(|i| i % step == 0).collect()
}

/// Port of `_label_period_filter_expr`'s opener gate, once the caller has
/// already resolved its own x-axis cadence (`data_tu` vs `visibility_tu` —
/// the SAME axis-label visibility resolution `resolve_vega_x_labels`/
/// `resolve_temporal_labels` already compute for the axis itself):
/// production's own comment is explicit that the filter "matches the
/// opener list used by labelExpr so every visible axis label has exactly
/// one support_table cell" (`support_table_attachment.py:2440`). `None` —
/// no coarsening, keep every cell — when `visibility_tu == data_tu`
/// (production's own `_label_period_filter_expr` returns `None` there too)
/// or a domain entry fails to parse as a date (fails open, matching
/// production's own "unsupported encoding" `None` fallback rather than
/// guessing).
pub fn label_period_keep(
    domain: &[String],
    data_tu: TimeUnit,
    visibility_tu: TimeUnit,
    fiscal_start: u8,
) -> Option<Vec<bool>> {
    if visibility_tu == data_tu {
        return None;
    }
    let mut mask = Vec::with_capacity(domain.len());
    for d in domain {
        let (y, m, day) = crate::families::temporal::parse_iso_date(d)?;
        mask.push(crate::families::temporal::is_opener(y, m, day, data_tu, visibility_tu, fiscal_start));
    }
    Some(mask)
}

/// Applies a keep-mask (from [`label_period_keep`] or [`width_sampling_step`]
/// via [`sample_cell_mask`]) to one composed row's own cells, in place.
pub fn filter_composed_row(row: &mut ComposedRow, keep: &[bool]) {
    let mut i = 0;
    row.cells.retain(|_| {
        let k = keep[i];
        i += 1;
        k
    });
}

/// [`width_sampling_step`] + [`sample_cell_mask`] + [`filter_composed_row`]
/// composed for the common caller shape: every row shares one global step
/// derived from the widest cell any row painted (production's own
/// `max_cell_w = max(_entry_cell_widths(...))` scans every entry, not one
/// row at a time).
pub fn apply_width_sampling(
    chart_id: &str,
    rows: &mut [ComposedRow],
    axis_x_width: f64,
    row_padding_horizontal: f64,
    ordered_x: bool,
) {
    // `c.w` already includes each row's own actual anchor cell's affix
    // (`build_cells` measures the anchor's OWN decorated text), but
    // `anchor_worst_case_w` bounds a wider case production also budgets
    // for: the anchor's affix applied to the row's LARGEST value, wherever
    // that value actually falls (`band_budget_w`'s own widening loop,
    // `support_table_attachment.py:4318-4337`) — folded in here rather
    // than at each row's own construction site so a caller building
    // `ComposedRow`s directly (a test, a future arm) still gets a correct
    // width budget without remembering to add it in twice.
    let max_cell_w = rows
        .iter()
        .flat_map(|r| &r.cells)
        .map(|c| c.w)
        .chain(rows.iter().map(|r| r.anchor_worst_case_w))
        .fold(0.0_f64, f64::max);
    let n = rows.first().map_or(0, |r| r.cells.len());
    // Only an ordered x axis is thinned; any other never drops a category.
    let step = if ordered_x {
        // Production's two-term `max(sampling_step, width_step)`.
        let cap_step = n.div_ceil(SUPPORT_TABLE_MAX_X_TICKS).max(1);
        cap_step.max(width_sampling_step(n, axis_x_width, min_band_px(max_cell_w, row_padding_horizontal)))
    } else {
        1
    };
    warn_cell_overflow(chart_id, max_cell_w, row_padding_horizontal, axis_x_width, n, step);
    if step <= 1 {
        return;
    }
    let keep = sample_cell_mask(n, step);
    for row in rows {
        filter_composed_row(row, &keep);
    }
}

/// Vertical ink extent the strip's topmost row reaches above the plot top,
/// as a positive pixel count — |top row's mark y| plus the text box's rise
/// above its mark anchor (`vega_fit::text_ink`: `round(0.8·fs) −
/// baseline_shift("middle")`, 6px at the themed 11). This is what Vega's
/// autosize-pad bounds clear above the view, on top of the title's own
/// offset.
pub fn composed_ink_extent(style: &SupportTableStyle, n_rows: usize) -> f64 {
    if n_rows == 0 {
        return 0.0;
    }
    let fs = style.font.size_or(11.0);
    let rise = crate::vega_fit::middle_rise(fs);
    -row_y_base(n_rows - 1, style, 0.0) + rise
}

/// How far a bottom strip's axis gap grows to clear x labels whose block is
/// `block_height` tall (`_tilted_label_axis_offset`,
/// `support_table_attachment.py:4549`): the compile-baked gap reserves
/// `label_max_lines` flat lines, and a label rotated toward vertical is as tall
/// as it is long (`AxisLabelLayout.label_block_height`). Hidden labels reserve
/// nothing.
pub(crate) fn tilted_axis_gap_growth(
    style: &SupportTableStyle,
    labels: &crate::common::AxisLabels,
    font_size: f64,
    block_height: f64,
) -> f64 {
    if labels.visible == Some(false) || font_size <= 0.0 {
        return 0.0;
    }
    (block_height - font_size * style.label_max_lines).max(0.0)
}

/// Round-half-to-even, matching Python's `round()` — production's own
/// `_support_table_title_offset_correction` calls bare `round()`, which ties
/// to even, not Rust's `f64::round` (half-away-from-zero). Kept as its own
/// copy, same reasoning as `sizing.rs`'s and `table.rs`'s: each family module
/// keeps a private copy rather than reaching across module boundaries for
/// one three-line helper.
fn python_round(x: f64) -> f64 {
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

/// Closed-form `title.offset` correction for a titled `position: top`
/// support_table strip — ports `support_table_attachment.py::
/// _support_table_title_offset_correction` (`:4425`); production writes
/// `title.offset = existing_offset + correction` once, at spec-build time
/// (`:4951-4962`), and renders once. Under
/// `autosize: pad`, the strip's own negative-y text marks
/// already grow the view-bounds top margin by their own ink
/// (`composed_ink_extent`, added separately at the `plot_top` call site) —
/// `title.offset` never reserves `strip_h` itself. It only needs
/// to cancel Vega's own title-baseline algebra (vega-scenegraph `text.js`'s
/// `offset()`: a "top"-baseline title's text sits `round(0.79 * font_size)`
/// px below its group's nominal top). The `11.0` constant is production's
/// own empirical measurement (not derived from Vega's source), verified
/// independent of strip height, series count and chart type at the three
/// font-size tiers the board renders at (14/18/24).
pub fn support_table_title_offset_correction(title_font_size: f64) -> f64 {
    11.0 - title_font_size + python_round(0.79 * title_font_size)
}

/// Union the composed strip's own text ink into `b`, in plot coordinates —
/// what Vega's autosize-pad bounds read above and beside the plot. The cells
/// anchor on the parent's x per S4, so the edge cell facing the aligned side
/// overhangs the plot by its full shaped width and joins the canvas
/// (`weekly_line`: 1200 marks + 46 cells + 25 stubs = 1271). The vertical
/// reach of the topmost row is what [`composed_ink_extent`] gives callers
/// that compose their margins term by term instead of from bounds.
pub fn composed_ink(
    b: &mut crate::vega_fit::Bounds,
    rows: &[ComposedRow],
    style: &SupportTableStyle,
    ay_label_padding: f64,
    plot_w: f64,
    axis_on_right: bool,
    origin_y: f64,
    axis_offset: f64,
) {
    let font_size = style.font.size_or(11.0);
    let (_, label_size) = label_font_resolved(style);
    let (cell_align, stub_align) =
        if axis_on_right { ("right", "left") } else { ("left", "right") };
    let stub_x = if axis_on_right { plot_w + ay_label_padding } else { -ay_label_padding };
    for (i, row) in rows.iter().enumerate() {
        let y = origin_y + row_y_base(i, style, axis_offset);
        let dx = if axis_on_right { row.dx } else { -row.dx };
        for cell in &row.cells {
            b.union(&crate::vega_fit::text_ink(
                cell.x + dx,
                y,
                cell.w,
                font_size,
                cell_align,
                "middle",
            ));
        }
        b.union(&crate::vega_fit::text_ink(
            stub_x + row.stub_dx,
            y,
            row.label_w,
            label_size,
            stub_align,
            "middle",
        ));
    }
}

/// S8's strip term in the rail-concat arm's own `pane0_x2` union: how far
/// past `pane_w` the composed strip's own cells reach, in plot-local
/// coordinates (0 when every cell stays inside the pane) — an ink AMOUNT,
/// added to `pane_w` the same way the arm's mark-reach/label-overhang terms
/// are. Only the axis-left orientation is reachable here (the rail arm's own
/// gate keeps the value axis left), where cells anchor `start` at
/// `cell.x − row.dx` and grow rightward by their shaped width; the
/// left-overhang half of S6 (an axis-right cell's `end` anchor) is the
/// composed (non-concat) arm's `composed_ink`, not this closed-form one.
pub fn composed_right_overhang(rows: &[ComposedRow], pane_w: f64) -> f64 {
    rows.iter()
        .flat_map(|row| row.cells.iter().map(move |cell| cell.x - row.dx + cell.w - pane_w))
        .fold(0.0_f64, f64::max)
}

/// Emit the composed strip above the plot, in absolute canvas coordinates.
/// `origin` is the plot's top-left corner; rows sit at production's
/// `_row_y_pixel` offsets above it (`row_y_base`), painted at Vega's
/// middle-baseline shift.
///
/// S4: the anchoring mirrors with the value axis's orient, exactly as
/// production's `_row_text_layer`/`_label_stub_layer` do — on an
/// orient-right axis cells align right at `x + dx` and the stub starts at
/// `plot_w + axis_y.labels.padding`; on an orient-left axis cells align
/// left at `x − dx` (production writes `mark["dx"] = -dx`) and the stub
/// ends at `−axis_y.labels.padding`.
#[allow(clippy::too_many_arguments)]
pub fn render_rows_composed(
    out: &mut String,
    rows: &[ComposedRow],
    style: &SupportTableStyle,
    ay_label_padding: f64,
    plot_w: f64,
    origin_x: f64,
    origin_y: f64,
    axis_on_right: bool,
    axis_offset: f64,
    link_open: Option<crate::href::LinkOpen>,
) -> Result<(), String> {
    // The row labels and dividers read no datum: on a linked chart each carries an undefined link.
    let undefined = link_open.and_then(|open| open.anchor(Some(crate::href::UNDEFINED_HREF)));
    let font_family = style.font.family_or("sans-serif");
    let font_size = style.font.size_or(11.0);
    let font_color = style.font.color.as_deref().unwrap_or("#333");
    let font_weight = style.font.weight_str();
    let (label_family, label_size) = label_font_resolved(style);
    let label_color = style.label.font.color.as_deref().unwrap_or(font_color);
    let label_weight = style.label.font.weight_str();
    let baseline = measure::baseline_shift("middle", font_size);

    if style.divider.line.visible {
        let y = origin_y + divider_y_base(style, axis_offset);
        out.push_str(&crate::href::anchor_open(undefined));
        out.push_str(&divider_line(origin_x, y, origin_x + plot_w, y, &style.divider.line));
        out.push_str(crate::href::anchor_close(undefined));
    }
    let (cell_anchor, stub_anchor) =
        if axis_on_right { ("end", "start") } else { ("start", "end") };
    let stub_x = if axis_on_right {
        origin_x + plot_w + ay_label_padding
    } else {
        origin_x - ay_label_padding
    };
    for (i, row) in rows.iter().enumerate() {
        let row_y = origin_y + row_y_base(i, style, axis_offset) + baseline;
        let dx = if axis_on_right { row.dx } else { -row.dx };
        crate::contract::mark_group_open(out, "text", "strip")?;
        for (cell, copy_dx) in row.cells.iter().flat_map(|cell| row.copy_dx.iter().map(move |dx| (cell, dx))) {
            let anchor = link_open.and_then(|open| open.anchor(cell.href.as_deref()));
            out.push_str(&crate::href::anchor_open(anchor));
            let _ = write!(
                out,
                "<text{} x=\"{:.2}\" y=\"{row_y:.2}\" text-anchor=\"{cell_anchor}\" \
                 font-family=\"{}\" font-size=\"{font_size}\" font-weight=\"{font_weight}\" \
                 fill=\"{font_color}\">{}</text>",
                crate::contract::cell_label_attr(&cell.aria)?,
                origin_x + cell.x + dx + copy_dx,
                vega_escape_attr(font_family),
                xml_escape(&cell.text), cell_anchor = xml_escape_attr(cell_anchor), font_weight = xml_escape_attr(&font_weight), font_color = xml_escape_attr(font_color)
            );
            out.push_str(crate::href::anchor_close(anchor));
        }
        crate::contract::mark_group_close(out);
        let row_label_color = row.label_color.as_deref().unwrap_or(label_color);
        out.push_str(&crate::href::anchor_open(undefined));
        let _ = write!(
            out,
            "<text x=\"{:.2}\" y=\"{row_y:.2}\" text-anchor=\"{stub_anchor}\" \
             font-family=\"{}\" font-size=\"{label_size}\" font-weight=\"{label_weight}\" \
             fill=\"{row_label_color}\">{}</text>",
            stub_x + row.stub_dx,
            vega_escape_attr(label_family),
            xml_escape(&row.label), stub_anchor = xml_escape_attr(stub_anchor), label_weight = xml_escape_attr(&label_weight), row_label_color = xml_escape_attr(row_label_color)
        );
        out.push_str(crate::href::anchor_close(undefined));
        if style.row.divider.visible && i + 1 < rows.len() {
            let y = origin_y + inter_row_y_base(i, style, axis_offset);
            out.push_str(&crate::href::anchor_open(undefined));
            out.push_str(&divider_line(origin_x, y, origin_x + plot_w, y, &style.row.divider));
            out.push_str(crate::href::anchor_close(undefined));
        }
    }
    Ok(())
}

/// Data-aware guards this config must pass before any entry is resolved —
/// port of the checks `validate_support_table_against_data` runs at production
/// render time (`support_table_attachment.py:213-322`). Past the tick cap an ordered
/// (temporal or quantitative) x is sampled by the caller (`apply_width_sampling`);
/// any other x type fails closed, as dropping categories would be silent data loss.
pub(crate) fn validate_against_data(
    config: &SupportTableConfig,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    ordered_x: bool,
) -> Result<(), String> {
    if domain.len() > SUPPORT_TABLE_MAX_X_TICKS && !ordered_x {
        return Err(format!(
            "support_table: supports at most {SUPPORT_TABLE_MAX_X_TICKS} x-axis ticks; got {}. \
             Aggregate or filter in the query before attaching a support_table.",
            domain.len()
        ));
    }
    if let Some(first) = rows.first() {
        let available = first;
        for entry in &config.entries {
            // Column presence covers whichever field this entry reads —
            // `source` (aggregate/source entries) or `per_series`
            // (validate_support_table_against_data:296-322 checks
            // `entry.per_series` there the same way).
            let Some(field) = entry.source.as_deref().or(entry.per_series.as_deref()) else {
                continue;
            };
            if !available.contains_key(field) {
                let cols: Vec<&str> = available.keys().map(String::as_str).collect();
                return Err(format!(
                    "support_table: entry references source column {field:?} which is \
                     not in the query output. Available columns: {}.",
                    cols.join(", ")
                ));
            }
        }
    }
    let mut x_counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for row in rows {
        if let Some(x) = row.get(x_field) {
            *x_counts.entry(value_to_key(x)).or_insert(0) += 1;
        }
    }
    let multi_row = x_counts.values().any(|&c| c > 1);
    if multi_row {
        for entry in &config.entries {
            // Aggregate entries collapse multiple rows themselves; a normal
            // per_series entry is exempt too — its own VL transform groups by
            // [x, color] and expects multiple rows per x (the ambiguity
            // guard's per_series carve-out, support_table_attachment.py's
            // `is_normal_per_series` at :319-322).
            if entry.aggregate.is_some() || entry.per_series.is_some() {
                continue;
            }
            // `parse_config` rejects any entry with neither `source`,
            // `aggregate`, nor `per_series`, so a bare entry always has a source.
            let field = entry
                .source
                .as_deref()
                .expect("parse_config guarantees source when aggregate is absent");
            return Err(format!(
                "support_table: entry references column {field:?} which is ambiguous \
                 on this chart — {x_field:?} returns multiple rows per x. Add \
                 'aggregate: <op>' (e.g. 'aggregate: sum, source: {field}') to resolve."
            ));
        }
    }
    Ok(())
}

/// Row-label font resolution shared by `build_rows`'s truncation measurement
/// and `render_rows`'s paint — a single source of truth so the two can never
/// diverge (measuring against one fallback family while painting another
/// would truncate to the wrong pixel width, or worse, measure a family
/// `face_for` can't find and error the whole render).
pub(crate) fn label_font_resolved(style: &SupportTableStyle) -> (&str, f64) {
    let body_family = style.font.family_or("sans-serif");
    let family = style.label.font.family.as_deref().unwrap_or(body_family);
    let size = style.label.font.size_or(style.font.size_or(11.0));
    (family, size)
}

/// Truncate `text` with a trailing ellipsis so it fits `LABEL_STUB_LIMIT_PX`
/// — port of Vega-Lite's `labelLimit` truncation on the row-label stub
/// (`_LABEL_STUB_LIMIT_PX`, `support_table_attachment.py:127`). `shaped`
/// selects the measurer: the composed arm truncates with the same
/// GPOS-shaped widths vl-convert's own labelLimit uses; the legacy splice
/// keeps raw advance sums (its pre-existing approximation).
fn truncate_label(
    fonts: &FontBook,
    family: &str,
    size: f64,
    text: &str,
    shaped: bool,
) -> Result<String, String> {
    let measure = |t: &str| -> Result<f64, String> {
        if shaped { fonts.measure_shaped(t, family, size) } else { fonts.measure(t, family, size) }
    };
    if measure(text)? <= LABEL_STUB_LIMIT_PX {
        return Ok(text.to_string());
    }
    let chars: Vec<char> = text.chars().collect();
    for n in (0..chars.len()).rev() {
        let candidate: String = chars[..n].iter().collect::<String>() + "…";
        if measure(&candidate)? <= LABEL_STUB_LIMIT_PX {
            return Ok(candidate);
        }
    }
    Ok("…".to_string())
}

#[cfg(test)]
fn pad() -> DecimalPad<'static> {
    static FONTS: std::sync::OnceLock<FontBook> = std::sync::OnceLock::new();
    let dir = format!("{}/../../../src/dbt_charts/core/render/fonts", env!("CARGO_MANIFEST_DIR"));
    DecimalPad {
        fonts: FONTS.get_or_init(|| FontBook::load_dir(&dir).expect("test fonts load")),
        family: "'dbt Sans Tabular', Inter, sans-serif",
    }
}

#[cfg(test)]
mod numerals_tests {
    use super::*;

    /// Trimmed currency (`$,.0f`): the anchor cell keeps the `$`, every
    /// other cell has the repeated affix removed from the formatted string
    /// (production's `strip_numerals_for_values` trimmed path). Oracle:
    /// product_bar's Revenue row paints `$75,404  68,400  62,070 …`.
    #[test]
    fn trimmed_currency_declares_the_dollar_once() {
        let vals = [75404.0, 68400.0, 62070.0];
        let n = strip_numerals_for_values(&vals, "$,.0f", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(75404.0, true).unwrap(), "$75,404");
        assert_eq!(n.cell_text(68400.0, false).unwrap(), "68,400");
        assert_eq!(n.cell_text(62070.0, false).unwrap(), "62,070");
    }

    /// Trimmed percent (`.1%`): `%` on the anchor only. Oracle:
    /// weekly_bar_compact_formats' Share row paints `23.2%  22.0  19.5 …`.
    #[test]
    fn trimmed_percent_declares_the_percent_once() {
        let vals = [0.232, 0.22, 0.195];
        let n = strip_numerals_for_values(&vals, ".1%", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(0.232, true).unwrap(), "23.2%");
        assert_eq!(n.cell_text(0.22, false).unwrap(), "22.0");
        assert_eq!(n.cell_text(0.195, false).unwrap(), "19.5");
    }

    /// Composed SI (`$.3~s`): the row divides by one shared magnitude and
    /// rebuilds a fixed-point digit spec; the anchor carries `$` and the
    /// narrative suffix, siblings are bare scaled digits. Oracle:
    /// weekly_bar_compact_formats' Revenue row paints `$102.8k  97.5 …`
    /// (thousands tier; precision 1 from the finest value's 2 integer
    /// digits at sig-figs 3; 102.85's exact double is 102.849… so the cell
    /// rounds DOWN — the committed production golden pins `$102.8k`).
    #[test]
    fn composed_si_divides_by_one_shared_magnitude() {
        let vals = [102850.0, 97469.0, 86180.0, 111122.0, 45402.0];
        let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(102850.0, true).unwrap(), "$102.8k");
        assert_eq!(n.cell_text(97469.0, false).unwrap(), "97.5");
        assert_eq!(n.cell_text(45402.0, false).unwrap(), "45.4");
    }

    /// A magnitude suffix never rides a zero cell ("0k" is meaningless) and
    /// the anchor skips zero cells — the first non-zero cell declares.
    #[test]
    fn composed_zero_cell_is_never_the_anchor_and_never_suffixed() {
        let vals = [0.0, 102850.0, 97469.0];
        let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
        assert!(!n.carries(0.0), "zero cannot carry the unit");
        assert!(n.carries(102850.0), "first non-zero cell declares");
        // The zero cell prints no decimals beside `97.5`, so it takes the
        // whole-number pad: one figure unit and the dot's.
        assert_eq!(n.cell_text(0.0, false).unwrap(), "0\u{2007}\u{200b}\u{2008}\u{200b}");
    }

    /// A set whose extreme stays under six written integer digits does not
    /// compact — the author's spec paints every cell unchanged (production's
    /// `shared_scale_for_column` compaction threshold).
    #[test]
    fn composed_below_compaction_threshold_keeps_the_author_spec() {
        let vals = [75404.0, 68400.0];
        let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(75404.0, true).unwrap(), "$75.4k");
        assert_eq!(
            n.cell_text(68400.0, false).unwrap(),
            "$68.4k",
            "no shared magnitude declared — every cell formats through the author's own spec"
        );
    }

    /// Un-anchored rows (production's `StripAnchor.nowhere()` / plain
    /// numerals: an unpinned sort, quantitative x) repeat the full format on
    /// every cell — the pre-feature output.
    #[test]
    fn unanchored_rows_format_every_cell_in_full() {
        let vals = [75404.0, 68400.0];
        let n = StripNumerals::undeclared(&vals, "$,.0f", &pad()).unwrap();
        assert_eq!(n.cell_text(75404.0, true).unwrap(), "$75,404");
        assert_eq!(n.cell_text(68400.0, false).unwrap(), "$68,400");
    }

    fn affixed(prefix: &str, suffix: &str, repeat: Option<&str>) -> FormatSlot {
        FormatSlot {
            prefix: prefix.into(),
            suffix: suffix.into(),
            repeat: repeat.map(Into::into),
            ..FormatSlot::from(",.0f")
        }
    }

    /// `_authored_affix_numerals`: `repeat: anchor` declares the affix on the
    /// anchor cell alone and the anchor leads its sign with the prefix (`US$`
    /// then `\u{2212}`); `repeat: every`, a literal spec with no `repeat`, and
    /// an unanchored strip paint the full affix on every cell, zero included,
    /// placing the sign by the prefix (`CHF \u{2212}10`).
    #[test]
    fn an_authored_affix_declares_on_the_anchor_for_repeat_anchor_and_on_every_cell_otherwise() {
        let anchor = StripNumerals::authored(&affixed("US$", "", Some("anchor")), true, false, &pad(), "p").unwrap();
        assert_eq!(anchor.cell_text(-40.0, true).unwrap(), "US$\u{2212}40");
        assert_eq!(anchor.cell_text(-40.0, false).unwrap(), "\u{2212}40");
        assert_eq!(anchor.cell_text(0.0, false).unwrap(), "0");
        let trailing = StripNumerals::authored(&affixed("", " CHF", Some("anchor")), true, false, &pad(), "p").unwrap();
        assert_eq!(trailing.cell_text(10.0, true).unwrap(), "10 CHF");
        assert_eq!(trailing.hung_suffix(), " CHF", "a right-aligned anchor's suffix hangs past the edge");
        for numerals in [
            StripNumerals::authored(&affixed("CHF ", "", Some("every")), true, false, &pad(), "p").unwrap(),
            StripNumerals::authored(&affixed("CHF ", "", None), true, false, &pad(), "p").unwrap(),
            StripNumerals::authored(&affixed("CHF ", "", Some("anchor")), false, false, &pad(), "p").unwrap(),
        ] {
            assert_eq!(numerals.cell_text(-10.0, true).unwrap(), "CHF \u{2212}10");
            assert_eq!(numerals.cell_text(-10.0, false).unwrap(), "CHF \u{2212}10");
            assert_eq!(numerals.cell_text(0.0, false).unwrap(), "CHF 0");
            assert_eq!(numerals.hung_suffix(), "");
        }
        // A glued prefix signs before itself on an undeclared row: the sign
        // placement is the slot's own, not the anchor's `after_prefix`.
        let glued = StripNumerals::authored(&affixed("US$", "", Some("every")), true, false, &pad(), "p").unwrap();
        assert_eq!(glued.cell_text(-10.0, false).unwrap(), "\u{2212}US$10");
        assert_eq!(glued.cell_text(-10.0, true).unwrap(), "\u{2212}US$10");
    }

    /// A `repeat` or `sign_placement` on a format with no authored affix
    /// changes nothing (`_authored_affix_numerals` returns `None`): the strip
    /// paints the spec.
    #[test]
    fn a_repeat_or_sign_placement_without_an_authored_affix_paints_the_spec() {
        for slot in [
            FormatSlot { repeat: Some("every".into()), ..FormatSlot::from(",.0f") },
            FormatSlot { sign_placement: Some(SignPlacement::AfterPrefix), ..FormatSlot::from(",.0f") },
        ] {
            let entry = SupportTableEntry {
                source: Some("units".into()),
                aggregate: None,
                format: Some(slot),
                label: None,
                per_series: None,
                by_measure: false,
            };
            let rows: Vec<serde_json::Map<String, Value>> =
                vec![serde_json::json!({"week": "2024-01-01", "units": 1500.0}).as_object().unwrap().clone()];
            let domain = vec!["2024-01-01".to_string()];
            let lines = entry_lines(&entry, &rows, "week", &domain, true, true, false, None, &[], &pad(), "c", 0).unwrap();
            assert_eq!(lines.numerals.expect("a spec paints").cell_text(1500.0, true).unwrap(), "1,500");
        }
    }

    /// A house format's default anchors its affix, but a leading prefix on a
    /// left-aligned strip would push the anchor's digits off the shared edge,
    /// so the prefix repeats on every cell instead (`house_anchors`).
    #[test]
    fn a_house_prefix_on_a_left_axis_repeats_on_every_cell() {
        let house = FormatSlot { raw: Some("number".into()), ..affixed("US$", "", None) };
        assert!(house.is_house());
        let left = StripNumerals::authored(&house, true, true, &pad(), "p").unwrap();
        assert!(!left.declares_once);
        assert_eq!(left.cell_text(5.0, true).unwrap(), "US$5");
        assert_eq!(left.cell_text(6.0, false).unwrap(), "US$6");
        let right = StripNumerals::authored(&house, true, false, &pad(), "p").unwrap();
        assert!(right.declares_once, "a right axis still declares once");
        assert_eq!(right.cell_text(6.0, false).unwrap(), "6");
    }

    #[test]
    fn an_authored_affix_on_a_left_axis_declares_once_and_hangs_nothing() {
        let slot = affixed("", " pts", Some("anchor"));
        let left = StripNumerals::authored(&slot, true, true, &pad(), "p").unwrap();
        assert_eq!(left.hung_suffix(), "", "a left-aligned anchor trails its suffix into its own band");
        assert_eq!(left.cell_text(5.0, true).unwrap(), "5 pts");
        assert_eq!(left.cell_text(6.0, false).unwrap(), "6");
        let slot = affixed("US$", "", Some("anchor"));
        let padded = FormatSlot { spec: ",.2~f".into(), ..slot.clone() };
        let aligned = StripNumerals::authored(&padded, true, false, &pad(), "p").unwrap();
        assert_eq!(aligned.cell_text(1.5, true).unwrap(), "US$1.5\u{2007}\u{200b}", "an authored affix keeps the trim spec's decimal pad");
        // A `$` spec keeps its symbol on the declaring cell and drops it from the rest.
        let symbol = FormatSlot { spec: "$,.0f".into(), ..affixed("", " pts", Some("anchor")) };
        let symbol = StripNumerals::authored(&symbol, true, false, &pad(), "p").unwrap();
        assert_eq!(symbol.cell_text(1500.0, true).unwrap(), "$1,500 pts");
        assert_eq!(symbol.cell_text(900.0, false).unwrap(), "900");
    }

    /// A fill-and-align format is just a spec: the anchor machinery reads its
    /// type and symbol, and the fill paints through d3.
    #[test]
    fn a_padded_format_paints_its_fill_in_every_cell() {
        let n = strip_numerals_for_values(&[100.0, 90.0], "*>10,.0f", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(100.0, true).unwrap(), "*******100");
        assert_eq!(n.cell_text(90.0, false).unwrap(), "********90");
    }

    /// A left-axis anchor is the stripped text plus the suffix
    /// (`bare + suffix`), so a padded spec keeps its fill before the unit.
    #[test]
    fn a_left_axis_padded_percent_anchor_puts_the_suffix_after_the_fill() {
        let n = strip_numerals_for_values(&[0.55, 0.09], "*<8.0%", true, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(0.55, true).unwrap(), "55*****%");
        assert_eq!(n.cell_text(0.09, false).unwrap(), "9******");
        let right = strip_numerals_for_values(&[0.55, 0.09], "*<8.0%", false, false, false, &pad()).unwrap();
        assert_eq!(right.cell_text(0.55, true).unwrap(), "55%*****");
    }

    /// No affix in the spec (`,d`): nothing to declare; identical output on
    /// every cell, anchored or not.
    #[test]
    fn affix_free_specs_are_unchanged() {
        let n = strip_numerals_for_values(&[2693.0, 2280.0], ",d", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(2693.0, true).unwrap(), "2,693");
        assert_eq!(n.cell_text(2280.0, false).unwrap(), "2,280");
    }

    /// A left-oriented value axis hangs the affix on the anchor cell's own
    /// side rather than the gutter (`hang="right"`, paired with
    /// `prefix_repeats` in `_entry_numerals`): a trimmed symbol format cannot
    /// anchor there without leaving the unit off every non-anchor cell, so
    /// every cell keeps its own `$` — `weekly_area_stacked`'s and
    /// `region_bar_grouped`'s reference rows, both left-axis rail concats.
    #[test]
    fn a_left_axis_trimmed_symbol_format_never_anchors() {
        let vals = [102850.0, 97469.0, 86180.0];
        let n = strip_numerals_for_values(&vals, "$,.0f", true, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(102850.0, true).unwrap(), "$102,850");
        assert_eq!(
            n.cell_text(97469.0, false).unwrap(),
            "$97,469",
            "a left-axis currency row keeps its $ on every cell, anchor or not"
        );
    }

    /// A composed (`s`) symbol format on a left axis repeats its `$` on every
    /// cell (`prefix_repeats`) and still declares the magnitude suffix once,
    /// in the anchor cell's own band (`hung_suffix` is empty there).
    #[test]
    fn a_left_axis_symbol_si_format_repeats_the_dollar_and_declares_the_suffix_once() {
        let vals = [102850.0, 97469.0, 86180.0, 111122.0, 45402.0, 0.0];
        let n = strip_numerals_for_values(&vals, "$.3~s", true, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(102850.0, true).unwrap(), "$102.8k");
        assert_eq!(n.cell_text(97469.0, false).unwrap(), "$97.5");
        assert_eq!(n.cell_text(45402.0, false).unwrap(), "$45.4");
        assert_eq!(
            n.cell_text(0.0, false).unwrap(),
            "$0\u{2007}\u{200b}\u{2008}\u{200b}",
            "a zero cell keeps the repeated $, no suffix, and the whole-number pad"
        );
        assert_eq!(n.cell_text(-97469.0, false).unwrap(), "\u{2212}$97.5");
        assert_eq!(n.hung_suffix(), "");
    }

    /// The same row on a right axis still declares both once.
    #[test]
    fn a_right_axis_symbol_si_format_declares_the_dollar_once() {
        let vals = [102850.0, 97469.0, 86180.0, 111122.0, 45402.0];
        let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(102850.0, true).unwrap(), "$102.8k");
        assert_eq!(n.cell_text(97469.0, false).unwrap(), "97.5");
        assert_eq!(n.hung_suffix(), "k");
    }

    /// A column block speaks `SharedScale.register`: the analytic word (glued
    /// to the digits for en-US) while the suffix declares once, hung whole;
    /// a horizontal row is always narrative.
    #[test]
    fn a_vertical_block_declares_its_magnitude_in_the_analytic_register() {
        let vals = [1_200_000.0, 3_400_000.0, 5_600_000.0];
        let n = strip_numerals_for_values(&vals, ".3~s", false, true, false, &pad()).unwrap();
        assert_eq!(n.cell_text(1_200_000.0, true).unwrap(), "1.2M");
        assert_eq!(n.cell_text(3_400_000.0, false).unwrap(), "3.4");
        assert_eq!(n.hung_suffix(), "M");
        let row = strip_numerals_for_values(&vals, ".3~s", false, false, false, &pad()).unwrap();
        assert_eq!(row.cell_text(1_200_000.0, true).unwrap(), "1.2mn");
    }

    /// The declared magnitude is the active locale's word. Oracle:
    /// `strip_numerals_for_values` under `Regional(locale="de-DE")`, whose
    /// analytic words are `k M Mrd Bio` behind a no-break space and whose
    /// decimal separator is a comma.
    #[test]
    fn a_vertical_block_speaks_the_boards_words_and_decimal() {
        let mut wire = crate::number_locale::en_us_wire();
        wire["decimal"] = serde_json::json!(",");
        wire["thousands"] = serde_json::json!(".");
        wire["analytic"] = serde_json::json!(["\u{a0}k", "\u{a0}M", "\u{a0}Mrd", "\u{a0}Bio"]);
        let de: crate::number_locale::NumberLocale = serde_json::from_value(wire).unwrap();
        crate::number_locale::with_number_locale(&de, || {
            let vals = [1_200_000.0, 3_400_000.0, 5_600_000.0];
            let n = strip_numerals_for_values(&vals, ".3~s", false, true, false, &pad()).unwrap();
            assert_eq!(n.cell_text(1_200_000.0, true).unwrap(), "1,2\u{a0}M");
            assert_eq!(n.cell_text(3_400_000.0, false).unwrap(), "3,4");
            assert_eq!(n.hung_suffix(), "\u{a0}M");
        });
    }

    /// de-DE writes its currency after the digits (`["", "\u{a0}€"]`), so the
    /// anchor declares it once, leading, and no other cell repeats it
    /// (`_currency_affixes`).
    #[test]
    fn an_anchored_dollar_strip_leads_the_board_currency_under_a_suffix_currency_locale() {
        let vals = [1_200_000.0, 3_400_000.0, 5_600_000.0];
        crate::number_locale::with_number_locale(&crate::number_locale::de_de(), || {
            let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
            assert!(n.cell_text(1_200_000.0, true).unwrap().starts_with('€'));
            assert!(!n.cell_text(3_400_000.0, false).unwrap().contains('€'));
        });
        let n = strip_numerals_for_values(&vals, "$.3~s", false, false, false, &pad()).unwrap();
        assert!(n.cell_text(1_200_000.0, true).unwrap().starts_with('$'));
    }

    /// A suffix every cell paints (`SuffixMode.REPEAT`: the extreme scales to
    /// four integer digits) is value-attached, so it reads narrative even in
    /// a column.
    #[test]
    fn a_repeating_vertical_suffix_reads_narrative() {
        let vals = [1_500_000.0, 2_500_000.0, 1_200_500_000.0];
        let n = strip_numerals_for_values(&vals, ".3~s", false, true, false, &pad()).unwrap();
        assert_eq!(n.cell_text(1_500_000.0, false).unwrap(), "1.5mn");
        assert_eq!(n.hung_suffix(), "");
    }

    /// An affix-free format has nothing to hang — the refusal is gated on
    /// `parts.symbol`, not `hang_right` alone, so a plain number still
    /// anchors (declares nothing either way, matching production's
    /// `Units` row on both left-axis reference cases).
    #[test]
    fn a_left_axis_affix_free_format_still_anchors() {
        let n = strip_numerals_for_values(&[2693.0, 2280.0], ",d", true, false, false, &pad()).unwrap();
        assert_eq!(n.cell_text(2693.0, true).unwrap(), "2,693");
        assert_eq!(n.cell_text(2280.0, false).unwrap(), "2,280");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A divider payload: a zero width stands for "not visible" here, since the
    /// engine now gates on `visible` and every theme carries a positive width.
    fn divider_json(width: f64) -> serde_json::Value {
        serde_json::json!({
            "visible": width > 0.0,
            "color": "#626366",
            "width": if width > 0.0 { width } else { 1.0 },
            "dasharray": null,
        })
    }

    fn style(padding_top: f64, padding_bottom: f64, row_pad_v: f64, divider_w: f64, rule_w: f64) -> SupportTableStyle {
        style_positioned(padding_top, padding_bottom, row_pad_v, divider_w, rule_w, "top")
    }

    fn style_positioned(
        padding_top: f64,
        padding_bottom: f64,
        row_pad_v: f64,
        divider_w: f64,
        rule_w: f64,
        position: &str,
    ) -> SupportTableStyle {
        serde_json::from_value(serde_json::json!({
            "font": {"family": "sans", "size": 11.0, "color": "#333"},
            "divider": {"gap": 4.0, "visible": divider_w > 0.0, "color": "#626366", "width": if divider_w > 0.0 { divider_w } else { 1.0 }, "dasharray": null},
            "row": {"padding": {"vertical": row_pad_v, "horizontal": 4.0}, "divider": divider_json(rule_w)},
            "label": {"font": {"family": "sans", "size": 11.0, "color": "#333"}},
            "padding_top": padding_top,
            "padding_bottom": padding_bottom,
            "label_max_lines": 2,
            "position": position,
        }))
        .unwrap()
    }

    /// A composed row read the way these tests assert it: `(x, text, aria)` cells.
    #[derive(Debug)]
    struct TestRow {
        label: String,
        cells: Vec<(f64, String, String)>,
        label_color: Option<String>,
        series: Option<String>,
    }

    impl TestRow {
        fn painted(&self) -> Vec<(f64, String)> {
            self.cells.iter().map(|(x, text, _)| (*x, text.clone())).collect()
        }
    }

    /// The composed builder with every cell spelling its own full spec.
    #[allow(clippy::too_many_arguments)]
    fn build_rows(
        config: &SupportTableConfig,
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        domain: &[String],
        x_positions: &[f64],
        fonts: &FontBook,
        style: &SupportTableStyle,
        color_field: Option<&str>,
        series_order: &[String],
        series_ink: &[String],
    ) -> Result<Vec<TestRow>, Diagnostic> {
        build_rows_composed(
            "test", config, rows, x_field, domain, x_positions, fonts, style, false, false, false, color_field,
            series_order, series_ink, None, false)
        .map(|rows| {
            rows.into_iter()
                .map(|r| TestRow {
                    label: r.label,
                    cells: r.cells.into_iter().map(|c| (c.x, c.text, c.aria)).collect(),
                    label_color: r.label_color,
                    series: r.series,
                })
                .collect()
        })
    }

    /// Load the shipped fonts so `build_rows`'s label-truncation measurement
    /// has real font metrics — mirrors `bar.rs`'s `test_fonts()`.
    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    // Real font family present in InterVariable.ttf; "sans-serif" is skipped
    // by `face_for()` (mirrors `bar.rs`'s `TEST_FONT`).
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    /// `build_rows`'s style param needs a full `SupportTableStyle` (not just the
    /// label font) since `label_font_resolved` falls back to the body font.
    fn label_style() -> SupportTableStyle {
        serde_json::from_value(serde_json::json!({
            "font": {"family": TEST_FONT, "size": 11.0, "color": "#333"},
            "divider": {"gap": 4.0, "visible": false, "color": "#626366", "width": 1.0, "dasharray": null},
            "row": {"padding": {"vertical": 2.0, "horizontal": 4.0}, "divider": divider_json(0.0)},
            "label": {"font": {"family": TEST_FONT, "size": 11.0}},
            "padding_top": 8.0,
            "padding_bottom": 4.0,
            "label_max_lines": 2,
            "position": "top",
        }))
        .unwrap()
    }

    /// Pins production's own measured formula: weekly_bar (3 rows, no
    /// divider, no inter-row rule, padding_top=8, padding_bottom=4, row
    /// vertical padding=2) reserves exactly 66px — verified against a real
    /// render (`support_table_attachment.support_table_strip_height`).
    #[test]
    fn strip_height_matches_production_measured_value() {
        let s = style(8.0, 4.0, 2.0, 0.0, 0.0);
        assert_eq!(strip_height(&s, 3), 66.0);
    }

    #[test]
    fn strip_height_includes_divider_and_inter_row_rule() {
        let s = style(8.0, 4.0, 2.0, 1.0, 1.0);
        // row_h = 18; divider = 1 + 4 = 5; inter_row = 1 * (3-1) = 2
        // total = 8 + 5 + 18*3 + 2 + 4 = 73
        assert_eq!(strip_height(&s, 3), 73.0);
    }

    #[test]
    fn the_divider_reserves_its_own_gap_before_the_first_row() {
        let mut s = style(8.0, 4.0, 2.0, 1.0, 0.0);
        s.divider.gap = 9.0;
        // divider = 1 + 9; padding_top 8 + 10 + one 18px row + padding_bottom 4
        assert_eq!(strip_height(&s, 1), 8.0 + 10.0 + 18.0 + 4.0);
    }

    /// `_strip_height`/`_row_y_pixel` gate on `visible`, never on `width`: a
    /// hidden divider keeps its theme width and reserves nothing.
    #[test]
    fn a_hidden_divider_reserves_no_strip_space_whatever_its_width() {
        let mut s = style(8.0, 4.0, 2.0, 3.0, 3.0);
        s.divider.line.visible = false;
        s.row.divider.visible = false;
        // padding_top + 3 rows of 18 + padding_bottom, no divider band, no inter-row.
        assert_eq!(strip_height(&s, 3), 8.0 + 54.0 + 4.0);
        assert_eq!(row_y_base(1, &s, 0.0), -(4.0 + 18.0 + 9.0));
    }

    /// `_divider_mark`: color and width from the divider, `strokeDash` as the
    /// comma-joined pattern Vega serializes; a solid divider adds no dash.
    #[test]
    fn a_strip_line_carries_the_dividers_color_width_and_vega_dash() {
        let dashed: Divider = serde_json::from_value(serde_json::json!(
            {"visible": true, "color": "#cc7700", "width": 2.0, "dasharray": "6 3.5"}
        ))
        .unwrap();
        assert_eq!(
            divider_line(10.0, 20.0, 110.0, 20.0, &dashed),
            "<line x1=\"10.00\" y1=\"20.00\" x2=\"110.00\" y2=\"20.00\" \
             stroke=\"#cc7700\" stroke-width=\"2\" stroke-dasharray=\"6,3.5\"/>"
        );
        let solid = Divider { dasharray: None, ..dashed };
        assert!(!divider_line(0.0, 0.0, 1.0, 0.0, &solid).contains("dasharray"));
    }

    #[test]
    fn a_dasharray_that_is_not_numbers_refuses_by_name() {
        let err = serde_json::from_value::<Divider>(serde_json::json!(
            {"visible": true, "color": "red", "width": 1.0, "dasharray": "dashed"}
        ))
        .unwrap_err()
        .to_string();
        assert!(err.contains("dasharray must be space-joined numbers"), "{err}");
    }

    #[test]
    fn a_divider_missing_visible_refuses_by_name() {
        let err = serde_json::from_value::<Divider>(serde_json::json!(
            {"color": "red", "width": 1.0, "dasharray": null}
        ))
        .unwrap_err()
        .to_string();
        assert!(err.contains("missing field `visible`"), "{err}");
    }

    /// Pinned against the corpus values: `weekly_bar`/`weekly_line` (18px
    /// title) and `weekly_bar_grouped` (a taller per_series strip, also 18px
    /// title) all land on `title.offset` 17 — `existing_offset` (10, the
    /// theme offset) + this function's `correction(18)` — independent of
    /// strip height.
    #[test]
    fn support_table_title_offset_correction_matches_production_probe() {
        assert_eq!(support_table_title_offset_correction(18.0), 7.0);
        assert_eq!(10.0 + support_table_title_offset_correction(18.0), 17.0);
    }

    /// The other two width-tier font sizes the board renders at, so a
    /// change that only happens to work at 18px doesn't silently regress.
    #[test]
    fn support_table_title_offset_correction_tracks_other_font_tiers() {
        assert_eq!(support_table_title_offset_correction(14.0), 8.0);
        assert_eq!(support_table_title_offset_correction(24.0), 6.0);
    }

    #[test]
    fn parse_config_accepts_a_normal_per_series_entry() {
        let v = serde_json::json!({"entries": [{"per_series": "revenue", "format": ",d"}]});
        let config = parse_config(&v, "bar").unwrap();
        assert_eq!(config.entries[0].per_series.as_deref(), Some("revenue"));
    }

    /// A `by_measure` entry is one row reading the column directly, headed by
    /// the raw column name unless it authors a label.
    #[test]
    fn a_by_measure_entry_is_a_source_row_headed_by_the_column() {
        let v = serde_json::json!({"entries": [
            {"per_series": "edge_share", "by_measure": true, "format": ",d"},
            {"per_series": "edge_share", "by_measure": true, "label": "Edge", "format": ",d"},
        ]});
        let config = parse_config(&v, "bar").unwrap();
        assert_eq!(config.entries[0].source.as_deref(), Some("edge_share"));
        assert_eq!(config.entries[0].per_series, None);
        assert_eq!(config.entries[0].label.as_deref(), Some("edge_share"));
        assert_eq!(config.entries[1].label.as_deref(), Some("Edge"));
        assert_eq!(visual_row_count(&config, 0).unwrap(), 2);
    }

    #[test]
    fn visual_row_count_expands_a_per_series_entry_by_series_count() {
        let config = SupportTableConfig {
            entries: vec![
                SupportTableEntry {
                    source: None,
                    aggregate: None,
                    format: Some(",d".into()),
                    label: None,
                    per_series: Some("revenue".into()),
                    by_measure: false,
                },
                SupportTableEntry {
                    source: Some("revenue".into()),
                    aggregate: Some("sum".into()),
                    format: Some(",d".into()),
                    label: Some("Total".into()),
                    per_series: None,
                    by_measure: false,
                },
            ],
            fold: None,
        };
        assert_eq!(visual_row_count(&config, 3).unwrap(), 4, "3 series rows + 1 aggregate row");
    }

    #[test]
    fn visual_row_count_refuses_a_per_series_entry_with_no_series_count() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: None,
                aggregate: None,
                format: Some(",d".into()),
                label: None,
                per_series: Some("revenue".into()),
                by_measure: false,
            }],
            fold: None,
        };
        let err = visual_row_count(&config, 0).unwrap_err();
        assert!(err.contains("series_count"), "names the missing input: {err}");
    }

    #[test]
    fn build_rows_refuses_a_per_series_entry_with_no_color_field() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: None,
                aggregate: None,
                format: Some(",d".into()),
                label: None,
                per_series: Some("revenue".into()),
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": 10.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[])
            .unwrap_err();
        assert!(err.contains("per_series"), "names the unsupported shape: {err}");
        assert!(err.contains("color"), "names the missing input: {err}");
    }

    /// The legacy splice arm expands a `per_series:` entry too — every cell
    /// formats its full spec (no `StripNumerals` declare-once affix on this
    /// path, see the module doc).
    #[test]
    fn build_rows_expands_a_per_series_entry_one_row_per_series() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: None,
                aggregate: None,
                format: Some(",d".into()),
                label: None,
                per_series: Some("revenue".into()),
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "category": "A", "revenue": 100.0})
                .as_object()
                .unwrap()
                .clone(),
            serde_json::json!({"week": "2024-01-01", "category": "B", "revenue": 50.0})
                .as_object()
                .unwrap()
                .clone(),
        ];
        let domain = vec!["2024-01-01".to_string()];
        let series_order = vec!["A".to_string(), "B".to_string()];
        let series_ink = vec!["#111111".to_string(), "#222222".to_string()];
        let out = build_rows(
            &config,
            &rows,
            "week",
            &domain,
            &[10.0],
            &test_fonts(),
            &label_style(),
            Some("category"),
            &series_order,
            &series_ink,
        )
        .unwrap();
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].label, "A");
        assert_eq!(out[0].painted(), vec![(10.0, "100".to_string())]);
        assert_eq!(out[0].label_color.as_deref(), Some("#111111"));
        assert_eq!(out[1].label, "B");
        assert_eq!(out[1].painted(), vec![(10.0, "50".to_string())]);
        assert_eq!(out[1].label_color.as_deref(), Some("#222222"));
    }

    #[test]
    fn build_rows_aggregates_multiple_rows_per_x() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: Some("sum".into()),
                format: Some(FormatSlot { raw: Some("currency_whole".into()), ..FormatSlot::from("$,.0f") }),
                label: Some("Total".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "revenue": 100.0}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-01", "revenue": 50.0}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-08", "revenue": 20.0}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let strip_rows =
            build_rows(&config, &rows, "week", &domain, &[10.0, 20.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        assert_eq!(strip_rows.len(), 1);
        assert_eq!(strip_rows[0].painted(), vec![(10.0, "$150".to_string()), (20.0, "$20".to_string())]);
    }

    #[test]
    fn build_rows_defaults_label_to_sentence_cased_source() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("avg_order_revenue".into()),
                aggregate: None,
                format: Some(",d".into()),
                label: None,
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "avg_order_revenue": 42.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let strip_rows =
            build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        // Sentence-cased ("Avg order revenue") overruns `LABEL_STUB_LIMIT_PX`
        // (80px) at real Inter Variable 11px metrics, so this also exercises
        // `truncate_label` on a realistic corpus label — exact expected
        // string, not just "ends with … and fits" (see the truncation test
        // above for why an exact pin matters).
        assert_eq!(strip_rows[0].label, "Avg order rev…");
    }

    #[test]
    fn build_rows_renders_unformatted_when_format_absent() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("units".into()),
                aggregate: None,
                format: None,
                label: Some("Units".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "units": 150.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let strip_rows =
            build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        assert_eq!(strip_rows[0].painted(), vec![(10.0, "150".to_string())]);
    }

    #[test]
    fn value_to_key_matches_row_str_for_null() {
        // A NULL x-bucket must key identically to `row_str`'s own `""` for
        // `Value::Null` — not `"null"` — or the strip's domain lookup misses
        // it entirely.
        assert_eq!(value_to_key(&Value::Null), "");
        assert_eq!(crate::common::row_str(&serde_json::json!({"x": null}).as_object().unwrap().clone(), "x"), "");
    }

    #[test]
    fn reduce_covers_every_numeric_aggregate_op() {
        let vals = vec![10.0, 20.0, 30.0];
        assert_eq!(reduce("sum", &vals).unwrap(), Some(60.0));
        assert_eq!(reduce("avg", &vals).unwrap(), Some(20.0));
        assert_eq!(reduce("min", &vals).unwrap(), Some(10.0));
        assert_eq!(reduce("max", &vals).unwrap(), Some(30.0));
        assert_eq!(reduce("median", &vals).unwrap(), Some(20.0));
        assert_eq!(reduce("median", &[10.0, 20.0, 30.0, 40.0]).unwrap(), Some(25.0));
    }

    #[test]
    fn reduce_rejects_unsupported_op() {
        let err = reduce("stddev", &[1.0]).unwrap_err();
        assert!(err.contains("stddev"));
    }

    /// Regression: `count`/`count_distinct` must count matching *rows*, not
    /// filter to numeric values first — a text column used to collapse to
    /// an empty numeric vec and paint every cell "-".
    #[test]
    fn count_and_count_distinct_work_on_non_numeric_columns() {
        let config = SupportTableConfig {
            entries: vec![
                SupportTableEntry {
                    source: Some("product".into()),
                    aggregate: Some("count".into()),
                    format: Some(",d".into()),
                    label: Some("Count".into()),
                    per_series: None,
                    by_measure: false,
                },
                SupportTableEntry {
                    source: Some("product".into()),
                    aggregate: Some("count_distinct".into()),
                    format: Some(",d".into()),
                    label: Some("Distinct".into()),
                    per_series: None,
                    by_measure: false,
                },
            ],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "product": "Alpha"}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-01", "product": "Alpha"}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-01", "product": "Beta"}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string()];
        let strip_rows =
            build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        assert_eq!(strip_rows[0].painted(), vec![(10.0, "3".to_string())]);
        assert_eq!(strip_rows[1].painted(), vec![(10.0, "2".to_string())]);
    }

    /// `float(raw)` accepts surrounding whitespace and single underscores
    /// between digits, and a bool is 0 or 1; a column measures a text cell
    /// that parses.
    #[test]
    fn coerce_numeric_reads_what_python_float_reads() {
        for (raw, want) in [("1e20", Some(1e20)), (" 12 ", Some(12.0)), ("1_0", Some(10.0)), ("1__0", None), ("_1", None), ("n/a", None), ("", None)] {
            assert_eq!(coerce_numeric(&Value::String(raw.to_string())), want, "{raw:?}");
        }
        assert_eq!(coerce_numeric(&serde_json::json!(true)), Some(1.0));
    }

    /// A bare number cell paints the way JavaScript spells it, exponent form
    /// past `1e21` and below `1e-6`.
    #[test]
    fn an_unformatted_number_cell_paints_as_javascript_spells_it() {
        assert_eq!(format_cell(Some(&CellValue::Num(2.5e-7)), None).unwrap(), "2.5e-7");
        assert_eq!(format_cell(Some(&CellValue::Num(1.5e20)), None).unwrap(), "150000000000000000000");
    }

    /// Deletion counterfactual: without the fix, `format_cell` swallows a
    /// d3rs format error into `"-"`, indistinguishable from missing data.
    #[test]
    fn format_cell_propagates_format_errors_instead_of_swallowing() {
        let err = format_cell(Some(&CellValue::Num(5.0)), Some("not-a-real-spec")).unwrap_err();
        assert!(err.contains("not-a-real-spec"));
    }

    /// HIGH: a bare `source:` entry with no baked `format` over a text
    /// column paints the strings verbatim, matching production's
    /// `_vl_format_calc` with `format_spec is None` (a string is *valid* and
    /// painted as-is, not "-"). Mutation counterfactual: reverting
    /// `entry_values`'s source branch to `and_then(Value::as_f64)` (the old
    /// numeric-only filter) drops every string value and fails this.
    #[test]
    fn source_entry_without_format_renders_text_column_verbatim() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("product".into()),
                aggregate: None,
                format: None,
                label: Some("Product".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "product": "Alpha"}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-08", "product": "Beta"}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let strip_rows =
            build_rows(&config, &rows, "week", &domain, &[10.0, 20.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        assert_eq!(
            strip_rows[0].painted(),
            vec![(10.0, "Alpha".to_string()), (20.0, "Beta".to_string())],
            "text values painted verbatim, not '-'"
        );
    }

    /// HIGH: a bare `source:` entry with no baked `format` must never coerce
    /// a numeric-*looking* string — production's `isNumber` guard
    /// (`_vl_format_calc`) is a `typeof` test, so `"007"`/`"1.10"`/`"1e3"`
    /// stay exactly as authored, never re-parsed and re-stringified.
    /// Mutation counterfactual: reverting `entry_values`'s bare-source match
    /// back to `coerce_numeric(raw)` (parsing any numeric-looking string)
    /// turns `"007"` into `"7"`/"7"-ish output and fails every assertion.
    #[test]
    fn source_entry_without_format_never_coerces_numeric_looking_strings() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("code".into()),
                aggregate: None,
                format: None,
                label: Some("Code".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "code": "007"}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-08", "code": "1.10"}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-15", "code": "1e3"}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string(), "2024-01-15".to_string()];
        let strip_rows = build_rows(&config, &rows, "week", &domain, &[10.0, 20.0, 30.0], &test_fonts(), &label_style(), None, &[], &[])
            .unwrap();
        assert_eq!(
            strip_rows[0].painted(),
            vec![(10.0, "007".to_string()), (20.0, "1.10".to_string()), (30.0, "1e3".to_string())],
            "numeric-looking strings painted exactly as authored, never re-parsed: {:?}",
            strip_rows[0].painted()
        );
    }

    /// A bare `source:` entry with a baked `format` over a non-number raises
    /// production's code, never a silent text fallback that ignores the format;
    /// a boolean is no number either.
    #[test]
    fn source_entry_with_format_over_non_numeric_value_errors() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: None,
                format: Some(",d".into()),
                label: Some("Revenue".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": "not-a-number"}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert_eq!(err.code, "ERR-SUPPORT-TABLE-FORMAT-NOT-NUMERIC");
        assert_eq!(err.params["source"], serde_json::json!("revenue"));
        assert_eq!(err.params["format_spec"], serde_json::json!(",d"));
        assert_eq!(err.params["value"], serde_json::json!("not-a-number"));

        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": true}).as_object().unwrap().clone()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert_eq!(err.params["value"], serde_json::json!(true));
    }

    /// HIGH: a non-numeric value under a numeric aggregate is a query
    /// authoring error in production (`_aggregate_by_x` lets `float()`
    /// raise) — this must surface as a named `Err`, not silently reduce over
    /// the numeric subset. Mutation counterfactual: reverting the reduce
    /// branch to `filter_map(... .and_then(Value::as_f64))` drops "Alpha"
    /// and returns `Ok` with a sum of only the numeric rows.
    #[test]
    fn aggregate_over_non_numeric_value_errors_instead_of_dropping_it() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: Some("sum".into()),
                format: None,
                label: Some("Total".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "revenue": 100.0}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-01", "revenue": "Alpha"}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert!(err.contains("revenue") && err.contains("sum"), "names the source and op: {err}");
    }

    /// HIGH: a bare `source:` entry is ambiguous when the query returns
    /// multiple rows per x — production raises `ERR-INPUT-INVALID` naming
    /// `aggregate: sum` as the fix; this renderer's own resolved payload
    /// never passed through that check (it lives only in the Vega-Lite
    /// emission pass this renderer replaces), so `build_rows` must check it
    /// itself rather than silently showing whichever row came last.
    #[test]
    fn bare_source_entry_over_multi_row_per_x_errors() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: None,
                format: None,
                label: Some("Revenue".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "category": "A", "revenue": 100.0})
                .as_object()
                .unwrap()
                .clone(),
            serde_json::json!({"week": "2024-01-01", "category": "B", "revenue": 200.0})
                .as_object()
                .unwrap()
                .clone(),
        ];
        let domain = vec!["2024-01-01".to_string()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert!(err.contains("revenue") && err.contains("aggregate"), "names the fix: {err}");
    }

    /// HIGH: an entry referencing a column absent from the query output must
    /// error, not silently paint every cell "-" (production's
    /// `validate_support_table_against_data` source-column-presence check).
    #[test]
    fn entry_source_column_absent_from_rows_errors() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("nonexistent_column".into()),
                aggregate: None,
                format: None,
                label: Some("Missing".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": 100.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let err = build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert!(err.contains("nonexistent_column"), "names the missing column: {err}");
    }

    /// HIGH: an ordinal/nominal x past the tick cap must hard-error
    /// (production's fail-closed cap, `chart_support_table_max_x_ticks`) rather
    /// than silently paint an unbounded number of cells.
    #[test]
    fn x_domain_past_the_tick_cap_errors() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: None,
                format: None,
                label: Some("Revenue".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let n = SUPPORT_TABLE_MAX_X_TICKS + 1;
        let rows: Vec<serde_json::Map<String, Value>> = (0..n)
            .map(|i| serde_json::json!({"week": format!("2024-{i:03}"), "revenue": 1.0}).as_object().unwrap().clone())
            .collect();
        let domain: Vec<String> = (0..n).map(|i| format!("2024-{i:03}")).collect();
        let x_positions: Vec<f64> = (0..n).map(|i| i as f64).collect();
        let err =
            build_rows(&config, &rows, "week", &domain, &x_positions, &test_fonts(), &label_style(), None, &[], &[]).unwrap_err();
        assert!(err.contains(&SUPPORT_TABLE_MAX_X_TICKS.to_string()), "names the cap: {err}");
    }

    /// MEDIUM: the row-label stub caps at `LABEL_STUB_LIMIT_PX`, matching
    /// Vega-Lite's `labelLimit` truncation (`_LABEL_STUB_LIMIT_PX` in
    /// production) — a long authored `label:` must not overrun the gutter.
    #[test]
    fn build_rows_truncates_a_label_past_the_stub_limit() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: None,
                format: None,
                label: Some("A Very Long Row Label That Overruns The Gutter Width".into()),
                per_series: None,
                by_measure: false,
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": 1.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let fonts = test_fonts();
        let strip_rows = build_rows(&config, &rows, "week", &domain, &[10.0], &fonts, &label_style(), None, &[], &[]).unwrap();
        // Exact truncation point (not just "ends with … and fits") — pins
        // where `truncate_label`'s binary-search-from-the-end actually
        // lands against real Inter Variable metrics at 11px, so a change to
        // the truncation algorithm that still "fits" but truncates at a
        // different length is caught.
        assert_eq!(strip_rows[0].label, "A Very Long …");
        assert!(
            fonts.measure(&strip_rows[0].label, TEST_FONT, 11.0).unwrap() <= LABEL_STUB_LIMIT_PX,
            "fits the stub limit: {:?}",
            strip_rows[0].label
        );
    }

    fn tilt_labels(visible: bool) -> crate::common::AxisLabels {
        serde_json::from_value(serde_json::json!({
            "font": {"family": "Inter", "size": 11.0, "weight": "400"},
            "visible": visible,
        }))
        .expect("axis labels parse")
    }

    /// The baked gap reserves `label_max_lines` flat rows; only the label block
    /// beyond them grows it, hidden labels reserve nothing, flat labels fit.
    #[test]
    fn the_axis_gap_grows_by_the_tilted_block_beyond_the_reserved_rows() {
        let style = style_positioned(8.0, 4.0, 2.0, 0.0, 0.0, "bottom");
        let reserved = 11.0 * style.label_max_lines;
        let block = |w: f64, angle: f64| crate::vega_fit::label_block_height(w, 11.0, angle);
        let growth = tilted_axis_gap_growth(&style, &tilt_labels(true), 11.0, block(60.0, 45.0));
        assert!((growth - (block(60.0, 45.0) - reserved)).abs() < 1e-9, "{growth}");
        assert_eq!(tilted_axis_gap_growth(&style, &tilt_labels(true), 11.0, block(5.0, 45.0)), 0.0, "a short label fits the rows");
        assert_eq!(tilted_axis_gap_growth(&style, &tilt_labels(true), 11.0, block(60.0, 0.0)), 0.0, "flat labels fit");
        assert_eq!(tilted_axis_gap_growth(&style, &tilt_labels(false), 11.0, block(60.0, 45.0)), 0.0, "hidden labels reserve nothing");
    }

    /// S4: the composed strip's anchoring follows the value axis's orient —
    /// production emits `align: right` cells at `band_center + dx` with the
    /// stub past `plot_w + axis_y.labels.padding` on an orient-RIGHT axis,
    /// and mirrors both on an orient-LEFT one (`mark["dx"] = -dx`, stub at
    /// `-padding`). Probed on production's own specs: `weekly_bar` (right)
    /// vs `weekly_area_stacked`/`region_bar_grouped` (left).
    #[test]
    fn composed_cells_and_stubs_follow_the_axis_orient() {
        let s = style(8.0, 4.0, 2.0, 0.0, 0.0);
        let rows = vec![ComposedRow {
            label: "Revenue".into(),
            label_w: 40.0,
            cells: vec![ComposedCell { x: 50.0, text: "$1".into(), w: 12.0, hang: 0.0, aria: String::new(), href: None }],
            dx: 7.0,
            label_color: None,
            stub_dx: 0.0,
            series: None,
            anchor_worst_case_w: 0.0,
            copy_dx: vec![0.0],
        }];
        let attr = |svg: &str, tag: &str, name: &str| -> String {
            let idx = svg.find(&format!(">{tag}<")).unwrap();
            let start = svg[..idx].rfind("<text").unwrap();
            svg[start..idx].split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string()
        };

        let mut right = String::new();
        render_rows_composed(&mut right, &rows, &s, 6.0, 100.0, 200.0, 300.0, true, 0.0, None).unwrap();
        assert_eq!(attr(&right, "$1", "text-anchor"), "end");
        assert_eq!(attr(&right, "$1", "x"), "257.00", "cell right-aligned at center + dx");
        assert_eq!(attr(&right, "Revenue", "text-anchor"), "start");
        assert_eq!(attr(&right, "Revenue", "x"), "306.00", "stub past plot_w + padding");

        let mut left = String::new();
        render_rows_composed(&mut left, &rows, &s, 6.0, 100.0, 200.0, 300.0, false, 0.0, None).unwrap();
        assert_eq!(attr(&left, "$1", "text-anchor"), "start");
        assert_eq!(attr(&left, "$1", "x"), "243.00", "cell left-aligned at center - dx");
        assert_eq!(attr(&left, "Revenue", "text-anchor"), "end");
        assert_eq!(attr(&left, "Revenue", "x"), "194.00", "stub left of the plot by padding");
    }

    fn per_series_config(label: Option<&str>) -> SupportTableConfig {
        SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: None,
                aggregate: None,
                format: Some(",d".into()),
                label: label.map(str::to_string),
                per_series: Some("revenue".into()),
                by_measure: false,
            }],
            fold: None,
        }
    }

    fn per_series_rows() -> Vec<serde_json::Map<String, Value>> {
        vec![
            serde_json::json!({"week": "2024-01-01", "category": "A", "revenue": 100.0})
                .as_object()
                .unwrap()
                .clone(),
            serde_json::json!({"week": "2024-01-01", "category": "B", "revenue": 50.0})
                .as_object()
                .unwrap()
                .clone(),
            serde_json::json!({"week": "2024-01-08", "category": "A", "revenue": 20.0})
                .as_object()
                .unwrap()
                .clone(),
            // No row for B at 2024-01-08 — that cell must render "-".
        ]
    }

    /// One row per series in `series_order`, each cell the SUM of
    /// `per_series` grouped by (x, color), skipping a null (x, series) pair
    /// — `_per_series_row_layers`, support_table_attachment.py:1387-1426.
    /// The row label is always the series name — `entry.label` (authored
    /// here as "Ignored") is never read on this path.
    #[test]
    fn build_rows_composed_expands_a_per_series_entry_one_row_per_series() {
        let config = per_series_config(Some("Ignored"));
        let rows = per_series_rows();
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let series_order = vec!["A".to_string(), "B".to_string()];
        let series_ink = vec!["#111111".to_string(), "#222222".to_string()];
        let out = build_rows_composed("c",
            &config,
            &rows,
            "week",
            &domain,
            &[10.0, 20.0],
            &test_fonts(),
            &label_style(),
            true,
            true,
            false,
            Some("category"),
            &series_order,
            &series_ink, None, false)
        .unwrap();
        assert_eq!(out.len(), 2, "one row per series, not one row per entry");
        assert_eq!(out[0].label, "A");
        assert_eq!(out[0].label_color.as_deref(), Some("#111111"));
        assert_eq!(
            out[0].cells.iter().map(|c| c.text.clone()).collect::<Vec<_>>(),
            vec!["100".to_string(), "20".to_string()]
        );
        assert_eq!(out[1].label, "B");
        assert_eq!(out[1].label_color.as_deref(), Some("#222222"));
        assert_eq!(
            out[1].cells.iter().map(|c| c.text.clone()).collect::<Vec<_>>(),
            vec!["50".to_string(), "-".to_string()],
            "B has no row at 2024-01-08 — that cell dashes rather than reading 0"
        );
    }

    /// dx is shared across the WHOLE per_series block — `_compute_support_
    /// table_entry_dx` is per ENTRY, fed by the pooled sum-per-(x,series)
    /// width measurement (`_aggregate_by_x_and_color`,
    /// support_table_attachment.py:2121-2145) — not recomputed per row off
    /// only that row's own cells.
    #[test]
    fn build_rows_composed_per_series_dx_is_pooled_across_every_row_in_the_block() {
        let config = per_series_config(None);
        let rows = per_series_rows();
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let series_order = vec!["A".to_string(), "B".to_string()];
        let series_ink = vec!["#111111".to_string(), "#222222".to_string()];
        let fonts = test_fonts();
        let out = build_rows_composed("c",
            &config,
            &rows,
            "week",
            &domain,
            &[10.0, 20.0],
            &fonts,
            &label_style(),
            true,
            true,
            false,
            Some("category"),
            &series_order,
            &series_ink, None, false)
        .unwrap();
        let expected_max_bare = ["100", "20", "50"]
            .iter()
            .map(|t| fonts.measure(t, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        assert_eq!(out[0].dx, expected_max_bare / 2.0);
        assert_eq!(out[1].dx, out[0].dx, "the block shares one dx, not one per row");
    }

    /// Rules between every adjacent visual row, `n_series - 1` inside the
    /// per_series block plus one trailing rule to the next entry — the same
    /// every-adjacent-pair cadence source/aggregate rows already take
    /// (support_table_attachment.py:1950-1975), free once per_series rows
    /// are ordinary rows in the same list.
    #[test]
    fn per_series_block_gets_a_rule_between_every_adjacent_row_plus_trailing() {
        let config = SupportTableConfig {
            entries: vec![
                SupportTableEntry {
                    source: None,
                    aggregate: None,
                    format: Some(",d".into()),
                    label: None,
                    per_series: Some("revenue".into()),
                    by_measure: false,
                },
                SupportTableEntry {
                    source: Some("revenue".into()),
                    aggregate: Some("sum".into()),
                    format: Some(",d".into()),
                    label: Some("Total".into()),
                    per_series: None,
                    by_measure: false,
                },
            ],
            fold: None,
        };
        let mut rows = per_series_rows();
        rows.push(
            serde_json::json!({"week": "2024-01-01", "category": "C", "revenue": 5.0})
                .as_object()
                .unwrap()
                .clone(),
        );
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let series_order = vec!["A".to_string(), "B".to_string(), "C".to_string()];
        let series_ink = vec!["#111".to_string(), "#222".to_string(), "#333".to_string()];
        let s = style(8.0, 4.0, 2.0, 0.0, 1.0);
        let out = build_rows_composed("c",
            &config,
            &rows,
            "week",
            &domain,
            &[10.0, 20.0],
            &test_fonts(),
            &s,
            true,
            true,
            false,
            Some("category"),
            &series_order,
            &series_ink, None, false)
        .unwrap();
        assert_eq!(out.len(), 4, "3 series rows + 1 aggregate row");
        let mut svg = String::new();
        render_rows_composed(&mut svg, &out, &s, 6.0, 100.0, 0.0, 0.0, false, 0.0, None).unwrap();
        assert_eq!(
            svg.matches("<line").count(),
            3,
            "n_series-1 (2) within-block rules + 1 trailing rule to the aggregate row"
        );
    }

    #[test]
    fn build_rows_composed_refuses_a_per_series_entry_with_no_color_field() {
        let config = per_series_config(None);
        let rows = per_series_rows();
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let err = build_rows_composed("c",
            &config,
            &rows,
            "week",
            &domain,
            &[10.0, 20.0],
            &test_fonts(),
            &label_style(),
            true,
            true,
            false,
            None,
            &[],
            &[], None, false)
        .unwrap_err();
        assert!(err.contains("color"), "names the missing input: {err}");
    }

    #[test]
    fn build_rows_composed_refuses_a_per_series_entry_with_empty_series_order() {
        let config = per_series_config(None);
        let rows = per_series_rows();
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let err = build_rows_composed("c",
            &config,
            &rows,
            "week",
            &domain,
            &[10.0, 20.0],
            &test_fonts(),
            &label_style(),
            true,
            true,
            false,
            Some("category"),
            &[],
            &[], None, false)
        .unwrap_err();
        assert!(err.contains("series_order"), "names the missing input: {err}");
    }

    fn house_entry(name: &str, spec: &str) -> SupportTableEntry {
        SupportTableEntry {
            source: Some("share".into()),
            aggregate: None,
            format: Some(FormatSlot { raw: Some(name.into()), ..FormatSlot::from(spec) }),
            label: Some("Share".into()),
            per_series: None,
            by_measure: false,
        }
    }

    fn share_rows(values: &[f64]) -> (Vec<serde_json::Map<String, Value>>, Vec<String>) {
        let domain: Vec<String> = (0..values.len()).map(|i| format!("Q{i}")).collect();
        let rows = domain
            .iter()
            .zip(values)
            .map(|(q, v)| serde_json::json!({"q": q, "share": v}).as_object().unwrap().clone())
            .collect();
        (rows, domain)
    }

    fn build_share_row(
        entry: SupportTableEntry,
        values: &[f64],
        axis_on_right: bool,
    ) -> Vec<ComposedRow> {
        let (rows, domain) = share_rows(values);
        let xs: Vec<f64> = (0..values.len()).map(|i| 100.0 * i as f64).collect();
        build_rows_composed("c",
            &SupportTableConfig { entries: vec![entry], fold: None },
            &rows,
            "q",
            &domain,
            &xs,
            &test_fonts(),
            &label_style(),
            true,
            true,
            axis_on_right,
            None,
            &[],
            &[], None, false)
        .unwrap()
    }

    /// A right-aligned row paints its trailing affix on the anchor only, so the
    /// anchor hangs one suffix-width past the edge every bare cell's digits
    /// sit on (`_hung_anchor_layers`, support_table_attachment.py:1197-1226).
    #[test]
    fn the_anchors_trailing_suffix_hangs_past_the_digit_edge() {
        let out = build_share_row(house_entry("percent_whole", ".0%"), &[0.03, 0.05, 0.07], true);
        let cells = &out[0].cells;
        assert_eq!(cells[0].text, "3%");
        let suffix_w = test_fonts().measure("%", TEST_FONT, 11.0).unwrap();
        assert_eq!(cells[0].x, 0.0 + suffix_w, "the anchor shifts by the measured suffix");
        assert_eq!(cells[1].x, 100.0, "a bare cell stays on its band");
        assert_eq!(cells[2].x, 200.0);
    }

    /// A left-aligned (left-axis) row trails the affix into the anchor's own
    /// band: nothing hangs (`StripNumerals.hung_suffix`, `hang != "left"`).
    #[test]
    fn a_left_axis_row_hangs_nothing() {
        let out = build_share_row(house_entry("percent_whole", ".0%"), &[0.03, 0.05], false);
        assert_eq!(out[0].cells[0].x, 0.0);
    }

    /// A prefix leads the digits into the gutter and a REPEAT magnitude row
    /// paints its suffix on every cell: neither has a lone trailing affix.
    #[test]
    fn a_prefix_only_or_repeating_row_hangs_nothing() {
        let out = build_share_row(house_entry("currency_whole", "$,.0f"), &[3.0, 5.0], true);
        assert_eq!(out[0].cells[0].x, 0.0, "prefix only");
        let out = build_share_row(
            house_entry("currency", "$.3~s"),
            &[2_000_000.0, 3_000_000.0, 4_000_000.0, 1_200_000_000.0],
            true,
        );
        assert!(out[0].cells[1].text.ends_with("mn"), "the row repeats: {:?}", out[0].cells);
        assert!(
            out[0].cells.iter().all(|c| c.x % 100.0 == 0.0),
            "a repeating magnitude suffix rides every cell: {:?}",
            out[0].cells
        );
    }

    /// An unanchored row (a d3 literal spec keeps per-cell units) has no
    /// anchor to hang.
    #[test]
    fn a_row_without_an_anchor_hangs_nothing() {
        let entry = SupportTableEntry {
            format: Some(".0%".into()),
            ..house_entry("percent_whole", ".0%")
        };
        assert_eq!(build_share_row(entry, &[0.03, 0.05], true)[0].cells[0].x, 0.0);
    }

    /// The composed magnitude row's anchor carries its "mn" the same way.
    #[test]
    fn a_magnitude_anchor_hangs_its_suffix() {
        let out = build_share_row(
            house_entry("currency", "$.3~s"),
            &[1_100_000.0, 2_200_000.0, 3_300_000.0],
            true,
        );
        assert_eq!(out[0].cells[0].text, "$1.1mn");
        let suffix_w = test_fonts().measure("mn", TEST_FONT, 11.0).unwrap();
        assert_eq!(out[0].cells[0].x, suffix_w);
        assert_eq!(out[0].cells[1].x, 100.0);
    }
}
