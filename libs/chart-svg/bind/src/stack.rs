//! The stack grammar: a board's `stack` mode on d3-shape's stack, and the series order a stacked
//! chart piles in.

use d3rs::stack::{stack, Offset, Order};
use serde_json::Value;

use crate::rows::{row_num, row_str};

/// A board's `stack` mode on d3-shape's stack, `values[series][column]` with NaN
/// for no datum, baseline series first, and each datum's `[start, end]` back.
///
/// `zero` is the diverging offset: positives pile up from 0, negatives down from
/// it, a zero riding the positive pile. `normalize` expands each sign's series over that sign's own sum, so a
/// mixed-sign column is two `[0, 1]` piles and a negative segment is drawn as its
/// magnitude; a sign's pile that sums to 0 (a column of zeros, or a zero beside a
/// negative) has no share and its spans are NaN (Vega's 0/0), where d3 would leave
/// the zeros unscaled. `center` is the
/// silhouette of the magnitudes floated up by half the tallest column, so the
/// tallest fills `[0, max]` and every column centers on `max / 2`. An unknown
/// mode refuses by name.
pub fn grammar_stack(mode: &str, values: &[Vec<f64>]) -> Result<Vec<Vec<[f64; 2]>>, String> {
    match mode {
        "zero" => Ok(zero_stack(values)),
        "normalize" => Ok(normalized(values)),
        "center" => {
            let columns = values.first().map_or(0, Vec::len);
            Ok(centered(values, tallest_pile((0..columns).map(|j| values.iter().map(move |series| series[j])))))
        }
        other => Err(format!("stack: unknown mode {other:?}")),
    }
}

/// The largest column sum of magnitudes, an absent (NaN) datum adding nothing: the
/// height of the tallest `center` pile.
pub fn tallest_pile<C: IntoIterator<Item = f64>>(columns: impl IntoIterator<Item = C>) -> f64 {
    columns
        .into_iter()
        .map(|column| column.into_iter().filter(|v| !v.is_nan()).map(f64::abs).sum::<f64>())
        .fold(0.0, f64::max)
}

/// `grammar_stack("center", values)` with the tallest pile given: a facet centers
/// every panel on the tallest column of all of them.
pub fn centered(values: &[Vec<f64>], tallest: f64) -> Vec<Vec<[f64; 2]>> {
    let magnitudes: Vec<Vec<f64>> = values.iter().map(|series| series.iter().map(|v| v.abs()).collect()).collect();
    let mut spans = stack(&magnitudes, Order::None, Offset::Silhouette).series;
    for span in spans.iter_mut().flatten() {
        span[0] += tallest / 2.0;
        span[1] += tallest / 2.0;
    }
    spans
}

fn normalized(values: &[Vec<f64>]) -> Vec<Vec<[f64; 2]>> {
    // A sign's pile that sums to 0 has no share to divide by (Vega's 0/0): d3's expand
    // leaves its zeros at [0, 0], here they have no span.
    let sign = |keep: fn(f64) -> bool| -> Vec<Vec<[f64; 2]>> {
        let kept: Vec<Vec<f64>> = values
            .iter()
            .map(|series| series.iter().map(|&v| if keep(v) { v } else { f64::NAN }).collect())
            .collect();
        let mut spans = stack(&kept, Order::None, Offset::Expand).series;
        for j in 0..kept.first().map_or(0, Vec::len) {
            if kept.iter().map(|series| series[j]).filter(|v| !v.is_nan()).sum::<f64>() == 0.0 {
                for span in spans.iter_mut() {
                    span[j] = [f64::NAN; 2];
                }
            }
        }
        spans
    };
    let (positive, negative) = (sign(|v| v >= 0.0), sign(|v| v < 0.0));
    values
        .iter()
        .enumerate()
        .map(|(i, series)| {
            series
                .iter()
                .enumerate()
                .map(|(j, &v)| if v < 0.0 { negative[i][j] } else { positive[i][j] })
                .collect()
        })
        .collect()
}

/// Where a rail label anchors on a stacked segment: its midpoint, or the raw zero
/// stack's midpoint, 0, for a normalized segment with no share (`_stacked_midpoints`:
/// `mid / col_total if col_total else mid`).
pub fn rail_anchor(span: [f64; 2]) -> f64 {
    if span[0].is_nan() {
        0.0
    } else {
        (span[0] + span[1]) / 2.0
    }
}

