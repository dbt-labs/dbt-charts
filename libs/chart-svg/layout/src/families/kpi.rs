//! A KPI card: the headline number, with the magnitude word beside it in the affix face, and the
//! label and support row under it (stacked), on one row (inline) or in a column beside it (compact). `plan` decides what a width and the style decide (the card's least
//! height, whether the label fits), `draw` places the nodes in a slot of a given height.
//!
//! The headline's glyph rides before it in the glyph face and its prefix (sign, currency symbol)
//! in the affix face, each on its own baseline; the support row, when there is one, sits under the
//! label's reserved slot.
//!
//! The rules are `chart-renderer/src/families/kpi.rs`'s (`vertical`, `card_height`,
//! `wrap_label_lines`, `affix_baselines`, `support_runs`, `stacked`), cited at each constant; the
//! card's `<svg>` is part of the DOM contract and is emitted here. The faces are read off the
//! chart's total style ([`KpiFaces::of`]); everything else a width or the style does not decide is
//! refused by name.
//!
//! [`KpiFaces::of`] reads each slot's face off the chart's own total `KpiChartStyle` (a chart-local
//! font differs from its neighbor's) under the board's emoji mode; a weight outside the valid set is
//! painted as 500; the vertical rhythm is production's, ported as cited constants.

use bind::bound::{Kpi, KpiSupport};
use crate::families::failed::Weight;
use board_model::primitives::LinkOpen;
use board_model::style::board::{EmojiMode, OverflowMode};
use board_model::style::font::{FontSlant, SingleLineFontStyle, TextDecoration};
use board_model::style::kpi::{KpiAlign, KpiChartStyle, KpiSupportFontStyle, KpiVariant};
use diagnostic::{diag, Diagnostic};
use measure::font_files::font_file as family_file;
use measure::fonts::css_weight_axis;
use measure::shaped::FontBook;
use measure::weighted::font_file;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::Face;

/// kpi.rs:30-31. The share of a font's size that a capital and a descender take.
const CAP_HEIGHT_RATIO: f64 = 0.7;
const DESCENDER_RATIO: f64 = 0.2;
/// kpi.rs:34. The least gap between the value baseline and the label block.
const MINIMUM_TITLE_VALUE_GAP: f64 = 14.0;
/// kpi.rs:`AFFIX_ELEVATION`. The share of the size gap between the value and its prefix or glyph
/// that the prefix or glyph baseline rides above the value baseline.
const AFFIX_ELEVATION: f64 = 0.37;
/// kpi.rs:`INLINE_GAP`. The gap before each slot of an inline row, and between a compact card's value
/// and its right column.
const INLINE_GAP: f64 = 14.0;
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
    pub glyph: Face,
    /// The face of the glyph a `good` sign draws.
    pub arrow: Face,
    pub label: Face,
    /// The support row's value and explainer face, and its glyph's; their ink is the bound row's.
    pub support: Face,
    pub support_glyph: Face,
}

impl KpiFaces {
    /// These faces with what a matched `style.conditional` rule set on the card: the value's and the label's
    /// weight, and the label's color (the value's color is the headline run's `ink`).
    pub fn ruled_by(mut self, kpi: &Kpi) -> Self {
        if let Some(weight) = &kpi.value_weight {
            self.value.weight = Weight::of(weight);
        }
        if let Some(weight) = &kpi.label_weight {
            self.label.weight = Weight::of(weight);
        }
        if let Some(color) = &kpi.label_color {
            self.label.color = color.clone();
        }
        self
    }

