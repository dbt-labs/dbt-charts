//! A line breaks across a gap the data does not fill.
//!
//! Two rules, either of which breaks a gap between drawn neighbors:
//!
//! - **Far wider than usual.** The yardstick is the series' 90th-percentile step,
//!   not its median: trading-day data steps 1 day most of the time and 3 or 4 across
//!   a weekend or holiday, and those ordinary long steps must not break it. A gap
//!   breaks when it is more than `STEP_MULTIPLE` of that step and more than
//!   `MIN_PLOT_SHARE` of the plot width, so a dense series never draws a break too
//!   narrow to read as one.
//! - **A skipped period in evenly spaced data.** In a short series the gap is its
//!   own yardstick (5 yearly points with one year missing), so the first rule
//!   cannot see it. When most steps equal the smallest one, the data has a period;
//!   a gap of at least one and a half periods skips one, and it breaks once it spans
//!   more than `SKIP_PLOT_SHARE` of the plot. Unevenly spaced data (samples at
//!   1800, 1900, 1950, 2000) has no period to skip. A series with no weekend rows
//!   does not skip a period across a weekend.
//!
//! Evenly spaced points with nothing missing never break, however sparse.

/// How many usual steps a gap spans before the line breaks across it.
const STEP_MULTIPLE: f64 = 3.0;
/// The narrowest break drawn, as a share of the plot width.
const MIN_PLOT_SHARE: f64 = 0.02;
/// The narrowest skipped period that breaks, as a share of the plot width.
const SKIP_PLOT_SHARE: f64 = 0.15;
/// How far a step may exceed the smallest and still be one period: a calendar
/// month runs 28 to 31 days.
const PERIOD_TOLERANCE: f64 = 1.15;

/// `runs` split wherever two neighbors in a run sit across a gap the data does not
/// fill. `at` is a point's x-domain index, `x` an index's pixel position on a
/// continuous axis, and `days` each index's day count when x is a date.
pub fn split_at_large_gaps<T: Copy>(
    runs: Vec<Vec<T>>,
    at: impl Fn(T) -> usize,
    x: impl Fn(usize) -> f64,
    days: Option<&[f64]>,
    plot_w: f64,
) -> Vec<Vec<T>> {
    let step = |a: T, b: T| x(at(b)) - x(at(a));
    let mut steps: Vec<f64> = runs.iter().flat_map(|run| run.windows(2).map(|w| step(w[0], w[1]))).filter(|s| *s > 0.0).collect();
    if steps.len() < 2 {
        return runs;
    }
    steps.sort_by(f64::total_cmp);
    let usual = steps[(0.9 * (steps.len() - 1) as f64).round() as usize];
    let wide = (STEP_MULTIPLE * usual).max(MIN_PLOT_SHARE * plot_w);
    let period = steps[0];
    let regular = 2 * steps.iter().filter(|&&s| s <= PERIOD_TOLERANCE * period).count() > steps.len();
    let weekday = |d: f64| (d.floor() as i64 + 3).rem_euclid(7);
    let weekdays_only = days.is_some_and(|days| runs.iter().flatten().all(|&p| weekday(days[at(p)]) < 5));
    let skips = |a: T, b: T| {
        if !regular || step(a, b) < 1.5 * period {
            return false;
        }
        match days.filter(|_| weekdays_only) {
            Some(days) => (days[at(a)].floor() as i64 + 1..days[at(b)].floor() as i64).any(|d| weekday(d as f64) < 5),
            None => true,
        }
    };
    let breaks = |a: T, b: T| step(a, b) > wide || (step(a, b) > SKIP_PLOT_SHARE * plot_w && skips(a, b));
    let mut out = Vec::with_capacity(runs.len());
    for run in runs {
        let mut current: Vec<T> = Vec::with_capacity(run.len());
        for p in run {
            if let Some(&prev) = current.last() {
                if breaks(prev, p) {
                    out.push(std::mem::take(&mut current));
                }
            }
            current.push(p);
        }
        if !current.is_empty() {
            out.push(current);
        }
    }
    out
}

