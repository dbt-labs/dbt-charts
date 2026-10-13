//! `Array.prototype.sort` as V8 runs it on short arrays: TimSort with one
//! forced run — count the leading run (reversing a strictly descending one),
//! then binary-insertion-sort the rest. Layout ports need this, not Rust's
//! sort, because some upstream comparators are not total orders (sankey-plus
//! returns 0 for pairs it does not rank), and the result of sorting with
//! such a comparator is whatever V8's algorithm produces.
//! Reference: v8/third_party/v8/builtins/array-sort.tq
//!
//! Past 64 elements V8 merges several runs, which is not ported; such an
//! input refuses rather than risk a different order.

/// V8's `ComputeMinRunLength`: arrays shorter than this are one run.
const MIN_MERGE: usize = 64;

/// Sort `v` in place; `cmp(a, b) < 0` means `a` sorts first, and any other
/// value (including NaN, as JS coerces it) keeps order.
pub fn js_sort<T: Copy>(v: &mut [T], mut cmp: impl FnMut(T, T) -> f64) -> Result<(), String> {
    let n = v.len();
    if n < 2 {
        return Ok(());
    }
    if n >= MIN_MERGE {
        return Err(format!("sort of {n} items: V8's multi-run merge is not ported"));
    }
    let run = count_and_make_run(v, &mut cmp);
    binary_insertion_sort(v, run, &mut cmp);
    Ok(())
}

fn count_and_make_run<T: Copy>(v: &mut [T], cmp: &mut impl FnMut(T, T) -> f64) -> usize {
    let n = v.len();
    if n == 1 {
        return 1;
    }
    let descending = cmp(v[1], v[0]) < 0.0;
    let mut run = 2;
    let mut prev = v[1];
    for &cur in &v[2..] {
        let order = cmp(cur, prev);
        if descending != (order < 0.0) {
            break;
        }
        prev = cur;
        run += 1;
    }
    if descending {
        v[..run].reverse();
    }
    run
}

fn binary_insertion_sort<T: Copy>(v: &mut [T], start: usize, cmp: &mut impl FnMut(T, T) -> f64) {
    for i in start.max(1)..v.len() {
        let pivot = v[i];
        let (mut left, mut right) = (0, i);
        while left < right {
            let mid = left + ((right - left) >> 1);
            if cmp(pivot, v[mid]) < 0.0 {
                right = mid;
            } else {
                left = mid + 1;
            }
        }
        v.copy_within(left..i, left + 1);
        v[left] = pivot;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sorts_stably() {
        let mut v = [(3, 'a'), (1, 'b'), (3, 'c'), (2, 'd'), (1, 'e')];
        js_sort(&mut v, |a, b| (a.0 - b.0) as f64).unwrap();
        assert_eq!(v, [(1, 'b'), (1, 'e'), (2, 'd'), (3, 'a'), (3, 'c')]);
    }

    /// Expected orders are node's own `[...].sort(cmp)` output for a
    /// comparator that ranks only even numbers and ties every other pair.
    #[test]
    fn matches_v8_on_a_partial_comparator() {
        let cmp = |a: i32, b: i32| if a % 2 == 0 && b % 2 == 0 { (a - b) as f64 } else { 0.0 };
        let mut v = [8, 3, 6, 1, 4, 7, 2];
        js_sort(&mut v, cmp).unwrap();
        assert_eq!(v, [8, 3, 6, 1, 4, 7, 2]);
        let mut w = [6, 4, 2, 5, 0];
        js_sort(&mut w, cmp).unwrap();
        assert_eq!(w, [0, 2, 4, 6, 5]);
    }
}
