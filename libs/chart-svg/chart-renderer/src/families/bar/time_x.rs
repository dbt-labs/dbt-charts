//! The UTC time scale of a vertical bar: a date column whose ordinal scaffold
//! is past the budget (or that no calendar grain explains) resolves a
//! continuous temporal axis, where each bar is positioned by its instant
//! instead of its band.

use std::borrow::Cow;
use std::collections::{BTreeSet, HashMap};

use serde::Deserialize;
use serde_json::{Map, Value};

use crate::common::{row_num, row_str, AxisStyle};
use crate::families::scatter_axis::{DateColumn, TemporalXAxis};
use crate::families::subday::SubdaySource;
use crate::families::support_table_strip::label_period_keep;
use crate::families::temporal::{
    civil_from_days, complete_buckets, complete_ordinal_rows, date_text_day, is_opener, days_from_civil, floor_to_bucket_start, next_bucket, normalize_labeled_temporal,
    parse_iso_date, time_unit_floor, TimeUnit,
};
use crate::families::x_type::{Grain, XResolution};
use crate::text::FontBook;

use super::{continuous_pad_px, x_resolution, BandColumns, BarResolved, ColumnSplit, ContinuousWidth, MarkStyle};

type Row = Map<String, Value>;

/// The date `row` carries in `field`, as production's tooltip names it: the one before any bucket floored it.
pub(super) fn tooltip_date(row: &Row, field: &str) -> String {
    row_str(row, if row.contains_key(RAW_X) { RAW_X } else { field })
}

/// The key under which a row floored to its bucket keeps the date it carried, which production's tooltip reads.
pub(super) const RAW_X: &str = "\u{1d}raw_x";

enum Sizing {
    /// A bar that kept its `timeUnit` spans its bucket: Vega-Lite draws it across `band_width` of the
    /// bucket's pixel span, centered, the stroke taking one pixel; an authored `size` centers on the bucket
    /// instead.
    Bucket { size: Option<f64>, grain: TimeUnit, band_width: f64 },
    /// A bar centered on its instant, sized by the law over `closest`, the closest pair of distinct dates
    /// (none for a lone instant, which takes the law's ceiling).
    Instant { width: ContinuousWidth, closest: Option<(String, String)> },
}

/// The dates a layer puts on the shared time scale. Every layer's dates extend the scale; only those of a
/// layer on the base's own x column also join the axis's labels (`overlay_x_domain_values`).
pub(super) struct LayerX {
    pub(super) dates: Vec<String>,
    pub(super) on_base_x: bool,
    /// A bar layer spans its bucket, so the scale reaches its last bucket's end.
    pub(super) spans_buckets: bool,
}

pub(super) struct TimeX {
    /// The rows production plots with a measure: the gap-filled scaffold where a fill ran, each x keyed to its
    /// bucket's start.
    pub(super) rows: Vec<Row>,
    /// The distinct x of the plotted rows in time order: a bucket's start, or the instant itself where no
    /// calendar grain survives.
    pub(super) cats: Vec<String>,
    /// The exclusive end of each bucket of `cats`.
    ends: Vec<String>,
    sizing: Sizing,
    ladder: Vec<String>,
    /// The dates the scale spans: a bucketed axis reaches the end of its last bucket.
    kept: Vec<String>,
    law: XResolution,
    scale_ax: AxisStyle,
    pub(super) grain: Option<TimeUnit>,
    /// How many distinct x values the chart's rows carry, before any bucket folds them.
    distinct: usize,
    /// The grain production's tooltip detects over the rows' own dates, which an authored `time_unit` on the
    /// axis does not change.
    pub(super) tooltip_grain: Option<TimeUnit>,
    /// The rows' and layers' own dates, read for a time of day.
    pub(super) timed: SubdaySource,
}

/// The calendar range a query filter proves for x (`execute/date_range_axis.py::proven_range`), ISO dates.
#[derive(Deserialize)]
pub(crate) struct DateRange {
    start: String,
    end: String,
}

