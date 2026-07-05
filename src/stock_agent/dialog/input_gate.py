"""Persistent single-input coordination for interactive interfaces."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4

InputSource = Literal["cli", "telegram", "fastapi"]
SwitchStatus = Literal["pending", "approved", "rejected", "expired"]
NowFn = Callable[[], datetime]

if TYPE_CHECKING:
    from stock_agent.config import InputControlConfig

REQUEST_TTL = timedelta(minutes=10)
ONLINE_TIMEOUTS: dict[InputSource, timedelta] = {
    "cli": timedelta(seconds=45),
    "fastapi": timedelta(seconds=45),
    "telegram": timedelta(seconds=120),
}


class InputGateError(ValueError):
    """Raised when an input-control transition is not allowed."""


@dataclass(frozen=True)
class InputDecision:
    allowed: bool
    source: InputSource
    active_source: InputSource | None
    message: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class InputSwitchRequest:
    request_id: str
    from_source: InputSource
    to_source: InputSource
    requested_by: str
    status: SwitchStatus
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("created_at", "expires_at", "decided_at"):
            value = payload[key]
            payload[key] = _dump_datetime(value) if isinstance(value, datetime) else None
        return payload


@dataclass(frozen=True)
class InputControlState:
    active_source: InputSource | None
    active_actor_ref: str | None
    activated_at: datetime | None
    updated_at: datetime | None
    active_online: bool
    pending_requests: list[InputSwitchRequest]

    def as_dict(self) -> dict[str, object]:
        return {
            "active_source": self.active_source,
            "activated_at": _dump_datetime(self.activated_at) if self.activated_at else None,
            "updated_at": _dump_datetime(self.updated_at) if self.updated_at else None,
            "active_online": self.active_online,
            "pending_requests": [item.as_dict() for item in self.pending_requests],
        }


class InputGate:
    """Coordinate one active command source across CLI, Telegram, and FastAPI."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        now_fn: NowFn | None = None,
        request_ttl_sec: int = 600,
        online_timeout_sec: dict[InputSource, int] | None = None,
    ) -> None:
        self.connection = connection
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.request_ttl = timedelta(seconds=request_ttl_sec)
        self.online_timeouts = (
            {
                source: timedelta(seconds=seconds)
                for source, seconds in online_timeout_sec.items()
            }
            if online_timeout_sec is not None
            else ONLINE_TIMEOUTS
        )

    @classmethod
    def from_config(
        cls,
        connection: sqlite3.Connection,
        config: InputControlConfig,
        *,
        now_fn: NowFn | None = None,
    ) -> "InputGate":
        return cls(
            connection,
            now_fn=now_fn,
            request_ttl_sec=config.request_ttl_sec,
            online_timeout_sec={
                "cli": config.cli_online_timeout_sec,
                "fastapi": config.fastapi_online_timeout_sec,
                "telegram": config.telegram_online_timeout_sec,
            },
        )

    def check(self, source: InputSource, *, actor_ref: str) -> InputDecision:
        """Touch the source and allow it only when it owns the global input."""
        now = self._now()
        self._expire_requests(now)
        self.heartbeat(source, actor_ref=actor_ref, now=now)
        row = self._state_row()
        if row is None:
            timestamp = _dump_datetime(now)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO input_control_state (
                    singleton_id, active_source, active_actor_ref, activated_at, updated_at
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (source, actor_ref, timestamp, timestamp),
            )
            self.connection.commit()
            row = self._state_row()
            assert row is not None
        active_source = _source(row["active_source"])
        if active_source == source:
            return InputDecision(
                allowed=True,
                source=source,
                active_source=active_source,
                message=(
                    f"当前输入接口已设置为 {source}"
                    if row["active_actor_ref"] == actor_ref and row["activated_at"] == _dump_datetime(now)
                    else f"当前允许 {source} 输入"
                ),
            )
        return InputDecision(
            allowed=False,
            source=source,
            active_source=active_source,
            message=f"当前仅允许 {active_source} 作为输入，是否申请调整至 {source} 接口？",
        )

    def heartbeat(
        self,
        source: InputSource,
        *,
        actor_ref: str,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or self._now()
        self.connection.execute(
            """
            INSERT INTO input_interface_presence (source, actor_ref, online, last_seen_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(source) DO UPDATE SET
                actor_ref = excluded.actor_ref,
                online = 1,
                last_seen_at = excluded.last_seen_at
            """,
            (source, actor_ref, _dump_datetime(timestamp)),
        )
        self.connection.commit()

    def mark_offline(self, source: InputSource, *, actor_ref: str | None = None) -> None:
        if actor_ref is None:
            self.connection.execute(
                "UPDATE input_interface_presence SET online = 0 WHERE source = ?",
                (source,),
            )
        else:
            self.connection.execute(
                """
                UPDATE input_interface_presence
                SET online = 0
                WHERE source = ? AND actor_ref = ?
                """,
                (source, actor_ref),
            )
        self.connection.commit()

    def request_switch(
        self,
        source: InputSource,
        *,
        actor_ref: str,
    ) -> InputSwitchRequest:
        now = self._now()
        self._expire_requests(now)
        self.heartbeat(source, actor_ref=actor_ref, now=now)
        state = self._state_row()
        if state is None:
            self._activate(source, actor_ref=actor_ref, now=now)
            raise InputGateError(f"{source} 已成为当前输入接口，无需申请切换")
        active_source = _source(state["active_source"])
        if active_source == source:
            raise InputGateError(f"{source} 已经是当前输入接口")
        if not self.is_online(active_source, now=now):
            raise InputGateError(f"当前输入接口 {active_source} 离线，无法发起切换")

        existing = self.connection.execute(
            """
            SELECT * FROM input_switch_requests
            WHERE from_source = ? AND to_source = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (active_source, source),
        ).fetchone()
        if existing is not None:
            return _switch_from_row(existing)

        request = InputSwitchRequest(
            request_id=f"switch-{uuid4().hex[:12]}",
            from_source=active_source,
            to_source=source,
            requested_by=actor_ref,
            status="pending",
            created_at=now,
            expires_at=now + self.request_ttl,
        )
        self.connection.execute(
            """
            INSERT INTO input_switch_requests (
                request_id, from_source, to_source, requested_by, status,
                created_at, expires_at, decided_at, decided_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                request.request_id,
                request.from_source,
                request.to_source,
                request.requested_by,
                request.status,
                _dump_datetime(request.created_at),
                _dump_datetime(request.expires_at),
            ),
        )
        self.connection.commit()
        return request

    def decide(
        self,
        request_id: str,
        *,
        source: InputSource,
        actor_ref: str,
        approve: bool,
    ) -> InputSwitchRequest:
        now = self._now()
        self._expire_requests(now)
        self.heartbeat(source, actor_ref=actor_ref, now=now)
        row = self.connection.execute(
            "SELECT * FROM input_switch_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise InputGateError(f"切换请求不存在: {request_id}")
        request = _switch_from_row(row)
        if request.status != "pending":
            raise InputGateError(f"切换请求状态不是 pending: {request.status}")
        state = self._state_row()
        active_source = _source(state["active_source"]) if state is not None else None
        if source != request.from_source or source != active_source:
            raise InputGateError("只有当前原输入接口可以审批切换请求")
        if not self.is_online(source, now=now):
            raise InputGateError(f"当前输入接口 {source} 离线，无法审批")

        status: SwitchStatus = "approved" if approve else "rejected"
        self.connection.execute(
            """
            UPDATE input_switch_requests
            SET status = ?, decided_at = ?, decided_by = ?
            WHERE request_id = ?
            """,
            (status, _dump_datetime(now), actor_ref, request_id),
        )
        if approve:
            self._activate(request.to_source, actor_ref=request.requested_by, now=now)
            self.connection.execute(
                """
                UPDATE input_switch_requests
                SET status = 'rejected', decided_at = ?, decided_by = ?
                WHERE status = 'pending' AND request_id <> ?
                """,
                (_dump_datetime(now), "system:superseded", request_id),
            )
        self.connection.commit()
        updated = self.connection.execute(
            "SELECT * FROM input_switch_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        assert updated is not None
        return _switch_from_row(updated)

    def pending_for(self, source: InputSource) -> list[InputSwitchRequest]:
        self._expire_requests(self._now())
        rows = self.connection.execute(
            """
            SELECT * FROM input_switch_requests
            WHERE from_source = ? AND status = 'pending'
            ORDER BY created_at
            """,
            (source,),
        ).fetchall()
        return [_switch_from_row(row) for row in rows]

    def get_request(self, request_id: str) -> InputSwitchRequest | None:
        self._expire_requests(self._now())
        row = self.connection.execute(
            "SELECT * FROM input_switch_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return _switch_from_row(row) if row is not None else None

    def state(self) -> InputControlState:
        now = self._now()
        self._expire_requests(now)
        row = self._state_row()
        pending_rows = self.connection.execute(
            """
            SELECT * FROM input_switch_requests
            WHERE status = 'pending'
            ORDER BY created_at
            """
        ).fetchall()
        if row is None:
            return InputControlState(
                active_source=None,
                active_actor_ref=None,
                activated_at=None,
                updated_at=None,
                active_online=False,
                pending_requests=[_switch_from_row(item) for item in pending_rows],
            )
        active_source = _source(row["active_source"])
        return InputControlState(
            active_source=active_source,
            active_actor_ref=row["active_actor_ref"],
            activated_at=_load_datetime(row["activated_at"]),
            updated_at=_load_datetime(row["updated_at"]),
            active_online=self.is_online(active_source, now=now),
            pending_requests=[_switch_from_row(item) for item in pending_rows],
        )

    def is_online(self, source: InputSource, *, now: datetime | None = None) -> bool:
        row = self.connection.execute(
            "SELECT online, last_seen_at FROM input_interface_presence WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None or not bool(row["online"]):
            return False
        last_seen = _load_datetime(row["last_seen_at"])
        return (now or self._now()) - last_seen <= self.online_timeouts[source]

    def _activate(self, source: InputSource, *, actor_ref: str, now: datetime) -> None:
        timestamp = _dump_datetime(now)
        self.connection.execute(
            """
            INSERT INTO input_control_state (
                singleton_id, active_source, active_actor_ref, activated_at, updated_at
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                active_source = excluded.active_source,
                active_actor_ref = excluded.active_actor_ref,
                activated_at = excluded.activated_at,
                updated_at = excluded.updated_at
            """,
            (source, actor_ref, timestamp, timestamp),
        )
        self.connection.commit()

    def _expire_requests(self, now: datetime) -> None:
        self.connection.execute(
            """
            UPDATE input_switch_requests
            SET status = 'expired', decided_at = ?, decided_by = 'system:timeout'
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (_dump_datetime(now), _dump_datetime(now)),
        )
        self.connection.commit()

    def _state_row(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM input_control_state WHERE singleton_id = 1"
        ).fetchone()

    def _now(self) -> datetime:
        value = self.now_fn()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _source(value: str) -> InputSource:
    if value not in {"cli", "telegram", "fastapi"}:
        raise InputGateError(f"未知输入接口: {value}")
    return value  # type: ignore[return-value]


def _switch_from_row(row: sqlite3.Row) -> InputSwitchRequest:
    return InputSwitchRequest(
        request_id=row["request_id"],
        from_source=_source(row["from_source"]),
        to_source=_source(row["to_source"]),
        requested_by=row["requested_by"],
        status=row["status"],
        created_at=_load_datetime(row["created_at"]),
        expires_at=_load_datetime(row["expires_at"]),
        decided_at=_load_datetime(row["decided_at"]) if row["decided_at"] else None,
        decided_by=row["decided_by"],
    )


def _dump_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


__all__ = [
    "InputControlState",
    "InputDecision",
    "InputGate",
    "InputGateError",
    "InputSource",
    "InputSwitchRequest",
    "ONLINE_TIMEOUTS",
    "REQUEST_TTL",
]
