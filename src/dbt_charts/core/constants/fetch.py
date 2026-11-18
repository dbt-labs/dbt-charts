"""Warehouse fetch sizes."""

from typing import Final

# Rows asked of a cursor per fetch while the cut reads the result: a byte cut
# reads at most one chunk past the budget.
FETCH_CHUNK_ROWS: Final[int] = 1_000
