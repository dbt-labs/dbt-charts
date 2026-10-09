"""Tests for the unified DiagnosticCode registry (DiagnosticCode/ErrorCode/WarningCode/DiagnosticRegistry).

Covers:
- Registry shape: register(), get(), all(), codes()
- Level/prefix mismatch rejection
- Structure gate: format, title/doc/fix_template content floor, emission scan
"""

from __future__ import annotations

import ast
import inspect
import re
import string
from pathlib import Path

from dbt_charts.core.diagnostics.registry import DiagnosticCode

from ..._paths import DBT_CHARTS_DIR, DBT_CHARTS_PKG_DIR


def _hint_generator_missing_params(dc: DiagnosticCode) -> set[str]:
    """Required hint_generator params not covered by the code's template fields.

    Catches a generator/template *signature* mismatch — e.g. `ERR-SOURCE-
    INVALID-TYPE` was once wired to `suggest_close_source`, which needs a
    `source=` kwarg no raise site for that code ever supplied. `build_diagnostic`
    calls `ec.hint_generator(**fields)` as an untyped splat, so nothing short
    of this check catches the gap — every `fields` key comes from
    `message_template`'s own field names, so a required param outside that
    set can never be supplied and always raises `TypeError` at fire time.
    """
    assert dc.hint_generator is not None
    required = {
        name
        for name, param in inspect.signature(dc.hint_generator).parameters.items()
        if param.default is inspect.Parameter.empty
        and param.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    template_fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(dc.message_template)
        if field_name
    }
    return required - template_fields


# ─────────────────────────────── Registry shape ───────────────────────────────


