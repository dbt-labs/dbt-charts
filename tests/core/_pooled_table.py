"""What a reader sees of an interactive table.

An interactive host draws every row once and hides all but the open page
(`display="none"` on the row groups and the other pagers), so a test asking
"which rows are on this page" reads the visible part.
"""

import re

_POOLED_GROUP = re.compile(r'<g (?:class="dbt-table-row"|data-dbt-pager=)')


def visible_svg(svg: str) -> str:
    """`svg` without its hidden row groups and hidden pager variants."""
    starts = [m.start() for m in _POOLED_GROUP.finditer(svg)]
    if not starts:
        return svg
    ends = [*starts[1:], len(svg)]
    kept = [
        svg[start:end]
        for start, end in zip(starts, ends, strict=True)
        if 'display="none"' not in svg[start : svg.index(">", start)]
    ]
    return svg[: starts[0]] + "".join(kept)
