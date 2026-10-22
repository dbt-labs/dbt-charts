//! d3-geo 3.1.1 `src/clip/antimeridian.js`: cuts lines and polygons where they cross the antimeridian.

use super::{Clip, ClipGeometry, ClipLine};
use crate::geo::math::{EPSILON, HALF_PI, PI};
use crate::geo::stream::GeoStream;

pub struct Antimeridian;

pub fn clip_antimeridian<S: GeoStream>(sink: S) -> Clip<S, Antimeridian> {
    Clip::new(Antimeridian, sink)
}

impl ClipGeometry for Antimeridian {
    type Line = AntimeridianLine;

    fn point_visible(&self, _lambda: f64, _phi: f64) -> bool {
        true
    }

    fn new_line(&self) -> AntimeridianLine {
        AntimeridianLine { lambda0: f64::NAN, phi0: f64::NAN, sign0: f64::NAN, clean: 0 }
    }

    fn interpolate(&self, from_to: Option<([f64; 2], [f64; 2])>, direction: i32, stream: &mut impl GeoStream) {
        let direction = f64::from(direction);
        match from_to {
            None => {
                let phi = direction * HALF_PI;
                stream.point(-PI, phi, None);
                stream.point(0.0, phi, None);
                stream.point(PI, phi, None);
                stream.point(PI, 0.0, None);
                stream.point(PI, -phi, None);
                stream.point(0.0, -phi, None);
                stream.point(-PI, -phi, None);
                stream.point(-PI, 0.0, None);
                stream.point(-PI, phi, None);
            }
            Some((from, to)) if (from[0] - to[0]).abs() > EPSILON => {
                let lambda = if from[0] < to[0] { PI } else { -PI };
                let phi = direction * lambda / 2.0;
                stream.point(-lambda, phi, None);
                stream.point(0.0, phi, None);
                stream.point(lambda, phi, None);
            }
            Some((_, to)) => stream.point(to[0], to[1], None),
        }
    }

    fn start(&self) -> [f64; 2] {
        [-PI, -HALF_PI]
    }
}

/// Takes a line and cuts into visible segments.
pub struct AntimeridianLine {
    lambda0: f64,
    phi0: f64,
    sign0: f64,
    clean: u8,
}

impl ClipLine for AntimeridianLine {
    fn line_start(&mut self, stream: &mut impl GeoStream) {
        stream.line_start();
        self.clean = 1;
    }

    fn point(&mut self, stream: &mut impl GeoStream, lambda1: f64, phi1: f64) {
        let mut lambda1 = lambda1;
        let sign1 = if lambda1 > 0.0 { PI } else { -PI };
        let delta = (lambda1 - self.lambda0).abs();
        if (delta - PI).abs() < EPSILON {
            // line crosses a pole
            self.phi0 = if (self.phi0 + phi1) / 2.0 > 0.0 { HALF_PI } else { -HALF_PI };
            stream.point(self.lambda0, self.phi0, None);
            stream.point(self.sign0, self.phi0, None);
            stream.line_end();
            stream.line_start();
            stream.point(sign1, self.phi0, None);
            stream.point(lambda1, self.phi0, None);
            self.clean = 0;
        } else if self.sign0 != sign1 && delta >= PI {
            // line crosses antimeridian
            if (self.lambda0 - self.sign0).abs() < EPSILON {
                self.lambda0 -= self.sign0 * EPSILON; // handle degeneracies
            }
            if (lambda1 - sign1).abs() < EPSILON {
                lambda1 -= sign1 * EPSILON;
            }
            self.phi0 = clip_antimeridian_intersect(self.lambda0, self.phi0, lambda1, phi1);
            stream.point(self.sign0, self.phi0, None);
            stream.line_end();
            stream.line_start();
            stream.point(sign1, self.phi0, None);
            self.clean = 0;
        }
        self.lambda0 = lambda1;
        self.phi0 = phi1;
        stream.point(lambda1, phi1, None);
        self.sign0 = sign1;
    }

    fn line_end(&mut self, stream: &mut impl GeoStream) {
        stream.line_end();
        self.lambda0 = f64::NAN;
        self.phi0 = f64::NAN;
    }

    /// If intersections, rejoin first and last segments.
    fn clean(&self) -> u8 {
        2 - self.clean
    }
}

fn clip_antimeridian_intersect(lambda0: f64, phi0: f64, lambda1: f64, phi1: f64) -> f64 {
    let sin_lambda0_lambda1 = libm::sin(lambda0 - lambda1);
    if sin_lambda0_lambda1.abs() > EPSILON {
        let cos_phi0 = libm::cos(phi0);
        let cos_phi1 = libm::cos(phi1);
        libm::atan(
            (libm::sin(phi0) * cos_phi1 * libm::sin(lambda1) - libm::sin(phi1) * cos_phi0 * libm::sin(lambda0))
                / (cos_phi0 * cos_phi1 * sin_lambda0_lambda1),
        )
    } else {
        (phi0 + phi1) / 2.0
    }
}
