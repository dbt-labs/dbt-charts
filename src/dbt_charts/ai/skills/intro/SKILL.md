---
name: intro
kind: workflow
surfaces: [cli]
description: >
  Start here. What dbt Charts is, how to set up a project, and everything
  needed to write a typical board: its shape, its fields, the format names,
  and how to check a render without pictures. Use when asked to 'make charts
  of this with dbt charts' or 'use dbt charts', before any other skill. Do NOT
  use once you know the tool.
metadata:
  author: fivetran
---

# dbt Charts: start here

dbt Charts turns a YAML file into a rendered board: queries (SQL against a
source, or rows written inline), charts that map a query's columns to marks,
and a layout. `dct` is the open-source CLI that validates, renders and serves
those files. dbtcharts.com hosts the same files from a git repository. A local
board needs no account.

The deliverable is board YAML rendered by `dct`, not a plotting library, a
notebook or hand-written HTML.

## 1. Check the install

```bash
dct --version || uv tool install dbt-charts   # or: pip install dbt-charts
```

## 2. Find the data, then the project

Every chart reads a query, and every query reads a source:

- **Rows in hand**: a `type: values` query with the rows inline. No source.
- **A file** (CSV, JSON, Parquet): `source: ./data/orders.csv` on the query, or
  a named source in `dbt_charts.yml` (`type: csv`, a `files:` map of table name
  to path).
- **A warehouse**: a `sources:` entry in `dbt_charts.yml`, or the dbt profile of
  the dbt project you are in. DuckDB and SQLite work locally. dbtcharts.com
  connects to BigQuery, Postgres, Redshift and Snowflake.

A project is a `dbt_charts.yml` beside a `charts/` directory. `dct init --yes`
scaffolds one, with a starter board. For a one-off, `dct render - --format
terminal` reads a board from a pipe.

## 3. Write a board

A board is one YAML file under `charts/`: `source`, `queries` (SQL), `charts`
that read them, and a layout (`rows`/`cols`).

**Check every edit with `dct render <board> --format text`.** It prints each
chart's fields, row count and value ranges, each KPI as painted, and every
warning. Warnings print to stderr: add `2>&1` if you redirect. Open a picture
once, at the end.

Run commands that belong together in one shell call. After an edit:
`dct validate charts/ && dct render charts/sales.yml --format text`.

`dct serve --port 8080` serves `charts/sales.yml` at
`http://localhost:8080/sales/`. Give that board URL in run instructions, never
the server root. Delete `charts/guide.yml`, the starter board, once yours
renders.

### Fields for a typical board

| Field | What it does |
|---|---|
| `title`, `source` | The board's heading, and the default source for its queries. |
| `queries.*.sql` | The statement to run. SQL does the math: a chart never aggregates, filters or joins. |
| `charts.*.type` | `bar`, `line`, `area`, `scatter`, `heatmap`, `histogram`, `pie`, `donut`, `kpi`, `table` and more. |
| `charts.*.query`, `charts.*.title` | The query the chart reads, and a heading that says what it shows. |
| `charts.*.x`, `charts.*.y`, `charts.*.color` | The columns on the two axes, and the column that splits the marks into series. |
| `charts.*.x_label`, `charts.*.y_label` | Axis titles, where a column name would not read well. |
| `charts.*.sort.by`, `charts.*.sort.order` | The column to sort by, and `asc` or `desc`. |
| `charts.*.value`, `charts.*.label` | KPI: the column holding the number, and its caption. |
| `charts.*.support.value`, `charts.*.support.format` | KPI: a second line under the number, such as the change from the prior period. |
| `charts.*.style.number_format` | The format of a chart's measure axis and tooltips. |
| `charts.*.style.value.format` | The format of a KPI's number. |
| `charts.*.style.columns.*.label`, `charts.*.style.columns.*.format` | Table: a column's header and its format. |

A fixed category order (urgent, high, normal, low) belongs in the query: add
an ordering column and `sort.by` it.

### Formats

A format is one of these names: `currency`, `currency_whole`, `currency_full`,
`number`, `number_full`, `integer`, `delta`, `percent`, `percent_whole`,
`percent_delta`, `percent_number`, `percent_number_delta`,
`percentage_points_delta`, `year`, `date_short`, `time_short`. The three
percent formats take a fraction (0.25). The two "percent number" formats and
the points format take a whole number (25), on KPI values and table columns
only. A d3 number or time spec also works.

### Styling a board

Colors: `dct docs color`. Fonts, themes and anything else: `dct docs -s "<what you want>"`.

### Before you hand over

- A title or subtitle says what the chart shows and what to take from it.
- Every rate says what it is a share of ("won ÷ closed opportunities").
- An incomplete last period is cut in the query or named in the subtitle.
- Axis titles are words, not column names.

## 4. Install the skills

In a git repository, install the skills for your harness: `dct init skills claude`
writes them to `.claude/skills/` for Claude Code, and `dct init skills agents` to
`.agents/skills/` for Codex, Cursor and Copilot. An agent's next session finds them by
description. Outside a repository, skip it: `dct skills <name>` prints any skill in full.

## 5. Other skills

None is required for a typical board. Read one only if its condition matches:

| Read this only if you are about to… | Skill |
|---|---|
| Answer an analytical question ("why did X change") | `{{ s_skill_name_analyst_runbook }}` |
| Look at an unfamiliar schema before writing anything | `{{ s_skill_name_data_exploration }}` |
| Choose chart types, layout, or color | `{{ s_skill_name_design_board }}` |
| Write a narrative report rather than a board | `{{ s_skill_name_design_report }}` |
| Reproduce a board from a screenshot or export | `{{ s_skill_name_replicate_board }}` |
| Check the board before handing it over | `{{ s_skill_name_review }}` |
| Fix a render or query error | `{{ s_skill_name_troubleshooting }}` |
| Publish to dbtcharts.com | `{{ s_skill_name_cloud_setup }}` |

`dct skills` lists every skill.

## Done looks like

The user looking at the board: the `dct serve` URL, with the server still
running and you saying so, or the dbtcharts.com URL `dct cloud boards` reports
after `{{ s_skill_name_cloud_setup }}`. Alongside it, one or two sentences on what the data says
and what you assumed about metric, grain, and time window.
