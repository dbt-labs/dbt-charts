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

use std::fmt::Write as _;

use crate::common::AxisStyle;
use crate::families::line::{bare_label_anchor, bare_label_layout, flush_align, flush_anchor, tilted_temporal_label};
use crate::families::temporal::{
    authored_tick_count, bare_temporal_ticks, date_text_day, generic_layout, none_tick_labels, part_band_texts, time_part_ticks, AuthoredTickCadence, build_all_temporal_labels, complete_buckets, days_from_civil, interval_tick_days, interval_tick_labels, native_tick_interval,
    build_temporal_labels, parse_authored_time_unit, parse_iso_date, ResolvedVegaLabels, resolve_vega_x_labels, time_unit_floor, tooltip_header_date, TemporalLabel,
    TimePart, TimeUnit,
};
use crate::families::x_type::{Grain, XResolution};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
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
}

/// The scale ticks and points are positioned on.
#[derive(Clone)]
enum Scale {
    /// `lo..hi` in days, each end `pad` pixels inside the range
    /// (`scale.padding` on a continuous scale).
    Utc { lo: f64, hi: f64, pad: f64 },
    /// A point scale over the sorted distinct buckets.
    Point { domain: Vec<String>, padding: f64 },
}

impl Scale {
    /// Vega's `padDomain`: the domain widened about its center so its ends sit
    /// `pad` pixels inside a range `pw` wide.
    fn domain_at(&self, pw: f64) -> (f64, f64) {
        match *self {
            Scale::Utc { lo, hi, pad } => vega_fit::pad_domain((lo, hi), pw, pad),
            Scale::Point { .. } => unreachable!("only a UTC scale has a day domain"),
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
                // d3's point scale: `step = 1 / max(1, n - 1 + 2·padding)`, the
                // run centered (`align: 0.5`).
                let n = domain.len() as f64;
                let step = 1.0 / (n - 1.0 + 2.0 * padding).max(1.0);
                (1.0 - step * (n - 1.0)) * 0.5 + step * i as f64
            }
            _ => unreachable!("a UTC scale positions days, a point scale categories"),
        }
    }
}

/// How the ticks are produced.
#[derive(Clone)]
enum Ticks {
    /// A fixed set: the injected openers, or the point scale's categories.
    Fixed(Vec<AxisLabel>),
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
    /// Vega's own ticks of the domain, which move with the plot width.
    Bare(Option<AuthoredTickCadence>),
    /// Vega's own ticks of a time-part scale (`timeUnit: month`, ...), under
    /// the part's own text.
    Part { part: TimePart, count: Option<usize>, time_format: Option<String> },
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
    /// The grain the structured tooltip names a date at: the data's own.
    tooltip_grain: Option<TimeUnit>,
    /// An authored `labels.align` wins over every derived anchor.
    align: Option<String>,
    angle: f64,
    parity: bool,
    /// The labels measured when skip/tilt were tried and they still collide.
    pub(crate) collision: Option<usize>,
    font_size: f64,
    family: String,
    color: String,
    weight: String,
    label_pad: f64,
    label_limit: f64,
    separation: f64,
    tick_len: f64,
    ticks_visible: bool,
    tick_color: String,
    tick_width: f64,
    /// `axis.offset`; the drawn axis group adds its own half-pixel crisp
    /// translate on top (`axis_drop`).
    axis_offset: f64,
    title_text: String,
    title_key: Option<crate::contract::LabelKey>,
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
}

/// `scale.continuous.domain` on a date axis: a pair of ISO dates, or of epoch
/// milliseconds, as days.
fn authored_day_domain(ax_x: &AxisStyle) -> Result<Option<(f64, f64)>, String> {
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
            serde_json::Value::String(s) => parse_iso_date(s)
                .map(|(y, m, d)| days_from_civil(y, m, d) as f64)
                .ok_or_else(|| format!("scatter: the authored date domain value {s:?} is not an ISO date")),
            other => Err(format!("scatter: the authored date domain value {other} is not a date")),
        }
    };
    Ok(Some((day(&pair[0])?, day(&pair[1])?)))
}

