"""Unified diagnostic registry: DiagnosticCode, ErrorCode, WarningCode, DiagnosticRegistry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError

from dbt_charts._docs_site import docs_site_url

# The conceptual guide page (what errors/warnings are, how suppression works).
# Per-code documentation lives on the generated reference pages instead — see
# build_doc_url().
ERROR_GUIDE_PATH = "guides/error-handling"

_DC = TypeVar("_DC", bound="DiagnosticCode")

# pydantic error type of ErrorCode.validation_error(); ctx["code"] names the code.
CODED_VALIDATION_ERROR = "coded_error"

_LEVEL_PREFIX: dict[str, str] = {
    "error": "ERR-",
    "warning": "WARN-",
}

_REFERENCE_PAGE: dict[str, str] = {
    "error": "errors",
    "warning": "warnings",
}


def build_doc_url(code: str) -> str:
    """Build the canonical doc URL — an anchor on the code's generated reference page."""
    if code.startswith(_LEVEL_PREFIX["error"]):
        page = _REFERENCE_PAGE["error"]
    elif code.startswith(_LEVEL_PREFIX["warning"]):
        page = _REFERENCE_PAGE["warning"]
    else:
        raise ValueError(
            f"Code {code!r} has no recognized ERR-/WARN- prefix; cannot build a doc URL."
        )
    return f"{docs_site_url()}/reference/{page}/#{code.lower()}"


class DiagnosticCode(BaseModel):
    """Base for all diagnostic codes. Level is pinned by ErrorCode/WarningCode subclasses."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    level: Literal["error", "warning"]
    title: str
    domain: Literal["compile", "query", "execute", "render", "serve", "unknown"]
    docs_topic: str
    message_template: str
    doc: str
    # Explicit one-line summary. Only needed when `doc`'s first sentence can't
    # be split on ./!/? cleanly (e.g. an "(e.g. ...)" parenthetical before the
    # real sentence boundary) — the agent_api warnings surface derives a
    # summary from `doc` by default and raises instead of guessing when it
    # can't find a clean boundary; author `summary` explicitly for those codes.
    summary: str | None = None
    hint_generator: Callable[..., str | None] | None = None

    @model_validator(mode="after")
    def _validate_code_prefix(self) -> DiagnosticCode:
        expected = _LEVEL_PREFIX[self.level]
        if not self.code.startswith(expected):
            raise ValueError(
                f"Code {self.code!r} has level {self.level!r} but must start with "
                f"{expected!r}. Level is authored by the subclass, not the caller."
            )
        return self

    @property
    def doc_url(self) -> str:
        """Derived URL — never authored as a constructor argument."""
        return build_doc_url(self.code)


class ErrorCode(DiagnosticCode):
    """An error-level diagnostic code. level is always 'error'.

    fix_template is declared here, not on the base, and is optional — an
    error's fix is normally embedded in message_template or delegated to
    hint_generator; set fix_template explicitly only when the fix text is
    long enough or reusable enough to warrant its own field. (It can't be a
    narrowing override of a base field: WarningCode needs the opposite
    variance — required, not optional — and mypy's attribute-override check
    only tolerates narrowing, not widening, in either direction. Declaring
    each subclass's fix_template independently sidesteps the conflict.)
    """

    level: Literal["error"] = "error"
    fix_template: str | None = None

    def validation_error(
        self,
        **fields: object,  # type-state: object_annotation — template fields are heterogeneous, as in DbtChartsError.from_code
    ) -> PydanticCustomError:
        """The error a pydantic validator raises to report this code.

        The rendered message rides in context so pydantic never re-formats it.
        """
        return PydanticCustomError(
            CODED_VALIDATION_ERROR,
            "{message}",
            {
                "message": self.message_template.format(**fields),
                "code": self.code,
                "fields": fields,
            },
        )


class WarningCode(DiagnosticCode):
    """A warning-level diagnostic code. level is always 'warning'.

    fix_template is required here — every WARN-* code must tell the user how
    to fix it, and pydantic enforces this at construction time. No downstream
    `assert fix_template is not None` is ever needed.

    redundant=True marks codes whose fix is always "delete this authored
    element" — the warning fires because something the author wrote is
    unnecessary, not because something is wrong. VS Code renders these with
    DiagnosticTag.Unnecessary (a visual fade) rather than a squiggle.
    """

    level: Literal["warning"] = "warning"
    fix_template: str
    redundant: bool = False


class DiagnosticRegistry:
    """Duplicate-rejecting registry for all DiagnosticCode instances."""

    def __init__(self) -> None:
        self._by_code: dict[str, DiagnosticCode] = {}

    def register(self, dc: _DC) -> _DC:
        """Register a code; raises ValueError on duplicate. Returns the same instance."""
        if dc.code in self._by_code:
            raise ValueError(f"DiagnosticCode {dc.code!r} already registered")
        self._by_code[dc.code] = dc
        return dc

    def get(self, code: str) -> DiagnosticCode:
        """Return the code; raises KeyError if not registered."""
        return self._by_code[code]

    def unregister(self, code: str) -> None:
        """Remove a registered code; raises KeyError if not registered.

        For tests that register a throwaway code to prove a code-agnostic
        pass works generically, then must remove it so it doesn't leak into
        the module-global REGISTRY for later tests.
        """
        del self._by_code[code]

    def all(
        self,
        *,
        level: Literal["error", "warning"] | None = None,
    ) -> list[DiagnosticCode]:
        """Return all codes matching optional filters, sorted by code string."""
        result: list[DiagnosticCode] = list(self._by_code.values())
        if level is not None:
            result = [dc for dc in result if dc.level == level]
        return sorted(result, key=lambda dc: dc.code)

    def codes(self, *, level: str | None = None) -> frozenset[str]:
        """Return all registered code strings, optionally filtered by level."""
        if level is None:
            return frozenset(self._by_code)
        return frozenset(dc.code for dc in self._by_code.values() if dc.level == level)


REGISTRY = DiagnosticRegistry()