/// `temporal_range_domain`: the domain a proven date range pins a time axis to. A bucketed axis runs from where
/// Vega-Lite draws the bucket holding the range's start to the end of the one holding its end (a week mark sits
/// on the Sunday on or before its bucket); an unbucketed one over the range's whole days. `None` when a data
/// date would fall outside it, which leaves the axis as the data made it rather than clipping a mark.
fn range_domain(range: &DateRange, grain: Option<TimeUnit>, dates: &[&str], fiscal: u8) -> Result<Option<[String; 2]>, String> {
    let day_of = |text: &str| -> Result<i64, String> {
        let (y, m, d) = parse_iso_date(text).ok_or_else(|| format!("bar: date range bound {text:?} is not an ISO date"))?;
        Ok(days_from_civil(y, m, d))
    };
    let iso = |day: i64| {
        let (y, m, d) = civil_from_days(day);
        format!("{y:04}-{m:02}-{d:02}")
    };
    let (start, end) = (day_of(&range.start)?, day_of(&range.end)?);
    let instants = dates.iter().map(|d| date_text_day(d).ok_or_else(|| format!("bar: date {d:?} is not a date or timestamp"))).collect::<Result<Vec<_>, _>>()?;
    let Some(anchor) = instants.iter().copied().reduce(f64::min).map(f64::floor) else { return Ok(None) };
    let (lower, upper, marks) = match grain {
        Some(tu) => {
            let enclosing = |day: i64| match tu {
                TimeUnit::YearWeek => anchor as i64 + 7 * (day - anchor as i64).div_euclid(7),
                _ => {
                    let (y, m, d) = civil_from_days(day);
                    let (fy, fm, fd) = floor_to_bucket_start((y, m, d), tu, fiscal);
                    days_from_civil(fy, fm, fd)
                }
            };
            let position = |day: i64| if tu == TimeUnit::YearWeek { day - (day + 4).rem_euclid(7) } else { day };
            let (lower, upper) = (position(enclosing(start)), position(enclosing(end)));
            let (ny, nm, nd) = civil_from_days(upper);
            let (ey, em, ed) = next_bucket((ny, nm, nd), tu);
            let upper = days_from_civil(ey, em, ed);
            (lower, upper, instants.iter().map(|&at| position(enclosing(at.floor() as i64)) as f64).collect::<Vec<_>>())
        }
        None => (start, end + 1, instants),
    };
    Ok(marks.iter().all(|&mark| (lower as f64..=upper as f64).contains(&mark)).then(|| [iso(lower), iso(upper)]))
}

/// The closest pair of distinct dates, the pair a continuous bar's width is sized against.
fn closest_pair(dates: &[String]) -> Option<(String, String)> {
    let mut days: Vec<(f64, &str)> = dates.iter().map(|d| (date_text_day(d).expect("a date parsed above"), d.as_str())).collect();
    days.sort_by(|a, b| a.0.total_cmp(&b.0));
    days.dedup_by(|a, b| a.0 == b.0);
    (1..days.len())
        .min_by(|&i, &j| (days[i].0 - days[i - 1].0).total_cmp(&(days[j].0 - days[j - 1].0)))
        .map(|at| (days[at - 1].1.to_string(), days[at].1.to_string()))
}

/// `rows` with each date of `field` keyed the way the time axis keys the bars': at the start of the `grain`
/// bucket holding it, or, with no grain, at its UTC instant text.
pub(super) fn at_bucket_starts<'a>(rows: Cow<'a, [Row]>, field: &str, grain: Option<TimeUnit>) -> Cow<'a, [Row]> {
    let start_of = |row: &Row| {
        let cell = row.get(field)?;
        let instant = instant_text(cell)?;
        let key = match grain {
            Some(tu) => time_unit_floor(&instant, tu)?,
            None => instant,
        };
        (cell.as_str() != Some(&key)).then_some(key)
    };
    if !rows.iter().any(|row| start_of(row).is_some()) {
        return rows;
    }
    Cow::Owned(
        rows.iter()
            .map(|row| {
                let mut row = row.clone();
                if let Some(start) = start_of(&row) {
                    let raw = row.insert(field.to_string(), Value::String(start)).expect("start_of read the field");
                    row.insert(RAW_X.to_string(), raw);
                }
                row
            })
            .collect(),
    )
}

/// The shapes of a time-axis bar this arm does not draw: each names what it declines.
fn refuse_unported_shape(r: &BarResolved) -> Result<(), String> {
    if r.style.axis_x.labels.expr.is_some() {
        return Err("bar: authored `axis_x.labels.expr` on a time x axis is not supported".into());
    }
    Ok(())
}

