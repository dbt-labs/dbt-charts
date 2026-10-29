from typing import TypeVar

from sqlglot import exp
from sqlglot.dialects.dialect import NormalizationStrategy

E = TypeVar("E", bound=exp.Expr)

def canonical(
    expression: E, dialect: str | None, strategy: NormalizationStrategy
) -> E: ...
def read_identifier(
    text: str, dialect: str | None, strategy: NormalizationStrategy
) -> exp.Identifier: ...
