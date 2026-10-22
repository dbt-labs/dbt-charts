//! vega-geo 5.1.3 `src/Projection.js` (the `~5.1.0` range Vega 6.2.0 depends on) with vega-projection 2.1.0's
//! `projectionProperties`: how a Vega `projection` params object is applied to a freshly created projection.
//!
//! Each parameter applies only when present (`!= null`), in this order, and only if the projection has the
//! method (`isFunction(proj[key])`, so `parallels` skips a projection that is not conic):
//!
//! 1. `clipAngle`, `clipExtent`, `scale`, `translate`, `center`, `rotate`, `parallels`, `precision`,
//!    `reflectX`, `reflectY`. (vega-projection then lists d3-geo-projection's `coefficient`, `distance`,
//!    `fraction`, `lobes`, `parallel`, `radius`, `ratio`, `spacing`, `tilt`; none of Vega's 17 projections has
//!    one, so they never apply.)
//! 2. `pointRadius` goes to `proj.path.pointRadius`: the path owns it and it cannot move scale or translate.
//! 3. `fit`: the data is `collectGeoJSON(fit)` (one object as it is, else a FeatureCollection of their
//!    features, a bare geometry wrapped in a Feature), fitted by `fitExtent(extent)` if `extent` is set, else
//!    `fitSize(size)` if `size` is set, else not at all. `fit` runs last, so it overwrites scale and translate.
//!
//! `rotate` takes two angles or more and reads the first three, as Vega does; fewer is an error. A fit whose
//! bounds are not finite (no objects, everything clipped) is an error, where Vega leaves a NaN scale.

use super::azimuthal_equal_area::azimuthal_equal_area;
use super::azimuthal_equidistant::azimuthal_equidistant;
use super::equal_earth::equal_earth;
use super::equirectangular::equirectangular;
use super::fit::Fit;
use super::gnomonic::gnomonic;
use super::identity::{identity, Identity};
use super::mercator::mercator;
use super::mollweide::mollweide;
use super::natural_earth1::natural_earth1;
use super::orthographic::orthographic;
use super::stereographic::stereographic;
use super::transverse_mercator::transverse_mercator;
use super::Projection;
use crate::geo::stream::GeoStream;
use crate::geojson::{Feature, FeatureCollection, GeoJson};
use serde::Deserialize;
use serde_json::Value;

/// The projection parameters Vega's registry knows; any other key is an error.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VegaParams {
    pub clip_angle: Option<f64>,
    pub clip_extent: Option<[[f64; 2]; 2]>,
    pub scale: Option<f64>,
    pub translate: Option<[f64; 2]>,
    pub center: Option<[f64; 2]>,
    pub rotate: Option<Vec<f64>>,
    pub parallels: Option<[f64; 2]>,
    pub precision: Option<f64>,
    pub reflect_x: Option<bool>,
    pub reflect_y: Option<bool>,
    /// Read by the path (`geoPath().pointRadius`), not by the projection.
    pub point_radius: Option<f64>,
    pub fit: Option<Vec<GeoJson>>,
    pub extent: Option<[[f64; 2]; 2]>,
    pub size: Option<[f64; 2]>,
}

/// `collectGeoJSON`.
fn collect_geojson(items: &[GeoJson]) -> GeoJson {
    match items {
        [only] => only.clone(),
        _ => {
            let features = items
                .iter()
                .flat_map(|item| match item {
                    GeoJson::FeatureCollection(collection) => collection.features.clone(),
                    GeoJson::Feature(feature) => vec![feature.clone()],
                    GeoJson::Geometry(geometry) => {
                        vec![Feature { id: None, bbox: None, properties: Value::Null, geometry: Some(geometry.clone()) }]
                    }
                })
                .collect();
            GeoJson::FeatureCollection(FeatureCollection { features })
        }
    }
}

