from collections.abc import Sequence
from os import PathLike
from types import TracebackType
from typing import Any, BinaryIO

class TTLibError(Exception): ...

class TTFont:
    def __init__(
        self,
        file: str | PathLike[str] | BinaryIO | None = ...,
        *,
        recalcTimestamp: bool = ...,
        fontNumber: int = ...,
    ) -> None: ...
    def __enter__(self) -> TTFont: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def __contains__(self, tag: str | bytes) -> bool: ...
    # Font tables expose different attributes according to their registered tag.
    def __getitem__(self, tag: str | bytes) -> Any: ...
    def getBestCmap(
        self, cmapPreferences: Sequence[tuple[int, int]] = ...
    ) -> dict[int, str] | None: ...
    def save(
        self,
        file: str | PathLike[str] | BinaryIO,
        reorderTables: bool | None = ...,
    ) -> None: ...
