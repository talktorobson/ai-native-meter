"""Objective evaluation of code-review agents against a seeded-defect benchmark.

A reviewer is any callable ``ReviewCase -> Sequence[Finding]``: an LLM-backed agent, a
vendor API wrapper or the rule-based baseline below. Every candidate is scored on the
same cases with the same matcher, so build-vs-partner decisions rest on numbers, not demos.

Precision is weighted above recall (F0.5 by default): a noisy reviewer trains engineers
to ignore it, which costs more than the defect it occasionally catches.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from pathlib import Path

__all__ = [
    "SEVERITY_WEIGHT",
    "Defect",
    "EvalReport",
    "Finding",
    "ReviewCase",
    "Reviewer",
    "compare",
    "evaluate",
    "keyword_reviewer",
    "load_cases",
    "match",
    "rule_reviewer",
    "to_markdown",
]

SEVERITY_WEIGHT: Mapping[str, int] = {"critical": 5, "high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True, slots=True)
class Defect:
    line: int
    category: str
    severity: str


@dataclass(frozen=True, slots=True)
class Finding:
    line: int
    category: str
    severity: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class ReviewCase:
    id: str
    title: str
    code: str
    defects: tuple[Defect, ...]

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.code.splitlines())


Reviewer = Callable[[ReviewCase], Sequence[Finding]]


@dataclass(frozen=True, slots=True)
class EvalReport:
    reviewer: str
    cases: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    severity_weighted_recall: float
    f_beta: float
    noise_per_pr: float
    clean_case_silence_rate: float
    cost_usd: float
    cost_per_true_positive_usd: float


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f_beta(precision: float, recall: float, beta: float) -> float:
    b2 = beta * beta
    return _safe_div((1 + b2) * precision * recall, b2 * precision + recall)


def match(
    defects: Sequence[Defect], findings: Sequence[Finding], tolerance: int = 1
) -> tuple[tuple[Defect, ...], tuple[Finding, ...]]:
    """Greedy one-to-one matching. Returns (caught defects, unmatched findings).

    A finding catches a defect when the category agrees and the line is within tolerance.
    Duplicate findings on an already-caught defect are noise.
    """

    def step(
        state: tuple[tuple[Defect, ...], tuple[Finding, ...]], finding: Finding
    ) -> tuple[tuple[Defect, ...], tuple[Finding, ...]]:
        caught, noise = state
        hit = next(
            (
                d
                for d in defects
                if d not in caught
                and d.category == finding.category
                and abs(d.line - finding.line) <= tolerance
            ),
            None,
        )
        return ((*caught, hit), noise) if hit else (caught, (*noise, finding))

    ordered = sorted(findings, key=lambda f: (f.line, f.category))
    return reduce(step, ordered, ((), ()))


def evaluate(
    name: str,
    reviewer: Reviewer,
    cases: Iterable[ReviewCase],
    *,
    cost_per_case_usd: float = 0.0,
    beta: float = 0.5,
    tolerance: int = 1,
) -> EvalReport:
    items = tuple(cases)
    outcomes = tuple((case, tuple(reviewer(case))) for case in items)
    matched = tuple((case, *match(case.defects, found, tolerance)) for case, found in outcomes)

    tp = sum(len(caught) for _, caught, _ in matched)
    fp = sum(len(noise) for _, _, noise in matched)
    total_defects = sum(len(case.defects) for case in items)
    weight_total = sum(SEVERITY_WEIGHT[d.severity] for case in items for d in case.defects)
    weight_caught = sum(SEVERITY_WEIGHT[d.severity] for _, caught, _ in matched for d in caught)
    clean = tuple(found for case, found in outcomes if not case.defects)

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, total_defects)
    cost = cost_per_case_usd * len(items)
    return EvalReport(
        reviewer=name,
        cases=len(items),
        true_positives=tp,
        false_positives=fp,
        false_negatives=total_defects - tp,
        precision=precision,
        recall=recall,
        severity_weighted_recall=_safe_div(weight_caught, weight_total),
        f_beta=_f_beta(precision, recall, beta),
        noise_per_pr=_safe_div(fp, len(items)),
        clean_case_silence_rate=_safe_div(sum(not found for found in clean), len(clean)),
        cost_usd=cost,
        cost_per_true_positive_usd=_safe_div(cost, tp),
    )


def compare(
    reviewers: Mapping[str, tuple[Reviewer, float]], cases: Iterable[ReviewCase]
) -> tuple[EvalReport, ...]:
    """Score every reviewer on the same cases, best F-beta first."""
    items = tuple(cases)
    reports = (
        evaluate(name, reviewer, items, cost_per_case_usd=cost)
        for name, (reviewer, cost) in reviewers.items()
    )
    return tuple(sorted(reports, key=lambda r: (-r.f_beta, r.cost_usd)))


def to_markdown(reports: Sequence[EvalReport]) -> str:
    header = (
        "| Reviewer | Precision | Recall | Sev-weighted recall | F0.5 | Noise/PR "
        "| Silent on clean | Cost/TP |\n|---|---|---|---|---|---|---|---|"
    )
    rows = (
        f"| {r.reviewer} | {r.precision:.0%} | {r.recall:.0%} | "
        f"{r.severity_weighted_recall:.0%} | {r.f_beta:.2f} | {r.noise_per_pr:.2f} | "
        f"{r.clean_case_silence_rate:.0%} | ${r.cost_per_true_positive_usd:.3f} |"
        for r in reports
    )
    return "\n".join((header, *rows))


def load_cases(path: str | Path) -> tuple[ReviewCase, ...]:
    raw = json.loads(Path(path).read_text())
    return tuple(
        ReviewCase(
            id=item["id"],
            title=item["title"],
            code="\n".join(item["code"]),
            defects=tuple(Defect(**d) for d in item["defects"]),
        )
        for item in raw
    )


Rule = tuple[str, str, re.Pattern[str], str]

RULES: tuple[Rule, ...] = (
    (
        "secret",
        "critical",
        re.compile(r"""(api_?key|secret|password|token)\s*=\s*["'][^"']{8,}["']""", re.I),
        "Hard-coded credential; load it from the secret manager.",
    ),
    (
        "sql-injection",
        "critical",
        re.compile(r"""execute\(\s*(f["']|["'][^"']*["']\s*(\+|%))"""),
        "Query built from strings; use bound parameters.",
    ),
    (
        "float-money",
        "high",
        re.compile(r"float\([^)]*(amount|balance|price|total|fee)", re.I),
        "Monetary value as float; use Decimal or integer minor units.",
    ),
    (
        "missing-timeout",
        "medium",
        re.compile(r"requests\.(get|post|put|patch|delete)\((?![^)]*timeout=)"),
        "Outbound call without timeout can exhaust the worker pool.",
    ),
    (
        "broad-except",
        "medium",
        re.compile(r"except\s*(Exception)?\s*:"),
        "Broad except hides failures; catch the specific error.",
    ),
    (
        "pii-logging",
        "high",
        re.compile(r"log(ger)?\.\w+\(.*\b(cpf|ssn|card_number|document_id|email)\b", re.I),
        "Personal data written to logs.",
    ),
)


def _scan(rules: Sequence[Rule], case: ReviewCase) -> tuple[Finding, ...]:
    return tuple(
        Finding(line=number, category=category, severity=severity, message=message)
        for number, text in enumerate(case.lines, start=1)
        for category, severity, pattern, message in rules
        if pattern.search(text)
    )


def rule_reviewer(case: ReviewCase) -> tuple[Finding, ...]:
    """Deterministic baseline every AI reviewer must beat."""
    return _scan(RULES, case)


_KEYWORDS: Mapping[str, str] = {
    "password": "secret",
    "execute": "sql-injection",
    "float": "float-money",
    "requests.": "missing-timeout",
    "except": "broad-except",
    "log": "pii-logging",
}


def keyword_reviewer(case: ReviewCase) -> tuple[Finding, ...]:
    """A deliberately noisy reviewer: high recall, poor precision. Shows why F0.5 matters."""
    return tuple(
        Finding(line=number, category=category, severity="medium", message=f"Check '{word}'")
        for number, text in enumerate(case.lines, start=1)
        for word, category in _KEYWORDS.items()
        if word in text
    )
