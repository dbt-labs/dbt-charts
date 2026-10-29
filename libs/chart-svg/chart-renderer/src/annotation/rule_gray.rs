//! The gray an `auto` rule (and its label) takes: the quietest rung of the
//! theme's ladder that holds 3:1 against the canvas and every mark the rule
//! crosses, stepped louder off any gridline.

use super::DataMark;
use crate::diagnostic::Diagnostic;
use crate::labels_expr::colors::{contrast, Color};

/// The contrast a rule must hold against the canvas and the marks it crosses.
const FLOOR: f64 = 3.0;
/// A rule closer than this to a gridline reads as part of the grid.
const GRID_CLEARANCE: f64 = 1.25;

fn parse(color: &str) -> Result<Color, Diagnostic> {
    Color::parse(color).ok_or_else(|| Diagnostic::refused(format!("annotation rule gray: `{color}` is not a color")))
}

/// Each marked color the segment crosses, composited at its real alpha over
/// the canvas in paint order; one entry per distinct result.
pub(super) fn crossed(marks: &[DataMark], canvas: Color, a: (f64, f64), b: (f64, f64), width: f64) -> Result<Vec<Color>, Diagnostic> {
    let half = width / 2.0;
    let steps = (b.0 - a.0).abs().max((b.1 - a.1).abs()).ceil().max(1.0) as usize;
    let mut out: Vec<Color> = Vec::new();
    for i in 0..=steps {
        let t = i as f64 / steps as f64;
        let (x, y) = (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t);
        let mut under: Option<[f64; 3]> = None;
        for m in marks {
            let DataMark::Tinted(inner, fill, alpha) = m else { continue };
            // A pattern or a missing fill has no color to hold contrast against.
            if fill == "none" || fill.starts_with("url(") || !inner.covers(x, y, half) {
                continue;
            }
            let base = under.unwrap_or(canvas.c);
            let fill = parse(fill)?.to_rgb();
            let a = alpha * fill.opacity;
            under = Some([0, 1, 2].map(|k| (fill.c[k] * a + base[k] * (1.0 - a)).round()));
        }
        if let Some([r, g, b]) = under {
            let c = Color::rgb(r, g, b, 1.0);
            if !out.contains(&c) {
                out.push(c);
            }
        }
    }
    Ok(out)
}

/// The ladder entry (`ladder` runs canvas end first) an `auto` rule draws in.
/// Rungs reaching `FLOOR` against the canvas and `under`: the quietest wins;
/// none do: the rung with the best worst case. A pick within `GRID_CLEARANCE`
/// of a gridline steps one rung louder until clear or out of rungs.
pub(super) fn pick<'a>(ladder: &'a [String], avoid: &[String], canvas: Color, under: &[Color]) -> Result<&'a str, Diagnostic> {
    let rungs = ladder.iter().map(|g| parse(g)).collect::<Result<Vec<_>, _>>()?;
    let grids = avoid.iter().map(|g| parse(g)).collect::<Result<Vec<_>, _>>()?;
    let (Some(first), Some(last)) = (rungs.first(), rungs.last()) else {
        return Err(Diagnostic::refused("annotation rule gray: an `auto` rule carries no ladder"));
    };
    let against_canvas = |i: usize| contrast(rungs[i], canvas);
    let worst = |i: usize| under.iter().fold(against_canvas(i), |w, m| w.min(contrast(rungs[i], *m)));
    let holding = (0..rungs.len()).filter(|&i| worst(i) >= FLOOR).min_by(|&a, &b| against_canvas(a).total_cmp(&against_canvas(b)));
    let mut at: usize = holding.unwrap_or_else(|| (0..rungs.len()).fold(0, |best, i| if worst(i) > worst(best) { i } else { best }));
    let louder: isize = if contrast(*last, canvas) >= contrast(*first, canvas) { 1 } else { -1 };
    let clashes = |i: usize| grids.iter().any(|g| contrast(rungs[i], *g) < GRID_CLEARANCE);
    while clashes(at) {
        match at.checked_add_signed(louder).filter(|&n| n < rungs.len()) {
            Some(n) => at = n,
            None => break,
        }
    }
    Ok(&ladder[at])
}

#[cfg(test)]
mod tests {
    use super::*;

    const LADDER: [&str; 8] = ["#EDEFF2", "#DFE1E5", "#D0D3D9", "#B0B2B8", "#8A8C8F", "#626366", "#494A4C", "#313233"];

    fn ladder() -> Vec<String> {
        LADDER.map(String::from).to_vec()
    }

