//! The largest wheel whose ink fits a box: a pie's radius and center solved
//! directly, in closed form, from the width of each drawn label.
//!
//! A pie lays out as a wheel of outer radius `r` centered on `(cx, cy)` with a
//! label anchored `offset` beyond the rim along its mid-angle:
//! `(cx + sin·(r + offset), cy − cos·(r + offset) + dy)`. The label's ink is a
//! fixed rectangle around that anchor, so every edge of every item bounds
//! `cx`, `cy` and `r` linearly, and the x edges never mention `cy` or the y
//! edges `cx`. Each axis is therefore a set of lower and upper bounds on its
//! center, `cx ≥ l + a·r` and `cx ≤ u + b·r`; a lower and an upper bound
//! coexist when `l + a·r ≤ u + b·r`, which bounds `r` alone. The largest `r`
//! is the tightest of those pair bounds over both axes and the cap; the center
//! is then the point of the surviving interval closest to the box center.
//!
//! No iteration and no tolerance hides an overflow: a box no positive radius
//! fits is an error.
//!
//! The size stage (`chart-renderer`'s `pie_layout`) decides a pie's mode with
//! this solve and the renderer draws with the same call, so the ring decided
//! on is the ring drawn.

/// An ink rectangle.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Ink {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
}

/// A label anchored beyond the rim. `sin`/`cos` give the mid-angle's
/// direction (clockwise from twelve o'clock), `dy` shifts the anchor's y, and
/// `ink` is the painted rectangle relative to the anchor.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Callout {
    pub sin: f64,
    pub cos: f64,
    pub offset: f64,
    pub dy: f64,
    pub ink: Ink,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Wheel {
    pub cx: f64,
    pub cy: f64,
    pub r: f64,
}

/// Why no wheel was solved: `NoRoom` is a verdict on the box (the ink leaves no
/// positive radius), `Inconsistent` the solve disagreeing with its own bounds,
/// which no box can cause.
#[derive(Debug, PartialEq)]
pub enum SolveError {
    NoRoom(String),
    Inconsistent(String),
}

/// `cx ≥ l + a·r`.
type Lower = (f64, f64);
/// `cx ≤ u + b·r`.
type Upper = (f64, f64);

/// The bounds one axis puts on `r`: `(hi, lo)`, or an error when a pair of
/// edges cannot both hold at any radius.
fn pair_bounds(lowers: &[Lower], uppers: &[Upper]) -> Result<(f64, f64), SolveError> {
    let (mut hi, mut lo) = (f64::INFINITY, f64::NEG_INFINITY);
    for &(l, a) in lowers {
        for &(u, b) in uppers {
            let d = a - b;
            let rhs = u - l;
            if d > 0.0 {
                hi = hi.min(rhs / d);
            } else if d < 0.0 {
                lo = lo.max(rhs / d);
            } else if rhs < 0.0 {
                return Err(SolveError::NoRoom("an item does not fit the box".to_string()));
            }
        }
    }
    Ok((hi, lo))
}

fn x_edges(box_x: (f64, f64), pad: f64, callouts: &[Callout], centered: &[Ink]) -> (Vec<Lower>, Vec<Upper>) {
    let (x0, x1) = box_x;
    let mut lowers = vec![(x0 + pad, 1.0)];
    let mut uppers = vec![(x1 - pad, -1.0)];
    // Ink fixed on the center does not grow with the radius.
    for c in centered {
        lowers.push((x0 - c.x1, 0.0));
        uppers.push((x1 - c.x2, 0.0));
    }
    for c in callouts {
        lowers.push((x0 - c.ink.x1 - c.sin * c.offset, -c.sin));
        uppers.push((x1 - c.ink.x2 - c.sin * c.offset, -c.sin));
    }
    (lowers, uppers)
}

fn y_edges(box_y: (f64, f64), pad: f64, callouts: &[Callout], centered: &[Ink]) -> (Vec<Lower>, Vec<Upper>) {
    let (y0, y1) = box_y;
    let mut lowers = vec![(y0 + pad, 1.0)];
    let mut uppers = vec![(y1 - pad, -1.0)];
    for c in centered {
        lowers.push((y0 - c.y1, 0.0));
        uppers.push((y1 - c.y2, 0.0));
    }
    for c in callouts {
        lowers.push((y0 - c.ink.y1 - c.dy + c.cos * c.offset, c.cos));
        uppers.push((y1 - c.ink.y2 - c.dy + c.cos * c.offset, c.cos));
    }
    (lowers, uppers)
}

