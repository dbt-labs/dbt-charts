//! Pattern fills: a [`Paint`] is a flat color or a pattern tile, and a [`PatternPlan`] hands
//! one out per series slot or ramp step and collects the tiles a chart drew into its `<defs>`.
//!
//! Tiles are `userSpaceOnUse`, sized in multiples of 4 px, and never carry an
//! `objectBoundingBox` or a `patternTransform`, so the period stays integral at the device
//! scales a chart is shown at.

pub mod library;

use dbt_charts_color::{parse_css_color, rgb01_to_hex};
use library::{Library, Mark, Tile};
use scene::{Attrs, Node, PathData, Role, Shape};
use serde::Deserialize;
use diagnostic::{codes, Diagnostic};
use serde_json::json;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

/// A mark with fewer repeats than this across its short side draws a flat tint instead.
const MIN_REPEATS: f64 = 1.25;
/// The contrast a motif keeps against a ground that is not the chart background (WCAG's floor
/// for graphics), below which the motif is redrawn in the nearer light or dark of its own hue.
const PATTERN_INK_MIN_CONTRAST: f64 = 3.0;
/// Width of the outline every patterned mark carries, in the ink color.
pub const OUTLINE_W: f64 = 1.0;
/// Width of the one line two touching patterned shapes share, centered on their edge, whatever
/// the ground: a pie wedge's and a stacked segment's or band's.
pub const PIE_EDGE_W: f64 = 2.0;
pub const STACK_EDGE_W: f64 = 1.5;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Scale {
    Category,
    Sequential,
    Diverging,
}

/// What a pattern is drawn on, as the resolved payload words it: `background`, `tint`, a color,
/// or one color per series slot.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(untagged)]
pub enum Ground {
    Slots(Vec<String>),
    Named(String),
}

/// The resolved payload's `pattern` block.
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PatternSpec {
    pub field: String,
    pub scale: Scale,
    /// Category only: the motif of each slot, wrapping like the color palette.
    #[serde(default)]
    pub motifs: Vec<String>,
    /// Sequential and diverging only: the ramp's name.
    #[serde(default)]
    pub ramp: Option<String>,
    /// Sequential and diverging only: how many steps the ramp has (3 to 9).
    #[serde(default)]
    pub steps: Option<usize>,
    /// The pattern's color; absent, each series draws in its own hue.
    #[serde(default)]
    pub color: Option<String>,
    pub ground: Ground,
    #[serde(default)]
    pub inverse: bool,
}

/// How a mark is filled: its plain color, or a pattern.
#[derive(Clone, Debug, PartialEq)]
pub enum Paint {
    Color(String),
    Pattern(PatternPaint),
}

#[derive(Clone, Debug, PartialEq)]
pub struct PatternPaint {
    /// The tile's id.
    tile: String,
    /// The tile's spacing in px, the unit repeats are counted in.
    spacing: f64,
    /// The tint a mark too small for the tile draws, matched to the tile's rendered L*.
    flat: String,
    /// The outline color: the pattern color, or on an `inverse` ground the ink that is the ground.
    ink: String,
    /// The color of the line shared with neighbors: `ink`, or the canvas on an `inverse` ground,
    /// where an ink line would vanish into the mark.
    edge: String,
    /// The id of the pattern a legend key paints with: the tile at key scale under an ink outline.
    key: String,
    /// Whether the axis names what the pattern marks, so a flat tint still reads.
    named_by_axis: bool,
    /// The series slot or ramp step this paint belongs to, the identity a fallback collision
    /// is counted between.
    series: usize,
    fallbacks: Rc<Fallbacks>,
}

/// The marks of one chart that drew a flat tint in place of their tile.
#[derive(Debug, Default, PartialEq)]
struct Fallbacks {
    marks: Cell<usize>,
    /// The (tint, outline) each unnamed fallback drew and the series that drew it.
    drawn: RefCell<HashMap<(String, String), BTreeSet<usize>>>,
}

impl Fallbacks {
    /// Whether two different series fell back to the same tint and outline, so the chart can no
    /// longer tell them apart. A series the axis names does not count.
    fn collide(&self) -> bool {
        self.drawn.borrow().values().any(|series| series.len() > 1)
    }

    fn reset(&self) {
        self.marks.set(0);
        self.drawn.borrow_mut().clear();
    }
}

/// What a mark draws with: the fill and stroke to hand its shape.
#[derive(Clone, Debug, PartialEq)]
pub struct MarkPaint {
    pub fill: String,
    pub stroke: String,
    pub stroke_w: f64,
    /// A pattern's outline color.
    pub ink: Option<String>,
    /// The color of the line a pattern shares with its neighbors (see [`PatternPaint`]).
    pub edge: String,
}

impl MarkPaint {
    /// The line a mark shares with its neighbors, as (color, width): a patterned mark strokes its
    /// `edge` `shared_w` wide, centered on the shape's edge; any other mark draws its own stroke.
    pub fn seam(&self, shared_w: f64) -> (&str, f64) {
        match &self.ink {
            None => (&self.stroke, self.stroke_w),
            Some(_) => (&self.edge, shared_w),
        }
    }
}

impl Paint {
    /// This paint for a mark whose pattern the axis names (a single-series bar), where a flat
    /// tint still reads.
    pub fn named_by_axis(self) -> Self {
        match self {
            Paint::Pattern(pattern) => Paint::Pattern(PatternPaint { named_by_axis: true, ..pattern }),
            color => color,
        }
    }

    /// This paint as series `series`'s: the identity fallback collisions are counted between.
    fn in_series(self, series: usize) -> Self {
        match self {
            Paint::Pattern(pattern) => Paint::Pattern(PatternPaint { series, ..pattern }),
            color => color,
        }
    }