    /// The faces `style` paints its three slots in, each stack drawn under the board's emoji mode.
    pub fn of(style: &KpiChartStyle, emoji: &EmojiMode) -> Self {
        let slant = |face: Face, style: FontSlant, decoration: TextDecoration| Face {
            italic: style == FontSlant::Italic,
            decoration: (decoration != TextDecoration::None).then(|| decoration.as_str().to_string()),
            ..face
        };
        let face = |font: &SingleLineFontStyle| slant(Face::of(font.family.as_str(), font.size, &font.weight, font.color.as_str(), emoji), font.style, font.decoration);
        let support = |font: &KpiSupportFontStyle| slant(Face::of(font.family.as_str(), font.size, &font.weight, "", emoji), font.style, font.decoration);
        Self {
            value: face(&style.value.font),
            affix: face(&style.value.affix.font),
            glyph: face(&style.value.glyph.slot.font),
            arrow: face(&style.value.glyph.arrow.font),
            label: face(&style.label.font),
            support: support(&style.support.font),
            support_glyph: support(&style.support.glyph.font),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct KpiCard<'a> {
    pub kpi: &'a Kpi,
    pub style: &'a KpiChartStyle,
    pub faces: &'a KpiFaces,
}

/// The face the headline glyph is drawn in: the arrow's when a `good` sign drew it.
fn glyph_face<'a>(card: &KpiCard<'a>) -> &'a Face {
    if card.kpi.arrow { &card.faces.arrow } else { &card.faces.glyph }
}

/// What a width decides, shared by the least height and the drawing.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    width: f64,
    variant: KpiVariant,
    /// The value run's width, which sets where a compact card's right column starts.
    value_run_width: f64,
    value_baseline: f64,
    prefix_baseline: f64,
    glyph_baseline: f64,
    label_baseline: f64,
    support_baseline: f64,
    /// Where a stacked card's value, label lines and support runs start under its `align`; an inline
    /// card's one row and a compact card's block start at `value_x`.
    value_x: f64,
    label_xs: Vec<f64>,
    support_x: f64,
    /// The label as drawn (`wrap_label_lines`): at most two lines, and whether the overflow mode cut
    /// text off, which the host reports as a warning.
    label_lines: Vec<String>,
    pub label_truncated: bool,
    /// An inline card whose row overflowed stacked instead, which the host reports as a warning.
    pub inline_fell_back: bool,
    /// The explainer the support row overflowed was cut to an ellipsis (its run's text as drawn), which
    /// the host reports as a warning.
    support_cut: Option<String>,
    /// `align` was set but a run was wider than the card, so every run is left-anchored, which the host
    /// reports as a warning.
    pub align_overflowed: bool,
    /// The card's least height: its slots, padding included. A stacked card grows to it; an inline or
    /// compact card keeps the height it is given, so its least is none.
    pub natural_height: f64,
}


impl Plan {
    /// Whether the support row's explainer was cut, which the host reports as a warning.
    pub fn support_truncated(&self) -> bool {
        self.support_cut.is_some()
    }
}

