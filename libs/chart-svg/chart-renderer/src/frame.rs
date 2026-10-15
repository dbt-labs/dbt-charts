//! The outer chrome of a chart card: root `<svg>`, background rect and Vega's
//! title band, plus the fit that negotiates the body ("data box") inside the
//! padding. A family draws its body into the origin and size this yields;
//! a body with no axes or legend (`custom`) fits with no ink and gets the box
//! below the title.

use crate::common::{Font, TitleBlock, TitleStyle};
use crate::svg::xml_escape_attr;
use crate::text::FontBook;
use crate::vega_fit::{self, Bounds, FitMargins, TitleInk};
use crate::Payload;

/// The card: the canvas `<svg>` and the padded rect the body fits into.
/// `dim` prints the root's `width`/`height` (and so the rect's): families
/// disagree on how a whole-number size reads (`600` vs Python's `600.0`).
pub struct ChartFrame<'a> {
    pub w: f64,
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
        ChartFrame { w, h, pad, background, dim }
    }

    /// The payload's own card, its dims printed as bare numbers.
    pub fn of(payload: &Payload, background: &'a str) -> Self {
        let pad = payload.padding.as_ref();
        Self::new(
            payload.width,
            payload.height,
            (
                pad.map_or(0.0, |p| p.left),
                pad.map_or(0.0, |p| p.right),
                pad.map_or(0.0, |p| p.top),
                pad.map_or(0.0, |p| p.bottom),
            ),
            background,
            crate::svg::num_full,
        )
    }

    /// The width a title wraps against.
    pub fn avail_w(&self) -> f64 {
        self.w - self.pad.0 - self.pad.1
    }

    /// `fit_plot` over `ink` and the title, or the named refusal when nothing
    /// is left for the plot. Pass a no-op `ink` for a body with no axes.
    pub fn fit(
        &self,
        family: &str,
        title: &FrameTitle,
        ink: impl FnMut(f64, f64, &mut Bounds),
    ) -> Result<FitMargins, String> {
        let m = vega_fit::fit_plot(self.w, self.h, self.pad, ink, title.ink());
        if m.plot_w <= 0.0 || m.plot_h <= 0.0 {
            return Err(format!("{family}: chart box {}×{} too small for content", self.w, self.h));
        }
        Ok(m)
    }

    /// The body origin a fit yields, in canvas coordinates.
    pub fn origin(&self, m: &FitMargins) -> (f64, f64) {
        (self.pad.0 + m.left, self.pad.2 + m.top)
    }

    /// Writes the opening `<svg>` tag.
    pub fn root(&self, out: &mut String) {
        let (w, h) = ((self.dim)(self.w), (self.dim)(self.h));
        out.push_str(&format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" \
             viewBox=\"0 0 {w} {h}\">"
        ));
    }

    /// Writes the background rect.
    pub fn background(&self, out: &mut String) {
        out.push_str(&format!(
            "<rect width=\"{}\" height=\"{}\" fill=\"{}\"/>",
            (self.dim)(self.w),
            (self.dim)(self.h),
            xml_escape_attr(self.background)
        ));
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
                crate::case::apply_case(t, case)
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

    pub fn ink(&self) -> Option<&TitleInk> {
        self.block.ink.as_ref()
    }

    pub fn block(&self) -> &TitleBlock {
        &self.block
    }

    /// Paints the title group with its top-left at `(x, y)`.
    pub fn paint_at(&self, out: &mut String, x: f64, y: f64) {
        crate::common::emit_title_block_vega(out, &self.block, self.style.as_ref(), x, y, self.font_size);
    }

    /// Paints the title at the fit's `title_pos` relative to the body
    /// `origin`; false when there is nothing to paint (untitled, or no fit).
    pub fn paint(&self, out: &mut String, title_pos: Option<(f64, f64)>, origin: (f64, f64)) -> bool {
        let (Some((tx, ty)), false) = (title_pos, self.block.title_lines.is_empty()) else {
            return false;
        };
        self.paint_at(out, origin.0 + tx, origin.1 + ty);
        true
    }
}
