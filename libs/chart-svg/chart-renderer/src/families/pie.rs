//! Pie/donut family: ResolvedPieChart payload → SVG.
//! Evaluates the same math the pie emitter writes as Vega-Lite expressions —
//! mid-angle labels, dark-companion inks, radius = min(w,h)/2·outer_fraction.
//! The standalone wheel lays out through `vega_fit` (label ink pushes the
//! plot margins, as `autosize: fit` does); the attached-table composition
//! keeps its own direct layout, mirroring production's Python SVG composer.
//!
//! `link` (chart-root `ResolvedChart.link`) paints one `<a href>` around each
//! wedge, slice label and center-total text inside its `.role-mark` group —
//! every layer inheriting the shared `href` encoding — with
//! `{{ color }}`/`{{ theta }}` substituted per row (`crate::href`); the total
//! texts take row 0's. `{{ x }}`/`{{ y }}` refuse with production's own error.
//!
//! Unsupported → clear `Err`:
//! - resolved_channels.color.mode == "conditional" present (pie's authored
//!   surface can no longer produce this from a real board; guards a
//!   hand-written or stale resolved-JSON fixture instead — covers both
//!   paint sites the deleted implementation had, slice fill and label ink)

pub mod bind;
pub mod layout;

use std::collections::HashMap;

use measure::{FontBook, Measure};
use print::print;
use serde::Deserialize;
use serde_json::Value;

use crate::common::{Font, FormatSlot, Stroke, StyleChannel, TitleStyle};
use crate::diagnostic::{warn, Diagnostic};
use crate::families::legend::LegendConfig;
use crate::layout::Rect;
use crate::Payload;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    theta: String,
    field_labels: crate::common::FieldLabels,
    #[serde(default)]
    tooltip: Vec<crate::families::tooltip_extra::TooltipField>,
    identity_field: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    title_style: Option<TitleStyle>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    /// Effective chart background — see `BarResolved::background` and
    /// `docs/laws-shared.md#background`.
    background: String,
    palette: Vec<String>,
    dark_companion_stops: Vec<String>,
    /// `(pinned color, ink)` pairs for the `category_colors` pins outside the
    /// palette (`common::category_ink`).
    pin_companions: Vec<(String, String)>,
    /// (#7167) the board's plan for a `series`-mode field when one binds
    /// it; `literal` stays on the palette-by-index status quo (no data
    /// field to bind). `mode == "conditional"` is checked once, at
    /// `render`'s own entry gate, and refused — pie's authored surface can
    /// no longer produce it from a real board.
    #[serde(default)]
    resolved_channels: HashMap<String, StyleChannel>,
    /// Board-wide value→slot bindings for this chart's categorical fields
    /// (#7167) — see `common::category_scale_for`/`category_color`.
    #[serde(default)]
    category_colors: Vec<crate::common::CategoryColorScale>,
    outer_fraction: f64,
    slice_label_indices: Vec<usize>,
    style: PieStyle,
    #[serde(default)]
    total: Option<TotalConfig>,
    /// The resolved legend (`ResolvedLegendStyle`), always carried. Production
    /// hides it outside "direct" label mode (`suppress_legend`), so a visible
    /// one is drawn by `pie/layout.rs`'s wheel.
    legend: LegendConfig,
    #[serde(default)]
    attached_table: Option<Value>,
    #[serde(default)]
    attached_table_placement: Option<String>,
    #[serde(default)]
    attached_table_align: Option<String>,
    #[serde(default)]
    attached_table_width: Option<f64>,
    #[serde(default)]
    attached_table_gap: Option<f64>,
    #[serde(default)]
    hybrid_heading_gap: Option<f64>,
    #[serde(default)]
    wheel_width: Option<f64>,
    #[serde(default)]
    attached_heading: Option<String>,
    #[serde(default)]
    attached_heading_font: Option<Font>,
    #[serde(default)]
    attached_row_indices: Vec<usize>,
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// once per wedge (`click_interactivity.py`); an empty string is no link,
    /// production's `if not chart.link`.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    /// How `link` opens (`ResolvedChart.link_open`).
    link_open: crate::href::LinkOpen,
    /// The slices a held link variable selects.
    #[serde(skip)]
    selection: crate::selected::Selection,
}

impl Resolved {
    fn from_payload(payload: &Payload) -> Result<Self, String> {
        let mut r: Self =
            Deserialize::deserialize(&payload.resolved).map_err(|e| format!("pie payload: {e}"))?;
        let keys: Vec<(&str, &str)> = bind::color_field_of(&r).map(|field| ("color", field)).into_iter().collect();
        r.selection = crate::selected::Selection::of(payload, r.link.as_deref(), &keys)?;
        Ok(r)
    }
}

#[derive(Deserialize)]
struct TotalConfig {
    visible: bool,
    label: Option<String>,
    /// Retired key, present only to be refused.
    format: Option<Value>,
}

#[derive(Deserialize)]
struct TotalStyle {
    value: TotalValueStyle,
    label: TotalSlotStyle,
}

/// The center value's resolved style. Its d3 `format` lives here and nowhere
/// else: `ChartTotal` has no `format` key.
#[derive(Deserialize)]
struct TotalValueStyle {
    font: Font,
    #[serde(default)]
    format: Option<FormatSlot>,
}

#[derive(Deserialize)]
struct TotalSlotStyle {
    font: Font,
}

#[derive(Deserialize)]
struct PieStyle {
    inner_radius: f64,
    slice_mark: SliceMark,
    total_style: TotalStyle,
    /// Width-resolved title font (production's `chart_title_spec` output) —
    /// always preferred over the un-tiered `title_style.font`.
    #[serde(default)]
    title_font: Option<Font>,
    /// The format of the tooltip's value and `Σ` rows -- absent (older
    /// fixtures) or empty means no tooltip aria-labels for this chart, same
    /// gate as bar's own `tooltip_labels` (`chart.style.tooltip_format`).
    #[serde(default)]
    tooltip_format: FormatSlot,
}

#[derive(Deserialize)]
struct SliceMark {
    /// Arc mark opacity, painted on every wedge. A legend symbol would carry
    /// it too (`encode.ts` `symbols` forwards it), so a visible legend refuses it.
    opacity: Option<f64>,
    gap: Option<f64>,
    corner_radius: Option<f64>,
    stroke: Option<Stroke>,
    labels: Option<Labels>,
}

#[derive(Deserialize)]
struct Labels {
    offset: f64,
    line_height: f64,
    font: Font,
    template: String,
}

/// Bind, lay out the payload's own card, forward the diagnostics, print.
pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    draw(&bind::bind(payload)?, fonts)
}

/// A pie over no rows: the empty arc and label layers inside the negotiated frame.
pub fn render_empty(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    draw(&bind::bind_empty(payload)?, fonts)
}

fn draw(bound: &bind::Bound, fonts: &FontBook) -> Result<String, Diagnostic> {
    let cell = Rect { x: 0.0, y: 0.0, width: bound.payload.width, height: bound.payload.height };
    let laid_out = layout::layout(bound, cell, &Measure { fonts })?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
}

#[cfg(test)]
mod tests {
    use super::bind::project_pie_table_rows;
    use super::*;
    use serde_json::json;

    fn make_pie_payload(total: Value) -> Value {
        json!({
            "resolved": {
                "link_open": "page",
                "id": "pie",
                "chart_type": "pie",
                "theta": "revenue",
                "identity_field": "category",
                "title": null,
                "title_style": null,
                "background": "#FAFAFA",
                "palette": ["#3164a3", "#779bc9", "#ad9c7f"],
                "dark_companion_stops": ["#183253", "#3c4d65"], "pin_companions": [],
                "outer_fraction": 0.9,
                "slice_label_indices": [],
                "total": total,
                "attached_table": null,
                "legend": {
                    "visible": false,
                    "position": {"edge": "right", "align": "start", "overlay": false},
                    "direction": "vertical",
                    "label": {"font": {"size": 11.0}},
                    "title": {"font": {"case": "title"}}
                },
                "field_labels": {},
                "style": {
                    "inner_radius": 0.6,
                    "slice_mark": {
                        "gap": 0.02,
                        "corner_radius": 2.0,
                        "stroke": null,
                        "labels": null
                    },
                    "total_style": {
                        "value": {
                            "font": {
                                "family": "Inter",
                                "color": "#222222",
                                "size": 18.0,
                                "weight": "600",
                                "line_height": 1.25
                            }
                        },
                        "label": {
                            "font": {
                                "family": "Inter",
                                "color": "#8A8C8F",
                                "size": 11.0,
                                "weight": "400",
                                "line_height": 1.25
                            }
                        }
                    }
                }
            },
            "rows": [
                {"category": "Electronics", "revenue": 197199.0},
                {"category": "Accessories", "revenue": 152504.0},
                {"category": "Tools", "revenue": 93320.0}
            ],
            "width": 600.0,
            "height": 432.0,
            "background": "#FAFAFA",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    fn make_payload(payload_json: Value) -> Payload {
        crate::test_payload(payload_json)
    }

    /// The `share` cell of each slice value of a pie totaling 100.
    fn share_cells(values: &[f64]) -> Vec<String> {
        let mut payload = make_payload(make_pie_payload(Value::Null));
        payload.rows = values
            .iter()
            .map(|value| {
                let mut row = serde_json::Map::new();
                row.insert("revenue".into(), json!(value));
                row
            })
            .collect();
        let indices: Vec<usize> = (0..values.len()).collect();
        let palette = vec!["#3164a3".to_string()];
        project_pie_table_rows(&payload, &indices, &palette, None, "revenue", values, 100.0, None)
            .expect("rows project")
            .iter()
            .map(|row| row["share"].as_str().expect("share is a string").to_string())
            .collect()
    }

    /// The attached table's `share` cell is the slice label's own `.0%`: d3 rounds the exact
    /// decimal expansion half away from zero, so 0.5 of 100 prints `1%`, never the even `0%`.
    /// Every string is production's `d3_format(".0%")(share)`:
    /// 0.4 `40%`, 0.3 `30%`, 0.2 `20%`, 0.08 `8%`, 0.015 `2%`, 0.005 `1%`,
    /// 0.175 `18%`, 0.025 `3%`, 0.0049 `0%`, 0.0051 `1%`, 0.125 `13%`, 0.375 `38%`.
    /// Each share is the slice value divided by 100 in doubles, as production computes it.
    #[test]
    fn attached_table_share_rounds_like_production_percent() {
        let ladder = [40.0, 30.0, 20.0, 8.0, 1.5, 0.5];
        let neighbors = [
            (17.5, "18%"),
            (2.5, "3%"),
            (0.49, "0%"),
            (0.51, "1%"),
            (12.5, "13%"),
            (37.5, "38%"),
        ];
        let cases: Vec<(f64, &str)> = ladder
            .iter()
            .copied()
            .zip(["40%", "30%", "20%", "8%", "2%", "1%"])
            .chain(neighbors)
            .collect();
        let values: Vec<f64> = cases.iter().map(|(value, _)| *value).collect();
        let shares = share_cells(&values);
        let expected: Vec<&str> = cases.iter().map(|(_, share)| *share).collect();
        assert_eq!(shares, expected);
    }

    /// The share cell prints through the board's number locale, as the slice labels do.
    /// Production under fr-FR (`number_locale_scope` over `house_locale(
    /// number_locale_definition("fr-FR"), ...)`): `d3_format(".0%")(0.005)` is `1\u{202f}%`
    /// and `(0.4)` is `40\u{202f}%`, the locale's percent sign with its narrow no-break space.
    #[test]
    fn attached_table_share_takes_the_board_number_locale() {
        let shares = crate::number_locale::with_number_locale(&crate::number_locale::fr_fr(), || {
            share_cells(&[40.0, 0.5])
        });
        assert_eq!(shares, ["40\u{202f}%", "1\u{202f}%"]);
    }

    /// Same law as `bar.rs`'s canvas-rect test — see
    /// `pie.rs::Resolved::background`.
    #[test]
    fn canvas_rect_reads_the_chart_own_background_not_the_board_background() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["background"] = json!("#d9a45b");
        p["background"] = json!("#ffffff");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("<rect width=\"600\" height=\"432\" fill=\"#d9a45b\"/>"), "{svg}");
    }

    /// With no color channel every wedge paints one constant `palette[0]`. Palette-by-index is the
    /// color scale's range — it only exists when a color channel does
    /// (`color_key` is the channel's non-empty `data_field`).
    #[test]
    fn a_pie_without_a_color_channel_paints_every_arc_the_first_palette_stop() {
        let mut p = make_pie_payload(Value::Null);
        let fills = |payload: &Value| -> Vec<String> {
            let svg = render(&make_payload(payload.clone()), &test_fonts()).expect("renders");
            svg.match_indices("<path d=")
                .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string())
                .collect()
        };
        assert_eq!(fills(&p), ["#3164a3", "#3164a3", "#3164a3"], "no color channel → one constant fill");

        // A color channel restores the scale: the range cycles by slice index.
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        assert_eq!(fills(&p), ["#3164a3", "#779bc9", "#ad9c7f"], "color channel → palette by index");

        // A channel whose data_field is empty is production's `color_key = ""`
        // — the same no-encoding branch, not a color scale.
        p["resolved"]["resolved_channels"]["color"]["data_field"] = json!("");
        assert_eq!(fills(&p), ["#3164a3", "#3164a3", "#3164a3"], "empty data_field is no color key");
    }

