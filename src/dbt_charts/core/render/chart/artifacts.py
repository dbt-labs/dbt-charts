"""Render output types for the chart render pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ArtifactKind = Literal["svg", "json"]

# Shared by render functions so their signatures don't each carry their own
# copy of this annotation.
ChartRenderData = list[dict[str, Any]]


@dataclass(frozen=True)
class RenderArtifact:
    """Chart-domain render output before transport conversion."""

    kind: ArtifactKind
    payload: dict[str, Any] | str
