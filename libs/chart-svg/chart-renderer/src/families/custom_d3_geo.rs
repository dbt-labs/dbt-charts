//! The geo half of a custom chart's `d3` namespace: d3-geo's projections and
//! `geoPath` over the `d3rs::geo` ports, so a template draws GeoJSON it holds
//! in a column or builds from its rows. A projection is immutable, like the
//! scales: every setter returns a new projection, and a getter is the same
//! method called with no argument, as in d3.

use std::collections::BTreeMap;
use std::fmt;
use std::sync::Arc;

use d3rs::geo::path::index::{GeoPath, NoProjection, PathProjection, PointRadius};
use d3rs::geo::projection::fit::Fit;
use d3rs::geo::stream::GeoStream;
use d3rs::geojson::GeoJson;
use minijinja::value::{Object, ObjectRepr, Rest, Value, ValueKind};
use minijinja::{Error, State};

use ::layout::scene_path::scene_path;
use super::custom::invalid;
use super::custom_d3::{bind, extent, finite, floats, items, non_negative_or, pair};
use super::geo::vega::{VegaConstructor, VegaProjection, VEGA_PROJECTIONS};

/// Each projection's template name and its name in vega-projection's registry.
const PROJECTIONS: [(&str, &str); 17] = [
    ("geo_albers", "albers"),
    ("geo_albers_usa", "albersusa"),
    ("geo_azimuthal_equal_area", "azimuthalequalarea"),
    ("geo_azimuthal_equidistant", "azimuthalequidistant"),
    ("geo_conic_conformal", "conicconformal"),
    ("geo_conic_equal_area", "conicequalarea"),
    ("geo_conic_equidistant", "conicequidistant"),
    ("geo_equal_earth", "equalearth"),
    ("geo_equirectangular", "equirectangular"),
    ("geo_gnomonic", "gnomonic"),
    ("geo_identity", "identity"),
    ("geo_mercator", "mercator"),
    ("geo_mollweide", "mollweide"),
    ("geo_natural_earth1", "naturalearth1"),
    ("geo_orthographic", "orthographic"),
    ("geo_stereographic", "stereographic"),
    ("geo_transverse_mercator", "transversemercator"),
];

pub(super) fn register(m: &mut BTreeMap<&'static str, Value>) {
    for (name, vega) in PROJECTIONS {
        let (_, construct) = VEGA_PROJECTIONS.iter().find(|(registered, _)| *registered == vega).expect("registered");
        m.insert(
            name,
            Value::from_function(move |args: Rest<Value>| {
                bind(&format!("d3.{name}()"), [], 0, &args)?;
                Ok::<Value, Error>(Value::from_object(Projection::build(name, *construct, Vec::new())?))
            }),
        );
    }
    m.insert("geo_path", Value::from_function(|args: Rest<Value>| geo_path(&args)));
}

// ── GeoJSON input ───────────────────────────────────────────────────────────

/// A GeoJSON object: a mapping, or its JSON text (a column a warehouse's
/// `ST_AsGeoJSON` returns).
fn geojson(site: &str, name: &str, value: &Value) -> Result<GeoJson, Error> {
    let parsed = match value.as_str() {
        Some(text) => serde_json::from_str::<GeoJson>(text),
        None if value.kind() == ValueKind::Map => {
            serde_json::to_value(value).and_then(serde_json::from_value::<GeoJson>)
        }
        None => {
            return Err(invalid(format!(
                "{site}: {name} must be a GeoJSON mapping or its JSON text, got {value:?}"
            )))
        }
    };
    parsed.map_err(|e| {
        invalid(format!(
            "{site}: {name} is not GeoJSON (a Feature, FeatureCollection or geometry, with a \
             \"type\"): {e}"
        ))
    })
}

// ── Projections ─────────────────────────────────────────────────────────────

/// A setter call; a projection is its constructor plus the calls so far.
#[derive(Clone)]
enum Op {
    Scale(f64),
    Translate([f64; 2]),
    Center([f64; 2]),
    Rotate([f64; 3]),
    Parallels([f64; 2]),
    ClipAngle(f64),
    ClipExtent(Option<[[f64; 2]; 2]>),
    Precision(f64),
    Angle(f64),
    ReflectX(bool),
    ReflectY(bool),
    FitExtent([[f64; 2]; 2], Arc<GeoJson>),
    FitSize([f64; 2], Arc<GeoJson>),
    FitWidth(f64, Arc<GeoJson>),
    FitHeight(f64, Arc<GeoJson>),
}

