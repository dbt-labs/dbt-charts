//! d3-geo 3.1.1 `src/clip/circle.js`: clips to the small circle of a given angular radius about the origin
//! (`projection.clipAngle`).

use super::{Clip, ClipGeometry, ClipLine, READ_OF_UNDEFINED};
use crate::geo::cartesian::{
    cartesian, cartesian_add_in_place, cartesian_cross, cartesian_dot, cartesian_scale, spherical,
};
use crate::geo::circle::circle_stream;
use crate::geo::math::{EPSILON, PI, RADIANS};
use crate::geo::point_equal::point_equal;
use crate::geo::stream::GeoStream;

#[derive(Clone, Copy)]
pub struct CircleClip {
    radius: f64,
    cr: f64,
    delta: f64,
    small_radius: bool,
    not_hemisphere: bool,
}

pub fn clip_circle<S: GeoStream>(radius: f64, sink: S) -> Clip<S, CircleClip> {
    let cr = libm::cos(radius);
    let geometry = CircleClip {
        radius,
        cr,
        delta: 2.0 * RADIANS,
        small_radius: cr > 0.0,
        not_hemisphere: cr.abs() > EPSILON,
    };
    Clip::new(geometry, sink)
}

/// What intersecting a great circle with the clip circle found.
enum Cut {
    /// The two points are on a plane through the circle's axis (d3: "Two polar points").
    Parallel,
    /// The great circle misses the clip circle.
    Miss,
    /// The first intersection `q`, and what the second one needs.
    Hit { q: [f64; 2], a: [f64; 3], u: [f64; 3], w: f64, uu: f64, t: f64 },
}

impl CircleClip {
    fn visible(&self, lambda: f64, phi: f64) -> bool {
        libm::cos(lambda) * libm::cos(phi) > self.cr
    }

    /// Intersects the great circle between a and b with the clip circle.
    fn cut(&self, a: [f64; 2], b: [f64; 2]) -> Cut {
        let pa = cartesian(a[0], a[1]);
        let pb = cartesian(b[0], b[1]);

        // We have two planes, n1.p = d1 and n2.p = d2.
        // Find intersection line p(t) = c1 n1 + c2 n2 + t (n1 ⨯ n2).
        let n1 = [1.0, 0.0, 0.0]; // normal
        let n2 = cartesian_cross(pa, pb);
        let n2n2 = cartesian_dot(n2, n2);
        let n1n2 = n2[0]; // cartesianDot(n1, n2)
        let determinant = n2n2 - n1n2 * n1n2;

        if determinant == 0.0 || determinant.is_nan() {
            return Cut::Parallel;
        }

        let c1 = self.cr * n2n2 / determinant;
        let c2 = -self.cr * n1n2 / determinant;
        let n1xn2 = cartesian_cross(n1, n2);
        let mut aa = cartesian_scale(n1, c1);
        let bb = cartesian_scale(n2, c2);
        cartesian_add_in_place(&mut aa, bb);

        // Solve |p(t)|^2 = 1.
        let u = n1xn2;
        let w = cartesian_dot(aa, u);
        let uu = cartesian_dot(u, u);
        let t2 = w * w - uu * (cartesian_dot(aa, aa) - 1.0);

        if t2 < 0.0 {
            return Cut::Miss;
        }

        let t = libm::sqrt(t2);
        let mut q = cartesian_scale(u, (-w - t) / uu);
        cartesian_add_in_place(&mut q, aa);
        Cut::Hit { q: spherical(q), a: aa, u, w, uu, t }
    }

    /// The point where the great circle between a and b leaves or enters the clip circle.
    fn intersect(&self, a: [f64; 2], b: [f64; 2]) -> Option<[f64; 2]> {
        match self.cut(a, b) {
            Cut::Parallel => Some(a),
            Cut::Miss => None,
            Cut::Hit { q, .. } => Some(q),
        }
    }

    /// Both points where the great circle between a and b crosses the clip circle, if the segment between a
    /// and b contains them.
    fn intersect_two(&self, a: [f64; 2], b: [f64; 2]) -> Option<[[f64; 2]; 2]> {
        let Cut::Hit { q, a: aa, u, w, uu, t } = self.cut(a, b) else {
            return None;
        };

        let (mut lambda0, mut lambda1, mut phi0, mut phi1) = (a[0], b[0], a[1], b[1]);

        if lambda1 < lambda0 {
            std::mem::swap(&mut lambda0, &mut lambda1);
        }

        let delta = lambda1 - lambda0;
        let polar = (delta - PI).abs() < EPSILON;
        let meridian = polar || delta < EPSILON;

        if !polar && phi1 < phi0 {
            std::mem::swap(&mut phi0, &mut phi1);
        }

        // Check that the first point is between a and b.
        let between = if meridian {
            if polar {
                (phi0 + phi1 > 0.0) ^ (q[1] < if (q[0] - lambda0).abs() < EPSILON { phi0 } else { phi1 })
            } else {
                phi0 <= q[1] && q[1] <= phi1
            }
        } else {
            (delta > PI) ^ (lambda0 <= q[0] && q[0] <= lambda1)
        };
        if between {
            let mut q1 = cartesian_scale(u, (-w + t) / uu);
            cartesian_add_in_place(&mut q1, aa);
            Some([q, spherical(q1)])
        } else {
            None
        }
    }

