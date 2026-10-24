---
name: dct
description: >
  Use for any request for a dashboard, board, chart, graph, KPI or report, and
  for any edit under charts/ or to dbt_charts.yml. This project builds them with
  dbt Charts: SQL for the data, YAML for the charts, checked and rendered by the
  dct command line. Read this before writing or editing any board YAML.
metadata:
  author: fivetran
---
# dbt Charts in this project

The board and chart know-how ships inside the installed `dct` command line, so
this file is a pointer and never goes stale. Before authoring, every time:

1. `dct --version` confirms the CLI is installed. If it is missing:
   `uv tool install dbt-charts` (or `pip install dbt-charts`).
2. `dct skills chart` prints the board and chart grammar and the
   build-render-fix loop. Read it first.
3. `dct skills` lists the other skills (styling, custom charts, report design,
   replicating an existing board, Cloud setup). Read the one that matches the task.

With the dbt Charts MCP server configured, `get_skill("chart")` returns the same
text as `dct skills chart`.

Then follow the loop the chart skill describes: write the board YAML under
`charts/`, run `dct validate`, run `dct render`, and fix what they report before
telling the user the board is done.
