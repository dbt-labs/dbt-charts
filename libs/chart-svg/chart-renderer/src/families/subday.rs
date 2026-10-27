//! Sub-day continuous time axes: the clock vocabulary, the clock-aligned tick ladder and the tooltip
//! header production gives a temporal x whose values carry a time of day
//! (`time_unit_detect.py`'s `default_subday_label_expr_for`, `default_subday_tick_interval`,
//! `subday_header_for`).

use super::temporal::{civil_from_days, clock_instants_at, AuthoredTickCadence};
use crate::common::AxisStyle;
use crate::time_axis::TimeAxis;

/// The clock the instants of an axis are read on: an offset or a bare date is an absolute instant, a bare
/// timestamp is local time (the render pins the zone, so both read alike), and a mix has no single reader.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Zone {
    Naive,
    Absolute,
    Mixed,
}

const DAY_MS: i64 = 86_400_000;

/// The distinct instants of a temporal x column, in milliseconds since the epoch.
#[derive(Clone, Debug)]
pub struct SubdaySource {
    instants: Vec<i64>,
    zone: Zone,
    timed: bool,
}

impl SubdaySource {
    /// The column's own values, as the axis read them: one it did not read, or a short date form, is skipped.
    pub(crate) fn of<'a>(axis: &TimeAxis, values: impl IntoIterator<Item = &'a str>) -> Self {
        let mut instants = Vec::new();
        let (mut absolute, mut naive, mut timed) = (false, false, false);
        for text in values {
            let Some(instant) = axis.get(text).filter(|_| text.len() >= 10) else { continue };
            instants.push((instant.at * DAY_MS as f64).round() as i64);
            timed |= instant.timed;
            if instant.absolute {
                absolute = true;
            } else {
                naive = true;
            }
        }
        instants.sort_unstable();
        instants.dedup();
        let zone = match (absolute, naive) {
            (true, true) => Zone::Mixed,
            (_, true) => Zone::Naive,
            _ => Zone::Absolute,
        };
        Self { instants, zone, timed }
    }

    /// `_subday_instants`: the sorted distinct instants the axis spans, and whether they read as absolute ones.
    /// `domain` is an authored scale domain, which decides the extent once set. `None` without a time of day, with
    /// a mix of clocks, or with fewer than two instants.
    fn extent(&self, domain: Option<[&str; 2]>) -> Option<Extent> {
        if !self.timed {
            return None;
        }
        let (instants, zone) = match domain {
            None => (self.instants.clone(), self.zone),
            Some(pair) => {
                let mut bounds = TimeAxis::new(crate::time_axis::Grain::default(), std::collections::HashMap::new());
                for text in pair {
                    bounds.add(text).ok()?;
                }
                let bound = Self::of(&bounds, pair);
                let zone = match (self.zone, bound.zone) {
                    (a, b) if a == b => a,
                    _ => Zone::Mixed,
                };
                (bound.instants, zone)
            }
        };
        if zone == Zone::Mixed || instants.len() < 2 {
            return None;
        }
        Some(Extent { first: instants[0], last: *instants.last().expect("two instants"), absolute: zone == Zone::Absolute, instants })
    }
}

struct Extent {
    instants: Vec<i64>,
    first: i64,
    last: i64,
    absolute: bool,
}

impl Extent {
    fn span_ms(&self) -> i64 {
        self.last - self.first
    }
}

const SPAN_CEILING_MS: i64 = 365 * DAY_MS;
const TICK_PITCH_PX: f64 = 40.0;
const ESTIMATED_Y_AXIS_CHROME_PX: f64 = 100.0;
const LADDER_PITCH_PX: f64 = 64.0;
/// `sqrt(12 * 24)` hours: where d3's tick ladder steps from hours to days.
const DAY_STEP_BOUNDARY_HOURS: f64 = 16.970_562_748_477_14;
/// `sqrt(30 * 60)` seconds: where d3's tick ladder steps from seconds to minutes.
const MINUTE_STEP_BOUNDARY_SECONDS: f64 = 42.426_406_871_192_85;
const TOOLTIP_SPAN_MS: i64 = 2 * DAY_MS;

/// A clock-aligned tick step a person reads off a clock face: its unit's length in days and its step.
#[derive(Clone, Copy, PartialEq, Debug)]
pub struct ClockStep {
    unit_ms: i64,
    /// The count the unit's field (minute of the hour, hour of the day) wraps at.
    parent: i64,
    step: i64,
}