class TestRegistryShape:
    def test_register_rejects_duplicate(self) -> None:
        from dbt_charts.core.diagnostics.registry import DiagnosticRegistry, ErrorCode

        reg = DiagnosticRegistry()
        ec = ErrorCode(
            code="ERR-TEST-UNIQUE",
            domain="render",
            title="Test code",
            message_template="Test {x}",
            doc="Fires during testing when uniqueness is checked.",
            docs_topic="errors",
        )
        reg.register(ec)
        import pytest

        with pytest.raises(ValueError, match="already registered"):
            reg.register(ec)

    def test_unregister_removes_a_registered_code(self) -> None:
        from dbt_charts.core.diagnostics.registry import DiagnosticRegistry, ErrorCode

        reg = DiagnosticRegistry()
        ec = ErrorCode(
            code="ERR-TEST-TEMP",
            domain="render",
            title="Temp code",
            message_template="Temp {x}",
            doc="A throwaway code registered then removed.",
            docs_topic="errors",
        )
        reg.register(ec)
        assert "ERR-TEST-TEMP" in reg.codes()

        reg.unregister("ERR-TEST-TEMP")

        assert "ERR-TEST-TEMP" not in reg.codes()

    def test_unregister_unknown_code_raises(self) -> None:
        import pytest

        from dbt_charts.core.diagnostics.registry import DiagnosticRegistry

        reg = DiagnosticRegistry()
        with pytest.raises(KeyError):
            reg.unregister("ERR-NOT-REGISTERED")

    def test_register_rejects_warn_code_on_errorcode(self) -> None:
        """WarningCode with ERR- prefix must fail at construction (field validation)."""
        import pytest

        from dbt_charts.core.diagnostics.registry import WarningCode

        with pytest.raises(Exception, match="ERR-"):  # ValidationError from Pydantic
            WarningCode(
                code="ERR-WRONG-LEVEL",
                domain="render",
                title="Should fail",
                message_template="fail",
                fix_template="fix",
                doc="Should not be constructable.",
                docs_topic="errors",
            )

    def test_register_rejects_err_code_on_warningcode(self) -> None:
        """ErrorCode with WARN- prefix must fail at construction (field validation)."""
        import pytest

        from dbt_charts.core.diagnostics.registry import ErrorCode

        with pytest.raises(Exception, match="WARN-"):  # ValidationError from Pydantic
            ErrorCode(
                code="WARN-WRONG-LEVEL",
                domain="render",
                title="Should fail",
                message_template="fail",
                doc="Should not be constructable.",
                docs_topic="errors",
            )

    def test_warningcode_requires_fix_template_at_construction(self) -> None:
        """fix_template is a required field on WarningCode, not registry-checked.

        Narrowing the type (mirroring how `level` is already narrowed on this
        subclass) means a missing fix_template is a pydantic ValidationError at
        construction time — a bare `assert` at a call site can't launder this
        gap because there's no gap to launder.
        """
        import pytest

        from dbt_charts.core.diagnostics.registry import WarningCode

        with pytest.raises(Exception, match="fix_template"):
            WarningCode(
                code="WARN-TEST-NO-FIX",
                domain="render",
                title="Should fail",
                message_template="fail",
                doc="Should not be constructable without fix_template.",
                docs_topic="errors",
            )

    def test_get_missing_raises_keyerror(self) -> None:
        import pytest

        from dbt_charts.core.diagnostics import REGISTRY

        with pytest.raises(KeyError):
            REGISTRY.get("ERR-DOES-NOT-EXIST")

    def test_codes_level_filter_warning(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        warning_codes = REGISTRY.codes(level="warning")
        assert all(c.startswith("WARN-") for c in warning_codes)

    def test_codes_level_filter_error(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        error_codes = REGISTRY.codes(level="error")
        assert all(c.startswith("ERR-") for c in error_codes)

    def test_all_level_filter(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        all_codes = REGISTRY.all()
        by_level_warning = REGISTRY.all(level="warning")
        by_level_error = REGISTRY.all(level="error")
        assert len(all_codes) == len(by_level_warning) + len(by_level_error)

    def test_doc_url_is_property_not_field(self) -> None:
        """doc_url must be derived from the code, never authored."""
        from dbt_charts.core.diagnostics.registry import DiagnosticCode

        fields = set(DiagnosticCode.model_fields.keys())
        assert "doc_url" not in fields, (
            "doc_url must be a @property, not a model field — "
            "one authored source per fact."
        )

    def test_no_level_arg_on_errorcode_or_warningcode(self) -> None:
        """ErrorCode and WarningCode must pin level; no caller can override it."""
        from dbt_charts.core.diagnostics.registry import ErrorCode, WarningCode

        ec = ErrorCode(
            code="ERR-TEST-LEVEL",
            domain="render",
            title="Test",
            message_template="t",
            doc="fires to test level pinning",
            docs_topic="errors",
        )
        assert ec.level == "error"

        wc = WarningCode(
            code="WARN-TEST-LEVEL",
            domain="render",
            title="Test warning",
            message_template="t",
            fix_template="fix it",
            doc="fires to test level pinning",
            docs_topic="errors",
        )
        assert wc.level == "warning"

    def test_every_hint_generator_params_subset_of_template_fields(self) -> None:
        """Every code's hint_generator required params must all be fields the
        code's own message_template supplies — `build_diagnostic` splats
        `fields` (derived from the template) straight into the generator, so
        a required param the template never carries always raises TypeError."""
        from dbt_charts.core.diagnostics import REGISTRY

        bad = {
            dc.code: missing
            for dc in REGISTRY.all()
            if dc.hint_generator is not None
            for missing in [_hint_generator_missing_params(dc)]
            if missing
        }
        assert bad == {}, f"hint_generator required params missing from template: {bad}"

    def test_sweep_detects_hint_generator_param_mismatch(self) -> None:
        """Proves the sweep helper actually catches a mis-wired generator —
        one requiring a param no template field supplies."""

        def _needs_unfulfilled_kwarg(
            unfulfilled: str, available: str = ""
        ) -> str | None:
            del unfulfilled, available
            return None

        from dbt_charts.core.diagnostics.registry import ErrorCode

        bad_code = ErrorCode(
            code="ERR-TEST-MISWIRED-HINT",
            domain="render",
            title="Mis-wired hint generator",
            message_template="Only {available} appears here.",
            doc="Fires only to exercise the sweep test's mismatch detection.",
            docs_topic="errors",
            hint_generator=_needs_unfulfilled_kwarg,
        )
        assert _hint_generator_missing_params(bad_code) == {"unfulfilled"}

    def test_registry_all_warns_through_agent_api_seam(self) -> None:
        """REGISTRY.codes(level='warning') through agent_api contains every WARN-*."""
        from dbt_charts.agent_api.diagnostics import REGISTRY

        warning_codes = REGISTRY.codes(level="warning")
        assert len(warning_codes) > 0
        assert all(c.startswith("WARN-") for c in warning_codes)

    def test_warning_codes_in_registry_via_agent_api(self) -> None:
        """Every WARN-* declaration is queryable through the agent_api seam."""
        from dbt_charts.agent_api.diagnostics import REGISTRY

        warn_codes = REGISTRY.codes(level="warning")
        # Spot-check a few known warning codes from each home
        expected = {
            "WARN-PIE-TOO-MANY-SEGMENTS",
            "WARN-REDUNDANT-ENCODING",
            "WARN-UNREFERENCED-CHART",
            "WARN-REAGGREGATION",
            "WARN-REDUNDANT-AUTHORED-LABEL",
        }
        missing = expected - warn_codes
        assert not missing, f"Expected warning codes not in registry: {missing}"


# ─────────────────────────────── Structure gate ──────────────────────────────


class TestStructureGate:
    def test_every_code_format_valid(self) -> None:
        """^(ERR|WARN)-[A-Z0-9]+(-[A-Z0-9]+)*$."""
        from dbt_charts.core.diagnostics import REGISTRY

        pattern = re.compile(r"^(ERR|WARN)-[A-Z0-9]+(-[A-Z0-9]+)*$")
        bad = [dc.code for dc in REGISTRY.all() if not pattern.match(dc.code)]
        assert bad == [], f"Codes with invalid format: {bad}"

    def test_every_code_has_nonempty_title(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        missing = [dc.code for dc in REGISTRY.all() if not dc.title]
        assert missing == [], f"Codes with empty title: {missing}"

    def test_every_code_has_nonempty_doc(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        missing = [dc.code for dc in REGISTRY.all() if not dc.doc]
        assert missing == [], f"Codes with empty doc: {missing}"

    def test_every_code_has_docs_topic(self) -> None:
        from dbt_charts.core.diagnostics import REGISTRY

        missing = [dc.code for dc in REGISTRY.all() if not dc.docs_topic]
        assert missing == [], f"Codes with empty docs_topic: {missing}"

    def test_doc_not_equal_to_title(self) -> None:
        """doc must not merely restate the title."""
        from dbt_charts.core.diagnostics import REGISTRY

        bad = [dc.code for dc in REGISTRY.all() if dc.doc == dc.title]
        assert bad == [], f"Codes where doc == title: {bad}"

    def test_doc_not_substring_of_message_template(self) -> None:
        """doc must not be a substring of message_template."""
        from dbt_charts.core.diagnostics import REGISTRY

        bad = [
            dc.code for dc in REGISTRY.all() if dc.doc and dc.doc in dc.message_template
        ]
        assert bad == [], f"Codes where doc is a substring of message_template: {bad}"

    def test_every_warning_code_has_fix_template(self) -> None:
        """fix_template is required on every WARN-* code, enforced at registration."""
        from dbt_charts.core.diagnostics import REGISTRY

        bad = [dc.code for dc in REGISTRY.all(level="warning") if not dc.fix_template]
        assert bad == [], f"Warning codes missing fix_template: {bad}"

    def test_docs_topic_is_a_real_syntax_guide_h2_slug(self) -> None:
        from dbt_charts.agent_api.docs._loader import _load_sections
        from dbt_charts.core.diagnostics import REGISTRY

        real_slugs = set(_load_sections().keys())
        bad = {
            dc.code: dc.docs_topic
            for dc in REGISTRY.all()
            if dc.docs_topic not in real_slugs
        }
        assert bad == {}, f"Codes with non-slug docs_topic: {bad}"

    def test_no_domain_segment_in_code(self) -> None:
        """domain is dropped from code string, lives only in the domain field."""
        from dbt_charts.core.diagnostics import REGISTRY

        bad = [
            dc.code
            for dc in REGISTRY.all()
            if dc.code.split("-", 1)[1].split("-")[0] == dc.domain.upper()
        ]
        assert bad == [], f"Codes still carrying a domain segment: {bad}"

    def test_suppressing_err_code_rejected(self) -> None:
        """Suppression is only valid for level='warning' codes."""
        import pytest

        from dbt_charts.core.diagnostics import REGISTRY
        from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

        error_code = next(iter(REGISTRY.codes(level="error")))
        with pytest.raises(ValueError, match="error code"):
            validate_suppression_codes([error_code], source="test")

    def test_registry_length_equals_declaration_count(self) -> None:
        """REGISTRY.all() matches declarations parsed from codes_*.py (relational check)."""
        from dbt_charts.core.diagnostics import REGISTRY

        declared = _parse_declared_codes()
        registered = set(REGISTRY.codes())
        assert registered == declared, (
            f"Registry and declarations out of sync. "
            f"Only-in-registry: {registered - declared}, "
            f"Only-in-declarations: {declared - registered}"
        )


# ──────────────────────────────── Emission scan ──────────────────────────────


def _parse_declared_codes() -> set[str]:
    """AST-walk codes_*.py to extract all DiagnosticCode/ErrorCode/WarningCode declarations."""
    codes_dir = DBT_CHARTS_PKG_DIR / "core" / "diagnostics"
    declared: set[str] = set()
    for codes_file in sorted(codes_dir.glob("codes_*.py")):
        src = codes_file.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in (
                    "DiagnosticCode",
                    "ErrorCode",
                    "WarningCode",
                ):
                    for kw in node.keywords:
                        if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                            declared.add(kw.value.value)
    return declared


def _find_emitted_codes(root_dirs: list[Path]) -> set[str]:
    """Find ERR-*/WARN-* code references in non-declaration, non-test source files.

    Two passes per file, both over the AST (never raw text) so a comment or
    docstring mentioning a symbol/code can't masquerade as a real emit site:
    1. Python symbol references: ``ast.Name`` nodes matching ``ERR_FOO`` /
       ``WARN_BAR`` are converted to code strings by replacing ``_`` with
       ``-``. Imports don't produce ``ast.Name`` nodes for the imported
       symbol, so a bare ``from ... import WARN_FOO`` with no other reference
       does not count as an emit site (a lingering import after the real
       emit site is deleted must not satisfy the coverage gate).
    2. String literal values: exact ``"ERR-FOO"`` / ``"WARN-BAR"`` constants
       via ``ast.Constant`` nodes.

    Skipped entirely:
    - test files
    - ``codes_*.py`` (declarations, not emitters)
    - ``diagnostics/__init__.py`` and ``diagnostics/registry.py`` (re-export/
      infrastructure: symbol names there are imports, not emission sites).
    """
    sym_re = re.compile(r"^(?:ERR|WARN)_[A-Z0-9]+(?:_[A-Z0-9]+)*$")
    str_re = re.compile(r"^(ERR|WARN)-[A-Z0-9]+(-[A-Z0-9]+)*$")
    # Files in core/diagnostics/ that only re-export or implement the registry —
    # not genuine emission sites.
    _DIAG_NON_EMITTERS = frozenset({"__init__.py", "registry.py"})
    emitted: set[str] = set()
    for root in root_dirs:
        for py_file in root.rglob("*.py"):
            parts = py_file.parts
            if "tests" in parts or "test_" in py_file.name:
                continue
            # Generated list of every registered code: a mention, not an emit site.
            if py_file.name == "schema_names.py":
                continue
            if py_file.parent.name == "diagnostics" and (
                py_file.name.startswith("codes_") or py_file.name in _DIAG_NON_EMITTERS
            ):
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # Pass 1: real identifier references (ast.Name), not text —
                # `import`s use ast.alias, not ast.Name, so a bare import
                # with no other reference is correctly excluded.
                if isinstance(node, ast.Name) and sym_re.match(node.id):
                    emitted.add(node.id.replace("_", "-"))
                # Pass 2: quoted string literals.
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if str_re.match(node.value):
                        emitted.add(node.value)
    return emitted


class TestEmissionScan:
    """Narrow, dbt-charts-only sub-assertions for the emission scan.

    The wide cross-package scan (all declared codes have an emit site
    somewhere in apps/ or libs/, and vice versa) is intentionally not
    covered here, since it reads outside dbt-charts/.
    """

    def test_sub_assertion_sql_lint_codes_found_in_inspect_and_adapter(self) -> None:
        """Narrowing to core/inspect/ + from_query_diagnostic.py must still find every SQL-lint code.

        This sub-assertion documents that the wide scan must reach core/inspect/ (where
        QueryDiagnostic.code is one of the SQL-lint codes) and
        from_query_diagnostic.py (the adapter that maps lowercase codes to WARN-* strings).
        """
        narrow_roots = [
            DBT_CHARTS_DIR / "src" / "dbt_charts" / "core" / "inspect",
            DBT_CHARTS_DIR
            / "src"
            / "dbt_charts"
            / "core"
            / "diagnostics"
            / "from_query_diagnostic.py",
        ]
        # Build a small emission check just on narrow roots
        narrow_roots_for_fn = []
        for r in narrow_roots:
            if r.is_dir():
                narrow_roots_for_fn.append(r)
            elif r.is_file():
                narrow_roots_for_fn.append(r.parent)

        narrow_emitted = _find_emitted_codes(narrow_roots_for_fn)
        sql_lint_codes = {
            "WARN-MISSING-JOIN-PREDICATE",
            "WARN-PARSE-ERROR",
            "WARN-REAGGREGATION",
            "WARN-DATE-AS-TEXT",
        }
        missing = sql_lint_codes - narrow_emitted
        assert missing == set(), (
            f"SQL-lint warning codes not found in core/inspect/ + "
            f"from_query_diagnostic.py (wide scan would flag them dead): {missing}"
        )

    def test_sub_assertion_compile_codes_found_in_compile(self) -> None:
        """Narrowing to core/compile/ must still find UNREFERENCED-CHART + 5 authoring codes."""
        compile_root = [DBT_CHARTS_DIR / "src" / "dbt_charts" / "core" / "compile"]
        compile_emitted = _find_emitted_codes(compile_root)
        compile_warn_codes = {
            "WARN-UNREFERENCED-CHART",
            "WARN-REDUNDANT-AUTHORED-LABEL",
            "WARN-REDUNDANT-AUTHORED-DEFAULT",
            "WARN-FLAT-COLS-UNSIZED-OVERFLOW",
            "WARN-DOUBLE-HEADER",
            "WARN-SINGLE-CHART-REDUNDANT-TITLE",
        }
        missing = compile_warn_codes - compile_emitted
        assert missing == set(), (
            f"Compile warning codes not found in core/compile/ "
            f"(wide scan would flag them dead): {missing}"
        )


# Step 9 regression test: WarningCode.redundant classifies the 6 redundancy codes.
def test_warning_code_redundant_flag_exists() -> None:
    """WarningCode.redundant defaults to False and is true for the six redundancy codes."""
    from dbt_charts.core.diagnostics.registry import REGISTRY, WarningCode

    # All WarningCode instances have the attribute
    all_warnings = [
        dc for dc in REGISTRY.all(level="warning") if isinstance(dc, WarningCode)
    ]
    assert all_warnings, "expected warning codes in registry"
    for dc in all_warnings:
        assert hasattr(dc, "redundant"), f"{dc.code} missing `redundant` attr"

    REDUNDANT_CODES = {
        "WARN-REDUNDANT-AUTHORED-LABEL",
        "WARN-REDUNDANT-AUTHORED-DEFAULT",
        "WARN-REDUNDANT-ENCODING",
        "WARN-SINGLE-CHART-REDUNDANT-TITLE",
        "WARN-DOUBLE-HEADER",
        "WARN-UNREFERENCED-CHART",
    }
    for code in REDUNDANT_CODES:
        dc = REGISTRY.get(code)
        assert isinstance(dc, WarningCode), f"{code} should be a WarningCode"
        assert dc.redundant is True, f"{code}.redundant should be True"

    # Non-redundant codes have redundant=False by default
    non_redundant = [dc for dc in all_warnings if dc.code not in REDUNDANT_CODES]
    for dc in non_redundant:
        assert dc.redundant is False, f"{dc.code}.redundant should default to False"
