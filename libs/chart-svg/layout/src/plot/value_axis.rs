//! A cartesian plot's value axis: which rungs it draws at a plot size, their
//! text, where the labels sit, and the gutter their ink takes past the plot.
//! One law serves both orientations: a left or right axis rungs over the plot's
//! height, a bottom or top one over its width.
//!
//! Bind owns the domain's final bounds; the rungs of a `BySize` ladder are
//! chosen here, because they follow the axis's pixel length.

use bind::formats::locale::NumberLocale;
use bind::formats::ResolvedFormat;
use bind::bound::{Extent, Ladder, ValueDomain};
use d3rs::ticks::nice_tick_values;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::Bounds;

use crate::text_ink;
use crate::geom::{Insets, Size};
use crate::plot::label::{
    flush_align, parity_advance, parity_visible, vega_baseline, vega_line, vega_space, Align,
    Baseline,
};

/// Which edge of the plot the axis labels hang from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Left,
    /// A right axis whose labels end at the widest label's far edge plus
    /// [`RIGHT_GUTTER_PAD`]: production's plain `position: right`.
    Right,
    /// A right axis whose labels start `padding` past the tick: a base axis
    /// re-pinned onto the right, or one with an authored `labels.expr`.
    RightAway,
    /// A horizontal axis whose labels hang below the plot.
    Bottom,
    /// A horizontal axis whose labels stand above the plot.
    Top,
}

/// The room a right-aligned right axis leaves past its widest label
/// (`VALUE_AXIS_BREATHING_ROOM`).
const RIGHT_GUTTER_PAD: f64 = 4.0;

/// What an axis with no rung drawn reserves for its side gutter.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EmptyAxis {
    /// Its tick and padding.
    Reserve,
    /// Nothing.
    Zero,
}

/// The width a label counts for in the side gutter. Its ink and the parity rule
/// always read the width capped at the limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LabelWidth {
    Clamped,
    Unclamped,
}

/// What a right axis with away-aligned labels (anchored `padding` past the tick)
/// reserves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AwayReach {
    /// Tick, padding and the widest label.
    Padded,
    /// Tick and the cover-ladder estimate (see [`ValueAxis::place`]).
    Estimated,
}

/// What an away right axis over a baked ladder reserves: it measures its drawn rungs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BakedAway {
    /// Tick, padding and the widest label.
    Padded,
    /// Tick, the widest label and the breathing room.
    Plain,
}

/// The gutter law a family and fit path state; production's paths disagree. A
/// right axis always counts its widest label clamped at the limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GutterRule {
    pub empty_axis: EmptyAxis,
    /// The left axis's gutter width.
    pub left_width: LabelWidth,
    pub away_by_size: AwayReach,
    pub away_baked: BakedAway,
}

impl GutterRule {
    /// The line family's fit: `line/layout.rs:3296-3300` reserves nothing for an
    /// empty axis, `:3015` and `:3300` clamp, `:3291` swaps in `away_gutter`
    /// (padding plus the widest label) for every away axis.
    pub const LINE_FIT: GutterRule = GutterRule {
        empty_axis: EmptyAxis::Zero,
        left_width: LabelWidth::Clamped,
        away_by_size: AwayReach::Padded,
        away_baked: BakedAway::Padded,
    };
    /// The line family's rail concat: the left axis is unclamped (`:4275`, an empty
    /// axis reserves its tick and padding); a right axis reads `y_gutter` (`:4485`),
    /// the estimate for an unbaked ladder (`:2963`), else `gutter_of` the drawn rungs.
    pub const LINE_RAIL_CONCAT: GutterRule = GutterRule {
        empty_axis: EmptyAxis::Reserve,
        left_width: LabelWidth::Unclamped,
        away_by_size: AwayReach::Estimated,
        away_baked: BakedAway::Plain,
    };
    /// The bar family's fit: `ax_off_y + val_tick_size + val_gutter` always
    /// (`bar/vertical.rs:2930-2973`), `val_gutter` clamped (`:1548-1550`), the
    /// estimate for an unbaked right axis (`:1595`), else for an away axis
    /// `base_label_pad + max_w.min(limit)` (`:1550`).
    pub const BAR_FIT: GutterRule = GutterRule {
        empty_axis: EmptyAxis::Reserve,
        left_width: LabelWidth::Clamped,
        away_by_size: AwayReach::Estimated,
        away_baked: BakedAway::Padded,
    };
    /// The bar family's rail concat: the left axis reads the unclamped `max_val_w`
    /// (`bar/vertical.rs:2624`); a right axis reads `val_gutter` (`:2388`), as in Fit.
    pub const BAR_RAIL_CONCAT: GutterRule = GutterRule {
        empty_axis: EmptyAxis::Reserve,
        left_width: LabelWidth::Unclamped,
        away_by_size: AwayReach::Estimated,
        away_baked: BakedAway::Padded,
    };
}

