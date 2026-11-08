//! A grained axis whose labels stand on its ticks: the native cadence (a tick at every bucket of the
//! data's grain), the openers of a coarser or thinned one (a tick where a label is), the first and
//! last data points named where there is room, and a tilt or Vega's parity pass over the lot.
//!
//! Production decides this at `line/layout.rs:2657-2680` (the interval axis returned none), takes the
//! ticks from `native_candidate` `:2565` or the opener rows `:3595`, the labels from `grain_labels`
//! `:2813` and the end points from `with_edge_point_labels` (`temporal.rs:2659`), and thins by
//! `labelOverlap: parity` over the labels' ink `:3549`.

use bind::temporal::TimeUnit;
use d3rs::calendar::{civil_from_days, days_from_civil, MS_PER_DAY};
use diagnostic::{diag, Diagnostic};
use measure::baseline_shift;
use measure::shaped::Font;
use scene::{Bounds, Transform};

use super::{gutter_of, parity_visible, Align, Baseline, Grained, PlacedLabel, Placement};
use crate::geom::Size;
use crate::plot::time_axis::TimeAxis;
use crate::plot::time_cadence::{
    decision_width, label_rows, labeled, opens_label, tick_opens_label, Date, Rows,
};
use crate::time_ladder::{interval_instants, native_tick_interval};

/// The share of the tick spacing an end-point label leaves empty beside its neighbor
/// (`EDGE_LABEL_MIN_GAP_RATIO`, `constants/axis.rs:10`).
const EDGE_LABEL_MIN_GAP_RATIO: f64 = 0.45;

/// Whether an end-point label `w` wide keeps its place: empty space of at least
/// [`EDGE_LABEL_MIN_GAP_RATIO`] of the tick spacing `pitch` (a fraction of the plot `plot_w`) beside the
/// label it neighbors, `beside` being that label's distance from the end (a fraction) and width. A label
/// with no width, or no neighbor, always fits.
pub(super) fn edge_clear(plot_w: f64, w: f64, beside: Option<(f64, f64)>, pitch: f64) -> bool {
    match beside {
        Some((at, beside_w)) if w > 0.0 => {
            at * plot_w - beside_w / 2.0 - w >= EDGE_LABEL_MIN_GAP_RATIO * pitch * plot_w
        }
        _ => true,
    }
}

/// A label before it is measured: the day it stands on and what it says.
struct Slot {
    day: f64,
    rows: Rows,
}

