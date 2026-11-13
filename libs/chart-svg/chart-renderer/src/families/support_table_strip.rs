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
//! * **Composed** (`build_rows_composed`/`composed_rows_nodes`/`composed_ink`,
//!   driven by bar.rs's and line.rs's composed arms): production's
//!   `autosize: pad` shape — the plot keeps the payload view verbatim, the
//!   strip floats above it, and the canvas grows around the ink, the strip's
//!   own cells and stubs included. Carries bare numerals (`StripNumerals`,
//!   below: digits alone, the unit in the row label) and, off a band scale
//!   only, the centering cell dx. Cells and stubs anchor from the value axis's orient.
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

use diagnostic::codes;
use ::bind::temporal::TimeUnit;
use diagnostic::Diagnostic;
use crate::common::{slug_to_display, Divider, FormatSlot, GappedDivider, Font};
use ::bind::rows::{row_str};
use ::bind::formats::locale::active_number_locale;
use measure::FontBook;
use scene::{Attrs, Node, Role};

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
    /// The column a `percent:` entry reads: a `source` entry whose values
    /// print as a share of the first stage's.
    percent: Option<String>,
    #[serde(default)]
    ink: Option<String>,
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
    /// A funnel's `percent:` entry: `source` names the column, and each stage
    /// prints as a share of the first stage's value.
    pub percent: bool,
    /// The header's ink when it names the layered series its column reads
    /// (resolve bakes it with the legend hidden); `None` keeps the style's label ink.
    pub ink: Option<String>,
}

impl From<RawSupportTableEntry> for SupportTableEntry {
    fn from(raw: RawSupportTableEntry) -> Self {
        if let Some(column) = raw.percent {
            return SupportTableEntry {
                source: Some(column),
                aggregate: None,
                format: raw.format,
                label: raw.label,
                per_series: None,
                by_measure: false,
                percent: true,
                ink: raw.ink,
            };
        }
        match raw.per_series {
            Some(column) if raw.by_measure => SupportTableEntry {
                label: Some(raw.label.unwrap_or_else(|| column.clone())),
                source: Some(column),
                aggregate: raw.aggregate,
                format: raw.format,
                per_series: None,
                by_measure: true,
                percent: false,
                ink: raw.ink,
            },
            per_series => SupportTableEntry {
                source: raw.source,
                aggregate: raw.aggregate,
                format: raw.format,
                label: raw.label,
                per_series,
                by_measure: false,
                percent: false,
                ink: raw.ink,
            },
        }
    }
}

#[derive(Deserialize, Debug, Clone)]
pub struct SupportTableConfig {
    pub entries: Vec<SupportTableEntry>,
    #[serde(skip)]
    fold: Option<StripFold>,
}

