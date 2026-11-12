//! The board's own bands, measured and drawn from the total [`Style`]: what a body adds to its
//! intrinsic width and its band height ([`own`]), and the root's footer and timestamp row
//! ([`footer_height`], [`draw`]).
//!
//! The footer's laws are production's, cited per item: `compile/sizing.py` (`board_own_width`,
//! `footer_text_width`, `_footer_row_width`) for the width a board asks for, and `chart-renderer/src/board.rs`
//! (`footer_attribution_svg`, `render_board_in_scope`) for the row it draws.
//!
//! A timestamp directive d3 and Python do not print alike (`%z`, `%T`, `%F` ...) is refused by name.

use bind::font_stack::with_emoji;
use board_model::style::page::{TimestampAlign, TimestampPosition};
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use fancy_regex::Regex;
use measure::shaped::{Font, FontBook};
use scene::{Attrs, Chars, Child, Element, Escape, Node};
use std::sync::LazyLock;

use crate::board::assemble::Own;
use crate::board::tree::Title;
use crate::board::width::IntrinsicWidth;

/// `FOOTER_BRAND_WORD_RE` (`compile/sizing.py`): ASCII neighbors only, since a translated footer may
/// run the brand into non-Latin text.
static BRAND: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?<![A-Za-z0-9_])dbt charts(?![A-Za-z0-9_])").expect("the brand pattern compiles")
});

/// `_SAMPLE_TIMESTAMP` (`compile/sizing.py`), in milliseconds since the epoch: 2026-12-28 22:22 UTC. A
/// fixed instant, so the floor never depends on when the board renders; late December is among the
/// widest in month names.
const SAMPLE_TIMESTAMP_MS: f64 = 1_798_496_520_000.0;

/// The brand `_bake_theme_words` (`resolve/style/board.py`) fills the `auto` words with.
const BRAND_NAME: &str = "dbt Charts";

/// `body`'s own footer: at the root the footer row's minimum width. A title and text band is
/// `prose.rs`'s, and a variables band is not modeled.
/// A footer or timestamp left on `auto` takes the words of the active board language.
pub fn own(root: bool, style: &Style, book: &FontBook) -> Result<Own, Diagnostic> {
    let width = if root {
        IntrinsicWidth { min: Some(row_width(style, book)?), preferred: None }
    } else {
        IntrinsicWidth::NONE
    };
    Ok(Own { width, band: 0.0, title: Title::None })
}

/// `_footer_row_width`: the attribution, plus the timestamp and its gap when it shares the row. The
/// width is measured in the board's font, as production does, not the footer's own.
fn row_width(style: &Style, book: &FontBook) -> Result<f64, Diagnostic> {
    let footer = &style.footer;
    if !footer.visible {
        return Ok(0.0);
    }
    let family = style.font.family.as_str();
    let mut width = Attribution::measure(&footer_text(style)?, footer.brand_weight, footer.font.size, family, book)?.width();
    let stamp = &style.timestamp;
    if stamp.visible && stamp.position == TimestampPosition::Footer {
        width += stamp.gap.get();
        width += advance(book, &sample_stamp(style)?, family, stamp.font.size, 400.0)?;
    }
    Ok(width)
}

fn advance(book: &FontBook, text: &str, family: &str, size: f64, weight: f64) -> Result<f64, Diagnostic> {
    let font = Font { family, size, weight: weight as f32, italic: false };
    let measured = if weight == 400.0 { book.raw_advance(text, &font) } else { book.weighted_advance(text, &font) };
    measured.map_err(diag::err_internal)
}

/// The footer's words, the active board language's when left on `auto`.
fn footer_text(style: &Style) -> Result<String, Diagnostic> {
    match style.footer.text.as_str() {
        "auto" => display_text::language::tr("footer.made_with", None, &[("brand", BRAND_NAME)]).map_err(diag::err_internal),
        text => Ok(text.to_string()),
    }
}

/// The timestamp's strftime format, the board language's words around the time and date when left on `auto`.
fn stamp_format(style: &Style) -> Result<String, Diagnostic> {
    match style.timestamp.format.as_str() {
        "auto" => display_text::language::tr("footer.data_as_of", None, &[("time", "%H:%M %Z"), ("date", "%-d %b %Y")])
            .map_err(diag::err_internal),
        format => Ok(format.to_string()),
    }
}

/// Production's `format_d3.py::VEGA_SAFE_TIME_DIRECTIVES`, the directives d3's formatter and
/// `portable_strftime` print alike, plus a literal `%`. `%s` reads the host's zone and `%f`/`%g`
/// print their modifier forms differently, so they are not in it.
const SHARED_DIRECTIVES: &str = "aAbBcdeGHIjLmMpqQSuUVwWxXyY%";

/// `portable_strftime(_SAMPLE_TIMESTAMP, format)`.
fn sample_stamp(style: &Style) -> Result<String, Diagnostic> {
    strftime(style, SAMPLE_TIMESTAMP_MS, "UTC")
}

