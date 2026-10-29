//! Scatter's date x axis, one law for every shape production's
//! `resolve_cartesian_x_type` (`families::x_type`) routes a date column to:
//!
//! - **bucketed**: a UTC time scale over a calendar grain (`timeUnit`): each
//!   point sits at its bucket start, the ticks are Vega's own interval ticks
//!   (or the ladder's injected openers once the labels thin), the labels the
//!   shared temporal ladder (`temporal::resolve_vega_x_labels`) line/area run;
//! - **bare**: a UTC time scale no calendar grain explains: Vega's own ticks
//!   under d3's multi-scale format (`temporal::bare_temporal_ticks`);
//! - **ordinal**: a point scale over the distinct buckets (a fiscal year start
//!   turns a year or quarter grain ordinal), labeled by the same ladder.
//! - **time part**: `time_unit: monthofyear` (...) is `timeUnit: month` on a
//!   `time` scale over 2012, ticked by d3's ladder under the part's own text
//!   (`temporal::time_part_ticks`).
//!
//! Baked once per (date column, crowding width), then queried per candidate
//! plot width by the fit and painted at the final one, either inside a single
//! chart's plot group or, for a small-multiples composite, in the shared
//! footer band.


use crate::common::AxisStyle;
use crate::families::axis_caption::{authored_time_format, bucket_text, padded_day_domain, vega_axis_format, AxisCaption, CaptionScale, MS_PER_DAY};
use crate::families::cartesian::BandGeometry;
use crate::families::line::{bare_label_anchor, bare_label_layout, flush_anchor_px, tilted_temporal_label};
use crate::families::subday::{SubdayPlan, SubdaySource};
use crate::families::temporal::{
    authored_tick_count, authored_tick_interval, bare_temporal_ticks, generic_layout, none_tick_labels, part_band_texts, time_part_ticks, AuthoredTickCadence, build_all_temporal_labels, complete_buckets, interval_axis, interval_labels_allowed, labels_unpinned, IntervalAxis, interval_tick_days, interval_tick_labels, native_tick_interval,
    build_temporal_labels, parse_authored_time_unit, ResolvedVegaLabels, resolve_vega_x_labels, tooltip_header_date, TemporalLabel,
    time_unit_specifier, with_edge_point_labels, BareTick, TimePart, TimeUnit,
};
use crate::time_axis::{date_text_day, Grain as AxisGrain, TimeAxis};
use crate::families::x_type::{Grain, XResolution};
use scene::{Attrs, Chars, Escape, Node, TextBlock};
use measure::FontBook;
use crate::vega_fit::{self, Bounds};

/// Where a tick sits on the scale.
#[derive(Clone, Copy)]
enum At {
    /// A UTC instant, in days since the epoch.
    Day(f64),
    /// A point scale's `i`-th category.
    Index(usize),
}

#[derive(Clone)]
struct AxisLabel {
    at: At,
    /// Row one, or the inline `"2024 Jan"` text once tilted.
    text: String,
    /// The flat two-row label's context row (`"2024"` under `"Jan"`).
    row2: Option<String>,
    /// Measured advance of the widest drawn row.
    width: f64,
    /// `vega_fit::text_ink`'s alignment vocabulary for a rotated label; a flat
    /// label flushes at a range end and centers otherwise.
    tilted_align: &'static str,
    baseline: &'static str,
    /// A data point's own label, past the outermost tick: it takes no tick or gridline.
    edge: bool,
}

/// What names the first and last data points on an on-tick axis.
#[derive(Clone)]
struct EdgeFormat {
    format_tu: TimeUnit,
    fiscal: u8,
    time_format: Option<String>,
    /// The openers' unformatted labels, in the order of a [`Ticks::Fixed`] axis's labels.
    openers: Vec<TemporalLabel>,
}

impl AxisLabel {
    /// The lines the label paints on: its two rows, or the lines an authored `labels.expr` returned.
    fn rows(&self) -> usize {
        if self.row2.is_some() { 2 } else { crate::text::line_count(&self.text) }
    }
}

/// The scale ticks and points are positioned on.
#[derive(Clone)]
enum Scale {
    /// `lo..hi` in days, each end `pad` pixels inside the range
    /// (`scale.padding` on a continuous scale).
    Utc { lo: f64, hi: f64, pad: f64 },
    /// A point scale over the sorted distinct buckets.
    Point { domain: Vec<String>, padding: f64 },
    /// Beside a bar layer, the band scale a bar needs, over a unit range.
    Band { domain: Vec<String>, geometry: BandGeometry },
}

impl Scale {
    /// Vega's `padDomain`: the domain widened about its center so its ends sit
    /// `pad` pixels inside a range `pw` wide.
    fn domain_at(&self, pw: f64) -> (f64, f64) {
        match *self {
            Scale::Utc { lo, hi, pad } => {
                // A time scale's domain holds `Date`s, which keep whole milliseconds.
                let (lo, hi) = vega_fit::pad_domain((lo, hi), pw, pad, vega_fit::Bend::Linear);
                let whole_ms = |day: f64| (day * 864e5).trunc() / 864e5;
                (whole_ms(lo), whole_ms(hi))
            }
            Scale::Point { .. } | Scale::Band { .. } => unreachable!("only a UTC scale has a day domain"),
        }
    }

    /// A band axis sits half a pixel left of its band centers (Vega places a band
    /// tick at `scale(d) + bandwidth / 2` under the axis's own +0.5 shift).
    fn band_shift(&self, pw: f64) -> f64 {
        match self {
            Scale::Band { .. } if pw > 0.0 => 0.5 / pw,
            _ => 0.0,
        }
    }

    /// The fraction of the plot width `at` lands on, d3 centering a
    /// degenerate domain.
    fn frac(&self, at: At, pw: f64) -> f64 {
        match (self, at) {
            (Scale::Utc { .. }, At::Day(day)) => {
                let (lo, hi) = self.domain_at(pw);
                if lo == hi { 0.5 } else { (day - lo) / (hi - lo) }
            }
            (Scale::Point { domain, padding }, At::Index(i)) => {
                // d3's point scale: `step = 1 / max(1, n - 1 + 2·padding)`, the run centered.
                let n = domain.len() as f64;
                let step = 1.0 / (n - 1.0 + 2.0 * padding).max(1.0);
                (1.0 - step * (n - 1.0)) * 0.5 + step * i as f64
            }
            // A band's mark sits at its band's center.
            (Scale::Band { geometry, .. }, At::Index(i)) => geometry.edge(i, 0.5),
            _ => unreachable!("a UTC scale positions days, a point scale categories"),
        }
    }
}

/// How the ticks are produced.
#[derive(Clone)]
enum Ticks {
    /// A fixed set: the injected openers, or the point scale's categories.
    Fixed(Vec<AxisLabel>),
    /// Labels centered in the calendar period they name, which is coarser than the data's grain; ticks
    /// and gridlines sit on the period boundaries (days).
    Interval { labels: Vec<AxisLabel>, boundaries: Vec<f64> },
    /// Vega's calendar interval ticks of a bucketed scale, labeled where their
    /// date opens a label period; a padded scale moves the domain they span.
    Native {
        ladder: Vec<String>,
        tu: TimeUnit,
        format_tu: TimeUnit,
        visibility_tu: TimeUnit,
        fiscal: u8,
        time_format: Option<String>,
        /// `labels.time_unit: none`: Vega-Lite's own label per tick.
        vl_default_labels: bool,
        /// An authored `ticks.count`, which replaces the label grain's own
        /// calendar interval.
        cadence: Option<AuthoredTickCadence>,
    },
    /// Vega's own ticks of the domain, which move with the plot width, each
    /// painted through the authored time format where there is one.
    Bare {
        cadence: Option<AuthoredTickCadence>,
        time_format: Option<String>,
        subday: Option<SubdayPlan>,
        /// A dropped grain's labels from the curated ladder, in place of d3's multi-scale text.
        curated: Option<Vec<BareTick>>,
    },
    /// Vega's own ticks of a time-part scale (`timeUnit: month`, ...), under
    /// the part's own text.
    Part { part: TimePart, count: Option<usize>, time_format: Option<String> },
}

/// Whether a label is painted: parity keeps it and it has text. A thinned bar axis ticks exactly these.
pub(crate) fn is_painted(keep: bool, text: &str) -> bool {
    keep && !text.is_empty()
}

