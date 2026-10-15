from collections.abc import Iterable

from .ttLib import TTFont

class Options:
    layout_features: list[str]
    def __init__(self) -> None: ...

class Subsetter:
    def __init__(self, options: Options | None = ...) -> None: ...
    def populate(
        self,
        glyphs: Iterable[str] = ...,
        gids: Iterable[int] = ...,
        unicodes: Iterable[int] = ...,
        text: str | bytes = ...,
    ) -> None: ...
    def subset(self, font: TTFont) -> None: ...
