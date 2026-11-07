"""Deterministic synthetic usage events for the token-economics demo.

One fictional org, four teams, one quarter. On ``ROUTING_CHANGE`` the platform ships eval-driven
routing (``routing.route`` picks the model per task class) and a shared prompt cache. Before,
every agent session and AI review runs on the frontier model. One team, mobile, goes further
and forces the small model for everything to cut cost; its revert rate rises, which is what
the team guard is there to catch. A handful of agent sessions loop away. The numbers are
made up; the shape is the one real telemetry would show.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import count

from devex import routing
from devex.usage import UsageEvent, identity

__all__ = ["AS_OF", "BUDGET_USD", "ROUTING_CHANGE", "START", "TEAMS", "generate"]

ORG = "acme"
START = date(2026, 4, 1)
AS_OF = date(2026, 6, 10)
ROUTING_CHANGE = date(2026, 5, 4)
BUDGET_USD = 4_500.0  # the illustrative AI budget for the quarter
TEAMS = (("payments", 10), ("platform", 10), ("data", 8), ("mobile", 3))
REPOS = {"payments": "acme/payments", "platform": "acme/platform", "data": "acme/lakehouse"}
TIER_MODEL = {
    "frontier-large": ("anthropic", "claude-opus-4"),
    "mid-tier": ("anthropic", "claude-sonnet-4"),
    "small-fast": ("anthropic", "claude-3-5-haiku"),
}
COMPLETION = ("openai", "gpt-4.1-mini")
CLASS_MIX = (("trivial", 0.30), ("standard", 0.50), ("complex", 0.20))
CONTEXT = {"trivial": (20_000, 800), "standard": (70_000, 2_500), "complex": (160_000, 6_000)}
REVERT_EVERY = 33  # about 3% of merged changes are reverted
REVERT_EVERY_CHEAP = 6  # forcing the small model on non-trivial work: about 17%
SEED = 20260401


@dataclass(frozen=True, slots=True)
class _Ctx:
    rng: random.Random
    merged: Iterator[int]
    team: str
    engineer: str
    after: bool

    @property
    def cheap_only(self) -> bool:
        return self.after and self.team == "mobile"


def _pick_class(rng: random.Random) -> str:
    roll = rng.random()
    for name, share in CLASS_MIX:
        if roll < share:
            return name
        roll -= share
    return CLASS_MIX[-1][0]


def _model(ctx: _Ctx, task_class: str, surface: str, input_tokens: int) -> tuple[str, str]:
    if ctx.cheap_only:
        return TIER_MODEL["small-fast"]
    if not ctx.after:
        frontier = surface in ("coding_agent", "code_review", "sre")
        return TIER_MODEL["frontier-large" if frontier else "mid-tier"]
    request = routing.Request("", input_tokens, 0)
    chosen = routing.route(routing.TaskClass(task_class), routing.DEFAULT_CATALOG, 0.9, request)
    return TIER_MODEL[chosen.name]


def _call(
    ctx: _Ctx, at: datetime, surface: str, task_class: str, scale: float = 1.0, **fields
) -> UsageEvent:
    base_in, base_out = CONTEXT[task_class]
    jitter = 0.6 + 0.8 * ctx.rng.random()
    input_tokens = int(base_in * scale * jitter)
    output_tokens = int(base_out * scale * (0.6 + 0.8 * ctx.rng.random()))
    cached_share = (0.55 if ctx.after else 0.25) * (0.7 + 0.6 * ctx.rng.random())
    provider, model = _model(ctx, task_class, surface, input_tokens)
    return UsageEvent(
        timestamp=at,
        org=ORG,
        team=ctx.team,
        engineer=identity(ctx.engineer),
        surface=surface,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=int(input_tokens * min(cached_share, 0.9)),
        task_class=task_class,
        **fields,
    )


def _change(ctx: _Ctx, day: date, number: int, runaway: bool) -> Iterator[UsageEvent]:
    rng = ctx.rng
    repo = REPOS.get(ctx.team, "acme/mobile-app")
    task_class = _pick_class(rng)
    at = datetime(day.year, day.month, day.day, 9 + rng.randrange(8), rng.randrange(60))
    agent = runaway or rng.random() < (0.45 if task_class != "complex" else 0.3)
    task_id = f"{'agent' if agent else 'pr'}-{repo.split('/')[1]}-{number}"
    task_type = "agent_session" if agent else "pr"
    weak = ctx.cheap_only and task_class != "trivial"
    abandon_p = (0.75 if runaway else 0.07 if agent else 0.03) + (0.05 if weak else 0.0)
    outcome = "abandoned" if rng.random() < abandon_p else "merged"
    every = REVERT_EVERY_CHEAP if weak else REVERT_EVERY
    reverted = outcome == "merged" and next(ctx.merged) % every == every - 1
    link = {"repo": repo, "pr": number}

    steps = (
        rng.randrange(40, 80) if runaway else rng.randrange(3, 9) if agent else rng.randrange(1, 3)
    )
    surface = "coding_agent" if agent else "ide_chat"
    for step in range(steps):
        yield _call(
            ctx,
            at + timedelta(minutes=3 * step),
            surface,
            task_class,
            1.0 + 0.05 * step if agent else 1.0,
            task_id=task_id,
            task_type=task_type,
            **link,
        )
    if outcome == "abandoned":
        last = at + timedelta(minutes=3 * steps)
        yield _call(ctx, last, surface, "trivial", 0.3, task_id=task_id, task_type=task_type,
                    outcome="abandoned", **link)  # fmt: skip
        return
    review_at = at + timedelta(hours=2 + rng.randrange(20))
    for review in range(1 + (task_class == "complex")):
        yield _call(
            ctx,
            review_at + timedelta(minutes=10 * review),
            "code_review",
            task_class,
            1.2,
            task_id=f"review-{repo.split('/')[1]}-{number}",
            task_type="review",
            **link,
        )
    merged_at = review_at + timedelta(hours=1 + rng.randrange(6))
    yield _call(ctx, merged_at, "ci", "trivial", 1.5, task_id=task_id, task_type=task_type,
                outcome="merged", **link)  # fmt: skip
    if reverted:
        yield _call(ctx, merged_at + timedelta(days=1 + rng.randrange(4)), "ci", "trivial", 1.0,
                    task_id=task_id, task_type=task_type, outcome="reverted", **link)  # fmt: skip


def _incident(ctx: _Ctx, day: date, number: int) -> Iterator[UsageEvent]:
    rng = ctx.rng
    at = datetime(day.year, day.month, day.day, rng.randrange(24), rng.randrange(60))
    steps = rng.randrange(4, 12)
    task_id = f"inc-{number}"
    for step in range(steps):
        final = step == steps - 1
        resolved = final and rng.random() < 0.92
        yield _call(
            ctx,
            at + timedelta(minutes=7 * step),
            "sre",
            "complex",
            0.8,
            task_id=task_id,
            task_type="incident",
            **({"outcome": "resolved"} if resolved else {}),
        )


def _weekly(ctx: _Ctx, monday: date) -> Iterator[UsageEvent]:
    """Completions and free-form chat arrive pre-aggregated per engineer and week, without a
    task, as IDE usage exports usually do."""
    rng = ctx.rng
    at = datetime(monday.year, monday.month, monday.day, 18)
    provider, model = COMPLETION
    completion_in = rng.randrange(150_000, 450_000)
    yield UsageEvent(
        timestamp=at,
        org=ORG,
        team=ctx.team,
        engineer=identity(ctx.engineer),
        surface="completion",
        provider=provider,
        model=model,
        input_tokens=completion_in,
        output_tokens=completion_in // 8,
        cached_input_tokens=int(completion_in * (0.5 if ctx.after else 0.3)),
    )
    yield _call(ctx, at, "ide_chat", "standard", 6.0, task_type="chat")


def _days(start: date, end: date) -> Iterator[date]:
    return (start + timedelta(days=n) for n in range((end - start).days + 1))


def generate(seed: int = SEED) -> tuple[UsageEvent, ...]:
    rng = random.Random(seed)
    people = tuple((team, f"{team}-{n:02d}") for team, size in TEAMS for n in range(1, size + 1))
    runaway_days = {date(2026, 4, 9), date(2026, 4, 21), date(2026, 4, 28), date(2026, 5, 19)}
    merged = {team: count(REVERT_EVERY // 2) for team, _ in TEAMS}
    events: list[UsageEvent] = []
    number = 1000
    incidents = 0
    for day in _days(START, AS_OF):
        after = day >= ROUTING_CHANGE
        if day.weekday() == 0:
            for team, engineer in people:
                events.extend(_weekly(_Ctx(rng, merged[team], team, engineer, after), day))
        if day.weekday() >= 5:
            continue
        loop_owner = rng.randrange(len(people)) if day in runaway_days else -1
        for index, (team, engineer) in enumerate(people):
            ctx = _Ctx(rng, merged[team], team, engineer, after)
            for _ in range(int(rng.random() < 0.35) + int(index == loop_owner)):
                number += 1
                events.extend(_change(ctx, day, number, runaway=index == loop_owner))
                loop_owner = -1 if index == loop_owner else loop_owner
        if rng.random() < 0.3:
            incidents += 1
            team, engineer = people[rng.randrange(10, 20)]  # platform runs on-call
            events.extend(_incident(_Ctx(rng, merged[team], team, engineer, after), day, incidents))
    # Work still open on AS_OF has no outcome yet: it is in flight, as in a live export.
    current = (e for e in events if e.timestamp.date() <= AS_OF)
    return tuple(sorted(current, key=lambda e: (e.timestamp, e.task_id, e.surface)))