    /// The plain color: a color's own, a pattern's flat tint.
    pub fn color(&self) -> &str {
        match self {
            Paint::Color(color) => color,
            Paint::Pattern(pattern) => &pattern.flat,
        }
    }

    /// The plain color, owned.
    pub fn into_color(self) -> String {
        match self {
            Paint::Color(color) => color,
            Paint::Pattern(pattern) => pattern.flat,
        }
    }

    /// The paint of a mark whose short side is `short_side` px. A color keeps `stroke` and
    /// `stroke_w`; a pattern takes a 1 px outline in its ink, and below [`MIN_REPEATS`]
    /// repeats across the short side draws its flat tint in place of the tile.
    pub fn for_mark(&self, short_side: f64, stroke: &str, stroke_w: f64) -> MarkPaint {
        match self {
            Paint::Color(color) => MarkPaint { fill: color.clone(), stroke: stroke.to_string(), stroke_w, ink: None, edge: stroke.to_string() },
            Paint::Pattern(pattern) => {
                let fill = if short_side / pattern.spacing >= MIN_REPEATS {
                    format!("url(#{})", pattern.tile)
                } else if short_side <= 0.0 {
                    // A zero-size mark draws nothing, so it cannot be mistaken for another series.
                    pattern.flat.clone()
                } else {
                    pattern.fallbacks.marks.set(pattern.fallbacks.marks.get() + 1);
                    if !pattern.named_by_axis {
                        pattern.fallbacks.drawn.borrow_mut().entry((pattern.flat.clone(), pattern.ink.clone())).or_default().insert(pattern.series);
                    }
                    pattern.flat.clone()
                };
                MarkPaint { fill, stroke: pattern.ink.clone(), stroke_w: OUTLINE_W, ink: Some(pattern.ink.clone()), edge: pattern.edge.clone() }
            }
        }
    }

    /// The paint of a legend swatch. A pattern's is its key, which draws the tile
    /// with its own ink outline.
    pub fn swatch(&self, stroke: &str, stroke_w: f64) -> MarkPaint {
        match self {
            Paint::Color(_) => self.for_mark(f64::INFINITY, stroke, stroke_w),
            Paint::Pattern(pattern) => MarkPaint { fill: format!("url(#{})", pattern.key), stroke: pattern.ink.clone(), stroke_w: OUTLINE_W, ink: Some(pattern.ink.clone()), edge: pattern.edge.clone() },
        }
    }
}

/// The chart's pattern encoding: which tile each slot or ramp step takes, and the tiles
/// handed out so far.
pub struct PatternPlan {
    lib: &'static Library,
    chart_id: String,
    chart_key: String,
    ground: String,
    spec: Option<PatternSpec>,
    used: RefCell<Vec<(String, Node)>>,
    fallbacks: Rc<Fallbacks>,
}

impl PatternPlan {
    /// `ground` is the chart's opaque canvas, the color a `background` tile lies on.
    pub(crate) fn new(chart_id: &str, spec: Option<PatternSpec>, ground: &str) -> Result<Self, String> {
        Self::with_library(library::shipped(), chart_id, spec, ground)
    }

    pub(crate) fn with_library(
        lib: &'static Library,
        chart_id: &str,
        spec: Option<PatternSpec>,
        ground: &str,
    ) -> Result<Self, String> {
        let known = |motif: &str| {
            if lib.motifs.contains_key(motif) {
                Ok(())
            } else {
                Err(format!("pattern: unknown motif {motif:?} (known: {})", lib.motifs.keys().cloned().collect::<Vec<_>>().join(", ")))
            }
        };
        if let Some(spec) = &spec {
            match spec.scale {
                Scale::Category => {
                    if spec.motifs.is_empty() {
                        return Err(format!("pattern on {:?}: a category scale needs at least one motif", spec.field));
                    }
                    spec.motifs.iter().try_for_each(|motif| known(motif))?;
                }
                Scale::Sequential | Scale::Diverging => {
                    let name = spec.ramp.as_deref().ok_or_else(|| format!("pattern on {:?}: a {:?} scale needs a ramp", spec.field, spec.scale))?;
                    let steps = spec.steps.ok_or_else(|| format!("pattern on {:?}: a {:?} scale needs `steps`", spec.field, spec.scale))?;
                    let ramps = lib.ramps.get(name).ok_or_else(|| format!("pattern: unknown ramp {name:?}"))?;
                    ramps.get(&steps.to_string()).ok_or_else(|| format!("pattern: ramp {name:?} has no {steps}-step form"))?;
                }
            }
        }
        parse_css_color(ground).map_err(|e| format!("pattern: the chart canvas {ground:?} is not a color: {e}"))?;
        Ok(Self {
            lib,
            chart_id: chart_id.to_string(),
            chart_key: crate::contract::safe_svg_id(chart_id),
            ground: ground.to_string(),
            spec,
            used: RefCell::new(Vec::new()),
            fallbacks: Rc::default(),
        })
    }

    /// The plan of a family's resolved payload: its optional `pattern` block. A chart without one
    /// needs nothing else from the payload; a patterned one lies on its `canvas`, which resolve
    /// composites chart over card over board, always opaque, so a transparent chart background
    /// still has the color that really sits behind it.
    pub fn from_resolved(resolved: &serde_json::Value) -> Result<Self, String> {
        let chart_id = resolved["id"].as_str().ok_or("pattern: payload.resolved.id missing")?;
        let spec: Option<PatternSpec> = resolved
            .get("pattern")
            .filter(|p| !p.is_null())
            .map(|p| serde_json::from_value(p.clone()))
            .transpose()
            .map_err(|e| format!("pattern block: {e}"))?;
        if spec.is_none() {
            return Ok(Self::default());
        }
        let ground = resolved
            .get("canvas")
            .and_then(serde_json::Value::as_str)
            .ok_or("pattern: a patterned chart needs the `canvas` it is drawn on")?;
        Self::new(chart_id, spec, ground)
    }

