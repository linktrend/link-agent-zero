import json

import pytest

from python.helpers.fasta2a_server import AgentZeroWorker
from python.helpers import linktrend_audit


class _DummyStorage:
    async def update_task(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_worker_exits_on_tenant_mismatch_before_execution(monkeypatch, tmp_path) -> None:
    identity_path = tmp_path / "IDENTITY.md"
    identity_path.write_text(
        "authorized_tenant_id: tenant-allowed\n"
        "dpr_id: INT-EXE-260311-1A2B-WORKER\n",
        encoding="utf-8",
    )

    payload = {
        "dpr_id": "INT-EXE-260311-0001-TEST",
        "run_id": "run-001",
        "task_id": "task-001",
        "tenant_id": "tenant-other",
        "agent_id": "A0",
    }

    monkeypatch.setenv("LINKTREND_IDENTITY_PATH", str(identity_path))
    monkeypatch.setenv("AIOS_TASK_PAYLOAD", json.dumps(payload))

    # Keep the test isolated from external audit infrastructure.
    async def _noop_log_event(*args, **kwargs):
        return None

    monkeypatch.setattr("python.helpers.linktrend_audit.log_event", _noop_log_event)

    exit_calls: list[int] = []
    ensure_calls: list[int] = []
    init_calls: list[int] = []

    def _fake_exit(code: int):
        exit_calls.append(code)
        raise SystemExit(code)

    async def _fake_ensure_required_env_vars(*args, **kwargs):
        ensure_calls.append(1)
        return None

    def _fake_initialize_agent(*args, **kwargs):
        init_calls.append(1)
        return object()

    monkeypatch.setattr("python.helpers.linktrend_audit.os._exit", _fake_exit)
    monkeypatch.setattr(
        "python.helpers.fasta2a_server.linktrend_audit.ensure_required_env_vars",
        _fake_ensure_required_env_vars,
    )
    monkeypatch.setattr("python.helpers.fasta2a_server.initialize_agent", _fake_initialize_agent)

    worker = AgentZeroWorker(broker=None, storage=_DummyStorage())

    params = {
        "id": "task-001",
        "message": {"parts": [{"kind": "text", "text": "hello"}]},
    }

    with pytest.raises(SystemExit) as exc:
        await worker.run_task(params)

    assert exc.value.code == 1
    assert exit_calls == [1]
    assert ensure_calls == []
    assert init_calls == []


@pytest.mark.asyncio
async def test_telemetry_uses_identity_dpr_for_agent_id(monkeypatch, tmp_path) -> None:
    identity_path = tmp_path / "IDENTITY.md"
    identity_dpr = "INT-EXE-260311-AB12-WORKER"
    identity_path.write_text(
        "authorized_tenant_id: tenant-allowed\n"
        f"dpr_id: {identity_dpr}\n",
        encoding="utf-8",
    )

    payload = {
        "dpr_id": "INT-EXE-260311-0001-PAYLOAD",
        "run_id": "run-telemetry-1",
        "task_id": "task-telemetry-1",
        "tenant_id": "tenant-allowed",
        "agent_id": "A0",
    }

    monkeypatch.setenv("LINKTREND_IDENTITY_PATH", str(identity_path))
    monkeypatch.setenv("AIOS_TASK_PAYLOAD", json.dumps(payload))
    monkeypatch.setenv("LINKTREND_AUDIT_STRICT", "true")

    calls: list[dict] = []

    def _fake_set_context(_supabase, _tenant_id):
        return None

    def _fake_log_audit_run(_supabase, run_id, task_id, agent_id, status):
        calls.append(
            {
                "p_run_id": run_id,
                "p_task_id": task_id,
                "p_agent_id": agent_id,
                "p_status": status,
            }
        )
        return None

    monkeypatch.setattr(linktrend_audit._AUDIT_CLIENT, "_get_client", lambda: object())
    monkeypatch.setattr(linktrend_audit, "set_context", _fake_set_context)
    monkeypatch.setattr(linktrend_audit, "log_audit_run", _fake_log_audit_run)

    await linktrend_audit.enforce_identity_gate_or_exit(task_id="task-telemetry-1")
    await linktrend_audit.log_terminal_command("echo ok", session=0, runtime="terminal")

    assert calls, "Expected at least one audit RPC call"
    assert all(call["p_agent_id"] == identity_dpr for call in calls)
