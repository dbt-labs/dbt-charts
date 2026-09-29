# dbt charts

The dbt charts core engine: YAML board compiler, query executor, renderer, HTTP server, and inspector. The CLI (`dct`) and AI/MCP interfaces are thin wrappers over this package's Python APIs.

## Verbs
| Verb | Purpose |
|------|---------|
| `dct validate [PATH]` | Validate board YAML for errors (no DB); default: `charts/` |
| `dct render <board>` | Compile + execute + write static exports (svg, html, png, pdf, json) |
| `dct serve` | Start local server; use its URL for browser previews |
| `dct describe <board>` | Summarize a board's queries, charts, variables, and layout |
| `dct query SOURCE 'SELECT …'` | Execute raw SQL and return sample rows (CLI parity with MCP `execute_query`) |
| `dct query board.yml NAME` | Run a named board query (sample rows) |
| `dct query … --validate` | Static SQL lint, without executing |
| `dct query … --describe` | Column schema for the query |
| `dct search <query>` | Search boards by keyword with ranked results |
| `dct impact <column>` | Which boards reference a column (reverse index; no DB) |
| `dct docs [TOPIC]` | Offline YAML reference (topic catalog, one section, or `--search`) |
| `dct examples [SLUG]` | List bundled board specimens, or print one's YAML |
| `dct skills [NAME]` | List packaged agent skills, or print one |
| `dct migrate` | Rewrite older board YAML to the latest *released* syntax (never further, even with a change in flight) |
| `dct init` | Bootstrap a project (`skills`, `mcp`, `ci` subcommands wire up the rest) |
| `dct cloud` | Operate dbt charts Cloud from the terminal |
| `dct --version` | Print version + install path; first check when output looks stale |

## Quick start
```yaml
charts:
  rev: {query: {sql: "SELECT month, SUM(revenue) FROM orders GROUP BY 1"}, type: bar, x: month, y: revenue}
```
```bash
dct validate charts/rev.yaml && dct render charts/rev.yaml
```

Errors carry doc pointers and `did you mean` hints — follow them. Canonical registry: `dbt_charts/core/diagnostics/`. Every code is `ERR-{SLUG}` (no domain segment in the string — `ErrorCode.domain` carries that); ERR-INTERNAL is a fallback that signals a bug (an unmigrated or wrapped raise site), not an accepted tier — treat any occurrence on a common failure path as a defect.

## Testing

```bash
just test                                          # full suite
just test-file tests/<path>/test_foo.py::test_name  # one test
```

`test-file` takes a package-relative path; in the monorepo run both from the repo root.

**Changing a golden under `tests/visual/goldens/` requires `just viz-review` before pushing** — pre-push and the `visual` CI job check each golden's sha256 against an APPROVED viz-review artifact on the branch. Run it from the worktree holding the changed goldens; from any other checkout it silently no-ops. Workflow: `docs/contributing/visual-tests.md`.

Put test output in a temp file so further searches can be done without re-running. Repeatedly running the suite without code changes is inefficient.

## Type-state gate

`just type-state` (`scripts/type_state_gate.py`) flags a `dbt_charts/core` site iff it is unmarked **and** its line was added or modified since the merge base. No committed baseline, so nothing for two branches to conflict over. `silent_fallback` / `cast` / `type_ignore` / `object_annotation` / `explicit_any` are blocking; `optional` is report-only (`T | None` is a contract here, not rot on its own).

**A site is flagged by the line it's on, not by matching its code against other sites.** The trade-off: **editing a line that already carries pre-existing unmarked rot flags it, because you touched it.** Add a marker or leave the line alone.

To approve growth you believe is genuinely correct, add a marker on any line of the site's span:

```python
size = cfg.get("width", 320)  # type-state: silent_fallback — CLI default, documented in the docstring
```

The reason is mandatory and reviewed like a `# noqa: BLE001 — <reason>`. A missing reason, an unknown category, or a hyphen/en-dash instead of an em-dash is a hard error, not a silent non-approval — a reasonless marker would otherwise look "free" to the gate and swallow a later, real marker on the same line. Only one marker is honored per physical line, so a line carrying more than one rot site needs care — read `_print_sites` in the gate script before marking one.

## Comments

Comments in this package are about this library only — never about how it is hosted or deployed elsewhere.

## Key files

