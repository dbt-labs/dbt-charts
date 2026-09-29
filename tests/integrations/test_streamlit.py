"""Tests for dbt_charts.integrations.streamlit's st_board().

Covers:
- A fake Snowpark session receives the board's rendered SQL when passed
- No session registers no Snowpark adapter
- The board is mounted with its live control runtime and its own size
- A value the viewer picks in the board is rendered on the next run
- Values the app passes win over the viewer's
- A failed board or missing board file raises and mounts nothing
- import dbt_charts never imports streamlit or snowflake
- A missing streamlit install raises an actionable ImportError
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.render.errors import RenderError

DBT_CHARTS_YML = """\
sources:
  sf:
    type: snowflake
    account: xy12345
    database: analytics
"""

BOARD_YAML = """\
variables:
  year:
    input: number
    default: 2023
queries:
  sales:
    sql: "SELECT month, revenue FROM t WHERE year = {{ year }}"
    source: sf
charts:
  rev:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - rev
"""


class _FakeRow:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def as_dict(self) -> dict[str, Any]:
        return self._data


class _FakeResultSet:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def to_local_iterator(self) -> Iterator[_FakeRow]:
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [_FakeRow(r) for r in rows]
        self.received_sql: list[str] = []

    def sql(self, sql: str) -> _FakeResultSet:
        self.received_sql.append(sql)
        return _FakeResultSet(self._rows)

    def get_current_database(self) -> str | None:
        return '"ANALYTICS"'

    def get_current_schema(self) -> str | None:
        return '"PUBLIC"'


class _FrameRecorder:
    """Stands in for the mounted board component and for Streamlit's session
    state, which is where a component's state lives between runs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.session_state: dict[str, Any] = {}

    def __call__(self, *, key: str, data: dict[str, Any], **callbacks: Any) -> None:
        self.calls.append({"key": key, **data})


@pytest.fixture
def frame(monkeypatch: pytest.MonkeyPatch) -> _FrameRecorder:
    from dbt_charts.integrations import streamlit as st_integration

    recorder = _FrameRecorder()
    monkeypatch.setattr(st_integration, "_BOARD_FRAME", recorder)
    monkeypatch.setattr(st_integration.st, "session_state", recorder.session_state)
    return recorder


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(DBT_CHARTS_YML)
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    (charts_dir / "board.yml").write_text(BOARD_YAML)
    return tmp_path


class TestStBoardWithSession:
    def test_fake_session_receives_the_rendered_sql(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        project_dir = _make_project(tmp_path)
        fake_session = _FakeSession(rows=[{"MONTH": "Jan", "REVENUE": 100}])
        html_recorder = frame

        st_integration.st_board(
            "charts/board.yml",
            project_dir=project_dir,
            session=fake_session,
            variables={"year": 2024},
        )

        assert fake_session.received_sql, "session.sql was never called"
        assert "2024" in fake_session.received_sql[0]
        assert len(html_recorder.calls) == 1
        assert "<svg" in html_recorder.calls[0]["html"]
        call = html_recorder.calls[0]
        root = re.search(
            r'<svg[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"', call["html"]
        )
        assert root is not None
        assert call["width"] == math.ceil(float(root.group(1)))
        assert call["height"] == math.ceil(float(root.group(2)))

    def test_file_source_still_renders_when_a_session_is_passed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        project_dir = _make_project(tmp_path)
        (project_dir / "dbt_charts.yml").write_text(
            DBT_CHARTS_YML
            + "  local_files:\n    type: csv\n    files:\n      targets: data/targets.csv\n"
        )
        (project_dir / "data").mkdir()
        (project_dir / "data" / "targets.csv").write_text("region,target\nNorth,10\n")
        (project_dir / "charts" / "files.yml").write_text(
            "queries:\n"
            "  targets:\n"
            "    sql: SELECT region, target FROM targets\n"
            "    source: local_files\n"
            "charts:\n"
            "  t:\n"
            "    query: targets\n"
            "    type: bar\n"
            "    x: region\n"
            "    y: target\n"
            "rows:\n"
            "  - t\n"
        )
        html_recorder = frame

        session = _FakeSession(rows=[])
        st_integration.st_board(
            "charts/files.yml", project_dir=project_dir, session=session
        )

        assert len(html_recorder.calls) == 1
        assert not session.received_sql

    def test_renders_without_a_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: _FrameRecorder
    ) -> None:
        """Without `session=`, the board still renders (through DuckDB/local dbt
        profile registration) and no session object is ever created or touched."""
        from dbt_charts.integrations import streamlit as st_integration

        # A board over a plain values query (no snowflake source at all) proves
        # the no-session path renders end to end with nothing Snowpark-shaped
        # anywhere in the picture.
        project_dir = tmp_path
        (project_dir / "dbt_charts.yml").write_text(DBT_CHARTS_YML)
        charts_dir = project_dir / "charts"
        charts_dir.mkdir()
        (charts_dir / "board.yml").write_text(
            "queries:\n"
            "  sales:\n"
            "    type: values\n"
            "    rows:\n"
            "      - {month: Jan, revenue: 100}\n"
            "charts:\n"
            "  rev:\n"
            "    query: sales\n"
            "    type: bar\n"
            "    x: month\n"
            "    y: revenue\n"
            "rows:\n"
            "  - rev\n"
        )
        html_recorder = frame

        from_project_kwargs: list[dict[str, Any]] = []
        real_from_project = st_integration.ProjectSession.from_project

        def _recording_from_project(*args: Any, **kwargs: Any) -> Any:
            from_project_kwargs.append(kwargs)
            return real_from_project(*args, **kwargs)

        monkeypatch.setattr(
            st_integration.ProjectSession, "from_project", _recording_from_project
        )

        st_integration.st_board("charts/board.yml", project_dir=project_dir)

        assert len(html_recorder.calls) == 1
        assert from_project_kwargs[0]["extra_adapters_factory"] is None


