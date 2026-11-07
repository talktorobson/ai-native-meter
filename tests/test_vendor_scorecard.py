from dataclasses import replace
from pathlib import Path

import pytest

from devex import vendor_scorecard as vs
from devex.vendor_scorecard import Anchors, Candidate, Claim, Costs, Scale

CANDIDATES = Path(__file__).parents[1] / "data" / "vendor_candidates.json"
SCALE = Scale(
    engineers=100, prs_per_engineer_week=2.0, years=3, engineer_week_usd=4000, fte_year_usd=200000
)
ANCHORS = Anchors(tco_usd=(0.0, 1_000_000.0), time_to_value_weeks=(4.0, 40.0))
PORTABLE = dict.fromkeys(vs.LOCK_IN_FACTORS, True)


def candidate(**overrides) -> Candidate:
    base = dict(
        name="c",
        motion="buy",
        gates={"soc2": True},
        lock_in=PORTABLE,
        costs=Costs(),
        review_quality=Claim(0.8, "benchmark"),
        time_to_value=Claim(4, "benchmark"),
        operability=Claim(0.8, "benchmark"),
    )
    return Candidate(**base | overrides)


def test_tco_sums_licences_tokens_integration_run_and_people():
    cost = vs.tco(
        Costs(
            seat_usd_month=10,
            usd_per_m_tokens=5,
            tokens_m_per_pr=0.1,
            integration_weeks=10,
            run_usd_year=1000,
            in_house_fte=1.5,
        ),
        SCALE,
    )
    assert cost.licences == pytest.approx(10 * 100 * 12 * 3)
    assert cost.tokens == pytest.approx(5 * 0.1 * 100 * 2 * 52 * 3)
    assert cost.integration == pytest.approx(40_000)
    assert cost.run == pytest.approx(3000)
    assert cost.people == pytest.approx(900_000)
    assert cost.total == pytest.approx(36_000 + 15_600 + 40_000 + 3000 + 900_000)


@pytest.mark.parametrize(
    ("value", "best", "worst", "expected"),
    [(5, 0, 10, 0.5), (-1, 0, 10, 1.0), (20, 0, 10, 0.0), (0.75, 1.0, 0.5, 0.5)],
)
def test_linear_maps_anchors_and_clips(value, best, worst, expected):
    assert vs.linear(value, best, worst) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("evidence", "expected"), [("demo", 0.7), ("pilot", 0.82), ("benchmark", 0.9)]
)
def test_evidence_discount_pulls_unproven_scores_towards_neutral(evidence, expected):
    assert vs.discount(0.9, evidence) == pytest.approx(expected)
    assert vs.discount(0.5, evidence) == pytest.approx(0.5)
    assert vs.discount(0.1, evidence) == pytest.approx(1 - expected)  # bad claims count less too


def test_lock_in_risk_and_exit_cost_are_weighted_by_exit_effort():
    assert vs.lock_in_risk(PORTABLE) == 0.0
    assert vs.lock_in_risk({}) == 1.0
    no_export = PORTABLE | {"data_export": False}
    no_self_host = PORTABLE | {"self_host_option": False}
    assert vs.lock_in_risk(no_export) > vs.lock_in_risk(no_self_host)
    assert vs.lock_in_risk(no_export) == pytest.approx(26 / 72)
    assert vs.exit_cost_usd(no_export, 4000) == pytest.approx(26 * 4000)


def test_assess_normalises_discounts_and_flags_gates():
    a = vs.assess(
        candidate(
            gates={"soc2": True, "data_residency": False},
            costs=Costs(run_usd_year=100_000),
            time_to_value=Claim(22, "demo"),
            lock_in=PORTABLE | {"model_choice": False},
        ),
        SCALE,
        ANCHORS,
    )
    assert a.failed_gates == ("data_residency",)
    assert not a.qualified
    assert a.criterion("tco").normalized == pytest.approx(0.7)
    assert a.criterion("time_to_value").normalized == pytest.approx(0.5)
    assert a.criterion("time_to_value").effective == pytest.approx(0.5)
    assert a.criterion("lock_in").effective == pytest.approx(1 - 8 / 72)
    assert a.missing_terms == ("model_choice",)


def test_reweight_sets_one_weight_and_keeps_the_others_in_proportion():
    w = vs.reweight({"a": 2, "b": 1, "c": 1}, "a", 0.2)
    assert w == pytest.approx({"a": 0.2, "b": 0.4, "c": 0.4})
    assert sum(w.values()) == pytest.approx(1.0)


def test_contributions_sum_to_the_total():
    a = vs.assess(candidate(), SCALE, ANCHORS)
    weights = {c: 1.0 for c in vs.CRITERIA}
    assert sum(v for _, v in vs.contributions(a, weights)) == pytest.approx(vs.total(a, weights))
    assert vs.total(a, weights) == pytest.approx((0.8 + 1.0 + 1.0 + 0.8 + 1.0) / 5)


def test_proving_a_claim_raises_the_score():
    demo = candidate(review_quality=Claim(0.9, "demo"))
    proven = replace(demo, review_quality=Claim(0.9, "benchmark"))
    weights = dict.fromkeys(vs.CRITERIA, 1.0)
    assert vs.total(vs.assess(proven, SCALE, ANCHORS), weights) > vs.total(
        vs.assess(demo, SCALE, ANCHORS), weights
    )


def test_load_decision_takes_review_quality_from_the_eval_when_a_reviewer_is_named():
    spec = vs.load_decision(CANDIDATES, {"rule-baseline": 0.42})
    in_house = next(c for c in spec.candidates if c.motion == "build")
    assert in_house.review_quality == Claim(0.42, "benchmark", "rule-baseline")
    assert in_house.cost_evidence == "pilot"
    assert spec.policy.seed == 7
    assert sum(spec.weights.values()) == pytest.approx(1.0)


def test_sample_decision_is_deterministic_and_gates_the_captive_saas():
    spec = vs.load_decision(CANDIDATES, {"rule-baseline": 0.9})
    first = tuple(vs.assess(c, spec.scale, spec.anchors) for c in spec.candidates)
    again = tuple(vs.assess(c, spec.scale, spec.anchors) for c in spec.candidates)
    assert first == again
    saas = next(a for a in first if a.name == "Vendor C SaaS")
    assert saas.failed_gates == ("data_residency",)
    assert saas.lock_in_risk == 1.0
