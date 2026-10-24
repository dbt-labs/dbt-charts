//! A channel's value-to-pixel map, built by a family from the scale it
//! already draws with. Pixels are plot-relative along the screen axis the
//! channel occupies.

use serde_json::Value;

use super::model::{payload_error, Anchor, Scalar};
use crate::codes;
use crate::diagnostic::{self, Diagnostic};
use crate::families::cartesian::{BandGeometry, LinearScale, LogScale};
use crate::vega_fit::Bend;

/// The annotation a lookup is for: the `chart_id` and `index` params.
pub struct Site<'a> {
    pub chart_id: &'a str,
    pub index: usize,
}

impl Site<'_> {
    pub fn payload(&self, reason: impl Into<String>) -> Diagnostic {
        payload_error(self.chart_id, self.index, reason)
    }

    /// Records `WARN-ANNOTATION-NOTHING-DRAWN`; the caller then draws nothing.
    pub fn nothing_drawn(&self, reason: impl Into<String>) {
        diagnostic::warn(Diagnostic::new(
            &codes::WARN_ANNOTATION_NOTHING_DRAWN,
            [
                ("chart_id", Value::String(self.chart_id.to_string())),
                ("index", Value::from(self.index)),
                ("reason", Value::String(reason.into())),
            ],
        ));
    }
}

/// `range` is `(low_px, high_px)` in domain order: a y channel on a vertical
/// chart passes `(ph, 0.0)`. On `Band` and `Point` it is the channel's full
/// extent, not the slots'.
#[derive(Clone)]
pub enum AxisMap {
    Linear { domain: (f64, f64), range: (f64, f64) },
    Log { domain: (f64, f64), range: (f64, f64) },
    /// Pow, sqrt or symlog: the bend the family's marks draw with.
    Bent { bend: Bend, domain: (f64, f64), range: (f64, f64) },
    /// Discrete slots. `days` is Some when the categories are dates (bars on a
    /// date column): the day-fraction of each category, same order as `domain`.
    Band { domain: Vec<String>, geometry: BandGeometry, days: Option<Vec<f64>>, range: (f64, f64) },
    /// Line's point x. `days` holds each category's day-fraction for dates,
    /// or its number on a quantitative x; a value interpolates between them.
    Point { domain: Vec<String>, positions: Vec<f64>, days: Option<Vec<f64>>, range: (f64, f64) },
    /// Temporal line x: days-since-epoch extent to a pixel range, the
    /// `utc_fraction_at` law. `range` already includes the family's x padding.
    Time { extent: (f64, f64), range: (f64, f64) },
}

impl AxisMap {
    fn range(&self) -> (f64, f64) {
        match self {
            AxisMap::Linear { range, .. }
            | AxisMap::Log { range, .. }
            | AxisMap::Bent { range, .. }
            | AxisMap::Band { range, .. }
            | AxisMap::Point { range, .. }
            | AxisMap::Time { range, .. } => *range,
        }
    }

    /// The sorted `(min, max)` pixel extent.
    pub fn range_px(&self) -> (f64, f64) {
        let (a, b) = self.range();
        (a.min(b), a.max(b))
    }

    /// One pixel for a value anchor; `None` (warning recorded) when a date
    /// names no slot.
    pub fn position(&self, v: &Scalar, site: &Site) -> Result<Option<f64>, Diagnostic> {
        self.check()?;
        match self {
            AxisMap::Band { .. } => Ok(self
                .span(&Anchor::Value { value: v.clone() }, site)?
                .map(|(lo, hi)| (lo + hi) / 2.0)),
            AxisMap::Point { positions, days, .. } => {
                let days = point_days(days, site)?;
                Ok(Some(interpolate(days, positions, point_ordinal(v, site)?)))
            }
            _ => self.continuous(v, site).map(Some),
        }
    }

    /// One pixel for a trend sample. A band axis interpolates between slot
    /// centers, extending past the ends along the end segments, so a fitted
    /// curve runs continuously across the bars; any other axis is `position`.
    pub fn curve_position(&self, v: &Scalar, site: &Site) -> Result<Option<f64>, Diagnostic> {
        let AxisMap::Band { geometry, days, .. } = self else { return self.position(v, site) };
        self.check()?;
        let days = days.as_deref().ok_or_else(|| site.payload("a value on a category axis"))?;
        let centers: Vec<f64> = (0..days.len()).map(|i| geometry.edge(i, 0.5)).collect();
        Ok(Some(interpolate(days, &centers, date_days(v, site)?)))
    }

