# dbt charts YAML Schema Reference

Fields are optional unless they appear under a **Required** label.

<a id="board"></a>
## Board
AuthoredBoard definition from YAML.

| Field | Type | Description |
|-------|------|-------------|
| `title` | str | Heading shown at the top of the board. |
| `notes` | str | Prose summary of what this board covers; read by AI search and board listings. |
| `tags` | list[str] | Keywords for grouping and searching boards. |
| `aliases` | list[str] | Additional URLs that redirect to this board's canonical file-path URL. Each entry must be absolute (leading /). Requests to these URLs are redirected (302) to the board's real path, query string preserved. Valid on .yml, .yaml, .md, and folder index.* boards. |
| `_schema_version` | str | The latest released dbt charts YAML schema version this file was last migrated to, written by `dct migrate` only -- never hand-author this. Informational: nothing reads it back when your board loads, and it is not a validated guarantee about the file's actual grammar (a hand-edit after migration can make it stale). YAML key: _schema_version. |
| `text` | str | Markdown text content for text-only sections. |
| `html_policy` | enum: "none", "safe-subset", "trusted-raw" | HTML rendering policy for the board's body text. One of: "none" (default): HTML is escaped and rendered as plain markdown; "safe-subset": reserved for a parser-checked allowlist (not yet enforced; currently renders as none); "trusted-raw": raw HTML via foreignObject. TRUSTED-CONTENT ONLY: this is NOT a security sandbox. &lt;script&gt;/event-handlers are stripped as a best-effort guard, not a guarantee. Enable only on first-party boards you fully control. |
| `source` | str | Default source name for all queries in this board. Inheritable via meta.yml cascade. |
| `cache` | [Cache](#cache) | Cache policy for every query in this dashboard, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts the whole dashboard out. Inheritable via the meta.yml cascade. |
| `incremental` | str \| const: false | Default watermark column for incremental refresh: queries in this board fetch only new rows since the last run and merge them with the cached result, keyed on this column. Queries inherit this value and may override it with their own incremental: setting. Set to false on a nested board to opt out of a parent's incremental setting. |
| `variables` | dict[str, [Variable](#variable) \| str \| [VariableRef](#variableref)] | Named inputs that parameterize queries; each renders as a control unless it sets visible: false. |
| `queries` | dict[str, str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref)] | Named result sets the charts draw from (SQL, CSV, HTTP, and more). |
| `charts` | dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| str \| [ChartRef](#chartref)] | Named chart definitions. When no explicit layout is present, charts render as an implicit row layout in authored order. |
| `rows` | list[str \| [Board](#board) \| [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart)]] | Vertical stack layout: list of chart names or inline chart/board definitions. |
| `cols` | list[str \| [Board](#board) \| [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart)]] | Horizontal layout: list of chart names or inline chart/board definitions. |
| `grid` | [GridLayout](#gridlayout) | CSS-grid style layout with explicit row/column placement. |
| `tabs` | [TabLayout](#tablayout) | Tabbed navigation layout where each tab contains its own layout. |
| `card_gap` | bool | When True, adds gap between cards. Default: cards are edge-to-edge (0 gap). |
| `chart_focus` | str | Render only this named chart with its dependent variables (useful for embedding or SVG export). |
| `details` | [BoardDetails](#boarddetails) | Collapsible section metadata. String shorthand: details: 'text' → BoardDetails(summary='text'). Block form: details: {summary: ..., expanded_title: ..., expanded: false}. |
| `id` | str | Explicit ID for this board. Auto-generated from filename if omitted. |
| `style` | [Style](#style) | Appearance overrides for this board (background, border, and more). Most fields this board or an ancestor board explicitly authors cascade to nested child boards. Per-board fields (frame, layout, gap, margin, padding): a nested board that authors any style of its own resolves these against its own theme, never an ancestor's. Root-board-only fields (footer, timestamp): a nested board never draws its own footer or timestamp line, so these never reach it either. |
| `width` | str \| int | Width when nested (e.g., '50%', '400px', or an integer in pixels). On the root board there is no parent to place it into, so it instead sets the board's own width (equivalent to 'style.frame.width'); percentages are rejected there since there's nothing to size relative to. |
| `height` | str \| int | Height when nested (e.g., '300px' or an integer in pixels). |
| `visible` | bool \| str \| [SingleRowBoolProbe](#singlerowboolprobe) | Controls whether this layout item is rendered. Accepts a bool, variable name, Jinja expression, or {query, column} probe. |
| `extends` | str \| list[str] \| enum: "clarity", "neon", "paper", "stark", "vivid" | Board name(s) or relative path(s) this board inherits from, low to high priority. A built-in theme name resolves it directly. |
| `auto_link` | bool | When True, table charts with no explicit link: automatically link each row to its canonical /data/&lt;source&gt;/&lt;schema&gt;/&lt;table&gt;/detail/ page. Default off. An explicit link: always wins; set link: false on a chart to suppress its automatic link. |
| `theme` | enum: "clarity", "neon", "paper", "stark", "vivid" | Built-in theme name; shorthand for `extends: &lt;name&gt;`. |

<a id="cache"></a>
## Cache
Cache policy at one scope: cache: 1h / forever / true / false (the only opt-out).

| Field | Type | Description |
|-------|------|-------------|
| `ttl` | str \| const: "forever" | Expire cached results after this wall-clock age (lazy: the next read recomputes). Short-duration string: units s/m/h/d/w, m = minutes, compound allowed ('1h30m'). Omit to inherit from the parent scope; write 'forever' to override an inherited ttl with never-auto-expire (manual refresh always remains available). |

<a id="variable"></a>
## Variable
Variable definition from YAML.

| Field | Type | Description |
|-------|------|-------------|
| `input` | enum: "auto", "select", "multiselect", "input", "text", "number", "textarea", "slider", "range", "date", "datepicker", "daterange", "checkbox", "radio" | UI control type (select, multiselect, slider, daterange, etc.). 'auto' detects from options. |
| `label` | str | Caption naming what this input sets. |
| `notes` | str | Help text for this input, carried to the host rather than drawn on the board. |
| `default` | Any | Value the variable takes when neither a URL param nor --var supplies one. |
| `placeholder` | str | Hint text shown inside the input while it is empty. |
| `required` | bool | When True, a value must be provided before queries execute. |
| `visible` | bool | When False, the variable is not rendered in the UI but can still be set via URL params. |
| `enabled` | bool \| str \| [SingleRowBoolProbe](#singlerowboolprobe) | Enable this control. Accepts: static bool; a variable name or Jinja boolean expression string (no {{ }} required, bare names auto-wrap); or a {query, column} form that reads a single boolean cell from a named query. None = enabled. Absent variable in a string expression raises (use a default). |
| `column` | str | Table column to draw option values from, as 'table.column'. The table may be schema-qualified ('schema.table.column') when it is not in the connection's default schema. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) | Query name or inline query definition for populating options. |
| `options` | [VariableOptions](#variableoptions) | Where the selectable values come from: a written-out list or a query. |
| `data_type` | enum: "string", "number", "date", "boolean", "array" | The type of the values a select, radio or multiselect sends back. 'number', 'date' and 'boolean' convert the value before it reaches SQL; needed when the options come from a query, since a static numeric list already implies 'number'. 'string' and 'array' leave the value as sent. |
| `min` | int \| float | Minimum value for number, slider and range inputs. |
| `max` | int \| float | Maximum value for number, slider and range inputs. |
| `step` | int \| float | Step size for number, slider and range inputs. |
| `operator` | str | SQL operator used when generating filter expressions (e.g., '=', 'IN', 'LIKE'). |

<a id="variableref"></a>
## VariableRef
Cross-file variable reference. Bare string is coerced automatically.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `ref` | str | Reference path: '&lt;file&gt;.variables.&lt;name&gt;'. May start with one or more '../' segments to reach a sibling directory. A bare string value is coerced automatically. |

<a id="sqlquery"></a>
## SqlQuery
Raw SQL query: the default query type.

| Field | Type | Description |
|-------|------|-------------|
| `source` | str \| dict[str, Any] | Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing dict (`{type: postgres, ...}`) is rejected at compile time; reference a named source instead. |
| `notes` | str | Prose summary of what this query returns, passed along with its results to tooling. |
| `ignore` | list[str] | Diagnostic codes to suppress for this query (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION']). |
| `cache` | [Cache](#cache) | Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references. |
| `incremental` | str \| const: false | Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted. |
| `type` | const: "sql" |  |
| `sql` | str | The statement to run, with Jinja2 over board variables, other queries, and the filter helpers. |
| `setup_sql` | str | Non-nestable SQL preamble executed before the main query (e.g., CREATE TEMP FUNCTION). |
| `target` | str | dbt target name for queries against a dbt_profile source (defaults to 'dev'). |

<a id="httpquery"></a>
## HttpQuery
REST API query.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | HTTP endpoint URL for REST API queries. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `source` | str \| dict[str, Any] | Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing dict (`{type: postgres, ...}`) is rejected at compile time; reference a named source instead. |
| `notes` | str | Prose summary of what this query returns, passed along with its results to tooling. |
| `ignore` | list[str] | Diagnostic codes to suppress for this query (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION']). |
| `cache` | [Cache](#cache) | Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references. |
| `incremental` | str \| const: false | Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted. |
| `type` | const: "http" |  |
| `method` | enum: "GET", "POST", "PUT", "DELETE", "PATCH" | Verb the request is sent with (GET, POST, PUT, DELETE, PATCH). |
| `headers` | dict[str, str] | Header lines sent with the request, such as auth and content type. |
| `params` | dict[str, Any] | Values appended to the URL after the '?'. |
| `body` | dict[str, Any] \| str | Payload sent with the request, on POST, PUT, and PATCH. |
| `limit` | int | Maximum number of rows returned. |
| `json_path` | str | JSONPath expression to extract tabular data from the HTTP response. |

<a id="valuesquery"></a>
## ValuesQuery
Inline data authored as a list of row mappings.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `rows` | list[dict[str, Any]] | Inline data rows (list of row dicts). Use the compact columns-and-values form instead when row values are positional. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `source` | str \| dict[str, Any] | Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing dict (`{type: postgres, ...}`) is rejected at compile time; reference a named source instead. |
| `notes` | str | Prose summary of what this query returns, passed along with its results to tooling. |
| `ignore` | list[str] | Diagnostic codes to suppress for this query (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION']). |
| `cache` | [Cache](#cache) | Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references. |
| `incremental` | str \| const: false | Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted. |
| `type` | const: "values" |  |

<a id="compactvaluesquery"></a>
## CompactValuesQuery
Inline data authored as column names plus positional row values.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `columns` | list[str] | Column names for the compact syntax. Every compact values query also requires values. |
| `values` | list[list[Any]] | Inline row-oriented data (list of lists) for the compact syntax. Every compact values query also requires columns. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `source` | str \| dict[str, Any] | Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing dict (`{type: postgres, ...}`) is rejected at compile time; reference a named source instead. |
| `notes` | str | Prose summary of what this query returns, passed along with its results to tooling. |
| `ignore` | list[str] | Diagnostic codes to suppress for this query (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION']). |
| `cache` | [Cache](#cache) | Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references. |
| `incremental` | str \| const: false | Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted. |
| `type` | const: "values" |  |

<a id="schemaquery"></a>
## SchemaQuery
dbt source schema query.

| Field | Type | Description |
|-------|------|-------------|
| `source` | str \| dict[str, Any] | Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing dict (`{type: postgres, ...}`) is rejected at compile time; reference a named source instead. |
| `notes` | str | Prose summary of what this query returns, passed along with its results to tooling. |
| `ignore` | list[str] | Diagnostic codes to suppress for this query (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION']). |
| `cache` | [Cache](#cache) | Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references. |
| `incremental` | str \| const: false | Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted. |
| `type` | const: "schema" | Never inferred; write `type: schema` explicitly. |
| `schema` | str | Which schema to inspect; lists every schema in the source if omitted (YAML key: schema). |
| `table` | str | Which table to inspect; lists the schema's tables if omitted. |
| `column` | str | Which column to profile; profiles every column in the table if omitted. |
| `fields` | list[str] | Project the result rows to exactly these keys, in this order: the schema-query counterpart of a SQL SELECT list. A projected key a row lacks yields null. Omit to return every key each row carries. |

<a id="queryref"></a>
## QueryRef
Cross-file query reference. Bare string is coerced automatically.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `ref` | str | Reference path: '&lt;file&gt;.queries.&lt;name&gt;'. May start with one or more '../' segments to reach a sibling directory. A bare string value is coerced automatically. |

<a id="barchart"></a>
## BarChart
Authored patch for bar and histogram charts; histogram adds automatic x binning.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum: "bar", "histogram" | Selects the chart family. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | X-axis column name from the query result. |
| `y` | str \| list[str] | Y-axis column name(s). Accepts a single column or list for multi-series charts. |
| `x_label` | str | Title for the X axis, replacing the one derived from the x column's name. |
| `y_label` | str | Title for the Y axis, replacing the one the chart derives on its own. |
| `color` | str | Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only. |
| `sort` | [ChartSort](#chartsort) | Which column orders the marks, and in which direction (asc/desc). |
| `multiples` | [MultiplesConfig](#multiplesconfig) | Partition this chart into small multiples by a `rows` column (vertical stack), a `columns` column (side by side), or both (grid). Panels share one measure scale by default. |
| `support_table` | list[str \| ChartSupportTableSource \| ChartSupportTableAggregate \| ChartSupportTablePerSeries] \| [ChartSupportTable](#chartsupporttable) | Optional mini data-grid attached below/above the chart. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `style` | [BarChartStyle](#barchartstyle) | Appearance overrides for this chart alone. |
| `layers` | list[[BarLayer](#barlayer) \| [LineLayer](#linelayer) \| [AreaLayer](#arealayer) \| [ScatterLayer](#scatterlayer)] | Extra marks drawn over this chart, each with its own type and columns. Not supported when type: histogram: a histogram bins x and aggregates to a count, so there is no shared y measure for an overlay to plot against. |

<a id="linechart"></a>
## LineChart
Authored patch for line charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "line" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | X-axis column name from the query result. |
| `y` | str \| list[str] | Y-axis column name(s). Accepts a single column or list for multi-series charts. |
| `x_label` | str | Title for the X axis, replacing the one derived from the x column's name. |
| `y_label` | str | Title for the Y axis, replacing the one the chart derives on its own. |
| `color` | str | Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only. |
| `sort` | [ChartSort](#chartsort) | Which column orders the marks, and in which direction (asc/desc). |
| `multiples` | [MultiplesConfig](#multiplesconfig) | Partition this chart into small multiples by a `rows` column (vertical stack), a `columns` column (side by side), or both (grid). Panels share one measure scale by default. |
| `support_table` | list[str \| ChartSupportTableSource \| ChartSupportTableAggregate \| ChartSupportTablePerSeries] \| [ChartSupportTable](#chartsupporttable) | Optional mini data-grid attached below/above the chart. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `style` | [LineChartStyle](#linechartstyle) | Appearance overrides for this chart alone. |
| `layers` | list[[BarLayer](#barlayer) \| [LineLayer](#linelayer) \| [AreaLayer](#arealayer) \| [ScatterLayer](#scatterlayer)] | Extra marks drawn over this chart, each with its own type and columns. |

<a id="areachart"></a>
## AreaChart
Authored patch for area charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "area" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | X-axis column name from the query result. |
| `y` | str \| list[str] | Y-axis column name(s). Accepts a single column or list for multi-series charts. |
| `x_label` | str | Title for the X axis, replacing the one derived from the x column's name. |
| `y_label` | str | Title for the Y axis, replacing the one the chart derives on its own. |
| `color` | str | Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only. |
| `sort` | [ChartSort](#chartsort) | Which column orders the marks, and in which direction (asc/desc). |
| `multiples` | [MultiplesConfig](#multiplesconfig) | Partition this chart into small multiples by a `rows` column (vertical stack), a `columns` column (side by side), or both (grid). Panels share one measure scale by default. |
| `support_table` | list[str \| ChartSupportTableSource \| ChartSupportTableAggregate \| ChartSupportTablePerSeries] \| [ChartSupportTable](#chartsupporttable) | Optional mini data-grid attached below/above the chart. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `style` | [AreaChartStyle](#areachartstyle) | Appearance overrides for this chart alone. |
| `layers` | list[[BarLayer](#barlayer) \| [LineLayer](#linelayer) \| [AreaLayer](#arealayer) \| [ScatterLayer](#scatterlayer)] | Extra marks drawn over this chart, each with its own type and columns. |

<a id="scatterchart"></a>
## ScatterChart
Authored patch for scatter charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "scatter" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | X-axis column name from the query result. |
| `y` | str \| list[str] | Y-axis column name(s). Accepts a single column or list for multi-series charts. |
| `x_label` | str | Title for the X axis, replacing the one derived from the x column's name. |
| `y_label` | str | Title for the Y axis, replacing the one the chart derives on its own. |
| `color` | str | Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only. |
| `sort` | [ChartSort](#chartsort) | Which column orders the marks, and in which direction (asc/desc). |
| `multiples` | [MultiplesConfig](#multiplesconfig) | Partition this chart into small multiples by a `rows` column (vertical stack), a `columns` column (side by side), or both (grid). Panels share one measure scale by default. |
| `support_table` | list[str \| ChartSupportTableSource \| ChartSupportTableAggregate \| ChartSupportTablePerSeries] \| [ChartSupportTable](#chartsupporttable) | Optional mini data-grid attached below/above the chart. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `size` | str | Column used to size-encode data points (quantitative). |
| `shape` | str | Column used to shape-encode data points (categorical). |
| `style` | [ScatterChartStyle](#scatterchartstyle) | Appearance overrides for this chart alone. |
| `layers` | list[[BarLayer](#barlayer) \| [LineLayer](#linelayer) \| [AreaLayer](#arealayer) \| [ScatterLayer](#scatterlayer)] | Extra marks drawn over this chart, each with its own type and columns. |

<a id="heatmapchart"></a>
## HeatmapChart
Authored patch for heatmap charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "heatmap" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | X-axis column name from the query result. |
| `y` | str \| list[str] | Y-axis column name(s). Accepts a single column or list for multi-series charts. |
| `x_label` | str | Title for the X axis, replacing the one derived from the x column's name. |
| `y_label` | str | Title for the Y axis, replacing the one the chart derives on its own. |
| `color` | str | Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only. |
| `sort` | [ChartSort](#chartsort) | Which column orders the marks, and in which direction (asc/desc). |
| `multiples` | [MultiplesConfig](#multiplesconfig) | Partition this chart into small multiples by a `rows` column (vertical stack), a `columns` column (side by side), or both (grid). Panels share one measure scale by default. |
| `support_table` | list[str \| ChartSupportTableSource \| ChartSupportTableAggregate \| ChartSupportTablePerSeries] \| [ChartSupportTable](#chartsupporttable) | Optional mini data-grid attached below/above the chart. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. Valid on cartesian chart families (area, bar, heatmap, histogram, line, scatter), pie/donut, and geo families (geoshape, map, point_map, bubble_map). Other chart families use renderer-owned or layout-owned sizing contracts. |
| `style` | [HeatmapChartStyle](#heatmapchartstyle) | Appearance overrides for this chart alone. |

<a id="piechart"></a>
## PieChart
Authored patch for pie and donut charts; donut defaults `style.inner_radius` to 0.6.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum: "pie", "donut" | Selects the chart family. |
| `theta` | str | Column for angular encoding in pie (arc) charts. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `color` | str | Column naming each wedge, giving it its own hue. Bare column name only. |
| `total` | [ChartTotal](#charttotal) | Sum of the slice values, drawn in the donut hole. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. |
| `style` | [PieChartStyle](#piechartstyle) | Appearance overrides for this chart alone. |

<a id="kpichart"></a>
## KpiChart
Authored patch for KPI (key performance indicator) charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "kpi" |  |
| `value` | str | Column reference (string column name) for the headline number/text. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `conditional_formatting` | dict[str, [FieldConditionalFormatting](#fieldconditionalformatting)] | Discrete rule-driven style overrides indexed by column name. Available on type: table and type: kpi only. |
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `label` | str | Caption naming what the headline value measures; `variant` decides where it sits. |
| `variant` | enum: "stacked", "inline", "compact" | Layout variant. 'stacked' (default) shows value, label, and support on three vertical lines. 'inline' lays value, label, and support out on a single baseline-aligned row. 'compact' is 2-column: big value on the left, up to two stacked lines on the right with the bottom line sharing baseline with the value; a lone `support` block splits across the two right-column lines. |
| `support` | [KpiSupportConfig](#kpisupportconfig) | Secondary block, a delta, comparison, or note; `variant` decides where it sits. |
| `style` | [KpiChartStyle](#kpichartstyle) | Appearance overrides for this chart alone. |
| `background` | str \| dict[str, Any] | Gradient background channel, {column, scale} shape. Paints the card background by the value's position in the scale. |

<a id="tablechart"></a>
## TableChart
Authored patch for table charts.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "table" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `conditional_formatting` | dict[str, [FieldConditionalFormatting](#fieldconditionalformatting)] | Discrete rule-driven style overrides indexed by column name. Available on type: table and type: kpi only. |
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `style` | [TableChartStyle](#tablechartstyle) | Appearance overrides for this chart alone. |
| `rows` | list[str] | Fields whose distinct values form the row dimension of a pivot cross-tab. Each string is a column name from the query result. Omit for flat (non-pivot) tables. |
| `columns` | list[str] | Fields whose distinct values become column headers in a pivot cross-tab. Multiple fields create a nested multi-dimension pivot (outer → inner). Omit for flat tables. |
| `values` | list[str] | Measure fields that fill pivot cells. Each string is a column name from the query result. When omitted, all query columns not claimed by rows or columns are used. |

<a id="pointmapchart"></a>
## PointMapChart
Authored patch for point_map and bubble_map charts; the two type spellings are synonyms.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum: "point_map", "bubble_map" | Selects the chart family. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `projection` | str \| Projection | Map projection name or Vega-Lite projection config. |
| `color` | str | Column carried on the color channel: fill for geoshape regions, hue for point-map points. Bare column name only. |
| `geo` | str \| dict[str, Any] | Named GeoJSON boundary source, or inline GeoJSON spec, for geoshape charts. |
| `geo_source` | str | Named geographic data source for loading GeoJSON boundaries. |
| `lookup` | str | Data column to join against geographic data (map join key). |
| `value` | str | Data column mapped to the fill color on geoshape, taking precedence over `color:` when both are set. Ignored on point_map and bubble_map. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. |
| `latitude` | str | Column containing latitude values for point/bubble maps. |
| `longitude` | str | Column containing longitude values for point/bubble maps. |
| `size` | str | Quantitative column that scales point area. Mutually exclusive with `collapse`. |
| `collapse` | bool | Collapse marks sharing an exact latitude/longitude into one mark sized by count. Mutually exclusive with `size` and with any color channel. |
| `basemap` | [BasemapConfig](#basemapconfig) | Styled geographic background layer. |
| `style` | [PointMapChartStyle](#pointmapchartstyle) | Appearance overrides for this chart alone. |

<a id="geoshapechart"></a>
## GeoshapeChart
Authored patch for map and geoshape charts; the two type spellings are synonyms.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum: "map", "geoshape" | Selects the chart family. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `projection` | str \| Projection | Map projection name or Vega-Lite projection config. |
| `color` | str | Column carried on the color channel: fill for geoshape regions, hue for point-map points. Bare column name only. |
| `geo` | str \| dict[str, Any] | Named GeoJSON boundary source, or inline GeoJSON spec, for geoshape charts. |
| `geo_source` | str | Named geographic data source for loading GeoJSON boundaries. |
| `lookup` | str | Data column to join against geographic data (map join key). |
| `value` | str | Data column mapped to the fill color on geoshape, taking precedence over `color:` when both are set. Ignored on point_map and bubble_map. |
| `height` | int \| float | Explicit chart height in pixels. Positive number only. |
| `width` | int \| float | Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot. |
| `style` | [GeoshapeChartStyle](#geoshapechartstyle) | Appearance overrides for this chart alone. |

<a id="calloutchart"></a>
## CalloutChart
Static callout/message chart. Minimal: no chrome, no styling, no query.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "callout" |  |
| `message` | str | Body text the callout displays. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `title` | str | Optional chart title shown above the message. |
| `style` | [CalloutChartStyle](#calloutchartstyle) | Appearance overrides for this chart alone (tone). |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |

<a id="sparkbarchart"></a>
## SparkBarChart
Authored patch for spark_bar charts (compact horizontal bars).

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "spark_bar" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position. |
| `notes` | str | Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels. |
| `query` | str \| [SqlQuery](#sqlquery) \| [HttpQuery](#httpquery) \| [ValuesQuery](#valuesquery) \| [CompactValuesQuery](#compactvaluesquery) \| [SchemaQuery](#schemaquery) \| [QueryRef](#queryref) | Where this chart reads its data: a named query, an inline query block, or a SQL string. |
| `link` | str \| const: false | Click-through URL template for drill-down links. Set to false to suppress this chart's automatic link on a board with auto_link: true; table column links are unaffected. |
| `warnings_ignore` | list[str] | Codes of render warnings to suppress for this chart. |
| `title` | str | Heading naming what the chart shows. Rejected on type: kpi. |
| `subtitle` | str | Supporting text beneath the chart title. |
| `x` | str | Bar-magnitude (numeric) column name. |
| `y` | str \| list[str] | Bar-label (category) column name(s). |
| `style` | [SparkBarChartStyle](#sparkbarchartstyle) | Appearance overrides for this chart alone. |

<a id="chartref"></a>
## ChartRef
Cross-file chart reference. Bare string is coerced automatically.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `ref` | str | Reference path: '&lt;file&gt;.charts.&lt;name&gt;'. May start with one or more '../' segments to reach a sibling directory. A bare string value is coerced automatically. |

<a id="gridlayout"></a>
## GridLayout
Grid layout configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `items` | list[[GridItem](#griditem)] | Cells of this grid, each pairing content with its placement. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `columns` | int | Number of grid columns (default: 24). |

<a id="tablayout"></a>
## TabLayout
Tab layout configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `items` | list[[TabItem](#tabitem)] | Tabs in display order. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Variable name and URL param base for tab selection (auto-generated if omitted). |
| `position` | enum: "top", "left" | Which edge the tab bar sits on (top or left). |
| `default` | str | Title of the tab opened on load; the first tab if omitted. |

<a id="boarddetails"></a>
## BoardDetails
Collapsible section metadata for a board.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `summary` | str | Label shown when the section is collapsed. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `expanded_title` | str | Label shown when the section is expanded. Defaults to summary. |
| `expanded` | bool | Whether the section is open by default. |

<a id="style"></a>
## Style
Authored overlay for Style: all fields optional. Adds CSS shorthand coercers.

| Field | Type | Description |
|-------|------|-------------|
| `frame` | [FrameStyle](#framestyle) | Board-level structural frame dimensions. |
| `background` | str | Working-surface background color (board and card fills). |
| `accent` | str | Accent color token cascaded to sparklines, bars, and focus rings. |
| `muted` | str | Muted secondary-text color token (KPI support rows, table and spark subtitles). |
| `font` | [RootFontStyle](#rootfontstyle) | Root font configuration including emoji mode. |
| `border` | [BorderStyle](#borderstyle) | Default border style cascaded to all chart cards. |
| `box_shadow` | str | CSS box shadow for chart cards; None means no shadow. |
| `opacity` | float | Default mark opacity (0–1). |
| `title` | [TitleStyle](#titlestyle) | Typography for every heading: board and prose titles, and chart, table, and spark object titles. |
| `text` | [TextStyle](#textstyle) | Markdown and plain text content style. |
| `placeholder` | [PlaceholderStyle](#placeholderstyle) | Appearance of the stand-in drawn on a chart with no data. |
| `charts` | [ChartsStyle](#chartsstyle) | Root of all chart-type styles and shared chart configuration. |
| `layout` | [LayoutStyle](#layoutstyle) | Spacing and arrangement inside the containers (rows, cols, grid, tabs, details). |
| `variables` | [VariablesStyle](#variablesstyle) | Variable controls chrome style. |
| `footer` | [FooterStyle](#footerstyle) | Page footer chrome visibility. |
| `timestamp` | [TimestampStyle](#timestampstyle) | Data-freshness chrome: visibility, placement, format, and font. |
| `formats` | dict[str, str] | Format alias map; None means no aliases at this cascade level. |
| `palettes` | dict[str, one of: 'category-6-tonal-blue', 'category-6-tonal-brown', 'category-6-tonal-green', 'category-6-tonal-orange', 'category-6-tonal-purple', 'dbt-creams', 'dbt-div-blue-red', 'dbt-div-blue-red-dark', 'dbt-div-coolwarm', 'dbt-div-coolwarm-dark', 'dbt-div-crimson-green', 'dbt-div-crimson-green-dark', 'dbt-div-orange-teal', 'dbt-div-orange-teal-dark', 'dbt-div-sunset', 'dbt-div-sunset-dark', 'dbt-grays', 'dbt-seq-amber', 'dbt-seq-amber-dark', 'dbt-seq-blue', 'dbt-seq-blue-dark', 'dbt-seq-brown', 'dbt-seq-brown-dark', 'dbt-seq-gray', 'dbt-seq-gray-dark', 'dbt-seq-green', 'dbt-seq-green-dark', 'dbt-seq-purple', 'dbt-seq-purple-dark', 'dbt-seq-rust', 'dbt-seq-rust-dark', 'dbt-seq-teal', 'dbt-seq-teal-dark', 'editorial-10', 'editorial-10-dark', 'editorial-10-ghost', 'editorial-10-ink', 'editorial-10-light', 'hero-6', 'info', 'negative', 'positive', 'tableau', 'vivid-10', 'vivid-10-dark', 'vivid-10-ghost', 'vivid-10-ink', 'vivid-10-light', 'warning'] | Theme palette role assignments: open dict mapping role name to palette file name. Default seed: chrome, info, negative, positive, warning, category, sequence, diverge. |
| `tones` | [KpiTonesStyle](#kpitonesstyle) | Semantic tone color palette (positive/negative/warning/info) for KPI support rows, table conditional glyphs, and spark negative_color. |
| `roles` | dict[str, str] | Optional top-level theme role aliases: bare name → role.alias. e.g. ink: chrome.heading |
| `padding` | [SpacingValues](#spacingvalues) | Per-board padding override (CSS shorthand or structured). |
| `margin` | [SpacingValues](#spacingvalues) | Per-board margin override (CSS shorthand or structured). |
| `gap` | float | Per-board gap between layout items in pixels. |

<a id="singlerowboolprobe"></a>
## SingleRowBoolProbe
Single-row boolean query probe.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | Name of the query to execute. |
| `column` | str | Column in the single result row holding the boolean value. |

<a id="variableoptions"></a>
## VariableOptions
Options configuration for variable inputs.

| Field | Type | Description |
|-------|------|-------------|
| `static` | list[str \| int \| float] | Option values written out in place, all strings or all numbers. Numeric options type the value the control sends back as a number unless data_type says otherwise. |
| `query` | str | Query name whose result rows provide option values. |
| `column` | str | Column in the query result to use as option values. |
| `label_column` | str | Column in the query result to use as display labels (separate from values). |

<a id="chartsort"></a>
## ChartSort
Chart-level sort configuration for categorical axes.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `by` | str | Column name to sort by. A category holding several rows (a color series, or a y: [...] list) is folded to one value of this column first, and on a bar that fold is the stacked total only when the chart stacks and this names its single y column; everything else, a y: [...] measure included, ranks by the smallest value the column holds in that category. Name a column that is constant within a category, or pre-aggregate in the query. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `order` | enum: "asc", "desc" | Sort direction (asc or desc). |

<a id="multiplesconfig"></a>
## MultiplesConfig
Small-multiples partition, keyed by layout direction.

| Field | Type | Description |
|-------|------|-------------|
| `rows` | str | Column whose distinct values become vertically stacked panel rows. |
| `columns` | str | Column whose distinct values become side-by-side panel columns. |
| `scale` | enum: "shared", "independent" | Measure-scale sharing across panels. 'shared' (default) makes panels visually comparable; 'independent' gives each panel its own scale. |

<a id="chartsupporttable"></a>
## ChartSupportTable
Container for a chart's support_table block.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `entries` | list[str \| [ChartSupportTableSource](#chartsupporttablesource) \| [ChartSupportTableAggregate](#chartsupporttableaggregate) \| [ChartSupportTablePerSeries](#chartsupporttableperseries)] | List of support-table entries (source, aggregate, or per-series rows). A source entry may be written as a bare column name (`revenue` in place of `{source: revenue}`), the same scalar-listable spelling `y:` uses. |

<a id="barchartstyle"></a>
## BarChartStyle
Authored overlay for BarChartStyle. Bar chart style: chart-level fields + marks sub-block.

| Field | Type | Description |
|-------|------|-------------|
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render. |
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `orientation` | enum: "horizontal", "vertical", "auto" | Preferred bar orientation; None behaves like 'auto', which picks horizontal for a categorical x and vertical for a continuous one (temporal, quantitative, or date-like). Never remaps x/y. |
| `stack` | enum: "none", "zero", "normalize", "center" | Default stack mode for bar charts; none renders side-by-side columns. |
| `overlap` | float \| enum: "auto", "none", "flush", "partial", "full" | Within-group spacing for grouped bars. Keywords: 'auto' (2 series → partial, 3+ → none), 'none' (small gap), 'flush' (bars touch), 'partial' (25% overlap), 'full' (bars coincide). Or a number as a fraction of bar width: &gt;0 overlaps, 0 touches, &lt;0 gaps; 1 is the maximum (bars fully coincide, same as 'full') and values above 1 are clamped to 1; bars never cross past each other. None uses the renderer default ('auto'). Only applies to grouped bars; setting it together with an active stack mode is an error. |
| `stack_order` | enum: "value", "data", "alphabetical" | Z-order of stacked segments. None/'value' puts the largest aggregate at baseline. 'data' follows SQL row order (orientation-stable not guaranteed). 'alphabetical' sorts by color column name. Ignored when stacking is off or no color. |
| `endpoint_labels` | [EndpointLabelsConfig](#endpointlabelsconfig) | Series names printed on stacked bars instead of in a legend. |
| `marks` | [BarChartMarksStyle](#barchartmarksstyle) | Bar-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="barlayer"></a>
## BarLayer
A bar-type layer on a cartesian chart.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "bar" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | Query name for this layer's data (overrides chart-level query). |
| `x` | str | X-axis column name for this layer. |
| `y` | str | Y-axis column name for this layer. |
| `label` | str | Name for this layer's measure wherever the layer is identified. Defaults to its y column name. |
| `color` | str | Column whose values split this layer into colored series; bare column name only. |
| `axis_y` | [LayerAxisYStyle](#layeraxisystyle) | This layer's own y axis: which side it sits on, its title, scale, ticks, grid. |
| `style` | [BarLayerStyle](#barlayerstyle) | Appearance overrides for this layer's bar marks. |

<a id="linelayer"></a>
## LineLayer
A line-type layer on a cartesian chart.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "line" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | Query name for this layer's data (overrides chart-level query). |
| `x` | str | X-axis column name for this layer. |
| `y` | str | Y-axis column name for this layer. |
| `label` | str | Name for this layer's measure wherever the layer is identified. Defaults to its y column name. |
| `color` | str | Column whose values split this layer into colored series; bare column name only. |
| `axis_y` | [LayerAxisYStyle](#layeraxisystyle) | This layer's own y axis: which side it sits on, its title, scale, ticks, grid. |
| `style` | [LineLayerStyle](#linelayerstyle) | Appearance overrides for this layer's line marks. |

<a id="arealayer"></a>
## AreaLayer
An area-type layer on a cartesian chart.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "area" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | Query name for this layer's data (overrides chart-level query). |
| `x` | str | X-axis column name for this layer. |
| `y` | str | Y-axis column name for this layer. |
| `label` | str | Name for this layer's measure wherever the layer is identified. Defaults to its y column name. |
| `color` | str | Column whose values split this layer into colored series; bare column name only. |
| `axis_y` | [LayerAxisYStyle](#layeraxisystyle) | This layer's own y axis: which side it sits on, its title, scale, ticks, grid. |
| `style` | [AreaLayerStyle](#arealayerstyle) | Appearance overrides for this layer's area marks. |

<a id="scatterlayer"></a>
## ScatterLayer
A scatter-type layer on a cartesian chart.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "scatter" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | Query name for this layer's data (overrides chart-level query). |
| `x` | str | X-axis column name for this layer. |
| `y` | str | Y-axis column name for this layer. |
| `label` | str | Name for this layer's measure wherever the layer is identified. Defaults to its y column name. |
| `color` | str | Column whose values split this layer into colored series; bare column name only. |
| `axis_y` | [LayerAxisYStyle](#layeraxisystyle) | This layer's own y axis: which side it sits on, its title, scale, ticks, grid. |
| `style` | [ScatterLayerStyle](#scatterlayerstyle) | Appearance overrides for this layer's point marks. |

<a id="linechartstyle"></a>
## LineChartStyle
Authored overlay for LineChartStyle. Line chart style: chart-level fields + marks sub-block.

| Field | Type | Description |
|-------|------|-------------|
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render. |
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `endpoint_labels` | [EndpointLabelsConfig](#endpointlabelsconfig) | Series names printed at the end of each line instead of in a legend. |
| `marks` | [LineChartMarksStyle](#linechartmarksstyle) | Line-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="areachartstyle"></a>
## AreaChartStyle
Authored overlay for AreaChartStyle. Area chart style: chart-level fields + marks sub-block.

| Field | Type | Description |
|-------|------|-------------|
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render. |
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `stack` | enum: "none", "zero", "normalize", "center" | Default stack mode for area charts: 'none', 'zero', 'normalize', or 'center'. |
| `endpoint_labels` | [EndpointLabelsConfig](#endpointlabelsconfig) | Series names printed on the bands instead of in a legend. |
| `marks` | [AreaChartMarksStyle](#areachartmarksstyle) | Area-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="scatterchartstyle"></a>
## ScatterChartStyle
Authored overlay for ScatterChartStyle. Scatter chart style: chart-level fields + marks sub-block.

| Field | Type | Description |
|-------|------|-------------|
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render. |
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `marks` | [ScatterChartMarksStyle](#scatterchartmarksstyle) | Scatter-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="heatmapchartstyle"></a>
## HeatmapChartStyle
Authored overlay for HeatmapChartStyle. Heatmap chart style.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `cell_padding` | float | Padding between heatmap cells in pixels. |
| `marks` | [HeatmapChartMarksStyle](#heatmapchartmarksstyle) | Heatmap-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="charttotal"></a>
## ChartTotal
Donut center total: auto-rendered sum at the center of a donut, with author override.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Whether to render the donut center total. Defaults True; set False to suppress the auto-rendered center on donuts whose theta values aren't a meaningful sum (e.g. pre-aggregated percentage shares). |
| `label` | str | Caption text displayed below the center total value. |

<a id="piechartstyle"></a>
## PieChartStyle
Authored overlay for PieChartStyle. Pie/donut chart style: geometry + total (flat) + marks sub-block.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Aspect ratio (width/height) of the pie chart viewport. |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `inner_radius` | float | Hole-to-disk ratio 0–1 (inner radius / outer radius). None = solid pie; `type: donut` overrides this with a chart-local 0.6 patch, beating a theme value. |
| `total` | [TotalStyle](#totalstyle) | Donut center total paint (value and label). |
| `marks` | [PieChartMarksStyle](#piechartmarksstyle) | Pie-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="fieldconditionalformatting"></a>
## FieldConditionalFormatting
Conditional formatting rules scoped to a single column.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `when` | list[[ConditionalRule](#conditionalrule)] | Ordered list of conditional rules. The first matching rule applies; a 'default: true' rule must be last. |

<a id="kpisupportconfig"></a>
## KpiSupportConfig
Support-line block authored alongside a KPI's main value.

| Field | Type | Description |
|-------|------|-------------|
| `value` | str | Column reference (string column name) for the support number/text. |
| `label` | str | Trailing explainer text rendered beside the support value. |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | How the number is written: a D3 spec, a preset name, or a format block. |
| `glyph` | str | Text shown before the value (e.g. '▲', '▼', '●'). |
| `tone` | enum: "positive", "negative", "warning", "info" | Semantic styling for the support value/glyph. |

<a id="kpichartstyle"></a>
## KpiChartStyle
Authored overlay for KpiChartStyle. Produced by cascade from theme YAML.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `font` | [FontStyle](#fontstyle) | KPI chart-level font overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `value` | [KpiValueStyle](#kpivaluestyle) | KPI headline value slot. |
| `label` | [KpiSlotStyle](#kpislotstyle) | KPI card label slot. |
| `affix` | [KpiSlotStyle](#kpislotstyle) | Currency/percent affix slot. |
| `glyph` | [KpiSlotStyle](#kpislotstyle) | Indicator glyph (▲▼●) slot. |
| `min_card_width` | float | Minimum KPI card width in pixels. |
| `default_height` | float | Default KPI card height in pixels. |
| `border` | [BorderStyle](#borderstyle) | KPI card border style. |
| `content_padding` | [SpacingValues](#spacingvalues) | Inner inset (top/right/bottom/left) for KPI card content in pixels. |
| `align` | enum: "left", "center", "right" | Horizontal alignment of the value, label, and support text within the card. |

<a id="tablechartstyle"></a>
## TableChartStyle
Authored overlay for TableChartStyle. Table chart style overrides layered on top of shared chart defaults.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Table background color; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `font` | [FontStyle](#fontstyle) | Table chart-level font overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `border` | [BorderStyle](#borderstyle) | Chart card border style. |
| `color` | [StaticGradientColorStyle](#staticgradientcolorstyle) | Table color: static text paint or gradient scale only (no categorical arm). |
| `rule` | [TableRuleStyle](#tablerulestyle) | Table rule style overrides (color). None = no override; inherits from theme. |
| `outer_padding` | float | Outer table padding in pixels. |
| `bottom_padding` | float | Extra bottom padding below the last row in pixels. |
| `column_layout` | [TableColumnsStyle](#tablecolumnsstyle) | Default column width and cell padding settings. |
| `header` | [TableHeaderStyle](#tableheaderstyle) | Table header row style. |
| `row` | [TableRowStyle](#tablerowstyle) | Table body row style. |
| `row_numbers` | [TableRowNumbersStyle](#tablerownumbersstyle) | Leading row-number column configuration. |
| `title_row` | [TableTitleStyle](#tabletitlestyle) | Title block rendered above the header row. |
| `wrap` | bool | Allow cell text wrapping; false clips to single line. |
| `pagination` | [PaginationConfig](#paginationconfig) | Client-side pagination defaults for table charts. |
| `column_defaults` | [TableColumnDefaultsConfig](#tablecolumndefaultsconfig) | Table-level column defaults applied to every column; None means no defaults authored. |
| `columns` | dict[str, [TableColumnConfig](#tablecolumnconfig)] | Per-column display configuration keyed by column name (label, format, width, etc.). |
| `header_overflow` | enum: "clip", "truncate", "wrap-two", "wrap" | Table column header text overflow mode; None inherits from theme. |
| `paginator` | [PaginatorStyle](#paginatorstyle) | Visual style for the paginator control (chevrons + page numbers). |
| `symbol_mode` | enum: "all", "anchors" | Where to show currency-prefix and magnitude/unit suffix symbols in a numeric column. 'all' shows the full formatted value on every row; 'anchors' shows it only on the first data row and summary/total rows, stripping prefix and suffix from plain middle rows so the anchors guide the reader at the top and bottom of the value field. |
| `more_rows` | [TableEdgeStyle](#tableedgestyle) | Style for the 'more rows' edge-case indicator. |
| `empty_state` | [TableEdgeStyle](#tableedgestyle) | Style for the empty-state (no data) indicator. |
| `spark` | [SparkStyle](#sparkstyle) | Inline sparkline defaults for table cells. |
| `transpose` | bool | When True, render a single wide data row as N (label, value) rows, one per column. Raises ChartDataError when data has more than one row. Used for Looker single-value summary tiles with multiple measures. |
| `text_baseline_offset` | float | Vertical offset to align SVG text baseline with cell grid in pixels. |
| `title_subtitle_gap` | float | Pure whitespace between the title's descent and the subtitle's ascent, in pixels, not a baseline-to-baseline distance. Combined with the title and subtitle font sizes to reproduce Vega-Lite's title-&gt;subtitle spacing at any font size, not one calibrated pair. Font size and color for the subtitle itself come from style.title.subtitle (the same source chart-family titles use), not a table-local constant. |

<a id="basemapconfig"></a>
## BasemapConfig
Styled geographic background layer for point_map and bubble_map charts.

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Named geographic boundary source (e.g. 'us-states') for overlay rendering. |
| `fill` | str | Fill color for geographic boundary overlay. |
| `stroke` | str | Stroke color for geographic boundary overlay. |

<a id="pointmapchartstyle"></a>
## PointMapChartStyle
Authored overlay for PointMapChartStyle. Point map chart style.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [StaticGradientColorStyle](#staticgradientcolorstyle) | Geo color: static paint or gradient scale only (no categorical arm). |
| `font` | [FontStyle](#fontstyle) | Chart-level font overrides. |
| `border` | [BorderStyle](#borderstyle) | Chart card border style. |
| `projection` | [ProjectionStyle](#projectionstyle) | Vega-Lite projection configuration for this geo family. |
| `basemap` | [BasemapStyle](#basemapstyle) | Background map layer; None source = no topo layer. |
| `marks` | [PointMapChartMarksStyle](#pointmapchartmarksstyle) | Point-map-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="geoshapechartstyle"></a>
## GeoshapeChartStyle
Authored overlay for GeoshapeChartStyle. Geoshape (choropleth) chart style.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [StaticGradientColorStyle](#staticgradientcolorstyle) | Geo color: static paint or gradient scale only (no categorical arm). |
| `font` | [FontStyle](#fontstyle) | Chart-level font overrides. |
| `border` | [BorderStyle](#borderstyle) | Chart card border style. |
| `projection` | [ProjectionStyle](#projectionstyle) | Vega-Lite projection configuration for this geo family. |
| `basemap` | [BasemapStyle](#basemapstyle) | Background map layer; None source = no topo layer. |
| `marks` | [GeoshapeChartMarksStyle](#geoshapechartmarksstyle) | Geoshape-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="calloutchartstyle"></a>
## CalloutChartStyle
Authored overlay for CalloutChartStyle. Chart-family style for ``type: callout`` charts and runtime chart-error fallback cards.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred callout width in pixels. |
| `tone` | enum: "positive", "negative", "warning", "info" | Semantic tone for palette-role lookup (info \| positive \| negative \| warning). |
| `background` | str | Callout card background color (info.bg default). |
| `border` | [BorderStyle](#borderstyle) | Callout card border style. |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `section_gap` | float | Vertical gap between callout title and message in pixels. |
| `title` | [CalloutElementStyle](#calloutelementstyle) | Callout title element style. |
| `message` | [CalloutElementStyle](#calloutelementstyle) | Callout message element style. |

<a id="sparkbarchartstyle"></a>
## SparkBarChartStyle
Authored overlay for SparkBarChartStyle. Produced by cascade from theme YAML.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred spark_bar chart width in pixels. |
| `min_width` | float | Minimum spark_bar chart width in pixels. |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `bar` | [SparkBarBarStyle](#sparkbarbarstyle) | Bar geometry (height, padding, color). |
| `label` | [SparkBarChartLabelStyle](#sparkbarchartlabelstyle) | Category-label column (visibility, reserved width). |
| `count` | [SparkBarCountStyle](#sparkbarcountstyle) | Count-value column (visibility, reserved width). |
| `max_bars` | int | Maximum number of bars to render. |
| `font` | [FontStyle](#fontstyle) | Spark_bar font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `border` | [CornerStyle](#cornerstyle) | Spark_bar outer corner rounding. |
| `subtitle` | [SubtitleStyle](#subtitlestyle) | Spark_bar subtitle font-sizing constants. |

<a id="griditem"></a>
## GridItem
Grid layout item with position and span.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `item` | str \| [Board](#board) \| [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart)] | Chart name or inline chart/board definition to place in this grid cell. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `col` | int | Column position (0-indexed). Auto-placed if omitted. |
| `row` | int | Row position (0-indexed). Auto-placed if omitted. |
| `col_span` | int | Number of columns to span (width in grid units). |
| `row_span` | int | Number of rows to span (height in grid units). |
| `width` | int | Alias for col_span (more intuitive name). |
| `height` | int | Alias for row_span (more intuitive name). |
| `notes` | str | Optional metadata for AI search. Emitted into the SVG DOM as a data-layout-notes attribute; never painted as visible pixels. |

<a id="tabitem"></a>
## TabItem
Tab layout item.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `title` | str | Tab label displayed in the tab bar. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `icon` | str | Optional icon shown in the tab (e.g., emoji or icon name). |
| `notes` | str | Optional metadata for AI search. Emitted into the SVG DOM as a data-layout-notes attribute; never painted as visible pixels. |
| `text` | str | Markdown text content shown in this tab. |
| `style` | [Style](#style) | Appearance overrides for this tab's content area. |
| `rows` | list[str \| [Board](#board) \| [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart)]] | Vertical stack layout for this tab's content. |
| `cols` | list[str \| [Board](#board) \| [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart) \| dict[str, [BarChart](#barchart) \| [LineChart](#linechart) \| [AreaChart](#areachart) \| [ScatterChart](#scatterchart) \| [HeatmapChart](#heatmapchart) \| [PieChart](#piechart) \| [KpiChart](#kpichart) \| [TableChart](#tablechart) \| [PointMapChart](#pointmapchart) \| [GeoshapeChart](#geoshapechart) \| [CalloutChart](#calloutchart) \| [SparkBarChart](#sparkbarchart)]] | Horizontal layout for this tab's content. |
| `grid` | [GridLayout](#gridlayout) | CSS-grid layout for this tab's content. |
| `tabs` | [TabLayout](#tablayout) | A further set of tabs opening inside this one. |

<a id="framestyle"></a>
## FrameStyle
Authored overlay for FrameStyle. Board-level structural dimensions. Do NOT cascade to child boards.

| Field | Type | Description |
|-------|------|-------------|
| `width` | float | Exact board width in pixels. Set it, on a board, a template it extends, or a project's meta.yml, and the board is exactly this wide; the layout distributes it. Leave it unset and the board sizes itself to its content, bounded by max_width. Rendered as an on-screen pixel size everywhere except the dct HTML page (dct serve, dct render --format html), which scales the board to its container. |
| `max_width` | float | Widest a board without an exact width may grow, in pixels. A board with no width of its own measures its charts' preferred widths and hugs them up to this bound: a single small chart stays a small card. Ignored when width is set. Themes supply the default; a project's meta.yml can lower or raise it. |
| `min_height` | float | Minimum board height in pixels. |
| `margin` | float | Board outer margin in pixels. |
| `card_padding` | float | Padding added to each card side in pixels. |
| `card_gap` | float | Gap between cards in pixels. |

<a id="rootfontstyle"></a>
## RootFontStyle
Authored overlay for RootFontStyle. Root-level font configuration: FontStyle fields plus a required emoji mode.

| Field | Type | Description |
|-------|------|-------------|
| `family` | str | Font family name (e.g., 'sans-serif', 'Roboto'). |
| `color` | str | Text color as a CSS color string. |
| `size` | float | Font size in pixels. |
| `weight` | str \| float | How heavy the type is drawn (e.g., 'bold', 400, 700). |
| `style` | enum: "normal", "italic" | Upright or slanted type (normal or italic). |
| `decoration` | enum: "none", "line-through", "underline" | Line drawn on the text (underline, line-through, or none). |
| `case` | enum: "none", "sentence", "title", "upper", "lower", "slug", "camel" | Letter-case transform applied at render time. 'title' uses Chicago/Gruber rules and preserves tokens with internal capitals (ARR, iPhone). 'sentence' uppercases only the first character. 'none' (default) emits the string without any letter-case change. |
| `line_height` | float | Line height as a unitless multiple of font size. Cascades through Style.font to all text roles that carry a FontStyle slot. Body prose defaults to 1.25; titles override tighter via their font patch in theme YAML. |
| `emoji` | enum: "monochrome", "system-default", "disabled" | Emoji rendering mode for the dashboard font stack. |

<a id="borderstyle"></a>
## BorderStyle
Box border. Does NOT cascade (ADR-003: box properties reset per level).

| Field | Type | Description |
|-------|------|-------------|
| `radius` | float | Border corner radius in pixels. |
| `color` | str | Border color (CSS color string). |
| `width` | float | Border width in pixels. |
| `dash_array` | list[float] | SVG stroke-dasharray pattern in pixels (e.g. [4, 4]). None means a solid border. |
| `line_cap` | enum: "butt", "round", "square" | How each dash's ends are finished on a dashed border. None uses the renderer default (butt). |
| `dash_offset` | float | How far into the dash pattern the line starts, in pixels. None means 0. |

<a id="titlestyle"></a>
## TitleStyle
Authored overlay for TitleStyle. Board and board titles.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Title font style overrides. Unset fields fall back to [`style.font`](#style). |
| `compact_weight` | str \| float | Object-title font weight on tiny cards. |
| `sizes` | list[float] | Font sizes for the H1–H6 heading ramp, indexed by ``board.level - 1``. Board/prose titles index this ramp directly (no width input). Object titles (chart/table/spark) combine ``width_offsets`` with a fixed object-title anchor to pick a slot. |
| `width_offsets` | [TitleWidthOffsetsStyle](#titlewidthoffsetsstyle) | Additive level offsets by card width (tiny/narrow/medium/wide). Object titles add the tier offset to a fixed anchor to pick an H slot from ``sizes``. Board/prose titles are level-only and do not consult these. |
| `min_height` | float | Minimum title row height in pixels. |
| `overflow` | enum: "clip", "truncate", "wrap-two", "wrap" | Text overflow mode (clip, truncate, wrap-two, wrap). |
| `position` | [TitlePositionStyle](#titlepositionstyle) | Vega-Lite title positioning: anchor, angle, offset, baseline. |
| `level` | int \| const: "auto" | Heading level override for board titles. ``'auto'`` (default) computes the level semantically as the count of titled ancestors. An integer value locks all titles in this board and its descendants to that H-level. |
| `subtitle` | [TitleSubtitleStyle](#titlesubtitlestyle) | Subtitle font styles. |

<a id="textstyle"></a>
## TextStyle
Authored overlay for TextStyle. Markdown / plain text content.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Text font style overrides. Unset fields fall back to [`style.font`](#style). |
| `align` | enum: "left", "center", "right" | Text alignment for the board body-text block. |
| `paragraph` | [BlockMarginStyle](#blockmarginstyle) | Spacing above/below each paragraph block (in line-height units). |
| `heading` | [BlockMarginStyle](#blockmarginstyle) | Spacing above/below heading blocks (H1–H6) in line-height units. |
| `column` | [TextColumnStyle](#textcolumnstyle) | Multi-column layout for the board body-text block. |
| `code` | [TextCodeStyle](#textcodestyle) | Inline + fenced code box styling (font, background, border). |
| `blockquote` | [TextBlockquoteStyle](#textblockquotestyle) | Blockquote box styling (font, background, border/left-rule). |
| `bold` | [TextBoldStyle](#textboldstyle) | Inline bold-run styling (weight), distinct from heading weight. |
| `rule` | [TextRuleStyle](#textrulestyle) | Markdown horizontal-rule and table-gridline color. |

<a id="placeholderstyle"></a>
## PlaceholderStyle
Authored overlay for PlaceholderStyle.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Opacity of the placeholder overlay (0–1). |
| `overlay` | [PlaceholderOverlay](#placeholderoverlay) | Overlay text and background style. |

<a id="chartsstyle"></a>
## ChartsStyle
Authored overlay for ChartsStyle. Registry of all chart-type styles plus shared chart configuration.

| Field | Type | Description |
|-------|------|-------------|
| `preferred_width` | float | Preferred chart width in pixels. |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. |
| `background` | str | Chart canvas background; None inherits from the board background via apply_inherit. Falls back to [`style.background`](#style). |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). |
| `min_height` | float | Minimum chart height in pixels. |
| `max_height` | float | Maximum chart height in pixels. |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `font` | [FontStyle](#fontstyle) | Chart-level font overrides. Unset fields fall back to [`style.font`](#style). |
| `border` | [BorderStyle](#borderstyle) | Chart card border style. |
| `category_colors` | dict[str, [CategoryColorBinding](#categorycolorbinding)] | Board-wide category→color bindings, keyed by data field name. Pins a category to one swatch across every chart on the board. |
| `tooltip` | [TooltipStyle](#tooltipstyle) | Board-wide chart tooltip style. |
| `hover_emphasis` | [HoverEmphasisStyle](#hoveremphasisstyle) | Board-wide switch for hover emphasis on charts. |
| `dashes` | list[list[int]] | Ordered list of Vega-Lite strokeDash arrays for line-family categorical encoding; None disables dash emission. |
| `default_chart_height` | float | Fallback chart height in pixels when aspect-ratio sizing is unavailable. |
| `default_table_height` | float | Placeholder table height in pixels; replaced by data-aware row-count sizing at render time. |
| `label_usable_ratio` | float | Fraction of chart width usable for axis labels (0–1); labels are tilted when full labels exceed this width. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Shared axis style applied to all axes before per-axis overrides. |
| `axis_x` | [AxisXStyle](#axisxstyle) | X-axis style overrides applied after the shared axis. Unset fields fall back to style.charts.axis. |
| `axis_y` | [AxisYStyle](#axisystyle) | Y-axis style overrides applied after the shared axis. Unset fields fall back to style.charts.axis. |
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Quantitative axis style overrides applied after axis_x/axis_y. Unset fields fall back to style.charts.axis. |
| `view` | [ViewStyle](#viewstyle) | Vega-Lite view dimensions and border. |
| `marks` | [GlobalMarksStyle](#globalmarksstyle) | Global mark defaults (tier-1 of the marks cascade). |
| `bar` | [BarChartStyle](#barchartstyle) | Bar chart style; histogram has its own block. |
| `line` | [LineChartStyle](#linechartstyle) | Line chart style. |
| `area` | [AreaChartStyle](#areachartstyle) | Area chart style. |
| `scatter` | [ScatterChartStyle](#scatterchartstyle) | Scatter chart style. |
| `histogram` | [HistogramChartStyle](#histogramchartstyle) | Histogram chart style. |
| `heatmap` | [HeatmapChartStyle](#heatmapchartstyle) | Heatmap chart style. |
| `geoshape` | [GeoshapeChartStyle](#geoshapechartstyle) | Geoshape (choropleth) chart style. |
| `point_map` | [PointMapChartStyle](#pointmapchartstyle) | Point map chart style. |
| `pie` | [PieChartStyle](#piechartstyle) | Pie/donut chart style. |
| `series_label` | [SeriesLabelStyle](#serieslabelstyle) | Shared series-label typography for endpoint and stack labels. |
| `kpi` | [KpiChartStyle](#kpichartstyle) | KPI card chart style. |
| `table` | [TableChartStyle](#tablechartstyle) | Table chart style. |
| `spark_bar` | [SparkBarChartStyle](#sparkbarchartstyle) | Spark_bar (full-chart horizontal bar) style. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Attached support_table strip style. |
| `callout` | [CalloutChartStyle](#calloutchartstyle) | Callout chart-family style for type:callout and runtime fallback cards. |

<a id="layoutstyle"></a>
## LayoutStyle
Authored overlay for LayoutStyle.

| Field | Type | Description |
|-------|------|-------------|
| `rows` | [LayoutGapStyle](#layoutgapstyle) | Gap sizing between rows. |
| `cols` | [LayoutGapStyle](#layoutgapstyle) | Gap sizing between columns. |
| `grid` | [GridLayoutStyle](#gridlayoutstyle) | Grid layout configuration. |
| `tabs` | [TabsStyle](#tabsstyle) | Tabs layout style. |
| `details` | [DetailsStyle](#detailsstyle) | Details (accordion) layout style. |

<a id="variablesstyle"></a>
## VariablesStyle
Authored overlay for VariablesStyle. Variable controls chrome styling.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the variables control panel. |
| `position` | enum: "top", "bottom", "title-inline" | Variables strip placement: stacked under the title (top/bottom) or on one horizontal band with the board title (title-inline). |
| `title_inline_band_bottom_pad` | float | Bottom padding in pixels added below the title-inline band. |
| `gap` | float | Gap between variable controls in pixels. |
| `label_position` | str | Position of labels relative to their input controls (e.g. 'left', 'top'). |
| `title_inline_title_max_width` | float | When position is title-inline: max title column width in px. 0 means no cap (title uses remaining width after reserving space for variables). |
| `font` | [FontStyle](#fontstyle) | Variables panel base font style overrides. Unset fields fall back to [`style.font`](#style). |
| `label` | [VariablesLabelStyle](#variableslabelstyle) | Variable label typography. |
| `value` | [VariablesValueStyle](#variablesvaluestyle) | Variable value typography. |
| `placeholder` | [VariablesPlaceholderStyle](#variablesplaceholderstyle) | Style for unselected/hint text in variable inputs. |
| `container_height` | float | Height of the variables panel container in pixels. |
| `border` | [BorderStyle](#borderstyle) | Variables panel border style. |
| `control_gap` | float | Gap between label and input within a single control in pixels. |
| `input` | [InputStyle](#inputstyle) | Input control style. |

<a id="footerstyle"></a>
## FooterStyle
Authored overlay for FooterStyle. Page footer chrome: visibility, attribution text, font, and rule.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the footer attribution line. |
| `text` | str | Attribution text shown in the footer. |
| `link` | str | URL the footer brand phrase 'dbt charts' links to; null renders plain text. |
| `font` | [FontStyle](#fontstyle) | Footer text font style (size and color required). |
| `y_offset` | float | Vertical offset from bottom edge in pixels. |
| `rule` | [FooterRule](#footerrule) | Hairline rule above footer text; null disables the rule. |

<a id="timestampstyle"></a>
## TimestampStyle
Authored overlay for TimestampStyle. Authored data-freshness chrome: visibility, placement, strftime format, font, and y-offset.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the data-freshness line. |
| `position` | enum: "top", "footer" | Timestamp row: top page chrome or footer baseline. |
| `align` | enum: "left", "right" | Timestamp horizontal alignment within its row. |
| `format` | str | strftime format for the data-freshness line, including any literal label text (e.g. '%H:%M %Z on %-d %b %Y'). The value is always UTC; a format that prints a clock must disclose the zone (%Z or a literal 'UTC'), else compile rejects it: an unlabeled clock reads as local. |
| `y` | float | Y-coordinate for top-positioned timestamp in pixels. |
| `font` | [FontStyle](#fontstyle) | Timestamp font style overrides (size, color, weight, ...). |

<a id="kpitonesstyle"></a>
## KpiTonesStyle
Authored overlay for KpiTonesStyle. Semantic tone palette shared by the KPI support row and table conditional glyphs.

| Field | Type | Description |
|-------|------|-------------|
| `positive` | str | Color for positive/good tone indicators. |
| `negative` | str | Color for negative/bad tone indicators. |
| `warning` | str | Color for warning/caution tone indicators. |
| `info` | str | Color for neutral/informational tone indicators. |

<a id="spacingvalues"></a>
## SpacingValues
Pre-parsed CSS spacing (margin/padding).

| Field | Type | Description |
|-------|------|-------------|
| `top` | float | Top spacing in pixels. |
| `right` | float | Right spacing in pixels. |
| `bottom` | float | Bottom spacing in pixels. |
| `left` | float | Left spacing in pixels. |

<a id="chartsupporttablesource"></a>
## ChartSupportTableSource
A support_table row that reads a column's raw per-x value.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Query column the row reads from (per-x raw value). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | D3 format string or format config object. Optional; inherits the chart measure format when omitted and source matches chart.y. |
| `label` | str | Left-stub row label. Optional. |

<a id="chartsupporttableaggregate"></a>
## ChartSupportTableAggregate
A support_table row that reads an aggregate of a column grouped by x.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `aggregate` | enum: "sum", "avg", "min", "max", "median", "count", "count_distinct" | Aggregate operation applied per x-group. One of: sum, avg, min, max, median, count, count_distinct. Exact names only: no aliases (spec G4). |
| `source` | str | Query column being aggregated. Always required alongside `aggregate:` (spec G2). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | D3 format string or format config object for the aggregated value. Optional. |
| `label` | str | Column header label override for this row. |

<a id="chartsupporttableperseries"></a>
## ChartSupportTablePerSeries
A support_table entry that expands into one row per color: series.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `per_series` | str | Query column the row reads from (per-x, per-series value). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `by_measure` | bool | When True, expand one row reading the named measure field directly (no color: groupby). Required for multi-y charts where each measure is its own y-field rather than a color-encoded series. |
| `label` | str | Row label displayed in the strip's label gutter. When None and by_measure=True, the per_series column name is used. Has no effect when by_measure=False (series name is the label). |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | D3 format string or format config object. Optional. |

<a id="quantitativeaxisstyle"></a>
## QuantitativeAxisStyle
Authored overlay for QuantitativeAxisStyle. Scale-type overlay for quantitative axes. Theme slot: axis_quantitative.

| Field | Type | Description |
|-------|------|-------------|
| `grid` | [BaseAxisGridStyle](#baseaxisgridstyle) | Grid line style for this axis. |
| `line` | [AxisLineStyle](#axislinestyle) | Domain line style for this axis. |
| `ticks` | [AxisTicksStyle](#axisticksstyle) | Tick mark style for this axis. |
| `labels` | [AxisLabelStyle](#axislabelstyle) | Axis label style. |
| `title` | [AxisTitleStyle](#axistitlestyle) | Axis title style. |
| `scale` | [BaseScaleStyle](#basescalestyle) | Per-axis scale overrides; None means no override. |

<a id="paddingstyle"></a>
## PaddingStyle
Authored overlay for PaddingStyle. Per-chart padding inset (px). All 4 sides required; theme YAML supplies defaults.

| Field | Type | Description |
|-------|------|-------------|
| `left` | float | Left padding in pixels. |
| `right` | float | Right padding in pixels. |
| `top` | float | Top padding in pixels. |
| `bottom` | float | Bottom padding in pixels. |

<a id="legendstyle"></a>
## LegendStyle
Authored overlay for LegendStyle.

| Field | Type | Description |
|-------|------|-------------|
| `position` | enum: "left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right" | Legend position (VL legend orient). |
| `direction` | enum: "horizontal", "vertical" | Legend layout direction. |
| `columns` | int | Legend entry columns. Zero keeps the renderer default; positive values set Vega-Lite legend columns. |
| `compact_columns` | int | Entry columns for an automatic compact top-horizontal legend. |
| `label` | [LegendLabelStyle](#legendlabelstyle) | Legend label style. |
| `title` | [LegendTitleStyle](#legendtitlestyle) | Legend title style. |
| `visible` | bool | Show the legend. None = legend visible; False = explicitly suppressed. |
| `symbol_limit` | int | Maximum number of legend entries to display; maps to VL symbolLimit. None uses Vega-Lite's default (no cap). Set to a positive integer to prevent legend overflow on high-cardinality series. |
| `values` | list[str] | Explicit legend entry order/filter; each entry resolves against the real legend domain by its rendered text or its column/measure name (case/separator-insensitive). None lets the renderer infer order from the data. |
| `symbol_shape` | str | Override the legend glyph shape; maps to VL legend.symbolType. None uses the mark-aware glyph derived from the chart's mark type. |
| `symbol_fill` | bool | When False, emits symbolFillColor='transparent' to produce a hollow legend glyph. None uses Vega-Lite's default (filled symbol). |

<a id="colorstyle"></a>
## ColorStyle
Authored overlay for ColorStyle. Unified chart color config: static paint, categorical palette, gradient scale.

| Field | Type | Description |
|-------|------|-------------|
| `static` | str | One explicit color: the marks on most families when no color column is encoded, the cell text on a table. |
| `gradient` | [ScaleTargetConfig](#scaletargetconfig) | Continuous gradient scale for the color encoding. |
| `categorical` | [CategoricalColorStyle](#categoricalcolorstyle) | Palettes used when color encodes distinct categories, or a lone series. |

<a id="baseaxisstyle"></a>
## BaseAxisStyle
Authored overlay for BaseAxisStyle. Universal axis surface: grid/line/ticks/labels/title/scale.

| Field | Type | Description |
|-------|------|-------------|
| `grid` | [BaseAxisGridStyle](#baseaxisgridstyle) | Grid line style for this axis. |
| `line` | [AxisLineStyle](#axislinestyle) | Domain line style for this axis. |
| `ticks` | [AxisTicksStyle](#axisticksstyle) | Tick mark style for this axis. |
| `labels` | [AxisLabelStyle](#axislabelstyle) | Axis label style. |
| `title` | [AxisTitleStyle](#axistitlestyle) | Axis title style. |
| `scale` | [BaseScaleStyle](#basescalestyle) | Per-axis scale overrides; None means no override. |

<a id="axisxstyle"></a>
## AxisXStyle
Authored overlay for AxisXStyle. Dimension/category-time axis style. Theme slot: axis_x.

| Field | Type | Description |
|-------|------|-------------|
| `grid` | [BaseAxisGridStyle](#baseaxisgridstyle) | Grid line style for this axis. |
| `line` | [AxisLineStyle](#axislinestyle) | Domain line style for this axis. |
| `ticks` | [DimensionTicksStyle](#dimensionticksstyle) | Dimension axis tick style. |
| `labels` | [DimensionLabelStyle](#dimensionlabelstyle) | Dimension axis label style. |
| `title` | [AxisTitleStyle](#axistitlestyle) | Axis title style. |
| `scale` | [XScaleStyle](#xscalestyle) | X-axis scale overrides including x_reverse; None means no override. |
| `position` | enum: "top", "bottom" | X-axis position; None uses Vega-Lite's default (bottom). |
| `time_unit` | enum: "auto", "year", "yearquarter", "yearmonth", "yearweek", "yearmonthdate", "monthofyear", "dayofweek", "dayofmonth", "dayofyear", "hourofday", "none" | Time-unit bucketing for temporal x-axes; None or 'auto' auto-detects from data. |
| `type` | enum: "auto", "ordinal", "temporal" | Scale type for bucketed-time x-axes; None/'auto' infers from time_unit grain. |
| `fill` | enum: "null", "zero", "linear", "step-after", "step-before", "step-center", "curve" | Fill for synthesized missing-bucket rows: null, zero, linear, step-after / step-before / step-center (Looker step), or curve (smoothstep). |
| `fiscal_year_start_month` | int | Calendar month (1=Jan..12=Dec) that opens a fiscal year/quarter for year/yearquarter bucketing; theme default 1 is the calendar convention (Q1=Jan-Mar). A non-default value always wins over axis_x.type: temporal for year/yearquarter grains - Vega-Lite's native timeUnit transform has no fiscal-offset concept and would otherwise silently discard the offset. |

<a id="axisystyle"></a>
## AxisYStyle
Authored overlay for AxisYStyle. Measure axis style. Theme slot: axis_y.

| Field | Type | Description |
|-------|------|-------------|
| `grid` | [BaseAxisGridStyle](#baseaxisgridstyle) | Grid line style for this axis. |
| `line` | [AxisLineStyle](#axislinestyle) | Domain line style for this axis. |
| `ticks` | [AxisTicksStyle](#axisticksstyle) | Tick mark style for this axis. |
| `labels` | [AxisLabelStyle](#axislabelstyle) | Axis label style. |
| `title` | [AxisTitleStyle](#axistitlestyle) | Axis title style. |
| `scale` | [BaseScaleStyle](#basescalestyle) | Per-axis scale overrides; None means no override. |
| `position` | enum: "left", "right", "auto" | Y-axis position; auto flips when endpoint labels are on the right. |
| `mirror` | bool \| [AxisMirrorStyle](#axismirrorstyle) | Draw the y-scale on both left and right edges (wide charts). true mirrors the primary axis's label verbatim; an object (format/expr) relabels only the mirrored edge (e.g. a percent-of-total right axis next to an absolute-value left axis) while ticks stay aligned to the single shared scale. Only meaningful on axis_y. |

<a id="bandaxisstyle"></a>
## BandAxisStyle
Authored overlay for BandAxisStyle. Scale-type overlay for band (categorical) axes. Theme slot: axis_band.

| Field | Type | Description |
|-------|------|-------------|
| `grid` | [BaseAxisGridStyle](#baseaxisgridstyle) | Grid line style for this axis. |
| `line` | [AxisLineStyle](#axislinestyle) | Domain line style for this axis. |
| `ticks` | [AxisTicksStyle](#axisticksstyle) | Tick mark style for this axis. |
| `labels` | [AxisLabelStyle](#axislabelstyle) | Axis label style. |
| `title` | [AxisTitleStyle](#axistitlestyle) | Axis title style. |
| `scale` | [BaseScaleStyle](#basescalestyle) | Per-axis scale overrides; None means no override. |
| `band_position` | float | Band position within the step (0–1); None uses Vega-Lite's default. |

<a id="supporttablestyle"></a>
## SupportTableStyle
Authored overlay for SupportTableStyle. Attached support_table style. Lives at style.charts.support_table.*.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Support_table font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `divider` | [RuleStyle](#rulestyle) | Rule at the boundary between the chart plot and the data strip. For position='bottom': rule sits above the strip (below the axis). For position='top': rule sits below the strip rows (above the plot top). |
| `row` | [SupportTableRowStyle](#supporttablerowstyle) | Support_table row padding and rule style. |
| `label` | [SupportTableLabelStyle](#supporttablelabelstyle) | Row label (series name) style. |
| `padding_top` | float | Padding above the topmost strip row in pixels. For position='bottom': gap between axis labels and the first row. For position='top': space above the topmost row (outer edge of strip). |
| `padding_bottom` | float | Padding below the last strip row in pixels. For position='bottom': space below the last row. For position='top': gap between the last row and the plot top edge. |
| `label_max_lines` | int | Number of x-axis label lines to reserve in the axis gap (only used for position='bottom'; ignored for position='top'). Typically 1 or 2. |
| `position` | enum: "top", "bottom", "left", "right" | Strip placement relative to the chart plot. 'top'/'bottom' apply when the chart's category axis is horizontal (vertical bar, line, area): 'top' places the strip above the plot, 'bottom' places it below with the x-axis between plot and strip. 'left'/'right' apply when the category axis is vertical (a horizontal bar): the strip renders as value columns beside the plot instead of rows above or below it. Left unset, 'top' is used on a horizontal category axis and the side the category labels are on is used on a vertical one. |

<a id="endpointlabelsconfig"></a>
## EndpointLabelsConfig
Authored overlay for EndpointLabelsConfig. Series names printed on the chart itself instead of in a legend.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Print series names on the chart instead of in a legend. True on every built-in theme, and used wherever the shape can name every series: line and area charts, and stacked bars. Grouped bars, layered charts, small multiples, and very narrow cards keep a legend instead. Set false to move the names back into a legend everywhere. Where the labels do appear they replace the color legend, so this setting and not `legend` is what removes them. |
| `label_offset` | float | Gap in pixels between the plot and the series labels. |
| `height` | float | Height in pixels of the label strip above a horizontal stacked bar. |

<a id="barchartmarksstyle"></a>
## BarChartMarksStyle
Authored overlay for BarChartMarksStyle. Bar-family mark overrides. Only bar and text (endpoint labels) are valid.

| Field | Type | Description |
|-------|------|-------------|
| `bar` | [BarMarkStyle](#barmarkstyle) | Bar mark overrides for bar charts; inherits from global. |
| `text` | [TextMarkStyle](#textmarkstyle) | Text mark overrides for bar endpoint labels; inherits from global. |

<a id="layeraxisystyle"></a>
## LayerAxisYStyle
Per-layer y-axis settings on a layered chart.

| Field | Type | Description |
|-------|------|-------------|
| `position` | enum: "left", "right" | Y-axis side for this layer (left or right). |
| `title` | str | Title on this layer's own y axis; defaults to the layer's label. |
| `scale` | [LayerAxisYScale](#layeraxisyscale) | Scale options for this layer's y axis, holding the [min, max] domain. |
| `ticks` | [LayerAxisYTicks](#layeraxisyticks) | Tick options for this layer's y axis, holding the target count. |
| `grid` | [LayerAxisYGrid](#layeraxisygrid) | Grid options for this layer's y axis, holding its visibility. |
| `labels` | [LayerAxisYLabels](#layeraxisylabels) | Tick-label options for this layer's y axis, holding the number format. |

<a id="barlayerstyle"></a>
## BarLayerStyle
Authored overlay for BarLayerStyle. Required wrapper for bar-layer mark overrides (built into a Patch by build_patch_model).

| Field | Type | Description |
|-------|------|-------------|
| `marks` | [BarChartMarksStyle](#barchartmarksstyle) | Mark overrides for this bar layer. |

<a id="linelayerstyle"></a>
## LineLayerStyle
Authored overlay for LineLayerStyle. Required wrapper for line-layer mark overrides (built into a Patch by build_patch_model).

| Field | Type | Description |
|-------|------|-------------|
| `marks` | [LineChartMarksStyle](#linechartmarksstyle) | Mark overrides for this line layer. |

<a id="arealayerstyle"></a>
## AreaLayerStyle
Authored overlay for AreaLayerStyle. Required wrapper for area-layer mark overrides (built into a Patch by build_patch_model).

| Field | Type | Description |
|-------|------|-------------|
| `marks` | [AreaChartMarksStyle](#areachartmarksstyle) | Mark overrides for this area layer. |

<a id="scatterlayerstyle"></a>
## ScatterLayerStyle
Authored overlay for ScatterLayerStyle. Required wrapper for scatter-layer mark overrides (built into a Patch by build_patch_model).

| Field | Type | Description |
|-------|------|-------------|
| `marks` | [ScatterChartMarksStyle](#scatterchartmarksstyle) | Mark overrides for this scatter layer. |

<a id="linechartmarksstyle"></a>
## LineChartMarksStyle
Authored overlay for LineChartMarksStyle. Line-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `line` | [LineMarkStyle](#linemarkstyle) | Line mark overrides; inherits from global. |
| `point` | [PointMarkStyle](#pointmarkstyle) | Point mark overrides; inherits from global. |
| `text` | [TextMarkStyle](#textmarkstyle) | Text mark overrides; inherits from global. |
| `rule` | [RuleMarkStyle](#rulemarkstyle) | Rule mark overrides; None inherits global. |

<a id="areachartmarksstyle"></a>
## AreaChartMarksStyle
Authored overlay for AreaChartMarksStyle. Area-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `area` | [AreaMarkStyle](#areamarkstyle) | Area fill mark overrides; inherits from global. |
| `line` | [AreaLineStyle](#arealinestyle) | Top-edge line stroke/halo/label overrides; inherits from global. |
| `point` | [PointMarkStyle](#pointmarkstyle) | Point-overlay mark overrides; inherits from global. |

<a id="scatterchartmarksstyle"></a>
## ScatterChartMarksStyle
Authored overlay for ScatterChartMarksStyle. Scatter-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `point` | [PointMarkStyle](#pointmarkstyle) | Point mark overrides; inherits from global. |
| `text` | [TextMarkStyle](#textmarkstyle) | Text mark overrides; inherits from global. |

<a id="heatmapchartmarksstyle"></a>
## HeatmapChartMarksStyle
Authored overlay for HeatmapChartMarksStyle. Heatmap-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `rect` | [RectMarkStyle](#rectmarkstyle) | Rect mark overrides; inherits from global. |
| `text` | [TextMarkStyle](#textmarkstyle) | Text mark overrides; None inherits global. |

<a id="totalstyle"></a>
## TotalStyle
Authored overlay for TotalStyle. Donut center total paint: value (the number) and label (the caption).

| Field | Type | Description |
|-------|------|-------------|
| `value` | [TotalValueSlotStyle](#totalvalueslotstyle) | Style for the donut center value (the number), including its format. |
| `label` | [TotalSlotStyle](#totalslotstyle) | Style for the donut center label (the caption). |

<a id="piechartmarksstyle"></a>
## PieChartMarksStyle
Authored overlay for PieChartMarksStyle. Pie/donut-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `slice` | [SliceMarkStyle](#slicemarkstyle) | Slice mark overrides; inherits from global. |
| `text` | [TextMarkStyle](#textmarkstyle) | Text mark overrides; None inherits global. |

<a id="conditionalrule"></a>
## ConditionalRule
A single conditional formatting rule.

| Field | Type | Description |
|-------|------|-------------|
| `eq` | Any | Match rows where the column value equals this value. |
| `ne` | Any | Match rows where the column value does not equal this value. |
| `lt` | int \| float | Match rows where the column value is less than this number. |
| `lte` | int \| float | Match rows where the column value is less than or equal to this number. |
| `gt` | int \| float | Match rows where the column value is greater than this number. |
| `gte` | int \| float | Match rows where the column value is greater than or equal to this number. |
| `between` | list[int \| float] | Match rows where the column value falls in [low, high] (inclusive). |
| `in` | list[Any] | Match rows where the column value is in this list. |
| `is_null` | bool | Match null rows (true) or non-null rows (false). |
| `default` | const: true | Catch-all rule that matches any row not matched by earlier rules. Must be the last entry. |
| `background` | str | Cell background color applied when the rule matches. |
| `font` | [FontStyle](#fontstyle) | Font style overrides (color, weight, style, decoration) applied when the rule matches. |
| `glyph` | str | Text shown before the cell value when the rule matches. |
| `glyph_color` | str | Color for the glyph when the rule matches. Requires glyph to be set. |
| `tone` | enum: "positive", "negative", "warning", "info" | Semantic tone (positive\|negative\|warning\|info) that colors the glyph via the theme's tone palette; the preferred, theme-adaptive alternative to a raw glyph_color. Requires glyph. Explicit glyph_color wins. |

<a id="formatconfig"></a>
## FormatConfig
Format configuration for value display.

| Field | Type | Description |
|-------|------|-------------|
| `spec` | str | D3 format string (e.g., ',.0f'), preset name (e.g., 'currency'), or Excel pattern. |
| `prefix` | str | Text placed before the formatted value (e.g., '$'). |
| `suffix` | str | Text placed after the formatted value (e.g., ' USD', '%'). |
| `notation` | enum: "analytic", "narrative" | Notation style: 'analytic' for SI-prefix (1 B, 1 M) or 'narrative' for prose-style (1bn, 1mn). |

<a id="fontstyle"></a>
## FontStyle
Text appearance. Merged as a unit.

| Field | Type | Description |
|-------|------|-------------|
| `family` | str | Font family name (e.g., 'sans-serif', 'Roboto'). |
| `color` | str | Text color as a CSS color string. |
| `size` | float | Font size in pixels. |
| `weight` | str \| float | How heavy the type is drawn (e.g., 'bold', 400, 700). |
| `style` | enum: "normal", "italic" | Upright or slanted type (normal or italic). |
| `decoration` | enum: "none", "line-through", "underline" | Line drawn on the text (underline, line-through, or none). |
| `case` | enum: "none", "sentence", "title", "upper", "lower", "slug", "camel" | Letter-case transform applied at render time. 'title' uses Chicago/Gruber rules and preserves tokens with internal capitals (ARR, iPhone). 'sentence' uppercases only the first character. 'none' (default) emits the string without any letter-case change. |
| `line_height` | float | Line height as a unitless multiple of font size. Cascades through Style.font to all text roles that carry a FontStyle slot. Body prose defaults to 1.25; titles override tighter via their font patch in theme YAML. |

<a id="kpivaluestyle"></a>
## KpiValueStyle
Authored overlay for KpiValueStyle. KPI headline value slot: font and format. Theme populates font.size directly.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Headline value font. Unset fields fall back to [`style.charts.kpi.font`](#kpichartstyle) (except `color`). |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Number format for the KPI headline value: D3 format string, preset name, or FormatConfig object. |

<a id="kpislotstyle"></a>
## KpiSlotStyle
Authored overlay for KpiSlotStyle. KPI per-slot style for label, affix, and glyph slots.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Slot font style. Unset fields fall back to [`style.charts.kpi.font`](#kpichartstyle) (except `color`). |
| `character` | str | Glyph character to render (e.g. '▲'). None = no glyph. |

<a id="staticgradientcolorstyle"></a>
## StaticGradientColorStyle
Authored overlay for StaticGradientColorStyle. Color config for geo/point_map/table families, no categorical arm.

| Field | Type | Description |
|-------|------|-------------|
| `static` | str | One explicit color: the marks on most families when no color column is encoded, the cell text on a table. |
| `gradient` | [ScaleTargetConfig](#scaletargetconfig) | Continuous gradient scale for the color encoding. |

<a id="tablerulestyle"></a>
## TableRuleStyle
Authored overlay for TableRuleStyle. Table rule color override block.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Table rule color; None uses the theme default. |

<a id="tablecolumnsstyle"></a>
## TableColumnsStyle
Authored overlay for TableColumnsStyle.

| Field | Type | Description |
|-------|------|-------------|
| `default_width` | float | Default column width in pixels. |
| `cell_padding` | float | Horizontal padding inside table cells in pixels. |
| `width_similarity_threshold` | float | Auto-width columns whose min/max ratio &gt;= this threshold are snapped to a shared width before budget allocation. 1.0 disables clustering; 0.0 forces all auto-columns to equal width. |
| `content_headroom` | float | Fractional breathing room added above the raw p95 column demand when pinning compact columns in mixed (compact + text) tables.  0.10 means each compact column is pinned at 10 % above its measured demand; the extra width is funded by the text-column budget.  Has no effect on all-compact tables (proportional scaling already fills the budget).  0.0 disables headroom. |

<a id="tableheaderstyle"></a>
## TableHeaderStyle
Authored overlay for TableHeaderStyle.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the header row (column labels + rule). Set to False on series-keyed tables where column meanings are obvious from context (e.g. donut-attached tables: swatch / share / name / value). Theme YAML supplies the UX default (true) via _base.yaml. |
| `height` | float | Header row height in pixels. |
| `font` | [FontStyle](#fontstyle) | Header font style overrides. Unset fields fall back to [`style.charts.table.font`](#tablechartstyle). |
| `font_compact` | [FontStyle](#fontstyle) | Compact-tier font overrides applied when body size ≤11px; None means no compact override. |
| `background` | str | Header background color; None means no fill (rule alone separates header from body). |
| `overflow` | enum: "clip", "truncate", "wrap-two", "wrap" | What happens to header text too wide for its column (clip, truncate, wrap-two, wrap). |
| `rule` | [RuleStyle](#rulestyle) | Header bottom rule style. |

<a id="tablerowstyle"></a>
## TableRowStyle
Authored overlay for TableRowStyle.

| Field | Type | Description |
|-------|------|-------------|
| `height` | float | Body row height in pixels. |
| `stripe` | [TableRowStripeStyle](#tablerowstripestyle) | Alternating row stripe style; None means no stripe. |
| `rule` | [RuleStyle](#rulestyle) | Row bottom rule style. |
| `role` | str | Default row role assignment; None means plain body row. |
| `roles` | [TableRowRolesStyle](#tablerowrolesstyle) | Per-role style overrides for summary and total rows. |

<a id="tablerownumbersstyle"></a>
## TableRowNumbersStyle
Authored overlay for TableRowNumbersStyle. Leading row-number column (style.table.row_numbers).

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show a leading row-number column; false by default. |
| `header` | str | Header label for the row-number column. |
| `align` | enum: "left", "right" | Text alignment for the row-number column. |

<a id="tabletitlestyle"></a>
## TableTitleStyle
Authored overlay for TableTitleStyle.

| Field | Type | Description |
|-------|------|-------------|
| `height` | float | Minimum title-block height in pixels. Applies as a floor only when a subtitle is present; a title-only block sizes to its natural content height instead. |
| `font` | [FontStyle](#fontstyle) | Table title font style overrides. Unset fields fall back to [`style.charts.table.font`](#tablechartstyle). |

<a id="paginationconfig"></a>
## PaginationConfig
Table pagination configuration.

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Enable client-side pagination for table charts. |
| `page_rows` | int | Rows per page. When enabled and None, the renderer auto-fits page size to the cell; set explicitly to pin the page size. |

<a id="tablecolumndefaultsconfig"></a>
## TableColumnDefaultsConfig
Table-level defaults applied to every column unless overridden per-column.

| Field | Type | Description |
|-------|------|-------------|
| `label` | str | One header text applied to every column, unless a column sets its own. |
| `width` | int \| str | Override column width in pixels (integer) or a CSS width string. |
| `align` | enum: "left", "center", "right" | Override cell text alignment (left, center, or right). |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | How values are written in every column, unless a column sets its own. |
| `background` | str | Override cell background color (CSS color string). |
| `font` | [FontStyle](#fontstyle) | Override cell font style (size, weight, color, family). |

<a id="tablecolumnconfig"></a>
## TableColumnConfig
Configuration for a single table column.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Whether this column renders. Defaults to true: style.columns is styling only, so naming a column here never hides it or any other column. Set false to hide it while keeping its values available to link: templates and style-input references. A column consumed as a style input (another column's background / font.color / font.weight names it) is hidden automatically unless it has its own style.columns entry; an explicit entry is a display signal and the column renders. |
| `label` | str | Display header label (defaults to column name). |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | How the number is written: a D3 spec, a preset name, or a format block. |
| `spark` | [SparkConfig](#sparkconfig) \| enum: "line", "area", "bar", "bar-normalize", "column", "columns" | Miniature chart drawn inside each cell: a type name, or a full block. |
| `swatch` | bool | When True, render this column's cells as small rounded color squares instead of text. Cell value must be a CSS color string (e.g. '#3164a3'). Useful for series-keyed tables, e.g. a 'Series' column where each row is identified by its color in the parent chart's palette. |
| `width` | int \| str | Column width (integer pixels or CSS string like '10%'). |
| `max_width` | int \| str | Maximum column width for auto-sized text columns (integer pixels or CSS string like '30%'). Cannot be set together with width:. |
| `align` | enum: "left", "center", "right" | Text alignment in cells (left, center, right). |
| `header_overflow` | enum: "clip", "truncate", "wrap-two", "wrap" | What happens to header text too wide for its column (clip, truncate, wrap-two, wrap). |
| `header_link` | str | URL template that makes the column header clickable. |
| `link` | str | URL template for cell values (Jinja template with row fields available). |
| `background` | str | Cell background color (hex string, or 'transparent'/'none'), or a column ID: a value matching a query column name uses that row's value in the named column instead of the literal string. |
| `font` | [FontStyle](#fontstyle) | Cell font style overrides. `color` and `weight` resolve column-ID-first, the same as `background`: a value matching a query column name uses that row's value in the named column. |
| `scale` | [ColumnScaleConfig](#columnscaleconfig) | Continuous color mapping configuration for this column. |
| `glyph` | str | Text shown before every cell value; fills the same slot as format.prefix and wins over it. |
| `glyph_color` | str | Color for the glyph. Requires glyph to be set. |

<a id="paginatorstyle"></a>
## PaginatorStyle
Authored overlay for PaginatorStyle. Visual style for the paginator control (chevrons + page numbers).

| Field | Type | Description |
|-------|------|-------------|
| `color_active` | str | Current page and live chevron color (theme body ink). |
| `color_inactive` | str | Other pages and ellipsis color (theme secondary text). |
| `color_disabled` | str | Chevron color when at first/last page (signals disabled by tone). |
| `font` | [FontStyle](#fontstyle) | Paginator font overrides (size, family). |
| `weight_active` | int | Font weight for the current (selected) page number. |
| `weight_inactive` | int | Font weight for other pages and ellipsis. |
| `weight_chevron` | int | Font weight for the prev/next chevrons (live and disabled). Usually heavier than weight_active so the chevrons read as interactive affordances against the lighter page numbers. |
| `item_width` | float | Per-item slot width in pixels (drives layout step). |

<a id="tableedgestyle"></a>
## TableEdgeStyle
Authored overlay for TableEdgeStyle. more_rows or empty_state edge-case UI.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Edge-case UI (more_rows / empty_state) font style overrides. Unset fields fall back to [`style.charts.table.font`](#tablechartstyle). |

<a id="sparkstyle"></a>
## SparkStyle
Authored overlay for SparkStyle. Inline sparkline defaults (inside table cells).

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Sparkline line/point color; None seeds from style.charts.color.categorical.single_series_palette[0]. |
| `padding` | [SpacingValues](#spacingvalues) | Cell padding around the sparkline in pixels. |
| `empty` | [SparkEmptyStyle](#sparkemptystyle) | Style for empty/no-data sparklines. |
| `single_value` | [SparkSingleValueStyle](#sparksinglevaluestyle) | Style for single-data-point sparklines. |
| `columns` | [SparkColumnsStyle](#sparkcolumnsstyle) | Style for column-type sparklines. |
| `column` | [SparkColumnStyle](#sparkcolumnstyle) | Style for the single vertical `column` spark mark. |
| `bar` | [SparkBarCellStyle](#sparkbarcellstyle) | Style for bar/bar-normalize sparklines. |
| `area` | [SparkAreaStyle](#sparkareastyle) | Style for area sparklines. |

<a id="projectionstyle"></a>
## ProjectionStyle
Authored overlay for ProjectionStyle. Vega-Lite map projection configuration for geo chart families.

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Vega-Lite projection type (e.g. 'mercator', 'albersUsa', 'equalEarth'). |

<a id="basemapstyle"></a>
## BasemapStyle
Authored overlay for BasemapStyle. Background map layer for geo charts (especially point maps).

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Background topo source identifier; None means no base layer. |

<a id="pointmapchartmarksstyle"></a>
## PointMapChartMarksStyle
Authored overlay for PointMapChartMarksStyle. Point map-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `point` | [PointMarkStyle](#pointmarkstyle) | Point mark overrides; inherits from global. |

<a id="geoshapechartmarksstyle"></a>
## GeoshapeChartMarksStyle
Authored overlay for GeoshapeChartMarksStyle. Geoshape-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `geoshape` | [GeoshapeMarkStyle](#geoshapemarkstyle) | Geoshape mark overrides; inherits from global. |

<a id="calloutelementstyle"></a>
## CalloutElementStyle
Authored overlay for CalloutElementStyle. Font + y_offset for a callout title or message.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `y_offset` | float | Vertical offset from the element's anchor in pixels. |

<a id="sparkbarbarstyle"></a>
## SparkBarBarStyle
Authored overlay for SparkBarBarStyle. Bar geometry sub-block for SparkBarChartStyle.

| Field | Type | Description |
|-------|------|-------------|
| `height` | float | Height of each bar in pixels. |
| `padding` | float | Vertical padding between bars in pixels. |
| `color` | str | Bar fill color; None seeds from style.charts.color.categorical.single_series_palette[0]. |
| `background` | str | Bar track background color - fill-grade, pinned per theme. |

<a id="sparkbarchartlabelstyle"></a>
## SparkBarChartLabelStyle
Authored overlay for SparkBarChartLabelStyle. Category-label sub-block for SparkBarChartStyle.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show series label text next to bars. |
| `width` | float | Reserved width for bar label text in pixels. |

<a id="sparkbarcountstyle"></a>
## SparkBarCountStyle
Authored overlay for SparkBarCountStyle. Count-value sub-block for SparkBarChartStyle.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show count/value text next to bars. |
| `width` | float | Reserved width for bar count text in pixels. |

<a id="cornerstyle"></a>
## CornerStyle
Authored overlay for CornerStyle. Corner rounding only, for a slot whose renderer never draws a stroke.

| Field | Type | Description |
|-------|------|-------------|
| `radius` | float | Corner radius in pixels. |

<a id="subtitlestyle"></a>
## SubtitleStyle
Authored overlay for SubtitleStyle. Subtitle font style for chart subtitles (Table, SparkBar).

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Subtitle font style; theme populates font.size directly. |

<a id="titlewidthoffsetsstyle"></a>
## TitleWidthOffsetsStyle
Authored overlay for TitleWidthOffsetsStyle. Additive level offsets applied to a board/chart title's heading level, by card pixel width.

| Field | Type | Description |
|-------|------|-------------|
| `tiny` | int | Level offset for the tiny tier (cards narrower than ~360px). |
| `narrow` | int | Level offset for the narrow tier (cards ~360–559px). |
| `medium` | int | Level offset for the medium tier (cards ~560–1099px). |
| `wide` | int | Level offset for the wide tier (cards ~1100px and up). |

<a id="titlepositionstyle"></a>
## TitlePositionStyle
Authored overlay for TitlePositionStyle. Vega-Lite title positioning pass-throughs grouped as a sub-object.

| Field | Type | Description |
|-------|------|-------------|
| `anchor` | str | Title anchor position; theme always sets this. |
| `angle` | float | Title rotation angle in degrees; None lets Vega-Lite choose. |
| `offset` | float | Title offset from its anchor in pixels; None uses Vega-Lite's default. |
| `baseline` | str | Title text baseline alignment; None uses Vega-Lite's default. |

<a id="titlesubtitlestyle"></a>
## TitleSubtitleStyle
Authored overlay for TitleSubtitleStyle. VL subtitle font pass-through, grouped for consistency with TitleStyle.font.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Subtitle font style overrides (color, family, size, weight). Unset fields fall back to [`style.font`](#style). |
| `overflow` | enum: "clip", "truncate", "wrap-two", "wrap" | Text overflow mode for the subtitle (clip, truncate, wrap-two, wrap). None means not set at this cascade level (theme floor is wrap-two). |

<a id="blockmarginstyle"></a>
## BlockMarginStyle
Authored overlay for BlockMarginStyle. Top and bottom margin for a prose block role, in line-height units.

| Field | Type | Description |
|-------|------|-------------|
| `margin_top` | float | Space above the block, in line-height units (lh × font_size × value). |
| `margin_bottom` | float | Space below the block, in line-height units (lh × font_size × value). |

<a id="textcolumnstyle"></a>
## TextColumnStyle
Authored overlay for TextColumnStyle. Author overrides for the column layout of board body text.

| Field | Type | Description |
|-------|------|-------------|
| `max_number` | int | Ceiling on the column count. The renderer may choose fewer when there is not enough text to fill them. None = no ceiling. |
| `gap` | float | Gap between columns in pixels. None = 1.5 line boxes, so the gutter scales with the type it separates. |
| `rule` | [ColumnRuleStyle](#columnrulestyle) | Vertical rule drawn between columns. None = no rule. |
| `max_chars` | int | Column width as a character count, overriding the shipped measure. The width is used exactly and the column count follows from it. |

<a id="textcodestyle"></a>
## TextCodeStyle
Authored overlay for TextCodeStyle. Inline and fenced code spans in markdown prose. Box group; mono font by default.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Box text font overrides: full FontStyle (family, color, size, weight, style, decoration, case). |
| `background` | str | Box background fill. |
| `border` | [BorderStyle](#borderstyle) | Box border (width, color, radius). |
| `highlight` | bool | Syntax-highlight fenced markdown code blocks. |
| `theme` | str | Pygments style name for fenced markdown code tokens. |

<a id="textblockquotestyle"></a>
## TextBlockquoteStyle
Authored overlay for TextBlockquoteStyle. Blockquote prose. Box group; border is the left rule.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Box text font overrides: full FontStyle (family, color, size, weight, style, decoration, case). |
| `background` | str | Box background fill. |
| `border` | [BorderStyle](#borderstyle) | Box border (width, color, radius). |

<a id="textboldstyle"></a>
## TextBoldStyle
Authored overlay for TextBoldStyle. Inline **bold** text runs in markdown prose, distinct from heading weight.

| Field | Type | Description |
|-------|------|-------------|
| `weight` | str \| float | CSS font-weight for bold text runs and bold table cells. |

<a id="textrulestyle"></a>
## TextRuleStyle
Authored overlay for TextRuleStyle. Horizontal rules in markdown prose: `---` and markdown-table gridlines.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Color of markdown horizontal rules and markdown-table gridlines. |

<a id="placeholderoverlay"></a>
## PlaceholderOverlay
Authored overlay for PlaceholderOverlay.

| Field | Type | Description |
|-------|------|-------------|
| `text` | str | Placeholder overlay text shown on empty charts. |
| `background` | str | Overlay background color. |
| `font` | [FontStyle](#fontstyle) | Overlay font style overrides. Unset fields fall back to [`style.font`](#style). |

<a id="categorycolorbinding"></a>
## CategoryColorBinding
Value→color assignments for one data field, shared by every chart.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `values` | dict[str, one of: 'category[1]', 'category[2]', 'category[3]', 'category[4]', 'category[5]', 'category[6]', 'category[7]', 'category[8]', 'category[9]', 'category[10]' \| str] | Data value → palette token (`category[1]`, `dbt-grays.muted`) or literal hex. Tokens resolve against the board's theme. |

<a id="tooltipstyle"></a>
## TooltipStyle
Authored overlay for TooltipStyle. Tooltip box style: all cascade keys for the hover bubble.

| Field | Type | Description |
|-------|------|-------------|
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Default tooltip value format string; theme always provides this. |
| `background` | str | Tooltip background color (CSS color string); theme always provides this. |
| `line_height` | float | Tooltip line-height multiplier; theme always provides this. |
| `max_width` | float | Maximum tooltip width in pixels; theme always provides this. |
| `gap` | float | Gap in pixels between label and value columns; theme always provides this. |
| `font` | [FontStyle](#fontstyle) | Tooltip font overrides (size etc.); cascade fills missing fields. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Tooltip inner padding (4 sides in pixels); theme always provides this. |
| `label` | [TooltipSlotStyle](#tooltipslotstyle) | Label-column font overrides (color, weight). |
| `value` | [TooltipSlotStyle](#tooltipslotstyle) | Value-column font overrides (color, weight). |
| `border` | [TooltipBorderStyle](#tooltipborderstyle) | Tooltip border style; theme always provides this. |
| `shadow` | [TooltipShadowStyle](#tooltipshadowstyle) | Tooltip drop-shadow config; theme always provides this. |
| `swatch` | [TooltipSwatchStyle](#tooltipswatchstyle) | Series color swatch size/shape; theme always provides this. |
| `active_marker` | enum: "fill", "triangle" | How the hovered row is marked in a multi-row (x-unified) tooltip: 'fill' tints the row background (default); 'triangle' draws an edge-flush wedge in the box's left padding instead. Theme always provides this. |

<a id="hoveremphasisstyle"></a>
## HoverEmphasisStyle
Authored overlay for HoverEmphasisStyle. Whether a chart visually answers "what am I pointing at", beyond the tooltip.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Whether hovering a mark visually emphasizes it; theme always provides this. |
| `drop_line_color` | str | Color of the vertical drop line from a hovered line/area datum down to its axis. |
| `drop_line_width` | float | Width, in pixels, of the vertical drop line from a hovered line/area datum down to its axis. |

<a id="viewstyle"></a>
## ViewStyle
Authored overlay for ViewStyle.

| Field | Type | Description |
|-------|------|-------------|
| `stroke` | str | Plot area border stroke color; None means no border. |
| `continuous_width` | float | Default plot width for continuous (quantitative) scales in pixels. |
| `continuous_height` | float | Default plot height for continuous (quantitative) scales in pixels. |
| `discrete_width` | float | Default plot width for discrete (ordinal/nominal) scales in pixels; None means auto. |
| `discrete_height` | float | Default plot height for discrete (ordinal/nominal) scales in pixels; None means auto. |

<a id="globalmarksstyle"></a>
## GlobalMarksStyle
Authored overlay for GlobalMarksStyle. Global mark defaults: one <Mark>MarkStyle per VL mark type.

| Field | Type | Description |
|-------|------|-------------|
| `bar` | [BarMarkStyle](#barmarkstyle) | Global bar mark defaults. |
| `line` | [LineMarkStyle](#linemarkstyle) | Global line mark defaults. |
| `area` | [AreaMarkStyle](#areamarkstyle) | Global area mark defaults. |
| `point` | [PointMarkStyle](#pointmarkstyle) | Global point mark defaults. |
| `slice` | [SliceMarkStyle](#slicemarkstyle) | Global slice (arc/pie) mark defaults. |
| `text` | [TextMarkStyle](#textmarkstyle) | Global text mark defaults. |
| `rule` | [RuleMarkStyle](#rulemarkstyle) | Global rule mark defaults. |
| `rect` | [RectMarkStyle](#rectmarkstyle) | Global rect mark defaults. |
| `circle` | [CircleMarkStyle](#circlemarkstyle) | Global circle mark defaults. |
| `geoshape` | [GeoshapeMarkStyle](#geoshapemarkstyle) | Global geoshape mark defaults. |

<a id="histogramchartstyle"></a>
## HistogramChartStyle
Authored overlay for HistogramChartStyle. Histogram chart style.

| Field | Type | Description |
|-------|------|-------------|
| `axis_quantitative` | [QuantitativeAxisStyle](#quantitativeaxisstyle) | Per-chart-type quantitative-axis overrides; None inherits the global axis_quantitative at render. |
| `preferred_width` | float | Preferred chart width in pixels. Falls back to [`style.charts.preferred_width`](#chartsstyle). |
| `padding` | [PaddingStyle](#paddingstyle) | Per-chart-type padding override; 4 sides in pixels. Unset fields fall back to [`style.charts.padding`](#chartsstyle). |
| `background` | str | Chart-local background color override; None inherits from theme. |
| `title` | [TitleStyle](#titlestyle) | Chart-level title style override; None inherits the theme title style. |
| `aspect_ratio` | float | Chart aspect ratio (width/height). Falls back to [`style.charts.aspect_ratio`](#chartsstyle). |
| `min_height` | float | Minimum chart height in pixels. Falls back to [`style.charts.min_height`](#chartsstyle). |
| `max_height` | float | Maximum chart height in pixels. Falls back to [`style.charts.max_height`](#chartsstyle). |
| `legend` | [LegendStyle](#legendstyle) | Chart legend style. |
| `color` | [ColorStyle](#colorstyle) | Chart color: static mark paint, categorical palette, and/or gradient scale. |
| `axis` | [BaseAxisStyle](#baseaxisstyle) | Override applied to both x and y axes; None inherits the global axis at render. |
| `axis_x` | [AxisXStyle](#axisxstyle) | Per-chart-type x-axis style overrides; None inherits the global axis_x at render. |
| `axis_y` | [AxisYStyle](#axisystyle) | Per-chart-type y-axis style overrides; None inherits the global axis_y at render. |
| `axis_band` | [BandAxisStyle](#bandaxisstyle) | Per-chart-type categorical (band) axis overrides; None inherits the global band axis at render. |
| `number_format` | str \| enum: "currency", "currency_full", "currency_whole", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "year" | Default number format for axes and tooltips (D3 format string); None inherits from theme. |
| `time_format` | str \| enum: "date_short", "time_short" | Default time format for temporal axes (D3 time format string or strftime spec like '%b %Y'); None inherits from theme. |
| `support_table` | [SupportTableStyle](#supporttablestyle) | Per-chart-type support_table style override. Unset fields fall back to [`style.charts.support_table`](#chartsstyle). |
| `bin_maxbins` | int | Maximum number of bins for auto-binning. |
| `marks` | [HistogramChartMarksStyle](#histogramchartmarksstyle) | Histogram-family mark overrides. Unset fields fall back to [`style.charts.marks`](#chartsstyle). |

<a id="serieslabelstyle"></a>
## SeriesLabelStyle
Authored overlay for SeriesLabelStyle. Series-label primitive: typography for any text mark that names a

| Field | Type | Description |
|-------|------|-------------|
| `font` | [SeriesLabelFontStyle](#serieslabelfontstyle) | Series label font style overrides; cascade fills missing fields from charts.font. Unset fields fall back to [`style.charts.font`](#chartsstyle). |

<a id="layoutgapstyle"></a>
## LayoutGapStyle
Authored overlay for LayoutGapStyle.

| Field | Type | Description |
|-------|------|-------------|
| `gap` | float | Gap between layout items in pixels. |

<a id="gridlayoutstyle"></a>
## GridLayoutStyle
Authored overlay for GridLayoutStyle.

| Field | Type | Description |
|-------|------|-------------|
| `columns` | int | Number of columns in the grid layout. |
| `gap` | float | Gap between grid cells in pixels. |

<a id="tabsstyle"></a>
## TabsStyle
Authored overlay for TabsStyle.

| Field | Type | Description |
|-------|------|-------------|
| `bar_height` | float | Tab bar height in pixels. |
| `border` | [BorderStyle](#borderstyle) | Tab bar border style. |
| `font` | [FontStyle](#fontstyle) | Tab label font style overrides. Unset fields fall back to [`style.font`](#style). |
| `active_weight` | str | Font weight for the active tab label. |
| `inactive_weight` | str | Font weight for inactive tab labels. |
| `title_baseline_offset` | float | Vertical offset to align SVG tab label baseline in pixels. |

<a id="detailsstyle"></a>
## DetailsStyle
Authored overlay for DetailsStyle.

| Field | Type | Description |
|-------|------|-------------|
| `summary_height` | float | Height of the details summary (collapsed) row in pixels. |
| `border` | [BorderStyle](#borderstyle) | Details element border style. |
| `font` | [FontStyle](#fontstyle) | Details summary font style overrides. Unset fields fall back to [`style.font`](#style). |
| `arrow` | [DetailsArrowStyle](#detailsarrowstyle) | Expand/collapse arrow glyph layout and font style. |
| `label_x` | float | X position of the details summary label text in pixels. |
| `text_baseline_offset` | float | Vertical offset to align SVG details text baseline in pixels. |
| `content_y_offset` | float | Y offset of the expanded details content area in pixels. |

<a id="variableslabelstyle"></a>
## VariablesLabelStyle
Authored overlay for VariablesLabelStyle. Per-label font substyle for variable controls. Cascades from variables.font.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Variable label font style overrides. Unset fields fall back to [`style.variables.font`](#variablesstyle). |

<a id="variablesvaluestyle"></a>
## VariablesValueStyle
Authored overlay for VariablesValueStyle. Per-value font substyle for variable controls. Cascades from variables.font.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Variable value font style overrides. Unset fields fall back to [`style.variables.font`](#variablesstyle). |

<a id="variablesplaceholderstyle"></a>
## VariablesPlaceholderStyle
Authored overlay for VariablesPlaceholderStyle. Per-placeholder font substyle for variable controls.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Variable placeholder-text font style overrides. Unset fields fall back to [`style.variables.font`](#variablesstyle). |

<a id="inputstyle"></a>
## InputStyle
Authored overlay for InputStyle.

| Field | Type | Description |
|-------|------|-------------|
| `height` | float | Input control height in pixels. |
| `border` | [CornerStyle](#cornerstyle) | Corner rounding for input controls. |
| `focus_color` | str | Input focus ring color. Falls back to [`style.accent`](#style). |
| `background` | str | Input background color. |
| `padding` | [SpacingValues](#spacingvalues) | Input inner padding in pixels. |
| `widths` | [InputWidths](#inputwidths) | Per-input-type default widths. |
| `range` | [RangeDefaults](#rangedefaults) | Range input default min/max/step values. |

<a id="footerrule"></a>
## FooterRule
Authored overlay for FooterRule. Hairline rule above the footer attribution text. None = no rule.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Rule stroke color. |
| `stroke_width` | float | Rule stroke width in pixels. |

<a id="baseaxisgridstyle"></a>
## BaseAxisGridStyle
Authored overlay for BaseAxisGridStyle. Grid line style for all axis variants.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show grid lines; None inherits from parent axis. |
| `opacity` | float | Grid line opacity; None uses Vega-Lite's default. |
| `width` | float | Grid line width in pixels; None uses Vega-Lite's default. |
| `color` | str | Grid line color; None uses Vega-Lite's default. |
| `dash` | list[float] | Dash pattern for grid lines; None renders a solid line. |
| `threshold` | [AxisGridThresholdStyle](#axisgridthresholdstyle) | Threshold-rule gridline style; None inherits from parent axis. |

<a id="axislinestyle"></a>
## AxisLineStyle
Authored overlay for AxisLineStyle. Axis domain/baseline line style. Renamed from ``AxisDomainStyle``:

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the axis domain line; None inherits from parent axis. |
| `width` | float | Domain line width in pixels; None uses Vega-Lite's default. |
| `color` | str | Domain line color; None uses Vega-Lite's default. |

<a id="axisticksstyle"></a>
## AxisTicksStyle
Authored overlay for AxisTicksStyle. Tick marks on an axis: visibility, color, size, and cadence.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show axis ticks; None inherits from parent axis. |
| `color` | str | Tick color; None uses Vega-Lite's default. |
| `length` | float | Tick length in pixels; None uses Vega-Lite's default. |
| `width` | float | Tick stroke width in pixels; None uses Vega-Lite's default. |
| `offset` | float | Pixel offset of ticks from their default position; None means no offset. |
| `count` | int | Target number of axis ticks: a target everywhere, never an exact count. On the measure axis (axis_y) the renderer computes an explicit round-numbered ladder of at most this many ticks. On axis_x it passes through as VL's axis.tickCount: a temporal scale honors it closely, a quantitative one rounds to a nearby round-numbered ladder. To name the interval instead of the count, author ticks.step on a quantitative axis_x. An ordinal axis_x has no tick-count concept and ignores this. |
| `step` | int | Tick interval on axis_x. Alongside ticks.time_unit it is a multiple of that calendar grain (time_unit: year, step: 5 -&gt; a tick every 5 years). On its own it is a numeric interval for a quantitative axis (step: 1000 -&gt; a tick every 1000), and acts as a floor rather than a fixed ladder, so the axis keeps covering the data as its range grows. A bare step on an axis that is not quantitative is an error, not a no-op; axis_y rejects step entirely (set ticks.count there instead). |

<a id="axislabelstyle"></a>
## AxisLabelStyle
Authored overlay for AxisLabelStyle. Axis label: font + padding + VL-passthrough.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Axis element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | float | Padding between axis labels and ticks in pixels; None inherits from parent axis. |
| `max_width` | float | Maximum label width in pixels; None uses Vega-Lite's default (180px). |
| `angle` | float | Label rotation angle in degrees; None uses Vega-Lite's default. |
| `align` | enum: "left", "right", "center", "inward", "outward" | Horizontal text alignment of labels. 'left'/'right'/'center' are absolute. 'inward' hugs the plot (own-side: left on a left axis, right on a right axis); 'outward' hugs away from the plot (Vega-Lite's default growth direction). inward/outward are a no-op on an axis with no left/right edge. None uses Vega-Lite's default. |
| `overlap` | [AxisLabelOverlapConfig](#axislabeloverlapconfig) | Label overlap strategy enablement. None inherits from the theme cascade. Set individual bools to enable/disable; they are applied in fixed order skip→tilt. On bucketed temporal axes, skip thins label visibility to a coarser calendar period without changing the axis or label time unit. |
| `min_gap` | float | Minimum pixel gap between labels; None uses Vega-Lite's default (0px). |
| `visible` | bool | Show axis labels; None uses Vega-Lite's default (labels shown). |
| `expr` | str | Custom Vega expression for label text; None uses smart temporal defaults when applicable. |
| `bound` | bool \| float | Hide labels that overflow the axis range; None uses Vega-Lite's default. |
| `flush` | bool \| float | Align first/last label flush with the scale range; None uses Vega-Lite's default. |
| `offset` | float | Pixel offset of the label from its tick anchor; None uses Vega-Lite's default. |
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Tick value format string; None uses auto-format. |

<a id="axistitlestyle"></a>
## AxisTitleStyle
Authored overlay for AxisTitleStyle. Axis title typography, deliberately thin. A title is one short static

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Axis title font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | float | Padding between axis title and labels in pixels; None uses Vega-Lite's default. |
| `angle` | float | Title rotation angle in degrees; None uses Vega-Lite's default. |
| `align` | enum: "left", "right", "center", "inward", "outward" | Horizontal text alignment of the title. 'left'/'right'/'center' are absolute. 'inward'/'outward' resolve against the axis's own left/right edge, same as label.align; a no-op on an axis with no left/right edge. None uses Vega-Lite's default. |
| `visible` | bool | Show the axis title; None uses Vega-Lite's default (title shown). An explicit value you set, on the board or on a single chart, wins over the title an authored x_label/y_label would force on. |

<a id="basescalestyle"></a>
## BaseScaleStyle
Authored overlay for BaseScaleStyle. Universal + continuous-only scale config.

| Field | Type | Description |
|-------|------|-------------|
| `round` | bool | Round scale outputs to nearest integer; None uses Vega-Lite's default (no rounding). |
| `clamp` | bool | Clamp values to scale domain; None uses Vega-Lite's default (no clamping). |
| `nice` | bool | Round the axis domain to nice values; None uses Vega-Lite's default. Forwards natively to Vega-Lite's scale.nice. |
| `padding` | float | Unified scale padding shortcut; dispatches to band, point, or continuous padding per scale type. |
| `headroom` | float | Fractional breathing room at measure-axis edges, exact (never nice-rounded). Zero-anchored axes (bar; line/area/scatter near zero): domain_max = data_max * (1 + headroom). Zoomed axes (line/area/scatter far from zero): both edges expand: domain_max = data_max + headroom * span; domain_min = data_min - headroom * span. None inherits the theme default; 0 disables headroom. Ignored with an explicit `domain` or `stack: normalize`. |
| `values` | list[Any] | Explicit tick values; None uses Vega-Lite's auto tick values. |
| `continuous` | [ScaleContinuousStyle](#scalecontinuousstyle) | Continuous-scale overrides (type, domain, zero, log/pow/symlog params); None means no override. |

<a id="legendlabelstyle"></a>
## LegendLabelStyle
Authored overlay for LegendLabelStyle.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Legend element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | float | Padding between legend symbol and element text in pixels. |
| `max_width` | float | Maximum label width in pixels; maps to VL labelLimit. None uses Vega-Lite's default. |

<a id="legendtitlestyle"></a>
## LegendTitleStyle
Authored overlay for LegendTitleStyle.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Legend element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | float | Padding between legend symbol and element text in pixels. |
| `visible` | bool | Show the legend title; None = shown, False = suppressed (VL legend.title: null). |

<a id="scaletargetconfig"></a>
## ScaleTargetConfig
Authored overlay for ScaleTargetConfig. Scale configuration for a single style target (background or color).

| Field | Type | Description |
|-------|------|-------------|
| `palette` | str \| list[str] \| list[float] \| enum: "accent", "bluegreen", "blueorange", "bluepurple", "blues", "brownbluegreen", "browns", "category-6-tonal-blue", "category-6-tonal-brown", "category-6-tonal-green", "category-6-tonal-orange", "category-6-tonal-purple", "category10", "category20", "category20b", "category20c", "cividis", "dark2", "darkblue", "darkgold", … (92 more; see the JSON Schema) | Which colors the scale draws from: a built-in palette name or Vega scheme, a CSS color list for categorical, or a float list for relative stops. |
| `domain` | const: "data" | Scale domain source ('data' uses the data extent, widened to nice round bounds when nice is true; None uses explicit min/max). |
| `min` | float \| int | Minimum scale domain value (overrides data minimum). |
| `max` | float \| int | Maximum scale domain value (overrides data maximum). |
| `nice` | bool | Widen a data-derived domain to round ('nice') bounds and label every nice tick on the legend, mirroring Vega-Lite's own scale.nice. Applies to the color scale a gradient legend labels, not the x/y position scale. Ignored once min or max is set; a single-sided bound already fixes that edge exactly. Widening is currently honored on heatmap's themed color gradient only; geoshape's choropleth honors `nice: false` (exact endpoint labels) but not `nice: true`'s widening. |
| `null_color` | str | Color assigned to null values. |
| `hinge` | float \| const: "auto" | Diverging scale midpoint value, or 'auto' to use the data midpoint. |
| `arm_mode` | enum: "asymmetric", "symmetric" | How diverging scale arms are stretched: 'asymmetric' (proportional) or 'symmetric' (equal arms). |

<a id="categoricalcolorstyle"></a>
## CategoricalColorStyle
Authored overlay for CategoricalColorStyle. Categorical palette config: per-series colors and single-series ink list.

| Field | Type | Description |
|-------|------|-------------|
| `palette` | str \| list[str] \| enum: "category-6-tonal-blue", "category-6-tonal-brown", "category-6-tonal-green", "category-6-tonal-orange", "category-6-tonal-purple", "dbt-creams", "dbt-div-blue-red", "dbt-div-blue-red-dark", "dbt-div-coolwarm", "dbt-div-coolwarm-dark", "dbt-div-crimson-green", "dbt-div-crimson-green-dark", "dbt-div-orange-teal", "dbt-div-orange-teal-dark", "dbt-div-sunset", "dbt-div-sunset-dark", "dbt-grays", "dbt-seq-amber", "dbt-seq-amber-dark", "dbt-seq-blue", … (25 more; see the JSON Schema) | Categorical color palette: list of stops or a named palette. Expanded to list[str] at validation time. |
| `single_series_palette` | str \| list[str] \| enum: "category-6-tonal-blue", "category-6-tonal-brown", "category-6-tonal-green", "category-6-tonal-orange", "category-6-tonal-purple", "dbt-creams", "dbt-div-blue-red", "dbt-div-blue-red-dark", "dbt-div-coolwarm", "dbt-div-coolwarm-dark", "dbt-div-crimson-green", "dbt-div-crimson-green-dark", "dbt-div-orange-teal", "dbt-div-orange-teal-dark", "dbt-div-sunset", "dbt-div-sunset-dark", "dbt-grays", "dbt-seq-amber", "dbt-seq-amber-dark", "dbt-seq-blue", … (25 more; see the JSON Schema) | Ordered list of single-series mark inks (must be non-empty when set), or a palette name. |

<a id="dimensionticksstyle"></a>
## DimensionTicksStyle
Authored overlay for DimensionTicksStyle. axis_x-only: adds the calendar unit that anchors a step cadence.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool \| const: "auto" | Show axis ticks. "auto" shows the tick only where the gridlines can't reach all the way to the labels on their own; None inherits from parent axis. |
| `color` | str | Tick color; None uses Vega-Lite's default. |
| `length` | float | Tick length in pixels; None uses Vega-Lite's default. |
| `width` | float | Tick stroke width in pixels; None uses Vega-Lite's default. |
| `offset` | float | Pixel offset of ticks from their default position; None means no offset. |
| `count` | int | Target number of axis ticks: a target everywhere, never an exact count. On the measure axis (axis_y) the renderer computes an explicit round-numbered ladder of at most this many ticks. On axis_x it passes through as VL's axis.tickCount: a temporal scale honors it closely, a quantitative one rounds to a nearby round-numbered ladder. To name the interval instead of the count, author ticks.step on a quantitative axis_x. An ordinal axis_x has no tick-count concept and ignores this. |
| `step` | int | Tick interval on axis_x. Alongside ticks.time_unit it is a multiple of that calendar grain (time_unit: year, step: 5 -&gt; a tick every 5 years). On its own it is a numeric interval for a quantitative axis (step: 1000 -&gt; a tick every 1000), and acts as a floor rather than a fixed ladder, so the axis keeps covering the data as its range grows. A bare step on an axis that is not quantitative is an error, not a no-op; axis_y rejects step entirely (set ticks.count there instead). |
| `time_unit` | enum: "auto", "year", "yearquarter", "yearmonth", "yearweek", "yearmonthdate", "monthofyear", "dayofweek", "dayofmonth", "dayofyear", "hourofday", "none" | Step-anchored tick cadence unit; None disables step-anchored ticks. |

<a id="dimensionlabelstyle"></a>
## DimensionLabelStyle
Authored overlay for DimensionLabelStyle. AxisLabelStyle + dimension-axis-only label fields.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Axis element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `padding` | float | Padding between axis labels and ticks in pixels; None inherits from parent axis. |
| `max_width` | float | Maximum label width in pixels; None uses Vega-Lite's default (180px). |
| `angle` | float | Label rotation angle in degrees; None uses Vega-Lite's default. |
| `align` | enum: "left", "right", "center", "inward", "outward" | Horizontal text alignment of labels. 'left'/'right'/'center' are absolute. 'inward' hugs the plot (own-side: left on a left axis, right on a right axis); 'outward' hugs away from the plot (Vega-Lite's default growth direction). inward/outward are a no-op on an axis with no left/right edge. None uses Vega-Lite's default. |
| `overlap` | [AxisLabelOverlapConfig](#axislabeloverlapconfig) | Label overlap strategy enablement. None inherits from the theme cascade. Set individual bools to enable/disable; they are applied in fixed order skip→tilt. On bucketed temporal axes, skip thins label visibility to a coarser calendar period without changing the axis or label time unit. |
| `min_gap` | float | Minimum pixel gap between labels; None uses Vega-Lite's default (0px). |
| `visible` | bool | Show axis labels; None uses Vega-Lite's default (labels shown). |
| `expr` | str | Custom Vega expression for label text; None uses smart temporal defaults when applicable. |
| `bound` | bool \| float | Hide labels that overflow the axis range; None uses Vega-Lite's default. |
| `flush` | bool \| float | Align first/last label flush with the scale range; None uses Vega-Lite's default. |
| `offset` | float | Pixel offset of the label from its tick anchor; None uses Vega-Lite's default. |
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Tick value format string; None uses auto-format. |
| `time_unit` | enum: "auto", "year", "yearquarter", "yearmonth", "yearweek", "yearmonthdate", "monthofyear", "dayofweek", "dayofmonth", "dayofyear", "hourofday", "none" | Label cadence for temporal axes; None inherits from the parent axis time_unit. |
| `clock` | one of: 24, 12 | Sub-day clock register for a continuous temporal x-axis: 24 for the unambiguous, meridiem-free 24-hour clock, or 12 for the 12-hour clock with the Noon/Midnight word vocabulary. None leaves the value to the theme cascade. |
| `tilt_increments` | list[float] | Descending tilt angles for label overlap resolution on discrete x-axes; None disables tilt. |
| `values` | list[Any] | Dates to keep label text on, chosen from among the axis's ticks (whatever axis_x.scale.values or the auto-fill cadence already produced): this filters which ticks show text, it does not add or remove ticks. Every tick not in this list keeps its position and gridline but has its label blanked. Set axis_x.scale.values separately to change tick/grid density itself. Temporal x-axes only; not supported on a horizontal bar's categorical axis. None labels every tick as usual. |

<a id="xscalestyle"></a>
## XScaleStyle
Authored overlay for XScaleStyle. Scale config for axis_x.scale only.

| Field | Type | Description |
|-------|------|-------------|
| `round` | bool | Round scale outputs to nearest integer; None uses Vega-Lite's default (no rounding). |
| `clamp` | bool | Clamp values to scale domain; None uses Vega-Lite's default (no clamping). |
| `nice` | bool | Round the axis domain to nice values; None uses Vega-Lite's default. Forwards natively to Vega-Lite's scale.nice. |
| `padding` | float | Unified scale padding shortcut; dispatches to band, point, or continuous padding per scale type. |
| `headroom` | float | Fractional breathing room at measure-axis edges, exact (never nice-rounded). Zero-anchored axes (bar; line/area/scatter near zero): domain_max = data_max * (1 + headroom). Zoomed axes (line/area/scatter far from zero): both edges expand: domain_max = data_max + headroom * span; domain_min = data_min - headroom * span. None inherits the theme default; 0 disables headroom. Ignored with an explicit `domain` or `stack: normalize`. |
| `values` | list[Any] | Explicit tick values; None uses Vega-Lite's auto tick values. |
| `continuous` | [ScaleContinuousStyle](#scalecontinuousstyle) | Continuous-scale overrides (type, domain, zero, log/pow/symlog params); None means no override. |
| `x_reverse` | bool | Reverse the x-axis scale direction; None means no reversal. |

<a id="axismirrorstyle"></a>
## AxisMirrorStyle
Per-edge label override for the mirrored ``axis_y.mirror`` ghost axis.

| Field | Type | Description |
|-------|------|-------------|
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Tick value format string for the mirrored edge; None reuses the primary axis's format. |
| `expr` | str | Custom Vega expression for the mirrored edge's label text; None reuses the primary axis's label expression. |

<a id="rulestyle"></a>
## RuleStyle
Authored overlay for RuleStyle. Shared rule-line primitive: width, color, continuous mode.

| Field | Type | Description |
|-------|------|-------------|
| `width` | float | Rule line width in pixels. |
| `color` | str | Rule color; None inherits from theme. |
| `continuous` | bool | Draw a continuous full-width rule (true) or only under columns (false). |

<a id="supporttablerowstyle"></a>
## SupportTableRowStyle
Authored overlay for SupportTableRowStyle.

| Field | Type | Description |
|-------|------|-------------|
| `padding` | [SupportTableRowPaddingStyle](#supporttablerowpaddingstyle) | Row padding style. |
| `rule` | [RuleStyle](#rulestyle) | Row bottom rule style. |

<a id="supporttablelabelstyle"></a>
## SupportTableLabelStyle
Authored overlay for SupportTableLabelStyle. Row label styling.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Row label font style overrides. Unset fields fall back to [`style.charts.support_table.font`](#supporttablestyle). |

<a id="barmarkstyle"></a>
## BarMarkStyle
Authored overlay for BarMarkStyle. Bar mark geometry and stroke. Chart-level bar fields live on BarChartStyle.

| Field | Type | Description |
|-------|------|-------------|
| `border` | [BorderStyle](#borderstyle) | Bar border style (color/width also serve as the bar stroke). |
| `padding` | float | Padding around bar marks in pixels. |
| `size` | float | Bar width in pixels; overrides all other sizing. |
| `band_width` | float | Bar width as a fraction of the band step (0–1). |
| `gap` | float | Target pixel gap between adjacent bars on a continuous scale. |
| `min_size` | float | Narrowest a computed bar width may shrink to on a continuous scale. |
| `max_size` | float | Widest a computed bar width may grow to on a continuous scale. Also stands in for an unauthored `size` when reserving axis padding and when budgeting a horizontal bar chart's minimum height. |
| `opacity` | float | Bar fill opacity (0–1); None uses VL default. |
| `labels` | [BarLabelsStyle](#barlabelsstyle) | Value label style for bar marks. |
| `total_label` | [BarTotalLabelStyle](#bartotallabelstyle) | Stack total label style. Only takes effect on stacked bar charts; ignored otherwise. |

<a id="textmarkstyle"></a>
## TextMarkStyle
Authored overlay for TextMarkStyle.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Text mark font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `align` | str | Horizontal text alignment for text marks. |

<a id="layeraxisyscale"></a>
## LayerAxisYScale
Per-layer y-axis scale patch.

| Field | Type | Description |
|-------|------|-------------|
| `domain` | list[float] | Explicit [min, max] domain for this layer's y scale. |

<a id="layeraxisyticks"></a>
## LayerAxisYTicks
Per-layer y-axis tick patch.

| Field | Type | Description |
|-------|------|-------------|
| `count` | int | Target number of ticks on this layer's y axis; a target, never an exact count. |

<a id="layeraxisygrid"></a>
## LayerAxisYGrid
Per-layer y-axis grid patch.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Whether to show grid lines on this layer's y axis. |

<a id="layeraxisylabels"></a>
## LayerAxisYLabels
Per-layer y-axis tick-label format patch.

| Field | Type | Description |
|-------|------|-------------|
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | d3 format string for this layer's y-axis tick labels. |

<a id="linemarkstyle"></a>
## LineMarkStyle
Authored overlay for LineMarkStyle. Line mark stroke, interpolation, and halo. Point mark lives on PointMarkStyle.

| Field | Type | Description |
|-------|------|-------------|
| `stroke` | [StrokeStyle](#strokestyle) | Line stroke style. |
| `curve` | enum: "linear", "monotone", "natural", "basis", "cardinal", "step", "step-before", "step-after" | Line interpolation curve, one of a fixed set: 'linear', 'monotone', 'natural', 'basis', 'cardinal', 'step', 'step-before', 'step-after'. On a categorical (nominal/ordinal) x-axis, 'step' draws a full-band-width plateau per x-value instead of VL's centered step; on a continuous (temporal/quantitative) x-axis it passes straight through to VL's native step. |
| `connect` | bool | For curve='step' on a categorical (band) x-axis, whether adjacent band plateaus are joined by vertical jumps (True/None) or left as disconnected segments (False). No-op for other curves or a continuous x-axis. |
| `disconnected_cap` | enum: "butt", "round", "square" | Stroke line cap for the sub-paths of a disconnected band-step (curve='step' + connect=False on a categorical x-axis). Each band is its own flat path there, so the cap lands on every band edge rather than only the two ends of one continuous line; 'butt' keeps plateaus flush with the band. Overrides stroke.cap in that geometry only; every other line keeps stroke.cap. |
| `halo_multiplier` | float | Halo stroke width multiplier relative to stroke.width; 0 disables the halo. |
| `labels` | [PointLabelsStyle](#pointlabelsstyle) | Value label style for line marks. |

<a id="pointmarkstyle"></a>
## PointMarkStyle
Authored overlay for PointMarkStyle. Point mark style (data-point markers on scatter/point/line/area charts).

| Field | Type | Description |
|-------|------|-------------|
| `size` | float | Point size in square pixels; 0 disables points on line charts. |
| `color` | str | Point color; None inherits the series color. |
| `shape` | str | Point shape (e.g. 'circle', 'square'); None uses VL default. |
| `opacity` | float | Point opacity 0–1; None uses VL default. |
| `filled` | bool | Whether points are filled; None uses VL default. |
| `fill` | str | Point interior fill color; only applied when filled=false. |
| `stroke_width` | float | Stroke width in pixels for hollow point rings; None uses VL default. |
| `labels` | [PointLabelsStyle](#pointlabelsstyle) | Value label style for point marks. On line charts, setting marks.point.labels is an alias for marks.line.labels. |

<a id="rulemarkstyle"></a>
## RuleMarkStyle
Authored overlay for RuleMarkStyle. Rule (reference line) mark opacity and stroke.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Mark opacity (0–1); None means not overridden at this level. |
| `stroke` | [FontColorStrokeStyle](#fontcolorstrokestyle) | Mark stroke style. |

<a id="areamarkstyle"></a>
## AreaMarkStyle
Authored overlay for AreaMarkStyle. Area mark fill opacity and shape.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Area fill opacity (0–1). |
| `curve` | enum: "linear", "monotone", "natural", "basis", "cardinal", "step", "step-before", "step-after" | Area interpolation curve, one of a fixed set: 'linear', 'monotone', 'natural', 'basis', 'cardinal', 'step', 'step-before', 'step-after'. On a categorical (nominal/ordinal) x-axis, 'step' draws a full-band-width plateau per x-value instead of VL's centered step; on a continuous (temporal/quantitative) x-axis it passes straight through to VL's native step. Applied to both the fill and its edge line (marks.line) so they trace the same path. |
| `backdrop` | bool | Whether to paint an opaque fill backdrop behind the area. |
| `stacked` | [AreaStackedMarkStyle](#areastackedmarkstyle) | Recipe override applied when the chart is stacked or has a single series: solid fill + background-color separator stroke. |

<a id="arealinestyle"></a>
## AreaLineStyle
Authored overlay for AreaLineStyle. Stroke/halo/labels for an area chart's top-edge line and value labels.

| Field | Type | Description |
|-------|------|-------------|
| `stroke` | [StrokeStyle](#strokestyle) | Area top-edge line stroke style. |
| `halo_multiplier` | float | Halo stroke width multiplier relative to stroke.width; 0 disables the halo. |
| `labels` | [PointLabelsStyle](#pointlabelsstyle) | Value label style for the area's plotted points; inherits from global. |

<a id="rectmarkstyle"></a>
## RectMarkStyle
Authored overlay for RectMarkStyle. Rect mark opacity and stroke.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Mark opacity (0–1); None means not overridden at this level. |
| `stroke` | [StrokeStyle](#strokestyle) | Mark stroke style; None means not overridden at this level. |

<a id="totalvalueslotstyle"></a>
## TotalValueSlotStyle
Authored overlay for TotalValueSlotStyle. Theme slot for the donut center value (the number): paint plus its format.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Donut center total element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `format` | str \| [FormatConfig](#formatconfig) \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | How the donut center value is written: a D3 spec, a preset name, or a format block. |

<a id="totalslotstyle"></a>
## TotalSlotStyle
Authored overlay for TotalSlotStyle. Theme slot for one text element of the donut center total.

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Donut center total element font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |

<a id="slicemarkstyle"></a>
## SliceMarkStyle
Authored overlay for SliceMarkStyle. Pie/donut slice mark: mark-level paint and layout only.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Arc slice opacity (0–1); None means not overridden at this level. |
| `gap` | float | Angular gap between slices in radians. |
| `corner_radius` | float | Corner radius of arc slices in pixels. |
| `stroke` | [StrokeStyle](#strokestyle) | Arc slice stroke style. |
| `labels` | [SliceLabelsStyle](#slicelabelsstyle) | Per-slice label style. |

<a id="tablerowstripestyle"></a>
## TableRowStripeStyle
Authored overlay for TableRowStripeStyle. Alternating row stripe style.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Alternating stripe background color; None means no alternating row fill. |

<a id="tablerowrolesstyle"></a>
## TableRowRolesStyle
Authored overlay for TableRowRolesStyle.

| Field | Type | Description |
|-------|------|-------------|
| `summary` | [TableRowRoleStyle](#tablerowrolestyle) | Style for summary role rows. |
| `total` | [TableRowRoleStyle](#tablerowrolestyle) | Style for total role rows. |

<a id="sparkconfig"></a>
## SparkConfig
Configuration for spark charts (inline sparklines) in table columns.

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum: "line", "area", "bar", "bar-normalize", "column", "columns" | Spark chart type (line, area, bar, bar-normalize, column, columns). |
| `color` | str | Color for the spark mark. |
| `height` | int | Spark chart height in pixels. |
| `width` | int | Spark chart width in pixels. |
| `last_visible` | bool | Highlight the last data point (line/area spark charts). |
| `min_max_visible` | bool | Annotate the min and max data points (line spark charts only). |
| `fill_opacity` | float | Fill opacity for area spark charts (0–1). |
| `max` | float | Scaling ceiling: bar-normalize and column clamp the value to it; bar uses it in place of the column's data max. |
| `thresholds` | dict[int \| float, str] | Color thresholds for bar / bar-normalize / column: {value: CSS color string}. |
| `background` | str | Background track color for bar-normalize chart. |
| `border_radius` | float | Corner radius in pixels for bar, bar-normalize, and column bars, and for the bar-normalize track. |
| `value_visible` | bool | Show numeric value label alongside the bar. |
| `value_suffix` | str | Text placed after the displayed value (e.g., '%'). |
| `negative_color` | bool | Paint negative values (bar / column / columns) with the theme's tones.negative color instead of the shared spark color. Has no effect on columns with no negative values. |

<a id="columnscaleconfig"></a>
## ColumnScaleConfig
Scale-based continuous color mapping for a table column.

| Field | Type | Description |
|-------|------|-------------|
| `background` | [ScaleTargetConfig](#scaletargetconfig) | Continuous background color mapping for this column. |
| `color` | [ScaleTargetConfig](#scaletargetconfig) | Continuous text color mapping for this column. |

<a id="sparkemptystyle"></a>
## SparkEmptyStyle
Authored overlay for SparkEmptyStyle.

| Field | Type | Description |
|-------|------|-------------|
| `inset_x` | float | Horizontal inset for the empty sparkline placeholder in pixels. |
| `stroke` | [StrokeStyle](#strokestyle) | Empty-state placeholder stroke style. |

<a id="sparksinglevaluestyle"></a>
## SparkSingleValueStyle
Authored overlay for SparkSingleValueStyle.

| Field | Type | Description |
|-------|------|-------------|
| `inset_x` | float | Horizontal inset for the single-value sparkline in pixels. |
| `marker_radius` | float | Radius of the single-value marker circle in pixels. |

<a id="sparkcolumnsstyle"></a>
## SparkColumnsStyle
Authored overlay for SparkColumnsStyle. Inline `spark.type: columns` (multi-value vertical bars) defaults.

| Field | Type | Description |
|-------|------|-------------|
| `gap` | float | Gap between column bars in pixels. |
| `padding` | float | Horizontal outer padding of the columns sparkline in pixels. |
| `min_bar_height` | float | Minimum rendered bar height in pixels. |
| `border` | [CornerStyle](#cornerstyle) | Column bar corner rounding. |

<a id="sparkcolumnstyle"></a>
## SparkColumnStyle
Authored overlay for SparkColumnStyle. Inline `spark.type: column` (single vertical bar) defaults.

| Field | Type | Description |
|-------|------|-------------|
| `width` | float | Spark column default width in pixels. |
| `height` | float | Spark column default height in pixels. |

<a id="sparkbarcellstyle"></a>
## SparkBarCellStyle
Authored overlay for SparkBarCellStyle. Inline `spark.type: bar` and `bar-normalize` (single horizontal bar) defaults.

| Field | Type | Description |
|-------|------|-------------|
| `background` | str | Track background color - fill-grade, pinned per theme. |
| `color` | str | Bar fill color; None seeds from style.charts.color.categorical.single_series_palette[0]. |
| `default_max` | float | Default maximum value for bar scale when no explicit max is authored. |
| `border` | [CornerStyle](#cornerstyle) | Corner rounding for spark bar cells. |
| `font` | [FontStyle](#fontstyle) | Spark bar cell font style overrides. Unset fields fall back to [`style.charts.font`](#chartsstyle). |
| `label` | [SparkBarLabelStyle](#sparkbarlabelstyle) | Spark bar inline label style. |

<a id="sparkareastyle"></a>
## SparkAreaStyle
Authored overlay for SparkAreaStyle.

| Field | Type | Description |
|-------|------|-------------|
| `fill_opacity` | float | Spark area fill opacity (0–1). |

<a id="geoshapemarkstyle"></a>
## GeoshapeMarkStyle
Authored overlay for GeoshapeMarkStyle. Geoshape (choropleth) mark fill and boundary stroke.

| Field | Type | Description |
|-------|------|-------------|
| `fill` | str | Neutral geoshape fill color. |
| `stroke` | [StrokeStyle](#strokestyle) | Geoshape boundary stroke style. |

<a id="columnrulestyle"></a>
## ColumnRuleStyle
Authored overlay for ColumnRuleStyle. Vertical rule drawn between prose columns, structured like BorderStyle.

| Field | Type | Description |
|-------|------|-------------|
| `width` | float | Rule line width in pixels. |
| `color` | str | Rule color as a CSS color string. |
| `style` | enum: "solid", "dashed", "dotted" | Rule line style. |

<a id="tooltipslotstyle"></a>
## TooltipSlotStyle
Authored overlay for TooltipSlotStyle. Typography for a single tooltip slot (label or value).

| Field | Type | Description |
|-------|------|-------------|
| `font` | [FontStyle](#fontstyle) | Font overrides for this tooltip slot (color, weight). Unset fields fall back to [`style.charts.font`](#chartsstyle). |

<a id="tooltipborderstyle"></a>
## TooltipBorderStyle
Authored overlay for TooltipBorderStyle. Tooltip box border: all fields required; theme YAML supplies defaults.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Border color as a CSS color string. |
| `width` | float | Border width in pixels. |
| `radius` | float | Border corner radius in pixels. |

<a id="tooltipshadowstyle"></a>
## TooltipShadowStyle
Authored overlay for TooltipShadowStyle. Tooltip drop-shadow toggle. JS applies the shadow expression when visible=true.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show a drop-shadow on the tooltip box; theme always provides this. |

<a id="tooltipswatchstyle"></a>
## TooltipSwatchStyle
Authored overlay for TooltipSwatchStyle. Series color swatch in the tooltip: the mark-colored chip next to each

| Field | Type | Description |
|-------|------|-------------|
| `size` | float | Swatch edge length in pixels (square). |
| `radius` | float | Swatch corner radius in pixels (0 = square, ~half of size = circle). |

<a id="circlemarkstyle"></a>
## CircleMarkStyle
Authored overlay for CircleMarkStyle. Circle mark opacity and stroke.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Mark opacity (0–1); None means not overridden at this level. |
| `stroke` | [StrokeStyle](#strokestyle) | Unset emits no stroke; Vega-Lite's default applies. |

<a id="histogramchartmarksstyle"></a>
## HistogramChartMarksStyle
Authored overlay for HistogramChartMarksStyle. Histogram-family mark overrides.

| Field | Type | Description |
|-------|------|-------------|
| `bar` | [BarMarkStyle](#barmarkstyle) | Bar mark overrides; inherits from global. |
| `rule` | [RuleMarkStyle](#rulemarkstyle) | Rule mark overrides; None inherits global. |

<a id="serieslabelfontstyle"></a>
## SeriesLabelFontStyle
Authored overlay for SeriesLabelFontStyle. Series-label typography with a compact width-tier size.

| Field | Type | Description |
|-------|------|-------------|
| `family` | str | Font family name (e.g., 'sans-serif', 'Roboto'). |
| `color` | str | Text color as a CSS color string. |
| `size` | float | Font size in pixels. |
| `weight` | str \| float | How heavy the type is drawn (e.g., 'bold', 400, 700). |
| `style` | enum: "normal", "italic" | Upright or slanted type (normal or italic). |
| `decoration` | enum: "none", "line-through", "underline" | Line drawn on the text (underline, line-through, or none). |
| `case` | enum: "none", "sentence", "title", "upper", "lower", "slug", "camel" | Letter-case transform applied at render time. 'title' uses Chicago/Gruber rules and preserves tokens with internal capitals (ARR, iPhone). 'sentence' uppercases only the first character. 'none' (default) emits the string without any letter-case change. |
| `line_height` | float | Line height as a unitless multiple of font size. Cascades through Style.font to all text roles that carry a FontStyle slot. Body prose defaults to 1.25; titles override tighter via their font patch in theme YAML. |
| `compact_size` | float | Series-label font size in pixels on tiny and narrow cards. |
| `compact_weight` | str \| float | Series-label font weight on tiny and narrow cards. |

<a id="detailsarrowstyle"></a>
## DetailsArrowStyle
Authored overlay for DetailsArrowStyle. Layout and font style for the expand/collapse arrow chevron.

| Field | Type | Description |
|-------|------|-------------|
| `x` | float | X position of the arrow in pixels. |
| `font` | [DetailsArrowFontStyle](#detailsarrowfontstyle) | Arrow glyph font style. |

<a id="inputwidths"></a>
## InputWidths
Authored overlay for InputWidths.

| Field | Type | Description |
|-------|------|-------------|
| `text` | float | Default width for text inputs in pixels. |
| `number` | float | Default width for number inputs in pixels. |
| `range` | float | Default width for range inputs in pixels. |
| `slider_value_min` | float | Minimum width for slider value display in pixels. |
| `checkbox` | float | Default width for checkbox inputs in pixels. |
| `daterange` | float | Default width for daterange chip triggers in pixels. |

<a id="rangedefaults"></a>
## RangeDefaults
Authored overlay for RangeDefaults.

| Field | Type | Description |
|-------|------|-------------|
| `default_min` | float | Default minimum value for range inputs. |
| `default_max` | float | Default maximum value for range inputs. |
| `default_step` | float | Default step size for range inputs. |

<a id="axisgridthresholdstyle"></a>
## AxisGridThresholdStyle
Authored overlay for AxisGridThresholdStyle. Threshold-rule color, width, and visibility overrides.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show the threshold rule; None inherits from parent axis. |
| `color` | str | Color of the threshold rule; None inherits from parent axis. |
| `width` | float | Width of the threshold rule in pixels; None inherits from parent axis. |

<a id="axislabeloverlapconfig"></a>
## AxisLabelOverlapConfig
Authored overlay for AxisLabelOverlapConfig. Two-bool overlap strategy enablement for x-axis labels.

| Field | Type | Description |
|-------|------|-------------|
| `tilt` | bool | Enable label tilt strategy; None inherits from parent axis. |
| `skip` | bool | Enable temporal label thinning; ignored on categorical axes; None inherits from parent axis. |

<a id="scalecontinuousstyle"></a>
## ScaleContinuousStyle
Authored overlay for ScaleContinuousStyle. Continuous-scale-only config: type, domain, zero-baseline, log/pow/symlog params.

| Field | Type | Description |
|-------|------|-------------|
| `zero` | bool \| const: "auto" | Force scale zero-baseline: True/False pins it; "auto" runs the dbt charts smart-zero heuristic; None passes through to Vega-Lite. |
| `type` | enum: "linear", "log", "pow", "sqrt", "symlog", "temporal" | Scale type override. 'linear'/'log'/'pow'/'sqrt'/'symlog' pass straight through to Vega-Lite's quantitative scale.type. 'temporal' is a dbt charts escape hatch for a cartesian x-axis: it forces a continuous temporal scale instead of the auto-inferred ordinal/nominal bucketed type, which is required before an authored ``domain`` of ISO dates can extend the visible range past the data extent. None lets dbt charts/Vega-Lite infer from the field type. |
| `domain` | tuple[int \| float \| str, int \| float \| str] | Explicit [low, high] scale domain; None lets Vega-Lite auto-determine from data. Must have exactly 2 elements: dbt charts only wires a continuous range override through to Vega-Lite, not an explicit category enumeration. Neither element may be null; pin both bounds, or omit the key to fit the data. When ``type: temporal`` is set, both elements must be ISO-8601 date/datetime strings. |
| `log` | [ScaleLogStyle](#scalelogstyle) | Log-scale param (base); only meaningful with type: log. |
| `pow` | [ScalePowStyle](#scalepowstyle) | Power-scale param (exponent); only meaningful with type: pow. |
| `symlog` | [ScaleSymlogStyle](#scalesymlogstyle) | Symlog-scale param (constant); only meaningful with type: symlog. |

<a id="supporttablerowpaddingstyle"></a>
## SupportTableRowPaddingStyle
Authored overlay for SupportTableRowPaddingStyle.

| Field | Type | Description |
|-------|------|-------------|
| `vertical` | float | Vertical (top/bottom) padding inside support_table rows in pixels. |
| `horizontal` | float | Horizontal (left/right) padding inside support_table rows in pixels. |

<a id="barlabelsstyle"></a>
## BarLabelsStyle
Authored overlay for BarLabelsStyle. Bar mark value-label config. Extends MarkLabelsStyle with bar-specific positions.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show numeric value labels on each mark; False by default. |
| `field` | str | Column to source label text from; None uses the chart's y-field. |
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Number format string for value labels. |
| `dx` | int | Horizontal pixel offset for value labels; overrides the position default. |
| `dy` | int | Vertical pixel offset for value labels; overrides the position default. |
| `font` | [FontStyle](#fontstyle) | Value label font style (color, size, family, etc.). |
| `position` | enum: "above", "top", "middle", "middle_aligned", "bottom" | Label position relative to the bar. 'above' places labels above the bar top (outside). 'top' places labels just inside the top edge. 'middle' centers labels vertically in the bar. 'middle_aligned' centers all labels at a common height (mean of bar heights / 2). 'bottom' places labels just inside the bottom edge. |

<a id="bartotallabelstyle"></a>
## BarTotalLabelStyle
Authored overlay for BarTotalLabelStyle. Stack total label style for bar marks.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show stack total labels above each bar stack. Only takes effect on stacked bar charts; ignored otherwise. |
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Number format string for stack total labels. |
| `dx` | int | Horizontal pixel offset for stack total labels. |
| `dy` | int | Vertical pixel offset for stack total labels. |
| `font` | [FontStyle](#fontstyle) | Stack total label font style overrides; cascade fills missing fields from charts.font. Unset fields fall back to [`style.charts.font`](#chartsstyle). |

<a id="strokestyle"></a>
## StrokeStyle
Authored overlay for StrokeStyle. Stroke appearance sub-block shared across mark families.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Stroke color as a CSS color string. |
| `width` | float | Stroke width in pixels. |
| `cap` | enum: "butt", "round", "square" | How the stroke's ends are finished (butt, round, or square). |
| `join` | enum: "miter", "round", "bevel" | How two stroke segments are joined (miter, round, or bevel). |
| `dasharray` | str | Lengths of the dashes and the blanks between them (e.g. '4 2'). |

<a id="pointlabelsstyle"></a>
## PointLabelsStyle
Authored overlay for PointLabelsStyle. Point/line mark value-label config. Used by both LineMarkStyle and PointMarkStyle.

| Field | Type | Description |
|-------|------|-------------|
| `visible` | bool | Show numeric value labels on each mark; False by default. |
| `field` | str | Column to source label text from; None uses the chart's y-field. |
| `format` | str \| enum: "currency", "currency_full", "currency_whole", "date_short", "delta", "integer", "number", "number_full", "percent", "percent_delta", "percent_whole", "time_short", "year" | Number format string for value labels. |
| `dx` | int | Horizontal pixel offset for value labels; overrides the position default. |
| `dy` | int | Vertical pixel offset for value labels; overrides the position default. |
| `font` | [FontStyle](#fontstyle) | Value label font style (color, size, family, etc.). |
| `position` | enum: "top", "bottom", "left", "right", "middle" | Label position relative to the point. 'top' places labels above the point. 'bottom' places labels below. 'left' to the left. 'right' to the right. 'middle' centers on the point. |

<a id="fontcolorstrokestyle"></a>
## FontColorStrokeStyle
Authored overlay for FontColorStrokeStyle. Stroke for rule marks whose color defaults to the root font color.

| Field | Type | Description |
|-------|------|-------------|
| `color` | str | Stroke color for rule marks. Falls back to [`style.font.color`](#rootfontstyle). |
| `width` | float | Stroke width in pixels. |
| `cap` | enum: "butt", "round", "square" | How the stroke's ends are finished (butt, round, or square). |
| `join` | enum: "miter", "round", "bevel" | How two stroke segments are joined (miter, round, or bevel). |
| `dasharray` | str | Lengths of the dashes and the blanks between them (e.g. '4 2'). |

<a id="areastackedmarkstyle"></a>
## AreaStackedMarkStyle
Authored overlay for AreaStackedMarkStyle. Stacked / streamgraph area recipe override: solid fill + perimeter stroke.

| Field | Type | Description |
|-------|------|-------------|
| `opacity` | float | Area fill opacity (0–1) for the stacked recipe. |
| `stroke` | [StrokeStyle](#strokestyle) | Perimeter stroke style for the stacked recipe. |
| `halo_multiplier` | float | Halo stroke width multiplier for the stacked recipe; 0 disables the halo. |

<a id="slicelabelsstyle"></a>
## SliceLabelsStyle
Authored overlay for SliceLabelsStyle. Pie labels: typography + positioning offsets for per-slice text.

| Field | Type | Description |
|-------|------|-------------|
| `offset` | float | Radial offset of slice labels from the arc in pixels. |
| `line_height` | float | Line height for slice labels in pixels. Reserved vertical space above the disk is ``line_height × &lt;rendered lines&gt;`` per row, so the same value handles 1-line, 2-line, and multi-line templates. |
| `font` | [FontStyle](#fontstyle) | Slice label font style overrides. ``color`` only takes effect on single-series pies (no ``color:`` channel authored); multi-series pies always paint each label the dark companion of its own wedge color and ignore an authored ``color`` here. Unset fields fall back to [`style.charts.font`](#chartsstyle) (except `color`). |
| `default_template` | [LabelsDefaultTemplate](#labelsdefaulttemplate) | Default Jinja templates for per-slice labels when template is not authored. |
| `template` | str | Jinja2 label template. Overrides default_template when authored. |
| `where` | str | Jinja2 boolean filter; labels only render on rows where this is truthy. |

<a id="tablerowrolestyle"></a>
## TableRowRoleStyle
Authored overlay for TableRowRoleStyle.

| Field | Type | Description |
|-------|------|-------------|
| `rule_width` | float | Rule width above rows with this role in pixels. |
| `font` | [FontStyle](#fontstyle) | Per-role font style override; None uses the default row font. |
| `background` | str | Per-role row background color; None means no override. |

<a id="sparkbarlabelstyle"></a>
## SparkBarLabelStyle
Authored overlay for SparkBarLabelStyle.

| Field | Type | Description |
|-------|------|-------------|
| `inset_x` | float | Horizontal inset for the spark bar label in pixels. |
| `fill` | str | Label text fill color. |
| `fill_opacity` | float | Label text fill opacity (0–1). |
| `min_size` | float | Minimum bar fill width required to show the label in pixels. |
| `height_offset` | float | Vertical offset of the label from its bar top in pixels. |

<a id="detailsarrowfontstyle"></a>
## DetailsArrowFontStyle
Authored overlay for DetailsArrowFontStyle. Font style for the expand/collapse arrow glyph.

| Field | Type | Description |
|-------|------|-------------|
| `size` | float | Font size of the arrow glyph in pixels. |

<a id="scalelogstyle"></a>
## ScaleLogStyle
Authored overlay for ScaleLogStyle. Log-scale-only param.

| Field | Type | Description |
|-------|------|-------------|
| `base` | float | Log base; only meaningful with type: log. None lets Vega-Lite apply its own default (10). |

<a id="scalepowstyle"></a>
## ScalePowStyle
Authored overlay for ScalePowStyle. Power-scale-only param.

| Field | Type | Description |
|-------|------|-------------|
| `exponent` | float | Power exponent; only meaningful with type: pow. None uses Vega-Lite's default. |

<a id="scalesymlogstyle"></a>
## ScaleSymlogStyle
Authored overlay for ScaleSymlogStyle. Symlog-scale-only param.

| Field | Type | Description |
|-------|------|-------------|
| `constant` | float | Symlog constant; only meaningful with type: symlog. None uses Vega-Lite's default. |

<a id="labelsdefaulttemplate"></a>
## LabelsDefaultTemplate
Authored overlay for LabelsDefaultTemplate. Default Jinja templates for per-slice pie/donut labels.

| Field | Type | Description |
|-------|------|-------------|
| `with_color` | str | Default per-slice label template when the chart has a color binding. |
| `no_color` | str | Default per-slice label template when the chart has no color binding. |

<a id="postgressourceconfig"></a>
## PostgresSourceConfig
Postgres source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "postgres" |  |
| `host` | str | Database host name or IP address. |
| `dbname` | str | Database name (dbt accepts database as an input alias). |
| `user` | str | Database user name. |
| `password` | str | Database password (dbt accepts pass as an input alias). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `port` | int | Database port number. |
| `schema` | str | Default schema for queries. |
| `connect_timeout` | int | Connection timeout in seconds. |
| `role` | str | PostgreSQL role to assume after connecting. |
| `search_path` | str | PostgreSQL search_path for the connection. |
| `keepalives_idle` | int | Idle seconds before TCP keepalives begin. |
| `sslmode` | enum: "disable", "allow", "prefer", "require", "verify-ca", "verify-full" | libpq SSL mode forwarded to psycopg2. None lets libpq decide its default. |
| `sslcert` | str | Path to the client SSL certificate. |
| `sslkey` | str | Path to the client SSL private key. |
| `sslrootcert` | str | Path to the trusted SSL certificate authority file. |
| `application_name` | str | Application name reported to PostgreSQL. |
| `retries` | int | Number of connection retry attempts. |

<a id="snowflakesourceconfig"></a>
## SnowflakeSourceConfig
Snowflake source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "snowflake" |  |
| `account` | str | Snowflake account identifier (e.g. xy12345.us-east-1). |
| `database` | str | Snowflake database name. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `user` | str | Snowflake user name. Omit for token-based authentication when supported. |
| `password` | str | Snowflake password. Omit when using OAuth or key-pair auth. |
| `warehouse` | str | Snowflake virtual warehouse name. |
| `schema` | str | Default schema for queries. |
| `role` | str | Snowflake role to assume for the session. |
| `authenticator` | str | Snowflake authenticator name or external-browser mode. |
| `private_key` | str | PEM private key used for key-pair authentication. |
| `private_key_path` | str | Path to a PEM private key for key-pair authentication. |
| `private_key_passphrase` | str | Passphrase for the configured private key. |
| `token` | str | OAuth access token for token authentication. |
| `oauth_client_id` | str | OAuth client identifier. |
| `oauth_client_secret` | str | OAuth client secret. |
| `query_tag` | str | Snowflake query tag applied to statements. |
| `client_session_keep_alive` | bool | Whether Snowflake keeps the client session alive. |
| `host` | str | Snowflake host override. |
| `port` | int | Snowflake port override. |
| `proxy_host` | str | HTTP proxy host for Snowflake connections. |
| `proxy_port` | int | HTTP proxy port for Snowflake connections. |
| `protocol` | str | Network protocol for the Snowflake connection. |
| `connect_retries` | int | Number of retries while opening a connection. |
| `connect_timeout` | int | Connection timeout in seconds. |
| `retry_on_database_errors` | bool | Whether database errors are retried. |
| `retry_all` | bool | Whether all connection errors are retried. |
| `insecure_mode` | bool | Whether TLS certificate verification is disabled. |
| `reuse_connections` | bool | Whether dbt reuses Snowflake connections. |
| `s3_stage_vpce_dns_name` | str | Private VPC endpoint DNS name for S3 staging. |
| `platform_detection_timeout_seconds` | float | Timeout for Snowflake platform detection. |

<a id="bigquerysourceconfig"></a>
## BigQuerySourceConfig
BigQuery source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "bigquery" |  |
| `project` | str | GCP project ID. |
| `dataset` | str | BigQuery dataset name (equivalent to schema). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `keyfile` | str | Path to service account JSON key file. |
| `keyfile_json` | dict[str, str \| int \| float \| bool] | Inline service account JSON dict. |
| `location` | str | Dataset location (e.g. US, EU). |
| `method` | enum: "oauth", "oauth-secrets", "service-account", "service-account-json", "external-oauth-wif" | dbt-bigquery authentication method. Inferred from keyfile/keyfile_json when omitted: 'service-account-json' if keyfile_json set, 'service-account' if keyfile set, 'oauth' (Application Default Credentials) otherwise. |
| `execution_project` | str | GCP project billed for BigQuery execution. |
| `quota_project` | str | GCP project used for quota attribution. |
| `api_endpoint` | str | BigQuery API endpoint override. |
| `priority` | enum: "interactive", "batch" | BigQuery job priority. |
| `maximum_bytes_billed` | int | Maximum bytes a BigQuery job may bill. |
| `impersonate_service_account` | str | Service account to impersonate for BigQuery jobs. |
| `job_retry_deadline_seconds` | int | Deadline in seconds for retrying a BigQuery job. |
| `job_retries` | int | Number of BigQuery job retry attempts (dbt accepts retries as an input alias). |
| `job_creation_timeout_seconds` | int | Timeout in seconds while creating a BigQuery job. |
| `job_execution_timeout_seconds` | int | Timeout in seconds while executing a BigQuery job. |
| `token` | str | OAuth access token. |
| `refresh_token` | str | OAuth refresh token. |
| `client_id` | str | OAuth client identifier. |
| `client_secret` | str | OAuth client secret. |
| `token_uri` | str | OAuth token endpoint URI. |
| `workload_pool_provider_path` | str | Workload identity pool provider resource path. |
| `service_account_impersonation_url` | str | Service-account impersonation endpoint URL. |
| `token_endpoint` | dict[str, str] | OAuth token endpoint configuration. |
| `compute_region` | str | Dataproc compute region. |
| `dataproc_cluster_name` | str | Dataproc cluster name. |
| `gcs_bucket` | str | Cloud Storage bucket for Dataproc submission. |
| `submission_method` | str | dbt-bigquery Dataproc submission method. |
| `dataproc_batch` | dict[str, str \| int \| float \| bool] | Dataproc batch configuration. |
| `scopes` | list[str] | OAuth scopes requested for BigQuery credentials. |

<a id="redshiftsourceconfig"></a>
## RedshiftSourceConfig
Redshift source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "redshift" |  |
| `host` | str | Redshift cluster host name. |
| `dbname` | str | Redshift database name (dbt accepts database as an input alias). |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `port` | int | Redshift port number. |
| `schema` | str | Default schema for queries. |
| `user` | str | Redshift user name. Omit for IAM-based authentication when supported. |
| `password` | str | Redshift password. Omit for IAM-based authentication when supported. |
| `method` | str | Redshift authentication method. |
| `cluster_id` | str | Redshift cluster identifier for IAM authentication. |
| `iam_profile` | str | IAM profile ARN used for Redshift authentication. |
| `autocreate` | bool | Whether dbt may create the database user. |
| `db_groups` | list[str] | Redshift database groups assigned to the user. |
| `ra3_node` | bool | Whether the target uses Redshift RA3 nodes. |
| `connect_timeout` | int | Connection timeout in seconds. |
| `role` | str | IAM role ARN used for Redshift operations. |
| `sslmode` | str | SSL verification mode for the Redshift connection. |
| `retries` | int | Number of Redshift connection retry attempts. |
| `retry_all` | bool | Whether all connection errors are retried. |
| `region` | str | AWS region containing the Redshift target. |
| `access_key_id` | str | AWS access key identifier. |
| `secret_access_key` | str | AWS secret access key. |
| `idc_region` | str | AWS IAM Identity Center region. |
| `issuer_url` | str | Identity-provider issuer URL. |
| `idp_listen_port` | int | Local port used for identity-provider callbacks. |
| `idc_client_display_name` | str | IAM Identity Center client display name. |
| `idp_response_timeout` | int | Identity-provider response timeout in seconds. |
| `token_endpoint` | dict[str, str] | Identity-provider token endpoint configuration. |
| `is_serverless` | bool | Whether the target is Redshift Serverless. |
| `serverless_work_group` | str | Redshift Serverless workgroup name. |
| `serverless_acct_id` | str | AWS account identifier for Redshift Serverless. |
| `tcp_keepalive` | bool | Whether TCP keepalives are enabled. |
| `tcp_keepalive_idle` | int | Idle seconds before TCP keepalives begin. |
| `tcp_keepalive_interval` | int | Seconds between TCP keepalive probes. |
| `tcp_keepalive_count` | int | Number of TCP keepalive probes before failure. |

<a id="mysqlsourceconfig"></a>
## MySQLSourceConfig
MySQL source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "mysql" |  |
| `host` | str | MySQL host name or IP address. |
| `database` | str | MySQL database name. |
| `user` | str | MySQL user name. |
| `password` | str | MySQL password. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `port` | int | MySQL port number. |
| `schema` | str | Default schema for queries. |

<a id="duckdbsourceconfig"></a>
## DuckDBSourceConfig
DuckDB source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "duckdb" |  |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `max_query_duration_seconds` | int | Maximum execution time for one query in seconds. Overrides execution.max_query_duration_seconds for this source. |
| `path` | str | DuckDB file path or ':memory:' for an in-memory database. |
| `schema` | str | Default schema for unqualified table names (sets search_path). |
| `duckdb_config` | dict[str, str \| int \| float \| bool] | DuckDB connection configuration values, such as enable_external_access. |
| `extensions` | list[str \| dict[str, str]] | DuckDB extensions to install and load. |
| `settings` | dict[str, str \| int \| float \| bool] | DuckDB settings and pragma values. |
| `secrets` | list[dict[str, str \| int \| float \| bool]] | DuckDB secret definitions for external services. |
| `external_root` | str | Root path for dbt-duckdb external materializations. |
| `use_credential_provider` | str | Credential-provider chain used for external services. |
| `attach` | list[DuckDBAttachmentConfig] | Databases to attach to the DuckDB connection. |
| `filesystems` | list[dict[str, str \| int \| float \| bool]] | fsspec filesystem configurations attached to DuckDB. |
| `remote` | DuckDBRemoteConfig | Remote DuckDB connection configuration. |
| `plugins` | list[DuckDBPluginConfig] | dbt-duckdb plugin configurations. |
| `disable_transactions` | bool | Whether dbt-duckdb disables statement transactions. |
| `keep_open` | bool | Whether dbt-duckdb holds its connection open between queries. Off by default: a held handle pins the database file at one DuckDB config, and DuckDB refuses any other connection to a pinned file whose config differs. Ignored for ':memory:' and MotherDuck, which dbt-duckdb holds open either way; closing an in-memory database would destroy it. |
| `module_paths` | list[str] | Python module paths dbt-duckdb loads. |
| `retries` | DuckDBRetriesConfig | dbt-duckdb connection and query retry configuration. |
| `is_ducklake` | bool | Whether this source uses DuckLake. |

<a id="sqlitesourceconfig"></a>
## SQLiteSourceConfig
SQLite source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "sqlite" |  |
| `path` | str | Path to the SQLite database file. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |

<a id="csvsourceconfig"></a>
## CsvSourceConfig
CSV file source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "csv" |  |
| `files` | dict[str, str] | Mapping of table_name → file path or glob (``*``/``?``), relative to the project root. A glob must match at least one file; matched files must share one column schema and concatenate into one table. Limits: max 1000 files per glob, max 500 tables, max 5 GB per table (``execution.max_glob_file_count`` / ``file_source_max_tables`` / ``file_source_max_bytes``). Required, non-empty. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `delimiter` | str | Field delimiter character. |
| `encoding` | str | File encoding. |

<a id="parquetsourceconfig"></a>
## ParquetSourceConfig
Parquet file source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "parquet" |  |
| `files` | dict[str, str] | Mapping of table_name → file path or glob (``*``/``?``), relative to the project root. A glob must match at least one file; matched files must share one column schema and concatenate into one table. Limits: max 1000 files per glob, max 500 tables, max 5 GB per table (``execution.max_glob_file_count`` / ``file_source_max_tables`` / ``file_source_max_bytes``). Required, non-empty. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |

<a id="jsonsourceconfig"></a>
## JsonSourceConfig
JSON file source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "json" |  |
| `files` | dict[str, str] | Mapping of table_name → file path or glob (``*``/``?``), relative to the project root. A glob must match at least one file; matched files concatenate into one table. Limits: max 1000 files per glob, max 500 tables, max 5 GB per table (``execution.max_glob_file_count`` / ``file_source_max_tables`` / ``file_source_max_bytes``). Required, non-empty. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `union_by_name` | bool | When True, rows from different files in a glob set are unified by column name: columns absent in a particular file are filled with NULL.  When False (default), all files in a glob set must share the same column schema; a mismatch raises an error. |

<a id="httpsourceconfig"></a>
## HttpSourceConfig
HTTP/REST API source configuration.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "http" |  |
| `url` | str | Base URL for HTTP requests. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `headers` | dict[str, str] | Default HTTP headers (e.g. Authorization). |

<a id="dbtprofilesourceconfig"></a>
## DbtProfileSourceConfig
Reference to a dbt profile.

**Required**

| Field | Type | Description |
|-------|------|-------------|
| `type` | const: "dbt_profile" |  |
| `profile` | str | dbt profile name from profiles.yml. |

**Optional**

| Field | Type | Description |
|-------|------|-------------|
| `cache` | [Cache](#cache) | Cache policy default for every query against this source, e.g. cache: 1h: queries inherit it and may refine it; cache: false opts them out. |
| `attribution` | dict[str, str] | Cost-attribution pairs sent with every query against this source, e.g. attribution: {team: analytics}. Emitted as BigQuery job labels and as a query comment elsewhere. Keys and values must match BigQuery's label rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix and the app key are reserved for the engine's own identity. |
| `target` | str | dbt target to use; defaults to the profile's default target. |
| `profiles_dir` | str | Directory containing profiles.yml, relative to the linked dbt project directory (see --dbt-project-dir). Use when profiles.yml is in a subdirectory (e.g. services/dbt). Resolution order: profiles_dir → $DBT_PROFILES_DIR → linked dbt project → ~/.dbt. |

# dbt charts Project Config Reference: execution settings

Settings authored under `execution:` in `dbt_charts.yml` at the project root, not in a board. The file's other sections (`sources:`, `server:`, `cache:`, ...) are not generated here yet.

<a id="executionconfig"></a>
## ExecutionConfig

| Field | Type | Description |
|-------|------|-------------|
| `max_workers` | int | Maximum parallel query workers for a render. DuckDB serializes access regardless, so this only moves external warehouses. |
| `max_query_duration_seconds` | int | Maximum seconds a single query may run (must be &gt; 0). |
| `max_glob_file_count` | int | Maximum files a single glob may match (must be &gt; 0). |
| `file_source_max_tables` | int | Max tables in a files: map (must be &gt; 0). |
| `file_source_max_bytes` | int | Max uncompressed bytes per file-source table (must be &gt; 0). |
| `max_rows` | int | Maximum rows a single query may return (must be &gt; 0). |
| `max_result_bytes` | int | Maximum serialized byte size of a single query result (must be &gt; 0). |
| `max_template_output_bytes` | int | Maximum cumulative bytes of Jinja-emitted template output for a single board render (must be &gt; 0). |
| `dialect_aliases` | dict[str, str] | Maps a dbt charts dialect name to its sqlglot equivalent before parsing. |
