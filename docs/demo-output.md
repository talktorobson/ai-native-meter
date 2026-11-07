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

## Build vs partner: motion fit (Stage 1, illustrative)

Fit = 0.5 + sum of affinity x (attribute - 0.5). Struck-through motions are blocked by a guard. Own the context, the evals and the interfaces; rent the rest.

| Capability | Build | Co-build | Buy | Recommended | Why |
|---|---|---|---|---|---|
| Context layer | **0.82** | 0.68 | ~~0.20~~ | **build** | build over co-build: differentiation +0.06, internal capacity +0.06; co-build leads on market maturity +0.01; buy blocked (it is our own context). |
| Eval harness and benchmarks | **0.73** | 0.58 | ~~0.28~~ | **build** | build over co-build: market velocity +0.06, differentiation +0.05; buy blocked (it is our own context). |
| Agentic code review | 0.56 | **0.65** | 0.46 | **co-build** | co-build over build: market velocity +0.09, time pressure +0.01; build leads on differentiation +0.01. We still own the context, the evals and the interfaces. |
| AI SRE diagnosis | 0.58 | **0.68** | 0.39 | **co-build** | co-build over build: market velocity +0.06, internal capacity +0.05; build leads on data sensitivity +0.01. We still own the context, the evals and the interfaces. |
| IDE coding assistant | 0.22 | 0.50 | **0.80** | **buy** | buy over co-build: market maturity +0.14, differentiation +0.10; co-build leads on market velocity +0.04. We still own the context, the evals and the interfaces. |
| Frontier model inference | ~~0.15~~ | 0.49 | **0.84** | **buy** | buy over co-build: proprietary context +0.20, differentiation +0.12; co-build leads on market velocity +0.05; build blocked (no team to own a build). We still own the context, the evals and the interfaces. |

## Build vs partner: Agentic code review (Stage 2, synthetic)

Stage 1 motion fit for Agentic code review: **co-build** (0.65; build 0.56, buy 0.46). Stage 2 agrees with it.

**Evidence, normalised and discounted** (raw · evidence level → effective score)

| Candidate | review quality | 3-year TCO | time to value | operability | lock-in |
|---|---|---|---|---|---|
| Vendor B co-build | F0.5 0.86 · pilot → 0.79 | $2.48M · contract → 0.72 | 12 wk · pilot → 0.72 | 0.75 · pilot → 0.70 | 8% risk · contract → 0.92 |
| In-house reviewer on context layer | F0.5 0.90 · benchmark → 0.90 | $3.18M · pilot → 0.52 | 30 wk · demo → 0.39 | 0.80 · pilot → 0.74 | 0% risk · contract → 1.00 |
| Vendor A review bot | F0.5 0.80 · demo → 0.65 | $3.53M · contract → 0.42 | 6 wk · demo → 0.72 | 0.60 · demo → 0.55 | 47% risk · contract → 0.53 |
| Vendor C SaaS | F0.5 0.93 · demo → 0.72 | $4.10M · contract → 0.26 | 4 wk · demo → 0.75 | 0.60 · demo → 0.55 | 100% risk · contract → 0.00 |

**Weighted contributions** (weight in brackets; the row sums to the score)

| Candidate | Motion | review quality (0.35) | 3-year TCO (0.25) | time to value (0.15) | operability (0.10) | lock-in (0.15) | Score | P(win) | Gates |
|---|---|---|---|---|---|---|---|---|---|
| Vendor B co-build | co-build | 0.276 | 0.180 | 0.108 | 0.070 | 0.137 | **0.772** | 82% | pass |
| In-house reviewer on context layer | build | 0.315 | 0.129 | 0.058 | 0.074 | 0.150 | **0.726** | 18% | pass |
| Vendor A review bot | buy | 0.227 | 0.105 | 0.108 | 0.055 | 0.079 | **0.575** | 0% | pass |
| Vendor C SaaS | buy | 0.250 | 0.065 | 0.112 | 0.055 | 0.000 | **0.482** | n/a | fail: data_residency |

P(win) from 2,000 seeded draws (seed 7) over weight and score uncertainty.

**Verdict (go):** Go with Vendor B co-build (co-build): it wins 82% of 2,000 simulations; robust unless the review quality weight rises above 0.54 (now 0.35).

**Sensitivity** (nearest flip per weight and direction)

| Weight | Now | Flips if it | Change needed | Winner then |
|---|---|---|---|---|
| review quality | 0.35 | rises above 0.54 | +54% | In-house reviewer on context layer |
| 3-year TCO | 0.25 | falls below 0.04 | -86% | In-house reviewer on context layer |
| time to value | 0.15 | falls below 0.02 | -90% | In-house reviewer on context layer |
| lock-in | 0.15 | rises above 0.45 | +200% | In-house reviewer on context layer |
| operability | 0.10 | rises above 0.58 | +479% | In-house reviewer on context layer |

**Rationale**

- Why Vendor B co-build wins: it leads In-house reviewer on context layer on 3-year TCO (+0.051) and time to value (+0.050) in weighted contribution.
- Trade-off: it gives up review quality (-0.039) to In-house reviewer on context layer.
- Evidence: Vendor A review bot's review quality is demo evidence, so it counts as 0.65 instead of 0.80.
- What would change it: the review quality weight rises above 0.54 (now 0.35), In-house reviewer on context layer would win.
- Vendor C SaaS is excluded by the data_residency gate, whatever it scores.