impl TimeAxis<'_> {
    /// The ticks and labels of a grained axis `plot` across whose labels stay on their ticks.
    pub(super) fn place_on_ticks(
        &self,
        grained: &Grained,
        plot: Size,
    ) -> Result<Placement, Diagnostic> {
        let (ticks, slots) = self.tick_slots(grained)?;
        // An authored cadence pins its labels: no end point is named (`labels_unpinned`).
        let slots = if self.cadence.is_some() {
            slots
        } else {
            self.with_end_points(grained, slots, &ticks, plot.width)?
        };
        let labels = self.on_tick_labels(grained, &slots, plot)?;
        let gutter = gutter_of(&labels, plot.width);
        Ok(Placement {
            ticks,
            labels,
            gutter,
        })
    }

    /// The ticks and the labels on them, before the end points. The native cadence ticks every
    /// bucket of the data's grain, a week's tick being its Sunday and its label the Monday; any
    /// other cadence ticks its openers, a week's raw date when the labels name a coarser period.
    /// An opener the data's span does not hold has no tick (`drop_off_domain_openers`).
    fn tick_slots(&self, grained: &Grained) -> Result<(Vec<f64>, Vec<Slot>), Diagnostic> {
        let Grained {
            data,
            fiscal_start,
            span,
            cadence,
        } = grained;
        let (lo, hi) = self.extent;
        let quarters = self.labels.quarters;
        if let Some(cadence) = self.cadence {
            let ticks = cadence
                .bucketed_ticks(*data, self.extent)
                .map_err(Diagnostic::refused)?;
            let slots = self.authored_slots(grained, &ticks)?;
            return Ok((ticks, slots));
        }
        // One bucket has no extent to tick or measure against: production (`label_extent` is
        // `None`) labels the openers it holds and d3 centers them, so no native tick, no clip.
        let one = hi <= lo;
        if !one && cadence.visibility == *data && cadence.year_step == 1 && cadence.format == *data
        {
            let ticks = interval_instants(native_tick_interval(*data), lo, hi, None)
                .map_err(Diagnostic::refused)?;
            let monday = i64::from(*data == TimeUnit::YearWeek);
            let slots = ticks
                .iter()
                .enumerate()
                .map(|(k, &day)| {
                    let date = civil_from_days(day as i64 + monday);
                    let rows = label_rows(date, cadence.format, k == 0, *fiscal_start, quarters)?;
                    Ok(Slot { day, rows })
                })
                .collect::<Result<Vec<_>, Diagnostic>>()?;
            return Ok((ticks, slots));
        }
        let mut slots = Vec::new();
        for (at, rows) in labeled(
            span,
            *data,
            cadence,
            *fiscal_start,
            &self.labels.crowding,
            quarters,
        )? {
            let (y, m, d) = span[at];
            // The span's date is its bucket's start, except a week's, which keeps the weekday the
            // rows are on: production floors it only for a week-visibility cadence, which is always
            // the native one above (`label_raw_openers`, `line/layout.rs:2804`).
            let day = days_from_civil(y, m, d) as f64;
            if one || (lo..=hi).contains(&day) {
                slots.push(Slot { day, rows });
            }
        }
        Ok((slots.iter().map(|s| s.day).collect(), slots))
    }

    /// The labels on an authored cadence's ticks (`interval_tick_labels`): a tick speaks because its own
    /// date opens a label period, never because a row sits on it. The anchor, which carries the year
    /// context, is the tick on the first opener of the data's span when the labels name a coarser
    /// period than the data (the first tick otherwise). A week labeled in the week vocabulary reads
    /// each Sunday tick as its Monday. Only the years divisible by the cadence's year step speak.
    fn authored_slots(&self, grained: &Grained, ticks: &[f64]) -> Result<Vec<Slot>, Diagnostic> {
        let Grained {
            data,
            fiscal_start,
            span,
            cadence,
        } = grained;
        let monday = i64::from(*data == TimeUnit::YearWeek && cadence.format == TimeUnit::YearWeek);
        // `anchor_formats`: the data grain's own `utcFormat` pattern, a week comparing as `%Y-%U`.
        let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
        let spec = match data {
            TimeUnit::Year => "%Y",
            TimeUnit::YearQuarter | TimeUnit::YearMonth => "%Y-%m",
            TimeUnit::YearWeek => "%Y-%U",
            TimeUnit::YearMonthDate => "%Y-%m-%d",
        };
        let key = |(y, m, d): Date| {
            let ms = days_from_civil(y, m, d) as f64 * MS_PER_DAY;
            d3rs::time_format::format(spec, ms, 0.0, &locale).map_err(diag::err_internal)
        };
        let anchor = if cadence.visibility == *data {
            None
        } else {
            let first = span
                .iter()
                .find(|&&date| opens_label(date, *data, cadence.visibility, *fiscal_start))
                .unwrap_or(&span[0]);
            Some(key(*first)?)
        };
        let mut slots = Vec::new();
        for (k, &day) in ticks.iter().enumerate() {
            let date = civil_from_days(day.floor() as i64 + monday);
            if date.0 % cadence.year_step != 0 {
                continue;
            }
            let anchored = match &anchor {
                Some(anchor) => key(date)? == *anchor,
                None => k == 0,
            };
            if anchored || tick_opens_label(date, *data, cadence.visibility, *fiscal_start) {
                let rows = label_rows(
                    date,
                    cadence.format,
                    anchored,
                    *fiscal_start,
                    self.labels.quarters,
                )?;
                slots.push(Slot { day, rows });
            }
        }
        Ok(slots)
    }

    /// The width a text advances, uncut, as painted: ink and positions.
    fn advance(&self, text: &str) -> Result<f64, Diagnostic> {
        Ok(self
            .book
            .run(text, &self.labels.font)
            .map_err(diag::err_internal)?
            .advance)
    }

    /// The width Vega's parity pass judges a label's text by: production sums the raw advances of
    /// the face's upright default instance in the family as authored (`FontBook::measure`,
    /// `line/layout.rs:3510`), whatever the painted weight and style.
    fn parity_advance(&self, text: &str) -> Result<f64, Diagnostic> {
        let upright = Font {
            weight: 400.0,
            italic: false,
            ..self.labels.font
        };
        self.book
            .raw_advance(text, &upright)
            .map_err(diag::err_internal)
    }

    /// An end-point label's row as production's clearance reads it (`measure_weighted`).
    fn end_point_width(&self, text: &str) -> Result<f64, Diagnostic> {
        decision_width(self.book, text, &self.labels.font)
    }

    /// `with_edge_point_labels`: the data's first and last points are named at the plot's edges when
    /// each has empty space beside the label it neighbors of at least [`EDGE_LABEL_MIN_GAP_RATIO`]
    /// of the tick spacing (row one against the nearest label, the context row against the
    /// nearest label that has one). The first takes the context row the first tick's label would
    /// have carried; a month or coarser that repeats its outermost tick's name is not repeated.
    fn with_end_points(
        &self,
        grained: &Grained,
        mut slots: Vec<Slot>,
        ticks: &[f64],
        width: f64,
    ) -> Result<Vec<Slot>, Diagnostic> {
        let (lo, hi) = self.extent;
        if hi <= lo || ticks.len() < 2 || slots.is_empty() {
            return Ok(slots);
        }
        let Grained {
            fiscal_start,
            cadence,
            ..
        } = grained;
        let point = |day: f64, first: bool| {
            label_rows(
                civil_from_days(day.floor() as i64),
                cadence.format,
                first,
                *fiscal_start,
                self.labels.quarters,
            )
        };
        let (first, last) = (point(lo, true)?, point(hi, false)?);
        let first_context = std::mem::take(&mut slots[0].rows.1);
        let handed_over = first_context == first.1;
        if !handed_over {
            slots[0].rows.1.clone_from(&first_context);
        }
        let widths = |rows: &Rows| -> Result<(f64, f64), Diagnostic> {
            let context = if rows.1.is_empty() {
                0.0
            } else {
                self.end_point_width(&rows.1)?
            };
            Ok((self.end_point_width(&rows.0)?, context))
        };
        let frac = |day: f64| (day - lo) / (hi - lo);
        let tick_fracs: Vec<f64> = ticks.iter().map(|&day| frac(day)).collect();
        let slot_widths = slots
            .iter()
            .map(|s| widths(&s.rows))
            .collect::<Result<Vec<_>, _>>()?;
        let (first_w, last_w) = (widths(&first)?, widths(&last)?);
        let end = slots.len() - 1;
        let clear =
            |w: f64, beside: Option<(f64, f64)>, pitch: f64| edge_clear(width, w, beside, pitch);
        let n = tick_fracs.len();
        let (head_pitch, tail_pitch) = (
            tick_fracs[1] - tick_fracs[0],
            tick_fracs[n - 1] - tick_fracs[n - 2],
        );
        let row_one = |k: usize| (frac(slots[k].day), slot_widths[k].0);
        let context_of = |k: usize| (frac(slots[k].day), slot_widths[k].1);
        let has_context = |&k: &usize| !slots[k].rows.1.is_empty();
        let first_fits = clear(first_w.0, Some(row_one(0)), head_pitch);
        let last_fits = clear(
            last_w.0,
            Some((1.0 - row_one(end).0, row_one(end).1)),
            tail_pitch,
        );
        let first_context_fits = clear(
            first_w.1,
            (0..slots.len()).find(has_context).map(context_of),
            head_pitch,
        );
        let last_context_fits = clear(
            last_w.1,
            (0..slots.len())
                .rev()
                .find(has_context)
                .map(context_of)
                .map(|(at, w)| (1.0 - at, w)),
            tail_pitch,
        );
        let names_period = super::rank(cadence.format) >= super::rank(TimeUnit::YearMonth);
        let first_repeats = names_period && first.0 == slots[0].rows.0;
        let last_repeats = names_period && last.0 == slots[end].rows.0;
        if first_fits && first_context_fits && !first_repeats {
            slots.insert(
                0,
                Slot {
                    day: lo,
                    rows: first,
                },
            );
        } else if handed_over {
            slots[0].rows.1 = first_context;
        }
        if last_fits && last_context_fits && !last_repeats {
            slots.push(Slot {
                day: hi,
                rows: last,
            });
        }
        Ok(slots)
    }

    /// Places each label: flat it anchors on its tick and flushes to an edge within a pixel; turned
    /// by the cadence's tilt it takes Vega-Lite's anchor for that angle, a steep one (90 degrees or
    /// more) printing its context inline. Vega's parity pass runs over the boxes production
    /// measures (`text_ink`), the drawn ink being the face's own lines (quirk class Q2d).
    fn on_tick_labels(
        &self,
        grained: &Grained,
        slots: &[Slot],
        plot: Size,
    ) -> Result<Vec<PlacedLabel>, Diagnostic> {
        let width = plot.width;
        let (lo, hi) = self.extent;
        let cadence = &grained.cadence;
        let angle = cadence.angle;
        let steep = angle.abs() >= 90.0;
        let super::TimeLabels {
            font,
            limit,
            offset,
            min_gap,
            ..
        } = self.labels;
        let line = self.ascent + self.descent;
        let mut placed = Vec::new();
        let mut vega = Vec::new();
        for slot in slots {
            let (row1, row2) = &slot.rows;
            let inline = || match cadence.format {
                TimeUnit::YearWeek | TimeUnit::YearMonthDate => format!("{row1} {row2}"),
                _ => format!("{row2} {row1}"),
            };
            let (rows, row1) = match (steep, row2.is_empty()) {
                (true, false) => (1, inline()),
                (_, empty) => (if empty { 1 } else { 2 }, row1.clone()),
            };
            let widest = |measure: &dyn Fn(&str) -> Result<f64, Diagnostic>| {
                let context = if row2.is_empty() { 0.0 } else { measure(row2)? };
                Ok::<_, Diagnostic>(measure(&row1)?.max(context).min(limit))
            };
            let ink_width = widest(&|t| self.advance(t))?;
            let verdict_width = widest(&|t| self.parity_advance(t))?;
            let frac = if hi <= lo {
                0.5
            } else {
                (slot.day - lo) / (hi - lo)
            };
            let x = self.x_at(frac, width);
            let (align, baseline) = if angle == 0.0 {
                (self.range_align(frac, width), Baseline::Top)
            } else {
                tilted_anchor(angle)
            };
            let cut = |text: &str| -> Result<String, Diagnostic> {
                Ok(self
                    .book
                    .truncate(text, &font, limit)
                    .map_err(diag::err_internal)?
                    .0)
            };
            let text = if rows == 2 {
                format!("{}\n{}", cut(&row1)?, cut(row2)?)
            } else {
                cut(&row1)?
            };
            let turn = |b: Bounds| {
                if angle == 0.0 {
                    b
                } else {
                    let about = Some((x, offset));
                    b.transformed(&Transform::Rotate { deg: angle, about }.affine())
                }
            };
            let across = |w: f64| match align {
                Align::Left => (x, x + w),
                Align::Center => (x - w / 2.0, x + w / 2.0),
                Align::Right => (x - w, x),
            };
            let (x1, x2) = across(ink_width);
            let (v1, v2) = across(verdict_width);
            let rise = baseline_shift(
                match baseline {
                    Baseline::Top => "top",
                    Baseline::Middle => "middle",
                    Baseline::Bottom => "bottom",
                },
                font.size,
            ) - (0.8 * font.size).round();
            // Production's box sits in the plot's frame, the row `offset` below its bottom edge, and
            // turns about that anchor with `vega_fit::Bounds::rotate`'s arithmetic; a gap that
            // ties the separation is decided by its rounding.
            let row = plot.height + offset;
            vega.push(vega_turn(
                Bounds {
                    x1: v1,
                    y1: row + rise,
                    x2: v2,
                    y2: row + rise + font.size + (rows - 1) as f64 * (font.size + 2.0),
                },
                angle,
                (x, row),
            ));
            let top = offset
                - line
                    * match baseline {
                        Baseline::Top => 0.0,
                        Baseline::Middle => 0.5,
                        Baseline::Bottom => 1.0,
                    };
            placed.push(PlacedLabel {
                text,
                day: slot.day,
                x,
                y: offset,
                align,
                baseline,
                angle,
                visible: true,
                ink: turn(Bounds {
                    x1,
                    y1: top,
                    x2,
                    y2: top + rows as f64 * line,
                }),
            });
        }
        if cadence.parity {
            for (label, keep) in placed.iter_mut().zip(parity_visible(&vega, min_gap)) {
                label.visible = keep;
            }
        }
        Ok(placed)
    }
}

