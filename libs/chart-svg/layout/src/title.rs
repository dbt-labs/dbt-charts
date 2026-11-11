//! A chart's title block: its title and subtitle as they draw in the width the
//! card gives.

use board_model::facet::FontWeightValue;
use board_model::style::board::OverflowMode;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use model_core::Engine;

use crate::markdown_style::heading_ramp;
use crate::tier::WidthTier;

/// An object title sits at this heading level before its width tier's offset
/// (typography.py:`OBJECT_TITLE_ANCHOR_LEVEL`).
const OBJECT_TITLE_ANCHOR_LEVEL: i64 = 2;

/// The size, weight and family stack an object title paints in at a card's outer `width`
/// (typography.py:`chart_title_spec`): the heading ramp at the tier's level, the compact weight on a
/// tiny card, the title slot's family on a medium or wide one and the chart font's below.
pub fn chart_title_spec(style: &Style, width: f64) -> Result<(f64, FontWeightValue, String), Diagnostic> {
    let title = &style.charts.title;
    let sizes = heading_ramp(style, "heading")?;
    let tier = WidthTier::of(width);
    let offset = match tier {
        WidthTier::Tiny => title.base.width_offsets.tiny,
        WidthTier::Narrow => title.base.width_offsets.narrow,
        WidthTier::Medium => title.base.width_offsets.medium,
        WidthTier::Wide => title.base.width_offsets.wide,
    };
    let level = (OBJECT_TITLE_ANCHOR_LEVEL + i64::from(offset)).clamp(1, sizes.len() as i64);
    let size = sizes[(level - 1) as usize].trunc();
    // An `auto` weight or family is the board title's (`style.title.font`, which the chart slot inherits).
    let weight = match (tier, &title.font.weight) {
        (WidthTier::Tiny, _) => title.base.compact_weight.clone(),
        (_, Engine::Value(weight)) => weight.clone(),
        (_, Engine::Rule(_)) => style.title.font.weight.clone(),
    };
    let family = match (tier, &title.font.family) {
        (WidthTier::Medium | WidthTier::Wide, Engine::Value(family)) if !family.as_str().is_empty() => family.as_str().to_string(),
        (WidthTier::Medium | WidthTier::Wide, Engine::Rule(_)) => style.title.font.family.as_str().to_string(),
        _ => style.charts.font.family.as_str().to_string(),
    };
    Ok((size, weight, family))
}

/// The gap between an object title's block and its body when `title.position.offset` is unset
/// (common.rs:`wrap_title_block`'s `unwrap_or(10.0)`).
const DEFAULT_TITLE_OFFSET: f64 = 10.0;

/// `title_ascent`'s ratio of a title's size to its first baseline (sizing.rs:`title_ascent`).
const TITLE_ASCENT_RATIO: f64 = 0.8;

/// A chart's one-line title as it draws: its text, its band and the face it paints in.
#[derive(Debug, Clone, PartialEq)]
pub struct ChartTitle {
    pub text: String,
    /// The block's top to the first baseline, and that baseline to the body (`vega_title_block`).
    pub metrics: TitleMetrics,
    pub size: f64,
    pub weight: FontWeightValue,
    /// The family stack as the style states it, before the board's emoji mode.
    pub family: String,
}

