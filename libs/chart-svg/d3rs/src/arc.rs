//! Port of d3-shape's arc() generator.
//! Reference: https://github.com/d3/d3-shape/blob/main/src/arc.js
//! Faithful line-for-line port of the geometry; the JS accessor plumbing is
//! replaced by a plain struct.

// arc.js guards with `!(x > epsilon)`, which is also true for NaN; `x <= epsilon`
// is not. The negated comparisons stay as written upstream.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use crate::path::{Path, WITH_PATH_DIGITS};
use std::f64::consts::{FRAC_PI_2, PI, TAU};

const EPSILON: f64 = 1e-12;

pub struct Arc {
    pub inner_radius: f64,
    pub outer_radius: f64,
    pub corner_radius: f64,
    pub pad_radius: Option<f64>,
    pub start_angle: f64,
    pub end_angle: f64,
    pub pad_angle: f64,
}

struct CornerTangent {
    cx: f64,
    cy: f64,
    x01: f64,
    y01: f64,
    x11: f64,
    y11: f64,
}

// Mirrors d3-shape arc.js `intersect(x0, y0, x1, y1, x2, y2, x3, y3)` line for line.
#[allow(clippy::too_many_arguments)]
fn intersect(
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    x2: f64,
    y2: f64,
    x3: f64,
    y3: f64,
) -> Option<[f64; 2]> {
    let x10 = x1 - x0;
    let y10 = y1 - y0;
    let x32 = x3 - x2;
    let y32 = y3 - y2;
    let mut t = y32 * x10 - x32 * y10;
    if t * t < EPSILON {
        return None;
    }
    t = (x32 * (y0 - y2) - y32 * (x0 - x2)) / t;
    Some([x0 + t * x10, y0 + t * y10])
}

// Compute perpendicular offset line of length rc.
fn corner_tangents(x0: f64, y0: f64, x1: f64, y1: f64, r1: f64, rc: f64, cw: bool) -> CornerTangent {
    let x01 = x0 - x1;
    let y01 = y0 - y1;
    let lo = (if cw { rc } else { -rc }) / (x01 * x01 + y01 * y01).sqrt();
    let ox = lo * y01;
    let oy = -lo * x01;
    let x11 = x0 + ox;
    let y11 = y0 + oy;
    let x10 = x1 + ox;
    let y10 = y1 + oy;
    let x00 = (x11 + x10) / 2.0;
    let y00 = (y11 + y10) / 2.0;
    let dx = x10 - x11;
    let dy = y10 - y11;
    let d2 = dx * dx + dy * dy;
    let r = r1 - rc;
    let dd = x11 * y10 - x10 * y11;
    let d = (if dy < 0.0 { -1.0 } else { 1.0 }) * (r * r * d2 - dd * dd).max(0.0).sqrt();
    let mut cx0 = (dd * dy - dx * d) / d2;
    let mut cy0 = (-dd * dx - dy * d) / d2;
    let cx1 = (dd * dy + dx * d) / d2;
    let cy1 = (-dd * dx + dy * d) / d2;
    let dx0 = cx0 - x00;
    let dy0 = cy0 - y00;
    let dx1 = cx1 - x00;
    let dy1 = cy1 - y00;

    // Pick the closer of the two intersection points.
    if dx0 * dx0 + dy0 * dy0 > dx1 * dx1 + dy1 * dy1 {
        cx0 = cx1;
        cy0 = cy1;
    }

    CornerTangent {
        cx: cx0,
        cy: cy0,
        x01: -ox,
        y01: -oy,
        x11: cx0 * (r1 / r - 1.0),
        y11: cy0 * (r1 / r - 1.0),
    }
}

