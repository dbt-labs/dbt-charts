"""Public re-export of the diagnostic registry for cli/ and LSP consumers.

cli/ and the LSP server may not import dbt_charts.core.* directly (tach
enforces this). This module is the authorized boundary for reading the
registry and its types, plus the list/get lookups the MCP tools
(`list_diagnostic_codes`/`get_diagnostic_code`) call — no lookup logic lives
in the wrapper.
No test may depend on the import order of this module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.diagnostics import (
    REGISTRY,
    DiagnosticCode,
    ErrorCode,
    SourceRange,
    WarningCode,
    display_message,
)

__all__ = [
    "REGISTRY",
    "DiagnosticCode",
    "ErrorCode",
    "SourceRange",
    "WarningCode",
    "display_message",
    "ListDiagnosticCodesArgs",
    "GetDiagnosticCodeArgs",
    "DiagnosticCodeSummary",
    "DiagnosticCodeDetail",
    "DiagnosticCodesListResult",
    "DiagnosticCodeDetailResult",
    "DiagnosticDisplay",
    "list_diagnostic_codes",
    "get_diagnostic_code",
    "display_map",
]

# ----- Args types ------------------------------------------------------------


class ListDiagnosticCodesArgs(BaseModel):
    """List registered diagnostic codes, optionally filtered by level."""

    level: Literal["error", "warning"] | None = Field(
        None, description="Restrict to 'error' or 'warning' codes; omit for both."
    )

    model_config = ConfigDict(extra="forbid")


class GetDiagnosticCodeArgs(BaseModel):
    """Return full documentation for a single diagnostic code."""

    code: str = Field(
        ...,
        description="Diagnostic code to look up (e.g. 'ERR-NO-LAYOUT', 'WARN-REDUNDANT-ENCODING')",
    )

    model_config = ConfigDict(extra="forbid")


# ----- Result types ---------------------------------------------------------


class DiagnosticCodeSummary(BaseModel):
    """One row in the diagnostic code listing: code + one-line description."""

    code: str
    summary: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticDisplay(BaseModel):
    """Registry-derived presentation data for one code, shipped per page.

    Both fields are derived DiagnosticCode properties rather than serialized
    Diagnostic fields, so a display surface resolves them from the code it
    already has instead of receiving them on every payload.
    """

    title: str
    doc_url: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticCodeDetail(BaseModel):
    """Full details for a single diagnostic code."""

    code: str
    level: Literal["error", "warning"]
    summary: str
    doc: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticCodesListResult(BaseModel):
    """Envelope returned by list_diagnostic_codes()."""

    success: bool
    mode: Literal["diagnostic_list"]
    codes: list[DiagnosticCodeSummary]
    errors: list[str] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticCodeDetailResult(BaseModel):
    """Envelope returned by get_diagnostic_code()."""

    success: bool
    mode: Literal["diagnostic_detail"]
    detail: DiagnosticCodeDetail | None = None
    errors: list[str] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


# ----- Internal helpers ------------------------------------------------------


_MIN_SENTENCE_WORDS = 3


def _first_sentence(text: str) -> str:
    """Return the first sentence of a multi-sentence doc string.

    Splits at the first `.`/`!`/`?` and accepts the split only when it is
    unambiguously a complete sentence: the terminator is followed by
    whitespace or end-of-string, parens are balanced, backticks are
    balanced, and the candidate has a plausible sentence's word count.
    Anything else — a period inside an inline code span (`` `scale.type:
    log` ``), a bare identifier (`meta.yml`), an abbreviation inside
    parens (`(e.g. ...)`) — is not a real boundary and raises rather than
    guessing at a later one. A code whose doc trips this must author an
    explicit `summary` on its DiagnosticCode instead of relying on
    derivation.
    """
    text = text.strip()
    indices = [idx for term in (".", "!", "?") if (idx := text.find(term)) != -1]
    if not indices:
        return text
    idx = min(indices)
    candidate = text[: idx + 1]
    followed_by_boundary = idx + 1 == len(text) or text[idx + 1].isspace()
    if (
        not followed_by_boundary
        or candidate.count("(") != candidate.count(")")
        or candidate.count("`") % 2 != 0
        or len(candidate.split()) < _MIN_SENTENCE_WORDS
    ):
        raise ValueError(
            f"Cannot derive a summary from doc {text!r}: naive "
            "sentence-boundary detection failed — the candidate is not "
            "followed by whitespace/end-of-string, lands inside an unclosed "
            "parenthetical or inline code span, or is implausibly short. "
            "Author an explicit `summary` on this DiagnosticCode instead."
        )
    return candidate


def _summary_for(dc: DiagnosticCode) -> str:
    """Return dc.summary if authored, else the derived first sentence of dc.doc."""
    if dc.summary is not None:
        return dc.summary
    return _first_sentence(dc.doc)


# ----- Public API -----------------------------------------------------------


def list_diagnostic_codes(
    *, level: Literal["error", "warning"] | None = None
) -> DiagnosticCodesListResult:
    """Return registered diagnostic codes, optionally filtered by level, sorted alphabetically."""
    codes = [
        DiagnosticCodeSummary(code=dc.code, summary=_summary_for(dc))
        for dc in REGISTRY.all(level=level)
    ]
    return DiagnosticCodesListResult(success=True, mode="diagnostic_list", codes=codes)


def get_diagnostic_code(code: str) -> DiagnosticCodeDetailResult:
    """Return full documentation for a single diagnostic code.

    Args:
        code: Diagnostic code to look up. Case-insensitive.

    Returns:
        DiagnosticCodeDetailResult envelope. success=False with errors when
        code is not registered at all; success=True with detail (including
        its actual `level`) otherwise — a code registered under the level
        the caller didn't expect still resolves, so the caller decides
        whether/how to flag the mismatch.
    """
    normalized = code.upper()
    all_codes = REGISTRY.codes()
    if normalized not in all_codes:
        return DiagnosticCodeDetailResult(
            success=False,
            mode="diagnostic_detail",
            errors=[
                f"Unknown diagnostic code: {code!r}. "
                f"Registered codes: {', '.join(sorted(all_codes))}"
            ],
        )
    dc = REGISTRY.get(normalized)
    detail = DiagnosticCodeDetail(
        code=dc.code, level=dc.level, summary=_summary_for(dc), doc=dc.doc
    )
    return DiagnosticCodeDetailResult(
        success=True, mode="diagnostic_detail", detail=detail
    )


def display_map() -> dict[str, DiagnosticDisplay]:
    """The registry's code -> display map, built once for embedding alongside a page.

    `title` and `doc_url` are derived DiagnosticCode properties, never
    authored/serialized Diagnostic fields — Cloud and Playground each ship this
    map once rather than re-deriving a URL or restating a title per diagnostic.
    """
    return {
        dc.code: DiagnosticDisplay(title=dc.title, doc_url=dc.doc_url)
        for dc in REGISTRY.all()
    }
