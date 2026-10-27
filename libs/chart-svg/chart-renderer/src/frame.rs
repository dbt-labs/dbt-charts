//! The outer chrome of a chart card: root `<svg>`, background rect and Vega's
//! title band, plus the fit that negotiates the body ("data box") inside the
//! padding. A family draws its body into the origin and size this yields;
//! a body with no axes or legend (`custom`) fits with no ink and gets the box
//! below the title.

use crate::common::{Font, TitleBlock, TitleStyle};
use print::print;
use scene::{Attrs, Extent, Node};
use measure::FontBook;
use crate::vega_fit::{self, Bounds, FitMargins, TitleInk};
use crate::Payload;

/// The card: the canvas `<svg>` and the padded rect the body fits into.
/// `dim` prints the root's `width`/`height` (and so the rect's): families
/// disagree on how a whole-number size reads (`600` vs Python's `600.0`).
pub struct ChartFrame<'a> {
    /// The card's width, which the emitters' own decisions read.
    pub w: f64,
    /// The width the view fits to and the canvas prints: the card's, unless the
    /// card has no room (`Payload::view_width`).
    pub view_w: f64,
    pub h: f64,
    /// `(left, right, top, bottom)`.
    pub pad: (f64, f64, f64, f64),
    pub background: &'a str,
    pub dim: fn(f64) -> String,
}

impl<'a> ChartFrame<'a> {
    pub fn new(
        w: f64,
        h: f64,
        pad: (f64, f64, f64, f64),
        background: &'a str,
        dim: fn(f64) -> String,
    ) -> Self {
        ChartFrame { w, view_w: w, h, pad, background, dim }
    }

    /// [`Self::of`] over a card with no room: the view takes the theme's default width.
    pub fn of_view(payload: &Payload, background: &'a str, x_scale: crate::ViewScale) -> Result<Self, String> {
        Self::at_view(payload.width, payload.height, payload.padding.as_ref(), payload.board_style.as_ref(), background, x_scale)
    }

    /// [`Self::at`] over a card with no room: the view takes the theme's default width.
    pub fn at_view(
        w: f64,
        h: f64,
        padding: Option<&crate::Padding>,
        board_style: Option<&crate::BoardStyle>,
        background: &'a str,
        x_scale: crate::ViewScale,
    ) -> Result<Self, String> {
        Ok(ChartFrame { view_w: crate::view_width(w, board_style, x_scale)?, ..Self::at(w, h, padding, background) })
    }

    /// The payload's own card, its dims printed as bare numbers.
    pub fn of(payload: &Payload, background: &'a str) -> Self {
        Self::at(payload.width, payload.height, payload.padding.as_ref(), background)
    }

    /// A `w` x `h` card, its dims printed as bare numbers.
    pub fn at(w: f64, h: f64, padding: Option<&crate::Padding>, background: &'a str) -> Self {
        Self::new(
            w,
            h,
            (
                padding.map_or(0.0, |p| p.left),
                padding.map_or(0.0, |p| p.right),
                padding.map_or(0.0, |p| p.top),
                padding.map_or(0.0, |p| p.bottom),
            ),
            background,
            crate::svg::num_full,
        )
    }

