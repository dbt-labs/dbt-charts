//! Daterange presets: the names a daterange value may hold instead of two dates and the window
//! each names on a board's clock (`template/daterange.py`).

use jiff::civil::Date as Day;
use jiff::Span;

pub(crate) const GRAMMAR: &str = "today, yesterday, last_<N>_<unit>, previous_<N>_<unit>, \
before_previous_<N>_<unit>, this_<unit>, <unit>_to_date (units: days, weeks, months, quarters, \
years, singular or plural; this_ and _to_date take week, month, quarter or year; fiscal_ may \
precede quarter or year, e.g. this_fiscal_quarter, last_4_fiscal_quarters)";

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Today,
    Yesterday,
    Last,
    Previous,
    BeforePrevious,
    This,
    ToDate,
}

#[derive(Clone, Copy, PartialEq)]
enum Unit {
    Day,
    Week,
    Month,
    Quarter,
    Year,
}

impl Unit {
    fn months(self) -> i64 {
        match self {
            Unit::Quarter => 3,
            Unit::Year => 12,
            _ => 1,
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) struct Preset {
    kind: Kind,
    unit: Unit,
    n: i64,
    fiscal: bool,
}

const UNITS: [(&str, Unit); 5] = [
    ("day", Unit::Day),
    ("week", Unit::Week),
    ("month", Unit::Month),
    ("quarter", Unit::Quarter),
    ("year", Unit::Year),
];

/// `unit`, or with `plural` its `unit` + `s`.
fn unit_of(text: &str, plural: bool) -> Option<Unit> {
    UNITS.iter().find_map(|&(name, unit)| {
        let whole = text == name || (plural && text.strip_prefix(name) == Some("s"));
        whole.then_some(unit)
    })
}

/// The rest of `this_` and of `_to_date`: `[fiscal_]` and a unit from week up.
fn calendar_unit(text: &str) -> Option<(Unit, bool)> {
    let (fiscal, unit) = match text.strip_prefix("fiscal_") {
        Some(unit) => (true, unit),
        None => (false, text),
    };
    let unit = unit_of(unit, false).filter(|u| *u != Unit::Day)?;
    (!fiscal || matches!(unit, Unit::Quarter | Unit::Year)).then_some((unit, fiscal))
}

/// The rest of `last_`, `previous_` and `before_previous_`: `<N>_[fiscal_]<unit>[s]`.
fn rolling(text: &str, kind: Kind) -> Option<Preset> {
    let (n, tail) = text.split_once('_')?;
    let mut digits = n.chars();
    let first_ok = digits.next().is_some_and(|c| ('1'..='9').contains(&c));
    if !first_ok || !digits.all(|c| c.is_ascii_digit()) {
        return None;
    }
    let (fiscal, unit) = match tail.strip_prefix("fiscal_") {
        Some(unit) => (true, unit),
        None => (false, tail),
    };
    let unit = unit_of(unit, true)?;
    let allowed = !fiscal || matches!(unit, Unit::Quarter | Unit::Year);
    allowed.then_some(Preset {
        kind,
        unit,
        n: n.parse().ok()?,
        fiscal,
    })
}

pub(crate) fn parse(name: &str) -> Option<Preset> {
    let calendar = |kind, (unit, fiscal)| Preset {
        kind,
        unit,
        n: 1,
        fiscal,
    };
    match name {
        "today" => return Some(calendar(Kind::Today, (Unit::Day, false))),
        "yesterday" => return Some(calendar(Kind::Yesterday, (Unit::Day, false))),
        _ => {}
    }
    if let Some(rest) = name.strip_prefix("before_previous_") {
        return rolling(rest, Kind::BeforePrevious);
    }
    if let Some(rest) = name.strip_prefix("previous_") {
        return rolling(rest, Kind::Previous);
    }
    if let Some(rest) = name.strip_prefix("last_") {
        return rolling(rest, Kind::Last);
    }
    if let Some(rest) = name.strip_prefix("this_") {
        return calendar_unit(rest).map(|u| calendar(Kind::This, u));
    }
    let rest = name.strip_suffix("_to_date")?;
    calendar_unit(rest).map(|u| calendar(Kind::ToDate, u))
}

/// Where the unit holding `d` starts. `week_start` counts from Sunday, `anchor` is the month a
/// year opens in.
fn unit_start(d: Day, unit: Unit, anchor: i64, week_start: i64) -> Result<Day, jiff::Error> {
    match unit {
        Unit::Day => Ok(d),
        Unit::Week => {
            let monday_zero = i64::from(d.weekday().to_monday_zero_offset());
            d.checked_sub(Span::new().try_days((monday_zero - (week_start - 1)).rem_euclid(7))?)
        }
        _ => {
            let since_anchor = (i64::from(d.month()) - anchor).rem_euclid(12);
            d.first_of_month()
                .checked_sub(Span::new().try_months(since_anchor % unit.months())?)
        }
    }
}

/// A window Python's `date` cannot hold.
fn too_far() -> jiff::Error {
    jiff::Error::from_args(format_args!("the window leaves the years 1 to 9999"))
}

fn shift(start: Day, unit: Unit, count: i64) -> Result<Day, jiff::Error> {
    let span = match unit {
        Unit::Day => Span::new().try_days(count)?,
        Unit::Week => Span::new().try_weeks(count)?,
        _ => Span::new().try_months(count.checked_mul(unit.months()).ok_or_else(too_far)?)?,
    };
    start.checked_add(span)
}

/// Python's `date` runs from year 1 to 9999; a day outside it is one Python refuses.
fn in_range(d: Day) -> Result<Day, jiff::Error> {
    if (1..=9999).contains(&d.year()) {
        Ok(d)
    } else {
        Err(too_far())
    }
}

impl Preset {
    /// The inclusive `(start, end)` on `today`; `before_previous` has no start.
    pub(crate) fn window(
        self,
        today: Day,
        fiscal_start_month: i64,
        week_start: i64,
    ) -> Result<(Option<Day>, Day), jiff::Error> {
        let (start, end) = self.unchecked(today, fiscal_start_month, week_start)?;
        start.map(in_range).transpose()?;
        Ok((start, in_range(end)?))
    }

    fn unchecked(
        self,
        today: Day,
        fiscal_start_month: i64,
        week_start: i64,
    ) -> Result<(Option<Day>, Day), jiff::Error> {
        let (
            day,
            Self {
                kind,
                unit,
                n,
                fiscal,
            },
        ) = (Span::new().days(1), self);
        match kind {
            Kind::Today => return Ok((Some(today), today)),
            Kind::Yesterday => {
                let yesterday = today.checked_sub(day)?;
                return Ok((Some(yesterday), yesterday));
            }
            _ => {}
        }
        let anchor = if fiscal { fiscal_start_month } else { 1 };
        let start = unit_start(today, unit, anchor, week_start)?;
        Ok(match kind {
            Kind::ToDate => (Some(start), today),
            Kind::This => (Some(start), shift(start, unit, 1)?.checked_sub(day)?),
            Kind::Last => (Some(shift(start, unit, 1 - n)?), today),
            Kind::Previous => (Some(shift(start, unit, -n)?), start.checked_sub(day)?),
            _ => (None, shift(start, unit, -n)?.checked_sub(day)?),
        })
    }
}
