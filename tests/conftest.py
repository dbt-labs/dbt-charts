from __future__ import annotations

import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from dbt_charts._render_tz import pin_vl_convert_tz_utc
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    CalloutChart,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.diagnostics.registry import DiagnosticCode
from dbt_charts.core.project import (
    ALL_FILES_GLOB,
    SKIP_SCAN_DIRS,
    GrepHit,
    Project,
    ProjectDirectory,
    ProjectFileQueries,
    ProjectPath,
    assert_relpath,
    glob_literal_prefix,
    glob_to_regex,
    iter_dir_from_relpaths,
)

from ._paths import DBT_CHARTS_DIR

_CHART_CLS = {
    "bar": BarChart,
    "histogram": BarChart,
    "line": LineChart,
    "area": AreaChart,
    "scatter": ScatterChart,
    "heatmap": HeatmapChart,
    "pie": PieChart,
    "donut": PieChart,
    "arc": PieChart,
    "kpi": KpiChart,
    "table": TableChart,
    "geoshape": GeoshapeChart,
    "map": GeoshapeChart,
    "point_map": PointMapChart,
    "bubble_map": PointMapChart,
    "spark_bar": SparkBarChart,
    "callout": CalloutChart,
}

# Maps chart_type → the style family key the old nested ChartStylePatch used.
# `layered` is intentionally absent: its style keeps nested per-family sub-patches.
_CHART_FAMILY_KEY = {
    "bar": "bar",
    "histogram": "bar",
    "line": "line",
    "area": "area",
    "scatter": "scatter",
    "heatmap": "heatmap",
    "pie": "pie",
    "donut": "pie",
    "arc": "pie",
    "kpi": "kpi",
    "table": "table",
    "geoshape": "geoshape",
    "map": "geoshape",
    "point_map": "point_map",
    "bubble_map": "point_map",
    "spark_bar": "spark_bar",
    "callout": "callout",
}

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.theme import Style

_DBT_CHARTS_DIR = DBT_CHARTS_DIR


@pytest.fixture(scope="session", autouse=True)
def pin_render_tz_utc() -> None:
    """Pin the test session to vl-convert's TZ=UTC before any test renders.

    The test harness (``tests/visual/discovery.py`` in particular, which
    powers golden regeneration) calls into ``core.render`` directly, not
    through a `dct` composition root, so it needs its own pin. Without this,
    a golden approved on one contributor's machine can silently differ from
    one regenerated on another's -- see ``dbt_charts._render_tz`` for why.

    Setting the env var alone only pins vl-convert's *next* fresh process --
    within this one, vl-convert's own TZ cache still seeds off whatever the
    first call observes. Force that first observation to happen right here,
    so no fixture-ordering accident (some other autouse fixture, or a test
    that imports vl-convert before this one runs) can seed it from the
    ambient host zone instead.
    """
    import vl_convert as vlc

    pin_vl_convert_tz_utc()
    vlc.get_local_tz()


@pytest.fixture
def non_utc_tz(monkeypatch):
    """Run the test with a local clock that is NOT UTC.

    Timestamp-frame bugs are invisible on a UTC machine — naive-local and
    naive-UTC are the same value there, so a test that only ever runs in UTC
    (CI does) would pass against a local-clock write. Forcing a real offset is
    what gives these assertions teeth.
    """
    if not hasattr(time, "tzset"):  # pragma: no cover — Windows has no tzset
        pytest.skip("tzset is Unix-only; the offset cannot be forced here")
    # West of Greenwich and DST-free: a local-clock timestamp then reads as
    # *older* than it is, which is the direction a ttl or retry-window
    # assertion can actually see (east makes an entry look newer, and
    # "newer than it is" still returns a hit).
    monkeypatch.setenv("TZ", "America/Phoenix")  # UTC-7, no DST
    time.tzset()
    if datetime.now().astimezone().utcoffset() == timedelta(0):
        # No tzdata (the default in python:*-slim), so libc silently fell back
        # to UTC and tzset() still succeeded. Every assertion that depends on a
        # real offset would then pass without being able to fail — skip rather
        # than report green on a test that checked nothing.
        monkeypatch.undo()
        time.tzset()
        pytest.skip("no tzdata installed; a non-UTC offset cannot be forced here")
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def register_temporarily() -> Iterator[Callable[[DiagnosticCode], None]]:
    """Register a throwaway DiagnosticCode into the module-global REGISTRY for
    one test, then remove it on teardown.

    Exists so a test can prove a code-agnostic pass (e.g. diagnostic source-map
    stamping) works for *any* code without wiring — a local ``DiagnosticRegistry()``
    doesn't work for this: ``Diagnostic._known_code`` validates against the
    module-global REGISTRY, so a code that lives only in a local registry
    can't be used to construct a Diagnostic at all.
    """
    from dbt_charts.core.diagnostics.registry import REGISTRY

    registered: list[str] = []

    def _register(dc: DiagnosticCode) -> None:
        REGISTRY.register(dc)
        registered.append(dc.code)

    yield _register

    for code in registered:
        REGISTRY.unregister(code)


