"""Discord HTTP API v10 client with pagination, message sending, and rate-limit handling."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .errors import (
    AuthFailedError,
    ChannelNotFoundError,
    FetchInterruptedError,
    InvalidArgumentsError,
    PermissionDeniedError,
    RateLimitExceededError,
)
from .time_utils import datetime_to_snowflake, parse_discord_timestamp

DISCORD_API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/IT-Engineer-K/discord-message-cli, 0.3.0)"
PAGE_LIMIT = 100
MAX_MESSAGE_LENGTH = 2000
MAX_THREAD_NAME_LENGTH = 100
MAX_LIST_MESSAGES_LIMIT = 500
VALID_AUTO_ARCHIVE_DURATIONS = (60, 1440, 4320, 10080)
MAX_RATE_LIMIT_PER_USER = 21600


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable automatic HTTP redirects so Authorization headers are never forwarded."""

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class UrllibTransport:
    """Standard-library HTTP transport restricted to GET/POST requests with redirects disabled."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        method_upper = method.upper()
        if method_upper not in ("GET", "POST"):
            raise ValueError("Only GET and POST requests are allowed.")

        req = urllib.request.Request(url, data=body, headers=headers, method=method_upper)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(
                    status=resp.status,
                    headers=resp_headers,
                    body=resp.read(),
                )
        except urllib.error.HTTPError as exc:
            resp_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            body = exc.read() if exc.fp else b""
            return HttpResponse(
                status=exc.code,
                headers=resp_headers,
                body=body,
            )
        except urllib.error.URLError as exc:
            raise FetchInterruptedError(f"Network error while contacting Discord API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise FetchInterruptedError("Timed out while contacting Discord API.") from exc


@dataclass(frozen=True)
class MessageRecord:
    """In-memory message representation used only for filtering and aggregation.

    Note: `content` is never serialized into CLI output in check/stats commands.
    """

    id: str
    timestamp: datetime
    content: str


@dataclass(frozen=True)
class CheckResult:
    channel_id: str
    reachable: bool
    history_readable: bool
    has_messages: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.reachable and self.history_readable,
            "channel_id": self.channel_id,
            "reachable": self.reachable,
            "history_readable": self.history_readable,
            "has_messages": self.has_messages,
        }


@dataclass(frozen=True)
class SendResult:
    channel_id: str
    message_id: str
    timestamp: str
    mentions_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "mentions_allowed": self.mentions_allowed,
        }


@dataclass(frozen=True)
class ThreadResult:
    thread_id: str
    parent_channel_id: str
    source_message_id: str
    name: str
    type: int
    auto_archive_duration: int | None = None
    archived: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "thread_id": self.thread_id,
            "parent_channel_id": self.parent_channel_id,
            "source_message_id": self.source_message_id,
            "name": self.name,
            "type": self.type,
            "auto_archive_duration": self.auto_archive_duration,
            "archived": self.archived,
        }


@dataclass(frozen=True)
class MessageItem:
    """Generic message representation returned by list_messages and wait_for_messages."""

    message_id: str
    channel_id: str
    author_id: str
    author_username: str
    author_display_name: str | None
    author_bot: bool
    timestamp: str
    content: str
    referenced_message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "author_display_name": self.author_display_name,
            "author_bot": self.author_bot,
            "timestamp": self.timestamp,
            "content": self.content,
            "referenced_message_id": self.referenced_message_id,
        }


@dataclass(frozen=True)
class MessageListResult:
    channel_id: str
    after: str | None
    cursor: str | None
    messages: list[MessageItem]
    scanned: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "channel_id": self.channel_id,
            "after": self.after,
            "cursor": self.cursor,
            "count": len(self.messages),
            "messages": [m.to_dict() for m in self.messages],
        }


@dataclass(frozen=True)
class WaitResult:
    channel_id: str
    after: str | None
    cursor: str | None
    messages: list[MessageItem]
    timed_out: bool
    elapsed_seconds: float
    polls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "channel_id": self.channel_id,
            "after": self.after,
            "cursor": self.cursor,
            "timed_out": self.timed_out,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "polls": self.polls,
            "count": len(self.messages),
            "messages": [m.to_dict() for m in self.messages],
        }


@dataclass(frozen=True)
class GuildInfo:
    id: str
    name: str


@dataclass(frozen=True)
class ChannelInfo:
    id: str
    name: str
    type: int
    type_name: str
    guild_id: str
    guild_name: str
    position: int = 0


_CHANNEL_TYPE_NAMES: dict[int, str] = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    13: "stage",
    15: "forum",
    16: "media",
}


def validate_snowflake(value: str, label: str = "ID") -> str:
    cleaned = value.strip()
    if not cleaned or not cleaned.isdigit():
        raise InvalidArgumentsError(
            f"Invalid {label} '{value}'. {label[0].upper() + label[1:]} must be a numeric Discord Snowflake."
        )
    return cleaned


def validate_channel_id(channel_id: str) -> str:
    return validate_snowflake(channel_id, "channel ID")


def validate_message_id(message_id: str) -> str:
    return validate_snowflake(message_id, "message ID")


def validate_author_id(author_id: str) -> str:
    return validate_snowflake(author_id, "author ID")


def validate_thread_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise InvalidArgumentsError("Thread name must not be empty.")
    if len(cleaned) > MAX_THREAD_NAME_LENGTH:
        raise InvalidArgumentsError(
            f"Thread name exceeds Discord's {MAX_THREAD_NAME_LENGTH}-character limit (got {len(cleaned)} characters)."
        )
    return cleaned


def resolve_target_channel_id(
    channel_id: str | None = None,
    *,
    thread_id: str | None = None,
) -> str:
    if channel_id is not None and thread_id is not None:
        raise InvalidArgumentsError("Specify either channel_id or thread_id, not both.")
    if thread_id is not None:
        return validate_snowflake(thread_id, "thread ID")
    if channel_id is not None:
        return validate_channel_id(channel_id)
    raise InvalidArgumentsError("Either channel_id or thread_id is required.")


def validate_message_content(content: str) -> str:
    if not content or not content.strip():
        raise InvalidArgumentsError("Message content must not be empty.")
    if len(content) > MAX_MESSAGE_LENGTH:
        raise InvalidArgumentsError(
            f"Message content exceeds Discord's {MAX_MESSAGE_LENGTH}-character limit (got {len(content)} characters)."
        )
    return content


def _parse_message_item(raw_msg: Any, fallback_channel_id: str) -> MessageItem:
    if not isinstance(raw_msg, dict):
        raise FetchInterruptedError("Malformed message item in Discord API response.")
    msg_id = str(raw_msg.get("id", "")).strip()
    raw_ts = raw_msg.get("timestamp")
    if not msg_id or not msg_id.isdigit() or not isinstance(raw_ts, str):
        raise FetchInterruptedError("Message item missing required 'id' or 'timestamp' field.")

    ch_id = str(raw_msg.get("channel_id") or fallback_channel_id).strip()
    raw_author = raw_msg.get("author")
    author = raw_author if isinstance(raw_author, dict) else {}
    author_id = str(author.get("id", "")).strip()
    author_username = str(author.get("username", "")).strip()
    raw_global_name = author.get("global_name")
    author_display_name = (
        raw_global_name.strip()
        if isinstance(raw_global_name, str) and raw_global_name.strip()
        else None
    )
    author_bot = bool(author.get("bot", False))

    content = raw_msg.get("content")
    text_content = content if isinstance(content, str) else ""

    raw_ref = raw_msg.get("message_reference")
    ref_msg_id: str | None = None
    if isinstance(raw_ref, dict):
        raw_ref_id = raw_ref.get("message_id")
        if raw_ref_id is not None and str(raw_ref_id).strip():
            ref_msg_id = str(raw_ref_id).strip()

    return MessageItem(
        message_id=msg_id,
        channel_id=ch_id,
        author_id=author_id,
        author_username=author_username,
        author_display_name=author_display_name,
        author_bot=author_bot,
        timestamp=raw_ts,
        content=text_content,
        referenced_message_id=ref_msg_id,
    )


class DiscordClient:
    """Client for Discord REST API v10 (read, send, thread creation, and cursor-based polling)."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DISCORD_API_BASE,
        transport: HttpTransport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        max_rate_limit_retries: int = 5,
        timeout: float = 15.0,
        diag_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport if transport is not None else UrllibTransport()
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._max_retries = max_rate_limit_retries
        self._timeout = timeout
        self._diag = diag_fn if diag_fn is not None else self._default_diag

    @staticmethod
    def _default_diag(msg: str) -> None:
        print(f"[discord-msg] {msg}", file=sys.stderr)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        return self._request_json("GET", path, query=query)

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json("POST", path, json_body=payload)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        headers = self._headers()
        body_bytes: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(json_body, ensure_ascii=False).encode("utf-8")

        attempts = 0
        while True:
            if body_bytes is None and method == "GET":
                resp = self._transport.request("GET", url, headers, self._timeout)
            else:
                resp = self._transport.request(method, url, headers, self._timeout, body_bytes)

            if resp.status in (200, 201):
                try:
                    return json.loads(resp.body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise FetchInterruptedError("Invalid JSON response from Discord API.") from exc

            if resp.status == 429:
                retry_after = self._extract_retry_after(resp)
                if attempts >= self._max_retries:
                    raise RateLimitExceededError(
                        f"Rate limit exceeded after {self._max_retries} retries (retry_after={retry_after}s)."
                    )
                attempts += 1
                self._diag(
                    f"Rate limited by Discord API (429). Waiting {retry_after:.2f}s before retry "
                    f"({attempts}/{self._max_retries})..."
                )
                self._sleep(retry_after)
                continue

            if resp.status == 401:
                raise AuthFailedError("Discord authentication failed (401 Unauthorized). Check your Bot token.")

            if resp.status == 403:
                api_msg = self._extract_api_error_detail(resp)
                if method == "POST":
                    if path.endswith("/threads"):
                        raise PermissionDeniedError(
                            f"Missing permission to create thread (403 Forbidden{api_msg}). "
                            "Ensure the Bot has 'View Channel', 'Read Message History', and 'Create Public Threads' permissions."
                        )
                    raise PermissionDeniedError(
                        f"Missing permission to send message to channel or thread (403 Forbidden{api_msg}). "
                        "Ensure the Bot has 'View Channel' and 'Send Messages' (or 'Send Messages in Threads') permissions."
                    )
                raise PermissionDeniedError(
                    f"Missing permission to access channel history (403 Forbidden{api_msg}). "
                    "Ensure the Bot has 'View Channel' and 'Read Message History' permissions."
                )

            if resp.status == 404:
                raise ChannelNotFoundError("Channel not found or inaccessible (404 Not Found).")

            if 500 <= resp.status < 600:
                raise FetchInterruptedError(
                    f"Discord API server error ({resp.status}) while contacting Discord API."
                )

            api_msg = self._extract_api_error_detail(resp)
            raise FetchInterruptedError(
                f"Unexpected HTTP status {resp.status} from Discord API{api_msg}."
            )

    @staticmethod
    def _extract_retry_after(resp: HttpResponse) -> float:
        try:
            payload = json.loads(resp.body.decode("utf-8"))
            if isinstance(payload, dict) and "retry_after" in payload:
                return max(float(payload["retry_after"]), 0.0)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass

        header_val = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-after")
        if header_val is not None:
            try:
                return max(float(header_val), 0.0)
            except ValueError:
                pass

        return 1.0

    @staticmethod
    def _extract_api_error_detail(resp: HttpResponse) -> str:
        try:
            payload = json.loads(resp.body.decode("utf-8"))
            if isinstance(payload, dict):
                code = payload.get("code")
                message = payload.get("message")
                if code is not None and message:
                    return f": [{code}] {message}"
                if message:
                    return f": {message}"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return ""

    def check_channel_access(self, channel_id: str) -> CheckResult:
        """Verify that the Bot can read message history in the channel without returning content."""
        cid = validate_channel_id(channel_id)
        payload = self._get_json(f"/channels/{cid}/messages", {"limit": "1"})
        if not isinstance(payload, list):
            raise FetchInterruptedError("Unexpected payload structure from Discord messages endpoint.")

        return CheckResult(
            channel_id=cid,
            reachable=True,
            history_readable=True,
            has_messages=len(payload) > 0,
        )

    def send_message(
        self,
        channel_id: str | None = None,
        content: str = "",
        *,
        thread_id: str | None = None,
        allow_mentions: bool = False,
    ) -> SendResult:
        """Send a message to a channel or thread with mentions disabled by default."""
        cid = resolve_target_channel_id(channel_id, thread_id=thread_id)
        validated_content = validate_message_content(content)

        allowed_mentions: dict[str, Any] = (
            {"parse": ["users", "roles", "everyone"]}
            if allow_mentions
            else {"parse": []}
        )
        body_payload: dict[str, Any] = {
            "content": validated_content,
            "allowed_mentions": allowed_mentions,
        }
        payload = self._post_json(f"/channels/{cid}/messages", body_payload)
        if not isinstance(payload, dict):
            raise FetchInterruptedError("Unexpected payload structure from Discord message creation endpoint.")

        msg_id = str(payload.get("id", "")).strip()
        raw_ts = payload.get("timestamp")
        if not msg_id or not isinstance(raw_ts, str):
            raise FetchInterruptedError("Created message response missing required 'id' or 'timestamp' field.")

        return SendResult(
            channel_id=cid,
            message_id=msg_id,
            timestamp=raw_ts,
            mentions_allowed=allow_mentions,
        )

    def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: str,
        *,
        auto_archive_duration: int | None = None,
        rate_limit_per_user: int | None = None,
    ) -> ThreadResult:
        """Create a new thread starting from an existing message in a channel."""
        cid = validate_channel_id(channel_id)
        mid = validate_message_id(message_id)
        thread_name = validate_thread_name(name)

        body_payload: dict[str, Any] = {"name": thread_name}
        if auto_archive_duration is not None:
            if auto_archive_duration not in VALID_AUTO_ARCHIVE_DURATIONS:
                valid_str = ", ".join(str(v) for v in VALID_AUTO_ARCHIVE_DURATIONS)
                raise InvalidArgumentsError(
                    f"Invalid auto_archive_duration '{auto_archive_duration}'. Must be one of: {valid_str}."
                )
            body_payload["auto_archive_duration"] = auto_archive_duration

        if rate_limit_per_user is not None:
            if rate_limit_per_user < 0 or rate_limit_per_user > MAX_RATE_LIMIT_PER_USER:
                raise InvalidArgumentsError(
                    f"rate_limit_per_user must be between 0 and {MAX_RATE_LIMIT_PER_USER}."
                )
            body_payload["rate_limit_per_user"] = rate_limit_per_user

        payload = self._post_json(f"/channels/{cid}/messages/{mid}/threads", body_payload)
        if not isinstance(payload, dict):
            raise FetchInterruptedError("Unexpected payload structure from Discord thread creation endpoint.")

        tid = str(payload.get("id", "")).strip()
        if not tid:
            raise FetchInterruptedError("Created thread response missing required 'id' field.")

        parent_id = str(payload.get("parent_id") or cid).strip()
        res_name = payload.get("name")
        resolved_name = res_name if isinstance(res_name, str) and res_name else thread_name
        raw_type = payload.get("type")
        thread_type = raw_type if isinstance(raw_type, int) else 11
        meta = payload.get("thread_metadata")
        meta_dict = meta if isinstance(meta, dict) else {}
        raw_archive_dur = meta_dict.get("auto_archive_duration")
        resolved_archive_dur = (
            raw_archive_dur if isinstance(raw_archive_dur, int) else auto_archive_duration
        )
        archived = bool(meta_dict.get("archived", False))

        return ThreadResult(
            thread_id=tid,
            parent_channel_id=parent_id,
            source_message_id=mid,
            name=resolved_name,
            type=thread_type,
            auto_archive_duration=resolved_archive_dur,
            archived=archived,
        )

    def list_messages(
        self,
        channel_id: str | None = None,
        *,
        thread_id: str | None = None,
        after: str | None = None,
        limit: int = 50,
        author_id: str | None = None,
        author_ids: Sequence[str] | None = None,
        exclude_author_ids: Sequence[str] | None = None,
        exclude_bots: bool = False,
        max_pages: int = 10,
    ) -> MessageListResult:
        """List messages in a channel or thread, optionally restricted to messages after a cursor.

        Does not bulk-collect history outside the specified range:
        - When `after` is provided, only messages strictly after `after` are scanned (ascending by ID).
        - When `after` is omitted, only a single page up to `limit` (max 100) is fetched.
        """
        target_id = resolve_target_channel_id(channel_id, thread_id=thread_id)
        if limit < 1 or limit > MAX_LIST_MESSAGES_LIMIT:
            raise InvalidArgumentsError(
                f"limit must be between 1 and {MAX_LIST_MESSAGES_LIMIT} (got {limit})."
            )
        if after is None and limit > PAGE_LIMIT:
            raise InvalidArgumentsError(
                f"limit must be between 1 and {PAGE_LIMIT} when 'after' is not specified (got {limit})."
            )
        if max_pages < 1:
            raise InvalidArgumentsError("max_pages must be at least 1.")

        validated_after = validate_message_id(after) if after is not None else None

        include_set: set[str] | None = None
        if author_id is not None or author_ids is not None:
            include_set = set()
            if author_id is not None:
                include_set.add(validate_author_id(author_id))
            if author_ids is not None:
                for aid in author_ids:
                    include_set.add(validate_author_id(aid))
            if not include_set:
                include_set = None

        exclude_set: set[str] = set()
        if exclude_author_ids is not None:
            for aid in exclude_author_ids:
                exclude_set.add(validate_author_id(aid))

        has_filters = include_set is not None or bool(exclude_set) or exclude_bots

        def _matches(item: MessageItem) -> bool:
            if exclude_bots and item.author_bot:
                return False
            if include_set is not None and item.author_id not in include_set:
                return False
            if exclude_set and item.author_id in exclude_set:
                return False
            return True

        matched: list[MessageItem] = []
        cursor: str | None = validated_after
        scanned = 0

        if validated_after is None:
            payload = self._get_json(f"/channels/{target_id}/messages", {"limit": str(limit)})
            if not isinstance(payload, list):
                raise FetchInterruptedError("Unexpected payload structure from Discord messages endpoint.")
            items = [_parse_message_item(raw, target_id) for raw in payload]
            items.sort(key=lambda m: int(m.message_id))
            for item in items:
                scanned += 1
                cursor = item.message_id
                if _matches(item):
                    matched.append(item)
                    if len(matched) >= limit:
                        break
            return MessageListResult(
                channel_id=target_id,
                after=None,
                cursor=cursor,
                messages=matched,
                scanned=scanned,
            )

        scan_cursor = validated_after
        for _ in range(max_pages):
            remaining = limit - len(matched)
            if remaining <= 0:
                break
            page_size = (
                min(PAGE_LIMIT, max(remaining * 2, 50))
                if has_filters
                else min(PAGE_LIMIT, remaining)
            )
            payload = self._get_json(
                f"/channels/{target_id}/messages",
                {"after": scan_cursor, "limit": str(page_size)},
            )
            if not isinstance(payload, list):
                raise FetchInterruptedError("Unexpected payload structure from Discord messages endpoint.")
            if not payload:
                break

            items = [_parse_message_item(raw, target_id) for raw in payload]
            scan_cursor_int = int(scan_cursor)
            items = [item for item in items if int(item.message_id) > scan_cursor_int]
            if not items:
                break
            items.sort(key=lambda m: int(m.message_id))

            reached_limit = False
            for item in items:
                scanned += 1
                cursor = item.message_id
                if _matches(item):
                    matched.append(item)
                    if len(matched) >= limit:
                        reached_limit = True
                        break

            if reached_limit:
                break

            next_scan_cursor = items[-1].message_id
            if next_scan_cursor == scan_cursor:
                break
            scan_cursor = next_scan_cursor

            if len(payload) < page_size:
                break

        return MessageListResult(
            channel_id=target_id,
            after=validated_after,
            cursor=cursor,
            messages=matched,
            scanned=scanned,
        )

    def wait_for_messages(
        self,
        channel_id: str | None = None,
        *,
        thread_id: str | None = None,
        after: str | None = None,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
        limit: int = 50,
        min_messages: int = 1,
        author_id: str | None = None,
        author_ids: Sequence[str] | None = None,
        exclude_author_ids: Sequence[str] | None = None,
        exclude_bots: bool = False,
    ) -> WaitResult:
        """Poll a channel or thread until matching new messages arrive or timeout elapses."""
        target_id = resolve_target_channel_id(channel_id, thread_id=thread_id)
        if timeout < 0:
            raise InvalidArgumentsError("timeout must be greater than or equal to 0.")
        if poll_interval <= 0:
            raise InvalidArgumentsError("poll_interval must be greater than 0.")
        if min_messages < 1:
            raise InvalidArgumentsError("min_messages must be at least 1.")
        if limit < min_messages:
            raise InvalidArgumentsError(
                f"limit ({limit}) must be greater than or equal to min_messages ({min_messages})."
            )
        if limit > MAX_LIST_MESSAGES_LIMIT:
            raise InvalidArgumentsError(
                f"limit must be between 1 and {MAX_LIST_MESSAGES_LIMIT} (got {limit})."
            )

        # Validate filters upfront before starting the polling loop.
        validated_author_id = validate_author_id(author_id) if author_id is not None else None
        validated_author_ids = (
            [validate_author_id(aid) for aid in author_ids]
            if author_ids is not None
            else None
        )
        validated_exclude_ids = (
            [validate_author_id(aid) for aid in exclude_author_ids]
            if exclude_author_ids is not None
            else None
        )

        start_time = self._monotonic()
        deadline = start_time + timeout

        if after is not None:
            cursor: str | None = validate_message_id(after)
        else:
            # Establish baseline cursor with a single-message peek so wait only returns new messages.
            baseline = self._get_json(f"/channels/{target_id}/messages", {"limit": "1"})
            if not isinstance(baseline, list):
                raise FetchInterruptedError("Unexpected payload structure from Discord messages endpoint.")
            if baseline:
                cursor = _parse_message_item(baseline[0], target_id).message_id
            else:
                cursor = None

        initial_after = cursor
        collected: list[MessageItem] = []
        polls = 0

        while True:
            polls += 1
            remaining_limit = max(limit - len(collected), 1)
            fetch_limit = min(remaining_limit, PAGE_LIMIT) if cursor is None else remaining_limit
            res = self.list_messages(
                target_id,
                after=cursor,
                limit=fetch_limit,
                author_id=validated_author_id,
                author_ids=validated_author_ids,
                exclude_author_ids=validated_exclude_ids,
                exclude_bots=exclude_bots,
            )
            if res.cursor is not None:
                cursor = res.cursor
            if res.messages:
                collected.extend(res.messages)

            if len(collected) >= min_messages:
                elapsed = max(self._monotonic() - start_time, 0.0)
                return WaitResult(
                    channel_id=target_id,
                    after=initial_after,
                    cursor=cursor,
                    messages=collected[:limit],
                    timed_out=False,
                    elapsed_seconds=elapsed,
                    polls=polls,
                )

            now_ts = self._monotonic()
            remaining_time = deadline - now_ts
            if remaining_time <= 0:
                elapsed = max(now_ts - start_time, 0.0)
                return WaitResult(
                    channel_id=target_id,
                    after=initial_after,
                    cursor=cursor,
                    messages=collected[:limit],
                    timed_out=True,
                    elapsed_seconds=elapsed,
                    polls=polls,
                )

            self._sleep(min(poll_interval, remaining_time))

    def fetch_messages_in_window(
        self,
        channel_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[MessageRecord], int]:
        """Fetch all messages in [start, end) by paging backwards from `end`.

        Uses a generous upper-bound snowflake for `end` (+1 second) on the first page
        to ensure boundary messages are never missed, and stops once messages older
        than `start` are reached or history is exhausted.
        If any page fails mid-stream, raises FetchInterruptedError / RateLimitExceededError
        so partial results are never mistaken for complete counts.
        """
        cid = validate_channel_id(channel_id)
        # Add 1000ms margin on initial cursor so messages right before `end` are included,
        # then filter strictly by `start <= timestamp < end`.
        from datetime import timedelta

        before_cursor = datetime_to_snowflake(end + timedelta(seconds=1))
        seen_ids: set[str] = set()
        collected: list[MessageRecord] = []
        pages_fetched = 0

        while True:
            query = {
                "limit": str(PAGE_LIMIT),
                "before": before_cursor,
            }
            payload = self._get_json(f"/channels/{cid}/messages", query)
            if not isinstance(payload, list):
                raise FetchInterruptedError("Unexpected payload structure from Discord messages endpoint.")

            pages_fetched += 1
            if not payload:
                break

            oldest_ts_in_page: datetime | None = None
            oldest_id_in_page: str | None = None

            for raw_msg in payload:
                if not isinstance(raw_msg, dict):
                    raise FetchInterruptedError("Malformed message item in Discord API response.")
                msg_id = str(raw_msg.get("id", "")).strip()
                raw_ts = raw_msg.get("timestamp")
                if not msg_id or not isinstance(raw_ts, str):
                    raise FetchInterruptedError("Message item missing required 'id' or 'timestamp' field.")

                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                try:
                    msg_ts = parse_discord_timestamp(raw_ts)
                except ValueError as exc:
                    raise FetchInterruptedError(str(exc)) from exc

                if oldest_ts_in_page is None or msg_ts < oldest_ts_in_page:
                    oldest_ts_in_page = msg_ts
                if oldest_id_in_page is None or int(msg_id) < int(oldest_id_in_page):
                    oldest_id_in_page = msg_id

                if start <= msg_ts < end:
                    content = raw_msg.get("content")
                    text_content = content if isinstance(content, str) else ""
                    collected.append(
                        MessageRecord(
                            id=msg_id,
                            timestamp=msg_ts,
                            content=text_content,
                        )
                    )

            if oldest_id_in_page is None or oldest_id_in_page == before_cursor:
                break

            before_cursor = oldest_id_in_page

            if oldest_ts_in_page is not None and oldest_ts_in_page < start:
                break

            if len(payload) < PAGE_LIMIT:
                break

        collected.sort(key=lambda m: (m.timestamp, int(m.id)))
        return collected, pages_fetched

    def list_guilds(self) -> list[GuildInfo]:
        """List guilds (servers) that the Bot belongs to."""
        guilds: list[GuildInfo] = []
        after_cursor: str | None = None
        guild_page_limit = 200

        while True:
            query: dict[str, str] = {"limit": str(guild_page_limit)}
            if after_cursor is not None:
                query["after"] = after_cursor
            payload = self._get_json("/users/@me/guilds", query=query)
            if not isinstance(payload, list):
                raise FetchInterruptedError("Unexpected response format from Discord guilds endpoint.")
            if not payload:
                break

            last_id: str | None = None
            for raw_guild in payload:
                if not isinstance(raw_guild, dict):
                    continue
                gid = raw_guild.get("id")
                gname = raw_guild.get("name")
                if isinstance(gid, str) and gid:
                    guilds.append(
                        GuildInfo(
                            id=gid,
                            name=gname if isinstance(gname, str) and gname else gid,
                        )
                    )
                    last_id = gid

            if last_id is None or last_id == after_cursor or len(payload) < guild_page_limit:
                break
            after_cursor = last_id

        return guilds

    def list_guild_channels(self, guild_id: str, guild_name: str = "") -> list[ChannelInfo]:
        """List channels in a specific guild."""
        cleaned_gid = validate_channel_id(guild_id)
        payload = self._get_json(f"/guilds/{cleaned_gid}/channels")
        if not isinstance(payload, list):
            raise FetchInterruptedError("Unexpected response format from Discord guild channels endpoint.")

        resolved_gname = guild_name or cleaned_gid
        channels: list[ChannelInfo] = []
        for raw_ch in payload:
            if not isinstance(raw_ch, dict):
                continue
            cid = raw_ch.get("id")
            if not isinstance(cid, str) or not cid:
                continue
            cname = raw_ch.get("name")
            ctype = raw_ch.get("type")
            cpos = raw_ch.get("position")
            ch_type = ctype if isinstance(ctype, int) else -1
            ch_pos = cpos if isinstance(cpos, int) else 0
            channels.append(
                ChannelInfo(
                    id=cid,
                    name=cname if isinstance(cname, str) and cname else cid,
                    type=ch_type,
                    type_name=_CHANNEL_TYPE_NAMES.get(ch_type, f"type_{ch_type}"),
                    guild_id=cleaned_gid,
                    guild_name=resolved_gname,
                    position=ch_pos,
                )
            )

        channels.sort(key=lambda c: (c.position, int(c.id) if c.id.isdigit() else 0))
        return channels

    def list_channels(
        self,
        *,
        guild_id: str | None = None,
        include_all: bool = False,
    ) -> list[ChannelInfo]:
        """List accessible channels across all guilds or a specific guild."""
        if guild_id is not None:
            channels = self.list_guild_channels(guild_id)
        else:
            channels = []
            for guild in self.list_guilds():
                channels.extend(self.list_guild_channels(guild.id, guild.name))

        if not include_all:
            channels = [c for c in channels if c.type in (0, 5)]
        return channels


# Backwards-compatible alias for existing callers/tests.
ReadOnlyDiscordClient = DiscordClient
