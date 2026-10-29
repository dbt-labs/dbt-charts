//! The row title shift: derived from the row's titles, then applied.
//!
//! Production's `render/title_band.py::row_title_shifts` puts every titled
//! cell of a `cols` row on one first baseline and one body top;
//! `layout_sizing.py::_assign_title_shifts_in_tree` stores the result as a
//! `TitleShift` on each layout item. [`row_title_shifts`] and
//! [`chart_title_metrics`] are the law and the per-chart title geometry;
//! `board/row_shifts.rs` walks the board tree with them. [`for_family`] is the
//! application.
//!
//! The Vega-family charts (cartesian, pie, geo) take the shift as a payload
//! rewrite — a larger `title.offset` and top padding — so every family's own
//! padding and title-gap law runs over them unchanged. Table and spark_bar
//! read `title_shift` themselves.

use std::borrow::Cow;

use serde::Deserialize;
use serde_json::Value;

use crate::{Padding, Payload};

/// `compile/models/board/normalized.py::TitleShift`: px the title moves down
/// (`title_dy`) and px everything below it moves down (`body_dy`, never less).
#[derive(Deserialize, Clone, Copy, Debug, Default, PartialEq)]
pub struct TitleShift {
    pub title_dy: f64,
    pub body_dy: f64,
}

impl TitleShift {
    pub fn is_zero(&self) -> bool {
        self.title_dy == 0.0 && self.body_dy == 0.0
    }
}

/// The payload a `chart_type`'s renderer draws from, with the item's shift
/// applied the way production applies it to that family.
///
/// A shift is assigned only to a titled item of a Vega-family chart that
/// paints marks, a table, a spark_bar or a custom chart (`title_band.py::chart_title_metrics`);
/// a kpi, a callout, a sankey and an attached-table pie are untitled for the
/// band and always carry a zero shift. A shift anywhere else refuses by name
/// rather than painting an unshifted title.
pub fn for_family<'a>(payload: &'a Payload, chart_type: &str) -> Result<Cow<'a, Payload>, String> {
    if payload.title_shift.is_zero() {
        return Ok(Cow::Borrowed(payload));
    }
    match chart_type {
        "bar" | "line" | "area" | "scatter" | "heatmap" | "histogram" | "custom" | "geoshape"
        | "point_map" => {
            shift_vega_payload(payload, payload.title_shift)
        }
        "pie" => {
            let name = payload.pie_mode.as_deref().ok_or("pie: a title shift needs the pie's sized mode")?;
            match crate::families::pie::Mode::parse(name)? {
                crate::families::pie::Mode::Direct => shift_vega_payload(payload, payload.title_shift),
                _ => Err("pie: a title shift over an attached table is not modeled — production keeps that \
                          composite out of the title band"
                    .to_string()),
            }
        }
        "table" | "spark_bar" => Ok(Cow::Borrowed(payload)),
        other => Err(format!(
            "{other}: production assigns no title shift to this family"
        )),
    }
}

/// The shift on a Vega-family chart's payload.
///
/// `title.offset` moves the plot alone, by the whole pixels
/// `round(body_dy - title_dy)` (Python's round-half-even); the top padding
/// carries the rest of `body_dy` and moves title and plot together. The plot
/// lands exactly `body_dy` lower and the baseline within half a pixel of
/// `title_dy`.
///
/// `shift` is a parameter because the pie's attached-table composition shifts
/// its own wheel by the room a top-placed table takes, not by a row's shift.
pub(crate) fn shift_vega_payload(
    payload: &Payload,
    shift: TitleShift,
) -> Result<Cow<'_, Payload>, String> {
    if shift.is_zero() {
        return Ok(Cow::Borrowed(payload));
    }
    let chart_id = payload
        .resolved
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("<unknown>");
    // Production's helper indexes `spec["title"]`: a titled chart is the only
    // one `chart_title_metrics` hands a shift to.
    let titled = payload
        .resolved
        .get("title")
        .is_some_and(|t| !t.is_null() && t.as_str() != Some(""));
    if !titled {
        return Err(format!(
            "chart {chart_id}: a title shift needs a title block to move"
        ));
    }
    let padding = payload
        .padding
        .ok_or_else(|| format!("chart {chart_id}: a title shift needs the 4-key spec padding"))?;
    let offset = payload
        .resolved
        .pointer("/title_style/position/offset")
        .and_then(Value::as_f64)
        .ok_or_else(|| {
            format!(
                "chart {chart_id}: a title shift over an unset title_style.position.offset \
                 would write Vega's default 4 plus the gap (spec_builders.py:91, :119), \
                 a title placement this port does not model"
            )
        })?;
    let gap_extra = crate::sizing::python_round(shift.body_dy - shift.title_dy);
    let mut shifted = payload.clone();
    shifted.padding = Some(Padding {
        top: padding.top + shift.body_dy - gap_extra,
        ..padding
    });
    *shifted
        .resolved
        .pointer_mut("/title_style/position/offset")
        .expect("pointer read the offset above") = Value::from(offset + gap_extra);
    shifted.title_shift = TitleShift::default();
    Ok(Cow::Owned(shifted))
}