/// The card at `width`: its vertical rhythm, its arrangement (an inline row that overflows stacks),
/// and the label wrapped or cut by its overflow mode.
///
/// `book` holds the served faces; the label is wrapped as production's `wrap_label_lines` does
/// (instanced at the label's weight). Whether text was cut, and whether an inline card stacked, are
/// on the plan for the host to report as warnings.
pub fn plan(card: &KpiCard, book: &FontBook, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;

    let pad = &style.content_padding;
    let label = &card.faces.label;
    let faces = card.faces;
    let value_run_width = value_run_width(card, book)?;
    // kpi.rs:`arrangement`: an inline card whose one row overflows the card stacks, with a warning,
    // unless it is a bare value that stacking would overflow the same.
    let mut variant = style.variant;
    let mut inline_fell_back = false;
    let mut inline_row = value_run_width;
    if variant == KpiVariant::Inline && (card.kpi.label.is_some() || card.kpi.support.is_some()) {
        if let Some(label) = &card.kpi.label {
            inline_row += INLINE_GAP + measure_text(book, &faces.label, label, false)?;
        }
        if let Some(support) = &card.kpi.support {
            inline_row += INLINE_GAP + runs_width(book, &support_runs(faces, support))?;
        }
        if inline_row > width - pad.left - pad.right {
            variant = KpiVariant::Stacked;
            inline_fell_back = true;
        }
    }
    let aligned = matches!(style.align, Some(KpiAlign::Center | KpiAlign::Right));
    // kpi.rs:`wrap_label_lines`, which `layout` runs for every arrangement and warns from:
    // `compute_title_limit` is `int(width - left - right)`, none at or below zero. The label wraps
    // by the overflow mode's line budget, and no more than two lines are drawn.
    let limit = (width - pad.left - pad.right) as i64;
    let stacked = variant == KpiVariant::Stacked;
    let (mut label_lines, mut label_truncated) = (Vec::new(), false);
    if let (Some(text), true) = (&card.kpi.label, limit > 0) {
        let (max_lines, ellipsis) = match style.label.overflow {
            OverflowMode::Clip => (Some(1), false),
            OverflowMode::Truncate => (Some(1), true),
            OverflowMode::Wrap => (None, false),
            OverflowMode::WrapTwo => (Some(2), true),
        };
        // `wrap_label` measures at the label's weight in the upright face, whether or not it paints italic.
        let measurer = book.file(family_file(&label.family)).map_err(diag::err_internal)?.measurer(0, css_weight_axis(&slot_weight(label))?);
        let (lines, truncated) = measure::fonts::wrap_text_precise(text, limit as f64, label.size, &measurer, max_lines, ellipsis, true);
        label_lines = lines.into_iter().take(2).collect();
        label_truncated = truncated;
    }
    if label_lines.is_empty() {
        label_lines = card.kpi.label.iter().cloned().collect();
    }

    // kpi.rs:`vertical`.
    let value_baseline = pad.top + faces.value.size * CAP_HEIGHT_RATIO;
    let prefix_baseline = value_baseline - (faces.value.size - faces.affix.size) * AFFIX_ELEVATION;
    let glyph_baseline = value_baseline - (faces.value.size - glyph_face(card).size) * AFFIX_ELEVATION;
    let label_baseline = value_baseline + MINIMUM_TITLE_VALUE_GAP + label.size * CAP_HEIGHT_RATIO;
    // A single-line overflow mode reserves one label line, the others two; the slot is reserved
    // whether or not there is a label.
    let reserved_lines = if matches!(style.label.overflow, OverflowMode::Clip | OverflowMode::Truncate) { 1.0 } else { 2.0 };
    let support_baseline = label_baseline + reserved_lines * (label.size + LABEL_LEADING);
    let natural_height = match card.kpi.support {
        Some(_) => support_baseline + faces.support.size * DESCENDER_RATIO + pad.bottom,
        None => label_baseline + (reserved_lines - 1.0) * (label.size + LABEL_LEADING) + label.size * DESCENDER_RATIO + pad.bottom,
    };
    // kpi.rs:`fit_explainer`: a stacked card's explainer the row overflows is cut to an ellipsis (the
    // glyph and value are never cut) and the full text rides a `<title>`.
    let available = width - pad.left - pad.right;
    let mut support_cut = None;
    if let (Some(support), true) = (card.kpi.support.as_ref().filter(|support| !support.explainer.is_empty()), stacked) {
        let runs = support_runs(card.faces, support);
        if let Some(((face, _, text), head)) = runs.split_last() {
            let room = available - runs_width(book, head)?;
            if measure_text(book, face, text, false)? > room {
                let spacer = if text.starts_with(' ') { " " } else { "" };
                let chars: Vec<char> = text.trim_start().chars().collect();
                let mut cut = String::new();
                for keep in (0..chars.len()).rev() {
                    let kept: String = chars[..keep].iter().collect();
                    cut = format!("{spacer}{}\u{2026}", kept.trim_end());
                    if measure_text(book, face, &cut, false)? <= room {
                        break;
                    }
                }
                support_cut = Some(cut);
            }
        }
    }
    // kpi.rs:`card_align`, `run_x`: `align` is a whole-card choice, honored only when every run fits;
    // an overflowing card is left-anchored and warns. A stacked card anchors each run on its own, an
    // inline card its one row and a compact card its value-and-column block.
    let (mut value_x, mut support_x) = (pad.left, pad.left);
    let mut label_xs = vec![pad.left; label_lines.len()];
    let mut align_overflowed = false;
    if aligned {
        let run_x = |run_width: f64| match style.align {
            Some(KpiAlign::Right) => pad.left + available - run_width,
            _ => pad.left + (available - run_width) / 2.0,
        };
        match variant {
            KpiVariant::Stacked => {
                let mut label_widths = Vec::new();
                for line in &label_lines {
                    label_widths.push(measure_text(book, &faces.label, line, false)?);
                }
                let support_width = match &card.kpi.support {
                    Some(support) => runs_width(book, &fitted_support_runs(faces, support, &support_cut))?,
                    None => 0.0,
                };
                let widest = label_widths.iter().copied().fold(value_run_width.max(support_width), f64::max);
                if widest > available {
                    align_overflowed = true;
                } else {
                    value_x = run_x(value_run_width);
                    support_x = run_x(support_width);
                    label_xs = label_widths.into_iter().map(run_x).collect();
                }
            }
            KpiVariant::Inline => {
                if inline_row > available {
                    align_overflowed = true;
                } else {
                    value_x = run_x(inline_row);
                }
            }
            KpiVariant::Compact => {
                let (top, bottom, ..) = compact_column(card);
                let column = runs_width(book, &top)?.max(runs_width(book, &bottom)?);
                let block = if top.is_empty() && bottom.is_empty() { value_run_width } else { value_run_width + INLINE_GAP + column };
                if block > available {
                    align_overflowed = true;
                } else {
                    value_x = run_x(block);
                }
            }
        }
    }
    let natural_height = if stacked { natural_height } else { 0.0 };
    Ok(Plan { width, variant, value_run_width, value_baseline, prefix_baseline, glyph_baseline, label_baseline, support_baseline, value_x, label_xs, support_x, label_lines, label_truncated, inline_fell_back, support_cut, align_overflowed, natural_height })
}