    /// Whether this chart draws any pattern.
    pub fn is_active(&self) -> bool {
        self.spec.is_some()
    }

    /// The `pattern` block, when the chart has a pattern channel.
    pub fn spec(&self) -> Option<&PatternSpec> {
        self.spec.as_ref()
    }

    /// The paint of category slot `slot` whose hue is `hue`: the spec's motif for the slot, in
    /// the spec's color (the hue when it names none) on the spec's ground; a chart with no
    /// `pattern` channel keeps the plain hue.
    pub fn category(&self, slot: usize, hue: &str) -> Result<Paint, String> {
        match &self.spec {
            Some(spec) if spec.scale == Scale::Category => {
                let motif = &spec.motifs[slot % spec.motifs.len()];
                let (ink, ground, outline) = self.inks(spec, Some(slot), hue)?;
                Ok(self.motif_paint(motif, &ink, &ground, &outline, spec.inverse)?.in_series(slot))
            }
            Some(spec) => Err(format!("pattern on {:?}: a {:?} scale paints ramp steps, not category slots", spec.field, spec.scale)),
            None => Ok(Paint::Color(hue.to_string())),
        }
    }

    /// The motif ink, ground, and outline of a series: `slot` is its palette slot, `None` for a
    /// ramp step, which has no series to take a ground from. The motif ink flips light or dark
    /// to stay readable on its ground; the outline stays the pattern color (the flipped ink on
    /// an `inverse` ground, where the marks are the ground). `tint` is a pale tint of the color
    /// under a darker shade of it.
    fn inks(&self, spec: &PatternSpec, slot: Option<usize>, hue: &str) -> Result<(String, String, String), String> {
        let base = spec.color.as_deref().unwrap_or(hue);
        let (ink, ground) = match &spec.ground {
            Ground::Named(name) if name == "background" => return Ok((base.to_string(), self.ground.clone(), base.to_string())),
            Ground::Named(name) if name == "tint" => (mix(base, "#000000", 0.45)?, mix(base, "#ffffff", 0.80)?),
            Ground::Named(color) => (base.to_string(), color.clone()),
            Ground::Slots(colors) => {
                let slot = slot.ok_or_else(|| format!("pattern on {:?}: a ground palette colors series, not ramp steps", spec.field))?;
                let ground = colors.get(slot).ok_or_else(|| {
                    format!("pattern on {:?}: the ground palette has {} colors, fewer than the series count (series {} has none)", spec.field, colors.len(), slot + 1)
                })?;
                (base.to_string(), ground.clone())
            }
        };
        let motif = contrasting(&ink, &ground)?;
        let outline = if spec.inverse { motif.clone() } else { ink };
        Ok((motif, ground, outline))
    }

    /// The ramp step of `value` over the field's extent `[lo, hi]`. Sequential: `steps` equal
    /// intervals. Diverging: `|v| / M` (`M` the larger magnitude of the extent) in equal bins on
    /// each side, darkest at `M`; an odd `steps` has a zero step, and with an even one a zero
    /// takes the lightest positive step. A sequential field with no range takes the first step.
    pub fn step_of(&self, value: f64, lo: f64, hi: f64) -> Result<usize, String> {
        let (spec, steps) = self.ramp()?;
        let n = steps.len();
        if spec.scale == Scale::Sequential {
            let share = if hi > lo { (value - lo) / (hi - lo) } else { 0.0 };
            return Ok(((share * n as f64).floor() as usize).min(n - 1));
        }
        let side = n / 2;
        let positive_start = if n % 2 == 1 { side + 1 } else { side };
        let magnitude = lo.abs().max(hi.abs());
        if magnitude == 0.0 || (value == 0.0 && n % 2 == 1) {
            return Ok(side);
        }
        let bin = ((value.abs() / magnitude * side as f64).ceil() as usize).clamp(1, side);
        Ok(if value < 0.0 { side - bin } else { positive_start + bin - 1 })
    }

    /// The paint of ramp step `step`, inked in `hue`.
    pub fn step(&self, index: usize, hue: &str) -> Result<Paint, String> {
        let (spec, steps) = self.ramp()?;
        let step = steps.get(index).ok_or_else(|| format!("pattern: ramp step {index} out of {}", steps.len()))?;
        let (ink, ground, outline) = self.inks(spec, None, hue)?;
        let name = spec.ramp.as_deref().unwrap_or_default();
        let key = format!("{name}{}-{index}", steps.len());
        // The tint's ink share is what the step's measured L* implies on a black-on-white ground,
        // not its nominal coverage: an inverse step's coverage is the ground's share.
        let dark = 1.0 - ((step.l_star + 16.0) / 116.0).powi(3);
        let flipped = step.inverse != spec.inverse;
        Ok(self.build(&key, &step.tile, if flipped == step.inverse { dark } else { 1.0 - dark }, &ink, &ground, &outline, flipped)?.in_series(index))
    }

    fn ramp(&self) -> Result<(&PatternSpec, &'static [library::Step]), String> {
        let spec = self.spec.as_ref().filter(|spec| spec.scale != Scale::Category).ok_or("pattern: this chart has no ramp")?;
        let name = spec.ramp.as_deref().ok_or("pattern: a ramp scale carries its ramp's name")?;
        let steps = spec.steps.ok_or("pattern: a ramp scale carries its step count")?;
        Ok((spec, &self.lib.ramps[name][&steps.to_string()]))
    }

    fn motif_paint(&self, motif: &str, ink: &str, ground: &str, outline: &str, inverse: bool) -> Result<Paint, String> {
        let tile = &self.lib.motifs[motif];
        self.build(motif, tile, self.nominal_share(tile.coverage, inverse), ink, ground, outline, inverse)
    }

