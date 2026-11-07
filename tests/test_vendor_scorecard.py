from pathlib import Path

import pytest

from devex.vendor_scorecard import Candidate, decide, load_candidates, lock_in_risk, score

CANDIDATES = Path(__file__).parents[1] / "data" / "vendor_candidates.json"
WEIGHTS = {"quality": 0.5, "cost": 0.5}
PORTABLE = dict.fromkeys(
    ("data_export", "open_interfaces", "model_choice", "self_host_option", "exit_under_90_days"),
    True,
)


def cand(name, quality, cost, *, gates=None, lock_in=None, motion="buy") -> Candidate:
    return Candidate(
        name=name,
        motion=motion,
        scores={"quality": quality, "cost": cost},
        gates=gates or {"soc2": True},
        lock_in=lock_in if lock_in is not None else PORTABLE,
    )


def test_lock_in_risk_bounds():
    assert lock_in_risk(cand("a", 1, 1)) == 0.0
    assert lock_in_risk(cand("a", 1, 1, lock_in={})) == 1.0


def test_score_applies_lock_in_penalty():
    assert score(cand("a", 1.0, 0.6), WEIGHTS, 0.25) == pytest.approx(0.8)
    assert score(cand("a", 1.0, 0.6, lock_in={}), WEIGHTS, 0.25) == pytest.approx(0.6)


def test_failed_gate_disqualifies_even_the_best_scorer():
    rec = decide(
        (cand("best", 1, 1, gates={"data_residency": False}), cand("ok", 0.6, 0.6)), WEIGHTS
    )
    assert rec.ranking[-1].name == "best"
    assert rec.ranking[-1].failed_gates == ("data_residency",)
    assert rec.verdict.startswith("Go with ok")


def test_close_scores_trigger_bake_off():
    rec = decide((cand("a", 0.8, 0.8), cand("b", 0.79, 0.79)), WEIGHTS)
    assert "bake-off" in rec.verdict


def test_no_qualified_candidate():
    rec = decide((cand("a", 1, 1, gates={"soc2": False}),), WEIGHTS)
    assert "No candidate" in rec.verdict


def test_sample_candidates():
    weights, candidates = load_candidates(CANDIDATES)
    rec = decide(candidates, weights)
    assert rec.ranking[-1].name == "Vendor C SaaS"
    assert not rec.ranking[-1].qualified
    assert all(d.qualified for d in rec.ranking[:-1])
