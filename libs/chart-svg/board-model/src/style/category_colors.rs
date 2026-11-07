//! `style/theme/category_colors.py`: the board-level category-to-color binding.
//!
//! Only `CategoryColorBinding` is authored. `CategoryColorScale` and its helpers
//! (`color_at`, `series_colors`, `category_scale_for`) are the resolved,
//! render-time tier, which lives in render `bind` (D-19).

use crate::facet::{open_names, Facet};
use indexmap::IndexMap;
use model_derive::Model;

/// Python's `core/colors.py` token shape: a palette token, a hex, or `none`/`transparent`.
const CATEGORY_COLOR_PATTERN: &str = r"(?:^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+|\[\d+\])(\.(dark|light|pale|deep))?$)|(?:^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$)|(?:^(?:[nN][oO][nN][eE]|[tT][rR][aA][nN][sS][pP][aA][rR][eE][nN][tT])$)";

fn category_color(v: &str) -> Result<String, String> {
    if color::is_color_token(v) || color::is_sanitizable_color(v) {
        return Ok(v.to_string());
    }
    Err(format!(
        "category_colors color '{v}' is neither a palette token (e.g. `category[1]`, \
         `dbt-grays.8`) nor a CSS hex color (e.g. `#1f77b4`)."
    ))
}

open_names!(
    /// A palette token (`category[1]`, `dbt-grays.8`) or a literal hex. Whether
    /// a token resolves needs the theme's palettes, so a later pass checks that.
    CategoryColor = "category_color",
    crate::names::CategorySlotToken::VALUES,
    category_color,
    model_core::Keywords::pattern(CATEGORY_COLOR_PATTERN)
);

/// Value-to-color assignments for one data field, shared by every chart.
///
/// Mapping order is the scale domain order, so it also fixes which swatch a
/// value lands on when the color is a palette token rather than a literal.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CategoryColorBinding {
    /// Data value to palette token (`category[1]`, `dbt-grays.8`) or literal hex.
    /// Tokens resolve against the board's theme.
    pub values: IndexMap<String, CategoryColor>,
}
