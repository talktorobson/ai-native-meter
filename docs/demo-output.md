# Demo output

Generated with `devex demo`. All data is synthetic.

## Scorecard: baseline vs platform rollout (120 engineers, synthetic)

| Metric | Baseline | With platform | Change |
|---|---|---|---|
| PRs / engineer / week | 1.99 | 2.60 | +31% |
| Quality-adjusted PRs / engineer / week | 1.82 | 2.39 | +32% |
| Change failure rate | 8.7% | 8.1% | -7% |
| Median cycle time (h) | 20.3 | 12.3 | -39% |
| Median review latency (h) | 13.9 | 6.2 | -55% |
| Median PR size (lines) | 72 | 73 | +1% |
| AI-assisted share | 16% | 55% | +252% |
| Token cost per merged PR (USD) | $3.04 | $6.84 | +125% |

**Goodhart guards**
- Token cost per merged PR grew faster than the guardrail: check routing and loops.

## Code-review eval (10 seeded cases)

| Reviewer | Precision | Recall | Sev-weighted recall | F0.5 | Noise/PR | Silent on clean | Cost/TP |
|---|---|---|---|---|---|---|---|
| rule-baseline | 90% | 90% | 84% | 0.90 | 0.10 | 100% | $0.000 |
| keyword-noisy | 56% | 90% | 84% | 0.61 | 0.70 | 33% | $0.000 |

Plug any agent in as `Callable[[ReviewCase], Sequence[Finding]]` and it is scored on the same cases.

## Context pack for "add idempotency to the batch transfers endpoint"

9 items, 393/400 tokens, 2 dropped by budget.

| Entity | Kind | Score | Why |
|---|---|---|---|
| tkt-881 | ticket | 10.06 | lexical match |
| inc-2041 | incident | 5.21 | lexical match |
| repo-transfers | repo | 5.03 | changes <- tkt-881 |
| svc-transfers | service | 2.60 | affected <- inc-2041 |
| adr-idempotency | adr | 2.04 | lexical match |
| api-transfers | api | 1.63 | lexical match |
| svc-ledger | service | 1.30 | depends_on <- svc-transfers |
| team-payments | team | 1.30 | owns <- svc-transfers |
| runbook-transfers | runbook | 0.65 | operates <- svc-transfers |

## Curation lint

- `doc-retry-guidelines` stale: 273 days old, SLA is 180 days.
- `doc-legacy-transfers` missing-owner: Every curated entity needs an owner.
- `doc-legacy-transfers` stale: 1280 days old, SLA is 180 days.
- `doc-legacy-transfers` deprecated-linked: Deprecated but still linked; archive it.
- `runbook-transfers` stale: 214 days old, SLA is 90 days.
- `doc-onboarding-snacks` missing-owner: Every curated entity needs an owner.
- `doc-onboarding-snacks` stale: 1035 days old, SLA is 180 days.
- `doc-onboarding-snacks` orphan: Not connected to any other entity.

## Model routing (quality bar 90%)

- Requests: 16 (complex: 4, standard: 5, trivial: 7)
- Models used: frontier-large: 4, mid-tier: 5, small-fast: 7
- Cost: $2.527 always-frontier vs $1.659 routed (34% saved)
- Expected pass rate: 92.6% vs 89.9%

## Build vs partner: code review

| Candidate | Motion | Score | Lock-in risk | Gates |
|---|---|---|---|---|
| Vendor B co-build | partner | 0.70 | 20% | pass |
| In-house reviewer on context layer | build | 0.67 | 0% | pass |
| Vendor A review bot | buy | 0.62 | 60% | pass |
| Vendor C SaaS | buy | 0.59 | 100% | data_residency |

**Verdict:** Too close to call (Vendor B co-build 0.70 vs In-house reviewer on context layer 0.67): run a time-boxed bake-off on the shared eval set.
