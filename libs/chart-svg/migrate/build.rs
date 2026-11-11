//! Locates the grammar schemas `catalog.rs` embeds and writes their embed list.
//! They are package data of `dbt-charts` (one copy); where they sit relative to
//! this crate depends on the checkout (monorepo, or an sdist, which roots
//! `dbt-charts` at the top). The list is every manifest entry with a `file`, so
//! freezing a release needs no edit here.

use std::fmt::Write as _;
use std::path::PathBuf;

const CANDIDATES: [&str; 2] = [
    "../../../dbt-charts/src/dbt_charts/data/schemas/yaml",
    "../../../src/dbt_charts/data/schemas/yaml",
];

fn main() {
    let manifest =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"));
    let found = CANDIDATES
        .iter()
        .map(|c| manifest.join(c))
        .find(|p| p.join("manifest.json").is_file());
    let Some(dir) = found else {
        eprintln!("the frozen schemas (schemas/yaml/manifest.json) were not found at {CANDIDATES:?} from {manifest:?}");
        std::process::exit(1);
    };
    let text = std::fs::read_to_string(dir.join("manifest.json")).expect("manifest.json reads");
    let parsed: serde_json::Value = serde_json::from_str(&text).expect("manifest.json parses");
    let mut files = String::from("const FILES: &[(&str, &str)] = &[\n");
    for entry in parsed["schemas"]
        .as_array()
        .expect("manifest lists schemas")
    {
        let (Some(version), Some(file)) = (entry["version"].as_str(), entry["file"].as_str())
        else {
            continue;
        };
        let path = dir.join(file).display().to_string();
        writeln!(files, "    ({version:?}, include_str!({path:?})),").expect("writes to a String");
    }
    files.push_str("];\n");
    let out = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    std::fs::write(out.join("schemas.rs"), files).expect("OUT_DIR is writable");
    println!("cargo:rustc-env=MIGRATE_SCHEMAS={}", dir.display());
    println!("cargo:rerun-if-changed={}", dir.display());
}
