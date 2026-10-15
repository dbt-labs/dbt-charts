//! Vega's `autosize: fit` margin algebra, ported from the vega v6.2.0
//! source (ViewLayout.js `layoutGroup`/`viewSizeLayout`, layout/axis.js,
//! layout/title.js, vega-scenegraph text/rect/rule bounds).
//!
//! This is NOT a Vega-Lite spec translation. The inputs are ink bounds in
//! plot coordinates — the same resolved payload values the families already
//! hold (label texts, font sizes, paddings) — and the output is the exact
//! plot rectangle vl-convert's renderer would negotiate for them:
//!
//! 1. `viewBounds` starts as the plot rect `(0,0,pw,ph)` and unions every
//!    contributor's ink bounds (axis labels, stroked rules, legends).
//! 2. The title stacks above the union: its group lands at
//!    `y = viewBounds.y1 − ink_h − offset`, plus a ±1px baseline-rounding
//!    quirk (`round(0.79·fs) − round(0.8·fs)`).
//! 3. Margins are the ceil'd overhangs (`ceil(−x1)` etc.); the plot is the
//!    padded view minus margins, and the layout re-runs at that size because
//!    right/bottom overhangs scale with the plot. Vega runs a BOUNDED number
//!    of those passes rather than searching for a fixed point — see
//!    `AUTOSIZE_PADDING_PASSES`, which is also why the title's x can end up
//!    outside the padded view and get clamped back to it.

use crate::constants::layout::VEGA_SUBTITLE_PADDING;

/// Vega-Lite's default `labelLimit` — the width cap it truncates axis and
/// legend labels to when the theme doesn't override it.
pub const DEFAULT_VL_LABEL_LIMIT: f64 = 180.0;

/// Axis-aligned bounds with Vega `Bounds` semantics (x1/y1 top-left).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bounds {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
}

impl Bounds {
    pub fn new(x1: f64, y1: f64, x2: f64, y2: f64) -> Self {
        Bounds { x1, y1, x2, y2 }
    }

    pub fn union(&mut self, o: &Bounds) {
        self.x1 = self.x1.min(o.x1);
        self.y1 = self.y1.min(o.y1);
        self.x2 = self.x2.max(o.x2);
        self.y2 = self.y2.max(o.y2);
    }

    /// Vega `Bounds.expand(d)` — every side moves outward by `d`.
    pub fn expand(mut self, d: f64) -> Self {
        self.x1 -= d;
        self.y1 -= d;
        self.x2 += d;
        self.y2 += d;
        self
    }

