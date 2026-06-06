# Branching and Deployment Policy

Owner: LiNKtrend Platform  
Last updated: 2026-06-06  
Aligned with: `LiNKdev/factory/rules/01-git-branching.mdc`, `LiNKtrend-System/docs/BRANCHING_AND_DEPLOYMENT_POLICY.md`

## Purpose

Protected promotion model for the **link-agentzero** fork. Upstream is [agent0ai/agent-zero](https://github.com/agent0ai/agent-zero); LiNKtrend never pushes upstream.

## Branch model (LiNKdev)

| Branch | Role |
|--------|------|
| `development` | Integration — all agent/issue work lands here via PR |
| `staging` | Pre-production — upstream sync target + promotion from `development` |
| `main` | Production — Principal promotion from `staging` only |

Short-lived branches: `issue/<id>-<slug>`, `dev/<machine><ide>`, `feature/*`, `fix/*`, `chore/*`.

## Promotion flow

```
issue/* or dev/*  →  PR to development  →  Integrator merges when merge-ready
development       →  PR to staging      →  Principal
staging           →  PR to main         →  Principal
```

- **Never** push directly to `staging` or `main`.
- Upstream sync merges into **`staging`** only (see `docs/UPSTREAM.md`).

## Upstream sync

- Workflow: `.github/workflows/upstream-sync-staging.yml`
- Source: `agent0ai/agent-zero` branch `main`
- Target: fork `staging` via `bot/upstream-sync` PR (auto-merge when clean)
- Conflict policy: **keep fork customizations** (`git merge -X ours`)

## Required gates

- CI + security workflow on PRs
- Branch source policy on `development`, `staging`, `main`
- Production deploy from tagged **`main`** commit only (pin by SHA)

## Deployment

- VPS path: `/opt/linktrend/link-agentzero`
- LiNKaios compose builds via `LiNKtrend-System/deploy/docker/agent-zero.Dockerfile`
- Health: `GET /api/health` on port 80