    /// The frame over a canvas of its own size: a plot laid out under
    /// `autosize: pad` grows the canvas around its ink.
    pub fn sized(&self, w: f64, h: f64) -> ChartFrame<'a> {
        ChartFrame { w, view_w: w, h, pad: self.pad, background: self.background, dim: self.dim }
    }

    /// The width a title wraps against.
    pub fn avail_w(&self) -> f64 {
        self.w - self.pad.0 - self.pad.1
    }

    /// `fit_plot` over `ink` and the title. A card with no room fits a plot
    /// with no width or height, as Vega's `max(0, …)` clamp does. Pass a no-op
    /// `ink` for a body with no axes.
    pub fn fit(&self, title: &FrameTitle, ink: impl FnMut(f64, f64, &mut Bounds)) -> FitMargins {
        self.fit_beside(0.0, title, ink)
    }

    /// [`Self::fit`] for a plot that shares the card's width with a pane of
    /// `reserve` px beside it (an endpoint rail's concat).
    pub fn fit_beside(&self, reserve: f64, title: &FrameTitle, ink: impl FnMut(f64, f64, &mut Bounds)) -> FitMargins {
        vega_fit::fit_plot(self.view_w - reserve, self.h, self.pad, ink, title.ink())
    }

    /// The body origin a fit yields, in canvas coordinates.
    pub fn origin(&self, m: &FitMargins) -> (f64, f64) {
        (self.pad.0 + m.left, self.pad.2 + m.top)
    }

    /// Writes the opening `<svg>` tag.
    pub fn root(&self, out: &mut String) {
        let printed = print(&[self.root_node(Vec::new())]);
        out.push_str(printed.strip_suffix("</svg>").expect("a childless root prints its end tag"));
    }

    /// Writes the background rect.
    pub fn background(&self, out: &mut String) {
        out.push_str(&print(&[self.background_node()]));
    }

    /// The root `<svg>` around `children`.
    fn root_node(&self, children: Vec<Node>) -> Node {
        let (w, h) = ((self.dim)(self.view_w), (self.dim)(self.h));
        let attrs = Attrs::new()
            .set("xmlns", "http://www.w3.org/2000/svg")
            .set("width", w.as_str())
            .set("height", h.as_str())
            .set("viewBox", format!("0 0 {w} {h}"));
        Node::Svg { extent: Extent { width: self.view_w, height: self.h }, attrs, children }
    }

    fn background_node(&self) -> Node {
        Node::Rect(
            Attrs::new()
                .set("width", (self.dim)(self.view_w))
                .set("height", (self.dim)(self.h))
                .set("fill", self.background),
        )
    }

    /// The whole card: root and background around `body`.
    pub fn svg(&self, body: Vec<Node>) -> Node {
        self.root_node(std::iter::once(self.background_node()).chain(body).collect())
    }

    /// Root and background; returns the byte offset where the background
    /// starts, for a composite that splices the body out from under its own
    /// background.
    pub fn open(&self, out: &mut String) -> usize {
        self.root(out);
        let rect_at = out.len();
        self.background(out);
        rect_at
    }
}

/// Where a composite cuts a printed panel: after the card's background rect, before its
/// closing tag, and around the value axis.
pub struct PanelCuts {
    pub content_start: usize,
    pub svg_end: usize,
    value_axis: Option<std::ops::Range<usize>>,
}

impl PanelCuts {
    /// The value axis's cut; the panel was drawn with one.
    pub fn value_axis(&self) -> std::ops::Range<usize> {
        self.value_axis.clone().expect("these cuts were taken with a value axis")
    }
}

/// Where a panel's value axis sits among the children of its printed card.
pub enum ValueAxisAt {
    /// `plot[range]`, inside the plot group.
    Plot(std::ops::Range<usize>),
    /// `tail[range]`, after the plot group.
    Tail(std::ops::Range<usize>),
}

impl ChartFrame<'_> {
    /// The cuts of the panel this frame draws: `head` ahead of the plot `group` of children `plot`,
    /// then `tail`, with the value axis, if the panel has one, at `value_axis`. Printing is concatenative, so each cut is
    /// the length of what is printed ahead of it.
    pub fn cuts(&self, head: &[Node], group: &Attrs, plot: &[Node], tail: &[Node], value_axis: Option<&ValueAxisAt>) -> PanelCuts {
        let opened = print(&[self.svg(Vec::new())]);
        let content_start = opened.strip_suffix("</svg>").expect("a childless card prints its end tag").len();
        let opened_group = print(&[Node::group(group.clone(), Vec::new())]);
        let group_open = opened_group.strip_suffix("</g>").expect("a childless group prints its end tag").len();
        let ahead = content_start + print(head).len() + group_open;
        let after_plot = ahead + print(plot).len() + "</g>".len();
        let value_axis = value_axis.map(|at| {
            let (children, base, range) = match at {
                ValueAxisAt::Plot(range) => (plot, ahead, range),
                ValueAxisAt::Tail(range) => (tail, after_plot, range),
            };
            let start = base + print(&children[..range.start]).len();
            start..start + print(&children[range.clone()]).len()
        });
        let svg_end = after_plot + print(tail).len();
        PanelCuts { content_start, svg_end, value_axis }
    }
}