    /// Vega `Bounds.rotate(angle, x, y)`: the box of the four corners
    /// rotated by `angle_deg` (clockwise, y down) about `(x, y)`.
    pub fn rotate(self, angle_deg: f64, x: f64, y: f64) -> Self {
        let (sin, cos) = angle_deg.to_radians().sin_cos();
        let (dx, dy) = (x - x * cos + y * sin, y - x * sin - y * cos);
        let mut out = Bounds::new(f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
        for (px, py) in [(self.x1, self.y1), (self.x1, self.y2), (self.x2, self.y1), (self.x2, self.y2)] {
            out.union(&Bounds::new(
                cos * px - sin * py + dx,
                sin * px + cos * py + dy,
                cos * px - sin * py + dx,
                sin * px + cos * py + dy,
            ));
        }
        out
    }

    /// Vega `Bounds()` (no-arg constructor): the sentinel "nothing unioned
    /// yet" state `families/facet_layout.rs`'s `grid_layout` port starts
    /// its own accumulator from.
    pub fn empty_new() -> Self {
        Bounds::new(f64::MAX, f64::MAX, -f64::MAX, -f64::MAX)
    }

    /// Vega `Bounds.prototype.translate`.
    pub fn translate(&mut self, dx: f64, dy: f64) {
        self.x1 += dx;
        self.x2 += dx;
        self.y1 += dy;
        self.y2 += dy;
    }
}

/// Vega-Lite's `labelAngle` normalization: the item angle in `[0, 360)`.
pub fn normalize_angle(angle: f64) -> f64 {
    ((angle % 360.0) + 360.0) % 360.0
}

/// Vega-Lite's default `(labelAlign, labelBaseline)` for a rotated x-axis
/// label (`compile/axis/properties.ts` `defaultLabelAlign` /
/// `defaultLabelBaseline`, `angle` already normalized): the anchor is
/// `center`/`top` at 0° and 180°, else `left`/`right` by which half-turn
/// the angle falls in relative to the axis side, and the baseline is
/// `middle` within 45° of vertical, `top`/`bottom` otherwise.
pub fn vl_x_label_anchor(angle: f64, orient_bottom: bool) -> (&'static str, &'static str) {
    let align = if angle % 180.0 == 0.0 {
        "center"
    } else if (0.0 < angle && angle < 180.0) == orient_bottom {
        "left"
    } else {
        "right"
    };
    let baseline = if (45.0 < angle && angle < 135.0) || (225.0 < angle && angle < 315.0) {
        "middle"
    } else if (angle <= 45.0 || 315.0 <= angle) == !orient_bottom {
        "bottom"
    } else {
        "top"
    };
    (align, baseline)
}

/// `offset(item)` from vega-scenegraph `util/text.js` — the font-baseline
/// shift Vega applies to every text item instead of SVG's
/// `alignment-baseline`.
pub fn baseline_shift(baseline: &str, font_size: f64) -> f64 {
    (match baseline {
        "top" => 0.79 * font_size,
        "middle" => 0.30 * font_size,
        "bottom" => -0.21 * font_size,
        _ => 0.0, // alphabetic
    })
    .round()
}

/// How far a `middle`-baselined text hangs above its anchor: the ink rise
/// (`0.8 * font_size`, rounded) less Vega's baseline shift.
pub fn middle_rise(font_size: f64) -> f64 {
    (0.8 * font_size).round() - baseline_shift("middle", font_size)
}

/// The advance between the rows of a stacked axis label (Vega's default
/// `lineHeight`: the font size plus 2).
pub fn label_row_advance(font_size: f64) -> f64 {
    font_size + 2.0
}

/// Ink bounds of a single-line text item, per vega-scenegraph
/// `marks/text.js bound()`: the box top sits at
/// `y + baseline_shift − round(0.8·fs)` and spans one `fs` of height;
/// horizontally it spans `width` from the anchor per `align`.
pub fn text_ink(x: f64, y: f64, width: f64, font_size: f64, align: &str, baseline: &str) -> Bounds {
    let dy = baseline_shift(baseline, font_size) - (0.8 * font_size).round();
    let dx = match align {
        "center" => -width / 2.0,
        "right" => -width,
        _ => 0.0,
    };
    Bounds::new(x + dx, y + dy, x + dx + width, y + dy + font_size)
}

/// The widest adjacent-pair mean advance in a sequence of label widths —
/// port of `_max_pair` (`emitters/_label_overlap.py`): a lone label is its
/// own width; otherwise only adjacent pairs' means count, never a single
/// wide label on its own. Shared by bar's ordinal tilt ladder
/// (`category_label_layout`) and line/area's native temporal generic layout
/// (L1's `_generic_layout`/`_fits_flat`).
pub fn max_pair(widths: &[f64]) -> f64 {
    if widths.len() == 1 {
        return widths[0];
    }
    widths.windows(2).map(|w| (w[0] + w[1]) / 2.0).fold(0.0, f64::max)
}

/// True when `widths` fit flat against one already-divided `band` — port of
/// `_fits_flat`.
pub fn fits_flat(widths: &[f64], band: f64) -> bool {
    if widths.is_empty() {
        return true;
    }
    max_pair(widths) <= band
}

/// A rung off the tilt ladder: its angle and whether its rotated footprint
/// fits one band (the last rung when none does).
pub struct TiltPick {
    pub angle: f64,
    pub fits: bool,
}

/// Shallowest ladder angle whose rotated footprint fits one `band` — port of
/// `_pick_tilt_for_widths` (`emitters/_label_overlap.py`), shared by bar's
/// ordinal category axis and line/area's native temporal labels (L1).
/// `band` is already divided by `widths.len()` (production recomputes it
/// against whatever `widths` slice — full or parity-halved — is passed in).
pub fn pick_tilt(widths: &[f64], band: f64, increments: &[f64], font_size: f64) -> TiltPick {
    if increments.is_empty() || widths.is_empty() {
        return TiltPick { angle: 0.0, fits: true };
    }
    let max_width = widths.iter().cloned().fold(0.0, f64::max);
    let fits = |angle: &&f64| {
        let (sin, cos) = angle.abs().to_radians().sin_cos();
        max_width * cos + font_size * sin <= band
    };
    match increments.iter().find(fits) {
        Some(&angle) => TiltPick { angle, fits: true },
        None => TiltPick { angle: increments.last().copied().unwrap_or(0.0), fits: false },
    }
}

/// An ordinal category axis's label layout: the tilt angle and, when every
/// enabled strategy was tried and the labels still collide, how many labels
/// were measured (`AxisLabelLayout.collision_label_count`).
pub struct CategoryLabels {
    pub angle: f64,
    pub collision: Option<usize>,
}

/// Production's ordinal branch of `resolve_axis_x_overlap`
/// (`emitters/_label_overlap.py`), reached once an authored
/// `labels.angle` and a date-valued axis are ruled out: labels measured in
/// the Inter stand-in plus one word space are tested flat — the widest
/// adjacent pair's mean advance against one band of `chart_width ×
/// label_usable_ratio` — and, failing that, with `overlap.tilt` the theme's
/// `tilt_increments` are walked to the first angle whose rotated footprint
/// fits the band (the last rung when none does). The labels and the space
/// are measured at the axis labels' painted weight
/// (`_label_overlap.py:907`: `get_weighted_font_measurer(font.family,
/// font.weight)`), so a bold category is not fitted flat at its regular
/// width. An absent `overlap` stays flat. Shared by bar's category axis; moved
/// here so line/area's native temporal generic layout (L1) can reuse the
/// same `fits_flat`/`pick_tilt` primitives without duplicating them.
pub fn category_label_layout(
    labels: &crate::common::AxisLabels,
    cats: &[String],
    chart_width: f64,
    usable_ratio: Option<f64>,
    fonts: &crate::text::FontBook,
    font_size: f64,
) -> Result<CategoryLabels, String> {
    let flat = CategoryLabels { angle: 0.0, collision: None };
    let Some(overlap) = &labels.overlap else { return Ok(flat) };
    if cats.is_empty() {
        return Ok(flat);
    }
    let widths = category_label_widths(labels, cats, fonts, font_size, true)?;
    let ratio = usable_ratio.ok_or("bar: label_usable_ratio absent (theme required for overlap)")?;
    let band = chart_width * ratio / widths.len() as f64;
    if fits_flat(&widths, band) {
        return Ok(flat);
    }
    if !overlap.tilt {
        return Ok(CategoryLabels { angle: 0.0, collision: Some(widths.len()) });
    }
    let increments = labels
        .tilt_increments
        .as_deref()
        .ok_or("bar: labels.tilt_increments absent (theme required for overlap.tilt)")?;
    let tilt = pick_tilt(&widths, band, increments, font_size);
    Ok(CategoryLabels { angle: tilt.angle, collision: (!tilt.fits).then_some(widths.len()) })
}

/// The category labels' advances at the axis labels' painted weight, each
/// padded by one word space when `with_gap` (the tilt resolver's measure; an
/// authored angle measures them bare, `_pinned_angle_block_height`). The one
/// reading of those widths: the tilt pick and a bottom strip's tilted axis gap
/// both come from here.
pub fn category_label_widths(
    labels: &crate::common::AxisLabels,
    cats: &[String],
    fonts: &crate::text::FontBook,
    font_size: f64,
    with_gap: bool,
) -> Result<Vec<f64>, String> {
    let gap = if with_gap { labels.font.measure_weighted(fonts, " ", font_size)? } else { 0.0 };
    cats.iter().map(|c| labels.font.measure_weighted(fonts, c, font_size).map(|w| w + gap)).collect()
}

/// Vega's `labelOverlap` parity transform (vega-encode overlap.js) over
/// label ink in axis order: while three or more labels remain and any
/// adjacent pair sits closer than `separation` (`labelSeparation`; the
/// larger of the pair's x and y gaps, on the raw text bounds), every
/// odd-indexed survivor is hidden; once fewer than three remain with the
/// axis's last label hidden, that last label is shown in place of the
/// last survivor. Returns the visible mask.
pub fn parity_visible(ink: &[Bounds], separation: f64) -> Vec<bool> {
    let overlaps = |a: &Bounds, b: &Bounds| {
        separation > (b.x1 - a.x2).max(a.x1 - b.x2).max(b.y1 - a.y2).max(a.y1 - b.y2)
    };
    let has_overlap = |kept: &[usize]| kept.windows(2).any(|w| overlaps(&ink[w[0]], &ink[w[1]]));
    let mut visible = vec![true; ink.len()];
    let mut kept: Vec<usize> = (0..ink.len()).collect();
    if kept.len() >= 3 && has_overlap(&kept) {
        while kept.len() >= 3 && has_overlap(&kept) {
            kept = kept.into_iter().step_by(2).collect();
        }
        for (i, v) in visible.iter_mut().enumerate() {
            *v = kept.contains(&i);
        }
        if kept.len() < 3 && !visible[ink.len() - 1] {
            if kept.len() > 1 {
                visible[kept[kept.len() - 1]] = false;
            }
            visible[ink.len() - 1] = true;
        }
    }
    visible
}

/// Vega's `labelOverlap: "greedy"` transform (vega-view-transforms'
/// `Overlap.js`'s `greedy` reducer, run under the SAME outer driver
/// `parity_visible` above already models): a forward pass over label ink
/// in axis order — the first label is always kept, and each subsequent
/// one is hidden only if it overlaps the LAST KEPT label (never its
/// immediate predecessor, so a hidden label doesn't reset the comparison
/// point). This is Vega-Lite's compiled default for a LOG-scaled
/// quantitative axis (`labelOverlap: true`/"parity" is the default
/// everywhere else — probe-confirmed: a log axis's compiled Vega spec
/// carries `labelOverlap: "greedy"` regardless of whether the axis
/// authors an explicit `format`).
///
/// Two guards `Overlap.js` wraps around every reducer, greedy included,
/// that a naive single pass omits:
/// - fewer than three candidates, or no overlap at all among them, and
///   removal never engages — every candidate stays visible, however badly
///   the boxes collide (`items.length >= 3 && hasOverlap(...)`).
/// - after reducing, if fewer than three candidates survived AND the
///   axis's own TRUE LAST candidate is among the casualties, it is
///   rescued unconditionally — at the cost of the survivor pass's own
///   last-kept slot (never the first, the pass never drops it). Unlike
///   `parity_visible`'s repeated halving, one greedy pass never needs a
///   second: each surviving pair, by construction, was already tested
///   against its immediate output-neighbor (the "last kept" comparison
///   point), so post-pass `hasOverlap` is always false and the reduce
///   loop's `do…while` never repeats — hence no explicit loop here.
///
/// Returns the visible mask.
pub fn greedy_visible(ink: &[Bounds], separation: f64) -> Vec<bool> {
    let overlaps = |a: &Bounds, b: &Bounds| {
        separation > (b.x1 - a.x2).max(a.x1 - b.x2).max(b.y1 - a.y2).max(a.y1 - b.y2)
    };
    let has_overlap = |kept: &[usize]| kept.windows(2).any(|w| overlaps(&ink[w[0]], &ink[w[1]]));
    let mut visible = vec![true; ink.len()];
    let all: Vec<usize> = (0..ink.len()).collect();
    if all.len() >= 3 && has_overlap(&all) {
        let mut kept = Vec::with_capacity(all.len());
        let mut last: Option<usize> = None;
        for &i in &all {
            if last.is_none_or(|l| !overlaps(&ink[l], &ink[i])) {
                kept.push(i);
                last = Some(i);
            }
        }
        for (i, v) in visible.iter_mut().enumerate() {
            *v = kept.contains(&i);
        }
        if kept.len() < 3 && !visible[ink.len() - 1] {
            if kept.len() > 1 {
                visible[kept[kept.len() - 1]] = false;
            }
            visible[ink.len() - 1] = true;
        }
    }
    visible
}

/// Title (and optional subtitle) contribution, stacked above the view per
/// layout/title.js. `ink_h` is the group's stacked ink height
/// (`fs` for a lone single-line title; `title_fs + padding + subtitle_fs`
/// with a subtitle). `dy_quirk` is the title text's own local bounds shift
/// `round(0.79·fs) − round(0.8·fs)` (0 or −1 depending on font size).
#[derive(Clone)]
pub struct TitleInk {
    pub ink_h: f64,
    pub dy_quirk: f64,
    /// Gap between the view ink and the title group (`title.offset`).
    pub offset: f64,
    /// Rendered text width — measured, capped at the compiled title
    /// `limit` (observed in every captured spec as the padded view width,
    /// e.g. 568 on a 600-wide card with 16px padding); anchored at
    /// `viewBounds.x1`. That anchoring plus that cap leave it inert on the
    /// horizontal for every width but the last pixel before the cap — see
    /// `a_capped_title_reaches_the_right_margin_only_at_its_cap`.
    pub width: f64,
}


impl TitleInk {
    pub fn single(font_size: f64, offset: f64, width: f64) -> Self {
        TitleInk {
            ink_h: font_size,
            dy_quirk: (0.79 * font_size).round() - (0.8 * font_size).round(),
            offset,
            width,
        }
    }

