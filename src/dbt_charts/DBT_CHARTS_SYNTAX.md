# dbt charts YAML Syntax

Authoring reference for dbt charts board YAML. Every option in this file is enforced by the compiler (`extra="forbid"` is set on every model — unknown keys are schema errors).

Browse with `dct docs` (run with no args for the topic catalog, `dct docs <topic>` for one section, `dct docs all` for the whole file).

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

**See also:** `dct docs cheatsheet` (minimal examples), `dct docs queries`, `dct docs charts`, `dct docs variables`.

## Cheatsheet

One screen of essentials. Each topic below has a dedicated H2 (`dct docs board`, `dct docs queries`, …) for full coverage.

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
    input: select
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
  region:
    input: select                  # See `dct docs variables` for all 14 input types
    options: { static: [US, EU, APAC] }
    default: US
```

Reference variables inside queries with bare `{{ region }}` — no `variables.` prefix.

### Charts

```yaml
charts:
  revenue_trend:
    query: revenue                 # Named query reference
    type: line                     # See `dct docs charts` for all 16 authorable chart types
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

**See also:** `dct docs board` (full top-level reference),
`dct docs queries`, `dct docs charts`, `dct docs variables`, `dct docs layout`,
`dct docs errors` (common error codes), `dct docs all` (whole reference).

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
style: { padding: 16, background: dbt-grays.canvas }
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
auto_link: true                # Table charts auto-link rows to /data/ detail pages
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
| `style` | object | Board-style block (see [Board style](#board-style)) |
| `width` | string \| int | Width when nested (`"50%"` or pixels) |
| `height` | string \| int | Height when nested |
| `visible` | bool \| string \| object | Render condition when nested as a layout item (see [Layout visibility](#layout-visibility-visible)) |
| `theme` | string | Theme name (e.g. `clarity`, `paper`, `neon`) — sugar for `extends:`; inherited by nested boards |
| `extends` | string \| list[string] | Board name(s)/path(s) or a built-in theme this board inherits from (see [Inheritance](#inheritance-extends-and-theme)) |
| `auto_link` | bool | Auto-link table rows to their `/data/…/detail/` pages (default `false`). An explicit chart `link:` always wins; `link: false` on a chart suppresses its automatic link. |

`board:` as a top-level key is rejected. Put board properties (title, rows, queries, …) directly at the YAML root.

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

### Caching

`cache:` sets the query-result cache policy. It is authorable at four scopes —
project config → source → board → query — and the nearest scope wins
field-by-field. Every scope but the project root takes any scalar below
(the root must state a ttl — it has no parent to inherit one from):

```yaml-schema
cache: 1h        # on, expire after an hour (units: s m h d w — compound 1h30m OK)
cache: forever   # on, never auto-expire
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

### Board style

`style:` on a board, nested board, or layout section accepts a fixed set of keys — not arbitrary CSS:

```yaml
style:
  padding: "16px"
  margin: "0 0 12px 0"
  background: dbt-grays.canvas
  font:
    color: dbt-grays.ink
  gap: 12
  border:
    width: 1
    color: dbt-grays.border
    radius: 8
  text:
    align: left
    column:
      max_number: 2
      gap: 24
      rule:
        width: 1
        color: dbt-grays.separator
```

CSS-only keys like `border-radius`, `border-left`, `margin-top` are not board-style fields and are rejected.

#### Title heading levels

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

**See also:** `dct docs queries` (data layer), `dct docs charts` (display layer),
`dct docs layout` (composition), `dct docs cheatsheet` (one-page essentials).

## Sources

Named connections, declared once in the project root config; boards reference them by name.

The `sources:` registry in the project root `dbt_charts.yml` names every connection boards may read from. A board's `source:` is always one of these names (or an inline path to a single CSV/JSON/Parquet file) — never a connection definition.

```yaml
# dbt_charts.yml — project root, not a board
sources:
  analytics:                     # A dbt project: credentials stay in profiles.yml
    type: dbt_profile
    profile: my_dbt_project
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

Types: `dbt_profile`, `postgres`, `snowflake`, `bigquery`, `redshift`, `mysql`, `trino`, `duckdb`, `sqlite`, `csv`, `json`, `parquet`, `http`.

**Local-only types.** `duckdb` and `sqlite` read a file on the machine running `dct`. dbt charts Cloud connects only to `bigquery`, `postgresql` (Cloud's spelling of `postgres`), `redshift`, and `snowflake` — it never reads a `.duckdb` file from a repo, committed or not. For Cloud, load the tables into one of those warehouses, or export each table to a Parquet/CSV/JSON file committed in the repo and register it once under `sources:` (`type: csv | json | parquet`, a `files:` map, paths relative to the project root); every query then references the source by name, and it renders identically locally and on Cloud with no connection step. A board that is the only reader of one file can skip the registry and point at it inline: `source: <path>` resolves against the board's own directory or the project root, whichever exists (`source: ../data/orders.parquet` from `charts/`, or `source: data/orders.parquet` from any board depth). Both existing at once is a compile error naming both.

## Queries

Queries are the data layer. Charts reference queries by name; queries never embed display logic.

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
| `source` | Source name (from `dbt_charts.yml`'s `sources:` registry), or an inline file path (`./data/x.csv`) for a single colocated CSV/JSON/Parquet. An inline connection dict (`{type: postgres, ...}`) is rejected — reference a named source instead. Not accepted on `http` queries. |
| `target` | dbt target name (defaults to `dev`) |
| `cache` | Result-cache override for this query — `5m`, `forever`, `true`, `false` — refines the board/source/project policy (see [Caching](#caching)) |
| `filters` | Post-execution result filters |
| `limit` | Maximum rows returned |
| `pivot` | Table-rendering cross-tab hint: `{column, value}` |
| `ignore` | Diagnostic codes to suppress (e.g. `["WARN-FANOUT-RISK"]`) |

SQL fields: `sql`, `setup_sql`.
HTTP fields: `url`, `method`, `headers`, `params`, `body`, `json_path`.
dbt-model fields: `model`, `columns`.
Inline-values fields: `columns`, `values` (or `rows` for record-shape).
Schema fields: `schema`, `table`, `column`, `fields` (project result rows to exactly these keys, in this order).

Connection source types (named in the project root `dbt_charts.yml`'s `sources:`
registry — never inline in a board): `postgres`, `snowflake`, `bigquery`,
`redshift`, `mysql`, `duckdb`, `sqlite`, `csv`, `json`, `parquet`, `http`, `dbt_profile`.
CSV/JSON/Parquet may instead be referenced inline as a `source:` file path
without a registry entry — see the example above.

### Jinja variable injection

Queries are Jinja-rendered before execution. Reference variables as bare names — `{{ region }}` — not with a `variables.` namespace prefix.

```yaml
variables:
  region: { input: select, options: { static: [US, EU, APAC] } }
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

**See also:** `dct docs variables` (use `{{ var }}` in SQL),
`dct docs charts` (charts reference queries by name),
`dct docs errors` (query-execution error codes).

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
      number_format: ",.0f"   # D3 format string or named alias for axis/tooltip format
      # bar/area families also accept style.orientation and style.stack
```

### Chart types (16 authorable)

Set `type:` to one of:

**Basic** (one mark per chart) -- `bar`, `line`, `area`, `scatter`, `pie`, `donut`, `kpi`, `table`.

**Statistical** -- `histogram`, `heatmap`.

**Geographic** -- `geoshape`, `map`, `point_map`, `bubble_map`.

**Overlays** -- `bar`, `line`, `area`, and `scatter` accept a `layers:` field for mixed-mark or dual-axis charts (see [Combo charts](#combo-charts-barlinearea-with-layers) and [Composition](#composition)).

**Sparklines** -- `spark_bar` (compact horizontal bars used in profiler cards).

**Auxiliary** -- `callout` (message card with a `style.tone:` field; `message:` required).

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
| dual axis, combo, bar and line | layers: on a cartesian chart, with axis_y.position: right on the added layer for its own y-axis |
| stacked column | type: bar with style.orientation: vertical, color:, and style.stack: zero |
| grouped column, clustered column | type: bar with style.orientation: vertical, color:, and style.stack: none |
| lollipop | type: bar with style.marks.bar.band_width thinned to a stem, plus a layers: scatter on the same y (style.marks.bar.size is a separate fixed-pixel mode and does not thin the bar) |
| bullet | type: bar with style.stack: zero, style.stack_order: data and color: on the qualitative range column — one row per (category, band) — plus a layers: bar on the value and a layers: line on the target, both reading their own one-row-per-category source via layers[].query (sharing the ranges' rows multiplies each by the band count). Thin the value bar with style.marks.bar.band_width so the ranges stay visible, give the target style.marks.line.curve: step with connect: false for the goal tick, and set style.orientation: vertical |
| slope | type: line with a two-category x and color: on the series column (year-shaped x values resolve to a continuous temporal scale, which fills in the span between the pair) |
| bump | type: line on a rank column with color: on the series column (the data is entity x period x rank, so without it the rows collide on the period key) and a descending style.axis_y.scale.continuous.domain (e.g. [6, 1]) so rank 1 is on top |
| dot plot, cleveland dot plot | type: scatter with a categorical x and a measure y; for several dots per category use long-format rows with color: on the series column rather than layers:, which keeps the shape rotatable |

Shapes dbt charts cannot draw at all — naming them is the half a recipe cannot cover:
`alluvial`, `barbell`, `candlestick`, `chord`, `connected dot plot`, `dumbbell`, `funnel`, `gantt`, `gauge`, `marimekko`, `network`, `radar`, `ranged dot`, `sankey`, `spider`, `sunburst`, `treemap`, `violin`, `waterfall`, `word cloud`.

`dumbbell` (and its synonyms `barbell` and `connected dot plot`) and `ranged dot` each
need a single mark spanning two values, and no layerable mark takes a second positional
channel. Spelling is tolerant here too: a trailing `chart`, `graph`, `plot` or `diagram`
is ignored when the full spelling does not match, so `dumbbell chart` resolves.

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
| `link` | string \| false | Click-through URL template for drill-down links; `false` suppresses the chart's automatic link when the board sets `auto_link: true` (table column links are unaffected) |
| `multiples` | object | Partition into small multiples: `{rows, columns, scale}` — cartesian charts (`bar`/`line`/`area`/`scatter`/`heatmap`) only (see [Small multiples](#small-multiples-multiples)) |
| `warnings_ignore` | list[string] | Render-warning codes to suppress for this chart only (e.g. `[WARN-AXIS-TITLE-TRUNCATED]`; unknown codes are rejected — list codes with `dct docs warnings`) |
| `layers` | list | Overlay layers on cartesian charts (`bar`/`line`/`area`/`scatter`) — see [Combo charts](#combo-charts-barlinearea-with-layers) |
| `conditional_formatting` | object | Discrete style rules by column — `table`/`kpi` only (see [Conditional formatting](#conditional-formatting)) |
| `support_table` | list | Attached mini-table on bar/line/area charts, above the plot by default (including those with `layers:`) — see [Composition](#composition) |
| `height` | int \| float | Exact pixel height. Wins over `aspect_ratio` and theme defaults. Bypasses `min_height`/`max_height`. Not valid on `kpi`, `table`, `callout`, `spark_bar`. |
| `aspect_ratio` | float | Chart shape: `height = width / aspect_ratio`. Theme default is `1.5`. Not valid on `kpi`, `table`, `callout`, `spark_bar`. |
| `min_height` | float | Height floor for this chart only; overrides `style.charts.min_height`. Ignored when `height` is set. |
| `max_height` | float | Height ceiling for this chart only; overrides `style.charts.max_height`. Ignored when `height` is set. |

`height` and `aspect_ratio` live at **chart root** — they are rejected under `style:`. `style:` is paint only (colors, fonts, marks).

KPI-only fields: `value`, `label`, `support`. KPI uses `label:` for the header text — `title:` is rejected on KPI charts. Chart-root `format:` / `formatter:` is rejected on all chart types. Use the family slot instead.

`axis_x`/`axis_y` name the channel, not the visual edge: on every cartesian family the measure is `axis_y`, including a horizontal bar that draws it along the bottom. A number preset belongs on the measure.

Put a number preset on an axis whose ticks can't carry it and the engine raises `ERR-LABEL-FORMAT-AXIS-MISMATCH` — on `axis_x` or `axis_y`, the mirror ghost (`axis_y.mirror.format`), or heatmap's y, whichever axis the format actually lives on. Two cases raise:

- The axis resolves to a **band** scale (nominal/ordinal) and its ticks are not already readable as numbers. Numeric categories (`stage_id: 1, 2, 3`), numeric strings and booleans format cleanly and stay legal — a bar over `stage_id` paints `$1 $2 $3` and never raises.
- The axis resolves to a **temporal** scale at all — dates get no numeric-tick exemption, since there's no reading of `$,.0f` over a date the author wanted.

| Shape | Dimension scale |
|---|---|
| `bar` over a category | band |
| `bar` over `yearweek` / `yearmonthdate` buckets | band, always |
| `bar` over `year` / `yearquarter` / `yearmonth` buckets | band, up to 60 distinct buckets (`max_ordinal_buckets`); temporal at 61+ |
| `line` / `area` / `scatter` over a text category | band |
| `line` / `area` / `scatter` over bucketed dates | temporal |
| `line` / `area` with `curve: step` over bucketed dates | band — the step plateau needs a band scale |
| `heatmap` x/y | band, always nominal |
| `scatter` over a genuinely numeric x, or its usual numeric y | quantitative — applies normally |
| `scatter` over a categorical y (a dot plot) | band |

A temporal x — `line`/`area`/`scatter` over dates, or a bar past 60 buckets — raises the same code; use a time token (`"%b %Y"`) or `style.time_format` there instead. A `heatmap` has no measure axis at all: both axes are grid dimensions and the value lives on the color channel, which carries no label format of its own, so a number preset on either axis — including its `y` — raises too. `style.axis_y.mirror.format` follows the same rule whenever the mirrored edge is categorical — a dot plot's y, and also a default-orientation (horizontal) bar, where the rotation puts the category on that edge. Every one of these used to render something wrong instead — look at the render only if you're debugging the diagnostic itself.

The family slots:

| Family | Format slot |
|--------|-------------|
| `line`, `bar`, `area`, `scatter` — the measure | `style.number_format` or `style.axis_y.labels.format` |
| same — the dimension, when it is a date | `style.time_format`, or a time token on `style.axis_x.labels.format` (e.g. `"%b %Y"`) |
| same — the dimension, when it is a text category | no format applies |
| same — the dimension, when its ticks are numbers | `style.axis_x.labels.format` — `number_format` never reaches the dimension axis |
| `heatmap` | no axis format applies — the value is on the color channel |
| `kpi` value | `style.value.format` |
| `kpi` support | `support.format` |
| `table` column | `style.columns.<col>.format` |

#### Number format aliases

`number_format`, `style.value.format`, table column `format`, and
`support.format` accept a D3 format string or a named alias (engine-owned
specs shown). The three *native* aliases at the bottom are Python-formatted
and valid only in KPI value/support and table-cell `format` slots —
`number_format` (a Vega-painted slot) rejects them with
`ERR-FORMAT-NATIVE-IN-VEGA-SLOT`:

| Alias | Spec | Renders like |
|-------|------|--------------|
| `integer` | `,.0f` | `12,346` |
| `number` | `.3~s` | Register depends on the slot: table cells render analytic `12.4 K`; KPI headline values and mark value labels render narrative `12.4k`; axis ticks compact only when the largest tick has ≥6 digits (below that the engine paints plain digits — `20,000`, no suffix). Also the engine fallback when no format is authored |
| `number_full` | `,.2f` | `12,345.68` — every digit at every magnitude |
| `currency` | `$.3~s` | `12400` → `$12.4 K` (analytic default; narrative slots render `$12.4k`) |
| `currency_whole` | `$,.0f` | `$12,346` |
| `currency_full` | `$,.2f` | `$12,345.68` — cents at every magnitude |
| `percent` | `.1%` | `0.42` → `42.0%` — **multiplies by 100**; for a value that already IS a whole-number percent (`42`, `148.23`), use `percent_number` |
| `percent_whole` | `.0%` | `0.42` → `42%` |
| `percent_delta` | `+.1%` | `0.018` → `+1.8%` (ratio in, signed percent out) |
| `percent_number` | native | `148.23` → `148.2%` (no multiplication; one decimal). Native — not valid in `number_format` |
| `percent_number_delta` | native | `1.8` → `+1.8%`. Native — not valid in `number_format` |
| `percentage_points_delta` | native | `1.8` → `+1.8 pts`. Native — not valid in `number_format` |
| `delta` | `+,d` | `+1,234` |
| `year` | `d` | `2026` |

`time_format` takes D3 time specs or `date_short` (`%-d %b %Y` → `5 Mar 2026`).

`glyph` and `tone` at chart root are also rejected on KPI — use `style.glyph.character` for the glyph. `tone` has no style-level home: it lives only on `support.tone`, since the support row is the block it paints (the headline value stays neutral). Override the value/glyph color with `style.value.font.color`, or the whole card's ink with `style.font.color`.

Top-level chart fields shared by all types: `id`, `query`, `type`, `title`, `subtitle`, `notes`, `height`, `aspect_ratio`, `style`, `link`, `warnings_ignore`.

### Chart-type cheatsheet

Each block below shows the minimum-viable shape for one chart type. They are stacked for compactness; in a board, each chart sits under its own key in `charts:`.

```yaml-schema
# bar — categorical x, numeric y
type: bar
x: category
y: value
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
variant: stacked        # stacked (default) | inline (one baseline row) | compact (2-column)
style:
  value:
    format: ",.0f"       # number format; `format:`/`formatter:` at chart root is rejected on KPI
    font:
      color: "#c2410c"   # optional; paints the headline value and its glyph
  label:
    font:
      color: "#2563eb"   # optional; paints the label only
  glyph:
    character: "▲"       # glyph character; moved from chart root (ADR-001)
  align: right           # left (default) | center | right — one field for the
                          # whole card; value/label/support move together
# style.font.color is the whole-card fallback under the two keys above.
# The headline value has no tone field — it stays neutral by design (NYT/FT
# convention). Tone lives on the block it paints: the support row.
support:                # Optional support line (same shape: value/label/format/glyph/tone)
  value: prev_revenue
  label: "vs last month"
  format: percent_delta
  glyph: "▲"
  tone: positive         # positive | negative | warning — colors this row only

# table — always renders every query column, in query order; `style.columns`
# is styling-only (keyed by column name) and `visible: false` is the sole way
# to hide a column
type: table
style:
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
      background: dbt-grays.canvas
      font: { color: dbt-grays.ink, weight: "600" }
      scale:
        background:
          palette: dbt-seq-blue                # palette name, or an explicit stop list
          domain: data                   # currently only "data"
          min: 0
          max: 1000000
          null_color: dbt-grays.surface-subtle
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
  fill: dbt-grays.surface-subtle
  stroke: dbt-grays.border

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

# Pure marks (advanced use — not accepted as base chart types):
# circle, square, tick, rule, trail, rect, arc, image

# callout — message card with a style.tone: field (info | negative | warning | positive)
type: callout
message: "Query is disabled in this environment."
style:
  tone: warning  # optional; defaults to info

```

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
      scale: shared          # shared (default; panels comparable) | independent
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
            font: { color: dbt-grays.ink }
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
      number_format: number           # compacts axis ticks only when the top tick has >=6 digits
      legend:
        visible: false                # hide the legend
      axis_x:
        labels:
          angle: -45                  # rotate crowded x labels
      axis_y:
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

   Optional per-entry fields: `format` (D3 format string), `label` (left-stub row label; not allowed on `per_series:`).

   `style.support_table.position` places the strip: `top` (the default on a horizontal category axis — vertical bar, line, area) or `bottom`; on a horizontal bar the categories run down the y-axis, so the strip becomes a column block on `left`/`right` and defaults to the side the category labels are on. Two things to know about `top`:
   - Rows read bottom-up — the first authored entry is the row nearest the plot, the last one sits at the top of the strip.
   - Only `style.support_table.padding_top` separates the strip from what is above it, so a two-line subtitle touches the first row until you raise that padding.

   Constraint: support_table requires a single chart-level `x:`. Layered charts with per-layer `x:` differing from the chart-level x are rejected.

3. **Layout composition** — the `rows`, `cols`, `grid`, `tabs` structure in [Layout](#layout). Layout composes charts into a board; chart-level composition belongs to `layers:` (overlays) and `support_table:`.

Non-goals (not part of the authored chart surface): Vega-Lite `encoding`, `mark`, `spec`, `config`, `transform`, `params`, `resolve`, `hconcat`, `vconcat`, `concat`, `repeat`. These keys are rejected at compile time. Use the typed channels (`x`, `y`, `color`, …) and `style:` instead; use the layout primitives for visual composition.

**See also:** `dct docs queries` (charts reference queries by name),
`dct docs variables` (use `{{ var }}` in chart queries),
`dct docs layout` (compose charts on the page),
`dct docs cheatsheet` (one-page essentials).

### Category colors (board-wide)

A data value takes one palette **slot** board-wide, not a hex, so a category
keeps the same color on every chart that draws it (and the same direct-label
ink, table swatch, and nested-board coloring). A field binds automatically
once two or more charts use it as their `color:` channel — a chart with
`layers:` has no categorical channel of its own, so it neither binds nor
counts toward the two. Author a pin to bind a field on a single chart too, or
to choose the slot:

```yaml
style:
  charts:
    category_colors:
      category:
        values:
          Electronics: category[1]      # a series slot — re-skins with the theme
          Accessories: dbt-grays.muted  # chrome — text, grid, borders
          Tools: negative.solid         # good / bad / attention
```

Pin keys are the exact data values from your query results, case-sensitive —
not a display label. A literal hex is accepted too. A pin naming a value no
chart on the render draws is skipped with `WARN-CATEGORY-COLOR-PIN-UNSEEN`
rather than raising; two pins resolving to the same color is
`ERR-CATEGORY-COLOR-PIN-DUPLICATE`; an authored field whose data has more
distinct values than the palette has swatches raises
`ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED` (an unauthored field just declines to
bind).

## Color

Colors are palette tokens, not hex — the theme resolves a token, so a board restyles itself on a theme switch.

| Want | Write |
|---|---|
| A series slot | `category[1]`, `category_dark[2]`, `category_light[3]`, `category_ghost[1]` |
| Good / bad / attention | `positive.solid`, `negative.solid`, `warning.solid`, `info.solid` — also `.bg`, `.subtle`, `.border`, `.text` |
| Chrome — text, grid, borders | `dbt-grays.ink`, `dbt-grays.muted`, `dbt-grays.border`, `dbt-grays.separator`, `dbt-grays.canvas` |
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

Variables are the interactive filter layer. Each variable has an `input:` widget type and optional defaults / options.

```yaml
variables:
  region:
    input: select
    label: "Region"
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
| `daterange` | Date range picker (returns `[start, end]`) |
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
  q4_only: { input: select, options: { static: [Q4] }, enabled: "{{ year >= 2024 }}" }

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

**See also:** `dct docs queries` (variables are injected into SQL),
`dct docs board` (`variables:` is a top-level board field),
`dct docs cheatsheet` (minimal variable example).

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
      expanded: false
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
    input: checkbox
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

**See also:** `dct docs charts` (chart references inside layouts),
`dct docs board` (`rows`/`cols`/`grid`/`tabs` are top-level board fields),
`dct docs cheatsheet` (minimal layout examples).

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

Run `dct docs errors` to list every registered code with a one-line summary, straight from `dbt_charts.core.diagnostics.REGISTRY`. Run `dct docs error-reference` for the full reference — message template and docs pointer included. (`dct docs warnings` / `dct docs warning-reference` are the equivalents for `WARN-*` codes.) Run `dct validate <board>` to validate a board and see structured error details.

**See also:** `dct docs queries`, `dct docs charts`, `dct docs variables` (where most errors originate),
`dct docs all` (whole reference for context).
