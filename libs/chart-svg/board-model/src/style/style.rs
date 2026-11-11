//! `style/theme/style.py` (and `style/theme/__init__.py`, which only re-exports): the theme root.
//!
//! Dissolved, not ported: `_validate_fonts` and the `heading_ramp`, `section_ramp`,
//! `numeral_family` and `secondary_ink` readers. They check and read the merged `fonts` map, so
//! they belong to the pass that has it (named fonts expand after the whole cascade has merged);
//! `ERR-FONT-REGISTRY` is that pass's code. `box_shadow`'s `BeforeValidator` is the
//! [`BoxShadow`](crate::primitives::BoxShadow) facet. The four maps (`formats`, `fonts`,
//! `palettes`, `roles`) are Python's `dict | None`: `None` there is "nothing at this cascade level",
//! which an empty map says (the patch twin keeps unset apart from empty).
//!
//! `Style.charts` and the chart families are `Box`ed so the total builds on a 512 KiB stack
//! (`a_total_style_builds_on_a_512_kib_stack`). The built `Style` stays total: `omit` (a scalar
//! or model, `Option` in the built type, twins are its own) is only for a leaf some mount really
//! leaves unset (the value-label font leaves, which no total builds; a stroke's `color` and
//! `width` are plain `Engine<_, Auto>` leaves, `fill_at(.. = "auto")` on the mounts whose `_base`
//! states nothing: Python's unset, the mark family's own color and width). A map, list or model
//! Python defaults to empty (`default_factory`), or to a `None` its resolved style asserts away
//! or reads as empty, is `default_empty` (the built value is the empty container or the model
//! built from an empty patch, which `Schema::of` checks). `tests/fixtures/omittable.json`
//! (written by `test_board_model_schema_fixture.py`) lists both and the schema parity gate fails
//! on one the spine states and Python does not. A Rust-required leaf Python leaves optional is
//! accepted only with `supplied_at`.

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
use crate::unit::pixel_length;
use indexmap::IndexMap;
use model_core::{build_leaf, Cx, FromNode, Leaf, Merge, Node, ReadScalar, Scalar, Spell, TypeExpr};
use model_derive::Model;
use yaml::{Kind, Value};

/// `style.gap`: pixels, written as a number or, in an authored layer, as a pixel length such as
/// `4px` (Python's `_parse_gap`). A theme file holds the number only.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Gap(f64);

impl Gap {
    pub fn px(self) -> f64 {
        self.0
    }

    fn read_authored(node: &Node, cx: &mut Cx) -> Option<Self> {
        let text = match &node.kind {
            Kind::Scalar(Value::Bool(b)) => {
                let got = if *b { "True" } else { "False" };
                cx.invalid(
                    node.span,
                    format!("Value error, gap must be a number or a pixel length, got {got}"),
                );
                return None;
            }
            Kind::Scalar(Value::Str(text)) => text,
            _ => return f64::read(node, cx).map(Self),
        };
        let Some(px) = pixel_length(text) else {
            let got = diagnostic::py_repr(&serde_json::Value::String(text.clone()));
            cx.invalid(
                node.span,
                format!("Value error, gap must be a number or a pixel length like '12px', got {got}"),
            );
            return None;
        };
        Some(Self(px))
    }
}

impl From<f64> for Gap {
    fn from(px: f64) -> Self {
        Self(px)
    }
}

impl FromNode for Gap {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read_authored(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read_authored(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        f64::read(node, cx).map(Self)
    }
}

impl Spell for Gap {
    fn spell(&self) -> serde_json::Value {
        self.0.spell()
    }
}

impl Leaf for Gap {
    type Whole = Gap;
    type Authored = Option<Gap>;
    type Patch = Option<Gap>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Scalar(Scalar::Float);
}
build_leaf!(Gap);

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
    pub gap: Option<Gap>,
}