#[derive(Clone)]
pub(crate) struct TemporalXAxis {
    scale: Scale,
    ticks: Ticks,
    /// The bucket grain a point's date floors to.
    grain: Option<TimeUnit>,
    /// The cyclic part a point's date maps to, on a `time_unit: monthofyear`
    /// (...) axis.
    part: Option<TimePart>,
    /// See [`DateColumn::folded`].
    folded: bool,
    /// The grain the structured tooltip names a date at: the data's own.
    tooltip_grain: Option<TimeUnit>,
    /// The tooltip header of timed values spanning two days at most, which names the time of day instead.
    tooltip_time: Option<crate::families::subday::Header>,
    /// The column's instants print their clock in the board zone.
    aware: bool,
    /// An authored `labels.align` wins over every derived anchor.
    align: Option<String>,
    angle: f64,
    parity: bool,
    /// The labels measured when skip/tilt were tried and they still collide.
    pub(crate) collision: Option<usize>,
    font_size: f64,
    family: String,
    /// The authored `labels.font.case`, applied to every label before it is measured and painted.
    label_case: Option<String>,
    color: String,
    weight: String,
    /// The label font's `style`; `None` writes no `font-style`.
    style: Option<String>,
    label_pad: f64,
    label_limit: f64,
    separation: f64,
    tick_len: f64,
    ticks_visible: bool,
    /// A thinned bar axis ticks only under the labels it paints, so the ticks draw the labels' cadence.
    ticks_under_labels: bool,
    tick_width: f64,
    /// The stroke of the baseline rule on the plot edge this axis hangs from, which its ticks continue;
    /// the arm that paints the rule sets it.
    pub(crate) floor_rule: Option<String>,
    /// `axis.offset`; the drawn axis group adds its own half-pixel crisp
    /// translate on top (`axis_drop`).
    axis_offset: f64,
    edge: crate::common::XEdge,
    title_text: String,
    title_key: Option<crate::contract::LabelKey>,
    /// An authored `labels.expr`: every tick speaks what it evaluates to at the tick's instant.
    expr: Option<crate::labels_expr::Expr>,
    /// A point scale flushes its edge labels like a time scale's.
    flush_points: bool,
    /// Production compiles a `tickCount` interval onto this discrete scale, which Vega rejects: the chart paints no marks.
    ticks_interval_on_discrete: bool,
    /// An authored `labels.values`, in days: a tick outside it paints no label.
    listed: Option<Vec<f64>>,
    /// The first and last data points' labels; set on an on-tick axis only.
    edge_points: Option<EdgeFormat>,
    /// Every date the axis places, read once: the ladder, the kept dates and an authored domain's ends.
    time: TimeAxis,
}

/// A date column as the axis sees it.
pub(crate) struct DateColumn<'a> {
    /// Every row's distinct ISO dates, ascending: production lays the labels
    /// out from them (`emitters/_cartesian.py:416-418`).
    pub(crate) ladder: &'a [String],
    /// The kept rows' distinct dates, ascending: the scale spans them, since
    /// Vega-Lite's invalid filter runs after the ladder is built
    /// (`filterinvalid.ts:36-55`).
    pub(crate) kept: &'a [String],
    pub(crate) law: XResolution,
    /// A band-needing layer shares the scale, which makes an ordinal one a band scale.
    pub(crate) band_layer: bool,
    /// Whole days the scale also spans, unfloored: an annotation's dates join
    /// the extent exactly, as a data row's date joins it at its bucket start.
    pub(crate) extra_days: &'a [i64],
    /// The point or band scale a temporal field rides when a layer beside it types the same field nominal.
    pub(crate) indexed: Option<Indexed>,
    /// Production's `bucket_aligned_temporal`: bar's marks span buckets, which lets a weekly or daily axis
    /// keep its native day vocabulary on a continuous scale.
    pub(crate) bucket_aligned: bool,
    /// The column's own values, read for a time of day: a sub-day axis ticks and labels on the clock.
    pub(crate) timed: &'a SubdaySource,
    /// The chart's outer width, which the clock vocabulary reads its tier and tick budget off.
    pub(crate) chart_width: f64,
    /// The dates already sit on a time part's cycle (a bar folds its rows and their bucket ends itself),
    /// so the axis must not fold them again: an end that crossed the cycle would wrap to its start.
    pub(crate) folded: bool,
    /// The axis that read every date of the column and of the scale: the ladder, the kept dates and any bucket end the
    /// caller will ask a place of.
    pub(crate) time: &'a TimeAxis,
}

/// A temporal x axis laid on a point or band scale over its distinct dates, each tick a category in date order.
#[derive(Clone, Copy)]
pub(crate) enum Indexed {
    Point { padding: f64 },
    Band { padding_inner: f64, padding_outer: f64 },
}

/// `scale.continuous.domain` on a date axis: a pair of ISO dates, or of epoch
/// milliseconds, as days.
pub(crate) fn authored_day_domain(ax_x: &AxisStyle, family: &str) -> Result<Option<(f64, f64)>, String> {
    let Some(pair) = ax_x
        .scale
        .get("continuous")
        .and_then(|c| c.get("domain"))
        .and_then(serde_json::Value::as_array)
        .filter(|pair| pair.len() == 2)
    else {
        return Ok(None);
    };
    let day = |v: &serde_json::Value| -> Result<f64, String> {
        match v {
            serde_json::Value::Number(n) => Ok(n.as_f64().expect("a JSON number is finite") / 864e5),
            serde_json::Value::String(s) => date_text_day(s)
                .ok_or_else(|| format!("{family}: the authored date domain value {s:?} is not an ISO date")),
            other => Err(format!("{family}: the authored date domain value {other} is not a date")),
        }
    };
    Ok(Some((day(&pair[0])?, day(&pair[1])?)))
}

/// The category an authored domain's day names: the ISO date of a whole day.
fn day_key(day: f64) -> String {
    let (y, m, d) = crate::families::temporal::civil_from_days(day as i64);
    if day.fract() == 0.0 { format!("{y:04}-{m:02}-{d:02}") } else { day.to_string() }
}

impl TemporalXAxis {
    /// Registers the x scale for the `labels.expr`s that read it, over a plot `pw` wide.
    pub(crate) fn register(&self, pw: f64) {
        use crate::labels_expr::view::{register_utc, Span};
        match &self.scale {
            Scale::Utc { .. } => {
                let (lo, hi) = self.scale.domain_at(pw);
                register_utc("x", lo * 864e5, hi * 864e5, Span::Width);
            }
            // A point or band scale of dates keys on the dates' instants, which a registration does not hold: a read
            // of it refuses.
            Scale::Point { .. } | Scale::Band { .. } => {}
        }
    }