    /// Generates a 4-bit vector representing the location of a point relative to
    /// the small circle's bounding box.
    fn code(&self, lambda: f64, phi: f64) -> u8 {
        let r = if self.small_radius { self.radius } else { PI - self.radius };
        let mut code = 0;
        if lambda < -r {
            code |= 1; // left
        } else if lambda > r {
            code |= 2; // right
        }
        if phi < -r {
            code |= 4; // below
        } else if phi > r {
            code |= 8; // above
        }
        code
    }
}

impl ClipGeometry for CircleClip {
    type Line = CircleLine;

    fn point_visible(&self, lambda: f64, phi: f64) -> bool {
        self.visible(lambda, phi)
    }

    fn new_line(&self) -> CircleLine {
        CircleLine { circle: *self, point0: None, c0: 0, v0: false, v00: false, clean: 0, error: None }
    }

    fn interpolate(&self, from_to: Option<([f64; 2], [f64; 2])>, direction: i32, stream: &mut impl GeoStream) {
        circle_stream(stream, self.radius, self.delta, direction, from_to);
    }

    fn start(&self) -> [f64; 2] {
        if self.small_radius {
            [0.0, -self.radius]
        } else {
            [-PI, self.radius - PI]
        }
    }
}

/// Takes a line and cuts into visible segments.
pub struct CircleLine {
    circle: CircleClip,
    point0: Option<[f64; 2]>, // previous point
    c0: u8,                   // code for previous point
    v0: bool,                 // visibility of previous point
    v00: bool,                // visibility of first point
    clean: u8,                // no intersections
    error: Option<&'static str>, // d3 throws; the line records it
}

impl ClipLine for CircleLine {
    fn line_start(&mut self, _stream: &mut impl GeoStream) {
        self.v00 = false;
        self.v0 = false;
        self.clean = 1;
    }

    fn point(&mut self, stream: &mut impl GeoStream, lambda: f64, phi: f64) {
        let circle = self.circle;
        let point1 = [lambda, phi];
        let v = circle.visible(lambda, phi);
        let c = if circle.small_radius {
            if v {
                0
            } else {
                circle.code(lambda, phi)
            }
        } else if v {
            circle.code(lambda + if lambda < 0.0 { PI } else { -PI }, phi)
        } else {
            0
        };
        if self.point0.is_none() {
            self.v00 = v;
            self.v0 = v;
            if v {
                stream.line_start();
            }
        }
        if v != self.v0 {
            let point0 = self.point0.expect("the first point of a line is as visible as itself");
            self.clean = 0;
            if v {
                // outside going in
                stream.line_start();
                let Some(point2) = circle.intersect(point1, point0) else {
                    self.error = Some(READ_OF_UNDEFINED);
                    return;
                };
                stream.point(point2[0], point2[1], None);
                self.point0 = Some(point2);
            } else {
                // inside going out
                let Some(point2) = circle.intersect(point0, point1) else {
                    self.error = Some(READ_OF_UNDEFINED);
                    return;
                };
                stream.point(point2[0], point2[1], Some(2.0));
                stream.line_end();
                self.point0 = Some(point2);
            }
        } else if let Some(point0) = self.point0 {
            if circle.not_hemisphere && (circle.small_radius ^ v) {
                // If the codes for two points are different, or are both zero,
                // and there this segment intersects with the small circle.
                if c & self.c0 == 0 {
                    if let Some(t) = circle.intersect_two(point1, point0) {
                        self.clean = 0;
                        if circle.small_radius {
                            stream.line_start();
                            stream.point(t[0][0], t[0][1], None);
                            stream.point(t[1][0], t[1][1], None);
                            stream.line_end();
                        } else {
                            stream.point(t[1][0], t[1][1], None);
                            stream.line_end();
                            stream.line_start();
                            stream.point(t[0][0], t[0][1], Some(3.0));
                        }
                    }
                }
            }
        }
        if v && self.point0.is_none_or(|point0| !point_equal(point0, point1)) {
            stream.point(point1[0], point1[1], None);
        }
        self.point0 = Some(point1);
        self.v0 = v;
        self.c0 = c;
    }

    fn line_end(&mut self, stream: &mut impl GeoStream) {
        if self.v0 {
            stream.line_end();
        }
        self.point0 = None;
    }

    /// Rejoin first and last segments if there were intersections and the first
    /// and last points were visible.
    fn clean(&self) -> u8 {
        self.clean | (u8::from(self.v00 && self.v0) << 1)
    }

    fn error(&self) -> Option<&str> {
        self.error
    }
}
