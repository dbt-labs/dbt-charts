//! Stacked totals and the stacked ladder. Stacking itself (offsets, series order) is
//! `crate::stack`.

use std::collections::HashMap;
use std::hash::Hash;

use d3rs::ticks::nice_tick_values;

use super::domain::{authored, clip_to_domain, fixed, headroom_top, nice_bounds, Axis, RungText};
use crate::bound::{Bounds, BoundsReason, Extent, Invalid, Ladder, Pin, PinReason, ValueDomain, Zero};

/// The tallest category total, `rows` being one panel's `(category, measures)` with the
/// category present. A stack piles positives up and negatives down separately, so only
/// positives count. A row of one measure with no value adds nothing; a wide row adds
/// its non-null measures, so a category of nulls totals 0.
pub fn max_total<'a, K: Hash + Eq>(rows: impl IntoIterator<Item = (K, &'a [Option<f64>])>) -> Option<f64> {
    let mut totals: HashMap<K, f64> = HashMap::new();
    for (category, measures) in rows {
        if let [None] = measures {
            continue;
        }
        *totals.entry(category).or_insert(0.0) += measures.iter().flatten().map(|v| v.max(0.0)).sum::<f64>();
    }
    totals.into_values().reduce(f64::max)
}

/// Shared scales take the tallest panel; independent panels each own theirs, so
/// nothing is shared.
pub fn fold_panels(panel_maxes: impl IntoIterator<Item = Option<f64>>, independent: bool) -> Option<f64> {
    if independent {
        return None;
    }
    panel_maxes.into_iter().flatten().reduce(f64::max)
}

/// The exact top of a zero stack, flush with its tallest total (annotations
/// included). A non-positive total pins nothing.
fn pinned_top(tallest: f64, annotations: &[f64], headroom: f64) -> Option<f64> {
    let total = annotations.iter().copied().fold(tallest, f64::max);
    (total > 0.0).then(|| headroom_top(total, headroom))
}

