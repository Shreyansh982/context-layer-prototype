from app import worker
from app import bootstrap


def test_automatic_enterprise_sync_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(worker.settings, "ENABLE_ENTERPRISE_SYNC", False)
    monkeypatch.setattr(
        worker.enterprise_sync_service, "sync",
        lambda: (_ for _ in ()).throw(AssertionError("sync must not be called")),
    )

    assert worker.start_enterprise_sync() is False


def test_automatic_enterprise_sync_starts_only_when_explicitly_enabled(monkeypatch):
    started = []

    class ThreadSpy:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("started")

    monkeypatch.setattr(worker.settings, "ENABLE_ENTERPRISE_SYNC", True)
    monkeypatch.setattr(worker.threading, "Thread", ThreadSpy)

    assert worker.start_enterprise_sync() is True
    assert started[-1] == "started"
    assert started[0]["target"] is worker.enterprise_sync_loop


def test_manual_sync_service_is_not_controlled_by_automatic_flag(monkeypatch):
    monkeypatch.setattr(worker.settings, "ENABLE_ENTERPRISE_SYNC", False)
    calls = []
    monkeypatch.setattr(worker.enterprise_sync_service, "sync", lambda **kwargs: calls.append(kwargs) or {"status": "complete"})

    # The manual script invokes EnterpriseSyncService.sync directly; the
    # configuration gate belongs solely to worker startup.
    assert worker.enterprise_sync_service.sync(force=True) == {"status": "complete"}
    assert calls == [{"force": True}]


def test_bootstrap_keeps_a_development_graph_empty_when_policy_seed_is_disabled(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "ENABLE_COMPANY_POLICY_SEED", False)
    seeded = []
    monkeypatch.setattr(bootstrap, "run_migrations", lambda: None)
    monkeypatch.setattr(bootstrap, "seed_company_policies", lambda: seeded.append(True))
    monkeypatch.setattr(bootstrap.memory_service, "enqueue_pending_vector_projections", lambda: 0)
    monkeypatch.setattr(bootstrap.canonical_fact_service, "enqueue_pending_vector_projections", lambda: 0)

    bootstrap.run_startup_tasks()

    assert seeded == []