/// The center interval an axis leaves at radius `r`, and the point of it
/// closest to `prefer`.
fn center(lowers: &[Lower], uppers: &[Upper], r: f64, prefer: f64) -> Result<f64, SolveError> {
    let lo = lowers.iter().fold(f64::NEG_INFINITY, |m, &(l, a)| m.max(l + a * r));
    let hi = uppers.iter().fold(f64::INFINITY, |m, &(u, b)| m.min(u + b * r));
    // The radius is a pair bound's own quotient, so the binding pair meets at
    // equality up to the last bit of that division.
    let slack = 1e-9 * (1.0 + lo.abs().max(hi.abs()));
    if lo > hi + slack {
        return Err(SolveError::Inconsistent("no center fits the radius".to_string()));
    }
    Ok(prefer.clamp(lo.min(hi), hi.max(lo)))
}

fn radius(cap: f64, axes: &[(f64, f64)]) -> Result<f64, SolveError> {
    let hi = axes.iter().fold(cap, |m, &(hi, _)| m.min(hi));
    let lo = axes.iter().fold(0.0_f64, |m, &(_, lo)| m.max(lo));
    if hi <= 0.0 || hi < lo {
        return Err(SolveError::NoRoom("no positive radius fits the box".to_string()));
    }
    Ok(hi)
}

/// The largest wheel inside `box_` (`x1, y1, x2, y2`) with outer radius at
/// most `cap`: the wheel itself (grown `pad` for its stroke) and each callout
/// all inside the box, and the `centered` ink (the center total, relative to
/// the wheel's center) too, whatever the radius.
pub fn solve(box_: Ink, cap: f64, pad: f64, callouts: &[Callout], centered: &[Ink]) -> Result<Wheel, SolveError> {
    let (xl, xu) = x_edges((box_.x1, box_.x2), pad, callouts, centered);
    let (yl, yu) = y_edges((box_.y1, box_.y2), pad, callouts, centered);
    let r = radius(cap, &[pair_bounds(&xl, &xu)?, pair_bounds(&yl, &yu)?])?;
    Ok(Wheel {
        cx: center(&xl, &xu, r, (box_.x1 + box_.x2) / 2.0)?,
        cy: center(&yl, &yu, r, (box_.y1 + box_.y2) / 2.0)?,
        r,
    })
}

