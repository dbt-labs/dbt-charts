//! d3-geo 3.1.1: `geoArea`, `geoBounds` and `geoCentroid` as `GeoStream` sinks (spherical geometry in degrees,
//! no projection), with d3-array's exact `Adder`. The pipeline modules (`stream`, `rotation`, `compose`,
//! `transform`) transliterate their upstream files of the same name.

pub mod cartesian;
pub mod circle;
pub mod clip;
pub mod compose;
pub mod math;
pub mod path;
pub mod point_equal;
pub mod polygon_contains;
pub mod projection;
pub mod rotation;
pub mod stream;
pub mod transform;

use cartesian::{cartesian, cartesian_cross};
use crate::geojson::GeoJson;
use math::{asin, DEGREES, EPSILON, EPSILON2, QUARTER_PI, RADIANS, TAU};
use crate::js_sort::js_sort;
use stream::{geo_stream, GeoStream};

/// d3-array's `Adder`: Shewchuk's exact running sum.
#[derive(Default, Clone)]
pub(crate) struct Adder {
    partials: Vec<f64>,
}

impl Adder {
    pub(crate) fn add(&mut self, mut x: f64) {
        let mut kept = Vec::with_capacity(self.partials.len() + 1);
        for &y in self.partials.iter().take(32) {
            let hi = x + y;
            let lo = if x.abs() < y.abs() { x - (hi - y) } else { y - (hi - x) };
            if lo != 0.0 {
                kept.push(lo);
            }
            x = hi;
        }
        kept.push(x);
        self.partials = kept;
    }

    pub(crate) fn value(&self) -> f64 {
        let p = &self.partials;
        let mut n = p.len();
        let (mut hi, mut lo) = (0.0, 0.0);
        if n > 0 {
            n -= 1;
            hi = p[n];
            while n > 0 {
                let x = hi;
                n -= 1;
                let y = p[n];
                hi = x + y;
                lo = y - (hi - x);
                if lo != 0.0 {
                    break;
                }
            }
            if n > 0 && ((lo < 0.0 && p[n - 1] < 0.0) || (lo > 0.0 && p[n - 1] > 0.0)) {
                let y = lo * 2.0;
                let x = hi + y;
                if y == x - hi {
                    hi = x;
                }
            }
        }
        hi
    }
}

fn hypot3(x: f64, y: f64, z: f64) -> f64 {
    crate::js_math::hypot(&[x, y, z])
}

/// d3-geo's `area` stream: the sum of the polygons' spherical excess. d3 declares the ring-start state
/// (`lambda00`, ...) without a value, so an empty ring reads `undefined`: NaN here.
pub struct Area {
    sum: Adder,
    ring: Adder,
    in_polygon: bool,
    ring_open: bool,
    first: bool,
    lambda00: f64,
    phi00: f64,
    lambda0: f64,
    cos_phi0: f64,
    sin_phi0: f64,
}

impl Default for Area {
    fn default() -> Self {
        Self {
            sum: Adder::default(),
            ring: Adder::default(),
            in_polygon: false,
            ring_open: false,
            first: false,
            lambda00: f64::NAN,
            phi00: f64::NAN,
            lambda0: f64::NAN,
            cos_phi0: f64::NAN,
            sin_phi0: f64::NAN,
        }
    }
}

impl Area {
    fn add_point(&mut self, lambda: f64, phi: f64) {
        let lambda = lambda * RADIANS;
        let phi = phi * RADIANS / 2.0 + QUARTER_PI;
        let d_lambda = lambda - self.lambda0;
        let sd_lambda = if d_lambda >= 0.0 { 1.0 } else { -1.0 };
        let ad_lambda = sd_lambda * d_lambda;
        let (cos_phi, sin_phi) = (libm::cos(phi), libm::sin(phi));
        let k = self.sin_phi0 * sin_phi;
        let u = self.cos_phi0 * cos_phi + k * libm::cos(ad_lambda);
        let v = k * sd_lambda * libm::sin(ad_lambda);
        self.ring.add(libm::atan2(v, u));
        self.lambda0 = lambda;
        self.cos_phi0 = cos_phi;
        self.sin_phi0 = sin_phi;
    }

    /// The spherical area of the polygons streamed so far, in steradians.
    pub fn result(&self) -> f64 {
        self.sum.value() * 2.0
    }
}