/// A date or timestamp cell as the text the scale keys it by: a plain date as it is, a timestamp as its UTC
/// instant (`YYYY-MM-DDTHH:MM:SS[.mmm]`), which folds an offset into the clock the way Vega-Lite reads it.
fn instant_text(cell: &Value) -> Option<String> {
    let text = cell.as_str()?;
    let day = date_text_day(text)?;
    if text.len() == 10 {
        return Some(text.to_string());
    }
    let (midnight, millis) = (day.floor(), ((day - day.floor()) * 86_400_000.0).round() as i64);
    let (y, m, d) = civil_from_days(midnight as i64 + millis.div_euclid(86_400_000));
    let millis = millis.rem_euclid(86_400_000);
    let (hours, minutes, seconds, ms) = (millis / 3_600_000, millis / 60_000 % 60, millis / 1000 % 60, millis % 1000);
    let fraction = if ms == 0 { String::new() } else { format!(".{ms:03}") };
    Some(format!("{y:04}-{m:02}-{d:02}T{hours:02}:{minutes:02}:{seconds:02}{fraction}"))
}

/// Vega's `timeunit` transform yields NaN for a year below 100, so no bar of the chart has a position and
/// production raises `ERR-CHART-PAINTED-NO-MARKS`.
pub(super) fn has_year_below_100(rows: &[Row], x: &str) -> bool {
    rows.iter().any(|row| row.get(x).and_then(Value::as_str).and_then(parse_iso_date).is_some_and(|(year, _, _)| year < 100))
}

/// Days a bucket spans in the reference month Vega-Lite sizes a grouped bar's offset scale against;
/// probe-pinned per grain by the `bar-continuous-time-x` grouped fixtures (month, quarter, year, week, day).
fn canonical_days(tu: TimeUnit) -> f64 {
    match tu {
        TimeUnit::Year => 365.0,
        TimeUnit::YearQuarter => 90.0,
        TimeUnit::YearMonth => 31.0,
        TimeUnit::YearWeek => 7.0,
        TimeUnit::YearMonthDate => 1.0,
    }
}

/// `rows` as production's time scale plots them: the plain-dated rows, each keyed to its bucket's start (a gap
/// fill's scaffold where one ran), in time order, with the dates they carried before any bucket folded them.
fn plot_rows(r: &BarResolved, rows: &[Row], filled: Option<TimeUnit>, grain: Option<TimeUnit>) -> Result<(Vec<Row>, Vec<String>, Vec<String>), String> {
    let ax_x = &r.style.axis_x;
    let normalized = normalize_labeled_temporal(rows, &r.x)?;
    let mut dated: Vec<Row> = Vec::with_capacity(normalized.len());
    let mut originals: Vec<String> = Vec::with_capacity(normalized.len());
    for row in normalized {
        match row.get(&r.x) {
            None | Some(Value::Null) => {}
            Some(cell) => {
                originals.extend(cell.as_str().map(str::to_string));
                let instant = instant_text(cell).ok_or_else(|| format!("bar: x value {cell} is not a date or timestamp on a time x axis"))?;
                let mut row = row;
                row.insert(r.x.clone(), Value::String(instant));
                dated.push(row);
            }
        }
    }
    let dims: Vec<&str> = r.color.as_deref().into_iter().collect();
    let raw_dates: Vec<String> = dated.iter().map(|row| row_str(row, &r.x)).collect();
    let plotted = match filled {
        Some(tu) => {
            let fill = ax_x.fill.as_deref().ok_or("bar: axis_x.fill absent (theme required for a date x axis)")?;
            complete_ordinal_rows(&dated, &r.x, &dims, tu, fill, ax_x.fiscal_year_start_month)?
        }
        None => {
            for row in &mut dated {
                let date = row_str(row, &r.x);
                let at = grain.map_or(Some(date.clone()), |tu| time_unit_floor(&date, tu)).expect("a plain date floors to its bucket");
                if at != date {
                    row.insert(RAW_X.to_string(), Value::String(date));
                }
                row.insert(r.x.clone(), Value::String(at));
            }
            dated.sort_by_key(|row| row_str(row, &r.x));
            dated
        }
    };
    Ok((plotted, raw_dates, originals))
}

