//! Port of topojson-client's `feature`, `mesh` (with `stitch`), `transform` and `reverse`
//! (v3.1.0, ISC, © Mike Bostock), the version Vega 6.2.0's `topojson` format reader imports.
//! Reference: https://github.com/topojson/topojson-client/tree/v3.1.0/src
//!
//! Differences from upstream, all of them `Err`s where JS would throw or emit `NaN`/`undefined`:
//! a geometry `type` that is neither null nor one of the seven GeoJSON ones (upstream returns a
//! null geometry; a `type: null` object, which topojson-server emits, is kept as one), an arc
//! index out of range, an arc with fewer than two points that `mesh` must stitch, a position with
//! fewer than two coordinates, a line with no points, and a null geometry inside a nested
//! `GeometryCollection` (GeoJSON has no null member).

use crate::geojson::{Feature, FeatureCollection, GeoJson, Geometry, Position};
use serde::Deserialize;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Debug, Clone, Deserialize)]
#[serde(try_from = "RawTopology")]
pub struct Topology {
    pub transform: Option<Transform>,
    pub arcs: Vec<Vec<Position>>,
    pub objects: BTreeMap<String, TopoObject>,
}

/// Objects stay raw JSON until each is parsed under its name, so a bad one is reported by name.
#[derive(Deserialize)]
struct RawTopology {
    transform: Option<Transform>,
    arcs: Vec<Vec<Position>>,
    objects: BTreeMap<String, Value>,
}

impl TryFrom<RawTopology> for Topology {
    type Error = String;

    fn try_from(raw: RawTopology) -> Result<Self, String> {
        let objects = raw
            .objects
            .into_iter()
            .map(|(name, value)| {
                TopoObject::deserialize(value)
                    .map(|o| (name.clone(), o))
                    .map_err(|e| format!("object {name:?}: {e}"))
            })
            .collect::<Result<_, _>>()?;
        Ok(Topology {
            transform: raw.transform,
            arcs: raw.arcs,
            objects,
        })
    }
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Transform {
    pub scale: [f64; 2],
    pub translate: [f64; 2],
}

/// `geometry` is `None` for `"type": null`.
#[derive(Debug, Clone, Deserialize)]
#[serde(try_from = "RawObject")]
pub struct TopoObject {
    pub id: Option<Value>,
    pub bbox: Option<Vec<f64>>,
    pub properties: Option<Value>,
    pub geometry: Option<TopoGeometry>,
}

#[derive(Deserialize)]
struct RawObject {
    id: Option<Value>,
    bbox: Option<Vec<f64>>,
    properties: Option<Value>,
    #[serde(flatten)]
    rest: Map<String, Value>,
}

const GEOMETRY_TYPES: [&str; 7] = [
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
];

impl TryFrom<RawObject> for TopoObject {
    type Error = String;

    fn try_from(raw: RawObject) -> Result<Self, String> {
        let geometry = match raw.rest.get("type") {
            Some(Value::Null) => None,
            Some(Value::String(t)) if !GEOMETRY_TYPES.contains(&t.as_str()) => {
                return Err(format!("unknown geometry type {t:?}"))
            }
            _ => Some(
                TopoGeometry::deserialize(Value::Object(raw.rest)).map_err(|e| e.to_string())?,
            ),
        };
        Ok(TopoObject {
            id: raw.id,
            bbox: raw.bbox,
            properties: raw.properties,
            geometry,
        })
    }
}

/// Arc indices are TopoJSON's: `i >= 0` is arc `i`, `i < 0` is arc `!i` reversed.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type")]
pub enum TopoGeometry {
    Point { coordinates: Position },
    MultiPoint { coordinates: Vec<Position> },
    LineString { arcs: Vec<i32> },
    MultiLineString { arcs: Vec<Vec<i32>> },
    Polygon { arcs: Vec<Vec<i32>> },
    MultiPolygon { arcs: Vec<Vec<Vec<i32>>> },
    GeometryCollection { geometries: Vec<TopoObject> },
}

fn arc_index(i: i32) -> usize {
    (if i < 0 { !i } else { i }) as usize
}

fn xy(p: &Position) -> Result<(f64, f64), String> {
    match p[..] {
        [x, y, ..] => Ok((x, y)),
        _ => Err(format!("position {p:?} has fewer than 2 coordinates")),
    }
}

/// `transform(t)(arc[k], k)` over a whole arc: delta-decode, then scale and translate. Without a
/// transform the arc is already absolute; its positions are still checked.
fn transform_arc(t: Option<&Transform>, arc: &[Position]) -> Result<Vec<Position>, String> {
    let (mut x0, mut y0) = (0.0, 0.0);
    arc.iter()
        .map(|p| {
            let (dx, dy) = xy(p)?;
            let Some(t) = t else { return Ok(p.clone()) };
            x0 += dx;
            y0 += dy;
            let mut out = p.clone();
            out[0] = x0 * t.scale[0] + t.translate[0];
            out[1] = y0 * t.scale[1] + t.translate[1];
            Ok(out)
        })
        .collect()
}

/// `transform(t)(p)` for a point, which is absolute even when quantized (no running sum).
fn transform_point(t: Option<&Transform>, p: &Position) -> Result<Position, String> {
    transform_arc(t, std::slice::from_ref(p)).map(|mut v| v.remove(0))
}

/// topojson-client's `reverse(array, n)`: reverses the last `n` elements in place.
fn reverse<T>(array: &mut [T], n: usize) {
    let len = array.len();
    array[len - n..].reverse();
}

impl Topology {
    fn object(&self, name: &str) -> Result<&TopoObject, String> {
        self.objects.get(name).ok_or_else(|| {
            let names: Vec<&str> = self.objects.keys().map(String::as_str).collect();
            format!(
                "topology has no object {name:?}; available: {}",
                names.join(", ")
            )
        })
    }

