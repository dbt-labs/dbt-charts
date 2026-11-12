//! What every axis's labels share: how a label sits on its anchor, Vega's flush anchor, the parity
//! rule and its measure, and Vega's baseline. [`vega_space`] is the room production reserves for a
//! label, which every gutter reads while the ink stays the face's own.

use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::Bounds;

/// Where a label's anchor sits along its text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Center,
    Right,
}

/// Which edge of its text sits on a label's anchor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Baseline {
    Top,
    Middle,
    Bottom,
}

/// Vega's `labelFlush` with its default `labelFlushOffset` of 0: a label within `threshold` pixels
/// of a range end anchors on it, the nearer end winning a tie; no threshold centers every label
/// (production's `flush_edge`, with `axis.labels.flush` read through `flush_threshold`).
pub(crate) fn flush_align(x: f64, span: f64, threshold: Option<f64>) -> Align {
    let Some(threshold) = threshold else {
        return Align::Center;
    };
    let (near_start, near_end) = (x.abs(), (span - x).abs());
    if near_start < near_end && near_start <= threshold {
        Align::Left
    } else if near_end <= threshold {
        Align::Right
    } else {
        Align::Center
    }
}

/// Where Vega puts the baseline of a text whose `baseline` edge sits on `anchor`
/// (`baseline_shift`; the ink is the face's own).
pub(crate) fn vega_baseline(anchor: f64, baseline: Baseline, size: f64) -> f64 {
    let edge = match baseline {
        Baseline::Top => "top",
        Baseline::Middle => "middle",
        Baseline::Bottom => "bottom",
    };
    anchor + measure::baseline_shift(edge, size)
}

/// The vertical span Vega reserves for one line of such a text: the font size tall, its top
/// `round(0.8 fs)` above the baseline (production's `text_ink`). Space is decided on it; the ink
/// is the face's own.
pub(crate) fn vega_line(anchor: f64, baseline: Baseline, size: f64) -> (f64, f64) {
    let top = vega_baseline(anchor, baseline, size) - (0.8 * size).round();
    (top, top + size)
}

/// The extra height Vega gives each row of a label after its first (`text::line_height`).
pub(crate) fn vega_row(size: f64) -> f64 {
    size + 2.0
}

/// The box production reserves for a label of `rows` rows spanning `x1..x2`: its Vega line
/// ([`vega_line`]) plus a row height for each row after the first, turned `angle` degrees about
/// its anchor `(x, y)` as `vega_fit::Bounds::rotate` turns it. Gutters are decided on it; the
/// ink is the face's own.
pub(crate) fn vega_space(
    (x1, x2): (f64, f64),
    (x, y): (f64, f64),
    baseline: Baseline,
    size: f64,
    rows: usize,
    angle: f64,
) -> Bounds {
    let (top, bottom) = vega_line(y, baseline, size);
    let flat = Bounds {
        x1,
        y1: top,
        x2,
        y2: bottom + rows.saturating_sub(1) as f64 * vega_row(size),
    };
    vega_turn(flat, angle, (x, y))
}

/// `vega_fit::Bounds::rotate`: `b` turned `degrees` about `(x, y)`, flat when 0.
pub(crate) fn vega_turn(b: Bounds, degrees: f64, (x, y): (f64, f64)) -> Bounds {
    if degrees == 0.0 {
        return b;
    }
    let (sin, cos) = degrees.to_radians().sin_cos();
    let (dx, dy) = (x - x * cos + y * sin, y - x * sin - y * cos);
    let corners = [(b.x1, b.y1), (b.x1, b.y2), (b.x2, b.y1), (b.x2, b.y2)];
    Bounds::of_points(corners.map(|(px, py)| (cos * px - sin * py + dx, sin * px + cos * py + dy)))
        .expect("four corners")
}

/// The width Vega's parity pass judges a label's text by: production sums the raw advances of
/// the face's upright default instance in the family as authored (`FontBook::measure`), whatever
/// the painted weight and style.
pub(crate) fn parity_advance(book: &FontBook, text: &str, font: &Font) -> Result<f64, Diagnostic> {
    let upright = Font {
        weight: 400.0,
        italic: false,
        ..*font
    };
    book.raw_advance(text, &upright).map_err(diag::err_internal)
}

/// Vega's `labelOverlap: parity` over label boxes in axis order: while three or more labels remain
/// and any neighboring pair sits closer than `separation` (the larger of the pair's x and y gaps),
/// every odd-indexed survivor goes; once fewer than three remain with the axis's last label gone,
/// that last label shows in place of the last survivor.
pub(crate) fn parity_visible(ink: &[Bounds], separation: f64) -> Vec<bool> {
    let close = |a: &Bounds, b: &Bounds| {
        separation
            > (b.x1 - a.x2)
                .max(a.x1 - b.x2)
                .max(b.y1 - a.y2)
                .max(a.y1 - b.y2)
    };
    let crowded = |kept: &[usize]| kept.windows(2).any(|w| close(&ink[w[0]], &ink[w[1]]));
    let mut kept: Vec<usize> = (0..ink.len()).collect();
    if kept.len() < 3 || !crowded(&kept) {
        return vec![true; ink.len()];
    }
    while kept.len() >= 3 && crowded(&kept) {
        kept = kept.into_iter().step_by(2).collect();
    }
    let mut visible: Vec<bool> = (0..ink.len()).map(|i| kept.contains(&i)).collect();
    let last = ink.len() - 1;
    if kept.len() < 3 && !visible[last] {
        if kept.len() > 1 {
            visible[kept[kept.len() - 1]] = false;
        }
        visible[last] = true;
    }
    visible
}

/// Vega-Lite's default label anchor on a bottom axis for an angle: centered and
/// hanging at 0° and 180°, else the text's start or end on the anchor by which
/// half-turn the angle is in, its middle on the anchor within 45° of vertical.
pub(crate) fn tilted_anchor(angle: f64) -> (Align, Baseline) {
    let angle = angle.rem_euclid(360.0);
    let align = if angle % 180.0 == 0.0 {
        Align::Center
    } else if 0.0 < angle && angle < 180.0 {
        Align::Left
    } else {
        Align::Right
    };
    let baseline = if (45.0 < angle && angle < 135.0) || (225.0 < angle && angle < 315.0) {
        Baseline::Middle
    } else if angle <= 45.0 || 315.0 <= angle {
        Baseline::Top
    } else {
        Baseline::Bottom
    };
    (align, baseline)
}