/// The support runs with the explainer's text the cut left, when the row overflowed.
fn fitted_support_runs<'a>(faces: &'a KpiFaces, support: &'a KpiSupport, cut: &Option<String>) -> Vec<(&'a Face, &'a str, String)> {
    let mut runs = support_runs(faces, support);
    if let (Some(cut), Some(last)) = (cut, runs.last_mut()) {
        last.2 = cut.clone();
    }
    runs
}

/// kpi.rs:`SlotFont::measure`: `text` in `face` at its painted weight.
fn measure_text(book: &FontBook, face: &Face, text: &str, numeric: bool) -> Result<f64, Diagnostic> {
    // `weighted_width`'s file for the face (`font_file`): the five an upright or italic run measures in.
    let file = match font_file(&face.family, face.italic, numeric) {
        "dbt Sans Tabular" => "DBTSansTabular-Regular.ttf",
        "Source Serif 4" => "SourceSerif4Variable.ttf",
        "Source Serif 4 Italic" => "SourceSerif4-Italic.ttf",
        "Inter Variable Italic" => "InterVariable-Italic.ttf",
        _ => "InterVariable.ttf",
    };
    let measurer = book.file(file).map_err(diag::err_internal)?.measurer(0, css_weight_axis(&slot_weight(face))?);
    Ok(measurer.measure(text, face.size))
}

fn runs_width(book: &FontBook, runs: &[(&Face, &str, String)]) -> Result<f64, Diagnostic> {
    runs.iter().map(|(face, _, text)| measure_text(book, face, text, false)).sum()
}

/// kpi.rs:`measure_value_run`: each part in its own face, the number as tabular figures, and the
/// kern before the suffix.
fn value_run_width(card: &KpiCard, book: &FontBook) -> Result<f64, Diagnostic> {
    let (kpi, faces) = (card.kpi, card.faces);
    let mut width = 0.0;
    if !kpi.glyph.is_empty() {
        width += measure_text(book, glyph_face(card), &format!("{} ", kpi.glyph), false)?;
    }
    if !kpi.prefix.is_empty() {
        width += measure_text(book, &faces.affix, &kpi.prefix, false)?;
    }
    width += measure_text(book, &faces.value, &kpi.number, true)?;
    if !kpi.suffix.is_empty() {
        width += measure_text(book, &faces.affix, &kpi.suffix, false)? + SUFFIX_KERN;
    }
    Ok(width)
}

