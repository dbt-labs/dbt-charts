//! Small multiples (`chart.multiples` / facet) — shared data-plane and
//! composite-layout primitives, used by every faceting family (bar first,
//! then line/area/scatter/heatmap per `AGENTS.md`).
//!
//! Two production sources this ports, function-for-function:
//!
//! * `compile/resolve/chart/_chart_rows.py`'s `canonical_key`/`regroup` —
//!   the render-time replay of resolve's baked `panel_axes`
//!   (`ChartDataset`/`Panel`/`PartitionAxis`). `partition()` itself (which
//!   BAKES `panel_axes` from raw rows at resolve) is not ported: every
//!   payload this renderer receives already carries `panel_axes` baked by
//!   production's own resolve pass (`resolved.panel_axes`), so render only
//!   ever needs to REPLAY that partition against the payload's flat `rows`
//!   — exactly what `regroup` does, never `partition`.
//! * `compile/resolve/chart/adaptive_stroke.py`'s `facet_panel_width` /
//!   `panel_axis_cardinality` — the per-panel pixel budget both resolve's
//!   adaptive stroke and render's own box construction key off.
//!
//! `gridLayout`/`layoutHeaders` (vega-scenegraph's own facet composite
//! sizing — the algorithm `_correct_facet_overshoot`'s probe render
//! ultimately measures) live in the sibling `facet_layout` module, not
//! here — this module stays the data-plane only (regroup/canonical_key/
//! per-panel pixel budget); `facet_layout` is the pure composite-geometry
//! port every family's own composer (`bar.rs::render_facet` first) calls
//! into. `bar.rs::render_vertical_panel` is that seam: `render_vertical`'s
//! own single-chart wrapper and `render_facet`'s per-panel calls both go
//! through it — see `render_facet`'s own doc comment for the composer and
//! the scope it still refuses outside of (a legend off `top`, layers,
//! support_table, wide measures, a value-axis
//! title or `axis_y.position: "left"` or a continuous x on a multi-column
//! facet, and — combined with `axis_y.mirror` — a `position: "left"` axis
//! of any shape, a continuous x, or a mirror `format` or `expr`).

use crate::constants::estimates::{FACET_CHROME_PX, FACET_MIRROR_AXIS_PX};
use crate::constants::layout::FACET_MIN_PANEL_PX;
use serde::Deserialize;
use serde_json::{Map, Value};

/// `chart.multiples` — the authored partition config (`_base.py::MultiplesConfig`,
/// as it lands in a resolved chart's own `multiples` field: `rows`/`columns`
/// field names plus scale sharing).
#[derive(Deserialize, Clone)]
pub struct MultiplesConfig {
    pub rows: Option<String>,
    pub columns: Option<String>,
    #[serde(default = "default_scale")]
    pub scale: String,
}

fn default_scale() -> String {
    "shared".into()
}

/// One baked small-multiples partition dimension — `resolved.panel_axes`
/// entries (`_partition.py::PartitionAxis`). `values` are already the
/// CANONICAL string form (`canonical_key`'s own output, baked once at
/// resolve) — panel render order, not display text.
#[derive(Deserialize, Clone)]
pub struct PartitionAxis {
    pub field: String,
    pub values: Vec<String>,
}

/// One small-multiples panel: its partition key (original values, one per
/// axis) plus the rows belonging to it. Mirrors `_chart_rows.py::Panel`,
/// minus `indices` — no caller here needs the original query-row order back
/// (`all_rows()`'s re-interleave has no analog: a facet renderer always
/// walks panels, never flattens them back to one wire array).
#[derive(Debug)]
pub struct Panel {
    pub key: Vec<Value>,
    pub rows: Vec<Map<String, Value>>,
}