- `core/compile/` — YAML → normalized document
- `core/execute/` — run queries against databases
- `core/render/` — normalized doc → output (HTML, SVG, etc.)
- `core/serve/` — HTTP server for boards
- `core/inspect/` — schema inspection / table profiling (powers the `/data` browser)
- `core/registered_views/` — route→template→generated-board mechanism behind the `/data/` browser; each *registered view* matches a URL pattern, runs optional pre-template queries, and renders an auto-generated board. Not to be confused with the `/data/` surface itself (user-facing) or `plan_entity_variables` (a different, unrelated meaning of "entity"). **Reserved slug prefixes:** `data/` and `inspector/` route to the built-in system views. Don't name user boards with these prefixes — they collide with the built-in handlers (a board *file* at the prefix is shadowed by the router; a real `charts/data/` *directory* is still directory-listed in `dct serve`).
- `src/dbt_charts/core/render/chart/AGENTS.md` — **canonical chart-rendering philosophy and render-layer invariants** (its `## Implementation philosophy`)
- `cli/` — `dct` command-line interface (thin wrapper)
- `ai/` — AI/MCP interfaces (thin wrappers)
- `agent_api/` — typed Python API every CLI and MCP verb delegates to
- `integrations/` — external integrations (Pygments lexer for board YAML, markdown, etc.)
- `core/compile/schema/renderers/` — schema IR → derived artifacts (JSON Schema, highlight manifest, TextMate grammar)
- `data/highlighting/board.json` — **committed highlight manifest** (single source of truth for top-level keys, enum values, SQL block scalar keys). Regenerate with `just gen-highlight-artifacts` after model changes.
- `core/defaults/themes/*.yaml`, `core/defaults/palettes/`, `core/defaults/default_config.yml` — themes, palettes, and default config (chart-level defaults live in theme YAML). Adding or removing a theme/palette YAML file also requires `just generate-schema-names` (regenerates `core/compile/models/schema_names.py`'s `ThemeName`/`PaletteName`/`StopsPaletteName`/`ScalePaletteName`).

## Implementation philosophy

**Render-layer invariants live in `src/dbt_charts/core/render/chart/AGENTS.md`** — re-read its `## Implementation philosophy` before adding anything to the render layer that touches data shape.

**Board text is parsed by libyaml's `CSafeLoader`, and only by it.** `core/utils.py`'s `YAML_LOADER` is the one loader every parse of board text in this library goes through (`UniqueKeyLoader`, the source map, the error formatter, authoring edits, the serve-time alias index, `agent_api`'s listings). The two scanners do not accept the same language, so a `yaml.safe_load` on board text anywhere is a bug, not a style choice. A PyYAML build without libyaml is unsupported: `core/utils.py` raises `ImportError` with the install hint, never a silent fallback to the pure-Python scanner. The loader also refuses text nested past `MAX_YAML_NESTING` before composing it: libyaml's C composer segfaults at ~25k levels rather than raising, and no caller can catch that.

**Don't pin theme/default values in tests.** Theme values (`page_canvas == "#fafafa"`, `bar.size == 20`, `compiled_style.border.radius == 8`) and chart defaults are tunable. Patterns like `assert page_canvas == "#fafafa"` fail on any legitimate default tweak and add zero signal beyond what existing behavior tests catch. Test structure, presence, behavior under override, and pipeline correctness instead.

**Also don't assert deletion or absence of Python symbols.** `assert not hasattr(...)`, source-grep for removed functions — that's testing Python, not our logic.

**Test file names mirror the src path.** `src/dbt_charts/agent_api/search.py` → `tests/agent_api/test_search.py`.

**Avoid bare-star keyword-only separators (`*`) in function signatures.** Private functions (`_name`) must never use `*`. Public functions: only reach for it when argument order at the call site would be genuinely ambiguous without keyword labels — not as a blanket API-stability hedge. Never force `foo(x=x)` when the variable name already makes the intent clear; pass positionally instead.

**New author-surface fields are reviewed, not free.** Boards, charts, and themes are a public contract — the JSON Schema, docs, and highlight manifest all derive from it, and removing a field later breaks every board that authored it. Before adding a field, try hard to reuse or slightly reshape an existing field's semantics instead (see the accepted/rejected chart-field table in `src/dbt_charts/core/AGENTS.md`). A diff touching `src/dbt_charts/agent_api/docs/yaml-reference.md` should carry a stated reason an existing field couldn't cover the need — question new entries there in review, don't wave them through.

**A change under `src/dbt_charts/core/render/` never also edits
`libs/chart-svg/`.** *(Monorepo-only — the paths below don't exist in the
exported OSS repo.)* The Rust port measures itself against this package —
production is its differential *oracle*, not a consumer — so a render change
moves the port's target by definition. Reconciling that is
`chart-svg-sweep-nightly.yml`'s job, not yours: it ratchets the sweep nightly
and, when one regresses, opens its own `chart-svg:` PR or files an issue. Do not mirror a semantic
into the Rust, do not refresh `libs/chart-svg/sweep/` baselines, and do not
refresh the production-source citations the Rust comments carry — and reviewers must
not ask for any of it. A render diff that also touches `libs/chart-svg/` is
wrong on that ground alone; split it. (Repo-wide changes that legitimately span
both trees — CI config, a lint sweep — are not render changes and are fine.)

**No internal decision identifiers in shipped artifacts.** Design-doc section labels (`C7`, `S3`, `D12`, etc.) are internal planning shorthand — they must not appear in code, tests, docstrings, AGENTS.md files, or any artifact that ships in the package or is visible to contributors. Use a plain description instead.

### agent_api thin-wrapper rule

`src/dbt_charts/agent_api/` is the canonical home for every function exposed to agents via CLI or MCP. Functions there must have typed args and typed returns (no `dict[str, Any]`, no JSON strings), no Cloud/Django imports, and I/O scoped to what the verb implies.

**`cli/commands/` and `ai/mcp/` are thin wrappers — they may only:**
1. Parse / validate arguments
2. Call a function from `dbt_charts.agent_api`
3. Format the result for output

Any validation, path resolution, compilation, execution, or rendering in these layers is a violation. A PR that adds a new `dct <verb>` without a corresponding `agent_api` function is rejected — **except `dct cloud` verbs**, whose one call is into `dbt_charts.cloud_client` instead, never `agent_api`: `agent_api` is local-by-contract (no network, no Django) and `cloud_client` is the only module that talks to Cloud. A PR that adds business logic to a CLI command file or MCP server module is rejected.

### Module boundaries

tach enforces: `dbt_charts.cli` may depend only on `dbt_charts.agent_api` and `dbt_charts.cloud_client` (both explicit `depends_on` edges on unlayered modules — in tach 0.35, layer ordering implicitly allows skip-level and same-layer edges, so this is the enforcing shape); `dbt_charts.cloud_client` may import nothing first-party at all (`cli/commands/cloud.py` parses arguments, calls one client method, prints the result — same thin-wrapper rule); `dbt_charts.core` cannot import `dbt_charts.cli` or `dbt_charts.ai` (layer ordering); `dbt_charts.ai.tools` cannot import `dbt_charts.ai.mcp`.

`dbt_charts.cli` → `dbt_charts.core` is forbidden — route through `dbt_charts.agent_api`. The remaining direct import (`commands/mcp.py`'s lazy `dbt_charts.ai.mcp` import) carries a `# tach-ignore(cli->ai debt…)` marker.

## Product skills (`dbt_charts/ai/skills/`)

Skills under `dbt_charts/ai/skills/` ship in the wheel and are the knowledge product dbt charts offers to AI agents. They are distinct from any skills that guide the humans and agents working *on* this repo.

### How agents reach them

The CLI verbs (bare `dct`, as a customer would invoke them):

```bash
dct skills                # list all product skills
dct skills <name>         # print one skill's SKILL.md body
dct docs                  # topic catalog (bare) or cheatsheet overview
dct docs <topic>          # one H2 section (board, queries, charts, variables, layout, getting-started, errors, …)
dct docs all              # full reference file
dct docs --search <q>     # full-text search across all topics
dct examples              # list bundled board specimens
dct examples <slug>       # print one specimen's board YAML (e.g. boards/kpi-overview)
dct examples --search <q> # search slugs, titles, and specimen YAML
```

`dct examples` is the third knowledge surface: skills give workflow prose, docs give field reference, examples give a whole working board to copy. Specimens live in `dbt_charts/ai/examples/<category>/` and every one renders standalone — inline `columns`/`values` data, no `source:` — so an agent can paste one into a project the wheel knows nothing about.

Topics are sliced from `src/dbt_charts/DBT_CHARTS_SYNTAX.md` (single source).

In-repo agent guidance — including any `AGENTS.md` / `CLAUDE.md` here — should point at these CLI verbs rather than at the on-disk markdown files. The CLI is the dogfooded surface; pointing at file paths invites stale copies.

### Convention: `dct` vs `uv run dct`

Two surfaces, two conventions — keep them straight:

- **Inside `dbt_charts/ai/skills/*/SKILL.md`** (the wheel-shipped product skills, read by customer-environment agents): use **bare `dct`**. Customer installs put `dct` on the venv's `PATH` via `pip install dbt-charts`, and `uv` may not be on their `PATH`.
- **Inside this repo's own contributor docs**: use **`uv run dct`**. The repo's `.venv` is not auto-activated for every shell, and bare `dct` will hit a stale `PATH` install. The `uv run` prefix forces resolution against this worktree's environment.

`cli/_workspace_guard.py` makes a violation of that second rule visible: inside a checkout that contains the package, `dct` warns to stderr when the running build didn't come from that checkout's own editable install. It never fails the command. Two limits worth knowing: a build published before the guard existed stays silent wherever it answers from, and the probe reads the source tree rather than the install, so a pip-installed `dct` run from inside a clone warns too. Machine stderr consumers set `DCT_NO_WORKSPACE_GUARD=1` — the advisory's non-JSON first line otherwise corrupts a `--diagnostics-json` parse.

### Authoring a product skill

Skills carry `kind: workflow | pattern` frontmatter and follow an `<object>-<action>` naming standard; shared surface names are macro-expanded from `agent_api/surface_aliases.yaml` (`{{ s_X }}`). Read an existing skill in `dbt_charts/ai/skills/` before adding one.

Source directory names, frontmatter `name:`, and the registry all stay bare (`board-build`, `troubleshooting`). `dct init skills` namespaces a file-installed copy under a `dct-` prefix (`dct-board-build/`) so it never collides with a project's or another tool's skills — that prefix exists only there, never in the source tree, Cloud's slash commands, or MCP `get_skill`. Full authoring detail (the `s_skill_name_*` bare-name macro family, the naming standard): `docs/contributing/product-skills-authoring.md`.
