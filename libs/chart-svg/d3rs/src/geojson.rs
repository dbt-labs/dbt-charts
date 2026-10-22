//! Minimal GeoJSON types: the geometry and feature shapes `topojson` decodes into and d3-geo's
//! stream walks. Positions are `Vec<f64>` because GeoJSON allows extra dimensions.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub type Position = Vec<f64>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Geometry {
    Point {
        coordinates: Position,
    },
    MultiPoint {
        coordinates: Vec<Position>,
    },
    LineString {
        coordinates: Vec<Position>,
    },
    MultiLineString {
        coordinates: Vec<Vec<Position>>,
    },
    Polygon {
        coordinates: Vec<Vec<Position>>,
    },
    MultiPolygon {
        coordinates: Vec<Vec<Vec<Position>>>,
    },
    GeometryCollection {
        geometries: Vec<Geometry>,
    },
    /// d3-geo's extension: the whole globe.
    Sphere,
}

/// `id` is a number or a string in the wild, so it stays a JSON value.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub struct Feature {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bbox: Option<Vec<f64>>,
    pub properties: Value,
    pub geometry: Option<Geometry>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub struct FeatureCollection {
    pub features: Vec<Feature>,
}

/// What `topojson::feature` returns (a collection for a `GeometryCollection` object, else one feature),
/// and what `geo_stream` accepts: a bare geometry too.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum GeoJson {
    FeatureCollection(FeatureCollection),
    Feature(Feature),
    Geometry(Geometry),
}