    /// Front-end contract: `tooltip_format` present wraps every arc in a
    /// `.role-mark` group and stamps its structured aria-label
    /// (`charts/sector-charts_0/revenue_share_hero`'s Electronics slice,
    /// see `contract.rs`'s pie tests for the formula). A pie with NO
    /// `tooltip_format` (an older/synthetic fixture) keeps today's
    /// unwrapped, unlabeled arcs -- the same `tooltip_labels` gate bar uses.
    #[test]
    fn tooltip_format_wraps_every_arc_with_a_structured_aria_label() {
        let legend = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = legend.clone();
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(
            svg.contains("<g class=\"mark-arc role-mark layer_0_marks\""),
            "every arc wraps in a role-mark group"
        );
        assert!(
            svg.contains(
                "aria-label=\"⁤Electronics; %: 45%; \u{2060}Revenue: 197,199; \u{2060}⁣Σ: 443,023\""
            ),
            "{svg}"
        );

        // No tooltip_format: today's unwrapped, unlabeled status quo.
        let mut without_format = make_pie_payload(Value::Null);
        without_format["resolved"]["legend"] = legend;
        let plain = render(&make_payload(without_format), &test_fonts()).expect("renders");
        assert!(!plain.contains("role-mark"));
        assert!(!plain.contains("aria-label="));
    }

