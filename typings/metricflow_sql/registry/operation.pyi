from collections.abc import Sequence

class Insert:
    def __init__(self, object: object) -> None: ...

class Batch:
    def __init__(self, ops: Sequence[Insert]) -> None: ...
