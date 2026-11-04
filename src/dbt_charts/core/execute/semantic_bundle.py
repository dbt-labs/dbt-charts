"""What a source's MetricFlow bundle offers, as plain typed data.

Expressions are rendered in the bundle's dialect with the registry's own
identifier quoting; `SemanticLayer.describe` builds one from a loaded layer.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.constants.semantics import (
    SEMANTIC_SEARCH_CONTENT_WEIGHT,
    SEMANTIC_SEARCH_HITS,
    SEMANTIC_SEARCH_MIN_WORD,
    SEMANTIC_SEARCH_NAME_WEIGHT,
)


class SemanticField(BaseModel):
    """A column or role (`datatype`), or a calculation, attribute or metric
    (`expr`)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    datatype: str | None = None
    expr: str | None = None


class SemanticRelationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    target: str


class SemanticDataset(BaseModel):
    """`sql` is what a derived dataset selects; a table dataset has none."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    sql: str | None = None
    columns: list[SemanticField]
    calculations: list[SemanticField]
    relationships: list[SemanticRelationship]


class SemanticEntity(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    key: list[SemanticField]
    attributes: list[SemanticField]


class SemanticConstellation(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    roles: list[SemanticField]
    metrics: list[SemanticField]


class SemanticPage(BaseModel):
    """A markdown page stored with the bundle, such as a saved query. Pages are
    read and adapted by hand; they are not tables a query can name."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    content: str


