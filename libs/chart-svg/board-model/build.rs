//! Generates `names.rs` and `parity_models.rs`.
//!
//! `parity_models.rs` lists every `#[derive(Model)]` struct under `src` for the schema parity gate
//! (`tests/it/schema_parity`), so a new port is compared with no edit to any shared file.
//!
//! `names.rs`: the closed name lists that come from package data.
//!
//! Theme and palette names, the `category[N]` slot tokens and the warning codes are read from the
//! `dbt-charts` defaults (located as `migrate/build.rs` locates its schemas) and from
//! `diagnostic/codes/*.yaml`, by the rules `compile/models/schema_names.py` derives them with.
//! Adding a palette or theme YAML needs no Rust edit. The constants below are Python constants the
//! rules read, not data; `tests/it/name_lists.rs` pins each against Python.

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

const DEFAULTS: [&str; 2] = [
    "../../../dbt-charts/src/dbt_charts/core/defaults",
    "../../../src/dbt_charts/core/defaults",
];

/// `PALETTE_FAMILIES`, in Python's scan order (a later family wins a repeated name).
const FAMILIES: [&str; 6] = [
    "sequential",
    "diverging",
    "categorical",
    "scaffold",
    "tone",
    "pattern",
];
/// `colors.VARIANT_WORDS`.
const VARIANT_WORDS: &[&str] = &["dark", "light", "pale", "deep"];
/// `schema_names._pattern_roles`.
const PATTERN_ROLES: &[&str] = &[
    "pattern_category",
    "pattern_sequential",
    "pattern_diverging",
];
/// `palette.HARD_FAIL_PALETTE_NAMES`.
const HARD_FAIL_PALETTE_NAMES: &[&str] = &["hsv", "jet", "rainbow"];
/// The keys of `palette.PALETTE_WARN_ALIASES`.
const PALETTE_WARN_ALIASES: &[&str] = &["RdYlGn", "parula"];
/// `codes_retired.RETIRED_WARNING_CODES`.
const RETIRED_WARNING_CODES: &[&str] = &[
    "WARN-FANOUT-RISK",
    "WARN-H1-BODY-NO-TITLE",
    "WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER",
    "WARN-PIE-TOO-MANY-SEGMENTS",
    "WARN-REDUNDANT-AUTHORED-DEFAULT",
    "WARN-REDUNDANT-AUTHORED-LABEL",
    "WARN-TABLE-CRAMPED",
];
/// `schema_names.VEGA_SCHEME_NAMES`.
const VEGA_SCHEME_NAMES: &[&str] = &[
    "accent",
    "bluegreen",
    "blueorange",
    "bluepurple",
    "blues",
    "brownbluegreen",
    "browns",
    "category10",
    "category20",
    "category20b",
    "category20c",
    "cividis",
    "dark2",
    "darkblue",
    "darkgold",
    "darkgreen",
    "darkmulti",
    "darkred",
    "goldgreen",
    "goldorange",
    "goldred",
    "greenblue",
    "greens",
    "greys",
    "inferno",
    "lightgreyred",
    "lightgreyteal",
    "lightmulti",
    "lightorange",
    "lighttealblue",
    "magma",
    "observable10",
    "orangered",
    "oranges",
    "paired",
    "pastel1",
    "pastel2",
    "pinkyellowgreen",
    "plasma",
    "purpleblue",
    "purplebluegreen",
    "purplegreen",
    "purpleorange",
    "purplered",
    "purples",
    "rainbow",
    "redblue",
    "redgrey",
    "redpurple",
    "reds",
    "redyellowblue",
    "redyellowgreen",
    "set1",
    "set2",
    "set3",
    "sinebow",
    "spectral",
    "tableau10",
    "tableau20",
    "tealblues",
    "teals",
    "turbo",
    "viridis",
    "warmgreys",
    "yellowgreen",
    "yellowgreenblue",
    "yelloworangebrown",
    "yelloworangered",
];

