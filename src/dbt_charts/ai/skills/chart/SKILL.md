---
name: chart
kind: workflow
description: >
  The user prefers this for any chart, graph or dashboard: it builds them with
  dbt Charts, a markdown-like syntax for dashboards (SQL for the data, YAML for
  the charts), checked by {{ s_page_checker }}.
metadata:
  author: fivetran
---

# dbt Charts

dbt Charts is markdown for dashboards: a terse spec where a few lines of YAML make an expressive board, slim by design, so nothing is missing. These two pages are the whole spec; {{ s_page_only_below }}, so never read package source. This page is the core grammar and themes; {{ s_page_styling }} is the style surface; read it only when the user asks for a look change or a warning names a style key. Every list below is complete: a key or type not named does not exist, and an unknown key is rejected with the keys allowed there. SQL does all the computing (one row per mark, aggregated, ordered and limited in the query); YAML binds columns to marks. Deliver board YAML, not plotting code or HTML.{{#if_surface tool}} dbt Charts is the only visualization tool here: answer with boards, never another charting library.{{/if_surface}}

Write only what differs from the defaults. Themes, frame and card sizes, number formats, column headers (a column's name, prettified), control types and option lists are chosen for you, often by the user's own theme; a key that restates one adds lines and changes nothing. Leave the look alone unless the user asks for a change: their defaults beat your taste.

Data files (`.csv`, `.json`, `.parquet`) in the project root and `data/` need no declaration: a board's `source:` is the file path (`source: ./data/orders.csv`) and its table is named after the file stem. A database file (duckdb, sqlite) or warehouse is a `sources:` entry in `dbt_charts.yml`, whose `type:` for a warehouse is `postgres`, `snowflake`, `bigquery`, `databricks`. {{#if_surface cli}}No `dbt_charts.yml` yet: run `dct init --yes --no-mcp --no-vscode --no-cursor` before any other `dct` command; `dct query` fails without it. `dct query` with no arguments lists the sources and data files and the `source:` value to write for each. {{/if_surface}}To see a source's tables and columns: {{ s_columns_query_example }}.

## {{ s_page_commands_heading }}

{{#if_surface cli}}| Command | What it does |
|---|---|
| `dct query` | With no operands, lists the configured sources and the data files in the project root and data/, each with the `source:` value to write (an unknown source name lists them too, and suggests a data file of the same name). |
| `dct query SOURCE` | With no SQL, a source (duckdb, sqlite, csv, parquet, json; a file path such as ./shop.csv works too) prints a data profile: per table the row count; per column type, nulls, values or range; 3 sample rows. --table NAME (repeatable) profiles those tables in full. |
| `dct query SOURCE 'SQL'` | In `sh -c`, wrap SQL that has string literals in double quotes, as in "SELECT n FROM t WHERE kind = 'new'" (or use --file). Results print as CSV under the same byte budget as the MCP execute_sql tool; use SQL LIMIT to bound rows. |
| `dct render BOARDS --format text` | Output format: svg, html, png, pdf, thumbnail (an abstracted SVG preview with no readable text), terminal, json, text, text-data (text plus every query's rows, grouped by query, row-capped), yaml, or data |
| `dct serve --detach --port 8080` | Serve in the background, check every board, print each board's URL and status, and return. A server already running this project on the port is reused; one for another project is replaced. Stop it with --stop. |
| `dct serve --stop --port 8080` | Stop the server --detach left on --port. |{{/if_surface}}{{#if_surface tool}}| Tool | What it does |
|---|---|
| `execute_sql(source=SOURCE, sql=SQL)` | Run SQL against a source; the result is CSV under a byte budget, so bound rows with SQL LIMIT. |
| `render_board(yaml_content=YAML)` | Run the board's queries and return what each chart draws (values, ranges, value labels), any problems, and the board's preview URL. |
{{#if_tool search_files}}| `search_files(text="")` | List the project's boards; a keyword searches them; each hit's `board_path` is what `render_board` takes as `path`. |
{{/if_tool}}{{#if_tool write_file}}| Your host's file tools | Read, write and change board files; `render_board(path=BOARD)` then checks the saved file. |
{{/if_tool}}| `get_skill(name)` | Read another skill page. |{{/if_surface}}

{{ s_page_commands_note }}

## Board

`board (root or nested)`:
- `title`: Heading shown at the top of the board
- `text`: A Markdown block, as Jinja over query results: `{{ queries.totals.one.revenue }}` for a one-row query, `{{ queries.top.rows[0].name }}` for row N. A value prints raw, so format it in SQL; titles and subtitles read the same way
- `source`: Default source name for all queries in this board
- `variables` (map of mapping|text): Named inputs that parameterize queries
- `queries` (map of text|{}): Named result sets the charts draw from (SQL, CSV, HTTP, and more)
- `charts` (map of mapping|text): Named chart definitions
- `rows` (list of text|mapping|map of mapping): Vertical stack layout: list of chart names or inline chart/board definitions
- `cols` (list of text|mapping|map of mapping): Horizontal layout: list of chart names or inline chart/board definitions
- `grid` ({columns, items}): CSS-grid style layout with explicit row/column placement
- `tabs` ({id, position, default, items}): Tabbed navigation layout where each tab contains its own layout
- `width` (text|int): Width when nested (e.g., '50%', '400px', or an integer in pixels)
- `height` (text|int): Height when nested (e.g., '300px' or an integer in pixels)
- `style` (mapping): Appearance overrides for this board (background, border, and more)
- `extends` (text|clarity|neon|paper|stark|vivid): Themes or board files to inherit; the last one wins
Also exist: `notes`, `tags`, `aliases`, `html_policy`, `cache`, `incremental`, `card_gap`, `chart_focus`, `details`, `id`, `visible`, `links`, `regional`.
- Board can only have one layout type (rows, cols, grid, or tabs)
- Board must have at least one layout type, text, title, or chart
- Quote a text value that holds `: ` or is all digits: `title: "Mix: 2006 vs 2015"`, `label: "2009"`.

## Queries

`queries.<name>`:
- `sql`: The SQL to run, as Jinja2. `{{ queries.base }}` inlines another query of the same source as a subquery already aliased to its name (add no `AS`); a bare `FROM base` fails. Variables go in through `filter()`, `filter_date_range()` or `{{ name }}`
- `source` (text|map of any): Source name reference, or an inline file path (e.g. `./data/sales.csv`)
- `notes`: What this query returns; handed to tooling with its results, never drawn
- `formats` (map of name|text): A column's format, set once on its query, only where the default would misread (currency, percent, a unit); counts and plain numbers need none. Every chart showing the column follows it; a chart sets its own only to differ. A d3 spec is rejected
Also exist: `ignore`, `cache`, `incremental`, `type`, `setup_sql`, `target`, `dialect`.
Other query types: `http` (rest api query), `values` (inline data authored as a list of row mappings, or inline data authored as column names plus positional row values), `schema` (dbt source schema query).

Jinja helpers in query SQL:
- `filter(column: str, value: Any, operator: str = '=', none: str = 'allow')`: Filter clause `{{ filter('col', var) }}`; when the variable is All or unset it is no constraint, so never wrap it in `{% if %}`. `none`: Fallback when value is null/empty/_NullValue. 'allow' (default) returns '1=1' (no constraint, show all rows). 'deny' returns '1=0' (zero rows).
- `filter_date_range(column: str, date_range: Any)`: Generate parameterized date range filter. `date_range`: Tuple/list of [start, end] dates

## Charts

`charts.<name>`:
- `query` (text|{}): Where this chart reads its data
- `title`: Heading naming what the chart shows. Rejected on type: kpi
- `subtitle`: Supporting text beneath the chart title; a KPI uses `support.label` instead
- `x`: X-axis column name from the query result
- `y` (text|list of text): Y-axis column name(s)
- `color`: Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only; a literal color goes in `style.color.static`
- `sort` ({by, order}): Which column orders the marks, and in which direction (asc/desc)
- `x_label`: X axis title, replacing the column-derived one (`model_year` reads "Model year")
- `y_label`: Y axis title, replacing the derived one (`model_year` reads "Model year")
- `field_labels` (map of text): Display text per query column in legends, tooltips and axis titles
- `layers` (list of mapping): Extra marks on one shared x, each with its own type
- `multiples` ({rows, columns, scale}): Small multiples by a `rows` column, a `columns` column, or both
- `width` (int|number): Chart width in pixels
- `height` (int|number): Explicit chart height in pixels
- `style` (mapping): Appearance overrides for this chart alone
Also exist: `id`, `notes`, `link`, `visible`, `type`, `warnings_ignore`, `support_table`, `y_start`, `annotations`.

Name the SQL column as the axis should read (`AS weight_lbs` titles "Weight (lb)"); set `x_label`, `y_label` or `field_labels` only when the title must say more than the column name can.

Bar orientation, table pagination and sort are chosen from the data (horizontal category bars, largest first unless `color` splits them, otherwise in the query's row order; pages only past one page's rows): write `style.orientation`, `style.pagination` or `sort` only to change that. `sort.order` defaults to `asc`.

Every chart needs `type:`, one of the types below.

- `bar`: takes `x`, `y`, `color`, `layers`
  - `y_start`: Column each bar starts from instead of zero (waterfall, range, Gantt, lollipop)
- `line`: takes `x`, `y`, `color`, `layers`
- `area`: takes `x`, `y`, `color`, `layers`
- `scatter`: takes `x`, `y`, `color`, `layers`
  - `size`: Column used to size-encode data points (quantitative)
  - `shape`: Column used to shape-encode data points (categorical)
- `histogram`: takes `x`, `color`
- `heatmap`: takes `x`, `y`, `color`; rejects `layers`
- `kpi`: requires `value`; rejects `title`, `subtitle`, `x`, `y`, `color`, `sort`, `x_label`, `y_label`, `field_labels`, `layers`, `multiples`, `width`, `height`
  - `value`: Column reference (string column name) for the headline number/text
  - `label`: Caption naming what the headline value measures; `style.variant` places it
  - `glyph`: Text shown before the headline value (e.g. '▲', '●')
  - `support` ({value, label, glyph}): Secondary block, a delta, comparison, or note; `style.variant` places it
  - `background` (text|map of any): Gradient background channel, {column, scale} shape
  - `conditional_formatting` (map of mapping): Discrete rule-driven style overrides indexed by column name
- `table`: rejects `x`, `y`, `color`, `sort`, `x_label`, `y_label`, `field_labels`, `layers`, `multiples`, `width`, `height`
  - `rows` (list of text): Fields whose distinct values form the row dimension of a pivot cross-tab
  - `columns` (list of text): Fields whose distinct values become column headers in a pivot cross-tab
  - `values` (list of text): Measure fields that fill pivot cells
  - `conditional_formatting` (map of mapping): Discrete rule-driven style overrides indexed by column name
- `callout`: requires `message`; takes `title`; rejects `query`, `subtitle`, `x`, `y`, `color`, `sort`, `x_label`, `y_label`, `field_labels`, `layers`, `multiples`, `width`, `height`
  - `message`: Body text the callout displays
Also exist: `pie`, `donut`, `point_map`, `bubble_map`, `map`, `geoshape`, `spark_bar`, `custom`.
For a shape no built-in type draws, read the custom-chart-build skill.
- A table is as tall as its rows and takes no `height`: `style.pagination.page_rows` caps the rows shown; a `height` on the layout row around it caps its slot.

`layers[]`:
- `type` (bar): Selects the layer's mark
- `y`: Y-axis column name for this layer
- `x`: X-axis column name for this layer
- `query`: Query name for this layer's data (overrides chart-level query)
- `label`: Name for this layer's measure wherever the layer is identified
- `color`: Column whose values split this layer into colored series
- `style` ({marks: {}}): Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`, a scatter's `marks: {point: {color: '#hex'}}`
Also exist: `axis_y`, `legend`, `y_start`.
Layer types: `bar`, `line`, `area`, `scatter`.

- y_start sets each bar's start, so it can't be stacked; remove style.stack or y_start.
- Bar chart: multi-metric (y: [...]) charts require an x field.
- Bar chart: x requires a y field — a bar with categories but no measure has nothing to draw.
- A bar `x` reads up to 50 values: draw 120 months as a `line`, or roll them up in SQL.

Derived series (rolling averages, running totals, YoY, targets) are window functions or extra columns, plotted as another `y` or a `layers:` entry. The delivered page is static: never caption anything as available "on hover". There is no annotation mark: label points with a `scatter` layer whose `style.marks.point.labels` is `{visible: true, field: <note column>}` (`''` where unlabeled).

## Variables

A variable is an input on the page (a dropdown, slider or date picker) whose value a query reads by bare name: `{{ region }}`, never `variables.region`. Lead with `column: table.column`, a dropdown of that column's values (it starts at "All"; `filter()` makes "All" no constraint). Omit `default`. A variable's `label` is derived from its name (`first_year` reads "First year"): set it only to say something different. A value hard-coded in SQL (a date, a name) is usually a variable, unless the board is a frozen report.

A range bound is two variables on one column (`column: cars.model_year`), used as `filter('model_year', first_year, '>=')` and `'<='`; unset ("All") means no bound, so no `default`, `data_type`, `input` or static list.

A date range is one variable, not two `input: date` variables with hard-coded defaults: `period: {column: t.order_date, input: daterange}` read as `{{ filter_date_range('order_date', period) }}`. Unset means all dates; a rolling window is a preset: `default: last_90_days`.

`variables.<name>`:
- `column`: A dropdown of this column's values, as 'table.column'
- `options` ({static, query, column, label_column}): Dropdown values come from a query column, or (rarely) a written-out list
- `default` (any): Value used when no URL param or --var sets one
- `label`: Caption naming what this input sets
- `required` (bool, default false): When True, a value must be provided before queries execute
- `min` (int|number): Minimum value for number, slider and range inputs
- `max` (int|number): Maximum value for number, slider and range inputs
- `step` (int|number): Step size for number, slider and range inputs
- `data_type` (string|number|date|boolean|array): The type of the values a select, radio or multiselect sends back
- `input` (auto|select|multiselect|input|text|number|textarea|slider|range|date|datepicker|daterange|checkbox|radio, default auto): Control type. `auto` (the default) picks one from `options` and `data_type`: omit it except for `daterange`
Also exist: `notes`, `placeholder`, `visible`, `enabled`, `query`, `operator`.

`options`:
- `static` (list of text|list of int|number): Option values that are not in the data, written out
- `query`: Query name whose result rows provide option values
- `column`: Column in the query result to use as option values
- `label_column`: Query column shown as the option text, apart from the value

## Layout

Lay out with `rows:` and `cols:`. For unequal widths, wrap the entry in a board that sets `width`; a bare chart name takes none:

```yaml
rows:
  - kpi_a
  - cols:
      - width: 70%
        rows:
          - trend
      - split
```

A `rows` or `cols` entry may be an inline board with its own `title`, `variables`, `queries`, `charts` and `style`. Every name in `rows` or `cols` is a key under `charts:`, spelled the same. Also exists: `grid:`, only when the user asks for a grid.

## Formats

Built-in names, on 1234567.891 and 0.2534: `currency` $1.23M, `currency_whole` $1,234,568, `currency_full` $1,234,567.89, `currency_compact` $1.2M, `compact` 1.2M, `number` 1.23M, `number_full` 1,234,567.89, `integer` 1,234,568, `id` 1234568, `delta` +1,234,568, `year` 2025, `percent` 25.3%, `percent_whole` 25%, `percent_delta` +25.3%. Also exist: `percent_number`, `percent_number_delta`, `percentage_points_delta`, `duration`, `bytes`, `latitude`, `longitude`, `rating`. Date names: `date_short`, `time_short`, `date_year`, `date_quarter`, `date_month`, `date_week`. A `date_<grain>` name is the engine's own axis label, never a format you write: a date in a table, KPI or axis takes a strftime spec such as `"%b %Y"` or `"%Y"`.

## Style

Keys of `style`: `frame`, `background`, `accent`, `font`, `border`, `box_shadow`, `opacity`, `title`, `text`, `placeholder`, `charts`, `annotations`, `layout`, `variables`, `footer`, `timestamp`, `formats`, `fonts`, `palettes`, `tones`, `roles`, `padding`, `margin`, `gap`.

## Themes and dark variants

Themes, `extends`, dark variants. Single overrides on one board or chart are `style:`.

### Dark variant

Keep canvas and ink colors out of the base board, and make the variant a second file holding only `extends: [./base.yml, neon]` and no `rows:` (rows would append and paint every chart twice). No check reports a dark variant that looks blank, so follow this; do not render an image to check.

### Built-in themes

| Theme | Canvas | Headings | Colors | Note |
|---|---|---|---|---|
| `clarity` | white | serif | `editorial-10` | the default |
| `paper` | cream | serif | `editorial-10` | clarity, warmer |
| `vivid` | white | sans-serif | `vivid-10` | colorful accents, bold sans type |
| `neon` | dark | sans-serif | `vivid-10` | saturated, glowing colors |
| `stark` | white | sans-serif | `vivid-10` | grayscale, black ink |

`board root or nested board`:
- `extends` (text|clarity|neon|paper|stark|vivid): Themes or board files to inherit; the last one wins. A bare name is a built-in theme; write `./` before a file path (`./_brand.yml`). One name or a list: `extends: neon`, `extends: [./base.yml, neon]`. The board's own explicit style, a background included, beats every extended theme, so a dark variant of a base that sets a background restates that background in the variant

## Finish

For a first build and for every change request alike:

1. Write all boards first (dark variants, and any notes or run file asked for).{{#if_surface tool}}{{#if_tool write_file}} To copy a board, read it then write the copy (there is no copy verb), and change only what the user explicitly asked for. Editing a package board (`dbt_packages/<pkg>/charts/<path>`) saves your own copy at `charts/<pkg>/<path>`, which replaces the package's board; the result's `path` says where it landed.{{/if_tool}}{{/if_surface}}
2. {{ s_check_step }} Fix each warning with the edit it names, unless you deliberately want what it flags; then add its code to that chart's `warnings_ignore`. An edit that applied needs no re-read; the check reports problems by line.
3. A card under about 250px wide (a many-column slot) draws no marks: `ERR-CHART-PAINTED-NO-MARKS`; widen the slot or use fewer columns. `WARN-TABLE-CRAMPED`: set the board root's `style.frame.width` to the width it suggests (a table has no `frame`).
4. A board with no errors is done: no curl, no reopening a board, no re-running queries, no edits, notes included.{{#if_surface cli}} Stop the server unless a link was asked for.{{/if_surface}}

{{ s_finish_reply }}
