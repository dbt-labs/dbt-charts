//! Expected advances are HarfBuzz's, the browser's shaper, at a font scale of
//! 4096 × upem so no rounding to the scale hides a difference; vertical metrics are the faces' own (fontTools: OS/2 typo metrics when
//! USE_TYPO_METRICS is set, else hhea). Wrap and truncate run in Source Code
//! Pro, whose every glyph, the ellipsis included, advances 0.6 em: 6 px at 10 px.

use super::*;

fn book() -> FontBook {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../dbt-charts/src/dbt_charts/core/render/fonts");
    FontBook::load_dir(&dir).expect("served faces")
}

fn font(family: &str, size: f64, weight: f32) -> Font<'_> {
    Font {
        family,
        size,
        weight,
        italic: false,
    }
}

const INTER: &str = "'Inter Variable', Inter, system-ui, sans-serif";
const MONO: &str = "'Source Code Pro', monospace";

fn close(got: f64, want: f64) {
    near(got, want, 1e-9);
}

/// rustybuzz rounds a variable face's kerning deltas to whole font units where
/// HarfBuzz keeps their fractions: half a unit per kerned pair, under 0.01 px
/// for these runs.
fn kerned(got: f64, want: f64) {
    near(got, want, 0.01);
}

fn near(got: f64, want: f64, tolerance: f64) {
    assert!((got - want).abs() <= tolerance, "got {got}, want {want}");
}

#[test]
fn a_run_advances_at_its_painted_weight() {
    let b = book();
    close(
        b.run("dbt Charts", &font(INTER, 11.0, 400.0))
            .unwrap()
            .advance,
        54.48974609375,
    );
    kerned(
        b.run("dbt Charts", &font(INTER, 11.0, 700.0))
            .unwrap()
            .advance,
        56.67267084121704,
    );
}

#[test]
fn a_run_is_kerned() {
    let b = book();
    close(
        b.run(
            "50,000",
            &font("'dbt Sans Tabular', Inter, sans-serif", 11.0, 400.0),
        )
        .unwrap()
        .advance,
        38.0595703125,
    );
    close(
        b.run("AVATAR", &font(INTER, 20.0, 400.0)).unwrap().advance,
        74.84375,
    );
}

#[test]
fn a_run_measures_in_its_own_face_not_inter() {
    let b = book();
    close(
        b.run("Revenue", &font(MONO, 12.0, 400.0)).unwrap().advance,
        50.4,
    );
    let serif = Font {
        family: "'Source Serif 4', Georgia, serif",
        size: 12.0,
        weight: 600.0,
        italic: true,
    };
    kerned(b.run("Revenue", &serif).unwrap().advance, 43.3136044921875);
    close(
        b.run("Revenue", &font(INTER, 12.0, 400.0)).unwrap().advance,
        48.99609375,
    );
}

#[test]
fn ascent_and_descent_come_from_the_face() {
    let b = book();
    let inter = b.run("Hg", &font(INTER, 10.0, 400.0)).unwrap();
    close(inter.ascent, 1984.0 / 2048.0 * 10.0);
    close(inter.descent, 494.0 / 2048.0 * 10.0);
    let mono = b.run("", &font(MONO, 10.0, 400.0)).unwrap();
    assert_eq!(mono.advance, 0.0);
    close(mono.ascent, 9.84);
    close(mono.descent, 2.73);
}

#[test]
fn a_character_the_primary_lacks_is_set_in_the_next_face_that_maps_it() {
    let b = book();
    let run = b
        .run(
            "a\u{1F600}a",
            &font("'Inter Variable', 'Noto Emoji', sans-serif", 10.0, 400.0),
        )
        .unwrap();
    close(run.advance, 5.615234375 + 12.6953125 + 5.615234375);
    // Noto Emoji's ascent (1900/2048) is below Inter's; its descent (500/2048) is deeper.
    close(run.ascent, 1984.0 / 2048.0 * 10.0);
    close(run.descent, 500.0 / 2048.0 * 10.0);
}

#[test]
fn a_family_with_no_italic_face_paints_its_upright() {
    let b = book();
    let upright = font("'dbt Sans Tabular'", 11.0, 400.0);
    let slanted = Font {
        italic: true,
        ..upright
    };
    assert_eq!(
        b.run("1,234", &upright).unwrap(),
        b.run("1,234", &slanted).unwrap()
    );
}

#[test]
fn an_unserved_family_measures_in_the_next_served_face_else_in_inter() {
    let b = book();
    let courier = b.run(
        "Revenue",
        &font("'Courier New', 'Source Code Pro', monospace", 12.0, 400.0),
    );
    close(courier.unwrap().advance, 50.4);
    // No served face in the stack: Inter, as production measures it.
    let bare = b.run("Revenue", &font("Courier New", 12.0, 400.0));
    close(bare.unwrap().advance, 48.99609375);
}

