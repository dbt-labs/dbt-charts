//! Locates the frozen grammar schemas `catalog.rs` embeds. They are package data
//! of `dbt-charts` (one copy); where they sit relative to this crate depends on
//! the checkout (monorepo, or an sdist, which roots `dbt-charts` at the top).

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
    println!("cargo:rustc-env=MIGRATE_SCHEMAS={}", dir.display());
    println!("cargo:rerun-if-changed={}", dir.display());
}
