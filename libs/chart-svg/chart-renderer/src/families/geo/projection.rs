//! The projection a map chart draws through, configured and fitted the way
//! Vega-Lite compiles a `projection` block (`compile/projection/parse.ts`,
//! `assemble.ts`): the authored params go to Vega's `Projection` transform
//! (`apply_vega_params`); a projection with no `scale` or `translate` is fitted
//! to the layer data at `size = [width, height]` of the view, otherwise its
//! `translate` defaults to the view's center.

use d3rs::geo::path::index::GeoPath;
use d3rs::geo::projection::fit::Fit;
use super::vega::{by_vega_name, FitObjects, VegaParams, VegaProjection};
use d3rs::geojson::GeoJson;
use serde_json::{Map, Value};
use std::cell::Cell;

/// A projection ready to draw: the configured, fitted projection and the params
/// the path reads (`pointRadius`).
pub struct Fitted {
    projection: VegaProjection,
    params: VegaParams,
}

impl Fitted {
    /// `[longitude, latitude]` to view pixels, for marks placed at a point; `None` where `albersUsa` has no
    /// inset for it.
    pub fn forward(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        self.projection.forward(point)
    }

    /// `geoPath(projection)`, with Vega's `pointRadius` when the params set one.
    pub fn into_path(self) -> GeoPath<VegaProjection> {
        self.projection.into_path(&self.params)
    }
}

/// A projection type with its authored params over the layers a view draws, fitted to any number of views.
///
/// A fit measures the layer data once at a fixed scale and translate, whatever the view (`Fit::fit_bounds`), so the
/// fitter measures on its first fit and reuses the bounds for every later view: the layout fits at each padding
/// pass and again for the paths, and each of those would otherwise project every feature afresh.
pub struct Fitter<'a> {
    name: &'a str,
    params: Option<&'a Map<String, Value>>,
    layer_data: Vec<GeoJson>,
    bounds: Cell<Option<[[f64; 2]; 2]>>,
    drawn: Cell<Option<((f64, f64), [[f64; 2]; 2])>>,
}

impl<'a> Fitter<'a> {
    /// `name` is a Vega projection type; `layer_data` is what the layers draw, in layer order.
    pub fn new(name: &'a str, params: Option<&'a Map<String, Value>>, layer_data: Vec<GeoJson>) -> Self {
        Fitter { name, params, layer_data, bounds: Cell::new(None), drawn: Cell::new(None) }
    }

    /// Layer `i` as the fitter holds it.
    pub fn layer(&self, i: usize) -> &GeoJson {
        &self.layer_data[i]
    }

    /// The projected bounds of layer 0 drawn over `view`: the ink the layout fits the plot around.
    /// The last view's answer is kept, since the padding passes settle on one size and ask it again.
    pub fn layer_bounds(&self, view: (f64, f64)) -> Result<[[f64; 2]; 2], String> {
        if let Some((at, bounds)) = self.drawn.get() {
            if at == view {
                return Ok(bounds);
            }
        }
        let bounds = self.at(view)?.into_path().bounds(self.layer(0))?;
        self.drawn.set(Some((view, bounds)));
        Ok(bounds)
    }

    /// The projection over a view of `view` = `(width, height)`.
    ///
    /// A name the registry lacks is refused, never replaced by another projection;
    /// a param Vega does not read is an error naming it.
    pub fn at(&self, view: (f64, f64)) -> Result<Fitted, String> {
        let name = self.name;
        let mut projection = by_vega_name(&name.to_lowercase())
            .map_err(|_| crate::refuse!("projection type {name:?} is not supported"))?;
        let authored = self.params.cloned().unwrap_or_default();
        let mut params: VegaParams = serde_json::from_value(Value::Object(authored))
            .map_err(|e| format!("projection {name:?} params: {e}"))?;
        let (width, height) = view;
        let in_context = |e: String| format!("projection {name:?}: {e}");
        if params.scale.is_none() && params.translate.is_none() {
            if self.layer_data.is_empty() {
                return Err(format!("projection {name:?} has no layer data to fit"));
            }
            // The fit is applied below from the measured bounds, not by Vega's `fit` and `size` params.
            params.fit = None;
            params.size = None;
            projection.apply_vega_params(&params).map_err(in_context)?;
            let bounds = match self.bounds.get() {
                Some(bounds) => bounds,
                None => {
                    let objects = FitObjects::Many(self.layer_data.iter().cloned().map(Some).collect());
                    let bounds = projection.fit_objects_bounds(&objects).map_err(in_context)?;
                    self.bounds.set(Some(bounds));
                    bounds
                }
            };
            let extent = params.extent.unwrap_or([[0.0, 0.0], [width, height]]);
            projection.fit_extent_to(extent, bounds);
        } else {
            if params.translate.is_none() {
                params.translate = Some([width / 2.0, height / 2.0]);
            }
            projection.apply_vega_params(&params).map_err(in_context)?;
        }
        Ok(Fitted { projection, params })
    }
}

