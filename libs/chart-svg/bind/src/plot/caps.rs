//! Whether a value axis's gridlines reach the plot's bottom edge, which decides the other axis's tick
//! stubs (`axis_x.ticks.visible: auto`, `_y_gridline_caps_bottom`).

use crate::bound::{Ladder, ValueDomain, Zero};

/// Whether the value axis's gridlines cap the plot's bottom edge (`_y_gridline_caps_bottom`). False when the grid is hidden; true when
/// the floor is unknown; else whether the first rung of the baked ladder is the
/// floor. A `BySize` ladder bakes none, so its floor
/// is unknown and the verdict is true: production's, not the rung layout picks.
/// `rail_may_discard_domain` skips a baked bottom bound (a line or area whose
/// endpoint rail merges the scales).
pub fn caps_bottom(
    domain: &ValueDomain,
    grid_visible: bool,
    rail_may_discard_domain: bool,
) -> bool {
    if !grid_visible {
        return false;
    }
    let first = match &domain.ladder {
        Ladder::Authored(rungs) => rungs.first().map(|r| r.value),
        Ladder::Fixed(fixed) => fixed.rungs().first().map(|r| r.value),
        Ladder::BySize { .. } | Ladder::Counts | Ladder::None => None,
    };
    let Some(first) = first else { return true };
    let anchored = matches!(domain.zero, Zero::Anchored | Zero::Pinned(true));
    let floor = if let Some(authored) = domain.authored {
        Some(if authored.reversed {
            authored.extent.hi()
        } else {
            authored.extent.lo()
        })
    } else if let (Some(pin), false) = (domain.min, rail_may_discard_domain) {
        Some(pin.value)
    } else if anchored {
        // An authored ladder says nothing about the domain: the floor is zero.
        Some(match &domain.ladder {
            Ladder::Fixed(_) => first,
            _ => 0.0,
        })
    } else if matches!(domain.ladder, Ladder::Authored(_)) {
        None
    } else {
        Some(first)
    };
    let Some(floor) = floor else { return true };
    (first - floor).abs() <= 1e-9_f64.max(1e-9 * first.abs().max(floor.abs()))
}
