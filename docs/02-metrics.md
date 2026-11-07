# 02 · Metrics: one north star, four guardrails, zero vanity

Implemented in [`src/devex/metrics.py`](https://github.com/talktorobson/ai-native-meter/blob/main/src/devex/metrics.py).

## North star

**Quality-adjusted merged PRs per engineer per week**: human-initiated PRs merged in
the window that were neither reverted nor linked to an incident, divided by
engineer-weeks of capacity.

Why PRs: they are the unit of shipped change every engineer already understands, they
are cheap to collect from the VCS, and they connect directly to DORA metrics. Why
"quality-adjusted": raw throughput is trivially gamed and quietly rewards breaking
production.

## Guardrails

| Guardrail | Definition | Why it exists |
|---|---|---|
| Change failure rate | Merged PRs reverted or linked to an incident / merged PRs | Speed bought with quality is not speed |
| Median cycle time | Opened → merged | Detects queues moving to review and CI |
| Median review latency | Opened → first review | Review is where the AI bottleneck lands first |
| Token cost per merged PR | All token spend in the window / merged PRs | Abandoned agent attempts are real cost |

With per-call telemetry, the cost guardrail sharpens into **cost per accepted change**: the
spend linked to each change divided by changes merged and not reverted, with spend on
reverted and abandoned work reported as waste. Cost per token is a diagnostic beneath it, never
the target. See [09 · Token economics](09-token-economics.md) and
[ADR-0006](adr/0006-cost-per-accepted-outcome.md).

## Goodhart guards (automatic)

`gaming_signals(baseline, current)` flags improvements that are probably not real:

- Throughput up while **median PR size collapses** → likely PR splitting.
- Throughput up while **change failure rate worsens** → quality is paying for speed.
- **Cost per merged PR** grows past the guardrail → loops, retries or bad routing.
- **Cost per change falls while change failure rate or review latency worsens** → savings
  bought with quality (`cost_quality_signal`, also run per team on token spend).

Example from the synthetic demo: throughput +31%, CFR down, review latency −55%,
but cost per merged PR +125%. The guard fires, which is exactly the conversation a
platform leader wants to have in month two rather than discover in the CFO's review.

## Measurement design rules

1. **Cohorts, not anecdotes.** Compare AI-assisted vs non-assisted and adopting vs
   not-yet-adopting teams with `cohort_scorecards`, and stagger rollouts so there is
   always a comparison group.
2. **Bots never count as throughput.** Dependency bumps are excluded by design.
3. **Attribute cost to outcomes.** Every token is tagged with a team and, where
   possible, a PR, so spend can be read per merged change.
4. **Publish the formula.** Every number on the executive scorecard links to the
   code that computes it. Trust in the metric is part of the product.
5. **Survey sparingly, instrument heavily.** Quarterly developer sentiment
   complements telemetry. It never replaces it.
