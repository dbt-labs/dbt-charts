//! The time domain: the calendar grain of a time x axis, its values keyed by the bucket that
//! holds them, and the buckets after the optional calendar gap fill. Label cadence is layout's.

use std::collections::HashSet;

use board_model::style::axis::FillMode;
use column_types::Dataset;
use d3rs::calendar::days_from_civil;
use diagnostic::{diag, py_str_repr, refuse, Diagnostic};

use crate::bound::{GrainDrop, Month, TimeDomain};
use crate::style::axis::AxisLadder;
use crate::style::Tiers;
use crate::temporal::{complete_buckets, detect_time_unit, floor_to_bucket_start, TimeUnit, VegaType};
use crate::time_axis::{Dates, Grain, TimeAxis, Unreadable};
use crate::x_type::{parse_date, resolve_cartesian_x_type, Grain as XGrain, XAxis};

/// `core/constants/axis.py::MAX_ORDINAL_BUCKETS`: the most empty buckets a calendar grain may
/// span before the x-type law drops it.
const MAX_ORDINAL_BUCKETS: usize = 60;

/// `axis_x.time_unit` as authored. Absent and `auto` differ for the bar orientation law (a set
/// unit, `auto` included, draws vertical bars) and agree for the domain: resolve collapses
/// `auto` to absent once orientation is decided, and both detect the grain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeUnitSetting {
    Absent,
    Auto,
    Unit(TimeUnit),
}

impl TimeUnitSetting {
    /// Whether the author set a unit at all, which is what the bar orientation law asks.
    pub fn is_set(self) -> bool {
        self != Self::Absent
    }

    fn authored(self) -> Option<TimeUnit> {
        match self {
            Self::Unit(unit) => Some(unit),
            Self::Absent | Self::Auto => None,
        }
    }
}

/// What the law reads off the chart.
pub struct TimeInput<'a> {
    pub chart_id: &'a str,
    pub family: &'a str,
    pub x_field: &'a str,
    pub time_unit: TimeUnitSetting,
    /// Why production labels the axis without its grain, when it does: the grain is still read
    /// into `TimeDomain.unit`, and the reason lands in `TimeDomain.grain_drop`. An axis with no
    /// grain to read has none to drop, so it carries no reason.
    pub grain_drop: Option<GrainDrop>,
    /// Whether the axis synthesizes the buckets no row falls in (`axis_x.fill`).
    pub fill_gaps: bool,
    /// `axis_x.fiscal_year_start_month`; `None` is a calendar year.
    pub fiscal_start: Option<Month>,
}

/// The domain of the distinct x values `x` (first seen), or `None` for no values at all.
///
/// A detected grain keys each value by the start of the bucket enclosing it (a month-end belongs
/// to its month, a date a fiscal start places in another quarter to that quarter), and
/// `fill_gaps` adds every empty bucket between the first and the last. With no grain (fewer than
/// two distinct dates, a sub-daily value, no calendar cadence) the axis is continuous: the
/// buckets are the values themselves, by instant.
///
/// Detection refuses at 10% or more unparseable values; a value that names no date refuses
/// with `ERR-X-VALUE-NOT-A-DATE` when the axis reads it.
pub fn time_domain(x: &[String], input: &TimeInput) -> Result<Option<TimeDomain>, Diagnostic> {
    if x.is_empty() {
        return Ok(None);
    }
    let unit = match input.time_unit.authored() {
        Some(unit) => Some(unit),
        None => detect(x)?,
    };
    let fiscal = input.fiscal_start.map_or(1, Month::get);
    // The axis floors an instant to a calendar bucket; a fiscal start moves year and quarter
    // boundaries off the calendar, so those buckets are read as the instants they start at.
    let moved = fiscal != 1 && unit.is_some_and(TimeUnit::is_fiscal_sensitive);
    let mut axis = TimeAxis::new(Grain { unit: unit.filter(|_| !moved), part: None, band: false }, Default::default());
    let unreadable = |e: Unreadable| e.diagnostic(input.chart_id, input.x_field, input.family);
    x.iter().try_for_each(|text| axis.add(text)).map_err(unreadable)?;
    let keys = match unit {
        Some(unit) => {
            let mut starts: Vec<(i32, u8, u8)> =
                x.iter().map(|text| floor_to_bucket_start(axis.date(text).expect("read"), unit, fiscal)).collect();
            starts.sort_unstable();
            starts.dedup();
            let keys: Vec<String> = starts.iter().map(|&(y, m, d)| format!("{y:04}-{m:02}-{d:02}")).collect();
            if !input.fill_gaps {
                keys
            } else {
                keys.iter().try_for_each(|key| axis.add(key)).map_err(unreadable)?;
                complete_buckets(&axis.dates(&keys), unit, fiscal).text
            }
        }
        None => {
            let mut keys = x.to_vec();
            axis.sort_by_instant(&mut keys);
            keys
        }
    };
    let buckets = keys.iter().map(|key| axis.read(key).cloned().map_err(unreadable)).collect::<Result<_, _>>()?;
    Ok(Some(TimeDomain { axis, unit, grain_drop: input.grain_drop.filter(|_| unit.is_some()), buckets, fiscal_start: input.fiscal_start }))
}

