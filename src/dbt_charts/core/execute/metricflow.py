"""MetricFlow semantic SQL, compiled to warehouse SQL in process.

A source's `metricflow:` names a directory of MetricFlow v1 fragments: datasets
with calculations and relationships, entities, and constellations. A query that
reads one of those datasets or constellations compiles through MetricFlow's own
compiler, `metricflow-sql`, from the query's dialect to the warehouse's, and the
source's driver runs the result; no Trino proxy is involved. Any other query is
left as written, unless the source declares the bundle `required`: then every
query compiles, and one that reads a table outside the bundle is refused.

`metricflow-sql` is optional and imported only when a source needs it;
`INSTALL_HINT` names the install.
"""

from __future__ import annotations

import hashlib
import importlib
import posixpath
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbt_charts.core.compile.models.source import MetricFlowConfig
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_METRICFLOW_BUNDLE_INVALID,
    ERR_METRICFLOW_COMPILE_FAILED,
    ERR_METRICFLOW_NOT_INSTALLED,
    ERR_METRICFLOW_OUTSIDE_BUNDLE,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.semantic_bundle import (
    SemanticBundle,
    SemanticConstellation,
    SemanticDataset,
    SemanticEntity,
    SemanticField,
    SemanticPage,
    SemanticRelationship,
)
from dbt_charts.core.project import Project

if TYPE_CHECKING:
    # Optional at runtime: imported inside the methods that need it.
    from metricflow_sql.registry.memory import InMemoryRegistry

INSTALL_HINT = (
    "pip install 'metricflow-sql[yaml] @ git+https://github.com/fivetran/metricflow"
    "#subdirectory=metricflow_sql/packages/metricflow-sql'"
)

# The sentinel `sql_literals.INLINE_PLACEHOLDERS` renders a variable as. MetricFlow
# validates every bare name as a column, so a sentinel crosses its compiler as a
# string literal and comes back afterwards, once per copy the compiler made of it.
_PARAM_SENTINEL = re.compile(r"\x00dct_param_(\d+)\x00")
_PARAM_LITERAL = re.compile(r"'__dct_param_(\d+)__'")


def bundle_files(project: Project, relpath: str) -> dict[str, bytes]:
    """The files of the bundle directory at `relpath`, by path within it."""
    return {
        posixpath.relpath(path, relpath): project.read_bytes(path)
        for path in project.iter_files(relpath, recursive=True)
    }