    /// Each line past the first advances the title block by the Vega text
    /// mark's default lineHeight, `fontSize + 2` (oracle-probed: a wheel
    /// spec's origin moves exactly +16 per extra 14px title line and +13
    /// per extra 11px subtitle line, at any of the probed sizes).
    pub const LINE_ADVANCE_EXTRA: f64 = 2.0;

    /// `single`, wrapped onto `lines` lines (wrap-two overflow).
    pub fn with_lines(font_size: f64, lines: usize, offset: f64, width: f64) -> Self {
        let mut t = Self::single(font_size, offset, width);
        t.ink_h += (lines.max(1) - 1) as f64 * (font_size + Self::LINE_ADVANCE_EXTRA);
        t
    }

    /// Title with a subtitle stacked under it. The group's box top follows
    /// the title row (so `dy_quirk` stays the title's, as in `single`), and
    /// its bottom is the subtitle line at
    /// `title_fs + VEGA_SUBTITLE_PADDING + sub_fs + q(sub)` below that row — so
    /// the box height carries both quirks: `t + 3 + s + q(s) − q(t)`.
    /// Oracle: scenegraph origin sweeps over (t, s) ∈ {12,14,17,18} ×
    /// {11,12,17} all land `margin = ink_h + offset − dy_quirk` exactly.
    pub fn with_subtitle(
        font_size: f64,
        subtitle_font_size: f64,
        offset: f64,
        width: f64,
        subtitle_width: f64,
    ) -> Self {
        Self::with_subtitle_lines(font_size, 1, subtitle_font_size, 1, offset, width, subtitle_width)
    }

