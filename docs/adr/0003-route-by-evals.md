# ADR-0003 · Route models by internal eval results, not by vendor benchmarks

- **Status:** accepted
- **Date:** 2026-11-07

## Context

Defaulting every request to the most capable model is simple and expensive. Public
leaderboards do not reflect internal languages, frameworks or conventions, so they are a
poor basis for routing.

## Decision

Each request is classified into a task class. The gateway routes it to the cheapest
model whose **measured pass rate on internal evals for that class** clears a quality
bar. Routing reports always show the expected quality delta next to the savings.

## Consequences

- Every model in the catalogue needs an internal eval score per task class before it
  can receive traffic.
- The classifier itself is evaluated; misclassification of complex work as trivial is
  the costliest error and is tracked separately.
- The quality bar is a business decision, owned jointly with the teams that bear the
  cost of failures.
