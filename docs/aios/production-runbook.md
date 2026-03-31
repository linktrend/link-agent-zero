# AgentZero Production Runbook (LiNKdroplet Admin)

## Purpose
Run execution worker as isolated service consuming canonical `aios.*` subjects.

## Deployment requirements
- Runtime env uses non-secret + `*_SECRET_NAME` contract.
- Secrets resolved from GSM before startup.
- Service fails fast when required env or resolved secrets are missing.
- Supabase schema ownership: none (AgentZero consumes orchestration/events and upstream APIs).

## Startup
```bash
cp deploy/linktrend-production/.env.example deploy/linktrend-production/.env.runtime
# Render resolved secrets into .env.runtime before startup.
docker compose -f deploy/linktrend-production/docker-compose.yml --env-file deploy/linktrend-production/.env.runtime up -d
```

## Health
- Verify worker consumes `aios.task.assigned` and `aios.task.handoff`.
- Verify emissions for `accepted`, `progress`, `completed`, `failed`.
- Verify dead-letter behavior after bounded retries.
