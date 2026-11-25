"""D3FormatError — raised for unsupported or invalid format specs."""

from __future__ import annotations


class D3FormatError(ValueError):
    """Raised when a format spec is invalid or uses an unsupported feature.

    Attributes:
        spec: The full format spec string that failed.
        position: 0-based position in spec where the problem was detected.
        reason: Human-readable explanation of the problem.
    """

    def __init__(self, spec: str, position: int, reason: str) -> None:
        self.spec = spec
        self.position = position
        self.reason = reason
        super().__init__(
            f"d3-format parse error at position {position} in {spec!r}: {reason}"
        )
