//! The one law that decides a cartesian x axis's scale type: a port of
//! production's `resolve_cartesian_x_type` (`type_inference.py`), pinned by
//! `fixtures/x_type_battery.json` (`chart_svg_dev/gen_x_type_battery.py`).

use std::collections::{BTreeSet, HashSet};

use serde_json::{Map, Value};

use crate::common::{py_scalar_key, PyKey};
use crate::constants::axis::MAX_ORDINAL_BUCKETS;

use super::temporal::{
    days_from_civil, detect_time_unit, floor_to_bucket_start, infer_vega_type, parse_bucket_string,
    TimePart, TimeUnit, VegaType,
};

type Row = Map<String, Value>;

/// What the law reads off the resolved x axis.
pub struct XAxis<'a> {
    /// Authored `axis_x.time_unit`: a calendar grain, `none`, or a time part.
    pub time_unit: Option<&'a str>,
    /// `resolve_authored_x_type`: `axis_x.type`, or `temporal` from `scale.type`.
    pub authored_type: Option<&'a str>,
    /// Authored `axis_x.type`, unresolved (`gap_fill_ordinal_time` reads it raw).
    pub raw_type: Option<&'a str>,
    /// `axis_x.fill`: how missing buckets are filled.
    pub fill: Option<&'a str>,
    pub fiscal_year_start_month: u8,
}

impl<'a> XAxis<'a> {
    pub fn of(axis: &'a crate::common::AxisStyle) -> Self {
        XAxis {
            time_unit: axis.time_unit.as_ref().and_then(Value::as_str),
            authored_type: axis.resolved_authored_type(),
            raw_type: axis.r#type.as_deref(),
            fill: axis.fill.as_deref(),
            fiscal_year_start_month: axis.fiscal_year_start_month,
        }
    }

    /// `gap_fill_ordinal_time` returns rows (so the chart's base spec carries its own data)
    /// when it fires, whether it builds the scaffold or only sorts and canonicalizes.
    pub fn rewrites_rows(&self, resolution: &XResolution) -> bool {
        if self.authored_type == Some("temporal") || self.time_unit == Some("none") {
            return false;
        }
        if !matches!(self.raw_type, None | Some("auto") | Some("ordinal")) {
            return false;
        }
        matches!(resolution.fill_grain, Grain::Calendar(_))
    }

    /// `gap_fill_ordinal_time`: whether the ordinal scaffold is built (bucket
    /// placement and missing-bucket synthesis) for this resolution.
    pub fn fills_gaps(&self, resolution: &XResolution) -> bool {
        // A continuous temporal scale under `fill: null` synthesizes no buckets.
        let continuous = self.fill == Some("null")
            && matches!(self.raw_type, None | Some("auto"))
            && self.time_unit.is_none()
            && resolution.vl_type == VegaType::Temporal;
        self.rewrites_rows(resolution) && !continuous
    }
}

/// The grain the axis ends up with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Grain {
    None,
    Calendar(TimeUnit),
    /// A cyclic time part (`monthofyear`, ...): always temporal.
    Part(TimePart),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct XResolution {
    pub vl_type: VegaType,
    pub from_data: VegaType,
    pub grain: Grain,
    /// The grain the gap-fill scaffold buckets by: authored, else detected
    /// from a temporal column, and kept even where the axis then drops it for
    /// being over the scaffold budget.
    pub fill_grain: Grain,
}

/// The x axis as `bar.py` resolves it: the law routes the gap fill on the
/// rows as they are, the fill runs per panel over each panel's own range, and
/// the law resolves again on the filled rows.
pub struct FilledX {
    /// The verdict over the filled rows (the rows themselves when no fill ran).
    pub resolution: XResolution,
    /// The grain of the ordinal scaffold, when one was built.
    pub filled: Option<TimeUnit>,
    /// One row per bucket and panel carrying the x (and panel) cells the fill
    /// writes; empty when no fill ran.
    pub scaffold: Vec<Row>,
}

