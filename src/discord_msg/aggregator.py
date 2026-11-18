"""Message filtering and time-bucket aggregation without exposing message content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Literal

from .client import MessageRecord
from .errors import InvalidArgumentsError

Granularity = Literal["hour", "day", "total"]


@dataclass(frozen=True)
class MessageFilter:
    prefix: str | None = None
    keyword: str | None = None
    ignore_case: bool = False

    def matches(self, content: str) -> bool:
        target = content.lower() if self.ignore_case else content

        if self.prefix is not None:
            pref = self.prefix.lower() if self.ignore_case else self.prefix
            if not target.startswith(pref):
                return False

        if self.keyword is not None:
            kw = self.keyword.lower() if self.ignore_case else self.keyword
            if kw not in target:
                return False

        return True


@dataclass(frozen=True)
class BucketCount:
    label: str
    start: datetime
    end: datetime
    count: int


@dataclass(frozen=True)
class AggregationReport:
    channel_id: str
    timezone_name: str
    start: datetime
    end: datetime
    granularity: Granularity
    msg_filter: MessageFilter
    buckets: list[BucketCount]
    total_matched: int
    total_in_window: int


def generate_buckets(
    start: datetime,
    end: datetime,
    granularity: Granularity,
    tz: tzinfo,
) -> list[tuple[str, datetime, datetime]]:
    """Generate contiguous [bucket_start, bucket_end) intervals covering [start, end) in `tz`."""
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)

    if granularity == "total":
        label = "total"
        return [(label, local_start, local_end)]

    if granularity == "hour":
        cursor = local_start.replace(minute=0, second=0, microsecond=0)
        intervals: list[tuple[str, datetime, datetime]] = []
        while cursor < local_end:
            next_cursor = cursor + timedelta(hours=1)
            b_start = max(cursor, local_start)
            b_end = min(next_cursor, local_end)
            if b_start < b_end:
                label = cursor.strftime("%Y-%m-%d %H:00")
                intervals.append((label, b_start, b_end))
            cursor = next_cursor
        return intervals

    if granularity == "day":
        cursor = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
        intervals = []
        while cursor < local_end:
            next_cursor = cursor + timedelta(days=1)
            b_start = max(cursor, local_start)
            b_end = min(next_cursor, local_end)
            if b_start < b_end:
                label = cursor.strftime("%Y-%m-%d")
                intervals.append((label, b_start, b_end))
            cursor = next_cursor
        return intervals

    raise InvalidArgumentsError(f"Unsupported granularity: {granularity}")


def aggregate_messages(
    *,
    channel_id: str,
    messages: list[MessageRecord],
    start: datetime,
    end: datetime,
    tz: tzinfo,
    timezone_name: str,
    granularity: Granularity = "hour",
    msg_filter: MessageFilter | None = None,
) -> AggregationReport:
    """Filter messages in [start, end) and count them per bucket."""
    active_filter = msg_filter if msg_filter is not None else MessageFilter()
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)

    in_window = [m for m in messages if local_start <= m.timestamp.astimezone(tz) < local_end]
    matched = [m for m in in_window if active_filter.matches(m.content)]

    raw_intervals = generate_buckets(local_start, local_end, granularity, tz)
    counts = [0] * len(raw_intervals)

    if granularity == "total":
        if counts:
            counts[0] = len(matched)
    else:
        for msg in matched:
            local_ts = msg.timestamp.astimezone(tz)
            for idx, (_, b_start, b_end) in enumerate(raw_intervals):
                if b_start <= local_ts < b_end:
                    counts[idx] += 1
                    break

    buckets = [
        BucketCount(label=label, start=b_start, end=b_end, count=counts[idx])
        for idx, (label, b_start, b_end) in enumerate(raw_intervals)
    ]

    return AggregationReport(
        channel_id=channel_id,
        timezone_name=timezone_name,
        start=local_start,
        end=local_end,
        granularity=granularity,
        msg_filter=active_filter,
        buckets=buckets,
        total_matched=len(matched),
        total_in_window=len(in_window),
    )