const MINUTE: i64 = 60_000;
const HOUR: i64 = 3_600_000;
const LADDER: [ClockStep; 10] = [
    ClockStep { unit_ms: MINUTE, parent: 60, step: 1 },
    ClockStep { unit_ms: MINUTE, parent: 60, step: 5 },
    ClockStep { unit_ms: MINUTE, parent: 60, step: 10 },
    ClockStep { unit_ms: MINUTE, parent: 60, step: 15 },
    ClockStep { unit_ms: MINUTE, parent: 60, step: 30 },
    ClockStep { unit_ms: HOUR, parent: 24, step: 1 },
    ClockStep { unit_ms: HOUR, parent: 24, step: 2 },
    ClockStep { unit_ms: HOUR, parent: 24, step: 3 },
    ClockStep { unit_ms: HOUR, parent: 24, step: 6 },
    ClockStep { unit_ms: HOUR, parent: 24, step: 12 },
];

impl ClockStep {
    /// Every aligned instant of `[lo, hi]`, d3's `interval.every(step).range(lo, hi + 1)`.
    pub fn instants(self, lo: f64, hi: f64) -> Vec<f64> {
        clock_instants_at(lo, hi, self.unit_ms, self.parent, self.step)
    }
}

/// How production labels and ticks a continuous temporal axis over timed values.
#[derive(Clone, Debug)]
pub struct SubdayPlan {
    /// The clock-aligned tick step, where the axis keeps no authored cadence.
    pub ladder: Option<ClockStep>,
    vocab: Option<Vocab>,
}

#[derive(Clone, Copy, Debug)]
struct Vocab {
    clock24: bool,
    narrow: bool,
    date_row: bool,
    hour_boundary: bool,
}

/// What the axis authors that decides its sub-day treatment.
pub struct AxisSetup<'a> {
    /// An authored `ticks.count` or `ticks.time_unit`.
    pub cadence: Option<AuthoredTickCadence>,
    /// Authored `labels.values`, which production keeps its own ticks beside.
    pub authored_values: bool,
    /// An authored label format or expression, which replaces the clock vocabulary.
    pub label_override: bool,
    /// The authored `scale.continuous.domain` endpoints, as text.
    pub domain: Option<[&'a str; 2]>,
    /// `labels.clock`: 24 or 12 (anything else reads the 12-hour clock).
    pub clock: Option<i64>,
    /// The chart's outer width, and the width left of it once the endpoint rail is reserved.
    pub outer_width: f64,
    pub available_width: f64,
}

/// The card width below which production's tiers call a chart tiny (`typography.width_tier`).
const TINY_MAX_PX: f64 = 352.5;

impl SubdayPlan {
    /// The treatment of an axis over `source`; `None` where production keeps Vega's own ticks and labels.
    pub fn of(source: &SubdaySource, axis: &AxisSetup) -> Option<Self> {
        if axis.label_override {
            return None;
        }
        let extent = source.extent(axis.domain)?;
        let span_ms = extent.span_ms();
        let (ladder, count, ceiling) = match axis.cadence {
            Some(AuthoredTickCadence::Count(n) | AuthoredTickCadence::UncappedCount(n)) => (None, Some(n), Some(n)),
            // An authored interval is a day or coarser: every tick lands on midnight.
            Some(AuthoredTickCadence::Interval(_)) => return None,
            None => match default_ladder(&extent, axis.available_width).filter(|_| !axis.authored_values) {
                Some((step, n)) => (Some(step), Some(n), Some(n)),
                None => {
                    let width = axis.available_width - ESTIMATED_Y_AXIS_CHROME_PX;
                    ((width > 0.0).then(|| (width / TICK_PITCH_PX).ceil() as usize)).map_or((None, None, None), |lower| {
                        (None, Some(lower), Some((axis.available_width / TICK_PITCH_PX).ceil() as usize))
                    })
                }
            },
        };
        let vocab = count.zip(ceiling).and_then(|(count, ceiling)| {
            let span_ms = span_ms as f64;
            if span_ms >= SPAN_CEILING_MS as f64
                || span_ms >= DAY_STEP_BOUNDARY_HOURS * HOUR as f64 * count as f64
                || span_ms < MINUTE_STEP_BOUNDARY_SECONDS * 1000.0 * ceiling as f64
            {
                return None;
            }
            Some(Vocab {
                clock24: axis.clock == Some(24),
                narrow: axis.outer_width < TINY_MAX_PX,
                date_row: midnight_count(extent.first, extent.last) > 1,
                hour_boundary: (extent.first + HOUR - 1).div_euclid(HOUR) * HOUR <= extent.last,
            })
        });
        Some(Self { ladder, vocab })
    }

