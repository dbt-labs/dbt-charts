//! The UTC time scale of a vertical bar: a date column whose ordinal scaffold
//! is past the budget (or that no calendar grain explains) resolves a
//! continuous temporal axis, where each bar is positioned by its instant
//! instead of its band.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use serde::Deserialize;
use serde_json::{Map, Value};

use crate::common::{row_num, row_str, AxisStyle};
use crate::families::scatter_axis::{authored_day_domain, DateColumn, Indexed, TemporalXAxis};
use crate::families::subday::SubdaySource;
use crate::families::temporal::{
    civil_from_days, complete_ordinal_rows, days_from_civil, floor_to_bucket_start, next_bucket, normalize_labeled_temporal,
    TimePart, TimeUnit,
};
use crate::diagnostic::Diagnostic;
use crate::time_axis::{TimeAxis, Unreadable};
use crate::families::x_type::{Grain, XResolution};
use crate::labels_expr::Expr;
use measure::FontBook;

use crate::common::RAW_X;
use super::marks::{BandColumns, ColumnSplit};
use super::{continuous_pad_px, x_resolution, BarResolved, ContinuousWidth, MarkStyle};

type Row = Map<String, Value>;

/// The date `row` carries in `field`, as production's tooltip names it: the one before any bucket floored it.
pub(super) fn tooltip_date(row: &Row, field: &str) -> String {
    row_str(row, if row.contains_key(RAW_X) { RAW_X } else { field })
}

enum Sizing {
    /// A bar that kept its `timeUnit` spans its bucket: Vega-Lite draws it across `band_width` of the
    /// bucket's pixel span, centered, the stroke taking one pixel; an authored `size` centers on the bucket
    /// instead.
    Bucket { size: Option<f64>, grain: TimeUnit, band_width: f64 },
    /// A bar centered on its instant, sized by the law over `closest`, the closest pair of distinct dates
    /// (none for a lone instant, which takes the law's ceiling).
    Instant { width: ContinuousWidth, closest: Option<(String, String)> },
    /// A bar centered on its date of a point scale, at Vega-Lite's own step size whatever the dates' spacing.
    Stepped { width: ContinuousWidth },
    /// Grouped bars under value labels sit on the band scale the labels' categories make, over the axis's bands:
    /// side by side on dates a grain explains (`None`), else every series on one extent the law sizes.
    Band { width: Option<ContinuousWidth> },
}

/// The dates a layer puts on the shared time scale. Every layer's dates extend the scale; only those of a
/// layer on the base's own x column also join the axis's labels (`overlay_x_domain_values`).
pub(super) struct LayerX {
    pub(super) dates: Vec<String>,
    /// The column the dates are read from, named when one is no date.
    pub(super) x_field: String,
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
    /// Value labels type the field nominal beside the temporal bars: the axis is a point or band scale over the dates.
    indexed: Option<Indexed>,
    /// A point scale holds only the dates a bar's width is measured between: where the first date and the
    /// closest one it is measured against are not neighbors the width is NaN and nothing paints.
    pub(super) width_is_nan: bool,
    /// The grain production's tooltip detects over the rows' own dates, which an authored `time_unit` on the
    /// axis does not change.
    pub(super) tooltip_grain: Option<TimeUnit>,
    /// The rows' and layers' own dates, read for a time of day.
    pub(super) timed: SubdaySource,
    /// Every date the chart's rows and layers carry, read once; the keys of `cats` and `ends` among them.
    axis: TimeAxis,
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
fn range_domain(axis: &mut TimeAxis, range: &DateRange, grain: Option<TimeUnit>, dates: &[&str], fiscal: u8) -> Result<Option<[String; 2]>, Unreadable> {
    let iso = |day: i64| {
        let (y, m, d) = civil_from_days(day);
        format!("{y:04}-{m:02}-{d:02}")
    };
    let (start, end) = (axis.read(&range.start)?.at.floor() as i64, axis.read(&range.end)?.at.floor() as i64);
    let instants = dates.iter().map(|d| axis.read(d).map(|instant| instant.at)).collect::<Result<Vec<_>, _>>()?;
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

/// The closest pair of distinct dates, the pair a continuous bar's width is sized against. Every date is read.
fn closest_pair(axis: &TimeAxis, dates: &[String]) -> Option<(String, String)> {
    let mut days: Vec<(f64, &str)> = dates.iter().map(|d| (axis.day(d), d.as_str())).collect();
    days.sort_by(|a, b| a.0.total_cmp(&b.0));
    days.dedup_by(|a, b| a.0 == b.0);
    (1..days.len())
        .min_by(|&i, &j| (days[i].0 - days[i - 1].0).total_cmp(&(days[j].0 - days[j - 1].0)))
        .map(|at| (days[at - 1].1.to_string(), days[at].1.to_string()))
}

/// `rows` with each date of `field` keyed the way the time axis keys the bars': at the start of the `grain`
/// bucket holding it, or, with no grain, at its UTC instant text. A value no date parses from is left for
/// [`TimeX::read`] to refuse.
pub(super) fn at_bucket_starts<'a>(axis: &mut TimeAxis, rows: Cow<'a, [Row]>, field: &str) -> Cow<'a, [Row]> {
    let bucketed = axis.grain().unit.is_some();
    let mut start_of = |row: &Row| -> Option<String> {
        let text = row.get(field)?.as_str()?;
        let instant = axis.read(text).ok()?;
        let key = if bucketed { instant.key.clone() } else { instant.iso.clone() };
        (text != key).then_some(key)
    };
    let starts: Vec<Option<String>> = rows.iter().map(&mut start_of).collect();
    if starts.iter().all(Option::is_none) {
        return rows;
    }
    Cow::Owned(
        rows.iter()
            .zip(starts)
            .map(|(row, start)| {
                let mut row = row.clone();
                if let Some(start) = start {
                    let raw = row.insert(field.to_string(), Value::String(start)).expect("start_of read the field");
                    row.insert(RAW_X.to_string(), raw);
                }
                row
            })
            .collect(),
    )
}