impl GeoStream for Area {
    fn point(&mut self, lambda: f64, phi: f64, _z: Option<f64>) {
        if !(self.in_polygon && self.ring_open) {
            return;
        }
        if self.first {
            self.first = false;
            self.lambda00 = lambda;
            self.phi00 = phi;
            self.lambda0 = lambda * RADIANS;
            let phi = phi * RADIANS / 2.0 + QUARTER_PI;
            self.cos_phi0 = libm::cos(phi);
            self.sin_phi0 = libm::sin(phi);
        } else {
            self.add_point(lambda, phi);
        }
    }

    fn line_start(&mut self) {
        if self.in_polygon {
            self.ring_open = true;
            self.first = true;
        }
    }

    fn line_end(&mut self) {
        if self.in_polygon && self.ring_open {
            self.add_point(self.lambda00, self.phi00);
            self.ring_open = false;
        }
    }

    fn polygon_start(&mut self) {
        self.ring = Adder::default();
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        let ring = self.ring.value();
        self.sum.add(if ring < 0.0 { TAU + ring } else { ring });
        self.in_polygon = false;
        self.ring_open = false;
    }

    fn sphere(&mut self) {
        self.sum.add(TAU);
    }
}

/// `geoArea(object)`: the spherical area of the object's polygons, in steradians.
pub fn area(object: &GeoJson) -> f64 {
    let mut stream = Area::default();
    geo_stream(object, &mut stream);
    stream.result()
}

/// d3-geo's `bounds` stream.
pub struct Bounds {
    lambda0: f64,
    phi0: f64,
    lambda1: f64,
    phi1: f64,
    lambda2: f64,
    lambda00: f64,
    phi00: f64,
    p0: Option<[f64; 3]>,
    delta_sum: Adder,
    ranges: Vec<[f64; 2]>,
    in_polygon: bool,
    in_ring: bool,
    in_line: bool,
    area: Area,
    /// The JavaScript error d3-geo throws on an unbalanced stream. The first one stops the sink, as the
    /// throw stops the stream.
    error: Option<String>,
}

impl Default for Bounds {
    fn default() -> Self {
        Self {
            lambda0: f64::INFINITY,
            phi0: f64::INFINITY,
            lambda1: f64::NEG_INFINITY,
            phi1: f64::NEG_INFINITY,
            lambda2: f64::NAN,
            lambda00: f64::NAN,
            phi00: f64::NAN,
            p0: None,
            delta_sum: Adder::default(),
            ranges: Vec::new(),
            in_polygon: false,
            in_ring: false,
            in_line: false,
            area: Area::default(),
            error: None,
        }
    }
}

impl Bounds {

    fn new_range(&mut self, lambda: f64) {
        self.lambda0 = lambda;
        self.lambda1 = lambda;
        self.ranges.push([lambda, lambda]);
    }

    fn plain_point(&mut self, lambda: f64, phi: f64) {
        self.new_range(lambda);
        if phi < self.phi0 {
            self.phi0 = phi;
        }
        if phi > self.phi1 {
            self.phi1 = phi;
        }
    }