/// Vega-Lite's `labelAlign` and `labelBaseline` for a label of a bottom axis turned `angle`
/// degrees (`vl_x_label_anchor`, `vega_fit.rs:122`).
fn tilted_anchor(angle: f64) -> (Align, Baseline) {
    let angle = angle.rem_euclid(360.0);
    let align = if angle % 180.0 == 0.0 {
        Align::Center
    } else if 0.0 < angle && angle < 180.0 {
        Align::Left
    } else {
        Align::Right
    };
    let baseline = if (45.0 < angle && angle < 135.0) || (225.0 < angle && angle < 315.0) {
        Baseline::Middle
    } else if angle <= 45.0 || 315.0 <= angle {
        Baseline::Top
    } else {
        Baseline::Bottom
    };
    (align, baseline)
}

/// `vega_fit::Bounds::rotate` (`vega_fit.rs:80`): `b` turned `degrees` about `(x, y)`, flat when 0.
fn vega_turn(b: Bounds, degrees: f64, (x, y): (f64, f64)) -> Bounds {
    if degrees == 0.0 {
        return b;
    }
    let (sin, cos) = degrees.to_radians().sin_cos();
    let (dx, dy) = (x - x * cos + y * sin, y - x * sin - y * cos);
    let corners = [(b.x1, b.y1), (b.x1, b.y2), (b.x2, b.y1), (b.x2, b.y2)];
    Bounds::of_points(corners.map(|(px, py)| (cos * px - sin * py + dx, sin * px + cos * py + dy)))
        .expect("four corners")
}