class TestLiveControls:
    def test_the_board_ships_its_control_runtime(
        self, tmp_path: Path, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        st_integration.st_board(
            "charts/board.yml",
            project_dir=_make_project(tmp_path),
            session=_FakeSession(rows=[{"MONTH": "Jan", "REVENUE": 100}]),
        )

        assert "dbt-variable-change" in frame.calls[0]["html"]

    def test_a_value_the_viewer_picked_is_rendered(
        self, tmp_path: Path, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        session = _FakeSession(rows=[{"MONTH": "Jan", "REVENUE": 100}])
        frame.session_state["sales"] = {"variables": {"year": "2025"}}

        st_integration.st_board(
            "charts/board.yml",
            project_dir=_make_project(tmp_path),
            session=session,
            key="sales",
        )

        assert "2025" in session.received_sql[0]

    def test_values_the_app_passes_win_over_the_viewers(
        self, tmp_path: Path, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        session = _FakeSession(rows=[{"MONTH": "Jan", "REVENUE": 100}])
        frame.session_state["sales"] = {"variables": {"year": "2025"}}

        st_integration.st_board(
            "charts/board.yml",
            project_dir=_make_project(tmp_path),
            session=session,
            variables={"year": 2024},
            key="sales",
        )

        assert "2024" in session.received_sql[0]
        assert "2025" not in session.received_sql[0]

    def test_each_board_has_its_own_state(
        self, tmp_path: Path, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        project_dir = _make_project(tmp_path)
        (project_dir / "charts" / "other.yml").write_text(BOARD_YAML)
        session = _FakeSession(rows=[{"MONTH": "Jan", "REVENUE": 100}])

        for board in ("charts/board.yml", "charts/other.yml"):
            st_integration.st_board(board, project_dir=project_dir, session=session)

        assert frame.calls[0]["key"] != frame.calls[1]["key"]


class TestStBoardFailure:
    def test_board_failure_raises_and_draws_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        (tmp_path / "dbt_charts.yml").write_text(DBT_CHARTS_YML)
        charts_dir = tmp_path / "charts"
        charts_dir.mkdir()
        (charts_dir / "board.yml").write_text(
            "charts:\n  rev:\n    query: nonexistent\n    type: bar\n    x: a\n    y: b\n"
        )
        html_recorder = frame

        with pytest.raises((ValueError, RenderError)):
            st_integration.st_board("charts/board.yml", project_dir=tmp_path)

        assert not html_recorder.calls

    def test_missing_board_file_raises_and_draws_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: _FrameRecorder
    ) -> None:
        from dbt_charts.integrations import streamlit as st_integration

        project_dir = _make_project(tmp_path)
        html_recorder = frame

        with pytest.raises(FileNotFoundError):
            st_integration.st_board("charts/missing.yml", project_dir=project_dir)

        assert not html_recorder.calls


class TestIframeSize:
    def test_reads_width_and_height_from_root_svg(self) -> None:
        from dbt_charts.integrations.streamlit import _board_size

        html = '<html><body><svg width="800" height="432.5" viewBox="0 0 800 432.5"></svg></body></html>'
        assert _board_size(html) == (800, 433)

    def test_raises_when_no_svg_present(self) -> None:
        from dbt_charts.integrations.streamlit import _board_size

        with pytest.raises(ValueError, match="svg"):
            _board_size("<html><body>no chart here</body></html>")

    def test_raises_when_svg_has_no_height_attribute(self) -> None:
        from dbt_charts.integrations.streamlit import _board_size

        with pytest.raises(ValueError, match="height"):
            _board_size('<svg width="100" viewBox="0 0 100 100"></svg>')


def test_importing_dbt_charts_does_not_import_streamlit_or_snowflake() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import dbt_charts; import sys; "
            "print('streamlit' in sys.modules); print('snowflake' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines == ["False", "False"], result.stdout


def test_dct_help_does_not_import_streamlit_or_snowflake() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['dct', '--help']\n"
            "try:\n"
            "    from dbt_charts.cli.main import app\n"
            "    app()\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('STREAMLIT_IMPORTED=', 'streamlit' in sys.modules)\n"
            "print('SNOWFLAKE_IMPORTED=', 'snowflake' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "STREAMLIT_IMPORTED= False" in result.stdout, result.stdout
    assert "SNOWFLAKE_IMPORTED= False" in result.stdout, result.stdout


def test_missing_streamlit_raises_actionable_import_error() -> None:
    code = (
        "import sys; sys.modules['streamlit'] = None\n"
        "import dbt_charts.integrations.streamlit\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "streamlit" in result.stderr.lower()
    assert "install" in result.stderr.lower()