/// The leaves of a `family` chart's x axis that move the x-type law's verdict or print its labels
/// by rules the time axis does not model: the gap fill, the fiscal year, a label format.
pub fn refuse_unmodeled_leaves(tiers: &Tiers<'_>, x_ladder: &AxisLadder<'_>, family: &str) -> Result<(), Diagnostic> {
    let board = Some(&tiers.charts.axis_x);
    let moved = [tiers.own.axis_x, tiers.family.axis_x, board].into_iter().flatten().any(|axis| {
        axis.fill.is_some_and(|fill| fill != FillMode::Null) || axis.fiscal_year_start_month.is_some_and(|month| month != 1)
    });
    if moved {
        return Err(Diagnostic::refused(refuse!("a {family} time axis that sets `fill` or `fiscal_year_start_month` is not supported")));
    }
    if x_ladder.labels_format().is_some() {
        return Err(Diagnostic::refused(refuse!("a {family} time axis with a label format is not supported")));
    }
    Ok(())
}

/// The time domain of the x `x_field` of a `family` chart over its `texts` (the x cells of the rows that
/// have one): the x-type law's verdict on the whole column, which must read as a time scale, then the
/// grain it settles on, dropped for the scaffold budget or authored `time_unit: none`.
pub fn x_domain(chart_id: &str, family: &str, dataset: &Dataset, x_field: &str, authored_none: bool, texts: &[&str]) -> Result<TimeDomain, Diagnostic> {
    let law_axis = XAxis {
        time_unit: authored_none.then_some("none"),
        authored_type: None,
        raw_type: None,
        fill: Some("null"),
        fiscal_year_start_month: 1,
        max_ordinal_buckets: MAX_ORDINAL_BUCKETS,
    };
    let law = resolve_cartesian_x_type(&dataset.schema, &dataset.rows, x_field, &law_axis, family, false, &[]).map_err(Diagnostic::refused)?;
    if law.vl_type != VegaType::Temporal {
        return Err(Diagnostic::refused(refuse!("a {family} on an x the x-type law does not read as a time scale is not supported")));
    }
    let time_unit = match law.grain {
        XGrain::Calendar(unit) => TimeUnitSetting::Unit(unit),
        XGrain::None => TimeUnitSetting::Absent,
        XGrain::Part(_) => return Err(diag::err_internal(format!("no time part is authored on a {family}"))),
    };
    let mut seen = HashSet::new();
    let distinct: Vec<String> = texts.iter().filter(|text| seen.insert(**text)).map(|text| text.to_string()).collect();
    // The grain is still read into the domain (`unit`); the reason says why production labels the
    // axis without it.
    let grain_drop = (law.grain == XGrain::None).then_some(if authored_none { GrainDrop::AuthoredNone } else { GrainDrop::ScaffoldBudget });
    let input = TimeInput { chart_id, family, x_field, time_unit, grain_drop, fill_gaps: false, fiscal_start: None };
    time_domain(&distinct, &input)?.ok_or_else(|| diag::err_internal(format!("a {family} with rows has an x to span")))
}

