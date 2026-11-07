from dataclasses import replace
from pathlib import Path

import pytest

from devex import decision, motion_fit
from devex import vendor_scorecard as vs
from devex.robustness import Odds

DATA = Path(__file__).parents[1] / "data"


@pytest.fixture(scope="module")
def spec() -> vs.DecisionSpec:
    return vs.load_decision(DATA / "vendor_candidates.json", {"rule-baseline": 0.9})


@pytest.fixture(scope="module")
def analysis(spec) -> decision.Analysis:
    fits = map(motion_fit.assess, motion_fit.load_capabilities(DATA / "capabilities.json"))
    return decision.analyse(spec, fit=next(f for f in fits if f.capability.name == spec.capability))


def test_clear_winner_is_a_go_that_agrees_with_stage_one(analysis):
    assert analysis.verdict.kind == "go"
    assert analysis.verdict.contenders == ("Vendor B co-build",)
    assert analysis.odds[0].name == analysis.ranking[0].name
    assert analysis.odds[0].p_win >= analysis.spec.policy.clear_win_probability
    assert "robust unless the review quality weight rises above 0.54" in analysis.verdict.text
    assert analysis.fit.recommended.motion == analysis.ranking[0].motion


def test_analysis_is_deterministic(spec, analysis):
    assert decision.analyse(spec, fit=analysis.fit) == analysis


def test_gated_candidates_rank_last_and_never_get_odds(analysis):
    assert analysis.ranking[-1].name == "Vendor C SaaS"
    assert analysis.odds_for("Vendor C SaaS") is None
    assert len(analysis.qualified) == 3


def test_split_odds_call_a_bake_off(spec):
    tense = decision.analyse(spec, weights=vs.reweight(spec.weights, "review_quality", 0.5))
    assert tense.verdict.kind == "bake-off"
    assert tense.verdict.contenders[:2] == (
        "Vendor B co-build",
        "In-house reviewer on context layer",
    )
    assert "time-boxed bake-off" in tense.verdict.text


def test_pwin_leader_that_differs_from_the_point_leader_is_a_bake_off(analysis):
    flipped = tuple(reversed(analysis.odds[:2]))
    v = decision.verdict(
        analysis.ranking, analysis.scores, flipped, analysis.flips, analysis.spec.policy
    )
    assert v.kind == "bake-off"


def test_no_go_when_nothing_passes_the_gates(spec):
    closed = replace(
        spec,
        candidates=tuple(replace(c, gates={**c.gates, "soc2": False}) for c in spec.candidates),
    )
    a = decision.analyse(closed)
    assert a.verdict.kind == "no-go"
    assert a.odds == () and a.flips == ()
    assert a.triggers == ("Re-open when any candidate clears data_residency, soc2.",)
    assert a.rationale[0] == "Vendor B co-build is excluded by the soc2 gate, whatever it scores."
    assert "excluded by the data_residency, soc2 gates" in a.rationale[-1]


def test_no_go_when_the_best_option_is_below_the_floor(spec):
    a = decision.analyse(replace(spec, policy=replace(spec.policy, min_score=0.9)))
    assert a.verdict.kind == "no-go"
    assert "below the 0.90 floor" in a.verdict.text


def test_rationale_explains_win_trade_off_evidence_and_change(analysis):
    why, trade, evidence, change, excluded = analysis.rationale
    assert why.startswith("Why Vendor B co-build wins: it leads In-house reviewer")
    assert "3-year TCO (+0.051)" in why
    assert (
        trade
        == "Trade-off: it gives up review quality (-0.039) to In-house reviewer on context layer."
    )
    assert evidence.startswith("Evidence: Vendor A review bot's review quality is demo evidence")
    assert change.startswith("What would change it: the review quality weight rises above 0.54")
    assert excluded.startswith("Vendor C SaaS is excluded by the data_residency gate")


def test_triggers_cover_weights_evidence_lock_in_scale_and_cadence(analysis):
    kinds = [t.split(":")[0] for t in analysis.triggers]
    assert kinds == ["Weights", "Weights", "Evidence", "Lock-in", "Scale", "Cadence"]
    assert "exit cost $24k today (missing self_host_option)" in analysis.triggers[3]


def test_markdown_rows_sum_to_the_score(analysis):
    md = decision.to_markdown(analysis)
    row = next(
        line for line in md.splitlines() if line.startswith("| Vendor B co-build | co-build | 0.")
    )
    cells = row.split(" | ")
    parts = [float(c) for c in cells[2:7]]
    assert sum(parts) == pytest.approx(float(cells[7].strip("*")), abs=0.002)
    assert "**Verdict (go):**" in md


def test_memo_has_adr_sections(analysis):
    memo = decision.memo(analysis, "2026-11-01")
    assert memo.startswith("# Decision memo · Agentic code review")
    for heading in (
        "## Context",
        "## Options considered",
        "## Decision",
        "## Rationale",
        "## Sensitivity",
        "## Revisit triggers",
    ):
        assert heading in memo
    assert "- **Date:** 2026-11-01" in memo
    assert "every vendor is fictional" in memo


def test_verdict_handles_a_single_qualified_option(spec):
    solo = replace(spec, candidates=spec.candidates[:1])
    a = decision.analyse(solo)
    assert a.verdict.kind == "go"
    assert "no single weight change flips it" in a.verdict.text
    assert a.odds == (Odds(a.ranking[0].name, 1.0, a.odds[0].p10, a.odds[0].p50, a.odds[0].p90),)
