//! Ranged bars (`y_start`, #9323/#9347): the start values and tooltip law
//! both of `bar.rs`'s arms share, and the date measure a bar between two date
//! columns puts on its value axis (`measure_type: temporal`, a gantt).
//! `docs/laws-bar.md#ranged-bars-y_start` carries the evidence.

use std::collections::HashMap;

use serde_json::Value;

use crate::common::FormatSlot;
use crate::contract::{self, SpanRow};
use crate::families::temporal::{
    civil_from_days, date_text_day, days_from_civil, parse_iso_date, utc_ticks_exact, AuthoredTimeFormat, TimeUnit,
    MONTH_ABBR,
};
use crate::Payload;

type Row = serde_json::Map<String, Value>;

/// A duration reads in the coarsest unit that keeps it a readable number
/// (`_tooltip.py::_DURATION_UNITS`): days up to three weeks, weeks up to
/// eight, months beyond.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum DurationUnit {
    Day,
    Week,
    Month,
}

impl DurationUnit {
    const ALL: [DurationUnit; 3] = [Self::Day, Self::Week, Self::Month];

    fn name(self) -> &'static str {
        match self {
            Self::Day => "day",
            Self::Week => "week",
            Self::Month => "month",
        }
    }

    fn days(self) -> f64 {
        match self {
            Self::Day => 1.0,
            Self::Week => 7.0,
            Self::Month => 30.4375,
        }
    }

    /// The longest length, in days, still read in this unit.
    fn limit(self) -> f64 {
        match self {
            Self::Day => 21.0,
            Self::Week => 56.0,
            Self::Month => f64::INFINITY,
        }
    }

    fn format(self) -> &'static str {
        match self {
            Self::Day => ".0f",
            Self::Week | Self::Month => ".1~f",
        }
    }

    /// `_unit_text_expr`: the length in this unit, singular by the PRINTED
    /// number (1.04 weeks prints "1 week").
    fn text(self, days: f64) -> Result<String, String> {
        let number = crate::number_locale::d3_format(self.format(), days / self.days())
            .map_err(|e| format!("bar: duration format {:?}: {e}", self.format()))?;
        let name = if number == "1" { self.name().to_string() } else { format!("{}s", self.name()) };
        Ok(format!("{number} {name}"))
    }

    /// One unit for every duration on a chart, picked from the median length
    /// (`span_duration_unit`), so rows compare at a glance.
    fn of_median(mut lengths: Vec<f64>) -> Self {
        lengths.sort_by(f64::total_cmp);
        let median = lengths.get(lengths.len() / 2).copied().unwrap_or(0.0);
        Self::ALL.into_iter().find(|u| median <= u.limit()).expect("the month limit is infinite")
    }

    /// The row's own natural unit, when it differs from the chart's: shown
    /// muted beneath the duration (`span_tooltip_rows`' native-duration row).
    fn native_text(self, days: f64) -> Result<String, String> {
        let natural = Self::ALL.into_iter().find(|u| days <= u.limit()).expect("the month limit is infinite");
        if natural == self {
            Ok(String::new())
        } else {
            natural.text(days)
        }
    }
}

/// A ranged bar's start values and the tooltip law that reads them
/// (`validate_span` has confirmed the shape: single-series, or a vertical
/// color split 1:1 with the category).
pub(crate) struct RangedBars {
    /// The start column's row-title (`field_title`; never the chart's
    /// `y_label`, which titles the end column).
    pub(crate) start_title: String,
    pub(crate) starts: HashMap<(String, String), f64>,
    /// No bar in the chart runs backwards, so its difference reads as an
    /// unsigned `Δ` (`span_rises_only`).
    rises_only: bool,
    /// Set for a date measure: bars carry day numbers (`with_day_numbers`)
    /// and read as durations in this unit.
    duration: Option<DurationUnit>,
}

impl RangedBars {
    pub(crate) fn new(
        start_field: &str,
        field_labels: &crate::common::FieldLabels,
        starts: HashMap<(String, String), f64>,
        ends: &HashMap<(String, String), f64>,
        temporal: bool,
    ) -> Self {
        let rises_only = span_rises_only(ends.iter().map(|(key, end)| (Some(*end), starts.get(key).copied())));
        let duration = temporal.then(|| {
            DurationUnit::of_median(ends.iter().filter_map(|(key, end)| starts.get(key).map(|s| end - s)).collect())
        });
        Self {
            start_title: display_text::field_title(start_field, field_labels),
            starts,
            rises_only,
            duration,
        }
    }

