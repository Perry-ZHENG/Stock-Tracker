"""Lease-based scheduler that submits research only after a material change."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.research.subscriptions import EvidenceSnapshot, ResearchSubscription, SubscriptionRepository, SubscriptionRun
from stock_agent.scheduler.market_calendar import USMarketCalendar

SubscriptionSnapshotProvider = Callable[[ResearchSubscription], EvidenceSnapshot]
SubscriptionTaskSubmitter = Callable[[ResearchSubscription, EvidenceSnapshot], str]


class ResearchSubscriptionScheduler:
    """A separate research scheduler; its slow tasks never run in WorkerPipeline.tick."""

    def __init__(
        self,
        *,
        repository: SubscriptionRepository,
        snapshot_provider: SubscriptionSnapshotProvider,
        task_submitter: SubscriptionTaskSubmitter,
        calendar_factory: Callable[[str], USMarketCalendar] = USMarketCalendar,
        lease_seconds: int = 120,
    ) -> None:
        self.repository = repository
        self.snapshot_provider = snapshot_provider
        self.task_submitter = task_submitter
        self.calendar_factory = calendar_factory
        self.lease_seconds = lease_seconds

    def tick(self, *, owner: str, now: datetime) -> SubscriptionRun | None:
        claimed = self.repository.claim_due(owner=owner, now=now, lease_seconds=self.lease_seconds)
        if claimed is None:
            return None
        subscription, run = claimed
        try:
            snapshot = self.snapshot_provider(subscription)
            fingerprint = snapshot.fingerprint()
            next_run = _next_run(subscription, now)
            if fingerprint == subscription.last_fingerprint:
                self.repository.finish(subscription, run, status="no_change", next_run_at=next_run, fingerprint=fingerprint, now=now)
                return run.model_copy(update={"status": "no_change", "fingerprint": fingerprint, "updated_at": now})
            task_id = self.task_submitter(subscription, snapshot)
            self.repository.finish(subscription, run, status="submitted", next_run_at=next_run, fingerprint=fingerprint, task_id=task_id, now=now)
            return run.model_copy(update={"status": "submitted", "task_id": task_id, "fingerprint": fingerprint, "updated_at": now})
        except Exception as exc:
            retry_at = now + timedelta(seconds=min(3600, 60 * 2 ** min(subscription.failure_count, 6)))
            self.repository.finish(subscription, run, status="failed", next_run_at=retry_at, fingerprint="", error=str(exc)[:500], now=now)
            return run.model_copy(update={"status": "failed", "error": "subscription execution failed", "updated_at": now})


def request_for_subscription(subscription: ResearchSubscription, *, now: datetime) -> ResearchRequest:
    """Build a new independent task request; previous references stay in the run audit row."""

    return ResearchRequest(
        request_id=f"request-{subscription.subscription_id}-{int(now.timestamp())}",
        question=subscription.target,
        symbols=subscription.symbols,
        time_window=subscription.time_window,
        report_type=subscription.report_type,
    )


def _next_run(subscription: ResearchSubscription, now: datetime) -> datetime:
    zone = ZoneInfo(subscription.schedule.timezone)
    local_now = now.astimezone(zone)
    candidate = local_now + timedelta(minutes=subscription.schedule.interval_minutes)
    calendar = USMarketCalendar(timezone=subscription.schedule.timezone)
    day = calendar.market_day(candidate.date())
    if not day.is_trading_day:
        assert calendar.next_trading_day(candidate.date()).open_at is not None
        return calendar.next_trading_day(candidate.date()).open_at.astimezone(UTC)
    return candidate.astimezone(UTC)


__all__ = ["ResearchSubscriptionScheduler", "request_for_subscription"]