fn main() {
    parity_models();
    let manifest =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"));
    let defaults = DEFAULTS
        .iter()
        .map(|c| manifest.join(c))
        .find(|p| p.join("themes").is_dir() && p.join("palettes").is_dir())
        .unwrap_or_else(|| {
            panic!("the dbt-charts defaults were not found at {DEFAULTS:?} from {manifest:?}")
        });
    let codes = manifest.join("../diagnostic/codes");
    for dir in [
        defaults.join("themes"),
        defaults.join("palettes"),
        codes.clone(),
    ] {
        println!("cargo:rerun-if-changed={}", dir.display());
    }

    let palettes = palette_index(&defaults.join("palettes"));
    let of_family = |f: &str| -> BTreeSet<String> {
        palettes
            .iter()
            .filter(|(_, v)| v.as_str() == f)
            .map(|(k, _)| k.clone())
            .collect()
    };
    let (tones, patterns, categorical) = (
        of_family("tone"),
        of_family("pattern"),
        of_family("categorical"),
    );

    let themes: Vec<String> = stems(&defaults.join("themes"))
        .into_iter()
        .filter(|n| !n.starts_with('_') && !n.starts_with("diagnostics-"))
        .collect();
    let mut palette_names: BTreeSet<String> = palettes.keys().cloned().collect();
    for name in &categorical {
        palette_names.extend(VARIANT_WORDS.iter().map(|t| format!("{name}.{t}")));
    }
    let stops: BTreeSet<String> = palette_names
        .iter()
        .filter(|n| !tones.contains(*n) && !patterns.contains(*n))
        .cloned()
        .collect();
    let pattern: BTreeSet<String> = patterns
        .iter()
        .cloned()
        .chain(PATTERN_ROLES.iter().map(|s| s.to_string()))
        .collect();
    let scale: BTreeSet<String> = stops
        .iter()
        .cloned()
        .chain(VEGA_SCHEME_NAMES.iter().map(|s| s.to_string()))
        .filter(|n| {
            !HARD_FAIL_PALETTE_NAMES.contains(&n.as_str())
                && !PALETTE_WARN_ALIASES.contains(&n.as_str())
        })
        .collect();
    let slots = category_slots(&defaults, &themes);
    let (warning, chart_warning) = warning_codes(&codes);

    let mut out = String::new();
    let lists: [(&str, &str, Vec<String>); 11] = [
        ("ThemeName", "A built-in theme: the names `theme:` and `extends:` take.", themes),
        ("PaletteName", "A built-in palette, or one of its tonal steps: the names `style.palettes` takes.", palette_names.into_iter().collect()),
        ("StopsPaletteName", "A palette that resolves to stops: the names a `palette:` field takes.", stops.into_iter().collect()),
        ("PatternPaletteName", "A pattern palette or a theme's pattern role: the names `style.pattern.palette` takes.", pattern.into_iter().collect()),
        ("ScalePaletteName", "A stops palette or a Vega scheme: the names `ScaleTargetConfig.palette` takes.", scale.into_iter().collect()),
        ("CategorySlotToken", "`category[1]` to `category[N]`, N the widest palette a built-in theme binds to `category`.", (1..=slots).map(|i| format!("category[{i}]")).collect()),
        ("WarningCode", "A warning code, retired ones included: a board that suppresses one keeps loading.", warning),
        ("ChartWarningCode", "A warning code a chart-scoped `warnings_ignore` can name.", chart_warning),
        ("NumberLocaleName", "A d3-format locale the engine vendors.", display_text::locale::NUMBER_LOCALES.iter().map(|s| s.to_string()).collect()),
        ("TimeLocaleName", "A d3-time-format locale the engine vendors.", display_text::locale::TIME_LOCALES.iter().map(|s| s.to_string()).collect()),
        ("TimeZoneName", "An IANA zone of the bundled time zone database: the names `regional.time_zone` takes.", {
            let mut zones: Vec<String> = jiff_tzdb::available().map(str::to_string).collect();
            zones.sort();
            zones
        }),
    ];
    for (name, doc, values) in lists {
        enum_of(&mut out, name, doc, &values);
    }
    for (name, values) in [
        ("VARIANT_WORDS", VARIANT_WORDS),
        ("PATTERN_ROLES", PATTERN_ROLES),
        ("HARD_FAIL_PALETTE_NAMES", HARD_FAIL_PALETTE_NAMES),
        ("PALETTE_WARN_ALIASES", PALETTE_WARN_ALIASES),
        ("VEGA_SCHEME_NAMES", VEGA_SCHEME_NAMES),
        ("RETIRED_WARNING_CODES", RETIRED_WARNING_CODES),
    ] {
        let _ = writeln!(out, "/// The Python constant `{name}` the lists are derived with.\npub const {name}: &[&str] = &{values:?};");
    }
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    std::fs::write(out_dir.join("names.rs"), out).expect("write names.rs");

    let mut sources = String::from("pub const THEME_SOURCES: &[(&str, &str)] = &[\n");
    for stem in stems(&defaults.join("themes")) {
        let path = defaults.join("themes").join(format!("{stem}.yaml"));
        let _ = writeln!(
            sources,
            "    ({stem:?}, include_str!({:?})),",
            path.display().to_string()
        );
    }
    sources.push_str("];\n");
    std::fs::write(out_dir.join("theme_sources.rs"), sources).expect("write theme_sources.rs");

    let mut palettes = String::from("pub const PALETTE_SOURCES: &[(&str, &str)] = &[\n");
    for family in FAMILIES {
        let dir = defaults.join("palettes").join(family);
        if !dir.is_dir() {
            continue;
        }
        for yml in files(&dir, "yml") {
            let _ = writeln!(
                palettes,
                "    ({family:?}, include_str!({:?})),",
                yml.display().to_string()
            );
        }
    }
    palettes.push_str("];\n");
    std::fs::write(out_dir.join("palette_sources.rs"), palettes).expect("write palette_sources.rs");
}