/// The target tick counts the cover-ladder estimate tries (`_ESTIMATE_TARGET_COUNTS`).
const ESTIMATE_COUNTS: [usize; 7] = [3, 4, 5, 6, 8, 10, 12];

/// The ghost axis `axis_y.mirror` draws on the plot's other edge, at the primary's rungs and
/// under its parity mask.
///
/// Pinned to production on a line chart and a vertical bar (`mirror: true` and a ghost `format`,
/// both on a left and a right axis, `LINE_FIT` and `BAR_FIT`). The caller must not pass a mirror
/// for anything else, and must refuse, because layout cannot see it:
/// - a chart that is not a line or a vertical bar (area, scatter, histogram, heatmap, a
///   horizontal bar's category axis), or a facet panel (the facet composer paints that ghost);
/// - a mirror beside an endpoint-label rail or any `*_RAIL_CONCAT` rule;
/// - a left axis authored `labels.align: right` (production re-measures its gutter and moves the
///   ghost);
/// - an axis offset (`ax_off_y`), which production adds to both edges;
/// - `labels.font.case`, which production applies to ghost labels, so a [`Mirror::Format`] ghost
///   would measure uncased text;
/// - a [`Mirror::Format`] ghost on a ruled axis (production rejects it);
/// - a dual-axis layer pinned to a side (production rejects it).
#[derive(Debug, Clone, Copy)]
pub enum Mirror<'a> {
    /// The primary's own labels.
    Repeat,
    /// The ghost's own number format, painted as a plain tick.
    Format(&'a ResolvedFormat),
    /// A ghost labeled by an authored `mirror.expr`, `mirror.ruler` or `mirror.tick_label`.
    /// [`ValueAxis::place`] refuses it whatever the plot, so a caller states it rather than
    /// dropping the request.
    Authored,
}

/// A value axis's labels as resolved: the face, the number format and locale
/// that print a `BySize` rung, the least gap Vega's parity rule leaves between
/// labels (`labelSeparation`), the width a label's box is capped at, the tick
/// length and the label padding. `rule`, `estimate_extent` and `mirror` are read
/// by a left or right axis only; `title` by a bottom or top axis only.
///
/// A bottom or top axis draws its labels flat, as production draws every
/// continuous x axis: it takes no tilt verdict, so the caller refuses an authored
/// `labels.angle`, which production ignores there.
#[derive(Clone, Copy)]
pub struct ValueLabels<'a> {
    pub font: Font<'a>,
    pub format: &'a ResolvedFormat,
    pub locale: &'a NumberLocale,
    pub side: Side,
    pub min_gap: f64,
    /// `axis.labels.flush` read as a threshold: an end label within this many pixels of a range
    /// end anchors on it; `None` centers every label.
    pub flush: Option<f64>,
    pub limit: f64,
    pub tick: f64,
    pub padding: f64,
    pub rule: GutterRule,
    /// The raw value extent of the axis's field (min, max of its rows): what
    /// production sizes the cover estimate from, never the shared-scale union
    /// (stack totals, annotations, layers). Its max also picks the end an anchored
    /// affix dresses on a house SI axis: the first tick when it is not above 0.
    pub estimate_extent: Extent,
    /// The mirror axis on the other edge, if the chart asks for one; see [`Mirror`] for what
    /// the caller must refuse before stating one.
    pub mirror: Option<Mirror<'a>>,
    pub title: Option<AxisTitle<'a>>,
}

/// An axis title as resolved: one line of text, its face and its padding from the labels.
#[derive(Debug, Clone, Copy)]
pub struct AxisTitle<'a> {
    pub text: &'a str,
    pub font: Font<'a>,
    pub padding: f64,
}

