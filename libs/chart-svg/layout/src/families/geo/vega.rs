//! vega-geo 5.1.3 `src/Projection.js` with vega-projection 2.1.0's `projectionProperties`: a Vega `projection`
//! params object applied to a fresh projection. A parameter applies when present, in `apply`'s order and only if
//! the projection has the method (`parallels` skips a non-conic one). `pointRadius` belongs to the path
//! ([`VegaProjection::into_path`]); `fit` runs last and overwrites scale and translate. `rotate` reads its
//! first three angles; fewer than two is an error, as is a fit whose bounds are not finite.

use d3rs::geo::projection::albers::albers;
use d3rs::geo::projection::albers_usa::{albers_usa, AlbersUsa};
use d3rs::geo::projection::azimuthal_equal_area::azimuthal_equal_area;
use d3rs::geo::projection::azimuthal_equidistant::azimuthal_equidistant;
use d3rs::geo::projection::conic_conformal::conic_conformal;
use d3rs::geo::projection::conic_equal_area::conic_equal_area;
use d3rs::geo::projection::conic_equidistant::conic_equidistant;
use d3rs::geo::projection::equal_earth::equal_earth;
use d3rs::geo::projection::equirectangular::equirectangular;
use d3rs::geo::projection::fit::Fit;
use d3rs::geo::projection::gnomonic::gnomonic;
use d3rs::geo::projection::identity::{identity, Identity};
use d3rs::geo::projection::mercator::mercator;
use d3rs::geo::projection::mollweide::mollweide;
use d3rs::geo::projection::natural_earth1::natural_earth1;
use d3rs::geo::projection::orthographic::orthographic;
use d3rs::geo::projection::stereographic::stereographic;
use d3rs::geo::projection::transverse_mercator::transverse_mercator;
use d3rs::geo::projection::Projection;
use d3rs::geo::path::index::{GeoPath, PathProjection, PointRadius};
use d3rs::geo::stream::GeoStream;
use d3rs::geojson::{Feature, FeatureCollection, GeoJson};
use serde::Deserialize;
use serde_json::Value;
use std::borrow::Cow;

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
    pub fit: Option<FitObjects>,
    pub extent: Option<[[f64; 2]; 2]>,
    pub size: Option<[f64; 2]>,
}

/// `fit`: one object or an array of them.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum FitObjects {
    Many(Vec<Option<GeoJson>>),
    One(GeoJson),
}

/// `collectGeoJSON`: a lone object as it is, else the features of the objects. A lone null streams nothing, so
/// the fit refuses it as not finite; among several objects a null is the TypeError Vega throws reading its type.
fn collect_geojson(objects: &FitObjects) -> Result<Cow<'_, GeoJson>, String> {
    let items = match objects {
        FitObjects::One(only) => return Ok(Cow::Borrowed(only)),
        FitObjects::Many(items) => items,
    };
    match items.as_slice() {
        [Some(only)] => Ok(Cow::Borrowed(only)),
        [None] => Ok(Cow::Owned(GeoJson::FeatureCollection(FeatureCollection { features: vec![] }))),
        _ => {
            let objects = items
                .iter()
                .enumerate()
                .map(|(i, item)| item.as_ref().ok_or_else(|| format!("fit: object {i} is null")))
                .collect::<Result<Vec<_>, _>>()?;
            let features = objects
                .into_iter()
                .flat_map(|item| match item {
                    GeoJson::FeatureCollection(collection) => collection.features.clone(),
                    GeoJson::Feature(feature) => vec![feature.clone()],
                    GeoJson::Geometry(geometry) => {
                        vec![Feature { id: None, bbox: None, properties: Value::Null, geometry: Some(geometry.clone()) }]
                    }
                })
                .collect();
            Ok(Cow::Owned(GeoJson::FeatureCollection(FeatureCollection { features })))
        }
    }
}

/// What a params object can set. A key the projection has no method for is skipped (`isFunction(proj[key])`),
/// which is each default here: `identity` has only `clipExtent`, `scale`, `translate`, `reflectX`, `reflectY`,
/// and `albersUsa` only `scale`, `translate` and `precision`.
trait VegaTarget: Fit {
    fn vega_clip_angle(&mut self, _degrees: f64) {}
    fn vega_clip_extent(&mut self, extent: [[f64; 2]; 2]) {
        self.set_clip_extent(Some(extent));
    }
    fn vega_center(&mut self, _center: [f64; 2]) {}
    fn vega_rotate(&mut self, _angles: &[f64]) -> Result<(), String> {
        Ok(())
    }
    fn vega_parallels(&mut self, _parallels: [f64; 2]) {}
    fn vega_precision(&mut self, _precision: f64) {}
    fn vega_reflect_x(&mut self, _reflect: bool) {}
    fn vega_reflect_y(&mut self, _reflect: bool) {}
}

impl VegaTarget for AlbersUsa {
    fn vega_clip_extent(&mut self, _extent: [[f64; 2]; 2]) {}

    fn vega_precision(&mut self, precision: f64) {
        self.set_precision(precision);
    }
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
        projection.vega_clip_extent(extent);
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
    let data = collect_geojson(fit)?;
    if let Some(extent) = params.extent {
        projection.fit_extent(extent, &data)?;
    } else if let Some(size) = params.size {
        projection.fit_size(size, &data)?;
    }
    Ok(())
}

/// A projection `vega-projection` registers: a spherical one, the planar `identity`, or the `albersUsa`
/// composite. d3 gives the composite no reflection or clip extent: its getters answer what that means (no
/// reflection, no clip extent) and its setters panic, since a caller that reaches them has the wrong
/// projection. Vega parameters go through [`VegaProjection::apply_vega_params`], which skips them as Vega does.
pub enum VegaProjection {
    Curved(Box<Projection>),
    Planar(Identity),
    Composite(Box<AlbersUsa>),
}

