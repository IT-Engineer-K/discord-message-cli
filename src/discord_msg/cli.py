"""Command-line entry point for Discord Message CLI."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import __version__
from .aggregator import Granularity, MessageFilter, aggregate_messages
from .auth import DEFAULT_TOKEN_ENV, resolve_token
from .client import HttpTransport, ReadOnlyDiscordClient, validate_channel_id
from .config import load_local_config
from .errors import DiscordMsgError, ExitCode, InvalidArgumentsError
from .formatter import (
    format_check_json,
    format_check_table,
    format_stats_json,
    format_stats_table,
)
from .time_utils import resolve_timezone, resolve_window


class _RaisingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InvalidArgumentsError(message)


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--channel",
        help="Target Discord channel ID (numeric Snowflake). Also reads DISCORD_CHANNEL_ID or local config.",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format on stdout: 'table' for humans (default) or 'json' for AI agents/scripts.",
    )
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Read Bot token interactively with hidden input on stderr.",
    )
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read Bot token from standard input (first line).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to optional local non-secret config JSON file.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _RaisingArgumentParser(
        prog="discord-msg",
        description="Read-only Discord channel message check and aggregation CLI.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # check subcommand
    check_parser = subparsers.add_parser(
        "check",
        help="Verify connection and message history read permission without displaying message content.",
    )
    _add_common_options(check_parser)

    # stats subcommand
    stats_parser = subparsers.add_parser(
        "stats",
        help="Aggregate message counts by time period without outputting message content or attachments.",
    )
    _add_common_options(stats_parser)
    stats_parser.add_argument(
        "--since",
        help="Start time (inclusive) in ISO 8601 format (e.g. 2026-11-17T00:00:00+09:00 or 2026-11-17).",
    )
    stats_parser.add_argument(
        "--until",
        help="End time (exclusive) in ISO 8601 format (defaults to current time).",
    )
    stats_parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Lookback window in hours when --since/--until are omitted (default: 24).",
    )
    stats_parser.add_argument(
        "--tz",
        default=None,
        help="Display and bucket timezone (default: Asia/Tokyo).",
    )
    stats_parser.add_argument(
        "--by",
        "--granularity",
        dest="granularity",
        choices=("hour", "day", "total"),
        default="hour",
        help="Aggregation bucket unit: 'hour' (default), 'day', or 'total'.",
    )
    stats_parser.add_argument(
        "--prefix",
        default=None,
        help="Count only messages whose body starts with this prefix.",
    )
    stats_parser.add_argument(
        "--keyword",
        default=None,
        help="Count only messages whose body contains this keyword.",
    )
    stats_parser.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Perform case-insensitive matching for --prefix and --keyword.",
    )

    return parser


def _resolve_channel_id(
    arg_channel: str | None,
    env: Mapping[str, str],
    config_channel: str | None,
) -> str:
    raw = arg_channel or env.get("DISCORD_CHANNEL_ID") or config_channel
    if not raw:
        raise InvalidArgumentsError(
            "Target channel ID is required. Specify --channel <CHANNEL_ID> or set DISCORD_CHANNEL_ID."
        )
    return validate_channel_id(raw)


def run_cli(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    now: datetime | None = None,
) -> int:
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    in_stream = stdin if stdin is not None else sys.stdin
    environ = env if env is not None else os.environ

    def diag(msg: str) -> None:
        print(f"[discord-msg] {msg}", file=err_stream)

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.subcommand:
            raise InvalidArgumentsError("A subcommand is required ('check' or 'stats'). Run with --help for usage.")

        local_cfg = load_local_config(args.config)
        channel_id = _resolve_channel_id(args.channel, environ, local_cfg.channel_id)

        token = resolve_token(
            prompt_token=args.prompt_token,
            token_stdin=args.token_stdin,
            env_var=DEFAULT_TOKEN_ENV,
            env=environ,
            stdin=in_stream,
        )

        client_kwargs: dict[str, object] = {
            "transport": transport,
            "diag_fn": diag,
        }
        if sleep_fn is not None:
            client_kwargs["sleep_fn"] = sleep_fn

        client = ReadOnlyDiscordClient(token, **client_kwargs)  # type: ignore[arg-type]

        if args.subcommand == "check":
            result = client.check_channel_access(channel_id)
            output = (
                format_check_json(result)
                if args.format == "json"
                else format_check_table(result)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        if args.subcommand == "stats":
            tz_input = args.tz or local_cfg.tz or "Asia/Tokyo"
            tz_info, tz_label = resolve_timezone(tz_input)
            start_dt, end_dt = resolve_window(
                since=args.since,
                until=args.until,
                hours=args.hours,
                tz=tz_info,
                now=now,
            )

            messages, pages = client.fetch_messages_in_window(channel_id, start_dt, end_dt)
            diag(
                f"Fetched {len(messages)} message(s) in window across {pages} API page(s) "
                f"[{start_dt.isoformat()} -> {end_dt.isoformat()}]."
            )

            msg_filter = MessageFilter(
                prefix=args.prefix,
                keyword=args.keyword,
                ignore_case=args.ignore_case,
            )
            granularity: Granularity = args.granularity
            report = aggregate_messages(
                channel_id=channel_id,
                messages=messages,
                start=start_dt,
                end=end_dt,
                tz=tz_info,
                timezone_name=tz_label,
                granularity=granularity,
                msg_filter=msg_filter,
            )

            output = (
                format_stats_json(report)
                if args.format == "json"
                else format_stats_table(report)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        raise InvalidArgumentsError(f"Unknown subcommand: {args.subcommand}")

    except DiscordMsgError as exc:
        print(f"[discord-msg] error ({exc.code}): {exc.message}", file=err_stream)
        return exc.exit_code


def main() -> None:
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