/// A rung's label as placed: its anchor in plot coordinates (the plot's top
/// left at the origin), how the text sits on it, whether the parity rule
/// keeps it, its ink, and the room production reserves for it
/// ([`vega_space`]), which the gutter reads. Hidden rungs still draw their
/// tick and gridline.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedRung {
    pub value: f64,
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub align: Align,
    pub baseline: Baseline,
    pub visible: bool,
    pub ink: Bounds,
    pub space: Bounds,
}

/// An axis title as placed: centered on its anchor in plot coordinates, the
/// edge `baseline` names on it.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedTitle {
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub baseline: Baseline,
    /// The quarter turn a left or right axis's title takes about its anchor, in degrees.
    pub turn: Option<f64>,
    pub ink: Bounds,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub rungs: Vec<PlacedRung>,
    pub gutter: Insets,
    pub title: Option<PlacedTitle>,
}

/// A value axis over one domain: it holds the face's ascent and descent, and
/// decides rungs and placement per plot size.
pub struct ValueAxis<'a> {
    domain: &'a ValueDomain,
    labels: ValueLabels<'a>,
    book: &'a FontBook,
    ascent: f64,
    descent: f64,
}

/// Vega-Lite's default `tickCount` for a quantitative axis with no authored
/// count: `ceil(length / 40)`.
fn default_tick_count(length: f64) -> usize {
    (length / 40.0).ceil() as usize
}

/// The domain's ends in scale order: a reversed authored domain starts at its high end.
pub(crate) fn direction(domain: &ValueDomain) -> (f64, f64) {
    let bounds = domain.bounds.extent;
    match domain.authored {
        Some(authored) if authored.reversed => (bounds.hi(), bounds.lo()),
        _ => (bounds.lo(), bounds.hi()),
    }
}

/// How far along the axis `value` sits, 0 at the domain's start and 1 at its end. d3's linear
/// scale puts a flat domain at the middle of the range.
pub(crate) fn fraction(domain: &ValueDomain, value: f64) -> f64 {
    let (start, stop) = direction(domain);
    if stop == start {
        0.5
    } else {
        (value - start) / (stop - start)
    }
}

impl<'a> ValueAxis<'a> {
    pub fn new(
        domain: &'a ValueDomain,
        labels: ValueLabels<'a>,
        book: &'a FontBook,
    ) -> Result<Self, Diagnostic> {
        let run = book.run("0", &labels.font).map_err(diag::err_internal)?;
        Ok(Self {
            domain,
            labels,
            book,
            ascent: run.ascent,
            descent: run.descent,
        })
    }

    /// The rungs drawn on an axis `length` px long, in domain order, each with
    /// its text. Authored and fixed ladders carry theirs; a `BySize` ladder takes
    /// d3's ticks at Vega's count (`ceil(length / 40)`, capped by the ladder's
    /// `min_step`) over the domain's bounds, and prints each through the format.
    /// Rungs outside the bounds are not drawn (Vega's `validTicks`).
    pub fn rungs(&self, length: f64) -> Result<Vec<(f64, String)>, Diagnostic> {
        let bounds = self.domain.bounds.extent;
        let (lo, hi) = (bounds.lo(), bounds.hi());
        let (start, stop) = direction(self.domain);
        let slack = (hi - lo) * 1e-9;
        match &self.domain.ladder {
            Ladder::Authored(rungs) => Ok(rungs
                .iter()
                .filter(|r| lo - slack <= r.value && r.value <= hi + slack)
                .map(|r| (r.value, r.text.clone()))
                .collect()),
            Ladder::Fixed(fixed) => Ok(fixed
                .rungs()
                .iter()
                .filter(|r| lo - slack <= r.value && r.value <= hi + slack)
                .map(|r| (r.value, r.text.clone()))
                .collect()),
            Ladder::BySize { min_step } => {
                let ValueLabels { format, locale, estimate_extent, .. } = self.labels;
                format.unbaked_rungs(locale, (start, stop), default_tick_count(length), *min_step, estimate_extent.hi())
            }
            Ladder::Counts => {
                let ValueLabels { format, locale, estimate_extent, .. } = self.labels;
                let count = default_tick_count(length).min(hi.floor() as usize).max(1);
                format.unbaked_rungs(locale, (start, stop), count, None, estimate_extent.hi())
            }
            Ladder::None => Err(diag::err_internal(
                "a value axis with no data-chosen ladder (log, independent panels) is not laid out here",
            )),
        }
    }