/// The `[start, end]` of every item on its key's `zero` pile, in input order:
/// items sharing a key stack in the order they come, one series each.
pub fn zero_piles<K: Eq + std::hash::Hash>(items: impl IntoIterator<Item = (K, f64)>) -> Vec<(f64, f64)> {
    let mut piles: Vec<Vec<f64>> = Vec::new();
    let mut pile_of: std::collections::HashMap<K, usize> = std::collections::HashMap::new();
    let mut place: Vec<(usize, usize)> = Vec::new();
    for (key, value) in items {
        let pile = *pile_of.entry(key).or_insert_with(|| {
            piles.push(Vec::new());
            piles.len() - 1
        });
        place.push((pile, piles[pile].len()));
        piles[pile].push(value);
    }
    let spans: Vec<Vec<[f64; 2]>> = piles
        .iter()
        .map(|pile| {
            let one_point_each: Vec<Vec<f64>> = pile.iter().map(|&v| vec![v]).collect();
            zero_stack(&one_point_each).into_iter().map(|series| series[0]).collect()
        })
        .collect();
    place.into_iter().map(|(pile, i)| (spans[pile][i][0], spans[pile][i][1])).collect()
}

/// d3's diverging offset, except that a zero datum rides the top of the positive pile
/// below it (Vega's `stackZero`, and d3's own `none` offset) instead of sitting at the
/// baseline: a zero-height segment keeps the seam between its neighbors, so a series that
/// is null or absent at one x pinches to a wedge there rather than dipping to 0.
fn zero_stack(values: &[Vec<f64>]) -> Vec<Vec<[f64; 2]>> {
    let mut spans = stack(values, Order::None, Offset::Diverging).series;
    for j in 0..values.first().map_or(0, Vec::len) {
        let mut top = 0.0;
        for (series, span) in values.iter().zip(spans.iter_mut()) {
            if series[j] == 0.0 {
                span[j] = [top, top];
            } else if series[j] > 0.0 {
                top = span[j][1];
            }
        }
    }
    spans
}

/// A stacked or grouped chart's series order, baseline-first or first-slot-first — port of
/// `core/utils.py::sorted_by_series_order`. `value` (the default when
/// `series_order` is unauthored): descending global sum of `y_field`, ties by
/// name; `alphabetical`: by name; `data`: first encounter of the series cell
/// in `rows`, a series never encountered ranking last. Any other string
/// refuses by name rather than ranking by a guessed rule.
pub fn sorted_by_series_order(
    series: &[String],
    rows: &[serde_json::Map<String, Value>],
    series_field: &str,
    y_field: &str,
    series_order: Option<&str>,
) -> Result<Vec<String>, String> {
    let mut ordered = series.to_vec();
    match series_order {
        Some("alphabetical") => ordered.sort(),
        Some("data") => {
            let mut encounter: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
            for row in rows {
                if row.get(series_field).is_some_and(|v| !v.is_null()) {
                    let next = encounter.len();
                    encounter.entry(row_str(row, series_field)).or_insert(next);
                }
            }
            let unseen = encounter.len();
            ordered.sort_by(|a, b| {
                let rank = |s: &String| encounter.get(s).copied().unwrap_or(unseen);
                rank(a).cmp(&rank(b)).then_with(|| a.cmp(b))
            });
        }
        None | Some("value") => {
            let mut sums: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
            for row in rows {
                if row.get(series_field).is_some_and(|v| !v.is_null()) {
                    if let Some(y) = row_num(row, y_field) {
                        *sums.entry(row_str(row, series_field)).or_insert(0.0) += y;
                    }
                }
            }
            let total = |s: &String| sums.get(s).copied().unwrap_or(0.0);
            ordered.sort_by(|a, b| total(b).total_cmp(&total(a)).then_with(|| a.cmp(b)));
        }
        Some(other) => {
            return Err(format!(
                "style.series_order={other:?} not supported — only \"value\", \"data\", or \"alphabetical\""
            ));
        }
    }
    Ok(ordered)
}

