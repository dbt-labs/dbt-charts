//! The projection a map chart draws through, configured and fitted the way
//! Vega-Lite compiles a `projection` block (`compile/projection/parse.ts`,
//! `assemble.ts`): the authored params go to Vega's `Projection` transform
//! (`apply_vega_params`); a projection with no `scale` or `translate` is fitted
//! to the layer data at `size = [width, height]` of the view, otherwise its
//! `translate` defaults to the view's center.

use d3rs::geo::path::index::GeoPath;
use d3rs::geo::projection::vega::{by_vega_name, FitObjects, VegaParams, VegaProjection};
use d3rs::geojson::GeoJson;
use serde_json::{Map, Value};

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

/// The projection `name` (a Vega projection type) with the authored `params`, over a
/// view of `view` = `(width, height)` whose layers draw `layer_data`.
///
/// A name the registry lacks is refused, never replaced by another projection;
/// a param Vega does not read is an error naming it.
pub fn fitted(
    name: &str,
    params: Option<&Map<String, Value>>,
    layer_data: Vec<GeoJson>,
    view: (f64, f64),
) -> Result<Fitted, String> {
    let mut projection = by_vega_name(&name.to_lowercase())
        .map_err(|_| crate::refuse!("projection type {name:?} is not supported"))?;
    let authored = params.cloned().unwrap_or_default();
    let mut params: VegaParams = serde_json::from_value(Value::Object(authored))
        .map_err(|e| format!("projection {name:?} params: {e}"))?;
    let (width, height) = view;
    if params.scale.is_none() && params.translate.is_none() {
        if layer_data.is_empty() {
            return Err(format!("projection {name:?} has no layer data to fit"));
        }
        params.size = Some([width, height]);
        params.fit = Some(FitObjects::Many(layer_data.into_iter().map(Some).collect()));
    } else if params.translate.is_none() {
        params.translate = Some([width / 2.0, height / 2.0]);
    }
    projection
        .apply_vega_params(&params)
        .map_err(|e| format!("projection {name:?}: {e}"))?;
    Ok(Fitted { projection, params })
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
        let path = fitted("equirectangular", None, data.clone(), (200.0, 100.0)).unwrap().into_path();
        let [[x0, y0], [x1, y1]] = path.bounds(&data[0]).unwrap();
        // The square is as tall as the view and centered across it; its edges are great-circle
        // arcs, which bow about a pixel past the corners the fit measured.
        assert!(y0.abs() < 1.0 && (y1 - 100.0).abs() < 1.0, "{y0} {y1}");
        assert!((x0 + x1 - 200.0).abs() < 1e-6, "{x0} {x1}");
    }

    #[test]
    fn albers_usa_places_no_point_outside_its_insets() {
        let fitted = fitted("albersUsa", Some(&params(json!({"scale": 1000}))), vec![], (200.0, 100.0)).unwrap();
        assert!(fitted.forward([-100.0, 40.0]).is_some());
        assert_eq!(fitted.forward([0.0, 0.0]), None);
    }

    #[test]
    fn a_scale_turns_the_fit_off_and_centers_the_projection_on_the_view() {
        let path = fitted("mercator", Some(&params(json!({"scale": 100}))), vec![], (200.0, 100.0)).unwrap().into_path();
        assert!(origin_path(&path).starts_with("M100,50"));
    }

    #[test]
    fn an_authored_translate_is_kept() {
        let p = params(json!({"scale": 100, "translate": [10, 20]}));
        let path = fitted("mercator", Some(&p), vec![], (200.0, 100.0)).unwrap().into_path();
        assert!(origin_path(&path).starts_with("M10,20"));
    }

    #[test]
    fn a_center_alone_still_fits() {
        let p = params(json!({"center": [5, 5]}));
        assert!(is_fit(Some(&p)) && is_fit(None));
        assert!(!is_fit(Some(&params(json!({"scale": 1})))));
        assert!(!is_fit(Some(&params(json!({"translate": [0, 0]})))));
        assert!(fitted("equirectangular", Some(&p), vec![square(0.0, 0.0, 4.0, 4.0)], (100.0, 100.0)).is_ok());
    }

    #[test]
    fn a_projection_the_registry_lacks_is_refused_by_name() {
        let err = fitted("nowhere", None, vec![square(0.0, 0.0, 1.0, 1.0)], (10.0, 10.0)).err().unwrap();
        assert!(err.contains("\"nowhere\"") && err.ends_with("not supported"), "{err}");
    }

    #[test]
    fn a_type_name_is_matched_the_way_vega_lowercases_it() {
        assert!(fitted("EqualEarth", None, vec![square(0.0, 0.0, 1.0, 1.0)], (10.0, 10.0)).is_ok());
    }

    #[test]
    fn a_param_vega_does_not_read_is_an_error_naming_it() {
        let p = params(json!({"scal": 5}));
        let err = fitted("mercator", Some(&p), vec![], (10.0, 10.0)).err().unwrap();
        assert!(err.contains("scal"), "{err}");
    }

    #[test]
    fn a_fit_with_no_layer_data_is_an_error() {
        let err = fitted("mercator", None, vec![], (10.0, 10.0)).err().unwrap();
        assert!(err.contains("no layer data"), "{err}");
    }

    #[test]
    fn point_radius_reaches_the_path() {
        let p = params(json!({"scale": 100, "pointRadius": 2}));
        let path = fitted("mercator", Some(&p), vec![], (10.0, 10.0)).unwrap().into_path();
        let point = GeoJson::Geometry(Geometry::Point { coordinates: vec![0.0, 0.0] });
        assert!(path.path(&point).unwrap().unwrap().contains("a2,2"));
    }
}
