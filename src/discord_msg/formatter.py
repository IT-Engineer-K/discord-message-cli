"""Human-readable table and AI-friendly JSON output formatters."""

from __future__ import annotations

import json
from typing import Any

from .aggregator import AggregationReport
from .auth import SetupResult
from .client import (
    ChannelInfo,
    CheckResult,
    MessageItem,
    MessageListResult,
    SendResult,
    ThreadResult,
    WaitResult,
)


def format_check_json(result: CheckResult) -> str:
    payload: dict[str, Any] = {
        "ok": result.reachable and result.history_readable,
        "channel_id": result.channel_id,
        "reachable": result.reachable,
        "history_readable": result.history_readable,
        "has_messages": result.has_messages,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_check_table(result: CheckResult) -> str:
    status = "OK" if (result.reachable and result.history_readable) else "NG"
    history_state = "Readable (has messages)" if result.has_messages else "Readable (empty history)"
    lines = [
        f"Connection Check : {status}",
        f"Channel ID       : {result.channel_id}",
        f"History Access   : {history_state}",
    ]
    return "\n".join(lines)


def format_send_json(result: SendResult) -> str:
    payload: dict[str, Any] = {
        "ok": True,
        "channel_id": result.channel_id,
        "message_id": result.message_id,
        "timestamp": result.timestamp,
        "mentions_allowed": result.mentions_allowed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_send_table(result: SendResult) -> str:
    mention_state = "Allowed" if result.mentions_allowed else "Disabled (default)"
    lines = [
        "Send Status      : OK",
        f"Channel ID       : {result.channel_id}",
        f"Message ID       : {result.message_id}",
        f"Timestamp        : {result.timestamp}",
        f"Mentions         : {mention_state}",
    ]
    return "\n".join(lines)


def format_stats_json(report: AggregationReport) -> str:
    payload: dict[str, Any] = {
        "channel_id": report.channel_id,
        "period": {
            "start": report.start.isoformat(),
            "end": report.end.isoformat(),
        },
        "timezone": report.timezone_name,
        "granularity": report.granularity,
        "filter": {
            "prefix": report.msg_filter.prefix,
            "keyword": report.msg_filter.keyword,
            "ignore_case": report.msg_filter.ignore_case,
        },
        "buckets": [
            {
                "label": b.label,
                "start": b.start.isoformat(),
                "end": b.end.isoformat(),
                "count": b.count,
            }
            for b in report.buckets
        ],
        "total": report.total_matched,
        "scanned_in_period": report.total_in_window,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_stats_table(report: AggregationReport) -> str:
    filter_parts: list[str] = []
    if report.msg_filter.prefix is not None:
        filter_parts.append(f"prefix={report.msg_filter.prefix!r}")
    if report.msg_filter.keyword is not None:
        filter_parts.append(f"keyword={report.msg_filter.keyword!r}")
    if filter_parts and report.msg_filter.ignore_case:
        filter_parts.append("ignore_case=True")
    filter_desc = ", ".join(filter_parts) if filter_parts else "all messages"

    lines = [
        f"Channel ID  : {report.channel_id}",
        f"Period      : {report.start.isoformat()} -> {report.end.isoformat()} (start inclusive, end exclusive)",
        f"Timezone    : {report.timezone_name}",
        f"Granularity : {report.granularity}",
        f"Filter      : {filter_desc}",
        "",
    ]

    if report.granularity != "total":
        col1_header = "Bucket"
        col2_header = "Count"
        col1_width = max([len(col1_header)] + [len(b.label) for b in report.buckets])
        col2_width = max([len(col2_header)] + [len(str(b.count)) for b in report.buckets] + [len(str(report.total_matched))])

        sep = f"+-{'-' * col1_width}-+-{'-' * col2_width}-+"
        lines.append(sep)
        lines.append(f"| {col1_header.ljust(col1_width)} | {col2_header.rjust(col2_width)} |")
        lines.append(sep)
        for b in report.buckets:
            lines.append(f"| {b.label.ljust(col1_width)} | {str(b.count).rjust(col2_width)} |")
        lines.append(sep)
        lines.append(f"| {'Total'.ljust(col1_width)} | {str(report.total_matched).rjust(col2_width)} |")
        lines.append(sep)
    else:
        lines.append(f"Total       : {report.total_matched}")

    return "\n".join(lines)


def format_setup_json(result: SetupResult) -> str:
    payload: dict[str, Any] = {
        "ok": True,
        "action": result.action,
        "env_var": result.env_var,
        "scope": result.scope,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_setup_table(result: SetupResult) -> str:
    lines = [
        f"Setup Status     : OK ({result.action})",
        f"Environment Var  : {result.env_var}",
        f"Scope            : {result.scope}",
        "Note             : Open a new terminal for the change to take effect.",
    ]
    return "\n".join(lines)


def format_channels_json(channels: list[ChannelInfo]) -> str:
    payload: dict[str, Any] = {
        "ok": True,
        "count": len(channels),
        "channels": [
            {
                "guild_id": ch.guild_id,
                "guild_name": ch.guild_name,
                "channel_id": ch.id,
                "name": ch.name,
                "type": ch.type_name,
            }
            for ch in channels
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_channels_table(channels: list[ChannelInfo]) -> str:
    if not channels:
        return "No accessible channels found."

    headers = ("Guild", "Channel ID", "Name", "Type")
    rows = [(ch.guild_name, ch.id, f"#{ch.name}", ch.type_name) for ch in channels]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(4)
    ]

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [
        sep,
        "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(4)) + " |",
        sep,
    ]
    for row in rows:
        lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(4)) + " |")
    lines.append(sep)
    return "\n".join(lines)


def format_thread_json(result: ThreadResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def format_thread_table(result: ThreadResult) -> str:
    lines = [
        "Thread Status    : OK",
        f"Thread ID        : {result.thread_id}",
        f"Parent Channel   : {result.parent_channel_id}",
        f"Source Message   : {result.source_message_id}",
        f"Name             : {result.name}",
    ]
    if result.auto_archive_duration is not None:
        lines.append(f"Auto Archive     : {result.auto_archive_duration} min")
    return "\n".join(lines)


def _format_message_rows(messages: list[MessageItem]) -> list[str]:
    if not messages:
        return ["(no messages)"]

    headers = ("Message ID", "Timestamp", "Author", "Content")
    rows: list[tuple[str, str, str, str]] = []
    for msg in messages:
        author_label = msg.author_display_name or msg.author_username or msg.author_id
        if msg.author_id and author_label != msg.author_id:
            author_label = f"{author_label} ({msg.author_id})"
        single_line = " ".join(msg.content.splitlines())
        if len(single_line) > 80:
            single_line = single_line[:77] + "..."
        rows.append((msg.message_id, msg.timestamp, author_label, single_line))

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(4)
    ]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [
        sep,
        "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(4)) + " |",
        sep,
    ]
    for row in rows:
        lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(4)) + " |")
    lines.append(sep)
    return lines


def format_messages_json(result: MessageListResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def format_messages_table(result: MessageListResult) -> str:
    lines = [
        f"Channel/Thread ID : {result.channel_id}",
        f"After             : {result.after or '(none)'}",
        f"Cursor            : {result.cursor or '(none)'}",
        f"Count             : {len(result.messages)}",
        "",
    ]
    lines.extend(_format_message_rows(result.messages))
    return "\n".join(lines)


def format_wait_json(result: WaitResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def format_wait_table(result: WaitResult) -> str:
    status = "TIMEOUT" if result.timed_out else "RECEIVED"
    lines = [
        f"Wait Status       : {status}",
        f"Channel/Thread ID : {result.channel_id}",
        f"After             : {result.after or '(none)'}",
        f"Cursor            : {result.cursor or '(none)'}",
        f"Polls / Elapsed   : {result.polls} poll(s) / {result.elapsed_seconds:.2f}s",
        f"Count             : {len(result.messages)}",
        "",
    ]
    lines.extend(_format_message_rows(result.messages))
    return "\n".join(lines)
