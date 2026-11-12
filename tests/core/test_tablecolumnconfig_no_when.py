"""Regression: TableColumnConfig does not accept a ``when`` field.

Post pre-launch clean-break (task: delete-tablecolumnconfig-when), the only
authored path for table conditional formatting is the chart-level
``conditional_formatting:`` block indexed by column. Authoring
``style.columns[*].when`` must raise ``pydantic.ValidationError`` via
``extra="forbid"`` so users get a clear migrate signal.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.compile.models.conditional_predicate import ConditionalPredicate


def test_tablecolumnconfig_rejects_when_kwarg() -> None:
    """Passing ``when=[...]`` to TableColumnConfig raises ValidationError."""
    with pytest.raises(ValidationError):
        TableColumnConfig(
            when=[ConditionalPredicate(gt=1_000_000)],
        )


def test_tablecolumnconfig_rejects_when_from_yaml_dict() -> None:
    """Parsing from a YAML-like dict with ``when:`` raises ValidationError."""
    with pytest.raises(ValidationError):
        TableColumnConfig.model_validate(
            {
                "when": [{"gt": 1_000_000, "background": "#166534"}],
            }
        )


def test_tablecolumnconfig_scale_still_accepted() -> None:
    """Scale encoding is orthogonal and remains supported on TableColumnConfig."""
    cfg = TableColumnConfig.model_validate(
        {
            "scale": {
                "background": {"palette": ["#ffffff", "#166534"]},
            },
        }
    )
    assert cfg.scale is not None
    assert cfg.scale.background is not None