    /// The widest label production could plausibly draw on an unbaked axis, plus the
    /// breathing room (`common::estimated_tick_label_gutter`): the zero-extended
    /// extent (the authored domain's when set), its ends and `nice_tick_values`
    /// at every estimate count, painted through the format and capped at `limit`.
    fn estimated_gutter(&self) -> Result<f64, Diagnostic> {
        let (lo, hi) = match self.domain.authored {
            Some(authored) => (authored.extent.lo(), authored.extent.hi()),
            None => (
                self.labels.estimate_extent.lo(),
                self.labels.estimate_extent.hi(),
            ),
        };
        let (lo, hi) = (lo.min(0.0), hi.max(0.0));
        let mut candidates = vec![lo, hi];
        for count in ESTIMATE_COUNTS {
            candidates.extend(nice_tick_values(lo, hi, count, None));
        }
        let mut widest = 0.0_f64;
        for value in candidates {
            let text = self.paint(value, None)?;
            let run = self
                .book
                .run(&text, &self.labels.font)
                .map_err(diag::err_internal)?;
            widest = widest.max(run.advance);
        }
        Ok(widest.min(self.labels.limit) + RIGHT_GUTTER_PAD)
    }

    /// A ladder-less axis's rung text (`rung` is the tick's index and the ladder's
    /// length; the estimate has none), printed by the one unbaked-rung law.
    fn paint(&self, value: f64, rung: Option<(usize, usize)>) -> Result<String, Diagnostic> {
        let ValueLabels { format, locale, .. } = self.labels;
        format.paint_unbaked_rung(locale, value, rung, self.labels.estimate_extent.hi())
    }