fn enum_of(out: &mut String, name: &str, doc: &str, values: &[String]) {
    let mut seen = BTreeSet::new();
    let _ = writeln!(out, "string_enum!(\n    /// {doc}\n    {name} {{");
    for v in values {
        let ident: String = v
            .replace('+', "Plus")
            .replace("-0", "Minus0")
            .replace("-1", "Minus1")
            .split(|c: char| !c.is_ascii_alphanumeric())
            .map(|p| {
                let mut cs = p.chars();
                cs.next()
                    .map(|f| f.to_ascii_uppercase().to_string() + cs.as_str())
                    .unwrap_or_default()
            })
            .collect();
        assert!(
            ident.starts_with(|c: char| c.is_ascii_alphabetic()),
            "{name}: {v:?} is no identifier"
        );
        assert!(
            seen.insert(ident.clone()),
            "{name}: {v:?} collides on {ident}"
        );
        let _ = writeln!(out, "        {ident} = {v:?},");
    }
    let _ = writeln!(out, "    }}\n);");
}

fn load(path: &Path) -> Value {
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    yaml::parse_node(&text)
        .unwrap_or_else(|d| {
            panic!(
                "{}: {:?}",
                path.display(),
                d.iter().map(|x| x.message()).collect::<Vec<_>>()
            )
        })
        .to_json()
}

fn files(dir: &Path, ext: &str) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("{}: {e}", dir.display()))
        .map(|e| e.expect("directory entry").path())
        .filter(|p| p.extension().is_some_and(|x| x == ext))
        .collect();
    v.sort();
    v
}

fn stems(dir: &Path) -> Vec<String> {
    let mut v: Vec<String> = files(dir, "yaml")
        .iter()
        .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
        .collect();
    v.sort();
    v
}

/// `build_palette_index`: palette `name:` to family, from `<family>/*.yml`.
fn palette_index(dir: &Path) -> BTreeMap<String, String> {
    let mut idx = BTreeMap::new();
    for family in FAMILIES {
        let fam = dir.join(family);
        if !fam.is_dir() {
            continue;
        }
        for yml in files(&fam, "yml") {
            if let Some(name) = load(&yml).get("name").and_then(Value::as_str) {
                idx.insert(name.to_string(), family.to_string());
            }
        }
    }
    idx
}

/// The widest palette any built-in theme binds to `category`, through `extends:`.
fn category_slots(defaults: &Path, themes: &[String]) -> usize {
    let binding = |theme: &str| -> String {
        let mut at = theme.to_string();
        loop {
            let data = load(&defaults.join("themes").join(format!("{at}.yaml")));
            if let Some(p) = data
                .pointer("/style/palettes/category")
                .and_then(Value::as_str)
            {
                return p.to_string();
            }
            at = data
                .get("extends")
                .and_then(Value::as_str)
                .unwrap_or_else(|| panic!("theme {at}: no category palette and no `extends:`"))
                .to_string();
        }
    };
    let colors = |palette: &str| -> usize {
        for yml in FAMILIES.iter().flat_map(|f| {
            let d = defaults.join("palettes").join(f);
            if d.is_dir() {
                files(&d, "yml")
            } else {
                Vec::new()
            }
        }) {
            let data = load(&yml);
            if data.get("name").and_then(Value::as_str) == Some(palette) {
                let colors = data
                    .get("colors")
                    .unwrap_or_else(|| panic!("palette {palette}: no `colors:`"));
                return colors
                    .as_array()
                    .map(Vec::len)
                    .or_else(|| colors.as_object().map(|o| o.len()))
                    .expect("colors is a list or map");
            }
        }
        panic!("category role bound to unknown palette {palette}");
    };
    themes
        .iter()
        .map(|t| colors(&binding(t)))
        .max()
        .expect("at least one built-in theme")
}

