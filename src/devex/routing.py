"""Prompt classification and eval-driven model routing.

Route each request to the cheapest model whose *measured* pass rate on that task class
clears the quality bar. Quality numbers come from the org's own evals, not vendor charts,
and the router reports the quality it expects to give up (if any) next to the savings.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "DEFAULT_CATALOG",
    "Classification",
    "Model",
    "Request",
    "RoutingReport",
    "TaskClass",
    "classify",
    "load_requests",
    "request_cost",
    "route",
    "simulate",
]


class TaskClass(StrEnum):
    TRIVIAL = "trivial"
    STANDARD = "standard"
    COMPLEX = "complex"


_COMPLEX = re.compile(
    r"\b(refactor|migrat\w*|architect\w*|design|concurren\w*|race condition|deadlock|"
    r"across (the )?(repo|services|modules)|security|threat model|distributed|flaky|root cause)\b",
    re.I,
)
_TRIVIAL = re.compile(
    r"\b(rename|typo|format|docstring|comment|lint|spelling|explain this (line|regex))\b", re.I
)


@dataclass(frozen=True, slots=True)
class Classification:
    task_class: TaskClass
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Model:
    name: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    quality: Mapping[TaskClass, float]


@dataclass(frozen=True, slots=True)
class Request:
    prompt: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class RoutingReport:
    requests: int
    mix: Mapping[TaskClass, int]
    model_usage: Mapping[str, int]
    baseline_cost_usd: float
    routed_cost_usd: float
    savings_ratio: float
    baseline_expected_quality: float
    routed_expected_quality: float


# Illustrative prices and pass rates. Replace with your own eval results.
DEFAULT_CATALOG: tuple[Model, ...] = (
    Model(
        "frontier-large",
        15.0,
        75.0,
        {TaskClass.TRIVIAL: 0.99, TaskClass.STANDARD: 0.93, TaskClass.COMPLEX: 0.81},
    ),
    Model(
        "mid-tier",
        3.0,
        15.0,
        {TaskClass.TRIVIAL: 0.98, TaskClass.STANDARD: 0.90, TaskClass.COMPLEX: 0.62},
    ),
    Model(
        "small-fast",
        0.25,
        1.25,
        {TaskClass.TRIVIAL: 0.95, TaskClass.STANDARD: 0.71, TaskClass.COMPLEX: 0.30},
    ),
)


def classify(prompt: str, input_tokens: int = 0) -> Classification:
    complex_hits = tuple(m.group(0).lower() for m in _COMPLEX.finditer(prompt))
    trivial_hits = tuple(m.group(0).lower() for m in _TRIVIAL.finditer(prompt))
    large = input_tokens > 6_000
    medium = input_tokens > 2_000
    if complex_hits or large:
        return Classification(
            TaskClass.COMPLEX, (*complex_hits, *(("large-context",) if large else ()))
        )
    if trivial_hits and not medium:
        return Classification(TaskClass.TRIVIAL, trivial_hits)
    return Classification(TaskClass.STANDARD, ("default", *(("medium-context",) if medium else ())))


def request_cost(model: Model, request: Request) -> float:
    return (
        request.input_tokens * model.input_usd_per_mtok
        + request.output_tokens * model.output_usd_per_mtok
    ) / 1_000_000


def route(
    task_class: TaskClass, catalog: Sequence[Model], min_quality: float, reference: Request
) -> Model:
    """Cheapest model that clears the bar; the best model if none does."""
    eligible = tuple(m for m in catalog if m.quality[task_class] >= min_quality)
    if eligible:
        return min(eligible, key=lambda m: request_cost(m, reference))
    return max(catalog, key=lambda m: m.quality[task_class])


def _mean(values: Iterable[float]) -> float:
    items = tuple(values)
    return sum(items) / len(items) if items else 0.0


def simulate(
    requests: Iterable[Request],
    catalog: Sequence[Model] = DEFAULT_CATALOG,
    *,
    min_quality: float = 0.9,
    baseline: str = "frontier-large",
) -> RoutingReport:
    items = tuple(requests)
    base = next(m for m in catalog if m.name == baseline)
    decisions = tuple(
        (r, c, route(c, catalog, min_quality, r))
        for r in items
        for c in (classify(r.prompt, r.input_tokens).task_class,)
    )
    baseline_cost = sum(request_cost(base, r) for r in items)
    routed_cost = sum(request_cost(m, r) for r, _, m in decisions)
    return RoutingReport(
        requests=len(items),
        mix=dict(Counter(c for _, c, _ in decisions)),
        model_usage=dict(Counter(m.name for _, _, m in decisions)),
        baseline_cost_usd=baseline_cost,
        routed_cost_usd=routed_cost,
        savings_ratio=1 - routed_cost / baseline_cost if baseline_cost else 0.0,
        baseline_expected_quality=_mean(base.quality[c] for _, c, _ in decisions),
        routed_expected_quality=_mean(m.quality[c] for _, c, m in decisions),
    )


def load_requests(path: str | Path) -> tuple[Request, ...]:
    return tuple(Request(**item) for item in json.loads(Path(path).read_text()))