const MS_PER_DAY: f64 = 86_400_000.0;
const DAY_MS: i64 = 86_400_000;
/// A header names the time of day alone while the rows lie within two days (`TOOLTIP_SPAN_MS`).
const HEADER_SPAN_MS: i64 = 2 * DAY_MS;

/// The first and last instant of a domain with a time of day, in whole milliseconds.
pub struct ClockExtent {
    pub first: i64,
    pub last: i64,
    /// Every value names an absolute instant (an offset or `Z`); none is a naive clock reading.
    pub absolute: bool,
}

impl TimeDomain {
    /// Whether every x is a timestamp that names an absolute instant, so its clock prints in the board zone.
    pub fn aware(&self) -> bool {
        self.buckets.iter().all(|b| b.absolute && b.iso.len() > 10)
    }

    /// `SubdaySource::extent`: the span of the values production reads a time of day off (a short date form is
    /// none), `None` without a time of day among them, with a mix of absolute and naive clocks, or with fewer
    /// than two instants.
    pub fn clock_extent(&self) -> Option<ClockExtent> {
        let read: Vec<_> = self.buckets.iter().filter(|b| b.iso.len() >= 10).collect();
        if !read.iter().any(|b| b.timed) {
            return None;
        }
        let mut instants: Vec<i64> = read.iter().map(|b| (b.at * MS_PER_DAY).round() as i64).collect();
        instants.sort_unstable();
        instants.dedup();
        let absolute = read.iter().any(|b| b.absolute);
        if (absolute && read.iter().any(|b| !b.absolute)) || instants.len() < 2 {
            return None;
        }
        Some(ClockExtent { first: instants[0], last: instants[instants.len() - 1], absolute })
    }

    /// The tooltip header of a grainless axis over timed rows (`Header::of`, `subday_header_for`): the
    /// 12-hour clock of the row, with the date where the rows cross a midnight; `None` for a grained axis, rows with no time of day
    /// and rows spanning more than two days, which read the date alone.
    pub fn clock_header(&self) -> Result<Option<ClockHeader>, Diagnostic> {
        let Some(extent) = self.clock_extent().filter(|extent| self.unit.is_none() && extent.last - extent.first <= HEADER_SPAN_MS) else {
            return Ok(None);
        };
        let aware = self.aware();
        let wall = |ms: i64| if aware { crate::time_zone::wall_ms(ms).map_err(diag::err_internal) } else { Ok(ms) };
        let seconds = self.buckets.iter().filter(|b| b.iso.len() >= 10).any(|b| ((b.at * MS_PER_DAY).round() as i64).rem_euclid(60_000) / 1000 != 0);
        let dated = wall(extent.first)?.div_euclid(DAY_MS) != wall(extent.last)?.div_euclid(DAY_MS);
        Ok(Some(ClockHeader { aware, seconds, dated }))
    }
}

/// How a tooltip names the time of a row (`Header`).
pub struct ClockHeader {
    aware: bool,
    seconds: bool,
    dated: bool,
}

impl ClockHeader {
    /// The header of the row at the instant `at` (days): its wall clock, in the board zone when the column is aware.
    pub fn text(&self, at: f64) -> Result<String, Diagnostic> {
        let wall = if self.aware { crate::time_zone::wall_day(at).map_err(diag::err_internal)? } else { at };
        let locale = crate::time_locale::active_time_locale().map_err(diag::err_internal)?;
        let ms = (wall * MS_PER_DAY).round();
        let say = |spec: &str| d3rs::time_format::format(spec, ms, 0.0, &locale).map_err(diag::err_internal);
        let seconds = if self.seconds { ":%S" } else { "" };
        let time = format!("{}{}", say(&format!("%-I:%M{seconds}"))?, say("%p")?.to_lowercase());
        if self.dated {
            Ok(format!("{}, {time}", say("%-d %b %Y")?))
        } else {
            Ok(time)
        }
    }
}