impl TimeX {
    /// The time axis's rows and buckets; a shape it does not model refuses by name
    /// (`docs/laws-bar.md#bucketed-temporal-domain` lists the shapes).
    pub(super) fn read(r: &BarResolved, rows: &[Row], chart_rows: &[Row], layer_x: &[LayerX]) -> Result<Self, String> {
        refuse_unported_shape(r)?;
        let ax_x = &r.style.axis_x;
        let filled_x = x_resolution(r, chart_rows)?;
        let law = filled_x.resolution;
        let grain = match law.grain {
            Grain::None => None,
            Grain::Calendar(tu) => Some(tu),
            Grain::Part(_) => return Err("bar: a time-part grain reached the time x axis, which authored time_unit refuses earlier".into()),
        };
        let (plotted, panel_dates, panel_originals) = plot_rows(r, rows, filled_x.filled, grain)?;
        // The scale spans every panel of the chart; a panel's bars are its own.
        let pooled = (!std::ptr::eq(rows, chart_rows)).then(|| plot_rows(r, chart_rows, filled_x.filled, grain)).transpose()?;
        let (scale_plotted, raw_dates, originals) = match &pooled {
            Some((plotted, dates, originals)) => (plotted, dates, originals),
            None => (&plotted, &panel_dates, &panel_originals),
        };
        if scale_plotted.is_empty() {
            return Err("bar: the time x axis resolved over rows that carry no date".into());
        }
        let ladder: Vec<String> = scale_plotted.iter().map(|row| row_str(row, &r.x)).collect::<BTreeSet<_>>().into_iter().collect();
        let range_dates = if filled_x.filled.is_some() { &ladder } else { raw_dates };
        // Each layer date sits where its own bucket does.
        let layer_start = |date: &String| -> Result<String, String> {
            let instant = instant_text(&Value::String(date.clone())).ok_or_else(|| format!("bar: layer x value {date:?} is not a date or timestamp on a time x axis"))?;
            Ok(grain.map_or(Some(instant.clone()), |tu| time_unit_floor(&instant, tu)).expect("a date floors to its bucket"))
        };
        let mut cats: BTreeSet<String> = ladder.iter().cloned().collect();
        // Production's ticks sit on the rows' own dates, not on the bucket starts the marks floor to.
        let mut label_dates: BTreeSet<String> = range_dates.iter().cloned().collect();
        for layer in layer_x {
            for date in &layer.dates {
                let start = layer_start(date)?;
                if layer.on_base_x {
                    label_dates.insert(start.clone());
                }
                cats.insert(start);
            }
        }
        let cats: Vec<String> = cats.into_iter().collect();
        let label_ladder: Vec<String> = label_dates.into_iter().collect();
        let ends: Vec<String> = match grain {
            Some(tu) => cats.iter().map(|date| bucket_end(date, tu)).collect(),
            None => Vec::new(),
        };
        let rows: Vec<Row> = plotted.into_iter().filter(|row| row_num(row, &r.y).is_some()).collect();
        // The negative-value corner law and signed y ladder are the linear arm's own, unmodeled.
        if rows.iter().any(|row| row_num(row, &r.y).is_some_and(|y| y < 0.0)) {
            return Err("bar: a negative bar on a time x axis needs the signed value axis ladder, which is not supported".into());
        }

        let mark = &r.style.mark;
        let mut scale_ax: AxisStyle = ax_x.clone();
        // An authored domain wins over the range a filter proves.
        if let (Some(range), None) = (&r.x_date_range, ax_x.scale.get("continuous").and_then(|c| c.get("domain"))) {
            let dates: Vec<&str> = range_dates.iter().map(String::as_str).collect();
            if let Some(domain) = range_domain(range, grain, &dates, ax_x.fiscal_year_start_month)? {
                scale_ax.scale["continuous"]["domain"] = Value::from(domain.to_vec());
            }
        }
        // The scale spans every datum, and a bar to the end of its bucket: a layer's own last date is a point.
        let mut kept: BTreeSet<String> = cats.iter().cloned().collect();
        let sizing = match grain {
            Some(grain) => {
                kept.insert(bucket_end(ladder.last().expect("a plotted date"), grain));
                for layer in layer_x.iter().filter(|layer| layer.spans_buckets) {
                    let last = layer.dates.iter().map(|date| layer_start(date)).collect::<Result<BTreeSet<_>, _>>()?;
                    kept.insert(bucket_end(last.last().expect("a layer with dates"), grain));
                }
                Sizing::Bucket { size: mark.size, grain, band_width: mark.band_width }
            }
            None => {
                let closest = closest_pair(&ladder);
                let width = ContinuousWidth::of(mark)?;
                scale_ax.scale["padding"] = Value::from(continuous_pad_px(mark, ax_x));
                Sizing::Instant { width, closest }
            }
        };
        let distinct = raw_dates.iter().collect::<BTreeSet<_>>().len();
        // Any clock time makes the rows continuous to the detector.
        let timed = raw_dates.iter().any(|date| date_text_day(date).is_some_and(|day| day.fract() != 0.0));
        let tooltip_grain = if timed {
            None
        } else {
            crate::families::temporal::detect_time_unit(&raw_dates.iter().map(String::as_str).collect::<Vec<_>>())
        };
        let own_dates: Vec<&str> = originals.iter().map(String::as_str).chain(layer_x.iter().flat_map(|l| l.dates.iter().map(String::as_str))).collect();
        let timed = SubdaySource::of(&own_dates);
        Ok(Self { rows, cats, ends, sizing, ladder: label_ladder, kept: kept.into_iter().collect(), law, scale_ax, grain, distinct, tooltip_grain, timed })
    }

