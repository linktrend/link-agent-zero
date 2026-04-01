# Documentation Governance

Owner: LiNKtrend Platform  
Last updated: 2026-04-01

## Canonical Rules
- Repository-level governance docs live in `/docs`.
- `/docs/README.md` is the canonical docs index.
- Outdated docs are moved to `/docs/archive` (not hard-deleted first).
- App/component implementation notes may exist near code, but governance/SOP/runbook docs must be indexed from `/docs/README.md`.

## Document Metadata Standard
Major SOPs/runbooks must include:
- Owner
- Last updated

## Change Discipline
Any PR that changes system behavior must update affected docs in the same PR.

## Relevance Policy
When reviewing docs:
1. If current and useful: keep active and indexed.
2. If useful but old context: move to `/docs/archive`.
3. If obsolete and superseded: archive first, then schedule deletion in a later cleanup PR.
