//! A cartesian plot's value axis: which rungs it draws at a plot size, their
//! text, where the labels sit, and the gutter their ink takes past the plot.
//!
//! Bind owns the domain's final bounds; the rungs of a `BySize` ladder are
//! chosen here, because they follow the axis's pixel length.

use bind::formats::locale::NumberLocale;
use bind::formats::{Repeat, ResolvedFormat};
use bind::model::{Extent, Ladder, ValueDomain, Zero};
use d3rs::ticks::{d3_ticks, nice_tick_values, vega_tick_count};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::Bounds;

use crate::geom::{Insets, Size};

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
/// length and the label padding.
#[derive(Clone, Copy)]
pub struct ValueLabels<'a> {
    pub font: Font<'a>,
    pub format: &'a ResolvedFormat,
    pub locale: &'a NumberLocale,
    pub side: Side,
    pub min_gap: f64,
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
}

/// A rung's label as placed: its anchor in plot coordinates (the plot's top
/// left at the origin), and whether the parity rule keeps it. Hidden rungs
/// still draw their tick and gridline.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedRung {
    pub value: f64,
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub visible: bool,
    pub ink: Bounds,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub rungs: Vec<PlacedRung>,
    pub gutter: Insets,
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

fn has_affix_text(format: &ResolvedFormat) -> bool {
    !format.prefix.is_empty() || !format.suffix.is_empty()
}

/// The domain's ends in scale order: a reversed authored domain starts at its high end.
fn direction(domain: &ValueDomain) -> (f64, f64) {
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
                let count = vega_tick_count(start, stop, default_tick_count(length), *min_step);
                let ticks = d3_ticks(start, stop, count);
                let total = ticks.len();
                ticks
                    .into_iter()
                    .enumerate()
                    .map(|(i, v)| Ok((v, self.paint(v, Some((i, total)))?)))
                    .collect()
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
    /// length; the estimate has none). A house SI format paints each tick by its
    /// magnitude (`sub_unit_guarded_format`): the SI spec from 1 up, else the plain
    /// significant-digit sibling, or the exponential one below 1e-10. Its anchored
    /// affix wears the last tick, or the first when no value is above 0
    /// (`ladderless_tick_label`). Any other format with an anchored affix wears the
    /// last nonzero tick alone (`affix_tick_label`); without one, every tick prints
    /// `paint_tick`. The estimate dresses every candidate.
    fn paint(&self, value: f64, rung: Option<(usize, usize)>) -> Result<String, Diagnostic> {
        let ValueLabels { format, locale, .. } = self.labels;
        let anchored = format.repeat == Some(Repeat::Anchor) && has_affix_text(format);
        if !format.is_house_si() {
            return match rung {
                Some((index, count)) if anchored => {
                    if index + 1 == count && value != 0.0 {
                        format.paint_anchored(locale, &format.spec, value, true)
                    } else {
                        format.bare_digits(locale, &format.spec, value)
                    }
                }
                _ => format.paint_tick(locale, value),
            };
        }
        let (digit, scientific) =
            d3rs::format::sub_unit_specs(&format.spec).map_err(Diagnostic::refused)?;
        let band = if value.abs() >= 1.0 {
            &format.spec
        } else if value != 0.0 && value.abs() < d3rs::format::SUB_UNIT_SCIENTIFIC_FLOOR {
            &scientific
        } else {
            &digit
        };
        let wears = match rung {
            Some((index, count)) if anchored => {
                index
                    == if self.labels.estimate_extent.hi() <= 0.0 {
                        0
                    } else {
                        count - 1
                    }
            }
            _ => true,
        };
        if wears && has_affix_text(format) && value != 0.0 {
            return format.paint_anchored(locale, band, value, anchored);
        }
        format.bare_digits(locale, band, value)
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
                Side::Left => false,
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
            .map(|((value, text, width, y), visible)| PlacedRung {
                value,
                text,
                x: distance,
                y,
                visible,
                ink: boxed(y, width.min(limit), height),
            })
            .collect();
        let (mut top, mut bottom) = (0.0_f64, 0.0_f64);
        for rung in rungs.iter().filter(|r| r.visible) {
            top = top.max(-rung.ink.y1);
            bottom = bottom.max(rung.ink.y2 - plot.height);
        }
        // The ghost starts where the primary's labels do on its own edge, so a left axis's ghost
        // hangs from the right tick and a right axis's ends at the far edge of the primary's
        // gutter: the primary's reserve is inherited, and each ghost label's width extends the
        // margin a second time.
        let gutter = match side {
            Side::Left => Insets {
                top,
                right: ghost.map_or(0.0, |width| tick + padding + width),
                bottom,
                left: reach,
            },
            Side::Right | Side::RightAway => Insets {
                top,
                right: reach,
                bottom,
                left: ghost.map_or(0.0, |width| reach + width),
            },
        };
        Ok(Placement { rungs, gutter })
    }
}

