# ADR-0006 · Meter cost per accepted outcome, not per token

- **Status:** accepted
- **Date:** 2026-11-08

## Context

AI spend grows with adoption: chat, completions, coding agents, AI review, CI and AI SRE all
call models, and their bills are measured in tokens. Tokens are the easy unit. Providers
report them, prices are per million, and a dashboard of tokens per engineer or dollars per
million tokens is a day's work.

That unit optimises the wrong thing. Fewer tokens per call can mean a weaker model that
needs three attempts, an agent that gives up early, or a change that merges and is reverted
a week later. All of these look cheaper per token and are more expensive per useful
result. Goodhart's law applies again: once cost per token or tokens per engineer is the
target, teams downgrade models and trim context until quality pays for it. The mirror risk
also exists: a budget conversation driven by the token bill alone can cut the tools that
produce the most accepted work.

The metrics in [doc 02](../02-metrics.md) already have a guardrail, cost per merged PR, and
the [routing evals](0003-route-by-evals.md) already say which tier is good enough for which
task class.

## Decision

1. **The headline is cost per accepted change.** All AI spend linked to a change (authoring
   chat, agent steps, AI review, CI) divided by changes that merged and were not reverted.
   In-flight changes stay out of both sides. Cost per resolved incident plays the same role
   for operations. Token counts, cost per token and cache ratios are diagnostics under it.
2. **Waste is reported, not hidden.** Spend on reverted and abandoned changes is a KPI of its
   own, so a cheaper path that throws more work away shows up as waste.
3. **Cheaper must not mean worse.** `metrics.cost_quality_signal` fires when cost per change
   falls while the change failure rate or review latency worsens beyond a bar. It is a
   Goodhart guard on the PR scorecard, and it runs per team on spend.
4. **Savings come from evals, not from guesses.** The routing opportunity counts only
   frontier spend on task classes where the evals say a cheaper tier passes, repriced at that
   tier. Unclassified spend is shown apart, not counted as a saving.
5. **Prices are dated data.** Every event is priced at the price in effect on its date, from a
   versioned table. A model without a price is an error, not a zero.
6. **People are pseudonyms.** Engineers are pseudonymised on ingest, and top spenders are
   shown with their accepted changes next to their spend, for anomaly follow-up under
   [ADR-0005](0005-individual-metrics-are-not-leaderboards.md), never as a leaderboard.
7. **No estimates where there is no telemetry.** Public repositories show PRs, not invoices,
   so the real-data dashboards leave these KPIs blank rather than inventing them.

## Consequences

- The metering needs the join from spend to change: every call on a PR is tagged with its
  repo and PR number, at the gateway or in the agent. Untagged spend is visible in the total
  but cannot be credited to an outcome, which is an incentive to tag.
- A team that cuts cost by forcing the smallest model sees its cost per accepted change fall
  and its guard fire in the same view, which is the conversation to have
  ([doc 09](../09-token-economics.md) shows exactly that case).
- Cost per accepted change moves with the revert rate and the merge rate, not just with
  prices, so it is noisier than cost per token. Read it per period and per team, with the
  sample sizes.
- Engineer-hour comparisons use an assumed loaded rate passed as a parameter. They give a
  sense of scale, not a return on investment.
