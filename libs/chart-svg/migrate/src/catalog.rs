//! The retained grammar versions: the manifest and the frozen schema files.
//!
//! The schema files are package data read from where Python's catalog reads
//! them, so there is one copy. The manifest's DEV entry is the *current*
//! grammar every recognition ends at: `dev.json`, the live grammar a release
//! would freeze, generated from the models (`dbt-charts/scripts/gen_dev_grammar.py`).

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

const DEV: &str = include_str!(concat!(env!("MIGRATE_SCHEMAS"), "/dev.json"));

const MANIFEST: &str = include_str!(concat!(env!("MIGRATE_SCHEMAS"), "/manifest.json"));

/// The retained versions, oldest first: the released ones, then the DEV entry
/// (no release date), whose grammar is `dev.json`.
pub fn entries() -> &'static [Entry] {
    static CELL: OnceLock<Vec<Entry>> = OnceLock::new();
    CELL.get_or_init(|| {
        let manifest: Json = serde_json::from_str(MANIFEST).unwrap_or(Json::Null);
        let list = manifest.get("schemas").and_then(Json::as_array);
        list.into_iter()
            .flatten()
            .filter(|e| {
                matches!(
                    e.get("status").and_then(Json::as_str),
                    Some("RELEASED" | "DEV")
                )
            })
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

/// The grammar of `version` (cross-field rules removed): a frozen file, or
/// `dev.json` for the DEV entry.
pub fn grammar(version: &str) -> Option<&'static Schema> {
    static CELLS: [OnceLock<&'static Schema>; FILES.len()] =
        [const { OnceLock::new() }; FILES.len()];
    if version == current().version {
        static DEV_CELL: OnceLock<&'static Schema> = OnceLock::new();
        return Some(*DEV_CELL.get_or_init(|| Schema::leak(DEV)));
    }
    let i = FILES.iter().position(|(v, _)| *v == version)?;
    let (_, text) = FILES.get(i)?;
    Some(*CELLS.get(i)?.get_or_init(|| Schema::leak(text)))
}

/// The DEV entry: what a migrated document must satisfy.
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