impl Op {
    fn method(&self) -> &'static str {
        match self {
            Op::Scale(_) => "scale",
            Op::Translate(_) => "translate",
            Op::Center(_) => "center",
            Op::Rotate(_) => "rotate",
            Op::Parallels(_) => "parallels",
            Op::ClipAngle(_) => "clip_angle",
            Op::ClipExtent(_) => "clip_extent",
            Op::Precision(_) => "precision",
            Op::Angle(_) => "angle",
            Op::ReflectX(_) => "reflect_x",
            Op::ReflectY(_) => "reflect_y",
            Op::FitExtent(..) => "fit_extent",
            Op::FitSize(..) => "fit_size",
            Op::FitWidth(..) => "fit_width",
            Op::FitHeight(..) => "fit_height",
        }
    }
}

struct Projection {
    name: &'static str,
    construct: VegaConstructor,
    ops: Vec<Op>,
    built: VegaProjection,
}

impl fmt::Debug for Projection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "d3.{}()", self.name)
    }
}

/// The getter/setter methods d3 gives each kind of projection.
fn methods(built: &VegaProjection) -> &'static [&'static str] {
    match built {
        VegaProjection::Curved(p) if p.parallels().is_some() => &[
            "scale", "translate", "center", "rotate", "parallels", "clip_angle", "clip_extent",
            "precision", "angle", "reflect_x", "reflect_y",
        ],
        VegaProjection::Curved(_) => &[
            "scale", "translate", "center", "rotate", "clip_angle", "clip_extent", "precision",
            "angle", "reflect_x", "reflect_y",
        ],
        VegaProjection::Planar(_) => &["scale", "translate", "clip_extent", "angle", "reflect_x", "reflect_y"],
        VegaProjection::Composite(_) => &["scale", "translate", "precision"],
    }
}

const FITS: [&str; 4] = ["fit_extent", "fit_size", "fit_width", "fit_height"];

/// The most settings one projection takes: every setter replays the earlier
/// ones on a fresh projection, and a template's instruction budget does not
/// count that native work.
const MAX_SETTINGS: usize = 32;

/// The finest `precision`: resampling subdivides each segment until it strays
/// less than this many pixels, so a tiny value makes one path explode.
/// d3's default is 0.707; 0 turns resampling off, as in d3.
const MIN_PRECISION: f64 = 0.01;

impl Projection {
    fn build(name: &'static str, construct: VegaConstructor, ops: Vec<Op>) -> Result<Projection, Error> {
        let mut built = construct();
        for op in &ops {
            apply(&mut built, op).map_err(|e| invalid(format!("d3.{name}().{}(): {e}", op.method())))?;
        }
        Ok(Projection { name, construct, ops, built })
    }

    /// The projection with one more setter call; every earlier call replays on
    /// a fresh one, so a chain of fits streams its objects again.
    fn with(&self, op: Op) -> Result<Value, Error> {
        if self.ops.len() >= MAX_SETTINGS {
            return Err(invalid(format!(
                "d3.{}().{}(): a projection takes at most {MAX_SETTINGS} settings; set it once, not in a loop",
                self.name,
                op.method()
            )));
        }
        let mut ops = self.ops.clone();
        ops.push(op);
        Ok(Value::from_object(Projection::build(self.name, self.construct, ops)?))
    }

    fn no_method(&self, method: &str) -> Error {
        let mut has: Vec<&str> = methods(&self.built).to_vec();
        has.push("invert");
        has.extend(FITS);
        invalid(format!("d3.{}(): no method {method:?}; it has {}", self.name, has.join(", ")))
    }

