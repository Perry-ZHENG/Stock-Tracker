"""Durable subscriptions and change fingerprints for continuous research."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from stock_agent.contracts.common import StrictSchema, TimeWindow, ensure_utc
from stock_agent.contracts.tasks import ReportType

SubscriptionStatus = Literal["active", "paused", "deleted"]
SubscriptionRunStatus = Literal["leased", "no_change", "submitted", "failed"]


class SubscriptionSchedule(StrictSchema):
    interval_minutes: int = Field(default=60, ge=5, le=10_080)
    timezone: str = Field(default="America/New_York", min_length=1)


class ResearchSubscription(StrictSchema):
    subscription_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    time_window: TimeWindow
    report_type: ReportType = "full"
    schedule: SubscriptionSchedule = Field(default_factory=SubscriptionSchedule)
    evidence_freshness_seconds: int = Field(default=900, ge=0)
    notification_channels: list[str] = Field(default_factory=list)
    status: SubscriptionStatus = "active"
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_fingerprint: str | None = None
    last_report_id: str | None = None
    failure_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("next_run_at", "last_run_at", "created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class EvidenceSnapshot(StrictSchema):
    evidence_ids: list[str] = Field(default_factory=list)
    signal_hashes: list[str] = Field(default_factory=list)
    analysis_ids: list[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SubscriptionRun(StrictSchema):
    run_id: str
    subscription_id: str
    scheduled_for: datetime
    status: SubscriptionRunStatus
    task_id: str | None = None
    fingerprint: str | None = None
    previous_report_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("scheduled_for", "created_at", "updated_at")
    @classmethod
    def _run_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class SubscriptionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, subscription: ResearchSubscription) -> None:
        payload = subscription.model_dump(mode="json")
        self.connection.execute(
            """INSERT INTO research_subscriptions (subscription_id,payload_json,status,next_run_at,last_run_at,last_fingerprint,last_report_id,failure_count,lease_owner,lease_until,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?)
            ON CONFLICT(subscription_id) DO UPDATE SET payload_json=excluded.payload_json,status=excluded.status,next_run_at=excluded.next_run_at,last_run_at=excluded.last_run_at,last_fingerprint=excluded.last_fingerprint,last_report_id=excluded.last_report_id,failure_count=excluded.failure_count,updated_at=excluded.updated_at""",
            (subscription.subscription_id, _json(payload), subscription.status, payload["next_run_at"], payload["last_run_at"], subscription.last_fingerprint, subscription.last_report_id, subscription.failure_count, payload["created_at"], payload["updated_at"]),
        )
        self.connection.commit()

    def get(self, subscription_id: str) -> ResearchSubscription | None:
        row = self.connection.execute("SELECT payload_json FROM research_subscriptions WHERE subscription_id = ?", (subscription_id,)).fetchone()
        return ResearchSubscription.model_validate_json(row["payload_json"]) if row else None

    def claim_due(self, *, owner: str, now: datetime, lease_seconds: int) -> tuple[ResearchSubscription, SubscriptionRun] | None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("""SELECT * FROM research_subscriptions WHERE status='active' AND next_run_at <= ? AND (lease_until IS NULL OR lease_until < ?) ORDER BY next_run_at LIMIT 1""", (_ts(now), _ts(now))).fetchone()
            if row is None:
                self.connection.commit()
                return None
            subscription = ResearchSubscription.model_validate_json(row["payload_json"])
            scheduled_for = subscription.next_run_at
            key = hashlib.sha256(f"{subscription.subscription_id}|{_ts(scheduled_for)}".encode()).hexdigest()
            existing = self.connection.execute("SELECT * FROM research_subscription_runs WHERE idempotency_key = ?", (key,)).fetchone()
            if existing is not None:
                self.connection.execute("UPDATE research_subscriptions SET next_run_at=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE subscription_id=?", (_ts(now), _ts(now), subscription.subscription_id))
                self.connection.commit()
                return None
            run = SubscriptionRun(run_id=f"subscription-run-{uuid4().hex}", subscription_id=subscription.subscription_id, scheduled_for=scheduled_for, status="leased", previous_report_id=subscription.last_report_id, created_at=now, updated_at=now)
            lease_until = now.timestamp() + lease_seconds
            self.connection.execute("UPDATE research_subscriptions SET lease_owner=?, lease_until=?, updated_at=? WHERE subscription_id=?", (owner, _ts(datetime.fromtimestamp(lease_until, UTC)), _ts(now), subscription.subscription_id))
            payload = run.model_dump(mode="json")
            self.connection.execute("INSERT INTO research_subscription_runs (run_id,subscription_id,scheduled_for,idempotency_key,status,task_id,fingerprint,previous_report_id,error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (run.run_id, run.subscription_id, payload["scheduled_for"], key, run.status, None, None, run.previous_report_id, None, payload["created_at"], payload["updated_at"]))
            self.connection.commit()
            return subscription, run
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def finish(self, subscription: ResearchSubscription, run: SubscriptionRun, *, status: SubscriptionRunStatus, next_run_at: datetime, fingerprint: str, task_id: str | None = None, error: str | None = None, now: datetime) -> None:
        failures = subscription.failure_count + 1 if status == "failed" else 0
        updated = subscription.model_copy(update={"next_run_at": next_run_at, "last_run_at": now, "last_fingerprint": fingerprint if status != "failed" else subscription.last_fingerprint, "failure_count": failures, "updated_at": now})
        payload = updated.model_dump(mode="json")
        self.connection.execute("UPDATE research_subscriptions SET payload_json=?,next_run_at=?,last_run_at=?,last_fingerprint=?,failure_count=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE subscription_id=?", (_json(payload), payload["next_run_at"], payload["last_run_at"], updated.last_fingerprint, failures, payload["updated_at"], subscription.subscription_id))
        self.connection.execute("UPDATE research_subscription_runs SET status=?,task_id=?,fingerprint=?,error=?,updated_at=? WHERE run_id=?", (status, task_id, fingerprint, error, _ts(now), run.run_id))
        self.connection.commit()

    def set_status(self, subscription_id: str, status: SubscriptionStatus, *, now: datetime) -> ResearchSubscription:
        current = self.get(subscription_id)
        if current is None:
            raise ValueError("subscription does not exist")
        updated = current.model_copy(update={"status": status, "updated_at": now})
        self.save(updated)
        return updated


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["EvidenceSnapshot", "ResearchSubscription", "SubscriptionRepository", "SubscriptionRun", "SubscriptionSchedule"]
