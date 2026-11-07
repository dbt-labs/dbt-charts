//! The plot-box negotiation: what is left of a chart's cell for its plot once the
//! padding, the title, the reserved strips and the axis gutters take theirs.
//!
//! Strips (legend, endpoint rail, support table) are reserved at their own size.
//! Gutters depend on the plot they surround (a tick count by plot height, a
//! label tilt by plot width, an end label's overhang by its band), so the plot is
//! the fixed point of `plot = area − gutters(plot)`, with no rounding of a gutter
//! (quirk class Q2a: no bounded pass count, no ceil'd overhangs).

use diagnostic::Diagnostic;

use crate::geom::{Insets, Rect, Size};
use crate::title::TitleMetrics;

/// The plot is within this of its fixed point: half of print's 0.01 px grid, so the fixed point
/// could not draw differently.
const SETTLED: f64 = 0.005;

/// A gutter that is a contraction gives back at most this share of the plot's growth per step. The
/// reviewed slice 2a example hangs a label from a tick at a tenth of its domain and gives back 0.9;
/// 0.95 bounds it with margin. It is not a bound on every gutter: a label wider than the plot gives
/// back everything (slope 1) and a flush switch jumps. Those do not contract; [`negotiate`] gives
/// them production's three rounded passes.
const MAX_SLOPE: f64 = 0.95;

/// The plot has settled once no edge moves by more than this: a contraction of slope
/// [`MAX_SLOPE`] still has `step · slope / (1 − slope)` to go, which this keeps within [`SETTLED`].
const SETTLED_STEP: f64 = SETTLED * (1.0 - MAX_SLOPE) / MAX_SLOPE;

/// Production's padding passes (`AUTOSIZE_PADDING_PASSES`).
const PRODUCTION_PASSES: usize = 3;

/// The largest distance a first candidate can be from its fixed point: a card's width.
const MAX_OFFSET: f64 = 10_000.0;

/// More steps than it takes the error to shrink from [`MAX_OFFSET`] to [`SETTLED_STEP`] at
/// [`MAX_SLOPE`], and the gutters are not settling: a law bug, not an input.
fn max_steps() -> usize {
    ((SETTLED_STEP / MAX_OFFSET).ln() / MAX_SLOPE.ln()).ceil() as usize
}

/// The negotiated plot and the gutters around it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fit {
    pub plot: Rect,
    pub gutters: Insets,
}

/// The area the plot and its gutters share: the cell less `padding`, the title's
/// band and the reserved `strips`.
pub fn body(cell: Rect, padding: Insets, title: Option<&TitleMetrics>, strips: Insets) -> Rect {
    let inner = cell.inset(padding);
    let band = title.map_or(0.0, TitleMetrics::height);
    let below_title = inner.inset(Insets {
        top: band,
        ..Insets::ZERO
    });
    below_title.inset(strips)
}

/// The plot in `area`. `gutters` answers, for a candidate plot size, how far the
/// axes' ink reaches past each edge of the plot; ink inside an edge takes no
/// gutter. The first candidate is the whole area. A gutter wider than the area
/// leaves a plot of zero extent on that axis, drawn by production's passes below.
///
/// A gutter that jumps (a label flushing to an edge once it is within a pixel of it) or hands
/// back more than the plot's growth (a label wider than the plot) can have no fixed point. On
/// that path only, production's bounded rule is the law: three passes from the whole area, each
/// rounding the overhangs it measures up to whole pixels, the third drawn with its own margins
/// (`vega_fit::fit_passes`, `AUTOSIZE_PADDING_PASSES`). Plots that settle keep the unrounded
/// fixed point (quirk class Q2a).
pub fn negotiate(
    area: Rect,
    mut gutters: impl FnMut(Size) -> Result<Insets, Diagnostic>,
) -> Result<Fit, Diagnostic> {
    let mut plot = area;
    for _ in 0..max_steps() {
        let reach = gutters(plot.size())?;
        let around = Insets {
            top: reach.top.max(0.0),
            right: reach.right.max(0.0),
            bottom: reach.bottom.max(0.0),
            left: reach.left.max(0.0),
        };
        // Gutters wider than the area clamp the plot to nothing, and a clamp settles any slope: that
        // is no fixed point. Skipping the settle here is intended: production's three passes are
        // the law in that case.
        if around.left + around.right > area.width || around.top + around.bottom > area.height {
            break;
        }
        let next = area.inset(around);
        let moved = [
            next.x - plot.x,
            next.y - plot.y,
            next.width - plot.width,
            next.height - plot.height,
        ];
        if moved.iter().all(|d| d.abs() <= SETTLED_STEP) {
            return Ok(Fit {
                plot: next,
                gutters: around,
            });
        }
        plot = next;
    }
    let mut plot = area;
    let mut margins = Insets::ZERO;
    for _ in 0..PRODUCTION_PASSES {
        let reach = gutters(plot.size())?;
        margins = Insets {
            top: reach.top.max(0.0).ceil(),
            right: reach.right.max(0.0).ceil(),
            bottom: reach.bottom.max(0.0).ceil(),
            left: reach.left.max(0.0).ceil(),
        };
        plot = area.inset(margins);
    }
    Ok(Fit {
        plot,
        gutters: margins,
    })
}