/// Vega's `timeunit` transform yields NaN for a year below 100, so no bar of the chart has a position and
/// production raises `ERR-CHART-PAINTED-NO-MARKS`.
pub(super) fn has_year_below_100(axis: &mut TimeAxis, rows: &[Row], x: &str) -> bool {
    rows.iter().any(|row| row.get(x).and_then(Value::as_str).and_then(|text| axis.civil(text).ok()).is_some_and(|(year, _, _)| year < 100))
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

/// The distinct `keys`, read by `axis` and left to right.
fn in_time_order(axis: &mut TimeAxis, keys: impl IntoIterator<Item = String>) -> Result<Vec<String>, Unreadable> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for key in keys {
        if seen.insert(key.clone()) {
            axis.add(&key)?;
            out.push(key);
        }
    }
    axis.sort_by_instant(&mut out);
    Ok(out)
}

/// `rows` as production's time scale plots them: the plain-dated rows, each keyed to its bucket's start (a gap
/// fill's scaffold where one ran), in time order, with the dates they carried before any bucket folded them.
fn plot_rows(
    axis: &mut TimeAxis,
    r: &BarResolved,
    rows: &[Row],
    filled: Option<TimeUnit>,
    grain: Option<TimeUnit>,
) -> Result<(Vec<Row>, Vec<String>, Vec<String>, Vec<String>), Diagnostic> {
    let ax_x = &r.style.axis_x;
    let unreadable = |e: Unreadable| e.diagnostic(&r.id, &r.x, "bar");
    let normalized = normalize_labeled_temporal(rows, &r.x)?;
    let folds = axis.grain().part.is_some();
    let mut dated: Vec<Row> = Vec::with_capacity(normalized.len());
    let mut originals: Vec<String> = Vec::with_capacity(normalized.len());
    for row in normalized.into_owned() {
        match row.get(&r.x) {
            None | Some(Value::Null) => {}
            Some(cell) => {
                let Value::String(text) = cell else { return Err(unreadable(Unreadable::NotADate(cell.to_string()))) };
                let text = text.clone();
                let instant = axis.read(&text).map_err(unreadable)?;
                let (iso, key) = (instant.iso.clone(), instant.key.clone());
                originals.push(text);
                let mut row = row;
                // A time part folds the date onto its cycle; the tooltip keeps the date it came from.
                let at = if folds {
                    row.insert(RAW_X.to_string(), Value::String(iso));
                    key
                } else {
                    iso
                };
                row.insert(r.x.clone(), Value::String(at));
                dated.push(row);
            }
        }
    }
    let dims: Vec<&str> = r.color.as_deref().into_iter().collect();
    let raw_dates: Vec<String> = dated.iter().map(|row| row_str(row, &r.x)).collect();
    let shown_dates: Vec<String> = dated.iter().map(|row| tooltip_date(row, &r.x)).collect();
    let plotted = match filled {
        Some(tu) => {
            let fill = ax_x.fill.as_deref().ok_or("bar: axis_x.fill absent (theme required for a date x axis)")?;
            complete_ordinal_rows(&dated, &r.x, &dims, tu, fill, ax_x.fiscal_year_start_month)?
        }
        None => {
            for row in &mut dated {
                let date = row_str(row, &r.x);
                let at = match grain {
                    Some(tu) => axis.floor(&date, tu).map_err(unreadable)?,
                    None => date.clone(),
                };
                if at != date {
                    row.insert(RAW_X.to_string(), Value::String(date));
                }
                axis.add(&at).map_err(unreadable)?;
                row.insert(r.x.clone(), Value::String(at));
            }
            dated.sort_by(|a, b| {
                let (key_a, key_b) = (row_str(a, &r.x), row_str(b, &r.x));
                let (at_a, at_b) = (axis.get(&key_a).expect("keyed above").at, axis.get(&key_b).expect("keyed above").at);
                at_a.total_cmp(&at_b).then_with(|| key_a.cmp(&key_b))
            });
            dated
        }
    };
    Ok((plotted, raw_dates, originals, shown_dates))
}

