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

Before your first board edit in a session, run `dct skills chart` and follow
it: it is the board grammar and how to check a board. If `dct` is missing,
`uv tool install dbt-charts`.

Other skills, only when the task is one (`dct skills <name>`): `styling` (a
look change), `report-design` (a written report), `custom-chart-build` (a shape
no chart type draws), `board-migrate` (a dashboard from another BI tool or a
screenshot), `cloud-setup`, `mcp-setup`.
