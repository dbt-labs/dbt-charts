---
name: troubleshooting
kind: workflow
description: >
  Diagnose and fix dbt charts dashboard errors. Use when validation, query
  execution, query inspection, or dashboard rendering returns an error, when a
  dashboard renders but looks wrong, when SQL fails, when YAML won't validate,
  or when the user says 'fix this error', 'debug this dashboard', 'why is this
  broken', 'chart shows no data'. Covers YAML validation failures, SQL
  execution errors, rendering issues, and variable misconfiguration. Do NOT
  use for building new dashboards from scratch (use {{ s_skill_name_build }}). Do NOT
  use for design quality review (use {{ s_skill_name_design_board }}).
metadata:
  author: fivetran
---

# Troubleshooting dbt charts

Systematically diagnose and fix errors when building dbt charts dashboards. Never guess — use the tools to confirm.

## The Iron Rule

**Never change working YAML to "fix" a problem you haven't diagnosed.** Read the error message. Reproduce it. Understand the cause. Then fix it.

## Diagnostic Workflow

```
Error occurs
  → Read the full error message (not just the first line)
  → Classify: YAML error, SQL error, or rendering issue?
  → Apply the relevant fix pattern below
  → Validate again to confirm the fix
```

## YAML Validation Errors

{{#if_tool validate_board}}These come from `{{ s_validate_board }}`. {{/if_tool}}The error message tells you exactly what's wrong.

### "must have at least one layout"

Every board needs visible content. Add a chart under `charts:`, an explicit
layout (`rows:`, `cols:`, `grid:`, or `tabs:`), or text/title content. If
`charts:` is present and no layout key is present, dbt charts renders those
charts as implicit rows.

### "references unknown chart 'X'"

**The most common error.** Layout references *charts*, not queries. The fix:

```yaml
# WRONG — referencing a query name in layout
queries:
  my_query:
    sql: SELECT ...
rows:
  - my_query              # ❌ This is a query, not a chart

# RIGHT — create a chart that references the query
queries:
  my_query:
    sql: SELECT ...
charts:
  my_chart:
    query: my_query       # Chart references query
    type: table
rows:
  - my_chart              # ✅ Layout references chart
```

**Structure order: Queries → Charts → Layout.** You cannot skip the Charts layer.

### "missing required: query"

Charts need a `query:` field. Either reference a named query or define one inline:

```yaml
charts:
  my_chart:
    query: my_query           # Reference a named query
    type: bar
    x: category
    y: value

  # OR inline
  my_chart:
    query:
      sql: SELECT ...
      source: my_profile
    type: bar
    x: category
    y: value
```

### "source required" / "missing source"

SQL queries must specify which data source to use. Either set a top-level `source:` or specify per-query:

```yaml
source: my_profile            # Top-level default

queries:
  uses_default:
    sql: SELECT ...           # Inherits top-level source

  explicit:
    sql: SELECT ...
    source: other_profile     # Overrides for this query
```

### Variable Options Errors

The `default` field goes at the variable level, NOT inside `options`:

```yaml
# WRONG
variables:
  region:
    input: select
    options:
      static: [North, South]
      default: North          # ❌

# RIGHT
variables:
  region:
    input: select
    options:
      static: [North, South]
    default: North            # ✅
```

Use `options.static` for static values, not `options.values`.

## SQL Execution Errors

These come from any of: {{#if_tool query_board}}`{{ s_query_board }}`, {{/if_tool}}`{{ s_execute_query }}`, `{{ s_render_board }}`.

### "table does not exist"

Discover available tables first with ad-hoc SQL against the information schema:

```sql
-- PostgreSQL / Redshift
SELECT table_schema, table_name FROM information_schema.tables;

-- BigQuery
SELECT table_name FROM `project.dataset.INFORMATION_SCHEMA.TABLES`;

-- Snowflake
SHOW TABLES IN SCHEMA my_schema;
```

### "column does not exist"

Check actual column names — don't guess:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'my_table';
```

### Type mismatches

Common causes:

- Comparing a string column to an unquoted value: `WHERE status = active` → `WHERE status = 'active'`
- Date functions that differ between databases (BigQuery vs Postgres vs Snowflake)
- Aggregating a non-numeric column

## Rendering Issues

These show up when `{{ s_render_board }}` produces unexpected visual results.

### Chart shows no data

- Verify the query returns rows: `{{ s_execute_query }}` with the same SQL
- Check that `x:` and `y:` column names match the query's SELECT aliases exactly (case-sensitive)
- Check variable defaults — a restrictive filter might exclude all data

### Chart axes are wrong

- Ensure `x:` is the categorical/time column and `y:` is the numeric column
- For time series, ensure the date column is sorted in the SQL: `ORDER BY date`

### Layout doesn't look right

- Charts in a `cols:` array share the row equally by default, and a bare number
  in the list (`cols: [chart_a, 2]`) is not a span — it does not validate. For
  an uneven split, wrap each side and put `width:` on the wrapper (`"70%"` or
  `"300px"`) — a bare number is pixels, so `width: 2` silently renders a
  2-pixel column rather than a 2:1 share
- Too many items in one `cols:` array = each gets too narrow

{{#if_tool docs}}## Full Error Reference

{{ s_error_reference_footer }} Check it when an error's code (`ERR-*`) isn't
covered by a pattern below.

{{/if_tool}}## Debugging Checklist

When stuck, work through this in order:

1. **Read the full error** — not just the first line. The details matter.
{{#if_tool validate_board}}1. **Validate the YAML** — `{{ s_validate_board }}` catches structural issues before SQL runs.
{{/if_tool}}1. **Test one query at a time** — {{#if_tool query_board}}`{{ s_query_board }}` for saved board queries, {{/if_tool}}`{{ s_execute_query }}` for raw SQL.
1. **Check column names** — INFORMATION_SCHEMA via `{{ s_execute_query }}`{{#if_tool describe_query}}, or `{{ s_describe_query }}` to verify{{/if_tool}}.
1. **Simplify** — Remove charts until you find the one that's broken.
1. **Re-render** — Confirm the fix visually.
1. **Restore metadata** — Ensure `notes` fields remain populated for queries/charts/layout sections and variables.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Changing YAML randomly until errors stop | Read the error. Diagnose first, then fix. |
| Referencing queries in layout | Create a chart that references the query. Layout references charts. |
| Assuming column names | Check INFORMATION_SCHEMA via `{{ s_execute_query }}` to verify |
| Wrong `source` | Source names are the project-configured names listed in your context, not adapter types |
| Variable `default` inside `options` | `default` goes at the variable level, not nested inside `options` |

## Rationalizations to Resist

| Excuse | Reality |
|--------|---------|
| "The error message is confusing, I'll try something else" | Read it again carefully — it tells you what's wrong. |
| "I'll just change the chart type, maybe that'll fix it" | Chart type doesn't fix SQL or YAML structure errors. |
| "It works for similar dashboards" | This data source may have different column names. Verify. |
| "I'll skip validation and just render" | Validation is instant and catches errors before the slow SQL runs. |

## Red Flags — STOP

- About to make a change without understanding the error message
- Changing multiple things at once (change one thing, validate, repeat)
- Guessing column names instead of checking the schema
- The same error keeps coming back (you're not fixing the root cause)
