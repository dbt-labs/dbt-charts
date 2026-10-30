# dbt charts YAML Syntax

Authoring reference for dbt charts board YAML. Every option in this file is enforced by the compiler (`extra="forbid"` is set on every model — unknown keys are schema errors).

The same reference is published at https://docs.dbtcharts.com/reference/yaml-reference/.

## Getting Started

dbt charts workflow — from a dbt project to a running dashboard.

### Step 1 — Validate your YAML

```bash
dct validate charts/my_dashboard.yml
```

`dct validate` performs YAML schema + cross-reference validation. It checks that every field, chart reference, query name, and variable is correctly structured. **No database connection is required.**

To verify that your database connection works, run a simple query:

```bash
dct query 'SELECT 1' --source <your_source>
```

A successful result means your data source is reachable.

### Step 2 — Browse your schema

Explore available tables and columns with metadata SQL before writing queries:

```bash
dct query mydb "SELECT table_schema, table_name FROM INFORMATION_SCHEMA.TABLES"
dct query mydb "SELECT column_name, data_type FROM INFORMATION_SCHEMA.COLUMNS WHERE table_name = 'orders'"
```

(DuckDB also supports `DESCRIBE orders`; SQLite uses `sqlite_master` and
`PRAGMA table_info(orders)`.)

Never invent column names — always verify them against the schema first.

### Step 3 — Build one chart

Write the minimal YAML needed for a single chart:

```yaml
source: mydb

queries:
  revenue: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1 ORDER BY 1

charts:
  revenue_trend:
    query: revenue
    type: line
    x: month
    y: total

rows:
  - revenue_trend
```

Then validate and render:

```bash
dct validate charts/my_dashboard.yml
dct render charts/my_dashboard.yml
```

Add each additional chart after the previous one validates and renders cleanly.

### Step 4 — Serve locally

```bash
dct serve
```

Opens a live-preview server. Edit the YAML and reload the browser to see changes.

**See also:** the Cheatsheet section (minimal examples), the Queries section, the Charts section, the Variables section.

## Cheatsheet

One screen of essentials. Each topic below has a dedicated H2 (Board, Queries, …) for full coverage.

### Minimal board

```yaml
source: my_profile

queries:
  revenue: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1 ORDER BY 1

charts:
  revenue_trend:
    query: revenue
    type: line
    x: month
    y: total

rows:
  - revenue_trend
```

### Top-level board fields

- `title`, `notes`, `tags`
- `source` / `sources` — default and named data connections
- `variables` — interactive filter controls
- `queries` — named SQL / CSV / HTTP / dbt / inline queries
- `charts` — named chart definitions
- Exactly one of `rows`, `cols`, `grid`, `tabs` (or `text:` for a text-only board)
- `theme`, `style`, `id`, `width`, `height` — presentation

### Queries (named, parameterized, inline)

```yaml
variables:
  region:
    options:
      static: [US, EU, APAC]

queries:
  # Bare-string form — SQL inherits the board-level source
  revenue: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1 ORDER BY 1

  # Long form with options
  filtered:
    sql: "SELECT * FROM orders WHERE region = '{{ region }}'"
    source: warehouse

  # Inline data (no DB needed)
  targets:
    columns: [region, target]
    values:
      - [US, 100]
      - [EU, 80]
```

### Variables

```yaml
variables:
  region:   # input is inferred (select); see Variables for all 14 input types
    options: { static: [US, EU, APAC] }
    default: US
```

Reference variables inside queries with bare `{{ region }}` — no `variables.` prefix.

### Charts

```yaml
charts:
  revenue_trend:
    query: revenue                 # Named query reference
    type: line                     # See the Charts section for all 16 authorable chart types
    x: month
    y: total
    color: segment

  quick_chart:
    query: "SELECT month, revenue FROM orders"   # Bare SQL shorthand (no named query needed)
    type: bar
    x: month
    y: revenue
```

### Layout

Pick exactly one of:

- `rows: [chart_a, chart_b]` — vertical stack
- `cols: [chart_a, chart_b]` — horizontal arrangement
- `grid: { columns: 24, items: [...] }` — CSS-grid placement
- `tabs: { items: [{title: ..., rows: [...]}] }` — tabbed navigation

**See also:** the Board section (full top-level reference),
the Queries section, the Charts section, the Variables section, the Layout section,
the Errors section (common error codes).

## Board

The top-level YAML mapping is a board. Exactly one layout key (`rows`, `cols`, `grid`, `tabs`) must be present unless `text:` is set.

```yaml-schema
title: "Sales Overview"
notes: "Monthly KPIs and trend"
tags: [sales, weekly]

source: my_profile             # Default source (a name from dbt_charts.yml's sources: registry)

variables:                     # Optional — interactive controls
queries:                       # Named queries
charts:                        # Named charts
rows: [ ... ]                  # Or cols:, grid:, tabs: (pick one)

theme: neon                    # Theme name — sugar for `extends: neon`; inherited by nested boards

# Nesting / layout primitives (mostly for nested boards inside rows/cols)
id: my_board                    # Auto-generated from filename if omitted
style: { padding: 16, background: dbt-grays.1 }
width: 400                     # Pixels or "50%" when nested
height: 300

card_gap: false                # When true, adds gap between cards
chart_focus: revenue_trend     # Render only one chart with its dependent variables

details: "Click to expand"     # Collapsible section (string, or the object form below)
# details:
#   summary: "Click to expand"
#   expanded_title: "Hide details"
#   expanded: false

cache: 1h                      # Result-cache policy for every query on this board
extends: base_board            # Inherit from another board / theme (see below)
links:
  auto: true                   # Table charts auto-link rows to /data/ detail pages
  open: modal                  # Default for how links open: page | modal | preview | new_tab
```

Top-level fields:

