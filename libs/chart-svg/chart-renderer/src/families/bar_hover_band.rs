//! `BarHoverBandFeature`'s invisible hit-target layer
//! (`render/chart/features/bar_hover_band.py`): one `opacity="0"` bar per row,
//! anchored on the zero baseline and extended toward the datum's sign, so a
//! bar too short to hover still has a target. A bar draws a band row whether
//! or not it extends; only a near-zero one (or, on a stack, the first row of a
//! near-zero total) gets an extent.

use super::cartesian::LinearScale;
use crate::common::row_num;
use crate::contract::{aria_attr, mark_group_close, mark_group_open, ItemRole};
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::fmt::Write as _;

/// `chart_rendering.bar.hover_band_trigger_fraction` (`default_config.yml`).
const TRIGGER_FRACTION: f64 = 0.02;
/// `chart_rendering.bar.hover_band_extend_fraction` (`default_config.yml`).
const EXTEND_FRACTION: f64 = 0.05;
/// Vega-Lite's default mark color: the layer authors no color of its own.
const FILL: &str = "#4c78a8";

type Rows = [Map<String, Value>];

enum Gate {
    /// Each row extends when its own rendered extent is under the trigger.
    Each,
    /// A stacked bar: the one carrier row of each near-zero total extends.
    Carriers(HashMap<String, String>),
}

struct Row {
    aria: Option<String>,
    start: f64,
    size: f64,
    value: Option<f64>,
    carrier: bool,
}

/// The band layer of one bar panel, collected while its bars paint.
pub(crate) struct HoverBand {
    gate: Gate,
    rows: Vec<Row>,
}

impl HoverBand {
    /// The unstacked layer, `None` when no row is small enough to need one
    /// (production never declares a layer that could not extend).
    pub(crate) fn plain(rows: &Rows, measure: &str) -> Option<Self> {
        let values: Vec<f64> = rows.iter().filter_map(|row| row_num(row, measure)).map(f64::abs).collect();
        let domain_max = values.iter().copied().fold(0.0_f64, f64::max);
        let needed = domain_max > 0.0 && values.iter().any(|v| *v < domain_max * TRIGGER_FRACTION);
        needed.then(|| HoverBand { gate: Gate::Each, rows: Vec::new() })
    }

    /// The `stack: zero` layer: one carrier row (the first in data order) per
    /// category whose positive total is under the trigger share of the
    /// domain max, `None` when there is none.
    pub(crate) fn stacked(
        rows: &Rows,
        cat_field: &str,
        measure: &str,
        series_field: &str,
        stacked_domain_max: Option<f64>,
    ) -> Option<Self> {
        let mut totals: HashMap<String, f64> = HashMap::new();
        for row in rows {
            if let Some(v) = row_num(row, measure) {
                *totals.entry(crate::common::row_str(row, cat_field)).or_insert(0.0) += v.max(0.0);
            }
        }
        let domain_max = stacked_domain_max.unwrap_or_else(|| totals.values().copied().fold(0.0_f64, f64::max));
        if domain_max <= 0.0 {
            return None;
        }
        let mut carriers: HashMap<String, String> = HashMap::new();
        for row in rows {
            let cat = crate::common::row_str(row, cat_field);
            if carriers.contains_key(&cat) || totals.get(&cat).is_none_or(|t| t / domain_max >= TRIGGER_FRACTION) {
                continue;
            }
            if !matches!(row.get(series_field), None | Some(Value::Null)) {
                carriers.insert(cat, crate::common::row_str(row, series_field));
            }
        }
        (!carriers.is_empty()).then(|| HoverBand { gate: Gate::Carriers(carriers), rows: Vec::new() })
    }

    /// Record one painted datum: its tooltip label, its cross-axis start and
    /// size, and its measure value (`None` when null).
    pub(crate) fn push(
        &mut self,
        cat: &str,
        series: &str,
        aria: Option<&str>,
        start: f64,
        size: f64,
        value: Option<f64>,
    ) {
        let carrier = match &self.gate {
            Gate::Each => false,
            Gate::Carriers(carriers) => carriers.get(cat).is_some_and(|s| s == series),
        };
        self.rows.push(Row { aria: aria.map(str::to_owned), start, size, value, carrier });
    }

    /// Paint the layer. `vertical` bars grow up from the baseline; `extent` is
    /// the plot's pixel size along the measure axis.
    pub(crate) fn emit(&self, out: &mut String, vertical: bool, scale: &LinearScale, extent: f64) {
        let baseline = scale.position(0.0);
        let band = extent * EXTEND_FRACTION;
        let trigger = extent * TRIGGER_FRACTION;
        mark_group_open(out, "rect", "hover_band_marks");
        for row in &self.rows {
            let grows = match (&self.gate, row.value) {
                (Gate::Each, Some(v)) => (scale.position(v) - baseline).abs() < trigger,
                (Gate::Each, None) => false,
                (Gate::Carriers(_), _) => row.carrier,
            };
            let sign = if row.value.is_some_and(|v| v < 0.0) { -1.0 } else { 1.0 };
            // Up (vertical) and right (horizontal) are the positive directions.
            let reach = if !grows { 0.0 } else if vertical { -sign * band } else { sign * band };
            let (lo, len) = (baseline.min(baseline + reach), reach.abs());
            let label = aria_attr(row.aria.as_deref(), ItemRole::Bar);
            let d = if vertical {
                format!("M{:.2},{lo:.2}h{:.2}v{len:.2}h-{:.2}Z", row.start, row.size, row.size)
            } else {
                format!("M{lo:.2},{:.2}h{len:.2}v{:.2}h-{len:.2}Z", row.start, row.size)
            };
            let _ = write!(out, "<path d=\"{d}\" fill=\"{FILL}\" opacity=\"0\"{label}/>");
        }
        mark_group_close(out);
    }
}
