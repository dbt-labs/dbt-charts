"""How many rows one window of a query result may carry."""

from __future__ import annotations

from typing import Final

# The most rows any consumer draws at once: a table's row pool. A window larger
# than what a page draws defeats the point of windowing.
WINDOW_MAX_ROWS: Final[int] = 1000