/// The timestamp row's text for the render at `rendered_at`, printed in the active board zone
/// (`portable_strftime(data_as_of.astimezone(zone), format)`).
pub fn stamp_text(style: &Style, rendered_at: jiff::Timestamp) -> Result<String, Diagnostic> {
    let utc_ms = rendered_at.as_millisecond();
    let wall = bind::time_zone::wall_ms(utc_ms).map_err(diag::err_internal)?;
    let (_, abbreviation) = bind::time_zone::zone_names(utc_ms).map_err(diag::err_internal)?;
    strftime(style, wall as f64, &abbreviation)
}

/// The timestamp format printed at the wall clock `wall_ms` (a zone's clock read as UTC milliseconds) in the
/// active time locale: `%Z` is the zone's abbreviation (d3's `%Z` is the offset). A directive the two
/// formatters do not share is refused by name.
fn strftime(style: &Style, wall_ms: f64, zone_abbreviation: &str) -> Result<String, Diagnostic> {
    let format = stamp_format(style)?;
    let mut spec = String::new();
    let mut chars = format.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '%' {
            spec.push(c);
            continue;
        }
        // `-`, `_` and `0` are padding modifiers of the directive after them.
        let modifier = chars.next_if(|m| "-_0".contains(*m));
        let Some(directive) = chars.next() else {
            spec.push('%');
            continue;
        };
        if directive == 'Z' && modifier.is_none() {
            spec.push_str(zone_abbreviation);
        } else if SHARED_DIRECTIVES.contains(directive) {
            spec.push('%');
            spec.extend(modifier);
            spec.push(directive);
        } else {
            return Err(Diagnostic::refused(refuse!("a timestamp format with the directive %{directive} is not supported")));
        }
    }
    let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
    d3rs::time_format::format(&spec, wall_ms, 0.0, &locale).map_err(diag::err_internal)
}

/// The footer's text split around the brand phrase, each run measured (`footer_text_width`,
/// `footer_attribution_svg`). A space separates the brand from a neighbor only where the text had one
/// (CJK runs the brand straight into its words).
struct Attribution {
    prefix: Run,
    /// `None` when the text has no brand phrase; the whole text is then `prefix`.
    brand: Option<Run>,
    suffix: Run,
    prefix_gap: f64,
    suffix_gap: f64,
}

struct Run {
    text: String,
    width: f64,
}

impl Attribution {
    fn measure(text: &str, brand_weight: u32, size: f64, family: &str, book: &FontBook) -> Result<Self, Diagnostic> {
        let run = |text: &str, weight: f64| Ok::<_, Diagnostic>(Run { text: text.to_string(), width: advance(book, text, family, size, weight)? });
        let Some(found) = BRAND.find(text).map_err(|e| diag::err_internal(e.to_string()))? else {
            return Ok(Attribution { prefix: run(text, 400.0)?, brand: None, suffix: run("", 400.0)?, prefix_gap: 0.0, suffix_gap: 0.0 });
        };
        let (before, after) = (&text[..found.start()], &text[found.end()..]);
        let (prefix, suffix) = (before.trim_end(), after.trim_start());
        let space = advance(book, " ", family, size, 400.0)?;
        Ok(Attribution {
            prefix: run(prefix, 400.0)?,
            // Measured at the weight it is painted at: the regular instance is narrower.
            brand: Some(run(found.as_str(), f64::from(brand_weight))?),
            suffix: run(suffix, 400.0)?,
            prefix_gap: if prefix.len() != before.len() { space } else { 0.0 },
            suffix_gap: if suffix.len() != after.len() { space } else { 0.0 },
        })
    }

    /// `footer_text_width`: the sizing pass's sum.
    fn width(&self) -> f64 {
        let Some(brand) = &self.brand else { return self.prefix.width };
        let before = if self.prefix.text.is_empty() { 0.0 } else { self.prefix_gap + self.prefix.width };
        let after = if self.suffix.text.is_empty() { 0.0 } else { self.suffix.width + self.suffix_gap };
        brand.width + before + after
    }

    /// The runs set right-aligned at `x`, each with the x its right edge lands on, and the width they
    /// take, the way the painter steps leftward from `x` (its sums differ from [`Self::width`]'s in the
    /// last bits, which the timestamp's x keeps).
    fn place(&self, x: f64) -> (Vec<(&Run, bool, f64)>, f64) {
        let Some(brand) = &self.brand else { return (vec![(&self.prefix, false, x)], self.prefix.width) };
        let mut runs = Vec::new();
        let mut right = x;
        if !self.suffix.text.is_empty() {
            runs.push((&self.suffix, false, x));
            right -= self.suffix.width + self.suffix_gap;
        }
        runs.push((brand, true, right));
        right -= brand.width;
        if !self.prefix.text.is_empty() {
            runs.push((&self.prefix, false, right - self.prefix_gap));
            right -= self.prefix_gap + self.prefix.width;
        }
        runs.reverse();
        (runs, x - right)
    }
}

