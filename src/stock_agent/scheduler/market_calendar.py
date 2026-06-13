"""US equity market calendar utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketDay:
    date: date
    timezone: str
    is_trading_day: bool
    is_weekend: bool
    is_holiday: bool
    is_half_day: bool
    holiday_name: str | None
    open_at: datetime | None
    close_at: datetime | None


class USMarketCalendar:
    """Rule-based NYSE/Nasdaq calendar with half-day support."""

    def __init__(
        self,
        *,
        timezone: str = "America/New_York",
        regular_open: time = time(9, 30),
        regular_close: time = time(16, 0),
        half_day_close: time = time(13, 0),
    ) -> None:
        self.timezone = timezone
        self.zone = ZoneInfo(timezone)
        self.regular_open = regular_open
        self.regular_close = regular_close
        self.half_day_close = half_day_close

    def market_day(self, target_date: date) -> MarketDay:
        is_weekend = target_date.weekday() >= 5
        holiday_name = market_holidays(target_date.year).get(target_date)
        half_day_name = market_half_days(target_date.year).get(target_date)
        is_trading_day = not is_weekend and holiday_name is None
        close_time = self.half_day_close if half_day_name and is_trading_day else self.regular_close

        return MarketDay(
            date=target_date,
            timezone=self.timezone,
            is_trading_day=is_trading_day,
            is_weekend=is_weekend,
            is_holiday=holiday_name is not None,
            is_half_day=half_day_name is not None and is_trading_day,
            holiday_name=holiday_name or half_day_name,
            open_at=_local_datetime(target_date, self.regular_open, self.zone) if is_trading_day else None,
            close_at=_local_datetime(target_date, close_time, self.zone) if is_trading_day else None,
        )

    def next_trading_day(self, start_date: date, *, include_start: bool = False) -> MarketDay:
        current = start_date if include_start else start_date + timedelta(days=1)
        for _ in range(370):
            market_day = self.market_day(current)
            if market_day.is_trading_day:
                return market_day
            current += timedelta(days=1)
        raise ValueError(f"could not find next trading day after {start_date}")


def market_holidays(year: int) -> dict[date, str]:
    holidays = {
        _observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents Day",
        _good_friday(year): "Good Friday",
        _last_weekday(year, 5, 0): "Memorial Day",
        _observed(date(year, 6, 19)): "Juneteenth National Independence Day",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    return {day: name for day, name in holidays.items() if day.year == year}


def market_half_days(year: int) -> dict[date, str]:
    holidays = market_holidays(year)
    half_days: dict[date, str] = {}
    independence_eve = _previous_weekday(_observed(date(year, 7, 4)))
    if independence_eve.year == year and independence_eve not in holidays:
        half_days[independence_eve] = "Independence Day early close"

    black_friday = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
    if black_friday.weekday() < 5 and black_friday not in holidays:
        half_days[black_friday] = "Day After Thanksgiving early close"

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5 and christmas_eve not in holidays:
        half_days[christmas_eve] = "Christmas Eve early close"
    return half_days


def _local_datetime(day: date, value: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, value, tzinfo=zone)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _previous_weekday(day: date) -> date:
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _good_friday(year: int) -> date:
    return _easter_sunday(year) - timedelta(days=2)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


__all__ = ["MarketDay", "USMarketCalendar", "market_half_days", "market_holidays"]