    pub(super) fn axis(
        &self,
        r: &BarResolved,
        svg_w: f64,
        title_text: String,
        title_key: Option<crate::contract::LabelKey>,
        fonts: &FontBook,
    ) -> Result<TemporalXAxis, String> {
        let usable = r.style.label_usable_ratio.ok_or("bar: label_usable_ratio absent (theme required for a date x axis)")?;
        let mut axis = TemporalXAxis::resolve(
            &DateColumn { ladder: &self.ladder, kept: &self.kept, law: self.law, band_layer: false, bucket_aligned: true, timed: &self.timed, chart_width: svg_w },
            &self.scale_ax,
            svg_w * usable,
            title_text,
            title_key,
            fonts,
        )?;
        if self.grain.is_some() && r.style.axis_x.ticks.count.is_none() {
            axis.show_injected_ticks();
        }
        Ok(axis)
    }

    /// Whether a bar spans its bucket: production draws it as the clipped group
    /// the band bar is, whose stroke antialiases twice.
    pub(super) fn spans_buckets(&self) -> bool {
        matches!(self.sizing, Sizing::Bucket { .. })
    }

    /// Each category's leading edge and extent at a plot `pw` wide: a bucket's own span, or the width
    /// centered on an instant.
    fn extents(&self, axis: &TemporalXAxis, pw: f64) -> Vec<(f64, f64)> {
        self.extents_sized(&self.sizing, axis, pw)
    }

    fn extents_sized(&self, sizing: &Sizing, axis: &TemporalXAxis, pw: f64) -> Vec<(f64, f64)> {
        let px = |date: &str| axis.frac(date, pw) * pw;
        match sizing {
            Sizing::Bucket { .. } => self
                .cats
                .iter()
                .zip(&self.ends)
                .map(|(date, end)| {
                    let start = px(date);
                    (start, (px(end) - start).abs())
                })
                .collect(),
            Sizing::Instant { width, closest } => {
                let width = match closest {
                    Some((from, to)) => width.at((px(to) - px(from)).abs()),
                    None => width.lone(),
                };
                self.cats.iter().map(|date| (px(date) - width / 2.0, width)).collect()
            }
        }
    }

    /// The cells of a support-table strip that survive its thinning to the axis's label openers
    /// (`_label_period_filter_expr`): on a bucketed axis whose labels thinned past the grain, every opener
    /// the axis names has a bucket of data and the buckets are too dense to show every cell.
    pub(super) fn strip_keep(&self, axis: &TemporalXAxis, strip_w: f64, fiscal: u8) -> Option<Vec<bool>> {
        // The narrowest band (`_LABEL_STRIP_MIN_BAND_PX`) that shows every cell of the strip.
        const MIN_BAND_PX: f64 = 45.0;
        let (data_tu, visibility_tu) = axis.label_cadence()?;
        if self.distinct == 0 || strip_w / self.distinct as f64 >= MIN_BAND_PX {
            return None;
        }
        let keep = label_period_keep(&self.cats, data_tu, visibility_tu, fiscal)?;
        let opener_has_data = complete_buckets(&self.cats, data_tu, fiscal).iter().all(|date| {
            let (y, m, d) = parse_iso_date(date).expect("a bucket start parses");
            self.cats.contains(date) || !is_opener(y, m, d, data_tu, visibility_tu, fiscal)
        });
        opener_has_data.then_some(keep)
    }