/// kpi.rs:`support_runs`: the glyph (with its trailing space), the value and the explainer (behind a
/// space when a glyph or value precedes it), each as its face, ink and text. The explainer keeps
/// the board's secondary ink.
fn support_runs<'a>(faces: &'a KpiFaces, support: &'a KpiSupport) -> Vec<(&'a Face, &'a str, String)> {
    let mut runs = Vec::new();
    if !support.glyph.is_empty() {
        runs.push((&faces.support_glyph, support.glyph_fill.as_str(), format!("{} ", support.glyph)));
    }
    if !support.value.is_empty() {
        runs.push((&faces.support, support.value_fill.as_str(), support.value.clone()));
    }
    if !support.explainer.is_empty() {
        let spacer = if !support.value.is_empty() || !support.glyph.is_empty() { " " } else { "" };
        runs.push((&faces.support, support.explainer_fill.as_str(), format!("{spacer}{}", support.explainer)));
    }
    runs
}

fn face_attrs(face: &Face) -> Attrs {
    let mut attrs = Attrs::new().quick("font-family", face.family.as_str()).num("font-size", face.size).quick("font-weight", slot_weight(face));
    if face.italic {
        attrs = attrs.quick("font-style", "italic");
    }
    if let Some(decoration) = &face.decoration {
        attrs = attrs.quick("text-decoration", decoration.as_str());
    }
    attrs
}

fn tspan(y: f64, dx: Option<f64>, attrs: Attrs, text: &str) -> Run {
    Run::Tspan { x: None, y: Some(y), dx, dy: None, attrs, runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] }
}

/// The newline a card's text has always carried between its runs.
fn newline() -> Run {
    Run::Chars(Chars::new("\n", Escape::Quick))
}

/// kpi.rs:`value_tspans`: the glyph, the prefix, the number and the suffix, each in its own face and
/// on its own baseline. The glyph and prefix take the value's ink, as the slots' fonts link it.
fn value_runs(card: &KpiCard, value_baseline: f64, prefix_baseline: f64, glyph_baseline: f64) -> Vec<Run> {
    let (faces, kpi) = (card.faces, card.kpi);
    let ink = |face: &Face| kpi.ink.clone().unwrap_or_else(|| face.color.clone());
    let mut runs = Vec::new();
    if !kpi.glyph.is_empty() {
        let glyph = glyph_face(card);
        runs.push(tspan(glyph_baseline, None, face_attrs(glyph).quick("fill", ink(glyph)), &format!("{} ", kpi.glyph)));
    }
    if !kpi.prefix.is_empty() {
        runs.push(tspan(prefix_baseline, None, face_attrs(&faces.affix).quick("fill", ink(&faces.affix)), &kpi.prefix));
    }
    runs.push(tspan(value_baseline, None, face_attrs(&faces.value).quick("fill", ink(&faces.value)), &kpi.number));
    if !kpi.suffix.is_empty() {
        runs.push(tspan(value_baseline, Some(SUFFIX_KERN), face_attrs(&faces.affix).quick("fill", ink(&faces.affix)), &kpi.suffix));
    }
    runs
}

/// A row of card text, stamped with the role token (`kpi-value`, `kpi-label` or `kpi-support`) the
/// equivalence comparator's quirk classes find it by.
fn text_row(role: &str, at: (f64, f64), runs: Vec<Run>) -> Node {
    Node::new(Role::Label, Attrs::new().quick("text-anchor", "start").quick("data-dct-role", role), Shape::Text { at, bounds: None, runs })
}

/// A line's runs as tspans on no baseline of their own, so they ride their text's; the label's line
/// names its authored kind on each.
fn line_tspans(runs: Vec<(&Face, &str, String)>, label: bool) -> Vec<Run> {
    runs.into_iter()
        .map(|(face, fill, text)| {
            let attrs = if label { Attrs::new().quick("data-authored-kind", "label") } else { Attrs::new() };
            Run::Tspan { x: None, y: None, dx: None, dy: None, attrs: attrs.extend(face_attrs(face)).quick("fill", fill), runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] }
        })
        .collect()
}

