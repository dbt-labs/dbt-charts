"""Board patch model: all-optional overlay used by the merge engine.

``BoardPatch`` is the all-optional counterpart to ``AuthoredBoard``, generated
mechanically by ``build_patch_model``.  Every field that carries a ``Merge``
annotation on ``AuthoredBoard`` has that annotation forwarded here, so
``merge_patches`` can read the strategy directly from the patch model's field
metadata without consulting the canonical model.

``EMPTY_PATCH`` is the all-None, no-fields-set sentinel used as the accumulator
base in ``merge_extends`` / ``merge_metas`` before any fragment is layered on.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator, TypeAdapter

from dbt_charts.core.compile.models.board.authored import (
    AuthoredBoard,
    desugar_theme,
)
from dbt_charts.core.compile.models.factories import (
    BoardPatchBase,
    build_patch_model_ext,
)

BoardPatch = build_patch_model_ext(AuthoredBoard, base_cls=BoardPatchBase)

# All-None sentinel: no fields set, safe as the identity element for merge_patches.
EMPTY_PATCH: BoardPatch = BoardPatch.model_construct()  # type: ignore[valid-type]

# BoardPatch's authoring-input entry point: desugars theme: -> extends: ahead of
# validation, mirroring AuthoredBoardInput (authored.py's desugar_theme). No
# SchemaSugar marker here -- BoardPatch is never schema-walked by introspection,
# and a fragment validated through the bare BoardPatch class (e.g. a nested
# item's own recursive field type) never sees a top-level `theme:` key.
BoardPatchInput = Annotated[BoardPatch, BeforeValidator(desugar_theme)]
BOARD_PATCH_ADAPTER: TypeAdapter[BoardPatch] = TypeAdapter(BoardPatchInput)  # type: ignore[valid-type]  # type-state: type_ignore — BoardPatch is a runtime-built class (build_patch_model_ext), not a static type expression; same shape as EMPTY_PATCH's annotation above
