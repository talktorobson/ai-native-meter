from pathlib import Path

import pytest

from devex import motion_fit
from devex.motion_fit import AFFINITY, ATTRIBUTES, MOTIONS, Capability

CAPABILITIES = Path(__file__).parents[1] / "data" / "capabilities.json"


def cap(**attributes) -> Capability:
    return Capability("c", {a: 0.5 for a in ATTRIBUTES} | attributes)


def test_affinities_are_complete_and_bounded():
    for motion in MOTIONS:
        assert set(AFFINITY[motion]) == set(ATTRIBUTES)
        assert sum(abs(v) for v in AFFINITY[motion].values()) == pytest.approx(1.0)


def test_fit_is_neutral_at_midpoint_and_stays_in_unit_interval():
    assert all(motion_fit.fit(cap().attributes, m) == pytest.approx(0.5) for m in MOTIONS)
    for motion in MOTIONS:
        extremes = (
            {a: 1.0 if AFFINITY[motion][a] > 0 else 0.0 for a in ATTRIBUTES},
            {a: 0.0 if AFFINITY[motion][a] > 0 else 1.0 for a in ATTRIBUTES},
        )
        assert [motion_fit.fit(e, motion) for e in extremes] == [
            pytest.approx(1.0),
            pytest.approx(0.0),
        ]


def test_fit_is_the_sum_of_attribute_contributions():
    attrs = cap(differentiation=0.9, market_maturity=0.2).attributes
    score = next(s for s in motion_fit.motion_scores(cap(**attrs)) if s.motion == "build")
    assert score.fit == pytest.approx(0.5 + 0.25 * 0.4 - 0.10 * -0.3)
    assert score.fit == pytest.approx(0.5 + sum(v for _, v in score.contributions))


@pytest.mark.parametrize(
    ("attributes", "motion"),
    [
        (dict(differentiation=0.9, proprietary_context=0.9, internal_capacity=0.8), "build"),
        (dict(proprietary_context=0.8, market_velocity=0.9, internal_capacity=0.3), "co-build"),
        (dict(differentiation=0.1, proprietary_context=0.1, market_maturity=0.9), "buy"),
    ],
)
def test_each_motion_wins_where_it_should(attributes, motion):
    assert motion_fit.assess(cap(**attributes)).recommended.motion == motion


def test_guards_block_a_motion_even_when_it_fits_best():
    own_context = motion_fit.assess(
        cap(proprietary_context=0.9, differentiation=0.0, market_maturity=1.0, time_pressure=1.0)
    )
    assert own_context.score("buy").blocked_by == ("it is our own context",)
    assert own_context.recommended.motion != "buy"
    assert own_context.score("buy").fit > own_context.recommended.fit

    no_team = motion_fit.assess(cap(internal_capacity=0.1, differentiation=1.0))
    assert not no_team.score("build").allowed
    assert no_team.scores[-1].motion == "build"


def test_rationale_names_the_deciding_attributes_and_what_we_keep():
    fit = motion_fit.assess(cap(differentiation=0.1, proprietary_context=0.1, market_maturity=0.9))
    assert fit.rationale.startswith("buy over co-build: proprietary context +")
    assert "We still own the context, the evals and the interfaces." in fit.rationale
    built = motion_fit.assess(cap(differentiation=0.9, proprietary_context=0.95))
    assert "buy blocked (it is our own context)" in built.rationale
    assert "We still own" not in built.rationale


def test_sample_capabilities_cover_every_motion():
    fits = tuple(map(motion_fit.assess, motion_fit.load_capabilities(CAPABILITIES)))
    by_name = {f.capability.name: f.recommended.motion for f in fits}
    assert by_name["Context layer"] == "build"
    assert by_name["Agentic code review"] == "co-build"
    assert by_name["AI SRE diagnosis"] == "co-build"
    assert by_name["IDE coding assistant"] == "buy"
    assert all(f.margin > 0 for f in fits)


def test_markdown_marks_the_pick_and_strikes_blocked_motions():
    md = motion_fit.to_markdown(
        tuple(map(motion_fit.assess, motion_fit.load_capabilities(CAPABILITIES)))
    )
    assert "| Context layer | **0.82** | 0.68 | ~~0.20~~ | **build** |" in md