impl Arc {
    pub fn path(&self) -> String {
        let mut ctx = Path::rounded(WITH_PATH_DIGITS);
        let mut r0 = self.inner_radius;
        let mut r1 = self.outer_radius;
        let a0 = self.start_angle - FRAC_PI_2;
        let a1 = self.end_angle - FRAC_PI_2;
        let da = (a1 - a0).abs();
        let cw = a1 > a0;

        // Ensure that the outer radius is always larger than the inner radius.
        if r1 < r0 {
            std::mem::swap(&mut r0, &mut r1);
        }

        if !(r1 > EPSILON) {
            // Is it a point?
            ctx.move_to(0.0, 0.0);
        } else if da > TAU - EPSILON {
            // Or is it a circle or annulus?
            ctx.move_to(r1 * a0.cos(), r1 * a0.sin());
            ctx.arc(0.0, 0.0, r1, a0, a1, !cw);
            if r0 > EPSILON {
                ctx.move_to(r0 * a1.cos(), r0 * a1.sin());
                ctx.arc(0.0, 0.0, r0, a1, a0, cw);
            }
        } else {
            // Or is it a circular or annular sector?
            let mut a01 = a0;
            let mut a11 = a1;
            let mut a00 = a0;
            let mut a10 = a1;
            let mut da0 = da;
            let mut da1 = da;
            let ap = self.pad_angle / 2.0;
            let rp = if ap > EPSILON {
                self.pad_radius
                    .unwrap_or_else(|| (r0 * r0 + r1 * r1).sqrt())
            } else {
                0.0
            };
            let rc = ((r1 - r0).abs() / 2.0).min(self.corner_radius).max(0.0);
            let mut rc0 = rc;
            let mut rc1 = rc;

            // Apply padding? Note that since r1 ≥ r0, da1 ≥ da0.
            if rp > EPSILON {
                let mut p0 = (rp / r0 * ap.sin()).asin();
                let mut p1 = (rp / r1 * ap.sin()).asin();
                da0 -= p0 * 2.0;
                if da0 > EPSILON {
                    p0 *= if cw { 1.0 } else { -1.0 };
                    a00 += p0;
                    a10 -= p0;
                } else {
                    da0 = 0.0;
                    a00 = (a0 + a1) / 2.0;
                    a10 = a00;
                }
                da1 -= p1 * 2.0;
                if da1 > EPSILON {
                    p1 *= if cw { 1.0 } else { -1.0 };
                    a01 += p1;
                    a11 -= p1;
                } else {
                    da1 = 0.0;
                    a01 = (a0 + a1) / 2.0;
                    a11 = a01;
                }
            }

            let x01 = r1 * a01.cos();
            let y01 = r1 * a01.sin();
            let x10 = r0 * a10.cos();
            let y10 = r0 * a10.sin();

            // Apply rounded corners?
            if rc > EPSILON {
                let x11 = r1 * a11.cos();
                let y11 = r1 * a11.sin();
                let x00 = r0 * a00.cos();
                let y00 = r0 * a00.sin();

                // Restrict the corner radius according to the sector angle.
                if da < PI {
                    if let Some(oc) = intersect(x01, y01, x00, y00, x11, y11, x10, y10) {
                        let ax = x01 - oc[0];
                        let ay = y01 - oc[1];
                        let bx = x11 - oc[0];
                        let by = y11 - oc[1];
                        let kc = 1.0
                            / (((ax * bx + ay * by)
                                / ((ax * ax + ay * ay).sqrt() * (bx * bx + by * by).sqrt()))
                            .acos()
                                / 2.0)
                                .sin();
                        let lc = (oc[0] * oc[0] + oc[1] * oc[1]).sqrt();
                        rc0 = rc.min((r0 - lc) / (kc - 1.0));
                        rc1 = rc.min((r1 - lc) / (kc + 1.0));
                    } else {
                        rc0 = 0.0;
                        rc1 = 0.0;
                    }
                }
            }

            if !(da1 > EPSILON) {
                // Is the sector collapsed to a line?
                ctx.move_to(x01, y01);
            } else if rc1 > EPSILON {
                // Does the sector's outer ring have rounded corners?
                let t0 = corner_tangents(x00_or(r0, a00), y00_or_s(r0, a00), x01, y01, r1, rc1, cw);
                let t1 = corner_tangents(x11_or(r1, a11), y11_or_s(r1, a11), x10, y10, r1, rc1, cw);

                ctx.move_to(t0.cx + t0.x01, t0.cy + t0.y01);

                if rc1 < rc {
                    // Have the corners merged?
                    ctx.arc(
                        t0.cx,
                        t0.cy,
                        rc1,
                        t0.y01.atan2(t0.x01),
                        t1.y01.atan2(t1.x01),
                        !cw,
                    );
                } else {
                    // Otherwise, draw the two corners and the ring.
                    ctx.arc(
                        t0.cx,
                        t0.cy,
                        rc1,
                        t0.y01.atan2(t0.x01),
                        t0.y11.atan2(t0.x11),
                        !cw,
                    );
                    ctx.arc(
                        0.0,
                        0.0,
                        r1,
                        (t0.cy + t0.y11).atan2(t0.cx + t0.x11),
                        (t1.cy + t1.y11).atan2(t1.cx + t1.x11),
                        !cw,
                    );
                    ctx.arc(
                        t1.cx,
                        t1.cy,
                        rc1,
                        t1.y11.atan2(t1.x11),
                        t1.y01.atan2(t1.x01),
                        !cw,
                    );
                }
            } else {
                // Or is the outer ring just a circular arc?
                ctx.move_to(x01, y01);
                ctx.arc(0.0, 0.0, r1, a01, a11, !cw);
            }

            if !(r0 > EPSILON) || !(da0 > EPSILON) {
                // Is there no inner ring, and it's a circular sector?
                ctx.line_to(x10, y10);
            } else if rc0 > EPSILON {
                // Does the sector's inner ring (or point) have rounded corners?
                let t0 = corner_tangents(x10, y10, x11_or(r1, a11), y11_or_s(r1, a11), r0, -rc0, cw);
                let t1 = corner_tangents(x01, y01, x00_or(r0, a00), y00_or_s(r0, a00), r0, -rc0, cw);

                ctx.line_to(t0.cx + t0.x01, t0.cy + t0.y01);

                if rc0 < rc {
                    ctx.arc(
                        t0.cx,
                        t0.cy,
                        rc0,
                        t0.y01.atan2(t0.x01),
                        t1.y01.atan2(t1.x01),
                        !cw,
                    );
                } else {
                    ctx.arc(
                        t0.cx,
                        t0.cy,
                        rc0,
                        t0.y01.atan2(t0.x01),
                        t0.y11.atan2(t0.x11),
                        !cw,
                    );
                    ctx.arc(
                        0.0,
                        0.0,
                        r0,
                        (t0.cy + t0.y11).atan2(t0.cx + t0.x11),
                        (t1.cy + t1.y11).atan2(t1.cx + t1.x11),
                        cw,
                    );
                    ctx.arc(
                        t1.cx,
                        t1.cy,
                        rc0,
                        t1.y11.atan2(t1.x11),
                        t1.y01.atan2(t1.x01),
                        !cw,
                    );
                }
            } else {
                // Or is the inner ring just a circular arc?
                ctx.arc(0.0, 0.0, r0, a10, a00, cw);
            }
        }

        ctx.close_path();
        ctx.into_string()
    }

}