    /// Where a point mark of each category sits at a plot `pw` wide (a layer's point, line vertex): on its instant,
    /// or at the start of its bucket.
    pub(super) fn anchors(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        match self.sizing {
            Sizing::Bucket { .. } => self.starts(axis, pw),
            Sizing::Instant { .. } => self.centers(axis, pw),
        }
    }

    /// Every category's center at a plot `pw` wide: where a support-table cell sits.
    pub(super) fn centers(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        self.extents(axis, pw).into_iter().map(|(start, span)| start + span / 2.0).collect()
    }

    /// Every category's leading edge at a plot `pw` wide.
    pub(super) fn starts(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        self.extents(axis, pw).into_iter().map(|(start, _)| start).collect()
    }

    /// Every category's columns at a plot `pw` wide, from its leading edge: a grouped bucket divides the span of
    /// the grain's reference bucket, so each bucket's series sit the same distance apart.
    pub(super) fn columns(&self, axis: &TemporalXAxis, pw: f64, split: Option<&ColumnSplit>) -> HashMap<String, BandColumns> {
        self.columns_sized(&self.sizing, axis, pw, split)
    }

    /// The columns a bar layer draws at a plot `pw` wide: its own mark's width law over the same instants or
    /// buckets, one column per category.
    pub(super) fn layer_columns(
        &self,
        axis: &TemporalXAxis,
        pw: f64,
        mark: &MarkStyle,
        split: Option<&ColumnSplit>,
        own_dates: &[String],
    ) -> Result<HashMap<String, BandColumns>, String> {
        // Over grouped buckets Vega-Lite sizes a layer bar off the series' offset scale, which leaves it no width:
        // a bar of nothing in the middle of the cluster.
        if let (Sizing::Bucket { .. }, Some(split)) = (&self.sizing, split) {
            return Ok(self
                .columns(axis, pw, Some(split))
                .into_iter()
                .map(|(cat, c)| (cat, BandColumns::unsplit(0.0, c.pad + ((split.n_series - 1) as f64 * c.step + c.width) / 2.0, 0.0, 0.0)))
                .collect());
        }
        let sizing = match &self.sizing {
            Sizing::Bucket { grain, .. } => Sizing::Bucket { size: mark.size, grain: *grain, band_width: mark.band_width },
            // The layer's own dates size its bars.
            Sizing::Instant { .. } => Sizing::Instant { width: ContinuousWidth::of(mark)?, closest: closest_pair(own_dates) },
        };
        Ok(self.columns_sized(&sizing, axis, pw, None))
    }

    fn columns_sized(&self, sizing: &Sizing, axis: &TemporalXAxis, pw: f64, split: Option<&ColumnSplit>) -> HashMap<String, BandColumns> {
        let extents = self.extents_sized(sizing, axis, pw);
        let reference = match (sizing, split) {
            (Sizing::Bucket { grain, .. }, Some(_)) => {
                let (start, end) = (&self.cats[0], &self.ends[0]);
                let days = |date: &str| {
                    let (y, m, d) = parse_iso_date(date).expect("a plain date parsed above");
                    days_from_civil(y, m, d) as f64
                };
                Some(canonical_days(*grain) * extents[0].1 / (days(end) - days(start)))
            }
            _ => None,
        };
        self.cats
            .iter()
            .zip(extents)
            .map(|(cat, (_, span))| {
                let columns = match (sizing, split) {
                    (Sizing::Instant { .. }, None) => BandColumns::unsplit(span, 0.0, span, span / 2.0),
                    (Sizing::Instant { .. }, Some(_)) => BandColumns::coincident(span),
                    (Sizing::Bucket { size: Some(size), .. }, None) => BandColumns::centered(span, *size),
                    // Vega-Lite offsets both edges by half a pixel, inward on a bucket
                    // narrower than a quarter pixel.
                    (Sizing::Bucket { size: None, band_width, .. }, None) => {
                        let (left, width) = super::bucket_bar_extent(0.0, span, *band_width);
                        BandColumns::unsplit(span, left, width, 0.0)
                    }
                    (Sizing::Bucket { .. }, Some(split)) => {
                        let reference = reference.expect("a grouped bucket has its reference span");
                        BandColumns::offset_bucket(reference, split)
                    }
                };
                (cat.clone(), columns)
            })
            .collect()
    }
}

