//! The UTC time scale of a vertical bar: a date column whose ordinal scaffold
//! is past the budget (or that no calendar grain explains) resolves a
//! continuous temporal axis, where each bar is positioned by its instant
//! instead of its band.

use std::collections::{BTreeSet, HashMap};

use serde_json::{Map, Value};

use crate::common::{row_num, row_str, AxisStyle};
use crate::families::scatter_axis::{DateColumn, TemporalXAxis};
use crate::families::temporal::{
    complete_ordinal_rows, days_from_civil, next_bucket, normalize_labeled_temporal, parse_iso_date, time_unit_floor,
    TimeUnit,
};
use crate::families::x_type::{Grain, XResolution};
use crate::text::FontBook;

use super::{continuous_pad_px, x_resolution, BandColumns, BarResolved, ColumnSplit, ContinuousWidth};

type Row = Map<String, Value>;

enum Sizing {
    /// A bar that kept its `timeUnit` spans its bucket: Vega-Lite draws it across `band_width` of the
    /// bucket's pixel span, centered, the stroke taking one pixel; an authored `size` centers on the bucket
    /// instead.
    Bucket { size: Option<f64>, grain: TimeUnit, band_width: f64 },
    /// A bar centered on its instant, sized by the law over `closest`, the closest pair of distinct dates.
    Instant { width: ContinuousWidth, closest: (String, String) },
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
}

/// The shapes of a time-axis bar this arm does not draw: each names what it declines.
fn refuse_unported_shape(r: &BarResolved) -> Result<(), String> {
    if r.style.axis_x.labels.expr.is_some() {
        return Err("bar: authored `axis_x.labels.expr` on a time x axis is not supported".into());
    }
    if r.y_start.is_some() {
        return Err("bar: a ranged bar on a time x axis is not supported".into());
    }
    if r.x_date_range.is_some() {
        return Err("bar: a date-filtered time x axis is not supported".into());
    }
    Ok(())
}

fn plain_date(cell: &Value) -> Option<&str> {
    cell.as_str().filter(|s| s.len() == 10 && parse_iso_date(s).is_some())
}