/// A label's stem and trailing number (`P10` is `("p", 10)`, `Tier 3` is `("tier", 3)`); none for a label
/// that is not one stem and a number.
fn ranked_number(label: &str) -> Option<(String, u64)> {
    let split = label.find(|c: char| c.is_ascii_digit())?;
    let (stem, digits) = label.split_at(split);
    let number = digits.parse().ok()?;
    Some((stem.trim_end().to_lowercase(), number))
}

/// A vertical stack's legend, top to bottom as the layers draw: the series that pile above the baseline,
/// last-stacked first, then the ones below it, nearest the baseline first. `series_order` is baseline-first;
/// a series piles below when its values sum negative.
pub fn vertical_legend_order(series_order: &[String], rows: &[serde_json::Map<String, Value>], series_field: &str, y_field: &str) -> Vec<String> {
    let mut sums: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    for row in rows {
        if row.get(series_field).is_some_and(|v| !v.is_null()) {
            if let Some(y) = row_num(row, y_field) {
                *sums.entry(row_str(row, series_field)).or_insert(0.0) += y;
            }
        }
    }
    let below = |s: &&String| sums.get(*s).copied().unwrap_or(0.0) < 0.0;
    let above = series_order.iter().filter(|s| !below(s)).rev();
    above.chain(series_order.iter().filter(below)).cloned().collect()
}

/// How a chart sizes its series: the sum of its measure (a bar, line or area), or the average of it
/// (a scatter, whose sum only counts points); `ascending` for an authored axis that runs backwards,
/// where the smaller value is the better one; `start` for a ranged bar (a waterfall), whose size is
/// each bar's drawn length from that column to the measure, not where it ends.
#[derive(Clone, Copy, Default)]
pub struct RankBy<'a> {
    pub mean: bool,
    pub ascending: bool,
    pub start: Option<&'a str>,
}

/// `series` in the order a chart lists them when its author said none: labels that are all one stem and a
/// number by the number (`P2` before `P10`), otherwise [`by_size`]. Python's `_ranked` ranks the board's
/// colors by the same rungs over every chart's shares.
pub fn ranked_series(series: &[String], rows: &[serde_json::Map<String, Value>], series_field: &str, y_field: &str, by: RankBy) -> Vec<String> {
    let mut sums: std::collections::HashMap<String, (f64, usize)> = std::collections::HashMap::new();
    for row in rows {
        if row.get(series_field).is_some_and(|v| !v.is_null()) {
            let size = match by.start {
                Some(start) => row_num(row, y_field).zip(row_num(row, start)).map(|(end, start)| (end - start).abs()),
                None => row_num(row, y_field),
            };
            if let Some(y) = size {
                let entry = sums.entry(row_str(row, series_field)).or_insert((0.0, 0));
                entry.0 += y;
                entry.1 += 1;
            }
        }
    }
    let sizes = sums.into_iter().map(|(name, (sum, n))| (name, if by.mean { sum / n as f64 } else { sum })).collect();
    ranked_by_size(series, &sizes, by.ascending)
}

/// [`ranked_series`] over sizes already summed per series.
pub fn ranked_by_size(series: &[String], sizes: &std::collections::HashMap<String, f64>, ascending: bool) -> Vec<String> {
    let mut ordered = series.to_vec();
    let numbers: Option<Vec<(String, u64)>> = series.iter().map(|s| ranked_number(s)).collect();
    if let Some(numbers) = numbers.filter(|n| n.windows(2).all(|w| w[0].0 == w[1].0)) {
        let number = |s: &String| numbers[series.iter().position(|t| t == s).unwrap_or(0)].1;
        ordered.sort_by(|a, b| number(a).cmp(&number(b)).then_with(|| a.cmp(b)));
        return ordered;
    }
    by_size(series, sizes, ascending)
}

/// `series` largest first by magnitude (a sum of -100 draws larger than one of 10), smallest first on a
/// backwards axis, ties by name. Python's `_ranked` weighs the board's colors by the same magnitude.
pub fn by_size(series: &[String], sizes: &std::collections::HashMap<String, f64>, ascending: bool) -> Vec<String> {
    let mut ordered = series.to_vec();
    let size = |s: &String| sizes.get(s).copied().unwrap_or(0.0).abs();
    ordered.sort_by(|a, b| {
        let by_size = if ascending { size(a).total_cmp(&size(b)) } else { size(b).total_cmp(&size(a)) };
        by_size.then_with(|| a.cmp(b))
    });
    ordered
}

