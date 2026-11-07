from pathlib import Path

import pytest

from devex.routing import (
    DEFAULT_CATALOG,
    Model,
    Request,
    TaskClass,
    classify,
    load_requests,
    request_cost,
    route,
    simulate,
)

REQUESTS = Path(__file__).parents[1] / "data" / "routing_requests.json"


@pytest.mark.parametrize(
    ("prompt", "tokens", "expected"),
    [
        ("Fix the typo in the README", 500, TaskClass.TRIVIAL),
        ("Fix the typo in the README", 3_000, TaskClass.STANDARD),
        ("Add pagination to the statements endpoint", 1_000, TaskClass.STANDARD),
        ("Refactor the ledger module", 1_000, TaskClass.COMPLEX),
        ("Add a field", 7_000, TaskClass.COMPLEX),
    ],
)
def test_classify(prompt, tokens, expected):
    assert classify(prompt, tokens).task_class is expected


def test_classify_reports_signals():
    signals = classify("Find the root cause of this race condition", 7_000).signals
    assert "root cause" in signals
    assert "race condition" in signals
    assert "large-context" in signals


def test_request_cost():
    model = Model("m", 1.0, 2.0, {})
    assert request_cost(model, Request("p", 1_000_000, 500_000)) == pytest.approx(2.0)


def test_route_picks_cheapest_model_that_clears_the_bar():
    ref = Request("p", 1_000, 1_000)
    assert route(TaskClass.TRIVIAL, DEFAULT_CATALOG, 0.9, ref).name == "small-fast"
    assert route(TaskClass.STANDARD, DEFAULT_CATALOG, 0.9, ref).name == "mid-tier"
    assert route(TaskClass.STANDARD, DEFAULT_CATALOG, 0.92, ref).name == "frontier-large"


def test_route_falls_back_to_best_model_when_none_clears_the_bar():
    ref = Request("p", 1_000, 1_000)
    assert route(TaskClass.COMPLEX, DEFAULT_CATALOG, 0.99, ref).name == "frontier-large"


def test_simulate_saves_money_and_reports_quality_tradeoff():
    report = simulate(load_requests(REQUESTS), min_quality=0.9)
    assert report.requests == 16
    assert sum(report.mix.values()) == 16
    assert 0.2 < report.savings_ratio < 0.6
    assert report.routed_cost_usd < report.baseline_cost_usd
    assert report.routed_expected_quality <= report.baseline_expected_quality


def test_simulate_with_impossible_bar_matches_baseline():
    report = simulate(load_requests(REQUESTS), min_quality=1.0)
    assert report.savings_ratio == pytest.approx(0.0)
    assert report.model_usage == {"frontier-large": 16}
