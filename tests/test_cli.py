import pytest

from devex import synthetic
from devex.cli import main
from devex.metrics import scorecard


def test_synthetic_generation_is_deterministic():
    kwargs = dict(
        seed=7,
        engineers=10,
        window=synthetic.QUARTER_1,
        profile=synthetic.BASELINE,
        label="t",
    )
    assert synthetic.generate(**kwargs) == synthetic.generate(**kwargs)


def test_synthetic_profile_hits_target_throughput():
    prs = synthetic.generate(
        seed=3,
        engineers=50,
        window=synthetic.QUARTER_1,
        profile=synthetic.BASELINE,
        label="t",
    )
    card = scorecard(prs, 50, synthetic.QUARTER_1)
    assert 1.8 < card.prs_per_engineer_week < 2.2


def test_demo_prints_every_section(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    for heading in (
        "## Scorecard",
        "## Code-review eval",
        "## Context pack",
        "## Curation lint",
        "## Model routing",
        "## Build vs partner",
    ):
        assert heading in out


def test_decide_prints_both_stages(capsys):
    assert main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "## Build vs partner: motion fit (Stage 1, illustrative)" in out
    assert "## Build vs partner: Agentic code review (Stage 2, synthetic)" in out
    assert "**Verdict (go):** Go with Vendor B co-build" in out


def test_decide_memo_uses_the_date_of_now(capsys):
    assert main(["decide", "--memo", "--now", "2027-02-03T09:00:00"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Decision memo · Agentic code review")
    assert "- **Date:** 2027-02-03" in out
    assert "## Revisit triggers" in out


def test_decide_weight_override_can_force_a_bake_off(capsys):
    assert main(["decide", "--weight", "review_quality=0.5"]) == 0
    out = capsys.readouterr().out
    assert "**Verdict (bake-off):**" in out
    assert "review quality (0.50)" in out


@pytest.mark.parametrize("bad", ["x", "tco=1.5", "tco=abc"])
def test_decide_rejects_malformed_weights(bad, capsys):
    with pytest.raises(SystemExit):
        main(["decide", "--weight", bad])
    assert "expected NAME=VALUE" in capsys.readouterr().err


def test_decide_rejects_unknown_weights():
    with pytest.raises(SystemExit, match="Unknown weight"):
        main(["decide", "--weight", "speed=0.3"])
