use crate::{FontBook, Measure};

fn fonts() -> FontBook {
    let dir = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../src/dbt_charts/core/render/fonts"
    );
    FontBook::load_dir(dir).expect("shipped fonts load")
}

#[test]
fn the_word_floor_skips_urls_and_opaque_tokens() {
    let fonts = fonts();
    let measure = Measure { fonts: &fonts };
    let word = fonts.measure("word", "Inter", 11.0).unwrap();
    let opaque = "x".repeat(41);
    let text = format!("https://example.com/a/very/long/path word {opaque}");
    assert_eq!(measure.widest_token(&text, "Inter", 11.0).unwrap(), word);
    assert_eq!(measure.widest_token("", "Inter", 11.0).unwrap(), 0.0);
}

#[test]
fn the_row_number_column_fits_the_digits_or_the_header_plus_padding() {
    let fonts = fonts();
    let measure = Measure { fonts: &fonts };
    let digits = fonts.measure("99999", "Inter", 11.0).unwrap();
    let header = fonts.measure("Rank of the row", "Inter", 11.0).unwrap();
    let header_digits = fonts.measure(&"9".repeat(15), "Inter", 11.0).unwrap();
    assert_eq!(
        measure
            .row_number_column("#", 12345, "Inter", "Inter", 11.0, 8.0)
            .unwrap(),
        digits + 16.0
    );
    assert_eq!(
        measure
            .row_number_column("Rank of the row", 9, "Inter", "Inter", 11.0, 8.5)
            .unwrap(),
        header.max(header_digits) + 16.0
    );
}

#[test]
fn a_page_slot_widens_by_each_digit_past_two() {
    let fonts = fonts();
    let measure = Measure { fonts: &fonts };
    let (two, four) = (
        measure.page_slot("12", "Inter", 11.0, 20.0).unwrap(),
        measure.page_slot("1234", "Inter", 11.0, 20.0).unwrap(),
    );
    assert_eq!(measure.page_slot("00", "Inter", 11.0, 20.0).unwrap(), 20.0);
    assert!(two >= 20.0 && four > two);
}

#[test]
fn a_column_demands_its_p95_cell_plus_padding() {
    let fonts = fonts();
    let measure = Measure { fonts: &fonts };
    let texts: Vec<String> = (1..=20).map(|n| "9".repeat(n)).collect();
    let widest_of_95 = fonts.measure(&texts[19], "Inter", 11.0).unwrap();
    let demand = measure
        .column_demand(texts.iter().map(|t| (t.as_str(), "Inter")), 11.0, 8.0)
        .unwrap();
    assert_eq!(demand, widest_of_95 + 16.0);
    assert_eq!(measure.column_demand([], 11.0, 8.0).unwrap(), 16.0);
}

#[test]
fn serif_measures_under_the_authored_family_not_the_web_alias() {
    use crate::stand_in_family;
    assert_eq!(
        stand_in_family("'Source Serif 4 Web', 'Source Serif 4', Georgia, serif"),
        "'Source Serif 4', Georgia, serif"
    );
    assert_eq!(stand_in_family("Inter Variable, sans-serif"), "Inter");
}

#[test]
fn label_widths_cap_per_label_and_find_the_widest() {
    let fonts = fonts();
    let measure = Measure { fonts: &fonts };
    let labels = ["a".to_string(), "a much wider label".to_string()];
    let (narrow, wide) = (
        fonts.measure(&labels[0], "Inter", 11.0).unwrap(),
        fonts.measure(&labels[1], "Inter", 11.0).unwrap(),
    );
    assert_eq!(measure.capped_widths(&labels, "Inter", 11.0, narrow + 1.0).unwrap(), [narrow, narrow + 1.0]);
    assert_eq!(measure.widest(&labels, "Inter", 11.0).unwrap(), wide);
    assert_eq!(measure.widest(&[], "Inter", 11.0).unwrap(), 0.0);
    assert_eq!(measure.widest_shaped(&[], "Inter", 11.0).unwrap(), None);
    let shaped = measure.widest_shaped(&labels, "Inter", 11.0).unwrap().unwrap();
    assert_eq!(shaped, fonts.measure_shaped(&labels[1], "Inter", 11.0).unwrap());
}
