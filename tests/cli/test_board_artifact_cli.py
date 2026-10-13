"""Tests for the dct artifact emit / artifact render CLI commands."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_CLIP_ID = re.compile(r"clip\d+")
_RENDER_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DATA_AS_OF = re.compile(r"Data as of [^<]+")

_BOARD_YAML = """
title: CLI Board
queries:
  channel_mix:
    columns: [month, signups]
    values:
      - ["2026-01-01", 90]
      - ["2026-02-01", 105]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
rows:
  - mix
"""


def _normalize(svg: str) -> str:
    out = _CLIP_ID.sub("clipN", svg)
    out = _RENDER_TIME.sub("RENDER_TIME", out)
    return _DATA_AS_OF.sub("DATA_AS_OF", out)


class TestArtifactCliSurface:
    def test_emit_artifact_is_registered(self) -> None:
        from typer.core import TyperGroup
        from typer.main import get_command

        click_app = get_command(app)
        assert isinstance(click_app, TyperGroup)
        artifact_group = click_app.commands["artifact"]
        assert isinstance(artifact_group, TyperGroup)
        assert "emit" in artifact_group.commands
        assert "render" in artifact_group.commands
        assert "schema" in artifact_group.commands

    def test_board_no_longer_registered(self) -> None:
        result = runner.invoke(app, ["board", "emit", "charts/sales.yml"])
        assert result.exit_code != 0
        assert "no such command" in result.output.lower()


class TestEmitArtifact:
    def test_emit_artifact_writes_default_paths(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)

        result = runner.invoke(
            app, ["artifact", "emit", str(board), "--project-dir", str(tmp_path)]
        )

        assert result.exit_code == 0, result.output
        artifact = tmp_path / "boards" / "board.board.json"
        recording = tmp_path / "boards" / "board.recording.json"
        assert artifact.exists()
        assert recording.exists()

    def test_emit_artifact_json_output(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        artifact = tmp_path / "out.board.json"
        recording = tmp_path / "out.recording.json"

        result = runner.invoke(
            app,
            [
                "artifact",
                "emit",
                str(board),
                "--artifact",
                str(artifact),
                "--recording",
                str(recording),
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["query_count"] == 1

    def test_emit_artifact_missing_board_exits_1(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "artifact",
                "emit",
                str(tmp_path / "nope.yml"),
                "--project-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 1
        assert "not found" in (result.output + (result.stderr or "")).lower()


class TestRenderArtifact:
    def test_render_artifact_prints_svg_to_stdout(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        artifact = tmp_path / "out.board.json"
        recording = tmp_path / "out.recording.json"
        runner.invoke(
            app,
            [
                "artifact",
                "emit",
                str(board),
                "--artifact",
                str(artifact),
                "--recording",
                str(recording),
                "--project-dir",
                str(tmp_path),
            ],
        )

        result = runner.invoke(
            app, ["artifact", "render", str(artifact), str(recording)]
        )

        assert result.exit_code == 0, result.output
        assert "<svg" in result.output

    def test_render_artifact_writes_output_file(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        artifact = tmp_path / "out.board.json"
        recording = tmp_path / "out.recording.json"
        runner.invoke(
            app,
            [
                "artifact",
                "emit",
                str(board),
                "--artifact",
                str(artifact),
                "--recording",
                str(recording),
                "--project-dir",
                str(tmp_path),
            ],
        )
        output = tmp_path / "board.svg"

        result = runner.invoke(
            app,
            [
                "artifact",
                "render",
                str(artifact),
                str(recording),
                "--output",
                str(output),
            ],
        )

        assert result.exit_code == 0, result.output
        assert output.exists()
        assert "<svg" in output.read_text(encoding="utf-8")

    def test_render_artifact_missing_artifact_exits_1(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "artifact",
                "render",
                str(tmp_path / "nope.board.json"),
                str(tmp_path / "nope.recording.json"),
            ],
        )

        assert result.exit_code == 1

    def test_emit_then_render_reproduces_live_svg(self, tmp_path: Path) -> None:
        """End-to-end: dct artifact emit then dct artifact render matches a live dct render."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        artifact = tmp_path / "out.board.json"
        recording = tmp_path / "out.recording.json"

        runner.invoke(
            app,
            [
                "artifact",
                "emit",
                str(board),
                "--artifact",
                str(artifact),
                "--recording",
                str(recording),
                "--project-dir",
                str(tmp_path),
            ],
        )
        replay_result = runner.invoke(
            app, ["artifact", "render", str(artifact), str(recording)]
        )
        assert replay_result.exit_code == 0, replay_result.output

        live_result = runner.invoke(
            app,
            ["render", str(board), "--output", "-", "--project-dir", str(tmp_path)],
        )
        assert live_result.exit_code == 0, live_result.output

        assert _normalize(replay_result.output) == _normalize(live_result.output)


class TestArtifactSchema:
    def test_schema_prints_the_artifact_json_schema(self) -> None:
        result = runner.invoke(app, ["artifact", "schema"])

        assert result.exit_code == 0, result.output
        schema = json.loads(result.output)
        assert (
            schema["$id"] == "https://dbtcharts.com/schemas/board-resolved.schema.json"
        )
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_emitted_artifact_validates_against_the_printed_schema(
        self, tmp_path: Path
    ) -> None:
        from jsonschema import Draft202012Validator

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)
        emit_result = runner.invoke(
            app, ["artifact", "emit", str(board), "--project-dir", str(tmp_path)]
        )
        assert emit_result.exit_code == 0, emit_result.output

        schema_result = runner.invoke(app, ["artifact", "schema"])
        assert schema_result.exit_code == 0, schema_result.output

        artifact = json.loads((tmp_path / "boards" / "board.board.json").read_text())
        Draft202012Validator(json.loads(schema_result.output)).validate(artifact)
