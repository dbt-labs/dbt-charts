# Diagnostic messaging standards

Rules for authoring a new `ErrorCode` or `WarningCode` in
`dbt_charts/core/diagnostics/codes_*.py`. Both are `DiagnosticCode` subclasses
registered on the same `REGISTRY` and share every rule below except where
called out. The full reference these rules produce is rendered from `REGISTRY`
by `render_reference.py` when the docs site builds, and published as the docs
site's Error Reference and Warning Reference pages.

## Code naming

`ERR-{DESCRIPTION}` for an `ErrorCode`, `WARN-{DESCRIPTION}` for a
`WarningCode` — both all caps, hyphen-separated, no domain segment in the
string. `DESCRIPTION` names the specific failure or condition, not the fix
(`KPI-MULTIROW`, not `FIX-YOUR-QUERY`). The domain still matters — it lives
on `DiagnosticCode.domain`, set from the file the code is registered in
(`codes_compile.py` → `compile`, `codes_render.py` → `render`,
`codes_execute.py` → `execute`, `codes_serve.py` → `serve`,
`codes_query.py` → `query`) — but two codes that would only differ by domain
are the same failure and must merge into one code, not grow a
domain-qualified pair. Constructing the code raises if its prefix doesn't
match its level (`ERR-` for `error`, `WARN-` for `warning`) — enforced by
`DiagnosticCode._validate_code_prefix`, a `model_validator` that runs at
construction time.

## `message_template`

- State what failed or was detected, with the concrete values substituted
  via `{field!r}` / `{field}` placeholders — never a generic "something went
  wrong" or "found an issue".
- Say what to do next in the same message when the fix is a single obvious
  action ("Use a query that returns a single row"). Reserve `hint_generator`
  for fixes that need computed context (a "did you mean" suggestion, a
  dynamic list of valid values).
- No raw `\n` inside a template meant to render on one line — the generated
  reference table renders each template as a single row; embed a real
  newline only in a template documented as intentionally multiline (e.g. an
  example YAML block scalar).
- Placeholders are named after the raise/emit site's actual variables —
  don't invent a placeholder the call site doesn't pass; a missing key
  raises `KeyError` at format time (fail loud, not a silently wrong message).

## `fix_template`

Required on every `WarningCode` — constructing a `WarningCode` with it unset
is a type error (`WarningCode.fix_template: str`, not `str | None`), so
pydantic rejects it before `REGISTRY.register()` is ever called. A warning
is, by definition, something the author can choose to
leave as-is or fix; the registry enforces that every warning documents the
fix, not just the condition. `ErrorCode.fix_template` is usually left
`None` — an error's fix is normally embedded in `message_template` (see
above) or delegated to `hint_generator`; only set it on an `ErrorCode` when
the fix text is long enough or reusable enough to warrant its own field, and
don't duplicate the same fix text in both `message_template` and
`fix_template`.

## `hint_generator`

Only add one when the hint needs computed context the template can't express
statically — a close-match suggestion (`suggest_close_source`,
`suggest_close_theme` in `hints.py`) or a value drawn
from runtime state. A hint that's always the same static string belongs in
`message_template` (errors) or `fix_template` (warnings) instead — don't
wrap a literal in a `lambda **_: "..."`.

## `docs_topic`

Required on every code (validated by `test_registry.py`'s coverage check).
Groups the code under its `##` section in the generated Error and Warning
Reference pages. Use the H2's slug (e.g. `charts`, `queries`) — not a
hand-invented dotted namespace.

## AI phrasing

Every surface (CLI, MCP `check_dashboard`, `dct serve` inline blocks) reads
the same `Diagnostic` shape. Write for that shared
audience:

- Lead with what failed or what was detected, not with an apology or hedge.
- Name the concrete file/field/chart when known — the diagnostic object
  already carries `range` (errors) or `chart`, `field`
  (warnings); don't repeat them prose-style in the template if the
  structured fields already say it.
- No silent fixes. An error means the input is wrong and a warning means a
  pattern is worth a second look; the template explains why and how to fix
  the YAML — it must never describe the engine silently working around bad
  input, because it doesn't.

## Adding a code

1. Register it in the domain's `codes_*.py` via `REGISTRY.register(ErrorCode(...))`
   or `REGISTRY.register(WarningCode(...))`.
2. Import it in `diagnostics/__init__.py` if any other module needs the constant
   (side-effect registration happens on import either way).
3. The docs site's Error/Warning Reference pages render from `REGISTRY`, so
   they need nothing regenerated.
4. For a `compile`- or `render`-domain code, regenerate the Rust code tables:
   `uv run python libs/chart-svg/src/chart_svg_dev/gen_diagnostic_codes.py`.
   `test_generated_codes_are_up_to_date` in
   `libs/chart-svg/tests/test_diagnostic_codes.py` fails while
   `libs/chart-svg/chart-renderer/src/codes.rs` and
   `libs/chart-svg/compile/src/codes.rs` are stale.