/// `detect_time_unit` over the distinct x texts: `None` below two dates, for a sub-daily value
/// and without a calendar cadence.
fn detect(x: &[String]) -> Result<Option<TimeUnit>, Diagnostic> {
    if x.len() < 2 {
        return Ok(None);
    }
    let parsed: Vec<_> = x.iter().map(|text| parse_date(text)).collect();
    let bad: Vec<&String> = x.iter().zip(&parsed).filter(|(_, p)| p.is_none()).map(|(text, _)| text).collect();
    if bad.len() * 10 >= x.len() {
        let examples = bad.iter().take(5).map(|text| py_str_repr(text)).collect::<Vec<_>>().join(", ");
        return Err(Diagnostic::refused(format!(
            "Couldn't auto-detect timeUnit: \u{2265}10% unparseable date values: [{examples}]. \
             Set style.axis_x.time_unit explicitly or fix the query."
        )));
    }
    let dated: Vec<((i32, u8, u8), bool)> = parsed.into_iter().flatten().collect();
    if dated.len() < 2 || dated.iter().any(|(_, sub_daily)| *sub_daily) {
        return Ok(None);
    }
    let dates = Dates {
        text: dated.iter().map(|((y, m, d), _)| format!("{y:04}-{m:02}-{d:02}")).collect(),
        day: dated.iter().map(|&((y, m, d), _)| Some(days_from_civil(y, m, d) as f64)).collect(),
        date: dated.iter().map(|&(date, _)| Some(date)).collect(),
    };
    Ok(detect_time_unit(&dates))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(fill_gaps: bool) -> TimeInput<'static> {
        TimeInput { chart_id: "c", family: "line", x_field: "d", time_unit: TimeUnitSetting::Absent, grain_drop: None, fill_gaps, fiscal_start: None }
    }

    fn domain(dates: &[&str], input: &TimeInput) -> Option<TimeDomain> {
        let x: Vec<String> = dates.iter().map(|d| d.to_string()).collect();
        time_domain(&x, input).unwrap()
    }

    fn keys(d: &TimeDomain) -> Vec<&str> {
        d.buckets.iter().map(|b| b.key.as_str()).collect()
    }

    #[test]
    fn the_grain_is_detected_from_every_distinct_date() {
        let unit = |dates: &[&str]| domain(dates, &input(false)).map(|d| d.unit);
        assert_eq!(unit(&["2024-01-01", "2025-01-01"]), Some(Some(TimeUnit::Year)));
        assert_eq!(unit(&["2024-01-01", "2024-04-01", "2024-05-01"]), Some(Some(TimeUnit::YearMonth)));
        assert_eq!(unit(&["2024-01-03", "2024-01-04"]), Some(Some(TimeUnit::YearMonthDate)));
        assert_eq!(unit(&["2024-01-03"]), Some(None));
    }

    #[test]
    fn fill_adds_every_empty_bucket_and_no_fill_keeps_the_present_ones() {
        let dates = ["2024-01-01", "2024-04-01", "2024-02-01"];
        assert_eq!(keys(&domain(&dates, &input(true)).unwrap()), ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]);
        assert_eq!(keys(&domain(&dates, &input(false)).unwrap()), ["2024-01-01", "2024-02-01", "2024-04-01"]);
    }

    #[test]
    fn a_value_is_keyed_by_the_bucket_holding_it_and_a_fiscal_start_moves_the_quarters() {
        let authored = TimeInput { time_unit: TimeUnitSetting::Unit(TimeUnit::YearMonth), ..input(false) };
        assert_eq!(keys(&domain(&["2024-01-31", "2024-02-29"], &authored).unwrap()), ["2024-01-01", "2024-02-01"]);
        let fiscal = TimeInput {
            time_unit: TimeUnitSetting::Unit(TimeUnit::YearQuarter),
            fiscal_start: Some(Month::new(2).unwrap()),
            ..input(true)
        };
        // A February start puts January in the quarter that began the previous November.
        assert_eq!(keys(&domain(&["2024-01-15", "2024-05-20"], &fiscal).unwrap()), ["2023-11-01", "2024-02-01", "2024-05-01"]);
    }

    #[test]
    fn a_continuous_axis_keeps_its_values_by_instant() {
        let d = domain(&["2024-03-05", "2024-01-02T10:30:00"], &input(true)).unwrap();
        assert_eq!(d.unit, None);
        assert_eq!(keys(&d), ["2024-01-02T10:30:00", "2024-03-05"]);
    }

    #[test]
    fn ten_percent_unparseable_refuses_in_detections_words_fewer_name_no_date() {
        let ten: Vec<String> = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01", "2024-07-01", "2024-08-01", "2024-09-01", "soon"]
            .iter().map(|d| d.to_string()).collect();
        let err = time_domain(&ten, &input(false)).unwrap_err();
        assert!(err.contains("Couldn't auto-detect timeUnit: \u{2265}10% unparseable date values: ['soon']. Set style.axis_x.time_unit"));
        let eleven: Vec<String> = ten.iter().cloned().chain(["2024-10-01".to_string()]).collect();
        let err = time_domain(&eleven, &input(false)).unwrap_err();
        assert_eq!(err.registered().unwrap().id, "ERR-X-VALUE-NOT-A-DATE");
        let authored = TimeInput { time_unit: TimeUnitSetting::Unit(TimeUnit::YearMonth), ..input(false) };
        let err = time_domain(&ten, &authored).unwrap_err();
        assert_eq!(err.registered().unwrap().id, "ERR-X-VALUE-NOT-A-DATE");
    }

    #[test]
    fn auto_and_absent_bind_one_domain_but_only_auto_counts_as_set() {
        let dates = ["2024-01-01", "2024-02-01", "2024-04-01"];
        let auto = TimeInput { time_unit: TimeUnitSetting::Auto, ..input(true) };
        let (a, b) = (domain(&dates, &input(true)).unwrap(), domain(&dates, &auto).unwrap());
        assert_eq!((a.unit, keys(&a)), (b.unit, keys(&b)));
        assert!(!TimeUnitSetting::Absent.is_set());
        assert!(TimeUnitSetting::Auto.is_set() && TimeUnitSetting::Unit(TimeUnit::Year).is_set());
    }

    #[test]
    fn a_dropped_grain_is_flagged_with_its_reason_and_keeps_the_grain_it_dropped() {
        let dates = ["2024-01-01", "2024-02-01", "2024-04-01"];
        let kept = |drop| {
            let d = domain(&dates, &TimeInput { grain_drop: drop, ..input(false) }).unwrap();
            (d.unit, d.grain_drop)
        };
        let month = Some(TimeUnit::YearMonth);
        assert_eq!(kept(None), (month, None));
        assert_eq!(kept(Some(GrainDrop::ScaffoldBudget)), (month, Some(GrainDrop::ScaffoldBudget)));
        assert_eq!(kept(Some(GrainDrop::AuthoredNone)), (month, Some(GrainDrop::AuthoredNone)));
    }

    #[test]
    fn an_axis_with_no_grain_has_none_to_drop() {
        let x = vec!["2024-01-03".to_string()];
        let input = TimeInput { grain_drop: Some(GrainDrop::AuthoredNone), ..input(false) };
        let domain = time_domain(&x, &input).unwrap().unwrap();
        assert_eq!((domain.unit, domain.grain_drop), (None, None));
    }
}