/// What a params object can set. A key the projection has no method for is skipped (`isFunction(proj[key])`),
/// which is each default here: `identity` has only `clipExtent`, `scale`, `translate`, `reflectX`, `reflectY`.
trait VegaTarget: Fit {
    fn vega_clip_angle(&mut self, _degrees: f64) {}
    fn vega_center(&mut self, _center: [f64; 2]) {}
    fn vega_rotate(&mut self, _angles: &[f64]) -> Result<(), String> {
        Ok(())
    }
    fn vega_parallels(&mut self, _parallels: [f64; 2]) {}
    fn vega_precision(&mut self, _precision: f64) {}
    fn vega_reflect_x(&mut self, reflect: bool);
    fn vega_reflect_y(&mut self, reflect: bool);
}

impl VegaTarget for Projection {
    fn vega_clip_angle(&mut self, degrees: f64) {
        self.set_clip_angle(degrees);
    }

    fn vega_center(&mut self, center: [f64; 2]) {
        self.set_center(center);
    }

    fn vega_rotate(&mut self, angles: &[f64]) -> Result<(), String> {
        let [lambda, phi, rest @ ..] = angles else {
            return Err(format!("rotate takes at least two angles, got {}", angles.len()));
        };
        self.set_rotate([*lambda, *phi, rest.first().copied().unwrap_or(0.0)]);
        Ok(())
    }

    fn vega_parallels(&mut self, parallels: [f64; 2]) {
        if self.parallels().is_some() {
            self.set_parallels(parallels);
        }
    }

    fn vega_precision(&mut self, precision: f64) {
        self.set_precision(precision);
    }

    fn vega_reflect_x(&mut self, reflect: bool) {
        self.set_reflect_x(reflect);
    }

    fn vega_reflect_y(&mut self, reflect: bool) {
        self.set_reflect_y(reflect);
    }
}

impl VegaTarget for Identity {
    fn vega_reflect_x(&mut self, reflect: bool) {
        self.set_reflect_x(reflect);
    }

    fn vega_reflect_y(&mut self, reflect: bool) {
        self.set_reflect_y(reflect);
    }
}

pub fn apply_vega_params(projection: &mut Projection, params: &VegaParams) -> Result<(), String> {
    apply(projection, params)
}

fn apply(projection: &mut impl VegaTarget, params: &VegaParams) -> Result<(), String> {
    if let Some(angle) = params.clip_angle {
        projection.vega_clip_angle(angle);
    }
    if let Some(extent) = params.clip_extent {
        projection.set_clip_extent(Some(extent));
    }
    if let Some(scale) = params.scale {
        projection.set_scale(scale);
    }
    if let Some(translate) = params.translate {
        projection.set_translate(translate);
    }
    if let Some(center) = params.center {
        projection.vega_center(center);
    }
    if let Some(rotate) = &params.rotate {
        projection.vega_rotate(rotate)?;
    }
    if let Some(parallels) = params.parallels {
        projection.vega_parallels(parallels);
    }
    if let Some(precision) = params.precision {
        projection.vega_precision(precision);
    }
    if let Some(reflect) = params.reflect_x {
        projection.vega_reflect_x(reflect);
    }
    if let Some(reflect) = params.reflect_y {
        projection.vega_reflect_y(reflect);
    }

    let (Some(fit), true) = (&params.fit, params.extent.is_some() || params.size.is_some()) else {
        return Ok(());
    };
    let data = collect_geojson(fit);
    if let Some(extent) = params.extent {
        projection.fit_extent(extent, &data)?;
    } else if let Some(size) = params.size {
        projection.fit_size(size, &data)?;
    }
    Ok(())
}

/// A projection `vega-projection` registers: a spherical one, or the planar `identity`.
pub enum VegaProjection {
    Curved(Box<Projection>),
    Planar(Identity),
}

impl VegaProjection {
    pub fn forward(&self, point: [f64; 2]) -> [f64; 2] {
        match self {
            Self::Curved(p) => p.forward(point),
            Self::Planar(p) => p.forward(point),
        }
    }

    /// `None` where a spherical projection has no inverse there; `identity` always has one.
    pub fn invert(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        match self {
            Self::Curved(p) => p.invert(point),
            Self::Planar(p) => Some(p.invert(point)),
        }
    }

