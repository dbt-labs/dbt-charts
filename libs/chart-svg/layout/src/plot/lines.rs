//! A line layer's marks in plot-local coordinates, as production paints a single series of one
//! line mark on a time x (`line/layout.rs`: the halo, the main line, the point companions and the
//! hover targets): the canvas-colored halo line and halo symbols, the series line, a point at each
//! defined datum, and an invisible hover target at each.
//!
//! The stroke and the point size are no style leaf: production derives them from how many pixels of
//! the chart each distinct x gets (`adaptive_stroke.py`, [`Metrics`]), so they wait for the
//! cell here. Bind hands one series on a time x with a linear curve, each datum's hover text, and
//! the halo multiplier; anything else reaching the painter is an engine bug. A null y breaks the
//! path into runs, and a point is drawn only for a defined datum.
//!
//! Production paints the value axis between the halo and the series line, so the painter says where
//! it goes ([`Painted::y_axis_at`]).

use bind::bound::{self, Datum, Paint, Pos, ScaleKind};
use d3rs::shape::{line_path, Curve};
use d3rs::vega_symbol::VegaSymbol;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role};

use crate::contract::{aria_attrs, connected_group, hidden_mark_group, mark_group, ItemRole};
use crate::geom::Size;
use crate::plot::cartesian::Painted;
use crate::plot::points::linear;
use crate::plot::time_axis::{day_extent, x_on_range};
use crate::plot::value_axis::fraction;
use crate::scene_path::scene_path;

/// The clamp of the stroke, a point's diameter as a multiple of it, and the pixels per distinct x a
/// point needs to show: the one copy engine-side of chart-renderer's generated
/// `constants/marks.rs` (`STROKE_MIN_WIDTH`, `STROKE_MAX_WIDTH`, `POINT_DIAMETER_RATIO`,
/// `POINT_MIN_PX_PER_POINT`), which the engine may not import.
const STROKE_MIN_WIDTH: f64 = 1.5;
const STROKE_MAX_WIDTH: f64 = 4.0;
const POINT_DIAMETER_RATIO: f64 = 1.7;
const POINT_MIN_PX_PER_POINT: f64 = 22.5;
/// The ring width of a halo symbol (`line.rs`: `HALO_POINT_STROKE_W`).
const HALO_POINT_STROKE_W: f64 = 2.0;
/// The area of a hover target's symbol (`common.rs`: `HOVER_TARGET_SIZE`).
const HOVER_TARGET_SIZE: f64 = 320.0;
/// The gap a continuous x range keeps between its end marks and the plot edge (`common.rs`: `x_edge_pad`).
const X_EDGE_GAP_PX: f64 = 2.0;

/// What the cell's width and the data's density make of a line chart's marks.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Metrics {
    /// The line's stroke width (`adaptive_stroke`).
    pub stroke: f64,
    /// The area of each point; `None` once the points would bead into a caterpillar.
    pub point: Option<f64>,
    /// The pixels the x range keeps off each plot edge: a point's radius and two, so the gap holds
    /// whether or not the points draw (`x_edge_pad`).
    pub pad: f64,
}

impl Metrics {
    /// The marks of a chart `width` px wide with `distinct_x` distinct x values in its densest
    /// series: `stroke = snap_half(clamp(1 + ln(px per point)))`, points of area
    /// `(1.7 stroke)^2` from 22.5 px per point.
    pub fn of(width: f64, distinct_x: usize) -> Result<Self, Diagnostic> {
        let px_per_point = width / distinct_x as f64;
        if !(px_per_point > 0.0 && px_per_point.is_finite()) {
            return Err(diag::err_internal(format!("a line over {distinct_x} x values in a {width} px chart has no density")));
        }
        let stroke = ((1.0 + px_per_point.ln()).clamp(STROKE_MIN_WIDTH, STROKE_MAX_WIDTH) * 2.0).round() / 2.0;
        let size = (POINT_DIAMETER_RATIO * stroke).powi(2);
        Ok(Self {
            stroke,
            point: (px_per_point >= POINT_MIN_PX_PER_POINT).then_some(size),
            pad: VegaSymbol::radius(size) + X_EDGE_GAP_PX,
        })
    }

    /// The metrics of the one line layer of `plot` in a chart `width` px wide.
    pub fn of_plot(plot: &bound::Plane, width: f64) -> Result<Self, Diagnostic> {
        let [layer] = plot.layers() else {
            return Err(diag::err_internal("line metrics of a plot that is not one layer"));
        };
        let [datums] = layer.panels.as_slice() else {
            return Err(diag::err_internal("line metrics of a plot in panels"));
        };
        Self::of(width, datums.len())
    }
}

