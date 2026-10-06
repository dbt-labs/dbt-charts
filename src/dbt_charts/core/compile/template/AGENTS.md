# compile/template

Jinja resolution for board-authored text: query SQL, chart titles, layout/sizing
expressions, chart labels, `where:` expressions.

- `jinja.py` — `resolve_jinja_template`, the main board-text render path
  (strict/lenient). `extract_variable_dependencies` only parses, never renders.
- `parameterized.py` — `render_parameterized`; values reach SQL only as
  collected parameters behind a placeholder, never interpolation.
- `labels_env.py` — `label_jinja_env`, for chart-label templates and `where:`
  expressions (`compile/resolve/chart/label_data.py` builds the actual context).
- `_helpers.py` — `_LenientUndefined`, `_QueryNamespace`/`_QueryProxy`, shared
  by the two paths above.
- `environment.py` — `BoardTemplateEnvironment`, the one sandboxed class every
  board-text renderer must build from; also governs `registered_views/expander.py`
  (outside this package, builds its own instance, enumerated below).
- `variables.py` — board-variable resolution; renders nothing.

## Implementation philosophy

**Every name placed in a context or registered on an environment is a
permanent, reviewed decision.** `ImmutableSandboxedEnvironment` blocks
dunder-attribute traversal off any value it hands a template —
`{{ x.__globals__ }}`, `{{ x|attr(...) }}`, `{{ x["__globals__"] }}` all raise
`SecurityError` (pinned by `test_jinja_sandbox.py`). It cannot judge what a
context-injected callable *does* when invoked, or what a non-dunder attribute
on its return value exposes — that gap is what this contract closes.

**Reachable set per render path**, pinned by
`tests/core/compile/template/test_render_context_survivors.py` (fails CI on
any name added, or existing name rebound, in a context or an environment's
`globals`/`filters`/`tests` — compared by identity against a stock
`BoardTemplateEnvironment()`, so overriding an existing name, like `format`
below, is caught too):

| Path | Context keys | `Environment.globals` | `Environment.filters` |
|---|---|---|---|
| `resolve_jinja_template` | board `variables`, `queries` (`_QueryNamespace`, when given), `filter`, `filter_date_range` | Jinja defaults | Jinja defaults |
| `render_parameterized` | board `variables` (wrapped in `_ParameterizedValue`, except `exclude_vars`), `queries` (verbatim from `variables["queries"]` — `render_parameterized_with_queries` wraps it in `_QueryNamespace` before delegating here), `filter`, `filter_date_range` | Jinja defaults | Jinja defaults |
| `label_data.py`'s `prepare_label_data`/`prepare_pie_label_data` | raw per-row query values (scalar/`Decimal`/`datetime`/JSON, never callable), `index`/`is_first`/`is_last`, plus (pie, the only caller today) `percent`/`value`/`total`/`color` | Jinja defaults | Jinja defaults; `format` **overridden** by our own d3-format callable |
| `expander.py`'s `render_template` | `path`, `queries` (dict of `ViewQueryResult`), `sql_identifier` (bound per-render) | Jinja defaults + `plan_variables`, `plan_key_variables`, `pivot_column_profiles`, `column_header_styles` (module-level, registered once at import) | Jinja defaults |

`filter`/`filter_date_range` are always exactly those two names: the raising
stubs in `jinja.py`, or (via `filter_helpers=`) the real closures from
`parameterized.py`. `resolve_jinja_template` rejects any other key in
`filter_helpers` (`RESERVED_FILTER_HELPER_NAMES`) — it swaps the stub, it
doesn't widen the context. The one caller (`execute/adapters/dbt_adapter.py`)
binds exactly those two.

`label_jinja_env` also sets `finalize=_finalize_none` (renders `None` as
`""`, not Jinja's `"None"`) — a namespace of its own, same discipline
applies. `Environment.tests` is a fifth namespace, stock and untouched on all
four environments, pinned the same way.

Jinja's own globals (`cycler`, `dict`, `joiner`, `lipsum`, `namespace`,
`range`) trace to no board feature and stay anyway: three (`cycler`,
`joiner`, `namespace`) return an object with public non-dunder attributes,
but dunder access is already blocked, and dropping them means forking
`ImmutableSandboxedEnvironment` into a real subclass. The "return a plain
string" rule below governs what *we* register, not the engine's own defaults.

**May go in a context/global/filter:** a plain board variable (str/number/bool/
date/list/dict — never a module, config object, or anything reaching
credentials); or a helper *we* register that validates its arguments and
returns a plain `str` (`filter`/`filter_date_range`/`format`/`sql_identifier`
all do) or, for `expander.py`'s four view-helper globals, a plain `list`/`dict`
of scalars — no object with its own methods.

**Must not:** return an object a template can keep traversing (a proxy, a
partial, `self`, anything with `__dict__`) — that widens the surface the
sandbox polices instead of narrowing it. Exceptions, each safe for a stated
reason: `_QueryNamespace`/`_QueryProxy` and `_ParameterizedValue` keep all
state `_`-prefixed, so sandboxed `getattr` blocks it, leaving only
`.cache`/`__str__`; `_NullValue` carries no state; `ViewQueryResult`
(`expander.py`'s `queries` value type) exposes `.rows`/`.columns`/`.one` by
design — result data for a page to iterate, the same trust level as row
dicts. A helper that must exist but never be called is marked
`@jinja2.sandbox.unsafe` — none currently need it; the raising stubs are
deliberately callable (they raise `CompilationError`, not silence).

**Adding a name is deliberate:** update the pinning test first (it should
fail on the new name), then this table, same PR. Only if an authored board
feature needs it, and only after confirming it validates arguments and
returns a plain string (or plain data structure, for a view-helper global).

### If you catch yourself thinking…

| Objection | Response |
|---|---|
| "it's just one more helper, filter_helpers already validates its args" | Validation says nothing about the *name* it's bound under — that's what widens the context. Bind through `RESERVED_FILTER_HELPER_NAMES`, or add the name to the table and test. |
| "wrap it in a proxy for a nicer API" | A proxy is itself an object with attributes — it widens what a template can traverse. Return a plain string/data structure. |
| "the sandbox already blocks `.__globals__`" | True and irrelevant — the sandbox blocks attribute traversal, not what a callable does or returns. That's this contract's job. |
| "it's read-only, not security-sensitive" | `expander.py`'s view helpers are read-only and still enumerated and pinned. |
