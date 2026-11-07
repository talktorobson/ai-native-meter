# ADR-0001 · Invest in the context layer before scaling agents

- **Status:** accepted
- **Date:** 2026-11-07

## Context

Agent quality on internal tasks is limited less by model capability than by missing
or wrong context: conventions, ownership, governing decisions and incident history.
Scaling agent usage without fixing context multiplies token spend and review load
while task success stays flat.

## Decision

The context layer (ontology, curation standards, curation-aware retrieval, provenance)
is the first and highest-priority workstream. Agent, review and SRE workstreams consume
it through one retrieval interface and contribute signals back to it.

## Consequences

- Early agent rollouts are scoped to domains whose context meets the curation bar.
- Content owners receive lint results and become part of the AI programme.
- Retrieval has its own eval set, so context improvements are measurable independently
  of model changes.
- Risk: the layer becomes a platform with no users. Mitigation: every context milestone
  ships with an agent or review use case that consumes it.
