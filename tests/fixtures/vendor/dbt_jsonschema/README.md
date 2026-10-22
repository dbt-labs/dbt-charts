dbt's published property-file schemas, from [dbt-labs/dbt-jsonschema](https://github.com/dbt-labs/dbt-jsonschema) at `6a401244651d3b5e0fd2f7cfcfb120187118edb4`. Emitted exposures must pass both (dbt-core and Fusion each reject shapes the other accepts).

Refresh:

```bash
sha=<commit>
base=https://raw.githubusercontent.com/dbt-labs/dbt-jsonschema/$sha/schemas
curl -sSfLo dbt_yml_files-latest.json $base/latest/dbt_yml_files-latest.json
curl -sSfLo dbt_yml_files-latest-fusion.json $base/latest_fusion/dbt_yml_files-latest-fusion.json
```
