//! A chart's title block: its title and subtitle as they draw in the width the
//! card gives.

use board_model::facet::FontWeightValue;
use board_model::style::board::{ChartTitleStyle, EmojiMode, OverflowMode};
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use model_core::Engine;
use scene::{Attrs, Node, Role};

use crate::families::failed::Weight;
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

/// A chart's title as it draws: its lines, its subtitle's, the bands they take and the face they
/// paint in.
#[derive(Debug, Clone, PartialEq)]
pub struct ChartTitle {
    pub lines: Vec<String>,
    pub subtitle: Option<ChartSubtitle>,
    /// The block's top to the first baseline, and that baseline to the body, as production's sizing
    /// has it (`vega_title_block`): one title line, one subtitle line. A `cols` row aligns by this.
    pub metrics: TitleMetrics,
    /// What the drawn block takes above the body: `metrics` and a line pitch for each line past the
    /// first of the title and of the subtitle.
    pub band: TitleMetrics,
    pub size: f64,
    pub weight: FontWeightValue,
    /// The family stack as the style states it, before the board's emoji mode.
    pub family: String,
    /// `WARN-CHART-TITLE-TRUNCATED` for the title and the subtitle a width cut.
    pub diagnostics: Vec<Diagnostic>,
}

/// A chart's subtitle lines and the face they paint in.
#[derive(Debug, Clone, PartialEq)]
pub struct ChartSubtitle {
    pub lines: Vec<String>,
    pub size: f64,
    pub weight: FontWeightValue,
    pub family: String,
}

/// A Vega text mark's line height past its font size (`vega_fit::TitleInk::LINE_ADVANCE_EXTRA`).
const LINE_PITCH_EXTRA: f64 = 2.0;

impl ChartTitle {
    /// Each title line with its baseline from the block's top.
    pub fn title_lines(&self) -> impl Iterator<Item = (&str, f64)> {
        let pitch = self.size + LINE_PITCH_EXTRA;
        self.lines.iter().enumerate().map(move |(i, line)| (line.as_str(), self.metrics.ascent + i as f64 * pitch))
    }

    /// Each subtitle line with its baseline from the block's top: the subtitle's first row starts
    /// `size + SUBTITLE_PADDING` under the last title row's top.
    pub fn subtitle_lines(&self) -> impl Iterator<Item = (&str, f64)> {
        let title_pitch = self.size + LINE_PITCH_EXTRA;
        let last_row = (self.lines.len() - 1) as f64 * title_pitch;
        self.subtitle.iter().flat_map(move |sub| {
            let first = last_row + self.size + SUBTITLE_PADDING + measure::baseline_shift("top", sub.size);
            sub.lines.iter().enumerate().map(move |(i, line)| (line.as_str(), first + i as f64 * (sub.size + LINE_PITCH_EXTRA)))
        })
    }
}

impl ChartTitle {
    /// The block as text nodes from its top left `(x, top)`: the title's lines and the subtitle's, each
    /// face's ink and slant from the chart title slot `style`. The first line of each carries its
    /// `data-authored-kind`, as production stamps the one `role-title-text` group.
    pub fn nodes(&self, (x, top): (f64, f64), style: &ChartTitleStyle, emoji: &EmojiMode) -> Vec<Node> {
        let face = |family: &str, size: f64, weight: &FontWeightValue, slant: &str, color: &str| {
            Attrs::new()
                .vega("font-family", bind::font_stack::with_emoji(family, emoji).as_str())
                .num("font-size", size)
                .set("font-style", if slant == "italic" { "italic" } else { "normal" })
                .set("font-weight", Weight::of(weight).attr())
                .set("fill", color)
        };
        let title = face(&self.family, self.size, &self.weight, style.font.style.as_str(), style.font.color.as_str());
        let lines = self.title_lines().enumerate().map(|(i, (text, baseline))| {
            let attrs = if i == 0 { title.clone().set("data-authored-kind", "title") } else { title.clone() };
            Node::text(Role::Title, (x, top + baseline), attrs, text)
        });
        let sub = self.subtitle.as_ref().map(|sub| {
            let font = &style.base.subtitle.font;
            let attrs = face(&sub.family, sub.size, &sub.weight, font.style.as_str(), font.color.as_str());
            self.subtitle_lines().enumerate().map(move |(i, (text, baseline))| {
                let attrs = if i == 0 { attrs.clone().set("data-authored-kind", "subtitle") } else { attrs.clone() };
                Node::text(Role::Title, (x, top + baseline), attrs, text)
            }).collect::<Vec<_>>()
        });
        lines.chain(sub.into_iter().flatten()).collect()
    }
}