/// Vega's `timeunit` transform yields NaN for a year below 100, so no bar of the chart has a position and
/// production raises `ERR-CHART-PAINTED-NO-MARKS`.
pub(super) fn has_year_below_100(rows: &[Row], x: &str) -> bool {
    rows.iter().any(|row| row.get(x).and_then(plain_date).and_then(parse_iso_date).is_some_and(|(year, _, _)| year < 100))
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

impl TimeX {
    /// The time axis's rows and buckets; a shape it does not model refuses by name
    /// (`docs/laws-bar.md#bucketed-temporal-domain` lists the shapes).
    pub(super) fn read(r: &BarResolved, rows: &[Row], chart_rows: &[Row]) -> Result<Self, String> {
        refuse_unported_shape(r)?;
        let ax_x = &r.style.axis_x;
        let filled_x = x_resolution(r, chart_rows)?;
        let law = filled_x.resolution;
        let grain = match law.grain {
            Grain::None => None,
            Grain::Calendar(tu) => Some(tu),
            Grain::Part(_) => return Err("bar: a time-part grain reached the time x axis, which authored time_unit refuses earlier".into()),
        };
        let normalized = normalize_labeled_temporal(rows, &r.x)?;
        let mut dated: Vec<Row> = Vec::with_capacity(normalized.len());
        for row in normalized {
            match row.get(&r.x) {
                None | Some(Value::Null) => {}
                Some(cell) if plain_date(cell).is_some() => dated.push(row),
                Some(_) => return Err("bar: a time x axis over timestamps is not supported".into()),
            }
        }
        if dated.is_empty() {
            return Err("bar: the time x axis resolved over rows that carry no date".into());
        }
        let dims: Vec<&str> = r.color.as_deref().into_iter().collect();
        let plotted: Vec<Row> = match filled_x.filled {
            Some(tu) => {
                let fill = ax_x.fill.as_deref().ok_or("bar: axis_x.fill absent (theme required for a date x axis)")?;
                complete_ordinal_rows(&dated, &r.x, &dims, tu, fill, ax_x.fiscal_year_start_month)?
            }
            None => {
                let mut dated = dated;
                let mut seen = BTreeSet::new();
                for row in &mut dated {
                    let date = row_str(row, &r.x);
                    let at = grain.map_or(Some(date.clone()), |tu| time_unit_floor(&date, tu));
                    let at = at.expect("a plain date floors to its bucket");
                    let series = dims.iter().map(|d| row_str(row, d)).collect::<Vec<_>>();
                    if !seen.insert((at.clone(), series)) {
                        return Err("bar: two rows in one time bucket on a time x axis are not supported".into());
                    }
                    row.insert(r.x.clone(), Value::String(at));
                }
                dated.sort_by_key(|row| row_str(row, &r.x));
                dated
            }
        };
        let ladder: Vec<String> = plotted.iter().map(|row| row_str(row, &r.x)).collect::<BTreeSet<_>>().into_iter().collect();
        let ends: Vec<String> = match grain {
            Some(tu) => ladder.iter().map(|date| bucket_end(date, tu)).collect(),
            None => Vec::new(),
        };
        let rows: Vec<Row> = plotted.into_iter().filter(|row| row_num(row, &r.y).is_some()).collect();
        if rows.is_empty() {
            return Err("bar: a time x axis with no numeric measure is not supported".into());
        }
        // The negative-value corner law and signed y ladder are the linear arm's own, unmodeled.
        if rows.iter().any(|row| row_num(row, &r.y).is_some_and(|y| y < 0.0)) {
            return Err("bar: a negative bar on a time x axis needs the signed value axis ladder, which is not supported".into());
        }

        let mark = &r.style.mark;
        let mut scale_ax: AxisStyle = ax_x.clone();
        let mut kept = ladder.clone();
        let sizing = match grain {
            Some(grain) => {
                kept.push(bucket_end(ladder.last().expect("a plotted date"), grain));
                Sizing::Bucket { size: mark.size, grain, band_width: mark.band_width }
            }
            None => {
                let mut days: Vec<(f64, &str)> = ladder
                    .iter()
                    .map(|d| {
                        let (y, m, dd) = parse_iso_date(d).expect("a plain date parsed above");
                        (days_from_civil(y, m, dd) as f64, d.as_str())
                    })
                    .collect();
                days.sort_by(|a, b| a.0.total_cmp(&b.0));
                if days.len() < 2 {
                    return Err("bar: a single instant on a time x axis is not supported".into());
                }
                let at = (1..days.len()).min_by(|&i, &j| (days[i].0 - days[i - 1].0).total_cmp(&(days[j].0 - days[j - 1].0))).expect("two distinct dates");
                let closest = (days[at - 1].1.to_string(), days[at].1.to_string());
                let width = ContinuousWidth::of(mark)?;
                scale_ax.scale["padding"] = Value::from(continuous_pad_px(mark, ax_x));
                Sizing::Instant { width, closest }
            }
        };
        Ok(Self { rows, cats: ladder.clone(), ends, sizing, ladder, kept, law, scale_ax, grain })
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
            &DateColumn { ladder: &self.ladder, kept: &self.kept, law: self.law, band_layer: false },
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
        let px = |date: &str| axis.frac(date, pw) * pw;
        match &self.sizing {
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
                let width = width.at((px(&closest.1) - px(&closest.0)).abs());
                self.cats.iter().map(|date| (px(date) - width / 2.0, width)).collect()
            }
        }
    }

    /// Every category's leading edge at a plot `pw` wide.
    pub(super) fn starts(&self, axis: &TemporalXAxis, pw: f64) -> Vec<f64> {
        self.extents(axis, pw).into_iter().map(|(start, _)| start).collect()
    }

    /// Every category's columns at a plot `pw` wide, from its leading edge: a grouped bucket divides the span of
    /// the grain's reference bucket, so each bucket's series sit the same distance apart.
    pub(super) fn columns(&self, axis: &TemporalXAxis, pw: f64, split: Option<&ColumnSplit>) -> HashMap<String, BandColumns> {
        let extents = self.extents(axis, pw);
        let reference = match (&self.sizing, split) {
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
                let columns = match (&self.sizing, split) {
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