    fn get(&self, method: &str) -> Value {
        let p = &self.built;
        let curved = match p {
            VegaProjection::Curved(p) => Some(p),
            _ => None,
        };
        match (method, p) {
            ("scale", _) => Value::from(p.scale()),
            ("translate", _) => floats(&p.translate()),
            ("center", _) => floats(&curved.expect("checked").center()),
            ("rotate", _) => floats(&curved.expect("checked").rotate()),
            ("parallels", _) => floats(&curved.expect("checked").parallels().expect("checked")),
            ("clip_angle", _) => Value::from(curved.expect("checked").clip_angle()),
            ("clip_extent", _) => match Fit::clip_extent(p) {
                Some(extent) => Value::from(extent.iter().map(|corner| floats(corner)).collect::<Vec<Value>>()),
                None => Value::from(()),
            },
            ("precision", VegaProjection::Curved(p)) => Value::from(p.precision()),
            ("precision", VegaProjection::Composite(p)) => Value::from(p.precision()),
            ("angle", VegaProjection::Curved(p)) => Value::from(p.angle()),
            ("angle", VegaProjection::Planar(p)) => Value::from(p.angle()),
            ("reflect_x", _) => Value::from(p.reflect_x()),
            ("reflect_y", _) => Value::from(p.reflect_y()),
            _ => unreachable!("methods() lists only getters"),
        }
    }
}

fn apply(p: &mut VegaProjection, op: &Op) -> Result<(), String> {
    if !methods(p).contains(&op.method()) && !FITS.contains(&op.method()) {
        return Err(format!("{} has no {}", p_name(p), op.method()));
    }
    match (op, &mut *p) {
        (Op::Scale(k), p) => drop(p.set_scale(*k)),
        (Op::Translate(t), p) => drop(p.set_translate(*t)),
        (Op::Center(c), VegaProjection::Curved(p)) => drop(p.set_center(*c)),
        (Op::Rotate(r), VegaProjection::Curved(p)) => drop(p.set_rotate(*r)),
        (Op::Parallels(ab), VegaProjection::Curved(p)) => drop(p.set_parallels(*ab)),
        (Op::ClipAngle(deg), VegaProjection::Curved(p)) => drop(p.set_clip_angle(*deg)),
        (Op::ClipExtent(extent), p) => drop(p.set_clip_extent(*extent)),
        (Op::Precision(x), VegaProjection::Curved(p)) => drop(p.set_precision(*x)),
        (Op::Precision(x), VegaProjection::Composite(p)) => drop(p.set_precision(*x)),
        (Op::Angle(deg), VegaProjection::Curved(p)) => drop(p.set_angle(*deg)),
        (Op::Angle(deg), VegaProjection::Planar(p)) => drop(p.set_angle(*deg)),
        (Op::ReflectX(on), p) => drop(p.set_reflect_x(*on)),
        (Op::ReflectY(on), p) => drop(p.set_reflect_y(*on)),
        (Op::FitExtent(extent, object), p) => drop(p.fit_extent(*extent, object)?),
        (Op::FitSize(size, object), p) => drop(p.fit_size(*size, object)?),
        (Op::FitWidth(width, object), p) => drop(p.fit_width(*width, object)?),
        (Op::FitHeight(height, object), p) => drop(p.fit_height(*height, object)?),
        _ => unreachable!("methods() admits only the ops the variant takes"),
    }
    // d3 fits a single point at an infinite scale; a template cannot draw that.
    if !(p.scale().is_finite() && p.translate().iter().all(|t| t.is_finite())) {
        return Err("the fit is not finite: the object has no extent to fit".to_string());
    }
    Ok(())
}

fn p_name(p: &VegaProjection) -> &'static str {
    match p {
        VegaProjection::Curved(_) => "a spherical projection",
        VegaProjection::Planar(_) => "the identity projection",
        VegaProjection::Composite(_) => "the albers_usa composite",
    }
}

