//! Style configuration for SVG rendering.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/style.py`. Field names,
//! order, and defaults match the Python `Style` dataclass exactly. Not
//! ported: `with_updates` (Python `**kwargs` has no Rust equivalent; callers
//! use Rust struct-update syntax `Style { field: x, ..Style::default() }`
//! instead — production always builds a full kwargs dict via
//! `compact_style_kwargs`, never a partial update), `merge_styles`,
//! `StylePresets`/`LIGHT_THEME`/`DARK_THEME`/`GITHUB_THEME` (theme/preset
//! constants no production call path reaches — `get_compact_style` always
//! builds every field from the resolved board theme).

use crate::css::{is_css_breaking, python_repr};

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CodeBlockOverflow {
    Wrap,
    Show,
    Hide,
    Ellipsis,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TextAlign {
    Left,
    Center,
    Right,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct Style {
    // Fonts
    pub font_family: String,
    pub mono_font_family: String,
    pub base_font_size: f64,
    pub font_weight: Option<String>,
    pub bold_font_weight: String,
    pub avoid_runts: bool,
    pub line_height: f64,

    // Colors
    pub text_color: String,
    pub heading_color: Option<String>,
    pub link_color: String,
    pub link_underline: bool,
    pub code_color: String,
    pub code_background: String,
    pub code_block_border_color: String,
    pub code_block_border_width: f64,
    pub blockquote_color: String,
    pub blockquote_border_color: String,
    pub blockquote_background: String,
    pub blockquote_border_radius: f64,

    // Code font overrides
    pub code_font_family: String,
    pub code_font_weight: String,
    pub code_font_style: String,
    pub code_font_size: f64,
    pub code_font_scale: f64,
    pub code_font_decoration: String,
    pub code_font_case: String,

    // Inline code chip geometry
    pub inline_code_padding: f64,
    pub inline_code_border_radius: f64,

    // Blockquote font overrides
    pub blockquote_font_family: String,
    pub blockquote_font_weight: String,
    pub blockquote_font_style: String,
    pub blockquote_font_size: f64,
    pub blockquote_font_decoration: String,
    pub blockquote_font_case: String,

    // Heading scales
    pub h1_scale: f64,
    pub h2_scale: f64,
    pub h3_scale: f64,
    pub h4_scale: f64,
    pub h5_scale: f64,
    pub h6_scale: f64,
    pub h1_size: Option<f64>,
    pub h2_size: Option<f64>,
    pub h3_size: Option<f64>,
    pub h4_size: Option<f64>,
    pub h5_size: Option<f64>,
    pub h6_size: Option<f64>,
    /// Empty means the heading rule inherits `font_family`.
    pub heading_font_family: String,
    pub heading_font_weight: String,
    pub heading_line_height: Option<f64>,
    pub heading_margin_top: f64,
    pub heading_margin_bottom: f64,
    pub heading_margin_top_px: Option<f64>,
    pub heading_margin_bottom_px: Option<f64>,

    // Spacing
    pub paragraph_spacing: f64,
    pub list_indent: f64,
    pub list_item_spacing: f64,
    pub code_block_padding: f64,
    pub code_block_border_radius: f64,
    pub code_block_overflow: CodeBlockOverflow,
    pub code_highlight: bool,
    pub code_theme: String,
    pub blockquote_padding: f64,
    pub blockquote_border_width: f64,

    // Table
    pub table_border_color: String,
    pub table_border_width: f64,
    pub table_border_dasharray: String,
    pub table_border_visible: bool,
    pub table_header_background: String,
    pub table_cell_padding: f64,

    // Horizontal rule
    pub hr_color: String,
    pub hr_height: f64,
    pub hr_dasharray: String,
    pub hr_visible: bool,

    // Images
    pub image_width: Option<f64>,
    pub image_height: Option<f64>,
    pub image_fallback_aspect_ratio: f64,
    pub image_enforce_aspect_ratio: bool,
    pub image_preserve_aspect_ratio: String,

    // Text measurement — last-resort estimates
    pub char_width_ratio: f64,
    pub bold_char_width_ratio: f64,
    pub italic_char_width_ratio: f64,

    pub text_align: TextAlign,
}

impl Default for Style {
    fn default() -> Self {
        Style {
            font_family: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif".to_string(),
            mono_font_family: "ui-monospace, 'SF Mono', Menlo, Consolas, monospace".to_string(),
            base_font_size: 14.0,
            font_weight: None,
            bold_font_weight: "bold".to_string(),
            avoid_runts: false,
            line_height: 1.5,

            text_color: "#1a1a1a".to_string(),
            heading_color: None,
            link_color: "#2563eb".to_string(),
            link_underline: true,
            code_color: "#be185d".to_string(),
            code_background: "#f3f4f6".to_string(),
            code_block_border_color: String::new(),
            code_block_border_width: 1.0,
            blockquote_color: "#6b7280".to_string(),
            blockquote_border_color: "#d1d5db".to_string(),
            blockquote_background: String::new(),
            blockquote_border_radius: 0.0,

            code_font_family: String::new(),
            code_font_weight: String::new(),
            code_font_style: String::new(),
            code_font_size: 0.0,
            code_font_scale: 0.9,
            code_font_decoration: String::new(),
            code_font_case: String::new(),

            inline_code_padding: 3.0,
            inline_code_border_radius: 3.0,

            blockquote_font_family: String::new(),
            blockquote_font_weight: String::new(),
            blockquote_font_style: String::new(),
            blockquote_font_size: 0.0,
            blockquote_font_decoration: String::new(),
            blockquote_font_case: String::new(),

            h1_scale: 2.0,
            h2_scale: 1.6,
            h3_scale: 1.35,
            h4_scale: 1.15,
            h5_scale: 1.0,
            h6_scale: 0.9,
            h1_size: None,
            h2_size: None,
            h3_size: None,
            h4_size: None,
            h5_size: None,
            h6_size: None,
            heading_font_family: String::new(),
            heading_font_weight: "bold".to_string(),
            heading_line_height: None,
            heading_margin_top: 1.5,
            heading_margin_bottom: 0.5,
            heading_margin_top_px: None,
            heading_margin_bottom_px: None,

            paragraph_spacing: 12.0,
            list_indent: 18.0,
            list_item_spacing: 1.0,
            code_block_padding: 12.0,
            code_block_border_radius: 4.0,
            code_block_overflow: CodeBlockOverflow::Wrap,
            code_highlight: false,
            code_theme: "monokai".to_string(),
            blockquote_padding: 16.0,
            blockquote_border_width: 3.0,

            table_border_color: "#e5e7eb".to_string(),
            table_border_width: 1.0,
            table_border_dasharray: String::new(),
            table_border_visible: true,
            table_header_background: "#f9fafb".to_string(),
            table_cell_padding: 8.0,

            hr_color: "#e5e7eb".to_string(),
            hr_height: 1.0,
            hr_dasharray: String::new(),
            hr_visible: true,

            image_width: None,
            image_height: None,
            image_fallback_aspect_ratio: 16.0 / 9.0,
            image_enforce_aspect_ratio: false,
            image_preserve_aspect_ratio: "xMidYMid meet".to_string(),

            char_width_ratio: 0.48,
            bold_char_width_ratio: 0.58,
            italic_char_width_ratio: 0.447,

            text_align: TextAlign::Left,
        }
    }
}

impl Style {
    /// Font size scale for a heading level (1-6); 1.0 for anything else,
    /// mirroring the Python dict `.get(level, 1.0)` fallback.
    pub fn get_heading_scale(&self, level: u8) -> f64 {
        match level {
            1 => self.h1_scale,
            2 => self.h2_scale,
            3 => self.h3_scale,
            4 => self.h4_scale,
            5 => self.h5_scale,
            6 => self.h6_scale,
            _ => 1.0,
        }
    }

    /// Absolute font size in pixels for a heading level: the `h*_size`
    /// override when set, else `base_font_size * get_heading_scale(level)`.
    pub fn get_heading_size(&self, level: u8) -> f64 {
        let absolute = match level {
            1 => self.h1_size,
            2 => self.h2_size,
            3 => self.h3_size,
            4 => self.h4_size,
            5 => self.h5_size,
            6 => self.h6_size,
            _ => None,
        };
        absolute.unwrap_or(self.base_font_size * self.get_heading_scale(level))
    }

    /// Color for headings, falling back to `text_color`.
    pub fn get_heading_color(&self) -> &str {
        // Python is `self.heading_color or self.text_color` — falsy, not
        // just `None`, falls back: an authored `heading_color=""` must
        // fall back to `text_color` too, not paint `fill: ;`.
        match self.heading_color.as_deref() {
            Some(color) if !color.is_empty() => color,
            _ => &self.text_color,
        }
    }

    /// `style.py:281-290` `Style.__post_init__`: every string field except a
    /// font-family stack (escaped by the renderer instead, `css.rs`) is refused
    /// when it holds a CSS-breaking character, naming the first such field in
    /// declaration order and the value's Python `repr`. Reads the fields the way
    /// Python's `fields(self)` does (declaration order, string values only,
    /// `None` skipped) by walking the serialized struct, so a field added to
    /// `Style` is validated without a second list to keep in step.
    pub fn validate_css(&self) -> Result<(), String> {
        let serde_json::Value::Object(fields) =
            serde_json::to_value(self).expect("Style serializes to a JSON object")
        else {
            unreachable!("Style is a struct")
        };
        for (name, value) in &fields {
            let Some(text) = value.as_str() else { continue };
            if !name.ends_with("font_family") && text.chars().any(is_css_breaking) {
                return Err(format!(
                    "Style.{name} is not valid CSS: {}",
                    python_repr(text)
                ));
            }
        }
        Ok(())
    }

    /// SVG `text-anchor` value corresponding to `text_align`.
    pub fn get_text_anchor(&self) -> &'static str {
        match self.text_align {
            TextAlign::Left => "start",
            TextAlign::Center => "middle",
            TextAlign::Right => "end",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    type StyleMutation<'a> = Box<dyn Fn(&mut Style) + 'a>;

    #[test]
    fn test_default_font_sizes() {
        let style = Style::default();
        assert_eq!(style.get_heading_size(1), 28.0);
        assert_eq!(style.get_heading_size(6), 12.6);
    }

    #[test]
    fn test_h1_override() {
        let style = Style {
            h1_size: Some(40.0),
            ..Style::default()
        };
        assert_eq!(style.get_heading_size(1), 40.0);
    }

    #[test]
    fn test_get_heading_color_falls_back() {
        let style = Style::default();
        assert_eq!(style.get_heading_color(), "#1a1a1a");
        let styled = Style {
            heading_color: Some("#fff".to_string()),
            ..Style::default()
        };
        assert_eq!(styled.get_heading_color(), "#fff");
    }

    #[test]
    fn test_get_heading_color_empty_string_falls_back_too() {
        // Python is `self.heading_color or self.text_color` — an authored
        // "" is falsy, not just None, and must still fall back.
        let style = Style {
            heading_color: Some(String::new()),
            ..Style::default()
        };
        assert_eq!(style.get_heading_color(), "#1a1a1a");
    }

    #[test]
    fn test_get_text_anchor() {
        assert_eq!(Style::default().get_text_anchor(), "start");
        let centered = Style {
            text_align: TextAlign::Center,
            ..Style::default()
        };
        assert_eq!(centered.get_text_anchor(), "middle");
    }

    #[test]
    fn default_style_is_valid_css() {
        assert_eq!(Style::default().validate_css(), Ok(()));
    }

    #[test]
    fn css_breaking_color_is_refused_with_the_field_and_the_repr_of_the_value() {
        let style = Style {
            text_color: "red;} svg{background:url(//evil)}".to_string(),
            ..Style::default()
        };
        assert_eq!(
            style.validate_css(),
            Err("Style.text_color is not valid CSS: 'red;} svg{background:url(//evil)}'"
                .to_string())
        );
    }

    #[test]
    fn every_non_font_family_string_field_is_refused() {
        let evil = || "red;}".to_string();
        let cases: Vec<(&str, StyleMutation<'_>)> = vec![
            ("font_weight", Box::new(|s| s.font_weight = Some(evil()))),
            ("bold_font_weight", Box::new(|s| s.bold_font_weight = evil())),
            ("text_color", Box::new(|s| s.text_color = evil())),
            ("heading_color", Box::new(|s| s.heading_color = Some(evil()))),
            ("link_color", Box::new(|s| s.link_color = evil())),
            ("code_color", Box::new(|s| s.code_color = evil())),
            ("code_background", Box::new(|s| s.code_background = evil())),
            ("code_block_border_color", Box::new(|s| s.code_block_border_color = evil())),
            ("blockquote_color", Box::new(|s| s.blockquote_color = evil())),
            ("blockquote_border_color", Box::new(|s| s.blockquote_border_color = evil())),
            ("blockquote_background", Box::new(|s| s.blockquote_background = evil())),
            ("code_font_weight", Box::new(|s| s.code_font_weight = evil())),
            ("code_font_style", Box::new(|s| s.code_font_style = evil())),
            ("code_font_decoration", Box::new(|s| s.code_font_decoration = evil())),
            ("code_font_case", Box::new(|s| s.code_font_case = evil())),
            ("blockquote_font_weight", Box::new(|s| s.blockquote_font_weight = evil())),
            ("blockquote_font_style", Box::new(|s| s.blockquote_font_style = evil())),
            ("blockquote_font_decoration", Box::new(|s| s.blockquote_font_decoration = evil())),
            ("blockquote_font_case", Box::new(|s| s.blockquote_font_case = evil())),
            ("heading_font_weight", Box::new(|s| s.heading_font_weight = evil())),
            ("code_theme", Box::new(|s| s.code_theme = evil())),
            ("table_border_dasharray", Box::new(|s| s.table_border_dasharray = evil())),
            ("table_border_color", Box::new(|s| s.table_border_color = evil())),
            ("table_header_background", Box::new(|s| s.table_header_background = evil())),
            ("hr_color", Box::new(|s| s.hr_color = evil())),
            ("hr_dasharray", Box::new(|s| s.hr_dasharray = evil())),
            ("image_preserve_aspect_ratio", Box::new(|s| s.image_preserve_aspect_ratio = evil())),
        ];
        for (field, set) in &cases {
            let mut style = Style::default();
            set(&mut style);
            assert_eq!(
                style.validate_css(),
                Err(format!("Style.{field} is not valid CSS: 'red;}}'")),
                "{field}"
            );
        }
    }

    #[test]
    fn font_family_fields_are_never_refused() {
        let hostile = "x;} svg{background:url(https://evil)} .a{".to_string();
        let style = Style {
            font_family: hostile.clone(),
            mono_font_family: hostile.clone(),
            code_font_family: hostile.clone(),
            blockquote_font_family: hostile.clone(),
            heading_font_family: hostile,
            ..Style::default()
        };
        assert_eq!(style.validate_css(), Ok(()));
    }

    #[test]
    fn ordinary_color_values_pass() {
        for color in ["oklch(0.7 0.1 250)", "var(--bg)", "#14532d", "", "rgba(0,0,0,0.07)"] {
            let style = Style {
                text_color: color.to_string(),
                ..Style::default()
            };
            assert_eq!(style.validate_css(), Ok(()), "{color:?}");
        }
    }

    #[test]
    fn the_first_breaking_field_in_declaration_order_names_the_error() {
        let style = Style {
            hr_color: "a;".to_string(),
            text_color: "b;".to_string(),
            ..Style::default()
        };
        assert_eq!(
            style.validate_css(),
            Err("Style.text_color is not valid CSS: 'b;'".to_string())
        );
    }
}
