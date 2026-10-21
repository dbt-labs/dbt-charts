# dbt Charts boards

Dashboards for this project, authored as YAML and rendered by
[dbt Charts](https://docs.dbtcharts.com).

- `dbt_charts.yml`: project config, data sources and engine settings.
- `charts/`: one board per `.yml` (or `.md`) file. `charts/meta.yml` holds
  defaults every board in the folder inherits.

Preview every board in your browser:

```bash
dct serve
```

Check boards for errors before committing:

```bash
dct validate
```
