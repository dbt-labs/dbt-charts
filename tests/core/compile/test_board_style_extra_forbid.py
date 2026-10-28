"""Regression: StylePatch rejects unknown keys (extra="forbid").

Every model class must declare or inherit ConfigDict(extra="forbid").
StylePatch inherits extra="forbid" from _PatchBase via build_patch_model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.authored import StylePatch


def test_style_patch_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="bogus_key"):
        StylePatch.model_validate({"bogus_key": "x"})
