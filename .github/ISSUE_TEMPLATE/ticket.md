---
name: Build ticket
about: The only shape of work ForgeOS accepts
title: "FOS-000: "
labels: ticket
---

## Objective
<!-- One sentence. What financial outcome or control does this deliver? -->

## User or system outcome
<!-- Who is better off, and how would they notice? -->

## In scope
<!-- Named files, modules, controls. Be specific. -->

## Explicitly out of scope
<!-- What this ticket must not touch. An unbounded ticket is an unreviewable diff. -->

## Data contract
<!-- Canonical entities read or written. Lineage and mapping version implications. -->

## Security constraints
<!-- Tenant isolation, secrets, tool permissions, prompt-injection surface. -->

## Tests
- [ ] Unit tests for the new logic
- [ ] Seeded-error case added to ForgeBench, if this changes detection
- [ ] Adversarial or decoy case added, if this changes thresholds

## Acceptance criteria
<!-- Checkable statements, not intentions. -->

## Rollback and migration notes

## Definition of done
- [ ] Deterministic math is in code, not in a prompt
- [ ] Every new finding carries evidence references and calculations
- [ ] ForgeBench critical recall has not regressed
- [ ] `forge tieout` still passes
- [ ] Audit events are emitted for anything that changes state
