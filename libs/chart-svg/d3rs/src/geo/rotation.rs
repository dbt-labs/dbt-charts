//! d3-geo 3.1.1 `src/rotation.js`: `geoRotation` and its radian building blocks.

use super::compose::{compose, Compose, Invertible};
use super::math::{asin, DEGREES, PI, RADIANS, TAU};
use crate::js_math::js_round;

fn wrap(lambda: f64) -> f64 {
    if lambda.abs() > PI {
        lambda - TAU * js_round(lambda / TAU)
    } else {
        lambda
    }
}

pub struct RotationIdentity;

impl Invertible for RotationIdentity {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [wrap(lambda), phi]
    }

    fn invert(&self, lambda: f64, phi: f64) -> [f64; 2] {
        self.forward(lambda, phi)
    }
}

pub struct RotationLambda {
    delta_lambda: f64,
}

impl Invertible for RotationLambda {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [wrap(lambda + self.delta_lambda), phi]
    }

    fn invert(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [wrap(lambda + -self.delta_lambda), phi]
    }
}

pub struct RotationPhiGamma {
    cos_delta_phi: f64,
    sin_delta_phi: f64,
    cos_delta_gamma: f64,
    sin_delta_gamma: f64,
}

impl RotationPhiGamma {
    fn new(delta_phi: f64, delta_gamma: f64) -> Self {
        Self {
            cos_delta_phi: libm::cos(delta_phi),
            sin_delta_phi: libm::sin(delta_phi),
            cos_delta_gamma: libm::cos(delta_gamma),
            sin_delta_gamma: libm::sin(delta_gamma),
        }
    }
}

impl Invertible for RotationPhiGamma {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        let cos_phi = libm::cos(phi);
        let x = libm::cos(lambda) * cos_phi;
        let y = libm::sin(lambda) * cos_phi;
        let z = libm::sin(phi);
        let k = z * self.cos_delta_phi + x * self.sin_delta_phi;
        [
            libm::atan2(y * self.cos_delta_gamma - k * self.sin_delta_gamma, x * self.cos_delta_phi - z * self.sin_delta_phi),
            asin(k * self.cos_delta_gamma + y * self.sin_delta_gamma),
        ]
    }

    fn invert(&self, lambda: f64, phi: f64) -> [f64; 2] {
        let cos_phi = libm::cos(phi);
        let x = libm::cos(lambda) * cos_phi;
        let y = libm::sin(lambda) * cos_phi;
        let z = libm::sin(phi);
        let k = z * self.cos_delta_gamma - y * self.sin_delta_gamma;
        [
            libm::atan2(y * self.cos_delta_gamma + z * self.sin_delta_gamma, x * self.cos_delta_phi + k * self.sin_delta_phi),
            asin(k * self.cos_delta_phi - x * self.sin_delta_phi),
        ]
    }
}

/// What `rotateRadians` returns: one of d3's four closures.
pub enum Rotation {
    Identity(RotationIdentity),
    Lambda(RotationLambda),
    PhiGamma(RotationPhiGamma),
    LambdaPhiGamma(Compose<RotationLambda, RotationPhiGamma>),
}

impl Invertible for Rotation {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        match self {
            Self::Identity(r) => r.forward(lambda, phi),
            Self::Lambda(r) => r.forward(lambda, phi),
            Self::PhiGamma(r) => r.forward(lambda, phi),
            Self::LambdaPhiGamma(r) => r.forward(lambda, phi),
        }
    }

    fn invert(&self, lambda: f64, phi: f64) -> [f64; 2] {
        match self {
            Self::Identity(r) => r.invert(lambda, phi),
            Self::Lambda(r) => r.invert(lambda, phi),
            Self::PhiGamma(r) => r.invert(lambda, phi),
            Self::LambdaPhiGamma(r) => r.invert(lambda, phi),
        }
    }
}

/// `rotateRadians(deltaLambda, deltaPhi, deltaGamma)`.
pub fn rotate_radians(delta_lambda: f64, delta_phi: f64, delta_gamma: f64) -> Rotation {
    // JavaScript truthiness: 0 and NaN are falsy.
    let truthy = |x: f64| x != 0.0 && !x.is_nan();
    let delta_lambda = delta_lambda % TAU;
    let lambda = || RotationLambda { delta_lambda };
    let phi_gamma = || RotationPhiGamma::new(delta_phi, delta_gamma);
    match (truthy(delta_lambda), truthy(delta_phi) || truthy(delta_gamma)) {
        (true, true) => Rotation::LambdaPhiGamma(compose(lambda(), phi_gamma())),
        (true, false) => Rotation::Lambda(lambda()),
        (false, true) => Rotation::PhiGamma(phi_gamma()),
        (false, false) => Rotation::Identity(RotationIdentity),
    }
}

/// `geoRotation([lambda, phi, gamma])`, in degrees. (d3 also takes two angles, meaning `gamma = 0`.)
pub struct GeoRotation {
    rotate: Rotation,
}

pub fn geo_rotation(rotate: [f64; 3]) -> GeoRotation {
    GeoRotation { rotate: rotate_radians(rotate[0] * RADIANS, rotate[1] * RADIANS, rotate[2] * RADIANS) }
}

impl GeoRotation {
    pub fn forward(&self, [lambda, phi]: [f64; 2]) -> [f64; 2] {
        let [lambda, phi] = self.rotate.forward(lambda * RADIANS, phi * RADIANS);
        [lambda * DEGREES, phi * DEGREES]
    }

    pub fn invert(&self, [lambda, phi]: [f64; 2]) -> [f64; 2] {
        let [lambda, phi] = self.rotate.invert(lambda * RADIANS, phi * RADIANS);
        [lambda * DEGREES, phi * DEGREES]
    }
}
