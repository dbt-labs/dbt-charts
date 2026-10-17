//! A chart title (and subtitle) laid out against the padded card width — the
//! one reading of `apply_title_overflow_to_spec` every family that hangs a
//! title above a Vega layout shares (scatter's fit arm, and the facet
//! composites of scatter and heatmap).

use crate::common::{Font, TitleBlock, TitleStyle};
use crate::text::FontBook;
use crate::vega_fit;

/// The resolved fields a title reads, borrowed off whichever chart owns them.
pub(crate) struct TitleSource<'a> {
    /// Names the refusing family in an error.
    pub(crate) family: &'a str,
    pub(crate) title: Option<&'a str>,
    pub(crate) subtitle: Option<&'a str>,
    pub(crate) title_font: Option<&'a Font>,
    pub(crate) title_style: Option<&'a TitleStyle>,
}

pub(crate) struct TitlePlan {
    text: String,
    subtitle: Option<String>,
    pub(crate) ink: vega_fit::TitleInk,
    /// Set when the title or subtitle wraps onto measured lines.
    block: Option<TitleBlock>,
    font_size: f64,
}

pub(crate) enum TitleLayout {
    Untitled,
    Plan(TitlePlan),
}

impl TitlePlan {
    /// `WARN-CHART-TITLE-TRUNCATED` for each row the wrap cut.
    pub(crate) fn warn_truncated(&self, chart_id: &str) {
        if let Some(block) = &self.block {
            crate::common::warn_title_truncations(
                chart_id,
                (Some(&self.text), block.title_truncated),
                (self.subtitle.as_deref(), block.subtitle_truncated),
            );
        }
    }
}

impl TitleSource<'_> {
    fn effective_style(&self) -> Option<TitleStyle> {
        crate::common::effective_title_style(self.title_font, self.title_style)
    }
}

/// `apply_title_overflow_to_spec` against `avail_w`, the padded card width
/// (a facet composite carries no top-level width, so production passes it in
/// by hand). An over-wide title or subtitle takes the wrap-two law.
pub(crate) fn layout_title(
    src: &TitleSource,
    fonts: &FontBook,
    avail_w: f64,
) -> Result<TitleLayout, String> {
    let eff_title_style = src.effective_style();
    let eff_title_ref = eff_title_style.as_ref().or(src.title_style);
    let font_size = eff_title_ref.and_then(|ts| ts.font.size).unwrap_or(18.0);
    let Some(raw_title) = src.title else { return Ok(TitleLayout::Untitled) };
    let case = eff_title_ref.and_then(|ts| ts.font.case.as_deref()).unwrap_or("none");
    let text = display_text::case::apply_case(raw_title, case)?;
    let subtitle = src.subtitle;
    let family = eff_title_ref.and_then(|ts| ts.font.family.as_deref()).unwrap_or("serif");
    let offset = eff_title_ref.and_then(|ts| ts.position.as_ref()).and_then(|pos| pos.offset).unwrap_or(10.0);
    let width = fonts.measure(&text, family, font_size)?;
    let sub_measure = match subtitle {
        None => None,
        Some(sub) => {
            let sf = eff_title_ref.and_then(|ts| ts.subtitle.as_ref());
            let sub_fs = sf.and_then(|f| f.font.size).unwrap_or(11.0);
            let sub_family = sf.and_then(|f| f.font.family.as_deref()).unwrap_or("sans-serif");
            Some((sub_fs, fonts.measure(sub, sub_family, sub_fs)?))
        }
    };
    let overflows = width > avail_w || sub_measure.is_some_and(|(_, sub_w)| sub_w > avail_w);
    let (ink, block) = if overflows {
        let block = crate::common::wrap_title_block(
            Some(&text),
            subtitle,
            eff_title_ref,
            font_size,
            crate::common::title_measure_font_size(src.title_style),
            avail_w,
            fonts,
        )?;
        let ink = block
            .ink
            .clone()
            .ok_or_else(|| format!("{}: a wrapped title block carries no ink", src.family))?;
        (ink, Some(block))
    } else {
        let ink = match sub_measure {
            None => vega_fit::TitleInk::single(font_size, offset, width),
            Some((sub_fs, sub_w)) => vega_fit::TitleInk::with_subtitle(font_size, sub_fs, offset, width, sub_w),
        };
        (ink, None)
    };
    Ok(TitleLayout::Plan(TitlePlan { text, subtitle: subtitle.map(str::to_string), ink, block, font_size }))
}

impl TitlePlan {
    /// Paints the title group at the absolute `(x, y)` of its origin.
    pub(crate) fn paint(&self, src: &TitleSource, svg: &mut String, x: f64, y: f64) {
        let eff_title_style = src.effective_style();
        let style = eff_title_style.as_ref().or(src.title_style);
        match &self.block {
            Some(block) => crate::common::emit_title_block_vega(svg, block, style, x, y, self.font_size),
            None => {
                crate::common::emit_title_vega(svg, &self.text, style, x, y, true);
                if let Some(sub) = &self.subtitle {
                    crate::common::emit_subtitle_vega(svg, std::slice::from_ref(sub), style, x, y, self.font_size);
                }
            }
        }
    }
}