impl Object for Projection {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    /// `projection([lon, lat])`: the planar point, or none where the
    /// projection has no place for it (an `albers_usa` point off every inset).
    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.{}()", self.name);
        let [point] = bind(&site, ["point"], 1, args)?.map(|v| v.expect("required"));
        Ok(self.built.forward(pair(&site, "point", &point)?).map_or(Value::from(()), |xy| floats(&xy)))
    }

    fn call_method(self: &Arc<Self>, _state: &State<'_, '_>, method: &str, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.{}().{method}()", self.name);
        if method == "invert" {
            let [point] = bind(&site, ["point"], 1, args)?.map(|v| v.expect("required"));
            return Ok(self.built.invert(pair(&site, "point", &point)?).map_or(Value::from(()), |ll| floats(&ll)));
        }
        if FITS.contains(&method) {
            let box_name = match method {
                "fit_extent" => "extent",
                "fit_size" => "size",
                "fit_width" => "width",
                _ => "height",
            };
            let [box_, object] = bind(&site, [box_name, "object"], 2, args)?.map(|v| v.expect("required"));
            let object = Arc::new(geojson(&site, "object", &object)?);
            return self.with(match method {
                "fit_extent" => Op::FitExtent(extent(&site, box_name, &box_)?, object),
                "fit_size" => Op::FitSize(pair(&site, box_name, &box_)?, object),
                "fit_width" => Op::FitWidth(finite(&site, box_name, &box_)?, object),
                _ => Op::FitHeight(finite(&site, box_name, &box_)?, object),
            });
        }
        if !methods(&self.built).contains(&method) {
            return Err(self.no_method(method));
        }
        let [value] = bind(&site, ["value"], 0, args)?;
        let Some(value) = value else {
            return Ok(self.get(method));
        };
        let number = |name: &str| finite(&site, name, &value);
        let degrees = |name: &str| pair(&site, name, &value);
        self.with(match method {
            "scale" => Op::Scale(number("value")?),
            "translate" => Op::Translate(pair(&site, "value", &value)?),
            "center" => Op::Center(degrees("value")?),
            "rotate" => {
                let angles = items(&site, "value", &value)?;
                let [lambda, phi, gamma] = match angles.as_slice() {
                    [lambda, phi] => [lambda, phi, &Value::from(0.0)],
                    [lambda, phi, gamma] => [lambda, phi, gamma],
                    _ => {
                        return Err(invalid(format!(
                            "{site}: value must be [lambda, phi] or [lambda, phi, gamma] in degrees, got {} values",
                            angles.len()
                        )))
                    }
                };
                Op::Rotate([finite(&site, "value", lambda)?, finite(&site, "value", phi)?, finite(&site, "value", gamma)?])
            }
            "parallels" => Op::Parallels(degrees("value")?),
            // d3's `clipAngle(null)` is the antimeridian clip, which d3rs reads as 0.
            "clip_angle" => Op::ClipAngle(if value.is_none() { 0.0 } else { number("value")? }),
            "clip_extent" => Op::ClipExtent(if value.is_none() { None } else { Some(extent(&site, "value", &value)?) }),
            "precision" => {
                let precision = number("value")?;
                if precision != 0.0 && precision < MIN_PRECISION {
                    return Err(invalid(format!(
                        "{site}: value must be 0 (no resampling) or at least {MIN_PRECISION}, got {precision}"
                    )));
                }
                Op::Precision(precision)
            }
            "angle" => Op::Angle(number("value")?),
            "reflect_x" | "reflect_y" => {
                if value.kind() != ValueKind::Bool {
                    return Err(invalid(format!("{site}: value must be true or false, got {value:?}")));
                }
                let on = value.is_true();
                if method == "reflect_x" {
                    Op::ReflectX(on)
                } else {
                    Op::ReflectY(on)
                }
            }
            _ => unreachable!("methods() lists only setters"),
        })
    }
}

// ── geoPath ─────────────────────────────────────────────────────────────────

fn geo_path(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.geo_path()";
    let [projection, digits, point_radius] = bind(SITE, ["projection", "digits", "point_radius"], 0, args)?;
    let projection = match projection {
        Some(p) if !p.is_none() => Some(
            p.downcast_object::<Projection>()
                .ok_or_else(|| invalid(format!("{SITE}: projection must be a d3.geo_* projection or none, got {p:?}")))?,
        ),
        _ => None,
    };
    // Absent is d3's default of 3 digits; an explicit `none` is `digits(null)`, unrounded.
    let digits = match digits {
        None => Some(3.0),
        Some(d) if d.is_none() => None,
        Some(d) => Some(non_negative_or(SITE, "digits", Some(&d), 3.0)?),
    };
    // d3's `pointRadius(null)` is `+null`: a radius of 0.
    let point_radius = match point_radius {
        Some(r) if r.is_none() => 0.0,
        r => non_negative_or(SITE, "point_radius", r.as_ref(), 4.5)?,
    };
    Ok(Value::from_object(Path { projection, digits, point_radius }))
}

/// `d3.geo_path(projection)`: calling it is `path(object)`; `area`, `bounds`,
/// `centroid` and `measure` are its planar measures.
#[derive(Debug)]
struct Path {
    projection: Option<Arc<Projection>>,
    digits: Option<f64>,
    point_radius: f64,
}

/// What a path streams through: a projection, or none for planar coordinates.
enum Through<'a> {
    Planar,
    Projected(&'a VegaProjection),
}

