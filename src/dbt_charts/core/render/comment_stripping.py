"""Fenced-comment stripping for JS embedded in rendered SVG output.

CSS comments don't need this at all -- `svg/styles.css` and
`controls/_styles.css` are authored with Jinja `{# ... #}` comments and
Jinja strips them natively at template-render time, before this module
would ever see them.

JS comments can't use bare `{# ... #}`: the runtime scripts (variables.js,
chart_interactivity.js) are also read raw and executed directly -- the JS
behavioral test suite evals them in jsdom, and Cloud's chat embed ships them
as a plain HTML `<script>` -- so they must stay valid, comment-and-all
JavaScript when nothing preprocesses them. They're authored as
`/*{# ... #}*/`: a real JS block comment whose entire content is the fence
marker. A raw consumer just sees an ordinary comment; this module finds the
literal `/*{#`...`#}*/` span and removes it.

This does NOT need string/regex-literal awareness the way stripping bare
`//`/`/* */` would. `/*{#` is a 4-character sequence real code would not
contain by coincidence -- unlike `//` or `/*`, which show up constantly in
URLs, regexes, and division. As long as authored code never puts that
literal marker text inside a real string, one non-greedy regex is
sufficient and correct.
"""

from __future__ import annotations

import re

_FENCED_COMMENT_RE = re.compile(r"/\*\{#.*?#\}\*/", re.DOTALL)


def strip_js_comments(source: str) -> str:
    """Remove every `/*{# ... #}*/`-fenced comment from JavaScript source.

    Replaces each fenced span with a single space so adjacent tokens never
    merge (e.g. `return/*{# ... #}*/5` must not become `return5`). Comments
    not wrapped in the fence (plain `//` or `/* */`) are left untouched.
    """
    return _FENCED_COMMENT_RE.sub(" ", source)
