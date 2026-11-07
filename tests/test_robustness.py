import random
from dataclasses import replace

import pytest

from devex import robustness
from devex import vendor_scorecard as vs
from devex.vendor_scorecard import Assessment, Criterion, Tco

WEIGHTS = {"quality": 0.5, "cost": 0.5}


def option(name, quality, cost, *, evidence="benchmark", gated=False) -> Assessment:
    return Assessment(
        name=name,
        motion="buy",
        failed_gates=("soc2",) if gated else (),
        criteria=(
            Criterion("quality", "", quality, evidence, quality),
            Criterion("cost", "", cost, evidence, cost),
        ),
        tco=Tco(0, 0, 0, 0, 0),
        lock_in_risk=0.0,
        exit_cost_usd=0.0,
        missing_terms=(),
    )


def test_samplers_are_reproducible_and_well_formed():
    a, b = random.Random(1), random.Random(1)
    assert [robustness.normal(a) for _ in range(5)] == [robustness.normal(b) for _ in range(5)]
    w = robustness.dirichlet(random.Random(3), {"x": 0.7, "y": 0.3, "z": 0.0}, 50)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["z"] == 0.0
    draws = [robustness.gamma(random.Random(i), 0.5) for i in range(2000)]
    assert sum(draws) / len(draws) == pytest.approx(0.5, abs=0.06)
    normals = [robustness.normal(random.Random(i)) for i in range(4000)]
    assert sum(normals) / len(normals) == pytest.approx(0.0, abs=0.06)


def test_simulation_is_reproducible_and_sums_to_one():
    pool = (
        option("a", 0.8, 0.6),
        option("b", 0.6, 0.8, evidence="demo"),
        option("c", 1, 1, gated=True),
    )
    first = robustness.simulate(pool, WEIGHTS, samples=500, seed=11)
    assert first == robustness.simulate(pool, WEIGHTS, samples=500, seed=11)
    assert first != robustness.simulate(pool, WEIGHTS, samples=500, seed=12)
    assert sum(o.p_win for o in first) == pytest.approx(1.0)
    assert {o.name for o in first} == {"a", "b"}  # gated options never win
    assert all(o.p10 <= o.p50 <= o.p90 for o in first)


def test_a_dominant_option_always_wins_and_a_near_tie_splits():
    clear = robustness.simulate(
        (option("a", 0.9, 0.9), option("b", 0.3, 0.3)), WEIGHTS, samples=300
    )
    assert clear[0].name == "a" and clear[0].p_win == 1.0
    tie = robustness.simulate((option("a", 0.7, 0.7), option("b", 0.7, 0.69)), WEIGHTS, samples=600)
    assert 0.3 < tie[0].p_win < 0.7


def test_flip_point_is_where_the_challenger_ties():
    leader, challenger = option("a", 0.9, 0.4), option("b", 0.5, 0.9)
    t = robustness.flip_point(leader, challenger, WEIGHTS, "cost")
    assert t == pytest.approx(0.4 / 0.9)
    at = vs.reweight(WEIGHTS, "cost", t)
    assert vs.total(leader, at) == pytest.approx(vs.total(challenger, at))
    above = vs.reweight(WEIGHTS, "cost", t + 0.01)
    assert vs.total(challenger, above) > vs.total(leader, above)


def test_no_flip_when_the_leader_dominates_every_criterion():
    assert (
        robustness.flip_point(option("a", 0.9, 0.9), option("b", 0.5, 0.5), WEIGHTS, "cost") is None
    )
    assert robustness.sensitivity((option("a", 0.9, 0.9), option("b", 0.5, 0.5)), WEIGHTS) == ()


def test_sensitivity_reports_the_nearest_flip_per_weight_and_direction():
    leader, challenger = option("a", 0.9, 0.5), option("b", 0.5, 0.8)
    flips = robustness.sensitivity((challenger, leader, option("x", 0, 0, gated=True)), WEIGHTS)
    assert {(f.criterion, f.direction) for f in flips} == {
        ("cost", "rises above"),
        ("quality", "falls below"),
    }
    cost = next(f for f in flips if f.criterion == "cost")
    assert cost.threshold == pytest.approx(0.4 / 0.7)
    assert cost.challenger == "b"
    assert [f.headroom for f in flips] == sorted(f.headroom for f in flips)
    assert all(f.headroom == pytest.approx(f.distance / f.weight) for f in flips)


def test_flip_description_reads_as_a_sentence():
    flip = robustness.Flip("cost", 0.25, 0.32, "b")
    assert flip.describe() == "the cost weight rises above 0.32 (now 0.25)"
    assert flip.describe("3-year TCO").startswith("the 3-year TCO weight")
    assert replace(flip, threshold=0.1).direction == "falls below"