/// The page the row sits on: the root `<svg>`'s width and height, the footer's height included.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Page {
    pub width: f64,
    pub height: f64,
}

/// The root's timestamp and footer as drawn, in the order the root `<svg>` holds them.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub timestamp: Option<Node>,
    /// The divider and the attribution (the brand's link around its run), `\n` between the two.
    pub footer: Vec<Node>,
}

/// What the footer adds to the root's height: its offset from the bottom edge, its text and the gap
/// its divider reserves (`render_board_in_scope`); nothing when it is not visible.
pub fn footer_height(style: &Style) -> f64 {
    let footer = &style.footer;
    if !footer.visible {
        return 0.0;
    }
    let rule_gap = if footer.divider.divider.visible { footer.divider.gap.get() } else { 0.0 };
    footer.y_offset + footer.font.size + rule_gap
}

fn element(tag: &str, attrs: Attrs, children: Vec<Child>) -> Element {
    Element { tag: tag.to_string(), attrs, children }
}

fn text(content: &str) -> Vec<Child> {
    vec![Child::Text(Chars::new(content, Escape::Attr))]
}

/// `svg_utils.px`: a coordinate snapped to the nearest whole pixel, ties to even (Python's `round`).
fn snap(v: f64) -> f64 {
    v.round_ties_even()
}

/// The timestamp and footer a root of `page` draws; `stamp` is the timestamp's text, which the caller
/// formats from the render's clock (`render_timestamp_text`).
pub fn draw(style: &Style, book: &FontBook, page: Page, stamp: &str) -> Result<Row, Diagnostic> {
    let footer = &style.footer;
    let padding = style.frame.margin;
    let footer_x = page.width - padding;
    let footer_y = page.height - footer.y_offset;
    let emoji = &style.font.emoji;
    let family = &with_emoji(footer.font.family.as_str(), emoji);
    let mut nodes = Vec::new();
    let mut footer_width = 0.0;
    if footer.visible {
        let size = footer.font.size;
        if footer.divider.divider.visible {
            let rule = &footer.divider.divider;
            let rule_y = footer_y - size - footer.divider.gap.get();
            let line = Attrs::new()
                .num("x1", padding)
                .num("y1", rule_y)
                .num("x2", footer_x)
                .num("y2", rule_y)
                .set("stroke", rule.color.as_str())
                .num("stroke-width", rule.width)
                .set_if(rule.dasharray.is_some(), "stroke-dasharray", rule.dasharray.as_ref().map_or("", |d| d.as_str()));
            nodes.push(Node::element(element("line", line, vec![])));
            nodes.push(Node::chars(Chars::new("\n", Escape::Attr)));
        }
        let attribution = Attribution::measure(&footer_text(style)?, footer.brand_weight, size, family, book)?;
        let (runs, width) = attribution.place(footer_x);
        footer_width = width;
        for (run, brand, end) in runs {
            let attrs = Attrs::new()
                .num("x", snap(end))
                .num("y", footer_y)
                .set("text-anchor", "end")
                .num("font-size", size)
                .set("fill", footer.font.color.as_str())
                .set("font-family", family)
                .set("data-dct-role", "footer-text")
                .set_if(brand, "font-weight", footer.brand_weight.to_string());
            let run_node = element("text", attrs, text(&run.text));
            nodes.push(Node::element(match (&footer.link, brand) {
                (Some(link), true) => element(
                    "a",
                    Attrs::new().set("class", "dbt-footer-link").set("href", link.as_str()).set("target", "_blank").set("rel", "noopener noreferrer"),
                    vec![Child::Element(run_node)],
                ),
                _ => run_node,
            }));
        }
    }
    let stamp_style = &style.timestamp;
    let timestamp = stamp_style.visible.then(|| {
        let y = if stamp_style.position == TimestampPosition::Top { stamp_style.y } else { footer_y };
        let (x, anchor) = if stamp_style.align == TimestampAlign::Left {
            (padding, "start")
        } else if stamp_style.position == TimestampPosition::Footer && footer.visible {
            (footer_x - footer_width - stamp_style.gap.get(), "end")
        } else {
            (page.width - padding, "end")
        };
        let attrs = Attrs::new()
            .set("data-role", "render-timestamp")
            .num("x", x)
            .num("y", y)
            .set("text-anchor", anchor)
            .num("font-size", stamp_style.font.size)
            .set("fill", stamp_style.font.color.as_str())
            .set("font-family", with_emoji(stamp_style.font.family.as_str(), emoji))
            .set("style", "font-variant-numeric: tabular-nums lining-nums;");
        Node::element(element("text", attrs, text(stamp)))
    });
    Ok(Row { timestamp, footer: nodes })
}