/// `complete_ordinal_time_series`'s buckets, one row per bucket and panel.
pub fn scaffold_rows(rows: &[Row], x_field: &str, panel_fields: &[&str], tu: TimeUnit, fiscal_start: u8) -> Vec<Row> {
    let mut panels: Vec<(Vec<PyKey>, Vec<Value>, Vec<String>)> = Vec::new();
    for row in rows {
        let Some(text) = row.get(x_field).and_then(Value::as_str) else { continue };
        let key: Vec<PyKey> = panel_fields.iter().map(|f| py_cell_key(row.get(*f))).collect();
        let at = match panels.iter().position(|(k, _, _)| *k == key) {
            Some(at) => at,
            None => {
                let cells = panel_fields.iter().map(|f| row.get(*f).cloned().unwrap_or(Value::Null)).collect();
                panels.push((key, cells, Vec::new()));
                panels.len() - 1
            }
        };
        if !panels[at].2.iter().any(|d| d == text) {
            panels[at].2.push(text.to_string());
        }
    }
    let mut scaffold: Vec<Row> = Vec::new();
    for (_, cells, domain) in &panels {
        for bucket in super::temporal::complete_buckets(domain, tu, fiscal_start) {
            let mut row = Row::from_iter([(x_field.to_string(), Value::String(bucket))]);
            for (f, cell) in panel_fields.iter().zip(cells) {
                row.insert((*f).to_string(), cell.clone());
            }
            scaffold.push(row);
        }
    }
    scaffold
}

pub fn resolve_filled_x(
    rows: &[Row],
    x_field: &str,
    axis: &XAxis,
    mark: &str,
    band_step: bool,
    panel_fields: &[&str],
) -> Result<FilledX, String> {
    let routed = resolve_cartesian_x_type(rows, x_field, axis, mark, band_step, panel_fields)?;
    let Grain::Calendar(tu) = routed.fill_grain else { return Ok(FilledX { resolution: routed, filled: None, scaffold: Vec::new() }) };
    if !axis.fills_gaps(&routed) {
        return Ok(FilledX { resolution: routed, filled: None, scaffold: Vec::new() });
    }
    let scaffold = scaffold_rows(rows, x_field, panel_fields, tu, axis.fiscal_year_start_month);
    let resolution = resolve_cartesian_x_type(&scaffold, x_field, axis, mark, band_step, panel_fields)?;
    Ok(FilledX { resolution, filled: Some(tu), scaffold })
}

/// `_parse_date`: a real ISO date or datetime (`fromisoformat` rejects an
/// impossible calendar day or clock), or a labeled bucket's first day. A bare
/// year is neither. A datetime keeps its wall clock whatever its offset, and
/// the flag says that clock's hour, minute or second is nonzero.
pub(crate) fn parse_date(s: &str) -> Option<((i32, u8, u8), bool)> {
    let b = s.as_bytes();
    let digits = |r: std::ops::Range<usize>| b.get(r).is_some_and(|d| d.iter().all(u8::is_ascii_digit));
    let iso = digits(0..4) && b.get(4) == Some(&b'-') && digits(5..7) && b.get(7) == Some(&b'-') && digits(8..10);
    if iso && (b.len() == 10 || matches!(b[10], b'T' | b' ')) {
        let (year, month, day): (i32, u8, u8) = (s[0..4].parse().ok()?, s[5..7].parse().ok()?, s[8..10].parse().ok()?);
        if year == 0 || !(1..=12).contains(&month) || day == 0 || u32::from(day) > super::temporal::days_in_month(year, u32::from(month)) {
            return None;
        }
        let sub_daily = if b.len() == 10 { false } else { iso_clock(&s[11..])? };
        return Some(((year, month, day), sub_daily));
    }
    parse_bucket_string(s).map(|(y, m, d)| ((y, m as u8, d as u8), false))
}

