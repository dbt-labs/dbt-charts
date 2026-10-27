//! A geo source as the engine reads it: the document text under a URL in
//! `payload.geometries`, decoded to features once per distinct content.
//!
//! The host hands the engine each document as raw JSON text, unparsed. Decoding
//! a county-level map costs far more than drawing it, so [`decode`] keeps the
//! result in a process-wide, size-capped cache addressed by a SHA-256 of the
//! exact bytes plus the reader parameters that shaped the result. The key is the
//! function's complete input and a hit is an immutable `Arc`, so the cache holds
//! no caller identity: the same bytes decode the same way for everyone, whether
//! they shipped with the package or were fetched from a URL.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use d3rs::geojson::{Feature, GeoJson};
use d3rs::topojson::{self, Topology};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::codes;
use crate::diagnostic::Diagnostic;

/// Each geo document's JSON text, keyed by the source URL a chart names.
pub type Geometries = HashMap<String, Arc<str>>;

/// The most document text the cache keeps decoded. A decoded map is several
/// times its text, so this bounds the cache near a few hundred megabytes; a
/// document larger than the cap is decoded for its render and not kept.
pub const CACHE_CAP_BYTES: usize = 32 * 1024 * 1024;

/// A geo source as Vega's data reader is told it: the document under `url` in
/// `payload.geometries`, read as `format_type` (`feature` names a TopoJSON object,
/// `property` the path to a GeoJSON feature list).
pub(super) struct GeoSource<'a> {
    /// What the source draws, named in its errors (`geoshape`, `basemap`).
    pub role: &'a str,
    /// The name the author knows the source by, for the error card; empty for an unnamed one.
    pub name: &'a str,
    pub url: &'a str,
    pub format_type: &'a str,
    pub feature: Option<&'a str>,
    pub property: Option<&'a str>,
}

/// What a source decodes to.
pub(super) struct Decoded {
    /// The features, as Vega's `topojson` and `json` readers list them.
    pub features: Vec<Feature>,
    /// A `json` source's items as the document spells them (what a join key reads); empty otherwise.
    pub items: Vec<Value>,
}

type Key = ([u8; 32], String, Option<String>, Option<String>);

/// Least-recently-used entries, capped by the document bytes they were decoded from.
struct Cache {
    entries: VecDeque<(Key, usize, Arc<Decoded>)>,
    bytes: usize,
    cap: usize,
}

impl Cache {
    const fn new(cap: usize) -> Self {
        Cache { entries: VecDeque::new(), bytes: 0, cap }
    }

    fn get(&mut self, key: &Key) -> Option<Arc<Decoded>> {
        let at = self.entries.iter().position(|(k, ..)| k == key)?;
        let entry = self.entries.remove(at).expect("position is in range");
        let hit = entry.2.clone();
        self.entries.push_back(entry);
        Some(hit)
    }

    fn insert(&mut self, key: Key, size: usize, value: Arc<Decoded>) {
        if size > self.cap {
            return;
        }
        if let Some(at) = self.entries.iter().position(|(k, ..)| *k == key) {
            self.bytes -= self.entries.remove(at).expect("position is in range").1;
        }
        while self.bytes + size > self.cap {
            let (_, evicted, _) = self.entries.pop_front().expect("bytes > 0 means an entry");
            self.bytes -= evicted;
        }
        self.bytes += size;
        self.entries.push_back((key, size, value));
    }
}

static CACHE: Mutex<Cache> = Mutex::new(Cache::new(CACHE_CAP_BYTES));
static DECODES: AtomicUsize = AtomicUsize::new(0);

/// How many documents this process has decoded rather than served from the cache.
pub fn geometry_decodes() -> usize {
    DECODES.load(Ordering::SeqCst)
}

/// The features of `source`, from the cache when these exact bytes were already read this way.
pub(super) fn decode(source: &GeoSource, geometries: &Geometries) -> Result<Arc<Decoded>, Diagnostic> {
    let (role, url) = (source.role, source.url);
    let text = geometries
        .get(url)
        .ok_or_else(|| format!("{role}: payload.geometries has no document for geo_url {url:?}"))?;
    let key = (
        Sha256::digest(text.as_bytes()).into(),
        source.format_type.to_string(),
        source.feature.map(str::to_string),
        source.property.map(str::to_string),
    );
    if let Some(hit) = CACHE.lock().expect("geometry cache mutex poisoned").get(&key) {
        return Ok(hit);
    }
    let decoded = Arc::new(decode_text(source, text)?);
    DECODES.fetch_add(1, Ordering::SeqCst);
    CACHE.lock().expect("geometry cache mutex poisoned").insert(key, text.len(), decoded.clone());
    Ok(decoded)
}

