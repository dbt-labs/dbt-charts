//! A chart bound over no rows ([`bound::Cartesian::NoData`]): its canvas, its title band when it has
//! a title (laid out as a plane's is), and a muted "No data" centered in the plot box, which is the
//! cell inside the card and chart padding and under the title's band. There are no scales, so there
//! are no axes, grids or marks to lay out; the text is set in the face the chart's y axis labels
//! would have had (spec Q10).

use bind::bound::{self, Family};
use board_model::style::board::EmojiMode;
use diagnostic::{diag, Diagnostic};
use measure::shaped::FontBook;
use scene::{Attrs, Extent, Node, Role, Shape};

use crate::geom::{Insets, Rect, Size};
use crate::plot::cartesian::{heading_block, opaque_canvas, Heading, Ink};
use crate::plot::negotiate::body;

/// The chart's root `<svg>` for a cell of `cell`, inside the board's `card_padding`. `heading` is
/// the chart's title resolved, present exactly when the chart has one. A subtitle is the caller's to
/// refuse: this draws none.
pub fn draw(chart: &bound::Chart, heading: Option<&Heading>, cell: Size, book: &FontBook, emoji: &EmojiMode, card_padding: f64) -> Result<(Vec<Node>, Vec<Diagnostic>), Diagnostic> {
    let (Ok(Family::Cartesian(bound::Cartesian::NoData(face))), Some(frame)) = (&chart.family, &chart.frame) else {
        return Err(diag::err_internal("an empty plot reached a chart that is not bound over no rows with a frame"));
    };
    let canvas = opaque_canvas(frame)?;
    let padding = Insets {
        top: frame.padding.top + card_padding,
        right: frame.padding.right + card_padding,
        bottom: frame.padding.bottom + card_padding,
        left: frame.padding.left + card_padding,
    };
    let title = heading_block(chart, heading, cell.width, (padding.left, padding.top), padding.right, book, emoji)?;
    let area = body(Rect { x: 0.0, y: 0.0, width: cell.width, height: cell.height }, padding, title.as_ref().map(|(title, _)| &title.metrics), Insets::ZERO);
    let ink = Ink::of(face, emoji)?;
    let font = ink.font();
    let centered = book.centered_baseline_offset(font.family, font.size).map_err(diag::err_internal)?;
    let text = display_text::language::tr("chart.no_data", None, &[])?;
    let at = (area.x + area.width / 2.0, area.y + area.height / 2.0 + centered);
    let attrs = ink.attrs(Some("middle")).set("data-authored-kind", "no_data");
    let mut children = vec![Node::rect(Role::Chrome, (0.0, 0.0, cell.width, cell.height), Attrs::new().set("fill", canvas.as_str()))];
    if let Some((_, nodes)) = &title {
        children.extend(nodes.iter().cloned());
    }
    children.push(Node::text(Role::Label, at, attrs, text.as_str()));
    let extent = Extent { width: cell.width, height: cell.height };
    let diagnostics = title.map_or_else(Vec::new, |(title, _)| title.diagnostics);
    Ok((vec![Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent, children })], diagnostics))
}