    /// The datum's tooltip label: its start, end and difference rows, or the
    /// plain bar's when a numeric bar starts at zero
    /// (`structured_tooltip.py:754-763`: `datum[y_start] == 0 ? plain : span`;
    /// a date measure has no such wrapper). `plain` builds that label.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn aria(
        &self,
        tooltip_labels: bool,
        cat: &str,
        detected_tu: Option<TimeUnit>,
        is_standard_date: bool,
        grain_format: Option<AuthoredTimeFormat>,
        series: Option<&str>,
        order: Option<i64>,
        (value_title, value_format): (&str, &FormatSlot),
        (start, end): (f64, f64),
        plain: impl FnOnce() -> Result<Option<String>, String>,
    ) -> Result<Option<String>, String> {
        if !tooltip_labels || (self.duration.is_none() && start == 0.0) {
            return plain();
        }
        let header = contract::tooltip_header_text(cat, detected_tu, is_standard_date, grain_format)?;
        let row = |title: &str, value: String, muted: bool| SpanRow { title: title.to_string(), value, muted, swatch: None };
        let rows = match self.duration {
            Some(unit) => {
                let days = end - start;
                vec![
                    row(&self.start_title, tooltip_date(start), false),
                    row(value_title, tooltip_date(end), false),
                    row("Duration", unit.text(days)?, false),
                    row("", unit.native_text(days)?, true),
                ]
            }
            None => {
                let fmt = |v: f64| value_format.paint(v).map_err(|e| format!("bar: tooltip value {e}"));
                vec![
                    row(&self.start_title, fmt(start)?, false),
                    row(value_title, fmt(end)?, false),
                    row("Δ", difference_text(value_format, self.rises_only, start, end)?, false),
                ]
            }
        };
        Ok(Some(contract::span_aria_label(&header, series, order, &rows)))
    }

    /// A date bar's value label: its length in the chart's one unit
    /// (`span_duration_expr`). Only a date measure prints one; a numeric
    /// span's signed-change label is unported (`validate_span` refuses it).
    pub(crate) fn duration_text(&self, start: f64, end: f64) -> Result<String, String> {
        self.duration
            .expect("only a date measure prints a duration label")
            .text(end - start)
    }
}

/// True when no row runs backwards, so the difference is an extent, not a move.
/// A row missing either end counts as rising (`_tooltip.py::span_rises_only`).
fn span_rises_only(pairs: impl Iterator<Item = (Option<f64>, Option<f64>)>) -> bool {
    pairs.into_iter().all(|(end, start)| !matches!((end, start), (Some(e), Some(s)) if e < s))
}

/// A numeric span's `Δ` row: the unsigned extent when no bar runs backwards,
/// else the signed move.
fn difference_text(format: &FormatSlot, rises_only: bool, start: f64, end: f64) -> Result<String, String> {
    let magnitude = (end - start).abs();
    let err = |e| format!("bar: tooltip value {e}");
    if rises_only {
        return format.paint(magnitude).map_err(err);
    }
    let sign = if end >= start { '+' } else { '\u{2212}' };
    format.paint_signed(sign, magnitude).map_err(err)
}

/// Where a bar's two ends live and what the tooltip calls them.
pub(crate) struct Ends {
    pub end: String,
    pub end_title: String,
    pub start: String,
    pub start_title: String,
}

/// One value row of a combined block: a column of the shared datum row.
pub(crate) struct BlockRow {
    pub title: String,
    pub field: String,
    pub swatch: Option<String>,
}

/// The one tooltip a ranged bar and the layers that share its rows all carry
/// (`structured_tooltip.py::_span_values`): each layer's value under its own
/// name, then the bar's ends and their difference; a nested ranged bar reads
/// open, high, low, close and its own change instead. An end a layer already
/// names is not repeated.
pub(crate) struct SpanBlock {
    rows: Vec<BlockRow>,
    change: BlockRow,
    change_ends: (String, String),
    rises_only: bool,
}

