"""Stage 2 of a sourcing decision: score concrete candidates on evidence, not opinions.

Pipeline per candidate:

1. **Hard gates.** Fail any and the candidate is disqualified, whatever it scores.
2. **Normalise raw evidence** to [0, 1] against anchors fixed *before* bids arrive (so
   adding a candidate can never reorder the others): review quality is the F0.5 from
   ``review_eval``, cost is a 3-year TCO, time to value is in weeks.
3. **Discount by evidence level.** ``effective = 0.5 + credibility * (score - 0.5)``:
   an unproven number regresses toward "unknown", so a demo cannot win on its own.
4. **Lock-in as a priced risk.** Each missing portability term carries the engineer-weeks
   it would cost to leave; risk is the share of the worst case, exit cost is in dollars.
5. **Weighted sum** of effective scores, kept as a per-criterion contribution breakdown.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CRITERIA",
    "EVIDENCE",
    "LOCK_IN_EXIT_WEEKS",
    "LOCK_IN_FACTORS",
    "Anchors",
    "Assessment",
    "Candidate",
    "Claim",
    "Costs",
    "Criterion",
    "DecisionSpec",
    "Evidence",
    "Policy",
    "Scale",
    "Tco",
    "assess",
    "contributions",
    "discount",
    "exit_cost_usd",
    "linear",
    "load_decision",
    "lock_in_risk",
    "normalize_weights",
    "reweight",
    "tco",
    "total",
]

CRITERIA: tuple[str, ...] = ("review_quality", "tco", "time_to_value", "operability", "lock_in")
NEUTRAL = 0.5


@dataclass(frozen=True, slots=True)
class Evidence:
    credibility: float  # how far a score may move away from "unknown" (0.5)
    sd: float  # its noise in the Monte Carlo


EVIDENCE: Mapping[str, Evidence] = {
    "demo": Evidence(0.5, 0.15),  # vendor demo, claim or unvalidated internal estimate
    "pilot": Evidence(0.8, 0.08),  # measured in a pilot on our repositories
    "benchmark": Evidence(1.0, 0.03),  # measured by review_eval on the shared benchmark
    "contract": Evidence(1.0, 0.05),  # priced or contracted terms
}

# Engineer-weeks it costs to leave when a portability term is missing.
LOCK_IN_EXIT_WEEKS: Mapping[str, float] = {
    "data_export": 26,  # rebuild review history and feedback labels
    "open_interfaces": 20,  # rewire CI, IDE and gateway integrations
    "exit_under_90_days": 12,  # notice period and dual running
    "model_choice": 8,  # re-run evals and re-tune on another model
    "self_host_option": 6,  # re-platform for residency-constrained repositories
}
LOCK_IN_FACTORS: tuple[str, ...] = tuple(LOCK_IN_EXIT_WEEKS)


@dataclass(frozen=True, slots=True)
class Scale:
    engineers: int
    prs_per_engineer_week: float
    years: int
    engineer_week_usd: float
    fte_year_usd: float

    @property
    def prs_per_year(self) -> float:
        return self.engineers * self.prs_per_engineer_week * 52


@dataclass(frozen=True, slots=True)
class Costs:
    seat_usd_month: float = 0.0
    usd_per_m_tokens: float = 0.0
    tokens_m_per_pr: float = 0.0
    integration_weeks: float = 0.0
    run_usd_year: float = 0.0
    in_house_fte: float = 0.0


@dataclass(frozen=True, slots=True)
class Tco:
    licences: float
    tokens: float
    integration: float
    run: float
    people: float

    @property
    def total(self) -> float:
        return self.licences + self.tokens + self.integration + self.run + self.people


@dataclass(frozen=True, slots=True)
class Claim:
    value: float
    evidence: str = "demo"  # absent evidence is treated as a demo
    source: str = ""


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    motion: str  # build | co-build | buy
    gates: Mapping[str, bool]
    lock_in: Mapping[str, bool]
    costs: Costs
    review_quality: Claim  # F0.5
    time_to_value: Claim  # weeks
    operability: Claim  # 0-1 rubric
    cost_evidence: str = "contract"


@dataclass(frozen=True, slots=True)
class Anchors:
    """(best, worst) raw values that map to 1 and 0. Fixed before bids are opened."""

    tco_usd: tuple[float, float]
    time_to_value_weeks: tuple[float, float]


@dataclass(frozen=True, slots=True)
class Policy:
    clear_win_probability: float = 0.7
    contender_probability: float = 0.15
    min_score: float = 0.5
    samples: int = 2000
    seed: int = 7
    weight_concentration: float = 50.0


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    raw: str
    normalized: float
    evidence: str
    effective: float

    @property
    def sd(self) -> float:
        return EVIDENCE[self.evidence].sd


@dataclass(frozen=True, slots=True)
class Assessment:
    name: str
    motion: str
    failed_gates: tuple[str, ...]
    criteria: tuple[Criterion, ...]
    tco: Tco
    lock_in_risk: float
    exit_cost_usd: float
    missing_terms: tuple[str, ...]

    @property
    def qualified(self) -> bool:
        return not self.failed_gates

    def criterion(self, name: str) -> Criterion:
        return next(c for c in self.criteria if c.name == name)

    @property
    def effective(self) -> Mapping[str, float]:
        return {c.name: c.effective for c in self.criteria}


@dataclass(frozen=True, slots=True)
class DecisionSpec:
    capability: str
    weights: Mapping[str, float]
    anchors: Anchors
    scale: Scale
    candidates: tuple[Candidate, ...]
    policy: Policy = field(default_factory=Policy)


def tco(costs: Costs, scale: Scale) -> Tco:
    """Total cost of ownership over the horizon at projected scale."""
    return Tco(
        licences=costs.seat_usd_month * scale.engineers * 12 * scale.years,
        tokens=costs.usd_per_m_tokens * costs.tokens_m_per_pr * scale.prs_per_year * scale.years,
        integration=costs.integration_weeks * scale.engineer_week_usd,
        run=costs.run_usd_year * scale.years,
        people=costs.in_house_fte * scale.fte_year_usd * scale.years,
    )


def _missing(lock_in: Mapping[str, bool]) -> tuple[str, ...]:
    return tuple(f for f in LOCK_IN_FACTORS if not lock_in.get(f, False))


def lock_in_risk(lock_in: Mapping[str, bool]) -> float:
    """0 = fully portable, 1 = fully captive, weighted by the cost of each missing term."""
    return sum(LOCK_IN_EXIT_WEEKS[f] for f in _missing(lock_in)) / sum(LOCK_IN_EXIT_WEEKS.values())


def exit_cost_usd(lock_in: Mapping[str, bool], engineer_week_usd: float) -> float:
    return sum(LOCK_IN_EXIT_WEEKS[f] for f in _missing(lock_in)) * engineer_week_usd


def linear(value: float, best: float, worst: float) -> float:
    """Map best -> 1 and worst -> 0, clipped. Works for either direction."""
    return min(max((value - worst) / (best - worst), 0.0), 1.0)


def discount(score: float, evidence: str) -> float:
    return NEUTRAL + EVIDENCE[evidence].credibility * (score - NEUTRAL)


def _criterion(name: str, raw: str, normalized: float, evidence: str) -> Criterion:
    return Criterion(name, raw, normalized, evidence, discount(normalized, evidence))


def assess(candidate: Candidate, scale: Scale, anchors: Anchors) -> Assessment:
    cost = tco(candidate.costs, scale)
    risk = lock_in_risk(candidate.lock_in)
    q, t, o = candidate.review_quality, candidate.time_to_value, candidate.operability
    return Assessment(
        name=candidate.name,
        motion=candidate.motion,
        failed_gates=tuple(sorted(g for g, ok in candidate.gates.items() if not ok)),
        criteria=(
            _criterion("review_quality", f"F0.5 {q.value:.2f}", q.value, q.evidence),
            _criterion(
                "tco",
                f"${cost.total / 1e6:.2f}M",
                linear(cost.total, *anchors.tco_usd),
                candidate.cost_evidence,
            ),
            _criterion(
                "time_to_value",
                f"{t.value:g} wk",
                linear(t.value, *anchors.time_to_value_weeks),
                t.evidence,
            ),
            _criterion("operability", f"{o.value:.2f}", o.value, o.evidence),
            _criterion("lock_in", f"{risk:.0%} risk", 1 - risk, "contract"),
        ),
        tco=cost,
        lock_in_risk=risk,
        exit_cost_usd=exit_cost_usd(candidate.lock_in, scale.engineer_week_usd),
        missing_terms=_missing(candidate.lock_in),
    )


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    total_weight = sum(weights.values())
    return {k: v / total_weight for k, v in weights.items()}


def reweight(weights: Mapping[str, float], criterion: str, value: float) -> dict[str, float]:
    """Set one weight and rescale the others proportionally, so the sum stays 1."""
    base = normalize_weights(weights)
    rest = 1 - base[criterion]
    return {
        k: value if k == criterion else (v * (1 - value) / rest if rest else 0.0)
        for k, v in base.items()
    }


def contributions(
    assessment: Assessment, weights: Mapping[str, float]
) -> tuple[tuple[str, float], ...]:
    w = normalize_weights(weights)
    return tuple((c.name, w.get(c.name, 0.0) * c.effective) for c in assessment.criteria)


def total(assessment: Assessment, weights: Mapping[str, float]) -> float:
    return sum(v for _, v in contributions(assessment, weights))


def _claim(raw: Mapping[str, Any], key: str, measured: Mapping[str, float]) -> Claim:
    source = raw.get("reviewer", "")
    value = measured[source] if source else raw[key]
    return Claim(value=float(value), evidence=raw.get("evidence", "demo"), source=source)


def _candidate(raw: Mapping[str, Any], measured: Mapping[str, float]) -> Candidate:
    costs = {k: v for k, v in raw["costs"].items() if k != "evidence"}
    return Candidate(
        name=raw["name"],
        motion=raw["motion"],
        gates=raw["gates"],
        lock_in=raw["lock_in"],
        costs=Costs(**costs),
        review_quality=_claim(raw["review_quality"], "f05", measured),
        time_to_value=_claim(raw["time_to_value"], "weeks", measured),
        operability=_claim(raw["operability"], "score", measured),
        cost_evidence=raw["costs"].get("evidence", "contract"),
    )


def load_decision(path: str | Path, measured: Mapping[str, float] | None = None) -> DecisionSpec:
    """Load a decision. ``measured`` maps reviewer names to F0.5 from ``review_eval``:
    a candidate that names a ``reviewer`` gets its quality from the eval, not the file."""
    raw = json.loads(Path(path).read_text())
    anchors = raw["anchors"]
    return DecisionSpec(
        capability=raw["capability"],
        weights=raw["weights"],
        anchors=Anchors(
            tco_usd=tuple(anchors["tco_usd"]),
            time_to_value_weeks=tuple(anchors["time_to_value_weeks"]),
        ),
        scale=Scale(**raw["scale"]),
        candidates=tuple(_candidate(c, measured or {}) for c in raw["candidates"]),
        policy=Policy(**raw.get("policy", {})),
    )
