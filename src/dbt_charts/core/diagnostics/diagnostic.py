"""Diagnostic: the one wire shape for both error- and warning-level diagnostics.

`level` is a property of the registered code, never a stored field — a
Diagnostic can't disagree with its own code's registered severity.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    computed_field,
    field_validator,
    model_validator,
)

from dbt_charts.core.diagnostics.registry import REGISTRY, DiagnosticCode, ErrorCode


class ColumnSpan(BaseModel):
    """1-based column range within a SourceRange's line(s).

    Optional by type, not by sentinel: not every diagnostic source can resolve
    a column (a path-based source-map lookup can't; a PyYAML mark can). Absent
    means "the range covers the whole line".
    """

    start_col: int
    end_col: int


class SourceRange(BaseModel):
    """A resolved position in a project file.

    `file` is required and never a sentinel for "we don't know" — a
    diagnostic whose origin file is unresolved gets no SourceRange at all
    (Diagnostic.range stays None), not a SourceRange with an empty file.
    """

    file: str
    start_line: int
    end_line: int
    columns: ColumnSpan | None = None


class RelatedLocation(BaseModel):
    """A second place a diagnostic is about, and why it points there.

    Some diagnostics are about a *relationship* between two authored spots —
    two y series sharing a scale, one column bound to two channels, a board
    title restated by a body heading. Marking one and staying silent about the
    other tells half the story, so those emitters attach the other half here.

    ``path`` and ``range`` mirror ``Diagnostic``'s own pair exactly, and
    ``stamp_diagnostics`` fills ``range`` in the same pass: one resolution
    mechanism, not a second. ``range`` is optional for the same single reason
    ``Diagnostic.range`` is — it is unset until stamping resolves it, and stays
    unset when the path does not resolve, rather than being fabricated.

    ``message`` is required: a secondary mark with no label is an unexplained
    squiggle, and an emitter that knows to point somewhere knows why. The pair
    maps onto LSP's ``DiagnosticRelatedInformation``.
    """

    path: str
    range: SourceRange | None = None
    message: str


class Diagnostic(BaseModel):
    """One error or warning crossing a boundary (CLI, API, UI, LSP).

    `title`, `doc_url`, `docs_topic`, and `domain` are NOT serialized here —
    consumers resolve them from the registry by `code` at display time.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    fix: str | None = None
    hint: str | None = None
    # provenance
    path: str | None = None  # dotted authoring path, e.g. "charts.rev.query"
    field: str | None = None  # the DATA column this diagnostic is about
    range: SourceRange | None = None
    chart: str | None = None
    query: str | None = None
    fields: dict[str, Any] = {}
    # Foreign text, log-only; see DbtChartsError.detail. Distinct from the same-named
    # fields["detail"] key (from_query_diagnostic.py), which is authored template text.
    detail: str | None = None
    # Secondary locations for a diagnostic about a relationship between two
    # authored spots. Populated at construction by the emitter that knows both
    # halves; never patched on afterwards.
    related: list[RelatedLocation] = []
    # Nested when the cause is itself a Diagnostic-producing error (preserves
    # code/level through the chain); a bare string for a foreign exception.
    cause: Diagnostic | str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def level(self) -> Literal["error", "warning"]:
        return REGISTRY.get(self.code).level

    @model_validator(mode="before")
    @classmethod
    def _check_and_pop_level(cls, data: Any) -> Any:
        # A computed field is dump-only — extra="forbid" would otherwise
        # reject the `level` key our own model_dump() just wrote. Pop it here,
        # after checking it against the registry so a stale/hand-edited
        # payload still fails loudly instead of deserializing clean.
        if isinstance(data, dict) and "level" in data:
            data = dict(data)
            claimed = data.pop("level")
            code = data.get("code")
            if code in REGISTRY.codes() and claimed != REGISTRY.get(code).level:
                raise ValueError(f"level {claimed!r} disagrees with registry")
        return data

    @field_validator("code")
    @classmethod
    def _known_code(cls, v: str) -> str:
        if v not in REGISTRY.codes():
            raise ValueError(f"unregistered diagnostic code: {v}")
        return v

    @classmethod
    def from_code(
        cls,
        dc: DiagnosticCode,
        *,
        message: str,
        fix: str | None = None,
        hint: str | None = None,
        path: str | None = None,
        field: str | None = None,
        range: SourceRange | None = None,
        chart: str | None = None,
        query: str | None = None,
        fields: dict[str, Any] | None = None,
        detail: str | None = None,
        cause: Diagnostic | str | None = None,
        related: tuple[RelatedLocation, ...] = (),
    ) -> Diagnostic:
        """Build a Diagnostic from a registered code — the one construction seam.

        `level` is never passed here; it derives from `dc` via the computed
        field. Every emitter (warning detectors, DbtChartsError.to_diagnostic,
        from_query_diagnostic) goes through this factory.
        """
        return cls(
            code=dc.code,
            message=message,
            fix=fix,
            hint=hint,
            path=path,
            field=field,
            range=range,
            chart=chart,
            query=query,
            fields=fields or {},
            detail=detail,
            cause=cause,
            related=list(related),
        )


def display_message(d: Diagnostic) -> str:
    """User-facing message with query attribution appended, when known.

    Legacy ``ExecutionError.__init__`` bakes ``(query: name)`` into the message
    itself via ``_format_message()``. ``DbtChartsError.from_code`` (used by
    migrated adapters) bypasses that constructor, so the name survives only as
    provenance and the raw message loses it. Every human-facing presentation
    surface (CLI panels, the SVG error placard) must call this instead of
    reading ``.message`` directly, so a failure reads identically regardless of
    which construction path raised it.
    """
    if not d.query:
        return d.message
    suffix = f" (query: {d.query})"
    if d.message.endswith(suffix):
        return d.message
    return d.message + suffix


def build_diagnostic(exc: Any, *, file: str | None = None) -> Diagnostic:
    """Build a Diagnostic from any DbtChartsError subclass.

    The exc must have a populated .code attribute (an ErrorCode instance).
    Raises RuntimeError if .code is None — that path should never happen once
    every subclass __init__ stamps the legacy fallback code; raise (not
    assert) so the guard survives `python -O`.
    """
    ec = exc.code
    if not isinstance(ec, ErrorCode):
        raise RuntimeError(
            f"build_diagnostic called on {type(exc).__name__!r} whose .code is "
            f"{type(ec).__name__!r}, not an ErrorCode. Every DbtChartsError "
            "subclass must stamp a code at construction."
        )

    fields: dict[str, Any] = getattr(exc, "fields", {})

    hint: str | None = getattr(exc, "hint", None)
    if hint is None and ec.hint_generator is not None:
        hint = ec.hint_generator(**fields)

    # "" is the class-level default for "no path resolved"; Diagnostic.path
    # types that absence as None.
    path = exc.field_path or None

    line = exc.line
    column = exc.column
    range_: SourceRange | None = None
    if file is not None and line is not None:
        columns = (
            ColumnSpan(start_col=column, end_col=column) if column is not None else None
        )
        range_ = SourceRange(file=file, start_line=line, end_line=line, columns=columns)

    cause: Diagnostic | str | None = None
    raw_cause = exc.__cause__
    if raw_cause is not None:
        # Recurse for our own DbtChartsError chain so structured consumers
        # keep code/level; flatten foreign exceptions to a string. Use
        # isinstance(ErrorCode) — duck-typing on `.code is not None` would
        # falsely recurse into foreign exceptions like urllib.error.HTTPError
        # that carry a non-ErrorCode `.code` attr.
        raw_code = getattr(raw_cause, "code", None)
        if isinstance(raw_code, ErrorCode):
            cause = build_diagnostic(raw_cause)
        else:
            cause = str(raw_cause)

    return Diagnostic.from_code(
        ec,
        message=str(exc),
        fix=ec.fix_template,
        hint=hint,
        path=path,
        range=range_,
        query=fields.get("query_name"),
        fields=fields,
        detail=exc.detail,
        cause=cause,
    )