    /// The share of a motif tile the ink color covers once rendered: its marks' coverage scaled
    /// by `flat_k`, or the rest of the tile when `inverse` puts the marks in the ground color.
    fn nominal_share(&self, coverage: f64, inverse: bool) -> f64 {
        let marks = (coverage * self.lib.flat_k).min(1.0);
        if inverse { 1.0 - marks } else { marks }
    }

    /// The pattern paint for `tile` in `ink` on `ground` (swapped when `inverse`), its tile
    /// registered for the chart's `<defs>` the first time it is handed out.
    fn build(&self, key: &str, tile: &Tile, ink_share: f64, ink: &str, ground: &str, outline: &str, inverse: bool) -> Result<Paint, String> {
        let (marks_color, bed) = if inverse { (ground, ink) } else { (ink, ground) };
        let flat = lin_mix(ground, ink, ink_share)?;
        let edge = if inverse { self.ground.clone() } else { outline.to_string() };
        let id = format!("dbt-pat-{}-{key}-{}-{}", self.chart_key, hex(marks_color)?, hex(bed)?);
        let key = format!("{}{}-{key}-{}-{}-{}", library::KEY_PREFIX, self.chart_key, hex(marks_color)?, hex(bed)?, hex(outline)?);
        self.define_key(&key, &id, tile, outline);
        let mut used = self.used.borrow_mut();
        if !used.iter().any(|(seen, _)| *seen == id) {
            used.push((id.clone(), tile_node(&id, tile, marks_color, bed)));
        }
        Ok(Paint::Pattern(PatternPaint { tile: id, spacing: tile.spacing, flat, ink: outline.to_string(), edge, key, named_by_axis: false, series: 0, fallbacks: self.fallbacks.clone() }))
    }

    /// Forgets the fallbacks counted so far. A chart laid out more than once (facet probes, a
    /// pie sized before it is drawn) calls this before each pass so only the last one reports.
    pub fn reset_fallbacks(&self) {
        self.fallbacks.reset();
    }

    /// Closes the chart once its marks are drawn: reports the marks that fell back to a flat tint,
    /// and returns the `<defs>` of every tile handed out, in first-use order. A facet
    /// panel (`whole` false) does neither: its composite does both for all panels at once.
    ///
    /// A fallback warns; it is an error when two different series fell back to the same tint and
    /// outline (no axis label naming them), since the chart would look right and cannot be read.
    pub fn finish(&self, whole: bool) -> Result<Option<Node>, Diagnostic> {
        if !whole {
            return Ok(None);
        }
        let count = self.fallbacks.marks.get();
        if count > 0 {
            let params = [("chart_id", json!(self.chart_id)), ("count", json!(count))];
            if self.fallbacks.collide() {
                return Err(Diagnostic::new(&codes::ERR_PATTERN_FALLBACK_FLAT, params));
            }
            diagnostic::warn(Diagnostic::new(&codes::WARN_PATTERN_FALLBACK_FLAT, params));
        }
        let used = self.used.borrow();
        Ok((!used.is_empty()).then(|| Node::new(Role::Chrome, Attrs::new(), Shape::Defs(used.iter().map(|(_, node)| node.clone()).collect()))))
    }
}

impl Default for PatternPlan {
    /// The plan of a chart with no pattern: every slot keeps its plain hue.
    fn default() -> Self {
        Self {
            lib: library::shipped(),
            chart_id: String::new(),
            chart_key: String::new(),
            ground: String::new(),
            spec: None,
            used: RefCell::new(Vec::new()),
            fallbacks: Rc::default(),
        }
    }
}

/// The shared line of a patterned path mark `d` drawn fill-only: a `color` stroke `width` wide,
/// centered on the edge. `extra` carries the stroke's join, cap, and opacity.
pub fn edge_node(d: PathData, color: &str, width: f64, extra: &Attrs) -> Node {
    let attrs = Attrs::new().set("fill", "none").set("stroke", color).num("stroke-width", width);
    Node::path(Role::Chrome, d, attrs.extend(extra.clone()).set("pointer-events", "none"))
}

/// `tile` as a `<pattern>`: a ground rect, then its marks in `marks_color`.
pub fn tile_node(id: &str, tile: &Tile, marks_color: &str, ground: &str) -> Node {
    let (w, h) = (tile.width, tile.height);
    let mut children = vec![Node::rect(Role::Chrome, (0.0, 0.0, w, h), Attrs::new().set("fill", ground))];
    for mark in &tile.marks {
        match mark {
            Mark::Circles { circles, r } => children.extend(
                circles.iter().map(|&[cx, cy]| Node::circle(Role::Chrome, (cx, cy), *r, Attrs::new().set("fill", marks_color))),
            ),
            Mark::Filled { path, fill_rule, .. } => {
                children.push(Node::path(Role::Chrome, path.clone(), Attrs::new().set("fill", marks_color).set("fill-rule", fill_rule)));
            }
            Mark::Stroked { path, stroke_width, linecap } => children.push(Node::path(
                Role::Chrome,
                path.clone(),
                Attrs::new().set("fill", "none").set("stroke", marks_color).num("stroke-width", *stroke_width).set("stroke-linecap", linecap),
            )),
        }
    }
    let attrs = Attrs::new().set("id", id).set("patternUnits", "userSpaceOnUse");
    Node::new(Role::Chrome, attrs, Shape::Pattern { w, h, transform: None, children })
}

/// `ink` when it keeps [`PATTERN_INK_MIN_CONTRAST`] against `ground`; otherwise the lightness of
/// `ink`'s hue nearest to it that does, toward whichever of black and white contrasts more with
/// `ground` (`dbt_charts_color::ensure_readable_ink`, the rule label ink follows).
fn contrasting(ink: &str, ground: &str) -> Result<String, String> {
    let (ink_hex, ground_hex) = (format!("#{}", hex(ink)?), format!("#{}", hex(ground)?));
    if dbt_charts_color::wcag_contrast(&ink_hex, &ground_hex)? >= PATTERN_INK_MIN_CONTRAST {
        return Ok(ink.to_string());
    }
    dbt_charts_color::ensure_readable_ink(&ink_hex, &ground_hex, PATTERN_INK_MIN_CONTRAST)
}