    fn line_point(&mut self, lambda: f64, phi: f64) {
        let p = cartesian(lambda * RADIANS, phi * RADIANS);
        if let Some(p0) = self.p0 {
            let normal = cartesian_cross(p0, p);
            let equatorial = [normal[1], -normal[0], 0.0];
            let mut inflection = cartesian_cross(equatorial, normal);
            let l = libm::sqrt(inflection[0] * inflection[0] + inflection[1] * inflection[1] + inflection[2] * inflection[2]);
            inflection = [inflection[0] / l, inflection[1] / l, inflection[2] / l];
            let (inf_lambda, inf_phi) = (libm::atan2(inflection[1], inflection[0]), asin(inflection[2]));
            let delta = lambda - self.lambda2;
            let sign = if delta > 0.0 { 1.0 } else { -1.0 };
            let mut lambdai = inf_lambda * DEGREES * sign;
            let antimeridian = delta.abs() > 180.0;
            if antimeridian ^ (sign * self.lambda2 < lambdai && lambdai < sign * lambda) {
                let phii = inf_phi * DEGREES;
                if phii > self.phi1 {
                    self.phi1 = phii;
                }
            } else {
                lambdai = (lambdai + 360.0) % 360.0 - 180.0;
                if antimeridian ^ (sign * self.lambda2 < lambdai && lambdai < sign * lambda) {
                    let phii = -inf_phi * DEGREES;
                    if phii < self.phi0 {
                        self.phi0 = phii;
                    }
                } else {
                    if phi < self.phi0 {
                        self.phi0 = phi;
                    }
                    if phi > self.phi1 {
                        self.phi1 = phi;
                    }
                }
            }
            if antimeridian {
                if lambda < self.lambda2 {
                    if angle(self.lambda0, lambda) > angle(self.lambda0, self.lambda1) {
                        self.lambda1 = lambda;
                    }
                } else if angle(lambda, self.lambda1) > angle(self.lambda0, self.lambda1) {
                    self.lambda0 = lambda;
                }
            } else if self.lambda1 >= self.lambda0 {
                if lambda < self.lambda0 {
                    self.lambda0 = lambda;
                }
                if lambda > self.lambda1 {
                    self.lambda1 = lambda;
                }
            } else if lambda > self.lambda2 {
                if angle(self.lambda0, lambda) > angle(self.lambda0, self.lambda1) {
                    self.lambda1 = lambda;
                }
            } else if angle(lambda, self.lambda1) > angle(self.lambda0, self.lambda1) {
                self.lambda0 = lambda;
            }
        } else {
            self.new_range(lambda);
        }
        if phi < self.phi0 {
            self.phi0 = phi;
        }
        if phi > self.phi1 {
            self.phi1 = phi;
        }
        self.p0 = Some(p);
        self.lambda2 = lambda;
    }

    fn close_range(&mut self) {
        let (l0, l1) = (self.lambda0, self.lambda1);
        match self.ranges.last_mut() {
            Some(range) => *range = [l0, l1],
            None => self.error = Some("Cannot set properties of null (setting '0')".to_string()),
        }
    }

    fn ring_point(&mut self, lambda: f64, phi: f64) {
        if self.p0.is_some() {
            let delta = lambda - self.lambda2;
            self.delta_sum.add(if delta.abs() > 180.0 { delta + if delta > 0.0 { 360.0 } else { -360.0 } } else { delta });
        } else {
            self.lambda00 = lambda;
            self.phi00 = phi;
        }
        self.area.point(lambda, phi, None);
        self.line_point(lambda, phi);
    }


    /// The `[[west, south], [east, north]]` box of the streamed objects, NaN where there are none; `Err` is
    /// the JavaScript error d3-geo throws on an unbalanced stream, or the refusal to order 64 or more
    /// ranges when one of them has a NaN longitude (V8's multi-run merge is not ported).
    pub fn result(mut self) -> Result<[[f64; 2]; 2], String> {
        if let Some(message) = self.error {
            return Err(message);
        }
        if !self.ranges.is_empty() {
            let mut ranges = std::mem::take(&mut self.ranges);
            // `ranges.sort(compareRanges)` with `a[0] - b[0]`. Without NaN that is a total order, where any
            // stable sort agrees with V8's. A NaN difference breaks the order, so only then is V8's
            // algorithm ported (`js_sort`, which refuses 64 or more ranges).
            if ranges.iter().any(|r| r[0].is_nan()) {
                js_sort(&mut ranges, |a, b| a[0] - b[0])?;
            } else {
                ranges.sort_by(|a, b| a[0].partial_cmp(&b[0]).expect("NaN ranges take the js_sort branch"));
            }
            let mut merged = vec![ranges[0]];
            for b in ranges.iter().skip(1) {
                let a = merged.last_mut().expect("merged starts non-empty");
                if range_contains(a, b[0]) || range_contains(a, b[1]) {
                    if angle(a[0], b[1]) > angle(a[0], a[1]) {
                        a[1] = b[1];
                    }
                    if angle(b[0], a[1]) > angle(a[0], a[1]) {
                        a[0] = b[0];
                    }
                } else {
                    merged.push(*b);
                }
            }
            let n = merged.len() - 1;
            let mut a = merged[n];
            let mut delta_max = f64::NEG_INFINITY;
            for b in &merged {
                let delta = angle(a[1], b[0]);
                if delta > delta_max {
                    delta_max = delta;
                    self.lambda0 = b[0];
                    self.lambda1 = a[1];
                }
                a = *b;
            }
        }
        Ok(if self.lambda0 == f64::INFINITY || self.phi0 == f64::INFINITY {
            [[f64::NAN, f64::NAN], [f64::NAN, f64::NAN]]
        } else {
            [[self.lambda0, self.phi0], [self.lambda1, self.phi1]]
        })
    }
}

