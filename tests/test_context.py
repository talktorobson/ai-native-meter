from datetime import datetime, timedelta
from pathlib import Path

import pytest

from devex.context import (
    Edge,
    Entity,
    EntityKind,
    build_graph,
    curation_weight,
    estimate_tokens,
    freshness,
    lint,
    load_graph,
    render,
    retrieve,
)

NOW = datetime(2026, 11, 1)
GRAPH = Path(__file__).parents[1] / "data" / "context_graph.json"


def entity(eid: str, kind=EntityKind.DOC, text="", age_days=0, owner="team", **kw) -> Entity:
    return Entity(
        id=eid,
        kind=kind,
        title=eid,
        text=text,
        source="test",
        updated_at=NOW - timedelta(days=age_days),
        owner=owner,
        **kw,
    )


def test_build_graph_rejects_dangling_edges():
    with pytest.raises(ValueError, match="unknown"):
        build_graph((entity("a"),), (Edge("a", "missing", "x"),))


def test_neighbors_are_undirected():
    graph = build_graph((entity("a"), entity("b")), (Edge("a", "b", "owns"),))
    assert graph.neighbors("a") == (("b", "owns"),)
    assert graph.neighbors("b") == (("a", "owns"),)


def test_freshness_halves_at_half_life():
    assert freshness(entity("d", age_days=90), NOW) == pytest.approx(0.5)
    assert freshness(entity("d", age_days=-5), NOW) == 1.0


def test_curation_weight():
    assert curation_weight(entity("d"), NOW) == 1.0
    assert curation_weight(entity("d", age_days=400), NOW) == 0.5
    assert curation_weight(entity("d", deprecated=True), NOW) == 0.0


def test_retrieve_respects_budget_and_explains_choices():
    big = "idempotency " * 200
    graph = build_graph(
        (
            entity("hit", text="idempotency keys for transfers"),
            entity("big", text=big),
            entity("linked", kind=EntityKind.SERVICE, text="ledger"),
        ),
        (Edge("hit", "linked", "governs"),),
    )
    pack = retrieve(graph, "idempotency transfers", budget_tokens=60, now=NOW)
    ids = [s.entity.id for s in pack.items]
    assert ids == ["hit", "linked"]
    assert pack.dropped == 1
    assert pack.tokens_used <= pack.budget
    assert pack.items[1].reason == "governs <- hit"


def test_retrieve_never_returns_deprecated_even_via_graph():
    graph = build_graph(
        (entity("hit", text="transfers"), entity("old", text="transfers", deprecated=True)),
        (Edge("hit", "old", "documents"),),
    )
    pack = retrieve(graph, "transfers", budget_tokens=1_000, now=NOW)
    assert [s.entity.id for s in pack.items] == ["hit"]


def test_sample_graph_puts_the_ticket_and_adr_in_context():
    graph = load_graph(GRAPH)
    pack = retrieve(
        graph, "add idempotency to the batch transfers endpoint", budget_tokens=400, now=NOW
    )
    ids = {s.entity.id for s in pack.items}
    assert {"tkt-881", "adr-idempotency", "repo-transfers"} <= ids
    assert "doc-legacy-transfers" not in ids
    assert "doc-onboarding-snacks" not in ids
    assert "source=jira" in render(pack)


def test_lint_reports_curation_issues():
    graph = build_graph(
        (
            entity("ok"),
            entity("ownerless", owner=None),
            entity("stale", age_days=400),
            entity("old", deprecated=True),
            entity("team", kind=EntityKind.TEAM, owner=None),
        ),
        (
            Edge("ok", "ownerless", "x"),
            Edge("ok", "stale", "x"),
            Edge("ok", "old", "x"),
            Edge("ok", "team", "x"),
        ),
    )
    found = {(i.entity_id, i.rule) for i in lint(graph, NOW)}
    assert found == {
        ("ownerless", "missing-owner"),
        ("stale", "stale"),
        ("old", "deprecated-linked"),
    }


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 10) == 10
