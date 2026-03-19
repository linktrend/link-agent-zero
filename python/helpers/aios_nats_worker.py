#!/usr/bin/env python3
"""AIOS NATS execution worker for AgentZero (MVO)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from uuid import uuid4

import nats

try:
    from nats.errors import TimeoutError as NatsTimeoutError
except Exception:  # pragma: no cover - version compatibility fallback
    NatsTimeoutError = asyncio.TimeoutError

TASK_ASSIGNED = "aios.task.assigned"
TASK_HANDOFF = "aios.task.handoff"
TASK_ACCEPTED = "aios.task.accepted"
TASK_PROGRESS = "aios.task.progress"
TASK_COMPLETED = "aios.task.completed"
TASK_FAILED = "aios.task.failed"

CONSUMED_EVENT_TYPES = {TASK_ASSIGNED, TASK_HANDOFF}


@dataclass
class RuntimeConfig:
    nats_servers: list[str]
    stream: str
    durable: str
    filter_subject: str
    schema_version: str
    agent_dpr_id: str
    manager_dpr_id: str
    batch_size: int
    fetch_timeout_sec: float
    max_deliver: int
    emit_progress: bool
    fail_keywords: set[str]
    aios_base_url: str
    ingress_token: str | None
    persona_tenant_id: str
    persona_policy_package: str
    persona_workspace: str
    persona_sync_enabled: bool
    persona_sync_strict: bool


def _parse_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback


def _parse_servers(raw: str) -> list[str]:
    servers = [entry.strip() for entry in raw.split(",") if entry.strip()]
    return servers or ["nats://127.0.0.1:4222"]


def _parse_keywords(raw: str | None) -> set[str]:
    if raw is None:
        return set()
    return {token.strip().lower() for token in raw.split(",") if token.strip()}


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _assert_envelope_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("event envelope must be an object")

    required_string_fields = [
        "event_id",
        "event_type",
        "occurred_at",
        "schema_version",
        "tenant_id",
        "mission_id",
        "run_id",
        "task_id",
        "from_dpr_id",
        "correlation_id",
        "idempotency_key",
    ]

    for field in required_string_fields:
        if not _non_empty_string(value.get(field)):
            raise ValueError(f"missing required envelope field: {field}")

    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("missing required envelope field: payload")

    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_from_base(
    base: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    *,
    from_dpr_id: str,
    to_dpr_id: str | None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "occurred_at": _now_iso(),
        "schema_version": base.get("schema_version") or "2026-03-17",
        "tenant_id": base["tenant_id"],
        "mission_id": base["mission_id"],
        "run_id": base["run_id"],
        "task_id": base["task_id"],
        "from_dpr_id": from_dpr_id,
        "to_dpr_id": to_dpr_id,
        "correlation_id": base["correlation_id"],
        "idempotency_key": f"{base['idempotency_key']}:{event_type}:{uuid4().hex[:8]}",
        "payload": payload,
    }


def _should_fail(envelope: dict[str, Any], keywords: set[str]) -> bool:
    if not keywords:
        return False
    haystack = json.dumps(envelope.get("payload", {}), ensure_ascii=True).lower()
    return any(keyword in haystack for keyword in keywords)


def _derive_outgoing_events(
    envelope: dict[str, Any],
    cfg: RuntimeConfig,
) -> list[dict[str, Any]]:
    outgoing = [
        _event_from_base(
            envelope,
            TASK_ACCEPTED,
            {
                **envelope["payload"],
                "stage": "execution_accept",
                "accepted_by": cfg.agent_dpr_id,
            },
            from_dpr_id=cfg.agent_dpr_id,
            to_dpr_id=cfg.agent_dpr_id,
        )
    ]

    if cfg.emit_progress:
        outgoing.append(
            _event_from_base(
                envelope,
                TASK_PROGRESS,
                {
                    **envelope["payload"],
                    "stage": "execution_in_progress",
                    "progress_pct": 75,
                    "update": "AgentZero execution in progress",
                },
                from_dpr_id=cfg.agent_dpr_id,
                to_dpr_id=cfg.agent_dpr_id,
            )
        )

    if _should_fail(envelope, cfg.fail_keywords):
        outgoing.append(
            _event_from_base(
                envelope,
                TASK_FAILED,
                {
                    **envelope["payload"],
                    "stage": "execution_failed",
                    "error": "Execution failed due to fail-keyword policy",
                },
                from_dpr_id=cfg.agent_dpr_id,
                to_dpr_id=cfg.manager_dpr_id,
            )
        )
        return outgoing

    outgoing.append(
        _event_from_base(
            envelope,
            TASK_COMPLETED,
            {
                **envelope["payload"],
                "stage": "execution_completed",
                "result": "AgentZero completed the task",
            },
            from_dpr_id=cfg.agent_dpr_id,
            to_dpr_id=cfg.manager_dpr_id,
        )
    )
    return outgoing


async def _publish(js: Any, event: dict[str, Any]) -> None:
    await js.publish(event["event_type"], json.dumps(event, ensure_ascii=True).encode("utf-8"))


async def _process_message(msg: Any, js: Any, cfg: RuntimeConfig) -> None:
    try:
        envelope = _assert_envelope_shape(json.loads(msg.data.decode("utf-8")))
    except Exception as exc:
        print(f"[agentzero-nats] invalid envelope, terminating message: {exc}")
        await msg.term()
        return

    if envelope["event_type"] not in CONSUMED_EVENT_TYPES:
        await msg.ack()
        return

    outgoing_events = _derive_outgoing_events(envelope, cfg)

    try:
        for event in outgoing_events:
            await _publish(js, event)
            print(
                f"[agentzero-nats] published {event['event_type']} run={event['run_id']} task={event['task_id']}"
            )
        await msg.ack()
    except Exception as exc:
        delivery_count = 1
        metadata = getattr(msg, "metadata", None)
        if metadata is not None:
            delivery_count = int(getattr(metadata, "num_delivered", 1) or 1)

        if delivery_count >= cfg.max_deliver:
            dead_letter_event = _event_from_base(
                envelope,
                TASK_FAILED,
                {
                    **envelope["payload"],
                    "stage": "execution_dead_lettered",
                    "error": "AgentZero exhausted retry budget and dead-lettered the message",
                    "retry_attempts": delivery_count,
                },
                from_dpr_id=cfg.agent_dpr_id,
                to_dpr_id=cfg.manager_dpr_id,
            )
            try:
                await _publish(js, dead_letter_event)
            except Exception as publish_exc:
                print(
                    f"[agentzero-nats] failed to publish dead-letter failure event: {publish_exc}"
                )
            print(
                f"[agentzero-nats] max deliveries exceeded; terminating message run={envelope['run_id']} task={envelope['task_id']}"
            )
            await msg.term()
            return

        print(f"[agentzero-nats] publish failed, nacking for retry: {exc}")
        await msg.nak()


async def _bind_pull_subscription(js: Any, cfg: RuntimeConfig) -> Any:
    return await js.pull_subscribe(
        subject=cfg.filter_subject,
        durable=cfg.durable,
        stream=cfg.stream,
    )


def _persona_headers(cfg: RuntimeConfig, include_json: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {}
    if cfg.ingress_token:
        headers["Authorization"] = f"Bearer {cfg.ingress_token}"
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def _sync_persona_bundle_sync(cfg: RuntimeConfig) -> None:
    if not cfg.persona_sync_enabled:
        return

    base = cfg.aios_base_url.rstrip("/")
    query = urllib_parse.urlencode(
        {"tenantId": cfg.persona_tenant_id, "dprId": cfg.agent_dpr_id}
    )
    sync_url = f"{base}/persona/sync/bundle?{query}"

    try:
        sync_req = urllib_request.Request(sync_url, headers=_persona_headers(cfg), method="GET")
        with urllib_request.urlopen(sync_req, timeout=15) as sync_resp:
            payload = json.loads(sync_resp.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        if exc.code == 404:
            print(
                "[agentzero-nats] no persona bundle published for execution DPR; continuing"
            )
            return
        if cfg.persona_sync_strict:
            raise
        print(f"[agentzero-nats] persona sync warning: fetch failed ({exc.code})")
        return
    except Exception as exc:
        if cfg.persona_sync_strict:
            raise
        print(f"[agentzero-nats] persona sync warning: {exc}")
        return

    if (
        not isinstance(payload, dict)
        or payload.get("accepted") is not True
        or not isinstance(payload.get("hash"), str)
        or not isinstance(payload.get("bundle"), dict)
    ):
        message = "[agentzero-nats] persona sync warning: response missing accepted/hash/bundle"
        if cfg.persona_sync_strict:
            raise RuntimeError(message)
        print(message)
        return

    workspace = Path(cfg.persona_workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    bundle = payload["bundle"]
    for filename, content in bundle.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        target = workspace / filename
        target.write_text(content if content.endswith("\n") else f"{content}\n", encoding="utf-8")

    ack_body = {
        "tenantId": cfg.persona_tenant_id,
        "dprId": cfg.agent_dpr_id,
        "acknowledgedRevisionHash": payload["hash"],
        "policyPackage": cfg.persona_policy_package,
        "metadata": {
            "source": "agentzero_nats_worker",
            "applied_at": _now_iso(),
            "workspace": str(workspace),
        },
    }
    ack_req = urllib_request.Request(
        f"{base}/persona/sync/ack",
        data=json.dumps(ack_body).encode("utf-8"),
        headers=_persona_headers(cfg, include_json=True),
        method="POST",
    )
    try:
        with urllib_request.urlopen(ack_req, timeout=15):
            pass
        print(
            f"[agentzero-nats] persona bundle applied + acknowledged dpr={cfg.agent_dpr_id} hash={payload['hash']}"
        )
    except Exception as exc:
        if cfg.persona_sync_strict:
            raise
        print(f"[agentzero-nats] persona sync warning: ack failed ({exc})")


async def _sync_persona_bundle(cfg: RuntimeConfig) -> None:
    await asyncio.to_thread(_sync_persona_bundle_sync, cfg)


async def run_worker(cfg: RuntimeConfig) -> None:
    print(
        f"[agentzero-nats] connecting to {', '.join(cfg.nats_servers)} "
        f"stream={cfg.stream} durable={cfg.durable}"
    )
    nc = await nats.connect(servers=cfg.nats_servers)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        js = nc.jetstream()
        sub = await _bind_pull_subscription(js, cfg)
        print("[agentzero-nats] pull subscription ready")
        await _sync_persona_bundle(cfg)

        while not stop_event.is_set():
            try:
                messages = await sub.fetch(batch=cfg.batch_size, timeout=cfg.fetch_timeout_sec)
            except (asyncio.TimeoutError, NatsTimeoutError):
                continue

            for msg in messages:
                await _process_message(msg, js, cfg)
    finally:
        await nc.drain()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentZero AIOS NATS execution worker")
    parser.add_argument(
        "--nats-url",
        default=os.getenv("AIOS_NATS_URL") or os.getenv("NATS_URL") or "nats://127.0.0.1:4222",
        help="Comma-separated NATS server URLs",
    )
    parser.add_argument("--stream", default=os.getenv("AIOS_NATS_STREAM", "AIOS_EVENTS"))
    parser.add_argument("--durable", default=os.getenv("AGENTZERO_NATS_DURABLE", "AGENTZERO_EXECUTION"))
    parser.add_argument("--filter-subject", default=os.getenv("AGENTZERO_NATS_FILTER", "aios.task.*"))
    parser.add_argument(
        "--schema-version",
        default=os.getenv("AIOS_NATS_SCHEMA_VERSION", "2026-03-17"),
    )
    parser.add_argument(
        "--agent-dpr-id",
        default=os.getenv("AGENTZERO_DPR_ID", "INT-EXE-000000-0000-AGENTZERO"),
    )
    parser.add_argument(
        "--manager-dpr-id",
        default=os.getenv("AGENTZERO_MANAGER_DPR_ID", "INT-MNG-000000-0000-OPENCLAW"),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("AGENTZERO_NATS_BATCH_SIZE", "25")),
    )
    parser.add_argument(
        "--fetch-timeout-sec",
        type=float,
        default=float(os.getenv("AGENTZERO_NATS_FETCH_TIMEOUT_SEC", "5")),
    )
    parser.add_argument(
        "--max-deliver",
        type=int,
        default=int(os.getenv("AGENTZERO_NATS_MAX_DELIVER", "10")),
    )
    parser.add_argument(
        "--emit-progress",
        action=argparse.BooleanOptionalAction,
        default=_parse_bool(os.getenv("AGENTZERO_EMIT_PROGRESS"), True),
    )
    parser.add_argument(
        "--fail-keywords",
        default=os.getenv("AGENTZERO_FAIL_KEYWORDS", ""),
        help="Comma-separated keywords that force TASK_FAILED",
    )
    parser.add_argument(
        "--aios-base-url",
        default=os.getenv("AIOS_BASE_URL")
        or os.getenv("PAPERCLIP_AIOS_BASE_URL")
        or "http://127.0.0.1:4000",
    )
    parser.add_argument(
        "--ingress-token",
        default=os.getenv("AIOS_INGRESS_TOKEN"),
    )
    parser.add_argument(
        "--persona-tenant-id",
        default=os.getenv("AIOS_TENANT_ID", "00000000-0000-0000-0000-000000000001"),
    )
    parser.add_argument(
        "--persona-policy-package",
        default=os.getenv("AGENTZERO_POLICY_PACKAGE", "default"),
    )
    parser.add_argument(
        "--persona-workspace",
        default=os.getenv("AGENTZERO_WORKSPACE", str(Path.cwd())),
    )
    parser.add_argument(
        "--persona-sync-enabled",
        action=argparse.BooleanOptionalAction,
        default=_parse_bool(os.getenv("AGENTZERO_PERSONA_SYNC_ENABLED"), True),
    )
    parser.add_argument(
        "--persona-sync-strict",
        action=argparse.BooleanOptionalAction,
        default=_parse_bool(os.getenv("AGENTZERO_PERSONA_SYNC_STRICT"), False),
    )
    return parser


def runtime_from_args(args: argparse.Namespace) -> RuntimeConfig:
    return RuntimeConfig(
        nats_servers=_parse_servers(args.nats_url),
        stream=args.stream,
        durable=args.durable,
        filter_subject=args.filter_subject,
        schema_version=args.schema_version,
        agent_dpr_id=args.agent_dpr_id,
        manager_dpr_id=args.manager_dpr_id,
        batch_size=max(1, args.batch_size),
        fetch_timeout_sec=max(0.5, args.fetch_timeout_sec),
        max_deliver=max(1, args.max_deliver),
        emit_progress=bool(args.emit_progress),
        fail_keywords=_parse_keywords(args.fail_keywords),
        aios_base_url=args.aios_base_url,
        ingress_token=(args.ingress_token.strip() if isinstance(args.ingress_token, str) and args.ingress_token.strip() else None),
        persona_tenant_id=args.persona_tenant_id,
        persona_policy_package=args.persona_policy_package,
        persona_workspace=args.persona_workspace,
        persona_sync_enabled=bool(args.persona_sync_enabled),
        persona_sync_strict=bool(args.persona_sync_strict),
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cfg = runtime_from_args(args)
    asyncio.run(run_worker(cfg))


if __name__ == "__main__":
    main()