    /// `with_subtitle`, with either block wrapped onto multiple lines: each
    /// extra line advances by its own `fontSize + 2` (see `with_lines`),
    /// additive across the title and subtitle blocks (oracle-probed).
    pub fn with_subtitle_lines(
        font_size: f64,
        title_lines: usize,
        subtitle_font_size: f64,
        subtitle_lines: usize,
        offset: f64,
        width: f64,
        subtitle_width: f64,
    ) -> Self {
        let q = |fs: f64| (0.79 * fs).round() - (0.8 * fs).round();
        let extra = (title_lines.max(1) - 1) as f64
            * (font_size + Self::LINE_ADVANCE_EXTRA)
            + (subtitle_lines.max(1) - 1) as f64
                * (subtitle_font_size + Self::LINE_ADVANCE_EXTRA);
        TitleInk {
            ink_h: font_size + VEGA_SUBTITLE_PADDING + subtitle_font_size
                + q(subtitle_font_size)
                - q(font_size)
                + extra,
            dy_quirk: q(font_size),
            offset,
            width: width.max(subtitle_width),
        }
    }
}

/// The negotiated plot rectangle: margins are relative to the padded view
/// (`plot_left = padding.left + left`).
#[derive(Debug, PartialEq)]
pub struct FitMargins {
    pub left: f64,
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
    pub plot_w: f64,
    pub plot_h: f64,
    /// Title group position in plot coordinates (anchor start / frame
    /// bounds: x at the view ink's left edge in the FINAL layout pass, y
    /// stacked above it), already clamped to `padding.left` the way
    /// production's `fix_title_alignment` post-pass does — where the title
    /// must actually be drawn. `None` for an untitled chart.
    pub title_pos: Option<(f64, f64)>,
    /// `left` before the `.ceil()` — the drawn ink's own raw `-b.x1`, never
    /// negative. Real Vega's cell bounds are the union of the group rect
    /// `[0, width]`, the mark ink, and the zero/baseline rule, ceiled ONCE
    /// on whichever side a caller needs; this is that same union's LEFT
    /// side, unceiled, so a caller measuring the RIGHT-side reach past
    /// `plot_w` (real Vega ceils `bounds.x2` once, never a hand-rolled
    /// mark-only term) can read this field instead of re-deriving one.
    /// NOT symmetric in general: this closure also measures a left value
    /// axis (its own gutter joins the SAME left union), a title, and a
    /// legend, none of which have a right-side counterpart — a caller may
    /// only treat `raw_left` as the right reach too on a shape with NO
    /// left value axis, title, or legend in its own `ink` closure (every
    /// admitted line facet shape, and a bar facet's right-axis arm; a bar
    /// facet's own left-axis shape refuses by name for exactly this
    /// reason — `bar.rs`'s own `axis_y.position: "left"` refusal).
    pub raw_left: f64,
    /// The plot rect vl-convert's scenegraph reports: `vegaToScenegraph`
    /// makes only [`SCENEGRAPH_PADDING_PASSES`] of the SVG's three padding
    /// passes, so on a sequence that has not converged it is one pass behind
    /// `plot_w`/`plot_h`. `legend_align.py` slides a legend against this
    /// rect (it reads the scenegraph), not against the drawn one.
    pub scenegraph_plot: (f64, f64),
}

/// Vega's autosize-fit resize loop is BOUNDED — it does not run to a fixed
/// point. Each `runAsync()` drains exactly one generation of the dataflow's
/// postrun queue (`evaluate` snapshots `_postrun`, rebinds the field, then
/// iterates the snapshot, so callbacks queued while draining wait for the
/// next run), and one generation is one padding computation: `viewSizeLayout`
/// measures the view ink and calls `_resizeView`, whose queued callback sets
/// `_autosize = 0` and re-runs the marks at the negotiated size — that layout
/// skips the autosize block — before `autosize.resize` re-arms
/// `view.resize()` for the run after. vl-convert's `vegaToSvg` makes three
/// such calls (run → the `geo_interval_init_tick` poke → run → `toSVG()`,
/// whose headless render awaits a third), so the THIRD pass's margins and
/// origin are what renders, and the layout it triggers — at the third pass's
/// plot size — is the drawn scenegraph.
///
/// `vegaToScenegraph` makes only two of those calls, so its `origin` is one
/// pass behind the SVG whenever the sequence has not converged: probe the
/// rendered SVG, not the scenegraph, on a diverging chart.
///
/// Shared with the legacy scatter arm's top-margin solve, which runs the same
/// passes over the one margin it derives from tick-label ink.
pub(crate) const AUTOSIZE_PADDING_PASSES: usize = 3;
/// The passes `vegaToScenegraph` runs (see above): `FitMargins::scenegraph_plot`.
const SCENEGRAPH_PADDING_PASSES: usize = 2;

/// Vega `titleLayout`: the title group lands at
/// `y = viewBounds.y1 − tempBounds.height() − offset`, anchored
/// (`anchor: start`, `frame: bounds`) at `x = viewBounds.x1`.
fn title_layout(b: &Bounds, t: &TitleInk) -> (f64, f64) {
    (b.x1, b.y1 - t.ink_h - t.offset)
}

/// Negotiate the plot rect the way `viewSizeLayout` does for
/// `autosize: {type: fit, contains: padding, resize: true}`.
///
/// `view_w`/`view_h` are the outer canvas; `pad_*` the four-sided padding;
/// `ink` yields the view ink union (title excluded) for a candidate plot
/// size, in plot coordinates — it must include the plot rect
/// `(0,0,pw,ph)` itself, which `fit_plot` seeds before calling. `title`
/// yields the title contribution (None for an untitled chart).
pub fn fit_plot(
    view_w: f64,
    view_h: f64,
    pad: (f64, f64, f64, f64), // (left, right, top, bottom)
    mut ink: impl FnMut(f64, f64, &mut Bounds),
    title: Option<&TitleInk>,
) -> FitMargins {
    let (pad_l, pad_r, pad_t, pad_b) = pad;
    let avail_w = view_w - pad_l - pad_r;
    let avail_h = view_h - pad_t - pad_b;
    // Vega's first layout pass runs at the spec width/height signals — the
    // full outer dims — before the first resize.
    let mut pw = view_w;
    let mut ph = view_h;
    let (mut left, mut top, mut right, mut bottom) = (0.0, 0.0, 0.0, 0.0);
    let mut raw_left = 0.0;
    let mut scenegraph_plot = (pw, ph);
    for pass in 1..=AUTOSIZE_PADDING_PASSES {
        let mut b = Bounds::new(0.0, 0.0, pw, ph);
        ink(pw, ph, &mut b);
        if let Some(t) = title {
            let (tx, ty) = title_layout(&b, t);
            b.union(&Bounds::new(tx, ty + t.dy_quirk, tx + t.width, ty + t.dy_quirk + t.ink_h));
        }
        raw_left = (-b.x1).max(0.0);
        left = raw_left.ceil();
        top = (-b.y1).ceil().max(0.0);
        right = (b.x2 - pw).ceil().max(0.0);
        bottom = (b.y2 - ph).ceil().max(0.0);
        pw = (avail_w - left - right).max(0.0);
        ph = (avail_h - top - bottom).max(0.0);
        if pass == SCENEGRAPH_PADDING_PASSES {
            scenegraph_plot = (pw, ph);
        }
    }
    // The layout `_resizeView` triggers inside the last padding pass runs
    // with `_autosize = 0`: it re-places every mark at the negotiated size
    // and never revisits the margins. This is the DRAWN layout, so it runs
    // unconditionally — callers whose `ink` closure records a decision as it
    // measures (a legend x, which tick labels survive) must record it at the
    // size the chart is drawn at, not at the size pass 3 measured.
    let mut drawn = Bounds::new(0.0, 0.0, pw, ph);
    ink(pw, ph, &mut drawn);
    // The title group is placed in that same layout, so its x is the drawn
    // ink's `x1` — on a sequence that has not converged that is left of the
    // `x1` whose ceil fixed `left`, and the title hangs outside the padded
    // view. Production then post-processes the rendered SVG with
    // `title_overflow.py::fix_title_alignment`: whenever `padding.left` is
    // positive, a title group whose absolute x (`main_group_x +
    // title_group_x`) sits below it is moved right to exactly
    // `padding.left`. Absolute x here is `pad_l + left + tx`.
    let title_pos = title.map(|t| {
        let (tx, ty) = title_layout(&drawn, t);
        (if pad_l > 0.0 { tx.max(-left) } else { tx }, ty)
    });
    FitMargins { left, top, right, bottom, plot_w: pw, plot_h: ph, title_pos, raw_left, scenegraph_plot }
}

/// Lay out `autosize: {type: pad, contains: padding}` — the mode production
/// switches a chart to when it attaches a support-table strip. The plot rect
/// is the spec's own `width`/`height` verbatim and never negotiated, so a
/// single layout pass settles it: each margin is the ceil'd ink overhang past
/// the plot on that side, and the canvas is `padding + margins + plot`.
///
/// `ink` and `title` carry the same contract as [`fit_plot`]'s. Production's
/// `fix_title_alignment` clamp has nothing to bite on here: it exists because
/// a fit's DRAWN bounds can sit left of the ones that fixed the margins two
/// passes earlier, and a single-pass layout places the title against the very
/// bounds its own margin was ceil'd from — `pad_l + left + title_x` is
/// `pad_l + frac` for every input.
pub fn pad_plot(
    pw: f64,
    ph: f64,
    mut ink: impl FnMut(f64, f64, &mut Bounds),
    title: Option<&TitleInk>,
) -> FitMargins {
    let mut b = Bounds::new(0.0, 0.0, pw, ph);
    ink(pw, ph, &mut b);
    let placed = title.map(|t| (t, title_layout(&b, t)));
    if let Some((t, (tx, ty))) = placed {
        b.union(&Bounds::new(tx, ty + t.dy_quirk, tx + t.width, ty + t.dy_quirk + t.ink_h));
    }
    let raw_left = (-b.x1).max(0.0);
    FitMargins {
        left: raw_left.ceil(),
        top: (-b.y1).ceil().max(0.0),
        right: (b.x2 - pw).ceil().max(0.0),
        bottom: (b.y2 - ph).ceil().max(0.0),
        plot_w: pw,
        plot_h: ph,
        title_pos: placed.map(|(_, pos)| pos),
        raw_left,
        scenegraph_plot: (pw, ph),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shipped_fonts() -> crate::text::FontBook {
        crate::text::FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load")
    }

    fn tilting_labels(weight: serde_json::Value) -> crate::common::AxisLabels {
        serde_json::from_value(serde_json::json!({
            "font": {"family": "'Inter Variable', Inter", "size": 11.0, "weight": weight},
            "overlap": {"skip": true, "tilt": true},
            "tilt_increments": [0.0, -30.0, -45.0, -60.0, -90.0],
        }))
        .expect("axis labels parse")
    }

    /// The tilt resolver pads each label by one word space; an authored angle
    /// measures them bare. A bottom strip's axis gap reads the same widths.
    #[test]
    fn category_label_widths_pad_by_a_word_space_only_when_asked() {
        let fonts = shipped_fonts();
        let labels = tilting_labels(serde_json::json!("400"));
        let cats = vec!["North".to_string()];
        let bare = category_label_widths(&labels, &cats, &fonts, 11.0, false).unwrap()[0];
        let padded = category_label_widths(&labels, &cats, &fonts, 11.0, true).unwrap()[0];
        let space = labels.font.measure_weighted(&fonts, " ", 11.0).unwrap();
        assert!(space > 0.0 && ((padded - bare) - space).abs() < 1e-9, "{bare} {padded} {space}");
    }

    /// `_label_overlap.py:907`: the ordinal crowding test measures the labels at
    /// the weight they paint at, so a band that holds the regular labels flat
    /// tilts the bold ones.
    #[test]
    fn a_bold_category_axis_tilts_in_a_band_that_holds_regular_labels_flat() {
        let fonts = shipped_fonts();
        let cats: Vec<String> =
            ["Northeast Territory", "Southwest Territory", "Central Territory"].map(String::from).to_vec();
        let band_for = |weight: f32| {
            let space = fonts.measure_at_weight(" ", "Inter", 11.0, Some(weight)).unwrap();
            let widths: Vec<f64> = cats
                .iter()
                .map(|c| fonts.measure_at_weight(c, "Inter", 11.0, Some(weight)).unwrap() + space)
                .collect();
            max_pair(&widths)
        };
        let (regular_band, bold_band) = (band_for(400.0), band_for(800.0));
        assert!(bold_band > regular_band + 1.0, "{regular_band} vs {bold_band}");
        let chart_width = (regular_band + bold_band) / 2.0 * cats.len() as f64;
        let angle = |weight| {
            category_label_layout(&tilting_labels(weight), &cats, chart_width, Some(1.0), &fonts, 11.0).unwrap().angle
        };
        assert_eq!(angle(serde_json::json!("400")), 0.0);
        assert_ne!(angle(serde_json::json!("800")), 0.0);
        assert_eq!(angle(serde_json::json!("semibold")), 0.0, "an unparseable weight paints at the default");
    }

    #[test]
    fn parity_hides_every_other_label_until_the_gap_clears() {
        let row = |n: usize, pitch: f64, w: f64| -> Vec<Bounds> {
            (0..n).map(|i| Bounds::new(i as f64 * pitch - w / 2.0, 0.0, i as f64 * pitch + w / 2.0, 11.0)).collect()
        };
        // 14 labels 20px wide at a 25px pitch: gap 5 < 16 → odd ones go;
        // the survivors' 50px pitch clears 16.
        let v = parity_visible(&row(14, 25.0, 20.0), 16.0);
        assert_eq!(v.iter().filter(|&&b| b).count(), 7);
        assert!(v[0] && !v[1] && v[2] && !v[13]);
        // Two rounds when one halving still collides.
        let v = parity_visible(&row(19, 28.0, 41.0), 16.0);
        assert_eq!((0..19).filter(|&i| v[i]).collect::<Vec<_>>(), vec![0, 4, 8, 12, 16]);
        // Fewer than three survivors with the end label hidden: the end
        // label replaces the last survivor.
        let v = parity_visible(&row(4, 10.0, 20.0), 200.0);
        assert_eq!(v, vec![true, false, false, true]);
        // No collision: untouched.
        assert!(parity_visible(&row(5, 100.0, 20.0), 16.0).iter().all(|&b| b));
        // The predicate is strict (`sep > gap`) on the raw box: a gap
        // exactly at the separation clears, half a pixel less collides.
        assert!(parity_visible(&row(5, 36.0, 20.0), 16.0).iter().all(|&b| b));
        assert!(!parity_visible(&row(5, 35.5, 20.0), 16.0)[1]);
        assert!(parity_visible(&[], 16.0).is_empty());
    }

    #[test]
    fn greedy_keeps_the_first_label_and_tests_each_candidate_against_the_last_kept() {
        // Oracle: charts/axis-scales_0 `world_population_log`'s reference
        // SVG — the right-orient y-axis label y positions (11px font,
        // labels.min_gap 16), text-anchor "end" so every box shares its
        // right edge (x2); the reference hides "400M"/"4G" (a candidate
        // 21.36px from the last-kept box) and keeps "500M"/"5G" (26.93px
        // from that SAME last-kept box, not from "400M").
        let ys = [
            345.0,
            293.52387074145935,
            263.4122654429379,
            242.04774148291864,
            225.4761292585408,
            174.00000000000014,
            122.5238707414592,
            92.41226544293774,
            71.04774148291852,
            54.476129258540944,
            3.0,
        ];
        let ink: Vec<Bounds> =
            ys.iter().map(|&y| text_ink(0.0, y, 0.0, 11.0, "right", "middle")).collect();
        let visible = greedy_visible(&ink, 16.0);
        assert_eq!(
            visible,
            vec![true, true, true, false, true, true, true, true, false, true, true]
        );
        assert!(greedy_visible(&[], 16.0).is_empty());
        // No collision: untouched, first item always kept.
        let row = |n: usize, pitch: f64| -> Vec<Bounds> {
            (0..n).map(|i| Bounds::new(0.0, i as f64 * pitch, 0.0, i as f64 * pitch)).collect()
        };
        assert!(greedy_visible(&row(5, 100.0), 16.0).iter().all(|&b| b));
    }

    /// vega-view-transforms' `Overlap.js`: the reduce (parity OR greedy)
    /// and the last-item rescue both live INSIDE a single guard —
    /// `items.length >= 3 && hasOverlap(items, sep)` — so with fewer than
    /// three candidates to begin with, overlap removal never engages at
    /// all, regardless of how badly the boxes collide.
    #[test]
    fn greedy_never_hides_anything_when_fewer_than_three_candidates() {
        let overlapping = vec![
            Bounds::new(0.0, 0.0, 20.0, 11.0),
            Bounds::new(5.0, 0.0, 25.0, 11.0),
        ];
        assert_eq!(greedy_visible(&overlapping, 16.0), vec![true, true]);
    }

    /// `Overlap.js`'s rescue: `if (items.length < 3 && !peek(source).opacity)
    /// { if (items.length > 1) peek(items).opacity = 0; peek(source).opacity = 1; }`
    /// — after the greedy pass, if fewer than 3 candidates survived AND the
    /// true last candidate is among the casualties, the axis's own last
    /// label is always rescued, at the cost of the survivor pass's own
    /// last-kept slot (never the first, which the pass never drops).
    #[test]
    fn greedy_rescues_the_last_label_when_fewer_than_three_survive() {
        // 5 boxes, width 20, height 11, sep 16. Centers 0, 10, 50, 55, 70:
        // 0 kept (first); 10 overlaps 0 (hidden); 50 clears 0 by 30px
        // (kept, becomes last-kept); 55 overlaps 50 (hidden); 70 overlaps
        // 50 by 0px (hidden by a plain forward pass) — naive kept = [0,
        // 50], 2 survivors, the true last candidate (70) among them.
        let at = |c: f64| Bounds::new(c - 10.0, 0.0, c + 10.0, 11.0);
        let ink = vec![at(0.0), at(10.0), at(50.0), at(55.0), at(70.0)];
        assert_eq!(greedy_visible(&ink, 16.0), vec![true, false, false, false, true]);
    }

    /// The rescue is conditional on `items.length < 3` — when the plain
    /// pass already keeps 3 or more, the true last candidate is left
    /// exactly where the pass put it, hidden or not (matches the real
    /// `axis-scales_0` reference at small plot heights: greedy hides "10G"
    /// itself when 3 OTHER mantissas already survive).
    #[test]
    fn greedy_does_not_rescue_when_three_or_more_already_survive() {
        // Same shape as the rescue case, extended with a 6th box (100)
        // that clears the last-kept (50) by 30px — becomes the new
        // last-kept — and a 7th (105) that overlaps it and is the true
        // last candidate: naive kept = [0, 50, 100], 3 survivors already.
        let at = |c: f64| Bounds::new(c - 10.0, 0.0, c + 10.0, 11.0);
        let ink = vec![at(0.0), at(10.0), at(50.0), at(55.0), at(100.0), at(105.0)];
        assert_eq!(
            greedy_visible(&ink, 16.0),
            vec![true, false, true, false, true, false]
        );
    }

    // Oracle: charts/bar-charts_0 `product_revenue_bars` (bc0), pinned
    // against `vl_convert.vega_to_scenegraph` on the production-compiled
    // spec: origin [70, 46], plot 512×353 on a 600×432 canvas with 16px
    // padding. Contributors measured from the scenegraph:
    // - left band axis: labels at x = −52.474 (labelPadding = max label
    //   width 48.474 + 4 breathing), axis offset 1;
    // - bottom value axis: labels at y = 6 (labelPadding), 11px, baseline
    //   top; last label "80k" (~20.9px wide) centered at x = pw·80000/81436.32;
    // - zero rule: vertical at x = 0 spanning the plot, strokeWidth 2
    //   (boundStroke expands by the FULL stroke width);
    // - title: 18px, offset 10.
    fn bc0_ink(pw: f64, ph: f64, b: &mut Bounds) {
        // left band axis (translate x = −offset)
        let mut ax = Bounds::new(-52.474, 0.0, 0.0, ph);
        ax.x1 -= 1.0;
        b.union(&ax);
        // bottom value axis: worst label ink
        let dmax = 81436.32;
        for (tv, w) in [(0.0, 6.1), (20000.0, 20.9), (40000.0, 20.9), (60000.0, 20.9), (80000.0, 20.9)] {
            let x = pw * tv / dmax;
            let align = if tv == 0.0 { "left" } else { "center" };
            b.union(&text_ink(x, ph + 6.0, w, 11.0, align, "top"));
        }
        // zero rule
        b.union(&Bounds::new(0.0, 0.0, 0.0, ph).expand(2.0));
    }

    #[test]
    fn bc0_margins_match_vega_scenegraph_origin() {
        let title = TitleInk::single(18.0, 10.0, 160.0);
        let m = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&title));
        assert_eq!(
            (m.left, m.top, m.right, m.bottom, m.plot_w, m.plot_h),
            (54.0, 30.0, 2.0, 17.0, 512.0, 353.0)
        );
        // Title group lands at viewBounds.y1 − ink − offset = −2 − 18 − 10.
        let (tx, ty) = m.title_pos.expect("titled chart");
        assert_eq!(ty, -30.0);
        assert_eq!(tx, -53.474);
    }