/// A chart's title at its card's outer `width`, in the `avail_w` the card's padding leaves: the one
/// place a family gets a title. The face is `chart_title_spec`'s, the case the chart title slot's,
/// the overflow `charts.title.overflow`, and the gap under the text `title.position.offset`; the
/// subtitle (when non-empty, as production tests it) paints in `charts.title.subtitle`'s face and
/// overflow. The sizing block is production's Vega-Lite title (title_shift.rs:`vega_title_block`):
/// the first baseline at `round(0.8 * size)`, the body `size` plus the subtitle's size and gap plus
/// `offset` under the top; each wrapped line past the first moves the body down by its size and 2 px
/// (`vega_fit::TitleInk::with_subtitle_lines`). The font's own ascent and descent (`title_block`)
/// only decide how the text wraps or is cut, which each `diagnostics` entry reports.
///
/// The wrap and the cut are one measure, in the face each line paints in at its painted size,
/// kerned: a line prints as many characters as fit its width and is never cut early (`clip` keeps the
/// longest prefix that fits, `truncate` the longest that fits with its ellipsis).
/// A size where Vega's `round(0.79 * size)` text baseline and `round(0.8 * size)` ascent part draws
/// a wrapped or subtitled block on a baseline nothing here is held to, and is refused.
/// `None` when the text is empty or blank in a wrapping mode.
pub fn chart_title(chart_id: &str, text: &str, subtitle: Option<&str>, style: &Style, width: f64, avail_w: f64, book: &FontBook) -> Result<Option<ChartTitle>, Diagnostic> {
    if text.is_empty() {
        return Ok(None);
    }
    let slot = &style.charts.title;
    if matches!(slot.font.size, Engine::Value(_)) {
        return Err(Diagnostic::refused(refuse!("a chart title with an authored font size is not supported")));
    }
    let (size, weight, family) = chart_title_spec(style, width)?;
    let cased = display_text::case::apply_case(text, slot.font.case.as_str()).map_err(diag::err_internal)?;
    let title_font = painted_font(&family, size, &weight, slot.font.style.as_str() == "italic")?;
    let offset = slot.base.position.offset.unwrap_or(DEFAULT_TITLE_OFFSET);
    let fitted = TitleText { text: &cased, font: title_font, overflow: overflow_of(slot.base.overflow) };
    let sub_face = &slot.base.subtitle;
    let sub_family = sub_face.font.family.as_str();
    let sub_italic = sub_face.font.style.as_str() == "italic";
    let sub_fitted = subtitle
        .filter(|text| !text.is_empty())
        .map(|text| Ok::<_, Diagnostic>(TitleText { text, font: painted_font(sub_family, sub_face.font.size, &sub_face.font.weight, sub_italic)?, overflow: overflow_of(sub_face.overflow) }))
        .transpose()?;
    let Some(block) = title_block(chart_id, Some(&fitted), sub_fitted.as_ref(), offset, avail_w, book).map_err(diag::err_internal)? else {
        return Ok(None);
    };
    let (lines, sub_lines) = (block.title.len(), block.subtitle.len());
    let rounds_apart = |size: f64| measure::baseline_shift("top", size) != (size * TITLE_ASCENT_RATIO).round();
    if (lines > 1 || sub_fitted.is_some()) && (rounds_apart(size) || sub_fitted.is_some() && rounds_apart(sub_face.font.size)) {
        return Err(Diagnostic::refused(refuse!("a wrapped or subtitled chart title at a size whose Vega baseline rounds off its ascent is not supported")));
    }
    let ascent = (size * TITLE_ASCENT_RATIO).round_ties_even();
    let sub_block = sub_fitted.as_ref().map_or(0.0, |_| sub_face.font.size + SUBTITLE_PADDING);
    let metrics = TitleMetrics { ascent, tail: size + sub_block + offset - ascent };
    let extra = (lines - 1) as f64 * (size + LINE_PITCH_EXTRA) + sub_lines.saturating_sub(1) as f64 * (sub_face.font.size + LINE_PITCH_EXTRA);
    let subtitle = sub_fitted.as_ref().map(|_| ChartSubtitle {
        lines: block.subtitle.iter().map(|line| line.text.clone()).collect(),
        size: sub_face.font.size,
        weight: sub_face.font.weight.clone(),
        family: sub_family.to_string(),
    });
    Ok(Some(ChartTitle {
        lines: block.title.iter().map(|line| line.text.clone()).collect(),
        subtitle,
        metrics,
        band: TitleMetrics { ascent, tail: metrics.tail + extra },
        size,
        weight,
        family,
        diagnostics: block.diagnostics,
    }))
}

fn weight_axis(weight: &FontWeightValue) -> Result<f32, Diagnostic> {
    match weight {
        FontWeightValue::Number(n) => Ok(*n as f32),
        FontWeightValue::Name(name) => measure::fonts::css_weight_axis(name).map_err(diag::err_internal)?.ok_or_else(|| Diagnostic::refused(refuse!("a chart title whose font weight names no axis value is not supported"))),
    }
}

/// The face a title or subtitle paints in, as the one measure its wrap and its cut read.
fn painted_font<'a>(family: &'a str, size: f64, weight: &FontWeightValue, italic: bool) -> Result<Font<'a>, Diagnostic> {
    Ok(Font { family: measure::stand_in_family(family), size, weight: weight_axis(weight)?, italic })
}

fn overflow_of(mode: OverflowMode) -> Overflow {
    match mode {
        OverflowMode::Clip => Overflow::Clip,
        OverflowMode::Truncate => Overflow::Truncate,
        OverflowMode::WrapTwo => Overflow::WrapTwo,
        OverflowMode::Wrap => Overflow::Wrap,
    }
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
