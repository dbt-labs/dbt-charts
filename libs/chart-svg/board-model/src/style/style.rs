//! `style/theme/style.py` (and `style/theme/__init__.py`, which only re-exports): the theme root.
//!
//! Dissolved, not ported: `_validate_fonts` and the `heading_ramp`, `section_ramp`,
//! `numeral_family` and `secondary_ink` readers. They check and read the merged `fonts` map, so
//! they belong to the pass that has it (named fonts expand after the whole cascade has merged);
//! `ERR-FONT-REGISTRY` is that pass's code. `box_shadow`'s `BeforeValidator` is the
//! [`BoxShadow`](crate::primitives::BoxShadow) facet. The four maps (`formats`, `fonts`,
//! `palettes`, `roles`) are Python's `dict | None`: `None` there is "nothing at this cascade level",
//! which an empty map says (the patch twin keeps unset apart from empty).

use crate::facet::Color;
use crate::names::PaletteName;
use crate::primitives::{BorderStyle, BoxShadow, FormatConfig, SpacingValues};
use crate::style::annotations::AnnotationsStyle;
use crate::style::board::{FrameStyle, PlaceholderStyle, RootFontStyle, TextStyle, TitleStyle};
use crate::style::charts::ChartsStyle;
use crate::style::font::NamedFont;
use crate::style::kpi::KpiTonesStyle;
use crate::style::layout::LayoutStyle;
use crate::style::page::{FooterStyle, TimestampStyle};
use crate::style::variables::VariablesStyle;
use indexmap::IndexMap;
use model_derive::Model;

/// Authoritative compiled style, built from a single theme YAML file.
///
/// Every section is present with the values of `themes/_base.yaml`; this is the "compiled from
/// theme" layer, not yet resolved or cascaded.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Style {
    /// Board-level structural frame dimensions.
    pub frame: FrameStyle,
    /// Working-surface background color (board and card fills).
    pub background: Color,
    /// Accent color token cascaded to focus rings.
    pub accent: Color,
    /// Root font: literal family, size, weight, color; never a `fonts` name.
    pub font: RootFontStyle,
    /// Frame drawn around the whole board: `{width, color, radius}` or `'2px solid #333'`. Omit the key for no border.
    pub border: BorderStyle,
    /// Drop shadow under chart cards: x offset, y offset, blur, color.
    #[model(absent = "no shadow")]
    pub box_shadow: Option<BoxShadow>,
    /// Default mark opacity (0–1).
    pub opacity: f64,
    /// Typography for every heading: board and prose titles, and chart, table, and spark object titles.
    pub title: TitleStyle,
    /// Markdown and plain text content style.
    pub text: TextStyle,
    /// Appearance of the stand-in drawn on a chart with no data.
    pub placeholder: PlaceholderStyle,
    /// Root of all chart-type styles and shared chart configuration.
    pub charts: Box<ChartsStyle>,
    /// Default look of each annotation type: rule, band, note, trend.
    pub annotations: AnnotationsStyle,
    /// Spacing and arrangement inside the containers (rows, cols, grid, tabs, details).
    pub layout: LayoutStyle,
    /// Variable controls chrome style.
    pub variables: VariablesStyle,
    /// Page footer chrome visibility.
    #[model(nested = drop)]
    pub footer: FooterStyle,
    /// Data-freshness chrome: visibility, placement, format, and font.
    #[model(nested = drop)]
    pub timestamp: TimestampStyle,
    /// Format alias map; empty means no aliases at this cascade level.
    pub formats: IndexMap<String, FormatConfig>,
    /// Named fonts as `<name>: {family, size, weight, color}`, never a bare family string; other font slots reference one as `font: <name>` or `font: {extends: <name>, ...}`. `heading` carries the H1–H6 ramp in `sizes` and `section` the H2–H6 section-title sizes. Any other entry no slot references paints nothing.
    #[model(default_empty, merge = deep)]
    pub fonts: IndexMap<String, NamedFont>,
    /// Theme palette role assignments: role name to a built-in palette name, never a color. Custom colors go in `charts.color.categorical.palette: [hex, ...]`. Default seed: chrome, info, negative, positive, warning, category, sequence, diverge.
    #[model(default_empty)]
    pub palettes: IndexMap<String, PaletteName>,
    /// Semantic tone color palette (positive/negative/warning/info) for KPI support rows, table conditional glyphs, and spark negative_color.
    pub tones: KpiTonesStyle,
    /// Optional top-level theme role aliases: bare name → role address (`role[N]` or `role.alias`). e.g. ink: chrome[10]
    #[model(default_empty)]
    pub roles: IndexMap<String, String>,
    /// Per-board padding override (CSS shorthand or structured).
    #[model(nested = drop, absent = "no per-board override")]
    pub padding: Option<SpacingValues>,
    /// Per-board margin override (CSS shorthand or structured).
    #[model(nested = drop, absent = "no per-board override")]
    pub margin: Option<SpacingValues>,
    /// Gap between layout items in pixels.
    #[model(absent = "the layout type's own gap")]
    pub gap: Option<f64>,
}
