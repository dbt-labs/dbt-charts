//! d3-geo 3.1.1 `src/projection/albersUsa.js`: a composite of three `albers`-family projections (the lower 48,
//! Alaska and Hawaii), each clipped to its own inset rectangle, with a router from a point to its inset.
//!
//! Unlike [`super::Projection`] it has no rotate, center, clip angle, clip extent, parallels or reflection;
//! d3 gives it only `scale`, `translate`, `precision`, `invert`, `stream` and the `fit*` methods, which is
//! what this type has.
//!
//! d3 forwards a point by streaming it through each inset's projection into a sink that keeps the last point
//! it saw, and so does `forward`.
//!
//! `multiplex` sends every event to the three insets' streams in order; their clip regions are mutually
//! exclusive on the sphere, which is what keeps their lines and polygons from interleaving.

use super::conic_equal_area::conic_equal_area;
use super::fit::Fit;
use super::albers::albers;
use super::{Projection, ProjectionStream};
use crate::geo::math::EPSILON;
use crate::geo::stream::{geo_stream, GeoStream};
use crate::geojson::GeoJson;
use std::cell::RefCell;
use std::rc::Rc;

pub struct AlbersUsa {
    lower48: Projection,
    alaska: Projection,
    hawaii: Projection,
}

pub fn albers_usa() -> AlbersUsa {
    let mut alaska = conic_equal_area();
    alaska.set_rotate([154.0, 0.0, 0.0]).set_center([-2.0, 58.5]).set_parallels([55.0, 65.0]); // EPSG:3338
    let mut hawaii = conic_equal_area();
    hawaii.set_rotate([157.0, 0.0, 0.0]).set_center([-3.0, 19.9]).set_parallels([8.0, 18.0]); // ESRI:102007
    let mut p = AlbersUsa { lower48: albers(), alaska, hawaii };
    p.set_scale(1070.0);
    p
}

/// d3's `pointStream`: keeps the last point it saw.
struct LastPoint(Option<[f64; 2]>);

impl GeoStream for LastPoint {
    fn point(&mut self, x: f64, y: f64, _: Option<f64>) {
        self.0 = Some([x, y]);
    }
    fn line_start(&mut self) {}
    fn line_end(&mut self) {}
    fn polygon_start(&mut self) {}
    fn polygon_end(&mut self) {}
    fn sphere(&mut self) {}
}

/// The point as the inset's stream emits it: `None` when its clip rectangle drops it.
fn inset_point(inset: &Projection, point: [f64; 2]) -> Option<[f64; 2]> {
    let mut last = LastPoint(None);
    inset.stream(&mut last).point(point[0], point[1], None);
    last.0
}

impl AlbersUsa {
    /// `None` outside all three insets.
    pub fn forward(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        [&self.lower48, &self.alaska, &self.hawaii].into_iter().find_map(|inset| inset_point(inset, point))
    }

    /// Routes by where the planar point falls relative to the lower 48's scale and translate, then inverts in
    /// that inset; `None` where its raw projection has no inverse.
    pub fn invert(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        let k = self.lower48.scale();
        let t = self.lower48.translate();
        let x = (point[0] - t[0]) / k;
        let y = (point[1] - t[1]) / k;
        let inset = if (0.120..0.234).contains(&y) && (-0.425..-0.214).contains(&x) {
            &self.alaska
        } else if (0.166..0.234).contains(&y) && (-0.214..-0.115).contains(&x) {
            &self.hawaii
        } else {
            &self.lower48
        };
        inset.invert(point)
    }

