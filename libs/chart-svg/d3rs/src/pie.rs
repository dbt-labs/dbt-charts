//! Port of d3-shape's pie() layout — the subset the dbt charts pie emitter
//! relies on: data-order slices (sort disabled), full-turn default, pad angle.
//! Reference: https://github.com/d3/d3-shape/blob/main/src/pie.js

use std::f64::consts::TAU;

#[derive(Debug, Clone, Copy)]
pub struct PieSlice {
    pub index: usize,
    pub start_angle: f64,
    pub end_angle: f64,
    pub pad_angle: f64,
}

/// d3 pie().sort(null).sortValues(null): angles accumulate in data order.
pub fn pie(values: &[f64], start_angle: f64, end_angle: f64, pad_angle: f64) -> Vec<PieSlice> {
    let n = values.len();
    let sum: f64 = values.iter().filter(|v| **v > 0.0).sum();
    let da = (end_angle - start_angle).clamp(-TAU, TAU);
    let p = pad_angle.min(da.abs() / n.max(1) as f64);
    let pa = p * if da < 0.0 { -1.0 } else { 1.0 };
    let k = if sum > 0.0 {
        (da - n as f64 * pa) / sum
    } else {
        0.0
    };

    let mut a0 = start_angle;
    values
        .iter()
        .enumerate()
        .map(|(index, &v)| {
            let value = if v > 0.0 { v } else { 0.0 };
            let a1 = a0 + value * k + pa;
            let slice = PieSlice {
                index,
                start_angle: a0,
                end_angle: a1,
                pad_angle: p,
            };
            a0 = a1;
            slice
        })
        .collect()
}