/// The time of day `datetime.fromisoformat` (Python 3.11+) accepts after the
/// date: `HH`, `HH:MM`, `HH:MM:SS[.f+]` or the basic form, then an optional
/// `Z` or `+HH[:MM[:SS[.f+]]]` offset. `Some(nonzero)` says whether the wall
/// clock's hour, minute or second is nonzero.
fn iso_clock(text: &str) -> Option<bool> {
    let end = text.find(['Z', '+', '-']).unwrap_or(text.len());
    let (clock, offset) = text.split_at(end);
    let [hour, minute, second] = clock_fields(clock, 23)?;
    match offset.as_bytes().first() {
        None => {}
        Some(b'Z') if offset.len() == 1 => {}
        Some(b'+' | b'-') => {
            clock_fields(&offset[1..], 23)?;
        }
        _ => return None,
    }
    Some(hour != 0 || minute != 0 || second != 0)
}

/// `parse_hh_mm_ss_ff`: two-digit fields with one consistent `:` style, an
/// optional fraction of any length on the seconds.
pub(crate) fn clock_fields(text: &str, max_hour: u8) -> Option<[u8; 3]> {
    let b = text.as_bytes();
    let pair = |at: usize| -> Option<u8> {
        let d = b.get(at..at + 2)?;
        d.iter().all(u8::is_ascii_digit).then(|| (d[0] - b'0') * 10 + (d[1] - b'0'))
    };
    let hour = pair(0)?;
    let mut at = 2;
    let mut fields = [hour, 0, 0];
    let colon = b.get(at) == Some(&b':');
    for slot in 1..3 {
        if at >= b.len() {
            break;
        }
        if colon {
            if b[at] != b':' {
                return None;
            }
            at += 1;
        }
        fields[slot] = pair(at)?;
        at += 2;
    }
    if at < b.len() {
        // A fraction hangs off the seconds only.
        let fraction = b.get(at).is_some_and(|c| matches!(c, b'.' | b','));
        let digits = &b[at + 1..];
        if !fraction || fields_len(b, colon) < 3 || digits.is_empty() || !digits.iter().all(u8::is_ascii_digit) {
            return None;
        }
    }
    (fields[0] <= max_hour && fields[1] <= 59 && fields[2] <= 59).then_some(fields)
}

/// How many `HH` fields the clock text carries before any fraction.
fn fields_len(b: &[u8], colon: bool) -> usize {
    let head = b.iter().position(|c| matches!(c, b'.' | b',')).unwrap_or(b.len());
    if colon { (head + 1) / 3 } else { head / 2 }
}

/// `detect_time_unit`: raises at 10% or more unparseable distinct values.
fn detect(cells: &[&Value]) -> Result<Option<TimeUnit>, String> {
    let mut seen = HashSet::new();
    let distinct: Vec<&str> = cells.iter().filter_map(|c| c.as_str()).filter(|cell| seen.insert(*cell)).collect();
    let non_string = cells.iter().filter(|c| !c.is_null() && !c.is_string()).map(|c| py_scalar_key(c)).collect::<HashSet<_>>().len();
    let total = distinct.len() + non_string;
    if total < 2 {
        return Ok(None);
    }
    let bad = distinct.iter().filter(|s| parse_date(s).is_none()).count() + non_string;
    if bad > 0 && bad * 10 >= total {
        return Err("Couldn't auto-detect timeUnit: 10% or more unparseable date values".into());
    }
    let parsed: Vec<((i32, u8, u8), bool)> = distinct.iter().filter_map(|s| parse_date(s)).collect();
    if parsed.iter().any(|(_, sub_daily)| *sub_daily) {
        return Ok(None);
    }
    let iso: Vec<String> = parsed.iter().map(|((y, m, d), _)| format!("{y:04}-{m:02}-{d:02}")).collect();
    let refs: Vec<&str> = iso.iter().map(String::as_str).collect();
    Ok(detect_time_unit(&refs))
}