#[cfg(test)]
mod ranked_series_tests {
    use super::*;
    use serde_json::json;

    fn rows(cells: &[(&str, f64)]) -> Vec<serde_json::Map<String, Value>> {
        cells.iter().map(|(s, y)| json!({"s": s, "y": y}).as_object().unwrap().clone()).collect()
    }

    fn names(list: &[&str]) -> Vec<String> {
        list.iter().map(ToString::to_string).collect()
    }

    #[test]
    fn the_largest_sum_comes_first_and_ties_go_by_name() {
        let data = rows(&[("a", 1.0), ("b", 5.0), ("c", 5.0)]);
        assert_eq!(ranked_series(&names(&["a", "b", "c"]), &data, "s", "y", RankBy::default()), ["b", "c", "a"]);
    }

    #[test]
    fn a_scatter_ranks_by_its_average_and_a_backwards_axis_by_the_smallest() {
        let data = rows(&[("a", 10.0), ("a", 10.0), ("a", 10.0), ("b", 25.0)]);
        let list = names(&["a", "b"]);
        assert_eq!(ranked_series(&list, &data, "s", "y", RankBy::default()), ["a", "b"]);
        assert_eq!(ranked_series(&list, &data, "s", "y", RankBy { mean: true, ..RankBy::default() }), ["b", "a"]);
        assert_eq!(ranked_series(&list, &data, "s", "y", RankBy { ascending: true, ..RankBy::default() }), ["b", "a"]);
    }

    #[test]
    fn a_ranged_bar_ranks_by_its_drawn_length_not_its_end() {
        let data: Vec<_> = [("Net", 0.0, 40.0), ("Gain", 40.0, 140.0), ("Loss", 140.0, 120.0)]
            .iter()
            .map(|(s, start, end)| json!({"s": s, "start": start, "y": end}).as_object().unwrap().clone())
            .collect();
        let list = names(&["Gain", "Loss", "Net"]);
        assert_eq!(ranked_series(&list, &data, "s", "y", RankBy::default()), ["Gain", "Loss", "Net"]);
        assert_eq!(ranked_series(&list, &data, "s", "y", RankBy { start: Some("start"), ..RankBy::default() }), ["Gain", "Net", "Loss"]);
    }

    #[test]
    fn a_negative_sum_ranks_by_its_magnitude() {
        let data = rows(&[("small", -10.0), ("big", -100.0), ("mid", 50.0)]);
        assert_eq!(ranked_series(&names(&["big", "mid", "small"]), &data, "s", "y", RankBy::default()), ["big", "mid", "small"]);
    }

    #[test]
    fn numbered_labels_go_by_number_not_size() {
        let data = rows(&[("P10", 9.0), ("P2", 1.0), ("P1", 5.0)]);
        assert_eq!(ranked_series(&names(&["P1", "P10", "P2"]), &data, "s", "y", RankBy::default()), ["P1", "P2", "P10"]);
    }

    #[test]
    fn labels_of_different_stems_go_by_size() {
        let data = rows(&[("P1", 1.0), ("Q2", 5.0)]);
        assert_eq!(ranked_series(&names(&["P1", "Q2"]), &data, "s", "y", RankBy::default()), ["Q2", "P1"]);
    }
}

#[cfg(test)]
mod grammar_stack_tests {
    use super::*;

    const NAN: f64 = f64::NAN;

    /// One column per entry of `columns`, one series per row: `values[series][column]`.
    fn series_major(columns: &[&[f64]]) -> Vec<Vec<f64>> {
        (0..columns[0].len()).map(|i| columns.iter().map(|column| column[i]).collect()).collect()
    }

    fn spans(mode: &str, columns: &[&[f64]]) -> Vec<Vec<[f64; 2]>> {
        grammar_stack(mode, &series_major(columns)).expect("a known mode")
    }

    fn assert_spans(got: &[Vec<[f64; 2]>], series: usize, column: usize, want: [f64; 2]) {
        assert_eq!(got[series][column], want, "series {series}, column {column}: {got:?}");
    }