impl PathProjection for Through<'_> {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        match self {
            Through::Planar => NoProjection.stream_object(object, sink),
            Through::Projected(p) => PathProjection::stream_object(*p, object, sink),
        }
    }
}

impl Path {
    fn generator(&self) -> GeoPath<Through<'_>> {
        let through = match &self.projection {
            Some(p) => Through::Projected(&p.built),
            None => Through::Planar,
        };
        GeoPath::new(through)
            .with_digits(self.digits)
            .expect("digits were checked non-negative")
            .with_point_radius(PointRadius::Constant(self.point_radius))
    }
}

impl Object for Path {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    /// `path(object)`: the SVG path data, or none where nothing is drawn (a
    /// point behind the globe, an empty collection).
    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        const SITE: &str = "d3.geo_path()";
        let [object] = bind(SITE, ["object"], 1, args)?.map(|v| v.expect("required"));
        let object = geojson(SITE, "object", &object)?;
        let drawn = self.generator().path(&object).map_err(|e| invalid(format!("{SITE}: {e}")))?;
        Ok(drawn.map_or(Value::from(()), |path| Value::from(print::path_d(&scene_path(&path)))))
    }

    fn call_method(self: &Arc<Self>, _state: &State<'_, '_>, method: &str, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.geo_path().{method}()");
        if !["area", "bounds", "centroid", "measure"].contains(&method) {
            return Err(invalid(format!(
                "d3.geo_path(): no method {method:?}; it has area, bounds, centroid and measure"
            )));
        }
        let [object] = bind(&site, ["object"], 1, args)?.map(|v| v.expect("required"));
        let object = geojson(&site, "object", &object)?;
        let path = self.generator();
        let measured = match method {
            "area" => path.area(&object).map(Value::from),
            "bounds" => path
                .bounds(&object)
                .map(|corners| Value::from(corners.iter().map(|corner| floats(corner)).collect::<Vec<Value>>())),
            "centroid" => path.centroid(&object).map(|c| floats(&c)),
            _ => path.measure(&object).map(Value::from),
        };
        measured.map_err(|e| invalid(format!("{site}: {e}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn construct(vega: &str) -> VegaConstructor {
        VEGA_PROJECTIONS.iter().find(|(registered, _)| *registered == vega).unwrap().1
    }

    #[test]
    fn every_registered_projection_constructs_and_names_its_methods() {
        for (name, vega) in PROJECTIONS {
            let p = Projection::build(name, construct(vega), Vec::new()).unwrap();
            assert!(!methods(&p.built).is_empty(), "{name}");
        }
    }

    #[test]
    fn geojson_reads_text_and_mappings_and_names_what_it_is_not() {
        let point = r#"{"type": "Point", "coordinates": [1, 2]}"#;
        assert!(matches!(geojson("s", "o", &Value::from(point)).unwrap(), GeoJson::Geometry(_)));
        let mapping: Value = serde_json::from_str::<serde_json::Value>(point).map(Value::from_serialize).unwrap();
        assert!(matches!(geojson("s", "o", &mapping).unwrap(), GeoJson::Geometry(_)));
        let err = geojson("s", "o", &Value::from(r#"{"type": "Blob"}"#)).unwrap_err().to_string();
        assert!(err.contains("o is not GeoJSON"), "{err}");
        let err = geojson("s", "o", &Value::from(3)).unwrap_err().to_string();
        assert!(err.contains("o must be a GeoJSON mapping or its JSON text"), "{err}");
    }

    #[test]
    fn a_fit_applied_through_ops_matches_d3() {
        let polygon: Arc<GeoJson> = Arc::new(
            serde_json::from_str(r#"{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,10],[0,10],[0,0]]]}"#).unwrap(),
        );
        let p = Projection::build("geo_mercator", construct("mercator"), vec![Op::FitSize([200.0, 100.0], polygon)]).unwrap();
        assert_eq!(p.built.scale(), 15.915494309182861);
        assert_eq!(p.built.translate(), [100.0, 49.99999999997903]);
    }

    #[test]
    fn a_method_the_variant_lacks_is_refused_by_name() {
        let usa = Projection::build("geo_albers_usa", construct("albersusa"), vec![Op::Rotate([1.0, 2.0, 0.0])]);
        let err = usa.unwrap_err().to_string();
        assert!(err.contains("d3.geo_albers_usa().rotate(): the albers_usa composite has no rotate"), "{err}");
    }
}
