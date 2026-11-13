//! Ribbons: the quadrilateral joining each bar, or stacked segment, to the same series in the next category.

use std::collections::hash_map::Entry;
use std::collections::HashMap;

use ::bind::label_template::{owned_by, RowScope, Template};
use ::bind::rows::{row_num, row_str};
use measure::FontBook;
use scene::{Attrs, Node, PathData, Role};
use serde::Deserialize;
use serde_json::{Map, Value};

use super::labels::{LabelFont, LABEL_FIT_PADDING, NEUTRAL_INK};
use crate::constants::bar::LABEL_FIT_LINE_HEIGHT_MULTIPLIER;

/// `style.marks.bar.ribbons`, resolved.
#[derive(Deserialize, Default)]
pub(crate) struct Ribbons {
    visible: bool,
    #[serde(default)]
    labels: RibbonLabelStyle,
}

/// `style.marks.bar.ribbons.labels`, resolved.
#[derive(Deserialize, Default)]
struct RibbonLabelStyle {
    #[serde(default)]
    visible: bool,
    #[serde(default)]
    template: Option<String>,
}

impl Ribbons {
    /// Whether the ribbons carry labels, which `RibbonDraft::new` needs a `RibbonLabelCtx` for.
    pub(super) fn labeled(&self) -> bool {
        self.visible && self.labels.visible
    }
}

/// What a ribbon's label reads: the chart's rows and fields, and the bar value labels' font.
pub(super) struct RibbonLabelCtx<'a> {
    pub(super) rows: &'a [Map<String, Value>],
    pub(super) cat_field: &'a str,
    pub(super) val_field: &'a str,
    pub(super) color_field: Option<&'a str>,
    pub(super) font: LabelFont,
    pub(super) fonts: &'a FontBook,
}

struct LabelPlan<'a> {
    template: Template,
    ctx: RibbonLabelCtx<'a>,
}

impl Ribbons {
    pub(crate) fn visible(&self) -> bool {
        self.visible
    }
}

/// The share of its bar's contrast against the canvas a ribbon keeps: the ratio raised to this.
const RIBBON_CONTRAST: f64 = 0.5;

/// A bar's box: `(x, y, width, height)`.
type Rect = (f64, f64, f64, f64);

struct RibbonBar {
    cat: String,
    series: String,
    rect: Rect,
    color: String,
    /// The bar's own `aria-label`, which the hover runtime keeps a ribbon lit by.
    aria: Option<String>,
    /// The series' legend key, on a chart with more than one series.
    legend_key: Option<String>,
}

/// The bars a panel draws, kept while it draws them so the ribbons between them can be painted under.
pub(super) struct RibbonDraft<'a> {
    visible: bool,
    bars: Vec<RibbonBar>,
    labels: Option<LabelPlan<'a>>,
}

impl<'a> RibbonDraft<'a> {
    /// `grouped` is an unstacked multi-series bar, which has no one bar to join a ribbon to. Labels are
    /// drawn between horizontal bars: `labels` is that bar's context, `None` elsewhere.
    pub(super) fn new(ribbons: &Ribbons, grouped: bool, labels: Option<RibbonLabelCtx<'a>>) -> Result<Self, String> {
        if ribbons.visible && grouped {
            return Err(diagnostic::refuse!("bar: ribbons on grouped bars are not supported; stack the series or draw one"));
        }
        let labels = match (ribbons.labeled(), labels) {
            (false, _) => None,
            (true, None) => return Err("bar: ribbon labels need horizontal bars".into()),
            (true, Some(ctx)) => {
                let source = ribbons.labels.template.as_deref().ok_or("bar: ribbons.labels.template absent (a visible ribbon label needs a template)")?;
                Some(LabelPlan { template: Template::parse(source).map_err(|e| owned_by("bar", e))?, ctx })
            }
        };
        Ok(Self { visible: ribbons.visible, bars: Vec::new(), labels })
    }

