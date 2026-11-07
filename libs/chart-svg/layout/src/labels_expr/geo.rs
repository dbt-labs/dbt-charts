//! `geoArea()`, `geoBounds()` and `geoCentroid()` over a GeoJSON value: the object walked the way d3-geo's `stream`
//! walks it (a JavaScript value, so a missing member reads as `undefined`) into the `d3rs::geo` sinks.

use d3rs::geo::stream::GeoStream;
use d3rs::geo::{Area, Bounds, Centroid};

use super::val::{throws, Res, Val};

fn prop(v: &Val, key: &'static str) -> Res<Val> {
    v.property(&Val::Str(key.into()))
}

fn index(v: &Val, i: usize) -> Res<Val> {
    v.property(&Val::Num(i as f64))
}

fn length(v: &Val) -> Res<usize> {
    let n = prop(v, "length")?.to_number();
    Ok(if n.is_nan() || n < 0.0 { 0 } else { n as usize })
}

fn type_of(v: &Val) -> Option<String> {
    match v {
        Val::Undefined | Val::Null => None,
        other => match prop(other, "type") {
            Ok(Val::Str(s)) => Some(s.into_string()),
            Ok(Val::Undefined | Val::Null) | Err(_) => None,
            Ok(other) => Some(other.to_js_string()),
        },
    }
}

fn point(coordinate: &Val, sink: &mut impl GeoStream) -> Res<()> {
    sink.point(index(coordinate, 0)?.to_number(), index(coordinate, 1)?.to_number(), None);
    Ok(())
}

fn line(coordinates: &Val, closed: usize, sink: &mut impl GeoStream) -> Res<()> {
    let n = length(coordinates)?.saturating_sub(closed);
    sink.line_start();
    for i in 0..n {
        point(&index(coordinates, i)?, sink)?;
    }
    sink.line_end();
    Ok(())
}

fn polygon(coordinates: &Val, sink: &mut impl GeoStream) -> Res<()> {
    sink.polygon_start();
    for i in 0..length(coordinates)? {
        line(&index(coordinates, i)?, 1, sink)?;
    }
    sink.polygon_end();
    Ok(())
}