    /// One pixel for a rule's or note's anchor: a value, or a category's slot
    /// center; `None` (warning recorded) when it names no slot.
    pub fn at(&self, a: &Anchor, site: &Site) -> Result<Option<f64>, Diagnostic> {
        match (self, a) {
            (_, Anchor::Value { value }) => self.position(value, site),
            (AxisMap::Point { domain, positions, .. }, Anchor::Category { value }) => {
                self.check()?;
                Ok(category_index(domain, value, site).map(|i| positions[i]))
            }
            (_, Anchor::Category { .. }) => Ok(self.span(a, site)?.map(|(lo, hi)| (lo + hi) / 2.0)),
            (_, Anchor::Range { .. }) => Err(site.payload("a range where one position is needed")),
        }
    }

    /// Pixel span `(lo, hi)`, `lo <= hi`, for any anchor; `None` (warning
    /// recorded) when it names no slot.
    pub fn span(&self, a: &Anchor, site: &Site) -> Result<Option<(f64, f64)>, Diagnostic> {
        self.check()?;
        match (self, a) {
            (AxisMap::Band { domain, geometry, .. }, Anchor::Category { value }) => {
                Ok(category_index(domain, value, site).map(|i| (geometry.edge(i, 0.0), geometry.edge(i, 1.0))))
            }
            (AxisMap::Band { geometry, days, .. }, _) => {
                let days = days.as_deref().ok_or_else(|| site.payload("a value on a category axis"))?;
                band_date_span(*geometry, days, a, site)
            }
            (AxisMap::Point { domain, positions, .. }, Anchor::Category { value }) => {
                let Some(i) = category_index(domain, value, site) else { return Ok(None) };
                let p = positions[i];
                let neighbors = [i.checked_sub(1), Some(i + 1)];
                let step = neighbors
                    .into_iter()
                    .flatten()
                    .filter_map(|j| positions.get(j))
                    .map(|q| (q - p).abs())
                    .reduce(f64::min);
                Ok(Some(match step {
                    Some(step) => (self.clamp(p - step / 2.0), self.clamp(p + step / 2.0)),
                    None => self.range_px(),
                }))
            }
            (AxisMap::Point { positions, days, .. }, Anchor::Value { value }) => {
                let days = point_days(days, site)?;
                let p = interpolate(days, positions, point_ordinal(value, site)?);
                Ok(Some((p, p)))
            }
            (AxisMap::Point { positions, days, .. }, Anchor::Range { from, to }) => {
                let days = point_days(days, site)?;
                let (r0, r1) = self.range();
                let at = |b: &Option<Scalar>, edge: f64| -> Result<f64, Diagnostic> {
                    match b {
                        Some(v) => Ok(interpolate(days, positions, point_ordinal(v, site)?)),
                        None => Ok(edge),
                    }
                };
                Ok(Some(sorted(at(from, r0)?, at(to, r1)?)))
            }
            (_, Anchor::Category { .. }) => Err(site.payload("a category on a continuous axis")),
            (_, Anchor::Value { value }) => {
                let p = self.continuous(value, site)?;
                Ok(Some((p, p)))
            }
            (_, Anchor::Range { from, to }) => {
                let (r0, r1) = self.range();
                let lo = from.as_ref().map_or(Ok(r0), |v| self.continuous(v, site))?;
                let hi = to.as_ref().map_or(Ok(r1), |v| self.continuous(v, site))?;
                Ok(Some(sorted(lo, hi)))
            }
        }
    }

    fn check(&self) -> Result<(), Diagnostic> {
        match self {
            AxisMap::Band { domain, days, .. } => check_discrete(domain.len(), domain.len(), days),
            AxisMap::Point { domain, positions, days, .. } => check_discrete(domain.len(), positions.len(), days),
            _ => Ok(()),
        }
    }