    pub fn scale(&self) -> f64 {
        match self {
            Self::Curved(p) => p.scale(),
            Self::Planar(p) => p.scale(),
        }
    }

    pub fn translate(&self) -> [f64; 2] {
        match self {
            Self::Curved(p) => p.translate(),
            Self::Planar(p) => p.translate(),
        }
    }

    pub fn reflect_x(&self) -> bool {
        match self {
            Self::Curved(p) => p.reflect_x(),
            Self::Planar(p) => p.reflect_x(),
        }
    }

    pub fn reflect_y(&self) -> bool {
        match self {
            Self::Curved(p) => p.reflect_y(),
            Self::Planar(p) => p.reflect_y(),
        }
    }

    pub fn set_reflect_x(&mut self, reflect: bool) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_reflect_x(reflect)),
            Self::Planar(p) => drop(p.set_reflect_x(reflect)),
        }
        self
    }

    pub fn set_reflect_y(&mut self, reflect: bool) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_reflect_y(reflect)),
            Self::Planar(p) => drop(p.set_reflect_y(reflect)),
        }
        self
    }

    /// Vega's `Projection` transform on this projection: [`apply_vega_params`] or its `identity` subset.
    pub fn apply_vega_params(&mut self, params: &VegaParams) -> Result<(), String> {
        match self {
            Self::Curved(p) => apply_vega_params(p, params),
            Self::Planar(p) => apply(p, params),
        }
    }
}

impl Fit for VegaProjection {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        match self {
            Self::Curved(p) => p.clip_extent(),
            Self::Planar(p) => p.clip_extent(),
        }
    }

    fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_clip_extent(extent)),
            Self::Planar(p) => drop(p.set_clip_extent(extent)),
        }
        self
    }

    fn set_scale(&mut self, k: f64) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_scale(k)),
            Self::Planar(p) => drop(p.set_scale(k)),
        }
        self
    }

    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_translate(translate)),
            Self::Planar(p) => drop(p.set_translate(translate)),
        }
        self
    }

    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        match self {
            Self::Curved(p) => Projection::stream_object(p, object, sink),
            Self::Planar(p) => Fit::stream_object(p, object, sink),
        }
    }
}

pub type VegaConstructor = fn() -> VegaProjection;

/// vega-projection 2.1.0's registry (`projections.js`) by the lowercase names it registers them under (Vega
/// lowercases a type before it looks it up; callers do too). It holds the projections that are not conic; the
/// conic ones join it.
pub const VEGA_PROJECTIONS: [(&str, VegaConstructor); 12] = [
    ("azimuthalequalarea", || VegaProjection::Curved(Box::new(azimuthal_equal_area()))),
    ("azimuthalequidistant", || VegaProjection::Curved(Box::new(azimuthal_equidistant()))),
    ("equalearth", || VegaProjection::Curved(Box::new(equal_earth()))),
    ("equirectangular", || VegaProjection::Curved(Box::new(equirectangular()))),
    ("gnomonic", || VegaProjection::Curved(Box::new(gnomonic()))),
    ("identity", || VegaProjection::Planar(identity())),
    ("mercator", || VegaProjection::Curved(Box::new(mercator()))),
    ("mollweide", || VegaProjection::Curved(Box::new(mollweide()))),
    ("naturalearth1", || VegaProjection::Curved(Box::new(natural_earth1()))),
    ("orthographic", || VegaProjection::Curved(Box::new(orthographic()))),
    ("stereographic", || VegaProjection::Curved(Box::new(stereographic()))),
    ("transversemercator", || VegaProjection::Curved(Box::new(transverse_mercator()))),
];

/// A new projection registered under `name`; an unregistered name is an error naming it.
pub fn by_vega_name(name: &str) -> Result<VegaProjection, String> {
    VEGA_PROJECTIONS
        .iter()
        .find(|(registered, _)| *registered == name)
        .map(|(_, constructor)| constructor())
        .ok_or_else(|| format!("unknown projection type {name:?}"))
}