/// A chart's title at its card's outer `width`, in the `avail_w` the card's padding leaves: the one
/// place a family gets a title. The face is `chart_title_spec`'s, the case the chart title slot's,
/// the overflow `charts.title.overflow`, and the gap under the text `title.position.offset`. The
/// band is production's Vega-Lite title (title_shift.rs:`vega_title_block`): the first baseline at
/// `round(0.8 * size)`, the body `size + offset` under the top; the font's own ascent and descent
/// (`title_block`) only decide whether the text fits one line. A title that wraps or is cut is
/// refused by name, since the engine carries no warning out of a painter.
/// `None` when the text is blank.
pub fn chart_title(chart_id: &str, text: &str, style: &Style, width: f64, avail_w: f64, book: &FontBook) -> Result<Option<ChartTitle>, Diagnostic> {
    let slot = &style.charts.title;
    if matches!(slot.font.size, Engine::Value(_)) {
        return Err(Diagnostic::refused(refuse!("a chart title with an authored font size is not supported")));
    }
    let (size, weight, family) = chart_title_spec(style, width)?;
    let cased = display_text::case::apply_case(text, slot.font.case.as_str()).map_err(diag::err_internal)?;
    let axis = match &weight {
        FontWeightValue::Number(n) => *n as f32,
        FontWeightValue::Name(name) => measure::fonts::css_weight_axis(name).map_err(diag::err_internal)?.ok_or_else(|| Diagnostic::refused(refuse!("a chart title whose font weight names no axis value is not supported")))?,
    };
    let font = Font { family: measure::stand_in_family(&family), size, weight: axis, italic: slot.font.style.as_str() == "italic" };
    let overflow = match slot.base.overflow {
        OverflowMode::Clip => Overflow::Clip,
        OverflowMode::Truncate => Overflow::Truncate,
        OverflowMode::WrapTwo => Overflow::WrapTwo,
        OverflowMode::Wrap => Overflow::Wrap,
    };
    let offset = slot.base.position.offset.unwrap_or(DEFAULT_TITLE_OFFSET);
    let fitted = TitleText { text: &cased, font, overflow };
    let Some(block) = title_block(chart_id, Some(&fitted), None, offset, avail_w, book).map_err(diag::err_internal)? else {
        return Ok(None);
    };
    let [line] = block.title.as_slice() else {
        return Err(Diagnostic::refused(refuse!("a chart title that needs more than one line is not supported")));
    };
    if !block.diagnostics.is_empty() {
        return Err(Diagnostic::refused(refuse!("a chart title cut to its width is not supported")));
    }
    let ascent = (size * TITLE_ASCENT_RATIO).round_ties_even();
    let metrics = TitleMetrics { ascent, tail: size + offset - ascent };
    Ok(Some(ChartTitle { text: line.text.clone(), metrics, size, weight, family }))
}

/// How a title's text fits its limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Overflow {
    /// One line, cut at the last character that fits, with no ellipsis.
    Clip,
    /// One line, ending in an ellipsis when cut.
    Truncate,
    /// Two lines, the second ending in an ellipsis when cut.
    WrapTwo,
    /// Every wrapped line.
    Wrap,
}

/// A title or subtitle as resolved: its text, the face it paints in, its overflow.
#[derive(Debug, Clone, Copy)]
pub struct TitleText<'a> {
    pub text: &'a str,
    pub font: Font<'a>,
    pub overflow: Overflow,
}

/// The gap between a title and a subtitle under it.
const SUBTITLE_PADDING: f64 = 3.0;

/// One drawn line and its baseline, from the block's top.
#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub text: String,
    pub baseline: f64,
}

/// What a `cols` row aligns titles by: the block's top to the first baseline,
/// and the first baseline to the body's top.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TitleMetrics {
    pub ascent: f64,
    pub tail: f64,
}

impl TitleMetrics {
    /// The band the title takes above the body.
    pub fn height(&self) -> f64 {
        self.ascent + self.tail
    }
}

/// A title and its subtitle as they draw.
#[derive(Debug, Clone, PartialEq)]
pub struct TitleBlock {
    pub title: Vec<Line>,
    pub subtitle: Vec<Line>,
    /// The widest line, never more than the limit.
    pub width: f64,
    pub metrics: TitleMetrics,
    /// `WARN-CHART-TITLE-TRUNCATED` for the title and the subtitle a line cap cut.
    pub diagnostics: Vec<Diagnostic>,
}