impl SpanBlock {
    /// `named` are the layers' rows, `inner` a nested ranged bar's ends.
    /// `bar_swatch` keys the bar's own rows to its paint when layers are named.
    pub(crate) fn new(
        data: &[Row],
        base: &Ends,
        named: Vec<BlockRow>,
        inner: Option<&Ends>,
        bar_swatch: Option<&str>,
    ) -> Self {
        let own = |title: &str, field: &str| BlockRow {
            title: title.to_string(),
            field: field.to_string(),
            swatch: bar_swatch.map(str::to_string),
        };
        let (tail, change): (Vec<BlockRow>, &Ends) = match inner {
            Some(inner) => (
                vec![
                    own(&inner.start_title, &inner.start),
                    own(&base.end_title, &base.end),
                    own(&base.start_title, &base.start),
                    own(&inner.end_title, &inner.end),
                ],
                inner,
            ),
            None => (
                [(&base.start_title, &base.start), (&base.end_title, &base.end)]
                    .into_iter()
                    .filter(|(_, field)| named.iter().all(|n| n.field != **field))
                    .map(|(title, field)| own(title, field))
                    .collect(),
                base,
            ),
        };
        let numeric = |row: &Row, field: &str| row.get(field).and_then(Value::as_f64);
        let rises_only =
            span_rises_only(data.iter().map(|row| (numeric(row, &change.end), numeric(row, &change.start))));
        Self {
            rows: named.into_iter().chain(tail).collect(),
            change: own("Δ", ""),
            change_ends: (change.end.clone(), change.start.clone()),
            rises_only,
        }
    }

    /// One datum's label. `series` is the series row's text, swatch included
    /// when it carries one.
    pub(crate) fn label(
        &self,
        header: &str,
        series: Option<&str>,
        datum: &Row,
        format: &FormatSlot,
    ) -> Result<String, String> {
        let number = |field: &str| {
            datum
                .get(field)
                .and_then(Value::as_f64)
                .ok_or_else(|| format!("bar: tooltip block: row has no numeric {field:?}"))
        };
        let row = |block: &BlockRow, value: String| SpanRow {
            title: block.title.clone(),
            value,
            muted: false,
            swatch: block.swatch.clone(),
        };
        let mut rows = Vec::with_capacity(self.rows.len() + 1);
        for block in &self.rows {
            let value = format.paint(number(&block.field)?).map_err(|e| format!("bar: tooltip value {e}"))?;
            rows.push(row(block, value));
        }
        let (end, start) = (number(&self.change_ends.0)?, number(&self.change_ends.1)?);
        rows.push(row(&self.change, difference_text(format, self.rises_only, start, end)?));
        Ok(contract::span_aria_label(header, series, None, &rows))
    }
}

/// `5 Jan 2026` (`PredefinedTimeFormat.date_short`, `%-d %b %Y`): the house
/// date every span tooltip prints.
fn tooltip_date(day: f64) -> String {
    let (y, m, d) = civil_from_days(day.floor() as i64);
    format!("{d} {} {y}", MONTH_ABBR[(m - 1) as usize])
}

/// The tick label of a date measure (`_DATE_TICK_LABEL_EXPR`): the coarsest
/// boundary the tick sits on — `2026` on a January 1, `Feb` on any other
/// month start, `Feb 9` mid-month.
fn date_tick_label(day: i64) -> String {
    let (y, m, d) = civil_from_days(day);
    if d != 1 {
        format!("{} {d}", MONTH_ABBR[(m - 1) as usize])
    } else if m != 1 {
        MONTH_ABBR[(m - 1) as usize].to_string()
    } else {
        y.to_string()
    }
}

/// A date measure's scale and ticks, in day numbers.
pub(crate) struct DateAxis {
    pub(crate) domain: (f64, f64),
    pub(crate) ticks: Vec<f64>,
    pub(crate) labels: Vec<String>,
}

/// The time scale a bar between two date columns puts on its value axis:
/// the exact extent of both columns (Vega-Lite never nices a time scale,
/// `vega-lite/src/compile/scale/properties.ts:270-278`) and d3's calendar
/// ticks at the axis's authored `tickCount` (`_temporal_measure_encoding`
/// forwards it; Vega's `tickCount` passes a number through, `vega.js:14514`).
pub(crate) fn date_axis(rows: &[Row], fields: [&str; 2], tick_count: usize) -> Result<DateAxis, String> {
    let days = rows.iter().flat_map(|row| fields.iter().map(|f| row.get(*f).and_then(Value::as_f64)));
    let (lo, hi) = days.flatten().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), d| (lo.min(d), hi.max(d)));
    if !(hi > lo) {
        return Err("bar: a date measure whose dates span no time has no scale".into());
    }
    let ticks = utc_ticks_exact(lo, hi, tick_count)?;
    Ok(DateAxis {
        domain: (lo, hi),
        labels: ticks.iter().map(|&t| date_tick_label(t)).collect(),
        ticks: ticks.into_iter().map(|t| t as f64).collect(),
    })
}