impl GeoStream for Bounds {
    fn point(&mut self, lambda: f64, phi: f64, _z: Option<f64>) {
        if self.error.is_some() {
            return;
        }
        if self.in_ring {
            self.ring_point(lambda, phi);
        } else if self.in_line {
            self.line_point(lambda, phi);
        } else {
            self.plain_point(lambda, phi);
        }
    }

    fn line_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        if self.in_polygon {
            self.in_ring = true;
            self.area.line_start();
        } else {
            self.in_line = true;
        }
    }

    fn line_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        if self.in_ring {
            self.ring_point(self.lambda00, self.phi00);
            self.area.line_end();
            if self.delta_sum.value().abs() > EPSILON {
                self.lambda1 = 180.0;
                self.lambda0 = -180.0;
            }
            self.close_range();
            self.p0 = None;
            self.in_ring = false;
        } else {
            self.close_range();
            self.in_line = false;
            self.p0 = None;
        }
    }

    fn polygon_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.in_polygon = true;
        self.delta_sum = Adder::default();
        self.area.polygon_start();
    }

    fn polygon_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.area.polygon_end();
        self.in_polygon = false;
        if self.area.ring.value() < 0.0 {
            self.lambda1 = 180.0;
            self.lambda0 = -180.0;
            self.phi1 = 90.0;
            self.phi0 = -90.0;
        } else if self.delta_sum.value() > EPSILON {
            self.phi1 = 90.0;
        } else if self.delta_sum.value() < -EPSILON {
            self.phi0 = -90.0;
        }
        self.close_range();
    }

    fn sphere(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.lambda1 = 180.0;
        self.lambda0 = -180.0;
        self.phi1 = 90.0;
        self.phi0 = -90.0;
    }
}

/// The left-right distance between two longitudes: `(l1 - l0 + 360) % 360`, except the distance between ±180 is 360.
fn angle(lambda0: f64, lambda1: f64) -> f64 {
    let d = lambda1 - lambda0;
    if d < 0.0 { d + 360.0 } else { d }
}

fn range_contains(range: &[f64; 2], x: f64) -> bool {
    if range[0] <= range[1] { range[0] <= x && x <= range[1] } else { x < range[0] || range[1] < x }
}

/// `geoBounds(object)`.
pub fn bounds(object: &GeoJson) -> Result<[[f64; 2]; 2], String> {
    let mut stream = Bounds::default();
    geo_stream(object, &mut stream);
    stream.result()
}

/// d3-geo's `centroid` stream.
pub struct Centroid {
    w0: f64,
    w1: f64,
    p0: [f64; 3],
    p1: [f64; 3],
    x2: Adder,
    y2: Adder,
    z2: Adder,
    in_polygon: bool,
    in_ring: bool,
    in_line: bool,
    first: bool,
    lambda00: f64,
    phi00: f64,
    prev: [f64; 3],
}

impl Default for Centroid {
    fn default() -> Self {
        Self {
            w0: 0.0,
            w1: 0.0,
            p0: [0.0; 3],
            p1: [0.0; 3],
            x2: Adder::default(),
            y2: Adder::default(),
            z2: Adder::default(),
            in_polygon: false,
            in_ring: false,
            in_line: false,
            first: false,
            lambda00: f64::NAN,
            phi00: f64::NAN,
            prev: [f64::NAN; 3],
        }
    }
}

impl Centroid {
    fn mean_point(&mut self, x: f64, y: f64, z: f64) {
        self.w0 += 1.0;
        self.p0[0] += (x - self.p0[0]) / self.w0;
        self.p0[1] += (y - self.p0[1]) / self.w0;
        self.p0[2] += (z - self.p0[2]) / self.w0;
    }

    fn line_to(&mut self, v: [f64; 3]) {
        let [x0, y0, z0] = self.prev;
        let w = {
            let a = y0 * v[2] - z0 * v[1];
            let b = z0 * v[0] - x0 * v[2];
            let c = x0 * v[1] - y0 * v[0];
            libm::atan2(libm::sqrt(a * a + b * b + c * c), x0 * v[0] + y0 * v[1] + z0 * v[2])
        };
        self.w1 += w;
        self.p1[0] += w * (x0 + v[0]);
        self.p1[1] += w * (y0 + v[1]);
        self.p1[2] += w * (z0 + v[2]);
        self.prev = v;
        self.mean_point(v[0], v[1], v[2]);
    }

