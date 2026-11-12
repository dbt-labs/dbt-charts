//! A band or point axis's labels: which angle they draw at, and the gutter
//! their ink takes below and beside the plot.

use d3rs::scale::Band;
use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::{Bounds, Transform};

use crate::geom::{Insets, Size};
use crate::plot::label::{tilted_anchor, vega_baseline, vega_line, vega_space, Align, Baseline};
use crate::plot::value_axis::{place_across_title, AxisTitle, PlacedRung, Placement as ValuePlacement};

/// How an axis's labels may resolve crowding, as the axis style sets it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Overlap<'a> {
    /// No overlap handling: labels stay flat and are not checked.
    Unchecked,
    /// Labels stay flat; a crowded axis collides.
    Flat,
    /// The angles to try in order, shallowest first.
    Tilt(&'a [f64]),
    /// An authored angle: drawn as given, never checked.
    Authored(f64),
}

/// The angle the labels draw at, and whether they still collide there.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Tilt {
    pub angle: f64,
    pub collides: bool,
}

/// The angle for labels `widths` wide (each with its trailing word space), one
/// per band of `band` px, at `font_size`. Flat when every neighboring pair's
/// mean width fits a band; else the first ladder angle whose rotated footprint
/// `w·cos + font_size·sin` of the widest label fits a band, else the last angle,
/// colliding; an empty ladder stays flat. The footprint takes the font size as
/// the text's height, as production decides; where the labels are placed and
/// inked, their height is the face's ascent plus descent (quirk class Q2d).
pub fn tilt(widths: &[f64], band: f64, overlap: &Overlap, font_size: f64) -> Tilt {
    let flat = Tilt {
        angle: 0.0,
        collides: false,
    };
    let ladder = match overlap {
        Overlap::Unchecked => return flat,
        Overlap::Authored(angle) => {
            return Tilt {
                angle: *angle,
                collides: false,
            }
        }
        Overlap::Flat | Overlap::Tilt(_) if fits_flat(widths, band) => return flat,
        Overlap::Flat => {
            return Tilt {
                angle: 0.0,
                collides: true,
            }
        }
        Overlap::Tilt(ladder) => *ladder,
    };
    let widest = widths.iter().copied().fold(0.0, f64::max);
    let footprint = |angle: f64| {
        let (sin, cos) = angle.abs().to_radians().sin_cos();
        widest * cos + font_size * sin
    };
    match (
        ladder.iter().find(|&&angle| footprint(angle) <= band),
        ladder.last(),
    ) {
        (Some(&angle), _) => Tilt {
            angle,
            collides: false,
        },
        (None, Some(&last)) => Tilt {
            angle: last,
            collides: true,
        },
        (None, None) => flat,
    }
}

/// Every neighboring pair's mean width fits a band; a lone label fits by its own width.
fn fits_flat(widths: &[f64], band: f64) -> bool {
    match widths {
        [] => true,
        [only] => *only <= band,
        _ => widths
            .windows(2)
            .all(|pair| (pair[0] + pair[1]) / 2.0 <= band),
    }
}

/// How a band scale spreads its domain over the plot: d3's band padding and
/// alignment, `padding_inner` 1 for a point scale.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BandGeometry {
    pub padding_inner: f64,
    pub padding_outer: f64,
    pub align: f64,
    pub round: bool,
}

/// A band axis's labels as resolved: their text in domain order, the face they
/// paint in, how crowding resolves, the width they truncate at, the width the
/// tilt is decided against (the card's width times the theme's usable ratio),
/// and how far below the axis line they hang (tick size plus label padding).
#[derive(Debug, Clone, Copy)]
pub struct BandLabels<'a> {
    pub text: &'a [String],
    pub font: Font<'a>,
    pub overlap: Overlap<'a>,
    pub limit: f64,
    pub tilt_width: f64,
    pub offset: f64,
}

/// A drawn label: its text, its anchor in plot coordinates (the axis line at
/// `y = 0`), how the text sits on the anchor, its ink, and the room production
/// reserves for it ([`vega_space`]), which the gutter reads.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedLabel {
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub angle: f64,
    pub align: Align,
    pub baseline: Baseline,
    pub ink: Bounds,
    pub space: Bounds,
}

/// The labels placed for one plot width, and how far their ink reaches past the plot.
#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub labels: Vec<PlacedLabel>,
    pub gutter: Insets,
}

/// A bottom band axis's labels once their size-free decisions are made: the
/// angle (measured on the full text) and the drawn text (truncated at the limit).
#[derive(Debug, Clone)]
pub struct BandAxis {
    tilt: Tilt,
    offset: f64,
    /// Each label's drawn text, its advance, and its full advance capped at the limit.
    drawn: Vec<(String, f64, f64)>,
    font_size: f64,
    ascent: f64,
    descent: f64,
}

impl BandAxis {
    pub fn new(labels: &BandLabels, book: &FontBook) -> Result<Self, String> {
        let space = book.run(" ", &labels.font)?.advance;
        let widths = labels
            .text
            .iter()
            .map(|t| Ok(book.run(t, &labels.font)?.advance + space))
            .collect::<Result<Vec<_>, String>>()?;
        let band = labels.tilt_width / labels.text.len().max(1) as f64;
        let tilt = tilt(&widths, band, &labels.overlap, labels.font.size);
        let mut drawn = Vec::with_capacity(labels.text.len());
        let (mut ascent, mut descent) = (0.0_f64, 0.0_f64);
        for text in labels.text {
            let (cut, _) = book.truncate(text, &labels.font, labels.limit)?;
            let run = book.run(&cut, &labels.font)?;
            ascent = ascent.max(run.ascent);
            descent = descent.max(run.descent);
            let full = book.run(text, &labels.font)?.advance.min(labels.limit);
            drawn.push((cut, run.advance, full));
        }
        Ok(Self {
            tilt,
            offset: labels.offset,
            drawn,
            font_size: labels.font.size,
            ascent,
            descent,
        })
    }