/// Whether a projection with these authored params fits the layer data (and so
/// draws unclipped): they set neither `scale` nor `translate`.
pub fn is_fit(params: Option<&Map<String, Value>>) -> bool {
    params.is_none_or(|p| !p.contains_key("scale") && !p.contains_key("translate"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use d3rs::geojson::{Feature, Geometry};
    use serde_json::json;

    fn square(x0: f64, y0: f64, x1: f64, y1: f64) -> GeoJson {
        GeoJson::Feature(Feature {
            id: None,
            bbox: None,
            properties: Value::Null,
            geometry: Some(Geometry::Polygon {
                coordinates: vec![vec![vec![x0, y0], vec![x0, y1], vec![x1, y1], vec![x1, y0], vec![x0, y0]]],
            }),
        })
    }

    /// The path of the point at longitude 0, latitude 0.
    fn origin_path(path: &GeoPath<VegaProjection>) -> String {
        let point = GeoJson::Geometry(Geometry::Point { coordinates: vec![0.0, 0.0] });
        path.path(&point).unwrap().unwrap()
    }

    fn params(value: Value) -> Map<String, Value> {
        value.as_object().unwrap().clone()
    }

    #[test]
    fn a_projection_without_scale_fits_the_data_to_the_view() {
        let data = vec![square(-10.0, -10.0, 10.0, 10.0)];
        let path = Fitter::new("equirectangular", None, data.clone()).at((200.0, 100.0)).unwrap().into_path();
        let [[x0, y0], [x1, y1]] = path.bounds(&data[0]).unwrap();
        // The square is as tall as the view and centered across it; its edges are great-circle
        // arcs, which bow about a pixel past the corners the fit measured.
        assert!(y0.abs() < 1.0 && (y1 - 100.0).abs() < 1.0, "{y0} {y1}");
        assert!((x0 + x1 - 200.0).abs() < 1e-6, "{x0} {x1}");
    }

    #[test]
    fn albers_usa_places_no_point_outside_its_insets() {
        let fitted = Fitter::new("albersUsa", Some(&params(json!({"scale": 1000}))), vec![]).at((200.0, 100.0)).unwrap();
        assert!(fitted.forward([-100.0, 40.0]).is_some());
        assert_eq!(fitted.forward([0.0, 0.0]), None);
    }

    #[test]
    fn a_scale_turns_the_fit_off_and_centers_the_projection_on_the_view() {
        let path = Fitter::new("mercator", Some(&params(json!({"scale": 100}))), vec![]).at((200.0, 100.0)).unwrap().into_path();
        assert!(origin_path(&path).starts_with("M100,50"));
    }

    #[test]
    fn an_authored_translate_is_kept() {
        let p = params(json!({"scale": 100, "translate": [10, 20]}));
        let path = Fitter::new("mercator", Some(&p), vec![]).at((200.0, 100.0)).unwrap().into_path();
        assert!(origin_path(&path).starts_with("M10,20"));
    }

    #[test]
    fn a_center_alone_still_fits() {
        let p = params(json!({"center": [5, 5]}));
        assert!(is_fit(Some(&p)) && is_fit(None));
        assert!(!is_fit(Some(&params(json!({"scale": 1})))));
        assert!(!is_fit(Some(&params(json!({"translate": [0, 0]})))));
        assert!(Fitter::new("equirectangular", Some(&p), vec![square(0.0, 0.0, 4.0, 4.0)]).at((100.0, 100.0)).is_ok());
    }

    #[test]
    fn a_projection_the_registry_lacks_is_refused_by_name() {
        let err = Fitter::new("nowhere", None, vec![square(0.0, 0.0, 1.0, 1.0)]).at((10.0, 10.0)).err().unwrap();
        assert!(err.contains("\"nowhere\"") && err.ends_with("not supported"), "{err}");
    }

    #[test]
    fn a_type_name_is_matched_the_way_vega_lowercases_it() {
        assert!(Fitter::new("EqualEarth", None, vec![square(0.0, 0.0, 1.0, 1.0)]).at((10.0, 10.0)).is_ok());
    }

    #[test]
    fn a_param_vega_does_not_read_is_an_error_naming_it() {
        let p = params(json!({"scal": 5}));
        let err = Fitter::new("mercator", Some(&p), vec![]).at((10.0, 10.0)).err().unwrap();
        assert!(err.contains("scal"), "{err}");
    }

    #[test]
    fn a_fit_with_no_layer_data_is_an_error() {
        let err = Fitter::new("mercator", None, vec![]).at((10.0, 10.0)).err().unwrap();
        assert!(err.contains("no layer data"), "{err}");
    }

    #[test]
    fn point_radius_reaches_the_path() {
        let p = params(json!({"scale": 100, "pointRadius": 2}));
        let path = Fitter::new("mercator", Some(&p), vec![]).at((10.0, 10.0)).unwrap().into_path();
        let point = GeoJson::Geometry(Geometry::Point { coordinates: vec![0.0, 0.0] });
        assert!(path.path(&point).unwrap().unwrap().contains("a2,2"));
    }

    #[test]
    fn a_fitter_measures_once_and_fits_every_view_as_a_fresh_fit_would() {
        let data = vec![square(-10.0, -10.0, 10.0, 10.0), square(30.0, 5.0, 40.0, 25.0)];
        let fitter = Fitter::new("equirectangular", None, data.clone());
        for view in [(200.0, 100.0), (50.0, 300.0), (200.0, 100.0)] {
            let reused = fitter.at(view).unwrap().into_path();
            let fresh = Fitter::new("equirectangular", None, data.clone()).at(view).unwrap().into_path();
            for layer in &data {
                assert_eq!(reused.bounds(layer).unwrap(), fresh.bounds(layer).unwrap(), "{view:?}");
                assert_eq!(reused.path(layer).unwrap(), fresh.path(layer).unwrap(), "{view:?}");
            }
        }
    }

    #[test]
    fn layer_bounds_follow_the_view_asked_even_when_the_last_one_is_kept() {
        let data = vec![square(-10.0, -10.0, 10.0, 10.0)];
        let fitter = Fitter::new("equirectangular", None, data.clone());
        for view in [(200.0, 100.0), (200.0, 100.0), (50.0, 300.0), (200.0, 100.0)] {
            let fresh = Fitter::new("equirectangular", None, data.clone()).at(view).unwrap().into_path();
            assert_eq!(fitter.layer_bounds(view).unwrap(), fresh.bounds(&data[0]).unwrap(), "{view:?}");
        }
    }
}
