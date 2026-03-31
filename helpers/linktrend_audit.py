import asyncio
import contextvars
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from helpers.print_style import PrintStyle

try:
    from supabase import Client, create_client  # type: ignore
except Exception:  # pragma: no cover - optional dependency at runtime
    Client = Any  # type: ignore
    create_client = None  # type: ignore


PAYLOAD_ENV = "AIOS_TASK_PAYLOAD"
IDENTITY_ENV = "LINKTREND_IDENTITY_PATH"
DPR_PATTERN = re.compile(r"^INT-EXE-\d{6}-[A-F0-9]{4}-[A-Z0-9]+$")

_AUDIT_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "linktrend_audit_context",
    default={},
)


@dataclass
class AuditMetadata:
    dpr_id: str
    run_id: str
    task_id: str
    tenant_id: str
    agent_id: str


def _strict_mode_enabled() -> bool:
    raw = os.getenv("LINKTREND_AUDIT_STRICT", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _warn(message: str) -> None:
    PrintStyle(background_color="yellow", font_color="black", padding=True).print(
        f"[LiNKtrend Audit] {message}"
    )


def _fatal_if_strict(message: str) -> None:
    if _strict_mode_enabled():
        raise RuntimeError(message)
    _warn(message)


def _load_aios_payload() -> dict[str, Any]:
    raw = os.getenv(PAYLOAD_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        _warn(f"Unable to parse {PAYLOAD_ENV}; continuing with empty payload")
        return {}


def _first_non_empty(*values: Any, default: str = "UNKNOWN") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _extract_task_payload_metadata(payload: dict[str, Any]) -> AuditMetadata:
    tenant_obj = payload.get("tenant") if isinstance(payload.get("tenant"), dict) else {}
    return AuditMetadata(
        dpr_id=_first_non_empty(payload.get("dpr_id"), payload.get("dprId")),
        run_id=_first_non_empty(payload.get("run_id"), payload.get("runId")),
        task_id=_first_non_empty(
            payload.get("task_id"), payload.get("taskId"), payload.get("id")
        ),
        tenant_id=_first_non_empty(
            payload.get("tenant_id"),
            payload.get("tenantId"),
            tenant_obj.get("id"),
        ),
        agent_id=_first_non_empty(
            payload.get("agent_id"),
            payload.get("agentId"),
            os.getenv("AIOS_AGENT_ID"),
            default="A0",
        ),
    )


def _current_metadata() -> AuditMetadata:
    payload_meta = _extract_task_payload_metadata(_load_aios_payload())
    ctx = _AUDIT_CONTEXT.get()
    return AuditMetadata(
        dpr_id=_first_non_empty(ctx.get("dpr_id"), payload_meta.dpr_id),
        run_id=_first_non_empty(ctx.get("run_id"), payload_meta.run_id),
        task_id=_first_non_empty(ctx.get("task_id"), payload_meta.task_id),
        tenant_id=_first_non_empty(ctx.get("tenant_id"), payload_meta.tenant_id),
        agent_id=_first_non_empty(ctx.get("agent_id"), payload_meta.agent_id, default="A0"),
    )


def bind_audit_context(**kwargs: str) -> None:
    current = dict(_AUDIT_CONTEXT.get())
    for key in ("dpr_id", "run_id", "task_id", "tenant_id", "agent_id"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            current[key] = value.strip()
    _AUDIT_CONTEXT.set(current)


def set_context(supabase: Client, tenant_id: str):
    return supabase.rpc("set_tenant_context", {"p_tenant": tenant_id}).execute()


def log_audit_run(
    supabase: Client,
    run_id: str,
    task_id: str,
    agent_id: str,
    status: str,
):
    return supabase.rpc(
        "log_audit_run",
        {
            "p_run_id": run_id,
            "p_task_id": task_id,
            "p_agent_id": agent_id,
            "p_status": status,
        },
    ).execute()


class SupabaseAuditClient:
    def __init__(self) -> None:
        self._client: Client | None = None

    def _get_client(self) -> Client:
        if self._client is not None:
            return self._client

        if create_client is None:
            _fatal_if_strict("supabase-py is not installed but audit is required")
            raise RuntimeError("supabase-py unavailable")

        url = os.getenv("SUPABASE_URL", "").strip()
        key = _first_non_empty(
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            os.getenv("SUPABASE_KEY"),
            os.getenv("SUPABASE_ANON_KEY"),
            default="",
        )
        if not url or not key:
            _fatal_if_strict("Supabase credentials missing for LiNKtrend audit")
            raise RuntimeError("Supabase credentials missing")

        self._client = create_client(url, key)
        return self._client

    def _log_status_sync(self, meta: AuditMetadata, status: str) -> None:
        client = self._get_client()
        set_context(client, meta.tenant_id)
        log_audit_run(
            client,
            run_id=meta.run_id,
            task_id=meta.task_id,
            agent_id=meta.agent_id,
            status=status,
        )

    async def log_status(self, meta: AuditMetadata, status: str) -> None:
        await asyncio.to_thread(self._log_status_sync, meta, status)


_AUDIT_CLIENT = SupabaseAuditClient()


async def log_event(
    event_type: str,
    details: str,
    payload: dict[str, Any] | None = None,
) -> None:
    meta = _current_metadata()
    status_payload = {
        "event_type": event_type,
        "details": details,
        "payload": payload or {},
        "dpr_id": meta.dpr_id,
        "tenant_id": meta.tenant_id,
    }
    status = json.dumps(status_payload, ensure_ascii=True)
    try:
        await _AUDIT_CLIENT.log_status(meta, status)
    except Exception as exc:
        _fatal_if_strict(f"Failed to write audit event '{event_type}': {exc}")


async def log_terminal_command(command: str, session: int, runtime: str = "terminal") -> None:
    await log_event(
        event_type="terminal_command",
        details=command,
        payload={"session": session, "runtime": runtime},
    )


async def log_reasoning_step(chunk: str, full_text_len: int) -> None:
    await log_event(
        event_type="reasoning_step",
        details=chunk,
        payload={"chunk_len": len(chunk), "full_text_len": full_text_len},
    )


async def log_token_usage(token_text: str, token_count: int, source: str) -> None:
    await log_event(
        event_type="token_usage",
        details=token_text,
        payload={"token_count": token_count, "source": source},
    )


def _read_identity_text() -> str:
    explicit_path = os.getenv(IDENTITY_ENV, "").strip()
    if explicit_path:
        path = Path(explicit_path)
    else:
        path = Path(__file__).resolve().parents[2] / "IDENTITY.md"
    if not path.exists():
        raise FileNotFoundError(f"IDENTITY.md not found at {path}")
    return path.read_text(encoding="utf-8")


def _extract_authorized_tenant(identity_text: str) -> str:
    patterns = [
        r"authorized_tenant_id\s*[:=]\s*([A-Za-z0-9._:-]+)",
        r"authorized_tenant\s*[:=]\s*([A-Za-z0-9._:-]+)",
        r"tenant_id\s*[:=]\s*([A-Za-z0-9._:-]+)",
        r"tenant\s*[:=]\s*([A-Za-z0-9._:-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, identity_text, re.IGNORECASE)
        if match and match.group(1):
            return match.group(1).strip()
    raise ValueError("Unable to extract authorized tenant from IDENTITY.md")


def _extract_identity_dpr(identity_text: str) -> str:
    patterns = [
        r"dpr_id\s*[:=]\s*([A-Za-z0-9-]+)",
        r"dpr\s*[:=]\s*([A-Za-z0-9-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, identity_text, re.IGNORECASE)
        if match and match.group(1):
            dpr_id = match.group(1).strip().upper()
            if not DPR_PATTERN.fullmatch(dpr_id):
                raise ValueError(
                    "Invalid dpr_id format in IDENTITY.md; expected INT-EXE-YYMMDD-XXXX-NAME"
                )
            return dpr_id
    raise ValueError("Unable to extract dpr_id from IDENTITY.md")


async def enforce_identity_gate_or_exit(task_id: str | None = None) -> None:
    payload = _load_aios_payload()
    payload_meta = _extract_task_payload_metadata(payload)
    if task_id and task_id.strip():
        payload_meta.task_id = task_id.strip()

    try:
        identity_text = _read_identity_text()
        authorized_tenant = _extract_authorized_tenant(identity_text)
        identity_dpr = _extract_identity_dpr(identity_text)
        payload_meta.agent_id = identity_dpr
        bind_audit_context(**asdict(payload_meta))
    except Exception as exc:
        await log_event(
            event_type="critical_security_breach",
            details="Critical Security Breach: identity file unavailable or invalid dpr_id",
            payload={"error": str(exc)},
        )
        os._exit(1)

    if not payload_meta.tenant_id or payload_meta.tenant_id == "UNKNOWN":
        await log_event(
            event_type="critical_security_breach",
            details="Critical Security Breach: tenant_id missing in AIOS_TASK_PAYLOAD",
            payload={"authorized_tenant": authorized_tenant},
        )
        os._exit(1)

    if payload_meta.tenant_id != authorized_tenant:
        await log_event(
            event_type="critical_security_breach",
            details="Critical Security Breach: tenant mismatch",
            payload={
                "authorized_tenant": authorized_tenant,
                "payload_tenant": payload_meta.tenant_id,
            },
        )
        os._exit(1)

    await log_event(
        event_type="identity_check_passed",
        details="Worker identity and tenant check passed",
        payload={"authorized_tenant": authorized_tenant},
    )


def _extract_required_env_vars(payload: dict[str, Any]) -> list[str]:
    candidate = payload.get("required_env_vars", payload.get("requiredEnvVars"))
    if isinstance(candidate, list):
        return [str(item).strip() for item in candidate if str(item).strip()]
    if isinstance(candidate, str):
        return [item.strip() for item in candidate.split(",") if item.strip()]
    return []


def _linktrend_secret_name(resource: str) -> str:
    resource_normalized = resource.strip().upper()
    prefix = "LINKTREND_AIOS_PROD_"
    if resource_normalized.startswith(prefix):
        resource_normalized = resource_normalized[len(prefix) :]
    return f"{prefix}{resource_normalized}"


def _gsm_resolve_sync(var_name: str) -> str | None:
    try:
        from google.cloud import secretmanager  # type: ignore
    except Exception:
        return None

    project_id = _first_non_empty(
        os.getenv("GOOGLE_CLOUD_PROJECT"),
        os.getenv("GCP_PROJECT"),
        default="",
    )
    if not project_id:
        return None

    secret_name = _linktrend_secret_name(var_name)
    client = secretmanager.SecretManagerServiceClient()
    try:
        secret_path = f"projects/{project_id}/secrets/{secret_name}"
        secret_meta = client.get_secret(request={"name": secret_path})
        labels = dict(getattr(secret_meta, "labels", {}) or {})
        if labels.get("venture", "").strip().lower() != "linktrend":
            return None
        version_name = f"{secret_path}/versions/latest"
        resp = client.access_secret_version(request={"name": version_name})
        return resp.payload.data.decode("utf-8")
    except Exception:
        return None


async def ensure_required_env_vars(secret_name_pattern: str = "LINKTREND_AIOS_PROD_{resource}") -> None:
    payload = _load_aios_payload()
    required_vars = _extract_required_env_vars(payload)
    if not required_vars:
        return

    _ = secret_name_pattern  # Maintains explicit LiNKtrend naming contract at call sites.
    missing = [name for name in required_vars if not os.getenv(name, "").strip()]

    for name in missing:
        value = await asyncio.to_thread(_gsm_resolve_sync, name)
        if value:
            os.environ[name] = value
            await log_event(
                event_type="secret_resolved",
                details=f"Resolved missing env var via GSM: {name}",
                payload={
                    "env_var": name,
                    "source": "gsm",
                    "secret_name": _linktrend_secret_name(name),
                },
            )
        else:
            await log_event(
                event_type="secret_missing",
                details=f"Required env var missing and GSM lookup failed: {name}",
                payload={"env_var": name, "secret_name": _linktrend_secret_name(name)},
            )
            _fatal_if_strict(
                f"Required env var '{name}' is missing locally and not available in GSM"
            )
