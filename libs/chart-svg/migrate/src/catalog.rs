//! The retained grammar versions: the manifest and the frozen schema files.
//!
//! The schema files are package data read from where Python's catalog reads
//! them, so there is one copy. The newest file embedded here is the *current*
//! grammar every recognition ends at; a not-yet-frozen DEV boundary needs its
//! file embedded before its version module can be registered.

use serde_json::Value as Json;
use std::sync::OnceLock;

use crate::schema::Schema;

pub type Date = (i32, u32, u32);

pub struct Entry {
    pub version: String,
    pub released_at: Option<Date>,
    pub predecessor: Option<String>,
}

macro_rules! schemas {
    ($($v:literal),*) => {
        const FILES: &[(&str, &str)] = &[$(($v, include_str!(concat!(
            env!("MIGRATE_SCHEMAS"), "/", $v, ".json")))),*];
    };
}
schemas!("0.3.0", "0.3.1", "0.4.0", "0.5.0", "0.6.0", "0.7.0", "0.9.0", "0.10.0", "0.11.0");

const MANIFEST: &str = include_str!(concat!(env!("MIGRATE_SCHEMAS"), "/manifest.json"));

/// Released versions, oldest first. The unreleased DEV entry has no grammar and is left out.
pub fn entries() -> &'static [Entry] {
    static CELL: OnceLock<Vec<Entry>> = OnceLock::new();
    CELL.get_or_init(|| {
        let manifest: Json = serde_json::from_str(MANIFEST).unwrap_or(Json::Null);
        let list = manifest.get("schemas").and_then(Json::as_array);
        list.into_iter()
            .flatten()
            .filter(|e| e.get("status").and_then(Json::as_str) == Some("RELEASED"))
            .filter_map(|e| {
                let text = |k: &str| e.get(k).and_then(Json::as_str).map(str::to_string);
                Some(Entry {
                    version: text("version")?,
                    released_at: text("released_at").and_then(|d| parse_date(&d)),
                    predecessor: text("predecessor"),
                })
            })
            .collect()
    })
}

pub fn parse_date(text: &str) -> Option<Date> {
    let mut parts = text.splitn(3, '-');
    let y = parts.next()?.parse().ok()?;
    let m = parts.next()?.parse().ok()?;
    let d = parts.next()?.parse().ok()?;
    Some((y, m, d))
}

/// The frozen grammar of `version` (cross-field rules removed).
pub fn grammar(version: &str) -> Option<&'static Schema> {
    static CELLS: [OnceLock<&'static Schema>; FILES.len()] =
        [const { OnceLock::new() }; FILES.len()];
    let i = FILES.iter().position(|(v, _)| *v == version)?;
    let (_, text) = FILES.get(i)?;
    Some(*CELLS.get(i)?.get_or_init(|| Schema::leak(text)))
}

/// The newest embedded grammar: what a migrated document must satisfy.
pub fn current() -> &'static Entry {
    static EMPTY: Entry = Entry {
        version: String::new(),
        released_at: None,
        predecessor: None,
    };
    entries().last().unwrap_or(&EMPTY)
}

pub fn current_grammar() -> Option<&'static Schema> {
    grammar(&current().version)
}