    fn arc(&self, i: i32) -> Result<&[Position], String> {
        let idx = arc_index(i);
        self.arcs
            .get(idx)
            .map(Vec::as_slice)
            .ok_or_else(|| format!("arc {idx} is out of range ({} arcs)", self.arcs.len()))
    }

    fn arc_into(&self, i: i32, points: &mut Vec<Position>) -> Result<(), String> {
        points.pop();
        let a = self.arc(i)?;
        points.extend(transform_arc(self.transform.as_ref(), a)?);
        if i < 0 {
            reverse(points, a.len());
        }
        Ok(())
    }

    fn line(&self, arcs: &[i32]) -> Result<Vec<Position>, String> {
        let mut points = Vec::new();
        for &i in arcs {
            self.arc_into(i, &mut points)?;
        }
        let first = points
            .first()
            .ok_or_else(|| {
                if arcs.is_empty() {
                    "a line has no arcs".to_string()
                } else {
                    format!("a line over arcs {arcs:?} has no points")
                }
            })?
            .clone();
        // The specification forbids a one-point line; upstream pads it.
        if points.len() < 2 {
            points.push(first.clone());
        }
        Ok(points)
    }

    fn ring(&self, arcs: &[i32]) -> Result<Vec<Position>, String> {
        let mut points = self.line(arcs)?;
        while points.len() < 4 {
            points.push(points[0].clone());
        }
        Ok(points)
    }

    fn polygon(&self, arcs: &[Vec<i32>]) -> Result<Vec<Vec<Position>>, String> {
        arcs.iter().map(|r| self.ring(r)).collect()
    }

    fn geometry(&self, g: &TopoGeometry) -> Result<Geometry, String> {
        let t = self.transform.as_ref();
        Ok(match g {
            TopoGeometry::GeometryCollection { geometries } => Geometry::GeometryCollection {
                geometries: geometries
                    .iter()
                    .map(|o| {
                        let g = o
                            .geometry
                            .as_ref()
                            .ok_or("null geometry inside a nested GeometryCollection")?;
                        self.geometry(g)
                    })
                    .collect::<Result<_, _>>()?,
            },
            TopoGeometry::Point { coordinates } => Geometry::Point {
                coordinates: transform_point(t, coordinates)?,
            },
            TopoGeometry::MultiPoint { coordinates } => Geometry::MultiPoint {
                coordinates: coordinates
                    .iter()
                    .map(|p| transform_point(t, p))
                    .collect::<Result<_, _>>()?,
            },
            TopoGeometry::LineString { arcs } => Geometry::LineString {
                coordinates: self.line(arcs)?,
            },
            TopoGeometry::MultiLineString { arcs } => Geometry::MultiLineString {
                coordinates: arcs
                    .iter()
                    .map(|l| self.line(l))
                    .collect::<Result<_, _>>()?,
            },
            TopoGeometry::Polygon { arcs } => Geometry::Polygon {
                coordinates: arcs
                    .iter()
                    .map(|r| self.ring(r))
                    .collect::<Result<_, _>>()?,
            },
            TopoGeometry::MultiPolygon { arcs } => Geometry::MultiPolygon {
                coordinates: arcs
                    .iter()
                    .map(|p| self.polygon(p))
                    .collect::<Result<_, _>>()?,
            },
        })
    }