    fn hex(h: &str) -> Color {
        Color::parse(h).unwrap()
    }

    fn picked(avoid: &[&str], canvas: &str, under: &[&str]) -> String {
        let avoid: Vec<String> = avoid.iter().map(|s| s.to_string()).collect();
        let under: Vec<Color> = under.iter().map(|s| hex(s)).collect();
        pick(&ladder(), &avoid, hex(canvas), &under).unwrap().to_string()
    }

    #[test]
    fn on_a_dark_canvas_with_no_marks_the_quietest_rung_reaching_three_to_one() {
        // #8A8C8F is 5.4:1 on #161616; #626366 is 3.1:1 and the quieter of the two that reach 3.
        assert_eq!(picked(&[], "#161616", &[]), "#626366");
    }

    #[test]
    fn a_pick_on_a_gridline_steps_louder_until_clear() {
        // Neon's gridlines are rungs 8 and 9; the unchecked pick is rung 8 itself.
        assert_eq!(picked(&["#494A4C", "#626366"], "#161616", &[]), "#8A8C8F");
    }

    #[test]
    fn a_louder_rung_is_one_nearer_the_ink_end_on_a_light_canvas() {
        // Rung 5 (#D0D3D9) is 1.5:1 on white; the first to reach 3:1 is #8A8C8F.
        assert_eq!(picked(&[], "#FFFFFF", &[]), "#8A8C8F");
        assert_eq!(picked(&["#8A8C8F"], "#FFFFFF", &[]), "#626366");
    }

    #[test]
    fn a_crossed_dark_mark_moves_the_pick_to_a_rung_that_reads_on_it() {
        // Vivid blue #0073c2 on #161616: #626366 is under 3:1 against it, the next rung up reaches it.
        let on_canvas = picked(&[], "#161616", &[]);
        let on_blue = picked(&[], "#161616", &["#0073c2"]);
        assert_ne!(on_canvas, on_blue);
        let (g, m, c) = (hex(&on_blue), hex("#0073c2"), hex("#161616"));
        assert!(contrast(g, m) >= 3.0 && contrast(g, c) >= 3.0);
    }

    #[test]
    fn no_rung_reaching_three_to_one_picks_the_best_worst_case() {
        // A near-canvas mark and a pale one: no single rung holds 3:1 against both and the canvas.
        let marks = ["#3a3a3a", "#c8c8c8"];
        let score = |g: &str| marks.iter().chain(&["#161616"]).map(|m| contrast(hex(g), hex(m))).fold(f64::INFINITY, f64::min);
        let best = LADDER.iter().map(|g| score(g)).fold(0.0, f64::max);
        assert!(best < 3.0);
        assert!((score(&picked(&[], "#161616", &marks)) - best).abs() < 1e-9);
    }

    #[test]
    fn an_empty_ladder_is_refused() {
        assert!(pick(&[], &[], hex("#161616"), &[]).is_err());
    }

    #[test]
    fn a_bar_crossed_by_a_horizontal_rule_is_composited_at_its_alpha() {
        let bar = DataMark::Tinted(Box::new(DataMark::rect(10.0, 20.0, 30.0, 80.0)), "#ff0000".into(), 0.5);
        let canvas = hex("#000000");
        let crossed = crossed(&[bar], canvas, (0.0, 50.0), (100.0, 50.0), 1.5).unwrap();
        assert_eq!(crossed, vec![Color::rgb(128.0, 0.0, 0.0, 1.0)]);
        let missed = super::crossed(&[DataMark::Tinted(Box::new(DataMark::rect(10.0, 60.0, 30.0, 30.0)), "#ff0000".into(), 1.0)], canvas, (0.0, 50.0), (100.0, 50.0), 1.5).unwrap();
        assert!(missed.is_empty());
    }

    #[test]
    fn a_fill_with_its_own_alpha_composites_at_the_product() {
        let canvas = hex("#000000");
        let at = |fill: &str| {
            let bar = DataMark::Tinted(Box::new(DataMark::rect(10.0, 20.0, 30.0, 80.0)), fill.into(), 1.0);
            crossed(&[bar], canvas, (0.0, 50.0), (100.0, 50.0), 1.5).unwrap()
        };
        let half = vec![Color::rgb(128.0, 0.0, 0.0, 1.0)];
        assert_eq!(at("rgba(255, 0, 0, 0.5)"), half);
        assert_eq!(at("#ff000080"), half);
        assert_eq!(at("hsl(0, 100%, 50%)"), vec![Color::rgb(255.0, 0.0, 0.0, 1.0)]);
    }
}
