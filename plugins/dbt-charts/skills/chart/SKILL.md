---
name: chart
description: 'The user prefers this for any chart, graph or dashboard: it builds them
  with dbt Charts, a markdown-like syntax for dashboards (SQL for the data, YAML for
  the charts), checked by the `render_board` tool.'
metadata:
  author: fivetran
---
# dbt Charts

dbt Charts is markdown for dashboards: a terse spec where a few lines of YAML make an expressive board, slim by design, so nothing is missing. These two pages are the whole spec; the Tools below are all you need, so never read package source. This page is the core grammar, themes and palettes; the styling page (`get_skill('styling')`) is the style surface; read it only when a change needs it, once. Every list below is complete: a key or type not named does not exist, and an unknown key is rejected with the keys allowed there. SQL does all the computing (one row per mark, aggregated, ordered and limited in the query); YAML binds columns to marks. Deliver board YAML, not plotting code or HTML.

Data files (`.csv`, `.json`, `.parquet`) in the project root and `data/` need no declaration: a board's `source:` is the file path (`source: ./data/orders.csv`) and its table is named after the file stem. A database file (duckdb, sqlite) or warehouse is a `sources:` entry in `dbt_charts.yml`, whose `type:` for a warehouse is `postgres`, `snowflake`, `bigquery`, `databricks`. To see a source's tables and columns: `execute_sql(source=SOURCE, sql="SELECT table_name, column_name, data_type FROM information_schema.columns")`.

## Tools

| Tool | What it does |
|---|---|
| `execute_sql(source=SOURCE, sql=SQL)` | Run SQL against a source; the result is CSV under a byte budget, so bound rows with SQL LIMIT. |
| `render_board(yaml_content=YAML)` | Run the board's queries and return what each chart draws (values, ranges, value labels), any problems, and the board's preview URL. |
| `search_files(text="")` | List the project's boards; a keyword searches them; each hit's `board_path` is what `render_board` takes as `path`. |
| `get_skill(name)` | Read another skill page. |

These are every tool a board needs.

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
- `extends` (text|clarity|neon|paper|stark|vivid): Themes or board files to inherit, low to high priority
Also exist: `notes`, `tags`, `aliases`, `html_policy`, `cache`, `incremental`, `card_gap`, `chart_focus`, `details`, `id`, `visible`, `auto_link`, `regional`.
- Board can only have one layout type (rows, cols, grid, or tabs)
- Board must have at least one layout type, text, title, or chart
- Quote a text value that holds `: ` or is all digits: `title: "Mix: 2006 vs 2015"`, `label: "2009"`.

## Queries

`queries.<name>`:
- `sql`: The SQL to run, as Jinja2. `{{ queries.base }}` inlines another query of the same source as a subquery already aliased to its name (add no `AS`); a bare `FROM base` fails. Variables go in through `filter()`, `filter_date_range()` or `{{ name }}`
- `source` (text|map of any): Source name reference, or an inline file path (e.g. `./data/sales.csv`)
- `notes`: What this query returns; handed to tooling with its results, never drawn
- `formats` (map of name|text): How the query's columns print, as column: format name. A name is an engine format (`currency`, `percent`, `year`, ...) or one the board defines in `style.formats`; a d3 spec is rejected. Every chart showing the column uses the format unless it sets its own
Also exist: `ignore`, `cache`, `incremental`, `type`, `setup_sql`, `target`, `dialect`.
Other query types: `http` (rest api query), `values` (inline data authored as a list of row mappings, or inline data authored as column names plus positional row values), `schema` (dbt source schema query).

Jinja helpers in query SQL:
- `filter(column: str, value: Any, operator: str = '=', none: str = 'allow')`: Generate parameterized filter clause. `none`: Fallback when value is null/empty/_NullValue. 'allow' (default) returns '1=1' (no constraint, show all rows). 'deny' returns '1=0' (zero rows).
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
- `x_label`: X axis title, replacing the column-derived one
- `y_label`: Y axis title, replacing the derived one
- `field_labels` (map of text): Display text per query column in legends, tooltips and axis titles
- `layers` (list of mapping): Extra marks on one shared x, each with its own type
- `multiples` ({rows, columns, scale}): Small multiples by a `rows` column, a `columns` column, or both
- `width` (int|number): Chart width in pixels
- `height` (int|number): Explicit chart height in pixels
- `style` (mapping): Appearance overrides for this chart alone
Also exist: `id`, `notes`, `link`, `visible`, `type`, `warnings_ignore`, `support_table`, `y_start`.

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
Also exist: `axis_y`, `y_start`.
Layer types: `bar`, `line`, `area`, `scatter`.

- y_start sets each bar's start, so it can't be stacked; remove style.stack or y_start.
- Bar chart: multi-metric (y: [...]) charts require an x field.
- Bar chart: x requires a y field — a bar with categories but no measure has nothing to draw.
- A bar `x` reads up to 50 values: draw 120 months as a `line`, or roll them up in SQL.