    /// Lays the axis out for `col`, `usable_width` being the crowding width the
    /// ladder measures labels against.
    pub(crate) fn resolve(
        col: &DateColumn,
        ax_x: &AxisStyle,
        usable_width: f64,
        title_text: String,
        title_key: Option<crate::contract::LabelKey>,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let labels_cfg = &ax_x.labels;
        // Production hands an authored count to Vega as `tickCount` on a
        // temporal scale and ignores it on an ordinal one.
        let count = ax_x.ticks.count.map(authored_tick_count);
        let cadence = match ax_x.ticks.time_unit.as_deref() {
            Some(unit) => Some(authored_tick_interval(unit, ax_x.ticks.step).map_err(|e| format!("scatter: {e}"))?),
            None => count.map(AuthoredTickCadence::Count),
        };
        let font_size = labels_cfg.font.size_or(11.0);
        let family = labels_cfg.font.family_or("sans-serif").to_string();
        let overlap = labels_cfg.overlap.as_ref();
        // An authored angle bypasses the overlap ladder (`resolve_axis_x_overlap`):
        // no tilt, no thinning, and `labelOverlap: false` beside an authored
        // overlap block.
        let pinned = labels_cfg.angle.is_some();
        let (skip, tilt) = if pinned {
            (false, false)
        } else {
            (overlap.is_some_and(|o| o.skip), overlap.is_some_and(|o| o.tilt))
        };
        let flushed = labels_cfg.flush.as_ref() != Some(&serde_json::Value::Bool(false));
        let ticks_cfg = &ax_x.ticks;
        let mut axis = Self {
            scale: Scale::Utc { lo: 0.0, hi: 0.0, pad: 0.0 },
            ticks: Ticks::Fixed(Vec::new()),
            grain: None,
            part: None,
            folded: false,
            tooltip_grain: None,
            tooltip_time: None,
            aware: false,
            align: labels_cfg.align.clone(),
            angle: 0.0,
            parity: false,
            collision: None,
            font_size,
            family: family.clone(),
            label_case: labels_cfg.font.case.clone(),
            color: labels_cfg.font.color.as_deref().unwrap_or("#626366").to_string(),
            weight: labels_cfg.font.weight_str(),
            style: labels_cfg.font.style.clone(),
            label_pad: labels_cfg.padding.unwrap_or(6.0),
            label_limit: labels_cfg.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT),
            separation: labels_cfg.min_gap.unwrap_or(0.0),
            tick_len: ticks_cfg.length.unwrap_or(6.0),
            ticks_visible: ticks_cfg.visible.unwrap_or(true),
            ticks_under_labels: false,
            tick_width: ticks_cfg.width.unwrap_or(1.0),
            floor_rule: None,
            axis_offset: ticks_cfg.offset.unwrap_or(0.0),
            edge: ax_x.x_edge(),
            title_text,
            title_key,
            expr: None,
            flush_points: false,
            ticks_interval_on_discrete: false,
            listed: labels_cfg.values.as_ref().map(|values| {
                values.iter().filter_map(|v| v.as_str().and_then(crate::families::temporal::iso_format_day)).collect()
            }),
            edge_points: None,
            time: TimeAxis::new(AxisGrain::default(), std::collections::HashMap::new()),
        };
        let grain = match col.law.grain {
            Grain::Calendar(tu) => Some(tu),
            Grain::None | Grain::Part(_) => None,
        };
        let part = match col.law.grain {
            Grain::Part(part) => Some(part),
            _ => None,
        };
        axis.grain = grain;
        axis.part = part;
        let unreadable = |e: crate::time_axis::Unreadable| match e {
            crate::time_axis::Unreadable::NotADate(value) => format!("scatter: x value {value:?} is not an ISO date"),
            outside @ crate::time_axis::Unreadable::OutsideYears(_) => outside.diagnostic("", "", "scatter").to_string(),
        };
        axis.time = col.time.clone();
        for date in col.ladder.iter().chain(col.kept) {
            axis.time.add(date).map_err(unreadable)?;
        }
        axis.folded = col.folded;
        axis.tooltip_grain = match part {
            Some(_) => {
                let dates: Vec<&str> = col.ladder.iter().map(String::as_str).collect();
                crate::families::temporal::detect_time_unit(&dates)
            }
            None => grain,
        };
        axis.aware = col.timed.aware();
        axis.tooltip_time = crate::families::subday::Header::of(col.timed, labels_cfg.clock).filter(|_| axis.tooltip_grain.is_none());
        let authored_domain = authored_day_domain(ax_x, "scatter")?;
        let time = axis.time.clone();
        let pad = ax_x.scale.get("padding").and_then(serde_json::Value::as_f64).unwrap_or(0.0);
        let fiscal = ax_x.fiscal_year_start_month;
        let label_grain = match grain {
            Some(tu) => crate::families::temporal::LabelUnit::resolve(tu, labels_cfg.time_unit.as_deref())?.grain(),
            None => None,
        };
        let authored = crate::families::temporal::AuthoredTimeFormat::parse(
            "scatter",
            crate::families::temporal::axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, label_grain),
        )?;
        let mut measure = |t: &str| labels_cfg.measure(fonts, t, font_size);
        // An authored angle skips the overlap ladder altogether
        // (`resolve_axis_x_overlap`): the labels keep the vocabulary of the
        // authored label grain (else the data's own), none thinned or tilted.
        let layout = |domain: &[String],
                      tu: TimeUnit,
                      unit: Option<&str>,
                      flushed: bool,
                      continuous: bool,
                      measure: &mut dyn FnMut(&str) -> Result<f64, String>|
         -> Result<ResolvedVegaLabels, String> {
            match labels_cfg.angle.filter(|_| unit != Some("none")) {
                Some(angle) => {
                    let format_tu = unit.and_then(parse_authored_time_unit).unwrap_or(tu);
                    Ok(ResolvedVegaLabels {
                        labels: build_temporal_labels(domain, tu, format_tu, format_tu, 1, fiscal, authored)?,
                        angle,
                        block_height: crate::families::temporal::pinned_block_height(
                            domain, format_tu, authored, angle, font_size, measure,
                        )?,
                        parity: false,
                        collision: None,
                        format_tu,
                        visibility_tu: format_tu,
                        year_step: 1,
                    })
                }
                None => resolve_vega_x_labels(
                    domain,
                    tu,
                    unit,
                    usable_width,
                    flushed,
                    col.bucket_aligned,
                    continuous,
                    fiscal,
                    skip,
                    tilt,
                    labels_cfg.tilt_increments.as_deref(),
                    font_size,
                    authored,
                    None,
                    measure,
                ),
            }
        };
        // The scale spans the kept rows' buckets, each floored to the grain.
        let floor = |date: &String| -> f64 { time.day_of_bucket(date) };
        // Vega's `timeunit` transform yields NaN for a year below 100: such a row stays out of the
        // scale domain and the ladder, and its point sits at x 0.
        let nan = |date: &String| grain.is_some() && time.get(date).is_some_and(|instant| instant.nan);
        let kept_at = col
            .kept
            .iter()
            .filter(|date| !nan(date))
            .map(|date| match part {
                Some(_) if col.folded => time.day(date),
                _ => floor(date),
            })
            .collect::<Vec<_>>();
        let spanned: Vec<f64> = kept_at.iter().copied().chain(col.extra_days.iter().map(|&day| day as f64)).collect();
        let span = spanned.iter().copied().reduce(f64::min).zip(spanned.iter().copied().reduce(f64::max));

        if let Some(indexed) = col.indexed {
            // An authored domain is the discrete scale's whole domain: its two ends, not the dates between.
            let domain: Vec<String> = match authored_domain {
                Some((lo, hi)) => [lo, hi].map(day_key).to_vec(),
                None => col.kept.to_vec(),
            };
            for key in &domain {
                axis.time.add(key).map_err(unreadable)?;
            }
            axis.scale = match indexed {
                Indexed::Point { padding } => Scale::Point { domain: domain.clone(), padding },
                Indexed::Band { padding_inner, padding_outer } => Scale::Band {
                    domain: domain.clone(),
                    geometry: BandGeometry::new(domain.len(), 0.0, 1.0, padding_inner, padding_outer),
                },
            };
            axis.flush_points = flushed && matches!(indexed, Indexed::Point { .. });
            let Some(tu) = grain else {
                // No grain explains the dates: Vega labels every category under d3's multi-scale format.
                let mut labels = Vec::with_capacity(domain.len());
                for (i, date) in domain.iter().enumerate() {
                    let (y, m, d) = axis.time.civil_of(date);
                    let text = match authored {
                        Some(format) => format.text(y, m, d),
                        None => crate::families::temporal::d3_multi_format(y, m, d)?,
                    };
                    let (tilted_align, baseline) = bare_label_anchor(labels_cfg.angle.unwrap_or(0.0), 0.5, 100.0, axis.edge);
                    let text = axis.cased(&text)?;
                    let width = fonts.measure(&text, &axis.family, axis.font_size)?;
                    labels.push(AxisLabel { at: At::Index(i), text, row2: None, width, tilted_align, baseline, edge: false });
                }
                axis.angle = labels_cfg.angle.unwrap_or(0.0);
                axis.parity = false;
                axis.ticks = Ticks::Fixed(labels);
                axis.ticks_interval_on_discrete = ax_x.ticks.time_unit.is_some();
                return Ok(axis);
            };
            let ladder = complete_buckets(col.ladder, tu, fiscal);
            let resolved = if labels_cfg.time_unit.as_deref() == Some("none") {
                layout(col.ladder, tu, labels_cfg.time_unit.as_deref(), flushed, true, &mut measure)?
            } else {
                layout(&ladder, tu, labels_cfg.time_unit.as_deref(), flushed, true, &mut measure)?
            };
            axis.angle = labels_cfg.angle.unwrap_or(resolved.angle);
            axis.parity = if pinned { overlap.is_none() } else { resolved.parity };
            axis.collision = resolved.collision;
            // Production lets Vega tick every boundary of an unthinned axis (`tickCount: {interval}`); a point
            // or band scale throws on that.
            axis.ticks_interval_on_discrete =
                resolved.visibility_tu == tu && resolved.format_tu == tu && resolved.year_step == 1 || ax_x.ticks.time_unit.is_some();
            let mut labels = Vec::new();
            for tl in &resolved.labels {
                let Some(i) = domain.iter().position(|d| *d == ladder[tl.domain_idx]) else { continue };
                labels.push(axis.label(tl, At::Index(i), resolved.format_tu, fonts)?);
            }
            axis.ticks = Ticks::Fixed(labels);
            return Ok(axis);
        }

        if col.law.vl_type == crate::families::temporal::VegaType::Ordinal {
            let tu = grain.expect("production routes a date column ordinal only over a calendar grain");
            let resolved = layout(col.ladder, tu, labels_cfg.time_unit.as_deref(), false, false, &mut measure)?;
            let domain: Vec<String> = col.kept.to_vec();
            let padding = ax_x.scale.get("padding").and_then(serde_json::Value::as_f64).unwrap_or(0.5);
            axis.angle = labels_cfg.angle.unwrap_or(resolved.angle);
            axis.parity = if pinned { overlap.is_none() } else { resolved.parity };
            axis.collision = resolved.collision;
            // Unthinned, every bucket is a tick that speaks (`values` stays the
            // full bucket list); the opener gate only applies once thinned.
            let speaking = if resolved.visibility_tu == tu && resolved.year_step == 1 {
                build_all_temporal_labels(col.ladder, resolved.format_tu, fiscal, authored)?
            } else {
                resolved.labels.clone()
            };
            let mut labels = Vec::new();
            for tl in &speaking {
                // A tick on a category no kept row carries is outside the scale.
                let Some(i) = domain.iter().position(|d| *d == col.ladder[tl.domain_idx]) else { continue };
                labels.push(axis.label(tl, At::Index(i), resolved.format_tu, fonts)?);
            }
            axis.scale = if col.band_layer {
                let n = domain.len();
                Scale::Band { domain, geometry: BandGeometry::new(n, 0.0, 1.0, padding, padding) }
            } else {
                Scale::Point { domain, padding }
            };
            axis.ticks = Ticks::Fixed(labels);
            return Ok(axis);
        }

        let ladder_dates: Vec<String> = col.ladder.iter().filter(|date| !nan(date)).cloned().collect();
        if authored_domain.is_none() && span.is_none() && col.kept.iter().any(nan) {
            axis.scale = Scale::Utc { lo: 0.0, hi: 0.0, pad };
            return Ok(axis);
        }
        let (lo, hi) = match (authored_domain, span) {
            (Some(pair), _) => pair,
            (None, Some(pair)) => pair,
            (None, None) => return Err("scatter: a date x needs at least one kept date".into()),
        };
        axis.scale = Scale::Utc { lo, hi, pad };
        let label_unit = crate::families::temporal::label_unit_with_boundary(labels_cfg.time_unit.as_deref(), (lo, hi), fiscal);
        let edge_allowed = labels_unpinned(labels_cfg, cadence.is_some());
        let interval_allowed = interval_labels_allowed(labels_cfg, cadence.is_some(), authored.is_some());
        if let Some(part) = part {
            let texts = part_band_texts(col.ladder, part, labels_cfg.time_unit.as_deref(), authored)
                .map_err(|e| format!("scatter: {e}"))?;
            let gap = measure(" ")?;
            let widths = texts.iter().map(|t| measure(t).map(|w| w + gap)).collect::<Result<Vec<_>, _>>()?;
            let layout = generic_layout(&widths, usable_width, skip, tilt, labels_cfg.tilt_increments.as_deref(), font_size)?;
            axis.collision = layout.collision;
            axis.angle = labels_cfg.angle.unwrap_or(layout.angle);
            axis.parity = if pinned { overlap.is_none() } else { layout.parity || layout.collision.is_some() && skip };
            axis.ticks = Ticks::Part {
                part,
                count,
                time_format: crate::families::temporal::axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, None)
                    .map(str::to_string),
            };
            return Ok(axis);
        }
        let Some(tu) = grain else {
            // No calendar grain: Vega's own ticks, laid out under the grain the
            // detector would find (`_temporal_layout`), thinned by parity.
            let layout_tu = match col.law.fill_grain {
                Grain::Calendar(tu) => Some(tu),
                // An authored `none` is no grain `_temporal_layout` takes, so it detects its own.
                Grain::None if ax_x.time_unit.as_ref().and_then(serde_json::Value::as_str) == Some("none") => {
                    crate::families::temporal::detect_time_unit(&col.ladder.iter().map(String::as_str).collect::<Vec<_>>())
                }
                _ => None,
            };
            let time_format = labels_cfg.spec().filter(|f| crate::families::temporal::is_time_format(f)).map(str::to_string);
            let subday = crate::families::subday::plan_for(col.timed, ax_x, cadence, time_format.is_some(), col.chart_width, col.chart_width);
            let keeps_ladder = crate::families::temporal::keeps_dropped_grain_ladder(ax_x, col.law.fill_grain, cadence, time_format.is_some(), subday.is_some(), authored_domain.is_some());
            let mut curated = None;
            let resolved = match layout_tu {
                Some(tu) => {
                    let ladder = complete_buckets(col.ladder, tu, fiscal);
                    let resolved = layout(&ladder, tu, label_unit, flushed, true, &mut measure)?;
                    if keeps_ladder {
                        curated = Some(crate::families::temporal::ladder_bare_ticks((lo, hi), &ladder, &resolved)?);
                    }
                    Some((tu, resolved))
                }
                None => None,
            };
            let (angle, parity) = match &resolved {
                Some((_, resolved)) => {
                    axis.collision = resolved.collision;
                    bare_label_layout(Some(resolved), skip, labels_cfg)
                }
                None => bare_label_layout(None, skip, labels_cfg),
            };
            if let Some((tu, resolved)) = resolved.as_ref().filter(|_| interval_allowed) {
                let interval = interval_axis(
                    (lo, hi),
                    &|day| day,
                    Some(*tu),
                    resolved.periods(),
                    usable_width,
                    fiscal,
                    &mut measure,
                )?;
                if let Some(interval) = interval {
                    axis.set_interval(interval, resolved.visibility_tu, fonts)?;
                    return Ok(axis);
                }
            }
            axis.angle = labels_cfg.angle.unwrap_or(angle);
            axis.parity = if pinned { overlap.is_none() } else { parity };
            axis.edge_points = resolved
                .as_ref()
                .filter(|_| edge_allowed && subday.is_none())
                .map(|(_, resolved)| EdgeFormat {
                    format_tu: resolved.format_tu,
                    fiscal,
                    time_format: time_format.clone(),
                    openers: Vec::new(),
                });
            axis.ticks = Ticks::Bare { cadence, time_format, subday, curated };
            return Ok(axis);
        };

        let ladder = complete_buckets(&ladder_dates, tu, fiscal);
        for date in &ladder {
            axis.time.add(date).map_err(unreadable)?;
        }
        // `labels.time_unit: none` measures the rows' own dates, not the
        // enumerated span (`_temporal_layout`'s generic branch).
        let resolved = if labels_cfg.time_unit.as_deref() == Some("none") {
            layout(&ladder_dates, tu, label_unit, flushed, true, &mut measure)?
        } else {
            layout(&ladder, tu, label_unit, flushed, true, &mut measure)?
        };
        axis.angle = labels_cfg.angle.unwrap_or(resolved.angle);
        axis.parity = if pinned { overlap.is_none() } else { resolved.parity };
        axis.collision = resolved.collision;
        // With the label grain at the data grain Vega ticks every calendar
        // boundary of the scale (`tickCount: {interval}`, `type_inference.py:
        // 1049-1052`), an empty bucket included; a thinner ladder injects its
        // openers as `values` instead.
        let native = resolved.visibility_tu == tu && resolved.format_tu == tu && resolved.year_step == 1;
        let time_format = crate::families::temporal::axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, Some(tu)).map(str::to_string);
        axis.edge_points = edge_allowed.then(|| EdgeFormat {
            format_tu: resolved.format_tu,
            fiscal,
            time_format: time_format.clone(),
            openers: if native { Vec::new() } else { resolved.labels.clone() },
        });
        if native {
            axis.ticks = Ticks::Native {
                ladder,
                tu,
                format_tu: resolved.format_tu,
                visibility_tu: resolved.visibility_tu,
                fiscal,
                time_format,
                vl_default_labels: labels_cfg.time_unit.as_deref() == Some("none"),
                cadence,
            };
            return Ok(axis);
        }
        if interval_allowed {
            let interval = interval_axis(
                (lo, hi),
                &|day| day,
                Some(tu),
                resolved.periods(),
                usable_width,
                fiscal,
                &mut measure,
            )?;
            if let Some(interval) = interval {
                axis.set_interval(interval, resolved.visibility_tu, fonts)?;
                return Ok(axis);
            }
        }
        // An opener's own raw date, off its floored bucket start only under
        // the week grain (a Sunday bucket opened by a Monday).
        let raw = tu == TimeUnit::YearWeek;
        let mut labels = Vec::new();
        for tl in &resolved.labels {
            let date = &ladder[tl.domain_idx];
            let day = if raw { axis.time.day(date) } else { axis.time.day_of_bucket(date) };
            labels.push(axis.label(tl, At::Day(day), resolved.format_tu, fonts)?);
        }
        axis.ticks = Ticks::Fixed(labels);
        Ok(axis)
    }

    /// Labels centered on the periods of `interval`, ticks and gridlines on their boundaries.
    fn set_interval(&mut self, interval: IntervalAxis, visibility_tu: TimeUnit, fonts: &FontBook) -> Result<(), String> {
        self.parity = false;
        // A boundary tick is what separates one period's label from the next: always drawn.
        self.ticks_visible = true;
        let mut labels = Vec::new();
        for (tl, &(from, to)) in interval.labels.iter().zip(&interval.label_spans) {
            labels.push(self.label(tl, At::Day((from + to) / 2.0), visibility_tu, fonts)?);
        }
        self.ticks = Ticks::Interval { labels, boundaries: interval.tick_days };
        Ok(())
    }

    fn cased(&self, text: &str) -> Result<String, String> {
        crate::common::label_case(self.label_case.as_deref(), text)
    }

    fn label(&self, tl: &TemporalLabel, at: At, format_tu: TimeUnit, fonts: &FontBook) -> Result<AxisLabel, String> {
        let (text, rows, tilted_align, baseline) = tilted_temporal_label(tl, self.angle, format_tu, self.edge);
        let (text, row2) = (self.cased(&text)?, self.cased(&tl.row2)?);
        let mut width = fonts.measure_shaped(&text, &self.family, self.font_size)?;
        if rows == 2 {
            width = width.max(fonts.measure_shaped(&row2, &self.family, self.font_size)?);
        }
        Ok(AxisLabel { at, text, row2: (rows == 2).then_some(row2), width, tilted_align, baseline, edge: false })
    }

    fn plain_label(&self, text: String, row2: Option<String>, day: f64, frac: f64, pw: f64, fonts: &FontBook) -> Result<(f64, AxisLabel), String> {
        let (tilted_align, baseline) = bare_label_anchor(self.angle, frac, pw, self.edge);
        let text = self.cased(&text)?;
        let row2 = row2.map(|row| self.cased(&row)).transpose()?;
        let mut width = fonts.measure_shaped(&text, &self.family, self.font_size)?;
        if let Some(row2) = &row2 {
            width = width.max(fonts.measure_shaped(row2, &self.family, self.font_size)?);
        }
        Ok((frac, AxisLabel { at: At::Day(day), text, row2, width, tilted_align, baseline, edge: false }))
    }

    /// The tick labels at a plot `pw` wide, each with its scale fraction.
    /// `labelFlush` tests the pixel position against the range ends, so on a
    /// collapsed plot, where every tick sits at both, the right end wins.
    fn is_listed(&self, day: f64) -> bool {
        self.listed.as_ref().is_none_or(|days| days.iter().any(|d| (d - day).abs() < 1e-9))
    }

    fn labels_at(&self, pw: f64, fonts: &FontBook) -> Result<Vec<(f64, AxisLabel)>, String> {
        if crate::labels_expr::view::active() {
            self.register(pw);
        }
        let mut labels = self.labels_on_scale(pw, fonts)?;
        if pw == 0.0 {
            for (frac, _) in &mut labels {
                *frac = 1.0;
            }
        }
        Ok(labels)
    }

    /// Whether Vega throws on this axis's compiled ticks, so the chart paints no marks.
    pub(crate) fn raises_no_marks(&self) -> bool {
        self.ticks_interval_on_discrete
    }

    /// The ticks speak `expr` evaluated at their instants, in place of the ladder's own text. An expression that
    /// throws on a date of the axis fails Vega's whole view: the axis then only records that no mark paints.
    pub(crate) fn speak(&mut self, expr: crate::labels_expr::Expr, dates: &[String]) -> Result<(), String> {
        let days = dates.iter().map(|date| self.time.day(date)).collect::<Vec<_>>();
        let thrown = crate::families::temporal::first_throw_at_dates(&expr, dates, &days, false, |day| self.default_label(day))?;
        if thrown.is_some() {
            self.ticks_interval_on_discrete = true;
            return Ok(());
        }
        self.expr = Some(expr);
        Ok(())
    }

    /// `datum.label`: the text Vega formats a tick with when the axis authors no format, which Vega-Lite takes
    /// from the grain the field is bucketed to.
    fn default_label(&self, day: i64) -> Result<String, String> {
        crate::families::temporal::datum_label(self.tooltip_grain.filter(|_| self.part.is_none()), day)
    }

    fn labels_on_scale(&self, pw: f64, fonts: &FontBook) -> Result<Vec<(f64, AxisLabel)>, String> {
        let labels = self.tick_labels(pw, fonts)?;
        let Some(expr) = &self.expr else { return Ok(labels) };
        let count = labels.len();
        labels
            .into_iter()
            .enumerate()
            .map(|(i, (frac, label))| {
                let day = match (label.at, &self.scale) {
                    (At::Day(day), _) => day,
                    (At::Index(i), Scale::Point { domain, .. } | Scale::Band { domain, .. }) => self.time.day(&domain[i]),
                    (At::Index(_), Scale::Utc { .. }) => unreachable!("a UTC scale ticks days"),
                };
                // An unbucketed aware axis ticks the board zone's midnights: the expression reads the tick's own instant in
                // that zone, and the default label names its wall-clock day.
                let aware = self.instants_aware();
                let (instant, wall) = if aware { (day, crate::time_zone::wall_day(day)?) } else { (day.floor(), day) };
                let value = crate::labels_expr::Val::Date(instant * 864e5);
                let default = if expr.uses_label() { self.default_label(wall.floor() as i64)? } else { String::new() };
                let text = crate::time_zone::with_expression_local(aware, || {
                    expr.eval(&crate::labels_expr::Tick::at(i, count, value, &default))
                })
                .map_err(|e| crate::labels_expr::describe(&e, "a validated tick"))?;
                let text = self.cased(&text)?;
                let width = fonts.measure(&text, &self.family, self.font_size)?;
                Ok((frac, AxisLabel { text, row2: None, width, ..label }))
            })
            .collect()
    }

    fn tick_labels(&self, pw: f64, fonts: &FontBook) -> Result<Vec<(f64, AxisLabel)>, String> {
        let in_range = |frac: f64| (0.0..=1.0).contains(&frac);
        match &self.ticks {
            Ticks::Fixed(labels) if self.edge_points.is_some() && self.expr.is_none() => {
                let edge = self.edge_points.as_ref().expect("guarded");
                let kept: Vec<usize> = (0..labels.len()).filter(|&i| in_range(self.scale.frac(labels[i].at, pw))).collect();
                let days: Vec<f64> = kept.iter().map(|&i| match labels[i].at {
                    At::Day(day) => day,
                    At::Index(_) => unreachable!("a UTC scale's openers sit on days"),
                }).collect();
                let tls = kept.iter().enumerate().map(|(k, &i)| TemporalLabel { domain_idx: k, ..edge.openers[i].clone() }).collect();
                self.with_edge_points(edge, tls, &days, &days, pw, fonts)
            }
            Ticks::Fixed(labels) | Ticks::Interval { labels, .. } => Ok(labels
                .iter()
                .map(|l| (self.scale.frac(l.at, pw) - self.scale.band_shift(pw), l.clone()))
                .filter(|&(frac, _)| matches!(self.scale, Scale::Point { .. } | Scale::Band { .. }) || in_range(frac))
                .collect()),
            Ticks::Native { ladder, tu, format_tu, visibility_tu, fiscal, time_format, vl_default_labels, cadence } => {
                let instants = native_ticks(&self.scale, *tu, *cadence, pw);
                // A tick's label reads the date it falls on, the half-day ticks of a short span included.
                let days: Vec<i64> = instants.iter().map(|at| at.floor() as i64).collect();
                let authored =
                    crate::families::temporal::AuthoredTimeFormat::parse("scatter", time_format.as_deref())?;
                let ticks = if *vl_default_labels {
                    none_tick_labels(&days, *tu, authored)?
                } else {
                    interval_tick_labels(&days, ladder, *tu, *format_tu, *visibility_tu, 1, *fiscal, authored)?
                };
                if let Some(edge) = self.edge_points.as_ref().filter(|_| self.expr.is_none() && !*vl_default_labels) {
                    let label_days: Vec<f64> = ticks.iter().map(|tl| instants[tl.domain_idx]).collect();
                    let tls = ticks.into_iter().enumerate().map(|(i, tl)| TemporalLabel { domain_idx: i, ..tl }).collect();
                    return self.with_edge_points(edge, tls, &label_days, &instants, pw, fonts);
                }
                ticks
                    .into_iter()
                    .map(|tl| {
                        let day = instants[tl.domain_idx];
                        let tl = if self.is_listed(day) { tl } else { TemporalLabel { row1: String::new(), row2: String::new(), ..tl } };
                        Ok((self.scale.frac(At::Day(day), pw), self.label(&tl, At::Day(day), *format_tu, fonts)?))
                    })
                    .collect()
            }
            Ticks::Bare { cadence, time_format, subday, curated } => {
                let (lo, hi) = self.scale.domain_at(pw);
                let authored = crate::families::temporal::AuthoredTimeFormat::parse("scatter", time_format.as_deref())?;
                let ticks = match curated {
                    Some(ticks) => ticks
                        .iter()
                        .map(|t| BareTick { frac: (t.day - lo) / (hi - lo), ..t.clone() })
                        .filter(|t| (0.0..=1.0).contains(&t.frac))
                        .collect(),
                    None => bare_temporal_ticks((lo, hi), pw, *cadence, authored, subday.as_ref(), self.aware)?,
                };
                if let Some(edge) = self.edge_points.as_ref().filter(|_| self.expr.is_none()) {
                    let days: Vec<f64> = ticks.iter().map(|t| t.day).collect();
                    let tls = ticks
                        .into_iter()
                        .enumerate()
                        .map(|(domain_idx, t)| TemporalLabel { domain_idx, row1: t.text, row2: t.row2.unwrap_or_default() })
                        .collect();
                    return self.with_edge_points(edge, tls, &days, &days, pw, fonts);
                }
                ticks
                    .into_iter()
                    .map(|t| {
                        let listed = self.is_listed(t.day);
                        self.plain_label(if listed { t.text } else { String::new() }, t.row2.filter(|_| listed), t.day, t.frac, pw, fonts)
                    })
                    .collect()
            }
            Ticks::Part { part, count, time_format } => {
                let (lo, hi) = self.scale.domain_at(pw);
                let authored = crate::families::temporal::AuthoredTimeFormat::parse("scatter", time_format.as_deref())?;
                time_part_ticks((lo, hi), pw, *part, *count, authored)?
                    .into_iter()
                    .map(|t| self.plain_label(t.text, None, t.at, t.frac, pw, fonts))
                    .collect()
            }
        }
    }

    /// The on-tick `tls` (at `days`, among ticks at `tick_days`) with the first and last data points labeled where
    /// [`with_edge_point_labels`] finds room.
    fn with_edge_points(
        &self,
        edge: &EdgeFormat,
        tls: Vec<TemporalLabel>,
        days: &[f64],
        tick_days: &[f64],
        pw: f64,
        fonts: &FontBook,
    ) -> Result<Vec<(f64, AxisLabel)>, String> {
        let Scale::Utc { lo, hi, .. } = self.scale else { unreachable!("only a UTC axis labels its edge points") };
        let (f0, f1) = (self.scale.frac(At::Day(lo), pw), self.scale.frac(At::Day(hi), pw));
        let n = tls.len();
        let tls = if hi > lo && pw > 0.0 {
            let rel = |day: &f64| (self.scale.frac(At::Day(*day), pw) - f0) / (f1 - f0);
            let authored = crate::families::temporal::AuthoredTimeFormat::parse("scatter", edge.time_format.as_deref())?;
            let mut measure = |t: &str| fonts.measure_shaped(t, &self.family, self.font_size);
            with_edge_point_labels(
                tls,
                &days.iter().map(rel).collect::<Vec<_>>(),
                &tick_days.iter().map(rel).collect::<Vec<_>>(),
                (n, n + 1),
                (lo, hi),
                edge.format_tu,
                edge.fiscal,
                authored,
                pw * (f1 - f0),
                &mut measure,
            )?
        } else {
            tls
        };
        tls.into_iter()
            .map(|tl| {
                let (day, is_edge) = match tl.domain_idx {
                    i if i < n => (days[i], false),
                    i if i == n => (lo, true),
                    _ => (hi, true),
                };
                let label = self.label(&tl, At::Day(day), edge.format_tu, fonts)?;
                Ok((self.scale.frac(At::Day(day), pw), AxisLabel { edge: is_edge, ..label }))
            })
            .collect()
    }

    /// Whether the axis draws its tick marks.
    pub(crate) fn draws_ticks(&self) -> bool {
        self.ticks_visible
    }

    /// A thinned bar axis draws a tick under each label it paints, whatever the theme says of the axis's ticks.
    pub(crate) fn tick_under_labels(&mut self) {
        if matches!(self.ticks, Ticks::Fixed(_) | Ticks::Interval { .. }) {
            self.ticks_visible = true;
            self.ticks_under_labels = true;
        }
    }

    /// The scale fractions a tick is drawn at: every label's, or only those painted when ticking under labels.
    fn painted_tick_fracs(&self, labels: &[(f64, AxisLabel)], pw: f64) -> Vec<f64> {
        let kept = self.kept(labels, pw);
        labels
            .iter()
            .zip(kept)
            .filter(|((_, l), keep)| !l.edge && (!self.ticks_under_labels || is_painted(*keep, &l.text)))
            .map(|((frac, _), _)| *frac)
            .collect()
    }

    /// The scale fraction of one row's `date` (floored to the grain) at plot
    /// width `pw`.
    pub(crate) fn frac(&self, date: &str, pw: f64) -> f64 {
        match &self.scale {
            Scale::Utc { .. } => {
                let instant = self.instant(date);
                if self.grain.is_some() && instant.nan {
                    return 0.0;
                }
                let day = if self.part.is_some() && self.folded { instant.at } else { instant.pos };
                self.scale.frac(At::Day(day), pw)
            }
            Scale::Point { domain, .. } | Scale::Band { domain, .. } => {
                let i = domain.iter().position(|d| d == date).expect("a kept date is in the point domain");
                self.scale.frac(At::Index(i), pw)
            }
        }
    }

    /// Whether a date has a place on the scale: a discrete scale holds only its domain's dates.
    pub(crate) fn knows(&self, date: &str) -> bool {
        match &self.scale {
            Scale::Point { domain, .. } | Scale::Band { domain, .. } => domain.iter().any(|d| d == date),
            Scale::Utc { .. } => true,
        }
    }

    /// A band scale's bandwidth as a fraction of the plot width; `None` on any
    /// other scale.
    pub(crate) fn bandwidth(&self) -> Option<f64> {
        match &self.scale {
            Scale::Band { geometry, .. } => Some(geometry.bandwidth()),
            _ => None,
        }
    }

    /// The leading edge in pixels of each date's band over a plot `pw` wide; a date outside the domain has no
    /// band and leads at 0, as Vega places a mark whose position is undefined.
    pub(crate) fn band_starts(&self, dates: &[String], pw: f64) -> Vec<f64> {
        let Scale::Band { domain, geometry } = &self.scale else { unreachable!("only a band axis has band starts") };
        dates.iter().map(|date| domain.iter().position(|d| d == date).map_or(0.0, |i| geometry.edge(i, 0.0) * pw)).collect()
    }

    /// The scale fractions of a band scale's first and last band centers.
    pub(crate) fn band_ends(&self, pw: f64) -> Option<(f64, f64)> {
        match &self.scale {
            Scale::Band { domain, .. } => {
                Some((self.scale.frac(At::Index(0), pw), self.scale.frac(At::Index(domain.len() - 1), pw)))
            }
            _ => None,
        }
    }

    /// Whether `date` is a row Vega's `timeunit` transform places at NaN (drawn at x 0): only on
    /// a UTC scale, where a bucket start is an instant.
    pub(crate) fn is_nan_at(&self, date: &str) -> bool {
        matches!(self.scale, Scale::Utc { .. }) && self.grain.is_some() && self.time.get(date).is_some_and(|instant| instant.nan)
    }

    /// A UTC scale's domain in days at plot width `pw`; `None` on a point scale.
    pub(crate) fn day_domain(&self, pw: f64) -> Option<(f64, f64)> {
        matches!(self.scale, Scale::Utc { .. }).then(|| self.scale.domain_at(pw))
    }

    /// The instant of a date the axis read when it was resolved.
    fn instant(&self, date: &str) -> &crate::time_axis::Instant {
        self.time.get(date).expect("a date of the scale was read at resolve")
    }

    /// The whole UTC day a date of the axis falls on.
    pub(crate) fn date_day(&self, date: &str) -> Option<f64> {
        self.time.get(date).map(|instant| instant.at.floor())
    }

    /// The fractions a `date`'s bucket starts and ends at on a bucketed scale at
    /// plot width `pw`; `None` on a scale no calendar grain buckets.
    pub(crate) fn bucket_span(&self, date: &str, pw: f64) -> Option<(f64, f64)> {
        self.grain.filter(|_| matches!(self.scale, Scale::Utc { .. }))?;
        let instant = self.instant(date);
        // A NaN position is a bucket of no width at 0.
        if instant.nan {
            return Some((0.0, 0.0));
        }
        let at = |day: f64| self.scale.frac(At::Day(day), pw);
        Some((at(instant.pos), at(instant.end.expect("a grain buckets its dates"))))
    }

    /// A facet panel's tooltip x value: Vega-Lite's own description of the date.
    pub(crate) fn native_date(&self, date: &str) -> Result<String, String> {
        crate::families::temporal::default_time_unit_text(date, self.grain)
    }

    /// A point's tooltip x value: the full bucket at the data grain
    /// (`"Jan 2024"`), the header the structured tooltip formats a date with.
    pub(crate) fn tooltip_date(&self, date: &str) -> Result<String, String> {
        if let Some(header) = self.tooltip_time {
            return header.text(self.time.day(date));
        }
        let (y, m, d) = match self.tooltip_grain {
            Some(_) => self.time.civil_of(date),
            None => crate::time_axis::parse_iso_date(date).expect("a plotted date parsed at resolve"),
        };
        tooltip_header_date(y, m, d, self.tooltip_grain)
    }

    /// How far the first surviving label reaches left of the plot origin at a plot `pw` wide: a tick the
    /// 1px flush leaves centered puts half its label there.
    pub(crate) fn label_left_reach(&self, pw: f64, fonts: &FontBook) -> Result<f64, String> {
        let mut ink = Bounds::empty_new();
        self.union_label_ink(pw, 0.0, &mut ink, fonts)?;
        Ok((-ink.x1).max(0.0))
    }

    /// How far a tilted label column reaches below the plot, which no painted `y` attribute carries; `None` on
    /// a flat axis.
    pub(crate) fn rotated_footer_reach(&self, pw: f64, fonts: &FontBook) -> Result<Option<f64>, String> {
        if self.angle == 0.0 {
            return Ok(None);
        }
        let mut ink = Bounds::empty_new();
        self.union_label_ink(pw, 0.0, &mut ink, fonts)?;
        Ok(Some(match self.edge {
            crate::common::XEdge::Bottom => ink.y2,
            crate::common::XEdge::Top => -ink.y1,
        }))
    }

    /// Plot-width fractions of every tick — the ticks and gridlines, which
    /// Vega keeps at each tick whether or not parity later hides its label.
    pub(crate) fn tick_fracs(&self, pw: f64, fonts: &FontBook) -> Result<Vec<f64>, String> {
        Ok(self.tick_marks(pw, &self.labels_at(pw, fonts)?))
    }

    /// Where the ticks and gridlines fall: on the labels, or on the period boundaries an interval axis names.
    fn tick_marks(&self, pw: f64, labels: &[(f64, AxisLabel)]) -> Vec<f64> {
        match &self.ticks {
            Ticks::Interval { boundaries, .. } => boundaries.iter().map(|&day| self.scale.frac(At::Day(day), pw)).collect(),
            _ => self.painted_tick_fracs(labels, pw),
        }
    }

    fn tick_eff(&self) -> f64 {
        if self.ticks_visible { self.tick_len } else { 0.0 }
    }

    /// The label anchor's row below the plot bottom, before the axis group's
    /// half pixel.
    fn label_top(&self) -> f64 {
        self.axis_offset + self.tick_eff() + self.label_pad
    }

    /// `pw` is the plot width `frac` is a share of: `labelFlush` is a pixel test against the range ends.
    fn align(&self, l: &AxisLabel, frac: f64, pw: f64) -> &str {
        match self.align.as_deref() {
            Some(authored) => authored,
            None if self.angle != 0.0 => l.tilted_align,
            None if self.flush_points => crate::families::line::flush_align(frac * pw, pw),
            None if matches!(self.scale, Scale::Point { .. } | Scale::Band { .. }) => "center",
            None => match flush_anchor_px(frac * pw, pw) {
                "start" => "left",
                "end" => "right",
                _ => "center",
            },
        }
    }

    /// How a lone chart's labels lie, for an expression that reads its label `item`. A rotated axis whose labels
    /// do not share one anchor is left unregistered: the expression then refuses.
    fn register_item_axis(&self, labels: &[(f64, AxisLabel)], plot_w: f64, ax_x: &AxisStyle) -> Result<(), String> {
        use crate::labels_expr::view::{anchor, register_axis, AxisSpec, Orient};
        let (align, baseline, flush) = if self.angle == 0.0 {
            match self.align.as_deref() {
                Some(authored) => (Some(anchor(authored)?), None, None),
                None if self.flush_points || matches!(self.scale, Scale::Utc { .. }) => (None, None, Some(1.0)),
                None => (None, None, None),
            }
        } else {
            let Some((_, first)) = labels.first() else { return Ok(()) };
            let same = labels.iter().all(|(frac, l)| l.baseline == first.baseline && self.align(l, *frac, plot_w) == self.align(first, labels[0].0, plot_w));
            if !same {
                return Ok(());
            }
            (Some(anchor(self.align(first, labels[0].0, plot_w))?), Some(anchor(first.baseline)?), None)
        };
        let angle = vega_fit::normalize_angle(self.angle);
        let spec = AxisSpec { orient: Orient::Bottom, offset: self.tick_eff() + self.label_pad, shift: 0.0, align, baseline, angle, flush, style: ax_x.labels.item_style()? };
        register_axis("x", spec);
        Ok(())
    }

    /// `text-anchor` for [`Self::align`]'s vocabulary.
    fn anchor(&self, l: &AxisLabel, frac: f64, pw: f64) -> &'static str {
        match self.align(l, frac, pw) {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        }
    }

    fn ink(&self, l: &AxisLabel, frac: f64, pw: f64, row_y: f64) -> Bounds {
        let x = pw * frac;
        let mut ink = vega_fit::text_ink(x, row_y, l.width.min(self.label_limit), self.font_size, self.align(l, frac, pw), l.baseline);
        ink.y2 += (l.rows() - 1) as f64 * (self.font_size + 2.0);
        if self.angle != 0.0 {
            ink = ink.rotate(self.angle, x, row_y);
        }
        ink
    }

    /// Vega's runtime `labelOverlap: parity` over the labels' real ink at plot
    /// width `pw`; every label survives when the ladder did not request it.
    fn kept(&self, labels: &[(f64, AxisLabel)], pw: f64) -> Vec<bool> {
        if !self.parity {
            return vec![true; labels.len()];
        }
        let ink: Vec<Bounds> = labels.iter().map(|(frac, l)| self.ink(l, *frac, pw, 0.0)).collect();
        vega_fit::parity_visible(&ink, self.separation)
    }

    /// Joins the surviving labels' ink into `b` for a `pw` × `ph` plot.
    pub(crate) fn union_label_ink(&self, pw: f64, ph: f64, b: &mut Bounds, fonts: &FontBook) -> Result<(), String> {
        let row_y = self.edge.at(ph, self.label_top());
        let labels = self.labels_at(pw, fonts)?;
        for ((frac, l), keep) in labels.iter().zip(self.kept(&labels, pw)) {
            if keep {
                b.union(&self.ink(l, *frac, pw, row_y));
            }
        }
        // A tick is a rule stroked its full width past its own ends (`boundStroke`): one at the plot's
        // edge reaches past it. Off the thinned bar axis a tick stays whether or not parity hid its label.
        if self.ticks_visible {
            let (near, far) = (self.edge.at(ph, self.axis_offset), self.edge.at(ph, self.axis_offset + self.tick_len));
            for frac in self.tick_marks(pw, &labels) {
                let x = pw * frac;
                b.union(&Bounds::new(x, near.min(far), x, near.max(far)).expand(self.tick_width));
            }
        }
        Ok(())
    }

    /// An unbucketed aware axis reads the board zone; a bucket start or time part prints as written.
    fn instants_aware(&self) -> bool {
        self.aware && self.grain.is_none() && self.part.is_none()
    }

    /// Vega's caption of this axis on a plot `plot_w` wide, where a padded
    /// scale's domain is settled.
    fn caption(&self, ax_x: &AxisStyle, plot_w: f64) -> Result<AxisCaption, String> {
        let scale = match &self.scale {
            Scale::Point { domain, .. } | Scale::Band { domain, .. } => CaptionScale::Discrete {
                values: domain
                    .iter()
                    .map(|v| match self.grain {
                        Some(unit) => Ok(bucket_text(v, Grain::Calendar(unit))),
                        // No grain explains the dates: Vega labels each category under d3's multi-scale format.
                        None => {
                            let (y, m, d) = self.time.civil_of(v);
                            crate::families::temporal::d3_multi_format(y, m, d)
                        }
                    })
                    .collect::<Result<_, String>>()?,
                format: vega_axis_format(ax_x, false)?,
            },
            Scale::Utc { lo, hi, pad } => {
                let (lo, hi) = padded_day_domain((*lo, *hi), plot_w, *pad);
                let default = match (self.grain, self.part) {
                    (Some(unit), _) => Some(time_unit_specifier(unit)),
                    (None, Some(part)) => Some(part.format_spec()),
                    (None, None) => None,
                };
                CaptionScale::Time {
                    utc: self.grain.is_some(),
                    aware: self.instants_aware(),
                    domain: (lo * MS_PER_DAY, hi * MS_PER_DAY),
                    format: authored_time_format(ax_x, self.grain)?.or(default.map(str::to_string)),
                }
            }
        };
        Ok(AxisCaption::new(&self.title_text, &ax_x.title, scale).bare_if_undrawn(ax_x))
    }

    /// Ticks, labels and title, positioned against a plot at `plot_left` of
    /// width `plot_w` whose bottom edge is `plot_bottom`.
    pub(crate) fn nodes(
        &self,
        ax_x: &AxisStyle,
        plot_left: f64,
        plot_w: f64,
        (plot_bottom, floor_y): (f64, f64),
        fonts: &FontBook,
    ) -> Result<Vec<Node>, String> {
        let fs = self.font_size;
        // The drawn axis group is translated by half a pixel so 1px strokes
        // land on a pixel boundary; the fit measures the ink before it.
        let sign = self.edge.sign();
        let group_y = plot_bottom + sign * self.axis_offset + 0.5;
        let labels = self.labels_at(plot_w, fonts)?;
        let caption = self.caption(ax_x, plot_w)?;
        let mut items = Vec::new();
        if self.ticks_visible {
            let xs = self.tick_marks(plot_w, &labels).into_iter().map(|frac| plot_left + (plot_w * frac).round() + 0.5);
            let end_y = group_y + sign * self.tick_len;
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, self.floor_rule.as_deref(), &[], xs, (floor_y, end_y), 3)?);
        }
        if crate::labels_expr::view::active() && self.title_key.is_some() {
            self.register_item_axis(&labels, plot_w, ax_x)?;
        }
        let kept = self.kept(&labels, plot_w);
        let anchor_y = group_y + sign * (self.tick_eff() + self.label_pad);
        let flat_y = anchor_y + measure::baseline_shift(self.edge.baseline(), fs);
        let edge_half = ax_x.edge_rule_half_width(self.ticks_visible);
        let mut reach = 0.0_f64;
        for ((frac, l), keep) in labels.iter().zip(&kept) {
            if !is_painted(*keep, &l.text) {
                continue;
            }
            let ink = self.ink(l, *frac, plot_w, 0.0);
            reach = reach.max(match self.edge {
                crate::common::XEdge::Bottom => ink.y2,
                crate::common::XEdge::Top => -ink.y1,
            });
            let gx = plot_left + plot_w * frac + 0.5;
            if self.angle != 0.0 {
                let text_anchor = self.anchor(l, *frac, plot_w);
                let attrs = Attrs::new()
                    .set("text-anchor", text_anchor)
                    .set(
                        "transform",
                        format!(
                            "translate({gx:.3},{anchor_y:.3}) rotate({}) translate(0,{})",
                            self.angle,
                            measure::baseline_shift(l.baseline, fs)
                        ),
                    )
                    .vega("font-family", self.family.as_str())
                    .set("font-size", fs.to_string())
                    .quick_some("font-style", self.style.as_deref())
                    .set("font-weight", self.weight.as_str())
                    .set("fill", self.color.as_str());
                items.push(match &l.row2 {
                    Some(row2) => Node::TextRuns {
                        attrs,
                        children: vec![
                            Node::Tspan(TextBlock {
                                attrs: Attrs::new(),
                                text: Chars::new(ax_x.labels.clip(&l.text, fonts)?, Escape::Attr),
                            }),
                            Node::Tspan(TextBlock {
                                attrs: Attrs::new().set("x", "0").set("dy", (fs + 2.0).to_string()),
                                text: Chars::new(ax_x.labels.clip(row2, fonts)?, Escape::Attr),
                            }),
                        ],
                    },
                    None => Node::text(attrs, ax_x.labels.clip(&l.text, fonts)?),
                });
                continue;
            }
            let (anchor, flush) = match (&self.align, &self.scale) {
                (Some(_), _) | (None, Scale::Point { .. } | Scale::Band { .. }) => (self.anchor(l, *frac, plot_w), 0.0),
                _ => {
                    let anchor = flush_anchor_px(*frac * plot_w, plot_w);
                    (anchor, crate::families::line::edge_nudge(anchor, edge_half))
                }
            };
            let gx = gx + flush;
            let row = |y: f64, text: &str| -> Result<Node, String> {
                Ok(Node::text(
                    Attrs::new()
                        .set("x", format!("{gx:.3}"))
                        .set("y", format!("{y:.3}"))
                        .set("text-anchor", anchor)
                        .vega("font-family", self.family.as_str())
                        .set("font-size", fs.to_string())
                        .quick_some("font-style", self.style.as_deref())
                        .set("font-weight", self.weight.as_str())
                        .set("fill", self.color.as_str()),
                    ax_x.labels.clip(text, fonts)?,
                ))
            };
            // The label block anchors on its far baseline: the second row hangs below the first
            // under a bottom axis, and the first sits above the second over a top one.
            match (&l.row2, self.edge) {
                (Some(row2), crate::common::XEdge::Bottom) => {
                    items.push(row(flat_y, &l.text)?);
                    items.push(row(flat_y + fs + 2.0, row2)?);
                }
                (Some(row2), crate::common::XEdge::Top) => {
                    items.push(row(flat_y - fs - 2.0, &l.text)?);
                    items.push(row(flat_y, row2)?);
                }
                (None, _) => items.push(row(flat_y, &l.text)?),
            }
        }
        // Vega's `axisTitleLayout`: the title clears the label ink's own
        // reach past the anchor, then `titlePadding`.
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_y = anchor_y
            + sign * (reach + ax_x.title.padding.unwrap_or(10.0))
            + measure::baseline_shift(self.edge.baseline(), title_fs);
        items.extend(crate::common::axis_title_at(
            &self.title_text,
            &ax_x.title,
            plot_left + plot_w / 2.0,
            title_y,
            self.title_key,
        ));
        crate::contract::axis_group(crate::contract::AxisChannel::X, &caption, (plot_left + 0.5, group_y), items)
    }
}

