"""Time parsing, timezone conversion, andDiscord Snowflake utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import InvalidArgumentsError

DISCORD_EPOCH_MS = 1420070400000
JST_FIXED = timezone(timedelta(hours=9), name="Asia/Tokyo")

_TZ_ALIASES = {
    "JST": "Asia/Tokyo",
    "ASIA/TOKYO": "Asia/Tokyo",
    "UTC": "UTC",
}


def resolve_timezone(tz_name: str) -> tuple[tzinfo, str]:
    """Return a tzinfo instance and normalized timezone label."""
    cleaned = tz_name.strip()
    if not cleaned:
        raise InvalidArgumentsError("Timezone name cannot be empty.")

    canonical = _TZ_ALIASES.get(cleaned.upper(), cleaned)
    if canonical == "UTC":
        return timezone.utc, "UTC"

    try:
        return ZoneInfo(canonical), canonical
    except ZoneInfoNotFoundError as exc:
        if canonical == "Asia/Tokyo":
            return JST_FIXED, "Asia/Tokyo"
        raise InvalidArgumentsError(f"Unknown timezone: {tz_name}") from exc


def parse_user_datetime(value: str, default_tz: tzinfo) -> datetime:
    """Parse an ISO-8601 date or datetime string into a timezone-aware datetime.

    If `value` is date-only (YYYY-MM-DD), 00:00:00 in `default_tz` is assumed.
    If `value` has no offset, `default_tz` is attached.
    """
    text = value.strip()
    if not text:
        raise InvalidArgumentsError("Datetime value cannot be empty.")

    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            dt = datetime.strptime(text, "%Y-%m-%d")
            return dt.replace(tzinfo=default_tz)
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidArgumentsError(
            f"Invalid date/datetime format '{value}'. Expected ISO 8601 (e.g. 2026-11-18T00:00:00+09:00 or 2026-11-18)."
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(default_tz)


def parse_discord_timestamp(value: str) -> datetime:
    """Parse a Discord API message timestamp into an aware UTC datetime."""
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid Discord timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_window(
    *,
    since: str | None,
    until: str | None,
    hours: float | None,
    tz: tzinfo,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Resolve the [start, end) aggregation window in the target timezone."""
    if hours is not None and (since is not None or until is not None):
        raise InvalidArgumentsError("--hours cannot be combined with --since or --until.")
    if hours is not None and hours <= 0:
        raise InvalidArgumentsError("--hours must be greater than 0.")

    current = (now if now is not None else datetime.now(timezone.utc)).astimezone(tz)

    if until is not None:
        end_dt = parse_user_datetime(until, tz)
    else:
        end_dt = current

    if since is not None:
        start_dt = parse_user_datetime(since, tz)
    else:
        span_hours = hours if hours is not None else 24.0
        start_dt = end_dt - timedelta(hours=span_hours)

    if start_dt >= end_dt:
        raise InvalidArgumentsError(
            f"Start time ({start_dt.isoformat()}) must be earlier than end time ({end_dt.isoformat()})."
        )

    return start_dt, end_dt


def datetime_to_snowflake(dt: datetime) -> str:
    """Convert a datetime to a synthetic Discord Snowflake ID (lower bound at that millisecond)."""
    utc_dt = dt.astimezone(timezone.utc)
    unix_ms = int(utc_dt.timestamp() * 1000)
    if unix_ms <= DISCORD_EPOCH_MS:
        return "0"
    snowflake = (unix_ms - DISCORD_EPOCH_MS) << 22
    return str(snowflake)