def bundle_fingerprint(files: dict[str, bytes]) -> str:
    """A digest of a bundle's files, the same for the same content. Needs no
    MetricFlow: a cache key folds it in without loading the bundle."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(files[path])
        digest.update(b"\0")
    return digest.hexdigest()


def _require_metricflow() -> None:
    try:
        importlib.import_module("metricflow_sql")
    except ImportError as exc:
        raise ExecutionError.from_code(
            ERR_METRICFLOW_NOT_INSTALLED, install=INSTALL_HINT
        ) from exc


# Loaded layers by the files and `required` flag they came from, least recently
# used first. Keyed by content, never by path or project: the same files load the
# same layer anywhere, and an edit is a new key, so a layer cannot go stale.
_LAYERS: OrderedDict[tuple[str, bool], SemanticLayer] = OrderedDict()
_LAYERS_LOCK = threading.Lock()
# Each edit of a bundle adds a layer, and a layer is its whole registry, roughly ten
# times the bundle's size in memory: a long-lived server keeps the few in use, not
# every version it has seen.
_LAYER_CACHE_SIZE = 4


@dataclass(frozen=True)
class SemanticLayer:
    """A source's MetricFlow bundle, loaded.

    `catalog` is the bundle's name, which unqualified table names in a query
    resolve in. A `required` layer compiles every query on its source; an
    optional one only the semantic ones. Every caller of an unchanged bundle
    shares one layer, registry included, so nothing may modify it.
    """

    registry: InMemoryRegistry
    catalog: str
    required: bool

    @classmethod
    def load(cls, project: Project, bundle: MetricFlowConfig) -> SemanticLayer:
        """The layer for the bundle `bundle` declares.

        Every caller shares one layer while the bundle's files are unchanged; an
        edited bundle loads afresh.

        Raises:
            ExecutionError: ERR-METRICFLOW-NOT-INSTALLED without the library;
                ERR-METRICFLOW-BUNDLE-INVALID when the directory has no files, a
                fragment is malformed, or its objects do not resolve.
        """
        relpath = bundle.path
        try:
            files = bundle_files(project, relpath)
        except ValueError as exc:
            # A path the project refuses: it escapes the root, or is read-only.
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID, path=relpath, reason=str(exc)
            ) from exc
        if not files:
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID,
                path=relpath,
                reason="the directory has no files",
            )
        key = (bundle_fingerprint(files), bundle.required)
        with _LAYERS_LOCK:
            if (shared := _LAYERS.get(key)) is not None:
                _LAYERS.move_to_end(key)
                return shared
        _require_metricflow()
        from metricflow_sql.registry.memory import InMemoryRegistry
        from metricflow_sql.registry.operation import Batch, Insert
        from metricflow_sql.schemas.bundle import (
            Bundle,
            bundle_dialect,
            bundle_name,
            bundle_normalization_strategy,
            load_bundle,
        )

        try:
            fragments = Bundle(files)
            registry = InMemoryRegistry(
                dialect=bundle_dialect(fragments),
                normalization_strategy=bundle_normalization_strategy(fragments),
            )
            registry.apply(Batch([Insert(obj) for obj in load_bundle(fragments)]))
            catalog = bundle_name(fragments)
        except (ValueError, SqlglotError) as exc:
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID, path=relpath, reason=str(exc)
            ) from exc
        except ImportError as exc:
            # A YAML fragment needs the library's `yaml` extra.
            raise ExecutionError.from_code(
                ERR_METRICFLOW_NOT_INSTALLED, install=INSTALL_HINT
            ) from exc
        layer = cls(registry, catalog, bundle.required)
        # Two threads that miss together both build; the later store wins.
        with _LAYERS_LOCK:
            _LAYERS[key] = layer
            while len(_LAYERS) > _LAYER_CACHE_SIZE:
                _LAYERS.popitem(last=False)
        return layer

    def _tables(self, sql: str, dialect: str) -> list[exp.Table]:
        """The tables `sql`, written in `dialect`, reads, its CTEs' names left
        out. SQL sqlglot cannot parse reads none: it is not semantic, and the
        warehouse, or a required layer's compiler, judges it as written."""
        tables: list[exp.Table] = []
        try:
            trees = sqlglot.parse(sql, read=sqlglot_dialect(dialect))
        except SqlglotError:
            return tables
        for tree in trees:
            if tree is None:
                continue
            ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
            tables.extend(
                table
                for table in tree.find_all(exp.Table)
                if table.db or table.catalog or table.name not in ctes
            )
        return tables

    def _declares(self, table: exp.Table) -> bool:
        """Whether `table` names a dataset or constellation: unqualified or in
        the bundle's default namespace. A catalog-qualified table is the
        warehouse's own."""
        from metricflow_sql.objects.base import DEFAULT_NAMESPACE, in_namespace
        from metricflow_sql.utils.normalize import canonical, read_identifier

        if table.catalog or not isinstance(table.this, exp.Identifier):
            return False
        registry = self.registry
        db = table.args.get("db")
        name = exp.Table(
            this=table.this.copy(),
            db=db.copy() if isinstance(db, exp.Identifier) else None,
        )
        name = in_namespace(
            canonical(name, registry.dialect, registry.normalization_strategy),
            read_identifier(
                DEFAULT_NAMESPACE, registry.dialect, registry.normalization_strategy
            ),
        )
        return bool(registry.get_dataset(name) or registry.get_constellation(name))

    def references(self, sql: str, dialect: str) -> bool:
        """Whether `sql`, written in `dialect`, reads a dataset or constellation."""
        return any(self._declares(table) for table in self._tables(sql, dialect))

    def governs(self, sql: str, dialect: str) -> bool:
        """Whether MetricFlow compiles `sql`, written in `dialect`.

        A required layer compiles every statement, so a table the bundle does
        not declare is refused here, by name, instead of as the compiler's own
        unknown-table failure. An optional layer compiles only a statement that
        `references` the bundle.

        Raises:
            ExecutionError: ERR-METRICFLOW-OUTSIDE-BUNDLE when the layer is
                required and the statement reads a table outside the bundle.
        """
        if not self.required:
            return self.references(sql, dialect)
        read = sqlglot_dialect(dialect)
        outside = sorted(
            {
                table.sql(read)
                for table in self._tables(sql, dialect)
                if not self._declares(table)
            }
        )
        if outside:
            raise ExecutionError.from_code(
                ERR_METRICFLOW_OUTSIDE_BUNDLE,
                bundle=self.catalog,
                tables=", ".join(f"`{table}`" for table in outside),
            )
        return True

    def describe(self) -> SemanticBundle:
        """Every dataset, entity, constellation and page the bundle declares.

        Names outside the default namespace keep it (`sales.orders`): a query
        must write them that way to resolve.
        """
        from metricflow_sql.objects.base import DEFAULT_NAMESPACE
        from metricflow_sql.objects.constellation import Metric, Role
        from metricflow_sql.objects.dataset import (
            Calculation,
            Column,
            Relationship,
            View,
        )
        from metricflow_sql.utils.normalize import read_identifier
        from metricflow_sql.utils.sql import entity_name

        registry = self.registry
        dialect = registry.dialect
        default_namespace = read_identifier(
            DEFAULT_NAMESPACE, dialect, registry.normalization_strategy
        ).name

        def qualified(table: exp.Table) -> str:
            if table.db in ("", default_namespace):
                return table.name
            return f"{table.db}.{table.name}"

        def datatype(node: exp.DataType | None) -> str | None:
            if node is None:
                return None
            entity = entity_name(node)
            if entity is not None:
                return f"entity('{qualified(entity)}')"
            return node.sql(dialect)

        datasets = []
        for table, dataset in registry.datasets.items():
            fields = registry.dataset_fields[table].values()
            datasets.append(
                SemanticDataset(
                    name=qualified(table),
                    description=dataset.description,
                    sql=dataset.sql if isinstance(dataset, View) else None,
                    columns=[
                        SemanticField(
                            name=f.name.name,
                            description=f.description,
                            datatype=datatype(f.datatype),
                        )
                        for f in fields
                        if isinstance(f, Column)
                    ],
                    calculations=[
                        SemanticField(
                            name=f.name.name,
                            description=f.description,
                            expr=f.expr.sql(dialect),
                        )
                        for f in fields
                        if isinstance(f, Calculation)
                    ],
                    relationships=[
                        SemanticRelationship(
                            name=f.name.name, target=qualified(f.target)
                        )
                        for f in fields
                        if isinstance(f, Relationship)
                    ],
                )
            )
        entities = [
            SemanticEntity(
                name=qualified(table),
                description=entity.description,
                key=[
                    SemanticField(name=name.name, datatype=datatype(column.datatype))
                    for name, column in entity.key.items()
                ],
                attributes=[
                    SemanticField(
                        name=a.name.name,
                        description=a.description,
                        expr=a.expr.sql(dialect),
                    )
                    for a in registry.entity_fields[table].values()
                ],
            )
            for table, entity in registry.entities.items()
        ]
        constellations = []
        for table, constellation in registry.constellations.items():
            # The composed view, so roles and metrics a constellation imports
            # are listed with its own.
            composed = registry.get_constellation(table)
            assert composed is not None
            fields = [composed.get_column(name) for name in composed.column_names()]
            constellations.append(
                SemanticConstellation(
                    name=qualified(table),
                    description=constellation.description,
                    roles=[
                        SemanticField(
                            name=f.name.name,
                            description=f.description,
                            datatype=datatype(f.datatype),
                        )
                        for f in fields
                        if isinstance(f, Role)
                    ],
                    metrics=[
                        SemanticField(
                            name=f.name.name,
                            description=f.description,
                            expr=f.expr.sql(dialect),
                        )
                        for f in fields
                        if isinstance(f, Metric)
                    ],
                )
            )
        # A page and an object share one namespace of names to read by.
        declared = {o.name for o in (*datasets, *entities, *constellations)}
        if clash := sorted(declared & set(registry.pages)):
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID,
                path=self.catalog,
                reason=f"pages share a name with an object: `{'`, `'.join(clash)}`",
            )
        return SemanticBundle(
            name=self.catalog,
            datasets=datasets,
            entities=entities,
            constellations=constellations,
            pages=[
                SemanticPage(
                    name=page.name,
                    description=page.description,
                    content=page.content,
                )
                for page in registry.pages.values()
            ],
        )

    def compile(self, sql: str, read: str, write: str) -> str:
        """`sql`, written in `read` with inline variable sentinels, as `write` SQL.

        Raises:
            ExecutionError: ERR-METRICFLOW-COMPILE-FAILED when MetricFlow rejects
                the query: an unknown attribute, a table outside the bundle, or
                SQL it cannot plan.
        """
        from metricflow_sql.compiler.catalog.cluster import Cluster
        from metricflow_sql.compiler.catalog.semantic import SemanticCatalog
        from metricflow_sql.compiler.compiler import Compiler
        from metricflow_sql.objects.base import DEFAULT_NAMESPACE

        compiler = Compiler(
            cluster=Cluster({self.catalog: SemanticCatalog(self.registry)}),
            catalog=self.catalog,
            db=DEFAULT_NAMESPACE,
            input_dialect=sqlglot_dialect(read),
            output_dialect=sqlglot_dialect(write),
            normalization_strategy=self.registry.normalization_strategy,
        )
        literal = _PARAM_SENTINEL.sub(lambda m: f"'__dct_param_{m.group(1)}__'", sql)
        try:
            out = compiler.compile(literal, read_only=True).out_sql
        except (SqlglotError, ValueError) as exc:
            reason = str(exc)
            if hints := self.describe().did_you_mean(reason):
                reason += " Did you mean " + " or ".join(f"`{h}`" for h in hints) + "?"
            raise ExecutionError.from_code(
                ERR_METRICFLOW_COMPILE_FAILED, reason=reason
            ) from exc
        return _PARAM_LITERAL.sub(lambda m: f"\x00dct_param_{m.group(1)}\x00", out)