/// The radius at which a hole of fraction `inner_fraction` of it just clears the
/// `centered` ink plus `margin`: the circle around the ink's far corners must
/// fit inside the inner ring. `None` for a pie with no hole.
pub fn min_radius_for_hole(centered: &[Ink], margin: f64, inner_fraction: f64) -> Option<f64> {
    if inner_fraction <= 0.0 || centered.is_empty() {
        return None;
    }
    let reach = centered
        .iter()
        .flat_map(|c| [(c.x1, c.y1), (c.x1, c.y2), (c.x2, c.y1), (c.x2, c.y2)])
        .fold(0.0_f64, |m, (x, y)| m.max(x.hypot(y)));
    Some((reach + margin) / inner_fraction)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A label's painted rectangle around its anchor, `text_ink`'s box with
    /// `baseline: top`: `width` wide, one `font_size` tall and `lines - 1` more
    /// `line_height`s, starting `round(0.79·size) − round(0.8·size)` below the
    /// anchor; a label on the right of the wheel starts at the anchor, one on the
    /// left ends there. A label in the top half hangs its whole block above the
    /// anchor (`dy`).
    fn callout(sin: f64, cos: f64, offset: f64, width: f64, font_size: f64, lines: usize, line_height: f64) -> Callout {
        let top = (0.79 * font_size).round() - (0.8 * font_size).round();
        let (x1, x2) = if sin >= 0.0 { (0.0, width) } else { (-width, 0.0) };
        let block = (lines as f64 - 1.0) * line_height;
        let dy = if cos >= 0.0 { -(lines as f64) * line_height } else { 0.0 };
        Callout { sin, cos, offset, dy, ink: Ink { x1, y1: top, x2, y2: top + font_size + block } }
    }

    const BOX: Ink = Ink { x1: 0.0, y1: 0.0, x2: 400.0, y2: 300.0 };

    #[test]
    fn ink_fixed_on_the_center_keeps_the_center_inside_the_box() {
        let total = Ink { x1: -150.0, y1: -10.0, x2: 150.0, y2: 10.0 };
        let w = solve(BOX, 100.0, 0.0, &[], &[total]).unwrap();
        assert_eq!(w.cx, 200.0);
        let wider = Ink { x1: -250.0, y1: -10.0, x2: 250.0, y2: 10.0 };
        assert!(solve(BOX, 100.0, 0.0, &[], &[wider]).is_err());
        let label = callout(1.0, 0.0, 0.0, 120.0, 10.0, 1, 12.0);
        let pushed = solve(BOX, 100.0, 0.0, &[label], &[total]).unwrap();
        assert!(pushed.cx >= 150.0 && pushed.cx <= 250.0, "{pushed:?}");
    }

    /// The radius, not only the center, answers to ink fixed on the center: a
    /// label on the right edge and a total 120 either side of the center leave
    /// 300 - 100 - 120 for the wheel, where the label alone leaves 100.
    #[test]
    fn the_center_ink_binds_the_radius_when_a_label_shares_its_axis() {
        let tall = Ink { x1: 0.0, y1: 0.0, x2: 300.0, y2: 600.0 };
        let label = callout(1.0, 0.0, 0.0, 100.0, 10.0, 1, 12.0);
        let total = Ink { x1: -120.0, y1: -10.0, x2: 120.0, y2: 10.0 };
        let alone = solve(tall, f64::INFINITY, 0.0, &[label], &[]).unwrap();
        let with_total = solve(tall, f64::INFINITY, 0.0, &[label], &[total]).unwrap();
        assert_eq!(alone.r, 100.0);
        assert_eq!(with_total.r, 80.0);
    }

    #[test]
    fn a_box_with_no_room_is_a_no_room_verdict() {
        let wide = callout(1.0, 0.0, 0.0, 900.0, 10.0, 1, 12.0);
        assert!(matches!(solve(BOX, 100.0, 0.0, &[wide], &[]), Err(SolveError::NoRoom(_))));
    }

    /// A bound that forces the radius above the maximum is no room, not a wheel
    /// at the maximum (which the center check would then call inconsistent).
    #[test]
    fn a_minimum_radius_above_the_maximum_is_no_room() {
        assert!(matches!(radius(100.0, &[(f64::INFINITY, 150.0)]), Err(SolveError::NoRoom(_))));
        assert_eq!(radius(100.0, &[(f64::INFINITY, 50.0)]), Ok(100.0));
    }

    #[test]
    fn a_bare_wheel_takes_the_cap_and_the_box_center() {
        let w = solve(BOX, 100.0, 0.0, &[], &[]).unwrap();
        assert_eq!(w, Wheel { cx: 200.0, cy: 150.0, r: 100.0 });
    }

    #[test]
    fn a_bare_wheel_without_a_cap_fills_the_shorter_side() {
        let w = solve(BOX, f64::INFINITY, 2.0, &[], &[]).unwrap();
        assert_eq!((w.cy, w.r), (150.0, 148.0));
    }

    #[test]
    fn a_label_on_the_right_pushes_the_wheel_left_and_every_edge_stays_inside() {
        let label = callout(1.0, 0.0, 6.0, 120.0, 14.0, 1, 14.0);
        let w = solve(BOX, f64::INFINITY, 0.0, &[label], &[]).unwrap();
        assert!(w.cx - w.r >= -1e-9 && w.cx + w.r + 6.0 + 120.0 <= 400.0 + 1e-9, "{w:?}");
        // The radius is maximal: the label's right edge meets the box edge.
        assert!((w.cx + w.r + 6.0 + 120.0 - 400.0).abs() < 1e-9, "{w:?}");
    }

    #[test]
    fn labels_wider_than_the_box_have_no_wheel() {
        let wide = callout(1.0, 0.0, 6.0, 500.0, 14.0, 1, 14.0);
        assert!(solve(BOX, 100.0, 0.0, &[wide], &[]).is_err());
        let (l, r) = (callout(-1.0, 0.0, 6.0, 200.0, 14.0, 1, 14.0), callout(1.0, 0.0, 6.0, 200.0, 14.0, 1, 14.0));
        assert!(solve(BOX, 100.0, 0.0, &[l, r], &[]).is_err());
    }

    #[test]
    fn a_hole_needs_the_circle_around_its_total() {
        let total = [Ink { x1: -30.0, y1: -18.0, x2: 30.0, y2: 0.0 }, Ink { x1: -20.0, y1: 2.0, x2: 20.0, y2: 13.0 }];
        let need = (30.0_f64.hypot(18.0) + 4.0) / 0.6;
        assert_eq!(min_radius_for_hole(&total, 4.0, 0.6), Some(need));
        assert_eq!(min_radius_for_hole(&total, 4.0, 0.0), None);
        assert_eq!(min_radius_for_hole(&[], 4.0, 0.6), None);
    }

    const TALL: Ink = Ink { x1: 0.0, y1: 0.0, x2: 400.0, y2: 2000.0 };

    fn side(sin: f64, width: f64) -> Callout {
        callout(sin, 0.0, 6.0, width, 14.0, 1, 14.0)
    }

    #[test]
    fn each_side_reserves_only_its_own_widest_label() {
        // Left 60 wide, right 120 wide: the left edge bounds the left label and
        // the right edge the right one, `r = (400 - 6 - 120 - 6 - 60) / 2`.
        // Charging the widest to both sides would give 74.
        let w = solve(TALL, f64::INFINITY, 0.0, &[side(-1.0, 60.0), side(1.0, 120.0)], &[]).unwrap();
        assert_eq!(w.r, 104.0);
        assert_eq!(w.cx, 170.0);
    }

    #[test]
    fn a_side_with_no_label_reserves_nothing() {
        let w = solve(TALL, f64::INFINITY, 0.0, &[side(1.0, 100.0)], &[]).unwrap();
        assert_eq!(w.r, (400.0 - 6.0 - 100.0) / 2.0);
        assert_eq!(w.cx - w.r, 0.0);
    }

    #[test]
    fn a_starved_side_takes_the_wheel_down_with_it() {
        let w = solve(TALL, f64::INFINITY, 0.0, &[side(-1.0, 250.0), side(1.0, 10.0)], &[]).unwrap();
        assert_eq!(w.r, (400.0 - 6.0 - 250.0 - 6.0 - 10.0) / 2.0);
    }

    /// Whether some center on a 0.25px grid holds the wheel and every callout at
    /// radius `r`, checked in coordinates and not through the solve's own pair
    /// bounds.
    fn some_center_fits(b: Ink, pad: f64, callouts: &[Callout], centered: &[Ink], r: f64) -> bool {
        let mut cx = b.x1 + pad + r;
        while cx <= b.x2 - pad - r {
            let mut cy = b.y1 + pad + r;
            while cy <= b.y2 - pad - r {
                let total_fits = centered.iter().all(|c| {
                    cx + c.x1 >= b.x1 && cx + c.x2 <= b.x2 && cy + c.y1 >= b.y1 && cy + c.y2 <= b.y2
                });
                let fits = total_fits && callouts.iter().all(|c| {
                    let (ax, ay) = (cx + c.sin * (r + c.offset), cy - c.cos * (r + c.offset) + c.dy);
                    ax + c.ink.x1 >= b.x1 && ax + c.ink.x2 <= b.x2 && ay + c.ink.y1 >= b.y1 && ay + c.ink.y2 <= b.y2
                });
                if fits {
                    return true;
                }
                cy += 0.25;
            }
            cx += 0.25;
        }
        false
    }

    #[test]
    fn the_solved_wheel_fits_and_half_a_pixel_more_does_not() {
        // A deterministic spread of wheels: 2 to 8 labels around the rim, widths
        // from 30 to 150, one to three lines, in boxes from 340 to 640 wide.
        let mut seed = 0x2545_f491_4f6c_dd1d_u64;
        let mut next = move |lo: f64, hi: f64| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            lo + (seed >> 11) as f64 / (1_u64 << 53) as f64 * (hi - lo)
        };
        let mut maximal = 0;
        for _ in 0..40 {
            let (w, h) = (next(340.0, 640.0), next(260.0, 520.0));
            let b = Ink { x1: 0.0, y1: 0.0, x2: w, y2: h };
            let n = next(2.0, 9.0) as usize;
            let mut angle = next(0.0, 1.0);
            let callouts: Vec<Callout> = (0..n)
                .map(|_| {
                    angle += next(0.3, 1.0) * std::f64::consts::TAU / n as f64;
                    callout(angle.sin(), angle.cos(), 6.0, next(30.0, 150.0), 14.0, next(1.0, 4.0) as usize, 14.0)
                })
                .collect();
            // Every other box also carries a center total, a rect fixed on the center.
            let half = next(10.0, 110.0);
            let total = Ink { x1: -half, y1: -next(6.0, 20.0), x2: half, y2: next(6.0, 20.0) };
            let centered: &[Ink] = if maximal % 2 == 0 { &[] } else { &[total] };
            let Ok(wheel) = solve(b, f64::INFINITY, 1.5, &callouts, centered) else { continue };
            assert!(some_center_fits(b, 1.5, &callouts, centered, wheel.r - 1.0), "{wheel:?} is not feasible a pixel inside");
            assert!(!some_center_fits(b, 1.5, &callouts, centered, wheel.r + 0.5), "{wheel:?} grows half a pixel");
            maximal += 1;
        }
        assert!(maximal > 25, "{maximal} solvable wheels");
    }
}
