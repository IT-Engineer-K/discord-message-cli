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
from .auth import (
    DEFAULT_TOKEN_ENV,
    SetupResult,
    clear_user_env_var,
    read_new_token,
    resolve_token,
    save_user_env_var,
)
from .client import (
    DiscordClient,
    HttpTransport,
    VALID_AUTO_ARCHIVE_DURATIONS,
    validate_channel_id,
    validate_message_content,
    validate_snowflake,
)
from .config import load_local_config
from .errors import DiscordMsgError, ExitCode, InvalidArgumentsError
from .formatter import (
    format_channels_json,
    format_channels_table,
    format_check_json,
    format_check_table,
    format_messages_json,
    format_messages_table,
    format_send_json,
    format_send_table,
    format_setup_json,
    format_setup_table,
    format_stats_json,
    format_stats_table,
    format_thread_json,
    format_thread_table,
    format_wait_json,
    format_wait_table,
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
        description="Discord channel/thread messaging, reply polling, and aggregation CLI.",
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

    # send subcommand
    send_parser = subparsers.add_parser(
        "send",
        help="Send a message to a channel or thread (mentions are disabled by default).",
    )
    _add_common_options(send_parser)
    send_parser.add_argument(
        "-t",
        "--thread",
        default=None,
        help="Target Discord thread ID (numeric Snowflake). Mutually exclusive with --channel.",
    )
    send_parser.add_argument(
        "content",
        nargs="?",
        default=None,
        help="Message text to send.",
    )
    send_parser.add_argument(
        "-m",
        "--message",
        dest="message_opt",
        default=None,
        help="Message text to send (alternative to positional argument).",
    )
    send_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read message text from standard input.",
    )
    send_parser.add_argument(
        "--allow-mentions",
        action="store_true",
        help="Allow @everyone, @here, user, and role mentions (disabled by default).",
    )

    # thread subcommand
    thread_parser = subparsers.add_parser(
        "thread",
        help="Thread operations (e.g. create a thread from an existing message).",
    )
    thread_subparsers = thread_parser.add_subparsers(dest="thread_subcommand")
    thread_create_parser = thread_subparsers.add_parser(
        "create",
        help="Create a thread starting from an existing message in a channel.",
    )
    _add_common_options(thread_create_parser)
    thread_create_parser.add_argument(
        "--message-id",
        required=True,
        help="Existing message ID (numeric Snowflake) to start the thread from.",
    )
    thread_create_parser.add_argument(
        "--name",
        required=True,
        help="Thread name (1-100 characters).",
    )
    thread_create_parser.add_argument(
        "--auto-archive-duration",
        type=int,
        choices=VALID_AUTO_ARCHIVE_DURATIONS,
        default=None,
        help="Auto-archive duration in minutes (60, 1440, 4320, or 10080).",
    )
    thread_create_parser.add_argument(
        "--rate-limit-per-user",
        type=int,
        default=None,
        help="Slowmode delay in seconds (0-21600).",
    )

    # messages subcommand
    messages_parser = subparsers.add_parser(
        "messages",
        help="List messages in a channel or thread, optionally restricted to after a message_id cursor.",
    )
    _add_common_options(messages_parser)
    messages_parser.add_argument(
        "-t",
        "--thread",
        default=None,
        help="Target Discord thread ID (numeric Snowflake). Mutually exclusive with --channel.",
    )
    messages_parser.add_argument(
        "--after",
        default=None,
        help="Fetch only messages strictly after this message ID (numeric Snowflake).",
    )
    messages_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of matching messages to return (default: 50).",
    )
    messages_parser.add_argument(
        "--author-id",
        action="append",
        dest="author_ids",
        default=None,
        help="Include only messages from this author ID (repeatable).",
    )
    messages_parser.add_argument(
        "--exclude-author-id",
        action="append",
        dest="exclude_author_ids",
        default=None,
        help="Exclude messages from this author ID (repeatable).",
    )
    messages_parser.add_argument(
        "--exclude-bots",
        action="store_true",
        help="Exclude messages sent by bot accounts.",
    )

    # wait subcommand
    wait_parser = subparsers.add_parser(
        "wait",
        help="Wait until new matching messages arrive in a channel or thread, or until timeout.",
    )
    _add_common_options(wait_parser)
    wait_parser.add_argument(
        "-t",
        "--thread",
        default=None,
        help="Target Discord thread ID (numeric Snowflake). Mutually exclusive with --channel.",
    )
    wait_parser.add_argument(
        "--after",
        default=None,
        help="Start cursor message ID (numeric Snowflake). If omitted, waits for messages after the current latest message.",
    )
    wait_parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds to wait before timing out (default: 60).",
    )
    wait_parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2).",
    )
    wait_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of matching messages to return (default: 50).",
    )
    wait_parser.add_argument(
        "--min-messages",
        type=int,
        default=1,
        help="Minimum number of matching messages required before returning (default: 1).",
    )
    wait_parser.add_argument(
        "--author-id",
        action="append",
        dest="author_ids",
        default=None,
        help="Include only messages from this author ID (repeatable).",
    )
    wait_parser.add_argument(
        "--exclude-author-id",
        action="append",
        dest="exclude_author_ids",
        default=None,
        help="Exclude messages from this author ID (repeatable).",
    )
    wait_parser.add_argument(
        "--exclude-bots",
        action="store_true",
        help="Exclude messages sent by bot accounts.",
    )

    # setup subcommand
    setup_parser = subparsers.add_parser(
        "setup",
        help="Persist or clear DISCORD_BOT_TOKEN in the Windows User environment variables.",
    )
    setup_parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format on stdout: 'table' for humans (default) or 'json' for AI agents/scripts.",
    )
    setup_parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Read Bot token interactively with hidden input on stderr (default).",
    )
    setup_parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read Bot token from standard input (first line).",
    )
    setup_parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove DISCORD_BOT_TOKEN from the Windows User environment variables.",
    )

    # channels subcommand
    channels_parser = subparsers.add_parser(
        "channels",
        help="List accessible Discord channels and their IDs.",
    )
    channels_parser.add_argument(
        "--guild",
        default=None,
        help="Optional guild (server) ID to list channels for.",
    )
    channels_parser.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="Include non-text channels (voice, categories, forums, etc.).",
    )
    channels_parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format on stdout: 'table' for humans (default) or 'json' for AI agents/scripts.",
    )
    channels_parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Read Bot token interactively with hidden input on stderr.",
    )
    channels_parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read Bot token from standard input (first line).",
    )

    return parser