fn bucket_end(date: &str, tu: TimeUnit) -> String {
    let (y, m, d) = parse_iso_date(date).expect("a plain date parsed above");
    let (ey, em, ed) = next_bucket((y, m, d), tu);
    format!("{ey:04}-{em:02}-{ed:02}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn range(start: &str, end: &str) -> DateRange {
        DateRange { start: start.into(), end: end.into() }
    }

    #[test]
    fn a_bucketed_range_runs_from_the_start_bucket_to_the_end_of_the_end_bucket() {
        let dates = ["2024-01-01", "2024-02-01", "2024-05-01"];
        let domain = range_domain(&range("2024-01-15", "2024-05-20"), Some(TimeUnit::YearMonth), &dates, 1).unwrap();
        assert_eq!(domain, Some(["2024-01-01".to_string(), "2024-06-01".to_string()]));
    }

    #[test]
    fn an_unbucketed_range_runs_over_its_whole_days() {
        let domain = range_domain(&range("2024-02-10", "2024-04-20"), None, &["2024-02-10", "2024-04-20"], 1).unwrap();
        assert_eq!(domain, Some(["2024-02-10".to_string(), "2024-04-21".to_string()]));
    }

    #[test]
    fn a_range_a_date_falls_outside_is_left_to_the_data() {
        let domain = range_domain(&range("2024-02-10", "2024-04-20"), None, &["2024-01-01", "2024-03-01"], 1).unwrap();
        assert_eq!(domain, None);
    }

    /// A week's mark sits on the Sunday on or before its bucket, and the week buckets step 7 days from the data's
    /// first day.
    #[test]
    fn a_weekly_range_floors_its_marks_to_sundays() {
        let dates = ["2024-01-03", "2024-01-10", "2024-01-17"];
        let domain = range_domain(&range("2024-01-03", "2024-01-17"), Some(TimeUnit::YearWeek), &dates, 1).unwrap();
        assert_eq!(domain, Some(["2023-12-31".to_string(), "2024-01-21".to_string()]));
    }

    #[test]
    fn a_timestamp_with_an_offset_folds_into_utc() {
        assert_eq!(instant_text(&json!("2024-01-03T23:00:00-05:00")).as_deref(), Some("2024-01-04T04:00:00"));
        assert_eq!(instant_text(&json!("2024-01-03 08:15")).as_deref(), Some("2024-01-03T08:15:00"));
        assert_eq!(instant_text(&json!("2024-01-03")).as_deref(), Some("2024-01-03"));
        assert_eq!(instant_text(&json!("soon")), None);
    }

    #[test]
    fn the_closest_pair_is_measured_in_fractional_days() {
        let dates: Vec<String> = ["2024-01-03T00:00:00", "2024-01-10T00:00:00", "2024-01-10T12:00:00"].map(String::from).to_vec();
        assert_eq!(closest_pair(&dates), Some(("2024-01-10T00:00:00".into(), "2024-01-10T12:00:00".into())));
        assert_eq!(closest_pair(&dates[..1]), None);
    }

    #[test]
    fn a_layer_timestamp_with_no_grain_is_keyed_by_its_utc_instant() {
        let rows: Vec<Row> = vec![serde_json::from_value(json!({"t": "2024-01-03 08:15:00", "cost": 1})).unwrap()];
        let keyed = at_bucket_starts(Cow::Borrowed(&rows), "t", None);
        assert_eq!(keyed[0]["t"], json!("2024-01-03T08:15:00"));
    }

    #[test]
    fn a_layer_row_is_keyed_by_the_bucket_start_of_its_date() {
        let rows: Vec<Row> = vec![serde_json::from_value(json!({"month": "2024-01-15", "cost": 1})).unwrap()];
        let keyed = at_bucket_starts(Cow::Borrowed(&rows), "month", Some(TimeUnit::YearMonth));
        assert_eq!(keyed[0]["month"], json!("2024-01-01"));
        assert!(matches!(at_bucket_starts(Cow::Borrowed(&rows), "month", None), Cow::Borrowed(_)));
    }
}
