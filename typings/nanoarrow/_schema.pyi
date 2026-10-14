from collections.abc import Iterable
from typing import Any

class CSchema:
    @property
    def format(self) -> str: ...
    @property
    def name(self) -> str | None: ...
    @property
    def children(self) -> Iterable[CSchema]: ...
    @property
    def dictionary(self) -> CSchema | None: ...
    def modify(
        self,
        *,
        format: str | None = None,
        children: Iterable[CSchema] | None = None,
        dictionary: CSchema | None = None,
    ) -> CSchema: ...
    def __arrow_c_schema__(self) -> Any: ...
