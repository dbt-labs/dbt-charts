//! Port of d3-array 3.2.4's `fsum.js`: `Adder`, Shewchuk's exact running sum
//! (https://github.com/d3/d3-array/blob/v3.2.4/src/fsum.js, after CPython's
//! `math.fsum`), and `fsum`. d3-array's plain `sum` is a naive left-to-right
//! loop and has no port: use `Iterator::sum`.

use crate::js_math::js_truthy;

/// d3-array's `Adder`: the exact sum of every value added, as the partials
/// that hold it without rounding. Like d3's fixed `Float64Array(32)`, a 33rd
/// partial is dropped.
#[derive(Default, Clone)]
pub struct Adder {
    partials: [f64; 32],
    n: usize,
}

impl Adder {
    /// `adder.add(x)`.
    pub fn add(&mut self, mut x: f64) {
        let mut i = 0;
        for j in 0..self.n.min(32) {
            let y = self.partials[j];
            let hi = x + y;
            let lo = if x.abs() < y.abs() { x - (hi - y) } else { y - (hi - x) };
            if js_truthy(lo) {
                self.partials[i] = lo;
                i += 1;
            }
            x = hi;
        }
        if i < 32 {
            self.partials[i] = x;
        }
        self.n = i + 1;
    }

    /// `adder.valueOf()`: the partials rounded once. A partial past the typed
    /// array reads `undefined`, which d3 turns into NaN.
    pub fn value(&self) -> f64 {
        let partial = |k: usize| self.partials.get(k).copied().unwrap_or(f64::NAN);
        let mut n = self.n;
        let (mut hi, mut lo) = (0.0, 0.0);
        if n > 0 {
            n -= 1;
            hi = partial(n);
            while n > 0 {
                let x = hi;
                n -= 1;
                let y = partial(n);
                hi = x + y;
                lo = y - (hi - x);
                if js_truthy(lo) {
                    break;
                }
            }
            if n > 0 && ((lo < 0.0 && partial(n - 1) < 0.0) || (lo > 0.0 && partial(n - 1) > 0.0)) {
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

/// `fsum(values)`: the exactly rounded sum. A zero or NaN value is skipped, as d3's `if (value = +value)` does.
pub fn fsum(values: &[f64]) -> f64 {
    let mut adder = Adder::default();
    for &v in values {
        if js_truthy(v) {
            adder.add(v);
        }
    }
    adder.value()
}
