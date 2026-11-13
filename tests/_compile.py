"""Plans for the boards tests compile: the board's text, through the real engine.

``tests/conftest.py`` records the text of every board ``compile`` returns here, so a
test that holds a compiled ``Board`` gets the plan the engine makes of the text it was
compiled from. A board built by hand has no text and no plan: asking for one raises.
"""

import weakref
from datetime import datetime
from typing import Any

import yaml

from dbt_charts.core.board import plan_for_board
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.normalize.queries import is_file_source_ref
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.plan_reader import env_sources
from dbt_charts.core.execute.query_plan import QueryPlan
from dbt_charts.core.project import InMemoryBoard, ProjectDirectory

_TEXTS: dict[int, tuple[str, ProjectDirectory | None, ProjectSourcesConfig | None]] = {}


def remember(
    board: Board,
    text: str,
    base_dir: ProjectDirectory | None = None,
    project_sources: ProjectSourcesConfig | None = None,
) -> None:
    """Record the *text* that *board* was compiled from, the directory its file refs
    resolved against (``None``: a board with no location) and the sources it was
    compiled against (``None``: none given)."""
    _TEXTS[id(board)] = (text, base_dir, project_sources)
    weakref.finalize(board, _TEXTS.pop, id(board), None)


def _named_sources(node: object) -> set[str]:
    """Every source name a board's text refers to, the way a test writes one."""
    if isinstance(node, dict):
        # An inline file path is no registry name: the engine reads it as a file source.
        found = {
            v
            for k, v in node.items()
            if k == "source" and isinstance(v, str) and not is_file_source_ref(v)
        }
        return found.union(*(_named_sources(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_named_sources(v) for v in node))
    return set()


def _sources_for(
    text: str, env: ExecEnv, declared_sources: ProjectSourcesConfig | None
) -> ProjectSourcesConfig:
    """The env's registry, then the *declared* one the board was compiled against; with none
    declared, an in-memory DuckDB for each name the text uses that the env lacks (the engine
    refuses an undeclared source, and a test's env answers for any name)."""
    base = env_sources(env)
    if declared_sources is not None:
        return ProjectSourcesConfig(
            sources={**declared_sources.sources, **base.sources},
            implicit_source=base.implicit_source,
        )
    try:
        named = _named_sources(yaml.safe_load(text))
    except yaml.YAMLError:
        named = set()
    declared: dict[str, dict[str, Any]] = {
        name: {"type": "duckdb", "path": ":memory:"}
        for name in named - base.sources.keys()
    }
    return ProjectSourcesConfig(
        sources={**declared, **base.sources}, implicit_source=base.implicit_source
    )


def plan_of(
    board: Board,
    env: ExecEnv,
    now: datetime,
    variables: VariableValues | None = None,
    *,
    strict: bool = False,
    active_tab_only: bool = False,
    all_queries: bool = False,
) -> QueryPlan:
    """The engine's plan of the text *board* was compiled from, under *variables*."""
    recorded = _TEXTS.get(id(board))
    if recorded is None:
        raise LookupError(
            "this board was not compiled from text in this test, so it has no plan; "
            "compile its YAML instead of building it by hand"
        )
    text, base_dir, declared_sources = recorded
    return plan_for_board(
        InMemoryBoard(text, path=None if base_dir is None else base_dir / "board.yaml"),
        env.project,
        variables,
        None,
        now,
        strict,
        active_tab_only,
        all_queries=all_queries,
        sources=_sources_for(text, env, declared_sources),
    )
