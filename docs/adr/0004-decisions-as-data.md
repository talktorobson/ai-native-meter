# ADR-0004 · Make build-vs-partner decisions as data, not as debate

- **Status:** accepted
- **Date:** 2026-11-07

## Context

AI capabilities for developers are being offered faster than they can be evaluated.
Make-or-buy calls made on the strength of a demo or the most senior opinion produce
three predictable failures:

- We buy what should be our own context.
- We build commodities that the market will overtake in a quarter.
- We sign contracts whose exit cost nobody priced.

A single weighted scorecard with a fixed tie margin helps, but it treats a vendor's demo
like a measured result. It also says nothing about how fragile the winner is.

## Decision

Sourcing decisions run through a two-stage engine whose inputs are versioned data files
and whose outputs are reproducible:

1. **Motion fit first.** Each capability is profiled on seven attributes and scored for
   build, co-build and buy, with guards that veto a motion outright (never buy our own
   context; never build without a team). The default is to own the context, the evals and
   the interfaces, and rent the rest.
2. **Evidence, not opinions.** Candidates pass hard gates, then each criterion is
   normalised against anchors fixed before bids open. Every score is discounted by its
   evidence level (demo < pilot < benchmark). Review quality comes from our own eval
   harness, and cost is a 3-year TCO at projected scale.
3. **Lock-in is priced.** Each missing portability term has an exit cost in
   engineer-weeks, and lock-in is a weighted criterion.
4. **Robustness replaces the tie margin.** A seeded Monte Carlo over weight and score
   uncertainty gives each candidate a win probability. A closed-form sensitivity analysis
   reports the exact weight at which the winner flips. The verdict is go, bake-off or
   no-go according to written rules.
5. **The memo is generated.** `devex decide --memo` produces the rationale, the
   sensitivity table and the revisit triggers, and the memo is filed with the decision.

## Consequences

- Stakeholders argue about **weights, anchors and evidence**, which are explicit and
  versioned, instead of about the conclusion.
- Vendors are told the gates, criteria and anchors before a pilot. Running a pilot on our
  benchmark is in their interest, because it removes the evidence discount.
- A close call becomes a time-boxed bake-off on the shared eval set, not another meeting.
- Every decision carries revisit triggers (a weight shift, evidence upgrades, a renewal
  term, adoption scale), so it is re-opened for a reason rather than on a whim.
- Profiles and weights are still judgements. The engine makes them visible and tests how
  much they matter; it does not remove them.