/// A wide chart's strip reads the rows as queried, one per x, and paints each
/// cell once per measure: the fold hands the strip's text layer one datum per
/// measure per row, each carrying the same cell value.
#[derive(Debug, Clone)]
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
    /// A funnel's ribbon rows: one step quieter than the stage rows.
    pub ribbon_color: String,
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
pub(crate) fn divider_line(x1: f64, y1: f64, x2: f64, y2: f64, divider: &Divider) -> Node {
    let line = crate::contract::rule(Role::Chrome, (x1, y1), (x2, y2), &divider.color, divider.width);
    match divider.vega_dash() {
        Some(dash) => Node { attrs: line.attrs.set("stroke-dasharray", dash), ..line },
        None => line,
    }
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

/// A JSON number as-is, a bool as 0 or 1. A string is text whatever it reads
/// like, so `None`: the caller can tell "no value" (skip) from "value that
/// failed to coerce" (error).
fn coerce_numeric(v: &Value) -> Option<f64> {
    match v {
        Value::Number(_) => v.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
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
        let values: Vec<Option<CellValue>> = domain.iter().map(|x| by_x.get(x).cloned()).collect();
        if entry.percent {
            return share_of_first(values, field);
        }
        Ok(values)
    }
}

/// A `percent:` column: each stage's value over the first stage's.
fn share_of_first(values: Vec<Option<CellValue>>, field: &str) -> Result<Vec<Option<CellValue>>, Diagnostic> {
    let first = match values.first() {
        Some(Some(CellValue::Num(n))) if n.is_finite() && *n != 0.0 => *n,
        _ => {
            return Err(format!("support_table: percent: {field:?} needs a nonzero number in the first stage").into());
        }
    };
    Ok(values
        .into_iter()
        .map(|v| match v {
            Some(CellValue::Num(n)) => Some(CellValue::Num(n / first)),
            other => other,
        })
        .collect())
}

/// The change from each stage to the next, one value per pair, for the ribbon rows of
/// a funnel's table: in the column's own units, or, on a `percent:` column, as a share
/// of the stage it leaves. A pair with a missing or non-numeric stage has none.
fn ribbon_changes(entry: &SupportTableEntry, stages: &[Option<CellValue>]) -> Result<Vec<Option<CellValue>>, Diagnostic> {
    stages
        .windows(2)
        .map(|pair| match (&pair[0], &pair[1]) {
            (Some(CellValue::Num(from)), Some(CellValue::Num(to))) if from.is_finite() && to.is_finite() => {
                if !entry.percent {
                    return Ok(Some(CellValue::Num(to - from)));
                }
                if *from == 0.0 {
                    return Err(format!(
                        "support_table: percent: a stage of {:?} follows a stage of 0, so its change has no share",
                        entry.source.as_deref().unwrap_or_default()
                    )
                    .into());
                }
                Ok(Some(CellValue::Num((to - from) / from)))
            }
            _ => Ok(None),
        })
        .collect()
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

// ── Bare numerals (unit notation rule 3) ─────────────────────────────────

/// How one strip row spells its numbers: digits alone, whatever the format, with the unit its
/// cells leave out kept in `unit` for the row label to name (`Pipeline, $K`). An `s`-type (SI)
/// format is COMPOSED: the row divides by one shared magnitude and formats through a rebuilt
/// fixed-point spec. A row that cannot share a tier (values a tier apart, or a sliver that would
/// print as zero) prints its values in full, unrounded digits and names no tier, so no cell ever
/// carries a magnitude letter the label does not name.
pub struct StripNumerals {
    pub(crate) digit_spec: String,
    /// The locale's percent sign, which a `%`-type spec's cells drop.
    percent: String,
    /// What a non-negative composed cell leads with when the spec asks for a sign
    /// (`+` or a space); a negative one leads with the locale's minus instead.
    plus: String,
    divisor: f64,
    /// Each cell prints its value's shortest exact digits (`digit_spec`'s `~f` takes the
    /// value's own decimals): a row that names no tier must not round.
    exact: bool,
    /// The pieces of the unit, in reading order (`$`, `K`), so a label that names some of them is
    /// given only the rest.
    unit: Vec<String>,
}

impl StripNumerals {
    /// The numerals of a row of `values` formatted by `resolved`.
    pub(crate) fn for_values(values: &[f64], resolved: &str) -> Result<StripNumerals, String> {
        let parts = d3rs::format::parse_parts(resolved).map_err(|e| format!("support_table: format {resolved:?}: {e}"))?;
        let currency = if parts.symbol { vec![currency_lead()] } else { Vec::new() };
        let spec = if parts.symbol { d3rs::format::without_symbol(resolved)? } else { resolved.to_string() };
        if parts.type_ != 's' {
            let percent = if parts.type_ == '%' { active_number_locale().locale.percent.clone() } else { String::new() };
            let unit = currency.into_iter().chain((!percent.is_empty()).then(|| percent.clone())).collect();
            return Ok(StripNumerals { digit_spec: spec, percent, plus: String::new(), divisor: 1.0, exact: false, unit });
        }
        let sign = if parts.sign == '-' { String::new() } else { parts.sign.to_string() };
        // Unrounded: under a label that names no tier, a rounded `951,000` would read as exact.
        let full_digits = || StripNumerals {
            digit_spec: format!("{sign},~f"),
            percent: String::new(),
            plus: String::new(),
            divisor: 1.0,
            exact: true,
            unit: currency.clone(),
        };
        let voters: Vec<f64> = values.iter().copied().filter(|v| *v != 0.0 && v.is_finite()).collect();
        let extreme = voters.iter().fold(0.0_f64, |a, v| a.max(v.abs()));
        // The tier most values sit in; a tie that includes values below every tier goes to the largest value's.
        let mut tally: std::collections::HashMap<Option<i32>, usize> = std::collections::HashMap::new();
        for v in &voters {
            *tally.entry(natural_tier(*v)).or_insert(0) += 1;
        }
        let top = tally.values().copied().max().unwrap_or(0);
        let tied: Vec<Option<i32>> = tally.iter().filter(|(_, &count)| count == top).map(|(tier, _)| *tier).collect();
        let tier = if tied.contains(&None) { natural_tier(extreme) } else { tied.iter().filter_map(|t| *t).max() };
        let Some(majority) = tier else { return Ok(full_digits()) };
        let magnitude = 10f64.powi(majority);
        let finest = voters.iter().copied().min_by(|a, b| a.abs().partial_cmp(&b.abs()).expect("voters are finite")).expect("a tier needs a voter");
        // Values more than one tier below the shared one would inflate every sibling's precision.
        let tiers_ascending = [3, 6, 9, 12];
        let tier_index = |tier: i32| tiers_ascending.iter().position(|&t| t == tier).expect("a tier") as i32;
        match natural_tier(finest) {
            Some(own) if tier_index(majority) - tier_index(own) <= 1 => {}
            _ => return Ok(full_digits()),
        }
        let decimals = (parts.precision.unwrap_or(6) as i32 - decimal_exponent(finest.abs() / magnitude)).max(0);
        let digit_spec = format!(",.{decimals}~f");
        // A voter whose scaled form prints no non-zero digit would be indistinguishable from a genuine zero.
        for v in &voters {
            let printed = ::bind::formats::locale::d3_format(&digit_spec, v / magnitude)
                .map_err(|e| format!("support_table: format {digit_spec:?}: {e}"))?;
            if !printed.chars().any(|c| c.is_ascii_digit() && c != '0') {
                return Ok(full_digits());
            }
        }
        let plus = if matches!(parts.sign, '+' | ' ') { parts.sign.to_string() } else { String::new() };
        let unit = currency.into_iter().chain([crate::common::suffix_at_register(majority, true)?]).collect();
        Ok(StripNumerals { digit_spec, percent: String::new(), plus, divisor: magnitude, exact: false, unit })
    }

    /// The unit around `self`'s own: an authored `prefix` leads it and a `suffix` trails it.
    pub(crate) fn wrap_unit(&mut self, prefix: &str, suffix: &str) {
        self.unit.insert(0, prefix.trim().to_string());
        self.unit.push(suffix.trim().to_string());
    }

    /// What `label` is missing of this row's unit, as the `, K` its row label is given
    /// (`Total $` is given `, K`): empty when the label names the whole unit.
    pub(crate) fn unit_suffix(&self, label: &str) -> String {
        let words: Vec<&str> = label.split(|c: char| !c.is_alphanumeric()).collect();
        let names = |piece: &str| {
            let piece = piece.trim();
            if piece.chars().all(char::is_alphanumeric) {
                words.contains(&piece)
            } else {
                label.contains(piece)
            }
        };
        // A code and a tier read apart (`EUR K`); a symbol hugs what follows (`$K`).
        let missing = self.unit.iter().filter(|piece| !piece.is_empty() && !names(piece)).fold(String::new(), |mut joined, piece| {
            if joined.ends_with(char::is_alphabetic) && piece.starts_with(char::is_alphabetic) {
                joined.push(' ');
            }
            joined.push_str(piece);
            joined
        });
        if missing.is_empty() {
            missing
        } else {
            format!(", {missing}")
        }
    }

    /// `label` with its [`Self::unit_suffix`].
    pub(crate) fn labeled(&self, label: &str) -> String {
        format!("{label}{}", self.unit_suffix(label))
    }

    /// The text one cell paints: digits alone.
    pub fn cell_text(&self, v: f64) -> Result<String, String> {
        let wrong = |e: String| format!("support_table: format {:?}: {e}", self.digit_spec);
        let text = if self.exact {
            // Rust's `Display` for `f64` is the shortest round-trip form, never exponent notation.
            let decimals = v.abs().to_string().split_once('.').map_or(0, |(_, fraction)| fraction.len());
            let spec = self.digit_spec.replacen("~f", &format!(".{decimals}f"), 1);
            ::bind::formats::locale::d3_format(&spec, v).map_err(wrong)?
        } else if self.divisor != 1.0 {
            let digits = ::bind::formats::locale::d3_format(&self.digit_spec, v.abs() / self.divisor).map_err(wrong)?;
            let locale = active_number_locale();
            format!("{}{digits}", if v < 0.0 { locale.locale.minus.as_str() } else { self.plus.as_str() })
        } else {
            ::bind::formats::locale::d3_format(&self.digit_spec, v).map_err(wrong)?
        };
        Ok(if self.percent.is_empty() { text } else { text.replacen(self.percent.as_str(), "", 1) })
    }
}

/// SI tiers a shared scale may land on, highest first (`_TIERS`).
const SI_TIERS: [i32; 4] = [12, 9, 6, 3];

fn natural_tier(v: f64) -> Option<i32> {
    let magnitude = v.abs();
    SI_TIERS.iter().copied().find(|&t| magnitude >= 10f64.powi(t))
}

/// Digits before the decimal point, negative-capable below 1.0
/// (`_decimal_exponent`).
fn decimal_exponent(v: f64) -> i32 {
    if v == 0.0 {
        return 1;
    }
    v.abs().log10().floor() as i32 + 1
}

/// The currency symbol a row's unit names: the board's own.
fn currency_lead() -> String {
    let locale = active_number_locale();
    let (lead, trail) = locale.composed_currency("$", false);
    if lead.is_empty() && trail.is_empty() {
        locale.locale.currency[0].clone()
    } else {
        lead
    }
}

pub(crate) fn format_cell(v: Option<&CellValue>, spec: Option<&str>) -> Result<String, String> {
    match (v, spec) {
        (None, _) => Ok("-".to_string()),
        (Some(CellValue::Num(v)), _) if !v.is_finite() => Ok("-".to_string()),
        // No baked format (production only bakes one when entry.source ==
        // y_field) — render the plain number, matching production.
        (Some(CellValue::Num(v)), None) => Ok(d3rs::js_math::js_number_string(*v)),
        (Some(CellValue::Num(v)), Some(spec)) => {
            ::bind::formats::locale::d3_format(spec, *v).map_err(|e| format!("support_table: format {spec:?}: {e}"))
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

/// One composed-layout cell, its text painted ending at `x + row.dx`
/// (`text-anchor: end`, on every axis side): `x` is the cell's center on the
/// parent chart's own x encoding.
/// `w` is the shaped width Vega's autosize-pad bounds union reads (S6 — a cell
/// past the plot's edge overhangs it by that width).
#[derive(Debug, Clone, serde::Serialize)]
pub struct ComposedCell {
    pub x: f64,
    pub text: String,
    pub w: f64,
    /// How far right of its column's x the cell centers to align as the column's
    /// axis label does (`Column::center_dx`), 0 under a centered label.
    pub align_dx: f64,
    /// Production's description of the cell, set by `describe`; empty until then.
    pub aria: String,
    /// The chart's link around the cell, when it has one (`link_cells`).
    pub href: Option<String>,
}

/// One composed-layout row. Each cell's digits end `dx` right of its center
/// (the strip's one cell width, `CellWidth::digits_edge`, shared by every row).
/// The row label is a stub in the value axis's own gutter,
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

/// Where a bucket's column stands: at the x of the bucket's axis label, aligned as
/// that label is. A cell sits in its label's cell: centered under a centered label,
/// beginning at a label flushed to the plot's start, ending at one flushed to its end.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Column {
    pub x: f64,
    pub align: Align,
}

/// An axis label's alignment on its x, in Vega's `align` vocabulary.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Align {
    Left,
    Center,
    Right,
}

impl Align {
    /// The alignment a label's `text-anchor` paints.
    pub fn of_text_anchor(anchor: &str) -> Align {
        match anchor {
            "start" => Align::Left,
            "end" => Align::Right,
            _ => Align::Center,
        }
    }
}

impl Column {
    pub fn centered(x: f64) -> Column {
        Column { x, align: Align::Center }
    }

    /// How far right of the column's x a cell `cell_w` wide centers.
    fn center_dx(self, cell_w: f64) -> f64 {
        match self.align {
            Align::Left => cell_w / 2.0,
            Align::Center => 0.0,
            Align::Right => -cell_w / 2.0,
        }
    }
}

/// Cells + their width (dx input) for one row's own values —
/// factored out of `build_rows_composed` so the per_series branch can build
/// N rows off the same logic a normal entry uses for one. Every cell ends at
/// its `x`; a column block's `x` is the column's right edge.
pub(crate) fn build_cells(
    values: &[Option<CellValue>],
    x_positions: &[f64],
    numerals: &Option<StripNumerals>,
    spec: Option<&str>,
    fonts: &FontBook,
    font_family: &str,
    font_size: f64,
) -> Result<(Vec<ComposedCell>, f64), String> {
    let mut width = 0.0_f64;
    let mut cells = Vec::with_capacity(values.len());
    for (v, &x) in values.iter().zip(x_positions.iter()) {
        let text = match (v, numerals) {
            (Some(CellValue::Num(num)), Some(n)) if num.is_finite() => n.cell_text(*num)?,
            _ => format_cell(v.as_ref(), spec)?,
        };
        if matches!(v, Some(CellValue::Num(_))) {
            width = width.max(fonts.measure(&text, font_family, font_size)?);
        }
        let w = fonts.measure_shaped(&text, font_family, font_size)?;
        cells.push(ComposedCell { x, text, w, align_dx: 0.0, aria: String::new(), href: None });
    }
    Ok((cells, width))
}

/// One entry's cells per visual line — a strip row or a column block column —
/// and the bare numerals its whole block shares. `lines` holds one
/// value list per series for a normal `per_series:` entry and exactly one
/// otherwise; `nums` pools every line's numbers, the input production's
/// per-ENTRY `entry_values`/`_entry_numerals` see (`support_table_attachment.py`
/// `_entry_values`/`_entry_numerals`), never one decision per line.
pub(crate) struct EntryLines<'a> {
    pub(crate) spec: Option<&'a str>,
    pub(crate) lines: Vec<Vec<Option<CellValue>>>,
    pub(crate) numerals: Option<StripNumerals>,
    pub(crate) nums: Vec<f64>,
    /// The change between consecutive stages of a funnel's table, empty unless
    /// the caller asked for ribbon rows and the entry reads a plain column.
    pub(crate) ribbons: Vec<Option<CellValue>>,
}

impl EntryLines<'_> {
    /// `label` with the unit its cells leave out appended: see [`StripNumerals::labeled`].
    pub(crate) fn labeled(&self, label: &str) -> String {
        self.numerals.as_ref().map_or_else(|| label.to_string(), |n| n.labeled(label))
    }

    /// The row label as the strip paints it: the name cut to `LABEL_STUB_LIMIT_PX`, then the unit
    /// it is missing, which is never cut (`Accessories…, $K`).
    pub(crate) fn stub(&self, fonts: &FontBook, family: &str, size: f64, name: &str) -> Result<String, String> {
        let kept = truncate_label(fonts, family, size, name, true)?;
        let suffix = self.numerals.as_ref().map_or_else(String::new, |n| n.unit_suffix(&kept));
        Ok(format!("{kept}{suffix}"))
    }
}

/// Resolve one entry against the query rows. A `per_series:` entry needs the
/// chart's color field and series list, refused by name otherwise (production's
/// own guard, `attach_support_table`, :1910-1921).
#[allow(clippy::too_many_arguments)]
pub(crate) fn entry_lines<'a>(
    entry: &'a SupportTableEntry,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    color_field: Option<&str>,
    series_order: &[String],
    ribbon_rows: bool,
) -> Result<EntryLines<'a>, Diagnostic> {
    let spec = entry.format.as_ref().map(|f| f.spec.as_str());
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
    let ribbons = if ribbon_rows && entry.aggregate.is_none() && entry.per_series.is_none() {
        ribbon_changes(entry, &lines[0])?
    } else {
        Vec::new()
    };
    let nums: Vec<f64> = lines
        .iter()
        .flatten()
        .chain(ribbons.iter())
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
    let numerals = spec
        .map(|spec| {
            let mut numerals = StripNumerals::for_values(&nums, spec)?;
            if let Some(slot) = &entry.format {
                numerals.wrap_unit(&slot.prefix, &slot.suffix);
            }
            Ok::<_, String>(numerals)
        })
        .transpose()?;
    Ok(EntryLines { spec, lines, numerals, nums, ribbons })
}