fn geometry(g: &Val, sink: &mut impl GeoStream) -> Res<()> {
    let Some(kind) = type_of(g) else { return Ok(()) };
    match kind.as_str() {
        "Sphere" => sink.sphere(),
        "Point" => point(&prop(g, "coordinates")?, sink)?,
        "MultiPoint" => {
            let coordinates = prop(g, "coordinates")?;
            for i in 0..length(&coordinates)? {
                point(&index(&coordinates, i)?, sink)?;
            }
        }
        "LineString" => line(&prop(g, "coordinates")?, 0, sink)?,
        "MultiLineString" => {
            let coordinates = prop(g, "coordinates")?;
            for i in 0..length(&coordinates)? {
                line(&index(&coordinates, i)?, 0, sink)?;
            }
        }
        "Polygon" => polygon(&prop(g, "coordinates")?, sink)?,
        "MultiPolygon" => {
            let coordinates = prop(g, "coordinates")?;
            for i in 0..length(&coordinates)? {
                polygon(&index(&coordinates, i)?, sink)?;
            }
        }
        "GeometryCollection" => {
            let geometries = prop(g, "geometries")?;
            for i in 0..length(&geometries)? {
                geometry(&index(&geometries, i)?, sink)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn stream(object: &Val, sink: &mut impl GeoStream) -> Res<()> {
    match type_of(object).as_deref() {
        Some("Feature") => geometry(&prop(object, "geometry")?, sink),
        Some("FeatureCollection") => {
            let features = prop(object, "features")?;
            for i in 0..length(&features)? {
                geometry(&prop(&index(&features, i)?, "geometry")?, sink)?;
            }
            Ok(())
        }
        _ => geometry(object, sink),
    }
}

fn streamed<S: GeoStream>(object: &Val, mut sink: S) -> Res<S> {
    stream(object, &mut sink)?;
    Ok(sink)
}

/// `geoArea` / `geoBounds` / `geoCentroid` of `vals[1]`, or, with a projection named, what a chart with no
/// projections finds: nothing.
pub fn call(name: &str, vals: &[Val], projection: Res<()>) -> Res<Val> {
    let first = vals.first().unwrap_or(&Val::Undefined);
    if first.truthy() {
        projection?;
        return Ok(Val::Undefined);
    }
    let object = vals.get(1).unwrap_or(&Val::Undefined);
    match name {
        "geoArea" => Ok(Val::Num(streamed(object, Area::default())?.result())),
        "geoBounds" => {
            let [[west, south], [east, north]] = streamed(object, Bounds::default())?.result().or_else(|message| throws(message))?;
            let pair = |a: f64, b: f64| Val::Array(vec![Val::Num(a), Val::Num(b)]);
            Ok(Val::Array(vec![pair(west, south), pair(east, north)]))
        }
        "geoCentroid" => {
            let [longitude, latitude] = streamed(object, Centroid::default())?.result();
            Ok(Val::Array(vec![Val::Num(longitude), Val::Num(latitude)]))
        }
        _ => throws(format!("{name} is not a geo method")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feature(json: &str) -> Val {
        fn lift(v: &serde_json::Value) -> Val {
            match v {
                serde_json::Value::Null => Val::Null,
                serde_json::Value::Bool(b) => Val::Bool(*b),
                serde_json::Value::Number(n) => Val::Num(n.as_f64().unwrap()),
                serde_json::Value::String(s) => Val::string(s.clone()).unwrap(),
                serde_json::Value::Array(a) => Val::Array(a.iter().map(lift).collect()),
                serde_json::Value::Object(o) => Val::Object(o.iter().map(|(k, v)| (k.clone(), lift(v))).collect()),
            }
        }
        lift(&serde_json::from_str(json).unwrap())
    }

    fn nums(v: Val) -> Vec<f64> {
        match v {
            Val::Array(items) => items.into_iter().flat_map(nums).collect(),
            Val::Num(n) => vec![n],
            other => panic!("{other:?}"),
        }
    }

    fn measure(name: &str, object: &Val) -> Val {
        call(name, &[Val::Null, object.clone()], Ok(())).unwrap()
    }

    /// The values d3-geo 3 answers for these objects (`d3.geoArea`, `geoBounds`, `geoCentroid`).
    #[test]
    fn geometry_measures_as_d3_geo_measures_it() {
        let sphere = feature(r#"{"type":"Sphere"}"#);
        assert!((nums(measure("geoArea", &sphere))[0] - 12.566370614359172).abs() < 1e-12);
        assert_eq!(nums(measure("geoBounds", &sphere)), [-180.0, -90.0, 180.0, 90.0]);
        let polygon = feature(r#"{"type":"Polygon","coordinates":[[[0,0],[10,0],[10,10],[0,10],[0,0]]]}"#);
        assert!((nums(measure("geoArea", &polygon))[0] - 12.53598845768457).abs() < 1e-9);
        assert_eq!(nums(measure("geoBounds", &polygon)), [-180.0, -90.0, 180.0, 90.0]);
        let c = nums(measure("geoCentroid", &polygon));
        assert!((c[0] + 175.0).abs() < 1e-9 && (c[1] + 5.005958633534433).abs() < 1e-9, "{c:?}");
        let line = feature(r#"{"type":"LineString","coordinates":[[0,0],[10,10]]}"#);
        assert_eq!(nums(measure("geoBounds", &line)), [0.0, 0.0, 10.0, 10.0]);
        let c = nums(measure("geoCentroid", &line));
        assert!((c[0] - 4.961631226702507).abs() < 1e-9 && (c[1] - 5.019000697861148).abs() < 1e-9, "{c:?}");
        let points = feature(r#"{"type":"MultiPoint","coordinates":[[0,0],[5,5]]}"#);
        assert_eq!(nums(measure("geoBounds", &points)), [0.0, 0.0, 5.0, 5.0]);
        assert!(nums(measure("geoBounds", &Val::Undefined)).iter().all(|n| n.is_nan()));
        assert_eq!(nums(measure("geoArea", &Val::Num(0.0))), [0.0]);
    }
}
