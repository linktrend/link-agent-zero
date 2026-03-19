# AIOS Integration Contract (AgentZero)

Last updated: 2026-03-19

AgentZero acts as the execution layer for AIOS MVO and must interoperate with Paperclip canonical NATS subjects.

## Consumes

- `aios.task.assigned`
- `aios.task.handoff`

## Produces

- `aios.task.accepted`
- `aios.task.progress`
- `aios.task.completed`
- `aios.task.failed`

## Constraints

- Preserve `tenant_id`, `run_id`, `task_id`, and DPR lineage metadata on every emitted event.
- Publish handoff/completion status for Chairman visibility in Slack through upstream notification flow.
- Treat `memory.md` style local notes as non-authoritative; durable traces belong to LiNKbrain.
- Consumer uses explicit ack/retry behavior with bounded retries. On exhausted retries it emits
  `aios.task.failed` with `stage=execution_dead_lettered` and terminates the message.
- On startup, the execution worker can sync persona bundles from AIOS (`/persona/sync/bundle`), write
  runtime markdown files to workspace, and acknowledge active revision (`/persona/sync/ack`).

## Runtime worker

Run AgentZero execution consumer:

```bash
python -m python.helpers.aios_nats_worker
```

Expected JetStream durable:

- `AGENTZERO_EXECUTION` on stream `AIOS_EVENTS`.

Key environment variables:

- `AIOS_NATS_URL` (default `nats://127.0.0.1:4222`)
- `AIOS_NATS_STREAM` (default `AIOS_EVENTS`)
- `AGENTZERO_NATS_DURABLE` (default `AGENTZERO_EXECUTION`)
- `AGENTZERO_DPR_ID` (execution DPR identity)
- `AGENTZERO_MANAGER_DPR_ID` (management DPR identity for completion/failure return)
- `AGENTZERO_FAIL_KEYWORDS` (optional comma-separated simulated failure trigger list)
- `AGENTZERO_NATS_MAX_DELIVER` (default `10`)
- `AIOS_BASE_URL` or `PAPERCLIP_AIOS_BASE_URL` (default `http://127.0.0.1:4000`)
- `AIOS_INGRESS_TOKEN` (required for authenticated persona sync endpoints)
- `AIOS_TENANT_ID` (default `00000000-0000-0000-0000-000000000001`)
- `AGENTZERO_WORKSPACE` (default current working directory)
- `AGENTZERO_POLICY_PACKAGE` (default `default`)
- `AGENTZERO_PERSONA_SYNC_ENABLED` (default `true`)
- `AGENTZERO_PERSONA_SYNC_STRICT` (default `false`, set `true` to fail hard on sync errors)
