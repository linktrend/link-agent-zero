# Branching and Deployment Policy

Owner: LiNKtrend Platform  
Last updated: 2026-04-01

## Purpose
This repository uses a protected promotion model so production deploys are deterministic and auditable.

## Branch Model
- `main` is production-only (protected, no direct pushes).
- `staging` is the integration branch (all feature work lands here first).
- `dev/<agent-name>/<topic>` branches are short-lived developer/agent branches.

## Promotion Flow
1. Develop in `dev/*`.
2. Open PR to `staging`.
3. Resolve conflicts and pass CI/security gates in `staging`.
4. Open PR from `staging` to `main`.
5. Deploy only from tagged commit on `main` (pin by tag/SHA, never `latest`).

## Required Gates
- CI must pass on both `staging` and `main`.
- Security checks required: SAST, dependency vulnerability scan, secret scan.
- PR review required for `staging` (minimum 1 approval).
- Stricter review for `main` (recommended 2 approvals + release owner sign-off).

## Deployment Rules
- Production deployment source is `main` only.
- Optional dev/staging VPS deployments may come from `staging`.
- Every production release must be tagged (example: `v2026.04.01-1`).

## Branch Protection Setup
Configure in GitHub repository settings:
- Protect `main`: no force-push, no direct push, required checks, required approvals.
- Protect `staging`: required checks and at least one approval.

## Note for Repos Still on `main`
If this repo currently uses `main` as default, treat `main` as production branch until default branch is renamed to `main`.