/// Build the composed strip's rows: bare numerals per entry, the strip's one
/// cell width measured with its own font (the tabular font production's
/// `numeric=True` measurer picks) over every value of every row, and the stub
/// label truncated at `LABEL_STUB_LIMIT_PX`.
///
/// Each cell sits in its column's label cell (`columns`, one per bucket of
/// `domain`), band or point alike, and its digits end at one edge whichever side
/// the value axis is on. This departs
/// from production, which centers a lane on a bar's band only and ends a line's
/// digits on the point.
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
    config: &SupportTableConfig,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    columns: &[Column],
    fonts: &FontBook,
    style: &SupportTableStyle,
    color_field: Option<&str>,
    series_order: &[String],
    series_ink: &[String],
    links: Option<&HashMap<(String, String), String>>,
    ordered_x: bool,
) -> Result<Vec<ComposedRow>, Diagnostic> {
    if domain.len() != columns.len() {
        return Err("support_table: domain/columns length mismatch".into());
    }
    let x_positions: Vec<f64> = columns.iter().map(|column| column.x).collect();
    let rows = config.source_rows(rows);
    validate_against_data(config, rows, x_field, domain, ordered_x)?;
    let (font_family, font_size) = cell_font(style)?;
    let (label_family, label_size) = label_font_resolved(style);

    let resolved = config
        .entries
        .iter()
        .map(|entry| {
            entry_lines(
                entry,
                rows,
                x_field,
                domain,
                color_field,
                series_order,
                false,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut out: Vec<ComposedRow> = Vec::new();
    let mut width = 0.0_f64;
    for (entry, resolved) in config.entries.iter().zip(resolved) {
        let series_labels = series_order.iter().map(|name| resolved.stub(fonts, label_family, label_size, name)).collect::<Result<Vec<_>, _>>()?;
        let row_label = if entry.per_series.is_some() { String::new() } else { resolved.stub(fonts, label_family, label_size, &entry_label(entry)?)? };
        let EntryLines { spec, lines, numerals, .. } = resolved;
        if entry.per_series.is_some() {
            if series_ink.len() != series_order.len() {
                return Err(
                    "support_table: internal error — series_ink/series_order length \
                     mismatch"
                        .to_string().into(),
                );
            }
            // Every series row's values join the strip's one cell width, like any
            // other row's.
            let mut cells_by_series: Vec<Vec<ComposedCell>> = Vec::with_capacity(lines.len());
            for values in &lines {
                let (mut cells, row_width) =
                    build_cells(values, &x_positions, &numerals, spec, fonts, font_family, font_size)?;
                link_cells(&mut cells, entry, domain, links);
                width = width.max(row_width);
                cells_by_series.push(cells);
            }
            for (i, cells) in cells_by_series.into_iter().enumerate() {
                // A per_series row is always headed by the series name
                // (a `by_measure` entry reads as a `source` one).
                let label = series_labels[i].clone();
                let label_w = fonts.measure_shaped(&label, label_family, label_size)?;
                out.push(ComposedRow {
                    label,
                    label_w,
                    cells,
                    dx: 0.0,
                    label_color: Some(series_ink[i].clone()),
                    stub_dx: 0.0,
                    series: Some(series_order[i].clone()),
                    copy_dx: vec![0.0],
                });
            }
            continue;
        }

        let (mut cells, entry_width) =
            build_cells(&lines[0], &x_positions, &numerals, spec, fonts, font_family, font_size)?;
        width = width.max(entry_width);
        link_cells(&mut cells, entry, domain, links);
        let label = row_label;
        let label_w = fonts.measure_shaped(&label, label_family, label_size)?;
        out.push(ComposedRow {
            label,
            label_w,
            cells,
            dx: 0.0,
            label_color: entry.ink.clone(),
            stub_dx: 0.0,
            series: None,
            copy_dx: vec![0.0; config.cell_copies()],
        });
    }
    // One cell width for the strip, so every row's digits share one edge.
    for row in &mut out {
        row.dx = width / 2.0;
        for (cell, column) in row.cells.iter_mut().zip(columns) {
            cell.align_dx = column.center_dx(width);
            cell.x += cell.align_dx;
        }
    }
    Ok(out)
}

/// What a source or aggregate entry's row label reads: its `label`, else its
/// source column spelled for display.
fn entry_label(entry: &SupportTableEntry) -> Result<String, String> {
    match entry.label.clone() {
        Some(label) => Ok(label),
        None => slug_to_display(entry.source.as_deref().expect("entry_values already required a source"), "none"),
    }
}

/// The widest row label the strip would paint, unit included, as `build_rows_composed`
/// truncates and measures it: a chart that parks the labels in another
/// column sizes that column before any row is built.
#[allow(clippy::too_many_arguments)]
pub fn widest_stub(
    config: &SupportTableConfig,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
    color_field: Option<&str>,
    series_order: &[String],
    fonts: &FontBook,
    style: &SupportTableStyle,
) -> Result<f64, Diagnostic> {
    let (family, size) = label_font_resolved(style);
    let rows = config.source_rows(rows);
    let mut widest = 0.0_f64;
    for entry in &config.entries {
        let lines = entry_lines(entry, rows, x_field, domain, color_field, series_order, false)?;
        let labels = if entry.per_series.is_some() {
            series_order.iter().map(|name| lines.stub(fonts, family, size, name)).collect::<Result<Vec<_>, _>>()?
        } else {
            vec![lines.stub(fonts, family, size, &entry_label(entry)?)?]
        };
        for label in labels {
            widest = widest.max(fonts.measure_shaped(&label, family, size)?);
        }
    }
    Ok(widest)
}

/// The typeface a strip cell is measured and painted in.
fn cell_font(style: &SupportTableStyle) -> Result<(&str, f64), String> {
    let family = style.font.family.as_deref().ok_or("support_table: font.family absent")?;
    let size = style.font.size.ok_or("support_table: font.size absent")?;
    Ok((family, size))
}

/// The strip's fit verdict, its formats already compact (settled at resolve).
/// An ordered (date) axis keeps every row for the caller's [`skip_strip`] to thin
/// to the axis labels. An unordered (categorical) one keeps them when every cell
/// clears the next by the row padding ([`narrowest_gap`]), else hides whole: a
/// number is never dropped from some of its buckets, never rotated, never
/// clipped. `None` means hidden, and the caller reserves no height or ink.
pub fn fit_strip(chart_id: &str, rows: Vec<ComposedRow>, row_padding_horizontal: f64, ordered_x: bool) -> Option<Vec<ComposedRow>> {
    if ordered_x {
        return Some(rows);
    }
    let gap = narrowest_gap(&rows, &columns_kept(rows.first().map_or(0, |row| row.cells.len()), None));
    if gap >= row_padding_horizontal {
        return Some(rows);
    }
    warn_cell_overflow(chart_id, row_padding_horizontal, gap);
    None
}

/// The buckets of an `n`-bucket strip that get a column: the first and, when the
/// axis thinned its labels (`labeled`), the buckets it labels; else all of them.
fn columns_kept(n: usize, labeled: Option<&[bool]>) -> Vec<usize> {
    (0..n).filter(|&i| i == 0 || labeled.is_none_or(|l| l[i])).collect()
}

/// The narrowest space between two neighboring `kept` cells (bucket indices,
/// ascending) of any of `rows`, as painted: a cell ends at its aligned edge and
/// reaches back by its own width. Infinite when fewer than two cells are kept.
fn narrowest_gap<'a>(rows: impl IntoIterator<Item = &'a ComposedRow>, kept: &[usize]) -> f64 {
    let edges = |row: &ComposedRow, i: usize| {
        let cell = &row.cells[i];
        let right = cell.x + row.dx;
        (right - cell.w, right)
    };
    rows.into_iter()
        .flat_map(|row| kept.windows(2).map(move |pair| edges(row, pair[1]).0 - edges(row, pair[0]).1))
        .fold(f64::INFINITY, f64::min)
}

/// `WARN-SUPPORT-TABLE-CELL-OVERFLOW`: the cells a strip paints run together
/// even after it skipped what it could.
fn warn_cell_overflow(chart_id: &str, clearance: f64, gap: f64) {
    diagnostic::warn(Diagnostic::new(
        &codes::WARN_SUPPORT_TABLE_CELL_OVERFLOW,
        [
            ("chart_id", serde_json::json!(chart_id)),
            ("detail", serde_json::json!(format!("{clearance:.0}px needed between cells, {gap:.0}px available"))),
        ],
    ));
}

/// The buckets that keep a column on an axis whose painted labels name periods:
/// the first bucket of each label's period, given each bucket's label (`labels`,
/// the index of the painted label whose period holds it). `None` when no bucket
/// has a label to follow, so every bucket keeps its column.
pub fn first_bucket_of_each_label(labels: &[Option<usize>]) -> Option<Vec<bool>> {
    labels.iter().any(Option::is_some).then(|| {
        labels.iter().enumerate().map(|(i, label)| label.is_some() && (i == 0 || labels[i - 1] != *label)).collect()
    })
}

/// The buckets an axis labels once its labels thinned from the data grain to
/// `visibility_tu` (the openers it names), `None` while it labels every bucket.
/// `dates` are the buckets' dates, as the chart's time axis read them.
pub fn label_period_keep(dates: &[Option<(i32, u8, u8)>], data_tu: TimeUnit, visibility_tu: TimeUnit, fiscal_start: u8) -> Option<Vec<bool>> {
    (visibility_tu != data_tu).then(|| {
        dates
            .iter()
            .map(|date| date.is_some_and(|(y, m, d)| crate::families::temporal::is_opener(y, m, d, data_tu, visibility_tu, fiscal_start)))
            .collect()
    })
}

/// Reduces an ordered (date) strip to the columns the axis labels, skipping
/// some of those until the cells it paints clear each other, the way axis
/// labels skip on a date ramp.
///
/// The candidates are the first bucket and the buckets `labeled` marks (the ones
/// the axis labels), or all of them when it is `None`. The stride is the
/// smallest whole step through the candidates at which every row's kept
/// neighbors clear each other ([`narrowest_gap`]), step 1 being all of them;
/// every row keeps the same buckets, so a column stays a column. A stride never
/// keeps fewer than the first and last candidate, and `WARN-SUPPORT-TABLE-CELL-OVERFLOW` fires
/// only if those still collide. Each row's first carrying kept cell declares
/// the unit ([`thinned`]), and its wider text is part of the clearance.
pub fn skip_strip(chart_id: &str, rows: &mut Vec<ComposedRow>, labeled: Option<&[bool]>, row_padding_horizontal: f64) {
    let (kept, gap) = thinned_strip(rows, labeled, row_padding_horizontal);
    if gap < row_padding_horizontal {
        warn_cell_overflow(chart_id, row_padding_horizontal, gap);
    }
    *rows = kept;
}

/// [`skip_strip`]'s verdict without its side effects: the rows it keeps and the
/// narrowest gap between their kept cells. A fit that reads the strip's ink
/// calls this before it measures, so it sizes what the strip will paint.
pub fn thinned_strip(rows: &[ComposedRow], labeled: Option<&[bool]>, row_padding_horizontal: f64) -> (Vec<ComposedRow>, f64) {
    let clearance = row_padding_horizontal;
    let candidates = columns_kept(rows.first().map_or(0, |row| row.cells.len()), labeled);
    let widest_step = candidates.len().saturating_sub(1).max(1);
    let thinned_at = |step: usize| thinned(rows, &candidates.iter().copied().step_by(step).collect::<Vec<_>>());
    let gap_of = |kept: &[ComposedRow]| narrowest_gap(kept.iter(), &(0..kept.first().map_or(0, |r| r.cells.len())).collect::<Vec<_>>());
    let step = (1..widest_step).find(|&step| gap_of(&thinned_at(step)) >= clearance).unwrap_or(widest_step);
    let kept = thinned_at(step);
    let gap = gap_of(&kept);
    (kept, gap)
}

/// Whether `rows`, fitted at the pane the reserved gutter left, stay where the layout
/// put them: within `reserved_left` px left of the plot, with no bucket past its right
/// edge (a hung suffix is not a bucket). Skipping drops cells, but a bucket can still land outside the
/// plot (a date past an authored x domain) and a first cell can sit nearer the plot's
/// edge on a narrower pane: that strip hides with a warning. A lane's overhang past the
/// edge is not a bucket past it.
pub fn gutter_holds(chart_id: &str, rows: &[ComposedRow], reserved_left: f64, plot_w: f64) -> bool {
    let left = composed_left_overhang(rows);
    let past_plot = rows.iter().flat_map(|row| &row.cells).any(|cell| cell.x - cell.align_dx > plot_w + 0.01);
    let detail = if left > reserved_left + 0.01 {
        format!("the first cell reaches {left:.0}px left of the plot, the gutter holds {reserved_left:.0}px")
    } else if past_plot {
        "a cell lands past the plot's right edge".to_string()
    } else {
        return true;
    };
    diagnostic::warn(Diagnostic::new(
        &codes::WARN_SUPPORT_TABLE_CELL_OVERFLOW,
        [("chart_id", serde_json::json!(chart_id)), ("detail", serde_json::json!(detail))],
    ));
    false
}

/// The farthest left of the plot any of `rows`' cells can reach if no column sits
/// nearer the plot's edge than `first_x`: each cell right-aligned at its widest
/// with its column there. A cell aligned on its column's start reaches nothing past
/// it; a cell ending at its column reaches a whole cell past it only when the first
/// column does too (an authored right alignment), since a column flushed to the
/// plot's end never comes near its start. A scale's first bucket sits nearer the
/// plot's edge on a narrower pane, so a gutter sized to this holds the strip at
/// every pane.
pub fn composed_left_bound(rows: &[ComposedRow], first_x: f64) -> f64 {
    rows.iter()
        .filter_map(|row| {
            let floor = row.cells.first()?.align_dx.min(0.0);
            Some(row.cells.iter().map(move |cell| cell.w - row.dx - cell.align_dx.max(floor) - first_x).fold(0.0_f64, f64::max))
        })
        .fold(0.0_f64, f64::max)
}

/// Moves row labels on the left of the plot `reach` px out from under cells that
/// reach past its left edge: right-aligned cells grow leftward, so the first
/// bucket's cell can run where the labels end.
pub fn clear_left_stubs(rows: &mut [ComposedRow], reach: f64) {
    rows.iter_mut().for_each(|row| row.stub_dx -= reach);
}

/// Moves row labels on the right of the plot out from under the last column: cells end at
/// `x + dx`, so a column centered on the plot's end reaches into the gutter where the labels begin.
/// Every label moves together, so the stubs stay one column.
pub fn clear_right_stubs(rows: &mut [ComposedRow], plot_w: f64) {
    let reach = composed_right_overhang(rows, plot_w);
    rows.iter_mut().for_each(|row| row.stub_dx += reach);
}

/// `rows` reduced to the `kept` buckets.
fn thinned(rows: &[ComposedRow], kept: &[usize]) -> Vec<ComposedRow> {
    rows.iter()
        .map(|row| {
            let mut row = row.clone();
            row.cells = kept.iter().map(|&i| row.cells[i].clone()).collect();
            row
        })
        .collect()
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
    stubs_on_right: bool,
    origin_y: f64,
    axis_offset: f64,
) {
    let font_size = style.font.size_or(11.0);
    let (_, label_size) = label_font_resolved(style);
    let stub_align = if stubs_on_right { "left" } else { "right" };
    let stub_x = if stubs_on_right { plot_w + ay_label_padding } else { -ay_label_padding };
    for (i, row) in rows.iter().enumerate() {
        let y = origin_y + row_y_base(i, style, axis_offset);
        for cell in &row.cells {
            b.union(&crate::vega_fit::text_ink(cell.x + row.dx, y, cell.w, font_size, "right", "middle"));
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
/// are. Cells end at `cell.x + row.dx` and grow leftward, so only a column
/// centered near the plot's end reaches past it.
pub fn composed_right_overhang(rows: &[ComposedRow], pane_w: f64) -> f64 {
    rows.iter()
        .flat_map(|row| row.cells.iter().map(move |cell| cell.x + row.dx - pane_w))
        .fold(0.0_f64, f64::max)
}

/// How far the strip's cells reach left of the plot's origin: the widest cell's
/// width past its right edge (`cell.x + row.dx`), 0 when every cell stays
/// inside. The value axis's gutter holds it.
pub fn composed_left_overhang(rows: &[ComposedRow]) -> f64 {
    rows.iter()
        .flat_map(|row| row.cells.iter().map(move |cell| cell.w - (cell.x + row.dx)))
        .fold(0.0_f64, f64::max)
}

/// Emit the composed strip above the plot, in absolute canvas coordinates.
/// `origin` is the plot's top-left corner; rows sit at production's
/// `_row_y_pixel` offsets above it (`row_y_base`), painted at Vega's
/// middle-baseline shift.
///
/// S4: every cell ends at `x + dx` whichever side the value axis is on; the
/// stubs are what follow `stubs_on_right`: they start at
/// `plot_w + axis_y.labels.padding` on the right and end at
/// `−axis_y.labels.padding` on the left.
#[allow(clippy::too_many_arguments)]
pub fn composed_rows_nodes(
    rows: &[ComposedRow],
    style: &SupportTableStyle,
    ay_label_padding: f64,
    plot_w: f64,
    origin_x: f64,
    origin_y: f64,
    stubs_on_right: bool,
    axis_offset: f64,
    link_open: Option<crate::href::LinkOpen>,
) -> Result<Vec<Node>, String> {
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

    let mut out = Vec::new();
    if style.divider.line.visible {
        let y = origin_y + divider_y_base(style, axis_offset);
        out.push(crate::href::linked(undefined, divider_line(origin_x, y, origin_x + plot_w, y, &style.divider.line)));
    }
    let stub_anchor = if stubs_on_right { "start" } else { "end" };
    let stub_x = if stubs_on_right {
        origin_x + plot_w + ay_label_padding
    } else {
        origin_x - ay_label_padding
    };
    for (i, row) in rows.iter().enumerate() {
        let row_y = origin_y + row_y_base(i, style, axis_offset) + baseline;
        let mut cells = Vec::new();
        for (cell, copy_dx) in row.cells.iter().flat_map(|cell| row.copy_dx.iter().map(move |dx| (cell, dx))) {
            let anchor = link_open.and_then(|open| open.anchor(cell.href.as_deref()));
            let text = Node::text(
                Role::Label,
                (origin_x + cell.x + row.dx + copy_dx, row_y),
                crate::contract::cell_label_attrs(&cell.aria)?
                    .set("text-anchor", "end")
                    .vega("font-family", font_family)
                    .num("font-size", font_size).quick_some("font-style", style.font.style.as_deref())
                    .set("font-weight", font_weight.clone())
                    .set("fill", font_color),
                cell.text.as_str(),
            );
            cells.push(crate::href::linked(anchor, text));
        }
        out.push(::layout::contract::mark_group("text", "strip", cells)?);
        let row_label_color = row.label_color.as_deref().unwrap_or(label_color);
        let stub = Node::text(Role::Label, (stub_x + row.stub_dx, row_y), Attrs::new().set("text-anchor", stub_anchor).vega("font-family", label_family).num("font-size", label_size).quick_some("font-style", style.label.font.style.as_deref()).set("font-weight", label_weight.clone()).set("fill", row_label_color), row.label.as_str());
        out.push(crate::href::linked(undefined, stub));
        if style.row.divider.visible && i + 1 < rows.len() {
            let y = origin_y + inter_row_y_base(i, style, axis_offset);
            out.push(crate::href::linked(undefined, divider_line(origin_x, y, origin_x + plot_w, y, &style.row.divider)));
        }
    }
    Ok(out)
}

/// Data-aware guards this config must pass before any entry is resolved —
/// port of the checks `validate_support_table_against_data` runs at production
/// render time (`support_table_attachment.py:213-322`). Past the tick cap an
/// unordered x fails closed; an ordered (temporal or quantitative) x paints
/// every cell and leaves density to `skip_strip`.
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

/// The painted boxes of a rendered chart's legend and its strip, read back
/// from the SVG: every rect and text under `role-legend`, and every text
/// under the strip's mark group, measured with the shipped fonts.
#[cfg(test)]
pub(crate) fn legend_and_strip_boxes(svg: &str, fonts: &FontBook) -> (crate::vega_fit::Bounds, crate::vega_fit::Bounds) {
    use crate::vega_fit::Bounds;
    fn offset(node: roxmltree::Node) -> (f64, f64) {
        node.ancestors().filter_map(|a| a.attribute("transform")).filter_map(|t| t.strip_prefix("translate(")).fold((0.0, 0.0), |(dx, dy), t| {
            let mut xy = t.trim_end_matches(')').split(',').map(|v| v.trim().parse::<f64>().expect("numeric translate"));
            (dx + xy.next().unwrap(), dy + xy.next().unwrap_or(0.0))
        })
    }
    let doc = roxmltree::Document::parse(svg).expect("rendered SVG parses");
    let painted = |class: &str| {
        let mut b = Bounds::empty_new();
        for group in doc.descendants().filter(|n| n.attribute("class") == Some(class)) {
            for n in group.descendants().filter(|n| n.is_element()) {
                let num = |a: &str| n.attribute(a).map(|v| v.parse::<f64>().expect("numeric attribute"));
                let (dx, dy) = offset(n);
                match n.tag_name().name() {
                    "rect" => {
                        let (x, y) = (num("x").unwrap_or(0.0) + dx, num("y").unwrap_or(0.0) + dy);
                        b.union(&Bounds::new(x, y, x + num("width").unwrap(), y + num("height").unwrap()));
                    }
                    "text" => {
                        let text: String = n.descendants().filter_map(|t| t.text()).collect();
                        let fs = num("font-size").expect("text has a font-size");
                        let family = n.attribute("font-family").expect("text has a font-family");
                        let w = fonts.measure(&text, family, fs).expect("test font measures");
                        let align = match n.attribute("text-anchor") {
                            Some("end") => "right",
                            Some("middle") => "center",
                            _ => "left",
                        };
                        b.union(&crate::vega_fit::text_ink(num("x").unwrap() + dx, num("y").unwrap() + dy, w, fs, align, "alphabetic"));
                    }
                    _ => {}
                }
            }
        }
        assert!(b.x1 <= b.x2, "no {class} paint in:\n{svg}");
        b
    };
    (painted("mark-group role-legend"), painted("mark-text role-mark strip"))
}

/// Where a rendered chart's strip paints, in canvas x: per row, each cell's
/// `(left, right)` and the row label's `(left, right)`, measured in `family`.
#[cfg(test)]
pub(crate) struct StripEdges {
    pub cells: Vec<Vec<(f64, f64)>>,
    pub stubs: Vec<(f64, f64)>,
    pub canvas_w: f64,
}

#[cfg(test)]
pub(crate) fn strip_edges(svg: &str, fonts: &FontBook, family: &str) -> StripEdges {
    let box_of = |tag: &str| {
        let attr = |name: &str| tag.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string();
        let text = tag.split_once('>').unwrap().1.split("</text>").next().unwrap();
        let w = fonts.measure_shaped(text, family, attr("font-size").parse().unwrap()).unwrap();
        let x: f64 = attr("x").parse().unwrap();
        match attr("text-anchor").as_str() {
            "end" => (x - w, x),
            _ => (x, x + w),
        }
    };
    let mut cells = Vec::new();
    let mut stubs = Vec::new();
    for part in svg.split("<g class=\"mark-text role-mark strip\"").skip(1) {
        let (group, after) = part.split_once("</g>").expect("a closed strip group");
        cells.push(group.split("<text ").skip(1).map(box_of).collect());
        stubs.push(box_of(after.split("<text ").nth(1).expect("a row label follows its cells")));
    }
    let canvas_w = svg.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
    StripEdges { cells, stubs, canvas_w }
}

/// The first-row x-axis labels (canvas x, `text-anchor`, text) and the first strip row's cells (canvas x of the
/// digit edge, text) of a rendered chart.
#[cfg(test)]
pub(crate) fn labels_and_cells(svg: &str) -> (Vec<(f64, String, String)>, Vec<(f64, String)>) {
    let doc = roxmltree::Document::parse(svg).expect("rendered SVG parses");
    let canvas_x = |node: roxmltree::Node| -> f64 {
        let shift: f64 = node
            .ancestors()
            .filter_map(|a| a.attribute("transform").and_then(|t| t.strip_prefix("translate(")))
            .map(|t| t.split([',', ' ', ')']).next().unwrap().parse::<f64>().expect("numeric translate"))
            .sum();
        node.attribute("x").unwrap().parse::<f64>().unwrap() + shift
    };
    let text_of = |n: roxmltree::Node| -> String { n.descendants().filter(|t| t.is_text()).filter_map(|t| t.text()).collect() };
    let axis = doc.descendants().find(|n| n.attribute("data-dct-role") == Some("x-axis")).expect("an x axis");
    let texts: Vec<roxmltree::Node> = axis.descendants().filter(|n| n.has_tag_name("text")).collect();
    let first_row_y = texts[0].attribute("y");
    let labels = texts
        .iter()
        .filter(|n| n.attribute("y") == first_row_y)
        .map(|n| (canvas_x(*n), n.attribute("text-anchor").unwrap_or("start").to_string(), text_of(*n)))
        .collect();
    let strip = doc.descendants().find(|n| n.attribute("class") == Some("mark-text role-mark strip")).expect("a strip");
    let cells = strip.descendants().filter(|n| n.has_tag_name("text")).map(|n| (canvas_x(n), text_of(n))).collect();
    (labels, cells)
}

/// The label of `labels` whose cell a cell `cell_w` wide ending at `end` sits in: centered on a centered label,
/// beginning at one anchored at its start, ending at one anchored at its end, to the pixel the axis group's
/// half-pixel shift and an edge label's nudge move a label by.
#[cfg(test)]
pub(crate) fn label_cell_of<'a>(labels: &'a [(f64, String, String)], end: f64, cell_w: f64) -> Option<&'a (f64, String, String)> {
    labels.iter().find(|(x, anchor, _)| {
        let begins = match anchor.as_str() {
            "start" => *x,
            "end" => x - cell_w,
            _ => x - cell_w / 2.0,
        };
        (end - cell_w - begins).abs() <= 1.0 + 1e-9
    })
}

/// Asserts that each cell of the strip's first row sits in its x-axis label's cell, pairing the first-row labels
/// with the cells in paint order. The row must print a bare spec (no hung unit).
#[cfg(test)]
pub(crate) fn assert_cells_sit_in_label_cells(svg: &str, fonts: &FontBook, family: &str) {
    let (labels, cells) = labels_and_cells(svg);
    assert_eq!(cells.len(), labels.len(), "one cell per label: {cells:?} vs {labels:?}");
    let cell_w = cells.iter().map(|(_, text)| fonts.measure(text, family, 11.0).unwrap()).fold(0.0_f64, f64::max);
    for ((end, text), label) in cells.iter().zip(&labels) {
        assert!(
            label_cell_of(std::slice::from_ref(label), *end, cell_w).is_some(),
            "{text} ends at {end}, out of the cell of {label:?} ({cell_w} wide): {cells:?} vs {labels:?}"
        );
    }
}

#[cfg(test)]
pub(crate) fn assert_legend_clears_strip(svg: &str, fonts: &FontBook) {
    let (legend, strip) = legend_and_strip_boxes(svg, fonts);
    let apart = legend.x2 <= strip.x1 || strip.x2 <= legend.x1 || legend.y2 <= strip.y1 || strip.y2 <= legend.y1;
    assert!(apart, "legend {legend:?} intersects strip {strip:?}\n{svg}");
}

#[cfg(test)]
mod numerals_tests {
    use super::*;

    /// Rule 3: a row's cells print digits alone and the row label carries the unit, in
    /// the analytic register (`$K`), whichever way the format would have declared it.
    #[test]
    fn a_bare_row_prints_digits_and_names_its_unit() {
        let row = |vals: &[f64], spec: &str| StripNumerals::for_values(vals, spec).unwrap();
        let money = row(&[412_000.0, 388_000.0, 406_000.0], "$.3~s");
        assert_eq!(money.labeled("Pipeline"), "Pipeline, $K");
        assert_eq!(money.cell_text(412_000.0).unwrap(), "412");
        assert_eq!(money.cell_text(388_000.0).unwrap(), "388");
        assert_eq!(money.cell_text(0.0).unwrap(), "0");
        assert_eq!(row(&[1_200_000.0, 3_400_000.0], ".3~s").labeled("Units"), "Units, M");
        let percent = row(&[0.232, 0.22], ".1%");
        assert_eq!(percent.labeled("Share"), "Share, %");
        assert_eq!(percent.cell_text(0.232).unwrap(), "23.2");
        let whole = row(&[75404.0, 68400.0], "$,.0f");
        assert_eq!(whole.labeled("Revenue"), "Revenue, $");
        assert_eq!(whole.cell_text(75404.0).unwrap(), "75,404");
        let count = row(&[12.0, 9.0], ",d");
        assert_eq!(count.labeled("Deals"), "Deals");
        assert_eq!(count.cell_text(12.0).unwrap(), "12");
    }

    /// A label that already names part of the unit gets only the rest; one that names it all stays.
    #[test]
    fn a_row_label_is_given_only_the_unit_it_is_missing() {
        let money = StripNumerals::for_values(&[412_000.0, 388_000.0], "$.3~s").unwrap();
        assert_eq!(money.labeled("Total $"), "Total $, K");
        assert_eq!(money.labeled("Pipeline, $K"), "Pipeline, $K");
        assert_eq!(money.labeled("Kids"), "Kids, $K", "a word that merely contains the letter names nothing");
        let mut euros = StripNumerals::for_values(&[412_000.0, 388_000.0], ".3~s").unwrap();
        euros.wrap_unit("EUR ", "");
        assert_eq!(euros.labeled("Net"), "Net, EUR K");
    }

    /// A row that cannot share one tier prints full digits and names no tier: no cell carries a
    /// magnitude letter the label leaves out.
    #[test]
    fn a_mixed_tier_row_prints_full_digits_under_a_label_with_no_tier() {
        let row = StripNumerals::for_values(&[950_000.0, 120.0], "$.3~s").unwrap();
        assert_eq!(row.cell_text(950_000.0).unwrap(), "950,000");
        assert_eq!(row.cell_text(120.0).unwrap(), "120");
        assert_eq!(row.labeled("Revenue"), "Revenue, $");
        let spread = StripNumerals::for_values(&[1_200_000.0, 3.0], ".3~s").unwrap();
        assert_eq!(spread.cell_text(1_200_000.0).unwrap(), "1,200,000");
        assert_eq!(spread.labeled("Units"), "Units");
    }

    /// A mixed-tier row prints each value exactly: a rounded `951,000` would read as exact.
    #[test]
    fn a_mixed_tier_row_prints_its_values_unrounded() {
        let row = StripNumerals::for_values(&[951_234.0, 120.0], ".3~s").unwrap();
        assert_eq!(row.cell_text(951_234.0).unwrap(), "951,234");
        let spread = StripNumerals::for_values(&[1_234_567.0, 3.0], "$.3~s").unwrap();
        assert_eq!(spread.cell_text(1_234_567.0).unwrap(), "1,234,567");
        assert_eq!(spread.cell_text(3.0).unwrap(), "3");
        let sliver = StripNumerals::for_values(&[4e-7, 2e-7], ".3~s").unwrap();
        assert_eq!(sliver.cell_text(4e-7).unwrap(), "0.0000004");
        let fine = StripNumerals::for_values(&[1.23456789, 2_000_000.0], ".3~s").unwrap();
        assert_eq!(fine.cell_text(1.23456789).unwrap(), "1.23456789");
        assert_eq!(fine.cell_text(2_000_000.0).unwrap(), "2,000,000");
    }

    /// A signed row keeps one sign per cell, and a sliver is not rounded to a zero cell.
    #[test]
    fn a_signed_row_prints_one_sign_per_cell() {
        let row = StripNumerals::for_values(&[14_000_000.0, -14_000_000.0, 2_500_000.0, 0.0], "+.3~s").unwrap();
        assert_eq!(row.cell_text(14_000_000.0).unwrap(), "+14");
        assert_eq!(row.cell_text(-14_000_000.0).unwrap(), "\u{2212}14");
        assert_eq!(row.cell_text(2_500_000.0).unwrap(), "+2.5");
        assert_eq!(row.cell_text(0.0).unwrap(), "+0");
        let sliver = StripNumerals::for_values(&[5_000_000.0, 40.0], ".3~s").unwrap();
        assert_eq!(sliver.cell_text(40.0).unwrap(), "40");
    }

    /// The board's words and decimal are the locale's: the unit names the analytic word.
    #[test]
    fn a_row_speaks_the_boards_words_and_decimal() {
        let mut wire = ::bind::formats::locale::en_us_wire();
        wire["decimal"] = serde_json::json!(",");
        wire["thousands"] = serde_json::json!(".");
        wire["analytic"] = serde_json::json!(["\u{a0}k", "\u{a0}M", "\u{a0}Mrd", "\u{a0}Bio"]);
        let de: ::bind::formats::locale::NumberLocale = serde_json::from_value(wire).unwrap();
        ::bind::formats::locale::with_number_locale(&de, || {
            let row = StripNumerals::for_values(&[1_200_000.0, 3_400_000.0], "$.3~s").unwrap();
            assert_eq!(row.cell_text(1_200_000.0).unwrap(), "1,2");
            assert_eq!(row.labeled("Umsatz"), "Umsatz, $\u{a0}M");
        });
    }

    /// A count compacted to a tier prints that tier's digits and names the tier in its label.
    #[test]
    fn a_compacted_count_row_names_its_tier_in_its_label() {
        let units = StripNumerals::for_values(&[9511.0, 9691.0, 10924.0], ".3~s").unwrap();
        assert_eq!(units.labeled("Units"), "Units, K");
        assert_eq!(units.cell_text(9511.0).unwrap(), "9.51");
        assert_eq!(units.cell_text(9691.0).unwrap(), "9.69");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::predefined_number_spec;

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
            "ribbon_color": "#999999",
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
        build_rows_composed( config, rows, x_field, domain, &centered(x_positions), fonts, style, color_field,
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
    fn centered(xs: &[f64]) -> Vec<Column> {
        xs.iter().map(|&x| Column::centered(x)).collect()
    }

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    // Real font family present in InterVariable.ttf; "sans-serif" is skipped
    // by `face_for()` (mirrors `bar.rs`'s `TEST_FONT`).
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    /// A row label cut to fit is given the unit its kept text is missing, not the unit the
    /// whole name was missing: a `$` past the cut is named again after it.
    #[test]
    fn a_cut_row_label_never_loses_a_unit_named_past_the_cut() {
        let lines = EntryLines {
            spec: Some("$.3~s"),
            lines: Vec::new(),
            numerals: Some(StripNumerals::for_values(&[412_000.0, 388_000.0], "$.3~s").unwrap()),
            nums: Vec::new(),
            ribbons: Vec::new(),
        };
        let stub = lines.stub(&test_fonts(), TEST_FONT, 11.0, "Pipeline value across every region in $").unwrap();
        assert!(stub.contains('\u{2026}') && stub.ends_with(", $K"), "{stub}");
    }

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
            "ribbon_color": "#999999",
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
            print::print(&[divider_line(10.0, 20.0, 110.0, 20.0, &dashed)]),
            "<line x1=\"10\" y1=\"20\" x2=\"110\" y2=\"20\" \
             stroke=\"#cc7700\" stroke-width=\"2\" stroke-dasharray=\"6,3.5\"/>"
        );
        let solid = Divider { dasharray: None, ..dashed };
        assert!(!print::print(&[divider_line(0.0, 0.0, 1.0, 0.0, &solid)]).contains("dasharray"));
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
                    percent: false,
                    ink: None,
                },
                SupportTableEntry {
                    source: Some("revenue".into()),
                    aggregate: Some("sum".into()),
                    format: Some(",d".into()),
                    label: Some("Total".into()),
                    per_series: None,
                    by_measure: false,
                    percent: false,
                    ink: None,
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
                percent: false,
                ink: None,
            }],
            fold: None,
        };
        let err = visual_row_count(&config, 0).unwrap_err();
        assert!(err.contains("series_count"), "names the missing input: {err}");
    }

    /// A `source:` row whose resolve baked an ink paints its label in it: with the
    /// legend hidden the label is what names the series.
    #[test]
    fn a_source_row_label_takes_its_baked_ink() {
        let config = SupportTableConfig {
            entries: vec![SupportTableEntry {
                source: Some("revenue".into()),
                aggregate: None,
                format: Some(",d".into()),
                label: None,
                per_series: None,
                by_measure: false,
                percent: false,
                ink: Some("#123456".into()),
            }],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> =
            vec![serde_json::json!({"week": "2024-01-01", "revenue": 100.0}).as_object().unwrap().clone()];
        let domain = vec!["2024-01-01".to_string()];
        let out =
            build_rows(&config, &rows, "week", &domain, &[10.0], &test_fonts(), &label_style(), None, &[], &[]).unwrap();
        assert_eq!(out[0].label_color.as_deref(), Some("#123456"));
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
        assert_eq!(strip_rows[0].painted(), vec![(10.0, "150".to_string()), (20.0, "20".to_string())]);
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
        assert_eq!(::bind::rows::row_str(&serde_json::json!({"x": null}).as_object().unwrap().clone(), "x"), "");
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
                    percent: false,
                    ink: None,
                },
                SupportTableEntry {
                    source: Some("product".into()),
                    aggregate: Some("count_distinct".into()),
                    format: Some(",d".into()),
                    label: Some("Distinct".into()),
                    per_series: None,
                    by_measure: false,
                    percent: false,
                    ink: None,
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

    /// A bool is 0 or 1; a string is text whatever it reads like.
    #[test]
    fn coerce_numeric_reads_numbers_and_bools_never_text() {
        for raw in ["1e20", " 12 ", "1_0", "n/a", ""] {
            assert_eq!(coerce_numeric(&Value::String(raw.to_string())), None, "{raw:?}");
        }
        assert_eq!(coerce_numeric(&serde_json::json!(true)), Some(1.0));
        assert_eq!(coerce_numeric(&serde_json::json!(5)), Some(5.0));
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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
                percent: false,
                ink: None,
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

    /// S4: every cell ends at `band_center + dx` on either value axis; only the
    /// stub follows the side — past `plot_w + axis_y.labels.padding` on the
    /// right, `-padding` on the left.
    #[test]
    fn composed_cells_end_at_one_edge_and_stubs_follow_the_axis_side() {
        let s = style(8.0, 4.0, 2.0, 0.0, 0.0);
        let rows = vec![ComposedRow {
            label: "Revenue".into(),
            label_w: 40.0,
            cells: vec![ComposedCell { x: 50.0, text: "$1".into(), w: 12.0, align_dx: 0.0, aria: String::new(), href: None }],
            dx: 7.0,
            label_color: None,
            stub_dx: 0.0,
            series: None,
            copy_dx: vec![0.0],
        }];
        let attr = |svg: &str, tag: &str, name: &str| -> String {
            let idx = svg.find(&format!(">{tag}<")).unwrap();
            let start = svg[..idx].rfind("<text").unwrap();
            svg[start..idx].split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string()
        };

        let right = print::print(&composed_rows_nodes(&rows, &s, 6.0, 100.0, 200.0, 300.0, true, 0.0, None).unwrap());
        assert_eq!(attr(&right, "$1", "text-anchor"), "end");
        assert_eq!(attr(&right, "$1", "x"), "257", "cell right-aligned at center + dx");
        assert_eq!(attr(&right, "Revenue", "text-anchor"), "start");
        assert_eq!(attr(&right, "Revenue", "x"), "306", "stub past plot_w + padding");

        let left = print::print(&composed_rows_nodes(&rows, &s, 6.0, 100.0, 200.0, 300.0, false, 0.0, None).unwrap());
        assert_eq!(attr(&left, "$1", "text-anchor"), "end", "a left axis right-aligns its cells too");
        assert_eq!(attr(&left, "$1", "x"), "257");
        assert_eq!(attr(&left, "Revenue", "text-anchor"), "end");
        assert_eq!(attr(&left, "Revenue", "x"), "194", "stub left of the plot by padding");
    }

    /// One band, one right edge: whichever side the value axis is on, every cell
    /// of every row ends at the same x, the declaring anchor included, and no
    /// cell carries a pad space.
    #[test]
    fn every_cells_digits_end_at_one_right_edge_with_no_pad_spaces_and_the_unit_hangs() {
        let domain: Vec<String> = ["2024-01-01", "2024-01-08", "2024-01-15"].map(String::from).to_vec();
        let rows: Vec<serde_json::Map<String, Value>> = domain
            .iter()
            .zip([(279_890.0, 9_511.0, 0.032), (283_211.0, 9_624.0, 0.033), (278_100.0, 10_302.0, 0.0334)])
            .map(|(d, (rev, units, share))| serde_json::json!({"week": d, "rev": rev, "units": units, "share": share}).as_object().unwrap().clone())
            .collect();
        let entry = |source: &str, format: FormatSlot| SupportTableEntry { format: Some(format), ..source_entry(source) };
        let config = SupportTableConfig {
            entries: vec![
                entry("rev", house("currency_compact")),
                entry("units", house("compact")),
                entry("share", FormatSlot::from(".1%")),
            ],
            fold: None,
        };
        let out = build_rows_composed( &config, &rows, "week", &domain, &centered(&[40.0, 120.0, 200.0]), &test_fonts(), &label_style(), None, &[], &[], None, true)
        .unwrap();
        for row in &out {
            for cell in &row.cells {
                assert!(!cell.text.contains(['\u{2007}', '\u{2008}', '\u{200b}']), "{:?} carries a pad", cell.text);
            }
        }
        let s = label_style();
        for stubs_on_right in [true, false] {
            let svg = print::print(&composed_rows_nodes(&out, &s, 6.0, 240.0, 0.0, 0.0, stubs_on_right, 0.0, None).unwrap());
            let cells: Vec<(f64, String)> = svg
                .split("<g class=\"mark-text role-mark strip\"")
                .skip(1)
                .flat_map(|group| group.split("</g>").next().unwrap().split("<text ").skip(1))
                .map(|tag| {
                    let attr = |name: &str| tag.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string();
                    (attr("x").parse::<f64>().unwrap(), attr("text-anchor"))
                })
                .collect();
            assert_eq!(cells.len(), 9);
            assert!(cells.iter().all(|(_, anchor)| anchor == "end"), "stubs_on_right {stubs_on_right}: {cells:?}");
            for band in 0..3 {
                let edges: Vec<f64> = (0..3).map(|row| cells[row * 3 + band].0).collect();
                assert!(edges.windows(2).all(|w| (w[0] - w[1]).abs() < 0.01), "band {band} digit edges differ: {edges:?}");
            }
        }
        // A row's cells carry no unit: the row label names it.
        assert_eq!(out[0].cells[0].text, "280");
        assert_eq!(out[0].label, "rev, $K");
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
                percent: false,
                ink: None,
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
        let out = build_rows_composed(
            &config,
            &rows,
            "week",
            &domain,
            &centered(&[10.0, 20.0]),
            &test_fonts(),
            &label_style(),
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
        let out = build_rows_composed(
            &config,
            &rows,
            "week",
            &domain,
            &centered(&[10.0, 20.0]),
            &fonts,
            &label_style(),
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

    fn source_entry(source: &str) -> SupportTableEntry {
        SupportTableEntry {
            source: Some(source.into()),
            aggregate: None,
            format: Some(",d".into()),
            label: Some(source.into()),
            per_series: None,
            by_measure: false,
            percent: false,
            ink: None,
        }
    }

    /// The numbers under one band form one column: every row's lane is the
    /// widest cell of any row, so their right edges share one x.
    #[test]
    fn rows_of_different_widths_share_one_lane_per_band() {
        let config = SupportTableConfig { entries: vec![source_entry("big"), source_entry("small")], fold: None };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "big": 1234567.0, "small": 5.0}).as_object().unwrap().clone(),
            serde_json::json!({"week": "2024-01-08", "big": 7.0, "small": 8.0}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let fonts = test_fonts();
        let out = build_rows_composed( &config, &rows, "week", &domain, &centered(&[10.0, 20.0]), &fonts, &label_style(), None, &[], &[], None, false)
        .unwrap();
        assert_eq!(out[0].dx, out[1].dx, "one lane for the column, not one per row");
        let widest = fonts.measure("1,234,567", TEST_FONT, 11.0).unwrap();
        assert_eq!(out[1].dx, widest / 2.0, "the lane is the widest cell of any row");
    }

    /// A strip has one cell width: the widest text before any trailing unit of every
    /// value of every row (the anchor's `$` counts) plus one unit slot, the widest
    /// unit. Each cell is centered on its column, so a row's digits end at the cell's
    /// right edge less the slot, with or without a band under it.
    #[test]
    fn one_cell_width_centers_every_row_on_its_column() {
        let config = SupportTableConfig {
            entries: vec![
                SupportTableEntry { format: Some(house("currency_compact")), ..source_entry("rev") },
                SupportTableEntry { format: Some(",d".into()), ..source_entry("units") },
            ],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = [("2024-01-01", 279_890.0, 283.0), ("2024-01-08", 283_211.0, 9.0)]
            .iter()
            .map(|(d, rev, units)| serde_json::json!({"week": d, "rev": rev, "units": units}).as_object().unwrap().clone())
            .collect();
        let domain = vec!["2024-01-01".to_string(), "2024-01-08".to_string()];
        let out = build_rows_composed( &config, &rows, "week", &domain, &centered(&[10.0, 20.0]), &test_fonts(), &label_style(), None, &[], &[], None, true)
        .unwrap();
        let anchor = &out[0].cells[0];
        assert_eq!(anchor.text, "280");
        let digits = anchor.w;
        assert_eq!(out[0].dx, out[1].dx, "one cell width for the strip");
        assert!((out[0].dx - digits / 2.0).abs() < 0.01, "dx {} vs {}", out[0].dx, digits / 2.0);
    }

    /// A column aligns as its axis label does: the cell under a label flushed to the
    /// plot's start begins at the label's x, the one under a label flushed to its end
    /// ends there, and every other cell centers on its label.
    #[test]
    fn a_cell_aligns_on_its_column_as_the_columns_axis_label_does() {
        let config = SupportTableConfig { entries: vec![source_entry("units")], fold: None };
        let rows: Vec<serde_json::Map<String, Value>> = [("2024-01-01", 206.0), ("2024-02-01", 9.0), ("2024-03-01", 1_234.0)]
            .iter()
            .map(|(d, units)| serde_json::json!({"month": d, "units": units}).as_object().unwrap().clone())
            .collect();
        let domain: Vec<String> = rows.iter().map(|row| row["month"].as_str().unwrap().to_string()).collect();
        let columns = [
            Column { x: 0.0, align: Align::Left },
            Column { x: 100.0, align: Align::Center },
            Column { x: 200.0, align: Align::Right },
        ];
        let fonts = test_fonts();
        let out = build_rows_composed( &config, &rows, "month", &domain, &columns, &fonts, &label_style(), None, &[], &[], None, true)
            .unwrap();
        let cell_w = fonts.measure("1,234", TEST_FONT, 11.0).unwrap();
        let ends: Vec<f64> = out[0].cells.iter().map(|cell| cell.x + out[0].dx).collect();
        assert!((ends[0] - cell_w).abs() < 0.01, "a left-flushed cell begins at its label: {ends:?}, cell {cell_w}");
        assert!((ends[1] - (100.0 + cell_w / 2.0)).abs() < 0.01, "a centered cell centers on its label: {ends:?}, cell {cell_w}");
        assert!((ends[2] - 200.0).abs() < 0.01, "a right-flushed cell ends at its label: {ends:?}");
        assert!(
            (composed_left_bound(&out, 0.0) - cell_w / 2.0).abs() < 0.01,
            "a column flushed to the plot's end counts as centered: it never comes near the plot's start"
        );
        let starts = [Column { x: 0.0, align: Align::Left }, Column { x: 100.0, align: Align::Left }, Column { x: 200.0, align: Align::Left }];
        let out = build_rows_composed( &config, &rows, "month", &domain, &starts, &fonts, &label_style(), None, &[], &[], None, true)
            .unwrap();
        assert!(composed_left_bound(&out, 0.0) <= 1e-9, "cells that start at their labels reach nothing left of the plot");
    }

    /// Twelve 8-digit values at `pitch`, in the compact `number` resolve leaves them in
    /// (`14m`, 13 more): the rows the fit keeps.
    fn crowded_strip(pitch: f64, ordered_x: bool) -> Option<Vec<ComposedRow>> {
        let domain: Vec<String> = (0..12).map(|i| format!("region {i}")).collect();
        let rows: Vec<serde_json::Map<String, Value>> = domain
            .iter()
            .enumerate()
            .map(|(i, d)| serde_json::json!({"region": d, "rev": 13_579_012.0 + 1_111_111.0 * i as f64}).as_object().unwrap().clone())
            .collect();
        let positions: Vec<f64> = (0..12).map(|i| pitch * (i as f64 + 0.5)).collect();
        let config = SupportTableConfig { entries: vec![SupportTableEntry { format: Some(house("number")), ..source_entry("rev") }], fold: None };
        let (fonts, style) = (test_fonts(), label_style());
        let composed = build_rows_composed( &config, &rows, "region", &domain, &centered(&positions), &fonts, &style, None, &[], &[], None, ordered_x)
            .unwrap();
        fit_strip("c", composed, style.row.padding.horizontal, ordered_x)
    }

    #[test]
    fn a_crowded_categorical_strip_hides_whole_with_a_warning() {
        assert!(crowded_strip(90.0, false).is_some_and(|rows| rows[0].cells.len() == 12), "room to spare: every cell paints");
        let (rows, warnings) = diagnostic::collect(|| crowded_strip(12.0, false));
        assert!(rows.is_none(), "a category never loses some of its numbers");
        assert!(warnings.iter().any(|d| d.code == "WARN-SUPPORT-TABLE-CELL-OVERFLOW"), "{warnings:?}");
    }

    #[test]
    fn a_crowded_date_strip_comes_back_whole_for_skipping() {
        let (rows, warnings) = diagnostic::collect(|| crowded_strip(12.0, true));
        let mut out = rows.expect("an ordered strip is never hidden");
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(out[0].cells.len(), 12);
        skip_strip("c", &mut out, None, 4.0);
        assert!(out[0].cells.len() < 12, "{:?}", out[0].cells.len());
    }

    /// The gutter reserved from the authored strip holds a fitted strip that
    /// reaches no farther left; one that reaches farther hides with a warning.
    #[test]
    fn a_strip_that_reaches_past_its_gutter_is_refused_with_a_warning() {
        let rows = vec![strip_row(30.0, &[40.0, 10.0])];
        assert_eq!(composed_left_overhang(&rows), 40.0, "the first cell ends at the plot's edge and is 40 wide");
        let (holds, warnings) = diagnostic::collect(|| gutter_holds("c", &rows, 40.0, 100.0));
        assert!(holds && warnings.is_empty());
        let (holds, warnings) = diagnostic::collect(|| gutter_holds("c", &rows, 30.0, 100.0));
        assert!(!holds);
        assert!(warnings.iter().any(|d| d.code == "WARN-SUPPORT-TABLE-CELL-OVERFLOW"), "{warnings:?}");
    }

    /// A bucket past the plot hides the strip, a lane overhanging the edge does not.
    #[test]
    fn only_a_bucket_past_the_plot_refuses_a_strip_at_the_right_edge() {
        let mut rows = vec![strip_row(30.0, &[10.0, 10.0])];
        rows[0].dx = 20.0;
        let last = rows[0].cells.last().expect("a cell").x;
        let (holds, warnings) = diagnostic::collect(|| gutter_holds("c", &rows, f64::INFINITY, last));
        assert!(holds && warnings.is_empty(), "the lane overhangs but the bucket is on the plot: {warnings:?}");
        let (holds, warnings) = diagnostic::collect(|| gutter_holds("c", &rows, f64::INFINITY, last - 1.0));
        assert!(!holds);
        assert!(format!("{warnings:?}").contains("past the plot's right edge"), "{warnings:?}");
    }

    /// A cell aligned on its column's start (an authored `labels.align: left`) centers half its width right of its
    /// bucket, so its center can sit past the plot while the bucket does not: the strip holds.
    #[test]
    fn a_left_aligned_last_column_on_the_plot_holds() {
        let fonts = test_fonts();
        let config = SupportTableConfig { entries: vec![source_entry("units")], fold: None };
        let rows: Vec<serde_json::Map<String, Value>> = [("2024-01-01", 206.0), ("2024-02-01", 1_234.0)]
            .iter()
            .map(|(d, units)| serde_json::json!({"month": d, "units": units}).as_object().unwrap().clone())
            .collect();
        let domain: Vec<String> = rows.iter().map(|row| row["month"].as_str().unwrap().to_string()).collect();
        let plot_w = 100.0;
        let columns = [Column { x: 0.0, align: Align::Left }, Column { x: plot_w - 1.0, align: Align::Left }];
        let out = build_rows_composed( &config, &rows, "month", &domain, &columns, &fonts, &label_style(), None, &[], &[], None, true)
            .unwrap();
        assert!(out[0].cells[1].x > plot_w, "the last cell centers past the plot: {}", out[0].cells[1].x);
        let (holds, warnings) = diagnostic::collect(|| gutter_holds("c", &out, f64::INFINITY, plot_w));
        assert!(holds && warnings.is_empty(), "its bucket is on the plot: {warnings:?}");
    }

    /// Fourteen daily buckets at 14px, rows in `values` order; `None` is no row at all.
    fn skipped_strip(config: &SupportTableConfig, series: &[(&str, Vec<Option<f64>>)]) -> Vec<ComposedRow> {
        let domain: Vec<String> = (1..=14).map(|d| format!("2024-01-{d:02}")).collect();
        let positions: Vec<f64> = (0..14).map(|i| 14.0 * i as f64).collect();
        let rows: Vec<serde_json::Map<String, Value>> = series
            .iter()
            .flat_map(|(name, values)| {
                domain.iter().zip(values).filter_map(move |(day, v)| {
                    v.map(|v| serde_json::json!({"week": day, "category": name, "revenue": v}).as_object().unwrap().clone())
                })
            })
            .collect();
        let order: Vec<String> = series.iter().map(|(name, _)| name.to_string()).collect();
        let ink = vec!["#111111".to_string(); order.len()];
        let mut out = build_rows_composed( config, &rows, "week", &domain, &centered(&positions), &test_fonts(), &label_style(), Some("category"), &order, &ink, None, true)
        .unwrap();
        skip_strip("c", &mut out, None, 4.0);
        out
    }

    fn house(name: &str) -> FormatSlot {
        FormatSlot { raw: Some(name.into()), ..FormatSlot::from(predefined_number_spec(name).unwrap()) }
    }

    fn with_format(source: &str, spec: &str) -> SupportTableEntry {
        SupportTableEntry { format: Some(spec.into()), ..source_entry(source) }
    }

    /// Mixed precisions under one band are plain right-aligned text: no pad
    /// spaces, so `232`, `7,852` and `29.50` all end at the lane's right edge.
    #[test]
    fn rows_of_different_precision_end_at_one_edge_with_no_pad() {
        let config = SupportTableConfig {
            entries: vec![with_format("a", ",d"), with_format("b", ",d"), with_format("c", ",.2f")],
            fold: None,
        };
        let rows: Vec<serde_json::Map<String, Value>> = vec![
            serde_json::json!({"week": "2024-01-01", "a": 232.0, "b": 7852.0, "c": 29.5}).as_object().unwrap().clone(),
        ];
        let domain = vec!["2024-01-01".to_string()];
        let out = build_rows_composed( &config, &rows, "week", &domain, &centered(&[10.0]), &test_fonts(), &label_style(), None, &[], &[], None, false)
        .unwrap();
        let texts: Vec<&str> = out.iter().map(|row| row.cells[0].text.as_str()).collect();
        assert_eq!(texts, ["232", "7,852", "29.50"]);
        assert!(out.iter().all(|row| row.cells[0].x + row.dx == out[0].cells[0].x + out[0].dx));
    }

    /// A strip row of `widths.len()` cells, one per bucket `pitch` apart, whose
    /// aligned right edge sits on the bucket (`dx` 0, axis on the right).
    fn strip_row(pitch: f64, widths: &[f64]) -> ComposedRow {
        ComposedRow {
            label: "Revenue".into(),
            label_w: 40.0,
            cells: widths
                .iter()
                .enumerate()
                .map(|(i, &w)| ComposedCell { x: pitch * i as f64, text: "$280,000".into(), w, align_dx: 0.0, aria: String::new(), href: None })
                .collect(),
            dx: 0.0,
            label_color: None,
            stub_dx: 0.0,
            series: None,
            copy_dx: vec![0.0],
        }
    }

    /// The bucket indices a row still paints, read off the cells' x at `pitch`.
    fn kept_buckets(row: &ComposedRow, pitch: f64) -> Vec<usize> {
        row.cells.iter().map(|c| (c.x / pitch).round() as usize).collect()
    }

    fn skipped(rows: &mut Vec<ComposedRow>, labeled: Option<&[bool]>) -> Vec<Diagnostic> {
        diagnostic::collect(|| skip_strip("c", rows, labeled, 4.0)).1
    }

    #[test]
    fn a_strip_that_fits_keeps_every_cell_and_stays_quiet() {
        let mut rows = vec![strip_row(43.0, &[30.0; 26])];
        let warnings = skipped(&mut rows, None);
        assert_eq!(rows[0].cells.len(), 26);
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn an_overflowing_strip_keeps_the_smallest_stride_whose_narrowest_gap_fits() {
        let mut rows = vec![strip_row(14.0, &[50.0; 26])];
        let warnings = skipped(&mut rows, None);
        // 3 buckets leave 42 - 50 < 0, short of the 4px of padding; 4 leave 6px.
        assert_eq!(kept_buckets(&rows[0], 14.0), vec![0, 4, 8, 12, 16, 20, 24]);
        assert!(warnings.is_empty(), "skipping resolved the overflow: {warnings:?}");
    }

    #[test]
    fn every_row_keeps_the_same_buckets_whatever_its_own_width() {
        let mut rows = vec![strip_row(14.0, &[50.0; 26]), strip_row(14.0, &[6.0; 26])];
        skipped(&mut rows, None);
        assert_eq!(kept_buckets(&rows[1], 14.0), kept_buckets(&rows[0], 14.0));
        assert_eq!(rows[1].cells.len(), 7, "the narrow row is thinned with the wide one");
    }

    #[test]
    fn the_first_bucket_survives_even_when_the_axis_does_not_label_it() {
        let labeled: Vec<bool> = (0..26).map(|i| i % 4 == 2).collect();
        let mut rows = vec![strip_row(20.0, &[30.0; 26])];
        let warnings = skipped(&mut rows, Some(&labeled));
        let kept = kept_buckets(&rows[0], 20.0);
        assert_eq!(kept, vec![0, 2, 6, 10, 14, 18, 22]);
        assert!(kept[1..].iter().all(|&i| labeled[i]), "past the first, only labeled buckets: {kept:?}");
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn a_strip_that_fits_still_keeps_only_the_first_bucket_and_the_labeled_ones() {
        let labeled: Vec<bool> = (0..26).map(|i| i % 4 == 2).collect();
        let mut rows = vec![strip_row(43.0, &[30.0; 26])];
        let warnings = skipped(&mut rows, Some(&labeled));
        assert_eq!(kept_buckets(&rows[0], 43.0), vec![0, 2, 6, 10, 14, 18, 22]);
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn labeled_buckets_are_skipped_in_their_own_order_when_they_crowd() {
        let labeled: Vec<bool> = (0..26).map(|i| i % 2 == 0).collect();
        let mut rows = vec![strip_row(10.0, &[30.0; 26])];
        skipped(&mut rows, Some(&labeled));
        // Labeled buckets are 20px apart: 2 of them leave 40 - 30 = 10px.
        assert_eq!(kept_buckets(&rows[0], 10.0), vec![0, 4, 8, 12, 16, 20, 24]);
    }

    #[test]
    fn a_wide_anchor_alone_never_forces_a_skip() {
        // `$280k` over a row of `283`: the anchor clears its right neighbor.
        let mut widths = vec![22.0; 26];
        widths[0] = 57.0;
        let mut rows = vec![strip_row(43.0, &widths)];
        let warnings = skipped(&mut rows, None);
        assert_eq!(rows[0].cells.len(), 26);
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn a_strip_that_still_overflows_after_skipping_warns() {
        let mut rows = vec![strip_row(10.0, &[200.0; 3])];
        let warnings = skipped(&mut rows, None);
        assert_eq!(kept_buckets(&rows[0], 10.0), vec![0, 2], "the first and last are the least a strip can keep");
        assert!(warnings.iter().any(|d| d.code == "WARN-SUPPORT-TABLE-CELL-OVERFLOW"), "{warnings:?}");
    }

    #[test]
    fn a_wide_cell_reaches_back_over_its_left_neighbor() {
        let mut rows = vec![strip_row(30.0, &[10.0, 10.0, 50.0, 10.0])];
        let warnings = skipped(&mut rows, None);
        // Right-aligned at x: the wide third cell reaches back over the second.
        assert_eq!(kept_buckets(&rows[0], 30.0), vec![0, 2]);
        assert!(warnings.is_empty(), "{warnings:?}");
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
                    percent: false,
                    ink: None,
                },
                SupportTableEntry {
                    source: Some("revenue".into()),
                    aggregate: Some("sum".into()),
                    format: Some(",d".into()),
                    label: Some("Total".into()),
                    per_series: None,
                    by_measure: false,
                    percent: false,
                    ink: None,
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
        let out = build_rows_composed(
            &config,
            &rows,
            "week",
            &domain,
            &centered(&[10.0, 20.0]),
            &test_fonts(),
            &s,
            Some("category"),
            &series_order,
            &series_ink, None, false)
        .unwrap();
        assert_eq!(out.len(), 4, "3 series rows + 1 aggregate row");
        let svg = print::print(&composed_rows_nodes(&out, &s, 6.0, 100.0, 0.0, 0.0, false, 0.0, None).unwrap());
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
        let err = build_rows_composed(
            &config,
            &rows,
            "week",
            &domain,
            &centered(&[10.0, 20.0]),
            &test_fonts(),
            &label_style(),
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
        let err = build_rows_composed(
            &config,
            &rows,
            "week",
            &domain,
            &centered(&[10.0, 20.0]),
            &test_fonts(),
            &label_style(),
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
            percent: false,
            ink: None,
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

    fn build_share_row(entry: SupportTableEntry, values: &[f64]) -> Vec<ComposedRow> {
        let (rows, domain) = share_rows(values);
        let xs: Vec<f64> = (0..values.len()).map(|i| 100.0 * i as f64).collect();
        build_rows_composed(
            &SupportTableConfig { entries: vec![entry], fold: None },
            &rows,
            "q",
            &domain,
            &centered(&xs),
            &test_fonts(),
            &label_style(),
            None,
            &[],
            &[], None, false)
        .unwrap()
    }

    /// A source row's cells hang nothing and stay on their bands: the unit is in the row
    /// label (`_hung_anchor_layers` only ever moves a `per_series:` row's anchor).
    #[test]
    fn a_source_rows_cells_carry_no_unit_and_hang_nothing() {
        let cases: [(&str, &[f64], &[&str], &str); 4] = [
            ("percent_whole", &[0.03, 0.05, 0.07], &["3", "5", "7"], "%"),
            ("currency_whole", &[3.0, 5.0, 7.0], &["3", "5", "7"], "$"),
            ("currency", &[1_100_000.0, 2_200_000.0, 3_300_000.0], &["1.1", "2.2", "3.3"], "$M"),
            ("number", &[3.0, 5.0, 7.0], &["3", "5", "7"], ""),
        ];
        for (name, values, texts, unit) in cases {
            let spec = predefined_number_spec(name).unwrap();
            let out = build_share_row(house_entry(name, spec), values);
            let cells = &out[0].cells;
            assert_eq!(cells.iter().map(|c| c.text.as_str()).collect::<Vec<_>>(), texts, "{name}");
            assert!(cells.iter().enumerate().all(|(i, c)| c.x == 100.0 * i as f64), "{name}: {cells:?}");
            let expected = if unit.is_empty() { "Share".to_string() } else { format!("Share, {unit}") };
            assert_eq!(out[0].label, expected, "{name}");
        }
    }
}
