"""Deterministic synthetic PR history so the metrics can be demonstrated without real data."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from devex.metrics import PullRequest, Window

__all__ = ["Profile", "generate"]


@dataclass(frozen=True, slots=True)
class Profile:
    prs_per_engineer_week: float
    ai_share: float
    failure_rate_ai: float
    failure_rate_human: float
    review_latency_hours: float
    abandoned_ratio: float
    ai_cost_usd: float


def _pr(rng: random.Random, index: int, window: Window, engineers: int, p: Profile, label: str):
    span = (window.end - window.start).total_seconds()
    opened = window.start + timedelta(seconds=rng.uniform(0, span * 0.97))
    ai = rng.random() < p.ai_share
    abandoned = rng.random() < p.abandoned_ratio
    review = opened + timedelta(hours=rng.expovariate(1 / p.review_latency_hours))
    merged = None if abandoned else review + timedelta(hours=rng.expovariate(1 / 6))
    failed = rng.random() < (p.failure_rate_ai if ai else p.failure_rate_human)
    tokens = int(rng.lognormvariate(14, 0.7)) if ai else int(rng.lognormvariate(8, 0.5))
    return PullRequest(
        id=f"{label}-{index:05d}",
        author=f"eng-{rng.randrange(engineers):03d}",
        opened_at=opened,
        merged_at=merged if merged and merged < window.end else None,
        first_review_at=review,
        lines_changed=max(5, int(rng.lognormvariate(4.3, 0.8))),
        ai_assisted=ai,
        reverted=failed and rng.random() < 0.6,
        caused_incident=failed,
        tokens=tokens,
        token_cost_usd=round(tokens / 1_000_000 * p.ai_cost_usd, 4),
    )


def generate(
    *, seed: int, engineers: int, window: Window, profile: Profile, label: str
) -> tuple[PullRequest, ...]:
    rng = random.Random(seed)
    expected_merged = profile.prs_per_engineer_week * engineers * window.weeks
    opened = round(expected_merged / (1 - profile.abandoned_ratio))
    return tuple(_pr(rng, i, window, engineers, profile, label) for i in range(opened))


BASELINE = Profile(
    prs_per_engineer_week=2.0,
    ai_share=0.15,
    failure_rate_ai=0.12,
    failure_rate_human=0.09,
    review_latency_hours=20,
    abandoned_ratio=0.08,
    ai_cost_usd=12.0,
)

WITH_PLATFORM = Profile(
    prs_per_engineer_week=2.6,
    ai_share=0.55,
    failure_rate_ai=0.08,
    failure_rate_human=0.085,
    review_latency_hours=9,
    abandoned_ratio=0.14,
    ai_cost_usd=7.0,
)

QUARTER_1 = Window(datetime(2026, 1, 5), datetime(2026, 4, 6))
QUARTER_2 = Window(datetime(2026, 4, 6), datetime(2026, 7, 6))
