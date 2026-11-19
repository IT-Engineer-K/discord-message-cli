"""Acceptance tests for Discord Message CLI."""

from __future__ import annotations

import io
import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from discord_msg.cli import run_cli
from discord_msg.client import HttpResponse
from discord_msg.errors import ExitCode
from discord_msg.time_utils import datetime_to_snowflake

FAKE_TOKEN = "FAKE_TEST_BOT_TOKEN_DO_NOT_LOG_999999"
FAKE_CHANNEL_ID = "123456789012345678"
JST = timezone(timedelta(hours=9))


def make_fake_message(
    index: int,
    dt: datetime,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a synthetic Discord API message object with a unique Snowflake ID."""
    base_sf = int(datetime_to_snowflake(dt))
    msg_id = str(base_sf + index)
    utc_iso = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": msg_id,
        "channel_id": FAKE_CHANNEL_ID,
        "timestamp": utc_iso,
        "content": content,
        "attachments": attachments
        or [
            {
                "id": "999000111",
                "filename": "synthetic_secret_attachment.txt",
                "url": "https://cdn.example.invalid/synthetic_secret_attachment.txt",
            }
        ],
    }


class FakeDiscordTransport:
    """In-memory mock transport simulating Discord's GET /channels/{id}/messages endpoint."""

    def __init__(self, messages: list[dict[str, Any]], scripted_responses: list[HttpResponse] | None = None) -> None:
        # Sort newest first (descending Snowflake ID) as Discord API returns
        self.messages = sorted(messages, key=lambda m: int(m["id"]), reverse=True)
        self.scripted_responses = list(scripted_responses) if scripted_responses else []
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.sent_payloads: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        self.calls.append((method, url, headers))
        if method not in ("GET", "POST"):
            raise AssertionError(f"Expected only GET or POST requests, got {method}")

        if self.scripted_responses:
            return self.scripted_responses.pop(0)

        if method == "POST":
            assert body is not None
            payload = json.loads(body.decode("utf-8"))
            self.sent_payloads.append(payload)
            created = {
                "id": "1308123456789012345",
                "channel_id": FAKE_CHANNEL_ID,
                "timestamp": "2026-11-19T04:55:00.000000+00:00",
                "content": payload.get("content", ""),
            }
            return HttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(created).encode("utf-8"),
            )

        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        limit = int(query.get("limit", ["100"])[0])
        before = query.get("before", [None])[0]

        filtered = self.messages
        if before is not None:
            before_int = int(before)
            filtered = [m for m in filtered if int(m["id"]) < before_int]

        page = filtered[:limit]
        return HttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(page).encode("utf-8"),
        )


