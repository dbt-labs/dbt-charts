"""Board text is parsed by libyaml's C loader — never the pure-Python scanner.

The two scanners do not accept the same language, so every parse of board
text must go through `YAML_LOADER`; a well-meaning swap back to
`yaml.SafeLoader`, or a stray `yaml.safe_load` on board text, is what this
file exists to catch.
"""

from __future__ import annotations

import io
import subprocess
import sys

import pytest
import yaml

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.parse import source_map
from dbt_charts.core.compile.validate.authoring_warnings import (
    detect_authoring_warnings,
)
from dbt_charts.core.utils import (
    MAX_YAML_NESTING,
    YAML_LOADER,
    NestingTooDeepError,
    UniqueKeyLoader,
)


def test_yaml_loader_is_pinned_to_libyaml() -> None:
    assert issubclass(YAML_LOADER, yaml.CSafeLoader)
    assert issubclass(UniqueKeyLoader, yaml.CSafeLoader)


def test_source_map_composes_with_the_pinned_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not decorative: `build_source_index`'s own `yaml.compose` actually routes
    through `YAML_LOADER`, not a hardcoded `yaml.SafeLoader`."""
    seen: list[str] = []

    class _MarkerLoader(yaml.CSafeLoader):
        def __init__(self, stream: str) -> None:
            seen.append("used")
            super().__init__(stream)

    monkeypatch.setattr(source_map, "YAML_LOADER", _MarkerLoader)
    index = source_map.build_source_index("title: T\n", "f.yml")
    assert index.source_map
    assert seen == ["used"]


# libyaml accepts a tab as separation whitespace; the pure-Python scanner does
# not. A board that the model parse accepts must be accepted by every later
# parse of the same text too, or it escapes `compile()` as a raw ScannerError.
TAB_BOARD = "title:\tMy Board\nrows: []\n"


def test_authoring_warnings_parse_the_same_dialect_as_the_model() -> None:
    assert detect_authoring_warnings(TAB_BOARD, "en") == []


def test_a_tab_never_escapes_compile_as_a_scanner_error() -> None:
    result = compile(TAB_BOARD)
    assert result.board is not None
    assert not result.errors


def test_a_pyyaml_without_libyaml_fails_at_import_with_the_install_hint() -> None:
    # A fresh interpreter: the flag is read at import, and this process has
    # already imported the module with libyaml present.
    # Block the C extension itself, as a wheel without libyaml would: PyYAML
    # then reports __with_libyaml__ False and our guard is what must fire.
    code = (
        "import sys; sys.modules['yaml._yaml'] = None; "
        "import yaml; assert not yaml.__with_libyaml__; "
        "import dbt_charts.core.utils"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0
    assert "ImportError: dbt-charts requires PyYAML built with libyaml" in proc.stderr


# libyaml's C composer recurses without a guard: past ~25k levels it segfaults
# the interpreter, where the Python scanner raised RecursionError. If the
# ceiling below ever stops holding, this test does not fail — the pytest
# process dies, which is the same signal, louder.
DEEP_BOARD = "title: T\nrows: " + "[" * 30_000 + "]" * 30_000 + "\n"


def test_nesting_past_the_ceiling_is_refused_not_composed() -> None:
    with pytest.raises(NestingTooDeepError) as caught:
        yaml.load(DEEP_BOARD, Loader=YAML_LOADER)  # noqa: S506 — BoardLoader, a safe loader
    assert caught.value.problem_mark is not None
    assert caught.value.problem_mark.line == 1


def test_nesting_at_the_ceiling_still_parses() -> None:
    # The top-level mapping is level one of the ceiling.
    inner = MAX_YAML_NESTING - 1
    text = "rows: " + "[" * inner + "]" * inner + "\n"
    assert yaml.load(text, Loader=YAML_LOADER)["rows"]  # noqa: S506 — BoardLoader, a safe loader


def test_a_deep_board_compiles_to_a_diagnostic_not_a_crash() -> None:
    result = compile(DEEP_BOARD)
    assert result.errors
    assert "nesting deeper than" in str(result.errors[0])


def test_a_stream_is_refused_rather_than_drained() -> None:
    with pytest.raises(TypeError, match="text, not a stream"):
        yaml.load(io.StringIO("title: T\n"), Loader=YAML_LOADER)  # noqa: S506 — BoardLoader, a safe loader
