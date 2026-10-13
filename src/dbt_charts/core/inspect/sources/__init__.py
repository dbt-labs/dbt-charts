"""Plugin-boundary schema sources.

Schema sources are horizontal contributors of facts about a warehouse
schema. Implementations are independently testable; the resolver composes
contributions across sources into the wire response.

Two peers ship today:

  * `SuperSchemaSource` — warm cache backed by ``target/super_schema.json``.
    Surfaces the rich profile (stats, distributions, semantic types, role
    tags, enum lists) that a profiling run computed.
  * `DbtSchemaSource` — cold-start, composes a dbt-core adapter + lazy
    ``target/manifest.json`` reader. Surfaces only what's directly
    available — no inference, no naming heuristics. Honest empty.

Each source exposes ``name``, ``generated_at``, ``list_schemas``,
``list_tables``, and ``profile_table``. Methods return
bare-scalar ``dict | None`` (no per-field provenance wrapping; provenance
lives only in the response-level ``_meta`` footer).
``None`` means "this source has nothing to contribute" — silent
no-contribution, not an error. The resolver falls through to the next
source. Field-name conventions communicate origin: ``actual_type`` came
from the adapter; ``declared_type`` / ``description`` / ``tests`` /
``relationships`` came from the manifest. The resolver stamps a single
response-level ``_meta`` footer with ``retrieved_at``, ``sources_consulted``,
and (when super_schema contributed) ``cache_built_at``.

Each source is *per-source* by construction: ``SuperSchemaSource`` is one
instance per project (its cache file is per-project); ``DbtSchemaSource``
is one instance per warehouse adapter. The resolver routes calls to the
right instance, so the methods don't take a ``source`` argument.
"""
