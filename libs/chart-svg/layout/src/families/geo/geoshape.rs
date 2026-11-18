//! A geoshape's base map: every region of its source drawn in one neutral fill under the chart's
//! title, in the box the size stage gives the card.
//!
//! The rules are `chart-renderer/src/families/geo/geoshape.rs`'s `layout` (`render`), on the clean
//! fit (quirk class Q2a): the plot is the fixed point of `plot = area - ink(plot)`, where the ink is the
//! fitted projection's bounds grown by the stroke (`boundStroke`), so a map whose stroke or reach
//! overhangs its fit takes the overhang out of its plot. A projection the author placed (`scale` or
//! `translate`) is not fitted, draws at the plot's size, is clipped to it, and takes no gutter.
//!
//! `draw` is the chart's root `<svg class="marks">`: its background, the title block and the regions
//! under [`crate::knockout`]'s mask when the stroke is the board's background.

use bind::bound::{GeoStroke, Geoshape, StrokeInk};
use board_model::chart::projection::ProjectionChoice;
use board_model::style::board::EmojiMode;
use board_model::style::style::Style;
use d3rs::geojson::{FeatureCollection, GeoJson};
use diagnostic::{diag, Diagnostic};
use measure::shaped::FontBook;
use model_core::Spell;
use scene::{Attrs, Extent, Node, PathData, Role, Shape};
use serde_json::{Map, Value};

use super::projection::{is_fit, Fitter};
use crate::geom::{Insets, Rect};
use crate::knockout::{clip, knock_out, safe_scope};
use crate::plot::negotiate::{body, negotiate};
use crate::scene_path::scene_path;
use crate::title::{chart_title, ChartTitle};

/// The chart's geoshape and the placement it draws in.
pub struct GeoshapeCard<'a> {
    pub geo: &'a Geoshape,
    /// The placement's total style, which the chart title slot is read from.
    pub placed: &'a Style,
    pub chart_id: &'a str,
    pub emoji: &'a EmojiMode,
}

/// A solved map: the title block, the plot box in the card and every region's path in it.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    title: Option<ChartTitle>,
    /// The title block's top left, the card's padding corner.
    title_at: (f64, f64),
    plot: Rect,
    /// One per feature, in source order; `None` where the projection draws nothing of it (an `albersUsa`
    /// territory outside its insets).
    paths: Vec<Option<PathData>>,
    /// The projection is fitted to the data, which draws unclipped.
    fit: bool,
}

impl Plan {
    /// What the title's wrap or cut warned of.
    pub fn diagnostics(&self) -> &[Diagnostic] {
        self.title.as_ref().map_or(&[], |title| title.diagnostics.as_slice())
    }
}

/// The Vega projection type and params a chart's `projection:` is: a name, or the block less its `type`.
fn vega_projection(choice: &ProjectionChoice) -> Result<(String, Option<Map<String, Value>>), Diagnostic> {
    match choice {
        ProjectionChoice::Name(name) => Ok((name.as_str().to_string(), None)),
        ProjectionChoice::Block(block) => {
            let Value::Object(mut params) = block.spell() else {
                return Err(diag::err_internal("a projection block spells an object".to_string()));
            };
            let Some(Value::String(name)) = params.remove("type") else {
                return Err(diag::err_internal("a projection block spells its type".to_string()));
            };
            Ok((name, (!params.is_empty()).then_some(params)))
        }
    }
}

/// How far the stroke grows the regions' bounds (`boundStroke`): its width, none for no stroke or a
/// transparent one.
fn stroke_reach(stroke: Option<&GeoStroke>) -> f64 {
    match stroke {
        Some(GeoStroke { ink: StrokeInk::Color(color), .. }) if color == "transparent" => 0.0,
        Some(stroke) => stroke.width,
        None => 0.0,
    }
}

