from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.contracts.common import TimeWindow
from stock_agent.research.scheduler import ResearchSubscriptionScheduler
from stock_agent.research.subscriptions import EvidenceSnapshot, ResearchSubscription, SubscriptionRepository
from stock_agent.storage.sqlite import initialize_database


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


def test_due_subscription_submits_once_and_no_change_skips_task(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = SubscriptionRepository(connection)
    initial = _subscription(next_run_at=NOW)
    repository.save(initial)
    submitted: list[str] = []
    snapshot = EvidenceSnapshot(evidence_ids=["evidence-1"], signal_hashes=["signal-a"])
    scheduler = ResearchSubscriptionScheduler(
        repository=repository,
        snapshot_provider=lambda _subscription: snapshot,
        task_submitter=lambda _subscription, _snapshot: submitted.append("task-subscription-1") or submitted[-1],
    )

    first = scheduler.tick(owner="scheduler-a", now=NOW)
    second = scheduler.tick(owner="scheduler-b", now=NOW)
    updated = repository.get(initial.subscription_id)

    assert first.status == "submitted"
    assert second is None
    assert submitted == ["task-subscription-1"]
    assert updated.last_fingerprint == snapshot.fingerprint()

    next_window = NOW + timedelta(minutes=5)
    repository.save(updated.model_copy(update={"next_run_at": next_window, "updated_at": next_window}))
    no_change = scheduler.tick(owner="scheduler-a", now=next_window)
    assert no_change.status == "no_change"
    assert submitted == ["task-subscription-1"]
    connection.close()


def test_subscription_pause_failure_backoff_and_restart_recovery(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = SubscriptionRepository(connection)
    subscription = _subscription(next_run_at=NOW)
    repository.save(subscription)
    failing = ResearchSubscriptionScheduler(
        repository=repository,
        snapshot_provider=lambda _subscription: (_ for _ in ()).throw(RuntimeError("source down")),
        task_submitter=lambda _subscription, _snapshot: "never",
    )

    failed = failing.tick(owner="scheduler-a", now=NOW)
    after_failure = SubscriptionRepository(connection).get(subscription.subscription_id)
    paused = repository.set_status(subscription.subscription_id, "paused", now=NOW)
    paused_tick = failing.tick(owner="scheduler-b", now=NOW + timedelta(days=1))

    assert failed.status == "failed"
    assert after_failure.failure_count == 1
    assert after_failure.next_run_at > NOW
    assert paused.status == "paused"
    assert paused_tick is None
    connection.close()


def test_non_trading_day_rolls_next_schedule_to_market_open(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = SubscriptionRepository(connection)
    subscription = _subscription(next_run_at=NOW, interval_minutes=60)
    repository.save(subscription)
    scheduler = ResearchSubscriptionScheduler(
        repository=repository,
        snapshot_provider=lambda _subscription: EvidenceSnapshot(evidence_ids=["changed"]),
        task_submitter=lambda _subscription, _snapshot: "task-weekend",
    )

    scheduler.tick(owner="scheduler", now=NOW)
    updated = repository.get(subscription.subscription_id)

    assert updated.next_run_at.weekday() == 0
    connection.close()


def _subscription(*, next_run_at: datetime, interval_minutes: int = 60) -> ResearchSubscription:
    return ResearchSubscription(
        subscription_id="subscription-1",
        target="Produce a bounded research report for QQQ.",
        symbols=["QQQ"],
        time_window=TimeWindow(from_ts=NOW - timedelta(days=1), to_ts=NOW, timezone="America/New_York"),
        next_run_at=next_run_at,
        schedule={"interval_minutes": interval_minutes, "timezone": "America/New_York"},
        created_at=NOW,
        updated_at=NOW,
    )
