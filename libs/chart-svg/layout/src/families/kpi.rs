//! A KPI card: the headline number, with the magnitude word beside it in the affix face, and the
//! label under it, stacked. `plan` decides what a width and the style decide (the card's least
//! height, whether the label fits), `draw` places the nodes in a slot of a given height.
//!
//! The rules are `chart-renderer/src/families/kpi.rs`'s (`vertical`, `card_height`,
//! `wrap_label_lines`, `stacked`), cited at each constant; the card's `<svg>` is part of the DOM
//! contract and is emitted here. The faces are read off the chart's total style ([`KpiFaces::of`]); everything else a width or the style does not decide is refused by name.

use bind::model::Kpi;
use board_model::style::board::{EmojiMode, OverflowMode};
use board_model::style::font::{FontSlant, LetterCase, SingleLineFontStyle, TextDecoration};
use board_model::style::kpi::{KpiChartStyle, KpiVariant};
use diagnostic::{refuse, Diagnostic};
use measure::font_files::font_path;
use measure::fonts::{cached_measurer, css_weight_axis};
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::Face;

/// kpi.rs:30-31. The share of a font's size that a capital and a descender take.
const CAP_HEIGHT_RATIO: f64 = 0.7;
const DESCENDER_RATIO: f64 = 0.2;
/// kpi.rs:34. The least gap between the value baseline and the label block.
const MINIMUM_TITLE_VALUE_GAP: f64 = 14.0;
/// kpi.rs:`value_tspans`. The kern before the suffix.
const SUFFIX_KERN: f64 = 2.0;
/// kpi.rs:`vertical`. A label line is its size and this much.
const LABEL_LEADING: f64 = 4.0;

/// kpi.rs:`FALLBACK_SLOT_WEIGHT` and table.rs:`VALID_FONT_WEIGHTS` (board.py:36-38). A weight outside
/// the set is painted, and measured, as 500.
const VALID_FONT_WEIGHTS: [&str; 11] = ["normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"];
const FALLBACK_SLOT_WEIGHT: &str = "500";

/// kpi.rs:`SlotFont::resolve`: the `font-weight` a slot paints.
fn slot_weight(face: &Face) -> String {
    let css = face.weight.attr();
    if VALID_FONT_WEIGHTS.contains(&css.as_str()) {
        css
    } else {
        FALLBACK_SLOT_WEIGHT.to_string()
    }
}

/// The resolved faces of the three text slots a card paints.
#[derive(Debug, Clone, PartialEq)]
pub struct KpiFaces {
    pub value: Face,
    pub affix: Face,
    pub label: Face,
}

impl KpiFaces {
    /// The faces `style` paints its three slots in, each stack drawn under the board's emoji mode.
    pub fn of(style: &KpiChartStyle, emoji: &EmojiMode) -> Self {
        let face = |font: &SingleLineFontStyle| Face::of(font.family.as_str(), font.size, &font.weight, font.color.as_str(), emoji);
        Self { value: face(&style.value.font), affix: face(&style.value.affix.font), label: face(&style.label.font) }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct KpiCard<'a> {
    pub kpi: &'a Kpi,
    pub style: &'a KpiChartStyle,
    pub faces: &'a KpiFaces,
}

/// What a width decides, shared by the least height and the drawing.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    width: f64,
    value_baseline: f64,
    label_baseline: f64,
    /// The card's least height: its slots, padding included. A stacked card grows to it.
    pub natural_height: f64,
}

fn refuse_slot(font: &SingleLineFontStyle, slot: &str) -> Result<(), Diagnostic> {
    if font.style != FontSlant::Normal || font.decoration != TextDecoration::None || font.case != LetterCase::None {
        return Err(Diagnostic::refused(refuse!("an italic, decorated or recased KPI {slot} font is not supported")));
    }
    Ok(())
}