/// The title block in `avail_w` (the card less its padding). Title and subtitle
/// each wrap once, in the face they paint in, to the whole-pixel limit, by their
/// own overflow (quirk class Q2e: no measure in a stand-in face, no second clip).
/// No limit left draws the text unbroken. Each line pitches by its face's ascent
/// plus descent, the first baseline at the title's ascent (quirk class Q2d: no
/// Vega text metrics); the subtitle sits `SUBTITLE_PADDING` under the title and
/// `offset` separates the block from the body.
///
/// `None` when nothing draws: no title (a subtitle needs one), or a blank title in
/// a wrapping mode with a limit. A blank title in `Clip` or `Truncate`, or with no
/// limit, keeps its band.
pub fn title_block(
    chart_id: &str,
    title: Option<&TitleText>,
    subtitle: Option<&TitleText>,
    offset: f64,
    avail_w: f64,
    book: &FontBook,
) -> Result<Option<TitleBlock>, String> {
    let Some(title) = title else {
        return Ok(None);
    };
    let limit = avail_w.trunc();
    let (title_lines, title_cut) = fit(title, limit, book)?;
    if title_lines.is_empty() {
        return Ok(None);
    }
    let (sub_lines, sub_cut) = match subtitle {
        Some(sub) => fit(sub, limit, book)?,
        None => (vec![], false),
    };

    let mut pen = Pen {
        top: 0.0,
        width: 0.0,
    };
    let drawn = pen.place(title_lines, &title.font, book)?;
    let sub_drawn = match subtitle {
        Some(sub) if !sub_lines.is_empty() => {
            pen.top += SUBTITLE_PADDING;
            pen.place(sub_lines, &sub.font, book)?
        }
        _ => vec![],
    };
    let (top, width) = (pen.top, pen.width);
    let height = top + offset;
    let ascent = drawn[0].baseline;

    let mut diagnostics = Vec::new();
    for (cut, field, text) in [
        (title_cut, "title", Some(title)),
        (sub_cut, "subtitle", subtitle),
    ] {
        if let (true, Some(text)) = (cut, text) {
            diagnostics.push(diag::warn_chart_title_truncated(chart_id, field, text.text));
        }
    }
    Ok(Some(TitleBlock {
        title: drawn,
        subtitle: sub_drawn,
        width: if limit > 0.0 { width.min(limit) } else { width },
        metrics: TitleMetrics {
            ascent,
            tail: height - ascent,
        },
        diagnostics,
    }))
}

/// Stacks lines down from the block's top, tracking the widest.
struct Pen {
    top: f64,
    width: f64,
}

impl Pen {
    fn place(
        &mut self,
        lines: Vec<String>,
        font: &Font,
        book: &FontBook,
    ) -> Result<Vec<Line>, String> {
        lines
            .into_iter()
            .map(|text| {
                let run = book.run(&text, font)?;
                self.width = self.width.max(run.advance);
                let line = Line {
                    text,
                    baseline: self.top + run.ascent,
                };
                self.top += run.ascent + run.descent;
                Ok(line)
            })
            .collect()
    }
}

/// A text's lines in `limit`, and whether a line cap cut it.
fn fit(t: &TitleText, limit: f64, book: &FontBook) -> Result<(Vec<String>, bool), String> {
    if limit <= 0.0 {
        return Ok((vec![t.text.to_string()], false));
    }
    if t.text.trim().is_empty() {
        let single_line = matches!(t.overflow, Overflow::Clip | Overflow::Truncate);
        return Ok((
            if single_line {
                vec![t.text.to_string()]
            } else {
                vec![]
            },
            false,
        ));
    }
    if t.overflow == Overflow::Clip {
        return clip(t, limit, book);
    }
    let max_lines = match t.overflow {
        Overflow::Truncate => Some(1),
        Overflow::WrapTwo => Some(2),
        Overflow::Clip | Overflow::Wrap => None,
    };
    let wrapped = book.wrap(t.text, &t.font, limit, max_lines)?;
    Ok((wrapped.lines, wrapped.truncated))
}

/// The longest prefix of the whitespace-collapsed text that fits `limit`, its
/// trailing spaces dropped, with no ellipsis; the first character when none fits.
fn clip(t: &TitleText, limit: f64, book: &FontBook) -> Result<(Vec<String>, bool), String> {
    let text = t.text.split_whitespace().collect::<Vec<_>>().join(" ");
    if book.run(&text, &t.font)?.advance <= limit {
        return Ok((vec![text], false));
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi) = (1, chars.len() - 1);
    let mut best = chars[0].to_string();
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let candidate = chars[..mid]
            .iter()
            .collect::<String>()
            .trim_end()
            .to_string();
        if book.run(&candidate, &t.font)?.advance <= limit {
            best = candidate;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    Ok((vec![best], true))
}
