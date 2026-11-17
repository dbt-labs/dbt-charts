//! A chart bound over no rows ([`bound::Cartesian::NoData`]): its marks background, its title band
//! when it has a title (laid out as a plane's is), and production's "No results" card
//! (`chart-renderer/src/families/no_results.rs`, spec Q10): the headline centered in the plot box, which
//! is the cell inside the card and chart padding and under the title's band, and under it the filters
//! that reach the chart. There are no scales, so there are no axes, grids or marks to lay out.

use bind::bound::{self, Family};
use board_model::style::font::FontStyle;
use board_model::style::board::EmojiMode;
use diagnostic::{diag, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::{baseline_shift, stand_in_family};
use scene::{Attrs, Extent, Node, Role, Shape};

use crate::geom::{Insets, Rect, Size};
use crate::plot::cartesian::{heading_block, marks_background, Heading, Ink};
use crate::plot::legend::ink;
use crate::plot::negotiate::body as plot_box;

/// The gap between the headline and the filters line under it (`no_results::LINE_GAP`).
const LINE_GAP: f64 = 6.0;

/// The "No results" card's text centered in `area` (`no_results::render`): the headline in `text_face`
/// (`data-authored-kind="no_results"`), and under it, when the chart has a filters line, that line in
/// its own face (`no_results_filters`), cut to `area`'s width.
pub fn body(text_face: &FontStyle, filters: Option<(&FontStyle, &str)>, area: Rect, book: &FontBook, emoji: &EmojiMode) -> Result<Vec<Node>, Diagnostic> {
    let center = (area.x + area.width / 2.0, area.y + area.height / 2.0);
    let headline = ink(text_face, emoji)?;
    let text = display_text::language::tr("chart.no_results", None, &[])?;
    let line = |ink: &Ink, at: (f64, f64), text: &str, kind: &str| Node::text(Role::Label, (at.0, at.1 + baseline_shift("middle", ink.size)), ink.attrs(Some("middle")).set("data-authored-kind", kind), text);
    let mut nodes = vec![line(&headline, center, text.as_str(), "no_results")];
    if let Some((face, filters)) = filters {
        let ink = ink(face, emoji)?;
        let measured = Font { family: stand_in_family(face.family.as_ref()), size: face.size, weight: 400.0, italic: false };
        let fitted = truncate_label(filters, area.width, |text| book.raw_advance(text, &measured).map_err(diag::err_internal))?;
        let at = (center.0, center.1 + headline.size / 2.0 + LINE_GAP + ink.size / 2.0);
        nodes.push(line(&ink, at, &fitted, "no_results_filters"));
    }
    Ok(nodes)
}

/// The chart's root `<svg>` for a cell of `cell`, inside the board's `card_padding`: the marks
/// background, the title band when the chart has a title and [`body`] in the plot box under it.
/// `heading` is the chart's title resolved, present exactly when the chart has one. A subtitle is
/// the caller's to refuse: this draws none.
pub fn draw(chart: &bound::Chart, heading: Option<&Heading>, cell: Size, book: &FontBook, emoji: &EmojiMode, card_padding: f64) -> Result<(Vec<Node>, Vec<Diagnostic>), Diagnostic> {
    let (Ok(Family::Cartesian(bound::Cartesian::NoData { empty, headline })), Some(frame)) = (&chart.family, &chart.frame) else {
        return Err(diag::err_internal("an empty plot reached a chart that is not bound over no rows with a frame"));
    };
    let padding = Insets {
        top: frame.padding.top + card_padding,
        right: frame.padding.right + card_padding,
        bottom: frame.padding.bottom + card_padding,
        left: frame.padding.left + card_padding,
    };
    let title = heading_block(chart, heading, cell.width, (padding.left, padding.top), padding.right, book, emoji)?;
    let area = plot_box(Rect { x: 0.0, y: 0.0, width: cell.width, height: cell.height }, padding, title.as_ref().map(|(title, _)| &title.metrics), Insets::ZERO);
    let filters = empty.filters.as_deref().map(|line| (&frame.title.base.subtitle.font, line));
    let text = body(headline, filters, area, book, emoji)?;
    let mut children = vec![Node::rect(Role::Chrome, (0.0, 0.0, cell.width, cell.height), Attrs::new().set("fill", marks_background(frame)?))];
    if let Some((_, nodes)) = &title {
        children.extend(nodes.iter().cloned());
    }
    children.extend(text);
    let extent = Extent { width: cell.width, height: cell.height };
    let diagnostics = title.map_or_else(Vec::new, |(title, _)| title.diagnostics);
    Ok((vec![Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent, children })], diagnostics))
}

/// `text` whole when it measures within `limit`, else its longest prefix, trailing spaces dropped,
/// that fits with an ellipsis after it, the ellipsis alone when none does (`common::truncate_label`,
/// measured unshaped as production measures a width it bakes in).
pub(crate) fn truncate_label(text: &str, limit: f64, measure: impl Fn(&str) -> Result<f64, Diagnostic>) -> Result<String, Diagnostic> {
    if measure(text)? <= limit {
        return Ok(text.to_string());
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi, mut best) = (0, chars.len(), None);
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let prefix: String = chars[..mid].iter().collect();
        let candidate = format!("{}\u{2026}", prefix.trim_end());
        if measure(&candidate)? <= limit {
            best = Some(candidate);
            lo = mid + 1;
        } else if mid == 0 {
            break;
        } else {
            hi = mid - 1;
        }
    }
    Ok(best.unwrap_or_else(|| "\u{2026}".to_string()))
}
