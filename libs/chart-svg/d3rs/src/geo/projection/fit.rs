//! d3-geo 3.1.1 `src/projection/fit.js`: `fitExtent`, `fitSize`, `fitWidth` and `fitHeight`, which choose the
//! scale and translate that put a GeoJSON object's projected bounds in a box. d3 hangs them on the projection
//! (`projection.fitSize`) and `identity` shares them, so they are the provided methods of [`Fit`].

use super::Projection;
use crate::geo::path::bounds::Bounds;
use crate::geo::stream::GeoStream;
use crate::geojson::GeoJson;
use crate::js_math::js_min;

/// What `fit.js` reads and writes of a projection: its clip extent, scale and translate, and its stream.
pub trait Fit: Sized {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]>;
    fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self;
    fn set_scale(&mut self, k: f64) -> &mut Self;
    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self;
    /// `geoStream(object, projection.stream(sink))`, failing with the error d3 would throw instead of
    /// returning the truncated output as though it were complete.
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String>;

    /// d3's `fit`: project `object` at scale 150 with no clip extent, then let `fit_bounds` set the scale and
    /// translate from the bounds.
    ///
    /// - A pipeline error (see [`Fit::stream_object`]) leaves the projection as d3 is left by the throw:
    ///   scale 150, translate [0, 0], clip extent cleared.
    /// - Bounds that are not finite (nothing projected) are an `Err` where d3 sets a NaN scale and goes on.
    ///   The clip extent is restored first, as d3 restores it; scale and translate stay 150 and [0, 0].
    /// - A single point has finite bounds of zero size, so the scale is infinite and the result is `Ok`, as in d3.
    fn fit(
        &mut self,
        object: &GeoJson,
        fit_bounds: impl FnOnce(&mut Self, [[f64; 2]; 2]),
    ) -> Result<&mut Self, String> {
        let clip = self.clip_extent();
        self.set_scale(150.0).set_translate([0.0, 0.0]);
        if clip.is_some() {
            self.set_clip_extent(None);
        }
        let mut bounds = Bounds::default();
        self.stream_object(object, &mut bounds)?;
        let extent = bounds.result();
        let finite = extent.iter().flatten().all(|v| v.is_finite());
        if finite {
            fit_bounds(self, extent);
        }
        if let Some(clip) = clip {
            self.set_clip_extent(Some(clip));
        }
        if finite {
            Ok(self)
        } else {
            Err(format!("fit: the projected extent is not finite: {extent:?}"))
        }
    }

    fn fit_extent(&mut self, extent: [[f64; 2]; 2], object: &GeoJson) -> Result<&mut Self, String> {
        self.fit(object, |p, b| fit_extent_to(p, extent, b))
    }

    /// The bounds `fit` measures: `object` projected at scale 150 and translate [0, 0] with no clip extent.
    /// They depend on the projection's configuration and `object`, not on the box fitted to, so a caller
    /// fitting one configuration to several boxes measures once and applies each with [`Fit::fit_extent_to`].
    /// Errors and leaves the projection exactly as [`Fit::fit`] does.
    fn fit_bounds(&mut self, object: &GeoJson) -> Result<[[f64; 2]; 2], String> {
        let mut measured = None;
        self.fit(object, |_, b| measured = Some(b))?;
        Ok(measured.expect("fit succeeded, so its bounds were finite"))
    }

    /// [`Fit::fit_extent`] with the bounds already measured by [`Fit::fit_bounds`] on this configuration.
    fn fit_extent_to(&mut self, extent: [[f64; 2]; 2], bounds: [[f64; 2]; 2]) -> &mut Self {
        fit_extent_to(self, extent, bounds);
        self
    }

    fn fit_size(&mut self, size: [f64; 2], object: &GeoJson) -> Result<&mut Self, String> {
        self.fit_extent([[0.0, 0.0], size], object)
    }

    fn fit_width(&mut self, width: f64, object: &GeoJson) -> Result<&mut Self, String> {
        self.fit(object, |p, b| {
            let k = width / (b[1][0] - b[0][0]);
            let x = (width - k * (b[1][0] + b[0][0])) / 2.0;
            let y = -k * b[0][1];
            p.set_scale(150.0 * k).set_translate([x, y]);
        })
    }

    fn fit_height(&mut self, height: f64, object: &GeoJson) -> Result<&mut Self, String> {
        self.fit(object, |p, b| {
            let k = height / (b[1][1] - b[0][1]);
            let x = -k * b[0][0];
            let y = (height - k * (b[1][1] + b[0][1])) / 2.0;
            p.set_scale(150.0 * k).set_translate([x, y]);
        })
    }
}

fn fit_extent_to(p: &mut impl Fit, extent: [[f64; 2]; 2], b: [[f64; 2]; 2]) {
    let w = extent[1][0] - extent[0][0];
    let h = extent[1][1] - extent[0][1];
    let k = js_min(w / (b[1][0] - b[0][0]), h / (b[1][1] - b[0][1]));
    let x = extent[0][0] + (w - k * (b[1][0] + b[0][0])) / 2.0;
    let y = extent[0][1] + (h - k * (b[1][1] + b[0][1])) / 2.0;
    p.set_scale(150.0 * k).set_translate([x, y]);
}

impl Fit for Projection {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        Projection::clip_extent(self)
    }

    fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        Projection::set_clip_extent(self, extent)
    }

    fn set_scale(&mut self, k: f64) -> &mut Self {
        Projection::set_scale(self, k)
    }

    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        Projection::set_translate(self, translate)
    }

    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        Projection::stream_object(self, object, sink)
    }
}
