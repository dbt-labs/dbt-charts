//! d3-geo 3.1.1 `src/compose.js`.

/// A point-to-point map with an inverse: d3's function carrying an `.invert` property. Every map ported so
/// far has one, so `compose` does not model d3's "only if both have `invert`".
pub trait Invertible {
    fn forward(&self, x: f64, y: f64) -> [f64; 2];
    fn invert(&self, x: f64, y: f64) -> [f64; 2];
}

pub struct Compose<A, B> {
    a: A,
    b: B,
}

/// `compose(a, b)`: `a` first, then `b`.
pub fn compose<A: Invertible, B: Invertible>(a: A, b: B) -> Compose<A, B> {
    Compose { a, b }
}

impl<A: Invertible, B: Invertible> Invertible for Compose<A, B> {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let [x, y] = self.a.forward(x, y);
        self.b.forward(x, y)
    }

    fn invert(&self, x: f64, y: f64) -> [f64; 2] {
        let [x, y] = self.b.invert(x, y);
        self.a.invert(x, y)
    }
}
