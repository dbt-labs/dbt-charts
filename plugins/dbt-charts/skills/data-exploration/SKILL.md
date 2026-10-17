---
name: data-exploration
description: Explore available sources, schemas, tables, columns, relationships, and
  data shape before writing dashboard YAML or answering analyst questions. Use when
  the user asks what data exists, where to find a metric/dimension, how tables relate,
  or which columns are available. Do NOT use for dashboard design decisions (use board-design)
  or error diagnosis after a failed render (use troubleshooting).
metadata:
  author: fivetran
---
# Data Exploration

Explore the schema with your own SQL through `execute_query` before
writing analysis queries. The warehouse's metadata views are the source of
truth: discover the real data vocabulary, then verify only the small pieces
needed to answer the question.

## Workflow

1. **Know your sources.** The configured data sources (name + dialect) are
   listed in your context. Source names are project-configured names (e.g.
   `db`), not adapter types.
2. **List what exists — scoped, not everything.** Query the dialect's metadata
   views. On most warehouses:

   ```sql
   SELECT table_schema, table_name FROM INFORMATION_SCHEMA.TABLES
   ORDER BY table_schema, table_name
   ```

   - BigQuery: qualify per dataset — `<dataset>.INFORMATION_SCHEMA.TABLES`;
     list datasets with `INFORMATION_SCHEMA.SCHEMATA`.
   - DuckDB: `SHOW ALL TABLES` or standard `INFORMATION_SCHEMA` views.
   - SQLite: `SELECT name, sql FROM sqlite_master WHERE type = 'table'`
     (the `sql` column carries the full column DDL).

   On a small schema, enumerate everything up front — keyword-fishing for
   "the customer table" misses tables you didn't think to search for. On a
   large warehouse, filter by schema first and drill only where needed.
3. **Check columns before writing SQL against a table.**

   ```sql
   SELECT column_name, data_type FROM INFORMATION_SCHEMA.COLUMNS
   WHERE table_schema = '<schema>' AND table_name = '<table>'
   ```

   (SQLite: `PRAGMA table_info(<table>)`; DuckDB: `DESCRIBE <table>`.)
4. **Find things by name.** Push the search into the metadata query:

   ```sql
   SELECT table_schema, table_name, column_name FROM INFORMATION_SCHEMA.COLUMNS
   WHERE LOWER(column_name) LIKE '%customer%' OR LOWER(table_name) LIKE '%customer%'
   ```

5. **Sample only after narrowing candidates.** Use `execute_query` on
   likely tables to validate cardinality, inspect example values (`SELECT
   DISTINCT` before filtering on a value), test joins, or answer a concrete
   data question.

## What To Return

For "what data do I have?" questions, answer with a concise analyst inventory:

- relevant sources and schemas
- likely fact tables and dimension tables
- important measures and dimensions
- known keys or relationships
- suggested analyses or dashboard ideas
- gaps where metadata is missing and the exact follow-up query you would run

For "where is X?" questions, list the best matching tables/columns from a
metadata search first, then explain why they match.

## Query Discipline

Keep metadata queries scoped: filter by schema or table instead of dumping
every column in the warehouse into one result.

When sampling with `execute_query`:

- select only the columns needed for the question
- use small limits for row samples
- aggregate before charting or comparing categories
- preserve dbt charts template placeholders such as `{{ variable_name }}` when
  testing parameterized SQL that may move into dashboard YAML

## Red Flags

- Writing SQL before checking table and column names
- Treating missing optional metadata as proof that a table or source is absent
- Guessing file paths or table names from memory
- Filtering on a display label you never verified is the stored value