    fn ring_to(&mut self, v: [f64; 3]) {
        let [x0, y0, z0] = self.prev;
        let (cx, cy, cz) = (y0 * v[2] - z0 * v[1], z0 * v[0] - x0 * v[2], x0 * v[1] - y0 * v[0]);
        let m = hypot3(cx, cy, cz);
        let w = asin(m);
        let k = if m != 0.0 { -w / m } else { m };
        self.x2.add(k * cx);
        self.y2.add(k * cy);
        self.z2.add(k * cz);
        self.w1 += w;
        self.p1[0] += w * (x0 + v[0]);
        self.p1[1] += w * (y0 + v[1]);
        self.p1[2] += w * (z0 + v[2]);
        self.prev = v;
        self.mean_point(v[0], v[1], v[2]);
    }

    /// The `[longitude, latitude]` centroid of the streamed objects, NaN where there is none.
    pub fn result(&self) -> [f64; 2] {
        let (mut x, mut y, mut z) = (self.x2.value(), self.y2.value(), self.z2.value());
        let mut m = hypot3(x, y, z);
        if m < EPSILON2 {
            (x, y, z) = (self.p1[0], self.p1[1], self.p1[2]);
            if self.w1 < EPSILON {
                (x, y, z) = (self.p0[0], self.p0[1], self.p0[2]);
            }
            m = hypot3(x, y, z);
            if m < EPSILON2 {
                return [f64::NAN, f64::NAN];
            }
        }
        [libm::atan2(y, x) * DEGREES, asin(z / m) * DEGREES]
    }
}

impl GeoStream for Centroid {
    fn point(&mut self, lambda: f64, phi: f64, _z: Option<f64>) {
        let v = cartesian(lambda * RADIANS, phi * RADIANS);
        if self.in_ring {
            if self.first {
                self.first = false;
                self.lambda00 = lambda;
                self.phi00 = phi;
                self.prev = v;
                self.mean_point(v[0], v[1], v[2]);
            } else {
                self.ring_to(v);
            }
        } else if self.in_line {
            if self.first {
                self.first = false;
                self.prev = v;
                self.mean_point(v[0], v[1], v[2]);
            } else {
                self.line_to(v);
            }
        } else {
            self.mean_point(v[0], v[1], v[2]);
        }
    }

    fn line_start(&mut self) {
        if self.in_polygon {
            self.in_ring = true;
        } else {
            self.in_line = true;
        }
        self.first = true;
    }

    fn line_end(&mut self) {
        if self.in_ring {
            let v = cartesian(self.lambda00 * RADIANS, self.phi00 * RADIANS);
            self.ring_to(v);
            self.in_ring = false;
        } else {
            self.in_line = false;
        }
    }

    fn polygon_start(&mut self) {
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        self.in_polygon = false;
    }

    fn sphere(&mut self) {}
}

/// `geoCentroid(object)`.
pub fn centroid(object: &GeoJson) -> [f64; 2] {
    let mut stream = Centroid::default();
    geo_stream(object, &mut stream);
    stream.result()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geojson::Geometry;

    #[test]
    fn a_sphere_has_the_whole_sky_for_an_area_and_every_degree_for_bounds() {
        let sphere = GeoJson::Geometry(Geometry::Sphere);
        assert!((area(&sphere) - 4.0 * std::f64::consts::PI).abs() < 1e-12);
        assert_eq!(bounds(&sphere).unwrap(), [[-180.0, -90.0], [180.0, 90.0]]);
        let nothing = GeoJson::Geometry(Geometry::GeometryCollection { geometries: vec![] });
        assert!(bounds(&nothing).unwrap().iter().flatten().all(|n| n.is_nan()));
    }

    #[test]
    fn nan_longitudes_among_many_ranges_refuse_rather_than_guess_v8s_order() {
        let mut coordinates = vec![vec![]];
        coordinates.extend((0..70).map(|i| vec![f64::from(i), 0.0]));
        let object = GeoJson::Geometry(Geometry::MultiPoint { coordinates });
        let err = bounds(&object).unwrap_err();
        assert!(err.contains("multi-run merge"), "{err}");
    }
}
