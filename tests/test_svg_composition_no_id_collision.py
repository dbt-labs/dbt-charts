"""Standing guard: two boards on different themes must not collide when their
SVGs land in one HTML document (the docs themes page, or any Cloud surface
stacking more than one board — review diffs, merge conflicts, chat embeds).

Inline SVG in an HTML page has no id scope: every ``id="..."`` a board's SVG
emits is document-global, so a duplicated, *referenced* id (``url(#...)``)
resolves to whichever def the DOM kept — silently repainting the second
board with the first board's fragment. This is the id-namespace sibling of
``test_svg_composition_no_scope_collision.py``'s CSS-selector guard.

An in-process render cannot reproduce the bug: vl-convert's own id counter
(``clip0``, ``clip1``, ...) is scoped to a single ``vegalite_to_svg`` call
and keeps advancing across renders made in one process, so two in-process
renders never mint the same low-numbered id. Two *separate processes* both
start that counter at zero — which is exactly what happens when a page like
the docs themes page composes boards rendered by independent build steps.
This test renders each board in its own subprocess to reproduce that.

Scope: every element id except the chart-wrapper form ``chart-<chart_id>``.
That id is a known duplicate across boards sharing a chart key (unlike
vl-convert's per-render ids, it is not scoped by anything) and is tolerated
here rather than fixed: nothing in the codebase selects it (no ``url(#...)``,
no ``href``, no CSS rule, no JS lookup) -- the sibling ``data-chart-id``
attribute is the documented hook for JS/CSS to key off a chart -- so it is
invalid-but-inert, unlike the vl-convert ids this test exists to guard. It is
excluded from the conflict set below, not from the guard's positive controls.
"""

from __future__ import annotations
