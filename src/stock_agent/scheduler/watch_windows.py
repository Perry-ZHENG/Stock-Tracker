"""Build market watch windows from calendar and runtime config."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from stock_agent.config import ScheduleConfig
from stock_agent.scheduler.market_calendar import MarketDay, USMarketCalendar

WindowKind = Literal["premarket", "regular", "close_focus", "afterhours", "closed"]


@dataclass(frozen=True)
class WatchWindow:
    kind: WindowKind
    start_at: datetime
    end_at: datetime
    strategy_enabled: bool
    note: str

    @property
    def start_utc(self) -> datetime:
        return self.start_at.astimezone(UTC)

    @property
    def end_utc(self) -> datetime:
        return self.end_at.astimezone(UTC)


@dataclass(frozen=True)
class WatchSchedule:
    market_day: MarketDay
    next_trading_day: MarketDay | None
    windows: list[WatchWindow]


def build_watch_schedule(
    *,
    config: ScheduleConfig,
    target_date: date,
) -> WatchSchedule:
    calendar = USMarketCalendar(
        timezone=config.timezone,
        regular_open=_parse_hhmm(config.regular_session_start),
        regular_close=_parse_hhmm(config.regular_session_end),
    )
    market_day = calendar.market_day(target_date)
    if not market_day.is_trading_day:
        next_day = calendar.next_trading_day(target_date)
        return WatchSchedule(
            market_day=market_day,
            next_trading_day=next_day,
            windows=[
                WatchWindow(
                    kind="closed",
                    start_at=_local_midnight(target_date, config.timezone),
                    end_at=_local_midnight(target_date + timedelta(days=1), config.timezone),
                    strategy_enabled=False,
                    note=market_day.holiday_name or "Market closed",
                )
            ],
        )

    assert market_day.open_at is not None
    assert market_day.close_at is not None
    windows: list[WatchWindow] = []
    if config.premarket_lead_minutes > 0:
        windows.append(
            WatchWindow(
                kind="premarket",
                start_at=market_day.open_at - timedelta(minutes=config.premarket_lead_minutes),
                end_at=market_day.open_at,
                strategy_enabled=False,
                note="display_only",
            )
        )

    windows.append(
        WatchWindow(
            kind="regular",
            start_at=market_day.open_at,
            end_at=market_day.close_at,
            strategy_enabled=True,
            note="strategy_window",
        )
    )

    if config.close_focus_window_minutes > 0:
        close_focus_start = max(
            market_day.open_at,
            market_day.close_at - timedelta(minutes=config.close_focus_window_minutes),
        )
        windows.append(
            WatchWindow(
                kind="close_focus",
                start_at=close_focus_start,
                end_at=market_day.close_at,
                strategy_enabled=True,
                note="strategy_window_close_focus",
            )
        )

    if config.afterhours_tail_minutes > 0:
        windows.append(
            WatchWindow(
                kind="afterhours",
                start_at=market_day.close_at,
                end_at=market_day.close_at + timedelta(minutes=config.afterhours_tail_minutes),
                strategy_enabled=False,
                note="display_only",
            )
        )

    return WatchSchedule(
        market_day=market_day,
        next_trading_day=calendar.next_trading_day(target_date),
        windows=windows,
    )


def _parse_hhmm(value: str) -> time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return time(int(hour_text), int(minute_text))
    except ValueError as exc:
        raise ValueError(f"invalid HH:MM time: {value}") from exc


def _local_midnight(target_date: date, timezone: str) -> datetime:
    return datetime.combine(target_date, time(0, 0), tzinfo=ZoneInfo(timezone))


__all__ = ["WatchSchedule", "WatchWindow", "build_watch_schedule"]