/// One item's natural title geometry, measured from the item's top edge
/// (`title_band.py:59-64` `TitleMetrics`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TitleMetrics {
    /// Top edge to the first baseline.
    pub ascent: f64,
    /// First baseline to the top of the body.
    pub tail: f64,
}

/// Per-item shifts that put every title on one baseline and every body on one
/// top (`title_band.py:67-87` `row_title_shifts`). `None` marks an item with no
/// title: it gets a zero shift and adds nothing to the band.
pub fn row_title_shifts(metrics: &[Option<TitleMetrics>]) -> Vec<TitleShift> {
    let titled = || metrics.iter().flatten();
    // `title_band.py:73-78`: the shared baseline is the largest ascent, and each
    // title block, once on it, ends at baseline + tail.
    let baseline = titled().map(|m| m.ascent).fold(f64::NEG_INFINITY, f64::max);
    let body_top = baseline + titled().map(|m| m.tail).fold(f64::NEG_INFINITY, f64::max);
    metrics
        .iter()
        .map(|m| match m {
            None => TitleShift::default(),
            Some(m) => TitleShift {
                title_dy: baseline - m.ascent,
                body_dy: body_top - m.ascent - m.tail,
            },
        })
        .collect()
}

/// Vega-Lite's default `title.offset` (`spec_builders.py:91`) when the style
/// leaves it unset.
const VEGA_TITLE_OFFSET: f64 = 4.0;

/// The title geometry of a chart's own title block, `None` when the band does
/// not align it (`title_band.py:90-145` `chart_title_metrics`).
///
/// `slot_width` is the width the chart's SVG family draws in (the item's width
/// less its padding); only a table's title wrap reads it. A Vega-Lite title is
/// one line in production's metrics, so a wrapping overflow mode does not
/// enter: the title block this returns is the font size, one subtitle line and
/// the offset, and the sizing pass shares one law with the renderer's shift.
pub fn chart_title_metrics(
    chart: &Value,
    card_padding: f64,
    slot_width: f64,
    fonts: &measure::FontBook,
    spark_title: Option<&crate::SparkBarTitle>,
    pie_mode: Option<crate::families::pie::Mode>,
) -> Result<Option<TitleMetrics>, String> {
    // `title_band.py:106`: a subtitle-only chart draws no title block.
    if !chart
        .get("title")
        .and_then(Value::as_str)
        .is_some_and(|t| !t.is_empty())
    {
        return Ok(None);
    }
    let chart_type = chart
        .get("chart_type")
        .and_then(Value::as_str)
        .ok_or("title metrics: chart missing chart_type")?;
    // `title_band.py:108-111`: the composite anchors its table from the wheel's
    // own height, which a shift does not change.
    if chart_type == "pie" {
        let mode = pie_mode.ok_or("title metrics: a pie needs its sized mode")?;
        if mode != crate::families::pie::Mode::Direct {
            return Ok(None);
        }
    }
    let chart_id = chart
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("<unknown>");
    let layout_top = chart
        .pointer("/layout_padding/top")
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("chart {chart_id}: layout_padding.top missing"))?;
    let inset_top = card_padding + layout_top;
    match chart_type {
        "table" => {
            let (ascent, tail) = crate::families::table::layout::title_geometry(chart, slot_width, fonts)?;
            Ok(Some(TitleMetrics {
                ascent: inset_top + ascent,
                tail,
            }))
        }
        "spark_bar" => {
            let (ascent, tail) = crate::families::spark_bar::title_geometry(
                chart,
                spark_title.ok_or("spark_bar title: the board style carries no spark_bar_title")?,
            )?;
            Ok(Some(TitleMetrics {
                ascent: inset_top + ascent,
                tail,
            }))
        }
        // `title_band.py:140`: the types in `PAINTS_MARKS`, and a custom chart,
        // which draws its title in the same geometry; a kpi, callout and sankey
        // have no title slot.
        "bar" | "histogram" | "line" | "area" | "scatter" | "heatmap" | "pie" | "geoshape"
        | "point_map" | "custom" => {
            let (ascent, tail) = vega_title_block(chart)?;
            Ok(Some(TitleMetrics {
                ascent: inset_top + ascent,
                tail,
            }))
        }
        _ => Ok(None),
    }
}