    pub fn stream<'a, S: GeoStream + 'a>(&'a self, sink: S) -> Multiplex<'a> {
        let sink = Rc::new(RefCell::new(sink));
        Multiplex {
            streams: [&self.lower48, &self.alaska, &self.hawaii].map(|inset| inset.stream(SharedSink(Rc::clone(&sink)))),
        }
    }

    pub fn set_precision(&mut self, precision: f64) -> &mut Self {
        self.lower48.set_precision(precision);
        self.alaska.set_precision(precision);
        self.hawaii.set_precision(precision);
        self
    }

    pub fn precision(&self) -> f64 {
        self.lower48.precision()
    }

    /// Alaska is drawn at 0.35 of the scale; `translate` then repositions all three insets.
    pub fn set_scale(&mut self, k: f64) -> &mut Self {
        self.lower48.set_scale(k);
        self.alaska.set_scale(k * 0.35);
        self.hawaii.set_scale(k);
        self.set_translate(self.lower48.translate())
    }

    pub fn scale(&self) -> f64 {
        self.lower48.scale()
    }

    pub fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        let k = self.lower48.scale();
        let [x, y] = translate;
        self.lower48
            .set_translate(translate)
            .set_clip_extent(Some([[x - 0.455 * k, y - 0.238 * k], [x + 0.455 * k, y + 0.238 * k]]));
        self.alaska.set_translate([x - 0.307 * k, y + 0.201 * k]).set_clip_extent(Some([
            [x - 0.425 * k + EPSILON, y + 0.120 * k + EPSILON],
            [x - 0.214 * k - EPSILON, y + 0.234 * k - EPSILON],
        ]));
        self.hawaii.set_translate([x - 0.205 * k, y + 0.212 * k]).set_clip_extent(Some([
            [x - 0.214 * k + EPSILON, y + 0.166 * k + EPSILON],
            [x - 0.115 * k - EPSILON, y + 0.234 * k - EPSILON],
        ]));
        self
    }

    pub fn translate(&self) -> [f64; 2] {
        self.lower48.translate()
    }
}

/// The one sink the three insets' streams write to.
struct SharedSink<S>(Rc<RefCell<S>>);

impl<S: GeoStream> GeoStream for SharedSink<S> {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        self.0.borrow_mut().point(x, y, z);
    }
    fn line_start(&mut self) {
        self.0.borrow_mut().line_start();
    }
    fn line_end(&mut self) {
        self.0.borrow_mut().line_end();
    }
    fn polygon_start(&mut self) {
        self.0.borrow_mut().polygon_start();
    }
    fn polygon_end(&mut self) {
        self.0.borrow_mut().polygon_end();
    }
    fn sphere(&mut self) {
        self.0.borrow_mut().sphere();
    }
}

/// d3's `multiplex([lower48, alaska, hawaii])`.
pub struct Multiplex<'a> {
    streams: [ProjectionStream<'a>; 3],
}

impl Multiplex<'_> {
    /// Once an inset has failed no later event is sent; the event it failed on still reaches the insets after
    /// it, since d3's throw comes only when the failing inset runs.
    fn each(&mut self, event: impl Fn(&mut ProjectionStream<'_>)) {
        if self.error().is_none() {
            self.streams.iter_mut().for_each(event);
        }
    }
}

impl GeoStream for Multiplex<'_> {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        self.each(|s| s.point(x, y, z));
    }
    fn line_start(&mut self) {
        self.each(|s| s.line_start());
    }
    fn line_end(&mut self) {
        self.each(|s| s.line_end());
    }
    fn polygon_start(&mut self) {
        self.each(|s| s.polygon_start());
    }
    fn polygon_end(&mut self) {
        self.each(|s| s.polygon_end());
    }
    fn sphere(&mut self) {
        self.each(|s| s.sphere());
    }
    /// The error d3 would have thrown from an inset's pre-clip stage; the first inset's wins.
    fn error(&self) -> Option<&str> {
        self.streams.iter().find_map(|s| s.error())
    }
}

/// d3's `fit` reads `projection.clipExtent && projection.clipExtent()`, and albersUsa has no `clipExtent`.
impl Fit for AlbersUsa {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        None
    }

    fn set_clip_extent(&mut self, _: Option<[[f64; 2]; 2]>) -> &mut Self {
        unreachable!("albersUsa has no clipExtent, so fit never restores one")
    }

    fn set_scale(&mut self, k: f64) -> &mut Self {
        AlbersUsa::set_scale(self, k)
    }

    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        AlbersUsa::set_translate(self, translate)
    }

    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        let mut stream = self.stream(sink);
        geo_stream(object, &mut stream);
        GeoStream::error(&stream).map_or(Ok(()), |message| Err(message.to_string()))
    }
}