impl TimeX {
    /// The time axis's rows and buckets; a shape it does not model refuses by name
    /// (`docs/laws-bar.md#bucketed-temporal-domain` lists the shapes).
    pub(super) fn read(
        axis: &mut TimeAxis,
        r: &BarResolved,
        rows: &[Row],
        chart_rows: &[Row],
        layer_x: &[LayerX],
        value_labels: bool,
    ) -> Result<Self, Diagnostic> {
        let unreadable = |e: Unreadable| e.diagnostic(&r.id, &r.x, "bar");
        let ax_x = &r.style.axis_x;
        let filled_x = x_resolution(r, chart_rows)?;
        let law = filled_x.resolution;
        let grain = match law.grain {
            Grain::None => None,
            Grain::Calendar(tu) => Some(tu),
            // A part's bars span its bucket on the folded cycle; an hour has no bucket and sits on its instant.
            Grain::Part(TimePart::MonthOfYear) => Some(TimeUnit::YearMonth),
            Grain::Part(TimePart::DayOfWeek | TimePart::DayOfMonth | TimePart::DayOfYear) => Some(TimeUnit::YearMonthDate),
            Grain::Part(TimePart::HourOfDay) => None,
        };
        let part = axis.grain().part;
        let (plotted, panel_dates, panel_originals, panel_shown) = plot_rows(axis, r, rows, filled_x.filled, grain)?;
        // The scale spans every panel of the chart; a panel's bars are its own.
        let pooled = (!std::ptr::eq(rows, chart_rows)).then(|| plot_rows(axis, r, chart_rows, filled_x.filled, grain)).transpose()?;
        let (scale_plotted, raw_dates, originals, shown_dates) = match &pooled {
            Some((plotted, dates, originals, shown)) => (plotted, dates, originals, shown),
            None => (&plotted, &panel_dates, &panel_originals, &panel_shown),
        };
        if scale_plotted.is_empty() {
            return Err("bar: the time x axis resolved over rows that carry no date".into());
        }
        let ladder: Vec<String> = in_time_order(axis, scale_plotted.iter().map(|row| row_str(row, &r.x))).map_err(unreadable)?;
        let range_dates = if filled_x.filled.is_some() { &ladder } else { raw_dates };
        // Each layer date sits where its own bucket does.
        let mut layer_starts: Vec<Vec<String>> = Vec::with_capacity(layer_x.len());
        for layer in layer_x {
            let mut starts = Vec::with_capacity(layer.dates.len());
            for date in &layer.dates {
                let start = match grain {
                    Some(tu) => axis.floor(date, tu),
                    None => axis.read(date).map(|instant| instant.iso.clone()),
                };
                starts.push(start.map_err(|e| e.diagnostic(&r.id, &layer.x_field, "bar"))?);
            }
            layer_starts.push(starts);
        }
        let mut cat_keys: Vec<String> = ladder.clone();
        // Production's ticks sit on the rows' own dates, not on the bucket starts the marks floor to.
        let mut label_keys: Vec<String> = range_dates.clone();
        for (layer, starts) in layer_x.iter().zip(&layer_starts) {
            for start in starts {
                if layer.on_base_x {
                    label_keys.push(start.clone());
                }
                cat_keys.push(start.clone());
            }
        }
        let cats: Vec<String> = in_time_order(axis, cat_keys).map_err(unreadable)?;
        let label_ladder: Vec<String> = in_time_order(axis, label_keys).map_err(unreadable)?;
        // An hour of the day has no calendar bucket: its bars span the hour on the folded instants.
        let hourly = part == Some(TimePart::HourOfDay);
        let spanning = grain.or(hourly.then_some(TimeUnit::YearMonthDate));
        let end_of = |axis: &mut TimeAxis, date: &str, tu: TimeUnit| -> Result<String, Unreadable> {
            if hourly {
                axis.hour_end(date)
            } else {
                axis.bucket_end(date, tu)
            }
        };
        let ends: Vec<String> = match spanning {
            Some(tu) => cats.iter().map(|date| end_of(axis, date, tu)).collect::<Result<_, _>>().map_err(unreadable)?,
            None => Vec::new(),
        };
        let signs = |keep: fn(f64) -> bool| scale_plotted.iter().filter_map(|row| row_num(row, &r.y)).any(keep);
        let mixed_sign = signs(|y| y > 0.0) && signs(|y| y < 0.0);
        // Vega-Lite splits a mixed-sign bar into the rows `>= 0` (a null counts) and the rows `< 0`, and a
        // discrete scale reads the dates of one then the other.
        let sign_split_order: Vec<String> = {
            let (below, above): (Vec<&Row>, Vec<&Row>) =
                scale_plotted.iter().partition(|row| row_num(row, &r.y).is_some_and(|y| y < 0.0));
            let mut seen = HashSet::new();
            above.into_iter().chain(below).map(|row| row_str(row, &r.x)).filter(|date| seen.insert(date.clone())).collect()
        };
        let rows: Vec<Row> = plotted.into_iter().filter(|row| row_num(row, &r.y).is_some()).collect();

        let mark = &r.style.mark;
        let mut scale_ax: AxisStyle = ax_x.clone();
        // An authored domain wins over the range a filter proves.
        if let (Some(range), None) = (&r.x_date_range, ax_x.scale.get("continuous").and_then(|c| c.get("domain"))) {
            let dates: Vec<&str> = range_dates.iter().map(String::as_str).collect();
            if let Some(domain) = range_domain(axis, range, grain, &dates, ax_x.fiscal_year_start_month).map_err(unreadable)? {
                scale_ax.scale["continuous"]["domain"] = Value::from(domain.to_vec());
            }
        }
        // The scale spans every datum, and a bar to the end of its bucket: a layer's own last date is a point.
        let mut kept: Vec<String> = cats.clone();
        let grouped = r.color.as_deref().is_some_and(|color| r.stack == "none" && !super::facet::color_1to1_with_cat(&rows, &r.x, color));
        let mut days: Vec<(f64, &str)> = ladder.iter().map(|d| (axis.day(d), d.as_str())).collect();
        days.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut indexed = None;
        let mut width_is_nan = false;
        let sizing = match grain {
            _ if !value_labels && spanning.is_some() => {
                let grain = spanning.expect("checked above");
                kept.push(end_of(axis, ladder.last().expect("a plotted date"), grain).map_err(unreadable)?);
                for (_, starts) in layer_x.iter().zip(&layer_starts).filter(|(layer, _)| layer.spans_buckets) {
                    let mut starts = starts.clone();
                    axis.sort_by_instant(&mut starts);
                    kept.push(end_of(axis, starts.last().expect("a layer with dates"), grain).map_err(unreadable)?);
                }
                Sizing::Bucket { size: mark.size, grain, band_width: mark.band_width }
            }
            _ => {
                if value_labels {
                    let padding = match grain {
                        Some(_) => ax_x.scale.get("padding").and_then(Value::as_f64).ok_or("bar: axis_x.scale.padding absent (theme required for a date x axis)")?,
                        None => continuous_pad_px(mark, ax_x)?,
                    };
                    // Vega-Lite sizes a bar on a point scale at the view step less two; a point scale pads at most a step.
                    indexed = Some(if grouped && grain.is_none() {
                        Indexed::Band { padding_inner: 0.0, padding_outer: padding.min(1.0) }
                    } else if grouped {
                        Indexed::Band { padding_inner: r.style.grouped.padding_inner, padding_outer: r.style.grouped.padding_outer }
                    } else {
                        Indexed::Point { padding: padding.min(1.0) }
                    });
                }
                match grain {
                    Some(_) if value_labels && grouped => Sizing::Band { width: None },
                    Some(_) => Sizing::Stepped { width: ContinuousWidth::stepped(mark) },
                    None => {
                        let closest = closest_pair(axis, &ladder);
                        let width = ContinuousWidth::of(mark)?;
                        if value_labels {
                            if let (Some(_), true) = (&closest, days.len() >= 2) {
                                let first_is_closest = days[1].0 - days[0].0 <= days.windows(2).map(|w| w[1].0 - w[0].0).fold(f64::INFINITY, f64::min);
                                // An authored domain is the scale's two ends alone; the pair must both be among them.
                                let in_domain = authored_day_domain(ax_x, "bar")?.is_none_or(|(lo, hi)| [days[0].0, days[1].0].iter().all(|d| *d == lo || *d == hi));
                                width_is_nan = !first_is_closest || !in_domain;
                            }
                        } else {
                            scale_ax.scale["padding"] = Value::from(continuous_pad_px(mark, ax_x)?);
                        }
                        match (value_labels && grouped, closest.clone()) {
                            (true, Some(_)) => Sizing::Band { width: Some(width) },
                            _ => Sizing::Instant { width, closest },
                        }
                    }
                }
            }
        };
        // Any clock time makes the rows continuous to the detector.
        let mut shown = Vec::with_capacity(shown_dates.len());
        for date in shown_dates {
            shown.push(axis.read(date).map_err(unreadable)?.at);
        }
        let timed = shown.iter().any(|day| day.fract() != 0.0);
        let tooltip_grain = if timed {
            None
        } else {
            crate::families::temporal::detect_time_unit(&shown_dates.iter().map(String::as_str).collect::<Vec<_>>())
        };
        let own_dates: Vec<&str> = originals.iter().map(String::as_str).chain(layer_x.iter().flat_map(|l| l.dates.iter().map(String::as_str))).collect();
        let timed = SubdaySource::of(axis, own_dates.iter().copied());
        let mut cats = cats;
        let mut kept: Vec<String> = in_time_order(axis, kept).map_err(unreadable)?;
        for end in &ends {
            axis.add(end).map_err(unreadable)?;
        }
        if indexed.is_some() {
            if mixed_sign {
                let ordered: HashSet<&String> = sign_split_order.iter().collect();
                cats = sign_split_order.iter().cloned().chain(cats.iter().filter(|c| !ordered.contains(c)).cloned()).collect();
            }
            kept = cats.clone();
        }
        let ladder = if indexed.is_some() { cats.clone() } else { label_ladder };
        Ok(Self { rows, cats, ends, sizing, ladder, kept, law, scale_ax, grain, indexed, width_is_nan, tooltip_grain, timed, axis: axis.clone() })
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
            &DateColumn { ladder: &self.ladder, kept: &self.kept, law: self.law, band_layer: false, indexed: self.indexed, bucket_aligned: true, timed: &self.timed, chart_width: svg_w, extra_days: &[], folded: matches!(self.law.grain, Grain::Part(_)), time: &self.axis },
            &self.scale_ax,
            svg_w * usable,
            title_text,
            title_key,
            fonts,
        )?;
        if self.grain.is_some() && r.style.axis_x.ticks.count.is_none() {
            axis.tick_under_labels();
        }
        if let Some(src) = r.style.axis_x.labels.expr.as_deref() {
            let expr = Expr::parse(src).map(|e| e.on("x")).map_err(|e| crate::labels_expr::describe(&e, src))?;
            axis.speak(expr, &self.ladder)?;
        }
        Ok(axis)
    }

    /// The UTC day of a date the chart read, if it did.
    pub(super) fn day_of(&self, key: &str) -> Option<f64> {
        self.axis.get(key).map(|instant| instant.at)
    }

    /// Whether a bar spans its bucket: production draws it as the clipped group
    /// the band bar is, whose stroke antialiases twice.
    pub(super) fn spans_buckets(&self) -> bool {
        matches!(self.sizing, Sizing::Bucket { .. })
    }

    /// Whether a bar is the clipped group the band bar is, whose stroke antialiases twice: one that keeps
    /// its calendar grain, bucketed or on the value labels' point scale.
    pub(super) fn double_stroke(&self) -> bool {
        self.grain.is_some()
    }

    /// Whether the x scale is discrete: value labels make it a point or band scale over the dates.
    pub(super) fn is_discrete(&self) -> bool {
        self.indexed.is_some()
    }

    /// Whether the bars sit on the axis's band scale rather than at positions of their own.
    pub(super) fn bars_on_band(&self) -> bool {
        matches!(self.sizing, Sizing::Band { .. })
    }

    pub(super) fn band_starts(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        axis.band_starts(&self.cats, pw)
    }

    /// The columns of every date's band bars, a band holding one per series. A date outside an authored domain has no
    /// band: Vega draws its columns on one extent at the plot's left edge, labels included.
    pub(super) fn band_columns(&self, axis: &TemporalXAxis, pw: f64, split: &ColumnSplit) -> HashMap<String, BandColumns> {
        let band_w = axis.bandwidth().expect("a band's bars sit on a band axis") * pw;
        let columns = match &self.sizing {
            Sizing::Band { width: None } => BandColumns::left_aligned(band_w, split),
            Sizing::Band { width: Some(width) } => BandColumns::centered_in_band(band_w, width.at(band_w), split),
            _ => unreachable!("only band bars take band columns"),
        };
        self.cats
            .iter()
            .map(|cat| (cat.clone(), if axis.knows(cat) { columns.clone() } else { columns.unplaced() }))
            .collect()
    }

    /// Each category centered on its date at `width`; a date outside a discrete domain has no position, and Vega
    /// places its bar at the plot's left edge.
    fn centered(&self, axis: &TemporalXAxis, pw: f64, width: f64) -> Vec<(f64, f64)> {
        self.cats
            .iter()
            .map(|date| if axis.knows(date) { (axis.frac(date, pw) * pw - width / 2.0, width) } else { (0.0, width) })
            .collect()
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
                self.centered(axis, pw, width)
            }
            Sizing::Stepped { width } => self.centered(axis, pw, width.at(0.0)),
            Sizing::Band { .. } => unreachable!("a support-table strip under bars on the band scale is refused before it paints")
        }
    }

    /// Where a point mark of each category sits at a plot `pw` wide (a layer's point, line vertex): on its instant,
    /// or at the start of its bucket.
    pub(super) fn anchors(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        match self.sizing {
            Sizing::Bucket { .. } => self.starts(axis, pw),
            Sizing::Instant { .. } | Sizing::Stepped { .. } => self.centers(axis, pw),
            Sizing::Band { .. } => self.band_starts(axis, pw),
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
            Sizing::Instant { .. } => Sizing::Instant { width: ContinuousWidth::of(mark)?, closest: closest_pair(&self.axis, own_dates) },
            Sizing::Stepped { .. } => Sizing::Stepped { width: ContinuousWidth::stepped(mark) },
            Sizing::Band { .. } => unreachable!("production paints no marks for a bar layer over grouped bars, which the chart refuses before it paints"),
        };
        Ok(self.columns_sized(&sizing, axis, pw, None))
    }

    fn columns_sized(&self, sizing: &Sizing, axis: &TemporalXAxis, pw: f64, split: Option<&ColumnSplit>) -> HashMap<String, BandColumns> {
        let extents = self.extents_sized(sizing, axis, pw);
        let reference = match (sizing, split) {
            (Sizing::Bucket { grain, .. }, Some(_)) => {
                let (start, end) = (&self.cats[0], &self.ends[0]);
                Some(canonical_days(*grain) * extents[0].1 / (self.axis.day(end).floor() - self.axis.day(start).floor()))
            }
            _ => None,
        };
        self.cats
            .iter()
            .zip(extents)
            .map(|(cat, (_, span))| {
                let columns = match (sizing, split) {
                    (Sizing::Band { .. }, _) => unreachable!("production paints no marks for a bar layer over grouped bars, which the chart refuses before it paints"),
                    (Sizing::Instant { .. } | Sizing::Stepped { .. }, None) => BandColumns::unsplit(span, 0.0, span, if axis.knows(cat) { span / 2.0 } else { 0.0 }),
                    (Sizing::Instant { .. } | Sizing::Stepped { .. }, Some(_)) => BandColumns::coincident(span),
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn plain_axis() -> TimeAxis {
        TimeAxis::new(crate::time_axis::Grain::default(), HashMap::new())
    }

    fn range(start: &str, end: &str) -> DateRange {
        DateRange { start: start.into(), end: end.into() }
    }

    #[test]
    fn a_bucketed_range_runs_from_the_start_bucket_to_the_end_of_the_end_bucket() {
        let dates = ["2024-01-01", "2024-02-01", "2024-05-01"];
        let domain = range_domain(&mut plain_axis(), &range("2024-01-15", "2024-05-20"), Some(TimeUnit::YearMonth), &dates, 1).unwrap();
        assert_eq!(domain, Some(["2024-01-01".to_string(), "2024-06-01".to_string()]));
    }

    #[test]
    fn an_unbucketed_range_runs_over_its_whole_days() {
        let domain = range_domain(&mut plain_axis(), &range("2024-02-10", "2024-04-20"), None, &["2024-02-10", "2024-04-20"], 1).unwrap();
        assert_eq!(domain, Some(["2024-02-10".to_string(), "2024-04-21".to_string()]));
    }

    #[test]
    fn a_range_a_date_falls_outside_is_left_to_the_data() {
        let domain = range_domain(&mut plain_axis(), &range("2024-02-10", "2024-04-20"), None, &["2024-01-01", "2024-03-01"], 1).unwrap();
        assert_eq!(domain, None);
    }

    /// A week's mark sits on the Sunday on or before its bucket, and the week buckets step 7 days from the data's
    /// first day.
    #[test]
    fn a_weekly_range_floors_its_marks_to_sundays() {
        let dates = ["2024-01-03", "2024-01-10", "2024-01-17"];
        let domain = range_domain(&mut plain_axis(), &range("2024-01-03", "2024-01-17"), Some(TimeUnit::YearWeek), &dates, 1).unwrap();
        assert_eq!(domain, Some(["2023-12-31".to_string(), "2024-01-21".to_string()]));
    }

    #[test]
    fn a_timestamp_with_an_offset_folds_into_utc() {
        let mut axis = plain_axis();
        let iso = |axis: &mut TimeAxis, text: &str| axis.read(text).map(|instant| instant.iso.clone());
        assert_eq!(iso(&mut axis, "2024-01-03T23:00:00-05:00"), Ok("2024-01-04T04:00:00".to_string()));
        assert_eq!(iso(&mut axis, "2024-01-03 08:15"), Ok("2024-01-03T08:15:00".to_string()));
        assert_eq!(iso(&mut axis, "2024-01-03"), Ok("2024-01-03".to_string()));
        assert_eq!(iso(&mut axis, "soon"), Err(Unreadable::NotADate("soon".into())));
        assert!(matches!(iso(&mut axis, "9999-12-31T23:00:00-05:00"), Err(Unreadable::OutsideYears(_))));
    }

    #[test]
    fn the_closest_pair_is_measured_in_fractional_days() {
        let dates: Vec<String> = ["2024-01-03T00:00:00", "2024-01-10T00:00:00", "2024-01-10T12:00:00"].map(String::from).to_vec();
        let mut axis = plain_axis();
        for date in &dates {
            axis.add(date).unwrap();
        }
        assert_eq!(closest_pair(&axis, &dates), Some(("2024-01-10T00:00:00".into(), "2024-01-10T12:00:00".into())));
        assert_eq!(closest_pair(&axis, &dates[..1]), None);
    }

    #[test]
    fn a_layer_timestamp_with_no_grain_is_keyed_by_its_utc_instant() {
        let rows: Vec<Row> = vec![serde_json::from_value(json!({"t": "2024-01-03 08:15:00", "cost": 1})).unwrap()];
        let keyed = at_bucket_starts(&mut plain_axis(), Cow::Borrowed(&rows), "t");
        assert_eq!(keyed[0]["t"], json!("2024-01-03T08:15:00"));
    }

    #[test]
    fn a_layer_row_is_keyed_by_the_bucket_start_of_its_date() {
        let rows: Vec<Row> = vec![serde_json::from_value(json!({"month": "2024-01-15", "cost": 1})).unwrap()];
        let keyed = at_bucket_starts(&mut TimeAxis::new(crate::time_axis::Grain { unit: Some(TimeUnit::YearMonth), part: None, band: false }, HashMap::new()), Cow::Borrowed(&rows), "month");
        assert_eq!(keyed[0]["month"], json!("2024-01-01"));
        assert!(matches!(at_bucket_starts(&mut plain_axis(), Cow::Borrowed(&rows), "month"), Cow::Borrowed(_)));
    }
}