def _resolve_send_content(args: argparse.Namespace, stdin: TextIO) -> str:
    if args.stdin and args.token_stdin:
        raise InvalidArgumentsError("--stdin and --token-stdin cannot be used together.")

    sources = [
        args.content is not None,
        args.message_opt is not None,
        bool(args.stdin),
    ]
    if sum(sources) > 1:
        raise InvalidArgumentsError(
            "Specify message content using only one of positional argument, --message, or --stdin."
        )
    if sum(sources) == 0:
        raise InvalidArgumentsError(
            "Message content is required. Provide text as an argument, --message <TEXT>, or --stdin."
        )

    if args.stdin:
        raw_content = stdin.read().rstrip("\r\n")
    elif args.message_opt is not None:
        raw_content = args.message_opt
    else:
        raw_content = args.content

    return validate_message_content(raw_content)


def _resolve_channel_id(
    arg_channel: str | None,
    env: Mapping[str, str],
    config_channel: str | None,
    *,
    arg_thread: str | None = None,
) -> str:
    if arg_channel is not None and arg_thread is not None:
        raise InvalidArgumentsError("--channel and --thread cannot be used together.")
    if arg_thread is not None:
        return validate_snowflake(arg_thread, "thread ID")
    raw = arg_channel or env.get("DISCORD_CHANNEL_ID") or config_channel
    if not raw:
        raise InvalidArgumentsError(
            "Target channel ID is required. Specify --channel <CHANNEL_ID> or set DISCORD_CHANNEL_ID."
        )
    return validate_channel_id(raw)


def _run_setup(
    args: argparse.Namespace,
    *,
    stdin: TextIO,
    set_env_fn: Callable[[str, str], None],
    clear_env_fn: Callable[[str], None],
) -> SetupResult:
    if args.prompt_token and args.token_stdin:
        raise InvalidArgumentsError("--prompt-token and --token-stdin cannot be used together.")
    if args.clear and (args.prompt_token or args.token_stdin):
        raise InvalidArgumentsError("--clear cannot be used with --prompt-token or --token-stdin.")

    if args.clear:
        clear_env_fn(DEFAULT_TOKEN_ENV)
        return SetupResult(env_var=DEFAULT_TOKEN_ENV, action="cleared")

    token = read_new_token(token_stdin=bool(args.token_stdin), stdin=stdin)
    set_env_fn(DEFAULT_TOKEN_ENV, token)
    return SetupResult(env_var=DEFAULT_TOKEN_ENV, action="saved")


