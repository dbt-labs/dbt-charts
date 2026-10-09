"""The one Jinja environment class every user-text renderer builds from.

Every site that renders user-authored board text (queries, titles, chart
labels, registered-view templates) must construct its environment from
``BoardTemplateEnvironment``, so the sandbox class is named in exactly one
place.

Two categories of site must NOT use it, because they are not rendering user
text and switching them would be a silent behavior change:

- ``jinja.py``'s dependency-extraction environment and ``sql_guard.py``'s
  AST-inspection environment only ``.parse()`` — they never render.
- ``render/template_loader.py`` and ``serve/server.py`` render internal
  packaged templates.

The ``jinja2>=3.1.6`` floor is load-bearing, not hygiene: below it the
``|attr("format")`` breakout escapes the sandbox with no cooperating filter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jinja2 import pass_context
from jinja2.filters import do_random
from jinja2.runtime import Context
from jinja2.sandbox import ImmutableSandboxedEnvironment


@pass_context
def _random_filter(
    context: Context,
    seq: Sequence[Any],  # type-state: explicit_any — any template value
) -> Any:  # type-state: explicit_any — any template value
    # Python 3.10's random.choice indexes seq[0] on an empty sequence instead of
    # raising first, so an index-guarded list (QueryItems) would raise its own error.
    if not len(seq):
        return context.environment.undefined("No random item, sequence was empty.")
    return do_random(context, seq)


class BoardTemplateEnvironment(ImmutableSandboxedEnvironment):
    def __init__(
        self,
        **kwargs: Any,  # type-state: explicit_any — Jinja options
    ) -> None:
        super().__init__(**kwargs)
        self.filters["random"] = _random_filter