/// Vega's `labelOverlap: parity` over label boxes in axis order: while three or
/// more labels remain and any neighboring pair sits closer than `separation`
/// (the larger of the pair's x and y gaps), every odd-indexed survivor goes;
/// once fewer than three remain with the axis's last label gone, that last
/// label shows in place of the last survivor.
fn parity_visible(ink: &[Bounds], separation: f64) -> Vec<bool> {
    let close = |a: &Bounds, b: &Bounds| {
        separation
            > (b.x1 - a.x2)
                .max(a.x1 - b.x2)
                .max(b.y1 - a.y2)
                .max(a.y1 - b.y2)
    };
    let crowded = |kept: &[usize]| kept.windows(2).any(|w| close(&ink[w[0]], &ink[w[1]]));
    let mut kept: Vec<usize> = (0..ink.len()).collect();
    if kept.len() < 3 || !crowded(&kept) {
        return vec![true; ink.len()];
    }
    while kept.len() >= 3 && crowded(&kept) {
        kept = kept.into_iter().step_by(2).collect();
    }
    let mut visible: Vec<bool> = (0..ink.len()).map(|i| kept.contains(&i)).collect();
    let last = ink.len() - 1;
    if kept.len() < 3 && !visible[last] {
        if kept.len() > 1 {
            visible[kept[kept.len() - 1]] = false;
        }
        visible[last] = true;
    }
    visible
}

/// RL §2e. Whether the value axis's gridlines cap the plot's bottom edge, which
/// decides the other axis's tick stubs (`_y_gridline_caps_bottom`, ported in
/// `resolve/src/cartesian/domain.rs`). False when the grid is hidden; true when
/// the floor is unknown; else whether the first rung of the baked ladder is the
/// floor. A `BySize` ladder bakes none, so its floor
/// is unknown and the verdict is true: production's, not the rung layout picks.
/// `rail_may_discard_domain` skips a baked bottom bound (a line or area whose
/// endpoint rail merges the scales).
pub fn caps_bottom(
    domain: &ValueDomain,
    grid_visible: bool,
    rail_may_discard_domain: bool,
) -> bool {
    if !grid_visible {
        return false;
    }
    let first = match &domain.ladder {
        Ladder::Authored(rungs) => rungs.first().map(|r| r.value),
        Ladder::Fixed(fixed) => fixed.rungs().first().map(|r| r.value),
        Ladder::BySize { .. } | Ladder::None => None,
    };
    let Some(first) = first else { return true };
    let anchored = matches!(domain.zero, Zero::Anchored | Zero::Pinned(true));
    let floor = if let Some(authored) = domain.authored {
        Some(if authored.reversed {
            authored.extent.hi()
        } else {
            authored.extent.lo()
        })
    } else if let (Some(pin), false) = (domain.min, rail_may_discard_domain) {
        Some(pin.value)
    } else if anchored {
        // An authored ladder says nothing about the domain: the floor is zero.
        Some(match &domain.ladder {
            Ladder::Fixed(_) => first,
            _ => 0.0,
        })
    } else if matches!(domain.ladder, Ladder::Authored(_)) {
        None
    } else {
        Some(first)
    };
    let Some(floor) = floor else { return true };
    (first - floor).abs() <= 1e-9_f64.max(1e-9 * first.abs().max(floor.abs()))
}