class TestDiscordMsgAcceptance(unittest.TestCase):
    def test_ac1_pagination_over_100_messages_and_half_open_interval(self) -> None:
        """AC1: Paginates >100 messages without dropping any; includes start time and excludes end time."""
        start_jst = datetime(2026, 11, 17, 10, 0, 0, tzinfo=JST)
        end_jst = datetime(2026, 11, 17, 13, 0, 0, tzinfo=JST)

        messages: list[dict[str, Any]] = []
        # 1 message strictly before start (09:59:59 JST) -> must be excluded
        messages.append(
            make_fake_message(1, start_jst - timedelta(seconds=1), "[INFO] before window")
        )
        # 1 message exactly at start (10:00:00 JST) -> must be included
        messages.append(
            make_fake_message(2, start_jst, "[INFO] exact start boundary")
        )
        # 248 messages inside [10:00, 13:00) across the 3 hours
        # Total inside window = 1 (at start) + 248 = 249 messages (requires 3 pages of 100)
        for i in range(248):
            offset_Minutes = (i * 179) // 248  # spread across 0..178 minutes
            dt = start_jst + timedelta(minutes=offset_Minutes, seconds=(i % 59) + 1)
            messages.append(
                make_fake_message(10 + i, dt, f"[INFO] synthetic event #{i}")
            )
        # 1 message exactly at end (13:00:00 JST) -> must be excluded
        messages.append(
            make_fake_message(900, end_jst, "[INFO] exact end boundary")
        )
        # 1 message after end (13:05:00 JST) -> must be excluded
        messages.append(
            make_fake_message(901, end_jst + timedelta(minutes=5), "[INFO] after window")
        )

        transport = FakeDiscordTransport(messages)
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = run_cli(
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-17T10:00:00+09:00",
                "--until",
                "2026-11-17T13:00:00+09:00",
                "--by",
                "hour",
                "--format",
                "json",
            ],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )

        self.assertEqual(exit_code, int(ExitCode.OK), stderr.getvalue())
        self.assertGreaterEqual(len(transport.calls), 3, "Expected at least 3 paginated API calls for >200 messages")

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 249)
        self.assertEqual(sum(b["count"] for b in payload["buckets"]), 249)
        self.assertEqual(len(payload["buckets"]), 3)

    def test_ac2_keyword_and_prefix_filtering_with_hourly_buckets(self) -> None:
        """AC2: Filtering by keyword/prefix and hourly bucket counts match expected values on synthetic notifications."""
        base_jst = datetime(2026, 11, 18, 9, 0, 0, tzinfo=JST)
        dataset = [
            # 09:00 hour: 2 matching "[ALERT] DiskHigh", 1 non-matching prefix, 1 non-matching keyword
            (base_jst + timedelta(minutes=5), "[ALERT] DiskHigh on node-a"),
            (base_jst + timedelta(minutes=25), "[ALERT] DiskHigh on node-b"),
            (base_jst + timedelta(minutes=40), "[INFO] DiskHigh normal check"),
            (base_jst + timedelta(minutes=50), "[ALERT] MemoryHigh on node-a"),
            # 10:00 hour: 0 matching
            (base_jst + timedelta(hours=1, minutes=15), "[INFO] Heartbeat ok"),
            # 11:00 hour: 3 matching
            (base_jst + timedelta(hours=2, minutes=0), "[ALERT] DiskHigh on node-c"),
            (base_jst + timedelta(hours=2, minutes=30), "[ALERT] DiskHigh on node-d"),
            (base_jst + timedelta(hours=2, minutes=59), "[ALERT] DiskHigh on node-e"),
        ]
        messages = [make_fake_message(idx + 1, dt, text) for idx, (dt, text) in enumerate(dataset)]
        transport = FakeDiscordTransport(messages)
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = run_cli(
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T09:00:00+09:00",
                "--until",
                "2026-11-18T12:00:00+09:00",
                "--by",
                "hour",
                "--prefix",
                "[ALERT]",
                "--keyword",
                "DiskHigh",
                "--format",
                "json",
            ],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )

        self.assertEqual(exit_code, int(ExitCode.OK), stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["timezone"], "Asia/Tokyo")
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["scanned_in_period"], 8)
        bucket_counts = {b["label"]: b["count"] for b in payload["buckets"]}
        self.assertEqual(
            bucket_counts,
            {
                "2026-11-18 09:00": 2,
                "2026-11-18 10:00": 0,
                "2026-11-18 11:00": 3,
            },
        )

    def test_ac3_distinguish_empty_auth_permission_rate_limit_and_midstream_failure(self) -> None:
        """AC3: Distinguishes empty history, 401 auth failure, 403 permission denied, 429 retry, and mid-fetch error."""
        # 1. Empty history -> ExitCode.OK, total=0
        empty_transport = FakeDiscordTransport([])
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T00:00:00+09:00",
                "--until",
                "2026-11-18T06:00:00+09:00",
                "--format",
                "json",
            ],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=empty_transport,
        )
        self.assertEqual(code, int(ExitCode.OK))
        self.assertEqual(json.loads(stdout.getvalue())["total"], 0)

        # 2. Auth failure (401) -> ExitCode.AUTH_FAILED, empty stdout
        auth_transport = FakeDiscordTransport(
            [],
            scripted_responses=[
                HttpResponse(status=401, headers={}, body=b'{"message": "401: Unauthorized", "code": 0}')
            ],
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["check", "--channel", FAKE_CHANNEL_ID, "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=auth_transport,
        )
        self.assertEqual(code, int(ExitCode.AUTH_FAILED))
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("AUTH_FAILED", stderr.getvalue())

        # 3. Permission denied (403) -> ExitCode.PERMISSION_DENIED, empty stdout
        perm_transport = FakeDiscordTransport(
            [],
            scripted_responses=[
                HttpResponse(status=403, headers={}, body=b'{"message": "Missing Access", "code": 50001}')
            ],
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["check", "--channel", FAKE_CHANNEL_ID, "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=perm_transport,
        )
        self.assertEqual(code, int(ExitCode.PERMISSION_DENIED))
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("PERMISSION_DENIED", stderr.getvalue())

        # 4. Rate limit (429) followed by 200 OK -> Honors retry_after and succeeds
        start_jst = datetime(2026, 11, 18, 0, 0, 0, tzinfo=JST)
        msg = make_fake_message(1, start_jst + timedelta(hours=1), "[NOTICE] test")
        rl_transport = FakeDiscordTransport(
            [msg],
            scripted_responses=[
                HttpResponse(
                    status=429,
                    headers={"retry-after": "1.25"},
                    body=b'{"message": "You are being rate limited.", "retry_after": 1.25, "global": false}',
                )
            ],
        )
        slept: list[float] = []
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T00:00:00+09:00",
                "--until",
                "2026-11-18T03:00:00+09:00",
                "--format",
                "json",
            ],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=rl_transport,
            sleep_fn=slept.append,
        )
        self.assertEqual(code, int(ExitCode.OK))
        self.assertEqual(slept, [1.25])
        self.assertEqual(json.loads(stdout.getvalue())["total"], 1)

        # 5. Mid-pagination failure (Page 1 returns 100 items, Page 2 fails with 500)
        # Must NOT output partial counts on stdout; must exit with FETCH_FAILED.
        page1_msgs = [
            make_fake_message(i + 1, start_jst + timedelta(minutes=i + 1), f"[NOTICE] item {i}")
            for i in range(100)
        ]
        mid_fail_transport = FakeDiscordTransport(
            [],
            scripted_responses=[
                HttpResponse(
                    status=200,
                    headers={"content-type": "application/json"},
                    body=json.dumps(sorted(page1_msgs, key=lambda m: int(m["id"]), reverse=True)).encode("utf-8"),
                ),
                HttpResponse(
                    status=503,
                    headers={},
                    body=b'{"message": "Service Unavailable"}',
                ),
            ],
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T00:00:00+09:00",
                "--until",
                "2026-11-18T05:00:00+09:00",
                "--format",
                "json",
            ],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=mid_fail_transport,
        )
        self.assertEqual(code, int(ExitCode.FETCH_FAILED))
        self.assertEqual(stdout.getvalue(), "", "Partial results must never be emitted to stdout on mid-fetch failure")
        self.assertIn("FETCH_FAILED", stderr.getvalue())

    def test_ac4_ai_json_output_excludes_token_content_and_attachments(self) -> None:
        """AC4: AI can parse stdout directly as JSON, and output contains neither token, message body, nor attachment."""
        secret_body = "CONFIDENTIAL_MESSAGE_BODY_XYZ_98765"
        start_jst = datetime(2026, 11, 18, 12, 0, 0, tzinfo=JST)
        msg = make_fake_message(1, start_jst + timedelta(minutes=10), f"[ALERT] {secret_body}")
        transport = FakeDiscordTransport([msg])

        for subcmd in (
            ["check", "--channel", FAKE_CHANNEL_ID, "--format", "json"],
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T12:00:00+09:00",
                "--until",
                "2026-11-18T14:00:00+09:00",
                "--format",
                "json",
            ],
            [
                "stats",
                "--channel",
                FAKE_CHANNEL_ID,
                "--since",
                "2026-11-18T12:00:00+09:00",
                "--until",
                "2026-11-18T14:00:00+09:00",
                "--format",
                "table",
            ],
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            code = run_cli(
                subcmd,
                env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
                stdout=stdout,
                stderr=stderr,
                transport=transport,
            )
            self.assertEqual(code, int(ExitCode.OK))
            combined_output = stdout.getvalue() + "\n" + stderr.getvalue()
            self.assertNotIn(FAKE_TOKEN, combined_output, "Bot token must never appear in stdout or stderr")
            self.assertNotIn(secret_body, combined_output, "Message body must never appear in check/stats output")
            self.assertNotIn(
                "synthetic_secret_attachment.txt",
                combined_output,
                "Attachment metadata must never appear in check/stats output",
            )
            if "json" in subcmd:
                parsed = json.loads(stdout.getvalue())
                self.assertIsInstance(parsed, dict)


if __name__ == "__main__":
    unittest.main()