/// The card at `width`: its vertical rhythm, and the label held to one line that fits.
///
/// `fonts_dir` holds the served faces; the label is measured as production's wrap does
/// (`measure::font_files::font_path`, instanced at the label's weight). Production cuts a label
/// wider than the card to an ellipsis and raises a warning, which this does not model.
pub fn plan(card: &KpiCard, fonts_dir: &str, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    if style.variant != KpiVariant::Stacked {
        return Err(Diagnostic::refused(refuse!("a KPI variant other than stacked is not supported")));
    }
    if style.align.is_some() {
        return Err(Diagnostic::refused(refuse!("a KPI `align` is not supported")));
    }
    if style.base.background.is_some() {
        return Err(Diagnostic::refused(refuse!("a KPI card background is not supported")));
    }
    refuse_slot(&style.value.font, "value")?;
    refuse_slot(&style.value.affix.font, "affix")?;
    refuse_slot(&style.label.font, "label")?;

    let pad = &style.content_padding;
    let label = &card.faces.label;
    // kpi.rs:`wrap_label_lines`: `compute_title_limit` is `int(width - left - right)`, none at or below zero.
    let limit = (width - pad.left - pad.right) as i64;
    if let (Some(text), true) = (&card.kpi.label, limit > 0) {
        let measurer = cached_measurer(&font_path(fonts_dir, &label.family), 0, css_weight_axis(&slot_weight(label))?);
        if !measurer.is_available() {
            return Err("kpi: the label font file cannot be read to measure text".into());
        }
        if measurer.measure(text, label.size) > limit as f64 {
            return Err(Diagnostic::refused(refuse!("a KPI label wider than its card is not supported")));
        }
    }

    // kpi.rs:`vertical`, with no support row.
    let value_baseline = pad.top + card.faces.value.size * CAP_HEIGHT_RATIO;
    let label_baseline = value_baseline + MINIMUM_TITLE_VALUE_GAP + label.size * CAP_HEIGHT_RATIO;
    // A single-line overflow mode reserves one label line, the others two; the slot is reserved
    // whether or not there is a label.
    let reserved_lines = if matches!(style.label.overflow, OverflowMode::Clip | OverflowMode::Truncate) { 1.0 } else { 2.0 };
    let label_slot_bottom = label_baseline + (reserved_lines - 1.0) * (label.size + LABEL_LEADING);
    let natural_height = label_slot_bottom + label.size * DESCENDER_RATIO + pad.bottom;
    Ok(Plan { width, value_baseline, label_baseline, natural_height })
}

fn face_attrs(face: &Face) -> Attrs {
    Attrs::new().quick("font-family", face.family.as_str()).num("font-size", face.size).quick("font-weight", slot_weight(face))
}

fn tspan(y: f64, dx: Option<f64>, attrs: Attrs, text: &str) -> Run {
    Run::Tspan { x: None, y: Some(y), dx, dy: None, attrs, runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] }
}

/// The newline a card's text has always carried between its runs.
fn newline() -> Run {
    Run::Chars(Chars::new("\n", Escape::Quick))
}

/// The card as nodes: its root `<svg>`, drawn in a slot `height` tall (never less than the plan's
/// natural height).
pub fn draw(card: &KpiCard, plan: &Plan, height: f64) -> Vec<Node> {
    let (faces, left) = (card.faces, card.style.content_padding.left);
    let mut runs = vec![tspan(plan.value_baseline, None, face_attrs(&faces.value).quick("fill", faces.value.color.as_str()), &card.kpi.number)];
    if !card.kpi.suffix.is_empty() {
        runs.push(tspan(plan.value_baseline, Some(SUFFIX_KERN), face_attrs(&faces.affix).quick("fill", faces.affix.color.as_str()), &card.kpi.suffix));
    }
    let attrs = Attrs::new().quick("text-anchor", "start");
    let mut parts = vec![Node::new(Role::Label, attrs, Shape::Text { at: (left, plan.value_baseline), bounds: None, runs })];
    if let Some(label) = &card.kpi.label {
        let line = Run::Tspan {
            x: Some(left),
            y: Some(plan.label_baseline),
            dx: None,
            dy: None,
            attrs: Attrs::new(),
            runs: vec![Run::Chars(Chars::new(label.as_str(), Escape::Quick))],
        };
        let attrs = Attrs::new()
            .quick("data-authored-kind", "label")
            .quick("text-anchor", "start")
            .extend(face_attrs(&faces.label))
            .quick("fill", faces.label.color.as_str());
        let shape = Shape::Text { at: (left, plan.label_baseline), bounds: None, runs: vec![newline(), line, newline()] };
        parts.push(Node::new(Role::Label, attrs, shape));
    }
    let extent = Extent { width: plan.width, height: height.max(plan.natural_height) };
    vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent, children: parts })]
}
