//! Area family — delegates to line::render_inner with draw_areas=true.
//!
//! Area charts share all geometry, scale, and axis code with line charts.
//! The additional rendering passes (backdrop areas, semi-transparent fills)
//! are gated by the draw_areas flag inside line::render_inner.

use crate::text::FontBook;
use crate::Payload;

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    crate::families::line::render_inner(payload, fonts, true)
}