    pub fn angle(&self) -> f64 {
        self.tilt.angle
    }

    /// `WARN-AXIS-LABEL-COLLISION` when the labels still collide at their angle.
    pub fn collision(&self, chart_id: &str, field: &str) -> Option<Diagnostic> {
        self.tilt
            .collides
            .then(|| diag::warn_axis_label_collision(chart_id, field, self.drawn.len()))
    }

    /// The labels on a plot `plot_width` wide: each anchored `offset` below its
    /// band's center, aligned as Vega-Lite aligns a label at its angle, and
    /// inked as a box of its advance by the face's ascent plus descent (quirk
    /// class Q2d) turned about the anchor. The gutter is the room production
    /// reserves: the full advance capped at the limit by Vega's line, turned
    /// as production turns it.
    pub fn place(&self, geometry: &BandGeometry, plot_width: f64) -> Placement {
        let scale = Band::new(
            self.drawn.len(),
            [0.0, plot_width],
            geometry.padding_inner,
            geometry.padding_outer,
            geometry.align,
            geometry.round,
        );
        let (align, baseline) = tilted_anchor(self.tilt.angle);
        let height = self.ascent + self.descent;
        let turn = Transform::Rotate {
            deg: self.tilt.angle,
            about: None,
        }
        .affine();
        let labels: Vec<PlacedLabel> = self
            .drawn
            .iter()
            .enumerate()
            .map(|(i, (text, width, full))| {
                let x = scale.position(i) + scale.bandwidth / 2.0;
                let across = |width: f64| match align {
                    Align::Left => (0.0, width),
                    Align::Center => (-width / 2.0, width / 2.0),
                    Align::Right => (-width, 0.0),
                };
                let (x1, x2) = across(*width);
                let (s1, s2) = across(*full);
                let (y1, y2) = match baseline {
                    Baseline::Top => (0.0, height),
                    Baseline::Middle => (-height / 2.0, height / 2.0),
                    Baseline::Bottom => (-height, 0.0),
                };
                let local = Bounds { x1, y1, x2, y2 }.transformed(&turn);
                let ink = Bounds {
                    x1: x + local.x1,
                    y1: self.offset + local.y1,
                    x2: x + local.x2,
                    y2: self.offset + local.y2,
                };
                PlacedLabel {
                    text: text.clone(),
                    x,
                    y: self.offset,
                    angle: self.tilt.angle,
                    align,
                    baseline,
                    ink,
                    space: vega_space(
                        (x + s1, x + s2),
                        (x, self.offset),
                        baseline,
                        self.font_size,
                        1,
                        self.tilt.angle,
                    ),
                }
            })
            .collect();
        let gutter = labels.iter().fold(Insets::ZERO, |g, l| Insets {
            top: 0.0,
            right: g.right.max(l.space.x2 - plot_width),
            bottom: g.bottom.max(l.space.y2),
            left: g.left.max(-l.space.x1),
        });
        Placement { labels, gutter }
    }
}

/// A bottom band axis as the plan lays it out: its labels, the face and title they hang with, and
/// how the band scale spreads over the plot.
pub struct BandPlan<'a> {
    pub axis: BandAxis,
    pub geometry: BandGeometry,
    pub book: &'a FontBook,
    pub title: Option<AxisTitle<'a>>,
    pub font: Font<'a>,
    /// How far below the axis line a label's anchor hangs.
    pub offset: f64,
}

impl BandPlan<'_> {
    /// The axis on a plot of `plot`: the labels as value-axis rungs (a band label is always kept:
    /// a crowded axis was refused when its tilt was decided) and the title below them, with the
    /// gutter the labels' room and the title's band make, as [`ValueAxis::place`] counts it.
    pub fn place(&self, plot: Size) -> Result<ValuePlacement, Diagnostic> {
        let placed = self.axis.place(&self.geometry, plot.width);
        let anchor = plot.height + self.offset;
        let rungs: Vec<PlacedRung> = placed
            .labels
            .iter()
            .enumerate()
            .map(|(index, label)| {
                let shift = |b: Bounds| Bounds { x1: b.x1, y1: b.y1 + plot.height, x2: b.x2, y2: b.y2 + plot.height };
                PlacedRung {
                    value: index as f64,
                    text: label.text.clone(),
                    x: label.x,
                    y: anchor,
                    align: label.align,
                    baseline: label.baseline,
                    visible: true,
                    ink: shift(label.ink),
                    space: shift(label.space),
                }
            })
            .collect();
        let label_baseline = (!rungs.is_empty()).then(|| (vega_baseline(anchor, Baseline::Top, self.font.size), self.font.size));
        let title = self.title.map(|title| place_across_title(self.book, title, plot, label_baseline, true)).transpose()?;
        let band = self.title.map_or(0.0, |title| title.padding + vega_line(0.0, Baseline::Top, title.font.size).1);
        let reach = rungs.iter().map(|r| r.space.y2).fold(plot.height, f64::max);
        let gutter = Insets { bottom: reach - plot.height + band, ..placed.gutter };
        Ok(ValuePlacement { rungs, gutter, title })
    }
}
