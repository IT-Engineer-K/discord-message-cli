"""Discord HTTP API v10 client with pagination, message sending, and rate-limit handling."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
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
USER_AGENT = "DiscordBot (https://github.com/IT-Engineer-K/discord-message-cli, 0.2.0)"
PAGE_LIMIT = 100
MAX_MESSAGE_LENGTH = 2000


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


@dataclass(frozen=True)
class SendResult:
    channel_id: str
    message_id: str
    timestamp: str
    mentions_allowed: bool


def validate_channel_id(channel_id: str) -> str:
    cleaned = channel_id.strip()
    if not cleaned or not cleaned.isdigit():
        raise InvalidArgumentsError(
            f"Invalid channel ID '{channel_id}'. Channel ID must be a numeric Discord Snowflake."
        )
    return cleaned


def validate_message_content(content: str) -> str:
    if not content or not content.strip():
        raise InvalidArgumentsError("Message content must not be empty.")
    if len(content) > MAX_MESSAGE_LENGTH:
        raise InvalidArgumentsError(
            f"Message content exceeds Discord's {MAX_MESSAGE_LENGTH}-character limit (got {len(content)} characters)."
        )
    return content


class ReadOnlyDiscordClient:
    """Client for Discord REST API v10 (read and message sending)."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DISCORD_API_BASE,
        transport: HttpTransport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_rate_limit_retries: int = 5,
        timeout: float = 15.0,
        diag_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport if transport is not None else UrllibTransport()
        self._sleep = sleep_fn
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
                    raise PermissionDeniedError(
                        f"Missing permission to send message to channel (403 Forbidden{api_msg}). "
                        "Ensure the Bot has 'View Channel' and 'Send Messages' permissions."
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
        channel_id: str,
        content: str,
        *,
        allow_mentions: bool = False,
    ) -> SendResult:
        """Send a message to the channel with mentions disabled by default."""
        cid = validate_channel_id(channel_id)
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