/// Vega's interval ticks of a bucketed scale: every calendar boundary of the
/// grain inside the (padded) domain, as instants in days.
fn native_ticks(scale: &Scale, tu: TimeUnit, cadence: Option<AuthoredTickCadence>, plot_w: f64) -> Vec<f64> {
    let (lo, hi) = scale.domain_at(plot_w);
    match cadence {
        Some(cadence) => cadence.bucketed_ticks(tu, (lo, hi)),
        None => interval_tick_days((lo.floor() as i64, hi.ceil() as i64), native_tick_interval(tu))
            .into_iter()
            .map(|day| day as f64)
            .filter(|&day| lo <= day && day <= hi)
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::{At, Scale};

    /// A padded UTC scale widens its domain about the center so the data ends
    /// land `pad` pixels inside the range (Vega's `padDomain`).
    #[test]
    fn a_padded_utc_scale_puts_the_data_ends_pad_pixels_inside_the_range() {
        let scale = Scale::Utc { lo: 100.0, hi: 140.0, pad: 8.0 };
        let pw = 200.0;
        assert!((scale.frac(At::Day(100.0), pw) - 8.0 / pw).abs() < 1e-9);
        assert!((scale.frac(At::Day(140.0), pw) - (pw - 8.0) / pw).abs() < 1e-9);
    }

    /// A degenerate domain (every point floors to one bucket) centers its
    /// points, as d3 does.
    #[test]
    fn a_degenerate_utc_scale_centers_every_point() {
        let scale = Scale::Utc { lo: 19723.0, hi: 19723.0, pad: 0.0 };
        assert_eq!(scale.frac(At::Day(19723.0), 400.0), 0.5);
    }

    /// A point scale at the theme's zero padding spreads its categories edge
    /// to edge, and centers a lone one.
    #[test]
    fn a_point_scale_spreads_edge_to_edge_and_centers_a_lone_category() {
        let scale = |n: usize| Scale::Point { domain: vec![String::new(); n], padding: 0.0 };
        let five = scale(5);
        assert_eq!(five.frac(At::Index(0), 100.0), 0.0);
        assert_eq!(five.frac(At::Index(4), 100.0), 1.0);
        assert_eq!(scale(1).frac(At::Index(0), 100.0), 0.5);
    }
}