@pytest.fixture(autouse=True)
def unset_dbt_target_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """DBT_TARGET_PATH moves where manifests are read from; a developer's shell
    value must not reach a test that expects dbt's default location."""
    monkeypatch.delenv("DBT_TARGET_PATH", raising=False)


@pytest.fixture(autouse=True)
def isolate_resolve_style_cache() -> Iterator[None]:
    """Clear the resolve_style cache around every test.

    resolve_style returns shared frozen ResolvedStyle instances. Tests that
    mutate nested pydantic sub-fields (e.g. ``es.table.symbol_mode = "all"``)
    would otherwise poison the cache for any later test on the same xdist
    worker. The cache is cheap to rebuild — a single _finalize_style
    pass per unique base — so isolating per-test is the simple, correct fix.
    """
    from dbt_charts.core.compile.resolve.style.board import clear_resolve_style_cache

    clear_resolve_style_cache()
    yield
    clear_resolve_style_cache()


@pytest.fixture(scope="session")
def built_dbt_charts_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the dbt-charts wheel once per pytest session.

    `uv build --wheel` skips the sdist; the install-smoke and wheel-content
    assertions both consume the wheel itself.
    """
    out_dir = tmp_path_factory.mktemp("dbt-charts-wheel")
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(out_dir),
        ],
        cwd=_DBT_CHARTS_DIR,
        check=True,
    )
    wheels = list(out_dir.glob("dbt_charts-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]


@pytest.fixture(scope="session")
def dbt_charts_wheel_entries(built_dbt_charts_wheel: Path) -> set[str]:
    with zipfile.ZipFile(built_dbt_charts_wheel) as wheel:
        return set(wheel.namelist())


@pytest.fixture(scope="session")
def compiled_themes() -> dict[str, Style]:
    """All production (non-diagnostics) built-in themes, compiled once per worker.

    Tests that iterate every theme should consume this dict directly instead of
    calling get_theme_style() in a loop. The value is stored in the fixture
    (not in the module-level cache), so reset_config() calls made by other tests
    cannot invalidate it. Compile errors surface immediately — a failed compile
    raises rather than silently producing a wrong value.
    """
    from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
    from dbt_charts.core.compile.models.style.theme import Style

    out: dict[str, Style] = {}
    for name in list_built_in_themes():
        if name.startswith("diagnostics-") or name.startswith("_"):
            continue
        theme = get_theme_style(name)
        assert isinstance(theme, Style), (
            f"Theme {name!r}: expected Style, got {type(theme)}"
        )
        out[name] = theme
    return out


@pytest.fixture
def model_copy_at():
    """Return a function that builds a modified copy of a frozen pydantic model.

    Compiled style models carry ``frozen=True``, so direct attribute assignment
    raises ``ValidationError``. Tests that need a custom style value call
    ``model_copy_at(model, "dotted.path", value)`` to rebuild the model tree immutably
    via ``model_copy(update=...)``.

    Raises ``ValueError`` when an intermediate field on the path is ``None``.
    """

    def _model_copy_at(model: BaseModel, dotted_path: str, value: Any) -> BaseModel:
        head, _, rest = dotted_path.partition(".")
        if not rest:
            return model.model_copy(update={head: value})
        child = getattr(model, head)
        if child is None:
            raise ValueError(
                f"Cannot traverse '{head}' in {type(model).__name__}: field is None. "
                f"Remaining path: '{rest}'"
            )
        return model.model_copy(update={head: _model_copy_at(child, rest, value)})

    return _model_copy_at


@pytest.fixture
def make_chart():
    """Factory fixture for creating Chart objects with sensible defaults."""

    def _make(chart_type="bar", x="x_field", y="y_field", **kwargs):
        chart_id = kwargs.get("id", f"test_{chart_type}")
        defaults: dict[str, Any] = {
            "id": chart_id,
            # The normalizer gives every `charts:` entry its authoring path, so
            # a chart built without one is a shape production never produces —
            # and a test that relies on that hole passes for the wrong reason.
            "source_path": f"charts.{chart_id}",
            "query": SqlQuery(sql="SELECT 1", source="test_profile"),
            "query_name": "q",
            "type": chart_type,
        }
        if chart_type in ("arc", "pie", "donut"):
            defaults.update(theta=y, color=x)
        elif chart_type not in ("kpi", "callout"):
            defaults.update(x=x, y=y)
        defaults.update(kwargs)

        # For V2 family models, style dicts must match the per-family patch shape.
        # V1 used ChartStylePatch(bar=BarChartStylePatch(...)); V2 uses
        # BarChartStylePatch(...) directly. Unwrap the legacy nested family key
        # that matches this chart's family (layered keeps its nested families).
        if "style" in defaults and isinstance(defaults["style"], dict):
            family_key = _CHART_FAMILY_KEY.get(chart_type)
            style = defaults["style"]
            if (
                family_key is not None
                and family_key in style
                and isinstance(style[family_key], dict)
            ):
                inner = style.pop(family_key)
                defaults["style"] = {**style, **inner}

        cls = _CHART_CLS[chart_type]
        # Filter out keys the family model doesn't declare (extra="forbid")
        valid = set(cls.model_fields)
        return cls(**{k: v for k, v in defaults.items() if k in valid})

    return _make


class InMemoryFileQueries(ProjectFileQueries):
    """glob + grep reference implementation for dict-backed test doubles.

    Every non-filesystem ``Project`` double in ``dbt-charts/tests`` wires this in
    for ``.files`` — it is the cross-host contract ``FilesystemFileQueries``'s
    bytes-scan grep must match, exercised in ``TestProjectFileQueriesContract``.
    ``glob`` enumerates via ``iter_files`` bounded by the pattern's literal
    prefix; ``grep`` reads each matched file's text and skips one that cannot
    be read (binary, vanished, over a host size cap) rather than raising.

    Defined once here (not an importable module) for the same reason as
    ``InMemoryProject`` below: pytest delivers it to every test package under
    ``dbt-charts/tests`` without an import, sidestepping the ``dbt-charts``
    namespace collision a top-level conftest hits when importing a submodule.
    """

    def __init__(self, project: Project) -> None:
        self._project = project

    def glob(self, pattern: str) -> Iterator[ProjectPath]:
        assert_relpath(pattern)
        under = glob_literal_prefix(pattern)
        if under != "." and SKIP_SCAN_DIRS.intersection(under.split("/")):
            return
        regex = glob_to_regex(pattern)
        for relpath in self._project.iter_files(under, recursive=True):
            if regex.match(relpath):
                yield ProjectPath(self._project, relpath)

    def grep(self, term: str, glob: str = ALL_FILES_GLOB) -> Iterator[GrepHit]:
        for pf in self.glob(glob):
            try:
                text = pf.read_text()
            except (ExecutionError, OSError, UnicodeDecodeError):
                continue  # skip binary / unreadable / vanished / oversized files
            for i, line in enumerate(text.splitlines(), start=1):
                if term in line:
                    yield GrepHit(pf.relpath, i, line)


class InMemoryProject(Project):
    """A Project whose reads come from an in-memory dict, never touching disk.

    Overrides Project's five file-access methods — the same seam Cloud's
    ``CloudManagedProject`` uses for its git-blob store. Exists as a subclass
    (not a bare factory) so it stays usable as a type annotation in tests.

    Shared from this top-level conftest rather than an importable module: pytest
    delivers it to every test package under ``dbt-charts/tests`` (including a
    single-file inner-loop run) without an import, sidestepping the ``dbt-charts``
    namespace collision that made the old same-package copies necessary.
    """

    def __init__(
        self, root: Path, files: dict[str, str], *, name: str | None = None
    ) -> None:
        # Not exposed as `.root` — the base holds no filesystem Path. Kept only
        # to derive a display `name` when the caller doesn't supply one; nothing
        # here ever reads or writes disk under it.
        self._root = root
        self._files = files
        self._name = name

    @cached_property
    def sources(self) -> ProjectSourcesConfig:
        return ProjectSourcesConfig(sources={})

    @cached_property
    def files(self) -> ProjectFileQueries:
        return InMemoryFileQueries(self)

    @cached_property
    def name(self) -> str:
        return self._name if self._name is not None else self._root.name

    def exists(self, relpath: str) -> bool:
        return relpath in self._files

    def read_text(self, relpath: str) -> str:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        return self._files[relpath]

    def read_bytes(self, relpath: str) -> bytes:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        return self._files[relpath].encode()

    def iter_files(self, under: str, *, recursive: bool) -> Iterator[str]:
        # under="." means the whole project (matches every relpath); otherwise
        # restrict to the named subtree.
        prefix = "" if under == "." else under.rstrip("/") + "/"
        for relpath in sorted(self._files):
            if not relpath.startswith(prefix):
                continue
            remainder = relpath[len(prefix) :]
            if not recursive and "/" in remainder:
                continue
            yield relpath

    def iter_dir(self, under: str) -> Iterator[ProjectPath | ProjectDirectory]:
        return iter_dir_from_relpaths(self, under, self._files)

    def write_text(self, relpath: str, content: str) -> None:
        self._files[relpath] = content

    def delete_text(self, relpath: str) -> None:
        if relpath not in self._files:
            raise FileNotFoundError(relpath)
        del self._files[relpath]


@pytest.fixture
def in_memory_project() -> Callable[..., Project]:
    """The shared dict-backed ``Project`` double, delivered without an import.

    Call it like the class: ``in_memory_project(root, {"charts/x.yml": "..."})``,
    or with an explicit display name: ``in_memory_project(root, {}, name="...")``.
    """
    return InMemoryProject


@pytest.fixture
def local_project() -> Callable[..., FilesystemProject]:
    """Factory for FilesystemProject, the one blessed way to build a test project.

    Pending dbt-charts.local extraction: this fixture is the single call site
    that migration will need to update.
    """
    return FilesystemProject


class NoGlobTraversable:
    """Minimal importlib.resources.Traversable double.

    Deliberately omits ``glob``/``stem`` — real ``Traversable`` objects don't
    have them (only ``iterdir``/``joinpath``/``is_dir``/``is_file``/``open``/
    ``read_bytes``/``read_text``/``name``/``__truediv__``). Catches a
    regression where enumeration code silently falls back to Path-only sugar.
    Implements every other protocol member (including ``open``/``read_bytes``)
    so ``isinstance(x, Traversable)`` — enforced by ``Skill.directory``'s
    ``arbitrary_types_allowed`` validation — still passes.

    Shared from this top-level conftest rather than an importable module: a
    test file under ``dbt-charts/tests`` that is analyzed by pyright (unlike
    ``tests/core``, which is excluded) cannot import from the
    ``dbt-charts.tests`` namespace package — it isn't statically resolvable.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def iterdir(self) -> Iterator[NoGlobTraversable]:
        return (type(self)(p) for p in self._path.iterdir())

    def is_dir(self) -> bool:
        return self._path.is_dir()

    def is_file(self) -> bool:
        return self._path.is_file()

    def joinpath(self, *parts: str) -> NoGlobTraversable:
        return type(self)(self._path.joinpath(*parts))

    def __truediv__(self, child: str) -> NoGlobTraversable:
        return self.joinpath(child)

    def open(self, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        return self._path.open(mode, *args, **kwargs)

    def read_bytes(self) -> bytes:
        return self._path.read_bytes()

    def read_text(self, encoding: str | None = None) -> str:
        return self._path.read_text(encoding=encoding)

    @property
    def name(self) -> str:
        return self._path.name


@pytest.fixture
def no_glob_traversable() -> Callable[[Path], Any]:
    """Factory for ``NoGlobTraversable``, delivered without an import.

    Typed as ``Callable[[Path], Any]`` (not ``NoGlobTraversable`` itself) for
    the same reason ``in_memory_project`` returns ``Project`` rather than
    ``InMemoryProject``: the concrete double lives only in this conftest.
    """
    return NoGlobTraversable
