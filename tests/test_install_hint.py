"""Regression tests pinning the install-hint shape and its selection signal.

The string returned by ``install_hint`` is what every runtime "missing
extras" or "missing optional dependency" error tells the customer to
type. This helper only ever runs from an install that already exists, so
its signal is how the running dbt-charts got here — not whether ``uv``
happens to be on ``PATH``. The extension-side mirror
(``apps/vscode-extension/src/utils/install-hint.ts``) uses ``uv`` on
``PATH`` instead, because at that call site nothing is installed yet to
inspect. See ``install-hint.test.ts`` for that half.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dbt_charts._install_hint import install_hint


class TestInstallHintCanonicalShape:
    def test_bare_hint(self) -> None:
        assert install_hint() == 'pip install "dbt-charts"'

    def test_extras_hint(self) -> None:
        for extra in ("mcp", "bigquery"):
            assert install_hint(extra) == f'pip install "dbt-charts[{extra}]"'


class TestInstallHintSignal:
    """The regression that matters: don't mistake `uv` on PATH for a uv-tool install."""

    def test_pip_venv_install_keeps_recommending_pip_even_with_uv_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "prefix", "/Users/dave/project/.venv")
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
        assert install_hint() == 'pip install "dbt-charts"'

    def test_uv_tool_install_recommends_uv_tool_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys, "prefix", "/Users/dave/.local/share/uv/tools/dbt-charts"
        )
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        monkeypatch.delenv("PATH", raising=False)
        assert install_hint() == 'uv tool install "dbt-charts"'

    def test_uv_tool_install_extras_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys, "prefix", "/Users/dave/.local/share/uv/tools/dbt-charts"
        )
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        for extra in ("mcp", "bigquery"):
            assert install_hint(extra) == f'uv tool install "dbt-charts[{extra}]"'

    def test_uv_tool_dir_override_is_honored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A custom `UV_TOOL_DIR` moves the tool venv out of `uv/tools/` entirely."""
        monkeypatch.setattr(sys, "prefix", "/opt/custom-tools/dbt-charts")
        monkeypatch.setenv("UV_TOOL_DIR", "/opt/custom-tools")
        assert install_hint() == 'uv tool install "dbt-charts"'

    def test_symlinked_tool_python_is_still_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`uv tool install` gives each tool venv a `bin/python` symlink back into
        uv's shared interpreter directory, outside `uv/tools/` entirely. A detector
        that resolves `sys.executable` before inspecting it follows that symlink
        and never sees the `uv/tools/` shape it's looking for — this reproduces
        that layout and pins detection against `sys.prefix` instead, which uv sets
        to the tool venv's own (unresolved) directory."""
        real_python_dir = tmp_path / "uv" / "python" / "cpython-3.11" / "bin"
        real_python_dir.mkdir(parents=True)
        real_python = real_python_dir / "python3.11"
        real_python.write_text("")

        tool_dir = tmp_path / "uv" / "tools" / "dbt-charts"
        bin_dir = tool_dir / "bin"
        bin_dir.mkdir(parents=True)
        symlinked_python = bin_dir / "python"
        symlinked_python.symlink_to(real_python)

        monkeypatch.setattr(sys, "executable", str(symlinked_python))
        monkeypatch.setattr(sys, "prefix", str(tool_dir))
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)

        assert install_hint() == 'uv tool install "dbt-charts"'