    /// Whether every label stacks a date row under its text.
    pub fn stacked(&self) -> bool {
        self.vocab.is_some_and(|vocab| vocab.date_row)
    }

    /// The text of the tick at `at` (days), `index` counting the ticks Vega drew: one row, and the date row where the
    /// axis crosses a midnight.
    pub fn label(&self, at: f64, index: usize) -> Result<Option<(String, Option<String>)>, String> {
        self.vocab.map(|vocab| vocab.label(at, index)).transpose()
    }
}

/// `default_subday_tick_interval`: the finest clock step that fits the width, with the count of ticks it draws.
fn default_ladder(extent: &Extent, available_width: f64) -> Option<(ClockStep, usize)> {
    let max_ticks = ((available_width - ESTIMATED_Y_AXIS_CHROME_PX) / LADDER_PITCH_PX).floor();
    if extent.absolute || extent.span_ms() >= SPAN_CEILING_MS {
        return None;
    }
    LADDER.into_iter().find_map(|step| {
        let count = (extent.span_ms() + step.unit_ms * step.step - 1).div_euclid(step.unit_ms * step.step);
        (2 <= count && count as f64 <= max_ticks).then_some((step, count as usize))
    })
}

/// `_midnight_count`: the distinct days' midnights inside `[first, last]`, a wrapped day (opening and closing on a
/// midnight) not counting its closing one.
fn midnight_count(first: i64, last: i64) -> i64 {
    let start = (first + DAY_MS - 1).div_euclid(DAY_MS);
    let mut end = last.div_euclid(DAY_MS);
    if first.rem_euclid(DAY_MS) == 0 && last.rem_euclid(DAY_MS) == 0 && last > first {
        end -= 1;
    }
    (end - start + 1).max(0)
}

/// `strftime` of the instant `at`'s wall clock, in the board's time locale.
fn clock_text(at: f64, spec: &str) -> Result<String, String> {
    super::table_dates::strftime(super::temporal::clock_moment(at)?, spec)
}

/// `%p` lowered, in the board's time locale.
fn meridiem(at: f64) -> Result<String, String> {
    Ok(clock_text(at, "%p")?.to_lowercase())
}

/// The wall clock `(hour, minute, second)` of an instant in days, to the millisecond.
fn clock_of(at: f64) -> (i64, i64, i64) {
    let ms = ((at * DAY_MS as f64).round() as i64).rem_euclid(DAY_MS);
    (ms / HOUR, ms / MINUTE % 60, ms / 1000 % 60)
}

impl Vocab {
    fn label(self, at: f64, index: usize) -> Result<(String, Option<String>), String> {
        let (hour, minute, _) = clock_of(at);
        let core = if self.clock24 {
            clock_text(at, "%H:%M")?
        } else if minute != 0 && !self.hour_boundary && index == 0 {
            format!("{}{}", clock_text(at, "%-I:%M")?, meridiem(at)?)
        } else if minute != 0 {
            format!(":{minute:02}")
        } else if self.narrow {
            format!("{}{}", clock_text(at, "%-I")?, meridiem(at)?)
        } else {
            match hour {
                0 => display_text::language::tr("axis.midnight", None, &[])?,
                12 => display_text::language::tr("axis.noon", None, &[])?,
                _ => format!("{}{}", clock_text(at, "%-I")?, meridiem(at)?),
            }
        };
        let row2 = self.date_row.then(|| if hour == 0 { date_text(at, "%b %-d") } else { Ok(String::new()) }).transpose()?;
        Ok((core, row2))
    }
}

/// `strftime` of the day `at` falls on, in the board's time locale.
fn date_text(at: f64, spec: &str) -> Result<String, String> {
    let (y, m, d) = civil_from_days(at.floor() as i64);
    let year = u32::try_from(y).map_err(|_| format!("year {y} out of range"))?;
    super::table_dates::strftime(super::table_dates::Moment::midnight(year, u32::from(m), u32::from(d)), spec)
}

