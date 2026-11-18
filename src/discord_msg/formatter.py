"""Human-readable table and AI-friendly JSON output formatters."""

from __future__ import annotations

import json
from typing import Any

from .aggregator import AggregationReport
from .client import CheckResult


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