/// Canonical string form of one partition value — `_chart_rows.py::canonical_key`,
/// which is `pydantic_core.to_json(value).decode()`: standard JSON scalar
/// encoding (a bare number for an int/float, a quoted string for a string,
/// `null`/`true`/`false` verbatim). `serde_json::to_string` on the same
/// `serde_json::Value` produces byte-identical output for every scalar this
/// renderer's payloads carry (string/int/float/bool/null) — both are
/// standard JSON encoders over the same value space, so there is no
/// independent formatting decision to drift. `to_string` only fails on a
/// value containing non-finite floats (NaN/Infinity), which JSON cannot
/// represent at all — unreachable here since the payload itself was
/// JSON-decoded from production's own serialization.
pub fn canonical_key(value: &Value) -> String {
    serde_json::to_string(value).expect("JSON value must itself be re-serializable")
}

/// Re-apply resolve's baked `panel_axes` to a chart's flat `rows` at render
/// — `_chart_rows.py::regroup`. `axes` empty means the chart is not faceted:
/// one panel, keyed `()`, holding every row — the same N=1 case `Payload`
/// already models for a non-faceted chart (nothing calls this then).
///
/// Panel order is `axes`' own cartesian product (`itertools.product` in
/// production — rows outer, columns inner when both are set), matching a
/// Vega-Lite facet's row-major panel grid. A combination with no matching
/// rows is skipped, never emitted as an empty panel (`if combo in groups`
/// in production) — a sparse facet grid renders only the panels it has data
/// for.
///
/// Errors (never silently drop or misroute a row):
/// - a row missing a partition field entirely (`ERR-MULTIPLES-ROW-MISSING-
///   PARTITION-FIELD`)
/// - a row's partition value canonicalizing to something outside the baked
///   axis's own `values` (`ERR-MULTIPLES-ROW-OUTSIDE-PANEL-AXES`) — the
///   row-truncated render path can only ever hold a SUBSET of the baked
///   axis values, never one the axes lack.
pub fn regroup(
    axes: &[PartitionAxis],
    rows: &[Map<String, Value>],
) -> Result<Vec<Panel>, String> {
    if axes.is_empty() {
        return Ok(vec![Panel { key: vec![], rows: rows.to_vec() }]);
    }
    let fields: Vec<&str> = axes.iter().map(|a| a.field.as_str()).collect();

    // canonical combo key -> (original values, rows in query order)
    let mut originals: std::collections::HashMap<Vec<String>, Vec<Value>> =
        std::collections::HashMap::new();
    let mut groups: std::collections::HashMap<Vec<String>, Vec<Map<String, Value>>> =
        std::collections::HashMap::new();
    for row in rows {
        let mut canon = Vec::with_capacity(fields.len());
        let mut original = Vec::with_capacity(fields.len());
        for (axis, field) in axes.iter().zip(fields.iter()) {
            let Some(value) = row.get(*field) else {
                return Err(format!(
                    "multiples: row missing partition field {field:?} (available: {:?})",
                    row.keys().collect::<Vec<_>>()
                ));
            };
            let canon_value = canonical_key(value);
            if !axis.values.iter().any(|v| v == &canon_value) {
                return Err(format!(
                    "multiples: row value for {field:?} ({canon_value}) outside baked panel_axes"
                ));
            }
            canon.push(canon_value);
            original.push(value.clone());
        }
        let stripped: Map<String, Value> = row
            .iter()
            .filter(|(k, _)| !fields.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        originals.entry(canon.clone()).or_insert(original);
        groups.entry(canon).or_default().push(stripped);
    }

    let combos = cartesian_product(axes.iter().map(|a| a.values.as_slice()).collect());
    let mut panels = Vec::new();
    for combo in combos {
        if let Some(panel_rows) = groups.remove(&combo) {
            panels.push(Panel { key: originals.remove(&combo).unwrap(), rows: panel_rows });
        }
    }
    Ok(panels)
}

/// One facet dimension's header values in baked axis order: every
/// `axis.values` entry some panel carries, as that panel's original value.
/// A value no panel holds (the row-truncated render path) has no header.
pub fn axis_header_values<'a>(axis: &PartitionAxis, axis_idx: usize, panels: &'a [Panel]) -> Vec<&'a Value> {
    axis.values
        .iter()
        .filter_map(|canon| panels.iter().map(|p| &p.key[axis_idx]).find(|key| canonical_key(key) == *canon))
        .collect()
}