    /// Notes the bar at `key`, `(category, series)`, in `color` (the series' base color, never a pattern).
    /// `aria` is the bar's `aria-label` and `legend_key` its series' legend key, `None` for one series.
    pub(super) fn record(&mut self, key: &(String, String), rect: Rect, aria: Option<&str>, legend_key: Option<String>, color: impl FnOnce() -> Result<String, String>) -> Result<(), String> {
        if self.visible {
            self.bars.push(RibbonBar { cat: key.0.clone(), series: key.1.clone(), rect, color: color()?, aria: aria.map(str::to_string), legend_key });
        }
        Ok(())
    }

    /// Paints the ribbons into `plot` at `at`, under the bars drawn from there. `cats` is the drawn
    /// category order, so a ribbon joins neighbors only.
    /// Returns the labels' group, which the caller adds above the bars.
    pub(super) fn paint(self, plot: &mut Vec<Node>, at: usize, horizontal: bool, cats: &[String], canvas: &str) -> Result<Option<Node>, String> {
        if !self.visible {
            return Ok(None);
        }
        // One bar per (category, series), the first drawn; a category's series keep their drawn order.
        let mut by_key: HashMap<(&str, &str), &RibbonBar> = HashMap::new();
        let mut by_cat: HashMap<&str, Vec<&RibbonBar>> = HashMap::new();
        for bar in &self.bars {
            if let Entry::Vacant(slot) = by_key.entry((&bar.cat, &bar.series)) {
                slot.insert(bar);
                by_cat.entry(&bar.cat).or_default().push(bar);
            }
        }
        let mut ribbons = Vec::new();
        let mut labels = Vec::new();
        for pair in cats.windows(2) {
            for from in by_cat.get(pair[0].as_str()).into_iter().flatten() {
                let Some(to) = by_key.get(&(pair[1].as_str(), from.series.as_str())) else { continue };
                // The edge that faces the other bar is the one nearer it along the category axis.
                let ((x, y, w, h), (x2, y2, w2, h2)) = match (horizontal, from.rect, to.rect) {
                    (true, a, b) if a.1 > b.1 => (b, a),
                    (false, a, b) if a.0 > b.0 => (b, a),
                    (_, a, b) => (a, b),
                };
                if (if horizontal { w.min(w2) } else { h.min(h2) }) <= 0.0 {
                    continue;
                }
                let outline = if horizontal {
                    PathData::new().move_to(x, y + h).line_to(x + w, y + h).line_to(x2 + w2, y2).line_to(x2, y2)
                } else {
                    PathData::new().move_to(x + w, y).line_to(x + w, y + h).line_to(x2, y2 + h2).line_to(x2, y2)
                };
                let fill = dbt_charts_color::recede(&from.color, canvas, RIBBON_CONTRAST)?;
                if let Some(plan) = &self.labels {
                    labels.push(plan.node(from, to, ((x, y, w, h), (x2, y2, w2, h2)), &fill, canvas)?);
                }
                // Hover keeps a ribbon lit while either bar it joins is, and a legend preview recedes it with its series.
                let mut attrs = Attrs::new().set("fill", fill);
                if let Some(key) = &from.legend_key {
                    attrs = attrs.set("data-dbt-series", key.as_str());
                }
                if let (Some(a), Some(b)) = (&from.aria, &to.aria) {
                    attrs = attrs.set("data-dbt-ribbon-from", a.as_str()).set("data-dbt-ribbon-to", b.as_str());
                }
                ribbons.push(Node::path(Role::Chrome, outline.close(), attrs));
            }
        }
        if ribbons.is_empty() {
            return Ok(None);
        }
        let attrs = Attrs::new().set("class", "role-ribbon").set("data-dct-role", "ribbon").set("aria-hidden", "true");
        plot.insert(at, Node::group(Role::Chrome, attrs, ribbons));
        let group = Attrs::new().set("class", "role-ribbon-label").set("data-dct-role", "ribbon-label");
        Ok((!labels.is_empty()).then(|| Node::group(Role::Chrome, group, labels)))
    }
}