fn unavailable(source: &GeoSource, reason: String) -> Diagnostic {
    let name = if source.name.is_empty() { source.url } else { source.name };
    Diagnostic::new(&codes::ERR_GEO_SOURCE_UNAVAILABLE, [("source", json!(name)), ("reason", json!(reason))])
}

fn decode_text(source: &GeoSource, text: &str) -> Result<Decoded, Diagnostic> {
    let (role, url) = (source.role, source.url);
    if !matches!(source.format_type, "topojson" | "json") {
        let other = source.format_type;
        return Err(crate::refuse!("production errors too, Vega raises \"Unknown data format type\": geo_format_type {other:?} is not supported").into());
    }
    if source.format_type == "topojson" {
        let name = source.feature.ok_or_else(|| format!("{role}: a topojson source names no geo_feature"))?;
        // Straight from the text: the usual document is valid, and building a `Value` tree of it first costs more
        // than the decode. A document that fails is read again as a `Value` to say whether it is not JSON, not an
        // object, or a topology that is wrong.
        let topology = match serde_json::from_str::<Topology>(text) {
            Ok(topology) => topology,
            Err(_) => {
                let doc = parse_object(source, text)?;
                Topology::deserialize(&doc).map_err(|e| format!("{role}: geo_url {url:?}: {e}"))?
            }
        };
        let features = match topojson::feature(&topology, name)? {
            GeoJson::FeatureCollection(collection) => collection.features,
            GeoJson::Feature(feature) => vec![feature],
            GeoJson::Geometry(_) => {
                return Err(format!("{role}: topojson object {name:?} decoded to a bare geometry").into())
            }
        };
        return Ok(Decoded { features, items: Vec::new() });
    }
    let doc = parse_object(source, text)?;
    let items = json_items(source, &doc)?;
    let features = items
        .iter()
        .map(|item| Feature::deserialize(item).map_err(|e| format!("{role}: geo_url {url:?}: {e}")))
        .collect::<Result<_, _>>()?;
    Ok(Decoded { features, items: items.clone() })
}

fn parse_object(source: &GeoSource, text: &str) -> Result<Value, Diagnostic> {
    let doc: Value =
        serde_json::from_str(text).map_err(|e| unavailable(source, format!("not valid JSON: {e}")))?;
    if !doc.is_object() {
        return Err(unavailable(source, "not a JSON object".to_string()));
    }
    Ok(doc)
}

