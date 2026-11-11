//! WARN-RESTATED-INFERRED / -FORMAT for a chart's own axis keys, read off the axis ladders the
//! family bind already walks: each own leaf is judged by the same walk with its rung dropped, so
//! neither the ladders nor the chart are built again per key. A chart whose own tier authors no axis
//! slot costs nothing. The warnings ride `Plane.diagnostics` to `bound::Board.diagnostics`, which the
//! engine's report carries.

use diagnostic::{diag, restated_text, Diagnostic};

use crate::style::axis::AxisLadder;
use crate::style::Tiers;

const SOURCE: &str = "what the renderer picks for this chart's data";

/// The own axis keys of a chart that the other tiers already answer. A chart whose own tier
/// authors no axis slot has none to restate and costs nothing.
pub fn axes(tiers: &Tiers<'_>, ladders: [&AxisLadder<'_>; 2], public_id: &str) -> Vec<Diagnostic> {
    if !tiers.own.sets_axis() {
        return Vec::new();
    }
    ladders
        .into_iter()
        .flat_map(AxisLadder::restated)
        .map(|r| {
            let path = format!("charts.{public_id}.style.{}.{}", r.slot, r.leaf);
            let value = restated_text(&r.value);
            if r.leaf == "labels.format" {
                diag::warn_restated_format(path, value, SOURCE)
            } else {
                diag::warn_restated_inferred(path, value, SOURCE)
            }
            .with_sink_param("why", "renderer picks it".into())
        })
        .collect()
}
