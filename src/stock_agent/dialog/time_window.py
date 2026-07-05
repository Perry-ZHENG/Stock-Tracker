"""Explicit time-window rules for symbol-specific market monitoring queries."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

MARKET_DYNAMIC_QUERIES = frozenset({"signals", "bars"})

_DATETIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2})?(?:[Zz]|[+-]\d{2}:\d{2})?\b"
)
_TIMEZONE_RE = re.compile(
    r"\b(?:UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)\b"
)


def requires_explicit_market_time(query: str, symbol: str | None) -> bool:
    """Return whether a query targets one instrument's changing market state."""

    return query == "bars" or (query == "signals" and bool(symbol))


def normalize_explicit_time_window(
    *,
    from_ts: str | None,
    to_ts: str | None,
    timezone_name: str | None,
) -> tuple[str, str]:
    """Validate a named-zone time range and return normalized UTC timestamps."""

    missing = [
        name
        for name, value in (
            ("from_ts", from_ts),
            ("to_ts", to_ts),
            ("timezone", timezone_name),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "symbol-specific market monitoring requires from_ts, to_ts, and timezone; "
            f"missing: {', '.join(missing)}"
        )

    assert from_ts is not None
    assert to_ts is not None
    assert timezone_name is not None
    zone = _resolve_zone(timezone_name)

    start = _parse_precise_datetime(from_ts, zone=zone, field_name="from_ts")
    end = _parse_precise_datetime(to_ts, zone=zone, field_name="to_ts")
    if end <= start:
        raise ValueError("to_ts must be later than from_ts")
    return (_to_utc_text(start), _to_utc_text(end))


def extract_explicit_time_window(text: str) -> dict[str, str] | None:
    """Extract two precise datetimes and one named timezone from natural text."""

    datetimes = _DATETIME_RE.findall(text)
    timezone_match = _TIMEZONE_RE.search(text)
    if len(datetimes) < 2 or timezone_match is None:
        return None
    timezone_name = timezone_match.group(0)
    try:
        from_ts, to_ts = normalize_explicit_time_window(
            from_ts=datetimes[0],
            to_ts=datetimes[1],
            timezone_name=timezone_name,
        )
    except ValueError:
        return None
    return {
        "from_ts": from_ts,
        "to_ts": to_ts,
        "timezone": timezone_name,
    }


def explicit_market_time_question(symbol: str | None = None) -> str:
    target = f"{symbol.upper()} 的" if symbol else "该股票或指数的"
    return (
        f"请提供{target}监控时间范围，包括开始时间、结束时间和明确的 IANA 时区。"
        "例如：2026-07-06 09:30 到 2026-07-06 16:00，America/New_York。"
    )


def _parse_precise_datetime(
    value: str,
    *,
    zone: ZoneInfo,
    field_name: str,
) -> datetime:
    text = value.strip()
    if _DATETIME_RE.fullmatch(text) is None:
        raise ValueError(
            f"{field_name} must include an explicit date and clock time, "
            "for example 2026-07-06T09:30:00"
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _to_utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _resolve_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        canonical_name = next(
            (
                candidate
                for candidate in available_timezones()
                if candidate.casefold() == timezone_name.casefold()
            ),
            None,
        )
        if canonical_name is not None:
            return ZoneInfo(canonical_name)
        raise ValueError(
            "timezone must be a valid IANA timezone, for example America/New_York"
        )


__all__ = [
    "MARKET_DYNAMIC_QUERIES",
    "explicit_market_time_question",
    "extract_explicit_time_window",
    "normalize_explicit_time_window",
    "requires_explicit_market_time",
]
