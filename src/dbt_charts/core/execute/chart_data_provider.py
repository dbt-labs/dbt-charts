"""The data seam the post-resolve render path depends on.

``render_board_svg`` and everything below it need only three things from the
execution layer: fetch a query's rows, ask when the cache entries backing them
were written, and ask when this render happened. ``Executor`` provides both (and satisfies this Protocol
structurally, with no changes of its own), but it is not the only possible
provider: replaying a recorded board from a serialized artifact has rows on
hand and no warehouse to reach.

Typing the render path against this Protocol rather than the concrete
``Executor`` is what makes such a provider a type-checked participant instead of
a duck-typed stand-in smuggled past an annotation. It is also a step toward the
boundary ``core/AGENTS.md`` commits to — render as a pure function of
``(Resolved*, data)`` plus foreign-format translation.

**Deliberately absent**, because the post-resolve path does not use them:

- ``adapter_registry`` — only the *resolve* path reaches for it
  (``render/chart/automatic_link.py``, ``render/layout_sizing.py::_require_resolved``).
  Auto-link bakes its result into ``resolved.link`` at resolve time, so a
  replayed board already carries its links. Adding it here would oblige every
  provider to own a live warehouse connection, defeating the point.
- ``board`` — the one read (``executor.board.variables`` in
  ``execute/chart_resolution.py``) is resolve-path only.
- ``execute_chart`` / ``is_cached`` — the resolve path, the terminal and
  data-format renderers, and the ``render()`` orchestrator's cache pre-pass.

Keep this Protocol minimal. Widening it to accommodate a new render-time reach
into the execution layer is the signal that the reach itself belongs earlier, on
the resolve path, where the value can be baked into ``Resolved*``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from dbt_charts.core.execute.cache_backend import CacheRows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import VariableValues


# Not @runtime_checkable: `cache_hit_ats` is a property, so this is a data
# protocol — `issubclass` is unsupported against it and `isinstance` would only
# check attribute presence, not signatures. Conformance is a type-checker
# concern, asserted at the seam in test_chart_data_provider.py.
class ChartDataProvider(Protocol):
    """Supplies query results to the post-resolve render path."""

    def execute_query(self, query_name: str, variables: VariableValues) -> CacheRows:
        """Return the rows for ``query_name`` under ``variables``.

        Deliberately narrower than ``Executor.execute_query``: every post-resolve
        call site passes exactly ``(query_name, variables)`` with a real mapping.
        ``Executor``'s extra defaulted ``use_cache`` / ``force_refresh`` and its
        wider ``VariableValues | None`` still satisfy this — an implementation may
        accept more than the Protocol promises. Declaring only what the seam uses
        keeps a provider from modeling cache control it has no concept of.
        """
        ...

    @property
    def cache_hit_ats(self) -> list[datetime]:
        """Write times of the cache entries served this render.

        Empty means everything ran fresh. Render takes ``min()`` of these for the
        board's "data as of" stamp, falling back to render time when empty.
        """
        ...

    @property
    def rendered_at(self) -> datetime:
        """The instant this render stands for, read once by the provider.

        Render never reads a clock of its own: the same board over the same data
        and the same ``rendered_at`` is the same bytes, so two renders compare
        equal without stripping timestamps first.
        """
        ...
