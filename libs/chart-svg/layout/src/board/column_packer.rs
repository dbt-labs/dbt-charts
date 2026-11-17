//! `render/column_packer.py`'s packer: where a run of prose lines breaks across columns. Every
//! candidate break carries a cost and the assignment of least total cost wins, so widow avoidance,
//! keep-with-next and balance resolve by arithmetic instead of by precedence: balance is a cost
//! term, never a constraint. `fixtures/column_packer_battery.json` holds production's answers and
//! `tests/it/column_packer.rs` replays them.

use d3rs::py_float::py_fsum;

/// `MIN_LINES_PER_COLUMN`: a column after the first must earn this many lines to exist.
pub const MIN_LINES_PER_COLUMN: usize = 5;

/// How the DP spells "this break is illegal".
const FORBIDDEN: f64 = f64::INFINITY;
// Only the ratios carry meaning: a stranded line outranks the imbalance avoiding it creates.
const ORPHAN_PENALTY: f64 = 3000.0;
const WIDOW_PENALTY: f64 = 3000.0;
const INTERIOR_BREAK_PENALTY: f64 = 1.0;
const BLOCK_BREAK_PENALTY: f64 = 0.0;
/// The weight on squared height deviation, and the surcharge on an early column's undershoot, so an
/// uneven remainder lands late.
const BALANCE_SCALE: f64 = 100.0;
const BALANCE_UNDERSHOOT: f64 = 0.35;

/// One placeable line, or one whole unsplittable block (its `advance` the block's full height). A
/// list item is one whole unit too, but the units of a list may part.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PackUnit {
    pub advance: f64,
    pub space_before: f64,
    pub block_id: usize,
    pub line_index: usize,
    pub line_total: usize,
    pub splittable: bool,
    pub keep_with_next: bool,
    pub leading_margin: f64,
    pub whole_item: bool,
}

impl PackUnit {
    pub fn height(&self) -> f64 {
        self.advance + self.space_before
    }

    /// The height this unit sheds by opening a column: the painter draws neither the gap above it
    /// nor the whitespace it draws above itself, so a column is costed at what is painted.
    pub fn collapse(&self) -> f64 {
        self.space_before + self.leading_margin
    }
}

/// `columns_earned`: the most columns, up to `spans`, that each earn `MIN_LINES_PER_COLUMN`.
pub fn columns_earned(total_lines: usize, spans: usize) -> usize {
    spans.min(total_lines / MIN_LINES_PER_COLUMN).max(1)
}

/// `_break_penalty`: the cost of breaking the column right after `units[i]`.
fn break_penalty(units: &[PackUnit], i: usize) -> f64 {
    let (Some(here), Some(next)) = (units.get(i), units.get(i + 1)) else {
        return FORBIDDEN;
    };
    // Never split an unsplittable block, and never part a heading from the text it introduces.
    if (here.block_id == next.block_id && !here.splittable) || here.keep_with_next {
        return FORBIDDEN;
    }
    if here.block_id != next.block_id {
        return BLOCK_BREAK_PENALTY;
    }
    if here.whole_item {
        return INTERIOR_BREAK_PENALTY;
    }
    if i >= 1 && units[i - 1].keep_with_next {
        return FORBIDDEN; // only one line of the block would follow the heading
    }
    // Signed, as Python's ints are: a unit past its block's last line takes the interior cost.
    let before = here.line_index as i64 + 1;
    let after = here.line_total as i64 - before;
    if before == 1 {
        ORPHAN_PENALTY
    } else if after == 1 {
        WIDOW_PENALTY
    } else {
        INTERIOR_BREAK_PENALTY
    }
}

/// `pack_columns`: each unit's column, at least total cost, non-decreasing in document order;
/// `None` when every arrangement breaks somewhere forbidden.
pub fn pack_columns(units: &[PackUnit], columns: usize) -> Option<Vec<usize>> {
    let n = units.len();
    if n == 0 {
        return Some(Vec::new());
    }
    if columns <= 1 {
        return Some(vec![0; n]);
    }
    let mut prefix = vec![0.0_f64; n + 1];
    for (i, u) in units.iter().enumerate() {
        prefix[i + 1] = prefix[i] + u.height();
    }
    let target = (prefix[n] - units[0].collapse()) / columns as f64;
    let penalty: Vec<f64> = (0..n).map(|i| break_penalty(units, i)).collect();

    // best[k][i]: the least cost of placing units[..i] in k columns; take[k][i]: where column k opens.
    let mut best = vec![vec![f64::INFINITY; n + 1]; columns + 1];
    let mut take = vec![vec![0usize; n + 1]; columns + 1];
    best[0][0] = 0.0;
    for k in 1..=columns {
        for i in 1..=n {
            for j in (k - 1)..i {
                let prior = best[k - 1][j];
                let brk = if j == 0 { 0.0 } else { penalty[j - 1] };
                if prior == f64::INFINITY || brk == f64::INFINITY {
                    continue;
                }
                let deviation = prefix[i] - prefix[j] - units[j].collapse() - target;
                let normalizer = target * target;
                let mut cost = prior + brk;
                if normalizer > 0.0 {
                    let lopsided = if deviation < 0.0 { 1.0 + BALANCE_UNDERSHOOT * (columns - k + 1) as f64 } else { 1.0 };
                    cost += lopsided * BALANCE_SCALE * (deviation * deviation) / normalizer;
                }
                if cost < best[k][i] {
                    best[k][i] = cost;
                    take[k][i] = j;
                }
            }
        }
    }
    if best[columns][n] == f64::INFINITY {
        return None;
    }
    let mut bounds = vec![n];
    let mut i = n;
    for k in (1..=columns).rev() {
        i = take[k][i];
        bounds.push(i);
    }
    bounds.reverse();
    let mut assignment = vec![0usize; n];
    for (column, window) in bounds.windows(2).enumerate() {
        assignment[window[0]..window[1]].fill(column);
    }
    Some(assignment)
}

/// `_painted_heights`: the height drawn in each column of `assignment`.
fn painted_heights(units: &[PackUnit], assignment: &[usize], columns: usize) -> Vec<f64> {
    let mut heights = vec![0.0_f64; columns];
    for (i, (unit, &column)) in units.iter().zip(assignment).enumerate() {
        let opens = i == 0 || assignment[i - 1] != column;
        heights[column] += unit.height() - if opens { unit.collapse() } else { 0.0 };
    }
    heights
}

/// `pack_earned`: packed into the most columns, up to `spans`, whose every painted column rounds to
/// at least `MIN_LINES_PER_COLUMN` lines of `line_height`; one column when none does. The total only
/// bounds the count: a list parts between items and a code block not at all, so a column can land
/// short of what an even division promises. Production calls it on a run that has units.
pub fn pack_earned(units: &[PackUnit], spans: usize, line_height: f64) -> Vec<usize> {
    let Some(first) = units.first() else { return Vec::new() };
    let heights: Vec<f64> = units.iter().map(PackUnit::height).collect();
    let total = py_fsum(&heights) - first.collapse();
    let lines = |height: f64| (height / line_height).round_ties_even().max(0.0) as usize;
    for columns in (2..=columns_earned(lines(total), spans)).rev() {
        if let Some(assignment) = pack_columns(units, columns) {
            if painted_heights(units, &assignment, columns).iter().all(|&h| lines(h) >= MIN_LINES_PER_COLUMN) {
                return assignment;
            }
        }
    }
    vec![0; units.len()]
}
