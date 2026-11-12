//! Port of d3-shape 3.2.0 `src/stack.js` with every `offset/*.js` and
//! `order/*.js`: series are indices into `values[series][point]`, a missing
//! value is NaN (d3's `+undefined`), and each point becomes its `[y0, y1]`.

use crate::invariant::{at, at_mut, Invariant};

/// `stackOffset*`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Offset {
    None,
    Expand,
    Diverging,
    Silhouette,
    Wiggle,
}

/// `stackOrder*`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Order {
    None,
    Ascending,
    Descending,
    InsideOut,
    Reverse,
    Appearance,
}

/// What `stack()(data)` returns: `series[i][j]` is series `i`'s `[y0, y1]`
/// at point `j`, and `index[i]` its position in the stacking order.
#[derive(Clone, Debug, PartialEq)]
pub struct Stacked {
    pub series: Vec<Vec<[f64; 2]>>,
    pub index: Vec<usize>,
}

/// `stack().keys(0..n).order(order).offset(offset)(data)` over
/// `values[series][point]`; every series must have the same number of points.
pub fn stack(values: &[Vec<f64>], order: Order, offset: Offset) -> Stacked {
    let mut series: Vec<Vec<[f64; 2]>> =
        values.iter().map(|row| row.iter().map(|v| [0.0, *v]).collect()).collect();
    let oz = order_of(values, order);
    let mut index = vec![0; series.len()];
    for (i, &o) in oz.iter().enumerate() {
        *at_mut(&mut index, o) = i;
    }
    match offset {
        Offset::None => offset_none(&mut series, &oz),
        Offset::Expand => offset_expand(&mut series, &oz),
        Offset::Diverging => offset_diverging(&mut series, &oz),
        Offset::Silhouette => offset_silhouette(&mut series, &oz),
        Offset::Wiggle => offset_wiggle(&mut series, &oz),
    }
    Stacked { series, index }
}

fn first(order: &[usize]) -> usize {
    *at(order, 0)
}

fn row_of(series: &[Vec<[f64; 2]>], s: usize) -> &[[f64; 2]] {
    at(series, s)
}

fn cell(series: &[Vec<[f64; 2]>], s: usize, j: usize) -> &[f64; 2] {
    at(row_of(series, s), j)
}

fn cell_mut(series: &mut [Vec<[f64; 2]>], s: usize, j: usize) -> &mut [f64; 2] {
    at_mut(at_mut(series, s), j)
}

/// `order/ascending.js`'s `sum`: a falsy value (0, NaN) adds nothing.
fn series_sum(values: &[f64]) -> f64 {
    values.iter().filter(|v| **v != 0.0 && !v.is_nan()).sum()
}

/// `order/appearance.js`'s `peak`: the index of the first maximum (`>`), 0 for an empty or all-NaN series.
fn peak(values: &[f64]) -> usize {
    let mut j = 0;
    let mut vj = f64::NEG_INFINITY;
    for (i, &vi) in values.iter().enumerate() {
        if vi > vj {
            vj = vi;
            j = i;
        }
    }
    j
}

/// `stackOrder*(series)`: the stacking order as series indices.
pub fn order_of(values: &[Vec<f64>], order: Order) -> Vec<usize> {
    let n = values.len();
    let mut none: Vec<usize> = (0..n).collect();
    match order {
        Order::None => none,
        Order::Reverse => {
            none.reverse();
            none
        }
        Order::Ascending => {
            let sums: Vec<f64> = values.iter().map(|s| series_sum(s)).collect();
            none.sort_by(|&a, &b| at(&sums, a).partial_cmp(at(&sums, b)).held("sums are finite"));
            none
        }
        Order::Descending => {
            let mut ascending = order_of(values, Order::Ascending);
            ascending.reverse();
            ascending
        }
        Order::Appearance => {
            let peaks: Vec<usize> = values.iter().map(|s| peak(s)).collect();
            none.sort_by_key(|&i| *at(&peaks, i));
            none
        }
        Order::InsideOut => {
            let sums: Vec<f64> = values.iter().map(|s| series_sum(s)).collect();
            let (mut top, mut bottom) = (0.0, 0.0);
            let (mut tops, mut bottoms) = (Vec::new(), Vec::new());
            for j in order_of(values, Order::Appearance) {
                if top < bottom {
                    top += at(&sums, j);
                    tops.push(j);
                } else {
                    bottom += at(&sums, j);
                    bottoms.push(j);
                }
            }
            bottoms.reverse();
            bottoms.extend(tops);
            bottoms
        }
    }
}