/// The card as nodes: its root `<svg>`, drawn in a slot `height` tall (a stacked card never less
/// than the plan's natural height).
pub fn draw(card: &KpiCard, plan: &Plan, height: f64) -> Vec<Node> {
    let (faces, kpi) = (card.faces, card.kpi);
    let extent = Extent { width: plan.width, height: height.max(plan.natural_height) };
    let parts = match plan.variant {
        KpiVariant::Stacked => stacked(card, plan),
        KpiVariant::Inline | KpiVariant::Compact => {
            // kpi.rs:`inline`, `compact`: the value's cap box is centered in the card.
            let value_baseline = (extent.height + faces.value.size * CAP_HEIGHT_RATIO) / 2.0;
            let prefix_baseline = value_baseline - (faces.value.size - faces.affix.size) * AFFIX_ELEVATION;
            let glyph_baseline = value_baseline - (faces.value.size - glyph_face(card).size) * AFFIX_ELEVATION;
            let mut runs = value_runs(card, value_baseline, prefix_baseline, glyph_baseline);
            if plan.variant == KpiVariant::Inline {
                let at = Some(value_baseline);
                let tspan_at = |dx: Option<f64>, attrs: Attrs, text: &str| Run::Tspan { x: None, y: at, dx, dy: None, attrs, runs: vec![Run::Chars(Chars::new(text, Escape::Quick))] };
                if let Some(label) = &kpi.label {
                    let attrs = Attrs::new().quick("data-authored-kind", "label").quick("data-dct-role", "kpi-label").extend(face_attrs(&faces.label)).quick("fill", faces.label.color.as_str());
                    runs.push(tspan_at(Some(INLINE_GAP), attrs, label));
                }
                if let Some(support) = &kpi.support {
                    for (index, (face, fill, text)) in support_runs(faces, support).into_iter().enumerate() {
                        runs.push(tspan_at((index == 0).then_some(INLINE_GAP), face_attrs(face).quick("fill", fill), &text));
                    }
                }
                vec![text_row("kpi-value", (plan.value_x, value_baseline), runs)]
            } else {
                let mut parts = vec![text_row("kpi-value", (plan.value_x, value_baseline), runs)];
                let x = plan.value_x + plan.value_run_width + INLINE_GAP;
                let (top, bottom, kind_on_top, kind_on_bottom) = compact_column(card);
                // The top line sits one top-line-font line-height above the baseline so the bottom
                // line stays flush with the value baseline: the label's when label and support share
                // the column, else a split-support piece's.
                let top_line_size = if kpi.label.is_some() && kpi.support.is_some() { faces.label.size } else { faces.support.size };
                if !top.is_empty() {
                    parts.push(text_row(if kind_on_top { "kpi-label" } else { "kpi-support" }, (x, value_baseline - (top_line_size + LABEL_LEADING)), line_tspans(top, kind_on_top)));
                }
                if !bottom.is_empty() {
                    parts.push(text_row(if kind_on_bottom { "kpi-label" } else { "kpi-support" }, (x, value_baseline), line_tspans(bottom, kind_on_bottom)));
                }
                parts
            }
        }
    };
    // Production wraps the value row alone on a stacked card and every row on the others.
    let parts = match (&kpi.link, plan.variant) {
        (None, _) => parts,
        (Some(href), KpiVariant::Stacked) => {
            let mut rest = parts.into_iter();
            let value = rest.next().into_iter().collect();
            std::iter::once(link_node(href.as_str(), kpi.link_open, value)).chain(rest).collect()
        }
        (Some(href), _) => vec![link_node(href.as_str(), kpi.link_open, parts)],
    };
    vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent, children: parts })]
}