/// The tooltip header of timed rows spanning at most two days: the time of day, with the date where the rows
/// cross one.
#[derive(Clone, Copy, Debug)]
pub struct Header {
    clock24: bool,
    with_date: bool,
    seconds: bool,
}

impl Header {
    /// `subday_header_for`.
    pub fn of(source: &SubdaySource, clock: Option<i64>) -> Option<Self> {
        let extent = source.extent(None)?;
        if extent.span_ms() > TOOLTIP_SPAN_MS {
            return None;
        }
        let seconds = extent.instants.iter().any(|ms| ms.rem_euclid(MINUTE) / 1000 != 0);
        Some(Self { clock24: clock == Some(24), with_date: extent.first.div_euclid(DAY_MS) != extent.last.div_euclid(DAY_MS), seconds })
    }

    /// The header of the row at `at`.
    pub fn text(self, at: f64) -> Result<String, String> {
        let seconds = if self.seconds { ":%S" } else { "" };
        let time = if self.clock24 {
            clock_text(at, &format!("%H:%M{seconds}"))?
        } else {
            format!("{}{}", clock_text(at, &format!("%-I:%M{seconds}"))?, meridiem(at)?)
        };
        if !self.with_date {
            return Ok(time);
        }
        Ok(format!("{}, {time}", date_text(at, "%-d %b %Y")?))
    }
}

/// The authored `scale.continuous.domain` endpoints as text: `None` when absent, `Some(None)` when an endpoint is
/// not text (production reads no instants off a number).
pub fn authored_domain(ax_x: &AxisStyle) -> Option<Option<[String; 2]>> {
    let pair = ax_x.scale.get("continuous").and_then(|c| c.get("domain")).and_then(serde_json::Value::as_array)?;
    Some(match &pair[..] {
        [serde_json::Value::String(lo), serde_json::Value::String(hi)] => Some([lo.clone(), hi.clone()]),
        _ => None,
    })
}