/// How many grid cells hold no data in a `rows_n x cols_n` facet whose
/// `panel_count` populated panels `regroup` returned. Vega's facet `cross`
/// aggregate emits those cells with undefined sort indices, which sort
/// before every populated cell: they take the first grid slots and the
/// populated panels, in `regroup`'s row-major order, fill the rest.
pub fn empty_leading_cells(panel_count: usize, rows_n: usize, cols_n: usize) -> Result<usize, String> {
    (rows_n * cols_n)
        .checked_sub(panel_count)
        .ok_or_else(|| format!("multiples: {panel_count} panels do not fit a {rows_n} x {cols_n} grid"))
}

/// `_chart_rows.py::restamp`: put a partition field's value back onto every
/// row of its panels. `regroup` strips the partition columns (a panel's key
/// already carries them), but a chart whose `x` or `color` IS a partition
/// field still reads it per row, so its own renderer re-stamps that one field.
/// A field that is not a partition axis is left alone.
pub fn restamp(panels: &mut [Panel], axes: &[PartitionAxis], field: &str) {
    let Some(axis_idx) = axes.iter().position(|a| a.field == field) else { return };
    for panel in panels {
        let value = &panel.key[axis_idx];
        for row in &mut panel.rows {
            row.insert(field.to_string(), value.clone());
        }
    }
}

/// `_cartesian.py::_panel_domain_is_proper_subset`: true when at least one
/// panel's rows hold a proper subset of `field`'s values across the whole
/// chart. A field with at most one distinct value overall has no narrower
/// state, and panels holding no value for it do not count.
pub fn field_is_proper_subset_in_a_panel(panels: &[Panel], field: &str) -> bool {
    let per_panel: Vec<std::collections::HashSet<String>> = panels
        .iter()
        .map(|panel| {
            panel.rows.iter().filter_map(|row| row.get(field).filter(|v| !v.is_null())).map(canonical_key).collect()
        })
        .filter(|values: &std::collections::HashSet<String>| !values.is_empty())
        .collect();
    let domain_len = per_panel.iter().flatten().collect::<std::collections::HashSet<_>>().len();
    domain_len > 1 && per_panel.iter().any(|values| values.len() < domain_len)
}

/// Row-major cartesian product of N value lists — `itertools.product(*lists)`:
/// the last list varies fastest (columns inner, rows outer, matching
/// production's `[multiples.rows, multiples.columns]` field order into
/// `regroup`/`partition`).
fn cartesian_product(lists: Vec<&[String]>) -> Vec<Vec<String>> {
    let mut result: Vec<Vec<String>> = vec![vec![]];
    for list in lists {
        let mut next = Vec::with_capacity(result.len() * list.len());
        for prefix in &result {
            for value in list {
                let mut combo = prefix.clone();
                combo.push(value.clone());
                next.push(combo);
            }
        }
        result = next;
    }
    result
}

/// Distinct values on one baked small-multiples axis (>= 1) —
/// `adaptive_stroke.py::panel_axis_cardinality`. `field: None` means that
/// facet dimension is absent (a single track).
pub fn panel_axis_cardinality(axes: &[PartitionAxis], field: Option<&str>) -> usize {
    let Some(field) = field else { return 1 };
    axes.iter().find(|a| a.field == field).map_or(1, |a| a.values.len().max(1))
}

/// The `(rows, cols)` panel grid a chart's `multiples:` block divides its box
/// into; `(1, 1)` when it has none.
pub fn grid_counts(mc: Option<&MultiplesConfig>, axes: &[PartitionAxis]) -> (usize, usize) {
    mc.map_or((1, 1), |mc| {
        (
            panel_axis_cardinality(axes, mc.rows.as_deref()),
            panel_axis_cardinality(axes, mc.columns.as_deref()),
        )
    })
}

/// Per-panel pixel width for a small-multiples chart —
/// `adaptive_stroke.py::facet_panel_width`. `chrome_px`/`mirror_axis_px` are
/// `FACET_CHROME_PX` / `FACET_MIRROR_AXIS_PX`.
pub fn facet_panel_width(
    width: f64,
    panel_cols: usize,
    has_mirror: bool,
    extra_axis_px: f64,
) -> f64 {
    let chrome = FACET_CHROME_PX + if has_mirror { FACET_MIRROR_AXIS_PX } else { 0.0 };
    let usable = (width - chrome).max(0.0);
    (usable / panel_cols as f64 - extra_axis_px).max(0.0)
}

