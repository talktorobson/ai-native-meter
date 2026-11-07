"""Build-vs-partner decisions as data: hard gates, weighted criteria and lock-in risk.

Subjective debates ("vendor X felt smarter") turn into a reproducible ranking. Quality
criteria should be fed from eval reports (see ``review_eval``), not from demos.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LOCK_IN_FACTORS",
    "Candidate",
    "Decision",
    "Recommendation",
    "decide",
    "load_candidates",
    "lock_in_risk",
    "score",
]

LOCK_IN_FACTORS: tuple[str, ...] = (
    "data_export",
    "open_interfaces",
    "model_choice",
    "self_host_option",
    "exit_under_90_days",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    motion: str  # build | partner | buy
    scores: Mapping[str, float]
    gates: Mapping[str, bool]
    lock_in: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class Decision:
    name: str
    motion: str
    score: float
    lock_in_risk: float
    failed_gates: tuple[str, ...]

    @property
    def qualified(self) -> bool:
        return not self.failed_gates


@dataclass(frozen=True, slots=True)
class Recommendation:
    ranking: tuple[Decision, ...]
    verdict: str


def lock_in_risk(candidate: Candidate) -> float:
    """0 = fully portable, 1 = fully captive."""
    return 1 - sum(candidate.lock_in.get(f, False) for f in LOCK_IN_FACTORS) / len(LOCK_IN_FACTORS)


def score(candidate: Candidate, weights: Mapping[str, float], lock_in_penalty: float) -> float:
    total_weight = sum(weights.values())
    weighted = sum(w * candidate.scores.get(name, 0.0) for name, w in weights.items())
    return weighted / total_weight * (1 - lock_in_penalty * lock_in_risk(candidate))


def decide(
    candidates: Iterable[Candidate],
    weights: Mapping[str, float],
    *,
    lock_in_penalty: float = 0.25,
    tie_margin: float = 0.05,
) -> Recommendation:
    decisions = tuple(
        Decision(
            name=c.name,
            motion=c.motion,
            score=score(c, weights, lock_in_penalty),
            lock_in_risk=lock_in_risk(c),
            failed_gates=tuple(sorted(g for g, ok in c.gates.items() if not ok)),
        )
        for c in candidates
    )
    ranking = tuple(sorted(decisions, key=lambda d: (not d.qualified, -d.score, d.name)))
    qualified = tuple(d for d in ranking if d.qualified)
    return Recommendation(ranking=ranking, verdict=_verdict(qualified, tie_margin))


def _verdict(qualified: tuple[Decision, ...], tie_margin: float) -> str:
    if not qualified:
        return "No candidate passes the hard gates: re-scope or build a minimal in-house path."
    top = qualified[0]
    if len(qualified) > 1 and top.score - qualified[1].score < tie_margin:
        runner = qualified[1]
        return (
            f"Too close to call ({top.name} {top.score:.2f} vs {runner.name} "
            f"{runner.score:.2f}): run a time-boxed bake-off on the shared eval set."
        )
    return f"Go with {top.name} ({top.motion}), score {top.score:.2f}."


def load_candidates(path: str | Path) -> tuple[Mapping[str, float], tuple[Candidate, ...]]:
    raw = json.loads(Path(path).read_text())
    return raw["weights"], tuple(Candidate(**c) for c in raw["candidates"])
