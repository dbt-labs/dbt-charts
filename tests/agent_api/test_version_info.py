"""Tests for `agent_api.version_info.collect` editable-install detection."""

from __future__ import annotations

import json
from unittest.mock import patch

from dbt_charts.agent_api import version_info


def test_collect_marks_editable_when_direct_url_says_so() -> None:
    payload = json.dumps({"url": "file:///x", "dir_info": {"editable": True}})
    with patch.object(version_info, "_read_direct_url_json", return_value=payload):
        info = version_info.collect()
    assert info.editable is True
    assert "(editable)" in info.render()


def test_collect_not_editable_when_direct_url_missing() -> None:
    with patch.object(version_info, "_read_direct_url_json", return_value=None):
        info = version_info.collect()
    assert info.editable is False
    assert "(editable)" not in info.render()


def test_collect_not_editable_when_dir_info_editable_false() -> None:
    payload = json.dumps({"url": "file:///x", "dir_info": {"editable": False}})
    with patch.object(version_info, "_read_direct_url_json", return_value=payload):
        info = version_info.collect()
    assert info.editable is False
    assert "(editable)" not in info.render()