impl VegaProjection {
    /// `None` where `albersUsa` has no inset for the point; every other projection always has a forward.
    pub fn forward(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        match self {
            Self::Curved(p) => Some(p.forward(point)),
            Self::Planar(p) => Some(p.forward(point)),
            Self::Composite(p) => p.forward(point),
        }
    }

    /// `None` where a spherical projection has no inverse there; `identity` always has one.
    pub fn invert(&self, point: [f64; 2]) -> Option<[f64; 2]> {
        match self {
            Self::Curved(p) => p.invert(point),
            Self::Planar(p) => Some(p.invert(point)),
            Self::Composite(p) => p.invert(point),
        }
    }

    pub fn scale(&self) -> f64 {
        match self {
            Self::Curved(p) => p.scale(),
            Self::Planar(p) => p.scale(),
            Self::Composite(p) => p.scale(),
        }
    }

    pub fn translate(&self) -> [f64; 2] {
        match self {
            Self::Curved(p) => p.translate(),
            Self::Planar(p) => p.translate(),
            Self::Composite(p) => p.translate(),
        }
    }

    pub fn reflect_x(&self) -> bool {
        match self {
            Self::Curved(p) => p.reflect_x(),
            Self::Planar(p) => p.reflect_x(),
            Self::Composite(_) => false,
        }
    }

    pub fn reflect_y(&self) -> bool {
        match self {
            Self::Curved(p) => p.reflect_y(),
            Self::Planar(p) => p.reflect_y(),
            Self::Composite(_) => false,
        }
    }

    pub fn set_reflect_x(&mut self, reflect: bool) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_reflect_x(reflect)),
            Self::Planar(p) => drop(p.set_reflect_x(reflect)),
            Self::Composite(_) => panic!("albersUsa has no reflectX"),
        }
        self
    }

    pub fn set_reflect_y(&mut self, reflect: bool) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_reflect_y(reflect)),
            Self::Planar(p) => drop(p.set_reflect_y(reflect)),
            Self::Composite(_) => panic!("albersUsa has no reflectY"),
        }
        self
    }

    /// The bounds the fit of `objects` measures (see [`Fit::fit_bounds`]), for [`Fit::fit_extent_to`] to apply
    /// at any number of boxes. The projection must already carry every other param.
    pub fn fit_objects_bounds(&mut self, objects: &FitObjects) -> Result<[[f64; 2]; 2], String> {
        let data = collect_geojson(objects)?;
        Fit::fit_bounds(self, &data)
    }

    /// `proj.path` with `pointRadius` from `params`, as the `Projection` transform sets it.
    pub fn into_path(self, params: &VegaParams) -> GeoPath<Self> {
        let path = GeoPath::new(self);
        match params.point_radius {
            Some(radius) => path.with_point_radius(PointRadius::Constant(radius)),
            None => path,
        }
    }

    /// Vega's `Projection` transform on this projection: [`apply_vega_params`] or its `identity` subset.
    pub fn apply_vega_params(&mut self, params: &VegaParams) -> Result<(), String> {
        match self {
            Self::Curved(p) => apply_vega_params(p, params),
            Self::Planar(p) => apply(p, params),
            Self::Composite(p) => apply(&mut **p, params),
        }
    }
}

impl Fit for VegaProjection {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        match self {
            Self::Curved(p) => p.clip_extent(),
            Self::Planar(p) => p.clip_extent(),
            Self::Composite(p) => Fit::clip_extent(&**p),
        }
    }

    fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_clip_extent(extent)),
            Self::Planar(p) => drop(p.set_clip_extent(extent)),
            Self::Composite(_) => panic!("albersUsa has no clipExtent"),
        }
        self
    }

    fn set_scale(&mut self, k: f64) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_scale(k)),
            Self::Planar(p) => drop(p.set_scale(k)),
            Self::Composite(p) => drop(p.set_scale(k)),
        }
        self
    }

    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        match self {
            Self::Curved(p) => drop(p.set_translate(translate)),
            Self::Planar(p) => drop(p.set_translate(translate)),
            Self::Composite(p) => drop(p.set_translate(translate)),
        }
        self
    }

    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        match self {
            Self::Curved(p) => Projection::stream_object(p, object, sink),
            Self::Planar(p) => Fit::stream_object(p, object, sink),
            Self::Composite(p) => Fit::stream_object(&**p, object, sink),
        }
    }
}

impl PathProjection for VegaProjection {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        match self {
            Self::Curved(p) => PathProjection::stream_object(&**p, object, sink),
            Self::Planar(p) => PathProjection::stream_object(p, object, sink),
            Self::Composite(p) => PathProjection::stream_object(&**p, object, sink),
        }
    }
}

pub type VegaConstructor = fn() -> VegaProjection;

/// vega-projection 2.1.0's registry (`projections.js`), keyed by the
/// lowercase names it registers them under. Vega lowercases a type before it looks it up; callers do too.
pub const VEGA_PROJECTIONS: [(&str, VegaConstructor); 17] = [
    ("albers", || VegaProjection::Curved(Box::new(albers()))),
    ("albersusa", || VegaProjection::Composite(Box::new(albers_usa()))),
    ("azimuthalequalarea", || VegaProjection::Curved(Box::new(azimuthal_equal_area()))),
    ("azimuthalequidistant", || VegaProjection::Curved(Box::new(azimuthal_equidistant()))),
    ("conicconformal", || VegaProjection::Curved(Box::new(conic_conformal()))),
    ("conicequalarea", || VegaProjection::Curved(Box::new(conic_equal_area()))),
    ("conicequidistant", || VegaProjection::Curved(Box::new(conic_equidistant()))),
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
