---
name: semantic-query
description: 'Write a board query against a source that carries a MetricFlow bundle
  (`metricflow:` on the source): `AGG()` over declared calculations and metrics, `relationship.attribute`
  paths instead of joins, constellations as tables, and the pitfalls that make a semantic
  query fail to compile or fail in the warehouse. Use whenever a query reads a dataset
  or constellation the bundle declares.'
metadata:
  author: fivetran
---
# Semantic query

A source with `metricflow: <dir>` carries a bundle: datasets (tables with
calculations and relationships), entities (keys and attributes) and
constellations (metrics conformed across datasets). A query that names one of
them is compiled into the warehouse's SQL before it runs; everything else is
ordinary SQL on the same connection. A source that sets `required: true` under
`metricflow:` allows no ordinary SQL: a query reading a table the bundle does
not declare is refused with `ERR-METRICFLOW-OUTSIDE-BUNDLE`.


## The grammar

```sql
SELECT account.industry AS industry,          -- relationship.attribute
       DATE_TRUNC(created_at, MONTH) AS month, -- a plain column, in BigQuery's argument order
       AGG(invoiced_total) AS invoiced_total,  -- a declared calculation
       AGG(collection_rate) AS collection_rate -- a ratio calculation, never a SUM
FROM invoices                                  -- the dataset's bare name
WHERE account.country = 'US'
GROUP BY 1, 2
ORDER BY 1, 2
```

- `AGG(<calculation>)` aggregates with the expression the bundle declares. A
  ratio such as `collection_rate` is `paid_total / invoiced_total` inside the
  compiler; `AGG(collection_rate)` is right, `AVG(collection_rate)` is wrong.
- `account.industry` follows the `account` relationship to that entity's
  attribute. Chain them: `document.owner.plan`. The join is declared
  many-to-one, so the aggregate never fans out.
- `FROM <constellation>` reads metrics from several datasets at once, each
  aggregated in its own branch and joined on the conformed roles. Group by the
  roles (`month`, `account.industry`) and read metrics with `AGG()`.
- `WHERE`, `GROUP BY ALL`, `ORDER BY`, `LIMIT`, CTEs and board variables all
  work. `{{ filter('account.industry', industry) }}` applies a dropdown through
  a relationship path, on a dataset or a constellation.

## Don't

- `COUNT(*)`, `SUM(amount)` or `AVG(x)` on a dataset or constellation: the
  bundle already declares the aggregate; use `AGG(<name>)`.
- `GROUP BY` a raw column of a joined table or a hand-written `JOIN`: name the
  path (`account.industry`) and let the compiler join.
- The physical location of a bundle object: `FROM invoices`, never
  `` FROM `proj`.dataset.invoices ``. The warehouse path names the raw table,
  which the bundle does not declare, so the query runs as plain SQL, or is
  refused when the source requires its bundle.
- An output alias that repeats the dataset's name (`AGG(ticket_count) AS
  tickets FROM tickets`): BigQuery resolves the compiled `GROUP BY` against
  the select list first and rejects it. Alias the calculation by its own
  name.

## Write the warehouse's SQL

A query without `dialect:` is read in the source's dialect, so the functions
around the semantic names are the warehouse's: BigQuery takes
`DATE_TRUNC(created_at, MONTH)`, Postgres and DuckDB take
`DATE_TRUNC('month', created_at)`. A compile error names the unknown
calculation, attribute or table; fix the name, do not fall back to plain SQL.
