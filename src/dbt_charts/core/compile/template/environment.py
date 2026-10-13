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

from jinja2.sandbox import ImmutableSandboxedEnvironment

BoardTemplateEnvironment = ImmutableSandboxedEnvironment