fn day_of(date: &str) -> Result<i64, String> {
    parse_iso_date(date)
        .map(|(y, m, d)| days_from_civil(y, m, d))
        .ok_or_else(|| format!("scatter: x value {date:?} is not an ISO date"))
}

impl TemporalXAxis {
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
        let count = ax_x
            .ticks
            .count
            .map(|count| authored_tick_count(count).map_err(|e| format!("scatter: {e}")))
            .transpose()?;
        let cadence = count.map(AuthoredTickCadence::Count);
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
            tooltip_grain: None,
            align: labels_cfg.align.clone(),
            angle: 0.0,
            parity: false,
            collision: None,
            font_size,
            family: family.clone(),
            color: labels_cfg.font.color.as_deref().unwrap_or("#626366").to_string(),
            weight: labels_cfg.font.weight_str(),
            label_pad: labels_cfg.padding.unwrap_or(6.0),
            label_limit: labels_cfg.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT),
            separation: labels_cfg.min_gap.unwrap_or(0.0),
            tick_len: ticks_cfg.length.unwrap_or(6.0),
            ticks_visible: ticks_cfg.visible.unwrap_or(true),
            tick_color: crate::common::required_ink(ticks_cfg.color.as_deref(), "scatter_axis", "ticks.color")?.to_string(),
            tick_width: ticks_cfg.width.unwrap_or(1.0),
            axis_offset: ticks_cfg.offset.unwrap_or(0.0),
            title_text,
            title_key,
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
        axis.tooltip_grain = match part {
            Some(_) => {
                let dates: Vec<&str> = col.ladder.iter().map(String::as_str).collect();
                crate::families::temporal::detect_time_unit(&dates)
            }
            None => grain,
        };
        let authored_domain = authored_day_domain(ax_x)?;
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
        let mut measure = |t: &str| labels_cfg.font.measure_weighted(fonts, t, font_size);
        // An authored angle skips the overlap ladder altogether
        // (`resolve_axis_x_overlap`): the labels keep the vocabulary of the
        // authored label grain (else the data's own), none thinned or tilted.
        let layout = |domain: &[String],
                      tu: TimeUnit,
                      flushed: bool,
                      continuous: bool,
                      measure: &mut dyn FnMut(&str) -> Result<f64, String>|
         -> Result<ResolvedVegaLabels, String> {
            match labels_cfg.angle.filter(|_| labels_cfg.time_unit.as_deref() != Some("none")) {
                Some(angle) => {
                    let format_tu = labels_cfg.time_unit.as_deref().and_then(parse_authored_time_unit).unwrap_or(tu);
                    Ok(ResolvedVegaLabels {
                        labels: build_temporal_labels(domain, tu, format_tu, format_tu, fiscal, authored)?,
                        angle,
                        block_height: crate::families::temporal::pinned_block_height(
                            domain, format_tu, authored, angle, font_size, measure,
                        )?,
                        parity: false,
                        collision: None,
                        format_tu,
                        visibility_tu: format_tu,
                    })
                }
                None => resolve_vega_x_labels(
                    domain,
                    tu,
                    labels_cfg.time_unit.as_deref(),
                    usable_width,
                    flushed,
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
        let floor = |date: &String| -> Result<i64, String> {
            let floored = match grain {
                Some(tu) => time_unit_floor(date, tu).ok_or_else(|| format!("scatter: x value {date:?} is not an ISO date"))?,
                None => date.clone(),
            };
            day_of(&floored)
        };
        let kept_at = col
            .kept
            .iter()
            .map(|date| match part {
                Some(part) => date_text_day(date)
                    .map(|day| part.instant(day))
                    .ok_or_else(|| format!("scatter: x value {date:?} is not an ISO date")),
                None => floor(date).map(|day| day as f64),
            })
            .collect::<Result<Vec<_>, _>>()?;
        let span = kept_at
            .iter()
            .copied()
            .reduce(f64::min)
            .zip(kept_at.iter().copied().reduce(f64::max));

        if col.law.vl_type == crate::families::temporal::VegaType::Ordinal {
            let tu = grain.expect("production routes a date column ordinal only over a calendar grain");
            let resolved = layout(col.ladder, tu, false, false, &mut measure)?;
            let domain: Vec<String> = col.kept.to_vec();
            let padding = ax_x.scale.get("padding").and_then(serde_json::Value::as_f64).unwrap_or(0.5);
            axis.angle = labels_cfg.angle.unwrap_or(resolved.angle);
            axis.parity = if pinned { overlap.is_none() } else { resolved.parity };
            axis.collision = resolved.collision;
            // Unthinned, every bucket is a tick that speaks (`values` stays the
            // full bucket list); the opener gate only applies once thinned.
            let speaking = if resolved.visibility_tu == tu {
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
            axis.scale = Scale::Point { domain, padding };
            axis.ticks = Ticks::Fixed(labels);
            return Ok(axis);
        }

        let (lo, hi) = match (authored_domain, span) {
            (Some(pair), _) => pair,
            (None, Some(pair)) => pair,
            (None, None) => return Err("scatter: a date x needs at least one kept date".into()),
        };
        axis.scale = Scale::Utc { lo, hi, pad };
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
                _ => None,
            };
            let (angle, parity) = match layout_tu {
                Some(tu) => {
                    let ladder = complete_buckets(col.ladder, tu, fiscal);
                    let resolved = layout(&ladder, tu, flushed, true, &mut measure)?;
                    axis.collision = resolved.collision;
                    bare_label_layout(Some(&resolved), skip)
                }
                None => bare_label_layout(None, skip),
            };
            axis.angle = labels_cfg.angle.unwrap_or(angle);
            axis.parity = if pinned { overlap.is_none() } else { parity };
            axis.ticks = Ticks::Bare(cadence);
            return Ok(axis);
        };

        let ladder = complete_buckets(col.ladder, tu, fiscal);
        // `labels.time_unit: none` measures the rows' own dates, not the
        // enumerated span (`_temporal_layout`'s generic branch).
        let resolved = if labels_cfg.time_unit.as_deref() == Some("none") {
            layout(col.ladder, tu, flushed, true, &mut measure)?
        } else {
            layout(&ladder, tu, flushed, true, &mut measure)?
        };
        axis.angle = labels_cfg.angle.unwrap_or(resolved.angle);
        axis.parity = if pinned { overlap.is_none() } else { resolved.parity };
        axis.collision = resolved.collision;
        // With the label grain at the data grain Vega ticks every calendar
        // boundary of the scale (`tickCount: {interval}`, `type_inference.py:
        // 1049-1052`), an empty bucket included; a thinner ladder injects its
        // openers as `values` instead.
        let native = resolved.visibility_tu == tu && resolved.format_tu == tu;
        if native {
            axis.ticks = Ticks::Native {
                ladder,
                tu,
                format_tu: resolved.format_tu,
                visibility_tu: resolved.visibility_tu,
                fiscal,
                time_format: crate::families::temporal::axis_time_format(labels_cfg.spec(), &ax_x.grain_formats, Some(tu))
                    .map(str::to_string),
                vl_default_labels: labels_cfg.time_unit.as_deref() == Some("none"),
                cadence,
            };
            return Ok(axis);
        }
        // An opener's own raw date, off its floored bucket start only under
        // the week grain (a Sunday bucket opened by a Monday).
        let raw = tu == TimeUnit::YearWeek;
        let mut labels = Vec::new();
        for tl in &resolved.labels {
            let date = &ladder[tl.domain_idx];
            let day = if raw { day_of(date)? } else { floor(date)? } as f64;
            labels.push(axis.label(tl, At::Day(day), resolved.format_tu, fonts)?);
        }
        axis.ticks = Ticks::Fixed(labels);
        Ok(axis)
    }

    fn label(&self, tl: &TemporalLabel, at: At, format_tu: TimeUnit, fonts: &FontBook) -> Result<AxisLabel, String> {
        let (text, rows, tilted_align, baseline) = tilted_temporal_label(tl, self.angle, format_tu);
        let mut width = fonts.measure(&text, &self.family, self.font_size)?;
        if rows == 2 {
            width = width.max(fonts.measure(&tl.row2, &self.family, self.font_size)?);
        }
        Ok(AxisLabel { at, text, row2: (rows == 2).then(|| tl.row2.clone()), width, tilted_align, baseline })
    }

    fn plain_label(&self, text: String, day: f64, frac: f64, fonts: &FontBook) -> Result<(f64, AxisLabel), String> {
        let (tilted_align, baseline) = bare_label_anchor(self.angle, frac);
        let width = fonts.measure(&text, &self.family, self.font_size)?;
        Ok((frac, AxisLabel { at: At::Day(day), text, row2: None, width, tilted_align, baseline }))
    }

    /// The tick labels at a plot `pw` wide, each with its scale fraction.
    /// `labelFlush` tests the pixel position against the range ends, so on a
    /// collapsed plot, where every tick sits at both, the right end wins.
    fn labels_at(&self, pw: f64, fonts: &FontBook) -> Result<Vec<(f64, AxisLabel)>, String> {
        let mut labels = self.labels_on_scale(pw, fonts)?;
        if pw == 0.0 {
            for (frac, _) in &mut labels {
                *frac = 1.0;
            }
        }
        Ok(labels)
    }

    fn labels_on_scale(&self, pw: f64, fonts: &FontBook) -> Result<Vec<(f64, AxisLabel)>, String> {
        let in_range = |frac: f64| (0.0..=1.0).contains(&frac);
        match &self.ticks {
            Ticks::Fixed(labels) => Ok(labels
                .iter()
                .map(|l| (self.scale.frac(l.at, pw), l.clone()))
                .filter(|&(frac, _)| matches!(self.scale, Scale::Point { .. }) || in_range(frac))
                .collect()),
            Ticks::Native { ladder, tu, format_tu, visibility_tu, fiscal, time_format, vl_default_labels, cadence } => {
                let days = native_tick_days(&self.scale, *tu, *cadence, pw)?;
                let authored =
                    crate::families::temporal::AuthoredTimeFormat::parse("scatter", time_format.as_deref())?;
                let ticks = if *vl_default_labels {
                    none_tick_labels(&days, *tu, authored)?
                } else {
                    interval_tick_labels(&days, ladder, *tu, *format_tu, *visibility_tu, *fiscal, authored)?
                };
                ticks
                    .into_iter()
                    .map(|tl| {
                        let day = days[tl.domain_idx] as f64;
                        Ok((self.scale.frac(At::Day(day), pw), self.label(&tl, At::Day(day), *format_tu, fonts)?))
                    })
                    .collect()
            }
            Ticks::Bare(cadence) => {
                let (lo, hi) = self.scale.domain_at(pw);
                bare_temporal_ticks((lo, hi), pw, *cadence, None)?
                    .into_iter()
                    .map(|t| self.plain_label(t.text, t.day as f64, t.frac, fonts))
                    .collect()
            }
            Ticks::Part { part, count, time_format } => {
                let (lo, hi) = self.scale.domain_at(pw);
                let authored = crate::families::temporal::AuthoredTimeFormat::parse("scatter", time_format.as_deref())?;
                time_part_ticks((lo, hi), pw, *part, *count, authored)?
                    .into_iter()
                    .map(|t| self.plain_label(t.text, t.at, t.frac, fonts))
                    .collect()
            }
        }
    }

    /// The scale fraction of one row's `date` (floored to the grain) at plot
    /// width `pw`.
    pub(crate) fn frac(&self, date: &str, pw: f64) -> f64 {
        match &self.scale {
            Scale::Utc { .. } => {
                let day = match self.part {
                    Some(part) => part.instant(date_text_day(date).expect("a kept date parsed at resolve")),
                    None => {
                        let floored = match self.grain {
                            Some(tu) => time_unit_floor(date, tu).expect("a kept date parsed at resolve"),
                            None => date.to_string(),
                        };
                        day_of(&floored).expect("a kept date parsed at resolve") as f64
                    }
                };
                self.scale.frac(At::Day(day), pw)
            }
            Scale::Point { domain, .. } => {
                let i = domain.iter().position(|d| d == date).expect("a kept date is in the point domain");
                self.scale.frac(At::Index(i), pw)
            }
        }
    }

    /// A point's tooltip x value: the full bucket at the data grain
    /// (`"Jan 2024"`), the header the structured tooltip formats a date with.
    pub(crate) fn tooltip_date(&self, date: &str) -> Result<String, String> {
        let (y, m, d) = parse_iso_date(date).expect("a plotted date parsed at resolve");
        tooltip_header_date(y, m, d, self.tooltip_grain)
    }

    /// Plot-width fractions of every tick — the ticks and gridlines, which
    /// Vega keeps at each tick whether or not parity later hides its label.
    pub(crate) fn tick_fracs(&self, pw: f64, fonts: &FontBook) -> Result<Vec<f64>, String> {
        Ok(self.labels_at(pw, fonts)?.into_iter().map(|(frac, _)| frac).collect())
    }

    fn tick_eff(&self) -> f64 {
        if self.ticks_visible { self.tick_len } else { 0.0 }
    }

    /// The label anchor's row below the plot bottom, before the axis group's
    /// half pixel.
    fn label_top(&self) -> f64 {
        self.axis_offset + self.tick_eff() + self.label_pad
    }

    fn align(&self, l: &AxisLabel, frac: f64) -> &str {
        match self.align.as_deref() {
            Some(authored) => authored,
            None if self.angle != 0.0 => l.tilted_align,
            None if matches!(self.scale, Scale::Point { .. }) => "center",
            None => flush_align(frac),
        }
    }

    /// `text-anchor` for [`Self::align`]'s vocabulary.
    fn anchor(&self, l: &AxisLabel, frac: f64) -> &'static str {
        match self.align(l, frac) {
            "left" => "start",
            "right" => "end",
            _ => "middle",
        }
    }

    fn ink(&self, l: &AxisLabel, frac: f64, pw: f64, row_y: f64) -> Bounds {
        let x = pw * frac;
        let mut ink = vega_fit::text_ink(x, row_y, l.width.min(self.label_limit), self.font_size, self.align(l, frac), l.baseline);
        if l.row2.is_some() {
            ink.y2 += self.font_size + 2.0;
        }
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
        let row_y = ph + self.label_top();
        let labels = self.labels_at(pw, fonts)?;
        for ((frac, l), keep) in labels.iter().zip(self.kept(&labels, pw)) {
            if keep {
                b.union(&self.ink(l, *frac, pw, row_y));
            }
        }
        Ok(())
    }

    /// Ticks, labels and title, positioned against a plot at `plot_left` of
    /// width `plot_w` whose bottom edge is `plot_bottom`.
    pub(crate) fn paint(
        &self,
        svg: &mut String,
        ax_x: &AxisStyle,
        plot_left: f64,
        plot_w: f64,
        plot_bottom: f64,
        fonts: &FontBook,
    ) -> Result<(), String> {
        let fs = self.font_size;
        // The drawn axis group is translated by half a pixel so 1px strokes
        // land on a pixel boundary; the fit measures the ink before it.
        let axis_drop = self.axis_offset + 0.5;
        let labels = self.labels_at(plot_w, fonts)?;
        crate::contract::axis_group_open(svg, crate::contract::AxisChannel::X, (plot_left + 0.5, plot_bottom + axis_drop))?;
        if self.ticks_visible {
            for (frac, _) in &labels {
                let gx = plot_left + (plot_w * frac).round() + 0.5;
                let _ = write!(
                    svg,
                    "<line x1=\"{gx:.3}\" y1=\"{:.3}\" x2=\"{gx:.3}\" y2=\"{:.3}\" \
                     stroke=\"{}\" stroke-width=\"{}\"/>",
                    plot_bottom + axis_drop,
                    plot_bottom + axis_drop + self.tick_len,
                    xml_escape_attr(&self.tick_color),
                    self.tick_width
                );
            }
        }
        let kept = self.kept(&labels, plot_w);
        let anchor_y = plot_bottom + axis_drop + self.tick_eff() + self.label_pad;
        let flat_y = anchor_y + vega_fit::baseline_shift("top", fs);
        let mut reach = 0.0_f64;
        for ((frac, l), keep) in labels.iter().zip(&kept) {
            if !keep {
                continue;
            }
            reach = reach.max(self.ink(l, *frac, plot_w, 0.0).y2);
            let gx = plot_left + plot_w * frac + 0.5;
            if self.angle != 0.0 {
                let text_anchor = self.anchor(l, *frac);
                let rows = match &l.row2 {
                    Some(row2) => format!(
                        "<tspan>{}</tspan><tspan x=\"0\" dy=\"{}\">{}</tspan>",
                        xml_escape(&l.text),
                        fs + 2.0,
                        xml_escape(row2)
                    ),
                    None => xml_escape(&l.text),
                };
                let _ = write!(
                    svg,
                    "<text text-anchor=\"{text_anchor}\" transform=\"translate({gx:.3},{anchor_y:.3}) \
                     rotate({}) translate(0,{})\" font-family=\"{}\" font-size=\"{fs}\" \
                     font-weight=\"{}\" fill=\"{}\">{rows}</text>",
                    self.angle,
                    vega_fit::baseline_shift(l.baseline, fs),
                    vega_escape_attr(&self.family),
                    xml_escape_attr(&self.weight),
                    xml_escape_attr(&self.color),
                );
                continue;
            }
            let anchor = match (&self.align, &self.scale) {
                (Some(_), _) | (None, Scale::Point { .. }) => self.anchor(l, *frac),
                _ => flush_anchor(*frac),
            };
            let mut row = |y: f64, text: &str| {
                let _ = write!(
                    svg,
                    "<text x=\"{gx:.3}\" y=\"{y:.3}\" text-anchor=\"{anchor}\" font-family=\"{}\" \
                     font-size=\"{fs}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    vega_escape_attr(&self.family),
                    xml_escape_attr(&self.weight),
                    xml_escape_attr(&self.color),
                    xml_escape(text)
                );
            };
            row(flat_y, &l.text);
            if let Some(row2) = &l.row2 {
                row(flat_y + fs + 2.0, row2);
            }
        }
        // Vega's `axisTitleLayout`: the title clears the label ink's own
        // reach past the anchor, then `titlePadding`.
        let title_fs = ax_x.title.font.size_or(11.0);
        let title_y = anchor_y + reach + ax_x.title.padding.unwrap_or(10.0) + vega_fit::baseline_shift("top", title_fs);
        crate::common::emit_axis_title_at(svg, &self.title_text, &ax_x.title, plot_left + plot_w / 2.0, title_y, self.title_key);
        crate::contract::axis_group_close(svg);
        Ok(())
    }
}

/// Vega's interval ticks of a bucketed scale: every calendar boundary of the
/// grain inside the (padded) domain.
fn native_tick_days(scale: &Scale, tu: TimeUnit, cadence: Option<AuthoredTickCadence>, plot_w: f64) -> Result<Vec<i64>, String> {
    let (lo, hi) = scale.domain_at(plot_w);
    let interval = match cadence {
        Some(cadence) => cadence.bucketed_interval(tu, (lo, hi))?,
        None => native_tick_interval(tu),
    };
    Ok(interval_tick_days((lo.floor() as i64, hi.ceil() as i64), interval)
        .into_iter()
        .filter(|&day| lo <= day as f64 && day as f64 <= hi)
        .collect())
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
        assert_eq!(scale.frac(At::Day(100.0), pw), 8.0 / pw);
        assert!((scale.frac(At::Day(140.0), pw) - (pw - 8.0) / pw).abs() < 1e-12);
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
