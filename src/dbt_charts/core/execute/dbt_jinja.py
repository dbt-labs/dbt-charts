"""Shared dbt Jinja patterns.

Single source of truth for both *detecting* dbt-specific Jinja calls (ref(),
source()) and *rewriting* them into relation names. Detection picks a source-less
query's default source and gates the manifest requirement;
substitution is what DbtRefResolver runs once a manifest is in hand.

Detection accepts strictly more than substitution rewrites, and always will:
`_DBT_REF_RE` matches the `{{ ref(` prefix — that is what routing needs — while
`REF_CALL_RE` has to parse a whole call to know what relation to write. No amount
of shared pattern-building closes a gap between a prefix matcher and a parser.

So the gap is closed downstream instead of asserted here: after substitution,
`resolve_dbt_refs_with_provenance` re-runs `has_dbt_jinja` on its own output and
raises ERR-DBT-CALL-UNSUPPORTED on anything left over. That covers every argument
spelling, present and future, rather than the list the patterns happen to know.
Widening the patterns below shrinks what that check rejects; it is not a
precondition for the check being sound.

The substitution patterns cover:
  - Delimiter spacing:  {{ ref( … ) }} and {{ref(…)}}
  - Whitespace trim:    {{- ref( … ) -}}, either delimiter, with or without space
  - Argument spacing:   {{ ref( 'orders' ) }}, {{ source('s' , 't') }}
  - Either quote style: 'orders' and "orders"

Deliberately not covered — each resolves to a guess, so each raises instead:
package-qualified `ref('pkg', 'orders')` (the manifest lookup does not scope by
package, so ignoring the package would pick whichever same-named model came
first), versioned `ref('orders', v=2)`, and any macro-computed argument.
"""

import re

# Jinja's `-` whitespace-trim marker is matched but not honored: substitution
# replaces the call in place and leaves surrounding whitespace alone. Trimming
# it the way Jinja would could weld the relation onto the previous token
# ("FROM" + "analytics.orders"); in SQL the spacing is what keeps them separate.
_OPEN = r"\{\{-?\s*"
_CLOSE = r"\s*-?\}\}"
# Excludes quotes, so a single argument can never span from one call into the
# next when two appear in the same statement.
_QUOTED = r"['\"]([^'\"]+)['\"]"
_ARG_SEP = r"\s*,\s*"

_DBT_REF_RE = re.compile(_OPEN + r"ref\(")
_DBT_SOURCE_RE = re.compile(_OPEN + r"source\(")

REF_CALL_RE = re.compile(_OPEN + r"ref\(\s*" + _QUOTED + r"\s*\)" + _CLOSE)
SOURCE_CALL_RE = re.compile(
    _OPEN + r"source\(\s*" + _QUOTED + _ARG_SEP + _QUOTED + r"\s*\)" + _CLOSE
)


_UNRESOLVED_CALL_RE = re.compile(_OPEN + r"((?:ref|source)\(.*?\))" + _CLOSE, re.DOTALL)


def first_dbt_call(sql: str) -> str:
    """Return the first `ref(...)`/`source(...)` call text, for diagnostics.

    Falls back to the whole `{{ … }}` expression when the call has no closing
    paren to anchor on — a malformed call still has to be quotable in the error
    that names it.
    """
    match = _UNRESOLVED_CALL_RE.search(sql)
    if match:
        return match.group(1)
    open_at = min(
        (m.start() for m in (_DBT_REF_RE.search(sql), _DBT_SOURCE_RE.search(sql)) if m),
        default=0,
    )
    return sql[open_at:].split("}}")[0].strip()


def has_dbt_jinja(sql: str) -> bool:
    """Return True if sql contains a dbt ref() or source() Jinja call."""
    return bool(_DBT_REF_RE.search(sql) or _DBT_SOURCE_RE.search(sql))


def dbt_macro_kind(sql: str) -> str:
    """Name the dbt macro this SQL calls, for errors that must say which.

    Matches the same patterns as has_dbt_jinja, so an identifier that merely
    contains "ref(" (`array_ref(...)`) cannot mislabel a source() call.
    """
    return "ref()" if _DBT_REF_RE.search(sql) else "source()"
