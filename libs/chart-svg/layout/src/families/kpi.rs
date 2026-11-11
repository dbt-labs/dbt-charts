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
use board_model::style::board::{EmojiMode, OverflowMode};
use board_model::style::font::{FontSlant, LetterCase, SingleLineFontStyle, TextDecoration};
use board_model::style::kpi::{KpiChartStyle, KpiSupportFontStyle, KpiVariant};
use diagnostic::{refuse, Diagnostic};
use measure::font_files::font_path;
use measure::fonts::{cached_measurer, css_weight_axis};
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
    /// The faces `style` paints its three slots in, each stack drawn under the board's emoji mode.
    pub fn of(style: &KpiChartStyle, emoji: &EmojiMode) -> Self {
        let face = |font: &SingleLineFontStyle| Face::of(font.family.as_str(), font.size, &font.weight, font.color.as_str(), emoji);
        let support = |font: &KpiSupportFontStyle| Face::of(font.family.as_str(), font.size, &font.weight, "", emoji);
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
    /// The card's least height: its slots, padding included. A stacked card grows to it; an inline or
    /// compact card keeps the height it is given, so its least is none.
    pub natural_height: f64,
}

fn refuse_slot(style: FontSlant, decoration: TextDecoration, case: LetterCase, slot: &str) -> Result<(), Diagnostic> {
    if style != FontSlant::Normal || decoration != TextDecoration::None || case != LetterCase::None {
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
    let variant = style.variant;
    if style.align.is_some() {
        return Err(Diagnostic::refused(refuse!("a KPI `align` is not supported")));
    }
    if style.base.background.is_some() {
        return Err(Diagnostic::refused(refuse!("a KPI card background is not supported")));
    }
    let single = |font: &SingleLineFontStyle, slot: &str| refuse_slot(font.style, font.decoration, font.case, slot);
    single(&style.value.font, "value")?;
    single(&style.value.affix.font, "affix")?;
    single(&style.label.font, "label")?;
    if !card.kpi.glyph.is_empty() {
        let font = if card.kpi.arrow { &style.value.glyph.arrow.font } else { &style.value.glyph.slot.font };
        single(font, "glyph")?;
    }
    if card.kpi.support.is_some() {
        for (font, slot) in [(&style.support.font, "support"), (&style.support.glyph.font, "support glyph")] {
            refuse_slot(font.style, font.decoration, font.case, slot)?;
        }
    }

    let pad = &style.content_padding;
    let label = &card.faces.label;
    // kpi.rs:`wrap_label_lines`, which `layout` runs for every arrangement and warns from:
    // `compute_title_limit` is `int(width - left - right)`, none at or below zero.
    let limit = (width - pad.left - pad.right) as i64;
    let stacked = variant == KpiVariant::Stacked;
    if let (Some(text), true) = (&card.kpi.label, limit > 0) {
        let measurer = cached_measurer(&font_path(fonts_dir, &label.family), 0, css_weight_axis(&slot_weight(label))?);
        if !measurer.is_available() {
            return Err("kpi: the label font file cannot be read to measure text".into());
        }
        if measurer.measure(text, label.size) > limit as f64 {
            return Err(Diagnostic::refused(refuse!("a KPI label wider than its card is not supported")));
        }
    }

    // kpi.rs:`vertical`.
    let faces = card.faces;
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
    // kpi.rs:`fit_explainer`: production cuts an explainer the row overflows to an ellipsis and
    // raises a warning, which this does not model; the glyph and value are never cut.
    if let (Some(support), true) = (card.kpi.support.as_ref().filter(|support| !support.explainer.is_empty()), stacked) {
        let runs = support_runs(card.faces, support);
        let mut total = 0.0;
        for (face, _, text) in &runs {
            let measurer = cached_measurer(&font_path(fonts_dir, &face.family), 0, css_weight_axis(&slot_weight(face))?);
            if !measurer.is_available() {
                return Err("kpi: the support font file cannot be read to measure text".into());
            }
            total += measurer.measure(text, face.size);
        }
        if total > width - pad.left - pad.right {
            return Err(Diagnostic::refused(refuse!("a KPI support row wider than its card is not supported")));
        }
    }
    let value_run_width = value_run_width(card, fonts_dir)?;
    // kpi.rs:`arrangement`: an inline card whose one row overflows the card stacks, with a warning
    // this does not raise, unless it is a bare value that stacking would overflow the same.
    if variant == KpiVariant::Inline && (card.kpi.label.is_some() || card.kpi.support.is_some()) {
        let mut row = value_run_width;
        if let Some(label) = &card.kpi.label {
            row += INLINE_GAP + measure_text(fonts_dir, &faces.label, label, false)?;
        }
        if let Some(support) = &card.kpi.support {
            row += INLINE_GAP + runs_width(fonts_dir, &support_runs(faces, support))?;
        }
        if row > width - pad.left - pad.right {
            return Err(Diagnostic::refused(refuse!("an inline KPI that overflows its card and falls back to stacked is not supported")));
        }
    }
    let natural_height = if stacked { natural_height } else { 0.0 };
    Ok(Plan { width, variant, value_run_width, value_baseline, prefix_baseline, glyph_baseline, label_baseline, support_baseline, natural_height })
}

/// kpi.rs:`SlotFont::measure`: `text` in `face` at its painted weight.
fn measure_text(fonts_dir: &str, face: &Face, text: &str, numeric: bool) -> Result<f64, Diagnostic> {
    // `weighted_width`'s file for the face (`font_file`): the three an upright run measures in.
    let file = match font_file(&face.family, false, numeric) {
        "dbt Sans Tabular" => "DBTSansTabular-Regular.ttf",
        "Source Serif 4" => "SourceSerif4Variable.ttf",
        _ => "InterVariable.ttf",
    };
    let measurer = cached_measurer(&format!("{fonts_dir}/{file}"), 0, css_weight_axis(&slot_weight(face))?);
    if !measurer.is_available() {
        return Err("kpi: the font file cannot be read to measure text".into());
    }
    Ok(measurer.measure(text, face.size))
}

fn runs_width(fonts_dir: &str, runs: &[(&Face, &str, String)]) -> Result<f64, Diagnostic> {
    runs.iter().map(|(face, _, text)| measure_text(fonts_dir, face, text, false)).sum()
}

/// kpi.rs:`measure_value_run`: each part in its own face, the number as tabular figures, and the
/// kern before the suffix.
fn value_run_width(card: &KpiCard, fonts_dir: &str) -> Result<f64, Diagnostic> {
    let (kpi, faces) = (card.kpi, card.faces);
    let mut width = 0.0;
    if !kpi.glyph.is_empty() {
        width += measure_text(fonts_dir, glyph_face(card), &format!("{} ", kpi.glyph), false)?;
    }
    if !kpi.prefix.is_empty() {
        width += measure_text(fonts_dir, &faces.affix, &kpi.prefix, false)?;
    }
    width += measure_text(fonts_dir, &faces.value, &kpi.number, true)?;
    if !kpi.suffix.is_empty() {
        width += measure_text(fonts_dir, &faces.affix, &kpi.suffix, false)? + SUFFIX_KERN;
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
    Attrs::new().quick("font-family", face.family.as_str()).num("font-size", face.size).quick("font-weight", slot_weight(face))
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

fn text_row(at: (f64, f64), runs: Vec<Run>) -> Node {
    Node::new(Role::Label, Attrs::new().quick("text-anchor", "start"), Shape::Text { at, bounds: None, runs })
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
    let (faces, kpi, left) = (card.faces, card.kpi, card.style.content_padding.left);
    let extent = Extent { width: plan.width, height: height.max(plan.natural_height) };
    let parts = match plan.variant {
        KpiVariant::Stacked => stacked(card, plan, left),
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
                    let attrs = Attrs::new().quick("data-authored-kind", "label").extend(face_attrs(&faces.label)).quick("fill", faces.label.color.as_str());
                    runs.push(tspan_at(Some(INLINE_GAP), attrs, label));
                }
                if let Some(support) = &kpi.support {
                    for (index, (face, fill, text)) in support_runs(faces, support).into_iter().enumerate() {
                        runs.push(tspan_at((index == 0).then_some(INLINE_GAP), face_attrs(face).quick("fill", fill), &text));
                    }
                }
                vec![text_row((left, value_baseline), runs)]
            } else {
                let mut parts = vec![text_row((left, value_baseline), runs)];
                let x = left + plan.value_run_width + INLINE_GAP;
                let (top, bottom, kind_on_top, kind_on_bottom) = compact_column(card);
                // The top line sits one top-line-font line-height above the baseline so the bottom
                // line stays flush with the value baseline: the label's when label and support share
                // the column, else a split-support piece's.
                let top_line_size = if kpi.label.is_some() && kpi.support.is_some() { faces.label.size } else { faces.support.size };
                if !top.is_empty() {
                    parts.push(text_row((x, value_baseline - (top_line_size + LABEL_LEADING)), line_tspans(top, kind_on_top)));
                }
                if !bottom.is_empty() {
                    parts.push(text_row((x, value_baseline), line_tspans(bottom, kind_on_bottom)));
                }
                parts
            }
        }
    };
    vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent, children: parts })]
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
fn stacked(card: &KpiCard, plan: &Plan, left: f64) -> Vec<Node> {
    let (faces, kpi) = (card.faces, card.kpi);
    let mut parts = vec![text_row((left, plan.value_baseline), value_runs(card, plan.value_baseline, plan.prefix_baseline, plan.glyph_baseline))];
    if let Some(label) = &kpi.label {
        let line = Run::Tspan { x: Some(left), y: Some(plan.label_baseline), dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new(label.as_str(), Escape::Quick))] };
        let attrs = Attrs::new().quick("data-authored-kind", "label").quick("text-anchor", "start").extend(face_attrs(&faces.label)).quick("fill", faces.label.color.as_str());
        let shape = Shape::Text { at: (left, plan.label_baseline), bounds: None, runs: vec![newline(), line, newline()] };
        parts.push(Node::new(Role::Label, attrs, shape));
    }
    if let Some(support) = &kpi.support {
        parts.push(text_row((left, plan.support_baseline), line_tspans(support_runs(faces, support), false)));
    }
    parts
}