Derived series (rolling averages, running totals, YoY, targets) are window functions or extra columns, plotted as another `y` or a `layers:` entry. The delivered page is static: never caption anything as available "on hover". There is no annotation mark: label points with a `scatter` layer whose `style.marks.point.labels` is `{visible: true, field: <note column>}` (`''` where unlabeled).

## Variables

A variable is an input on the page (a dropdown, slider or date picker) whose value a query reads by bare name: `{{ region }}`, never `variables.region`.

`variables.<name>`:
- `options` ({static, query, column, label_column}): Where the selectable values come from: a written-out list or a query
- `default` (any): Value used when no URL param or --var sets one
- `label`: Caption naming what this input sets
- `required` (bool, default false): When True, a value must be provided before queries execute
- `min` (int|number): Minimum value for number, slider and range inputs
- `max` (int|number): Maximum value for number, slider and range inputs
- `step` (int|number): Step size for number, slider and range inputs
- `data_type` (string|number|date|boolean|array): The type of the values a select, radio or multiselect sends back
- `input` (auto|select|multiselect|input|text|number|textarea|slider|range|date|datepicker|daterange|checkbox|radio, default auto): Control type. `auto` (the default) picks one from `options` and `data_type`: omit it except for `daterange`
Also exist: `notes`, `placeholder`, `visible`, `enabled`, `column`, `query`, `operator`.

`options`:
- `static` (list of text|list of int|number): Option values written out in place, all strings or all numbers
- `query`: Query name whose result rows provide option values
- `column`: Column in the query result to use as option values
- `label_column`: Query column shown as the option text, apart from the value

## Layout

A `rows` or `cols` entry may be an inline board with its own `title`, `variables`, `queries`, `charts` and `style`. Every name in `rows`, `cols` or `grid` is a key under `charts:`, spelled the same.

`grid`:
- `columns` (int, default 24): Number of grid columns (default: 24)
- `items` (list of mapping): Cells of this grid, each pairing content with its placement

`grid.items[]`:
- `item` (text|mapping|map of mapping): Chart name or inline chart/board definition to place in this grid cell
- `col` (int): Column position (0-indexed)
- `row` (int): Row position (0-indexed)
- `col_span` (int): Number of columns to span (width in grid units)
- `row_span` (int): Number of rows to span (height in grid units)
Also exist: `width`, `height`, `notes`.

## Formats

Built-in names, on 1234567.891 and 0.2534: `currency` $1.23M, `currency_whole` $1,234,568, `currency_full` $1,234,567.89, `currency_compact` $1.2M, `compact` 1.2M, `number` 1.23M, `number_full` 1,234,567.89, `integer` 1,234,568, `id` 1234568, `delta` +1,234,568, `year` 2025, `percent` 25.3%, `percent_whole` 25%, `percent_delta` +25.3%. Also exist: `percent_number`, `percent_number_delta`, `percentage_points_delta`, `duration`, `bytes`, `latitude`, `longitude`, `rating`. Date names: `date_short`, `time_short`, `date_year`, `date_quarter`, `date_month`, `date_week`. A `date_<grain>` name is the engine's own axis label, never a format you write: a date in a table, KPI or axis takes a strftime spec such as `"%b %Y"` or `"%Y"`.

## Style

Keys of `style`: `frame`, `background`, `accent`, `muted`, `font`, `border`, `box_shadow`, `opacity`, `title`, `text`, `placeholder`, `charts`, `layout`, `variables`, `footer`, `timestamp`, `formats`, `fonts`, `palettes`, `tones`, `roles`, `padding`, `margin`, `gap`.

## Themes and dark variants

Themes, `extends`, palettes, color tokens, dark variants. Single overrides on one board or chart are `style:`.

### Dark variant

Keep canvas and ink colors out of the base board, and make the variant a second file holding only `extends: [./base.yml, neon]` and no `rows:` (rows would append and paint every chart twice). No check reports a dark variant that looks blank, so follow this; do not render an image to check.

### Built-in themes

- `clarity`: light canvas `dbt-grays.white`, categorical `editorial-10`, `fonts.heading.family` 'Source Serif 4', Georgia, 'Times New Roman', serif
- `paper`: light canvas `dbt-creams.canvas`, categorical `editorial-10`, `fonts.heading.family` 'Source Serif 4', Georgia, 'Times New Roman', serif
- `vivid`: light canvas `dbt-grays.white`, categorical `vivid-10`, `fonts.heading.family` 'Inter Variable', Inter, system-ui, sans-serif
- `neon`: dark canvas `dbt-grays.void`, categorical `vivid-10`, `fonts.heading.family` 'Inter Variable', Inter, system-ui, sans-serif
- `stark`: light canvas `dbt-grays.white`, categorical `vivid-10`, `fonts.heading.family` 'Inter Variable', Inter, system-ui, sans-serif

