# dbt charts dbt Example Project

This example demonstrates how to use dbt charts with a dbt project, showcasing the SQL adapter integration.

## Setup

1. **Install dependencies:**
   ```bash
   pip install dbt-core dbt-duckdb dbt-charts
   ```

2. **Generate the dbt manifest:**
   ```bash
   cd examples/tutorial_dbt
   dbt parse --no-send-anonymous-usage-stats
   ```
   `ref()` calls in the charts resolve against `target/manifest.json`, which
   `dbt parse` writes. `target/` is gitignored dbt build output, so a fresh
   clone always needs this step. `data/sample.duckdb` (the DuckDB database
   the charts query) is already committed and populated — `dbt run` and
   `python scripts/setup_sample_data.py` both rewrite that committed file,
   so only run them if you actually want to regenerate the data.

3. **Serve a board:**
   ```bash
   dct serve --project-dir .
   ```
   Board paths map to URLs (`charts/customer_analytics.yml` → `/customer_analytics/`).

## Project Structure

```
tutorial_dbt/
├── dbt_charts.yml        # dbt charts project config
├── dbt_project.yml       # dbt project configuration
├── profiles.yml          # Database connection (DuckDB)
├── models/
│   ├── staging/          # Staging models
│   └── marts/            # Final fact table (fct_orders)
├── charts/
│   ├── customer_analytics.yml  # Customer segments, behavior, and value
│   ├── product_performance.yml
│   ├── sales_dashboard.yml
│   ├── dashboards/, general/, partials/, variables/  # More example boards
├── scripts/
│   └── setup_sample_data.py    # Regenerates data/sample.duckdb
└── data/
    └── sample.duckdb     # DuckDB database with sample data
```

## How It Works

1. **dbt models** transform raw data into `fct_orders`
2. **dbt charts dashboard** queries `fct_orders` using SQL adapter
3. **SQL adapter** uses dbt's adapter system to execute queries
4. **Charts** render the results using Vega-Lite

## Key Features Demonstrated

- ✅ dbt model references (`{{ ref('fct_orders') }}`)
- ✅ Variable filtering (region selector)
- ✅ Multiple chart types (line, bar, KPI)
- ✅ SQL adapter integration with dbt
