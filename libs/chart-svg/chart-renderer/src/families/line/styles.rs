//! Which style of a line palette each series draws: plain slot order for a few lines, then
//! sharing between lines that stay apart (D-04). Pure over pixel positions; it reads no style
//! values, colors or nodes.

use std::collections::BTreeMap;

use scene::Attrs;
use serde::Deserialize;

use crate::constants::marks::{LINE_STYLE_MAX_USES, LINE_STYLE_SHARE_FROM, LINE_STYLE_SHARE_MIN_GAP_PX};

/// One stroke style of the theme's line palette (`palettes.line_category`), as resolve bakes it.
#[derive(Deserialize)]
pub(crate) struct PaletteLineStyle {
    pub(crate) width: f64,
    /// Empty is a solid line.
    pub(crate) dash: Vec<f64>,
    pub(crate) cap: String,
    /// A canvas-colored stroke drawn over the ink.
    pub(crate) inline: Option<PaletteLineInline>,
}

#[derive(Deserialize)]
pub(crate) struct PaletteLineInline {
    pub(crate) width: f64,
    pub(crate) dash: Vec<f64>,
    pub(crate) cap: String,
    pub(crate) offset: f64,
}

fn dash_list(dash: &[f64]) -> Option<String> {
    (!dash.is_empty()).then(|| dash.iter().map(f64::to_string).collect::<Vec<_>>().join(","))
}

impl PaletteLineStyle {
    /// The attributes of the style's ink stroke in `color`; a `width` or `dash` given outranks the style's own.
    pub(crate) fn ink(&self, color: &str, join: &str, width: Option<f64>, dash: Option<String>) -> Attrs {
        let attrs = Attrs::new()
            .set("fill", "none")
            .set("stroke", color)
            .num("stroke-width", width.unwrap_or(self.width))
            .set("stroke-linecap", self.cap.as_str())
            .set("stroke-linejoin", join);
        match dash.or_else(|| dash_list(&self.dash)) {
            Some(list) => attrs.set("stroke-dasharray", list),
            None => attrs,
        }
    }

    /// The attributes of the inline stroke in the canvas color, when the style has one.
    pub(crate) fn inline(&self, canvas: &str, join: &str) -> Option<Attrs> {
        let inline = self.inline.as_ref()?;
        let attrs = Attrs::new()
            .set("fill", "none")
            .set("stroke", canvas)
            .num("stroke-width", inline.width)
            .set("stroke-linecap", inline.cap.as_str())
            .set("stroke-linejoin", join)
            .num("stroke-dashoffset", inline.offset);
        Some(match dash_list(&inline.dash) {
            Some(list) => attrs.set("stroke-dasharray", list),
            None => attrs,
        })
    }
}

/// One series' vertices in one panel, in plot pixels; empty where the panel holds none of it.
pub(crate) type Trace = Vec<(f64, f64)>;

/// A trace's y by x, x snapped to a millipixel so two series on one scale meet at the same key.
fn by_x(trace: &Trace) -> BTreeMap<i64, f64> {
    trace.iter().map(|&(x, y)| ((x * 1000.0).round() as i64, y)).collect()
}

/// Whether series `a` and `b` may not share a style: they cross (the sign of their difference
/// changes anywhere along x) or their mean vertical distance is under the minimum gap, in any panel.
/// Lines with no x in common anywhere never share.
fn clash(panels: &[Vec<Trace>], a: usize, b: usize) -> bool {
    let mut met = false;
    for panel in panels {
        let (ta, tb) = (by_x(&panel[a]), by_x(&panel[b]));
        let mut sign = 0.0_f64;
        let (mut gap, mut count) = (0.0, 0usize);
        for (x, ya) in &ta {
            let Some(yb) = tb.get(x) else { continue };
            let diff = ya - yb;
            if diff != 0.0 {
                if sign != 0.0 && diff.signum() != sign {
                    return true;
                }
                sign = diff.signum();
            }
            gap += diff.abs();
            count += 1;
        }
        if count > 0 {
            met = true;
            if gap / (count as f64) < LINE_STYLE_SHARE_MIN_GAP_PX {
                return true;
            }
        }
    }
    !met
}