/// [`split_at_large_gaps`] over runs of plain positions, one unit a pixel; with
/// `dates` the positions are also day counts. The rule as a caller outside the
/// layout sees it (the line-gap warning reads it through the Python extension).
pub fn split_runs(runs: Vec<Vec<f64>>, dates: bool, plot_w: f64) -> Vec<Vec<f64>> {
    let xs: Vec<f64> = runs.iter().flatten().copied().collect();
    let mut starts = runs.iter().scan(0, |start, run| Some(std::mem::replace(start, *start + run.len())));
    let indexed: Vec<Vec<usize>> = runs.iter().zip(&mut starts).map(|(run, start)| (start..start + run.len()).collect()).collect();
    split_at_large_gaps(indexed, |i| i, |i| xs[i], dates.then_some(xs.as_slice()), plot_w)
        .into_iter()
        .map(|run| run.into_iter().map(|i| xs[i]).collect())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lens(runs: Vec<Vec<f64>>, plot_w: f64, days: bool) -> Vec<usize> {
        split_runs(runs, days, plot_w).iter().map(Vec::len).collect()
    }

    fn split(xs: &[f64], plot_w: f64) -> Vec<usize> {
        lens(vec![xs.to_vec()], plot_w, false)
    }

    #[test]
    fn a_six_month_hole_in_monthly_data_breaks() {
        let xs: Vec<f64> = (0..36).filter(|m| !(14..20).contains(m)).map(f64::from).collect();
        assert_eq!(split(&xs, 35.0), [14, 16]);
    }

    #[test]
    fn one_missing_month_in_three_years_stays_joined() {
        let xs: Vec<f64> = (0..36).filter(|&m| m != 16).map(f64::from).collect();
        assert_eq!(split(&xs, 35.0), [35]);
    }

    #[test]
    fn evenly_spaced_sparse_points_never_break() {
        let xs: Vec<f64> = (0..8).map(|i| f64::from(i) * 100.0).collect();
        assert_eq!(split(&xs, 700.0), [8]);
    }

    #[test]
    fn weekends_and_holidays_in_trading_days_stay_joined() {
        // Mon-Fri for 13 weeks from Monday 2025-01-06, with a 4-day holiday weekend in week 8.
        let monday = 20094.0;
        let xs: Vec<f64> = (0..91).filter(|d| d % 7 < 5 && *d != 53).map(|d| monday + f64::from(d)).collect();
        assert_eq!(lens(vec![xs.clone()], 90.0, true), [xs.len()]);
    }

    #[test]
    fn a_weekend_in_two_weeks_of_trading_days_stays_joined() {
        let monday = 20094.0;
        let xs: Vec<f64> = (0..12).filter(|d| d % 7 < 5).map(|d| monday + f64::from(d)).collect();
        assert_eq!(lens(vec![xs.clone()], 11.0, true), [xs.len()]);
    }

    #[test]
    fn a_break_narrower_than_the_floor_stays_joined() {
        // Two years of daily points with a 4-day outage: 5 steps wide, 0.7% of the plot.
        let xs: Vec<f64> = (0..730).filter(|d| !(184..188).contains(d)).map(f64::from).collect();
        assert_eq!(split(&xs, 729.0), [xs.len()]);
    }

    #[test]
    fn three_points_never_break() {
        assert_eq!(split(&[0.0, 1.0, 100.0], 100.0), [3]);
    }

    #[test]
    fn a_missing_year_among_five_yearly_points_breaks() {
        assert_eq!(split(&[0.0, 1.0, 2.0, 4.0, 5.0], 5.0), [3, 2]);
    }

    #[test]
    fn unevenly_spaced_samples_have_no_period_to_skip() {
        // World population sampled at 1800, 1900, 1950, 2000, 2025.
        assert_eq!(split(&[1800.0, 1900.0, 1950.0, 2000.0, 2025.0], 225.0), [5]);
    }

    #[test]
    fn existing_runs_stay_split_and_join_their_steps() {
        let runs = vec![vec![0.0, 1.0, 2.0, 3.0], vec![5.0, 6.0, 7.0, 20.0, 21.0, 22.0]];
        assert_eq!(lens(runs, 22.0, false), [4, 3, 3]);
    }
}