    /// `Linear`, `Log`, `Bent` and `Time`: a pixel for any scalar, inside the domain or not.
    fn continuous(&self, v: &Scalar, site: &Site) -> Result<f64, Diagnostic> {
        match (self, v) {
            (AxisMap::Linear { domain, range }, Scalar::Number(n)) => {
                Ok(LinearScale::new(domain.0, domain.1, range.0, range.1).position(*n))
            }
            (AxisMap::Log { domain, range }, Scalar::Number(n)) => {
                if *n <= 0.0 {
                    return Err(site.payload(format!("{} on a log axis", v.text())));
                }
                Ok(LogScale::new(domain.0, domain.1, range.0, range.1).position(*n))
            }
            (AxisMap::Bent { bend, domain, range }, Scalar::Number(n)) => {
                Ok(range.0 + bend.frac(*domain, *n) * (range.1 - range.0))
            }
            (AxisMap::Time { extent, range }, Scalar::Date(_)) => {
                let d = date_days(v, site)?;
                let span = extent.1 - extent.0;
                if span <= 0.0 {
                    return Err(Diagnostic::refused(format!(
                        "annotation axis: a time extent {extent:?} has no width"
                    )));
                }
                Ok(range.0 + (d - extent.0) / span * (range.1 - range.0))
            }
            (AxisMap::Time { .. }, Scalar::Number(_)) => Err(site.payload("a number on a time axis")),
            (_, Scalar::Date(_)) => Err(site.payload("a date on a numeric axis")),
            (AxisMap::Band { .. } | AxisMap::Point { .. }, _) => unreachable!("discrete maps never reach here"),
        }
    }

    fn clamp(&self, p: f64) -> f64 {
        let (lo, hi) = self.range_px();
        p.clamp(lo, hi)
    }
}

fn sorted(a: f64, b: f64) -> (f64, f64) {
    (a.min(b), a.max(b))
}

fn date_days(v: &Scalar, site: &Site) -> Result<f64, Diagnostic> {
    match v {
        Scalar::Date(text) => v.days().ok_or_else(|| site.payload(format!("{text:?} is not a date"))),
        Scalar::Number(_) => Err(site.payload("a number on a date axis")),
    }
}

/// A value on a point axis: a date's day-fraction, or a quantitative x's number.
fn point_ordinal(v: &Scalar, site: &Site) -> Result<f64, Diagnostic> {
    match v {
        Scalar::Number(n) => Ok(*n),
        Scalar::Date(_) => date_days(v, site),
    }
}

fn point_days<'a>(days: &'a Option<Vec<f64>>, site: &Site) -> Result<&'a [f64], Diagnostic> {
    days.as_deref().ok_or_else(|| site.payload("a value on a category axis"))
}

/// A discrete map's per-category vectors must line up with its domain; a
/// family that builds them otherwise is refused, never indexed past.
fn check_discrete(n: usize, positions: usize, days: &Option<Vec<f64>>) -> Result<(), Diagnostic> {
    let days_len = days.as_ref().map_or(n, Vec::len);
    if n == 0 || positions != n || days_len != n {
        return Err(Diagnostic::refused(format!(
            "annotation axis: {n} categories, {positions} positions and {days_len} days"
        )));
    }
    Ok(())
}

fn category_index(domain: &[String], value: &str, site: &Site) -> Option<usize> {
    let found = domain.iter().position(|c| c == value);
    if found.is_none() {
        site.nothing_drawn(format!("{value:?} is not a category on the axis"));
    }
    found
}

/// Linear interpolation by days between neighboring points in day order,
/// extended past the ends along the end segments; the caller clamps. A
/// single point is its own position.
fn interpolate(days: &[f64], positions: &[f64], d: f64) -> f64 {
    if positions.len() < 2 {
        return positions[0];
    }
    let mut pts: Vec<(f64, f64)> = days.iter().copied().zip(positions.iter().copied()).collect();
    pts.sort_by(|a, b| a.0.total_cmp(&b.0));
    let k = pts.windows(2).position(|w| d <= w[1].0).unwrap_or(pts.len() - 2);
    let ((d0, p0), (d1, p1)) = (pts[k], pts[k + 1]);
    if d1 == d0 {
        return p0;
    }
    p0 + (d - d0) / (d1 - d0) * (p1 - p0)
}

/// Each date slot covers `[its day, the next slot's day)` in day order; the
/// last covers the median gap after its day (a lone slot, one day).
fn slot_ends(days: &[f64]) -> Vec<(usize, f64, f64)> {
    let mut order: Vec<usize> = (0..days.len()).collect();
    order.sort_by(|&a, &b| days[a].total_cmp(&days[b]));
    let mut gaps: Vec<f64> = order.windows(2).map(|w| days[w[1]] - days[w[0]]).collect();
    gaps.sort_by(f64::total_cmp);
    let step = match gaps.len() {
        0 => 1.0,
        n if n % 2 == 1 => gaps[n / 2],
        n => (gaps[n / 2 - 1] + gaps[n / 2]) / 2.0,
    };
    order
        .iter()
        .enumerate()
        .map(|(k, &i)| (i, days[i], order.get(k + 1).map_or(days[i] + step, |&j| days[j])))
        .collect()
}

