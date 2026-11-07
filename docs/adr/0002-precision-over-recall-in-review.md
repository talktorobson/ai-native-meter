# ADR-0002 · Optimise AI code review for precision before recall

- **Status:** accepted
- **Date:** 2026-11-07

## Context

Engineers mute reviewers that produce noise. Once muted, a reviewer's recall is zero in
practice. Vendor demos emphasise what a reviewer catches, rarely what it wrongly flags.

## Decision

Review candidates are ranked by **F0.5** (precision weighted twice as much as recall),
with noise per PR and silence on clean PRs reported alongside. A reviewer goes live on
a repository only after a shadow period above a precision bar agreed with that team.

## Consequences

- The seeded-defect benchmark must include clean cases, not only defective ones.
- Some real defects will be missed initially; recall is grown category by category.
- High-severity categories (money handling, secrets, PII, injection) are prioritised and
  weighted in severity-weighted recall.