fn rgb255(color: &str) -> Result<[f64; 3], String> {
    let (r, g, b, _) = parse_css_color(color)?;
    Ok([r, g, b].map(|c| (c * 255.0).round()))
}

fn hex(color: &str) -> Result<String, String> {
    let [r, g, b] = rgb255(color)?;
    Ok(rgb01_to_hex(r / 255.0, g / 255.0, b / 255.0).trim_start_matches('#').to_string())
}

/// A straight sRGB blend of `a` toward `b`.
fn mix(a: &str, b: &str, t: f64) -> Result<String, String> {
    let (a, b) = (rgb255(a)?, rgb255(b)?);
    let [r, g, bl] = std::array::from_fn(|i| (a[i] * (1.0 - t) + b[i] * t) / 255.0);
    Ok(rgb01_to_hex(r, g, bl))
}

fn linear(channel: f64) -> f64 {
    let c = channel / 255.0;
    if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
}

fn delinear(y: f64) -> f64 {
    let y = y.clamp(0.0, 1.0);
    (if y <= 0.0031308 { y * 12.92 } else { 1.055 * y.powf(1.0 / 2.4) - 0.055 }) * 255.0
}

/// The color whose linear luminance is `(1 - t) * a + t * b`, what the
/// eye averages across a tile.
fn lin_mix(a: &str, b: &str, t: f64) -> Result<String, String> {
    let (a, b) = (rgb255(a)?, rgb255(b)?);
    let [r, g, bl] = std::array::from_fn(|i| delinear(linear(a[i]) * (1.0 - t) + linear(b[i]) * t) / 255.0);
    Ok(rgb01_to_hex(r, g, bl))
}

impl PatternPlan {
    /// Registers a legend key's patterns the first time `key_id` is handed out.
    fn define_key(&self, key_id: &str, tile_id: &str, tile: &Tile, ink: &str) {
        let mut used = self.used.borrow_mut();
        if used.iter().any(|(seen, _)| seen == key_id) {
            return;
        }
        used.push((key_id.to_string(), library::key_node(key_id, tile_id, tile, ink)));
    }
}

