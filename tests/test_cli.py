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