    #[test]
    fn untitled_chart_still_carries_the_zero_rule_overhang() {
        // Ablation oracle: bc0 with the title removed → origin [70, 18]
        // (top margin 2 — the zero rule's full-strokeWidth expansion).
        let m = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, None);
        assert_eq!(m.left, 54.0);
        assert_eq!(m.top, 2.0);
    }

    /// A title's width reaches the right margin only in the last pixel
    /// before its cap.
    ///
    /// `TitleInk::width`'s doc says a long title can push the right margin,
    /// and geometrically it can: the group anchors at `viewBounds.x1`, so
    /// its box ends at `x1 + width`. But callers cap `width` at the compiled
    /// title `limit` — the padded view width `avail_w`. With
    /// `pw = avail_w − left − right` and `frac = x1 + left ∈ [0, 1)` (the
    /// slack the left margin's `ceil` leaves), the title's contribution is
    /// `ceil(frac + width − avail_w + right)`, which equals `right` for any
    /// `width ≤ avail_w − 1`. So every title but one within a pixel of its
    /// own cap is inert on the horizontal.
    ///
    /// Read that as making the title FAMILY inert too and you have it
    /// exactly backwards, which is the mistake this paragraph used to make.
    /// Wide cards do hide it — two families 57px apart render byte-identical
    /// above ~375px. But the wrong family is by construction the WIDER one,
    /// so as the card narrows it reaches the cap FIRST, while the correct
    /// one is still under it and inert. Between the two crossings the
    /// renderer is comparing a clamped width against an unclamped one:
    /// `bar.rs`'s `tiered_title_font_family_measures_the_band_too` measures
    /// 7px of plot at 350px, in a band that runs (317.6, 375.2) for that
    /// title. So the cap is what makes the wrong family visible, not what
    /// hides it.
    #[test]
    fn a_capped_title_reaches_the_right_margin_only_at_its_cap() {
        let avail_w = 600.0 - 32.0;
        let bare = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, None);
        for width in [0.0, 160.0, 400.0, avail_w - 1.0] {
            let title = TitleInk::single(18.0, 10.0, width);
            let m = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&title));
            assert_eq!(m.right, bare.right, "title width {width} moved the right margin");
            assert_eq!(m.plot_w, bare.plot_w, "title width {width} moved the plot");
        }
        // At the cap the `frac` slack tips it over, and — since each pass
        // shrinks `pw` by exactly the pixel it just added — it does not
        // converge; Vega's three-pass bound is what stops it.
        // Bar reaches this on both fit arms, neither of which gates on an
        // overflowing title, so it is a live shape and not just a boundary:
        // callers pin `width` at exactly the cap with `.min(avail_w)`,
        // where Vega truncates the title and bounds the TRUNCATED string,
        // landing strictly below. The clamp is the modeling artifact.
        let at_cap = TitleInk::single(18.0, 10.0, avail_w);
        let m = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&at_cap));
        assert!(m.right > bare.right, "a title at its cap reaches the margin: {m:?}");
    }

    #[test]
    fn title_dy_quirk_shifts_top_margin_at_quirky_font_sizes() {
        // Oracle: bc0 with title fontSize swept through vl_convert —
        // fs=12 → origin top 41 (25 above padding: 12 + 10 + 2 rule + 1
        // quirk), fs=14 → 42 (12+10+2+0... 14+10+2 = 26).
        for (fs, want_top) in [(10.0, 22.0), (12.0, 25.0), (14.0, 26.0), (18.0, 30.0), (30.0, 42.0)] {
            let title = TitleInk::single(fs, 10.0, 160.0);
            let m = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&title));
            assert_eq!(m.top, want_top, "fontSize {fs}");
        }
    }

    #[test]
    fn subtitle_stacks_with_both_quirks() {
        // Oracle: scenegraph origin deltas (subtitled − single, same title
        // fontSize) on a captured line spec with offset 10:
        //   t=14 s=12 → 14   t=12 s=11 → 15   t=17 s=17 → 20   t=18 s=11 → 14
        // i.e. delta = 3 + s + q(s) − q(t).
        for (t, s, want_delta) in
            [(14.0, 12.0, 14.0), (12.0, 11.0, 15.0), (17.0, 17.0, 20.0), (18.0, 11.0, 14.0)]
        {
            let single = TitleInk::single(t, 10.0, 160.0);
            let sub = TitleInk::with_subtitle(t, s, 10.0, 160.0, 120.0);
            let m1 = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&single));
            let m2 = fit_plot(600.0, 432.0, (16.0, 16.0, 16.0, 16.0), bc0_ink, Some(&sub));
            assert_eq!(m2.top - m1.top, want_delta, "title {t} subtitle {s}");
        }
    }

    #[test]
    fn extra_lines_advance_by_font_size_plus_two() {
        // Oracle: scenegraph origin probes on a captured wheel spec
        // (t=14, s=11, offset 10): 1-line title no sub → chrome 24; 2-line
        // title → 40 (+16 = t+2); 2-line title + 1-line sub → 54; subtitle
        // lines 1→2→3 at s=11 → 38/51/64 (+13 = s+2 each); s=13 2-line →
        // +15. Additive across the two blocks.
        let one = TitleInk::with_lines(14.0, 1, 10.0, 160.0);
        let two = TitleInk::with_lines(14.0, 2, 10.0, 160.0);
        assert_eq!(two.ink_h - one.ink_h, 16.0);

        let s1 = TitleInk::with_subtitle_lines(14.0, 1, 11.0, 1, 10.0, 160.0, 120.0);
        let s2 = TitleInk::with_subtitle_lines(14.0, 1, 11.0, 2, 10.0, 160.0, 120.0);
        let s3 = TitleInk::with_subtitle_lines(14.0, 1, 11.0, 3, 10.0, 160.0, 120.0);
        assert_eq!(s2.ink_h - s1.ink_h, 13.0);
        assert_eq!(s3.ink_h - s2.ink_h, 13.0);
        let s13 = TitleInk::with_subtitle_lines(14.0, 1, 13.0, 2, 10.0, 160.0, 120.0);
        let s13_one = TitleInk::with_subtitle_lines(14.0, 1, 13.0, 1, 10.0, 160.0, 120.0);
        assert_eq!(s13.ink_h - s13_one.ink_h, 15.0);
        // Both blocks wrapped: deltas stack.
        let both = TitleInk::with_subtitle_lines(14.0, 2, 11.0, 2, 10.0, 160.0, 120.0);
        assert_eq!(both.ink_h - s1.ink_h, 16.0 + 13.0);
        // One line of each is with_subtitle exactly.
        let legacy = TitleInk::with_subtitle(14.0, 11.0, 10.0, 160.0, 120.0);
        assert_eq!(s1.ink_h, legacy.ink_h);
        assert_eq!(s1.dy_quirk, legacy.dy_quirk);
    }

    #[test]
    fn rotated_bounds_take_vega_s_rotated_corner_box() {
        // A 60×11 top-baseline box anchored right at (100, 6), turned a
        // quarter clockwise-negative (Vega 270°): it hangs below the anchor
        // and spans one font height across it.
        let b = text_ink(100.0, 6.0, 60.0, 11.0, "right", "middle").rotate(270.0, 100.0, 6.0);
        assert!((b.x1 - 94.0).abs() < 1e-9 && (b.x2 - 105.0).abs() < 1e-9, "{b:?}");
        assert!((b.y1 - 6.0).abs() < 1e-9 && (b.y2 - 66.0).abs() < 1e-9, "{b:?}");
        // −30° (330°): a right/top box reaches w·cos left and h·sin+… down.
        let b = text_ink(0.0, 0.0, 60.0, 11.0, "right", "top").rotate(330.0, 0.0, 0.0);
        assert!((b.x1 - (-60.0 * 0.75_f64.sqrt())).abs() < 1e-9, "{b:?}");
        assert!((b.y2 - (60.0 * 0.5 + 11.0 * 0.75_f64.sqrt())).abs() < 1e-9, "{b:?}");
    }

    #[test]
    fn vl_x_label_anchor_follows_the_angle_quadrants() {
        assert_eq!(normalize_angle(-90.0), 270.0);
        assert_eq!(vl_x_label_anchor(0.0, true), ("center", "top"));
        assert_eq!(vl_x_label_anchor(330.0, true), ("right", "top"));
        assert_eq!(vl_x_label_anchor(315.0, true), ("right", "top"));
        assert_eq!(vl_x_label_anchor(300.0, true), ("right", "middle"));
        assert_eq!(vl_x_label_anchor(270.0, true), ("right", "middle"));
        assert_eq!(vl_x_label_anchor(45.0, true), ("left", "top"));
        assert_eq!(vl_x_label_anchor(270.0, false), ("left", "middle"));
    }

    #[test]
    fn baseline_shift_matches_vega_rounding() {
        assert_eq!(baseline_shift("top", 11.0), 9.0); // round(8.69)
        assert_eq!(baseline_shift("top", 18.0), 14.0); // round(14.22)
        assert_eq!(baseline_shift("middle", 11.0), 3.0); // round(3.3)
        assert_eq!(baseline_shift("bottom", 11.0), -2.0); // round(-2.31)
        assert_eq!(baseline_shift("alphabetic", 11.0), 0.0);
    }


    /// A wheel's slice-label ink is placed by the compiled spec's own
    /// transforms — `x = width/2 + sin(mid)·(min(w,h)/2·0.9 + 6)` — so it
    /// MOVES with the plot size and the fit sequence need not converge.
    /// `mid`, the label widths (Vega's own text measurement, bisected
    /// through a text mark's `limit`) and the arc stroke come off the
    /// production-compiled spec.
    struct WheelLabel {
        mid: f64,
        width: f64,
        lines: f64,
        right: bool,
        top: bool,
    }

    fn wheel_ink(labels: &[WheelLabel], stroke_w: f64, line_h: f64, pw: f64, ph: f64, b: &mut Bounds) {
        let r = pw.min(ph) / 2.0 * 0.9;
        b.union(
            &Bounds::new(pw / 2.0 - r, ph / 2.0 - r, pw / 2.0 + r, ph / 2.0 + r).expand(stroke_w / 2.0),
        );
        for l in labels {
            let lx = pw / 2.0 + l.mid.sin() * (r + 6.0);
            let ly = ph / 2.0 - l.mid.cos() * (r + 6.0) - if l.top { l.lines * line_h } else { 0.0 };
            let (x1, x2) = if l.right { (lx, lx + l.width) } else { (lx - l.width, lx) };
            b.union(&Bounds::new(x1, ly, x2, ly + l.lines * line_h));
        }
    }

    /// Oracle: `fixtures/sector/width-tier/medium_donut_a` — 564×432, padding
    /// 16, four labeled slices. Simulated pass by pass against the committed
    /// reference render (origin 16,23 / plot 499×393):
    ///
    /// ```text
    /// pass0 at 564×432 → L0 T4 R19 → 513×396
    /// pass1 at 513×396 → L0 T6 R29 → 503×394
    /// pass2 at 503×394 → L0 T7 R33 → 499×393   ← what renders
    /// pass3 → 498×393, pass4 → 497×393, fixed point 497×393
    /// ```
    ///
    /// The right overhang GROWS as the plot shrinks (the label anchor moves
    /// in by half the shrink while the wheel's own edge moves in by less), so
    /// the sequence has no fixed point within a pixel and the pass bound is
    /// the whole answer.
    #[test]
    fn fit_stops_at_vegas_third_padding_pass() {
        let labels = [
            WheelLabel { mid: 1.3865865806469633, width: 103.55772972106934, lines: 2.0, right: true, top: true },
            WheelLabel { mid: 3.6670058331159354, width: 112.04109191894531, lines: 2.0, right: false, top: false },
            WheelLabel { mid: 5.122348773133823, width: 62.82920837402344, lines: 2.0, right: false, top: true },
            WheelLabel { mid: 5.983522174254644, width: 100.76866149902344, lines: 2.0, right: false, top: true },
        ];
        let m = fit_plot(
            564.0,
            432.0,
            (16.0, 16.0, 16.0, 16.0),
            |pw, ph, b| wheel_ink(&labels, 3.0, 14.0, pw, ph, b),
            None,
        );
        assert_eq!(
            (m.left, m.top, m.right, m.bottom, m.plot_w, m.plot_h),
            (0.0, 7.0, 33.0, 0.0, 499.0, 393.0),
            "the third padding pass renders, not the fixed point (497×393)"
        );
    }

    /// Oracle: `charts/types_5/donut_example` — 554×432, padding 16, three
    /// labeled slices, an 18px title (`TitleInk::single`, offset 10, Vega
    /// width 190.204). Its 3-pass margins are L17 T28 (reference origin
    /// 33,44 / plot 460×372) and the fit diverges leftward, so the FINAL
    /// layout at 460×372 puts the title's ink `x1` at −24.483 — absolute
    /// 8.517, which `vl_convert.vegalite_to_svg` reproduces to the pixel
    /// (8.52) on the raw Vega render. Production then post-processes the SVG
    /// with `fix_title_alignment`, which moves any title whose absolute x is
    /// below `padding.left` out to exactly `padding.left`; the committed
    /// reference carries the result as `translate(-17.00,…)`.
    #[test]
    fn a_title_left_of_the_padding_clamps_back_to_it() {
        let m = fit_plot(554.0, 432.0, (16.0, 16.0, 16.0, 16.0), donut_example_ink, Some(&donut_example_title()));
        assert_eq!((m.left, m.top, m.plot_w, m.plot_h), (17.0, 28.0, 460.0, 372.0));
        let (tx, _) = m.title_pos.expect("titled wheel");
        assert_eq!(tx, -17.0, "clamped to −left, i.e. absolute padding.left");
        assert_eq!(16.0 + m.left + tx, 16.0, "the reference's title x");
    }

    /// The title's x is the DRAWN layout's ink `x1`, not the x1 of the pass
    /// that fixed the margins. Isolated on the same wheel with no left
    /// padding, where the clamp is gated off and cannot mask the difference:
    /// the layout at the final 508×372 puts `x1` at −0.483 where the pass
    /// that produced `left = 1` measured −0.983.
    #[test]
    fn an_unclamped_title_takes_the_final_layout_pass_ink() {
        let m = fit_plot(554.0, 432.0, (0.0, 16.0, 16.0, 16.0), donut_example_ink, Some(&donut_example_title()));
        assert_eq!((m.left, m.plot_w), (1.0, 508.0));
        let (tx, _) = m.title_pos.expect("titled wheel");
        assert!((tx - -0.48285954529922037).abs() < 1e-9, "final-pass ink x1, got {tx}");
    }

    /// `converters/chart.py` runs `fix_title_alignment` only
    /// `if padding_left > 0`, so a chart with no left padding keeps whatever
    /// x Vega gave its title — even a title hanging off the left edge of the
    /// canvas. The same wheel on a 422×300 card diverges far enough to make
    /// the gate observable: the drawn ink `x1` is −54.607 against `left = 44`,
    /// so a clamp that ignored the gate would move the title 10.6px right, to
    /// a padding.left that production never applies.
    #[test]
    fn no_left_padding_means_no_clamp() {
        let m = fit_plot(422.0, 300.0, (0.0, 16.0, 16.0, 16.0), donut_example_ink, Some(&donut_example_title()));
        assert_eq!((m.left, m.plot_w, m.plot_h), (44.0, 303.0, 237.0));
        let (tx, _) = m.title_pos.expect("titled wheel");
        assert!(tx < -m.left, "ungated, the clamp would pin this at −44: got {tx}");
        assert!((tx - -54.60693236689198).abs() < 1e-9, "drawn ink x1, got {tx}");
    }

    fn donut_example_title() -> TitleInk {
        TitleInk::single(18.0, 10.0, 190.20423889160156)
    }

    fn donut_example_ink(pw: f64, ph: f64, b: &mut Bounds) {
        let labels = [
            WheelLabel { mid: 1.512932121073514, width: 108.41803550720215, lines: 2.0, right: true, top: true },
            WheelLabel { mid: 4.062765420357249, width: 116.40243530273438, lines: 2.0, right: false, top: false },
            WheelLabel { mid: 5.691425952873528, width: 67.57335662841797, lines: 2.0, right: false, top: true },
        ];
        wheel_ink(&labels, 3.0, 14.0, pw, ph, b);
    }

    #[test]
    fn text_ink_top_baseline_box() {
        // 11px top-baseline label at y=6: dy = 9 − round(8.8) = 0 →
        // ink spans y 6..17 (the measured 17px bottom-axis extent).
        let b = text_ink(100.0, 6.0, 20.0, 11.0, "center", "top");
        assert_eq!((b.y1, b.y2), (6.0, 17.0));
        assert_eq!((b.x1, b.x2), (90.0, 110.0));
    }

    /// `vegaToScenegraph` makes two of the SVG's three padding passes: with
    /// an overhang of `ceil(pw / 10)` the passes run 90, 91, 90 (view 100),
    /// so the scenegraph's plot is the second pass's 91 while the drawn one
    /// is 90; a size-independent overhang converges and the two agree.
    #[test]
    fn the_scenegraph_plot_is_the_second_padding_pass() {
        let moving = fit_plot(100.0, 50.0, (0.0, 0.0, 0.0, 0.0), |pw, _, b| b.union(&Bounds::new(0.0, 0.0, pw + (pw / 10.0).ceil(), 50.0)), None);
        assert_eq!((moving.plot_w, moving.scenegraph_plot.0), (90.0, 91.0));
        let fixed = fit_plot(100.0, 50.0, (0.0, 0.0, 0.0, 0.0), |pw, _, b| b.union(&Bounds::new(0.0, 0.0, pw + 10.0, 50.0)), None);
        assert_eq!((fixed.plot_w, fixed.scenegraph_plot), (90.0, (90.0, 50.0)));
    }
}
