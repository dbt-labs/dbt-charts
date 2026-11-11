//! Boxes and affine maps for the scene's queries.

/// An axis-aligned box in user units.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bounds {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
}

impl Bounds {
    /// The smallest box holding every point; `None` for no points.
    pub fn of_points(points: impl IntoIterator<Item = (f64, f64)>) -> Option<Self> {
        points.into_iter().fold(None, |acc, (x, y)| {
            Some(match acc {
                None => Bounds { x1: x, y1: y, x2: x, y2: y },
                Some(b) => Bounds { x1: b.x1.min(x), y1: b.y1.min(y), x2: b.x2.max(x), y2: b.y2.max(y) },
            })
        })
    }

    pub fn union(self, other: Bounds) -> Bounds {
        Bounds { x1: self.x1.min(other.x1), y1: self.y1.min(other.y1), x2: self.x2.max(other.x2), y2: self.y2.max(other.y2) }
    }

    pub fn width(&self) -> f64 {
        self.x2 - self.x1
    }

    pub fn height(&self) -> f64 {
        self.y2 - self.y1
    }

    /// The box of this box's corners under `map`: exact for translation and
    /// scaling, the enclosing box under a rotation.
    pub fn transformed(&self, map: &Affine) -> Bounds {
        let corners = [(self.x1, self.y1), (self.x2, self.y1), (self.x2, self.y2), (self.x1, self.y2)];
        let [first, rest @ ..] = corners.map(|p| map.apply(p));
        let at = |(x, y): (f64, f64)| Bounds { x1: x, y1: y, x2: x, y2: y };
        rest.into_iter().fold(at(first), |acc, p| acc.union(at(p)))
    }
}

/// `x' = a·x + c·y + e`, `y' = b·x + d·y + f`: SVG's `matrix(a b c d e f)`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Affine {
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub d: f64,
    pub e: f64,
    pub f: f64,
}

impl Affine {
    pub const IDENTITY: Affine = Affine { a: 1.0, b: 0.0, c: 0.0, d: 1.0, e: 0.0, f: 0.0 };

    /// The map that applies `inner` first, then `self`.
    pub fn then_inner(&self, inner: &Affine) -> Affine {
        Affine {
            a: self.a * inner.a + self.c * inner.b,
            b: self.b * inner.a + self.d * inner.b,
            c: self.a * inner.c + self.c * inner.d,
            d: self.b * inner.c + self.d * inner.d,
            e: self.a * inner.e + self.c * inner.f + self.e,
            f: self.b * inner.e + self.d * inner.f + self.f,
        }
    }

    pub fn apply(&self, (x, y): (f64, f64)) -> (f64, f64) {
        (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)
    }
}

/// One entry of a node's transform list. The list applies left to right as SVG
/// reads it: `[Translate, Rotate]` is `translate(..) rotate(..)`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Transform {
    Translate { x: f64, y: f64 },
    /// Degrees clockwise on screen, about `about` (the origin when `None`).
    Rotate { deg: f64, about: Option<(f64, f64)> },
    /// Uniform, about the origin.
    Scale(f64),
}

impl Transform {
    pub fn affine(&self) -> Affine {
        match *self {
            Transform::Translate { x, y } => Affine { e: x, f: y, ..Affine::IDENTITY },
            Transform::Scale(k) => Affine { a: k, d: k, ..Affine::IDENTITY },
            Transform::Rotate { deg, about } => {
                let (sin, cos) = deg.to_radians().sin_cos();
                let turn = Affine { a: cos, b: sin, c: -sin, d: cos, e: 0.0, f: 0.0 };
                match about {
                    None => turn,
                    Some((cx, cy)) => Transform::Translate { x: cx, y: cy }
                        .affine()
                        .then_inner(&turn)
                        .then_inner(&Transform::Translate { x: -cx, y: -cy }.affine()),
                }
            }
        }
    }
}

/// The map a transform list composes to.
pub fn compose(transforms: &[Transform]) -> Affine {
    transforms.iter().fold(Affine::IDENTITY, |acc, t| acc.then_inner(&t.affine()))
}