/// An `<a>` as production's `Anchor::attrs` prints it: the href, how it opens (a page link says nothing)
/// and the `_blank` target of a new tab.
fn link_node(href: &str, open: LinkOpen, children: Vec<Node>) -> Node {
    let (opens, target) = match open {
        LinkOpen::Page => (None, None),
        LinkOpen::Modal => (Some("modal"), None),
        LinkOpen::Preview => (Some("preview"), None),
        LinkOpen::NewTab => (Some("new_tab"), Some("_blank")),
    };
    let attrs = Attrs::new().quick("href", href);
    let attrs = opens.map_or(attrs.clone(), |value| attrs.quick("data-dbt-link-open", value));
    let attrs = target.map_or(attrs.clone(), |value| attrs.quick("target", value));
    Node::new(Role::Chrome, attrs, Shape::Link(children))
}

/// kpi.rs:`compact_column`: the runs of the compact card's top and bottom lines beside the value,
/// and whether the label's authored kind rides the top line or the bottom one. A label over a support row shares the
/// column; a label alone sits on the baseline; a support row with both a value and an explainer
/// splits, its glyph and value above and its explainer (unspaced) on the baseline.
fn compact_column<'a>(card: &KpiCard<'a>) -> (Vec<(&'a Face, &'a str, String)>, Vec<(&'a Face, &'a str, String)>, bool, bool) {
    let (faces, kpi) = (card.faces, card.kpi);
    let label: Vec<_> = kpi.label.iter().map(|text| (&faces.label, faces.label.color.as_str(), text.clone())).collect();
    let mut support = kpi.support.as_ref().map(|support| support_runs(faces, support)).unwrap_or_default();
    let (has_label, has_support) = (!label.is_empty(), !support.is_empty());
    match &kpi.support {
        _ if has_label && has_support => (label, support, true, false),
        _ if has_label => (Vec::new(), label, false, true),
        Some(row) if !row.value.is_empty() && !row.explainer.is_empty() => {
            support.pop();
            (support, vec![(&faces.support, row.explainer_fill.as_str(), row.explainer.clone())], false, false)
        }
        _ => (Vec::new(), support, false, false),
    }
}

/// kpi.rs:`stacked`: the value, the label in its reserved slot and the support row below it.
fn stacked(card: &KpiCard, plan: &Plan) -> Vec<Node> {
    let (faces, kpi) = (card.faces, card.kpi);
    let mut parts = vec![text_row("kpi-value", (plan.value_x, plan.value_baseline), value_runs(card, plan.value_baseline, plan.prefix_baseline, plan.glyph_baseline))];
    if let Some(label) = &kpi.label {
        let mut runs = Vec::new();
        if plan.label_truncated {
            runs.push(Run::Title(Chars::new(label.as_str(), Escape::Quick)));
        }
        for (index, (line, x)) in plan.label_lines.iter().zip(&plan.label_xs).enumerate() {
            let y = plan.label_baseline + index as f64 * (faces.label.size + LABEL_LEADING);
            runs.push(newline());
            runs.push(Run::Tspan { x: Some(*x), y: Some(y), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(line.as_str(), Escape::Quick))] });
        }
        runs.push(newline());
        let attrs = Attrs::new().quick("data-authored-kind", "label").quick("data-dct-role", "kpi-label").quick("text-anchor", "start").extend(face_attrs(&faces.label)).quick("fill", faces.label.color.as_str());
        let at = (plan.label_xs[0], plan.label_baseline);
        parts.push(Node::new(Role::Label, attrs, Shape::Text { at, bounds: None, runs }));
    }
    if let Some(support) = &kpi.support {
        let mut runs = Vec::new();
        if plan.support_cut.is_some() {
            runs.push(Run::Title(Chars::new(support.explainer.as_str(), Escape::Quick)));
        }
        runs.extend(line_tspans(fitted_support_runs(faces, support, &plan.support_cut), false));
        parts.push(text_row("kpi-support", (plan.support_x, plan.support_baseline), runs));
    }
    parts
}

impl crate::family::Family for KpiCard<'_> {
    type Plan = Plan;

    fn plan(&self, env: &crate::family::Env, width: f64) -> Result<Plan, Diagnostic> {
        plan(self, env.book, width)
    }

    fn draw(&self, plan: Plan, height: f64) -> Result<Vec<Node>, Diagnostic> {
        Ok(draw(self, &plan, height))
    }
}