/// The least contrast a ribbon label's ink keeps against what it sits on: the ribbon or the canvas.
const RIBBON_INK_CONTRAST: f64 = 4.5;

impl LabelPlan<'_> {
    /// The label of the ribbon from `from`'s bar to `to`'s: what the stage lost, in the template's
    /// terms. `flow` is the two bars' boxes, the nearer one first.
    fn node(&self, from: &RibbonBar, to: &RibbonBar, flow: (Rect, Rect), fill: &str, canvas: &str) -> Result<Node, String> {
        let ctx = &self.ctx;
        let stage = |bar: &RibbonBar| -> Result<(&Map<String, Value>, f64), String> {
            let row = ctx
                .rows
                .iter()
                .find(|row| row_str(row, ctx.cat_field) == bar.cat && ctx.color_field.is_none_or(|color| row_str(row, color) == bar.series))
                .ok_or_else(|| format!("bar: no row for the ribbon's stage {:?}", bar.cat))?;
            let value = row_num(row, ctx.val_field).ok_or_else(|| format!("bar: the ribbon's stage {:?} has no value", bar.cat))?;
            Ok((row, value))
        };
        let ((_, before), (row, after)) = (stage(from)?, stage(to)?);
        if before == 0.0 {
            return Err(format!("bar: a ribbon label's percent divides by the stage before, {:?}, whose value is 0", from.cat));
        }
        let lost = after - before;
        let lines = self
            .template
            .render(&RowScope { row, names: &[("value", lost), ("percent", lost / before)] })
            .map_err(|e| owned_by("bar", e))?;
        let [text] = lines.as_slice() else {
            return Err(format!("bar: a ribbon label is one line; its template printed {}", lines.len()));
        };
        let ((x, y, w, h), (x2, y2, w2, _)) = flow;
        let (size, width) = (ctx.font.size, ctx.fonts.measure(text, &ctx.font.family, ctx.font.size)?);
        let (cy, thickness) = ((y + h + y2) / 2.0, y2 - (y + h));
        let fits = w.min(w2) >= width + 2.0 * LABEL_FIT_PADDING && thickness >= LABEL_FIT_LINE_HEIGHT_MULTIPLIER * size;
        let authored = ctx.font.color.as_deref().filter(|c| *c != "series");
        let (at_x, anchor, align, ink) = if fits {
            // Inside, the font's color or the neutral ink, never the series': a ribbon is already its tint.
            let base = authored.unwrap_or(NEUTRAL_INK);
            (((x + w / 2.0) + (x2 + w2 / 2.0)) / 2.0, "middle", "center", dbt_charts_color::ensure_readable_ink(base, fill, RIBBON_INK_CONTRAST)?)
        } else {
            // As a bar's own outside label: the font's color, else the series' (here the nearer bar's).
            let base = authored.unwrap_or(&from.color);
            (((x + w) + (x2 + w2)) / 2.0 + LABEL_FIT_PADDING, "start", "left", dbt_charts_color::ensure_readable_ink(base, canvas, RIBBON_INK_CONTRAST)?)
        };
        let mut attrs = Attrs::new()
            .set("text-anchor", anchor)
            .vega("font-family", ctx.font.family.as_str())
            .num("font-size", size)
            .set("font-weight", ctx.font.weight.as_str())
            .set("fill", ink);
        if let (Some(a), Some(b)) = (&from.aria, &to.aria) {
            attrs = attrs.set("data-dbt-ribbon-from", a.as_str()).set("data-dbt-ribbon-to", b.as_str());
        }
        let ink_box = crate::vega_fit::text_ink(at_x, cy, width, size, align, "middle");
        let bounds = scene::Bounds { x1: ink_box.x1, y1: ink_box.y1, x2: ink_box.x2, y2: ink_box.y2 };
        Ok(Node::text(Role::Label, (at_x, cy + measure::baseline_shift("middle", size)), attrs, text.as_str()).with_text_bounds(bounds))
    }
}