/// `_bucket_index`: a bucket start's position on its grain's ladder.
fn bucket_index(start: (i32, u8, u8), tu: TimeUnit) -> i64 {
    let (y, m, d) = start;
    let months = |step: i64| (i64::from(y) * 12 + i64::from(m) - 1).div_euclid(step);
    match tu {
        TimeUnit::Year => months(12),
        TimeUnit::YearQuarter => months(3),
        TimeUnit::YearMonth => months(1),
        // `date.toordinal()` is the proleptic day count from 0001-01-01 = 1.
        TimeUnit::YearWeek => (days_from_civil(y, m, d) + 719_163).div_euclid(7),
        TimeUnit::YearMonthDate => days_from_civil(y, m, d) + 719_163,
    }
}

/// `ordinal_scaffold_within_budget`: banding the panels at `tu` stays legible
/// while the synthesized empty buckets do not outnumber
/// `max(occupied, MAX_ORDINAL_BUCKETS)`.
fn scaffold_within_budget(panels: &[Vec<&Value>], tu: TimeUnit) -> bool {
    let mut spans: Vec<(i64, i64)> = Vec::new();
    let mut occupied: BTreeSet<(i32, u8, u8)> = BTreeSet::new();
    for cells in panels {
        let distinct: BTreeSet<(i32, u8, u8)> = cells
            .iter()
            .filter_map(|c| c.as_str())
            .filter_map(|c| parse_date(c).map(|(date, _)| date))
            .map(|date| floor_to_bucket_start(date, tu, 1))
            .collect();
        let (Some(&first), Some(&last)) = (distinct.first(), distinct.last()) else { continue };
        spans.push((bucket_index(first, tu), bucket_index(last, tu)));
        occupied.extend(distinct);
    }
    // The band scale is shared: a bucket two panels cover is one band.
    spans.sort_unstable();
    let mut total_bands = 0;
    let mut merged_hi: Option<i64> = None;
    for (lo, hi) in spans {
        let start = merged_hi.map_or(lo, |m| if lo > m { lo } else { m + 1 });
        if hi >= start {
            total_bands += hi - start + 1;
        }
        merged_hi = Some(merged_hi.map_or(hi, |m| m.max(hi)));
    }
    let occupied = occupied.len() as i64;
    total_bands - occupied <= occupied.max(MAX_ORDINAL_BUCKETS as i64)
}

pub(crate) fn py_cell_key(value: Option<&Value>) -> PyKey {
    value.map_or(PyKey::None, py_scalar_key)
}

