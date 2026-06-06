# Upstream fork policy — linktrend/link-agentzero

This repository is a **LiNKtrend fork** of [agent0ai/agent-zero](https://github.com/agent0ai/agent-zero). It is **not** a contribution path to upstream.

## Rules

1. **Never open pull requests** to `agent0ai/agent-zero`. Upstream changes are absorbed only inside this fork.
2. **Upstream sync** runs via `.github/workflows/upstream-sync-staging.yml`, merging `agent0ai/agent-zero` **`main`** into fork **`staging`** (custom/staging code wins on conflict: `-X ours`).
3. **Promotion** inside the fork: short-lived branches → **`development`** → **`staging`** → **`main`**, enforced by `.github/workflows/branch-source-policy.yml`.
4. LiNKtrend integration hooks live under `deploy/linktrend-production/` — do not push these upstream.

## Branch names

| Branch | Role |
|--------|------|
| `development` | Integration; receives LiNKdev `issue/*` and `dev/*` merges |
| `staging` | Pre-production; receives automated upstream merges + promotion from `development` |
| `main` | Production-stable fork line; Principal promotion from `staging` |

Allowed merge sources into `development`: `issue/*`, `dev/*`, `feature/*`, `fix/*`, `chore/*`, `codex/*`, `cursor/*`, `antigravity/*`, `dependabot/*`.

## Conflict resolution

If upstream sync fails with merge conflicts, resolve on **`staging`** in this repository. Do not push conflict resolution upstream.

## LiNKaios / LiNKbot

LiNKtrend-System owns lane mappings (`LiNKbot/roles/platform/agent-zero-lanes.ts`) and the TypeScript adapter (`LiNKbot/runtime-adapters/agent-zero/`). This fork owns the Python worker container and VPS compose under `deploy/linktrend-production/`.