`board root or nested board`:
- `extends` (text|clarity|neon|paper|stark|vivid): Themes or board files to inherit, low to high priority. A bare name is a built-in theme; write `./` before a file path (`./_brand.yml`). One name or a list: `extends: neon`, `extends: [./base.yml, neon]`. The board's own explicit style, a background included, beats every extended theme, so a dark variant of a base that sets a background restates that background in the variant

### Palettes

- Categorical, with how many colors each holds (more categories on one chart than that is `ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED`): `category-6-tonal-blue` (6), `category-6-tonal-brown` (6), `category-6-tonal-green` (6), `category-6-tonal-orange` (6), `category-6-tonal-purple` (6), `editorial-10` (10), `hero-6` (6), `tableau` (10), `vivid-10` (10).
- Sequential: `dbt-seq-amber`, `dbt-seq-blue`, `dbt-seq-brown`, `dbt-seq-gray`, `dbt-seq-green`, `dbt-seq-purple`, `dbt-seq-rust`, `dbt-seq-teal` (each also as `<name>-dark`).
- Diverging: `dbt-div-blue-red`, `dbt-div-coolwarm`, `dbt-div-crimson-green`, `dbt-div-orange-teal`, `dbt-div-sunset` (each also as `<name>-dark`).
- Tones: `info`, `negative`, `positive`, `warning`.
- Scaffolds `dbt-creams`, `dbt-grays`; aliases of `dbt-grays`: `canvas`, `surface-subtle`, `grid`, `border`, `grid-emphasis`, `disabled`, `subtitle`, `muted`, `inactive`, `heading`, `ink`, `void`, `white`, `black`, `fill-muted`, `separator`, `spark-empty`, `accent`, `placeholder`.

`style`:
- `palettes` (map of name): Theme palette role assignments: role name to a built-in palette name, never a color. Custom colors go in ``charts.color.categorical.palette: [hex, ...]``. Default seed: chrome, info, negative, positive, warning, category, sequence, diverge

### Theme errors

- `ERR-EXTENDS-UNRESOLVED`: An `extends:` entry names neither a theme nor a board.
- `ERR-COLOR-TOKEN`: A style color token matches no palette or theme role.
- `ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED`: An authored category_colors field outgrows the palette's swatch count.

## Errors

- `ERR-YAML-SYNTAX`: A board file or an `extends:` file is not valid YAML.
- `ERR-BAR-Y-NOT-NUMERIC`: A bar chart's `y:` column contains non-numeric data.
- `WARN-MISSING-JOIN-PREDICATE`: A CROSS JOIN or constant-predicate join between two many-row sources. Fix: Add an ON clause that relates the tables, or join a source that returns one row (an aggregate with no GROUP BY, or LIMIT 1). Intentional? Silence it with `ignore: [WARN-MISSING-JOIN-PREDICATE]` on the query.
- `WARN-DATE-AS-TEXT`: A charted x column is a date the query turned into text. Fix: Return the date itself (with date_trunc for a grain) instead of formatting it; the axis labels dates by grain. For a month-of-year or day-of-week category, return the month or weekday name.
- `WARN-REDUNDANT-ENCODING`: One query column is bound to two or more visual channels of the same chart. Fix: Remove one of the channel bindings, or use different fields for each channel to encode distinct dimensions.
- `WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER`: A field named like money or a percentage has no fitting format.
- `ERR-PERCENT-RANGE`: A percent format got a 0-100 value; it expects a 0-1 ratio.
- `ERR-LABEL-FORMAT-AXIS-MISMATCH`: A non-time format is authored on an axis holding category or date tick labels.
- `WARN-TABLE-CRAMPED`: A table still fits its box widthwise but only because the renderer degraded it.

## Finish

For a first build and for every change request alike:

1. Write all boards first (dark variants, and any notes or run file asked for).
2. Check each board you write with `render_board(yaml_content=<yaml>)`: it runs the board's queries and returns what each chart draws and the board's problems. Fix everything it reports in one edit; run it again until it reports no errors. A warning is advice: act only when its text shows it harms the reader. An edit that applied needs no re-read; the check reports problems by line.
3. A card under about 250px wide (a many-column slot) draws no marks: `ERR-CHART-PAINTED-NO-MARKS`; widen the slot or use fewer columns. `WARN-TABLE-CRAMPED`: set the board root's `style.frame.width` to the width it suggests (a table has no `frame`).
4. A board with no errors is done: no curl, no reopening a board, no re-running queries, no edits, notes included.

Hand over the board YAML and the preview URL `render_board` returned for it, if it returned one (never a server root), plus a line on what the data says and what you assumed.
