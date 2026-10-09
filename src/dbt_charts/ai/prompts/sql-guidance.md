## SQL Generation Rules

Before writing SQL, write a short query plan:

- **OUTPUT**: the columns to return, with clear aliases that describe the value
- **FROM/JOIN**: the tables and the join keys between them
- **GRAIN**: what one row represents after the joins; guard against fan-out / double-counting on the non-unique side of a join — if a join multiplies rows, aggregate before joining or add a GROUP BY. If the schema context already states a join's multiplicity (e.g. one-to-many) or a column's uniqueness, use that to decide grain — don't spend a query probing for what the schema already tells you
- **FILTERS**: each WHERE condition; verify the exact stored value before filtering (stored values may differ from display labels — e.g. `'LA'` not `'Los Angeles'`)
- **SORT/LIMIT**: include if the question asks to rank, sort, or return a top-N

Then translate the plan faithfully into SQL. Verify by executing the query and checking that shape and values match the plan. When you run a query, heed any diagnostics it returns — a `WARN-MISSING-JOIN-PREDICATE` or `WARN-REAGGREGATION` warning means the join or aggregate is probably double-counting even though the query ran; fix the grain before trusting the numbers.

Additional rules:

- Write SQL that matches the database dialect in the schema context.
- Use real table and column names from the provided schema context.
- Use clear column aliases so results are self-describing.
- Include appropriate aggregations, groupings, and ordering.
- Prefer explicit column names over `SELECT *`.
- Return executable SQL, not pseudocode or placeholders.