/// The stacked domain: zero to the headroom top of the tallest total. `values` is every
/// number on the measure columns, `panel_max` the folded tallest total (`None` falls
/// back to the data max), `annotations` the annotation anchors. `flush_top` pins a zero
/// stack's top to its tallest panel total, headroom 0 included. A floor that would push
/// a ladder rung below the data leaves the ladder to the plot's size.
pub fn linear(
    axis: &Axis,
    values: &[f64],
    annotations: &[f64],
    panel_max: Option<f64>,
    flush_top: bool,
    text: RungText,
) -> Result<Option<ValueDomain>, Invalid> {
    let Some(data_max) = values.iter().copied().reduce(f64::max) else {
        return Ok(None);
    };
    let tallest = annotations.iter().copied().fold(panel_max.unwrap_or(data_max), f64::max);
    let floor = values.iter().chain(annotations).copied().fold(0.0, f64::min);
    let authored_domain = authored(axis.domain)?;
    let max = panel_max
        .filter(|_| flush_top)
        .and_then(|total| pinned_top(total, annotations, axis.headroom))
        .map(|value| Pin { value, reason: PinReason::FlushTop });
    let bounds = nice_bounds(match authored_domain {
        Some(authored) => Bounds { extent: authored.extent, reason: BoundsReason::Authored },
        None => Bounds { extent: Extent::new(floor, max.map_or(tallest, |pin| pin.value))?, reason: BoundsReason::Stacked },
    }, axis.nice == Some(true))?;
    let domain = |ladder| -> Result<Option<ValueDomain>, Invalid> {
        Ok(Some(ValueDomain {
            extent: Extent::new(floor, tallest)?,
            zero: Zero::Anchored,
            headroom: axis.headroom,
            authored: authored_domain,
            ladder,
            min: None,
            max,
            bounds,
        }))
    };
    if let Some(authored_values) = axis.values {
        return domain(Ladder::Authored(super::domain::rungs(authored_values, text)));
    }
    let Some(count) = axis.count else {
        return domain(Ladder::BySize { min_step: axis.min_step });
    };
    let target = count.target();
    let (tick_min, tick_max) = match (axis.domain, axis.independent) {
        (Some(authored), _) => authored,
        (None, true) => return domain(Ladder::None),
        (None, false) => (floor, headroom_top(tallest, axis.headroom)),
    };
    let ticks = nice_tick_values(tick_min, tick_max, target, axis.min_step);
    if axis.domain.is_some() {
        return domain(fixed(clip_to_domain(&ticks, (tick_min, tick_max)), authored_domain.map(|a| a.extent), text)?);
    }
    if let Some(min_step) = axis.min_step.filter(|_| tick_min < 0.0) {
        let unfloored = nice_tick_values(tick_min, tick_max, target, None);
        // At the floor's own step, a negative reach under half of it is a zero, not a row.
        let phantom = ticks.windows(2).next().is_some_and(|w| w[1] - w[0] <= min_step && -tick_min < (w[1] - w[0]) / 2.0);
        if ticks[0] < unfloored[0] || phantom {
            return domain(Ladder::BySize { min_step: axis.min_step });
        }
    }
    domain(fixed(ticks, None, text)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plot::domain::{Count, Mark};

    fn digits(values: &[f64]) -> Vec<String> {
        values.iter().map(|v| v.to_string()).collect()
    }

    fn axis() -> Axis<'static> {
        Axis { zero: Zero::Anchored, headroom: 0.0, count: Some(Count::N(5)), values: None, domain: None, min_step: None, independent: false, nice: None, mark: Mark::Bar }
    }

    fn rungs(domain: &ValueDomain) -> Vec<f64> {
        match &domain.ladder {
            Ladder::Fixed(fixed) => fixed.rungs().iter().map(|r| r.value).collect(),
            other => panic!("not fixed: {other:?}"),
        }
    }

    #[test]
    fn totals_sum_positives_only() {
        let rows: [(&str, &[Option<f64>]); 4] = [
            ("a", &[Some(3.0)]),
            ("a", &[Some(4.0)]),
            ("a", &[Some(-9.0)]),
            ("b", &[None]),
        ];
        assert_eq!(max_total(rows), Some(7.0));
    }

    #[test]
    fn wide_rows_sum_their_measures_and_a_null_row_totals_zero() {
        let rows: [(&str, &[Option<f64>]); 3] =
            [("a", &[Some(2.0), Some(3.0)]), ("b", &[Some(-1.0), None]), ("c", &[None, None])];
        assert_eq!(max_total(rows), Some(5.0));
        assert_eq!(max_total([("c", &[None, None][..])]), Some(0.0));
        assert_eq!(max_total::<&str>([]), None);
    }

    #[test]
    fn panels_fold_to_the_tallest_unless_independent() {
        assert_eq!(fold_panels([Some(3.0), None, Some(9.0)], false), Some(9.0));
        assert_eq!(fold_panels([Some(3.0)], true), None);
    }

    #[test]
    fn the_ladder_spans_zero_to_the_headroom_top_of_the_tallest_total() {
        let axis = Axis { headroom: 0.1, ..axis() };
        let domain = linear(&axis, &[3.0, 4.0, 2.0], &[], Some(95.0), false, &digits).unwrap().unwrap();
        assert_eq!(rungs(&domain), [0.0, 50.0, 100.0, 150.0]);
        assert_eq!((domain.extent.lo(), domain.extent.hi()), (0.0, 95.0));
    }

    #[test]
    fn a_stack_is_not_niced() {
        let domain = linear(&axis(), &[3.0, 4.0, 2.0], &[], Some(95.0), false, &digits).unwrap().unwrap();
        assert_eq!((domain.bounds.extent.lo(), domain.bounds.extent.hi()), (0.0, 95.0));
        assert_eq!(domain.bounds.reason, BoundsReason::Stacked);
        let pinned = linear(&Axis { headroom: 0.1, ..axis() }, &[3.0], &[], Some(95.0), true, &digits).unwrap().unwrap();
        assert_eq!(pinned.bounds.extent.hi(), 95.0 * 1.1);
    }

    #[test]
    fn an_authored_nice_rounds_a_stack_outward() {
        let nice = Axis { nice: Some(true), ..axis() };
        let domain = linear(&nice, &[3.0], &[], Some(95.5), false, &digits).unwrap().unwrap();
        assert_eq!((domain.bounds.extent.hi(), domain.bounds.reason), (100.0, BoundsReason::Niced));
    }

    #[test]
    fn independent_panels_and_unset_counts_choose_no_data_ladder() {
        let independent = Axis { independent: true, ..axis() };
        assert_eq!(linear(&independent, &[1.0], &[], None, false, &digits).unwrap().unwrap().ladder, Ladder::None);
        let unset = Axis { count: None, min_step: Some(1.0), ..axis() };
        assert_eq!(linear(&unset, &[1.0], &[], None, false, &digits).unwrap().unwrap().ladder, Ladder::BySize { min_step: Some(1.0) });
    }

    #[test]
    fn a_floor_that_pushes_a_rung_below_the_data_leaves_the_ladder_to_the_size() {
        let floored = Axis { min_step: Some(10.0), count: Some(Count::N(8)), ..axis() };
        let domain = linear(&floored, &[-3.0, 25.0], &[], Some(25.0), false, &digits).unwrap().unwrap();
        assert_eq!(domain.ladder, Ladder::BySize { min_step: Some(10.0) });
    }

    #[test]
    fn a_zero_stack_pins_its_top_flush_even_without_headroom() {
        let top = |panel_max, annotations: &[f64], headroom| {
            let axis = Axis { headroom, ..axis() };
            linear(&axis, &[1.0], annotations, panel_max, true, &digits).unwrap().unwrap().max
        };
        assert_eq!(top(Some(95.0), &[], 0.0), Some(Pin { value: 95.0, reason: PinReason::FlushTop }));
        assert_eq!(top(None, &[], 0.0), None);
        assert_eq!(pinned_top(10.0, &[50.0], 0.1), Some(55.00000000000001));
        assert_eq!(pinned_top(0.0, &[], 0.1), None);
    }
}