/// CIE L* of a color.
#[cfg(test)]
fn lightness(color: &str) -> Result<f64, String> {
    let [r, g, b] = rgb255(color)?.map(linear);
    let y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    Ok(if y > 216.0 / 24389.0 { 116.0 * y.cbrt() - 16.0 } else { y * 24389.0 / 27.0 })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn library() -> &'static Library {
        let json = r#"{
            "flat_k": 1.108,
            "motifs": {
                "hatch-45": {"width": 8, "height": 8, "spacing": 5.657, "key_window": [0, 0], "coverage": 0.36,
                    "marks": [{"path": "M2 -2L-2 2", "stroke_width": 1.98, "linecap": "butt"}]},
                "dots": {"width": 4, "height": 16, "spacing": 4, "key_window": [0, 0], "coverage": 0.34,
                    "marks": [{"circles": [[0.0, 0.0], [2.0, 4.0]], "r": 1.335}]},
                "dither": {"width": 4, "height": 4, "spacing": 4, "key_window": [0, 0], "coverage": 0.375, "crisp": true,
                    "marks": [{"path": "M0 0h1v1h-1Z", "fill": true, "fill_rule": "nonzero"}]},
                "blob": {"width": 96, "height": 96, "spacing": 5.6, "key_window": [0, 0], "coverage": 0.35,
                    "marks": [{"path": "M0 0L4 0L4 4Z", "fill": true, "fill_rule": "evenodd"}]}
            },
            "ramps": {
                "seq": {"6": [
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.2, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.4, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.4, "inverse": true, "marks": [], "L_star": 50.0},
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.2, "inverse": true, "marks": [], "L_star": 50.0},
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.1, "inverse": true, "marks": [], "L_star": 50.0},
                    {"width": 60, "height": 60, "spacing": 42.4, "key_window": [0, 0], "coverage": 0.1, "inverse": true, "marks": [], "L_star": 50.0}
                ]},
                "div": {"7": [
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.5, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.35, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.2, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.1, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.2, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.35, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.5, "inverse": false, "marks": [], "L_star": 50.0}
                ], "4": [
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.5, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.2, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.2, "inverse": false, "marks": [], "L_star": 50.0},
                    {"width": 12, "height": 12, "spacing": 8.5, "key_window": [0, 0], "coverage": 0.5, "inverse": false, "marks": [], "L_star": 50.0}
                ]}
            }
        }"#;
        Box::leak(Box::new(serde_json::from_str(json).unwrap()))
    }

    fn category(motifs: &[&str], ground: Ground, inverse: bool) -> PatternSpec {
        PatternSpec {
            field: "series".into(),
            scale: Scale::Category,
            motifs: motifs.iter().map(|m| m.to_string()).collect(),
            ramp: None,
            steps: None,
            color: None,
            ground,
            inverse,
        }
    }

    fn background() -> Ground {
        Ground::Named("background".into())
    }

    fn ramp(scale: Scale, name: &str) -> PatternSpec {
        let steps = if scale == Scale::Sequential { 6 } else { 7 };
        PatternSpec { field: "v".into(), scale, motifs: vec![], ramp: Some(name.into()), steps: Some(steps), color: None, ground: background(), inverse: false }
    }

    fn plan(spec: Option<PatternSpec>) -> PatternPlan {
        PatternPlan::with_library(library(), "c 1", spec, "#ffffff").unwrap()
    }

    fn printed(node: &Node) -> String {
        print::print(std::slice::from_ref(node))
    }

    #[test]
    fn a_ramp_steps_flat_tint_has_the_lightness_of_its_pattern() {
        let data: serde_json::Value = serde_json::from_str(include_str!("../../data/pattern_motifs.json")).unwrap();
        for (name, count) in [("hatch-crosshatch", 6), ("hatch-crosshatch", 9), ("dots", 6), ("dots", 9), ("hatch-dots", 9)] {
            let spec = PatternSpec { field: "v".into(), scale: Scale::Sequential, motifs: vec![], ramp: Some(name.into()), steps: Some(count), color: None, ground: background(), inverse: false };
            let plan = PatternPlan::new("c", Some(spec), "#ffffff").unwrap();
            let mut previous = f64::INFINITY;
            for (i, step) in data["ramps"][name][count.to_string()].as_array().unwrap().iter().enumerate() {
                let l_star = step["L_star"].as_f64().unwrap();
                let tint = lightness(plan.step(i, "#000000").unwrap().color()).unwrap();
                assert!((tint - l_star).abs() <= 2.0, "{name}/{count} step {i}: tint L* {tint:.1} vs pattern {l_star}");
                if name != "hatch-dots" {
                    assert!(tint < previous, "{name}/{count} step {i}: tint L* {tint:.1} is not darker than step {}", i.saturating_sub(1));
                }
                previous = tint;
            }
        }
    }

    #[test]
    fn a_tile_draws_its_ground_then_each_kind_of_mark() {
        let lib = library();
        let hatch = tile_node("p", &lib.motifs["hatch-45"], "#f00", "#fff");
        assert!(matches!(hatch.shape, Shape::Pattern { w: 8.0, h: 8.0, transform: None, .. }));
        let [ground, stroke] = hatch.children() else { panic!("a ground and one stroke") };
        assert_eq!((ground.bounds(), ground.attrs.get("fill")), (Some(scene::Bounds { x1: 0.0, y1: 0.0, x2: 8.0, y2: 8.0 }), Some("#fff")));
        assert_eq!(stroke.shape, Shape::Path(PathData::new().move_to(2.0, -2.0).line_to(-2.0, 2.0)));
        assert_eq!((stroke.attrs.get("stroke"), stroke.attrs.get_num("stroke-width")), (Some("#f00"), Some(1.98)));

        let dots = tile_node("p", &lib.motifs["dots"], "#f00", "#fff");
        let circles: Vec<_> = dots.children()[1..].iter().map(|c| c.shape.clone()).collect();
        assert_eq!(circles, [Shape::Circle { cx: 0.0, cy: 0.0, r: 1.335 }, Shape::Circle { cx: 2.0, cy: 4.0, r: 1.335 }]);

        let blob = tile_node("p", &lib.motifs["blob"], "#f00", "#fff");
        assert_eq!(blob.children()[1].shape, Shape::Path(PathData::new().move_to(0.0, 0.0).line_to(4.0, 0.0).line_to(4.0, 4.0).close()));
        assert_eq!(blob.children()[1].attrs.get("fill-rule"), Some("evenodd"));
    }

    #[test]
    fn the_hue_variant_inks_the_series_color_on_the_canvas() {
        let plan = plan(Some(category(&["hatch-45", "dots"], background(), false)));
        let paint = plan.category(0, "#4c78a8").unwrap();
        let mark = paint.for_mark(100.0, "none", 0.0);
        assert_eq!(mark.fill, format!("url(#dbt-pat-{}-hatch-45-4c78a8-ffffff)", crate::contract::safe_svg_id("c 1")));
        assert_eq!((mark.stroke.as_str(), mark.stroke_w), ("#4c78a8", 1.0));
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert!(defs.contains("fill=\"#ffffff\"/>") && defs.contains("stroke=\"#4c78a8\""), "{defs}");
        assert_eq!(paint.color(), "#d0d7e2", "the flat tint is the linear-light mix at coverage * flat_k");
    }

    #[test]
    fn the_tint_ground_is_a_dark_shade_on_a_light_tint() {
        let plan = plan(Some(category(&["hatch-45"], Ground::Named("tint".into()), false)));
        plan.category(0, "#4c78a8").unwrap();
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert!(defs.contains("fill=\"#dbe4ee\"") && defs.contains("stroke=\"#2a425c\""), "{defs}");
    }

    #[test]
    fn inverse_swaps_ink_and_ground_but_outlines_in_the_ink() {
        let plan = plan(Some(category(&["hatch-45"], background(), true)));
        let mark = plan.category(0, "#4c78a8").unwrap().for_mark(100.0, "none", 0.0);
        assert_eq!(mark.stroke, "#4c78a8");
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert!(defs.contains("height=\"8\" fill=\"#4c78a8\"/>") && defs.contains("stroke=\"#ffffff\""), "{defs}");
        let flat = plan.category(0, "#4c78a8").unwrap();
        assert_eq!(flat.color(), "#b1bdd1", "an inverse tint runs from the ink toward the ground");
    }

    #[test]
    fn slots_wrap_and_identical_tiles_are_defined_once() {
        let plan = plan(Some(category(&["hatch-45", "dots"], background(), false)));
        for slot in [0, 2, 0] {
            plan.category(slot, "#4c78a8").unwrap();
        }
        plan.category(1, "#4c78a8").unwrap();
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert_eq!(defs.matches(" id=\"dbt-pat-").count(), 2, "{defs}");
        assert_eq!(defs.matches("<pattern width=\"16\" height=\"12\"").count(), 2, "one key per tile: {defs}");
    }

    #[test]
    fn a_dither_antialiases_and_draws_its_key_at_full_scale() {
        let plan = plan(Some(category(&["dither"], background(), false)));
        let paint = plan.category(0, "#4c78a8").unwrap();
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert!(defs.contains("fill-rule=\"nonzero\""), "{defs}");
        assert!(!defs.contains("crispEdges"), "crispEdges snaps each pixel to a whole device pixel, so a scaled board draws the tile nearly solid: {defs}");
        assert!(paint.for_mark(5.0, "none", 0.0).fill.starts_with("url(#"), "a 4 px period holds 1.25 repeats in 5 px");
    }

    #[test]
    fn a_swatch_is_the_slots_key_whose_pattern_crops_the_tile_under_an_ink_outline() {
        let plan = plan(Some(category(&["hatch-45", "dither"], background(), false)));
        let tiled = plan.category(0, "#4c78a8").unwrap().swatch("none", 0.0);
        let dither = plan.category(1, "#4c78a8").unwrap().swatch("none", 0.0);
        let key = format!("dbt-key-{}-hatch-45-4c78a8-ffffff-4c78a8", crate::contract::safe_svg_id("c 1"));
        assert_eq!((tiled.fill.as_str(), tiled.stroke.as_str()), (format!("url(#{key})").as_str(), "#4c78a8"));
        assert!(dither.fill.starts_with("url(#dbt-key-"), "every slot's key follows the same size and outline");
        let defs = printed(&plan.finish(true).unwrap().unwrap());
        assert!(defs.contains(&format!("<pattern width=\"16\" height=\"12\" id=\"{key}\" patternUnits=\"userSpaceOnUse\"><g transform=\"translate(")), "{defs}");
        assert!(defs.contains("fill=\"url(#dbt-pat-"), "the key fills with the tile: {defs}");
    }

    #[test]
    fn a_mark_under_one_and_a_quarter_repeats_draws_the_tint_with_the_outline() {
        let plan = plan(Some(category(&["hatch-45"], background(), false)));
        let paint = plan.category(0, "#4c78a8").unwrap();
        let spacing = 5.657;
        assert!(paint.for_mark(spacing * 1.25, "none", 0.0).fill.starts_with("url(#"));
        let small = paint.for_mark(spacing * 1.25 - 0.01, "#000", 2.0);
        assert_eq!((small.fill.as_str(), small.stroke.as_str(), small.stroke_w), ("#d0d7e2", "#4c78a8", 1.0));
        assert!(paint.swatch("none", 0.0).fill.starts_with("url(#"), "a legend swatch always shows the tile");
    }

    #[test]
    fn a_plain_color_keeps_the_stroke_it_was_given() {
        let mark = Paint::Color("#abc".into()).for_mark(1.0, "#fff", 1.5);
        assert_eq!(mark, MarkPaint { fill: "#abc".into(), stroke: "#fff".into(), stroke_w: 1.5, ink: None, edge: "#fff".into() });
    }

    #[test]
    fn a_pattern_color_replaces_the_series_hue() {
        let mut spec = category(&["hatch-45"], background(), false);
        spec.color = Some("#2b4c7e".into());
        let plan = plan(Some(spec));
        let mark = plan.category(0, "#e45756").unwrap().for_mark(100.0, "none", 0.0);
        assert_eq!(mark.stroke, "#2b4c7e");
        assert!(printed(&plan.finish(true).unwrap().unwrap()).contains("stroke=\"#2b4c7e\""));
    }

    #[test]
    fn a_color_ground_lies_under_every_series_and_a_palette_ground_under_its_slot() {
        let spec = category(&["hatch-45"], Ground::Named("#fff3d6".into()), false);
        let flat = plan(Some(spec));
        flat.category(0, "#4c78a8").unwrap();
        flat.category(1, "#e45756").unwrap();
        let defs = printed(&flat.finish(true).unwrap().unwrap());
        assert_eq!(defs.matches("height=\"8\" fill=\"#fff3d6\"/>").count(), 2, "{defs}");

        let slots = Ground::Slots(vec!["#dbe4ee".into(), "#f0dede".into()]);
        let by_slot = plan(Some(category(&["hatch-45"], slots, false)));
        by_slot.category(0, "#4c78a8").unwrap();
        by_slot.category(1, "#4c78a8").unwrap();
        let defs = printed(&by_slot.finish(true).unwrap().unwrap());
        assert!(defs.contains("fill=\"#dbe4ee\"") && defs.contains("fill=\"#f0dede\""), "{defs}");
    }

    #[test]
    fn ink_on_a_colored_ground_keeps_its_color_when_it_contrasts_and_flips_light_when_it_does_not() {
        let slots = Ground::Slots(vec!["#16263d".into(), "#dbe4ee".into(), "#2b4c7e".into()]);
        let mut spec = category(&["hatch-45"], slots, false);
        spec.color = Some("#2b4c7e".into());
        let plan = plan(Some(spec));
        // The tile id ends in its marks' color and its bed: `...-<marks>-<bed>`.
        let motif = |slot: usize| {
            let Paint::Pattern(pattern) = plan.category(slot, "#000000").unwrap() else { unreachable!() };
            format!("#{}", pattern.tile.rsplit('-').nth(1).unwrap())
        };
        for slot in 0..3 {
            let paint = plan.category(slot, "#000000").unwrap().for_mark(100.0, "none", 0.0);
            assert_eq!((paint.stroke.as_str(), paint.edge.as_str()), ("#2b4c7e", "#2b4c7e"), "slot {slot}: the line stays the pattern color");
        }
        assert_eq!(motif(1), "#2b4c7e", "a light ground keeps the pattern color");
        for slot in [0, 2] {
            let ink = motif(slot);
            let ground = ["#16263d", "", "#2b4c7e"][slot];
            assert!(dbt_charts_color::wcag_contrast(&ink, ground).unwrap() >= PATTERN_INK_MIN_CONTRAST, "slot {slot}: {ink} on {ground}");
            assert!(lightness(&ink).unwrap() > lightness(ground).unwrap(), "slot {slot}: a dark ground takes a light motif");
        }
    }

    #[test]
    fn the_background_ground_never_adjusts_the_ink() {
        let mut spec = category(&["hatch-45"], background(), false);
        spec.color = Some("#ffffff".into());
        let plan = PatternPlan::with_library(library(), "c 1", Some(spec), "#ffffff").unwrap();
        assert_eq!(plan.category(0, "#000000").unwrap().for_mark(100.0, "none", 0.0).stroke, "#ffffff");
    }

    #[test]
    fn a_ground_palette_shorter_than_the_series_is_an_error() {
        let slots = Ground::Slots(vec!["#dbe4ee".into()]);
        let plan = plan(Some(category(&["hatch-45"], slots, false)));
        let err = plan.category(1, "#4c78a8").unwrap_err();
        assert!(err.contains("fewer than the series count"), "{err}");
    }

    #[test]
    fn a_resolved_payload_carries_the_pattern_block() {
        let none = PatternPlan::from_resolved(&serde_json::json!({"id": "c"})).unwrap();
        assert!(!none.is_active());
        let spec = serde_json::json!({"id": "c", "pattern": {"field": "s", "scale": "category", "motifs": ["hatch-45"], "color": "#2b4c7e", "ground": ["#fff", "#eee"], "inverse": false}, "canvas": "#fff"});
        let plan = PatternPlan::from_resolved(&spec).unwrap();
        assert_eq!(plan.spec().map(|spec| spec.field.as_str()), Some("s"));
        assert_eq!(plan.spec().unwrap().ground, Ground::Slots(vec!["#fff".into(), "#eee".into()]));
        let err = PatternPlan::from_resolved(&serde_json::json!({"id": "c", "pattern": {"field": "s"}})).err().unwrap();
        assert!(err.starts_with("pattern block:"), "{err}");
    }

    #[test]
    fn a_patterned_chart_lies_on_its_composited_canvas_not_its_background() {
        let block = serde_json::json!({"field": "s", "scale": "category", "motifs": ["hatch-45"], "ground": "background"});
        let missing = PatternPlan::from_resolved(&serde_json::json!({"id": "c", "pattern": block, "background": "#fafafa"})).err().unwrap();
        assert!(missing.contains("`canvas`"), "{missing}");
        let plan = PatternPlan::from_resolved(&serde_json::json!({"id": "c", "pattern": block, "background": "transparent", "canvas": "#f4efe6"})).unwrap();
        plan.category(0, "#4c78a8").unwrap();
        assert!(printed(&plan.finish(true).unwrap().unwrap()).contains("height=\"8\" fill=\"#f4efe6\"/>"));
    }

    #[test]
    fn no_pattern_means_the_plain_hue() {
        let plan = plan(None);
        assert!(!plan.is_active());
        assert_eq!(plan.category(3, "#abc").unwrap(), Paint::Color("#abc".into()));
        assert!(plan.finish(true).unwrap().is_none());
    }

    #[test]
    fn a_sequential_value_falls_in_one_of_six_equal_intervals() {
        let plan = plan(Some(ramp(Scale::Sequential, "seq")));
        let bins: Vec<usize> = [0.0, 0.16, 0.17, 0.5, 0.83, 0.84, 1.0].iter().map(|v| plan.step_of(*v, 0.0, 1.0).unwrap()).collect();
        assert_eq!(bins, [0, 0, 1, 3, 4, 5, 5]);
        assert_eq!(plan.step_of(7.0, 7.0, 7.0).unwrap(), 0);
    }

    #[test]
    fn a_diverging_value_bins_by_its_share_of_the_larger_magnitude() {
        let plan = plan(Some(ramp(Scale::Diverging, "div")));
        let at = |v: f64| plan.step_of(v, -10.0, 4.0).unwrap();
        assert_eq!([at(-10.0), at(-6.0), at(-3.0), at(0.0), at(1.0), at(4.0)], [0, 1, 2, 3, 4, 5]);
        assert_eq!(at(10.0), 6);
    }

    #[test]
    fn an_even_diverging_ramp_splits_each_side_and_puts_zero_on_the_positive_side() {
        let mut spec = ramp(Scale::Diverging, "div");
        spec.steps = Some(4);
        let plan = plan(Some(spec));
        let at = |v: f64| plan.step_of(v, -10.0, 10.0).unwrap();
        assert_eq!([at(-10.0), at(-4.0), at(0.0), at(4.0), at(10.0)], [0, 1, 2, 2, 3]);
    }

    #[test]
    fn a_ramp_step_flips_its_own_inverse_with_the_authors() {
        let mut spec = ramp(Scale::Sequential, "seq");
        spec.inverse = true;
        let plan = plan(Some(spec));
        // Step 0 is not inverse in the ramp; the author's inverse draws it on the ink.
        plan.step(0, "#4c78a8").unwrap();
        assert!(printed(&plan.finish(true).unwrap().unwrap()).contains("height=\"60\" fill=\"#4c78a8\"/>"));
    }

    #[test]
    fn construction_refuses_an_unknown_motif_and_a_ramp_of_the_wrong_length() {
        let build = |spec| PatternPlan::with_library(library(), "c", Some(spec), "#fff");
        assert!(build(category(&["nope"], background(), false)).err().unwrap().contains("unknown motif"));
        assert!(build(category(&[], background(), false)).err().unwrap().contains("at least one motif"));
        let mut thirteen = ramp(Scale::Sequential, "seq");
        thirteen.steps = Some(13);
        assert!(build(thirteen).err().unwrap().contains("no 13-step form"));
        let mut unstepped = ramp(Scale::Sequential, "seq");
        unstepped.steps = None;
        assert!(build(unstepped).err().unwrap().contains("needs `steps`"));
        assert!(build(ramp(Scale::Sequential, "gone")).err().unwrap().contains("unknown ramp"));
    }
}
