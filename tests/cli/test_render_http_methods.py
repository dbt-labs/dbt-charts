"""A human running `dct render` keeps POST http sources (GraphQL, search APIs);
only agent sessions are held to GET (see test_dispatch's stdio-session test)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_BOARD = """\
title: Search
queries:
  hits:
    type: http
    url: https://api.example.com/search
    method: POST
    body:
      q: dbt
    json_path: $.hits
charts:
  table:
    query: hits
    type: table
rows:
  - table
"""


def test_render_sends_the_post_an_http_source_declares(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text('name: http_post\nversion: "1.0"\n')
    (tmp_path / "search.yml").write_text(_BOARD)
    response = MagicMock()
    response.json.return_value = {"hits": [{"title": "a"}]}

    with patch(
        "dbt_charts.core.execute.adapters.http_adapter.httpx.Client"
    ) as mock_client:
        post = mock_client.return_value.__enter__.return_value.post
        post.return_value = response
        result = runner.invoke(
            app,
            [
                "render",
                "search.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    post.assert_called_once()
