from typing import Any

from nanoarrow._schema import CSchema

class CArray:
    @property
    def length(self) -> int: ...
    @property
    def schema(self) -> CSchema: ...
    def __arrow_c_array__(
        self, requested_schema: object | None = None
    ) -> tuple[Any, Any]: ...
