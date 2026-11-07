"""Stage 1 of a sourcing decision: should we own this capability at all?

Each capability is profiled on a few attributes in [0, 1]. Each motion (build, co-build,
buy) has a fixed affinity per attribute, signed and summing to 1 in absolute value, so
a motion's fit is

    fit(m) = 0.5 + sum_i affinity[m][i] * (attribute_i - 0.5)

which stays in [0, 1]: 0.5 is "no opinion", every term is an explainable push up or
down. The affinities encode one principle: **own the context, the evals and the
interfaces; rent the rest.** Guards turn the principle into hard rules: a capability
that *is* our context is never bought, and nothing is built without a team to own it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AFFINITY",
    "ATTRIBUTES",
    "GUARDS",
    "MOTIONS",
    "OWN_ALWAYS",
    "Capability",
    "Guard",
    "MotionFit",
    "MotionScore",
    "assess",
    "fit",
    "load_capabilities",
    "motion_scores",
    "to_markdown",
]

ATTRIBUTES: tuple[str, ...] = (
    "differentiation",
    "proprietary_context",
    "market_maturity",
    "market_velocity",
    "data_sensitivity",
    "internal_capacity",
    "time_pressure",
)
MOTIONS: tuple[str, ...] = ("build", "co-build", "buy")
OWN_ALWAYS: tuple[str, ...] = ("the context", "the evals", "the interfaces")

AFFINITY: Mapping[str, Mapping[str, float]] = {
    # Build where the capability differentiates us and runs on our own context.
    "build": {
        "differentiation": 0.25,
        "proprietary_context": 0.25,
        "market_maturity": -0.10,
        "market_velocity": -0.10,
        "data_sensitivity": 0.10,
        "internal_capacity": 0.15,
        "time_pressure": -0.05,
    },
    # Co-build where our context matters but the model frontier moves faster than we can.
    "co-build": {
        "differentiation": 0.10,
        "proprietary_context": 0.25,
        "market_maturity": -0.15,
        "market_velocity": 0.20,
        "data_sensitivity": 0.05,
        "internal_capacity": -0.15,
        "time_pressure": 0.10,
    },
    # Buy commodities: mature, fast-moving markets that do not need our context.
    "buy": {
        "differentiation": -0.20,
        "proprietary_context": -0.25,
        "market_maturity": 0.20,
        "market_velocity": 0.10,
        "data_sensitivity": -0.05,
        "internal_capacity": -0.05,
        "time_pressure": 0.15,
    },
}


@dataclass(frozen=True, slots=True)
class Guard:
    motion: str
    attribute: str
    threshold: float
    above: bool  # True: blocked when attribute >= threshold; False: when below it
    reason: str

    def blocks(self, attributes: Mapping[str, float]) -> bool:
        value = attributes.get(self.attribute, 0.5)
        return value >= self.threshold if self.above else value < self.threshold


GUARDS: tuple[Guard, ...] = (
    Guard("buy", "proprietary_context", 0.85, True, "it is our own context"),
    Guard("build", "internal_capacity", 0.20, False, "no team to own a build"),
)


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    attributes: Mapping[str, float]
    note: str = ""


@dataclass(frozen=True, slots=True)
class MotionScore:
    motion: str
    fit: float
    contributions: tuple[tuple[str, float], ...]
    blocked_by: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.blocked_by


@dataclass(frozen=True, slots=True)
class MotionFit:
    capability: Capability
    scores: tuple[MotionScore, ...]  # allowed motions first, best fit first
    rationale: str

    @property
    def recommended(self) -> MotionScore:
        return self.scores[0]

    @property
    def margin(self) -> float:
        return self.scores[0].fit - self.scores[1].fit if len(self.scores) > 1 else 1.0

    def score(self, motion: str) -> MotionScore:
        return next(s for s in self.scores if s.motion == motion)


def _contributions(attributes: Mapping[str, float], motion: str) -> tuple[tuple[str, float], ...]:
    return tuple(
        (name, AFFINITY[motion][name] * (attributes.get(name, 0.5) - 0.5)) for name in ATTRIBUTES
    )


def fit(attributes: Mapping[str, float], motion: str) -> float:
    return 0.5 + sum(c for _, c in _contributions(attributes, motion))


def motion_scores(capability: Capability) -> tuple[MotionScore, ...]:
    scores = (
        MotionScore(
            motion=m,
            fit=fit(capability.attributes, m),
            contributions=_contributions(capability.attributes, m),
            blocked_by=tuple(
                g.reason for g in GUARDS if g.motion == m and g.blocks(capability.attributes)
            ),
        )
        for m in MOTIONS
    )
    return tuple(sorted(scores, key=lambda s: (not s.allowed, -s.fit, MOTIONS.index(s.motion))))


def _label(attribute: str) -> str:
    return attribute.replace("_", " ")


def _owned() -> str:
    return f"{', '.join(OWN_ALWAYS[:-1])} and {OWN_ALWAYS[-1]}"


def _rationale(scores: Sequence[MotionScore]) -> str:
    top, runner = scores[0], scores[1]
    deltas = sorted(
        (
            (name, a - b)
            for (name, a), (_, b) in zip(top.contributions, runner.contributions, strict=True)
        ),
        key=lambda item: item[1],
    )
    pros = ", ".join(f"{_label(n)} {d:+.2f}" for n, d in reversed(deltas[-2:]) if d >= 0.005)
    con_name, con = deltas[0]
    against = f"; {runner.motion} leads on {_label(con_name)} {-con:+.2f}" if con <= -0.005 else ""
    blocked = "".join(
        f"; {s.motion} blocked ({', '.join(s.blocked_by)})" for s in scores if s.blocked_by
    )
    owned = "" if top.motion == "build" else f". We still own {_owned()}"
    return f"{top.motion} over {runner.motion}: {pros or 'overall fit'}{against}{blocked}{owned}."


def assess(capability: Capability) -> MotionFit:
    scores = motion_scores(capability)
    return MotionFit(capability=capability, scores=scores, rationale=_rationale(scores))


def to_markdown(fits: Sequence[MotionFit]) -> str:
    header = (
        "| Capability | Build | Co-build | Buy | Recommended | Why |\n|---|---|---|---|---|---|"
    )

    def cell(f: MotionFit, motion: str) -> str:
        s = f.score(motion)
        text = f"{s.fit:.2f}" if s.allowed else f"~~{s.fit:.2f}~~"
        return f"**{text}**" if s is f.recommended else text

    rows = (
        f"| {f.capability.name} | {' | '.join(cell(f, m) for m in MOTIONS)} | "
        f"**{f.recommended.motion}** | {f.rationale} |"
        for f in fits
    )
    return "\n".join((header, *rows))


def load_capabilities(path: str | Path) -> tuple[Capability, ...]:
    raw = json.loads(Path(path).read_text())
    return tuple(Capability(**c) for c in raw["capabilities"])