/// The 0-based slot of each series, for a palette of `slots` styles. `panels[p][s]` is series `s`
/// in panel `p`; every panel lists every series, in slot order. The first series takes slot 0. From
/// `LINE_STYLE_SHARE_FROM` series up, each later series takes the first slot from 1 on that fewer
/// than `LINE_STYLE_MAX_USES` series use and whose users it does not clash with; a series that finds
/// none takes the last slot, the only one that may serve more.
pub(crate) fn assign_slots(panels: &[Vec<Trace>], slots: usize) -> Vec<usize> {
    let count = panels.first().map_or(0, Vec::len);
    let last = slots - 1;
    if count < LINE_STYLE_SHARE_FROM {
        return (0..count).map(|series| series.min(last)).collect();
    }
    let mut out: Vec<usize> = Vec::with_capacity(count);
    for series in 0..count {
        let slot = if series == 0 {
            0
        } else {
            (1..slots)
                .find(|slot| {
                    let users: Vec<usize> = (0..series).filter(|&other| out[other] == *slot).collect();
                    users.len() < LINE_STYLE_MAX_USES && users.iter().all(|&other| !clash(panels, series, other))
                })
                .unwrap_or(last)
        };
        out.push(slot);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const XS: [f64; 4] = [0.0, 100.0, 200.0, 300.0];

    fn flat(y: f64) -> Trace {
        XS.iter().map(|&x| (x, y)).collect()
    }

    /// Every series pivots about x = 150, so every pair crosses.
    fn tangled(series: usize) -> Trace {
        XS.iter().map(|&x| (x, 500.0 + (series as f64 - 6.0) * (x - 150.0) / 10.0)).collect()
    }

    #[test]
    fn three_series_take_slots_one_two_three() {
        let panel = vec![flat(0.0), flat(100.0), flat(200.0)];
        assert_eq!(assign_slots(&[panel], 10), [0, 1, 2]);
    }

    #[test]
    fn separate_lines_share_and_crossing_lines_do_not() {
        let crossing: Trace = vec![(0.0, 250.0), (100.0, 280.0), (200.0, 320.0), (300.0, 350.0)];
        let panel = vec![flat(0.0), flat(100.0), flat(200.0), flat(300.0), crossing, flat(400.0)];
        // s2 shares s1's thin line (60+ px apart); s4 crosses s3's flat 300 so it cannot share s3's slot.
        assert_eq!(assign_slots(&[panel], 10), [0, 1, 1, 2, 3, 2]);
    }

    #[test]
    fn lines_closer_than_the_gap_do_not_share() {
        let panel = vec![flat(0.0), flat(100.0), flat(110.0), flat(300.0), flat(400.0)];
        assert_eq!(assign_slots(&[panel], 10), [0, 1, 2, 1, 2]);
    }

    #[test]
    fn no_middle_slot_serves_more_than_two_lines_and_the_overflow_takes_the_last() {
        let panel: Vec<Trace> = (0..14).map(tangled).collect();
        let slots = assign_slots(&[panel], 10);
        assert_eq!(slots[..10], [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
        assert_eq!(slots[10..], [9, 9, 9, 9]);
        let wide: Vec<Trace> = (0..9).map(|i| flat(100.0 * i as f64)).collect();
        let shared = assign_slots(&[wide], 10);
        for slot in 1..9 {
            assert!(shared.iter().filter(|&&s| s == slot).count() <= 2, "{shared:?}");
        }
    }

    #[test]
    fn lines_with_no_x_in_common_never_share() {
        let panel: Vec<Trace> = (0..6).map(|i| vec![(i as f64, 100.0 * i as f64), (i as f64 + 0.5, 100.0 * i as f64)]).collect();
        assert_eq!(assign_slots(&[panel], 10), [0, 1, 2, 3, 4, 5]);
    }

    #[test]
    fn a_pair_that_clashes_in_one_panel_does_not_share_in_any() {
        let apart: Vec<Trace> = vec![flat(0.0), flat(100.0), flat(200.0), flat(1000.0), flat(2000.0)];
        let crossing: Vec<Trace> = vec![
            flat(0.0),
            vec![(0.0, 0.0), (100.0, 100.0), (200.0, 200.0), (300.0, 300.0)],
            vec![(0.0, 300.0), (100.0, 200.0), (200.0, 100.0), (300.0, 0.0)],
            flat(1000.0),
            flat(2000.0),
        ];
        assert_eq!(assign_slots(&[apart.clone()], 10)[2], 1);
        assert_ne!(assign_slots(&[apart, crossing], 10)[2], 1);
    }
}