/// A chart title after production's overflow wrap, with the fit ink it
/// reserves and the style it paints in.
pub struct FrameTitle {
    block: TitleBlock,
    style: Option<TitleStyle>,
    /// The size the title paints at.
    pub font_size: f64,
    /// The title with `font.case` applied.
    pub text: Option<String>,
    subtitle: Option<String>,
}

impl FrameTitle {
    /// `common::wrap_title_block` over the resolved title fields, `wrap_w`
    /// wide. `title_font` is the width-resolved `style.title_font`, which
    /// overrides `title_style`'s font; pass `None` for a family that never
    /// tiers it.
    pub fn new(
        title: Option<&str>,
        subtitle: Option<&str>,
        title_font: Option<&Font>,
        title_style: Option<&TitleStyle>,
        wrap_w: f64,
        fonts: &FontBook,
    ) -> Result<Self, String> {
        let style = crate::common::effective_title_style(title_font, title_style)
            .or_else(|| title_style.cloned());
        let font_size = style.as_ref().map_or(18.0, |t| t.font.size_or(18.0));
        let text = title
            .map(|t| {
                let case = style.as_ref().and_then(|s| s.font.case.as_deref()).unwrap_or("none");
                display_text::case::apply_case(t, case)
            })
            .transpose()?;
        let block = crate::common::wrap_title_block(
            text.as_deref(),
            subtitle,
            style.as_ref(),
            font_size,
            crate::common::title_measure_font_size(title_style),
            wrap_w,
            fonts,
        )?;
        Ok(FrameTitle { block, style, font_size, text, subtitle: subtitle.map(str::to_string) })
    }

    /// `WARN-CHART-TITLE-TRUNCATED` for each row the pre-pass cut.
    pub fn warn_truncated(&self, chart_id: &str) {
        crate::common::warn_title_truncations(
            chart_id,
            (self.text.as_deref(), self.block.title_truncated),
            (self.subtitle.as_deref(), self.block.subtitle_truncated),
        );
    }

    /// [`Self::warn_truncated`]'s findings, for a layout that returns its own.
    pub fn truncations(&self, chart_id: &str) -> Vec<crate::diagnostic::Diagnostic> {
        crate::common::title_truncations(
            chart_id,
            (self.text.as_deref(), self.block.title_truncated),
            (self.subtitle.as_deref(), self.block.subtitle_truncated),
        )
    }

    pub fn ink(&self) -> Option<&TitleInk> {
        self.block.ink.as_ref()
    }

    pub fn block(&self) -> &TitleBlock {
        &self.block
    }

    /// Paints the title group with its top-left at `(x, y)`.
    pub fn paint_at(&self, out: &mut String, x: f64, y: f64) {
        out.push_str(&print(&self.nodes_at(x, y)));
    }

    /// The title group with its top-left at `(x, y)`.
    pub fn nodes_at(&self, x: f64, y: f64) -> Vec<Node> {
        crate::common::title_block_nodes(&self.block, self.style.as_ref(), x, y, self.font_size)
    }

    /// Paints the title at the fit's `title_pos` relative to the body
    /// `origin`; false when there is nothing to paint (untitled, or no fit).
    pub fn paint(&self, out: &mut String, title_pos: Option<(f64, f64)>, origin: (f64, f64)) -> bool {
        let nodes = self.nodes(title_pos, origin);
        out.push_str(&print(&nodes));
        !nodes.is_empty()
    }

    /// [`Self::paint`] as nodes; none when there is nothing to paint.
    pub fn nodes(&self, title_pos: Option<(f64, f64)>, origin: (f64, f64)) -> Vec<Node> {
        match (title_pos, self.block.title_lines.is_empty()) {
            (Some((tx, ty)), false) => self.nodes_at(origin.0 + tx, origin.1 + ty),
            _ => Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_card_node_prints_what_open_and_the_closing_tag_write() {
        let frame = ChartFrame::new(600.0, 400.5, (0.0, 0.0, 0.0, 0.0), "#f<f", crate::svg::num_full);
        let mut written = String::new();
        frame.open(&mut written);
        written.push_str("<rect/></svg>");
        assert_eq!(print(&[frame.svg(vec![Node::Rect(Attrs::new())])]), written);
    }
}