fn offset_none(series: &mut [Vec<[f64; 2]>], order: &[usize]) {
    let n = series.len();
    if n <= 1 {
        return;
    }
    let m = row_of(series, first(order)).len();
    for i in 1..n {
        let (s0, s1) = (*at(order, i - 1), *at(order, i));
        for j in 0..m {
            let below = if cell(series, s0, j)[1].is_nan() { cell(series, s0, j)[0] } else { cell(series, s0, j)[1] };
            cell_mut(series, s1, j)[0] = below;
            cell_mut(series, s1, j)[1] += below;
        }
    }
}

/// `v || 0`.
fn or_zero(v: f64) -> f64 {
    if v.is_nan() {
        0.0
    } else {
        v
    }
}

fn offset_expand(series: &mut [Vec<[f64; 2]>], order: &[usize]) {
    let n = series.len();
    if n == 0 {
        return;
    }
    let m = row_of(series, 0).len();
    for j in 0..m {
        let y: f64 = (0..n).map(|i| or_zero(cell(series, i, j)[1])).sum();
        if y != 0.0 {
            for row in series.iter_mut() {
                at_mut(row, j)[1] /= y;
            }
        }
    }
    offset_none(series, order);
}

fn offset_diverging(series: &mut [Vec<[f64; 2]>], order: &[usize]) {
    let n = series.len();
    if n == 0 {
        return;
    }
    let m = row_of(series, first(order)).len();
    for j in 0..m {
        let (mut yp, mut yn) = (0.0, 0.0);
        for &i in order {
            let d = cell_mut(series, i, j);
            let dy = d[1] - d[0];
            if dy > 0.0 {
                d[0] = yp;
                yp += dy;
                d[1] = yp;
            } else if dy < 0.0 {
                d[1] = yn;
                yn += dy;
                d[0] = yn;
            } else {
                d[0] = 0.0;
                d[1] = dy;
            }
        }
    }
}

fn offset_silhouette(series: &mut [Vec<[f64; 2]>], order: &[usize]) {
    let n = series.len();
    if n == 0 {
        return;
    }
    let s0 = first(order);
    let m = row_of(series, s0).len();
    for j in 0..m {
        let y: f64 = (0..n).map(|i| or_zero(cell(series, i, j)[1])).sum();
        cell_mut(series, s0, j)[0] = -y / 2.0;
        cell_mut(series, s0, j)[1] += -y / 2.0;
    }
    offset_none(series, order);
}

fn offset_wiggle(series: &mut [Vec<[f64; 2]>], order: &[usize]) {
    let n = series.len();
    if n == 0 {
        return;
    }
    let s0 = first(order);
    let m = row_of(series, s0).len();
    if m == 0 {
        return;
    }
    let mut y = 0.0;
    for j in 1..m {
        let (mut s1, mut s2) = (0.0, 0.0);
        for i in 0..n {
            let si = *at(order, i);
            let sij0 = or_zero(cell(series, si, j)[1]);
            let sij1 = or_zero(cell(series, si, j - 1)[1]);
            let mut s3 = (sij0 - sij1) / 2.0;
            for &sk in order.iter().take(i) {
                let skj0 = or_zero(cell(series, sk, j)[1]);
                let skj1 = or_zero(cell(series, sk, j - 1)[1]);
                s3 += skj0 - skj1;
            }
            s1 += sij0;
            s2 += s3 * sij0;
        }
        cell_mut(series, s0, j - 1)[0] = y;
        cell_mut(series, s0, j - 1)[1] += y;
        if s1 != 0.0 {
            y -= s2 / s1;
        }
    }
    cell_mut(series, s0, m - 1)[0] = y;
    cell_mut(series, s0, m - 1)[1] += y;
    offset_none(series, order);
}