/// The items of a `json` source: the array at `geo_property`, as the document spells them.
fn json_items<'a>(source: &GeoSource, doc: &'a Value) -> Result<&'a Vec<Value>, String> {
    let (role, url) = (source.role, source.url);
    let path = source.property.ok_or_else(|| crate::refuse!("a json geo source with no geo_property, which Vega fits to NaN and draws as NaN paths, is not supported"))?;
    path.split('.')
        .try_fold(doc, |value, key| value.get(key))
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{role}: geo_url {url:?} has no array at {path:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SQUARE: &str = r#"{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"k":1},
        "geometry":{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[1,0],[0,0]]]}}]}"#;

    fn source<'a>(property: Option<&'a str>) -> GeoSource<'a> {
        GeoSource { role: "geoshape", name: "", url: "u", format_type: "json", feature: None, property }
    }

    fn docs(text: &str) -> Geometries {
        Geometries::from([("u".to_string(), Arc::from(text))])
    }

    fn entry(tag: u8, size: usize) -> (Key, usize, Arc<Decoded>) {
        let key = ([tag; 32], "json".to_string(), None, None);
        (key, size, Arc::new(Decoded { features: vec![], items: vec![] }))
    }

    #[test]
    fn the_same_bytes_decode_once_whatever_the_url() {
        let text = SQUARE.replace("\"k\":1", "\"k\":\"same-bytes-test\"");
        let src = source(Some("features"));
        let first = decode(&src, &docs(&text)).unwrap();
        let other_url = GeoSource { url: "elsewhere", ..source(Some("features")) };
        let second =
            decode(&other_url, &Geometries::from([("elsewhere".to_string(), Arc::from(text.as_str()))])).unwrap();
        assert!(Arc::ptr_eq(&first, &second));
        assert_eq!(first.features.len(), 1);
    }

    #[test]
    fn other_bytes_or_other_reader_parameters_decode_afresh() {
        let text = SQUARE.replace("\"k\":1", "\"k\":\"fresh-test\"");
        let src = source(Some("features"));
        let base = decode(&src, &docs(&text)).unwrap();
        let edited = decode(&src, &docs(&text.replace("fresh-test", "fresh-test2"))).unwrap();
        assert!(!Arc::ptr_eq(&base, &edited));
        let wrapped = format!(r#"{{"outer":{text}}}"#);
        let by_path = GeoSource { property: Some("outer.features"), ..source(None) };
        let nested = decode(&by_path, &docs(&wrapped)).unwrap();
        assert!(!Arc::ptr_eq(&base, &nested));
    }

    #[test]
    fn an_unreadable_document_is_a_coded_error_and_is_not_kept() {
        let src = GeoSource { name: "us-states", ..source(Some("features")) };
        for (text, reason) in [("nope", "not valid JSON"), ("[1]", "not a JSON object")] {
            let err = decode(&src, &docs(text)).err().unwrap();
            assert_eq!(err.code, "ERR-GEO-SOURCE-UNAVAILABLE");
            assert_eq!(err.params["source"], "us-states");
            assert!(err.params["reason"].as_str().unwrap().starts_with(reason), "{err}");
        }
    }

    #[test]
    fn a_topology_decodes_and_a_wrong_one_is_named_by_what_is_wrong() {
        let topology = GeoSource { format_type: "topojson", feature: Some("shapes"), ..source(None) };
        let ok = r#"{"type":"Topology","transform":{"scale":[1,1],"translate":[0,0]},"arcs":[[[0,0],[2,0],[0,2],[-2,0],[0,-2]]],
            "objects":{"shapes":{"type":"GeometryCollection","geometries":[{"type":"Polygon","arcs":[[0]],"id":"topology-decode-test"}]}}}"#;
        assert_eq!(decode(&topology, &docs(ok)).unwrap().features.len(), 1);

        let err = decode(&topology, &docs(r#"{"type":"Topology","arcs":"x","objects":{}}"#)).err().unwrap();
        assert!(err.to_string().contains("geoshape: geo_url \"u\""), "{err}");
        let err = decode(&topology, &docs("{")).err().unwrap();
        assert_eq!(err.code, "ERR-GEO-SOURCE-UNAVAILABLE");
    }

    #[test]
    fn a_missing_document_names_the_url() {
        let err = decode(&source(Some("features")), &Geometries::new()).err().unwrap();
        assert!(err.to_string().contains("no document for geo_url \"u\""), "{err}");
    }

    #[test]
    fn the_cache_evicts_the_least_recently_used_to_stay_under_its_cap() {
        let mut cache = Cache::new(10);
        let (a, b, c) = (entry(1, 4), entry(2, 4), entry(3, 4));
        cache.insert(a.0.clone(), a.1, a.2.clone());
        cache.insert(b.0.clone(), b.1, b.2.clone());
        assert!(cache.get(&a.0).is_some());
        cache.insert(c.0.clone(), c.1, c.2.clone());
        assert!(cache.get(&b.0).is_none(), "b was the least recently used");
        assert!(cache.get(&a.0).is_some() && cache.get(&c.0).is_some());
        assert_eq!(cache.bytes, 8);
    }

    #[test]
    fn a_document_over_the_cap_is_not_kept() {
        let mut cache = Cache::new(10);
        let big = entry(9, 11);
        cache.insert(big.0.clone(), big.1, big.2);
        assert!(cache.get(&big.0).is_none());
        assert_eq!(cache.bytes, 0);
    }

    #[test]
    fn reinserting_a_key_does_not_double_count_its_bytes() {
        let mut cache = Cache::new(10);
        let a = entry(1, 6);
        cache.insert(a.0.clone(), a.1, a.2.clone());
        cache.insert(a.0.clone(), a.1, a.2.clone());
        assert_eq!(cache.bytes, 6);
        assert_eq!(cache.entries.len(), 1);
    }
}