#[test]
fn wrap_fills_lines_greedily_by_word() {
    let b = book();
    let f = font(MONO, 10.0, 400.0);
    let lines = |w| b.wrap("alpha  beta\ngamma", &f, w, None).unwrap();
    assert_eq!(
        lines(96.0),
        Wrapped {
            lines: vec!["alpha beta gamma".into()],
            truncated: false
        }
    );
    assert_eq!(lines(60.0).lines, ["alpha beta", "gamma"]);
    assert_eq!(lines(59.0).lines, ["alpha", "beta", "gamma"]);
}

#[test]
fn wrap_breaks_a_long_word_at_a_seam_then_at_a_character() {
    let b = book();
    let f = font(MONO, 10.0, 400.0);
    assert_eq!(
        b.wrap("path/to/some_file", &f, 48.0, None).unwrap().lines,
        ["path/to/", "some_", "file"]
    );
    assert_eq!(
        b.wrap("abcdefghij", &f, 24.0, None).unwrap().lines,
        ["abcd", "efgh", "ij"]
    );
}

#[test]
fn wrap_past_max_lines_truncates_the_rest_with_an_ellipsis() {
    let b = book();
    let f = font(MONO, 10.0, 400.0);
    assert_eq!(
        b.wrap("alpha beta gamma delta", &f, 60.0, Some(2)).unwrap(),
        Wrapped {
            lines: vec!["alpha beta".into(), "gamma del…".into()],
            truncated: true
        }
    );
}

#[test]
fn truncate_keeps_the_longest_prefix_that_fits_with_its_ellipsis() {
    let b = book();
    let f = font(MONO, 10.0, 400.0);
    assert_eq!(
        b.truncate("Revenue", &f, 42.0).unwrap(),
        ("Revenue".into(), false)
    );
    assert_eq!(
        b.truncate("Revenue by region", &f, 54.0).unwrap(),
        ("Revenue…".into(), true)
    );
    assert_eq!(b.truncate("Revenue", &f, 3.0).unwrap(), ("R".into(), true));
}

#[test]
fn the_pieces_of_a_broken_word_rejoin_without_a_space() {
    let b = book();
    let f = font(MONO, 10.0, 400.0);
    let one_line = b.wrap("ab/cdefgh", &f, 30.0, Some(1)).unwrap();
    assert_eq!(one_line.lines, ["ab/c…"]);
    assert!(b.wrap("ab", &f, 30.0, Some(0)).is_err());
}

#[test]
fn the_served_faces_are_the_font_face_rules() {
    let css =
        include_str!("../../../../src/dbt_charts/core/render/fonts/_font_face.css");
    let mut rules: Vec<(String, bool, String)> = css
        .split("@font-face")
        .skip(1)
        .map(|rule| {
            let value = |key: &str| {
                rule.lines()
                    .find_map(|l| l.trim().strip_prefix(key))
                    .map(|v| v.trim_end_matches(';').trim().to_string())
            };
            let family = value("font-family:")
                .expect("family")
                .trim_matches('\'')
                .to_string();
            let italic = value("font-style:").as_deref() == Some("italic");
            let src = value("src:").expect("src");
            let file = src
                .split("/static/fonts/")
                .nth(1)
                .and_then(|f| f.split(".woff2").next());
            (family, italic, file.expect("woff2 file").to_string())
        })
        .collect();
    let mut table: Vec<(String, bool, String)> = PAINTED_FACES
        .iter()
        .map(|(family, italic, file)| {
            (
                family.to_string(),
                *italic,
                file.trim_end_matches(".ttf").to_string(),
            )
        })
        .collect();
    rules.sort();
    table.sort();
    assert_eq!(table, rules);
}

#[test]
fn a_verdict_resolves_the_face_as_the_measurer_production_sums_in() {
    // `FontBook::face_for` (`lib.rs`) reads only the stack's first entry and matches it against
    // the shipped stems (exact, then a prefix with "regular" or "variable", then any prefix, then
    // containment), else the body face: an unserved first entry measures in Inter whatever
    // follows it, and a stem prefix reaches the served family.
    let book = book();
    let advance = |family: &str| {
        book.raw_advance("Wednesday 18", &font(family, 10.0, 400.0))
            .unwrap()
    };
    let inter = advance("Inter Variable");
    let serif = advance("Source Serif 4");
    assert_ne!(inter, serif);
    assert_eq!(advance("Georgia, 'Source Serif 4'"), inter);
    assert_eq!(advance("sans-serif, 'Source Serif 4'"), inter);
    assert_eq!(advance("Inter"), inter);
    assert_eq!(advance("'Source Serif 4', Georgia, serif"), serif);
    assert_eq!(advance("Source Serif"), serif);
    // The ranks: a stem prefix followed by "regular" or "variable" beats any other prefix, and a
    // generic keyword is no family while a fragment of a stem is (containment).
    assert_eq!(
        advance("DBT Sans Tabular"),
        advance("DBTSansTabular-Regular")
    );
    assert_eq!(advance("Source Serif 4"), advance("SourceSerif4Variable"));
    assert_eq!(advance("serif4"), advance("SourceSerif4-Italic"));
    assert_eq!(advance("serif"), inter);
    assert_eq!(advance("system-ui"), inter);
}