/// The map's plan in a card of `(w, h)` with `pad` = (left, right, top, bottom) around its body.
pub fn plan(card: &GeoshapeCard, book: &FontBook, (w, h): (f64, f64), pad: (f64, f64, f64, f64)) -> Result<Plan, Diagnostic> {
    let geo = card.geo;
    let title = chart_title(card.chart_id, geo.title.as_deref().unwrap_or_default(), geo.subtitle.as_deref(), card.placed, w, w - pad.0 - pad.1, book)?;
    let padding = Insets { top: pad.2, right: pad.1, bottom: pad.3, left: pad.0 };
    let area = body(Rect { x: 0.0, y: 0.0, width: w, height: h }, padding, title.as_ref().map(|t| &t.band), Insets::ZERO);

    let (name, params) = vega_projection(&geo.projection)?;
    let fit = is_fit(params.as_ref());
    let layer = GeoJson::FeatureCollection(FeatureCollection { features: geo.features.features.clone() });
    let fitter = Fitter::new(&name, params.as_ref(), vec![layer]);
    let reach = stroke_reach(geo.stroke.as_ref());
    let fitted = negotiate(area, |plot| {
        if !fit {
            return Ok(Insets::ZERO);
        }
        let [[x0, y0], [x1, y1]] = fitter.layer_bounds((plot.width, plot.height))?;
        // A source with no region has empty bounds, which grow nothing.
        if ![x0, y0, x1, y1].iter().all(|v| v.is_finite()) {
            return Ok(Insets::ZERO);
        }
        Ok(Insets { top: reach - y0, right: x1 + reach - plot.width, bottom: y1 + reach - plot.height, left: reach - x0 })
    })?;
    let path = fitter.at((fitted.plot.width, fitted.plot.height))?.into_path();
    let paths = geo
        .features
        .features
        .iter()
        .map(|feature| Ok(path.path(&GeoJson::Feature(feature.clone()))?.map(|commands| scene_path(&commands))))
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    Ok(Plan { title, title_at: (pad.0, pad.2), plot: fitted.plot, paths, fit })
}

/// A card's root `<svg class="marks">` of `(w, h)`: the background, the title and the regions.
pub fn draw(card: &GeoshapeCard, plan: &Plan, (w, h): (f64, f64)) -> Result<Node, Diagnostic> {
    let geo = card.geo;
    let internal = |e: String| diag::err_internal(e);
    let scope = safe_scope(card.chart_id);
    let mut nodes = vec![Node::rect(Role::Chrome, (0.0, 0.0, w, h), Attrs::new().set("fill", geo.background.as_str()))];
    if let Some(title) = &plan.title {
        nodes.extend(title.nodes(plan.title_at, &card.placed.charts.title, card.emoji));
    }

    let stroke = geo.stroke.as_ref().map(|stroke| {
        let ink = match &stroke.ink {
            StrokeInk::BoardBackground(ground) => ground.as_str(),
            StrokeInk::Color(color) => color.as_str(),
        };
        (ink, stroke.width)
    });
    let regions = plan
        .paths
        .iter()
        .enumerate()
        .filter_map(|(feature, path)| {
            let mut attrs = Attrs::new().set("fill", geo.fill.as_str());
            if let Some((ink, width)) = stroke {
                attrs = attrs.set("stroke", ink).num("stroke-width", width);
            }
            path.as_ref().map(|path| Node::path(Role::Mark { layer: 0, series: None, datum: Some(feature) }, path.clone(), attrs).translate(0.0, 0.0))
        })
        .collect();
    let region = (0.0, 0.0, plan.plot.width, plan.plot.height);
    let knocked = knock_out(regions, &geo.canvas, "ko0", &scope, region);
    let mut attrs = Attrs::new().set("class", "mark-shape role-mark marks");
    let mut marks = knocked.marks;
    let clip_id = format!("geo-clip-0-{scope}");
    if !plan.fit {
        let url = format!("url(#{clip_id})");
        // A masked layer carries the clip on its masked group (`knockout::clip`).
        if marks.iter().any(|node| node.attrs.get("mask").is_some()) {
            marks = clip(marks, &url);
        } else {
            attrs = attrs.set("clip-path", url);
        }
    }
    let container = display_text::language::tr("aria.container_shape", None, &[]).map_err(internal)?;
    let layer = Node::group(Role::Chrome, attrs.set("role", "graphics-symbol").set("aria-roledescription", container), marks);
    nodes.push(Node::group(Role::Chrome, Attrs::new(), vec![layer]).translate(plan.plot.x, plan.plot.y));
    if !plan.fit {
        let area = Node::rect(Role::Chrome, (0.0, 0.0, plan.plot.width, plan.plot.height), Attrs::new());
        let clip_path = Node::new(Role::Chrome, Attrs::new().set("id", clip_id.as_str()), Shape::ClipPath(vec![area]));
        nodes.push(Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![clip_path])));
    }
    Ok(Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent: Extent { width: w, height: h }, children: nodes }))
}