// Small helpers so the ported body reads like the source (x00 = r0*cos(a00), etc.).
fn x00_or(r0: f64, a00: f64) -> f64 {
    r0 * a00.cos()
}
fn y00_or_s(r0: f64, a00: f64) -> f64 {
    r0 * a00.sin()
}
fn x11_or(r1: f64, a11: f64) -> f64 {
    r1 * a11.cos()
}
fn y11_or_s(r1: f64, a11: f64) -> f64 {
    r1 * a11.sin()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The `d` Vega prints for the first wedge of a 40/30/20/10 pie, outer
    /// radius 148.95 and corner radius 3 (vl-convert's SVG for the sector
    /// fixture): d3-shape's `arc` builds a `Path` rounded to 3 digits. Vega
    /// walks the wedge from its end angle back; the coordinates are the same.
    const VEGA_WEDGE: &str = "M85.769,118.051A3,3,0,0,0,90.009,118.678A148.95,148.95,0,0,0,\
                              3.062,-148.919A3,3,0,0,0,0,-145.919L0,0Z";

    fn decimals(d: &str) -> Vec<String> {
        let mut tokens: Vec<String> = d
            .split(|c: char| !(c.is_ascii_digit() || c == '.' || c == '-'))
            .filter(|t| t.contains('.'))
            .map(str::to_string)
            .collect();
        tokens.sort();
        tokens
    }

    #[test]
    fn a_wedge_prints_the_coordinates_vega_prints() {
        let arc = Arc {
            inner_radius: 0.0,
            outer_radius: 148.95,
            corner_radius: 3.0,
            pad_radius: None,
            start_angle: 0.0,
            end_angle: 0.4 * TAU,
            pad_angle: 0.0,
        };
        let d = arc.path();
        assert_eq!(decimals(&d), decimals(VEGA_WEDGE), "{d}");
    }
}