    #[test]
    fn zero_piles_positives_up_and_negatives_down_from_the_baseline() {
        let got = spans("zero", &[&[3.0, -2.0, 4.0, 0.0]]);
        assert_spans(&got, 0, 0, [0.0, 3.0]);
        assert_spans(&got, 1, 0, [-2.0, 0.0]);
        assert_spans(&got, 2, 0, [3.0, 7.0]);
        assert_spans(&got, 3, 0, [7.0, 7.0]);
    }

    #[test]
    fn zero_lets_a_zero_datum_ride_the_positive_pile_below_it() {
        let got = spans("zero", &[&[0.0, 2.0, 0.0, -1.0, 0.0]]);
        assert_spans(&got, 0, 0, [0.0, 0.0]);
        assert_spans(&got, 1, 0, [0.0, 2.0]);
        assert_spans(&got, 2, 0, [2.0, 2.0]);
        assert_spans(&got, 3, 0, [-1.0, 0.0]);
        assert_spans(&got, 4, 0, [2.0, 2.0]);
    }

    #[test]
    fn zero_skips_an_absent_datum() {
        let got = spans("zero", &[&[3.0, NAN, 4.0]]);
        assert!(got[1][0][1].is_nan(), "{got:?}");
        assert_spans(&got, 2, 0, [3.0, 7.0]);
    }

    #[test]
    fn normalize_gives_each_sign_its_own_unit_pile() {
        let got = spans("normalize", &[&[1.0, 3.0, -2.0, -6.0]]);
        assert_spans(&got, 0, 0, [0.0, 0.25]);
        assert_spans(&got, 1, 0, [0.25, 1.0]);
        assert_spans(&got, 2, 0, [0.0, 0.25]);
        assert_spans(&got, 3, 0, [0.25, 1.0]);
    }

    #[test]
    fn normalize_skips_an_absent_datum_and_leaves_an_all_zero_column_without_a_share() {
        let got = spans("normalize", &[&[1.0, NAN, 3.0], &[0.0, 0.0, NAN]]);
        assert_spans(&got, 0, 0, [0.0, 0.25]);
        assert!(got[1][0][1].is_nan(), "{got:?}");
        assert_spans(&got, 2, 0, [0.25, 1.0]);
        assert!(got[0][1].iter().all(|v| v.is_nan()) && got[1][1].iter().all(|v| v.is_nan()), "{got:?}");
    }

    #[test]
    fn normalize_gives_a_zero_beside_a_negative_no_share() {
        let got = spans("normalize", &[&[0.0, -5.0], &[0.0, 2.0]]);
        assert!(got[0][0].iter().all(|v| v.is_nan()), "{got:?}");
        assert_spans(&got, 1, 0, [0.0, 1.0]);
        assert_spans(&got, 0, 1, [0.0, 0.0]);
        assert_spans(&got, 1, 1, [0.0, 1.0]);
    }

    #[test]
    fn center_floats_each_column_to_the_middle_of_the_tallest() {
        let got = spans("center", &[&[4.0, 6.0], &[1.0, 3.0]]);
        assert_spans(&got, 0, 0, [0.0, 4.0]);
        assert_spans(&got, 1, 0, [4.0, 10.0]);
        assert_spans(&got, 0, 1, [3.0, 4.0]);
        assert_spans(&got, 1, 1, [4.0, 7.0]);
    }

    #[test]
    fn center_piles_a_negative_as_its_magnitude_and_skips_an_absent_datum() {
        let got = spans("center", &[&[4.0, 6.0, 0.0], &[-2.0, NAN, 2.0]]);
        assert_spans(&got, 0, 1, [3.0, 5.0]);
        assert!(got[1][1][1].is_nan(), "{got:?}");
        assert_spans(&got, 2, 1, [5.0, 7.0]);
    }

    #[test]
    fn an_unknown_mode_refuses_by_name() {
        let err = grammar_stack("silhouette", &[vec![1.0]]).unwrap_err();
        assert!(err.contains("\"silhouette\""), "{err}");
    }

    #[test]
    fn zero_piles_stack_each_key_in_item_order() {
        let got = zero_piles([("a", 2.0), ("b", 5.0), ("a", 3.0), ("a", -1.0)]);
        assert_eq!(got, vec![(0.0, 2.0), (0.0, 5.0), (2.0, 5.0), (-1.0, 0.0)]);
    }
}