/// `payload` with each row's date columns as day numbers (a time of day as the
/// fraction of its day), so the numeric arms draw a date measure unchanged.
pub(crate) fn with_day_numbers(payload: &Payload, fields: [&str; 2]) -> Result<Payload, String> {
    let mut converted = payload.clone();
    for (i, row) in converted.rows.iter_mut().enumerate() {
        for field in fields {
            let text = match row.get(field) {
                Some(Value::String(text)) => text.clone(),
                other => return Err(format!("bar: row {i}: date column {field:?} holds {other:?}, not a date")),
            };
            let day = date_text_day(&text).ok_or_else(|| {
                if text.len() > 10 && text.get(..10).and_then(parse_iso_date).is_some() {
                    format!("bar: row {i}: date column {field:?} value {text:?} has a time of day that is not modeled (only HH:MM[:SS])")
                } else {
                    format!("bar: row {i}: date column {field:?} value {text:?} is not a date")
                }
            })?;
            row.insert(field.to_string(), Value::from(day));
        }
    }
    Ok(converted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_median_length_picks_the_charts_one_unit() {
        // `composed-chart-shapes`' gantt: 18, 32, 60, 32, 11 days -> median 32.
        assert_eq!(DurationUnit::of_median(vec![18.0, 32.0, 60.0, 32.0, 11.0]), DurationUnit::Week);
        assert_eq!(DurationUnit::of_median(vec![3.0, 9.0, 21.0]), DurationUnit::Day);
        assert_eq!(DurationUnit::of_median(vec![90.0, 120.0, 400.0]), DurationUnit::Month);
        // The limits are inclusive: 21 days is still days, 56 still weeks.
        assert_eq!(DurationUnit::of_median(vec![21.0]), DurationUnit::Day);
        assert_eq!(DurationUnit::of_median(vec![56.0]), DurationUnit::Week);
    }

    #[test]
    fn a_duration_is_singular_by_its_printed_number() {
        assert_eq!(DurationUnit::Week.text(18.0).unwrap(), "2.6 weeks");
        // 7.3 days is 1.04 weeks, which prints "1".
        assert_eq!(DurationUnit::Week.text(7.3).unwrap(), "1 week");
        assert_eq!(DurationUnit::Day.text(1.0).unwrap(), "1 day");
        assert_eq!(DurationUnit::Month.text(60.0).unwrap(), "2 months");
    }

    #[test]
    fn a_rows_native_unit_shows_only_when_it_differs_from_the_charts() {
        // Reference tooltips: 18 days reads "18 days" beneath "2.6 weeks", 32
        // days adds nothing, 60 days reads "2 months".
        assert_eq!(DurationUnit::Week.native_text(18.0).unwrap(), "18 days");
        assert_eq!(DurationUnit::Week.native_text(32.0).unwrap(), "");
        assert_eq!(DurationUnit::Week.native_text(60.0).unwrap(), "2 months");
    }

    #[test]
    fn tick_labels_name_the_coarsest_boundary() {
        let day = |y, m, d| days_from_civil(y, m, d);
        assert_eq!(date_tick_label(day(2026, 1, 1)), "2026");
        assert_eq!(date_tick_label(day(2026, 2, 1)), "Feb");
        assert_eq!(date_tick_label(day(2026, 2, 9)), "Feb 9");
    }

    #[test]
    fn a_date_axis_spans_the_exact_extent_and_ticks_at_the_authored_count() {
        let rows: Vec<Row> = [("2026-01-05", "2026-01-23"), ("2026-04-20", "2026-05-01")]
            .iter()
            .map(|(s, e)| {
                let day = |t: &str| {
                    let (y, m, d) = parse_iso_date(t).unwrap();
                    Value::from(days_from_civil(y, m, d) as f64)
                };
                serde_json::json!({"start": day(s), "end": day(e)}).as_object().unwrap().clone()
            })
            .collect();
        let axis = date_axis(&rows, ["end", "start"], 6).unwrap();
        assert_eq!(axis.labels, ["Feb", "Mar", "Apr", "May"]);
        let (y, m, d) = civil_from_days(axis.domain.0 as i64);
        assert_eq!((y, m, d), (2026, 1, 5), "no nice: the first bar starts at the plot's left edge");
    }

    #[test]
    fn a_span_of_no_time_has_no_scale() {
        let rows: Vec<Row> = vec![serde_json::json!({"start": 5.0, "end": 5.0}).as_object().unwrap().clone()];
        assert!(date_axis(&rows, ["end", "start"], 6).is_err());
    }

    fn bars(pairs: &[(f64, f64)], temporal: bool) -> RangedBars {
        let key = |i: usize| (format!("c{i}"), String::new());
        let ends = pairs.iter().enumerate().map(|(i, p)| (key(i), p.1)).collect();
        let starts = pairs.iter().enumerate().map(|(i, p)| (key(i), p.0)).collect();
        RangedBars::new("start", &crate::common::FieldLabels::new(), starts, &ends, temporal)
    }

    fn label(rb: &RangedBars, start: f64, end: f64) -> String {
        let plain = || Ok(Some("plain".to_string()));
        rb.aria(true, "Region A", None, false, None, None, None, ("end", &FormatSlot::from(",.2~f")), (start, end), plain).unwrap().unwrap()
    }

    /// `floating_bar`'s reference tooltip: every bar rises, so an unsigned
    /// `Δ` (`_tooltip.py::span_tooltip_rows`).
    #[test]
    fn a_span_that_only_rises_reads_as_a_range() {
        let rb = bars(&[(71.6, 90.8), (28.1, 55.0)], false);
        assert_eq!(label(&rb, 71.6, 90.8), "\u{2061}Region A; start: 71.6; end: 90.8; Δ: 19.2");
    }

    #[test]
    fn the_start_row_takes_the_start_columns_field_label() {
        let labels: crate::common::FieldLabels =
            [("start".to_string(), "Opened on".to_string())].into();
        let starts = [(("c0".to_string(), String::new()), 71.6)].into();
        let ends = [(("c0".to_string(), String::new()), 90.8)].into();
        let rb = RangedBars::new("start", &labels, starts, &ends, false);
        assert!(label(&rb, 71.6, 90.8).contains("Opened on: 71.6"));
        assert!(label(&bars(&[(71.6, 90.8)], false), 71.6, 90.8).contains("start: 71.6"));
    }

    #[test]
    fn a_row_missing_an_end_does_not_make_a_span_run_backwards() {
        assert!(span_rises_only([(Some(5.0), None), (None, Some(9.0)), (Some(3.0), Some(1.0))].into_iter()));
        assert!(!span_rises_only([(Some(1.0), Some(3.0))].into_iter()));
    }

    /// Bars running both ways read as a signed `Δ` (`\u2212` for a fall,
    /// not a hyphen).
    #[test]
    fn a_span_that_runs_both_ways_reads_as_a_signed_change() {
        let rb = bars(&[(142.0, 120.0), (120.0, 142.0)], false);
        assert!(label(&rb, 142.0, 120.0).ends_with("Δ: \u{2212}22"));
        assert!(label(&rb, 120.0, 142.0).ends_with("Δ: +22"));
    }

    /// A numeric bar starting at zero is a plain bar; a date measure has no
    /// such wrapper (`structured_tooltip.py:754-763`).
    #[test]
    fn a_numeric_span_from_zero_keeps_the_plain_label() {
        let rb = bars(&[(0.0, 5.0)], false);
        assert_eq!(label(&rb, 0.0, 5.0), "plain");
    }

    /// Oracle: `sign_affixed(sign, format_d3(500, ",.0f", "US$", "", ...),
    /// "US$", placement)` is `'+US$500'` / `'US$−500'`: the signed `Δ` row
    /// places its sign against the prefix, and the unsigned `Δ` row wears the
    /// affix whole.
    #[test]
    fn the_difference_rows_wear_the_tooltip_affix() {
        let slot = |placement: Option<crate::common::SignPlacement>| FormatSlot {
            prefix: "US$".into(),
            sign_placement: placement,
            ..FormatSlot::from(",.0f")
        };
        let aria = |rb: &RangedBars, fmt: &FormatSlot, start, end| {
            let plain = || Ok(Some("plain".to_string()));
            rb.aria(true, "Region A", None, false, None, None, None, ("end", fmt), (start, end), plain).unwrap().unwrap()
        };
        let both_ways = bars(&[(100.0, 600.0), (700.0, 200.0)], false);
        assert!(aria(&both_ways, &slot(None), 100.0, 600.0).contains("Δ: +US$500"));
        let after = slot(Some(crate::common::SignPlacement::AfterPrefix));
        assert!(aria(&both_ways, &after, 700.0, 200.0).contains("Δ: US$\u{2212}500"));
        let rising = bars(&[(100.0, 600.0)], false);
        assert!(aria(&rising, &slot(None), 100.0, 600.0).contains("Δ: US$500"));
    }
}
