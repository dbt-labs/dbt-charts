"""How much cache history ``find_queries`` shows an agent per query."""

from typing import Final

# A week back covers the cadence of any board refreshed daily or weekly, and a
# slice older than that is rarely the one a question about "now" wants.
CACHED_WINDOW_DAYS: Final[int] = 7
# One row of a listing is a few dozen tokens; eight slices per query keeps a
# five-board answer well inside a tool result while still showing the spread of
# variable values a board is read under.
MAX_CACHED_VARIANTS: Final[int] = 8
# A cached result this small is the answer itself (a total, a short breakdown),
# so the listing carries its rows and the agent needs no second call to use it.
INLINE_ROWS: Final[int] = 25
# Rows inlined across one listing, so five boards of small queries stay within
# the size of one execute_sql result.
INLINE_ROW_BUDGET: Final[int] = 100
