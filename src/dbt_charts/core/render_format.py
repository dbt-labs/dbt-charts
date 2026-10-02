"""The `RenderFormat` type alias, split out of `dbt_charts.core.board`.

`RenderFormat` is pure type metadata with no runtime logic — but `board.py`
(where it used to live) is the compile+execute+render orchestrator and eagerly
imports the entire `core.compile` package. CLI/MCP surfaces that need
`RenderFormat` purely for argument typing (e.g. Typer's `--format` option,
which needs the real `Literal` object at decoration time, not just an
annotation) were paying that whole cost just to get this one type. Living
here, they don't.
"""

from __future__ import annotations

from typing import Literal

RenderFormat = Literal[
    "svg",
    "html",
    "png",
    "pdf",
    "thumbnail",
    "terminal",
    "json",
    "text",
    "text-data",
    "yaml",
    "data",
]