/// `extra_axis_is_affordable`: narrowing a position channel draws one more axis
/// per column panel, which is worth it only while that leaves a legible panel
/// (`FACET_MIN_PANEL_PX`). No extra axis is always affordable.
pub fn extra_axis_is_affordable(unnarrowed_panel_width: f64, extra_axis_px: f64) -> bool {
    extra_axis_px <= 0.0 || unnarrowed_panel_width - extra_axis_px >= FACET_MIN_PANEL_PX
}

/// Production resolve rejects a both-edge mirror under an independent scale
/// (`_axes.py::_apply_multiples_mirror`): each panel owns its scale, so
/// mirroring a shared one to both edges is meaningless.
pub fn independent_mirror_error(chart_id: &str) -> crate::diagnostic::Diagnostic {
    crate::diagnostic::Diagnostic::new(
        &crate::codes::ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR,
        [("chart_id", Value::from(chart_id))],
    )
}

/// `WARN-FACET-PANEL-WIDTH-BELOW-MINIMUM` (`facet_panel_width_below_minimum.py`):
/// production judges the `facet_panel_width` its spec gives each panel, before
/// any composite width correction.
pub fn warn_panel_below_minimum(chart_id: &str, panel_cols: usize, panel_width: f64) {
    if panel_width < FACET_MIN_PANEL_PX {
        crate::diagnostic::warn(crate::diagnostic::Diagnostic::new(
            &crate::codes::WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM,
            [
                ("chart_id", Value::from(chart_id)),
                ("min_panel_px", Value::from(FACET_MIN_PANEL_PX)),
                ("panel_cols", Value::from(panel_cols)),
                ("panel_width", Value::from(panel_width)),
            ],
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(pairs: &[(&str, Value)]) -> Map<String, Value> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    #[test]
    fn canonical_key_matches_json_scalar_encoding() {
        assert_eq!(canonical_key(&Value::from(2019)), "2019");
        assert_eq!(canonical_key(&Value::from("2019")), "\"2019\"");
        assert_eq!(canonical_key(&Value::Null), "null");
        assert_eq!(canonical_key(&Value::from(true)), "true");
    }

    #[test]
    fn regroup_empty_axes_is_one_panel() {
        let rows = vec![row(&[("x", Value::from(1))])];
        let panels = regroup(&[], &rows).unwrap();
        assert_eq!(panels.len(), 1);
        assert!(panels[0].key.is_empty());
        assert_eq!(panels[0].rows.len(), 1);
    }

    #[test]
    fn regroup_rows_only_splits_row_major_and_strips_partition_field() {
        let axes = vec![PartitionAxis {
            field: "year".into(),
            values: vec!["2019".into(), "2020".into()],
        }];
        let rows = vec![
            row(&[("year", Value::from(2019)), ("hour", Value::from(0))]),
            row(&[("year", Value::from(2020)), ("hour", Value::from(6))]),
            row(&[("year", Value::from(2019)), ("hour", Value::from(12))]),
        ];
        let panels = regroup(&axes, &rows).unwrap();
        assert_eq!(panels.len(), 2);
        assert_eq!(panels[0].key, vec![Value::from(2019)]);
        assert_eq!(panels[0].rows.len(), 2);
        // the partition field itself is stripped from each panel's own rows
        assert!(!panels[0].rows[0].contains_key("year"));
        assert_eq!(panels[1].key, vec![Value::from(2020)]);
        assert_eq!(panels[1].rows.len(), 1);
    }

    #[test]
    fn empty_cells_come_first_and_headers_follow_the_baked_axis_order() {
        let axes = vec![PartitionAxis { field: "c".into(), values: vec!["\"x\"".into(), "\"y\"".into()] }];
        let rows = vec![row(&[("c", Value::from("y"))]), row(&[("c", Value::from("x"))])];
        let panels = regroup(&axes, &rows).unwrap();
        assert_eq!(empty_leading_cells(panels.len(), 2, 2), Ok(2));
        assert_eq!(empty_leading_cells(3, 2, 2), Ok(1));
        assert!(empty_leading_cells(5, 2, 2).is_err());
        let headers = axis_header_values(&axes[0], 0, &panels);
        assert_eq!(headers, [&Value::from("x"), &Value::from("y")], "baked order, not first appearance");
        assert!(axis_header_values(&axes[0], 0, &panels[1..]).len() == 1, "a value with no panel has no header");
    }

    #[test]
    fn regroup_sparse_grid_skips_empty_combinations() {
        let axes = vec![
            PartitionAxis {
                field: "r".into(),
                values: vec!["\"a\"".into(), "\"b\"".into()],
            },
            PartitionAxis {
                field: "c".into(),
                values: vec!["\"x\"".into(), "\"y\"".into()],
            },
        ];
        // only (a,x) and (b,y) have rows — (a,y) and (b,x) are empty combos
        let rows = vec![
            row(&[("r", Value::from("a")), ("c", Value::from("x")), ("v", Value::from(1))]),
            row(&[("r", Value::from("b")), ("c", Value::from("y")), ("v", Value::from(2))]),
        ];
        let panels = regroup(&axes, &rows).unwrap();
        assert_eq!(panels.len(), 2);
        assert_eq!(panels[0].key, vec![Value::from("a"), Value::from("x")]);
        assert_eq!(panels[1].key, vec![Value::from("b"), Value::from("y")]);
    }

    #[test]
    fn regroup_row_outside_baked_axis_errors() {
        let axes =
            vec![PartitionAxis { field: "year".into(), values: vec!["2019".into()] }];
        let rows = vec![row(&[("year", Value::from(2099))])];
        let err = regroup(&axes, &rows).unwrap_err();
        assert!(err.contains("outside baked panel_axes"), "{err}");
    }

    #[test]
    fn regroup_row_missing_field_errors() {
        let axes =
            vec![PartitionAxis { field: "year".into(), values: vec!["2019".into()] }];
        let rows = vec![row(&[("other", Value::from(1))])];
        let err = regroup(&axes, &rows).unwrap_err();
        assert!(err.contains("missing partition field"), "{err}");
    }

    #[test]
    fn a_field_is_narrowed_only_when_some_panel_holds_a_proper_subset() {
        let panel = |values: &[&str]| Panel {
            key: vec![],
            rows: values.iter().map(|v| row(&[("x", Value::from(*v))])).collect(),
        };
        // Every panel holds the full domain: nothing to narrow.
        assert!(!field_is_proper_subset_in_a_panel(&[panel(&["a", "b"]), panel(&["b", "a"])], "x"));
        // One panel holds a slice.
        assert!(field_is_proper_subset_in_a_panel(&[panel(&["a", "b"]), panel(&["b"])], "x"));
        // A single distinct value overall has no narrower state.
        assert!(!field_is_proper_subset_in_a_panel(&[panel(&["a"]), panel(&["a"])], "x"));
        // A panel with no value for the field does not count as a subset.
        assert!(!field_is_proper_subset_in_a_panel(&[panel(&["a", "b"]), panel(&[])], "x"));
    }

    #[test]
    fn panel_axis_cardinality_absent_field_is_one() {
        let axes = vec![PartitionAxis { field: "year".into(), values: vec!["2019".into()] }];
        assert_eq!(panel_axis_cardinality(&axes, None), 1);
        assert_eq!(panel_axis_cardinality(&axes, Some("missing")), 1);
        assert_eq!(panel_axis_cardinality(&axes, Some("year")), 1);
    }

    #[test]
    fn facet_panel_width_matches_documented_formula() {
        // chrome_px=120, mirror_axis_px=80
        assert_eq!(facet_panel_width(600.0, 1, false, 0.0), 480.0);
        assert_eq!(facet_panel_width(600.0, 2, false, 0.0), 240.0);
        assert_eq!(facet_panel_width(600.0, 1, true, 0.0), 400.0);
        assert_eq!(facet_panel_width(600.0, 2, false, 30.0), 210.0);
    }
}
