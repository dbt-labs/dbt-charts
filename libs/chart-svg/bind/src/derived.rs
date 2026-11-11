//! The style facts a drawn cartesian chart needs beyond its data: its frame, single-series fill
//! and walked legend, derived from the placement's styles as production's style finalization does
//! (`merge_chart_style`, `_canvases`, `_effective_single_series_fill`, `get_chart_content_height`).
//! What a tier sets that no walk reads yet is refused by name.
//!
//! Derived per placement for a bar, line or scatter:
//! - The canvas is the chart's own background, else an opaque or clear `charts.background`,
//!   over each body's background from the root (`_canvases`, `own_canvas`). A translucent card
//!   fill, which cannot be told from its echo, is refused.
//! - The padding and aspect ratio come from the folded family style, the min and max height
//!   from the chart's own, else the board's `charts.*` (`get_chart_content_height`).
//! - The title and legend are the board's total with the leaves `style/title.rs` and
//!   `style/legend.rs` walk (`walked_title`, `walked_legend`); any other leaf a chart or family
//!   tier sets is refused by name.
//! - The single-series fill and the canvas read the chart's own tier alone (`own`), never the
//!   family's, as production does: `_effective_single_series_fill` takes a static fill, then
//!   the chart's own palette, then the board palette's first stop for a chart with layers, else
//!   the own or board single-series ink at the rhythm slot.
//!
//! `engine`'s `tests/it/derived.rs` replays `derived_production.json`, recorded from
//! production's style finalization by `record_derived_style.py`.

use board_model::board::BoardBody;
use board_model::chart::Chart;
use board_model::primitives::PaletteChoice;
use board_model::style::chart_base::PaintedChartStyleBase;
use board_model::style::legend::{LegendEdge, LegendPlacementEdge, SideLegendPositionStyle, SideLegendStyle};
use board_model::style::style::Style;
use color::Rgba;
use diagnostic::{diag, refuse, Diagnostic};
use board_model::facet::Color;
use model_core::{Engine, Leaf, Patch, Resolved, SetLeaves, Tri};

/// A style as one layer authored it, leaf by leaf.
pub type StylePatch = <Style as Leaf>::Patch;

use crate::families::cartesian::unsupported;
use crate::bound::{Frame, Sizing};
use crate::style::legend::walked_legend;
use crate::style::title::walked_title;
use crate::style::Tiers;

/// One placement of a cartesian chart: the chart, the bodies from the root to the one that places
/// it (the last), its folded family style, its own tier alone (the only one production reads for
/// the fill and the canvas) and its place in the single-series rhythm.
pub struct Placed<'a> {
    pub chart: &'a Chart,
    pub chain: Vec<&'a BoardBody>,
    pub style: &'a Style,
    pub own: &'a StylePatch,
    pub scope: &'a Resolved<Style>,
    pub rhythm_slot: Option<u32>,
    /// The chart's `Chart.id`: the name production writes in its errors, which for an inline
    /// chart is not its position.
    pub public_id: &'a str,
}

#[derive(Debug, Clone)]
pub struct DerivedChart {
    pub frame: Frame,
    pub legend: SideLegendStyle,
}

/// The leaves of a tier's legend and title patch the walks read; any other set leaf is refused.
const LEGEND_WALKED: [&str; 4] = ["symbol_limit", "direction", "visible", "title.visible"];
const TITLE_WALKED: [&str; 2] = ["min_height", "subtitle.overflow"];

fn refuse_unwalked(slot: &str, patch: &impl SetLeaves, walked: &[&str]) -> Result<(), Diagnostic> {
    let mut unwalked = None;
    patch.set_leaves("", &mut |path| {
        if !walked.contains(&path) {
            unwalked.get_or_insert_with(|| path.to_string());
        }
    });
    match unwalked {
        Some(leaf) => Err(unsupported(refuse!("a chart or family that sets `{slot}.{leaf}` is not supported"))),
        None => Ok(()),
    }
}

fn refuse_unwalked_tri(slot: &str, patch: &Tri<impl SetLeaves>, walked: &[&str]) -> Result<(), Diagnostic> {
    match patch {
        Tri::Unset => Ok(()),
        Tri::Null => Err(unsupported(refuse!("a chart that sets `{slot}` to null is not supported"))),
        Tri::Value(patch) => refuse_unwalked(slot, patch, walked),
    }
}