    fn feature(&self, o: &TopoObject) -> Result<Feature, String> {
        Ok(Feature {
            id: o.id.clone(),
            bbox: o.bbox.clone(),
            properties: o
                .properties
                .clone()
                .unwrap_or_else(|| Value::Object(Map::new())),
            geometry: o.geometry.as_ref().map(|g| self.geometry(g)).transpose()?,
        })
    }
}

/// `topojson.feature(topology, topology.objects[name])`: a `FeatureCollection` for a
/// `GeometryCollection` object, a single `Feature` otherwise.
pub fn feature(topology: &Topology, name: &str) -> Result<GeoJson, String> {
    let o = topology.object(name)?;
    match &o.geometry {
        Some(TopoGeometry::GeometryCollection { geometries }) => {
            Ok(GeoJson::FeatureCollection(FeatureCollection {
                features: geometries
                    .iter()
                    .map(|g| topology.feature(g))
                    .collect::<Result<_, _>>()?,
            }))
        }
        _ => Ok(GeoJson::Feature(topology.feature(o)?)),
    }
}

/// Called with the first and last geometry that use an arc.
pub type ArcFilter = dyn Fn(&TopoObject, &TopoObject) -> bool;

/// `topojson.mesh(topology, topology.objects[name], filter)`. The filter receives the first and
/// last geometry that use an arc; geometries are compared by address (`std::ptr::eq`), as
/// upstream compares them by identity.
pub fn mesh(
    topology: &Topology,
    name: &str,
    filter: Option<&ArcFilter>,
) -> Result<Geometry, String> {
    let mut geoms_by_arc: BTreeMap<usize, Vec<(i32, &TopoObject)>> = BTreeMap::new();
    extract(topology.object(name)?, &mut geoms_by_arc);
    let arcs = geoms_by_arc
        .values()
        .filter(|geoms| filter.is_none_or(|f| f(geoms[0].1, geoms[geoms.len() - 1].1)))
        .map(|geoms| geoms[0].0)
        .collect();
    mesh_geometry(topology, arcs)
}

/// `topojson.mesh(topology)`: every arc of the topology.
pub fn mesh_arcs(topology: &Topology) -> Result<Geometry, String> {
    mesh_geometry(topology, (0..topology.arcs.len() as i32).collect())
}

fn mesh_geometry(topology: &Topology, arcs: Vec<i32>) -> Result<Geometry, String> {
    let arcs = stitch(topology, arcs)?;
    topology.geometry(&TopoGeometry::MultiLineString { arcs })
}

fn extract<'a>(o: &'a TopoObject, by_arc: &mut BTreeMap<usize, Vec<(i32, &'a TopoObject)>>) {
    let Some(geometry) = &o.geometry else { return };
    if let TopoGeometry::GeometryCollection { geometries } = geometry {
        for g in geometries {
            extract(g, by_arc);
        }
        return;
    }
    let mut add = |i: i32| by_arc.entry(arc_index(i)).or_default().push((i, o));
    match geometry {
        TopoGeometry::LineString { arcs } => arcs.iter().for_each(|&i| add(i)),
        TopoGeometry::MultiLineString { arcs } | TopoGeometry::Polygon { arcs } => {
            arcs.iter().flatten().for_each(|&i| add(i))
        }
        TopoGeometry::MultiPolygon { arcs } => {
            arcs.iter().flatten().flatten().for_each(|&i| add(i))
        }
        TopoGeometry::Point { .. }
        | TopoGeometry::MultiPoint { .. }
        | TopoGeometry::GeometryCollection { .. } => {}
    }
}

type Key = Vec<u64>;

/// A point as a JS object key: arrays coerce through `toString`, where `-0` prints as `0`.
fn key(p: &[f64]) -> Key {
    p.iter().map(|v| (v + 0.0).to_bits()).collect()
}

struct Fragment {
    arcs: Vec<i32>,
    start: Key,
    end: Key,
}

/// A JS object keyed by non-integer strings: iterates in insertion order, and a deleted key
/// re-inserted moves to the end.
#[derive(Default)]
struct Ends {
    position: HashMap<Key, usize>,
    entries: Vec<Option<usize>>,
}

impl Ends {
    fn get(&self, k: &Key) -> Option<usize> {
        self.position.get(k).and_then(|&p| self.entries[p])
    }