/// The Vega-Lite title block of a resolved chart as `(ascent, tail)`: the top
/// to the first baseline (`title_ascent`), and that baseline to the body top
/// (the title's size, one subtitle line plus its padding, `title.offset`).
/// Both zero for an untitled chart (`title_band.py::vega_title_block_height`).
pub(crate) fn vega_title_block(chart: &Value) -> Result<(f64, f64), String> {
    if !chart
        .get("title")
        .and_then(Value::as_str)
        .is_some_and(|t| !t.is_empty())
    {
        return Ok((0.0, 0.0));
    }
    let chart_id = chart
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("<unknown>");
    let size = chart
        .pointer("/style/title_font/size")
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("chart {chart_id}: a titled chart needs style.title_font.size"))?;
    let ascent = crate::sizing::title_ascent(size);
    let has_subtitle = chart
        .get("subtitle")
        .and_then(Value::as_str)
        .is_some_and(|s| !s.is_empty());
    let subtitle = if has_subtitle {
        let subtitle_size = chart
            .pointer("/title_style/subtitle/font/size")
            .and_then(Value::as_f64)
            .ok_or_else(|| {
                format!("chart {chart_id}: a subtitle needs title_style.subtitle.font.size")
            })?;
        subtitle_size + crate::constants::layout::VEGA_SUBTITLE_PADDING
    } else {
        0.0
    };
    let offset = chart
        .pointer("/title_style/position/offset")
        .and_then(Value::as_f64)
        .unwrap_or(VEGA_TITLE_OFFSET);
    Ok((ascent, size + subtitle + offset - ascent))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SPARK_TITLE: crate::SparkBarTitle = crate::SparkBarTitle {
        height: 24.0,
        baseline_y: 16.0,
    };
    use measure::FontBook;
    use serde_json::json;

    fn m(ascent: f64, tail: f64) -> Option<TitleMetrics> {
        Some(TitleMetrics { ascent, tail })
    }

    fn shifts(metrics: &[Option<TitleMetrics>]) -> Vec<(f64, f64)> {
        row_title_shifts(metrics)
            .iter()
            .map(|s| (s.title_dy, s.body_dy))
            .collect()
    }

    // 24px and 18px bars, offset 10, card padding 16: ascents 16 + round(19.2) = 35 and
    // 16 + round(14.4) = 30; tails (24 + 10) - 19 = 15 and (18 + 10) - 14 = 14. The shared
    // baseline is 35 and the shared body top 35 + 15 = 50, so the 18px bar's title drops
    // 35 - 30 = 5 and its body 50 - 30 - 14 = 6.
    #[test]
    fn titles_of_different_size_share_the_largest_ascent_and_the_lowest_block_end() {
        let big = chart_title_metrics(
            &bar(24.0, None, Some(10.0)),
            16.0,
            500.0,
            &FontBook::empty(),
            None,
            None,
        );
        let small = chart_title_metrics(
            &bar(18.0, None, Some(10.0)),
            16.0,
            500.0,
            &FontBook::empty(),
            None,
            None,
        );
        assert_eq!(big.clone().unwrap(), m(35.0, 15.0));
        assert_eq!(small.clone().unwrap(), m(30.0, 14.0));
        assert_eq!(
            shifts(&[big.unwrap(), small.unwrap()]),
            [(0.0, 0.0), (5.0, 6.0)]
        );
    }

    // A subtitle lengthens the tail by its font size plus Vega's 3px subtitle padding:
    // 18 + (11 + 3) + 10 - 14 = 28 against the neighbor's 14, so the neighbor's body drops 14.
    #[test]
    fn a_subtitle_lengthens_the_tail_and_drops_the_neighbors_body() {
        let with_subtitle = chart_title_metrics(
            &bar(18.0, Some(11.0), Some(10.0)),
            16.0,
            500.0,
            &FontBook::empty(),
            None,
            None,
        )
        .unwrap();
        let plain = chart_title_metrics(
            &bar(18.0, None, Some(10.0)),
            16.0,
            500.0,
            &FontBook::empty(),
            None,
            None,
        )
        .unwrap();
        assert_eq!(with_subtitle, m(30.0, 28.0));
        assert_eq!(shifts(&[with_subtitle, plain]), [(0.0, 0.0), (0.0, 14.0)]);
    }

    // A subtitle-only chart has no title block (`not chart.title`), so it is
    // untitled for the band: it takes no shift and adds nothing to the row.
    #[test]
    fn a_row_mixing_a_titled_chart_and_a_subtitle_only_chart_shifts_neither() {
        let fonts = FontBook::empty();
        let mut subtitle_only = bar(18.0, Some(11.0), Some(10.0));
        subtitle_only["title"] = Value::Null;
        let titled =
            chart_title_metrics(&bar(24.0, None, Some(10.0)), 16.0, 500.0, &fonts, None, None).unwrap();
        let bare = chart_title_metrics(&subtitle_only, 16.0, 500.0, &fonts, None, None).unwrap();
        assert_eq!(bare, None);
        assert_eq!(shifts(&[titled, bare]), [(0.0, 0.0), (0.0, 0.0)]);
    }

    // A kpi draws its label under the value and has no title slot: it gets no metrics, so it
    // neither moves nor widens the band the two bars share.
    #[test]
    fn a_kpi_beside_bars_takes_no_part_in_the_band() {
        let fonts = FontBook::empty();
        let kpi = chart_title_metrics(&chart_of("kpi", 18.0), 16.0, 500.0, &fonts, None, None).unwrap();
        assert_eq!(kpi, None);
        let big =
            chart_title_metrics(&bar(24.0, None, Some(10.0)), 16.0, 500.0, &fonts, None, None).unwrap();
        let small =
            chart_title_metrics(&bar(18.0, None, Some(10.0)), 16.0, 500.0, &fonts, None, None).unwrap();
        assert_eq!(
            shifts(&[big, kpi, small]),
            [(0.0, 0.0), (0.0, 0.0), (5.0, 6.0)]
        );
    }

    // A lone titled cell is its own band, and a row with no titles has none.
    #[test]
    fn a_single_item_row_and_an_untitled_row_compute_zero() {
        assert_eq!(shifts(&[m(35.0, 15.0)]), [(0.0, 0.0)]);
        assert_eq!(shifts(&[None, None]), [(0.0, 0.0), (0.0, 0.0)]);
    }

    // `chart_title_metrics` hands a title to a Vega-Lite family that paints marks, a table
    // and a spark_bar; a callout, a sankey and an attached-table pie get none.
    #[test]
    fn families_without_a_shared_title_slot_get_no_metrics() {
        let fonts = FontBook::empty();
        for family in ["callout", "sankey", "kpi"] {
            let got =
                chart_title_metrics(&chart_of(family, 18.0), 16.0, 500.0, &fonts, None, None).unwrap();
            assert_eq!(got, None, "{family}");
        }
        use crate::families::pie::Mode;
        let pie = chart_of("pie", 18.0);
        for mode in [Mode::Hybrid, Mode::FullTable] {
            assert_eq!(
                chart_title_metrics(&pie, 16.0, 500.0, &fonts, None, Some(mode)).unwrap(),
                None
            );
        }
        let plain_pie = pie;
        for family in [
            "pie",
            "geoshape",
            "point_map",
            "histogram",
            "heatmap",
            "scatter",
            "area",
            "line",
        ] {
            let mut c = plain_pie.clone();
            c["chart_type"] = json!(family);
            let mode = (family == "pie").then_some(Mode::Direct);
            assert_eq!(
                chart_title_metrics(&c, 16.0, 500.0, &fonts, None, mode).unwrap(),
                m(30.0, 14.0),
                "{family}"
            );
        }
    }

    // Vega writes its default 4px title offset when the style leaves it unset
    // (`spec_builders.py:91`): 18 + 4 - 14 = 8. The layout padding top moves the ascent.
    #[test]
    fn an_unset_offset_is_vegas_default_and_layout_padding_moves_the_ascent() {
        let fonts = FontBook::empty();
        assert_eq!(
            chart_title_metrics(&bar(18.0, None, None), 16.0, 500.0, &fonts, None, None).unwrap(),
            m(30.0, 8.0)
        );
        let mut padded = bar(18.0, None, Some(10.0));
        padded["layout_padding"]["top"] = json!(4.0);
        assert_eq!(
            chart_title_metrics(&padded, 16.0, 500.0, &fonts, None, None).unwrap(),
            m(34.0, 14.0)
        );
    }

    // spark_bar: the title band is the config's 24px, baseline 16px down, and a subtitle
    // adds the title's whole-pixel font size (`spark_bar.py:427-431`).
    #[test]
    fn spark_bar_reads_its_own_title_band() {
        let fonts = FontBook::empty();
        let mut spark = chart_of("spark_bar", 11.0);
        assert_eq!(
            chart_title_metrics(&spark, 16.0, 500.0, &fonts, Some(&SPARK_TITLE), None).unwrap(),
            m(32.0, 8.0)
        );
        spark["subtitle"] = json!("sub");
        assert_eq!(
            chart_title_metrics(&spark, 16.0, 500.0, &fonts, Some(&SPARK_TITLE), None).unwrap(),
            m(32.0, 19.0)
        );
    }

    #[test]
    fn a_titled_vega_chart_without_a_title_font_size_refuses_by_name() {
        let mut c = bar(18.0, None, Some(10.0));
        c["style"]["title_font"]["size"] = Value::Null;
        let err = chart_title_metrics(&c, 16.0, 500.0, &FontBook::empty(), None, None).unwrap_err();
        assert!(err.contains("style.title_font.size"), "{err}");
    }

    fn chart_of(chart_type: &str, title_size: f64) -> Value {
        json!({
            "id": "c",
            "chart_type": chart_type,
            "title": "Revenue",
            "subtitle": "",
            "layout_padding": {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0},
            "style": {"title_font": {"size": title_size, "family": "serif", "weight": 500.0}},
            "title_style": {"position": {"offset": 10.0}, "subtitle": {"font": {"size": 11.0}}},
        })
    }

    fn bar(title_size: f64, subtitle_size: Option<f64>, offset: Option<f64>) -> Value {
        let mut c = chart_of("bar", title_size);
        c["title_style"]["position"]["offset"] = offset.map_or(Value::Null, Value::from);
        if let Some(size) = subtitle_size {
            c["subtitle"] = json!("A subtitle");
            c["title_style"]["subtitle"]["font"]["size"] = json!(size);
        }
        c
    }

    fn payload(chart_type: &str, shift: (f64, f64), mutate: impl FnOnce(&mut Value)) -> Payload {
        let mut resolved = json!({
            "id": "c",
            "chart_type": chart_type,
            "title": "Revenue",
            "title_style": {"position": {"offset": 10.0}},
        });
        mutate(&mut resolved);
        serde_json::from_value(json!({
            "resolved": resolved,
            "rows": [{"x": 1}],
            "width": 500.0,
            "height": 300.0,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0},
            "title_shift": {"title_dy": shift.0, "body_dy": shift.1},
        }))
        .unwrap()
    }

    fn shifted(p: &Payload, chart_type: &str) -> Result<(f64, f64), String> {
        let out = for_family(p, chart_type)?;
        let offset = out
            .resolved
            .pointer("/title_style/position/offset")
            .and_then(Value::as_f64)
            .unwrap();
        Ok((out.padding.unwrap().top, offset))
    }

    // `shift_chart_title`: offset gains round(body_dy - title_dy), the top
    // padding gains the rest of body_dy, so the plot moves exactly body_dy.
    #[test]
    fn the_plot_moves_body_dy_and_the_title_about_title_dy() {
        let p = payload("bar", (3.0, 4.0), |_| {});
        assert_eq!(shifted(&p, "bar").unwrap(), (16.0 + 3.0, 10.0 + 1.0));
    }

    #[test]
    fn a_map_takes_the_shift_like_the_other_vega_families() {
        for family in ["geoshape", "point_map"] {
            let p = payload(family, (3.0, 4.0), |_| {});
            assert_eq!(shifted(&p, family).unwrap(), (16.0 + 3.0, 10.0 + 1.0), "{family}");
        }
    }

    // Python's round() ties to even: 0.5 -> 0, 1.5 -> 2, 2.5 -> 2.
    #[test]
    fn the_whole_pixel_gap_rounds_half_to_even() {
        for (gap, whole) in [(0.5, 0.0), (1.5, 2.0), (2.5, 2.0)] {
            let p = payload("line", (0.0, gap), |_| {});
            assert_eq!(
                shifted(&p, "line").unwrap(),
                (16.0 + gap - whole, 10.0 + whole),
                "gap {gap}"
            );
        }
    }

    #[test]
    fn a_zero_shift_borrows_the_payload_untouched() {
        let p = payload("bar", (0.0, 0.0), |r| {
            r.as_object_mut().unwrap().remove("title");
        });
        assert!(matches!(for_family(&p, "bar").unwrap(), Cow::Borrowed(_)));
    }

    #[test]
    fn a_shift_over_an_untitled_chart_refuses() {
        let p = payload("bar", (1.0, 2.0), |r| r["title"] = Value::Null);
        assert!(shifted(&p, "bar")
            .unwrap_err()
            .contains("needs a title block"));
    }

    #[test]
    fn a_shift_over_an_unset_title_offset_refuses() {
        let p = payload("bar", (1.0, 2.0), |r| {
            r["title_style"]["position"]["offset"] = Value::Null
        });
        assert!(shifted(&p, "bar")
            .unwrap_err()
            .contains("title_style.position.offset"));
    }

    #[test]
    fn a_shift_on_a_family_production_never_shifts_refuses_by_name() {
        for family in ["kpi", "callout", "sankey"] {
            let p = payload(family, (1.0, 2.0), |_| {});
            assert!(
                shifted(&p, family).unwrap_err().starts_with(family),
                "{family}"
            );
        }
        let mut attached = payload("pie", (1.0, 2.0), |_| {});
        attached.pie_mode = Some("full_table".to_string());
        assert!(shifted(&attached, "pie")
            .unwrap_err()
            .contains("attached table"));
    }

    #[test]
    fn a_shift_over_an_empty_result_applies_like_any_other() {
        let mut p = payload("bar", (1.0, 2.0), |_| {});
        p.rows.clear();
        shifted(&p, "bar").expect("an empty cartesian chart takes the shift");
    }

    // A table draws its own empty arm, which applies the shift.
    #[test]
    fn a_shift_over_an_empty_table_is_left_to_the_table() {
        let mut p = payload("table", (1.0, 2.0), |_| {});
        p.rows.clear();
        assert!(matches!(for_family(&p, "table").unwrap(), Cow::Borrowed(_)));
    }

    // Table and spark_bar read the shift themselves, so their payload is not rewritten.
    #[test]
    fn table_and_spark_bar_keep_their_own_payload() {
        for family in ["table", "spark_bar"] {
            let p = payload(family, (3.0, 4.0), |_| {});
            assert!(
                matches!(for_family(&p, family).unwrap(), Cow::Borrowed(_)),
                "{family}"
            );
        }
    }
}