/// Warning codes from `diagnostic/codes/<domain>.yaml`: all, and those a chart can carry (a
/// `query`-domain diagnostic never carries a chart id).
fn warning_codes(dir: &Path) -> (Vec<String>, Vec<String>) {
    let (mut all, mut chart): (BTreeSet<String>, BTreeSet<String>) =
        (BTreeSet::new(), BTreeSet::new());
    for yml in files(dir, "yaml") {
        let domain = yml
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        // `yaml` takes a mapping root; the code files are lists.
        let text = std::fs::read_to_string(&yml).expect("read codes file");
        let wrapped: String = text.lines().map(|l| format!("  {l}\n")).collect();
        let node = yaml::parse_node(&format!("codes:\n{wrapped}")).expect("codes file parses");
        for entry in node.to_json()["codes"]
            .as_array()
            .expect("codes file is a list")
        {
            if entry.get("level").and_then(Value::as_str) == Some("warning") {
                let code = entry
                    .get("code")
                    .and_then(Value::as_str)
                    .expect("code")
                    .to_string();
                if domain != "query" {
                    chart.insert(code.clone());
                }
                all.insert(code);
            }
        }
    }
    let retired = RETIRED_WARNING_CODES.iter().map(|s| s.to_string());
    all.extend(retired.clone());
    chart.extend(retired);
    (all.into_iter().collect(), chart.into_iter().collect())
}

/// Whether `text` (the inside of one `derive(...)`) derives `Model`.
fn derives_model(text: &str) -> bool {
    text.split(|c: char| !c.is_alphanumeric() && c != '_')
        .any(|w| w == "Model")
}

/// The `#[derive(.., Model)]` structs of one source file, in order.
fn model_structs(text: &str) -> Vec<String> {
    const ITEMS: [&str; 10] = [
        "pub ", "impl", "fn ", "const ", "use ", "struct ", "enum ", "mod ", "static ", "type ",
    ];
    let mut found = Vec::new();
    let (mut armed, mut derive) = (false, None::<String>);
    for line in text.lines() {
        if let Some(open) = &mut derive {
            open.push_str(line);
            if line.contains(")]") {
                armed |= derives_model(open);
                derive = None;
            }
            continue;
        }
        if line.starts_with("#[derive(") {
            if line.contains(")]") {
                armed |= derives_model(line);
            } else {
                derive = Some(line.to_string());
            }
        } else if let Some(rest) = line.strip_prefix("pub struct ") {
            if armed {
                let name: String = rest
                    .chars()
                    .take_while(|c| c.is_alphanumeric() || *c == '_')
                    .collect();
                found.push(name);
            }
            armed = false;
        } else if line.starts_with('}') || ITEMS.iter().any(|i| line.starts_with(i)) {
            armed = false;
        }
    }
    found
}

fn rust_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(dir)
        .expect("read src")
        .map(|e| e.expect("dir entry").path())
        .collect();
    entries.sort();
    for p in entries {
        if p.is_dir() {
            rust_files(&p, out);
        } else if p.extension().is_some_and(|e| e == "rs") {
            out.push(p);
        }
    }
}

/// Writes `parity_models.rs`: a `use` per model and `all()`, the registry the schema parity gate
/// walks. Model names are unique across the crate.
fn parity_models() {
    let src =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"))
            .join("src");
    println!("cargo:rerun-if-changed={}", src.display());
    let mut files = Vec::new();
    rust_files(&src, &mut files);
    let mut ported: BTreeMap<String, String> = BTreeMap::new();
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        let rel = path
            .strip_prefix(&src)
            .expect("under src")
            .with_extension("");
        let module = std::iter::once("board_model".to_string())
            .chain(
                rel.components()
                    .map(|c| c.as_os_str().to_string_lossy().into_owned())
                    .filter(|p| p != "mod"),
            )
            .collect::<Vec<_>>()
            .join("::");
        let text = std::fs::read_to_string(&path).expect("read source");
        for name in model_structs(&text) {
            assert!(
                ported.insert(name.clone(), module.clone()).is_none(),
                "two Rust models named {name}"
            );
        }
    }
    let mut out = String::new();
    for (name, module) in &ported {
        let _ = writeln!(out, "use {module}::{name};");
    }
    out.push_str(
        "\ntype Build = fn() -> Result<model_core::schema::Schema, model_core::schema::SchemaError>;\n\npub fn all() -> Vec<(&'static str, Build)> {\n    vec![\n",
    );
    for name in ported.keys() {
        let _ = writeln!(
            out,
            "        (\"{name}\", model_core::schema::Schema::of::<{name}>),"
        );
    }
    out.push_str("    ]\n}\n");
    let dest = PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"))
        .join("parity_models.rs");
    std::fs::write(dest, out).expect("write parity_models.rs");
}
