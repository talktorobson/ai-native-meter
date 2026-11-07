"""A minimal context layer: ontology, knowledge graph, curation lint and budgeted retrieval.

The point is not the scoring formula; it is the contract. Every piece of context an agent
sees has a kind, an owner, a source and a freshness, and retrieval returns *why* each item
was picked. That contract is what lets a platform team improve context quality with evals
instead of guesswork.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

__all__ = [
    "ContextPack",
    "Edge",
    "Entity",
    "EntityKind",
    "Issue",
    "KnowledgeGraph",
    "Scored",
    "build_graph",
    "curation_weight",
    "estimate_tokens",
    "freshness",
    "is_stale",
    "lint",
    "load_graph",
    "render",
    "retrieve",
]


class EntityKind(StrEnum):
    SERVICE = "service"
    REPO = "repo"
    API = "api"
    DOC = "doc"
    RUNBOOK = "runbook"
    ADR = "adr"
    TICKET = "ticket"
    INCIDENT = "incident"
    TEAM = "team"


HALF_LIFE_DAYS: Mapping[EntityKind, float] = {
    EntityKind.SERVICE: 365,
    EntityKind.REPO: 365,
    EntityKind.API: 180,
    EntityKind.DOC: 90,
    EntityKind.RUNBOOK: 120,
    EntityKind.ADR: 730,
    EntityKind.TICKET: 30,
    EntityKind.INCIDENT: 90,
    EntityKind.TEAM: 365,
}

FRESHNESS_SLA_DAYS: Mapping[EntityKind, int] = {
    EntityKind.DOC: 180,
    EntityKind.RUNBOOK: 90,
    EntityKind.API: 180,
}

OWNERLESS_KINDS = frozenset({EntityKind.TEAM, EntityKind.TICKET, EntityKind.INCIDENT})

_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in"}
    | {"is", "it", "of", "on", "or", "the", "to", "we", "what", "when", "why", "with"}
)


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    kind: EntityKind
    title: str
    text: str
    source: str
    updated_at: datetime
    owner: str | None = None
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    relation: str


@dataclass(frozen=True, slots=True)
class KnowledgeGraph:
    entities: Mapping[str, Entity]
    edges: tuple[Edge, ...]

    def neighbors(self, entity_id: str) -> tuple[tuple[str, str], ...]:
        """Undirected neighbours as (entity_id, relation)."""
        return tuple(
            (e.dst if e.src == entity_id else e.src, e.relation)
            for e in self.edges
            if entity_id in (e.src, e.dst)
        )


@dataclass(frozen=True, slots=True)
class Scored:
    entity: Entity
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[Scored, ...]
    tokens_used: int
    budget: int
    dropped: int


@dataclass(frozen=True, slots=True)
class Issue:
    entity_id: str
    rule: str
    detail: str


def build_graph(entities: Iterable[Entity], edges: Iterable[Edge]) -> KnowledgeGraph:
    by_id = {e.id: e for e in entities}
    links = tuple(edges)
    dangling = [e for e in links if e.src not in by_id or e.dst not in by_id]
    if dangling:
        raise ValueError(f"Edges reference unknown entities: {dangling}")
    return KnowledgeGraph(entities=by_id, edges=links)


def load_graph(path: str | Path) -> KnowledgeGraph:
    raw = json.loads(Path(path).read_text())
    entities = (
        Entity(
            id=e["id"],
            kind=EntityKind(e["kind"]),
            title=e["title"],
            text=e["text"],
            source=e["source"],
            updated_at=datetime.fromisoformat(e["updated_at"]),
            owner=e.get("owner"),
            deprecated=e.get("deprecated", False),
        )
        for e in raw["entities"]
    )
    return build_graph(entities, (Edge(**e) for e in raw["edges"]))


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def freshness(entity: Entity, now: datetime) -> float:
    age_days = max((now - entity.updated_at).total_seconds() / 86_400, 0.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS[entity.kind])


def _idf(graph: KnowledgeGraph) -> Mapping[str, float]:
    docs = tuple(set(tokenize(f"{e.title} {e.text}")) for e in graph.entities.values())
    df = Counter(term for doc in docs for term in doc)
    return {term: math.log(1 + len(docs) / count) for term, count in df.items()}


def _lexical(query: tuple[str, ...], entity: Entity, idf: Mapping[str, float]) -> float:
    terms = Counter(tokenize(f"{entity.title} {entity.title} {entity.text}"))
    return sum(idf.get(q, 0.0) * math.log1p(terms[q]) for q in set(query))


def is_stale(entity: Entity, now: datetime) -> bool:
    sla = FRESHNESS_SLA_DAYS.get(entity.kind)
    return sla is not None and (now - entity.updated_at).days > sla


def curation_weight(entity: Entity, now: datetime, stale_penalty: float = 0.5) -> float:
    """Curation standards feed ranking: deprecated never ships, stale ships at a discount."""
    if entity.deprecated:
        return 0.0
    return stale_penalty if is_stale(entity, now) else 1.0


def _seed_scores(graph: KnowledgeGraph, query: str, now: datetime) -> dict[str, Scored]:
    idf = _idf(graph)
    terms = tokenize(query)
    scored = (
        (e, _lexical(terms, e, idf) * (0.5 + 0.5 * freshness(e, now)) * curation_weight(e, now))
        for e in graph.entities.values()
    )
    return {e.id: Scored(e, s, "lexical match") for e, s in scored if s > 0}


def _expand(
    graph: KnowledgeGraph, frontier: dict[str, Scored], hops: int, decay: float, now: datetime
) -> dict[str, Scored]:
    if hops == 0:
        return frontier
    reached = (
        Scored(neighbor, score, f"{relation} <- {item.entity.id}")
        for item in frontier.values()
        for nid, relation in graph.neighbors(item.entity.id)
        for neighbor in (graph.entities[nid],)
        for score in (item.score * decay * curation_weight(neighbor, now),)
        if score > 0
    )
    merged = dict(frontier)
    for candidate in reached:
        current = merged.get(candidate.entity.id)
        if current is None or candidate.score > current.score:
            merged[candidate.entity.id] = candidate
    return _expand(graph, merged, hops - 1, decay, now)


def _cost(entity: Entity) -> int:
    return estimate_tokens(f"{entity.title}\n{entity.text}")


def retrieve(
    graph: KnowledgeGraph,
    query: str,
    *,
    budget_tokens: int,
    now: datetime,
    hops: int = 2,
    decay: float = 0.5,
) -> ContextPack:
    """Lexical seeds, weighted by freshness and curation, expanded through the graph,
    then greedily packed under a token budget."""
    ranked = sorted(
        _expand(graph, _seed_scores(graph, query, now), hops, decay, now).values(),
        key=lambda s: (-s.score, s.entity.id),
    )
    chosen: list[Scored] = []
    used = 0
    for item in ranked:
        cost = _cost(item.entity)
        if used + cost <= budget_tokens:
            chosen.append(item)
            used += cost
    return ContextPack(
        items=tuple(chosen),
        tokens_used=used,
        budget=budget_tokens,
        dropped=len(ranked) - len(chosen),
    )


def render(pack: ContextPack) -> str:
    """Prompt-ready context block with provenance on every item."""
    return "\n\n".join(
        f"### [{s.entity.kind}] {s.entity.title}\n"
        f"source={s.entity.source} owner={s.entity.owner or 'none'} "
        f"updated={s.entity.updated_at.date()} why={s.reason}\n{s.entity.text}"
        for s in pack.items
    )


def lint(graph: KnowledgeGraph, now: datetime) -> tuple[Issue, ...]:
    """Curation standards as code: ownership, freshness SLA and connectivity."""
    linked = {e.src for e in graph.edges} | {e.dst for e in graph.edges}

    def issues_for(entity: Entity) -> Iterable[Issue]:
        if entity.owner is None and entity.kind not in OWNERLESS_KINDS:
            yield Issue(entity.id, "missing-owner", "Every curated entity needs an owner.")
        if is_stale(entity, now):
            age = (now - entity.updated_at).days
            sla = FRESHNESS_SLA_DAYS[entity.kind]
            yield Issue(entity.id, "stale", f"{age} days old, SLA is {sla} days.")
        if entity.deprecated and entity.id in linked:
            yield Issue(entity.id, "deprecated-linked", "Deprecated but still linked; archive it.")
        if entity.id not in linked:
            yield Issue(entity.id, "orphan", "Not connected to any other entity.")

    return tuple(issue for e in graph.entities.values() for issue in issues_for(e))
