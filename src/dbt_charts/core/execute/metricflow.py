"""MetricFlow semantic SQL, compiled to warehouse SQL in process.

A source's `metricflow:` names a directory of MetricFlow v1 fragments: datasets
with calculations and relationships, entities, and constellations. A query that
reads one of those datasets or constellations compiles through MetricFlow's own
compiler, `metricflow-sql`, from the query's dialect to the warehouse's, and the
source's driver runs the result; no Trino proxy is involved. Any other query is
left as written.

`metricflow-sql` is optional and imported only when a source needs it;
`INSTALL_HINT` names the install.
"""

from __future__ import annotations

import hashlib
import importlib
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_METRICFLOW_BUNDLE_INVALID,
    ERR_METRICFLOW_COMPILE_FAILED,
    ERR_METRICFLOW_NOT_INSTALLED,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
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


@dataclass(frozen=True)
class SemanticLayer:
    """A source's MetricFlow bundle, loaded.

    `catalog` is the bundle's name, which unqualified table names in a query
    resolve in; `fingerprint` identifies the files the layer was loaded from, so a
    caller can keep reusing it while the bundle is unchanged.
    """

    registry: InMemoryRegistry
    catalog: str
    fingerprint: str

    @classmethod
    def load(
        cls, project: Project, relpath: str, cached: SemanticLayer | None = None
    ) -> SemanticLayer:
        """The layer for the bundle directory at `relpath`.

        `cached` is the layer last loaded from that directory, if any; it is
        returned as is when the files have not changed.

        Raises:
            ExecutionError: ERR-METRICFLOW-NOT-INSTALLED without the library;
                ERR-METRICFLOW-BUNDLE-INVALID when the directory has no files, a
                fragment is malformed, or its objects do not resolve.
        """
        try:
            files = bundle_files(project, relpath)
        except ValueError as exc:
            # A path the project refuses: it escapes the root, or is read-only.
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID, path=relpath, reason=str(exc)
            ) from exc
        fingerprint = bundle_fingerprint(files)
        if cached is not None and cached.fingerprint == fingerprint:
            return cached
        if not files:
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID,
                path=relpath,
                reason="the directory has no files",
            )
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
            bundle = Bundle(files)
            registry = InMemoryRegistry(
                dialect=bundle_dialect(bundle),
                normalization_strategy=bundle_normalization_strategy(bundle),
            )
            registry.apply(Batch([Insert(obj) for obj in load_bundle(bundle)]))
            catalog = bundle_name(bundle)
        except (ValueError, SqlglotError) as exc:
            raise ExecutionError.from_code(
                ERR_METRICFLOW_BUNDLE_INVALID, path=relpath, reason=str(exc)
            ) from exc
        except ImportError as exc:
            # A YAML fragment needs the library's `yaml` extra.
            raise ExecutionError.from_code(
                ERR_METRICFLOW_NOT_INSTALLED, install=INSTALL_HINT
            ) from exc
        return cls(registry, catalog, fingerprint)

    def references(self, sql: str, dialect: str) -> bool:
        """Whether `sql`, written in `dialect`, reads a dataset or constellation.

        A name matches unqualified or in the bundle's default namespace; a
        catalog-qualified table is the warehouse's own. SQL sqlglot cannot parse
        is not semantic: the warehouse judges it as written.
        """
        from metricflow_sql.objects.base import DEFAULT_NAMESPACE, in_namespace
        from metricflow_sql.utils.normalize import canonical, read_identifier

        try:
            trees = sqlglot.parse(sql, read=sqlglot_dialect(dialect))
        except SqlglotError:
            return False
        registry = self.registry
        namespace = read_identifier(
            DEFAULT_NAMESPACE, registry.dialect, registry.normalization_strategy
        )
        for tree in trees:
            if tree is None:
                continue
            for table in tree.find_all(exp.Table):
                if table.catalog or not isinstance(table.this, exp.Identifier):
                    continue
                db = table.args.get("db")
                name = exp.Table(
                    this=table.this.copy(),
                    db=db.copy() if isinstance(db, exp.Identifier) else None,
                )
                name = in_namespace(
                    canonical(name, registry.dialect, registry.normalization_strategy),
                    namespace,
                )
                if registry.get_dataset(name) or registry.get_constellation(name):
                    return True
        return False

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
            raise ExecutionError.from_code(
                ERR_METRICFLOW_COMPILE_FAILED, reason=str(exc)
            ) from exc
        return _PARAM_LITERAL.sub(lambda m: f"\x00dct_param_{m.group(1)}\x00", out)
