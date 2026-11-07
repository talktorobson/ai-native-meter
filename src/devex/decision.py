"""Turn a scored, stress-tested decision into a verdict a reader can audit.

``analyse`` runs the whole Stage 2 pipeline; ``to_markdown`` and ``memo`` explain it:
why the winner wins, what it trades away, what would change the decision and when to
revisit it. Every sentence is generated from the numbers, so it cannot drift from them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from devex import robustness
from devex.motion_fit import MotionFit
from devex.robustness import Flip, Odds
from devex.vendor_scorecard import (
    CRITERIA,
    Assessment,
    DecisionSpec,
    Policy,
    assess,
    contributions,
    normalize_weights,
    total,
)

__all__ = [
    "LABELS",
    "Analysis",
    "Verdict",
    "analyse",
    "memo",
    "rationale",
    "to_markdown",
    "triggers",
    "verdict",
]

LABELS: Mapping[str, str] = {
    "review_quality": "review quality",
    "tco": "3-year TCO",
    "time_to_value": "time to value",
    "operability": "operability",
    "lock_in": "lock-in",
}


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: str  # go | bake-off | no-go
    text: str
    contenders: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Analysis:
    spec: DecisionSpec
    weights: Mapping[str, float]
    ranking: tuple[Assessment, ...]
    scores: Mapping[str, float]
    odds: tuple[Odds, ...]
    flips: tuple[Flip, ...]
    verdict: Verdict
    rationale: tuple[str, ...]
    triggers: tuple[str, ...]
    fit: MotionFit | None = None

    @property
    def qualified(self) -> tuple[Assessment, ...]:
        return tuple(a for a in self.ranking if a.qualified)

    def odds_for(self, name: str) -> Odds | None:
        return next((o for o in self.odds if o.name == name), None)


def _label(criterion: str) -> str:
    return LABELS.get(criterion, criterion.replace("_", " "))


def _usd(value: float) -> str:
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value / 1e3:.0f}k" if value else "$0"


def _describe(flip: Flip) -> str:
    return flip.describe(_label(flip.criterion))


def _robust_clause(flips: Sequence[Flip]) -> str:
    if not flips:
        return "; no single weight change flips it"
    return f"; robust unless {_describe(flips[0])}"


def verdict(
    ranking: Sequence[Assessment],
    scores: Mapping[str, float],
    odds: Sequence[Odds],
    flips: Sequence[Flip],
    policy: Policy,
) -> Verdict:
    qualified = tuple(a for a in ranking if a.qualified)
    if not qualified:
        return Verdict(
            "no-go",
            "No-go: no candidate passes the hard gates. Re-scope the need or fund a minimal "
            "in-house path.",
            (),
        )
    leader = qualified[0]
    if scores[leader.name] < policy.min_score:
        return Verdict(
            "no-go",
            f"No-go: the best qualified option ({leader.name}, {scores[leader.name]:.2f}) is "
            f"below the {policy.min_score:.2f} floor. Re-scope before buying anything.",
            (),
        )
    top = odds[0]
    if top.name == leader.name and top.p_win >= policy.clear_win_probability:
        return Verdict(
            "go",
            f"Go with {leader.name} ({leader.motion}): it wins {top.p_win:.0%} of "
            f"{policy.samples:,} simulations{_robust_clause(flips)}.",
            (leader.name,),
        )
    contenders = tuple(
        o for i, o in enumerate(odds) if i < 2 or o.p_win >= policy.contender_probability
    )
    names = " vs ".join(f"{o.name} ({o.p_win:.0%})" for o in contenders)
    return Verdict(
        "bake-off",
        f"Bake-off: {names}. No option wins {policy.clear_win_probability:.0%} of "
        "simulations, so run a time-boxed bake-off on the shared eval set.",
        tuple(o.name for o in contenders),
    )


def _deltas(
    leader: Assessment, runner: Assessment, weights: Mapping[str, float]
) -> tuple[tuple[str, float], ...]:
    pairs = zip(contributions(leader, weights), contributions(runner, weights), strict=True)
    return tuple(sorted(((k, a - b) for (k, a), (_, b) in pairs), key=lambda kv: -kv[1]))


def _proven(assessment: Assessment, weights: Mapping[str, float]) -> float:
    """Score if every claim were confirmed at full credibility."""
    w = normalize_weights(weights)
    return sum(w.get(c.name, 0.0) * c.normalized for c in assessment.criteria)


def rationale(
    ranking: Sequence[Assessment], weights: Mapping[str, float], flips: Sequence[Flip]
) -> tuple[str, ...]:
    qualified = tuple(a for a in ranking if a.qualified)
    excluded = tuple(
        f"{a.name} is excluded by the {', '.join(a.failed_gates)} "
        f"gate{'s' if len(a.failed_gates) > 1 else ''}, whatever it scores."
        for a in ranking
        if not a.qualified
    )
    if len(qualified) < 2:
        return excluded
    leader, runner = qualified[0], qualified[1]
    deltas = _deltas(leader, runner, weights)
    wins = " and ".join(f"{_label(k)} ({d:+.3f})" for k, d in deltas[:2] if d > 0)
    loss = tuple((k, d) for k, d in deltas if d < 0)
    trade = (
        f"Trade-off: it gives up {_label(loss[-1][0])} ({loss[-1][1]:+.3f}) to {runner.name}."
        if loss
        else f"Trade-off: none; it matches or beats {runner.name} on every criterion."
    )
    w = normalize_weights(weights)
    biggest = max(
        ((a, c) for a in qualified for c in a.criteria),
        key=lambda ac: w.get(ac[1].name, 0.0) * abs(ac[1].normalized - ac[1].effective),
    )
    a, c = biggest
    evidence = (
        f"Evidence: {a.name}'s {_label(c.name)} is {c.evidence} evidence, so it counts as "
        f"{c.effective:.2f} instead of {c.normalized:.2f}."
    )
    proven = _proven(runner, weights)
    change = (
        f"What would change it: {_describe(flips[0])}, {flips[0].challenger} would win"
        if flips
        else "What would change it: no single weight change"
    )
    proof = (
        f"; or {runner.name} proving its claims on our benchmark ({proven:.2f} vs "
        f"{total(leader, weights):.2f})."
        if proven > total(leader, weights)
        else "."
    )
    return (
        f"Why {leader.name} wins: it leads {runner.name} on {wins or 'overall score'} "
        "in weighted contribution.",
        trade,
        evidence,
        change + proof,
        *excluded,
    )


def triggers(
    ranking: Sequence[Assessment], flips: Sequence[Flip], engineers: int
) -> tuple[str, ...]:
    qualified = tuple(a for a in ranking if a.qualified)
    if not qualified:
        gates = sorted({g for a in ranking for g in a.failed_gates})
        return (f"Re-open when any candidate clears {', '.join(gates)}.",)
    leader = qualified[0]
    weights = tuple(
        f"Weights: re-decide if {_describe(f)}; {f.challenger} would win." for f in flips[:2]
    )
    soft = tuple(c for c in leader.criteria if c.evidence in ("demo", "pilot"))
    evidence = (
        (
            f"Evidence: re-score when {leader.name}'s "
            + ", ".join(f"{_label(c.name)} ({c.evidence})" for c in soft)
            + (" are" if len(soft) > 1 else " is")
            + " measured on the shared benchmark.",
        )
        if soft
        else ()
    )
    lock = (
        f"Lock-in: exit cost {_usd(leader.exit_cost_usd)} today (missing "
        f"{', '.join(leader.missing_terms)}); re-open if a renewal drops another portability term."
        if leader.missing_terms
        else "Lock-in: fully portable today; re-open if a renewal drops any portability term."
    )
    runner = qualified[1].name if len(qualified) > 1 else "one alternative"
    return (
        *weights,
        *evidence,
        lock,
        f"Scale: re-run the TCO if projected adoption moves 25% from {engineers:,} engineers.",
        f"Cadence: run the shared eval quarterly against {leader.name} and {runner}.",
    )


def analyse(
    spec: DecisionSpec,
    *,
    fit: MotionFit | None = None,
    weights: Mapping[str, float] | None = None,
) -> Analysis:
    w = normalize_weights(weights or spec.weights)
    assessed = tuple(assess(c, spec.scale, spec.anchors) for c in spec.candidates)
    scores = {a.name: total(a, w) for a in assessed}
    ranking = tuple(sorted(assessed, key=lambda a: (not a.qualified, -scores[a.name], a.name)))
    p = spec.policy
    odds = robustness.simulate(
        ranking, w, samples=p.samples, seed=p.seed, concentration=p.weight_concentration
    )
    flips = robustness.sensitivity(ranking, w)
    v = verdict(ranking, scores, odds, flips, p)
    return Analysis(
        spec=spec,
        weights=w,
        ranking=ranking,
        scores=scores,
        odds=odds,
        flips=flips,
        verdict=v,
        rationale=rationale(ranking, w, flips),
        triggers=triggers(ranking, flips, spec.scale.engineers),
        fit=fit,
    )


def _p_win(analysis: Analysis, a: Assessment) -> str:
    o = analysis.odds_for(a.name)
    return f"{o.p_win:.0%}" if o else "n/a"


def _gates(a: Assessment) -> str:
    return "pass" if a.qualified else f"fail: {', '.join(a.failed_gates)}"


def _evidence_table(analysis: Analysis) -> str:
    head = " | ".join(_label(k) for k in CRITERIA)
    rows = (
        f"| {a.name} | "
        + " | ".join(
            f"{c.raw} · {c.evidence} → {c.effective:.2f}"
            for c in sorted(a.criteria, key=lambda c: CRITERIA.index(c.name))
        )
        + " |"
        for a in analysis.ranking
    )
    return "\n".join((f"| Candidate | {head} |", "|---" * (len(CRITERIA) + 1) + "|", *rows))


def _contribution_table(analysis: Analysis) -> str:
    head = " | ".join(f"{_label(k)} ({analysis.weights.get(k, 0):.2f})" for k in CRITERIA)
    rows = (
        f"| {a.name} | {a.motion} | "
        + " | ".join(f"{v:.3f}" for _, v in contributions(a, analysis.weights))
        + f" | **{analysis.scores[a.name]:.3f}** | {_p_win(analysis, a)} | {_gates(a)} |"
        for a in analysis.ranking
    )
    return "\n".join(
        (
            f"| Candidate | Motion | {head} | Score | P(win) | Gates |",
            "|---" * (len(CRITERIA) + 5) + "|",
            *rows,
        )
    )


def _sensitivity_table(analysis: Analysis) -> str:
    if not analysis.flips:
        return "No single weight change in [0, 1] flips the winner."
    rows = (
        f"| {_label(f.criterion)} | {f.weight:.2f} | {f.direction} {f.threshold:.2f} | "
        f"{(f.threshold - f.weight) / f.weight:+.0%} | {f.challenger} |"
        for f in analysis.flips
    )
    return "\n".join(
        (
            "| Weight | Now | Flips if it | Change needed | Winner then |",
            "|---|---|---|---|---|",
            *rows,
        )
    )


def _fit_line(analysis: Analysis) -> str:
    fit = analysis.fit
    if fit is None:
        return ""
    others = ", ".join(f"{s.motion} {s.fit:.2f}" for s in fit.scores[1:])
    winner = next((a for a in analysis.ranking if a.name in analysis.verdict.contenders), None)
    agree = (
        ""
        if winner is None or analysis.verdict.kind != "go"
        else (
            " Stage 2 agrees with it."
            if winner.motion == fit.recommended.motion
            else f" Stage 2 picks a {winner.motion} option instead: the memo must justify it."
        )
    )
    return (
        f"Stage 1 motion fit for {fit.capability.name}: **{fit.recommended.motion}** "
        f"({fit.recommended.fit:.2f}; {others}).{agree}"
    )


def to_markdown(analysis: Analysis) -> str:
    p = analysis.spec.policy
    return "\n".join(
        (
            f"## Build vs partner: {analysis.spec.capability} (Stage 2, synthetic)",
            "",
            _fit_line(analysis),
            "",
            "**Evidence, normalised and discounted** (raw · evidence level → effective score)",
            "",
            _evidence_table(analysis),
            "",
            "**Weighted contributions** (weight in brackets; the row sums to the score)",
            "",
            _contribution_table(analysis),
            "",
            f"P(win) from {p.samples:,} seeded draws (seed {p.seed}) over weight and score "
            "uncertainty.",
            "",
            f"**Verdict ({analysis.verdict.kind}):** {analysis.verdict.text}",
            "",
            "**Sensitivity** (nearest flip per weight and direction)",
            "",
            _sensitivity_table(analysis),
            "",
            "**Rationale**",
            "",
            *(f"- {line}" for line in analysis.rationale),
        )
    )


def _options_table(analysis: Analysis) -> str:
    def band(a: Assessment) -> str:
        o = analysis.odds_for(a.name)
        return f"{o.p10:.2f} to {o.p90:.2f}" if o else "n/a"

    rows = (
        f"| {a.name} | {a.motion} | {_gates(a)} | {analysis.scores[a.name]:.2f} | "
        f"{_p_win(analysis, a)} | {band(a)} | {_usd(a.tco.total)} | {_usd(a.exit_cost_usd)} |"
        for a in analysis.ranking
    )
    return "\n".join(
        (
            "| Option | Motion | Hard gates | Score | P(win) | P10 to P90 | 3-yr TCO | Exit cost |",
            "|---|---|---|---|---|---|---|---|",
            *rows,
        )
    )


def memo(analysis: Analysis, date: str) -> str:
    spec, p = analysis.spec, analysis.spec.policy
    weights = ", ".join(f"{_label(k)} {v:.2f}" for k, v in analysis.weights.items())
    status = {"go": "proposed", "bake-off": "proposed, pending bake-off", "no-go": "rejected"}
    fit = analysis.fit
    return "\n".join(
        (
            f"# Decision memo · {spec.capability}",
            "",
            f"- **Status:** {status[analysis.verdict.kind]}",
            f"- **Date:** {date}",
            f"- **Verdict:** {analysis.verdict.kind}",
            "- **Data:** synthetic and illustrative; every vendor is fictional.",
            "",
            "## Context",
            "",
            *((_fit_line(analysis), "", fit.rationale, "") if fit else ()),
            f"Candidates are scored on {weights}. Cost is a {spec.scale.years}-year TCO at "
            f"{spec.scale.engineers:,} engineers and {spec.scale.prs_per_engineer_week:g} PRs "
            "per engineer per week. Scores are discounted by evidence level (demo < pilot < "
            f"benchmark), then stress-tested over {p.samples:,} seeded draws.",
            "",
            "## Options considered",
            "",
            _options_table(analysis),
            "",
            "## Decision",
            "",
            analysis.verdict.text,
            "",
            "## Rationale",
            "",
            *(f"- {line}" for line in analysis.rationale),
            "",
            "## Sensitivity",
            "",
            _sensitivity_table(analysis),
            "",
            "## Revisit triggers",
            "",
            *(f"- {line}" for line in analysis.triggers),
        )
    )