/// The sub-day treatment of the x axis `ax_x` over `source`, `available` wide of a chart `outer` wide.
pub fn plan_for(
    source: &SubdaySource,
    ax_x: &AxisStyle,
    cadence: Option<AuthoredTickCadence>,
    authored_format: bool,
    available: f64,
    outer: f64,
) -> Option<SubdayPlan> {
    let domain = authored_domain(ax_x);
    if matches!(domain, Some(None)) {
        return None;
    }
    let domain = domain.flatten();
    SubdayPlan::of(
        source,
        &AxisSetup {
            cadence,
            authored_values: ax_x.labels.values.is_some(),
            label_override: authored_format || ax_x.labels.expr.is_some(),
            domain: domain.as_ref().map(|[lo, hi]| [lo.as_str(), hi.as_str()]),
            clock: ax_x.labels.clock,
            outer_width: outer,
            available_width: available,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time_axis::date_text_day;

    fn setup(outer_width: f64) -> AxisSetup<'static> {
        AxisSetup {
            cadence: None,
            authored_values: false,
            label_override: false,
            domain: None,
            clock: Some(12),
            outer_width,
            available_width: outer_width,
        }
    }

    fn source_of(values: &[&str]) -> SubdaySource {
        let mut axis = TimeAxis::new(crate::time_axis::Grain::default(), std::collections::HashMap::new());
        for text in values {
            let _ = axis.add(text);
        }
        SubdaySource::of(&axis, values.iter().copied())
    }

    /// A timestamp per hour of `day`.
    fn day_hours(day: &str, count: usize) -> Vec<String> {
        (0..count).map(|h| format!("{day}T{h:02}:00:00")).collect()
    }

    fn plan_over(values: &[String], width: f64) -> (SubdayPlan, f64, f64) {
        let refs: Vec<&str> = values.iter().map(String::as_str).collect();
        let source = source_of(&refs);
        let (lo, hi) = (date_text_day(&values[0]).unwrap(), date_text_day(values.last().unwrap()).unwrap());
        (SubdayPlan::of(&source, &setup(width)).expect("a sub-day plan"), lo, hi)
    }

    #[test]
    fn a_day_of_hours_ticks_every_six_and_reads_the_clock_words() {
        let _locale = crate::time_locale::en_us_scope();
        let (plan, lo, hi) = plan_over(&day_hours("2024-03-05", 24), 600.0);
        let step = plan.ladder.expect("the clock ladder fits");
        let ticks = step.instants(lo, hi);
        let texts: Vec<String> = ticks.iter().enumerate().map(|(i, &at)| plan.label(at, i).unwrap().unwrap().0).collect();
        assert_eq!(texts, ["Midnight", "6am", "Noon", "6pm"]);
        assert!(!plan.stacked(), "one midnight draws no date row");
    }

    #[test]
    fn crossing_midnights_stacks_the_date_under_each_midnight() {
        let _locale = crate::time_locale::en_us_scope();
        let values: Vec<String> = day_hours("2024-03-05", 24).into_iter().chain(day_hours("2024-03-06", 24)).collect();
        let (plan, lo, hi) = plan_over(&values, 600.0);
        assert!(plan.stacked());
        let at = |h: f64| plan.label(lo + h / 24.0, 1).unwrap().unwrap();
        assert_eq!(at(0.0), ("Midnight".to_string(), Some("Mar 5".to_string())));
        assert_eq!(at(12.0), ("Noon".to_string(), Some(String::new())));
        assert!(hi > lo);
    }

    #[test]
    fn a_narrow_card_reads_numerals_and_the_24_hour_clock_its_own() {
        let _locale = crate::time_locale::en_us_scope();
        let values = day_hours("2024-03-05", 24);
        let refs: Vec<&str> = values.iter().map(String::as_str).collect();
        let source = source_of(&refs);
        let narrow = SubdayPlan::of(&source, &setup(340.0)).expect("a plan");
        assert_eq!(narrow.label(date_text_day(&values[0]).unwrap(), 0).unwrap().unwrap().0, "12am");
        let mut clock24 = setup(600.0);
        clock24.clock = Some(24);
        let plan = SubdayPlan::of(&source, &clock24).expect("a plan");
        assert_eq!(plan.label(date_text_day(&values[6]).unwrap() + 0.5 / 24.0, 1).unwrap().unwrap().0, "06:30");
    }

    #[test]
    fn a_span_of_days_keeps_vega_s_own_labels_and_an_offset_its_own_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let wide: Vec<String> = (0..400).map(|d| format!("{}T12:00:00", civil_text(date_text_day("2024-01-01").unwrap() + d as f64))).collect();
        let refs: Vec<&str> = wide.iter().map(String::as_str).collect();
        let plan = SubdayPlan::of(&source_of(&refs), &setup(600.0)).expect("a plan");
        assert!(plan.label(date_text_day(&wide[3]).unwrap(), 0).unwrap().is_none(), "a year or more spans no vocabulary");
        let utc: Vec<String> = day_hours("2024-03-05", 24).into_iter().map(|t| format!("{t}Z")).collect();
        let refs: Vec<&str> = utc.iter().map(String::as_str).collect();
        let plan = SubdayPlan::of(&source_of(&refs), &setup(600.0)).expect("a plan");
        assert!(plan.ladder.is_none(), "an absolute instant keeps Vega's ticks");
        assert!(plan.label(date_text_day(&utc[6]).unwrap(), 0).unwrap().is_some());
    }

    fn civil_text(day: f64) -> String {
        let (y, m, d) = civil_from_days(day as i64);
        format!("{y:04}-{m:02}-{d:02}")
    }

    #[test]
    fn the_tooltip_header_names_the_time_within_two_days_and_the_date_across_midnight() {
        let _locale = crate::time_locale::en_us_scope();
        let within: Vec<&str> = vec!["2024-03-05T09:00:00", "2024-03-05T17:30:00"];
        let header = Header::of(&source_of(&within), Some(12)).expect("a header");
        assert_eq!(header.text(date_text_day(within[1]).unwrap()).unwrap(), "5:30pm");
        let across: Vec<&str> = vec!["2024-03-05T23:00:00", "2024-03-06T01:15:30"];
        let header = Header::of(&source_of(&across), Some(24)).expect("a header");
        assert_eq!(header.text(date_text_day(across[1]).unwrap()).unwrap(), "6 Mar 2024, 01:15:30");
        let spread: Vec<&str> = vec!["2024-03-05T09:00:00", "2024-03-09T09:00:00"];
        assert!(Header::of(&source_of(&spread), Some(12)).is_none(), "more than 48 hours reads the date alone");
    }

    #[test]
    fn untimed_dates_have_no_clock_to_read() {
        let days: Vec<&str> = vec!["2024-03-05", "2024-03-06"];
        let source = source_of(&days);
        assert!(SubdayPlan::of(&source, &setup(600.0)).is_none());
    }
}
