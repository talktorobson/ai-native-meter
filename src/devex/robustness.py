"""Robustness of a scored decision: how often does the leader win, and what would flip it?

Instead of a magic tie margin, two questions are answered explicitly:

- **Monte Carlo.** Weights are drawn from a Dirichlet centred on the agreed weights and
  every effective score gets Gaussian noise sized by its evidence level. P(win) is the
  share of draws a candidate tops. Samplers use only ``Random.random()``, so a seed gives
  the same numbers on every supported Python version.
- **Sensitivity.** Raising one weight to ``t`` (others rescaled proportionally) makes
  each total linear in ``t``, so the exact flip point against each challenger has a
  closed form: ``t* = b / (b - a)``.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from devex.vendor_scorecard import Assessment, normalize_weights, total

__all__ = [
    "Flip",
    "Odds",
    "dirichlet",
    "flip_point",
    "gamma",
    "normal",
    "sensitivity",
    "simulate",
]


def normal(rng: random.Random) -> float:
    """Standard normal by Box-Muller."""
    return math.sqrt(-2 * math.log(1 - rng.random())) * math.cos(2 * math.pi * rng.random())


def gamma(rng: random.Random, alpha: float) -> float:
    """Gamma(alpha, 1) by Marsaglia-Tsang (boosted for alpha < 1)."""
    if alpha < 1:
        return gamma(rng, alpha + 1) * (1 - rng.random()) ** (1 / alpha)
    d = alpha - 1 / 3
    c = 1 / math.sqrt(9 * d)
    while True:
        x = normal(rng)
        v = (1 + c * x) ** 3
        if v > 0 and math.log(1 - rng.random()) < 0.5 * x * x + d - d * v + d * math.log(v):
            return d * v


def dirichlet(
    rng: random.Random, weights: Mapping[str, float], concentration: float
) -> dict[str, float]:
    """Weights drawn around ``weights``; higher concentration means tighter agreement."""
    draws = {k: gamma(rng, concentration * w) if w > 0 else 0.0 for k, w in weights.items()}
    norm = sum(draws.values())
    return {k: v / norm for k, v in draws.items()}


@dataclass(frozen=True, slots=True)
class Odds:
    name: str
    p_win: float
    p10: float
    p50: float
    p90: float


def _clip(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _draw(
    rng: random.Random,
    pool: Sequence[Assessment],
    weights: Mapping[str, float],
    concentration: float,
) -> tuple[float, ...]:
    w = dirichlet(rng, weights, concentration)
    return tuple(
        sum(w.get(c.name, 0.0) * _clip(c.effective + c.sd * normal(rng)) for c in a.criteria)
        for a in pool
    )


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[round(q * (len(ordered) - 1))]


def simulate(
    assessments: Sequence[Assessment],
    weights: Mapping[str, float],
    *,
    samples: int = 2000,
    seed: int = 7,
    concentration: float = 50.0,
) -> tuple[Odds, ...]:
    """P(win) and score quantiles for qualified candidates, most likely winner first."""
    pool = tuple(a for a in assessments if a.qualified)
    if not pool:
        return ()
    rng = random.Random(seed)
    base = normalize_weights(weights)
    draws = tuple(_draw(rng, pool, base, concentration) for _ in range(samples))
    wins = Counter(max(range(len(pool)), key=d.__getitem__) for d in draws)
    odds = (
        Odds(
            name=a.name,
            p_win=wins[i] / samples,
            p10=_quantile([d[i] for d in draws], 0.1),
            p50=_quantile([d[i] for d in draws], 0.5),
            p90=_quantile([d[i] for d in draws], 0.9),
        )
        for i, a in enumerate(pool)
    )
    return tuple(sorted(odds, key=lambda o: (-o.p_win, o.name)))


@dataclass(frozen=True, slots=True)
class Flip:
    criterion: str
    weight: float
    threshold: float
    challenger: str

    @property
    def direction(self) -> str:
        return "rises above" if self.threshold > self.weight else "falls below"

    @property
    def distance(self) -> float:
        return abs(self.threshold - self.weight)

    @property
    def headroom(self) -> float:
        """Relative change of the weight needed to flip the winner."""
        return self.distance / self.weight if self.weight else math.inf

    def describe(self, label: str = "") -> str:
        return (
            f"the {label or self.criterion.replace('_', ' ')} weight {self.direction} "
            f"{self.threshold:.2f} (now {self.weight:.2f})"
        )


def flip_point(
    leader: Assessment, challenger: Assessment, weights: Mapping[str, float], criterion: str
) -> float | None:
    """Weight of ``criterion`` at which ``challenger`` ties ``leader``, if within [0, 1]."""
    w = normalize_weights(weights)
    rest = 1 - w[criterion]
    lead, chal = leader.effective, challenger.effective
    a = lead[criterion] - chal[criterion]
    b = sum(w[k] * (lead[k] - chal[k]) for k in w if k != criterion) / rest if rest > 0 else a
    if math.isclose(a, b):
        return None
    t = b / (b - a)
    return t if 0 < t < 1 and not math.isclose(t, 1) else None


def sensitivity(
    assessments: Sequence[Assessment], weights: Mapping[str, float]
) -> tuple[Flip, ...]:
    """Nearest flip in each direction for every weight, smallest relative headroom first."""
    pool = sorted(
        (a for a in assessments if a.qualified), key=lambda a: (-total(a, weights), a.name)
    )
    if len(pool) < 2:
        return ()
    leader, challengers = pool[0], pool[1:]
    w = normalize_weights(weights)
    crossings = tuple(
        Flip(k, w[k], t, c.name)
        for k in w
        for c in challengers
        if (t := flip_point(leader, c, w, k)) is not None
    )
    nearest = {
        (f.criterion, f.threshold > f.weight): f
        for f in sorted(crossings, key=lambda f: -f.distance)
    }
    return tuple(sorted(nearest.values(), key=lambda f: (f.headroom, f.criterion)))