    #[test]
    fn a_field_label_titles_the_tooltip_value_row() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let plain = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(plain.contains("\u{2060}Revenue: 197,199"), "{plain}");
        p["resolved"]["field_labels"] = json!({"revenue": "Sales volume"});
        let labeled = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(labeled.contains("\u{2060}Sales volume: 197,199"), "{labeled}");
    }

    #[test]
    fn a_field_label_titles_the_legend() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        let plain = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert!(plain.contains(">Category<"), "{plain}");
        p["resolved"]["field_labels"] = json!({"category": "Department"});
        let labeled = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(labeled.contains(">Department<") && !labeled.contains(">Category<"), "{labeled}");
    }

    /// `chart_interactivity.js`'s `RECEDE_ROLES` matches `arc`
    /// literally: only an arc stamped so recedes on hover. The center total's
    /// text carries a description too but a different role, so it never
    /// enters the recede set.
    #[test]
    fn every_arc_carries_the_role_the_runtime_recedes() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let arcs = svg.matches("<g class=\"mark-arc role-mark").count();
        assert!(arcs > 1, "{svg}");
        assert_eq!(svg.matches("aria-roledescription=\"arc mark\"").count(), arcs, "{svg}");
        assert!(!svg.contains("aria-roledescription=\"bar\""), "{svg}");
    }

    /// No `total` config (no center-total display) still carries the
    /// identical Total tooltip row -- `charts/sector-charts_1/revenue_
    /// share_minimal`'s own shape, proving the row is unconditional.
    #[test]
    fn tooltip_total_row_is_unconditional_on_the_center_total_display() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("\u{2060}⁣Σ: 443,023"));
    }

    /// The board plans `category_colors` once per board (#7167): a value's
    /// slot wins over this chart's own row-position index. "Electronics"
    /// draws first (row 0, would take palette[0] positionally) but owns
    /// board slot 2; the other two values take slots 0 and 1.
    #[test]
    fn board_slot_wins_the_wedge_fill_over_row_position() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let fills: Vec<&str> = svg
            .match_indices("<path d=")
            .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap())
            .collect();
        assert_eq!(
            fills,
            ["#ad9c7f", "#3164a3", "#779bc9"],
            "Electronics/Accessories/Tools take slots 2/0/1: {svg}"
        );
    }

    /// The board slot governs the slice-label INK too, not just the wedge
    /// fill — `ink_at(scale, v, dark_companion_stops)` is a pure function of
    /// the row's own value, same as the wedge's `color_at`. Electronics'
    /// wedge is slot 2's swatch; its label must be slot 2's companion, not
    /// row 0's positional one.
    #[test]
    fn board_slot_wins_the_slice_label_ink_over_row_position() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["dark_companion_stops"] = json!(["#dark0", "#dark1", "#dark2"]);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": null, "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let label_fill = |name: &str| -> Option<String> {
            let tag = svg.split("<text").find(|t| t.contains(name)).expect(name).split('>').next().unwrap();
            tag.split("fill=\"").nth(1).map(|f| f.split('"').next().unwrap().to_string())
        };
        assert_eq!(label_fill("Electronics"), Some("#dark2".into()), "{svg}");
        assert_eq!(label_fill("Accessories"), Some("#dark0".into()), "{svg}");
        assert_eq!(label_fill("Tools"), Some("#dark1".into()), "{svg}");
    }

    /// A slice pinned to a color outside the palette is labeled in that
    /// color's own ink, not the companion of the slot it does not paint.
    #[test]
    fn a_pinned_slice_label_takes_its_pinned_colors_ink() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["dark_companion_stops"] = json!(["#dark0", "#dark1", "#dark2"]);
        p["resolved"]["pin_companions"] = json!([["#999999", "#pinink"]]);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": null, "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {"Electronics": "#999999"}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        let label_fill = |name: &str| -> Option<String> {
            let tag = svg.split("<text").find(|t| t.contains(name)).expect(name).split('>').next().unwrap();
            tag.split("fill=\"").nth(1).map(|f| f.split('"').next().unwrap().to_string())
        };
        assert_eq!(label_fill("Electronics"), Some("#pinink".into()), "{svg}");
        assert_eq!(label_fill("Accessories"), Some("#dark0".into()), "{svg}");
    }

    /// The deleted conditional-fill implementation covered two paint sites
    /// off one shared channel — the slice fill and the label ink — so one
    /// guard fixture with labels visible proves both are unreachable: had
    /// the guard only fired for one site, this payload would still render
    /// (or panic deeper in the deleted label-ink match) instead of
    /// refusing at the entry gate.
    #[test]
    fn a_conditional_color_channel_is_refused_for_both_slice_fill_and_label_ink() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        p["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "template": "{{ category }}",
            "offset": 8.0,
            "line_height": 1.25,
            "font": {"family": "Inter", "color": "#123456", "size": 14.0, "weight": "500", "line_height": 1.25}
        });
        p["resolved"]["resolved_channels"] = json!({"color": {
            "channel": "color", "mode": "conditional", "data_field": "revenue",
            "rules": [
                {"gte": 150000, "background": "#f59e0b"},
                {"lt": 150000, "background": "#e5e7eb"}
            ]
        }});
        let err = render(&make_payload(p), &test_fonts()).expect_err("refuses");
        assert!(err.contains("conditional color channel not supported"), "{err}");
    }

    fn empty_fonts() -> FontBook {
        FontBook::empty()
    }

    // Real font family present in InterVariable.ttf — needed for tests that
    // exercise the label-margin fitting pass, which measures real text.
    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    fn make_pie_payload_with_labels(template: &str) -> Value {
        let mut payload_json = make_pie_payload(Value::Null);
        payload_json["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        payload_json["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "offset": 8.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "color": "#333333", "size": 14.0, "weight": "500"},
            "template": template
        });
        payload_json
    }

    /// A color channel inks each label with its wedge's dark companion; an
    /// authored `font.color` applies only to a pie with no color channel.
    #[test]
    fn a_color_channel_label_ignores_an_authored_font_color() {
        let label_fills = |p: &Value| -> Vec<String> {
            let svg = render(&make_payload(p.clone()), &test_fonts()).unwrap();
            svg.split("<text ")
                .skip(1)
                .filter(|t| t.contains("<tspan"))
                .map(|t| t.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string())
                .collect()
        };
        let mut p = make_pie_payload_with_labels("{{ category }}");
        assert_eq!(label_fills(&p), ["#333333", "#333333", "#333333"], "no channel: authored");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        assert_eq!(label_fills(&p), ["#183253", "#3c4d65", "#183253"], "channel: dark companions");
    }

    #[test]
    fn category_only_template_renders_name_without_percent_or_value() {
        let payload = make_payload(make_pie_payload_with_labels("{{ category }}"));
        let svg = render(&payload, &test_fonts()).expect("category-only template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(!svg.contains("197,199"), "no value line for category-only template:\n{svg}");
        assert!(!svg.contains('%'), "no percent line for category-only template:\n{svg}");
    }

    #[test]
    fn a_template_naming_an_absent_column_errors() {
        let payload = make_payload(make_pie_payload_with_labels("{{ segment }}"));
        let err = render(&payload, &test_fonts()).expect_err("absent column rejected");
        assert!(err.contains("variable 'segment' is undefined"), "{err}");
    }

    #[test]
    fn percent_category_template_renders_percent_and_name_only() {
        let payload = make_payload(make_pie_payload_with_labels(
            "{{ '{:.0%}'.format(percent) }} {{ category }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("percent+category template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(!svg.contains("197,199"), "no value line:\n{svg}");
    }

    #[test]
    fn category_value_line_template_renders_currency_value_line() {
        let payload = make_payload(make_pie_payload_with_labels(
            "{{ category }}\n{{ '${:,.0f}'.format(revenue) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("category+currency template renders");
        assert!(svg.contains("Electronics"), "category name present:\n{svg}");
        assert!(svg.contains("$197,199"), "currency-formatted value present:\n{svg}");
    }

    #[test]
    fn percent_only_template_renders_share_without_name() {
        let payload = make_payload(make_pie_payload_with_labels("{{ '{:.0%}'.format(percent) }}"));
        let svg = render(&payload, &test_fonts()).expect("percent-only template renders");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(!svg.contains("Electronics"), "no category name:\n{svg}");
    }

    fn make_pie_payload_with_segment_labels(template: &str) -> Value {
        let mut payload_json = make_pie_payload_with_labels(template);
        payload_json["resolved"]["theta"] = json!("value");
        payload_json["resolved"]["identity_field"] = json!("segment");
        payload_json["rows"] = json!([
            {"segment": "Enterprise", "value": 12100.0},
            {"segment": "Mid-Market", "value": 7800.0},
            {"segment": "SMB", "value": 4900.0}
        ]);
        payload_json
    }

    #[test]
    fn segment_colon_value_template_renders_name_and_thousands_value() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ segment }}: {{ '{:,}'.format(value) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("segment-colon-value template renders");
        assert!(svg.contains("Enterprise: 12,100"), "single-line 'name: value':\n{svg}");
    }

    #[test]
    fn segment_newline_value_template_renders_two_lines() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ segment }}\n{{ '{:,}'.format(value) }}",
        ));
        let svg = render(&payload, &test_fonts()).expect("segment-newline-value template renders");
        assert!(svg.contains("Enterprise"), "name line present:\n{svg}");
        assert!(svg.contains("12,100"), "thousands-formatted value line present:\n{svg}");
    }

    #[test]
    fn jinja_filter_percent_color_value_template_renders_percent_and_scaled_k_value() {
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ percent | format('.0%') }} {{ color }}\n${{ (value / 1000) | format('.0f') }}k",
        ));
        let svg = render(&payload, &test_fonts()).expect("jinja-filter template renders");
        assert!(svg.contains("Enterprise"), "color (identity) name present:\n{svg}");
        assert!(svg.contains('%'), "percent share present:\n{svg}");
        assert!(svg.contains("$12k"), "value scaled to thousands with k suffix:\n{svg}");
    }

    #[test]
    fn template_with_yaml_block_scalar_trailing_newline_still_matches() {
        // A YAML `template: |` block scalar keeps one trailing "\n" that
        // Jinja2's default keep_trailing_newline=False then strips before
        // parsing (labels_env.py doesn't override it) — the resolved
        // payload's `template` field carries that same trailing "\n", and
        // this renderer must strip it the same way before matching.
        let payload = make_payload(make_pie_payload_with_segment_labels(
            "{{ percent | format('.0%') }} {{ color }}\n${{ (value / 1000) | format('.0f') }}k\n",
        ));
        let svg = render(&payload, &test_fonts()).expect("trailing-newline template still renders");
        assert!(svg.contains("$12k"), "value scaled to thousands with k suffix:\n{svg}");
    }

    #[test]
    fn total_visible_renders_summed_value_and_caption_at_center() {
        let payload_json = make_pie_payload(json!({
            "visible": true,
            "label": "Total Revenue"
        }));
        let mut payload_json = payload_json;
        // The format lives only on the resolved style: `ChartTotal` has no
        // `format` key.
        payload_json["resolved"]["style"]["total_style"]["value"]["format"] = json!("$,.0f");
        payload_json["resolved"]["style"]["total_style"]["value"]["font"]["family"] =
            json!(TEST_FONT);
        payload_json["resolved"]["style"]["total_style"]["label"]["font"]["family"] =
            json!(TEST_FONT);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with total overlay");

        // 197199 + 152504 + 93320 = 443023, formatted "$,.0f" → "$443,023".
        assert!(
            svg.contains("$443,023"),
            "expected formatted summed total in output:\n{svg}"
        );
        assert!(
            svg.contains("Total Revenue"),
            "expected total label caption in output:\n{svg}"
        );
    }

    /// A native total format paints its own unit, as `format_d3` does.
    #[test]
    fn a_native_total_format_paints_its_unit() {
        for (name, want) in [("duration", ">5d 3h<"), ("bytes", ">443 kB<")] {
            let mut payload_json = make_pie_payload(json!({"visible": true}));
            payload_json["resolved"]["style"]["total_style"]["value"]["format"] = json!(name);
            let svg = render(&make_payload(payload_json), &test_fonts()).expect(name);
            assert!(svg.contains(want), "{name}:\n{svg}");
        }
    }

    /// Oracle: `format_d3(443023, ".3~s", "CHF ", "", None, "after_prefix")`.
    #[test]
    fn a_total_value_format_paints_its_affix_around_the_digits() {
        let mut payload_json = make_pie_payload(json!({"visible": true, "label": "Total Revenue"}));
        let value = &mut payload_json["resolved"]["style"]["total_style"]["value"];
        value["format"] = json!({"spec": ".3~s", "prefix": "CHF ", "raw": "number"});
        value["font"]["family"] = json!(TEST_FONT);
        payload_json["resolved"]["style"]["total_style"]["label"]["font"]["family"] = json!(TEST_FONT);
        let svg = render(&make_payload(payload_json), &test_fonts()).expect("renders");
        assert!(svg.contains(">CHF 443k<"), "{svg}");
    }

    /// A donut's center total wider than the hole it sits in is
    /// WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS, measured on the hole the wheel
    /// actually draws (`2 × inner`), not a slot estimate.
    #[test]
    fn a_total_wider_than_the_hole_warns_and_one_that_fits_does_not() {
        let donut = |size: f64| {
            let mut json = make_pie_payload(json!({"visible": true}));
            let font = &mut json["resolved"]["style"]["total_style"]["value"]["font"];
            font["family"] = json!(TEST_FONT);
            font["size"] = json!(size);
            make_payload(json)
        };
        let (svg, warnings) = crate::diagnostic::collect(|| render(&donut(96.0), &test_fonts()));
        svg.expect("renders");
        assert_eq!(warnings.len(), 1, "{warnings:?}");
        let w = &warnings[0];
        assert_eq!(w.code, crate::codes::WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.id);
        assert_eq!(w.params["chart_id"], json!("pie"));
        assert_eq!(w.params["formatted_value"], json!("443023"));
        let text = w.params["text_width"].as_f64().unwrap();
        let hole = w.params["hole_diameter"].as_f64().unwrap();
        assert!(text > hole, "{text} > {hole}");
        let (slot_w, slot_h) =
            (w.params["slot_width"].as_f64().unwrap(), w.params["slot_height"].as_f64().unwrap());
        assert!((hole - slot_w.min(slot_h) * 0.9 * 0.6).abs() < 1e-9, "{hole} {slot_w} {slot_h}");

        let (svg, warnings) = crate::diagnostic::collect(|| render(&donut(18.0), &test_fonts()));
        svg.expect("renders");
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn total_null_renders_no_center_text() {
        let payload_json = make_pie_payload(Value::Null);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &empty_fonts()).expect("renders without total");
        assert!(
            !svg.contains("text-anchor=\"middle\""),
            "no center text layer expected when total is null:\n{svg}"
        );
    }

    #[test]
    fn the_retired_total_format_key_refuses_by_name() {
        let payload = make_payload(make_pie_payload(json!({
            "visible": true,
            "label": "Total Revenue",
            "format": "$,.0f"
        })));
        let err = render(&payload, &empty_fonts()).unwrap_err();
        assert!(err.contains("total.format"), "{err}");
    }

    #[test]
    fn total_invisible_renders_no_center_text() {
        let payload_json = make_pie_payload(json!({
            "visible": false,
            "label": "Total Revenue"
        }));
        let payload = make_payload(payload_json);
        let svg = render(&payload, &empty_fonts()).expect("renders without total");
        assert!(
            !svg.contains("text-anchor=\"middle\""),
            "no center text layer expected when total.visible is false:\n{svg}"
        );
    }

    // ── Attached table ──────────────────────────────────────────────────────

    fn attached_table_json() -> Value {
        let mut theme = crate::families::table::tests::base_table_theme();
        theme["font"] = json!({"family": "Inter", "color": "#222222", "size": 11.0, "weight": "400"});
        theme["row"]["height"] = json!(24.0);
        theme["row"]["stripe"] = json!({"color": "#F7F8FA"});
        theme["header"]["visible"] = json!(false);
        theme["outer_padding"] = json!(0.0);
        theme["pagination"] = json!({"enabled": false});
        json!({
            "link_open": "page",
            "columns": {
                "swatch": {"width": 24, "swatch": true, "link_open": "page", "header_link_open": "page"},
                "share": {"width": 44, "align": "right", "link_open": "page", "header_link_open": "page"},
                "name": {"width": 83, "link_open": "page", "header_link_open": "page"},
                "value": {"width": 61, "align": "right", "link_open": "page", "header_link_open": "page"}
            },
            "title_style": crate::families::table::tests::resolved_title_style(),
            "style": {
                "table": theme,
                "title_font": crate::families::table::tests::resolved_title_font()
            }
        })
    }

    #[test]
    fn an_attached_table_placement_or_align_production_does_not_index_refuses_by_name() {
        for (placement, align) in [("middle", "start"), ("bottom", "stretch"), ("none", "center")] {
            let mut p = make_pie_payload_with_attached_table("bottom", "");
            p["resolved"]["attached_table_placement"] = json!(placement);
            p["resolved"]["attached_table_align"] = json!(align);
            let payload: Payload = serde_json::from_value(p).unwrap();
            let err = render(&payload, &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                format!("pie: attached_table placement '{placement}' align '{align}' is not supported")
            );
        }
    }

    fn make_pie_payload_with_attached_table(placement: &str, heading: &str) -> Value {
        let mut payload_json = make_pie_payload(Value::Null);
        payload_json["resolved"]["attached_table"] = attached_table_json();
        payload_json["resolved"]["attached_table_placement"] = json!(placement);
        payload_json["resolved"]["attached_table_align"] =
            json!(if placement == "bottom" { "center" } else { "start" });
        payload_json["resolved"]["attached_table_width"] = json!(212.0);
        payload_json["resolved"]["attached_table_gap"] = json!(12.0);
        payload_json["resolved"]["hybrid_heading_gap"] = json!(6.0);
        payload_json["resolved"]["wheel_width"] = json!(300.0);
        payload_json["resolved"]["attached_heading"] = json!(heading);
        payload_json["resolved"]["attached_heading_font"] = json!({
            "family": "Inter", "color": "#222222", "size": 11.0, "weight": "600", "line_height": 1.25
        });
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 2]);
        // The wheel renders at the theme's view height (the sweep envelope's
        // board_style.continuous_height), never at the slot height.
        payload_json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},"continuous_height": 300.0, "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()});
        payload_json
    }

    /// The attached-table wheel child's own `wheel` layout (see
    /// `Resolved::background`) reads `r.background`, independently of the
    /// standalone-wheel call site — the composed rect is `wheel_width ×
    /// continuous_height` (300×300 for this fixture), not the card size.
    #[test]
    fn attached_table_wheel_child_reads_the_chart_own_background_not_the_board_background() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["background"] = json!("#d9a45b");
        payload_json["background"] = json!("#ffffff");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("<rect width=\"300\" height=\"300\" fill=\"#d9a45b\"/>"),
            "wheel child paints the chart's own background: {svg}"
        );
    }

    /// The attached table is a legend with a hidden header and no paging:
    /// there is nothing for the in-page table runtime to sort or filter.
    #[test]
    fn an_attached_table_is_never_a_row_pool() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_table"]["id"] = json!("pie1__attached_table");
        let mut payload = make_payload(payload_json);
        payload.interactive_controls = true;
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains("Electronics"), "{svg}");
        assert!(!svg.contains("data-dbt-table=") && !svg.contains("data-dbt-sort"), "{svg}");
    }

    #[test]
    fn attached_table_below_composes_at_the_view_height_and_grows_the_canvas() {
        let payload_json = make_pie_payload_with_attached_table("bottom", "");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with attached table below");

        // Table content: swatch chip, name text, share/value cells for each row.
        assert!(svg.contains("Electronics"), "row name present:\n{svg}");
        assert!(svg.contains("rx=\"3\" fill=\"#3164a3\""), "swatch chip present:\n{svg}");
        assert!(svg.contains('%'), "share percent cell present:\n{svg}");
        // compose_attached_table_svg: the outer canvas takes its OWN height
        // from the children — donut_h (the view height 300) + gap 12 +
        // table_h (3 rows × 24 = 72) = 384 — never the 432 slot.
        assert!(svg.contains("width=\"600\" height=\"384\""), "composed canvas size:\n{svg}");

        // Both group transforms pin the composition math directly.
        assert!(svg.contains("translate(150.00, 0)"), "wheel group centered, y=0:\n{svg}");
        assert!(svg.contains("translate(194.00, 312.00)"), "table group below the wheel (donut_h + gap):\n{svg}");
    }

    /// The table is the pie's legend: each row's swatch must be the exact
    /// fill its own wedge paints (`project_pie_table_rows`'s doc comment).
    /// A board slot reverses "Electronics" (row 0) off palette[0] onto
    /// palette[2], and the swatch follows it, not the row position.
    #[test]
    fn attached_table_swatch_follows_the_board_slot_not_row_position() {
        let mut p = make_pie_payload_with_attached_table("bottom", "");
        p["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        // Swatches in row order: Electronics (slot 2), Accessories (slot 0),
        // Tools (slot 1) — palette-by-ROW-POSITION would give 0/1/2 instead.
        let swatches: Vec<&str> = svg
            .match_indices("rx=\"3\" fill=\"")
            .map(|(i, _)| svg[i..].split("fill=\"").nth(1).unwrap().split('"').next().unwrap())
            .collect();
        assert_eq!(swatches, ["#ad9c7f", "#3164a3", "#779bc9"], "{svg}");
    }

    #[test]
    fn attached_table_right_places_table_beside_pie_at_disk_top() {
        let payload_json = make_pie_payload_with_attached_table("right", "Too small to label");
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with attached table on the right");

        assert!(svg.contains("Too small to label"), "heading text present:\n{svg}");
        // Wheel group is untranslated (right placement never shifts the wheel).
        assert!(svg.contains("translate(0.00, 0)"), "wheel group at origin:\n{svg}");
        // Table starts right of the wheel (wheel_width + gap) at the disk's
        // top edge: the wheel box is 300×300, so the fitted disk radius
        // decides disk_top = (300 − 2r)/2; with no label/total ink the
        // radius is min(pw, ph)/2 × outer_fraction — table_y must sit above
        // the vertical middle and the canvas keeps the wheel's 300 height
        // (disk_top + table_h < 300 here).
        assert!(svg.contains("translate(312.00"), "table positioned right of the wheel:\n{svg}");
        assert!(svg.contains("height=\"300\""), "right placement keeps the wheel height:\n{svg}");
    }

    fn attached_with(placement: &str, align: &str) -> Value {
        let mut p = make_pie_payload_with_attached_table(placement, "");
        p["resolved"]["attached_table_align"] = json!(align);
        p
    }

    /// The `(x, y)` of the table's group in a composed canvas: the one
    /// `translate` that precedes the 212px-wide table child.
    fn table_origin(svg: &str) -> (f64, f64) {
        let table_child = ")\"><svg xmlns=\"http://www.w3.org/2000/svg\" width=\"212\"";
        let end = svg.find(table_child).unwrap_or_else(|| panic!("no table child:\n{svg}"));
        let start = svg[..end].rfind("translate(").expect("table translate") + "translate(".len();
        let (x, y) = svg[start..end].split_once(", ").expect("x, y");
        (x.parse().expect("table x"), y.parse().expect("table y"))
    }

    fn canvas_height(svg: &str) -> f64 {
        svg.split("height=\"").nth(1).and_then(|s| s.split('"').next()).unwrap().parse().unwrap()
    }

    /// A table above or below the wheel aligns across the card — `start` at the left
    /// padding, `center` on the card's middle, `end` against the right padding.
    /// Card 600, table 212, padding 16.
    #[test]
    fn attached_table_aligns_across_the_card_inside_its_padding() {
        for (align, x) in [("start", 16.0), ("center", 194.0), ("end", 600.0 - 212.0 - 16.0)] {
            let svg = render(&make_payload(attached_with("bottom", align)), &test_fonts()).expect("renders");
            // y = donut_h 300 + gap 12; canvas = 300 + 12 + 72.
            assert_eq!(table_origin(&svg), (x, 312.0), "bottom / {align}:\n{svg}");
            assert_eq!(canvas_height(&svg), 384.0, "bottom / {align}");
        }
    }

    /// A table beside the wheel aligns down the
    /// wheel's DISK, never above its top. This wheel is 300 tall with no label
    /// ink, so the disk is 2 × 0.9 × (300 − 32)/2 = 241.2 and disk_top 29.4;
    /// `center` is `disk_top + (241.2 − 72)/2` = 114 and `end`
    /// `disk_top + 241.2 − 72` = 198.6. A right table sits at `wheel_width +
    /// gap`; a left one at the left padding, with the wheel at `table_width +
    /// gap`.
    #[test]
    fn attached_table_aligns_down_the_disk_on_a_side() {
        for (placement, x, wheel_x) in
            [("right", 300.0 + 12.0, "translate(0.00, 0)"), ("left", 16.0, "translate(224.00, 0)")]
        {
            for (align, y) in [("start", 29.4), ("center", 114.0), ("end", 198.6)] {
                let svg = render(&make_payload(attached_with(placement, align)), &test_fonts())
                    .expect("renders");
                let (tx, ty) = table_origin(&svg);
                assert_eq!(tx, x, "{placement} / {align}:\n{svg}");
                assert!((ty - y).abs() < 1e-9, "{placement} / {align}: table y {ty}, want {y}");
                assert!(svg.contains(wheel_x), "{placement} / {align} wheel group:\n{svg}");
                assert_eq!(canvas_height(&svg), 300.0, "{placement} / {align}");
            }
        }
    }

    /// A left table pushes the
    /// wheel right by `table_width + gap` and the title back by the same, so it
    /// stays on the card's left edge. Only a left placement slides it.
    #[test]
    fn attached_table_left_slides_the_title_back_by_the_wheel_offset() {
        for (placement, slid) in [("left", true), ("right", false), ("top", false), ("bottom", false)] {
            let mut p = attached_with(placement, "start");
            p["resolved"]["title"] = json!("Revenue");
            p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
            p["resolved"]["title_style"] =
                json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "position": {"offset": 10.0}});
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            assert_eq!(svg.contains("<g transform=\"translate(-224,0)\">"), slid, "{placement}:\n{svg}");
        }
    }

    /// `_slide_title` prints the slide with `{dx:g}`: a fractional natural
    /// table width slides the title by six significant digits, not the full
    /// float. `compose_attached_table_svg(..., table_width=212.123456789,
    /// gap=12.0, placement="left")` in production emits
    /// `translate(-224.123,0)` for the title group.
    #[test]
    fn attached_table_left_slide_prints_python_g_for_a_fractional_table_width() {
        let mut p = attached_with("left", "start");
        p["resolved"]["attached_table_width"] = json!(212.123456789);
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] =
            json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "position": {"offset": 10.0}});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(svg.contains("<g transform=\"translate(-224.123,0)\">"), "{svg}");
    }

    /// vega_lite.py:605-628, untitled: the wheel's view gains the table's
    /// height plus the gap (72 + 12) and its plot is pushed down by it
    /// (`bump_padding_top`), so the 300 + 84 = 384 view holds a plot box
    /// 268 square from y = 16 + 84 (center (150, 234)); the table drops in at
    /// the top padding.
    #[test]
    fn attached_table_top_pushes_the_untitled_wheel_down_by_the_table() {
        let svg = render(&make_payload(attached_with("top", "start")), &test_fonts()).expect("renders");
        assert_eq!(table_origin(&svg), (16.0, 16.0), "{svg}");
        assert_eq!(canvas_height(&svg), 384.0);
        assert!(svg.contains("<rect width=\"300\" height=\"384\""), "wheel child is the pushed view:\n{svg}");
        assert!(svg.contains("translate(150,234)"), "plot pushed below the table:\n{svg}");
    }

    /// vega_lite.py:605-628 + title_band.py `vega_title_block_height`, titled:
    /// the table lands below the title block — padding 16 + title 18 + one
    /// subtitle line 12 + 3 + `title.offset` 10 — while the wheel's title
    /// offset grows by `round(84)` (`shift_chart_title`, title_dy 0) so the
    /// plot clears the table.
    #[test]
    fn attached_table_top_sits_below_the_title_block() {
        let mut p = attached_with("top", "center");
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["subtitle"] = json!("by category");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] = json!({
            "font": {"size": 18.0, "family": "Source Serif 4"},
            "position": {"offset": 10.0},
            "subtitle": {"font": {"size": 12.0, "family": "Inter"}}
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(table_origin(&svg), (194.0, 16.0 + 18.0 + 15.0 + 10.0), "{svg}");
        assert_eq!(canvas_height(&svg), 384.0);
    }

    #[test]
    fn attached_table_top_under_a_title_without_an_offset_refuses() {
        let mut p = attached_with("top", "center");
        p["resolved"]["title"] = json!("Revenue");
        p["resolved"]["style"]["title_font"] = json!({"size": 18.0, "family": "Source Serif 4"});
        p["resolved"]["title_style"] = json!({"font": {"size": 18.0, "family": "Source Serif 4"}});
        let err = render(&make_payload(p), &test_fonts()).unwrap_err();
        assert!(err.contains("title_style.position.offset"), "{err}");
    }


    /// An attached-table pie's wheel is its own pane at `width=chart.wheel_width`,
    /// so `apply_title_overflow_to_spec`'s limit is `compute_title_limit(wheel_width,
    /// padding)`, not the card's — a title that fits the card but overruns the
    /// narrower wheel pane must still clip there, while the same title on a
    /// plain pie of the same card width is untouched.
    #[test]
    fn attached_table_bounds_the_title_to_the_wheel_pane_not_the_card() {
        let fonts = test_fonts();
        // wheel pane basis 300 - 2*16 = 268; card basis 600 - 2*16 = 568.
        // Build a title whose Inter-measured width (wrap_title_block's fixed
        // measure family) lands between the two: over the pane, under the card.
        let mut text = String::new();
        loop {
            text.push('M');
            if fonts.measure(&text, "Inter", 18.0).unwrap() > 400.0 {
                break;
            }
        }
        let title_style = json!({"font": {"size": 18.0, "family": "Source Serif 4"}, "overflow": "clip"});

        let mut attached_json = make_pie_payload_with_attached_table("right", "");
        attached_json["resolved"]["title"] = json!(text);
        attached_json["resolved"]["title_style"] = title_style.clone();
        let attached_svg =
            render(&make_payload(attached_json), &fonts).expect("attached-table pie renders");
        let attached_title = format!(">{text}<");
        assert!(
            !attached_svg.contains(&attached_title),
            "wheel-pane title must clip below the card width:\n{attached_svg}"
        );

        let mut plain_json = make_pie_payload(Value::Null);
        plain_json["resolved"]["title"] = json!(text);
        plain_json["resolved"]["title_style"] = title_style;
        let plain_svg = render(&make_payload(plain_json), &fonts).expect("plain pie renders");
        assert!(
            plain_svg.contains(&attached_title),
            "same title on a same-width plain pie must draw whole:\n{plain_svg}"
        );
    }

    #[test]
    fn attached_table_small_slot_still_composes_at_view_height() {
        // The slot height plays no part in the composition: production's
        // compose grows the canvas past a short slot rather than shrinking
        // the wheel (the old slot-fit arm errored here).
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["height"] = json!(50.0);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("short slot still renders");
        assert!(svg.contains("width=\"600\" height=\"384\""), "canvas grows past the slot:\n{svg}");
    }

    #[test]
    fn attached_table_missing_continuous_height_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},"format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()});
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("no view height to render the wheel at");
        assert!(err.contains("continuous_height"), "{err}");
    }

    #[test]
    fn attached_table_row_subset_uses_data_idx_not_row_idx() {
        // attached_row_indices = [1, 2] skips row 0 (Electronics) — the first
        // RENDERED row must be data row 1 ("Accessories", palette slot 1),
        // not row 0. A bug that used the loop's row_idx as the palette/data
        // lookup key instead of the row_indices value would put Electronics
        // (palette[0]) first instead.
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([1, 2]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("renders with a row subset");

        assert!(!svg.contains("Electronics"), "skipped row must not render:\n{svg}");
        assert!(svg.contains("Accessories"), "first rendered row is data row 1:\n{svg}");
        assert!(svg.contains("Tools"), "second rendered row is data row 2:\n{svg}");

        let accessories_swatch = svg.find("rx=\"3\" fill=\"#779bc9\"").expect("row 1's palette[1] swatch present");
        let tools_swatch = svg.find("rx=\"3\" fill=\"#ad9c7f\"").expect("row 2's palette[2] swatch present");
        assert!(
            accessories_swatch < tools_swatch,
            "Accessories (data_idx=1) renders as the first row, before Tools (data_idx=2)"
        );
    }

    #[test]
    fn attached_table_row_indices_empty_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([]);
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("empty row_indices is rejected");
        assert!(err.contains("attached_row_indices"), "{err}");
    }

    #[test]
    fn attached_table_row_indices_out_of_range_errors() {
        let mut payload_json = make_pie_payload_with_attached_table("bottom", "");
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 99]);
        let payload = make_payload(payload_json);
        let err = render(&payload, &test_fonts()).expect_err("out-of-range index is rejected, not a panic");
        assert!(err.contains("out of range"), "{err}");
    }

    #[test]
    fn attached_table_right_taller_than_wheel_grows_the_canvas() {
        // A tall table on the right grows the composed canvas past the
        // wheel's own height: outer_h = max(donut_h, disk_top + table_h) —
        // compose_attached_table_svg's right arm, the s15_donut shape.
        let mut payload_json = make_pie_payload_with_attached_table("right", "");
        payload_json["resolved"]["attached_row_indices"] = json!([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2]);
        let payload = make_payload(payload_json);
        let svg = render(&payload, &test_fonts()).expect("tall right table renders");
        // 15 rows × 24 = 360 table_h; disk_top > 0 ⇒ outer_h > 360 > 300.
        let h: f64 = svg
            .split("height=\"")
            .nth(1)
            .and_then(|s| s.split('"').next())
            .and_then(|s| s.parse().ok())
            .expect("outer height parses");
        assert!(h > 360.0, "canvas grows to disk_top + table_h: {h}\n{svg}");
    }








    /// Fit margins with no title and no labels are exactly the padding: the
    /// wheel centers the padded box and the radius is
    /// `min(pw, ph)/2 × outer_fraction` of it (568×400 → r 180, center
    /// (300, 216)). The legacy model agrees on this unconstrained shape —
    /// this pins the shared geometry; the model switch is discriminated by
    /// `fit_label_ink_pushes_margins_asymmetrically` below.
    #[test]
    fn fit_wheel_centers_padded_box_with_min_dim_radius() {
        let payload = make_payload(make_pie_payload(Value::Null));
        let svg = render(&payload, &test_fonts()).expect("pie renders");
        assert!(svg.contains("translate(300,216)"), "center at padded-box middle: {svg}");
        assert!(svg.contains("A180,"), "outer radius 0.9×min(568,400)/2 = 180: {svg}");
    }

    /// A visible slice label pushes the PLOT MARGIN on its side (asymmetric,
    /// like production's donut_example origin (33, 44)) — the radius shrinks
    /// only through the fitted box, never through a per-label radius min().
    #[test]
    fn fit_title_overflow_truncates_and_wraps_per_mode() {
        // The overflow wiring end to end: a truncate-mode title yields ONE
        // ellipsized title row; a wrap-two subtitle yields TWO rows, the
        // second advanced by sub_fs + 2 (Vega's default text lineHeight).
        let mut json = make_pie_payload(Value::Null);
        json["width"] = json!(220.0);
        json["height"] = json!(300.0);
        json["resolved"]["title"] =
            json!("A title far too long for a two hundred twenty pixel card to hold");
        json["resolved"]["subtitle"] =
            json!("a subtitle that also overflows the card width and wraps onto a second line");
        json["resolved"]["title_style"] = json!({
            // Source Serif paint stack: the wrap must still MEASURE with
            // Inter (WRAP_MEASURE_FAMILY) — reverting to the paint family
            // changes where the ellipsis lands and fails the exact pin.
            "font": {"family": "'Source Serif 4', Georgia, serif", "size": 14.0, "weight": "500"},
            "overflow": "truncate",
            "position": {"offset": 10.0},
            "subtitle": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "400"},
                "overflow": "wrap-two"
            }
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("overflowing titles render");
        let title_rows =
            svg.matches("font-size=\"14\"").count();
        assert_eq!(title_rows, 1, "truncate mode keeps one title row:\n{svg}");
        assert!(svg.contains('\u{2026}'), "the cut title carries the ellipsis:\n{svg}");
        // Exact pin: the cut lands where the INTER-measured wrap puts it —
        // measuring with the serif paint stack instead moves the ellipsis.
        assert!(
            svg.contains(">A title far too long for a tw\u{2026}<"),
            "wrap measured with Inter, not the paint family:\n{svg}"
        );
        // Subtitle rows: two 11px text rows, 13px apart.
        let mut ys: Vec<f64> = Vec::new();
        for (pos, _) in svg.match_indices("font-size=\"11\"") {
            let head = &svg[..pos];
            if let Some(i) = head.rfind(" y=\"") {
                if let Ok(v) = head[i + 4..].split('\"').next().unwrap_or("").parse::<f64>() {
                    ys.push(v);
                }
            }
        }
        assert_eq!(ys.len(), 2, "wrap-two subtitle renders two rows:\n{svg}");
        assert!((ys[1] - ys[0] - 13.0).abs() < 1e-6, "second row advances by sub_fs + 2: {ys:?}");
    }

    /// An authored `font.style` reaches the center total's value and label
    /// and every slice label as `font-style`, as Vega paints VL's `fontStyle`.
    #[test]
    fn an_authored_font_style_reaches_the_total_and_slice_labels() {
        let mut json = make_pie_payload(json!({"visible": true, "label": "Total"}));
        json["resolved"]["style"]["total_style"]["value"]["font"]["style"] = json!("italic");
        json["resolved"]["style"]["total_style"]["label"]["font"]["style"] = json!("oblique");
        json["resolved"]["slice_label_indices"] = json!([0, 1, 2]);
        json["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 12.0, "weight": "500", "style": "italic"},
            "template": "{{ category }}"
        });
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let texts: Vec<&str> = svg.split("<text ").skip(1).collect();
        let styled = |needle: &str| texts.iter().filter(|t| t.contains(needle)).count();
        assert_eq!(styled("font-style=\"oblique\""), 1, "total label:\n{svg}");
        assert_eq!(styled("font-style=\"italic\""), 4, "total value + 3 slice labels:\n{svg}");

        let plain = render(&make_payload(make_pie_payload(json!({"visible": true}))), &test_fonts()).unwrap();
        assert!(!plain.contains("font-style"), "no authored style, no attribute:\n{plain}");
    }

    /// An authored slice opacity paints on every wedge.
    #[test]
    fn an_authored_slice_opacity_paints_on_every_wedge() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["style"]["slice_mark"]["opacity"] = json!(0.55);
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let wedges: Vec<&str> = svg.split("<path ").skip(1).collect();
        assert_eq!(wedges.len(), 3, "{svg}");
        assert!(wedges.iter().all(|w| w.contains("opacity=\"0.55\"")), "{svg}");
    }

    /// A placeholder fades the whole drawing and lays the overlay over the
    /// canvas in the board's font, as `render_vega_spec` does.
    #[test]
    fn a_placeholder_fades_the_wheel_and_paints_the_overlay() {
        let mut json = make_pie_payload(Value::Null);
        json["board_style"] = json!({ "text_mark_font": {"family": "sans-serif", "size": 11.0},
            "format_locale": crate::number_locale::en_us_wire(),
            "time_locale": crate::time_locale::en_us_wire(),
            "placeholder": {
                "opacity": 0.4,
                "overlay": {
                    "text": "add data", "background": "rgba(255, 255, 255, 0.5)",
                    "font": {"size": 16.0, "weight": 500.0, "color": "#666666", "family": "Inter"}
                }
            }
        });
        let plain = render(&make_payload(json.clone()), &test_fonts()).unwrap();
        json["is_placeholder"] = json!(true);
        let svg = render(&make_payload(json), &test_fonts()).expect("renders");
        let (open, rest) = plain.split_at(plain.find('>').unwrap() + 1);
        let content = rest.strip_suffix("</svg>").unwrap();
        assert_eq!(
            svg,
            format!(
                "{open}<g opacity=\"0.4\">{content}</g><g class=\"placeholder-overlay\">\
                 <rect class=\"dbt-pointer-inert\" x=\"0\" y=\"0\" width=\"600.0\" height=\"432.0\" \
                 fill=\"rgba(255, 255, 255, 0.5)\"/><text class=\"dbt-pointer-inert\" x=\"300.0\" \
                 y=\"216.0\" text-anchor=\"middle\" dominant-baseline=\"middle\" font-family=\"Inter\" \
                 font-size=\"16.0\" font-weight=\"500.0\" fill=\"#666666\">add data</text></g></svg>"
            )
        );
    }

    /// A pre-pass cut reports `WARN-CHART-TITLE-TRUNCATED` per field, with the
    /// text as cased; a row that fits reports nothing.
    #[test]
    fn a_cut_title_or_subtitle_warns_title_truncated() {
        let mut json = make_pie_payload(Value::Null);
        json["width"] = json!(220.0);
        json["resolved"]["title"] = json!("a title far too long for a narrow card to hold");
        json["resolved"]["subtitle"] = json!("short");
        json["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500", "case": "upper"},
            "overflow": "truncate",
            "position": {"offset": 10.0},
            "subtitle": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "400"},
                "overflow": "truncate"
            }
        });
        let cut = |field: &str, text: &str| {
            crate::diagnostic::Diagnostic::new(
                &crate::codes::WARN_CHART_TITLE_TRUNCATED,
                [
                    ("authored_field", json!(field)),
                    ("authored_text", json!(text)),
                    ("chart_id", json!("pie")),
                ],
            )
        };
        let payload = make_payload(json.clone());
        let (svg, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        svg.expect("renders");
        assert_eq!(warnings, vec![cut("title", "A TITLE FAR TOO LONG FOR A NARROW CARD TO HOLD")]);

        let subtitle = "a subtitle far too long for a narrow card to hold as well";
        json["resolved"]["title"] = json!("Short");
        json["resolved"]["subtitle"] = json!(subtitle);
        let payload = make_payload(json);
        let (_, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert_eq!(warnings, vec![cut("subtitle", subtitle)]);

        let mut wide = make_pie_payload(Value::Null);
        wide["resolved"]["title"] = json!("Fits");
        let payload = make_payload(wide);
        let (_, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn fit_label_ink_pushes_margins_asymmetrically() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["slice_label_indices"] = serde_json::json!([0]);
        json["rows"][0]["category"] =
            serde_json::json!("Electronics and Consumer Appliances Division");
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("labeled pie renders");
        // Slice 0 (48% share) has its mid angle on the right — its label ink
        // moves the center left of the unlabeled 300.
        let cx: f64 = svg
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        assert!(cx < 300.0, "right-side label ink pushes the wheel left: cx={cx}: {svg}");
        assert!(svg.contains("text-anchor=\"start\""), "right-half label left-aligns: {svg}");
        assert!(!svg.contains("A180,"), "radius follows the fitted box: {svg}");
    }

    /// Vega writes a text mark's anchor as `translate(x,y)` with the number's
    /// own JavaScript string (`645.366207199934,100.61781672160188`), never
    /// rounded: the anchor is a measured position, and a two-decimal copy of
    /// it shifts every glyph's anti-aliasing.
    #[test]
    fn a_slice_label_anchor_prints_at_full_precision() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["slice_label_indices"] = serde_json::json!([0]);
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("labeled pie renders");
        let text = &svg[svg.find("<text x=\"").expect("a label text") + 9..];
        let x = text.split('"').next().unwrap();
        let y = text.split(" y=\"").nth(1).unwrap().split('"').next().unwrap();
        for (axis, printed) in [("x", x), ("y", y)] {
            let value: f64 = printed.parse().unwrap();
            assert_eq!(printed, value.to_string(), "{axis} is the double's own string: {svg}");
            assert_ne!(printed, format!("{value:.2}"), "{axis} is not a two-decimal copy: {svg}");
        }
        assert!(svg.contains(&format!("<tspan x=\"{x}\" dy=\"0\">")), "the tspan repeats the anchor: {svg}");
    }

    /// A wheel whose left-hand label ink grows as the plot shrinks pushes the
    /// title left of the padded view, because Vega places the title group at
    /// the FINAL layout pass's ink `x1` while the margins came from the pass
    /// before. Production's `fix_title_alignment` post-pass moves it back to
    /// exactly `padding.left`, and the committed references carry that as the
    /// two-decimal `translate(-17.00,…)` / `translate(-30.00,…)` signature.
    #[test]
    fn a_title_dragged_left_of_the_padding_is_drawn_at_the_padding() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = serde_json::json!("Revenue Wheel");
        json["resolved"]["title_style"] =
            serde_json::json!({"font": {"family": TEST_FONT, "size": 18.0, "weight": "600"}});
        json["resolved"]["slice_label_indices"] = serde_json::json!([1, 2]);
        json["rows"][1]["category"] = serde_json::json!("Accessories and Peripherals Group");
        json["rows"][2]["category"] = serde_json::json!("Tools and Hardware Group");
        json["resolved"]["style"]["slice_mark"]["labels"] = serde_json::json!({
            "offset": 6.0,
            "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        let svg = render(&make_payload(json), &test_fonts()).expect("titled wheel renders");
        let before = &svg[..svg.find(">Revenue Wheel<").expect("title drawn")];
        let tag = &before[before.rfind("<text").unwrap()..];
        assert!(tag.contains("x=\"16.00\""), "title clamped to padding.left: {tag}");
    }

    /// The width-resolved `style.title_font` is the drawn title's font slot,
    /// and the title stacks above the plot through the vega title algebra
    /// (emit_title_vega), not the legacy fs+10 band.
    #[test]
    fn fit_title_uses_width_resolved_font() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = serde_json::json!("Revenue Wheel");
        json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": "serif", "size": 18.0, "weight": "600", "color": "#111111"}
        });
        json["resolved"]["style"]["title_font"] =
            serde_json::json!({"family": TEST_FONT, "size": 14.0, "weight": "600", "color": "#222222"});
        let payload = make_payload(json);
        let svg = render(&payload, &test_fonts()).expect("titled pie renders");
        let before = &svg[..svg.find(">Revenue Wheel<").unwrap()];
        let tag = &before[before.rfind("<text").unwrap()..];
        assert!(tag.contains("font-size=\"14\""), "tiered 14px wins: {tag}");
        assert!(!tag.contains("font-family=\"serif\""), "tiered family wins: {tag}");
    }

    fn linked_pie_payload(link: &str) -> Value {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        p["resolved"]["link"] = json!(link);
        p
    }

    fn hrefs(svg: &str) -> Vec<&str> {
        svg.split("<a href=\"").skip(1).map(|s| s.split('"').next().unwrap()).collect()
    }

    /// `_HREF_PLACEHOLDER` matches only `x|y|color|theta`, so an authored
    /// `{{ category }}` passes through verbatim (`charts/sector-charts_2`):
    /// one `<a>` per wedge, opening right inside the arc's `.role-mark` group
    /// and closing before it does.
    #[test]
    fn a_link_paints_one_anchor_per_wedge_inside_its_mark_group() {
        let p = linked_pie_payload("/category/{{ category }}");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(hrefs(&svg), ["/category/{{ category }}"; 3], "{svg}");
        for (i, _) in svg.match_indices("<g class=\"mark-arc role-mark layer_0_marks\"") {
            let open_end = i + svg[i..].find('>').unwrap() + 1;
            assert!(svg[open_end..].starts_with("<a href=\""), "anchor opens the group: {svg}");
            let group_end = open_end + svg[open_end..].find("</g>").unwrap();
            assert!(svg[open_end..group_end].ends_with("</a>"), "anchor closes the group: {svg}");
        }
    }

    /// A modal link stamps every wedge anchor; page and missing stamp nothing.
    #[test]
    fn a_link_mode_stamps_only_modal_wedge_anchors() {
        let render_with = |mode: Option<&str>| {
            let mut p = linked_pie_payload("/c/");
            if let Some(mode) = mode {
                p["resolved"]["link_open"] = json!(mode);
            }
            render(&make_payload(p), &test_fonts())
        };
        let page = render_with(None).unwrap();
        assert_eq!(render_with(Some("page")).unwrap(), page);
        assert!(!page.contains("data-dbt-link-open"), "{page}");
        let modal = render_with(Some("modal")).unwrap();
        assert_eq!(modal.matches("<a href=\"/c/\" data-dbt-link-open=\"modal\">").count(), 3, "{modal}");
        let err = render_with(Some("popup")).unwrap_err();
        assert!(err.contains("popup"), "{err}");
    }

    /// A pie with no `tooltip_format` draws no `.role-mark` group; its link
    /// still wraps each wedge path.
    #[test]
    fn a_link_wraps_each_wedge_when_no_mark_group_is_drawn() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["link"] = json!("/c/");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(svg.matches("<a href=\"/c/\"><path d=").count(), 3, "{svg}");
        assert!(!svg.contains("role-mark"), "{svg}");
    }

    /// `{{ color }}` reads the color channel's `data_field` off the wedge's
    /// own row and `{{ theta }}` its measure, each through `_vega_encode_field`
    /// and JS `'' + value` (`197199.0` prints `197199`).
    #[test]
    fn color_and_theta_placeholders_substitute_per_wedge() {
        let mut p = linked_pie_payload("/c/?k={{ color }}&v={{theta}}");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        p["rows"][1]["category"] = json!("A&B C");
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(
            hrefs(&svg),
            [
                "/c/?k=Electronics&amp;v=197199",
                "/c/?k=A%26B%20C&amp;v=152504",
                "/c/?k=Tools&amp;v=93320"
            ],
            "{svg}"
        );
    }

    /// The center-total value and caption texts and each slice label carry the
    /// chart's link mode like the wedges: 3 wedges + 2 total texts + 3 labels.
    #[test]
    fn a_modal_link_stamps_the_total_and_slice_label_anchors() {
        let mut p = make_pie_payload_with_labels("{{ category }}");
        p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["link"] = json!("/c/");
        let page = render(&make_payload(p.clone()), &test_fonts()).expect("renders");
        assert_eq!(page.matches("<a href=\"/c/\">").count(), 8, "{page}");
        assert!(!page.contains("data-dbt-link-open"), "{page}");
        p["resolved"]["link_open"] = json!("modal");
        let modal = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(modal.matches("<a href=\"/c/\" data-dbt-link-open=\"modal\">").count(), 8, "{modal}");
        assert_eq!(modal.matches("data-dbt-link-open").count(), 8, "{modal}");
    }

    /// A pie has no `x`/`y` encoding: production raises at spec build
    /// (`_build_href_calc_expr`), so the renderer refuses with its message.
    #[test]
    fn x_and_y_placeholders_refuse_with_productions_message() {
        for channel in ["x", "y"] {
            let p = linked_pie_payload(&format!("/c/?v={{{{ {channel} }}}}"));
            let err = render(&make_payload(p), &test_fonts()).unwrap_err();
            assert_eq!(
                err,
                format!(
                    "link template references channel '{{{{ {channel} }}}}' but chart has no \
                     '{channel}' encoding assigned"
                )
            );
        }
    }

    /// No color channel bound to a data field: `{{ color }}` has no column
    /// to read, the same production error.
    #[test]
    fn a_color_placeholder_without_a_color_field_refuses() {
        let p = linked_pie_payload("/c/?k={{ color }}");
        let err = render(&make_payload(p), &test_fonts()).unwrap_err();
        assert!(err.contains("chart has no 'color' encoding assigned"), "{err}");
    }

    /// Every layer that inherits the shared `description` encoding inherits
    /// the shared `href` too: the center-total texts (`row_number === 1`, so
    /// row 0's href) and each slice label (its own row's) are anchored
    /// inside their mark groups, like the wedges.
    #[test]
    fn total_and_slice_label_texts_carry_their_rows_anchor() {
        let mut p = make_pie_payload_with_labels("{{ category }}");
        p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        p["resolved"]["link"] = json!("/c/?k={{ color }}");
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category"}});
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert_eq!(
            hrefs(&svg),
            [
                "/c/?k=Electronics", "/c/?k=Accessories", "/c/?k=Tools",
                "/c/?k=Electronics", "/c/?k=Electronics",
                "/c/?k=Electronics", "/c/?k=Accessories", "/c/?k=Tools",
            ],
            "{svg}"
        );
        for layer in ["layer_1_marks", "layer_2_marks", "layer_3_marks"] {
            for (i, _) in svg.match_indices(&format!("<g class=\"mark-text role-mark {layer}\"")) {
                let open_end = i + svg[i..].find('>').unwrap() + 1;
                assert!(svg[open_end..].starts_with("<a href=\""), "{layer}: {svg}");
                let group_end = open_end + svg[open_end..].find("</g>").unwrap();
                assert!(svg[open_end..group_end].ends_with("</text></a>"), "{layer}: {svg}");
            }
        }
    }

    // ── Visible legend ───────────────────────────────────────────────────

    fn legend_json(edge: &str, align: &str, direction: &str) -> Value {
        json!({
            "visible": true,
            "position": {"edge": edge, "align": align, "overlay": false},
            "direction": direction,
            "columns": 0,
            "label": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 8.0,
                "max_width": 200.0
            },
            "title": {
                "font": {"family": TEST_FONT, "color": "#313233", "size": 12.0, "weight": 600.0, "case": "title"},
                "visible": null
            },
            "symbol_limit": 20,
            "values": null,
            "symbol_shape": null,
            "symbol_fill": null
        })
    }

    fn legend_pie_payload(edge: &str, align: &str, direction: &str) -> Value {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category", "quantitative_data": false}});
        p["resolved"]["style"]["slice_mark"]["stroke"] =
            json!({"color": "#FFFFFF", "width": 3.0, "join": "round"});
        p["resolved"]["legend"] = legend_json(edge, align, direction);
        p
    }

    const LEGEND_LABELS: [&str; 3] = ["Electronics", "Accessories", "Tools"];

    /// The legend box the shared Vega layout gives `labels` under `payload`'s
    /// own legend config: `(box_w, box_h)`.
    fn legend_box(payload: &Value, labels: &[&str]) -> (f64, f64) {
        use crate::families::legend::{legend_title_text, vega_legend, LegendConfig, LegendEntry};
        let cfg: LegendConfig = Deserialize::deserialize(&payload["resolved"]["legend"]).unwrap();
        let entries: Vec<LegendEntry> =
            labels.iter().map(|label| LegendEntry {
                label,
                fill: "#000",
                symbol: None,
                series_key: None,
                recede: None,
            }).collect();
        let title = legend_title_text("category", &cfg, &Default::default()).unwrap();
        let legend = vega_legend(&entries, false, &title, &cfg, &test_fonts()).unwrap();
        (legend.box_w, legend.box_h())
    }

    /// The wheel's center, off its `<g transform="translate(cx,cy)">`.
    fn wheel_center(svg: &str) -> (f64, f64) {
        let rest = &svg[svg.find("<g transform=\"translate(").unwrap() + "<g transform=\"translate(".len()..];
        let (cx, cy) = rest.split(')').next().unwrap().split_once(',').unwrap();
        (cx.parse().unwrap(), cy.parse().unwrap())
    }

    /// Vega's autosize fit gives the plot box exactly what the legend takes:
    /// the legend box plus `legend.offset` (18) on the legend's own edge
    /// (`ViewLayout.js` `layoutLegend`: `F.add(o.x1, 0).add(o.x2, 0)` for a
    /// left/right legend, `F.add(0, o.y1).add(0, o.y2)` for a top/bottom one,
    /// the legend anchored at `ceil(x2)/ceil(y2) + offset` of the plot rect,
    /// or `floor(x1)/floor(y1) - offset - box` on the far edges). No title,
    /// no labels, no total: the wheel is the plot's own center.
    #[test]
    fn a_visible_legend_shrinks_the_plot_by_its_box_plus_the_offset_on_each_edge() {
        // (edge, direction, title visible): a top/horizontal legend is baked
        // untitled (`_base_kwargs`: top + horizontal + title.visible None).
        let (avail_w, avail_h) = (568.0, 400.0);
        for (edge, direction, untitled) in
            [("right", "vertical", false), ("left", "vertical", false), ("top", "horizontal", true), ("bottom", "horizontal", false)]
        {
            let mut p = legend_pie_payload(edge, "start", direction);
            if untitled {
                p["resolved"]["legend"]["title"]["visible"] = json!(false);
            }
            let (box_w, box_h) = legend_box(&p, &LEGEND_LABELS);
            let svg = render(&make_payload(p), &test_fonts()).unwrap_or_else(|e| panic!("{edge}: {e}"));
            let (cx, cy) = wheel_center(&svg);
            let (pw, ph, left, top) = match edge {
                "right" => (avail_w - 18.0 - box_w, avail_h, 0.0, 0.0),
                "left" => (avail_w - 18.0 - box_w, avail_h, 18.0 + box_w, 0.0),
                "top" => (avail_w, avail_h - 18.0 - box_h, 0.0, 18.0 + box_h),
                _ => (avail_w, avail_h - 18.0 - box_h, 0.0, 0.0),
            };
            assert_eq!((cx, cy), (16.0 + left + pw / 2.0, 16.0 + top + ph / 2.0), "{edge}: {svg}");
        }
    }

    /// The legend is the outermost ink on its own edge: a right legend's
    /// swatch column starts at `ceil(plot right) + 18`, a bottom one's title
    /// at `ceil(plot bottom) + 18` (`ViewLayout.js` legend anchors).
    #[test]
    fn the_legend_sits_past_the_plot_rect_on_its_own_edge() {
        let p = legend_pie_payload("right", "start", "vertical");
        let (box_w, _) = legend_box(&p, &LEGEND_LABELS);
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let plot_right = 16.0 + (568.0 - 18.0 - box_w);
        // First entry's symbol center: x = legend x + size/2, size = 12.
        assert!(svg.contains(&format!("translate({:.2},", plot_right + 18.0 + 6.0)), "{svg}");
        // The label sits at legend x + 12 + labelOffset 8.
        assert!(svg.contains(&format!("<text x=\"{:.2}\"", plot_right + 18.0 + 20.0)), "{svg}");
    }

    fn legend_swatches(svg: &str) -> Vec<(String, String)> {
        // (fill, label) per entry, in painted order.
        let legend = &svg[svg.find("d=\"M5,0A5,5,0,1,1,-5,0").unwrap() - 80..];
        legend
            .split("<path transform=\"translate(")
            .skip(1)
            .map(|entry| {
                let fill = entry.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
                let label = entry.split("role-legend-label").nth(1).unwrap().split("</text>").next().unwrap();
                (fill, label.rsplit('>').next().unwrap().to_string())
            })
            .collect()
    }

    /// Entries are the color scale's domain in row order, each swatch the scale's own color for that
    /// value, stroked in the slice mark's own stroke color
    /// (`encode.ts` `symbols`: the mark's `stroke` flows to the legend
    /// symbol, never its `strokeWidth` — the 1.5 stays Vega's).
    #[test]
    fn legend_entries_follow_slice_order_and_the_color_scale() {
        let p = legend_pie_payload("right", "start", "vertical");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#3164a3", "Electronics"), ("#779bc9", "Accessories"), ("#ad9c7f", "Tools")]
                .map(|(f, l)| (f.to_string(), l.to_string())),
            "{svg}"
        );
        assert_eq!(svg.matches("stroke=\"#FFFFFF\" stroke-width=\"1.5\"/>").count(), 3, "{svg}");

        let mut bound = legend_pie_payload("right", "start", "vertical");
        bound["resolved"]["category_colors"] = json!([{
            "field": "category",
            "slots": {"Electronics": 2, "Accessories": 0, "Tools": 1},
            "overrides": {}
        }]);
        let svg = render(&make_payload(bound), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#ad9c7f", "Electronics"), ("#3164a3", "Accessories"), ("#779bc9", "Tools")]
                .map(|(f, l)| (f.to_string(), l.to_string())),
            "the board slot wins the swatch, like the wedge: {svg}"
        );
    }

    /// A hidden legend reserves nothing and draws nothing, and a visible one
    /// over a pie with no color channel draws nothing either: there is no
    /// color scale to carry a legend.
    #[test]
    fn a_hidden_or_colorless_legend_draws_nothing_and_reserves_nothing() {
        let mut hidden = legend_pie_payload("right", "start", "vertical");
        hidden["resolved"]["legend"]["visible"] = json!(false);
        let svg = render(&make_payload(hidden), &test_fonts()).unwrap();
        assert!(!svg.contains("role-legend-label"), "{svg}");
        assert_eq!(wheel_center(&svg), (16.0 + 568.0 / 2.0, 16.0 + 400.0 / 2.0), "{svg}");

        let mut colorless = legend_pie_payload("right", "start", "vertical");
        colorless["resolved"]["resolved_channels"] = json!({});
        let svg = render(&make_payload(colorless), &test_fonts()).unwrap();
        assert!(!svg.contains("role-legend-label"), "{svg}");
        assert_eq!(wheel_center(&svg), (16.0 + 568.0 / 2.0, 16.0 + 400.0 / 2.0), "{svg}");
    }

    /// `symbolLimit` (theme 20): vega-encode's `LegendEntries` keeps the
    /// first `limit - 1` entries and folds the rest into one "…N entries"
    /// row (`w.length - (limit - 1)`) that carries the next domain value's
    /// own swatch — here the 20th value's palette slot.
    #[test]
    fn symbol_limit_folds_the_overflow_into_an_entries_row() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["rows"] = json!((0..22).map(|n| json!({"category": format!("C{n}"), "revenue": 10.0})).collect::<Vec<_>>());
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let swatches = legend_swatches(&svg);
        assert_eq!(swatches.len(), 20, "{svg}");
        assert_eq!(swatches[18].1, "C18");
        // 22 values, 19 kept: 3 folded into the last row, which paints the
        // 20th value's color (palette index 19 % 3).
        assert_eq!(swatches[19], ("#779bc9".to_string(), "\u{2026}3 entries".to_string()), "{svg}");

        let mut at_limit = legend_pie_payload("right", "start", "vertical");
        at_limit["rows"] = json!((0..20).map(|n| json!({"category": format!("C{n}"), "revenue": 10.0})).collect::<Vec<_>>());
        let svg = render(&make_payload(at_limit), &test_fonts()).unwrap();
        assert_eq!(legend_swatches(&svg).len(), 20);
        assert!(!svg.contains("entries"), "an exactly-full legend folds nothing: {svg}");
    }

    /// `labelLimit` (theme 200): a wider label is truncated with an
    /// ellipsis by the same Vega `truncate` the axes use, and the legend box
    /// is bounded at the limit.
    #[test]
    fn a_label_wider_than_the_label_limit_truncates_with_an_ellipsis() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["legend"]["label"]["max_width"] = json!(100.0);
        p["rows"][0]["category"] = json!("Enterprise accounts managed by the strategic partnerships team");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let drawn = &legend_swatches(&svg)[0].1;
        assert!(drawn.starts_with("Enterprise ") && drawn.ends_with('\u{2026}'), "{drawn}");
        let width = test_fonts().measure_shaped(drawn, TEST_FONT, 11.0).unwrap();
        assert!(width <= 100.0 && width > 90.0, "{width}");
        // Vega bounds a truncated text at its limit, not at the shorter
        // drawn text: the box is symbol cell + offset (20) + 100.
        let (cx, _) = wheel_center(&svg);
        assert_eq!(cx, 16.0 + (568.0 - 18.0 - 120.0) / 2.0, "{svg}");
    }

    /// `titleLimit` (180): the same law for the legend title — drawn
    /// truncated, bounded at the limit, so the box is as wide as the limit.
    #[test]
    fn a_title_wider_than_the_title_limit_truncates_and_bounds_the_box_at_the_limit() {
        let field = "a_remarkably_long_customer_segment_classification_field_name";
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["resolved_channels"]["color"]["data_field"] = json!(field);
        p["resolved"]["identity_field"] = json!(field);
        for row in p["rows"].as_array_mut().unwrap() {
            let value = row["category"].take();
            row[field] = value;
        }
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert!(svg.contains("Customer\u{2026}</text>"), "{svg}");
        let (cx, _) = wheel_center(&svg);
        assert_eq!(cx, 16.0 + (568.0 - 18.0 - 180.0) / 2.0, "{svg}");
    }

    /// A horizontal legend flows its entries across `columns` (the
    /// resolver's compact wrap, else one row): the box is the shared
    /// grid's, so the plot gives up exactly its height on the bottom edge.
    #[test]
    fn a_wrapped_horizontal_legend_gives_the_plot_its_taller_box() {
        let mut p = legend_pie_payload("bottom", "start", "horizontal");
        p["resolved"]["legend"]["columns"] = json!(2);
        let (_, box_h) = legend_box(&p, &LEGEND_LABELS);
        let (_, one_row) = legend_box(&legend_pie_payload("bottom", "start", "horizontal"), &LEGEND_LABELS);
        assert!(box_h > one_row, "two columns wrap three entries onto two rows");
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        let (_, cy) = wheel_center(&svg);
        assert_eq!(cy, 16.0 + (400.0 - 18.0 - box_h) / 2.0, "{svg}");
    }

    /// `legend_align.py:100-103`: center is `(span - block) / 2`, end is
    /// `span - block`, against the plot rect the scenegraph reports (a pie
    /// with no label ink converges, so that is the drawn plot).
    #[test]
    fn center_and_end_slide_the_legend_along_its_strip() {
        for (align, share) in [("start", 0.0), ("center", 0.5), ("end", 1.0)] {
            let p = legend_pie_payload("right", align, "vertical");
            let (box_w, box_h) = legend_box(&p, &LEGEND_LABELS);
            let svg = render(&make_payload(p), &test_fonts()).unwrap();
            let title_baseline = 16.0 + share * (400.0 - box_h) + 9.0;
            let x = 16.0 + (568.0 - 18.0 - box_w).ceil() + 18.0;
            assert!(
                svg.contains(&format!("<text x=\"{x:.2}\" y=\"{title_baseline:.2}\"")),
                "{align}: title at ({x}, {title_baseline}): {svg}"
            );
        }
    }

    /// An unstroked slice mark gives an unstroked symbol (`d.stroke` is
    /// never set); the legend keeps Vega's 1.5 width and draws no ring.
    #[test]
    fn an_unstroked_slice_mark_gives_unstroked_legend_symbols() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["style"]["slice_mark"]["stroke"] = Value::Null;
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(svg.matches("fill=\"#3164a3\" stroke-width=\"1.5\"/>").count(), 1, "{svg}");
        assert!(!svg.contains("stroke=\"#FFFFFF\" stroke-width=\"1.5\""), "{svg}");
        // The bare glyph's bound tightens the row pitch from the ringed 16 to
        // 13 (`gridLayout`: `rowPadding + ceil(-y1) + ceil(y2)`: 2 + 0 + 11, not 2 + 1 + 13).
        let ys = |svg: &str| -> Vec<f64> {
            svg.split("<path transform=\"translate(")
                .skip(1)
                .map(|t| t.split(')').next().unwrap().split_once(',').unwrap().1.parse().unwrap())
                .collect()
        };
        let pitch = |svg: &str| ys(svg)[1] - ys(svg)[0];
        assert_eq!(pitch(&svg), 13.0, "{svg}");
        let stroked = render(&make_payload(legend_pie_payload("right", "start", "vertical")), &test_fonts()).unwrap();
        assert_eq!(pitch(&stroked), 16.0, "{stroked}");
    }

    /// Every legend shape this port does not draw refuses by name rather
    /// than drawing a guess.
    #[test]
    fn legend_shapes_the_port_does_not_draw_refuse_by_name() {
        type Edit = fn(&mut Value);
        let cases: [(&str, Edit, &str); 10] = [
            ("symbol_shape", |p| p["resolved"]["legend"]["symbol_shape"] = json!("square"), "legend.symbol_shape"),
            ("symbol_fill", |p| p["resolved"]["legend"]["symbol_fill"] = json!(false), "legend.symbol_fill"),
            ("empty values", |p| p["resolved"]["legend"]["values"] = json!([]), "legend.values: []"),
            ("opacity", |p| p["resolved"]["style"]["slice_mark"]["opacity"] = json!(0.5), "slice opacity"),
            ("quantitative", |p| p["resolved"]["resolved_channels"]["color"]["quantitative_data"] = json!(true), "quantitative color"),
            ("no flag", |p| p["resolved"]["resolved_channels"]["color"]["quantitative_data"] = Value::Null, "quantitative_data flag"),
            ("gradient", |p| p["resolved"]["resolved_channels"]["color"]["mode"] = json!("gradient"), "gradient legend"),
            ("number", |p| p["rows"][0]["category"] = json!(7), "non-string color value"),
            ("date", |p| p["rows"][0]["category"] = json!("2024-01-31"), "date-like color"),
            ("overlay", |p| p["resolved"]["legend"]["position"]["overlay"] = json!(true), "overlay true"),
        ];
        for (name, edit, expected) in cases {
            let mut p = legend_pie_payload("right", "start", "vertical");
            edit(&mut p);
            let err = match serde_json::from_value::<Payload>(p) {
                Ok(payload) => render(&payload, &test_fonts()).unwrap_err().to_string(),
                Err(e) => e.to_string(),
            };
            assert!(err.contains(expected), "{name}: {err}");
        }
    }

    /// The legend and an attached table are alternative keys
    /// (`suppress_legend=mode != "direct"`), and an empty result draws no
    /// legend rows: both combinations refuse by name.
    #[test]
    fn a_visible_legend_beside_an_attached_table_or_over_no_rows_refuses() {
        let mut attached = legend_pie_payload("right", "start", "vertical");
        attached["resolved"]["attached_table"] = json!({"columns": {}});
        let err = render(&make_payload(attached), &test_fonts()).unwrap_err();
        assert!(err.contains("beside an attached table"), "{err}");

        let mut empty = legend_pie_payload("right", "start", "vertical");
        empty["rows"] = json!([]);
        let err = render_empty(&make_payload(empty), &test_fonts()).unwrap_err();
        assert!(err.contains("empty result set"), "{err}");
    }

    /// An authored `legend.values` (`apply_legend_entry_order`) orders and
    /// filters the entries drawn; each keeps the swatch its own value has in
    /// the scale, and a token that matches nothing is dropped.
    #[test]
    fn authored_legend_values_order_and_filter_the_entries() {
        let mut p = legend_pie_payload("right", "start", "vertical");
        p["resolved"]["legend"]["values"] = json!(["tools", "Electronics", "Nothing"]);
        let svg = render(&make_payload(p), &test_fonts()).unwrap();
        assert_eq!(
            legend_swatches(&svg),
            [("#ad9c7f", "Tools"), ("#3164a3", "Electronics")].map(|(f, l)| (f.to_string(), l.to_string())),
            "{svg}"
        );

        let mut none = legend_pie_payload("right", "start", "vertical");
        none["resolved"]["legend"]["values"] = json!(["x"]);
        let svg = render(&make_payload(none), &test_fonts()).unwrap();
        assert_eq!(legend_swatches(&svg).len(), 3, "nothing resolving leaves the full order: {svg}");
    }

    /// `legend-symbol.js`: a vertical legend with `columns` > 1 fills
    /// column-major (`row = index % ceil(n / columns)`), a horizontal one
    /// row-major (`row = floor(index / columns)`). Three entries over two
    /// columns: vertical gives Electronics/Accessories down the first column
    /// and Tools on top of the second; horizontal gives Electronics and
    /// Accessories across the first row.
    #[test]
    fn a_vertical_legend_over_columns_fills_column_major_and_a_horizontal_one_row_major() {
        let cells = |direction: &str, edge: &str| -> Vec<(String, f64, f64)> {
            let mut p = legend_pie_payload(edge, "start", direction);
            p["resolved"]["legend"]["columns"] = json!(2);
            p["resolved"]["legend"]["title"]["visible"] = json!(false);
            let svg = render(&make_payload(p), &test_fonts()).unwrap();
            let labels: Vec<String> = legend_swatches(&svg).into_iter().map(|(_, label)| label).collect();
            let positions: Vec<(f64, f64)> = svg
                .split("<path transform=\"translate(")
                .skip(1)
                .map(|t| {
                    let (x, y) = t.split(')').next().unwrap().split_once(',').unwrap();
                    (x.parse().unwrap(), y.parse().unwrap())
                })
                .collect();
            labels.into_iter().zip(positions).map(|(label, (x, y))| (label, x, y)).collect()
        };
        let vertical = cells("vertical", "right");
        let order: Vec<&str> = vertical.iter().map(|(label, ..)| label.as_str()).collect();
        assert_eq!(order, ["Electronics", "Tools", "Accessories"], "{vertical:?}");
        let (electronics, tools, accessories) = (&vertical[0], &vertical[1], &vertical[2]);
        assert!(tools.1 > electronics.1 && tools.2 == electronics.2, "Tools heads column two: {vertical:?}");
        assert!(accessories.1 == electronics.1 && accessories.2 > electronics.2, "Accessories under Electronics: {vertical:?}");

        let horizontal = cells("horizontal", "bottom");
        let order: Vec<&str> = horizontal.iter().map(|(label, ..)| label.as_str()).collect();
        assert_eq!(order, ["Electronics", "Accessories", "Tools"], "{horizontal:?}");
        assert!(horizontal[1].1 > horizontal[0].1 && horizontal[1].2 == horizontal[0].2, "{horizontal:?}");
        assert!(horizontal[2].1 == horizontal[0].1 && horizontal[2].2 > horizontal[0].2, "{horizontal:?}");
    }

    /// Both center-total text layers are `aria: False`: hidden mark sets whose
    /// text carries no per-datum label, with or without a tooltip format.
    #[test]
    fn the_center_total_is_a_hidden_mark_set_with_no_per_datum_label() {
        for tooltip_format in [json!(",.2~f"), Value::Null] {
            let mut p = make_pie_payload_with_labels("{{ category }}");
            p["resolved"]["total"] = json!({"visible": true, "label": "Total Revenue"});
            p["resolved"]["legend"] = json!({
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "label": {"font": {"size": 11.0}},
                "title": {"font": {"case": "title"}},
            });
            if !tooltip_format.is_null() {
                p["resolved"]["style"]["tooltip_format"] = tooltip_format;
            }
            let svg = render(&make_payload(p), &test_fonts()).expect("renders");
            for layer in ["layer_1_marks", "layer_2_marks"] {
                let open = format!("<g class=\"mark-text role-mark {layer}\" aria-hidden=\"true\">");
                let start = svg.find(&open).unwrap_or_else(|| panic!("{layer} not hidden: {svg}"));
                let group = &svg[start..start + svg[start..].find("</g>").unwrap()];
                assert!(!group.contains("aria-label") && !group.contains("role="), "{layer}: {group}");
            }
        }
    }

    /// Oracle: `format_d3(197199, ",.2~f", "US$", "", None, placement)` is
    /// `'US$197,199'` and the total `'US$443,023'`; the share keeps `.0%`.
    #[test]
    fn a_tooltip_row_paints_its_affix() {
        let mut p = make_pie_payload(Value::Null);
        p["resolved"]["style"]["tooltip_format"] = json!({"spec": ",.2~f", "prefix": "US$"});
        p["resolved"]["legend"] = json!({
            "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}},
            "title": {"font": {"case": "title"}},
        });
        let svg = render(&make_payload(p), &test_fonts()).expect("renders");
        assert!(
            svg.contains("aria-label=\"⁤Electronics; %: 45%; \u{2060}Revenue: US$197,199; \u{2060}⁣Σ: US$443,023\""),
            "{svg}"
        );
    }

    // ── The family contract: bind, intrinsic, height_for_width, layout, render ──

    use crate::layout::Rect;
    use measure::Measure;

    fn cell(width: f64, height: f64) -> Rect {
        Rect { x: 0.0, y: 0.0, width, height }
    }

    fn contract_payload() -> Payload {
        let mut json = make_pie_payload(json!({"visible": true, "label": "Total", "format": null}));
        json["resolved"]["link"] = json!("/b/{{ color }}");
        json["resolved"]["resolved_channels"] =
            json!({"color": {"channel": "color", "mode": "series", "data_field": "category", "quantitative_data": false}});
        json["resolved"]["slice_label_indices"] = json!([1]);
        json["resolved"]["style"]["tooltip_format"] = json!(",.0f");
        json["resolved"]["style"]["slice_mark"]["labels"] = json!({
            "offset": 6.0, "line_height": 14.0,
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "template": "{{ category }}"
        });
        json["resolved"]["legend"]["visible"] = json!(true);
        json["resolved"]["legend"]["label"]["padding"] = json!(8.0);
        make_payload(json)
    }

    #[test]
    fn bind_derives_the_slices_labels_total_and_legend_without_a_font() {
        let payload = contract_payload();
        let bound = bind::bind(&payload).expect("binds");
        let labels = &bound.wheel.labels;
        assert_eq!(bound.thetas, vec![197199.0, 152504.0, 93320.0]);
        assert_eq!(bound.total, 443023.0);
        let fills: Vec<&str> = bound.wheel.slices.iter().map(|s| s.fill.as_str()).collect();
        assert_eq!(fills, ["#3164a3", "#779bc9", "#ad9c7f"]);
        assert_eq!(bound.wheel.slices[2].href.as_deref(), Some("/b/Tools"));
        assert!(bound.wheel.slices[0].aria.is_some());
        assert_eq!(labels.len(), 1);
        assert_eq!((labels[0].idx, labels[0].lines.clone()), (1, vec!["Accessories".to_string()]));
        assert_eq!(bound.wheel.center.as_deref(), Some("443023"));
        assert_eq!(bound.wheel.legend.as_ref().expect("a drawn legend").entries.len(), 3);
        assert!(bound.attached.is_none());
    }

    #[test]
    fn a_pie_hugs_nothing_and_its_height_is_the_slot() {
        let payload = contract_payload();
        let bound = bind::bind(&payload).expect("binds");
        let measure = Measure { fonts: &test_fonts() };
        assert_eq!(layout::intrinsic(&bound, &measure).expect("intrinsic"), None);
        assert_eq!(layout::height_for_width(&bound, 123.0, &measure).expect("height"), 432.0);
    }

    #[test]
    fn an_attached_table_pie_reads_its_height_back_from_the_layout_it_draws() {
        for placement in ["bottom", "top", "right"] {
            let payload = make_payload(make_pie_payload_with_attached_table(placement, "Heading"));
            let bound = bind::bind(&payload).expect("binds");
            let fonts = test_fonts();
            let measure = Measure { fonts: &fonts };
            let natural = layout::height_for_width(&bound, payload.width, &measure).expect("height");
            let svg = render(&payload, &fonts).expect("renders");
            assert_eq!(natural, canvas_height(&svg), "{placement}");
        }
    }

    #[test]
    fn layout_draws_the_cell_it_is_given_and_returns_the_warnings_it_raised() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = json!("a title far too long for a narrow card to hold");
        json["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "overflow": "truncate",
            "position": {"offset": 10.0}
        });
        let payload = make_payload(json);
        let bound = bind::bind(&payload).expect("binds");
        let fonts = test_fonts();
        let laid_out = layout::layout(&bound, cell(220.0, 180.0), &Measure { fonts: &fonts }).expect("lays out");
        let [scene::Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else { panic!("one root card") };
        assert_eq!((extent.width, extent.height), (220.0, 180.0));
        assert_eq!(laid_out.diagnostics.len(), 1, "{:?}", laid_out.diagnostics);
        assert_eq!(laid_out.diagnostics[0].code, crate::codes::WARN_CHART_TITLE_TRUNCATED.id);
        assert!(laid_out.data_box.width > 0.0 && laid_out.insets.left >= 0.0);
    }

    #[test]
    fn render_prints_the_layout_of_the_payloads_own_cell() {
        let payload = contract_payload();
        let bound = bind::bind(&payload).expect("binds");
        let fonts = test_fonts();
        let laid_out = layout::layout(&bound, cell(payload.width, payload.height), &Measure { fonts: &fonts }).expect("lays out");
        assert_eq!(print::print(&laid_out.nodes), render(&payload, &fonts).expect("renders"));
    }

    #[test]
    fn a_layout_that_fails_hands_the_warnings_it_raised_to_the_sink() {
        let mut json = make_pie_payload(Value::Null);
        json["resolved"]["title"] = json!("a title far too long for a narrow card to hold");
        json["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "overflow": "truncate",
            "position": {"offset": 10.0}
        });
        let payload = make_payload(json);
        let bound = bind::bind(&payload).expect("binds");
        let fonts = test_fonts();
        let (laid_out, warnings) =
            crate::diagnostic::collect(|| layout::layout(&bound, cell(220.0, 14.0), &Measure { fonts: &fonts }));
        assert!(laid_out.is_err());
        assert_eq!(warnings.len(), 1, "{warnings:?}");
    }

    #[test]
    fn an_empty_result_binds_no_slices_and_is_never_faded() {
        let mut json = make_pie_payload(Value::Null);
        json["rows"] = json!([]);
        json["is_placeholder"] = json!(true);
        let payload = make_payload(json);
        let bound = bind::bind_empty(&payload).expect("binds");
        assert!(bound.wheel.slices.is_empty() && bound.thetas.is_empty());
        assert!(!bound.faded);
    }

    #[test]
    fn a_payload_with_two_defects_names_the_first_one_bind_meets() {
        let mut json = make_pie_payload(json!({"visible": true, "label": null, "format": ",.0f"}));
        json["width"] = json!(220.0);
        json["resolved"]["title"] = json!("a title far too long for a narrow card to hold");
        json["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 14.0, "weight": "500"},
            "overflow": "truncate",
            "position": {"offset": 10.0}
        });
        json["resolved"]["style"]["tooltip_format"] = json!(",.0f");
        json["resolved"]["legend"]["title"]["font"]["case"] = Value::Null;
        let payload = make_payload(json);
        // The retired total key is met before the tooltip title's missing case, and both before
        // any layout work: the title is never cut, so no WARN is raised.
        assert_eq!(
            bind::bind(&payload).err().expect("bind refuses"),
            "pie: total.format moved to style.total_style.value.format"
        );
        let (rendered, warnings) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert_eq!(rendered.unwrap_err(), "pie: total.format moved to style.total_style.value.format");
        assert!(warnings.is_empty(), "{warnings:?}");
    }

    #[test]
    fn an_attached_table_error_keeps_the_tables_own_code() {
        let mut json = make_pie_payload_with_attached_table("bottom", "");
        json["resolved"]["palette"] = json!(["not-a-color", "#779bc9", "#ad9c7f"]);
        let payload = make_payload(json);
        let err = render(&payload, &test_fonts()).expect_err("a swatch that is no color refuses");
        assert_eq!(err.code, crate::codes::ERR_TABLE_SWATCH_NOT_COLOR.id, "{err}");
    }

    #[test]
    fn a_legend_slide_refusal_keeps_its_code() {
        let mut json = legend_pie_payload("right", "center", "vertical");
        json["height"] = json!(120.0);
        json["rows"] = json!((0..14)
            .map(|i| json!({"category": format!("Category number {i}"), "revenue": 100.0 + f64::from(i)}))
            .collect::<Vec<_>>());
        let err = render(&make_payload(json), &test_fonts()).expect_err("a legend taller than its plot cannot center");
        assert_eq!(err.code, crate::codes::ERR_LEGEND_ALIGN_UNSUPPORTED.id, "{err}");
    }
}