fn band_date_span(
    geometry: BandGeometry,
    days: &[f64],
    a: &Anchor,
    site: &Site,
) -> Result<Option<(f64, f64)>, Diagnostic> {
    let slots = slot_ends(days);
    let hits: Vec<usize> = match a {
        Anchor::Value { value } => {
            let d = date_days(value, site)?;
            slots.iter().filter(|&&(_, lo, hi)| lo <= d && d < hi).map(|&(i, _, _)| i).collect()
        }
        Anchor::Range { from, to } => {
            let from = from.as_ref().map(|v| date_days(v, site)).transpose()?;
            let to = to.as_ref().map(|v| date_days(v, site)).transpose()?;
            slots
                .iter()
                .filter(|&&(_, d, _)| from.is_none_or(|f| d >= f) && to.is_none_or(|t| d < t))
                .map(|&(i, _, _)| i)
                .collect()
        }
        Anchor::Category { .. } => unreachable!("categories are matched by the caller"),
    };
    if hits.is_empty() {
        site.nothing_drawn("its date names no slot on the axis");
        return Ok(None);
    }
    let lo = hits.iter().map(|&i| geometry.edge(i, 0.0)).fold(f64::INFINITY, f64::min);
    let hi = hits.iter().map(|&i| geometry.edge(i, 1.0)).fold(f64::NEG_INFINITY, f64::max);
    Ok(Some((lo, hi)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostic::collect;
    use crate::families::temporal::date_text_day;

    const SITE: Site = Site { chart_id: "c", index: 3 };

    fn num(n: f64) -> Scalar {
        Scalar::Number(n)
    }
    fn date(s: &str) -> Scalar {
        Scalar::Date(s.into())
    }
    fn value(v: Scalar) -> Anchor {
        Anchor::Value { value: v }
    }
    fn range(from: Option<Scalar>, to: Option<Scalar>) -> Anchor {
        Anchor::Range { from, to }
    }
    fn cat(s: &str) -> Anchor {
        Anchor::Category { value: s.into() }
    }
    fn iso(d: f64) -> String {
        let (y, m, dd) = crate::families::temporal::civil_from_days(d as i64);
        format!("{y:04}-{m:02}-{dd:02}")
    }
    fn day(s: &str) -> f64 {
        date_text_day(s).unwrap()
    }
    fn close(a: (f64, f64), b: (f64, f64)) {
        assert!((a.0 - b.0).abs() < 1e-9 && (a.1 - b.1).abs() < 1e-9, "{a:?} != {b:?}");
    }
    fn payload_err(r: Result<impl std::fmt::Debug, Diagnostic>) {
        let d = r.expect_err("refused");
        assert_eq!(d.code, "ERR-ANNOTATION-PAYLOAD");
        assert_eq!(d.params["index"], 3);
    }
    /// The result plus whether `WARN-ANNOTATION-NOTHING-DRAWN` was recorded.
    fn warned<T>(f: impl FnOnce() -> T) -> (T, bool) {
        let (out, warnings) = collect(f);
        (out, warnings.iter().any(|w| w.code == "WARN-ANNOTATION-NOTHING-DRAWN"))
    }

    fn linear() -> AxisMap {
        AxisMap::Linear { domain: (0.0, 100.0), range: (200.0, 0.0) }
    }
    fn time() -> AxisMap {
        AxisMap::Time { extent: (day("2024-01-01"), day("2024-01-11")), range: (10.0, 110.0) }
    }
    /// Three slots over 0..300 with no padding: [0,100) [100,200) [200,300).
    fn band(days: Option<Vec<f64>>) -> AxisMap {
        AxisMap::Band {
            domain: vec!["2024-01-01".into(), "2024-02-01".into(), "2024-03-01".into()],
            geometry: BandGeometry::new(3, 0.0, 300.0, 0.0, 0.0),
            days,
            range: (0.0, 300.0),
        }
    }
    fn monthly_days() -> Option<Vec<f64>> {
        Some(vec![day("2024-01-01"), day("2024-02-01"), day("2024-03-01")])
    }
    fn point(days: Option<Vec<f64>>) -> AxisMap {
        AxisMap::Point {
            domain: vec!["a".into(), "b".into(), "c".into()],
            positions: vec![50.0, 100.0, 200.0],
            days,
            range: (0.0, 250.0),
        }
    }

    #[test]
    fn range_px_is_sorted_and_reads_the_full_extent_on_discrete_maps() {
        assert_eq!(linear().range_px(), (0.0, 200.0));
        assert_eq!(band(None).range_px(), (0.0, 300.0));
        assert_eq!(point(None).range_px(), (0.0, 250.0));
    }

    #[test]
    fn continuous_value_is_its_position_even_outside_the_domain() {
        assert_eq!(linear().position(&num(25.0), &SITE).unwrap(), Some(150.0));
        assert_eq!(linear().position(&num(150.0), &SITE).unwrap(), Some(-100.0));
        close(linear().span(&value(num(25.0)), &SITE).unwrap().unwrap(), (150.0, 150.0));
        let log = AxisMap::Log { domain: (1.0, 100.0), range: (0.0, 100.0) };
        assert!((log.position(&num(10.0), &SITE).unwrap().unwrap() - 50.0).abs() < 1e-9);
        assert_eq!(time().position(&date("2024-01-06"), &SITE).unwrap(), Some(60.0));
    }

    #[test]
    fn bent_maps_through_its_bend_on_both_signs() {
        let sqrt = AxisMap::Bent { bend: Bend::Pow(0.5), domain: (0.0, 100.0), range: (200.0, 0.0) };
        assert!((sqrt.position(&num(25.0), &SITE).unwrap().unwrap() - 100.0).abs() < 1e-9);
        let symlog = AxisMap::Bent { bend: Bend::Symlog(1.0), domain: (-99.0, 99.0), range: (0.0, 100.0) };
        assert!((symlog.position(&num(0.0), &SITE).unwrap().unwrap() - 50.0).abs() < 1e-9);
        assert!((symlog.position(&num(-9.0), &SITE).unwrap().unwrap() - 25.0).abs() < 1e-9);
        close(sqrt.span(&range(Some(num(25.0)), None), &SITE).unwrap().unwrap(), (0.0, 100.0));
        let flat = AxisMap::Bent { bend: Bend::Pow(2.0), domain: (5.0, 5.0), range: (0.0, 40.0) };
        assert_eq!(flat.position(&num(5.0), &SITE).unwrap(), Some(20.0));
        payload_err(sqrt.position(&date("2024-01-01"), &SITE));
        payload_err(sqrt.span(&cat("East"), &SITE));
    }

    #[test]
    fn continuous_range_reads_a_null_bound_as_the_range_edge() {
        close(linear().span(&range(Some(num(25.0)), None), &SITE).unwrap().unwrap(), (0.0, 150.0));
        close(linear().span(&range(None, Some(num(50.0))), &SITE).unwrap().unwrap(), (100.0, 200.0));
        close(time().span(&range(Some(date("2024-01-03")), None), &SITE).unwrap().unwrap(), (30.0, 110.0));
        let log = AxisMap::Log { domain: (1.0, 100.0), range: (0.0, 100.0) };
        close(log.span(&range(None, Some(num(10.0))), &SITE).unwrap().unwrap(), (0.0, 50.0));
    }

    #[test]
    fn a_point_anchor_sits_at_a_category_s_slot_center_or_its_point() {
        assert_eq!(band(None).at(&cat("2024-02-01"), &SITE).unwrap(), Some(150.0));
        assert_eq!(point(None).at(&cat("c"), &SITE).unwrap(), Some(200.0));
        assert_eq!(linear().at(&value(num(25.0)), &SITE).unwrap(), Some(150.0));
        payload_err(linear().at(&cat("East"), &SITE));
        let (out, drew_nothing) = warned(|| band(None).at(&cat("East"), &SITE).unwrap());
        assert!(out.is_none() && drew_nothing);
    }

    #[test]
    fn continuous_category_is_a_payload_error() {
        payload_err(linear().span(&cat("East"), &SITE));
        payload_err(time().span(&cat("East"), &SITE));
    }

    #[test]
    fn time_reads_the_clock_as_the_day_fraction() {
        let p = time().position(&date("2024-01-01T12:00"), &SITE).unwrap().unwrap();
        assert!((p - 15.0).abs() < 1e-9);
    }

    #[test]
    fn a_date_on_linear_or_a_number_on_time_is_a_payload_error() {
        payload_err(linear().position(&date("2024-01-01"), &SITE));
        payload_err(time().position(&num(3.0), &SITE));
        payload_err(time().position(&date("soon"), &SITE));
        payload_err(AxisMap::Log { domain: (1.0, 10.0), range: (0.0, 1.0) }.position(&num(0.0), &SITE));
    }

    #[test]
    fn band_category_is_its_slot_and_an_unknown_one_warns() {
        let (span, warn) = warned(|| band(None).span(&cat("2024-02-01"), &SITE).unwrap());
        close(span.unwrap(), (100.0, 200.0));
        assert!(!warn);
        let (span, warn) = warned(|| band(None).span(&cat("West"), &SITE).unwrap());
        assert_eq!(span, None);
        assert!(warn);
    }

    #[test]
    fn band_date_value_picks_the_slot_it_falls_in() {
        let b = band(monthly_days());
        close(b.span(&value(date("2024-02-14")), &SITE).unwrap().unwrap(), (100.0, 200.0));
        close(b.span(&value(date("2024-01-01")), &SITE).unwrap().unwrap(), (0.0, 100.0));
        // Last slot: the median gap (30 days, between 29 and 31) after its day.
        close(b.span(&value(date("2024-03-30")), &SITE).unwrap().unwrap(), (200.0, 300.0));
        let (span, warn) = warned(|| b.span(&value(date("2024-04-01")), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
        let (span, warn) = warned(|| b.span(&value(date("2023-12-31")), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
    }

    #[test]
    fn a_lone_date_slot_spans_one_day_and_the_last_slot_the_median_gap() {
        let lone = AxisMap::Band {
            domain: vec!["2024-01-01".into()],
            geometry: BandGeometry::new(1, 0.0, 100.0, 0.0, 0.0),
            days: Some(vec![day("2024-01-01")]),
            range: (0.0, 100.0),
        };
        close(lone.span(&value(date("2024-01-01T23:00")), &SITE).unwrap().unwrap(), (0.0, 100.0));
        let (span, warn) = warned(|| lone.span(&value(date("2024-01-02")), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
        // Gaps 1, 2 and 10 days: the median is 2, not the mean or the max.
        let b = AxisMap::Band {
            domain: vec!["a".into(), "b".into(), "c".into(), "d".into()],
            geometry: BandGeometry::new(4, 0.0, 400.0, 0.0, 0.0),
            days: Some(vec![0.0, 1.0, 3.0, 13.0]),
            range: (0.0, 400.0),
        };
        close(b.span(&value(date(&iso(14.5))), &SITE).unwrap().unwrap(), (300.0, 400.0));
        let (span, warn) = warned(|| b.span(&value(date(&iso(15.0))), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
    }

    #[test]
    fn a_zero_width_time_extent_or_mismatched_vectors_are_refused() {
        let flat = AxisMap::Time { extent: (day("2024-01-01"), day("2024-01-01")), range: (0.0, 100.0) };
        assert_eq!(flat.position(&date("2024-01-01"), &SITE).unwrap_err().code, "ERR-ENGINE-REFUSED");
        let ragged = AxisMap::Point { domain: vec!["a".into(), "b".into()], positions: vec![1.0], days: None, range: (0.0, 1.0) };
        assert_eq!(ragged.span(&cat("a"), &SITE).unwrap_err().code, "ERR-ENGINE-REFUSED");
        let empty = AxisMap::Band { domain: vec![], geometry: BandGeometry::new(0, 0.0, 1.0, 0.0, 0.0), days: None, range: (0.0, 1.0) };
        assert_eq!(empty.span(&cat("a"), &SITE).unwrap_err().code, "ERR-ENGINE-REFUSED");
    }

    #[test]
    fn point_dates_interpolate_in_day_order() {
        let p = AxisMap::Point {
            domain: vec!["c".into(), "b".into(), "a".into()],
            positions: vec![50.0, 100.0, 200.0],
            days: Some(vec![20.0, 10.0, 0.0]),
            range: (0.0, 250.0),
        };
        assert_eq!(p.position(&date(&iso(5.0)), &SITE).unwrap(), Some(150.0));
    }

    #[test]
    fn band_date_slots_follow_day_order_not_domain_order() {
        let b = AxisMap::Band {
            domain: vec!["2024-03-01".into(), "2024-01-01".into(), "2024-02-01".into()],
            geometry: BandGeometry::new(3, 0.0, 300.0, 0.0, 0.0),
            days: Some(vec![day("2024-03-01"), day("2024-01-01"), day("2024-02-01")]),
            range: (0.0, 300.0),
        };
        close(b.span(&value(date("2024-01-20")), &SITE).unwrap().unwrap(), (100.0, 200.0));
    }

    #[test]
    fn band_date_range_is_the_union_of_slots_it_starts() {
        let b = band(monthly_days());
        close(
            b.span(&range(Some(date("2024-01-15")), Some(date("2024-03-01"))), &SITE).unwrap().unwrap(),
            (100.0, 200.0),
        );
        close(b.span(&range(None, Some(date("2024-02-02"))), &SITE).unwrap().unwrap(), (0.0, 200.0));
        close(b.span(&range(Some(date("2024-02-01")), None), &SITE).unwrap().unwrap(), (100.0, 300.0));
        let (span, warn) = warned(|| b.span(&range(Some(date("2024-05-01")), None), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
    }

    #[test]
    fn band_without_days_refuses_values_and_ranges() {
        payload_err(band(None).span(&value(date("2024-01-01")), &SITE));
        payload_err(band(None).span(&range(None, None), &SITE));
        payload_err(band(None).position(&date("2024-01-01"), &SITE));
    }

    #[test]
    fn band_position_is_the_slot_midpoint_or_none() {
        let b = band(monthly_days());
        assert_eq!(b.position(&date("2024-02-10"), &SITE).unwrap(), Some(150.0));
        let (p, warn) = warned(|| b.position(&date("2025-01-01"), &SITE).unwrap());
        assert_eq!((p, warn), (None, true));
    }

    #[test]
    fn point_category_spans_half_the_nearest_gap_each_side() {
        let p = point(None);
        close(p.span(&cat("a"), &SITE).unwrap().unwrap(), (25.0, 75.0));
        close(p.span(&cat("b"), &SITE).unwrap().unwrap(), (75.0, 125.0));
        // Clamped to the full extent.
        let edge = AxisMap::Point { domain: vec!["a".into(), "b".into()], positions: vec![10.0, 110.0], days: None, range: (0.0, 120.0) };
        close(edge.span(&cat("a"), &SITE).unwrap().unwrap(), (0.0, 60.0));
        close(edge.span(&cat("b"), &SITE).unwrap().unwrap(), (60.0, 120.0));
        let lone = AxisMap::Point { domain: vec!["a".into()], positions: vec![40.0], days: None, range: (0.0, 80.0) };
        close(lone.span(&cat("a"), &SITE).unwrap().unwrap(), (0.0, 80.0));
        let (span, warn) = warned(|| p.span(&cat("z"), &SITE).unwrap());
        assert_eq!((span, warn), (None, true));
    }

    #[test]
    fn point_dates_interpolate_between_neighbors_and_extrapolate_past_the_ends() {
        let p = point(Some(vec![0.0, 10.0, 20.0]));
        let at = |d: f64| p.position(&Scalar::Date(iso(d)), &SITE).unwrap().unwrap();
        assert!((at(5.0) - 75.0).abs() < 1e-9);
        assert!((at(15.0) - 150.0).abs() < 1e-9);
        // Off the axis: the placer warns and draws nothing, never the edge.
        assert!(at(-100.0) < 0.0);
        assert!(at(100.0) > 250.0);
        let r = p.span(&range(None, Some(Scalar::Date(iso(10.0)))), &SITE).unwrap().unwrap();
        close(r, (0.0, 100.0));
        payload_err(point(None).position(&date("2024-01-01"), &SITE));
        payload_err(point(None).span(&range(None, None), &SITE));
    }

    #[test]
    fn point_numbers_interpolate_like_dates() {
        let p = point(Some(vec![0.0, 10.0, 20.0]));
        let at = |n: f64| p.position(&Scalar::Number(n), &SITE).unwrap().unwrap();
        assert!((at(5.0) - 75.0).abs() < 1e-9);
        assert!(at(100.0) > 250.0);
    }
}