    fn set(&mut self, k: Key, fragment: usize) {
        match self.position.get(&k) {
            Some(&p) => self.entries[p] = Some(fragment),
            None => {
                self.position.insert(k, self.entries.len());
                self.entries.push(Some(fragment));
            }
        }
    }

    fn delete(&mut self, k: &Key) {
        if let Some(p) = self.position.remove(k) {
            self.entries[p] = None;
        }
    }
}

/// `stitch(topology, arcs)`: joins arcs that meet end to start into longer lines.
fn stitch(topology: &Topology, mut arcs: Vec<i32>) -> Result<Vec<Vec<i32>>, String> {
    // Stitch empty arcs first, since they may be subsumed by other arcs.
    let mut empty_index = 0;
    for j in 0..arcs.len() {
        let i = arcs[j];
        let arc = topology.arc(i)?;
        let Some(second) = arc.get(1) else {
            return Err(format!("arc {} has fewer than 2 points", arc_index(i)));
        };
        if arc.len() < 3 && xy(second)? == (0.0, 0.0) {
            arcs.swap(empty_index, j);
            empty_index += 1;
        }
    }

    let ends = |i: i32| -> Result<(Key, Key), String> {
        let arc = topology.arc(i)?;
        let p0 = key(&arc[0]);
        let p1 = if topology.transform.is_some() {
            let (mut x, mut y) = (0.0, 0.0);
            for dp in arc {
                let (dx, dy) = xy(dp)?;
                x += dx;
                y += dy;
            }
            key(&[x, y])
        } else {
            key(&arc[arc.len() - 1])
        };
        Ok(if i < 0 { (p1, p0) } else { (p0, p1) })
    };

    let mut by_start = Ends::default();
    let mut by_end = Ends::default();
    let mut frags: Vec<Fragment> = Vec::new();
    for &i in &arcs {
        let (start, end) = ends(i)?;
        if let Some(f) = by_end.get(&start) {
            by_end.delete(&frags[f].end);
            frags[f].arcs.push(i);
            frags[f].end = end.clone();
            if let Some(g) = by_start.get(&end) {
                by_start.delete(&frags[g].start);
                if g != f {
                    let tail = std::mem::take(&mut frags[g].arcs);
                    frags[f].arcs.extend(tail);
                    frags[f].end = frags[g].end.clone();
                }
            }
            by_start.set(frags[f].start.clone(), f);
            by_end.set(frags[f].end.clone(), f);
        } else if let Some(f) = by_start.get(&end) {
            by_start.delete(&frags[f].start);
            frags[f].arcs.insert(0, i);
            frags[f].start = start.clone();
            // Upstream also looks for a fragment ending at `start` here, but that lookup just
            // failed to reach this branch, so there is nothing to join on this side.
            by_start.set(frags[f].start.clone(), f);
            by_end.set(frags[f].end.clone(), f);
        } else {
            frags.push(Fragment {
                arcs: vec![i],
                start: start.clone(),
                end: end.clone(),
            });
            let f = frags.len() - 1;
            by_start.set(start, f);
            by_end.set(end, f);
        }
    }

    let mut stitched: BTreeSet<usize> = BTreeSet::new();
    let mut fragments: Vec<Vec<i32>> = Vec::new();
    // Upstream's two flushes both delete the fragment's `start` key from the other table.
    let mut flush = |from: &Ends, other: &mut Ends| {
        for &f in from.entries.iter().flatten() {
            other.delete(&frags[f].start);
            stitched.extend(frags[f].arcs.iter().map(|&i| arc_index(i)));
            fragments.push(frags[f].arcs.clone());
        }
    };
    flush(&by_end, &mut by_start);
    flush(&by_start, &mut by_end);
    fragments.extend(
        arcs.iter()
            .filter(|&&i| !stitched.contains(&arc_index(i)))
            .map(|&i| vec![i]),
    );
    Ok(fragments)
}
