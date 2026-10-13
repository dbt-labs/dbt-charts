"""Test-only helper: make two renders of the same board comparable.

Render time is an input (``Executor(clock=...)``), so a test that pins the clock
gets the same stamp from both renders. What still varies between two renders in
one process is Vega-Lite's ``gradient_N`` / ``clip_N`` counters and the
renderer's own ``clipN``, all of which increment per render for the process's
lifetime. Any test asserting that two renders are equal — or that they differ —
renumbers them here.

A narrower sibling of ``tests/visual/discovery.py``'s ``normalize_svg``, which
also strips cross-tree-only noise (hitbox rects, editor/authored renames) that
two same-run renders never disagree on.
"""

from __future__ import annotations

import re


def normalize_same_run_svg(svg: str) -> str:
    """``svg`` with every per-process id counter renumbered in order of use."""
    for pattern in (r"gradient_(\d+)", r"clip_(\d+)", r"clip(\d+)"):
        prefix = pattern.split("(")[0]
        ids = list(dict.fromkeys(re.findall(pattern, svg)))
        id_map = {old: str(new) for new, old in enumerate(ids)}
        svg = re.sub(
            pattern, lambda m, _m=id_map, _p=prefix: f"{_p}{_m[m.group(1)]}", svg
        )
    return svg