    /// The number format the ghost paints with, `None` when it repeats the primary's labels.
    /// The refusals are decided on the request alone, never on what a plot happens to show.
    fn ghost_format(&self, mirror: Mirror<'a>) -> Result<Option<&'a ResolvedFormat>, Diagnostic> {
        if self.labels.side == Side::RightAway {
            return Err(Diagnostic::refused(refuse!(
                "a mirror axis beside a right axis whose labels start past the tick is not supported"
            )));
        }
        match mirror {
            Mirror::Repeat => Ok(None),
            Mirror::Format(format) => Ok(Some(format)),
            Mirror::Authored => Err(Diagnostic::refused(refuse!(
                "a mirror axis labeled by an authored mirror.expr, mirror.ruler or mirror.tick_label is not supported"
            ))),
        }
    }

    /// The widest shown ghost label, capped at the limit, `None` when none is shown: the ghost
    /// reuses the primary's parity mask, so a label the primary hides reserves nothing.
    fn ghost_width(
        &self,
        own: Option<&ResolvedFormat>,
        texts: &[(f64, String, f64, f64)],
        shown: &[bool],
    ) -> Result<Option<f64>, Diagnostic> {
        let ValueLabels {
            font,
            locale,
            limit,
            ..
        } = self.labels;
        let mut widest: Option<f64> = None;
        for ((value, _, advance, _), _) in texts.iter().zip(shown).filter(|(_, &shown)| shown) {
            let width = match own {
                None => *advance,
                Some(format) => {
                    let text = format.paint_tick(locale, *value)?;
                    self.book
                        .run(&text, &font)
                        .map_err(diag::err_internal)?
                        .advance
                }
            };
            widest = Some(widest.unwrap_or(0.0).max(width.min(limit)));
        }
        Ok(widest)
    }

    /// The labels on a plot of `plot` px, centered on their rung. A label's box
    /// is its advance (capped at `limit`) wide; which labels show is Vega's parity
    /// rule over boxes the font size tall, and where they are inked a box is the
    /// face's ascent plus descent tall (quirk class Q2d). The side gutter is the
    /// widest box of all, shown or not, and the anchor's distance from the plot;
    /// the top and bottom gutters are the shown labels' overhang. An axis that
    /// draws no rung reserves what its [`GutterRule`] says. A right axis over a
    /// `BySize` ladder sizes its gutter by [`Self::estimated_gutter`], production
    /// not knowing the rungs Vega will draw: always for `Right`, and for
    /// `RightAway` when the rule says `Estimated`.
    pub fn place(&self, plot: Size) -> Result<Placement, Diagnostic> {
        if matches!(self.labels.side, Side::Bottom | Side::Top) {
            return self.place_across(plot);
        }
        let ValueLabels {
            font,
            side,
            tick,
            padding,
            limit,
            rule,
            ..
        } = self.labels;
        let own_format = self
            .labels
            .mirror
            .map(|mirror| self.ghost_format(mirror))
            .transpose()?;
        let mut texts = Vec::new();
        for (value, text) in self.rungs(plot.height)? {
            let advance = self
                .book
                .run(&text, &font)
                .map_err(diag::err_internal)?
                .advance;
            texts.push((
                value,
                text,
                advance,
                plot.height * (1.0 - fraction(self.domain, value)),
            ));
        }
        let clamped = texts.iter().map(|t| t.2.min(limit)).fold(0.0, f64::max);
        let unclamped = texts.iter().map(|t| t.2).fold(0.0, f64::max);
        let by_size = matches!(self.domain.ladder, Ladder::BySize { .. });
        let estimated = by_size
            && match side {
                Side::Left | Side::Bottom | Side::Top => false,
                Side::Right => true,
                Side::RightAway => rule.away_by_size == AwayReach::Estimated,
            };
        let estimate = if estimated {
            self.estimated_gutter()?
        } else {
            0.0
        };
        let (distance, left_of_anchor, reach) = match side {
            Side::Left => {
                let widest = match rule.left_width {
                    LabelWidth::Clamped => clamped,
                    LabelWidth::Unclamped => unclamped,
                };
                (-(tick + padding), true, tick + padding + widest)
            }
            Side::Right => {
                let reach = tick
                    + if by_size {
                        estimate
                    } else {
                        clamped + RIGHT_GUTTER_PAD
                    };
                (plot.width + reach, true, reach)
            }
            Side::Bottom | Side::Top => unreachable!("a horizontal axis is placed across"),
            Side::RightAway => {
                let away = if by_size {
                    match rule.away_by_size {
                        AwayReach::Padded => padding + clamped,
                        AwayReach::Estimated => estimate,
                    }
                } else {
                    match rule.away_baked {
                        BakedAway::Padded => padding + clamped,
                        BakedAway::Plain => clamped + RIGHT_GUTTER_PAD,
                    }
                };
                (plot.width + tick + padding, false, tick + away)
            }
        };
        let reach = if texts.is_empty() && rule.empty_axis == EmptyAxis::Zero {
            0.0
        } else {
            reach
        };
        let boxed = |y: f64, width: f64, height: f64| {
            let x1 = if left_of_anchor {
                distance - width
            } else {
                distance
            };
            Bounds {
                x1,
                y1: y - height / 2.0,
                x2: x1 + width,
                y2: y + height / 2.0,
            }
        };
        // A Vega box's offset from its anchor is the same for every label, so
        // it cancels in the gaps the parity rule reads.
        let vega: Vec<Bounds> = texts
            .iter()
            .map(|&(_, _, w, y)| boxed(y, w.min(limit), font.size))
            .collect();
        let shown = parity_visible(&vega, self.labels.min_gap);
        let ghost = match own_format {
            None => None,
            Some(own) => self.ghost_width(own, &texts, &shown)?,
        };
        let height = self.ascent + self.descent;
        let rungs: Vec<PlacedRung> = texts
            .into_iter()
            .zip(shown)
            .map(|((value, text, width, y), visible)| {
                let ink = boxed(y, width.min(limit), height);
                PlacedRung {
                    value,
                    text,
                    x: distance,
                    y,
                    align: if left_of_anchor {
                        Align::Right
                    } else {
                        Align::Left
                    },
                    baseline: Baseline::Middle,
                    visible,
                    ink,
                    space: vega_space(
                        (ink.x1, ink.x2),
                        (distance, y),
                        Baseline::Middle,
                        font.size,
                        1,
                        0.0,
                    ),
                }
            })
            .collect();
        let (mut top, mut bottom) = (0.0_f64, 0.0_f64);
        for rung in rungs.iter().filter(|r| r.visible) {
            top = top.max(-rung.space.y1);
            bottom = bottom.max(rung.space.y2 - plot.height);
        }
        // The ghost starts where the primary's labels do on its own edge, so a left axis's ghost
        // hangs from the right tick and a right axis's ends at the far edge of the primary's
        // gutter: the primary's reserve is inherited, and each ghost label's width extends the
        // margin a second time.
        let band = self.labels.title.map_or(0.0, |title| title.padding - text_ink::rise(title.font.size, "bottom"));
        let gutter = match side {
            Side::Left => Insets {
                top,
                right: ghost.map_or(0.0, |width| tick + padding + width),
                bottom,
                left: reach + band,
            },
            Side::Right | Side::RightAway => Insets {
                top,
                right: reach + band,
                bottom,
                left: ghost.map_or(0.0, |width| reach + width),
            },
            Side::Bottom | Side::Top => unreachable!("a horizontal axis is placed across"),
        };
        // The title sits `padding` plus its rotation's nudge past the labels' far edge: the widest label's
        // start on a left axis, the anchor or the gutter's end on a right one.
        let title = self
            .labels
            .title
            .map(|title| self.place_rotated_title(title, plot, if side == Side::Left { -reach } else { plot.width + reach }, side == Side::Left))
            .transpose()?;
        Ok(Placement {
            rungs,
            gutter,
            title,
        })
    }

    /// A bottom or top axis on a plot of `plot` px. Which labels show is Vega's
    /// parity rule over boxes of production's measure ([`parity_advance`], capped
    /// at the limit) by the font size; where they are inked a box is the shaped advance (capped)
    /// by the face's ascent plus descent (quirk class Q2d). Space is production's: the
    /// gutter reaches the labels' room ([`vega_space`]) and then the title's band,
    /// and the title's baseline sits the label size plus its padding past theirs (`axis_title_flat`), or
    /// its near edge `padding` off the plot edge when nothing is rung, since no tick is drawn. The
    /// outermost ticks' stroke is the caller's.
    fn place_across(&self, plot: Size) -> Result<Placement, Diagnostic> {
        let ValueLabels {
            font,
            side,
            tick,
            padding,
            limit,
            min_gap,
            flush,
            ..
        } = self.labels;
        if self.labels.mirror.is_some() {
            return Err(diag::err_internal(
                "a mirror reached a bottom or top value axis: only axis_y mirrors",
            ));
        }
        let below = side == Side::Bottom;
        let edge = if below { plot.height } else { 0.0 };
        let away = |distance: f64| {
            if below {
                edge + distance
            } else {
                edge - distance
            }
        };
        let anchor = away(tick + padding);
        let baseline = if below {
            Baseline::Top
        } else {
            Baseline::Bottom
        };
        let boxed = |x: f64, width: f64, align: Align, height: f64| {
            let x1 = match align {
                Align::Left => x,
                Align::Center => x - width / 2.0,
                Align::Right => x - width,
            };
            let y1 = if below { anchor } else { anchor - height };
            Bounds {
                x1,
                y1,
                x2: x1 + width,
                y2: y1 + height,
            }
        };
        let height = self.ascent + self.descent;
        let (mut vega, mut placed) = (Vec::new(), Vec::new());
        for (value, text) in self.rungs(plot.width)? {
            let x = plot.width * fraction(self.domain, value);
            let align = flush_align(x, plot.width, flush);
            let decided = parity_advance(self.book, &text, &font)?;
            let advance = self
                .book
                .run(&text, &font)
                .map_err(diag::err_internal)?
                .advance;
            vega.push(boxed(x, decided.min(limit), align, font.size));
            let ink = boxed(x, advance.min(limit), align, height);
            let space = vega_space((ink.x1, ink.x2), (x, anchor), baseline, font.size, 1, 0.0);
            placed.push((value, text, x, align, ink, space));
        }
        let shown = parity_visible(&vega, min_gap);
        let rungs: Vec<PlacedRung> = placed
            .into_iter()
            .zip(shown)
            .map(
                |((value, text, x, align, ink, space), visible)| PlacedRung {
                    value,
                    text,
                    x,
                    y: anchor,
                    align,
                    baseline,
                    visible,
                    ink,
                    space,
                },
            )
            .collect();
        let outer = |reach: f64, ink: Bounds| {
            if below {
                reach.max(ink.y2)
            } else {
                reach.min(ink.y1)
            }
        };
        let mut reach = edge;
        let mut gutter = Insets::ZERO;
        for space in rungs.iter().filter(|r| r.visible).map(|r| r.space) {
            reach = outer(reach, space);
            gutter.left = gutter.left.max(-space.x1);
            gutter.right = gutter.right.max(space.x2 - plot.width);
        }
        let label_baseline =
            (!rungs.is_empty()).then(|| (vega_baseline(anchor, baseline, font.size), font.size));
        let title = self
            .labels
            .title
            .map(|title| self.place_title(title, plot, label_baseline, below))
            .transpose()?;
        // Production's title band (`x_title_extra`): the padding and the title's Vega line, below
        // its top anchor.
        let band = self.labels.title.map_or(0.0, |title| {
            title.padding + vega_line(0.0, Baseline::Top, title.font.size).1
        });
        if below {
            gutter.bottom = reach - edge + band;
        } else {
            gutter.top = edge - reach + band;
        }
        Ok(Placement {
            rungs,
            gutter,
            title,
        })
    }

    /// A left or right axis title, turned a quarter and centered on the plot's height, `label_edge` the
    /// plot-local x of the labels' far edge. It sits `padding` plus the turn's nudge past it
    /// (`chart-renderer/src/common.rs::axis_title_rotated`, its band `y_title_extra`); a title longer than the plot is refused, as production wraps it.
    fn place_rotated_title(&self, title: AxisTitle, plot: Size, label_edge: f64, left: bool) -> Result<PlacedTitle, Diagnostic> {
        if title.text.contains('\n') || title.text.contains(measure::LINE_SEP) {
            return Err(diag::err_internal(
                "an axis title reached layout on more than one line: production joins an authored break",
            ));
        }
        let run = self.book.run(title.text, &title.font).map_err(diag::err_internal)?;
        if run.advance > plot.height {
            return Err(Diagnostic::refused(refuse!("a value axis title longer than its plot is not supported")));
        }
        let sign = if left { -1.0 } else { 1.0 };
        let x = label_edge + sign * (title.padding - measure::baseline_shift("bottom", title.font.size));
        let y = plot.height / 2.0;
        Ok(PlacedTitle {
            text: title.text.to_string(),
            x,
            y,
            baseline: Baseline::Bottom,
            turn: Some(sign * 90.0),
            // The turned text's up points away from the plot.
            ink: Bounds {
                x1: x - if left { run.ascent } else { run.descent },
                y1: y - run.advance / 2.0,
                x2: x + if left { run.descent } else { run.ascent },
                y2: y + run.advance / 2.0,
            },
        })
    }

    /// A bottom or top axis title centered on the plot: its baseline the label
    /// size plus `padding` past the labels' (`label_baseline`, with their size), or,
    /// when nothing is rung, its near edge (the top of a bottom axis's title, the
    /// bottom of a top axis's) `padding` off the plot edge. Its anchor is where
    /// Vega's baseline offset puts that baseline; its ink is the face's own.
    /// A title wider than the plot is refused: production wraps it at the chart's
    /// width, which this axis does not see. A line break never reaches here, since
    /// production joins an authored one.
    fn place_title(
        &self,
        title: AxisTitle,
        plot: Size,
        label_baseline: Option<(f64, f64)>,
        below: bool,
    ) -> Result<PlacedTitle, Diagnostic> {
        if title.text.contains('\n') || title.text.contains(measure::LINE_SEP) {
            return Err(diag::err_internal(
                "an axis title reached layout on more than one line: production joins an authored break",
            ));
        }
        let run = self
            .book
            .run(title.text, &title.font)
            .map_err(diag::err_internal)?;
        if run.advance > plot.width {
            return Err(Diagnostic::refused(refuse!("a value axis title wider than its plot is not supported")));
        }
        let height = run.ascent + run.descent;
        let sign = if below { 1.0 } else { -1.0 };
        let baseline = if below {
            Baseline::Top
        } else {
            Baseline::Bottom
        };
        let edge = if below { plot.height } else { 0.0 };
        let y = match label_baseline {
            None => edge + sign * title.padding,
            Some((at, size)) => {
                let title_baseline = at + sign * (size + title.padding);
                title_baseline - vega_baseline(0.0, baseline, title.font.size)
            }
        };
        let y1 = if below { y } else { y - height };
        let x = plot.width / 2.0;
        Ok(PlacedTitle {
            text: title.text.to_string(),
            x,
            y,
            baseline,
            turn: None,
            ink: Bounds {
                x1: x - run.advance / 2.0,
                y1,
                x2: x + run.advance / 2.0,
                y2: y1 + height,
            },
        })
    }
}