| Field | Type | Notes |
|-------|------|-------|
| `title` | string | Display title |
| `notes` | string | Prose about the board, carried to the host rather than drawn on the board. Cloud shows it in the dashboard-card hover overlay. |
| `tags` | list[string] | Tags for categorization/search |
| `text` | string | Markdown body for text-only boards |
| `html_policy` | enum | HTML rendering policy for body text: `none` (default — HTML is escaped), `safe-subset` (reserved; currently renders as `none`), `trusted-raw` (raw HTML — trusted first-party content only; deployments may cap this). |
| `aliases` | list[string] | Absolute URL paths that 302-redirect here (see [Aliases](#aliases)) |
| `source` | string | Default source name for every query below (from `dbt_charts.yml`'s `sources:` registry), or an inline file path for a single colocated CSV/JSON/Parquet file. Inheritable via the `meta.yml` cascade. |
| `cache` | scalar \| object | Query-result cache policy for the board's queries (see [Caching](#caching)) |
| `variables` | object | See [Variables](#variables) |
| `queries` | object | See [Queries](#queries) |
| `charts` | object | See [Charts](#charts) |
| `rows` | list | Vertical layout — chart names or inline blocks |
| `cols` | list | Horizontal layout — chart names or inline blocks |
| `grid` | object | CSS-grid layout (see [Layout](#layout)) |
| `tabs` | object | Tabbed layout (see [Layout](#layout)) |
| `card_gap` | bool | Add visible gap between cards (default `false`) |
| `chart_focus` | string | Render only this chart (with its variables) |
| `details` | string \| object | Collapsible section: a bare summary string, or `{summary, expanded_title, expanded}` |
| `id` | string | Explicit board ID (auto-generated from filename) |
| `style` | object | Board-style block (see [Board style](#board-style) in the Styling section) |
| `width` | string \| int | Width when nested (`"50%"` or pixels) |
| `height` | string \| int | Height when nested |
| `visible` | bool \| string \| object | Render condition when nested as a layout item (see [Layout visibility](#layout-visibility-visible)) |
| `theme` | string | Theme name (e.g. `clarity`, `paper`, `neon`) — sugar for `extends:`; inherited by nested boards |
| `extends` | string \| list[string] | Board name(s)/path(s) or a built-in theme this board inherits from (see [Inheritance](#inheritance-extends-and-theme) in the Styling section) |
| `links.auto` | bool | Auto-link table rows to their `/data/…/detail/` pages (default `false`). An explicit chart `link:` always wins; `link: false` on a chart suppresses its automatic link. |
| `links.open` | `page` \| `modal` \| `preview` \| `new_tab` | How a link opens when it sets no `open:` of its own (default `page`). Precedence: the link's own `open:`, then `links.open`, then `page`. A `?variable=` link, a table cell's filter link, an implied email/url or KPI link, and a custom chart's per-row link always stay `page`. |

`links:` is set on the root board (or `charts/meta.yml`) only; a nested board that authors it is an error (`ERR-LINKS-NESTED`), and nested boards take the root's.

`board:` as a top-level key is rejected. Put board properties (title, rows, queries, …) directly at the YAML root.

A markdown board (a `.md` file under `charts/`) is a document: its prose flows in one column (`style.text.column.max_number: 1`) unless its frontmatter `board:` block, a `meta.yml`, or an `extends:` template sets another value. YAML boards flow into as many columns as the width allows.

### Caching

`cache:` sets the query-result cache policy. It is authorable at four scopes —
project config → source → board → query — and the nearest scope wins
field-by-field. Every scope but the project root takes any scalar below
(the root must state a ttl — it has no parent to inherit one from):

```yaml-schema
cache: 1h        # on, expire after an hour (units: s m h d w — compound 1h30m OK)
cache: forever   # on, never auto-expire (the default cache file still drops entries after ~1 week; --cache PATH keeps them)
cache: true      # on, inherit the parent scope's ttl
cache: false     # off — the only opt-out
```

A query-level `cache: 5m` refines the board's policy for that query only. The
block form `cache: {ttl: 5m}` means the same as the scalar.

### Aliases

`aliases:` is a list of absolute URL paths that 302-redirect to this board's canonical file-path URL.  Each entry must start with `/`; trailing slashes are normalized automatically.

```yaml
aliases:
  - /old-reports/
  - /legacy/sales/
```

Rules:
- Every alias must be absolute (leading `/`) — a relative entry fails validation on that board.
- An alias must not collide with a real board file path — `dct serve` raises an error at startup if it does.
- Each alias must be unique across the project — two boards claiming the same alias is a startup error under `dct serve`. A host with no startup step resolves a duplicate to the first board by slug instead of reporting it.
- Aliasing a generated system route (data or inspector view) is allowed: the redirect takes precedence, letting you override what that URL serves.

#### Parameterized aliases (capture a path segment into a variable)

An alias may contain `<name>` capture segments.  A request matching the pattern
302-redirects to the board's canonical URL with each captured segment appended as a
query param of the same name — which the board then reads as its variable.  This
gives a single detail board a clean per-entity URL:

```yaml
# charts/milestone.yml  (canonical URL: /milestone)
aliases:
  - /milestones/<name>
variables:
  name: { input: select, options: { query: milestone_options, column: slug } }
```

Now `/milestones/m3-public-launch` redirects to `/milestone/?name=m3-public-launch`,
and `milestone.yml` renders with `name` bound to `m3-public-launch`.

- Capture names use the same grammar as built-in routes: `<name>` matches exactly
  one non-empty path segment (no `/`).  Use a capture name that matches the
  variable you want it to fill.
- A real board file always wins over a pattern alias, so `/milestones/` (the list
  board) and `/milestones/<name>` (the detail redirect) coexist without conflict.
- Plain aliases (no `<...>`) redirect as before; only aliases with a capture are
  treated as patterns.

There are two ways to override a data/inspector route for a given path:
- **Board file at that path** — create `charts/data/warehouse/schema/table.yml` and the server renders it directly (no redirect).
- **`aliases:` entry** — any board can declare `/data/…/` as an alias; the server issues a 302 to the declaring board's canonical URL.

Both approaches work.  A board file wins without a redirect round-trip; an alias lets a board live anywhere and still capture a system-view URL.

**See also:** the Queries section (data layer), the Charts section (display layer),
the Layout section (composition), the Styling section (themes and `style:`),
the Cheatsheet section (one-page essentials).

## Styling

How a board looks: a theme, `extends:` layers, and `style:` overrides. `style:` is typed paint, not CSS.

Built-in themes: `clarity` (default), `paper`, `vivid`, `stark`, and `neon` (the dark one). Switching is one line: `theme: paper`.

Cascade, lowest to highest priority; each layer merges key by key, so an override names only the leaves it changes:

1. The theme (`theme:`, or the last theme named anywhere in the `extends:` chain).
2. Folder `meta.yml` defaults (`charts/meta.yml` applies to every board beneath it; `extends: ./_brand.yml` there brands a whole project).
3. The board's own `extends:` layers, in list order.
4. The board's own keys and `style:`.
5. A chart's `style:`. Sparse: an omitted field inherits, `null` clears a nullable one.

### What style cannot do

- **No title band.** `style.title` has no `background`. Use a styled first `text:` row or a `type: callout` chart, and set its font color yourself: ink is not adjusted against the band.
- **There is no light/dark switch.** A board does not follow `prefers-color-scheme`. A dark variant is a second board that extends the light one plus `neon`: `extends: [./sales.yml, neon]`. A hand-set dark `style.background` alone leaves marks, table stripes and axis labels in light-theme colors.
- **No CSS.** `border-radius`, `margin-top` and other CSS keys are rejected. A chart's `height` sits at chart root, not under `style:`.

### Inheritance (`extends` and `theme`)

A board can inherit from other boards or a built-in theme. `extends:` names one
or more boards (by name or relative path) or a theme, low to high priority; the
child's own keys win over everything it extends. A bare name is matched against
the built-in themes first, then against a board at the *project root* — a path
ref (`./_base.yaml`, anchored to the extending file's own directory) is the only
form that reaches a board elsewhere, and the only form that resolves outside a
project.

```yaml-schema
extends: [company_defaults, quarterly_base]
title: "Q4 edition"
```

`theme: neon` is authoring sugar for `extends: neon` — setting both is an
error; use one or the other.

### Board style

`style:` on a board, nested board, or layout section accepts a fixed set of keys — not arbitrary CSS:

```yaml
style:
  padding: "16px"
  margin: "0 0 12px 0"
  background: dbt-grays.1
  font:
    color: dbt-grays.11
  gap: 12
  border:
    width: 1
    color: dbt-grays.4
    radius: 8
  text:
    align: center
    column:
      max_number: 2
      divider:
        visible: true
        width: 2
        color: dbt-grays.4
```

CSS-only keys like `border-radius`, `border-left`, `margin-top` are not board-style fields and are rejected.

### Title heading levels

`style.title.level` controls the H-level (H1–H6) used for board and chart titles.

```yaml
# Default — compute from count of titled ancestors (the recommended default).
# A titled root board is H1, a titled child section is H2, and so on.
# Bare layout wrappers (no title) do not advance the counter.
style:
  title:
    level: auto

# Lock level to a specific heading — useful for embedded dashboards where
# H1/H2 are reserved by the outer page.
style:
  title:
    level: 3   # this board and all descendants start from H3
```

When set to an integer, it cascades to descendants: titled children render at
`level + 1`, bare wrappers inherit the locked level unchanged.

### Named fonts

Text is styled through seven named fonts under `style.fonts`: `heading`
(board and chart titles, markdown `#` to `######`), `subtitle`, `body` (prose),
`label` (axis, legend, tooltip, KPI label, table cells and header,
variable controls), `caption` (footers, timestamps), `numeral` (KPI values,
quantitative axis labels) and `code`. Set a family or size once and every slot
bound to that font follows. Do not repeat `family:` on individual slots.

```yaml
style:
  fonts:
    heading:
      family: "'Source Serif 4', Georgia, serif"
      sizes: [30, 20, 16, 14, 11, 11]   # H1 to H6; only heading takes sizes
    body:
      family: Inter
    tag:                                 # a new font extending a shipped one
      extends: label
      weight: 600
      case: upper
  charts:
    kpi:
      label:
        font: tag                        # a slot by name
      value:
        font:
          extends: numeral               # a slot by name plus overrides
          size: 40
```

- A `font:` slot takes a bare name (`font: heading`), `{extends: <name>, ...overrides}`, or an inline block (`family`, `color`, `size`, `weight`, `style`, `decoration`, `case`, `line_height`). A bare string is always a font name, never a family.
- A named slot replaces whatever a theme bound to it, so prefer editing the named font. The root `style.font` and `details.arrow.font` take no name; restyle base text with `fonts.body`.
- A board's `fonts:` merge key by key with the theme's. An unknown name raises `ERR-FONT-UNKNOWN`; an `extends` cycle raises `ERR-FONT-REGISTRY`.

## Sources

Named connections, declared once in the project root config; boards reference them by name.

The `sources:` registry in the project root `dbt_charts.yml` names every connection boards may read from. A board's `source:` is always one of these names (or an inline path to a single CSV/JSON/Parquet file) — never a connection definition.

```yaml
# dbt_charts.yml — project root, not a board
sources:
  analytics:                     # A dbt project: credentials stay in profiles.yml
    type: dbt_profile
    profile: my_dbt_project      # Optional: defaults to $DBT_PROFILE, then dbt_project.yml's profile:
    target: dev

  local:                         # A DuckDB file (local-only, see below)
    type: duckdb
    path: ./data/analytics.duckdb
    schema: main                 # Optional default schema

  warehouse:                     # Direct warehouse; secrets via env_var(), never literals
    type: postgres               # or redshift (same fields); other types take different fields
    host: "{{ env_var('PGHOST') }}"
    port: 5432
    dbname: analytics
    schema: public
    user: "{{ env_var('PGUSER') }}"
    password: "{{ env_var('PGPASSWORD') }}"

  bq:
    type: bigquery
    project: my-gcp-project
    dataset: analytics           # Default dataset for unqualified table names

  files:                         # CSV/JSON/Parquet: each key under files: is a table name
    type: csv                    # or json, parquet
    files:
      orders: data/orders.csv
      returns: data/returns.csv
```

Then in a board: `source: warehouse` at the root (default for every `sql` query) or per query (`queries.<name>.source`). Check a source before writing boards: `dct query warehouse 'SELECT 1'`.

**Inside a dbt project, `sources:` is optional.** When `dbt_project.yml` sits at the project root and `dbt_charts.yml` declares no `sources:` (or does not exist), dbt charts adds one source named after the project's `profile:`, as if you had written `<profile>: {type: dbt_profile}`. Every SQL query that names no `source:` (query, board root, or `meta.yml`) runs against it. Declaring any `sources:` turns it off, and sourceless SQL is then an `ERR-SOURCE-NOT-NAMED` error; so is sourceless SQL outside a dbt project, or in a `dbt_project.yml` with no `profile:` (a dbt package).

Types: `dbt_profile`, `postgres`, `snowflake`, `bigquery`, `redshift`, `trino`, `duckdb`, `sqlite`, `csv`, `json`, `parquet`, `http`.

**Local-only types.** `duckdb` and `sqlite` read a file on the machine running `dct`. dbt charts Cloud connects only to `bigquery`, `postgresql` (Cloud's spelling of `postgres`), `redshift`, and `snowflake` — it never reads a `.duckdb` file from a repo, committed or not. For Cloud, load the tables into one of those warehouses, or export each table to a Parquet/CSV/JSON file committed in the repo and register it once under `sources:` (`type: csv | json | parquet`, a `files:` map, paths relative to the project root); every query then references the source by name, and it renders identically locally and on Cloud with no connection step. A board that is the only reader of one file can skip the registry and point at it inline: `source: <path>` resolves against the board's own directory or the project root, whichever exists (`source: ../data/orders.parquet` from `charts/`, or `source: data/orders.parquet` from any board depth). Both existing at once is a compile error naming both.

## Queries

Queries are the data layer. Charts reference queries by name; queries own what a column means, including the format name it prints with (`formats:`), and nothing about how it is painted.

```yaml
source: my_profile             # Optional: default source for every query below

variables:
  region:
    input: select
    options:
      static: [US, EU, APAC]

queries:
  # SQL — the default. `type: sql` is implicit when `sql:` is present.
  revenue: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1 ORDER BY 1

  # SQL with metadata
  filtered:
    notes: Monthly revenue filtered by region
    sql: SELECT * FROM orders WHERE {{ filter('region', region) }}
    source: warehouse           # Override board-level source
    setup_sql: CREATE TEMP FUNCTION norm(x FLOAT64) AS (x / 100.0);

  # CSV / JSON / Parquet file — declare a file source, then query it with SQL
  # (sources.local_files must define files: { targets: data/targets.csv })
  targets:
    type: sql
    sql: SELECT region, target FROM targets
    source: local_files

  # HTTP / REST API — self-contained; no source: field (nothing to look up by name)
  customers:
    type: http
    url: https://api.example.com/customers
    method: GET                  # GET | POST | PUT | DELETE | PATCH
    headers: { Authorization: "Bearer {{ api_token }}" }  # api_token: a board variable
    params: { status: active }
    body: { ... }
    json_path: $.data

  # Inline file source — a single colocated CSV/JSON/Parquet, no registry entry
  targets_inline:
    type: sql
    sql: SELECT region, target FROM targets
    source: ./data/targets.csv   # path (contains "/" or a data extension) = inline file; table name = stem

  # Inline values (no database)
  sample:
    type: values                 # Optional — implied by `rows:`, `columns:`, or `values:`
    columns: [name, score]
    values:
      - [Alice, 92.4]
      - [Bob, 87.1]
    formats:                     # How each column prints, everywhere; names only
      score: number_full
```

Query types (`type:` literals): `sql`, `http`, `values`, `schema`.

`type: schema` is a schema-metadata query — it returns metadata rather than
table rows. Its fields form a strict prefix ladder (each level requires every
level above it): no fields → list configured sources; `source:` → that
source's schemas; `+ schema:` → tables; `+ table:` → column profile;
`+ column:` → a single column. `table:` without `schema:` (or `column:`
without `table:`) is rejected.

```yaml
queries:
  orders_columns:
    type: schema
    source: warehouse       # ladder level 1 (omit everything to list sources)
    schema: analytics       # level 2 — requires source
    table: orders           # level 3 — requires schema
    column: order_id        # level 4 — requires table
```

Common fields (all query types):

| Field | Description |
|-------|-------------|
| `notes` | Metadata sentence about the query, for AI search and context. Never drawn on the board. |
| `formats` | `column: format name` map: how each column prints on every chart, table and KPI that shows it. A name is an engine format (`currency`, `percent`, `integer`, `year`, …) or a `style.formats` key; a d3 spec is rejected (`ERR-QUERY-FORMAT-SPEC`). Beats board-level slot formats; loses to a chart's own slot `format:` (KPI `value.format`, table column `format`, `axis_*.labels.format`). A key the query does not return is `ERR-QUERY-FORMAT-COLUMN-MISSING` |
| `source` | Source name (from `dbt_charts.yml`'s `sources:` registry), or an inline file path (`./data/x.csv`) for a single colocated CSV/JSON/Parquet. An inline connection dict (`{type: postgres, ...}`) is rejected — reference a named source instead. Not accepted on `http` queries. |
| `cache` | Result-cache override for this query — `5m`, `forever`, `true`, `false` — refines the board/source/project policy (see [Caching](#caching)) |
| `ignore` | Diagnostic codes to suppress (e.g. `["WARN-REAGGREGATION"]`) |

SQL fields: `sql`, `setup_sql`, `target` (dbt target name, defaults to `dev`).
HTTP fields: `url`, `method`, `headers`, `params`, `body`, `json_path`, `limit`.
dbt-model fields: `model`, `columns`.
Inline-values fields: `columns`, `values` (or `rows` for record-shape).
Schema fields: `schema`, `table`, `column`, `fields` (project result rows to exactly these keys, in this order).

Connection source types (named in the project root `dbt_charts.yml`'s `sources:`
registry — never inline in a board): `postgres`, `snowflake`, `bigquery`,
`redshift`, `trino`, `duckdb`, `sqlite`, `csv`, `json`, `parquet`, `http`, `dbt_profile`.
CSV/JSON/Parquet may instead be referenced inline as a `source:` file path
without a registry entry — see the example above.

### Jinja variable injection

Queries are Jinja-rendered before execution. Reference variables as bare names — `{{ region }}` — not with a `variables.` namespace prefix.

```yaml
variables:
  region: { options: { static: [US, EU, APAC] } }
  period: { input: daterange }

queries:
  sales:
    sql: |
      SELECT month, revenue
      FROM orders
      WHERE {{ filter('region', region) }}
        AND {{ filter_date_range('month', period) }}
```

For multiline SQL, always use block scalar (`sql: |`). Never use `"SELECT\n…"` or `'SELECT\n…'` — escaped newlines make diffs unreadable and confuse agents learning from examples. Single-line SQL in double quotes is fine: `sql: "SELECT 1 AS n"`.

`filter()` and `filter_date_range()` are helper macros that emit safe SQL predicates for select/multiselect and daterange variables respectively.

**Common multiselect mistake** — `tojson` produces double-quoted strings; most SQL dialects (including DuckDB) treat `"trial"` as a *column reference*, not a string literal. The query silently returns an empty result and `dct render` exits 0.

```sql
-- WRONG: produces WHERE plan IN ("trial", "pro") — treated as column refs, not strings
WHERE plan IN ({{ plans | map('tojson') | join(', ') }})
```

Use the `filter()` macro instead — it emits correctly quoted predicates:

```sql
-- CORRECT
WHERE {{ filter('plan', plans) }}
```

The `filter()` macro handles `select` (single value → `=`) and `multiselect` (list → `IN (...)`) automatically and quotes all string literals correctly.

Select and multiselect controls render only the options the board author provides. dbt charts does not add an "All" option; author a real sentinel option and matching SQL/Jinja explicitly if a dashboard needs one.

### Inline query in a chart

A chart can carry its own one-off query instead of referencing a named one. Three equivalent forms:

```yaml
charts:
  rev_chart:
    # Shorthand — bare SQL string (simplest form)
    query: "SELECT month, revenue FROM orders"
    type: bar
    x: month
    y: revenue

  rev_chart2:
    # Explicit inline dict
    query:
      sql: "SELECT month, revenue FROM orders"
    type: bar
    x: month
    y: revenue
```

The bare-string shorthand works whenever the value contains a SQL keyword (`SELECT`, `WITH`, `INSERT`, etc.) and is not already a named query. Use `query: {sql: ...}` when you need additional query options (`source:`, `notes:`, etc.).

Inline queries are not reusable. Prefer named queries when more than one chart consumes the data.

**See also:** the Variables section (use `{{ var }}` in SQL),
the Charts section (charts reference queries by name),
the Errors section (query-execution error codes).

## Charts

Each chart binds a query to a chart type and an encoding. Unknown chart fields are rejected.

A request for "a chart" still produces a board file: write the YAML to
`charts/<name>.yml`, validate, and render it. A chart printed inline (or to
the terminal) is not a deliverable — the board file is.

```yaml
charts:
  revenue_trend:
    query: revenue            # Name of a query (or inline query def)
    type: line                # See chart types below
    title: "Revenue"
    subtitle: "Last 30 days"
    notes: "AI/tooltip metadata about what this chart answers."

    # Data mapping (the channels)
    x: month                  # column name
    y: total                  # column name OR [col, col, ...] for multi-series
    color: segment            # a bare column name; nothing else is accepted here
                              # literal color -> style.color.static; scale -> style.color.gradient

    # Sizing — height lives at chart root; aspect_ratio is a style field
    height: 400               # exact px; bypasses aspect_ratio and min/max clamps
    # height/aspect_ratio are ignored on kpi, table, callout, spark_bar

    # Style + behavior
    sort: { by: total, order: desc }
    x_label: "Month"
    y_label: "Revenue (USD)"
    link: "/orders?month={{ month }}"      # Click-through URL template (drill-down)

    style:                    # Chart-local style patch (typed; not raw CSS) — paint only
      aspect_ratio: 2.0       # shape without a fixed size; height = width / aspect_ratio
      # bar/area families also accept style.orientation and style.stack
```

### Chart types (17 authorable)

Set `type:` to one of:

**Basic** (one mark per chart) -- `bar`, `line`, `area`, `scatter`, `pie`, `donut`, `kpi`, `table`.

**Statistical** -- `histogram`, `heatmap`.

**Geographic** -- `geoshape`, `map`, `point_map`, `bubble_map`.

**Overlays** -- `bar`, `line`, `area`, and `scatter` accept a `layers:` field for mixed-mark or dual-axis charts (see [Combo charts](#combo-charts-barlinearea-with-layers) and [Composition](#composition)).

**Sparklines** -- `spark_bar` (compact horizontal bars used in profiler cards).

**Auxiliary** -- `callout` (message card with a `style.tone:` field; `message:` required).

**Custom** -- `custom` draws a shape the types above lack from an SVG template the engine renders (`template:` names `templates/<name>.svg.j2`, or `svg:` carries the source inline; channels such as `x:`/`y:` sit at the chart root and `options:` carries the template's other settings, both declared in its header). **A last resort:** use it only when the user asks for a specific shape by name or description that no built-in type or composed recipe draws; otherwise use the built-in types. See [Custom charts](#custom-charts-type-custom).

Note: `donut` is an internal alias for `pie` -- `donut` is accepted but normalizes to `pie` internally. `auto` is an internal sentinel (not authored). `boxplot`, `errorbar`, and `errorband` are Vega-Lite mark types that are not in the authorable surface.

Type aliases: `scatter` uses a circle mark, `heatmap` uses rect, `pie`/`donut` use arc, `histogram` uses bar with binning, `map` maps to geoshape.

### Named shapes that have no `type:`

Many chart shapes people ask for by name are compositions of the types above, not
types of their own. There is no `type: bullet` — writing one is an error. Author the
recipe instead. Spelling is tolerant: `bullet`, `bullet chart` and `bullet graph` all
resolve to the same recipe, and the same error names the recipe if you guess a `type:`.

| Shape | How to author it |
|---|---|
| streamgraph, stream chart | type: area with color: and style.stack: center |
| stacked area | type: area with color: and style.stack: zero |
| stacked bar | type: bar with color: and style.stack: zero |
| grouped bar, clustered bar | type: bar with color: and style.stack: none |
| horizontal bar, row chart | type: bar with style.orientation: horizontal |
| column, vertical bar | type: bar with style.orientation: vertical |
| 100% stacked | type: bar or type: area with color: and style.stack: normalize |
| percent stacked bar, normalized bar | type: bar with color: and style.stack: normalize |
| small multiples, trellis, faceted | multiples.rows: <column> (or multiples.columns:) on a cartesian chart |
| dual axis, combo, bar and line | layers: on a cartesian chart, with axis_y.position: right on the added layer for its own y-axis. Warns `WARN-DUAL-AXIS-COMPETING-SCALES` (independent scales mislead); prefer stacked charts, or indexing both series to a common start in the query |
| stacked column | type: bar with style.orientation: vertical, color:, and style.stack: zero |
| grouped column, clustered column | type: bar with style.orientation: vertical, color:, and style.stack: none |
| lollipop | type: bar with style.marks.bar.band_width thinned to a stem, plus a layers: scatter on the same y (style.marks.bar.size is a separate fixed-pixel mode and does not thin the bar) |
| bullet | type: bar with style.stack: zero, style.stack_order: data and color: on the qualitative range column — one row per (category, band) — plus a layers: bar on the value and a layers: line on the target, both reading their own one-row-per-category source via layers[].query (sharing the ranges' rows multiplies each by the band count). Thin the value bar with style.marks.bar.band_width so the ranges stay visible, give the target style.marks.line.curve: step with connect: false for the goal tick, and set style.orientation: vertical |
| slope | type: line with a two-category x and color: on the series column (year-shaped x values resolve to a continuous temporal scale, which fills in the span between the pair) |
| bump | type: line on a rank column with color: on the series column (the data is entity x period x rank, so without it the rows collide on the period key) and a descending style.axis_y.scale.continuous.domain (e.g. [6, 1]) so rank 1 is on top |
| dot plot, cleveland dot plot | type: scatter with a categorical x and a measure y; for several dots per category use long-format rows with color: on the series column rather than layers:, which keeps the shape rotatable |
| dumbbell, barbell, connected dot plot | type: bar with y_start for the far end, thinned to a stem with style.marks.bar.band_width, plus a layers: scatter at each end (color: from style.color.categorical.palette) |
| ranged dot | type: bar with y_start for the low end, drawn as a pale interval (color: from style.color.categorical.palette), plus a layers: scatter on the point estimate |
| floating bar, range bar | type: bar with y_start, so the bar runs from y_start to y instead of from zero |
| waterfall, bridge chart | type: bar with y_start from a running total the query computes, color: on the query's own direction column pinned with board category_colors, and style.overlap: full so each step's bar fills its band instead of splitting into a grouped slot |
| candlestick, ohlc, stock chart | type: bar with y_start: low and y: high as a thin wick, plus a layers: bar with y_start: open and y: close as the body. Same-color convention: color: on the query's session column on both the base bar and the body layer, with style.marks.bar.border.width: 0 to drop the knockout outline and border.radius: 0 for square candles. Ink-outline convention: no color: on the base bar (an ink palette slot), color: on the body layer alone, and style.marks.bar.border in ink |
| gantt | type: bar with a date y and a date y_start, style.orientation: horizontal so time runs left to right, keeping the query's row order rather than the usual value-descending default |

Shapes with no built-in type — naming them is the half a recipe cannot cover. When the user asks for one of these by name, draw it with `type: custom`; never substitute one for a built-in chart the user did not ask to replace:
`alluvial`, `funnel`, `gauge`, `marimekko`, `radar`, `sankey`, `spider`, `violin`.

`chord`, `network`, `sunburst`, `treemap` and `word cloud` are not supported: no built-in type draws them, and a custom template cannot compute their layouts. Say so instead of approximating one.

### Shared chart fields

All chart types accept the channels and style fields below — but each type rejects fields that don't belong to it (e.g. `theta` on a bar chart, `x` on a pie chart).

| Field | Type | Description |
|-------|------|-------------|
| `x` | string | X-axis field |
| `y` | string \| list[string] | Y-axis field(s) — list for multi-series |
| `color` | string \| object | Field name, `{value}`, `{field, scale}`, or `{field, when}` |
| `size` | string | Field for size encoding |
| `shape` | string | Field for shape encoding |
| `opacity` | string \| object | Field name or `{field, scale}` |
| `stroke` | object | `{color, width}` — each accepts field/scale/when |
| `theta` | string | Angular field (pie/donut/arc) |
| `style.inner_radius` | float 0–1 | Donut hole ratio (hole/outer disk; pie/donut only); `type: donut` sets it to `0.6` automatically |
| `total` | object | `{label}` — center total for donut; format lives at `style.total.value.format` |
| `style.marks.slice.labels` | object | `{template, where}` — per-row Jinja annotations near slice callouts |
| `x_label` | string | X-axis title override |
| `y_label` | string | Y-axis title override |
| `message` | string | Static text for `type: callout` |
| `geo` | string \| object | GeoJSON field or inline spec (geoshape) |
| `geo_source` | string | Named geographic data source |
| `lookup` | string | Field that joins to the geographic source key |
| `value` | string | KPI: column reference (string column name); map: data field for fill color |
| `projection` | string \| object | Projection name (e.g. `mercator`, `albersUsa`) or VL projection config |
| `latitude` | string | Latitude field (point/bubble map) |
| `longitude` | string | Longitude field (point/bubble map) |
| `background` | string \| object | Background channel — color, `{value}`, `{field, scale/when}`, or map layer |
| `sort` | object | `{by, order}` — categorical sort. Horizontal bar charts default to value-descending order when omitted. |
| `link` | string \| false | Click-through URL template for drill-down links; `false` suppresses the chart's automatic link when the board sets `links.auto: true` (table column links are unaffected) |
| `multiples` | object | Partition into small multiples: `{rows, columns, scale}` — cartesian charts (`bar`/`line`/`area`/`scatter`/`heatmap`) only (see [Small multiples](#small-multiples-multiples)) |
| `warnings_ignore` | list[string] | Render-warning codes to suppress for this chart only (e.g. `[WARN-AXIS-TITLE-TRUNCATED]`; unknown codes are rejected — every code is listed at https://docs.dbtcharts.com/reference/warnings/) |
| `layers` | list | Overlay layers on cartesian charts (`bar`/`line`/`area`/`scatter`) — see [Combo charts](#combo-charts-barlinearea-with-layers) |
| `conditional_formatting` | object | Discrete style rules by column — `table`/`kpi` only (see [Conditional formatting](#conditional-formatting)) |
| `support_table` | list | Attached mini-table on bar/line/area charts, above the plot by default (including those with `layers:`) — see [Composition](#composition) |
| `spanners` | list | Flat `table` only: `[{label, columns: [...]}]` — a label centered over a run of adjacent columns in an extra header row. Columns must be adjacent in query order and in at most one spanner; not combinable with `rows`/`columns`/`values` pivot channels |
| `height` | int \| float | Exact pixel height. Wins over `aspect_ratio` and theme defaults. Bypasses `min_height`/`max_height`. Not valid on `kpi`, `table`, `callout`, `spark_bar`, `custom`. |
| `style.aspect_ratio` | float | Chart shape: `height = width / aspect_ratio`. Theme default is `1.5`. Not valid on `kpi`, `table`, `callout`, `spark_bar`. |
| `style.min_height` | float | Height floor for this chart only; overrides `style.charts.min_height`. Ignored when `height` is set. |
| `style.max_height` | float | Height ceiling for this chart only; overrides `style.charts.max_height`. Ignored when `height` is set. |

`height` lives at **chart root** and is rejected under `style:`; `aspect_ratio`, `min_height` and `max_height` live under `style:` and are rejected at chart root.

KPI-only fields: `value`, `label`, `support`. KPI uses `label:` for the header text — `title:` is rejected on KPI charts. Chart-root `format:` / `formatter:` is rejected on all chart types. Use the family slot instead.

`axis_x`/`axis_y` name the channel, not the visual edge: on every cartesian family the measure is `axis_y`, including a horizontal bar that draws it along the bottom. A number preset belongs on the measure.

Put a number preset on an axis whose ticks can't carry it and the engine raises `ERR-LABEL-FORMAT-AXIS-MISMATCH` — on `axis_x` or `axis_y`, the mirror ghost (`axis_y.mirror.format`), or heatmap's y, whichever axis the format actually lives on. Two cases raise:

- The axis resolves to a **band** scale (nominal/ordinal) and its ticks are not already readable as numbers. Numeric categories (`stage_id: 1, 2, 3`), numeric strings and booleans format cleanly and stay legal — a bar over `stage_id` paints `$1 $2 $3` and never raises.
- The axis resolves to a **temporal** scale at all — dates get no numeric-tick exemption, since there's no reading of `$,.0f` over a date the author wanted.

| Shape | Dimension scale |
|---|---|
| `bar` over a category | band |
| `bar` over `yearweek` / `yearmonthdate` buckets | band, always |
| `bar` over `year` / `yearquarter` / `yearmonth` buckets | band, up to 60 distinct buckets; temporal at 61+ |
| `line` / `area` / `scatter` over a text category | band |
| `line` / `area` / `scatter` over bucketed dates | temporal |
| `line` / `area` with `curve: step` over bucketed dates | band — the step plateau needs a band scale |
| `heatmap` x/y | band, always nominal |
| `scatter` over a genuinely numeric x, or its usual numeric y | quantitative — applies normally |
| `scatter` over a categorical y (a dot plot) | band |

A temporal x — `line`/`area`/`scatter` over dates, or a bar past 60 buckets — raises the same code; use a time token (`"%b %Y"`) there instead. A `heatmap` has no measure axis at all: both axes are grid dimensions and the value lives on the color channel, which carries no label format of its own, so a number preset on either axis — including its `y` — raises too. `style.axis_y.mirror.format` follows the same rule whenever the mirrored edge is categorical — a dot plot's y, and also a default-orientation (horizontal) bar, where the rotation puts the category on that edge. Every one of these used to render something wrong instead — look at the render only if you're debugging the diagnostic itself.

The family slots:

| Family | Format slot |
|--------|-------------|
| `line`, `bar`, `area`, `scatter` — the measure | `style.axis_y.labels.format`, or `formats:` on the query to format the column everywhere |
| same — the dimension, when it is a date | reads by its grain with no key; a time token on `style.axis_x.labels.format` (e.g. `"%b %Y"`) shows another form |
| same — the dimension, when it is a text category | no format applies |
| same — the dimension, when its ticks are numbers | `style.axis_x.labels.format` — `axis_y` never reaches the dimension axis |
| `heatmap` | no axis format applies — the value is on the color channel |
| `kpi` value | `style.value.format` |
| `kpi` support | `style.support.format` |
| `table` column | `style.columns.<col>.format` |

#### Number format aliases

`style.axis_y.labels.format`, `style.value.format`, table column `format`, and
`style.support.format` accept a D3 format string, a named alias (engine-owned
specs shown), or an object with `spec`, `prefix`, `suffix` and `notation`
keys (see "Currency and other affixes" below). The three `percent_number*` /
`percentage_points_delta` aliases are native and Python-formatted: valid only
in KPI value/support and table-cell `format` slots; an axis format (a
Vega-painted slot) rejects them with `ERR-FORMAT-NATIVE-IN-VEGA-SLOT`.
`duration` and `bytes` are native too but also carry a Vega form, so they paint
on axes, value labels and tooltips as well as in a KPI or table:

| Alias | Spec | Renders like |
|-------|------|--------------|
| `integer` | `,.0f` | `12,346` |
| `number` | `.3~s` | Register depends on the slot: table cells render analytic `12.4K`; KPI headline values and mark value labels render narrative `12.4k`; axis ticks compact only when the largest tick has ≥6 digits (below that the engine paints plain digits — `20,000`, no suffix). Also the engine fallback when no format is authored |
| `number_full` | `,.2f` | `12,345.68` — every digit at every magnitude |
| `currency` | `$.3~s` | `12400` → `$12.4K` (analytic default; narrative slots render `$12.4k`) |
| `currency_whole` | `$,.0f` | `$12,346` |
| `currency_full` | `$,.2f` | `$12,345.68` — cents at every magnitude |
| `compact` | `.2~s` | `12K`, `1.2M`, `1.2B` — two significant digits with the K/M/B suffix in every slot; below 1 it falls back to `number_full` |
| `currency_compact` | `$.2~s` | `$12K`, `$1.2M` — `compact` with the currency symbol; below 1 it falls back to `currency_full` |
| `percent` | `.1%` | `0.42` → `42.0%` — **multiplies by 100**; for a value that already IS a whole-number percent (`42`, `148.23`), use `percent_number` |
| `percent_whole` | `.0%` | `0.42` → `42%` |
| `percent_delta` | `+.1%` | `0.018` → `+1.8%` (ratio in, signed percent out) |
| `percent_number` | native | `148.23` → `148.2%` (no multiplication; one decimal). Native — not valid in an axis format |
| `percent_number_delta` | native | `1.8` → `+1.8%`. Native — not valid in an axis format |
| `percentage_points_delta` | native | `1.8` → `+1.8 pts`. **Input is whole points, not a fraction**; a fraction delta (`0.018`) renders `+0.0 pts`, so multiply by 100 in the query. Native — not valid in an axis format |
| `delta` | `+,d` | `+1,234` |
| `year` | `d` | `2026` |
| `id` | `d` | `1204331` — digits, no separator, never compacted; a text cell (`A-1204331`) passes through unchanged |
| `duration` | native | seconds in → two largest units: `5025` → `1h 23m`, `45` → `45s`, `273600` → `3d 4h` (a zero lower unit is dropped: `3600` → `1h`) |
| `bytes` | native | bytes in → decimal SI with a spaced unit at every magnitude: `1534000` → `1.53 MB`, `950` → `950 B`, `1000` → `1 kB` |
| `latitude` | `.4~f` + `°` | `37.7749` → `37.7749°` (signed degrees; a whole degree prints `−33°`) |
| `longitude` | `.4~f` + `°` | `-122.41942` → `−122.4194°` |
| `rating` | `.1f` | `4.5` → `4.5` |

#### Currency and other affixes

`regional:` (board root, cascaded by `charts/meta.yml`, not by themes) sets how a board writes numbers, and the board's one currency: every money format and every `$` spec prints it. `locale.number` is a d3-format locale name (`fr-FR`, `de-CH`, ...), default `en-US`, or a full d3 locale definition (`decimal`, `thousands`, `grouping`, `currency: [prefix, suffix]`, optional `percent`, `minus`, and `analytic`/`narrative`: the extra-short and abbreviated thousand, million, billion, trillion suffixes, printed as written, default en-US's K/M/B/T and `["k", "mn", "bn", "trn"]`): separators, signs, magnitude suffixes (a named locale brings its own, `de-DE`: `M` analytic, `Mio.` narrative), and the currency template every `$` spec prints, the money names (`currency`, `currency_whole`, `currency_full`) included. `locale.time` is a d3-time-format locale name (`de-DE`, `fr-FR`, `ja-JP`, ...), default `en-US`, or a full definition (`date_time`, `date`, `time`, `periods`, `days`, `short_days`, `months`, `short_months`, `week_start`): it names every month, weekday and AM/PM on the board (`de-DE`: `Mrz`) and sets the first day of a date picker's week (`de-DE`: Monday). The two locales are independent, so set both. `currency` is an ISO 4217 code (USD, EUR, GBP, JPY, CAD, AUD, CHF, SEK, NOK, DKK, INR, CNY, BRL, MXN) whose symbol replaces the locale's own at the locale's placement (`de-DE` + `USD`: `1.234,50 $`); unset, the locale's own currency prints. Repeated values keep the locale's side; a value that declares the currency once (anchored table column, support_table row, axis tick) leads with it (`€1,2 M`). `JPY` has no decimals. `language` (en, de, fr, es, it, pt, nl, sv, da, nb, fi, pl, cs, ru, uk, tr, ja, zh, ko) picks the words dbt Charts generates; unset, it is the language of `locale.time` (`de-DE` is `de`), or `en` when that locale has no catalog or is a full definition. A nested board takes its root's `regional:` (`ERR-REGIONAL-NESTED-MISMATCH` otherwise).

```yaml
regional:
  locale:
    number: de-DE   # 1.234,50 €
    time: de-DE     # Mrz, Okt
  currency: USD     # only to change the locale's own currency: 1.234,50 $
  language: de      # generated words; default: the time locale's language
```

Any format slot above also accepts an object instead of a bare string, for
what `regional` cannot express: a second currency on the board (define it once
as a `style.formats` alias), a long-form name (`50 US dollars`), or a
combination like `50 $ US`. Its spec carries no `$`, which would print the
board's currency:

```yaml
style:
  axis_y:
    labels:
      format:
        spec: ",.0f"     # any spec or alias target above; omit to use the engine default
        prefix: "US$"    # leads the digits, after the sign
        suffix: " net"   # trails the digits
        notation: narrative  # or analytic; SI (~s) specs only, otherwise a no-op
```

The sign leads: a prefix reads `−US$500` (sign, then prefix, then digits); a
suffix trails the digits unaffected: `−500 net`. A table column keeps the sign
against the digits (`US$−500`) because its prefix lane is shared down the column.
`style.formats` aliases work the same way: an entry can name an object with
the same keys instead of a bare spec string, and every slot above resolves it
identically to writing the object inline.

A board restyles an engine format everywhere by redefining its name in
`style.formats` (`currency: "$,.0f"`, `number: ",.0f"`, `date_short: "%b %Y"`).
Slots with no format of their own read it; a query's `formats:` and a slot's
own `format:` still win. A redefined name prints exactly its own spec at every
magnitude: no compaction, no trim, no switch to `currency_full` / `number_full`
below 1. It must stay the same kind (`ERR-FORMAT-KIND-MISMATCH`).
Pairing a prefix with a spec that already carries its own native `$`/`#`
composes around it (a `$,.0f` spec with prefix `"US "` paints `US $154,500`),
including a spec that is both SI-shaped and native-symbol-bearing (the
`currency` preset, `$.3~s`, plus prefix `"US "` paints `US $12.4M`).

An authored `labels.expr` on an axis always wins over `labels.format`
outright, object or string; it renders exactly as written, with no affix or
notation composed in. Write the symbol into the expression itself (e.g.
`"'EUR ' + datum.label"`) if you need one there.

A date axis's `labels.format` takes D3 time specs or `date_short` (`%-d %b %Y` → `5 Mar 2026`). A date already reads by its grain, so set one only to show another form.

A date axis labels by the grain it detects, and each grain has an engine name:
`date_year`, `date_quarter`, `date_month`, `date_week` (days keep `date_short`).
An engine grain name means the engine's own labels for that grain, so a slot
naming one the board has not redefined raises
`ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED`; redefine the name in `style.formats` with
a strftime spec to relabel that grain on every axis and tooltip header of the
board (`date_month: "%b '%y"` reads `Jan '26`). An axis's own
`labels.format` still wins.

KPI content sits at the chart root (`value`, `label`, `glyph`, `support`); everything about how it looks sits under `style:`. `tone` at chart root is rejected: it lives only on `style.support.tone`, since the support row is the block it paints (the headline value stays neutral). Each slot paints with its own `font` (`style.value.font`, `style.value.affix.font`, `style.value.glyph.font`, `style.label.font`, `style.support.font`, `style.support.glyph.font`); `style.font` is the font they all start from. KPI fonts take no `line_height`: every slot is one line.

Top-level chart fields shared by all types: `id`, `query`, `type`, `title`, `subtitle`, `notes`, `height`, `aspect_ratio`, `style`, `link`, `warnings_ignore`.

### Chart-type cheatsheet

Each block below shows the minimum-viable shape for one chart type. They are stacked for compactness; in a board, each chart sits under its own key in `charts:`.

```yaml-schema
# bar — categorical x, numeric y
type: bar
x: category
y: value
y_start: floor          # Column the bar starts from; absent means zero. Same kind as y. Not with stacking.
color: group           # Optional second dimension; grouped side by side by default
style:
  orientation: vertical  # vertical = column chart; horizontal = horizontal bar chart.
                          # Auto-resolves from x's column type when omitted: categorical x → horizontal.
  stack: zero            # see the stack table under "area" — bar takes the same values

# line — temporal/ordered x, numeric y
type: line
x: date
y: revenue
color: segment         # Optional: one line per segment

# area — same encoding as line; filled below
type: area
x: date
y: value
color: segment         # Required for stacking — stack modes split by this field
style:
  # stack names the shape you get. All four values work on bar and area alike:
  stack: zero          # stacked
  # stack: none        # overlapping — bar groups side by side, area overlays.
                        # The default when color: is set.
  # stack: normalize   # 100% stacked — every column fills the full height
  # stack: center      # streamgraph — stacked around a centered baseline

# scatter — x and y numeric
type: scatter
x: spend
y: revenue
color: region
size: volume           # Optional bubble size

# pie / donut — pre-aggregated; one row per segment
type: pie
theta: revenue
color: segment
style:
  inner_radius: 0.6    # 0 = solid pie; >0 = donut (type: donut sets 0.6 by default)
  marks:
    slice:
      labels:
        template: "{{ segment }}\n{{ revenue | format(',.0f') }}"
  total:
    value:
      format: integer
total:
  label: Total

# kpi — requires exactly 1 row; value: is a column reference
type: kpi
value: total_revenue    # column name (always a column reference)
label: "Total Revenue"  # NOT `title:` — `title:` is rejected on KPI
glyph: "●"              # optional; drawn before the headline value
support:                # Optional support line
  value: prev_revenue
  label: "vs last month"
  glyph: "▲"
style:
  variant: stacked       # stacked (default) | inline (one baseline row) | compact (2-column)
  value:
    format: ",.0f"       # number format; `format:`/`formatter:` at chart root is rejected on KPI
    font:
      color: "#c2410c"   # optional; paints the headline value, its affix and glyph
    glyph:
      font:
        color: "#626366" # optional; paints the headline glyph only
  label:
    overflow: wrap       # clip | truncate | wrap-two | wrap; defaults to the chart title's
    font:
      color: "#2563eb"   # optional; paints the label only
  support:
    format: percent_delta
    tone: positive       # positive | negative | warning | info; colors the support value and glyph
  align: right           # left (default) | center | right — one field for the
                          # whole card; value/label/support move together
# style.font.color is the whole-card fallback under the slot colors above.
# The headline value has no tone field — it stays neutral by design (NYT/FT
# convention). Tone lives on the block it paints: the support row.

# table — always renders every query column, in query order; `style.columns`
# is styling-only (keyed by column name) and `visible: false` is the sole way
# to hide a column
type: table
style:
  column_layout:
    overflow: scroll                     # scroll (default): keep column widths, scroll sideways | fit: squeeze into the slot
    freeze: 1                            # leading visible columns pinned while scrolling (needs overflow: scroll)
  pagination:
    page_rows: 20                        # rows per page, or rows in view with mode: scroll
    mode: pages                          # pages (default): pager below | scroll: vertical scroll, header pinned
  columns:
    order_id:
      visible: false                     # hide; values stay usable in link: templates
    amount:
      label: Amount
      format: currency_whole
      align: right                       # left | center | right
      header_overflow: wrap-two          # clip | truncate | wrap-two (default) | wrap
      header_link: "/columns/amount"
      link: "/orders?id={{ order_id }}"
      background: dbt-grays.1
      font: { color: dbt-grays.11, weight: "600" }
      scale:
        background:
          palette: dbt-seq-blue                # palette name, or an explicit stop list
          domain: data                   # currently only "data"
          min: 0
          max: 1000000
          null_color: dbt-grays.2
          hinge: auto                    # number | "auto"
          arm_mode: asymmetric           # asymmetric | symmetric
      spark:
        type: line                       # line | area | bar | bar-normalize | columns
        color: category[1]
        height: 24
        last_visible: true
      width: 120                         # int (px) or string
      glyph: "!"
      glyph_color: warning.solid

# heatmap — pre-aggregated; one row per (x, y) cell
type: heatmap
x: day_of_week
y: hour
color: event_count

# histogram — pre-bin in SQL OR use Vega-Lite binning behavior
type: histogram
x: amount

# geoshape — choropleth via GeoJSON
type: geoshape
geo_source: us-states
lookup: state
value: revenue

# map — generic choropleth (alias for geoshape with named source)
# world-countries/world-50m join on numeric TopoJSON ids such as 840, not ISO alpha codes.
type: map
geo_source: us-states
lookup: state
value: revenue

# point_map / bubble_map — lat/lng points (optional size for bubble)
type: point_map
latitude: lat
longitude: lng
basemap:                # Optional styled background layer (point_map + bubble_map)
  source: us-states     # named boundary source
  fill: dbt-grays.2
  stroke: dbt-grays.4

type: bubble_map
latitude: lat
longitude: lng
size: events
color: severity

# spark_bar — compact horizontal bars (used inline in profiler cards)
# NOTE: spark_bar inverts the cartesian convention — x is the bar MAGNITUDE
# (must be numeric) and y is the LABEL (the text). This is the opposite of
# bar/line/area, where x is the dimension.
type: spark_bar
x: count
y: category

# custom — rows in, SVG out, drawn by a template the engine renders
type: custom
query: flows
template: sankey          # templates/sankey.svg.j2; or `svg: |` with inline source
x: [from_stage, to_stage] # channels at the root, as declared in the template header
value: visitors
options:
  node_width: 12          # template-specific knobs, validated against the header
style:
  aspect_ratio: 2         # sizing only: aspect_ratio, min_height, max_height

# custom, inline: the template under `svg:`; header optional; without one, read columns off rows
type: custom
query: quota
svg: |
  {% set band = height / (rows | length) %}
  {% for r in rows %}
    <rect y="{{ loop.index0 * band }}" height="{{ band * 0.6 }}"
          width="{{ (r.attainment | float) * width / 1.25 }}"
          fill="{{ style.charts.color.categorical.single_series_palette[0] }}"/>
  {% endfor %}

# Pure marks (advanced use — not accepted as base chart types):
# circle, square, tick, rule, trail, rect, arc, image

# callout — message card with a style.tone: field (info | negative | warning | positive)
type: callout
message: "Query is disabled in this environment."
style:
  tone: warning  # optional; defaults to info
  # background, border.color, title.font.color, message.font.color: each one
  # you set wins; `by_tone` (the default) is the tone's color.

```

### Custom charts (`type: custom`)

Use `type: custom` only when the user asks for a shape by name or description that no built-in type or composed recipe draws; a custom chart gets no axes, legend, sizing rules, or visual warnings for free. A custom chart is drawn by a Jinja-style template that emits SVG content. SQL does the
math (positions, stacking, binning); the template draws. Exactly one of
`template:` (a project file, `templates/<name>.svg.j2`) or `svg:` (inline
source) is required. Write one-off charts inline; an inline template may open with
the same header as a template file; without one it declares no channels or options and reads columns by name (`r.revenue`).
`dct examples charts/custom-inline` is a complete inline board. The platform clips the output to the `width x height`
drawing box and draws `title`/`subtitle` above it.

A template file may open with a Jinja comment holding YAML that declares its
`channels` and `options`:

```jinja
{#---
description: Flow diagram from an edge list.
channels:
  x:
    description: Two columns, source node then target node.
    multiple: true
  value:
    description: Size of each flow.
options:
  node_width:
    type: number
    default: 12
---#}
```

Channels use the built-in channel names (`x`, `y`, `color`, `size`, `shape`,
`theta`, `value`) and are set at the chart root, like any chart; pick the one
that matches the role (a funnel's stage is `x`, its count `y`; a waffle's parts
are `color` and `theta`, as on a pie). `multiple: true` allows a list of columns
and always passes a list. Option types: `column`, `columns`, `string`, `number`,
`integer`, `boolean`, `color`. A channel or option with no `default` is
required; `default: null` makes it optional (value `none`). Undeclared,
missing, or wrongly typed channels and options are compile errors
(`ERR-CUSTOM-FIELDS`); channel and column-option columns are checked against
the result columns at render. A headerless template takes neither.

`dct describe templates/` (or one `templates/<name>.svg.j2`) prints each
template's description, channels (required/optional, `multiple`), and options
(type, default, description); `--json` adds a `template` object to the result.
Run it before writing a custom chart to see which templates the project
already has and what they take. A missing `template:` name lists the available
ones.

Template context: `rows` (list of row dicts, raw values), `columns`, `channels`
(declared channel → column, or list), `options`,
`width`/`height` (px, inside the card padding and below the title), `uid` (id prefix for `id=`/`url(#...)`),
`style` (the merged style tree; `style.palettes.<role>` is a list of hex colors,
or an alias dict for tone roles such as `info`), `math` (`sin`, `cos`, `tan`,
`asin`, `acos`, `atan2`, `sqrt`, `log`, `exp`, `floor`, `ceil`, `pi`, `tau`),
`variables` (the board's variable values), filter `format` (d3-format), and the
helpers `text_width(text, size, weight)`, `color_for(value, column=None)` (board
pin for that column's value, else the categorical palette in first-call order),
`href(row)` (the chart's `link:` with its `{{ channel }}` placeholders filled from
the row; a `link:` with no placeholder wraps the whole drawing instead),
`raise_error(message)`. Names are strict: a misspelling fails the chart. The
engine renders the minijinja dialect: filters, not Python methods (`text | split(",")`,
`d[k] if k in d else 0`, `math.ceil(x)`). Output is autoescaped and validated
against an SVG allowlist (no `script`, `foreignObject`, `style`, `image`,
animation, `on*` attributes, non-`#local` `href`/`url()` outside `<a>`; an `<a href>`
must be a board path or an http, https or mailto URL); a violation fails the
chart (`ERR-CUSTOM-SVG`) rather than being stripped. `dct skills custom-chart-build`
is the authoring walkthrough.

### Small multiples (`multiples`)

Partition one chart into a panel per distinct value of a column — a row stack,
a column strip, or a grid when both are set. At least one of `rows`/`columns`
is required.

```yaml
charts:
  revenue_by_region:
    type: line
    query: monthly_by_region
    x: month
    y: revenue
    multiples:
      columns: region        # one panel per region, side by side
      # rows: product        # and/or: one panel row per product (grid when both set)
      # scale: independent   # default shared keeps panels comparable
```

Panels share one measure scale by default (`scale: shared`) so values are
comparable across panels; `scale: independent` gives each panel its own.

### Combo charts (bar/line/area with layers)

```yaml
type: bar
x: month
y: actual
layers:
  - type: line                      # bar | line | area | scatter
    y: target
    label: Target
    axis_y:
      position: right              # left | right
      title: "Target"
```

The base chart (`type: bar`, `type: line`, or `type: area`) sets the primary mark, `x`, `y`, and `query`. Additional marks go in `layers:`. Each layer accepts: `type`, `y`, `label`, `color` (data channel, bare field name), `query` (overrides the base chart query for this layer), `x` (layer x-values extend the base x-scale), `axis_y`, and `style` (marks-only patch). Vega-Lite `encoding:` is not allowed inside a layer — use the typed channels.

### Conditional formatting

Available on `type: table` and `type: kpi` only.

Discrete, rule-driven style overrides applied per column. Each entry under `conditional_formatting:` is keyed by column name and contains a `when:` list of rules.

```yaml
charts:
  accounts_table:
    type: table
    query: accounts
    conditional_formatting:
      status:
        when:
          - in: [blocked, escalated]      # predicate (exactly one per rule)
            background: negative.bg        # style output (at least one required)
            font:
              color: negative.text
              weight: "600"
          - is_null: true
            glyph: "!"
            glyph_color: warning.solid
          - default: true                  # must be LAST in the list when present
            font: { color: dbt-grays.11 }
```

Predicates (exactly one per rule): `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `between` (`[low, high]`), `in` (non-empty list), `is_null` (bool), `default` (`true` only — terminal fallback).

Style outputs (at least one per rule): `background`, `font` (color/weight/style/decoration), `glyph` (with optional `glyph_color`).

If `default: true` is set, it must be the last rule in the `when` list. Earlier rules win in order.

### Common styling recipes

The asks agents most often fumble, with the exact fields (all verified against
the style models):

```yaml
queries:
  monthly: SELECT month, total, region FROM rev GROUP BY 1, 3

charts:
  styled_bar:
    query: monthly
    type: bar
    x: month
    y: total
    style:
      legend:
        visible: false                # hide the legend
      axis_x:
        labels:
          angle: -45                  # rotate crowded x labels
      axis_y:
        labels:
          format: number              # compacts axis ticks only when the top tick has >=6 digits
        grid:
          visible: false              # hide y gridlines
        scale:
          continuous:
            domain: [0, 20000]        # pin the y range ([low, high]; both bounds)
            # zero: true              # or just force a zero baseline
      marks:
        bar:
          labels:
            visible: true             # value labels on each bar
            position: above           # above | top | middle | middle_aligned | bottom
            format: number

rows:
  - styled_bar
```

Color recipes (see [Color](#color) for the token system):

- **One series in a specific hue:** `style.color.static: dbt-seq-amber.4` — a
  pinned stop from a sequential ramp (`static` takes one color, never a
  palette name).
- **Continuous color by a field** (heatmap cells, choropleth values, tables):
  `style.color.gradient: {palette: dbt-seq-amber}` — `palette:` takes a ramp
  name or an explicit stop list.
- **Categorical series colors:** `style.color.categorical.palette` (list of
  stops or a palette name); `style.color.categorical.single_series_palette`
  sets the ink list used when the chart ends up with a single series.
- Warm sequential ramp: `dbt-seq-amber`; diverging: `dbt-div-orange-teal`,
  `dbt-div-blue-red`.

**Orientation gotcha:** `x` is always the categorical axis and `y` always the
measure on bar charts — *regardless of orientation*. On a horizontal bar,
zero-baseline and range pins still belong on `axis_y` (the measure), never
`axis_x`; `style.axis_quantitative` also targets the measure axis whichever
side it renders on.

Why: `x` and `y` name roles, not screen directions. They are algebra's
unknowns. In `y = f(x)`, `x` is the input (the independent variable: the
category, the time, the thing you slice by) and `y` is the output (the
dependent variable: the value). Drawing `x` horizontally is only a plotting
convention; the words that do mean position are *abscissa* (horizontal) and
*ordinate* (vertical). The belief that `x` means "horizontal" comes from
charting tools. Vega-Lite and the Grammar of Graphics treat `x`/`y` as screen
positions, which is why rotating a bar there means swapping the fields. dbt
Charts does not: pick `x` and `y` by role, then set `style.orientation` for
direction. One exception remains: a horizontal scatter dot plot is still
written `x: value`, `y: category`.

**Explicit category order:** `sort:` takes only `{by: <column>, order:
asc|desc}` — there is no explicit value-order list. A fixed stage order
(sent → viewed → signed) belongs in the query (`ORDER BY CASE …`), because
ordering is dataset meaning and the query layer owns it.

**Sort aggregate:** a category with a `color:` series or a `y: [...]` list
holds several rows, so the sort column is folded to one value per category
first. A *stacked* bar sorted by its own measure ranks by the stacked total;
every other sort ranks by the column's own value, so an ordering key
(`release_seq`) ranks by that key no matter how many rows a category holds.
A grouped (`stack: none`) bar sorted by its measure therefore ranks by each
category's smallest series, not by the group total — pre-sum in the query
when you want the total.

### Composition

dbt charts composes charts in three ways:

1. **`layers:` on a base chart** — multiple marks share one x-axis and frame. The base chart (`type: bar`, `type: line`, `type: area`, or `type: scatter`) owns the x-axis, frame, title, legend container, and `sort`. Each layer defaults to the base `query:` but may declare its own `query:` — layer x-values extend the base x-scale rather than clip it. See [Combo charts](#combo-charts-barlinearea-with-layers) above.

2. **`support_table:` attached to a chart** — a mini cross-tab strip whose columns align to the chart's x-axis ticks, rendered *above* the plot unless `style.support_table.position` says otherwise. Supported on `bar`, `line`, and `area` charts (including those with `layers:`).

   ```yaml
   charts:
     revenue_trend:
       type: line
       query: monthly
       x: month
       y: revenue
       color: region              # `per_series:` below needs a color: channel
       support_table:
         - source: revenue          # Read raw per-x value
           label: "Revenue"
           format: integer
         - aggregate: sum            # Per-x aggregate
           source: orders
           format: integer
           label: "Orders"
         - aggregate: avg
           source: order_value
         - per_series: revenue       # One row per color: series (requires `color:` on the chart)
   ```

   Each entry is one of three shapes (discriminated by which key is present):
   - `source:` — read the raw per-x value (no aggregation).
   - `aggregate:` + `source:` — apply an aggregate per x-group (`sum`, `avg`, `min`, `max`, `median`, `count`, `count_distinct` — exact names only, no aliases).
   - `per_series:` — expand into one row per `color:` series (requires the chart to have a `color:` channel).

   Optional per-entry fields: `format` (D3 format string), `label` (left-stub row label; on `per_series:` only with `by_measure: true`, or as the column header of a horizontal grouped bar).

   `style.support_table.position` places the strip: `top` (the default on a horizontal category axis — vertical bar, line, area) or `bottom`; on a horizontal bar the categories run down the y-axis, so the strip becomes a column block on `left`/`right` and defaults to the side the category labels are on. Two things to know about `top`:
   - Rows read bottom-up — the first authored entry is the row nearest the plot, the last one sits at the top of the strip.
   - Only `style.support_table.padding_top` separates the strip from what is above it, so a two-line subtitle touches the first row until you raise that padding.

   Constraint: support_table requires a single chart-level `x:`. Layered charts with per-layer `x:` differing from the chart-level x are rejected.

3. **Layout composition** — the `rows`, `cols`, `grid`, `tabs` structure in [Layout](#layout). Layout composes charts into a board; chart-level composition belongs to `layers:` (overlays) and `support_table:`.

Non-goals (not part of the authored chart surface): Vega-Lite `encoding`, `mark`, `spec`, `config`, `transform`, `params`, `resolve`, `hconcat`, `vconcat`, `concat`, `repeat`. These keys are rejected at compile time. Use the typed channels (`x`, `y`, `color`, …) and `style:` instead; use the layout primitives for visual composition.

**See also:** the Queries section (charts reference queries by name),
the Variables section (use `{{ var }}` in chart queries),
the Layout section (compose charts on the page),
the Cheatsheet section (one-page essentials).

### Category colors (board-wide)

A data value takes one palette **slot** board-wide, not a hex, so a category
keeps the same color on every chart that draws it (and the same direct-label
ink, table swatch, and nested-board coloring). A field binds automatically
once two or more charts use it as their `color:` channel, including a chart
with `layers:` (its base `color:` binds; layers keep their own series
colors). Author a pin to bind a field on a single chart too, or to choose the
slot:

```yaml
style:
  charts:
    category_colors:
      category:
        values:
          Electronics: category[1]      # a series slot — re-skins with the theme
          Accessories: dbt-grays.8  # chrome — text, grid, borders
          Tools: negative.solid         # good / bad / attention
```

Pin keys are the exact data values from your query results, case-sensitive —
not a display label. A literal hex is accepted too. A pin naming a value no
chart on the render draws is skipped with `WARN-CATEGORY-COLOR-PIN-UNSEEN`
rather than raising; two pins resolving to the same color is
`ERR-CATEGORY-COLOR-PIN-DUPLICATE`; an authored field with more unpinned
values plus palette-color pins than the palette has swatches raises
`ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED`; pins to a variant or hex need no swatch
(an unauthored field just declines to bind).

## Color

Colors are palette tokens, not hex — the theme resolves a token, so a board restyles itself on a theme switch.

| Want | Write |
|---|---|
| A series slot | `category[1]`, `category.blue`, `category[2].dark` (any categorical palette derives dark/light/pale/deep live, no companion file) |
| The theme's plain single-series fill (the color a lone-series bar/line gets with no `color:` channel) | `single_series[1]` |
| Good / bad / attention | `positive.solid`, `negative.solid`, `warning.solid`, `info.solid` — also `.bg`, `.subtle`, `.border`, `.text` |
| Chrome — text, grid, borders | `dbt-grays.11`, `dbt-grays.8`, `dbt-grays.4`, `dbt-grays.1` |
| A ramp for a continuous scale | `dbt-seq-blue`, `dbt-div-blue-red`; `:N` stops and `_r` reversed — `dbt-seq-blue:5_r` |
| A pin that must survive a theme switch | `vivid-10.1`, `dbt-seq-blue.3` |

Indices are 1-based, and one scope takes less than the table above implies:
`palette:` wants a palette name or a list of stops, never a single scalar hex.
Everywhere else — including `conditional_formatting` — a hex literal is
accepted too, the right choice only for a brand color that must not move.

```yaml
style:
  color:
    static: category[2]       # a literal color lives here, not at chart root
```

## Variables

Variables are the interactive filter layer. Each variable has an `input:` widget type, inferred from its options or default unless you name one, plus optional defaults / options.

```yaml
variables:
  region:
    notes: "Restrict every query to one region."
    options:
      static: [US, EU, APAC]
    default: US
```

Input types (14 total):

| Input | Widget |
|-------|--------|
| `auto` | Auto-detect from `options` shape (the default) |
| `select` | Single-value dropdown |
| `multiselect` | Multi-value dropdown |
| `input` | Plain text input (alias for `text` in some surfaces) |
| `text` | Free-text input |
| `textarea` | Multi-line text input |
| `number` | Numeric input |
| `slider` | Single-handle numeric slider |
| `range` | Two-handle numeric slider (returns `[low, high]`) |
| `date` | Single date picker (also `datepicker`) |
| `datepicker` | Single date picker |
| `daterange` | Date range picker (returns `[start, end]`; takes dates or a preset like `last_90_days`) |
| `checkbox` | Boolean toggle |
| `radio` | Radio group |

Common variable fields:

| Field | Type | Description |
|-------|------|-------------|
| `input` | enum | One of the input types above |
| `label` | string | UI label |
| `notes` | string | Help text for this input, carried to the host rather than drawn on the board |
| `default` | any | Value the variable takes when neither a URL param nor `--var` supplies one |
| `placeholder` | string | Placeholder text |
| `required` | bool | Block rendering until a value exists |
| `allow_null` | bool | `null` is a valid selection |
| `visible` | bool | Hidden when `false`; still settable via URL param |
| `disabled` | bool \| string \| `{query, column}` | Static, Jinja expr, or query-backed disable |
| `data_type` | string | Upstream type hint (informational; preserved through migrations) |

Slider / range fields: `min`, `max`, `step`.

Filter-generation field: `operator` — SQL operator used when generating predicates (e.g. `=`, `IN`, `LIKE`).

Options sources (for `select`, `multiselect`, `radio`):

```yaml
variables:
  product:
    input: select
    options:
      static: [All, Electronics, Clothing]    # Hardcoded list
      # OR
      query: products_list                     # Query whose first column is the option list
      column: product_name                     # Optional: which column in that query
      label_column: product_label              # Optional: separate label column

  region:
    input: select
    column: orders.region                      # Auto-populate from a database column
```

Top-level option-source binding (alternative to `options:`): `column`, `query`.

Top-level `column` is `table.column`, and the table may be schema-qualified when it is not in the connection's default schema — `column: gis.fact_sales.property_type`. `filter()` accepts the same qualified form.

Enabled/disabled forms (`enabled: false` means the control is inactive):

```yaml
variables:
  # Static bool — enabled: false disables the control
  closed: { input: checkbox, enabled: false }

  # Jinja expression against current variable values
  q4_only: { options: { static: [Q4] }, enabled: "{{ year >= 2024 }}" }

  # Query-backed (must return exactly 1 row with the named boolean column)
  territory:
    input: select
    options: { query: territory_options }
    enabled:
      query: control_state
      column: territory_enabled
```

**Multiselect SQL antipattern** — the first instinct for filtering a multiselect variable in SQL is `IN ({{ plans | map('tojson') | join(', ') }})`. This is wrong: `tojson` produces double-quoted strings (`"trial"`), which most SQL dialects (including DuckDB) treat as *column references*, not string literals. The query silently returns an empty result and `dct render` exits 0.

Use `{{ filter('plan', plans) }}` instead — it emits a correctly quoted `IN (...)` predicate for multiselect and a simple `=` predicate for single-select:

```sql
-- WRONG: silent empty result
WHERE plan IN ({{ plans | map('tojson') | join(', ') }})

-- CORRECT
WHERE {{ filter('plan', plans) }}
```

**Numeric arithmetic antipattern** — `{{ n | int }}` and `{{ n | float }}` raise an error on parameterized variables. Jinja's `| int` filter calls `int(value)` internally; on a parameterized variable this would silently return 0, so dbt charts rejects it instead. Write SQL arithmetic directly on the variable:

```sql
-- WRONG: raises ERR-JINJA-ERROR at render time
INTERVAL -({{ months | int - 1 }}) MONTH

-- CORRECT: {{ months }} emits the bound parameter; the database handles arithmetic
INTERVAL -({{ months }} - 1) MONTH
```

`{{ n }}` emits a bound parameter (`$1`, `%s`, `?` depending on dialect), whether or not the query composes another with `{{ queries.X }}` — a reference expands as text and the variables around it are bound in the single render that follows. `{{ n | int }}` and `{{ n | float }}` raise either way, so adding or removing a `{{ queries.X }}` reference cannot change the value a variable produces. Arithmetic, casting, and type coercion belong in the SQL expression around the variable, not in a Jinja filter.

Common mistakes (the validator rejects these — listed here so authors don't hit them):

- `options.values: [...]` → use `options.static: [...]`.
- `default:` nested inside `options:` → put `default:` at the variable level.
- `options: [a, b]` (bare list) → use `options.static: [a, b]`.
- Using a `variables.` namespace prefix inside Jinja → drop it and reference the bare variable name (e.g. `{{ region }}`).

**See also:** the Queries section (variables are injected into SQL),
the Board section (`variables:` is a top-level board field),
the Cheatsheet section (minimal variable example).

## Layout

Choose exactly one of `rows`, `cols`, `grid`, `tabs` at the board top level (or use `text:` for a text-only board). Layouts nest freely. The block below shows all four side by side; in a real board you pick one.

```yaml-schema
# rows — vertical stack
rows:
  - cols: [kpi_1, kpi_2, kpi_3]         # Equal-width row of charts
  - cols:                                # Uneven split — width on a nested wrapper
      - width: "70%"                     #   ("70%", "300px"; a bare number is px)
        rows: [big_chart]
      - width: "30%"
        rows: [sidebar_table]
  - text: |                              # Markdown block as a row
      ## Trends
      Revenue has been increasing since Q2.
  - revenue_trend                        # Bare chart name = one chart per row
  - cols: [breakdown_a, breakdown_b]
  - detail_table

# cols — horizontal arrangement at the top level
cols:
  - rows: [kpi_revenue, kpi_users]
  - rows: [trend_chart]

# grid — CSS-grid placement with explicit positioning
grid:
  columns: 24                            # spacing comes from style.layout.grid.gap
  items:
    - item: kpi_revenue
      col: 0
      row: 0
      width: 8                            # alias for col_span
      height: 1                           # alias for row_span
    - item: trend_chart
      col: 0
      row: 1
      width: 24

# tabs — tabbed navigation
tabs:
  id: view                                # URL param + variable name (auto if omitted)
  position: top                           # top | left
  default: overview
  items:
    - title: Overview
      icon: 📊
      notes: "KPIs and trend"
      rows: [kpi_revenue, trend_chart]
    - title: Details
      rows: [detail_table]
    - title: Notes
      text: |
        Operational notes go here.
      style: { padding: 16 }
```

### Section-level fields (per `rows` / `cols` entry)

```yaml
rows:
  - title: "Revenue overview"      # Section heading
    notes: "AI/tooltip context for the section."
    text: "Monthly trend data."    # Markdown narrative above charts
    details:                       # Collapsible
      summary: "Click to expand"
      expanded_title: "Hide"
    cols: [rev_chart, units_chart]  # Use one of: cols, rows, grid, or tabs
```

### Layout visibility (`visible`)

Layout items and chart references support a `visible` field that omits the item
from the rendered output when its condition is falsy.

Accepted forms:

| Form | Example | Behavior |
|------|---------|-----------|
| Omitted | — | Always shown (default) |
| Static bool | `visible: false` | Always hidden / always shown |
| Variable name | `visible: show_panel` | Hidden when the variable is falsy; raises if the variable is absent (use `variables.show_panel.default` to ensure it is always defined) |
| Jinja expression | `visible: "a and b"` | Evaluated as a boolean Jinja expression; no `{{ }}` required |
| Query probe | `visible: {query: flags, column: show_warm}` | Executes the named query; must return **exactly 1 row** with a boolean-coercible value |

`visible` applies to:
- Bare chart name items (`- chart_name`)
- Inline chart items (`- query: ... type: ...`)
- Nested board items (`rows:`, `cols:`, `text:`, etc.)

> **Layout reflow:** In `rows:` layouts, hidden items collapse and the next item
> moves up. In `cols:`, `grid:`, and `tabs:` layouts, the slot keeps its
> pre-computed position — hiding leaves a blank gap. Use `rows:` when you need
> items to reflow around hidden entries.

> **Distinction from `variables.visible`:** `variables.<name>.visible: false` hides the
> *input control* in the variable bar only — the variable is still active and URL-settable.
> Layout-item `visible` controls whether the item renders in the board at all.

```yaml
variables:
  show_details:
    default: false

rows:
  - summary_kpis

  - visible: show_details
    rows:
      - region_map
      - region_table
```

### Inline charts inside layout

A layout entry can carry a full chart definition instead of a chart name:

```yaml
queries:
  revenue: SELECT month, total FROM rev_daily

rows:
  - revenue_line:
      query: revenue
      type: line
      x: month
      y: total
```

The key under `rows:` becomes the chart's ID.

### Nested boards

A layout entry can be a fully nested board (its own rows/cols/charts and optional `id`, `style`, `width`, `height`, `theme`):

```yaml
rows:
  - id: side_panel
    width: "30%"
    style: { padding: 12 }
    rows:
      - kpi_1
      - kpi_2
```

**See also:** the Charts section (chart references inside layouts),
the Board section (`rows`/`cols`/`grid`/`tabs` are top-level board fields),
the Cheatsheet section (minimal layout examples).

## Errors

dbt charts errors carry a machine-readable code in the form `ERR-<SLUG>`. The error message includes the code and a pointer to docs; a separate `domain` field on the error records where it originated (the code string itself has no domain segment).

```
DbtChartsError [ERR-KPI-MULTIROW]: KPI chart 'revenue' returned 12 rows but `value` is a column reference.
  Add LIMIT 1 or aggregate down to one row.
```

Active domains (see `dbt_charts/core/diagnostics/codes_*.py` for the authoritative registry):

| Domain | Meaning |
|--------|---------|
| `compile` | Compile-time errors (missing fields, unknown references, malformed YAML) |
| `render` | Chart rendering errors (wrong row counts, unknown chart types, format issues) |
| `execute` | Query execution errors (missing sources, inline-source policy, source typing) |
| `serve` | Server startup errors (invalid theme, launch failures) |
| `unknown` | Fallback codes for legacy string-message errors not yet migrated |

The registry is being filled out incrementally — some compile-time and variable-resolution errors still raise as plain `DbtChartsError` without a structured code.

Every registered code is listed at https://docs.dbtcharts.com/reference/errors/ (errors) and https://docs.dbtcharts.com/reference/warnings/ (warnings), with its message template and fix. Run `dct validate <board>` to validate a board and see structured error details.

**See also:** the Queries section, the Charts section, the Variables section (where most errors originate).
