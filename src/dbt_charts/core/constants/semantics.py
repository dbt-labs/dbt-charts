"""How a search of a MetricFlow bundle ranks and limits what it returns."""

from typing import Final

# Enough to pick from without reading a page of near misses.
SEMANTIC_SEARCH_HITS: Final[int] = 25
# A shorter word is part of too many names to say anything.
SEMANTIC_SEARCH_MIN_WORD: Final[int] = 2
# A word in a name says more than one in a description, which counts 1.0.
SEMANTIC_SEARCH_NAME_WEIGHT: Final[float] = 2.0
# A page's SQL names the columns a question means, but also everything around them.
SEMANTIC_SEARCH_CONTENT_WEIGHT: Final[float] = 0.5