/// The datums of the plot's one line layer, drawn on a plot of `size` under `metrics`, over a
/// chart painted on `canvas`. [`bound::Plane::new`] holds that every datum names a series and sits
/// where the x scale places it; the plot is one series on a time x.
pub fn lines(plot: &bound::Plane, metrics: Metrics, canvas: &str, size: Size) -> Result<Painted, Diagnostic> {
    let [layer] = plot.layers() else {
        return Err(diag::err_internal("a line painter over a plot that is not one layer"));
    };
    let ([datums], [drawn]) = (layer.panels.as_slice(), plot.series()) else {
        return Err(diag::err_internal("a line painter over a plot in panels or of several series"));
    };
    let Some(style) = &plot.mark_style().lines else {
        return Err(diag::err_internal("a line layer on a plot with no line mark"));
    };
    let ScaleKind::Time(domain) = &plot.x().kind else {
        return Err(diag::err_internal("lines on a non-time x scale"));
    };
    let y = &linear(&plot.y()[layer.y_scale], "y")?.domain;
    let Paint::Solid(ink) = drawn.paint;
    let stroke_color = dbt_charts_color::rgb01_to_hex(ink.0, ink.1, ink.2);
    let extent = day_extent(domain, false);
    let at = |datum: &Datum| -> Result<(f64, f64), Diagnostic> {
        let Pos::Time(instant) = &datum.x else {
            return Err(diag::err_internal(format!("a line at x {:?}", datum.x)));
        };
        Ok((x_on_range(extent, metrics.pad, instant.pos, size.width), (1.0 - fraction(y, datum.y1)) * size.height))
    };
    let mut runs: Vec<Vec<(f64, f64)>> = Vec::new();
    let mut points: Vec<(usize, &Datum, (f64, f64))> = Vec::new();
    let mut open = false;
    for (index, datum) in datums.iter().enumerate() {
        if !datum.defined {
            open = false;
            continue;
        }
        let place = at(datum)?;
        if !open {
            runs.push(Vec::new());
            open = true;
        }
        runs.last_mut().into_iter().for_each(|run| run.push(place));
        points.push((index, datum, place));
    }
    let d = scene_path(&line_path(&runs, Curve::Linear));
    let line = |color: &str, width: f64| {
        Attrs::new().set("fill", "none").set("stroke", color).num("stroke-width", width).set("stroke-linecap", "round").set("stroke-linejoin", "round")
    };
    let mark = Role::Mark { layer: 0, series: None, datum: None };

    let mut marks = Vec::new();
    if style.halo_multiplier != 0.0 {
        marks.push(connected_group("line", "layer_1_marks", vec![Node::path(Role::Chrome, d.clone(), line(canvas, metrics.stroke * style.halo_multiplier))]));
        if let Some(point) = metrics.point {
            let ring = Attrs::new().set("fill", canvas).set("stroke", canvas).num("stroke-width", HALO_POINT_STROKE_W);
            let halos = points
                .iter()
                .map(|(_, _, (x, y))| Node::circle(Role::Chrome, (0.0, 0.0), VegaSymbol::radius(point * style.halo_multiplier), ring.clone()).translate(*x, *y))
                .collect();
            marks.push(hidden_mark_group("symbol", "layer_1_marks", halos));
        }
    }
    let y_axis_at = marks.len();
    marks.push(connected_group("line", "layer_3_marks", vec![Node::path(mark, d, line(&stroke_color, metrics.stroke))]));

    let described = |datum: &Datum, attrs: Attrs| -> Result<Attrs, Diagnostic> {
        Ok(attrs.extend(aria_attrs(Some(&datum.tooltip.text()), ItemRole::Point).map_err(diag::err_internal)?))
    };
    if let Some(point) = metrics.point {
        let circles = points
            .iter()
            .map(|(index, datum, (x, y))| {
                let attrs = described(datum, Attrs::new().set("fill", stroke_color.as_str()).num("opacity", 1.0))?;
                let role = Role::Mark { layer: 0, series: None, datum: Some(*index) };
                Ok(Node::circle(role, (0.0, 0.0), VegaSymbol::radius(point), attrs).translate(*x, *y))
            })
            .collect::<Result<Vec<_>, Diagnostic>>()?;
        marks.push(mark_group("symbol", "layer_3_marks", circles).map_err(diag::err_internal)?);
    }
    // A drawn point already announces its datum; with none, the hover target does.
    let announce = metrics.point.is_none();
    let targets = points
        .iter()
        .map(|(_, datum, (x, y))| {
            let base = Attrs::new().set("fill", stroke_color.as_str()).set("opacity", "0");
            let attrs = if announce { described(datum, base)? } else { base };
            Ok(Node::circle(Role::Chrome, (0.0, 0.0), VegaSymbol::radius(HOVER_TARGET_SIZE), attrs).translate(*x, *y))
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    marks.push(if announce { mark_group("symbol", "layer_3_marks", targets).map_err(diag::err_internal)? } else { hidden_mark_group("symbol", "layer_3_marks", targets) });
    Ok(Painted { marks, y_axis_at: Some(y_axis_at) })
}
