"""Penalty-driven column packing for flowed prose.

Chooses where a sequence of lines breaks across columns. The model is TeX's
page breaker rather than a set of rules: every candidate break carries a cost,
and the assignment minimizing total cost wins.

Rules-with-precedence and penalties differ in an important way. Rules need an
explicit conflict order, and when no break satisfies all of them they have no
answer. Penalties resolve conflicts by arithmetic and always have an answer --
the least-bad one. That matters because widow avoidance, keep-with-next and
column balance routinely cannot all hold at once on real prose.

Balance is therefore a cost *term*, not a constraint. Print convention accepts
a ragged column bottom to avoid a stranded line, and encoding balance as an
assertion would invert that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

# Not a tunable: infinity is how the DP spells "this break is illegal".
_FORBIDDEN = math.inf

# Deliberately hard-coded, not exposed to dbt_charts.yml (Dave, 2026-08-10):
# these are engine internals, not project-facing tuning. Do not "restore" them
# to typed config on the strength of core/AGENTS.md's general
# constants-belong-in-config guidance -- that guidance is overridden here.

# A column must earn this many lines to exist -- two columns of one line each
# is not a two-column layout. It gates only the columns after the first: one
# column is always available.
MIN_LINES_PER_COLUMN = 5

# Readable band, in characters per line (Bringhurst: 45-75 satisfactory). A
# grid span narrower than MIN_CHARS is not a readable column; MAX_CHARS caps
# the text inside a span that is wider, so the excess lands at the span's right
# and a column's left edge never leaves its card content edge.
MIN_CHARS = 45
MAX_CHARS = 75

# What each candidate column break costs the packer. Only the ratios carry
# meaning: a stranded line has to outrank the imbalance that avoiding it
# creates, which is why the line penalties sit three orders of magnitude
# above the balance terms below. Orphan and widow are kept as distinct
# constants (both 3000.0) rather than merged -- they are different
# typographic concepts (a paragraph's first line alone at a column foot vs.
# its last line alone at a column top), even though today they cost the same.
# Candidates for future exposure under `text:`, not `text.column:` -- orphans
# and widows apply to any fragmentation, not just columns (the same scope CSS
# uses).
_ORPHAN_PENALTY = 3000.0
_WIDOW_PENALTY = 3000.0
_INTERIOR_BREAK_PENALTY = 1.0  # mid-paragraph: fine, mildly preferred against
_BLOCK_BREAK_PENALTY = 0.0  # between blocks: the natural break

# Weight on squared height deviation, and the scale that puts it in the same
# units as the penalties above. Undershoot is surcharged more than overshoot,
# weighted by how early the column is, so an uneven remainder lands late.
_BALANCE_SCALE = 100.0
_BALANCE_UNDERSHOOT = 0.35


def grid_span(board_width: float, grid: int, gap: float) -> float:
    """Width of one span of the ``grid``-up card row across ``board_width``."""
    return (board_width - (grid - 1) * gap) / grid


def choose_grid(
    board_width: float, gap: float, card_padding: float, char_px: float
) -> Literal[1, 2, 3]:
    """Spans of the grid a block of prose needs: 3 (thirds), 2 (halves) or 1.

    Thirds is the default. Halves wins when a third's text would be narrower
    than ``MIN_CHARS`` -- there is no readable third. When even a half is
    unreadable the grid is one whole-board span: a one-card row. A list-heavy
    run narrows this further, which only the whole run can tell.
    """

    def readable(grid: int) -> bool:
        return (
            grid_span(board_width, grid, gap) - 2 * card_padding
        ) / char_px >= MIN_CHARS

    if not readable(2):
        return 1
    return 3 if readable(3) else 2


def span_boxes(
    container: float, board_width: float, grid: int, gap: float
) -> tuple[tuple[float, float], ...]:
    """The ``(x, width)`` boxes a container ``container`` wide offers prose.

    A full-width container offers every span of the grid. A card offers the
    spans it contains (a 2/3 card on thirds offers 2), each on the board grid's
    own offsets from the card's left edge, so the second column of a 2/3 card
    meets the second card of the 3-up row below. A card narrower than two spans
    offers itself whole: its left edge is already a card content edge. A card
    contains a span when it is within half a gap (and never less than a pixel)
    of holding it, because an authored ``66.67%`` is a few pixels short of two
    thirds and a row with no gap still divides unevenly; a last box never
    passes the container's right edge.
    """
    span = grid_span(board_width, grid, gap)
    slack = max(gap / 2, 1.0)
    count = max(1, min(grid, int((container + gap + slack) // (span + gap))))
    if count == 1:
        return ((0.0, container),)
    return tuple(
        (i * (span + gap), min(span, container - i * (span + gap)))
        for i in range(count)
    )


def columns_earned(total_lines: int, spans: int) -> int:
    """Most columns, up to ``spans``, that each earn ``MIN_LINES_PER_COLUMN``."""
    return max(1, min(spans, total_lines // MIN_LINES_PER_COLUMN))


@dataclass(frozen=True)
class PackUnit:
    """One placeable line, or one whole unsplittable block.

    An unsplittable block reports itself as a single unit whose ``advance`` is
    its full height, so the packer treats every input the same way.
    """

    advance: float
    space_before: float
    block_id: int
    line_index: int
    line_total: int
    splittable: bool
    keep_with_next: bool

    @property
    def height(self) -> float:
        return self.advance + self.space_before


def _break_penalty(units: list[PackUnit], i: int) -> float:
    """Cost of breaking the column immediately after ``units[i]``."""
    if i >= len(units) - 1:
        return _FORBIDDEN  # nothing follows; not a real break point
    here, nxt = units[i], units[i + 1]

    # Never split an unsplittable block, and never separate a heading from the
    # opening of the text it introduces.
    if here.block_id == nxt.block_id and not here.splittable:
        return _FORBIDDEN
    if here.keep_with_next:
        return _FORBIDDEN
    if i >= 1 and units[i - 1].keep_with_next:
        return _FORBIDDEN  # only one line would follow the heading

    if here.block_id != nxt.block_id:
        return _BLOCK_BREAK_PENALTY

    # Same paragraph: how many of its lines fall either side of the break?
    before = here.line_index + 1
    after = here.line_total - before
    if before == 1:
        return _ORPHAN_PENALTY
    if after == 1:
        return _WIDOW_PENALTY
    return _INTERIOR_BREAK_PENALTY


def pack_columns(units: list[PackUnit], columns: int) -> list[int]:
    """Assign each unit to a column index, minimizing total cost.

    Returns a list parallel to ``units``. Assignments are non-decreasing: a
    column never contains a unit that precedes one in an earlier column.
    """
    n = len(units)
    if n == 0:
        return []
    if columns <= 1:
        return [0] * n

    prefix = [0.0]
    for u in units:
        prefix.append(prefix[-1] + u.height)
    target = prefix[n] / columns

    penalty = [_break_penalty(units, i) for i in range(n)]

    # best[k][i] = min cost of placing units[:i] into k columns.
    inf = math.inf
    best = [[inf] * (n + 1) for _ in range(columns + 1)]
    take = [[0] * (n + 1) for _ in range(columns + 1)]
    best[0][0] = 0.0

    for k in range(1, columns + 1):
        for i in range(1, n + 1):
            # column k spans units[j:i]
            for j in range(k - 1, i):
                prior = best[k - 1][j]
                if prior == inf:
                    continue
                # cost of the break that ended the previous column
                brk = 0.0 if j == 0 else penalty[j - 1]
                if brk == inf:
                    continue
                height = prefix[i] - prefix[j]
                deviation = height - target
                normalizer = target * target
                cost = prior + brk
                if normalizer > 0.0:
                    earliness = columns - k + 1
                    lopsided = (
                        1.0 + _BALANCE_UNDERSHOOT * earliness
                        if deviation < 0.0
                        else 1.0
                    )
                    cost += (
                        lopsided * _BALANCE_SCALE * (deviation * deviation) / normalizer
                    )
                if cost < best[k][i]:
                    best[k][i] = cost
                    take[k][i] = j

    # Recover the split. If every arrangement was forbidden, fall back to an
    # even division rather than failing -- a rendered board beats an exception,
    # and the penalties have already been given their chance.
    if best[columns][n] == inf:
        per = math.ceil(n / columns)
        return [min(i // per, columns - 1) for i in range(n)]

    bounds = [n]
    i = n
    for k in range(columns, 0, -1):
        i = take[k][i]
        bounds.append(i)
    bounds.reverse()

    assignment = [0] * n
    for col in range(columns):
        for idx in range(bounds[col], bounds[col + 1]):
            assignment[idx] = col
    return assignment
