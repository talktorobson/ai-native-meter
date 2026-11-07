# 04 · Operating model: building a dedicated team from borrowed time

## Principles

1. **A product, not a programme.** Every workstream has users, a roadmap, a metric
   and a feedback loop.
2. **Measure before scaling.** Nothing goes org-wide without an eval and a cohort
   comparison.
3. **Paved roads, not mandates.** Adoption is earned by being the easiest path.
4. **Protect the people.** Momentum built on burnout is borrowed against next quarter.

## From borrowed to dedicated: Anchor, Rotate, Convert

| Phase | What happens | Guardrails for people |
|---|---|---|
| **Anchor** (weeks 0–6) | Each workstream gets one dedicated technical anchor, ideally from reallocated direct reports. Charters, metrics and interfaces are written. | No one anchors more than one workstream. |
| **Rotate** (weeks 4–16) | Borrowed contributors join on explicit 8-week rotations with a signed return date and a home-team agreement on capacity. | Rotations are full time, never "20% on top". Explicit stop-doing list per person. |
| **Convert** (from week 12) | Strong rotators are offered permanent moves through unlocked headcount; external hiring fills gaps the org lacks (retrieval/IR, eval engineering, sandbox security). | Home teams get backfill priority before the move. |

Illustrative shape after 12 months: six squads of 4–6 engineers plus a shared
**Eval & Insights** core of 3–4, about 30–35 people, each squad with a tech lead and
an embedded product-minded engineer.

## The first 90 days

**Days 0–30: understand and instrument**

- Meet every borrowed contributor 1:1; map who is doing what and at what cost to their
  home team.
- Ship the baseline scorecard (north star plus guardrails) for the whole org, with the
  formula published.
- Inventory every AI tool, contract and spend line; attribute spend to teams.
- Write one-page charters for each workstream: problem, user, metric, interfaces,
  non-goals.
- Agree the decision rights with the CTO and peer pillars (where routing and vendor
  management sit).

**Days 31–60: focus and first wins**

- Name anchors; start rotations; open requisitions.
- Context layer: ontology v1 and owners for three pilot domains; retrieval eval set.
- Review: shadow mode on five repositories with usefulness labelling.
- Coding environment: harden sandbox defaults; build the historical-ticket eval.
- Launch two structured third-party engagements against shared eval sets.

**Days 61–90: prove and decide**

- First cohort readout: AI-assisted vs not, adopting vs not.
- Build-vs-partner decisions for review and AI SRE, using the scorecard.
- Routing v1 in the gateway with showback per team.
- Year-one plan with headcount, budget and three bets the team will stop if the
  numbers do not move.

## Cadence

| Rhythm | Audience | Artefact |
|---|---|---|
| Weekly | Pillar leads | Scorecard review, guardrail breaches, blockers |
| Bi-weekly | Whole org | Demos of shipped capability, with numbers |
| Monthly | CTO and staff | One page: north star, guardrails, spend, decisions needed |
| Quarterly | Portfolio | Kill / continue / scale per workstream, against stated thresholds |

## Communicating with executives

- Lead with the north star and the guardrail most at risk, not with features.
- Every claim of impact names its cohort comparison.
- Every request names the decision, the options and the recommendation.
- Bad news travels fastest. The Goodhart guards exist so the team raises it first.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Home teams reclaim borrowed engineers mid-delivery | Written rotation agreements signed by both managers; CTO escalation path |
| Metric gaming | Quality-adjusted north star, automatic Goodhart guards, AI share never targeted |
| Security incident from an agent | Sandbox defaults, no production credentials, staged autonomy, audit logs |
| Vendor lock-in | Own the eval set and context layer; open interfaces; exit clauses (see doc 05) |
| Spend outruns value | Routing, budgets, cost per merged PR as a guardrail |
| Adoption stalls | Shadow modes, opt-in champions, paved-road integration in existing tools |