/// `resolve_cartesian_x_type`: the emitted VL type, the type the data alone
/// implies, and the calendar grain. `rows` carry the x column as the emitter
/// sees it (labeled buckets already rewritten to ISO dates).
pub fn resolve_cartesian_x_type(
    rows: &[Row],
    x_field: &str,
    axis: &XAxis,
    mark: &str,
    band_step: bool,
    panel_fields: &[&str],
) -> Result<XResolution, String> {
    let from_data = infer_vega_type(rows, x_field);
    let cells: Vec<&Value> = rows.iter().filter_map(|row| row.get(x_field)).collect();
    let grain = match axis.time_unit {
        None => match from_data {
            VegaType::Temporal => detect(&cells)?.map_or(Grain::None, Grain::Calendar),
            VegaType::Ordinal => detect(&cells).ok().flatten().map_or(Grain::None, Grain::Calendar),
            _ => Grain::None,
        },
        Some(name) => match super::temporal::parse_authored_time_unit(name) {
            Some(unit) => Grain::Calendar(unit),
            None => TimePart::parse(name).map_or(Grain::None, Grain::Part),
        },
    };
    if mark == "heatmap" {
        return Ok(XResolution { vl_type: VegaType::Nominal, from_data, grain, fill_grain: Grain::None });
    }
    let authored_none = axis.time_unit == Some("none");
    let scaffold_ok = match grain {
        Grain::Calendar(tu) if axis.time_unit.is_none() => {
            let mut groups: Vec<(Vec<PyKey>, Vec<&Value>)> = Vec::new();
            for row in rows.iter().filter(|row| row.contains_key(x_field)) {
                let key: Vec<PyKey> = panel_fields.iter().map(|f| py_cell_key(row.get(*f))).collect();
                match groups.iter_mut().find(|(k, _)| *k == key) {
                    Some((_, cells)) => cells.push(&row[x_field]),
                    None => groups.push((key, vec![&row[x_field]])),
                }
            }
            let panels: Vec<Vec<&Value>> = groups.into_iter().map(|(_, cells)| cells).collect();
            scaffold_within_budget(&panels, tu)
        }
        _ => true,
    };
    let mut vl_type = if axis.authored_type == Some("temporal") || authored_none || matches!(grain, Grain::Part(_)) {
        VegaType::Temporal
    } else if axis.authored_type == Some("ordinal") {
        VegaType::Ordinal
    } else if let Grain::Calendar(tu) = grain {
        if band_step {
            VegaType::Ordinal
        } else if matches!(mark, "line" | "area" | "scatter") {
            VegaType::Temporal
        } else if matches!(tu, TimeUnit::YearWeek | TimeUnit::YearMonthDate) {
            if scaffold_ok { VegaType::Ordinal } else { VegaType::Temporal }
        } else {
            let distinct: HashSet<PyKey> = rows
                .iter()
                .filter(|row| row.contains_key(x_field))
                .map(|row| py_cell_key(row.get(x_field)))
                .collect();
            if scaffold_ok && distinct.len() <= MAX_ORDINAL_BUCKETS { VegaType::Ordinal } else { VegaType::Temporal }
        }
    } else {
        from_data
    };
    let fill_grain = if axis.time_unit.is_none() && from_data != VegaType::Temporal { Grain::None } else { grain };
    // A continuous scale carrying an over-budget grain drops the grain.
    let grain = if vl_type == VegaType::Temporal && !scaffold_ok { Grain::None } else { grain };
    if vl_type == VegaType::Temporal
        && matches!(grain, Grain::Calendar(TimeUnit::Year | TimeUnit::YearQuarter))
        && axis.fiscal_year_start_month != 1
    {
        vl_type = VegaType::Ordinal;
    }
    Ok(XResolution { vl_type, from_data, grain, fill_grain })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scaffold_budget_counts_synthesized_slots_against_the_larger_of_distinct_and_the_cap() {
        let within = |dates: &[String], tu| {
            let cells: Vec<Value> = dates.iter().map(|d| Value::from(d.as_str())).collect();
            scaffold_within_budget(&[cells.iter().collect()], tu)
        };
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        // Four weeks present over a 20-week span: 16 empty are within max(4, 60).
        assert!(within(&s(&["2025-01-06", "2025-02-03", "2025-04-07", "2025-05-19"]), TimeUnit::YearWeek));
        // Two dailies a year apart: 363 empty are over max(2, 60).
        assert!(!within(&s(&["2025-01-01", "2026-01-01"]), TimeUnit::YearMonthDate));
        // The boundary is inclusive: 61 days apart synthesize exactly 60.
        assert!(within(&s(&["2025-01-01", "2025-03-03"]), TimeUnit::YearMonthDate));
        assert!(!within(&s(&["2025-01-01", "2025-03-04"]), TimeUnit::YearMonthDate));
        // Past the cap the distinct count is the budget: 200 weekly points over
        // a 299-week span synthesize 99, within 200.
        let sparse: Vec<String> = (0..200)
            .map(|i| {
                let (y, m, d) = super::super::temporal::civil_from_days(days_from_civil(2020, 1, 6) + 7 * (i + i / 2));
                format!("{y:04}-{m:02}-{d:02}")
            })
            .collect();
        assert!(within(&sparse, TimeUnit::YearWeek));
    }

    #[test]
    fn irregular_iso_dates_are_continuous_time_with_no_bands() {
        let rows: Vec<Row> = ["2024-01-03", "2024-03-19", "2024-07-30"]
            .iter()
            .map(|d| Row::from_iter([("x".to_string(), Value::from(*d))]))
            .collect();
        let axis = XAxis { time_unit: None, authored_type: None, raw_type: None, fill: Some("null"), fiscal_year_start_month: 1 };
        let bar = resolve_cartesian_x_type(&rows, "x", &axis, "bar", false, &[]).unwrap();
        assert_eq!((bar.vl_type, bar.grain), (VegaType::Temporal, Grain::None));
    }

    /// Vega reads a year below 100 as NaN on a temporal scale; each family positions the NaN row
    /// itself, the law resolves the same type and grain as for any other year.
    #[test]
    fn a_year_below_100_resolves_like_any_other_year() {
        let rows = |dates: &[&str]| -> Vec<Row> {
            dates.iter().map(|d| Row::from_iter([("x".to_string(), Value::from(*d))])).collect()
        };
        let axis = XAxis { time_unit: None, authored_type: Some("temporal"), raw_type: None, fill: Some("null"), fiscal_year_start_month: 1 };
        for mark in ["line", "area", "scatter", "bar"] {
            let low = resolve_cartesian_x_type(&rows(&["0099-01-01", "0100-01-01"]), "x", &axis, mark, false, &[]).unwrap();
            let high = resolve_cartesian_x_type(&rows(&["2099-01-01", "2100-01-01"]), "x", &axis, mark, false, &[]).unwrap();
            assert_eq!((low.vl_type, low.grain), (high.vl_type, high.grain), "{mark}");
        }
    }

    #[test]
    fn matches_production_across_the_battery() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../../../fixtures/x_type_battery.json")).expect("battery");
        assert!(cases.len() > 500);
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let panels = case["panels"].as_array();
            let rows: Vec<Row> = case["cells"]
                .as_array()
                .unwrap()
                .iter()
                .enumerate()
                .map(|(i, cell)| {
                    let mut row = Row::new();
                    row.insert("x".into(), cell.clone());
                    if let Some(panels) = panels {
                        row.insert("p".into(), panels[i].clone());
                    }
                    row
                })
                .collect();
            let axis = XAxis {
                time_unit: case["time_unit"].as_str(),
                authored_type: case["authored_type"].as_str(),
                raw_type: case["authored_type"].as_str(),
                fill: Some("null"),
                fiscal_year_start_month: case["fiscal"].as_u64().unwrap() as u8,
            };
            let panel_fields: &[&str] = if panels.is_some() { &["p"] } else { &[] };
            let got = resolve_cartesian_x_type(
                &rows,
                "x",
                &axis,
                case["mark"].as_str().unwrap(),
                case["band_step"].as_bool().unwrap(),
                panel_fields,
            );
            let label = format!("{name} {case}");
            let Some(want) = case["expect"].as_object() else {
                assert!(got.is_err(), "{label}: production raises");
                continue;
            };
            let got = got.unwrap_or_else(|e| panic!("{label}: {e}"));
            assert_eq!(got.vl_type.name(), want["vl_type"], "{label}");
            assert_eq!(got.from_data.name(), want["from_data"], "{label}");
            let grain = match got.grain {
                Grain::None if axis.time_unit == Some("none") => Value::from("none"),
                Grain::None => Value::Null,
                Grain::Calendar(tu) => Value::from(format!("{tu:?}").to_lowercase()),
                Grain::Part(_) => want["time_unit"].clone(),
            };
            assert_eq!(grain, want["time_unit"], "{label}");
        }
    }
}