impl Placed<'_> {
    /// The body that places the chart, whose style it draws with.
    pub fn body(&self) -> Result<&BoardBody, Diagnostic> {
        self.chain.last().copied().ok_or_else(|| diag::err_internal("a placement has a placing body"))
    }

    pub fn scope(&self) -> &Style {
        &self.scope.total
    }

    pub fn derive(&self) -> Result<DerivedChart, Diagnostic> {
        let scope = self.scope();
        let (painted, own_painted, height) = match self.chart {
            Chart::Bar(c) => {
                let (o, f) = (&*self.own.charts.bar, &*scope.charts.bar);
                refuse_unwalked_tri("legend", &o.legended.legend, &LEGEND_WALKED)?;
                refuse_unwalked_tri("title", &o.cartesian.titled.title, &TITLE_WALKED)?;
                if let Some(legend) = &f.legended.legend {
                    refuse_unwalked("legend", legend, &LEGEND_WALKED)?;
                }
                if let Some(title) = &f.cartesian.titled.title {
                    refuse_unwalked("title", title, &TITLE_WALKED)?;
                }
                (&self.style.charts.bar.cartesian.painted, &o.cartesian.painted, c.cartesian.height)
            }
            Chart::Line(c) => {
                let (o, f) = (&*self.own.charts.line, &*scope.charts.line);
                refuse_unwalked_tri("legend", &o.legended.legend, &LEGEND_WALKED)?;
                refuse_unwalked_tri("title", &o.cartesian.titled.title, &TITLE_WALKED)?;
                if let Some(legend) = &f.legended.legend {
                    refuse_unwalked("legend", legend, &LEGEND_WALKED)?;
                }
                if let Some(title) = &f.cartesian.titled.title {
                    refuse_unwalked("title", title, &TITLE_WALKED)?;
                }
                (&self.style.charts.line.cartesian.painted, &o.cartesian.painted, c.cartesian.height)
            }
            Chart::Scatter(c) => {
                let (o, f) = (&*self.own.charts.scatter, &*scope.charts.scatter);
                refuse_unwalked_tri("legend", &o.legended.legend, &LEGEND_WALKED)?;
                refuse_unwalked_tri("title", &o.cartesian.titled.title, &TITLE_WALKED)?;
                refuse_unwalked("legend", &f.legended.legend, &LEGEND_WALKED)?;
                if let Some(title) = &f.cartesian.titled.title {
                    refuse_unwalked("title", title, &TITLE_WALKED)?;
                }
                (&self.style.charts.scatter.cartesian.painted, &o.cartesian.painted, c.cartesian.height)
            }
            // `family_slots!` folds a histogram's own style into `charts.bar`.
            Chart::Histogram(c) => {
                let (o, f) = (&*self.own.charts.bar, &*scope.charts.histogram);
                refuse_unwalked_tri("legend", &o.legended.legend, &LEGEND_WALKED)?;
                refuse_unwalked_tri("title", &o.cartesian.titled.title, &TITLE_WALKED)?;
                refuse_unwalked("legend", &f.legended.legend, &LEGEND_WALKED)?;
                if let Some(title) = &f.cartesian.titled.title {
                    refuse_unwalked("title", title, &TITLE_WALKED)?;
                }
                (&self.style.charts.histogram.cartesian.painted, &o.cartesian.painted, c.cartesian.height)
            }
            other => {
                return Err(diag::err_internal(format!(
                    "style is derived for bar, histogram, line and scatter, asked of a {} chart",
                    crate::board::family(other)
                )))
            }
        };
        let tiers = Tiers::of(self.chart, self.own, &scope.charts)?;
        let ended = || diag::err_internal("the total style ends every title and legend walk");
        let title = walked_title(&tiers).ok_or_else(ended)?;
        let legend = SideLegendStyle {
            base: walked_legend(&tiers).ok_or_else(ended)?,
            position: SideLegendPositionStyle {
                leaves: scope.charts.legend.position.leaves.clone(),
                // A side legend has no `endpoints` edge: production leaves it to the engine, as `auto`. Safe
                // only while bind refuses `color:` and a list `y:`: one series has no endpoint rail.
                edge: match &scope.charts.legend.position.edge {
                    Engine::Rule(auto) => Engine::Rule(*auto),
                    Engine::Value(LegendPlacementEdge::Endpoints) => Engine::Rule(board_model::rule::Auto::Auto),
                    Engine::Value(LegendPlacementEdge::Left) => Engine::Value(LegendEdge::Left),
                    Engine::Value(LegendPlacementEdge::Right) => Engine::Value(LegendEdge::Right),
                    Engine::Value(LegendPlacementEdge::Top) => Engine::Value(LegendEdge::Top),
                    Engine::Value(LegendPlacementEdge::Bottom) => Engine::Value(LegendEdge::Bottom),
                },
            },
        };
        let frame = Frame {
            canvas: color::parse_css_color(&self.canvas(&own_painted.base.background, scope)?).map_err(diag::err_internal)?,
            padding: painted.base.padding.clone(),
            title,
            sizing: Sizing {
                height: height.map(|h| h.get()),
                aspect_ratio: painted.aspect_ratio,
                min_height: own_painted.min_height.as_ref().map_or(scope.charts.min_height, |own| *own),
                max_height: own_painted.max_height.as_ref().map_or(scope.charts.max_height, |own| *own),
            },
        };
        Ok(DerivedChart { frame, legend })
    }

    /// The chart's canvas (`_canvases`, `merge_chart_style`'s `own_canvas`): its own background
    /// over the board canvas, else the card fill over it. The board canvas is each body's
    /// background over its parent's. `charts.background` is the board's background echoed unless
    /// authored, and the two differ only for a translucent fill, so it is read where that cannot
    /// matter (opaque or clear) and refused otherwise.
    pub fn canvas(&self, own: &Tri<Color>, scope: &Style) -> Result<String, Diagnostic> {
        let mut layers: Vec<&str> = Vec::new();
        match own {
            Tri::Value(own) => layers.push(own.as_str()),
            Tri::Unset | Tri::Null => {
                let card = scope.charts.background.as_str();
                let alpha = color::parse_css_color(card).map_err(diag::err_internal)?.3;
                if alpha > 0.0 && alpha < 1.0 {
                    return Err(unsupported(refuse!("a translucent `charts.background` is not supported")));
                }
                layers.push(card);
            }
        }
        for body in self.chain.iter().rev() {
            let style = body.style.as_deref().ok_or_else(|| diag::err_internal("a normalized board carries its total style"))?;
            layers.push(style.background.as_str());
        }
        color::ink_canvas(&layers).map_err(|_| unsupported(refuse!("a translucent board background over the theme's is not supported")))
    }

    /// `_effective_single_series_fill`. It reads the chart's own tier alone: its static color,
    /// else its own categorical palette's first stop; then a chart with layers takes the board
    /// categorical palette's first stop, and one without takes its own `single_series_palette`
    /// or the board's at the chart's rhythm slot. A family's or the board's `color` is not
    /// consulted for the first two.
    pub fn single_series_fill(&self) -> Result<Rgba, Diagnostic> {
        let own = match self.chart {
            Chart::Bar(_) | Chart::Histogram(_) => &self.own.charts.bar.cartesian.painted,
            Chart::Line(_) => &self.own.charts.line.cartesian.painted,
            Chart::Scatter(_) => &self.own.charts.scatter.cartesian.painted,
            other => {
                return Err(diag::err_internal(format!(
                    "style is derived for bar, histogram, line and scatter, asked of a {} chart",
                    crate::board::family(other)
                )))
            }
        };
        let has_layers = match self.chart {
            Chart::Bar(c) | Chart::Histogram(c) => !c.layers.is_empty(),
            Chart::Line(c) => !c.layers.is_empty(),
            Chart::Scatter(c) => !c.layers.is_empty(),
            _ => false,
        };
        self.fill(own, self.scope(), has_layers)
    }

    fn fill(&self, own: &Patch<PaintedChartStyleBase>, scope: &Style, has_layers: bool) -> Result<Rgba, Diagnostic> {
        let parse = |c: &str| color::parse_css_color(c).map_err(diag::err_internal);
        if let Tri::Value(fixed) = &own.color.base.r#static {
            return parse(fixed.as_str());
        }
        let board = scope
            .charts
            .color
            .categorical
            .as_ref()
            .ok_or_else(|| diag::err_internal("the total style holds `charts.color.categorical`"))?;
        let colors = |choice: &PaletteChoice| match choice {
            PaletteChoice::Colors(colors) => Ok(colors.iter().map(|c| c.as_str().to_string()).collect::<Vec<_>>()),
            PaletteChoice::Name(name) => Err(diag::err_internal(format!("a palette name {name:?} reaches bind unresolved"))),
        };
        let categorical = &own.color.categorical;
        if let Tri::Value(own) = categorical {
            if let Some(palette) = &own.palette {
                // An empty authored palette names no stop, as in production's `if chart_palette:`.
                if let Some(first) = colors(palette)?.first() {
                    return parse(first);
                }
            }
        }
        // A chart with `layers:` is a multi-series chart in disguise: its base shares the category
        // palette with the layers, so the board's first stop paints it.
        if has_layers {
            let palette = crate::palette_of(scope)?;
            let first = palette.first().ok_or_else(|| diag::err_internal("the board's categorical palette is empty"))?;
            return parse(first);
        }
        let own_ink = match categorical {
            Tri::Value(own) => own.single_series_palette.as_ref(),
            Tri::Unset | Tri::Null => None,
        };
        let ink = colors(&own_ink.unwrap_or(&board.single_series_palette).0)?;
        let slot = self
            .rhythm_slot
            .ok_or_else(|| diag::err_internal("normalize gives every single-ink chart a rhythm slot"))?;
        parse(&ink[slot as usize % ink.len()])
    }
}