def run_cli(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: HttpTransport | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
    now: datetime | None = None,
    set_env_fn: Callable[[str, str], None] | None = None,
    clear_env_fn: Callable[[str], None] | None = None,
) -> int:
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    in_stream = stdin if stdin is not None else sys.stdin
    environ = env if env is not None else os.environ
    save_env = set_env_fn if set_env_fn is not None else save_user_env_var
    clear_env = clear_env_fn if clear_env_fn is not None else clear_user_env_var

    def diag(msg: str) -> None:
        print(f"[discord-msg] {msg}", file=err_stream)

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.subcommand:
            raise InvalidArgumentsError(
                "A subcommand is required ('check', 'stats', 'send', 'thread', 'messages', 'wait', 'channels', or 'setup'). Run with --help for usage."
            )

        if args.subcommand == "thread" and not getattr(args, "thread_subcommand", None):
            raise InvalidArgumentsError(
                "A thread subcommand is required ('create'). Run 'discord-msg thread --help' for usage."
            )

        if args.subcommand == "setup":
            setup_result = _run_setup(
                args,
                stdin=in_stream,
                set_env_fn=save_env,
                clear_env_fn=clear_env,
            )
            output = (
                format_setup_json(setup_result)
                if args.format == "json"
                else format_setup_table(setup_result)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        if args.subcommand == "channels":
            token = resolve_token(
                prompt_token=args.prompt_token,
                token_stdin=args.token_stdin,
                env_var=DEFAULT_TOKEN_ENV,
                env=environ,
                stdin=in_stream,
            )
            client_kwargs_ch: dict[str, object] = {
                "transport": transport,
                "diag_fn": diag,
            }
            if sleep_fn is not None:
                client_kwargs_ch["sleep_fn"] = sleep_fn
            if monotonic_fn is not None:
                client_kwargs_ch["monotonic_fn"] = monotonic_fn
            client = DiscordClient(token, **client_kwargs_ch)  # type: ignore[arg-type]
            channels = client.list_channels(
                guild_id=args.guild,
                include_all=bool(args.include_all),
            )
            output = (
                format_channels_json(channels)
                if args.format == "json"
                else format_channels_table(channels)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        local_cfg = load_local_config(args.config)
        channel_id = _resolve_channel_id(
            args.channel,
            environ,
            local_cfg.channel_id,
            arg_thread=getattr(args, "thread", None),
        )

        send_content: str | None = None
        if args.subcommand == "send":
            send_content = _resolve_send_content(args, in_stream)

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
        if monotonic_fn is not None:
            client_kwargs["monotonic_fn"] = monotonic_fn

        client = DiscordClient(token, **client_kwargs)  # type: ignore[arg-type]

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

        if args.subcommand == "send":
            assert send_content is not None
            send_result = client.send_message(
                channel_id,
                send_content,
                allow_mentions=bool(args.allow_mentions),
            )
            output = (
                format_send_json(send_result)
                if args.format == "json"
                else format_send_table(send_result)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        if args.subcommand == "thread" and args.thread_subcommand == "create":
            thread_result = client.create_thread(
                channel_id,
                args.message_id,
                args.name,
                auto_archive_duration=args.auto_archive_duration,
                rate_limit_per_user=args.rate_limit_per_user,
            )
            output = (
                format_thread_json(thread_result)
                if args.format == "json"
                else format_thread_table(thread_result)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        if args.subcommand == "messages":
            list_result = client.list_messages(
                channel_id,
                after=args.after,
                limit=args.limit,
                author_ids=args.author_ids,
                exclude_author_ids=args.exclude_author_ids,
                exclude_bots=bool(args.exclude_bots),
            )
            output = (
                format_messages_json(list_result)
                if args.format == "json"
                else format_messages_table(list_result)
            )
            print(output, file=out_stream)
            return int(ExitCode.OK)

        if args.subcommand == "wait":
            wait_result = client.wait_for_messages(
                channel_id,
                after=args.after,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
                limit=args.limit,
                min_messages=args.min_messages,
                author_ids=args.author_ids,
                exclude_author_ids=args.exclude_author_ids,
                exclude_bots=bool(args.exclude_bots),
            )
            output = (
                format_wait_json(wait_result)
                if args.format == "json"
                else format_wait_table(wait_result)
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