class SemanticSummary(BaseModel):
    """One object of the bundle, its fields counted rather than listed."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    counts: dict[str, int]


class SemanticOverview(BaseModel):
    """What a bundle declares, each object's fields counted, not listed. A bundle
    with a default namespace lists it and counts the others, which can hold
    thousands of tables or saved queries and are reached by search or by name."""

    model_config = ConfigDict(frozen=True)

    name: str
    datasets: list[SemanticSummary]
    entities: list[SemanticSummary]
    constellations: list[SemanticSummary]
    namespaces: dict[str, int] = {}
    pages: dict[str, int] = {}


class SemanticHit(BaseModel):
    """An object, or one `field` of it, a search matched. `kind` says what it is:
    dataset, entity, constellation, page, column, calculation, attribute, role or
    metric."""

    model_config = ConfigDict(frozen=True)

    object: str
    kind: str
    field: str | None = None
    description: str | None = None


class SemanticSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    hits: list[SemanticHit]


SemanticObject = SemanticDataset | SemanticEntity | SemanticConstellation | SemanticPage


class SemanticBundle(BaseModel):
    """Every dataset, entity and constellation a bundle declares."""

    model_config = ConfigDict(frozen=True)

    name: str
    datasets: list[SemanticDataset]
    entities: list[SemanticEntity]
    constellations: list[SemanticConstellation]
    pages: list[SemanticPage] = []

    def overview(self) -> SemanticOverview:
        def summary(
            obj: SemanticDataset | SemanticEntity | SemanticConstellation, *lists: str
        ) -> SemanticSummary:
            return SemanticSummary(
                name=obj.name,
                description=obj.description,
                counts={field: len(getattr(obj, field)) for field in lists},
            )

        names = [
            obj.name for obj in (*self.datasets, *self.entities, *self.constellations)
        ]
        counted = any("." not in name for name in names)

        def shown(name: str) -> bool:
            return not counted or "." not in name

        namespaces = Counter(
            name.partition(".")[0] for name in names if not shown(name)
        )
        sections = Counter(page.name.partition("/")[0] for page in self.pages)
        return SemanticOverview(
            name=self.name,
            datasets=[
                summary(d, "columns", "calculations", "relationships")
                for d in self.datasets
                if shown(d.name)
            ],
            entities=[
                summary(e, "key", "attributes") for e in self.entities if shown(e.name)
            ],
            constellations=[
                summary(c, "roles", "metrics")
                for c in self.constellations
                if shown(c.name)
            ],
            namespaces=dict(sorted(namespaces.items())),
            pages=dict(sorted(sections.items())),
        )

    def search(self, query: str, within: str | None = None) -> SemanticSearchResult:
        """The objects and fields that best match `query`'s words, every namespace
        and page included, or only `within`'s fields. A word found in few places
        counts for more than one found everywhere."""
        candidates = [
            entry
            for entry in self._searchable()
            if within is None or entry[0].object == within
        ]
        stems = {word.rstrip("s") for word in _WORD.findall(query.lower())}
        words = {stem for stem in stems if len(stem) >= SEMANTIC_SEARCH_MIN_WORD}
        found = {
            word: [i for i, (_, text) in enumerate(candidates) if word in text]
            for word in words
        }
        scores: defaultdict[int, float] = defaultdict(float)
        for word, places in found.items():
            # Above zero even for a word in every candidate: one still matches.
            weight = math.log((2 + len(candidates)) / (1 + len(places)))
            for i in places:
                hit = candidates[i][0]
                name = (hit.field or hit.object).lower()
                if word in name:
                    where = SEMANTIC_SEARCH_NAME_WEIGHT
                elif hit.description and word in hit.description.lower():
                    where = 1.0
                else:
                    where = SEMANTIC_SEARCH_CONTENT_WEIGHT
                scores[i] += weight * where
        ranked = sorted(
            scores,
            key=lambda i: (-scores[i], candidates[i][0].field is not None, i),
        )
        return SemanticSearchResult(
            query=query,
            hits=[candidates[i][0] for i in ranked[:SEMANTIC_SEARCH_HITS]],
        )

    def _searchable(self) -> Iterator[tuple[SemanticHit, str]]:
        """Every hit a search can return, with the lowercased text it matches."""

        def entry(hit: SemanticHit, *text: str | None) -> tuple[SemanticHit, str]:
            return hit, " ".join(t for t in text if t).lower()

        def fields(
            obj: SemanticDataset | SemanticEntity | SemanticConstellation,
            kind: str,
            listed: list[SemanticField],
        ) -> Iterator[tuple[SemanticHit, str]]:
            for field in listed:
                hit = SemanticHit(
                    object=obj.name,
                    kind=kind,
                    field=field.name,
                    description=field.description,
                )
                yield entry(hit, field.name, field.description)

        for dataset in self.datasets:
            hit = _object_hit(dataset, "dataset")
            yield entry(hit, dataset.name, dataset.description)
            yield from fields(dataset, "column", dataset.columns)
            yield from fields(dataset, "calculation", dataset.calculations)
        for entity in self.entities:
            hit = _object_hit(entity, "entity")
            yield entry(hit, entity.name, entity.description)
            yield from fields(entity, "attribute", entity.attributes)
        for constellation in self.constellations:
            hit = _object_hit(constellation, "constellation")
            yield entry(hit, constellation.name, constellation.description)
            yield from fields(constellation, "role", constellation.roles)
            yield from fields(constellation, "metric", constellation.metrics)
        for page in self.pages:
            hit = _object_hit(page, "page")
            yield entry(hit, page.name, page.description, page.content)

    def objects(self) -> dict[str, SemanticObject]:
        """Every dataset, entity, constellation and page by its declared name."""
        return {
            obj.name: obj
            for obj in (
                *self.datasets,
                *self.entities,
                *self.constellations,
                *self.pages,
            )
        }

    def did_you_mean(self, message: str) -> list[str]:
        """Declared names that the names in a compile error's `message` nearly are.

        A name the engine quotes (`'x'`, `"a"."x"`) or ends the message with is
        matched, then each shorter qualification of it, against what a query may
        write: datasets, calculations, columns, relationships, constellations,
        roles, metrics, and `relationship.attribute` paths. A qualifier the
        engine does not know (`"main"."accounts"`) is dropped by matching the
        shorter form, and an attribute written without its relationship gets the
        path that reaches it.
        """
        known = self._writable_names()
        suggestions: list[str] = []
        for parts in _mentioned_names(message):
            for start in range(len(parts)):
                text = ".".join(parts[start:]).lower()
                if text in known:
                    # An exact name is only a suggestion when the engine took
                    # issue with how it was written: qualified, or without its path.
                    if start > 0 or known[text].lower() != text:
                        suggestions.append(known[text])
                    break
                close = difflib.get_close_matches(text, known, n=1, cutoff=0.8)
                if close:
                    suggestions.append(known[close[0]])
                    break
        return list(dict.fromkeys(suggestions))

    def _writable_names(self) -> dict[str, str]:
        """Lowercased name -> the name as a query writes it. An attribute's own
        name maps to its path when exactly one path reaches it, over a column or
        calculation of that name: the engine already refused the bare name."""
        attributes = {
            entity.name: [a.name for a in entity.attributes] for entity in self.entities
        }
        names = [d.name for d in self.datasets] + [c.name for c in self.constellations]
        for dataset in self.datasets:
            names += [f.name for f in (*dataset.columns, *dataset.calculations)]
            names += [r.name for r in dataset.relationships]
        for constellation in self.constellations:
            names += [f.name for f in (*constellation.roles, *constellation.metrics)]
        paths = {
            f"{r.name}.{attribute}"
            for dataset in self.datasets
            for r in dataset.relationships
            if r.target in attributes
            for attribute in attributes[r.target]
        }
        known = {name.lower(): name for name in (*names, *paths)}
        by_attribute: dict[str, set[str]] = {}
        for path in paths:
            by_attribute.setdefault(path.rpartition(".")[2].lower(), set()).add(path)
        for attribute, reaching in by_attribute.items():
            if len(reaching) == 1:
                known[attribute] = next(iter(reaching))
        return known


def _object_hit(obj: SemanticObject, kind: str) -> SemanticHit:
    return SemanticHit(object=obj.name, kind=kind, description=obj.description)


_WORD = re.compile(r"[a-z0-9]{2,}")
_NAMES_IN_MESSAGE = re.compile(
    r"""'(?P<single>[^']+)'|(?P<chain>(?:"[^"]+"\.?)+)|:\s*(?P<tail>\w+(?:\.\w+)*)\s*$"""
)


def _mentioned_names(message: str) -> list[list[str]]:
    """The names a compile error mentions, each as its dotted parts."""
    names: list[list[str]] = []
    for match in _NAMES_IN_MESSAGE.finditer(message):
        if match["single"]:
            names.append(match["single"].split("."))
        elif match["chain"]:
            names.append(re.findall(r'"([^"]+)"', match["chain"]))
        else:
            names.append(match["tail"].split("."))
    return names
