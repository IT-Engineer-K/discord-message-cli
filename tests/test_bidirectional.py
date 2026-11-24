"""Tests for generic bidirectional thread, message listing, and wait features."""

from __future__ import annotations

import io
import json
import re
import unittest
import urllib.parse
from typing import Any

import discord_msg
from discord_msg import (
    DiscordClient,
    ExitCode,
    HttpResponse,
    InvalidArgumentsError,
    PermissionDeniedError,
    create_thread,
    list_messages,
    send_message,
    wait_for_messages,
)
from discord_msg.cli import run_cli

FAKE_TOKEN = "FAKE_BIDIR_BOT_TOKEN_DO_NOT_LOG_12345"
PARENT_CHANNEL_ID = "123456789012345678"
ROOT_MESSAGE_ID = "1308000000000000001"
BOT_AUTHOR_ID = "900000000000000001"
HUMAN_AUTHOR_ID = "222333444555666777"
OTHER_AUTHOR_ID = "333444555666777888"


def _make_msg(
    msg_id: str,
    channel_id: str,
    content: str,
    *,
    author_id: str = HUMAN_AUTHOR_ID,
    username: str = "alice",
    display_name: str | None = "Alice",
    is_bot: bool = False,
    timestamp: str = "2026-11-25T00:00:00.000000+00:00",
    referenced_message_id: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": msg_id,
        "channel_id": channel_id,
        "timestamp": timestamp,
        "content": content,
        "author": {
            "id": author_id,
            "username": username,
            "global_name": display_name,
            "bot": is_bot,
        },
    }
    if referenced_message_id is not None:
        msg["message_reference"] = {"message_id": referenced_message_id}
    return msg


class BidirectionalFakeTransport:
    """In-memory mock Discord REST transport supporting threads, sends, and `after` queries."""

    def __init__(self) -> None:
        self.messages_by_channel: dict[str, list[dict[str, Any]]] = {}
        self.threads: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.scripted_responses: list[HttpResponse] = []
        self._next_msg_id = 1309000000000000100
        self._next_thread_id = 1309500000000000001
        self.on_get_messages: Any = None  # optional hook called on GET /messages

    def add_message(self, msg: dict[str, Any]) -> None:
        ch_id = msg["channel_id"]
        self.messages_by_channel.setdefault(ch_id, []).append(msg)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        parsed_body = json.loads(body.decode("utf-8")) if body else None
        self.calls.append((method, url, parsed_body))

        if self.scripted_responses:
            return self.scripted_responses.pop(0)

        parsed = urllib.parse.urlparse(url)
        path = parsed.path

        # POST /api/v10/channels/{ch}/messages/{msg}/threads
        m_thread = re.match(r"^/api/v10/channels/(\d+)/messages/(\d+)/threads$", path)
        if method == "POST" and m_thread:
            ch_id, msg_id = m_thread.group(1), m_thread.group(2)
            assert parsed_body is not None
            tid = str(self._next_thread_id)
            self._next_thread_id += 1
            thread_obj = {
                "id": tid,
                "type": 11,
                "parent_id": ch_id,
                "name": parsed_body["name"],
                "thread_metadata": {
                    "archived": False,
                    "auto_archive_duration": parsed_body.get("auto_archive_duration", 1440),
                },
            }
            self.threads[tid] = thread_obj
            self.messages_by_channel.setdefault(tid, [])
            return HttpResponse(
                status=201,
                headers={"content-type": "application/json"},
                body=json.dumps(thread_obj).encode("utf-8"),
            )

        # POST /api/v10/channels/{ch}/messages
        m_send = re.match(r"^/api/v10/channels/(\d+)/messages$", path)
        if method == "POST" and m_send:
            ch_id = m_send.group(1)
            assert parsed_body is not None
            mid = str(self._next_msg_id)
            self._next_msg_id += 1
            created = _make_msg(
                mid,
                ch_id,
                parsed_body["content"],
                author_id=BOT_AUTHOR_ID,
                username="helper-bot",
                display_name="Helper Bot",
                is_bot=True,
                timestamp="2026-11-25T00:01:00.000000+00:00",
            )
            self.add_message(created)
            return HttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(created).encode("utf-8"),
            )

        # GET /api/v10/channels/{ch}/messages
        m_list = re.match(r"^/api/v10/channels/(\d+)/messages$", path)
        if method == "GET" and m_list:
            ch_id = m_list.group(1)
            if self.on_get_messages is not None:
                self.on_get_messages(ch_id, len(self.calls))
            query = urllib.parse.parse_qs(parsed.query)
            limit = int(query.get("limit", ["50"])[0])
            after = query.get("after", [None])[0]
            before = query.get("before", [None])[0]

            all_msgs = sorted(
                self.messages_by_channel.get(ch_id, []),
                key=lambda item: int(item["id"]),
            )
            if after is not None:
                after_int = int(after)
                # Discord returns the `limit` messages immediately after `after`, newest-first
                eligible = [item for item in all_msgs if int(item["id"]) > after_int]
                page = list(reversed(eligible[:limit]))
            elif before is not None:
                before_int = int(before)
                eligible = [item for item in reversed(all_msgs) if int(item["id"]) < before_int]
                page = eligible[:limit]
            else:
                page = list(reversed(all_msgs))[:limit]

            return HttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps(page).encode("utf-8"),
            )

        raise AssertionError(f"Unexpected request: {method} {url}")


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestBidirectionalPythonAPI(unittest.TestCase):
    def test_end_to_end_thread_send_wait_and_cursor_resume(self) -> None:
        """Initial AC: create thread -> send question -> wait for reply -> resume with cursor."""
        transport = BidirectionalFakeTransport()
        clock = FakeClock()
        client = DiscordClient(
            FAKE_TOKEN,
            transport=transport,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
            diag_fn=lambda _: None,
        )

        # 1. Create thread from an existing message
        thread = create_thread(
            PARENT_CHANNEL_ID,
            ROOT_MESSAGE_ID,
            "Question Thread",
            auto_archive_duration=1440,
            client=client,
        )
        self.assertEqual(thread.parent_channel_id, PARENT_CHANNEL_ID)
        self.assertEqual(thread.source_message_id, ROOT_MESSAGE_ID)
        self.assertEqual(thread.name, "Question Thread")
        self.assertEqual(thread.auto_archive_duration, 1440)

        # 2. Send question to the created thread
        sent = send_message(thread_id=thread.thread_id, content="Should we deploy v2?", client=client)
        self.assertEqual(sent.channel_id, thread.thread_id)
        self.assertFalse(sent.mentions_allowed)

        # Simulate: on poll 1 only another bot replies (should be skipped);
        # on poll 2 human replies!
        poll_counter = {"count": 0}

        def inject_replies(ch_id: str, _call_idx: int) -> None:
            if ch_id != thread.thread_id:
                return
            poll_counter["count"] += 1
            if poll_counter["count"] == 1:
                transport.add_message(
                    _make_msg(
                        "1309000000000000200",
                        thread.thread_id,
                        "Auto acknowledgement",
                        author_id=BOT_AUTHOR_ID,
                        username="helper-bot",
                        is_bot=True,
                    )
                )
            elif poll_counter["count"] == 2:
                transport.add_message(
                    _make_msg(
                        "1309000000000000205",
                        thread.thread_id,
                        "Yes, go ahead.",
                        author_id=HUMAN_AUTHOR_ID,
                        username="alice",
                        display_name="Alice",
                        is_bot=False,
                        referenced_message_id=sent.message_id,
                    )
                )

        transport.on_get_messages = inject_replies

        # 3. Wait for human reply strictly after the question message
        wait_res = wait_for_messages(
            thread_id=thread.thread_id,
            after=sent.message_id,
            timeout=10.0,
            poll_interval=2.0,
            author_id=HUMAN_AUTHOR_ID,
            exclude_bots=True,
            client=client,
        )
        self.assertFalse(wait_res.timed_out)
        self.assertEqual(wait_res.polls, 2)
        self.assertEqual(clock.sleeps, [2.0])
        self.assertEqual(len(wait_res.messages), 1)
        self.assertEqual(wait_res.messages[0].content, "Yes, go ahead.")
        self.assertEqual(wait_res.messages[0].author_id, HUMAN_AUTHOR_ID)
        self.assertEqual(wait_res.messages[0].referenced_message_id, sent.message_id)
        self.assertEqual(wait_res.cursor, "1309000000000000205")

        # 4. Resume using returned cursor -> no old messages returned
        transport.on_get_messages = None
        empty_resume = list_messages(
            thread_id=thread.thread_id,
            after=wait_res.cursor,
            author_id=HUMAN_AUTHOR_ID,
            client=client,
        )
        self.assertEqual(empty_resume.messages, [])
        self.assertEqual(empty_resume.cursor, "1309000000000000205")

        # 5. Another reply arrives -> resuming with cursor returns only the new reply
        transport.add_message(
            _make_msg(
                "1309000000000000210",
                thread.thread_id,
                "Also notify #ops after deploy.",
                author_id=HUMAN_AUTHOR_ID,
            )
        )
        resumed = list_messages(
            thread_id=thread.thread_id,
            after=empty_resume.cursor,
            author_id=HUMAN_AUTHOR_ID,
            client=client,
        )
        self.assertEqual([m.message_id for m in resumed.messages], ["1309000000000000210"])
        self.assertEqual(resumed.cursor, "1309000000000000210")

    def test_cursor_advances_accurately_with_filters_and_limit(self) -> None:
        """Cursor advances past filtered messages and stops at limit without skipping unread matches."""
        transport = BidirectionalFakeTransport()
        ch_id = "1309500000000000099"
        for msg in (
            _make_msg("100", ch_id, "before cursor", author_id=HUMAN_AUTHOR_ID),
            _make_msg("101", ch_id, "other user", author_id=OTHER_AUTHOR_ID),
            _make_msg("102", ch_id, "alice 1", author_id=HUMAN_AUTHOR_ID),
            _make_msg("103", ch_id, "bot msg", author_id=BOT_AUTHOR_ID, is_bot=True),
            _make_msg("104", ch_id, "alice 2", author_id=HUMAN_AUTHOR_ID),
            _make_msg("105", ch_id, "other user 2", author_id=OTHER_AUTHOR_ID),
        ):
            transport.add_message(msg)

        client = DiscordClient(FAKE_TOKEN, transport=transport, diag_fn=lambda _: None)

        # limit=1 with author_id filter -> returns 102 and cursor MUST be 102 (not 105!)
        page1 = client.list_messages(ch_id, after="100", limit=1, author_id=HUMAN_AUTHOR_ID)
        self.assertEqual([m.message_id for m in page1.messages], ["102"])
        self.assertEqual(page1.cursor, "102")

        # Resume from 102 with limit=1 -> returns 104 and cursor=104
        page2 = client.list_messages(ch_id, after=page1.cursor, limit=1, author_id=HUMAN_AUTHOR_ID)
        self.assertEqual([m.message_id for m in page2.messages], ["104"])
        self.assertEqual(page2.cursor, "104")

        # Resume from 104 -> 105 is filtered out, cursor advances to 105, messages=[]
        page3 = client.list_messages(ch_id, after=page2.cursor, limit=10, author_id=HUMAN_AUTHOR_ID)
        self.assertEqual(page3.messages, [])
        self.assertEqual(page3.cursor, "105")

    def test_wait_timeout_and_baseline_without_after(self) -> None:
        """wait_for_messages establishes baseline when after is omitted and returns timed_out=True on timeout."""
        transport = BidirectionalFakeTransport()
        ch_id = "1309500000000000100"
        transport.add_message(_make_msg("500", ch_id, "old message", author_id=HUMAN_AUTHOR_ID))

        clock = FakeClock()
        client = DiscordClient(
            FAKE_TOKEN,
            transport=transport,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
            diag_fn=lambda _: None,
        )

        res = client.wait_for_messages(ch_id, timeout=5.0, poll_interval=2.0)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.after, "500")
        self.assertEqual(res.cursor, "500")
        self.assertEqual(res.messages, [])
        self.assertEqual(res.polls, 4)
        self.assertEqual(clock.sleeps, [2.0, 2.0, 1.0])

    def test_validation_and_permission_errors(self) -> None:
        """Validates range boundaries, thread arguments, and 403 error messaging."""
        transport = BidirectionalFakeTransport()
        client = DiscordClient(FAKE_TOKEN, transport=transport, diag_fn=lambda _: None)

        # No bulk collection when `after` is omitted
        with self.assertRaises(InvalidArgumentsError):
            client.list_messages(PARENT_CHANNEL_ID, limit=101)

        # Mutually exclusive channel_id and thread_id
        with self.assertRaises(InvalidArgumentsError):
            client.send_message(PARENT_CHANNEL_ID, "hi", thread_id="999")

        # Invalid thread name / archive duration
        with self.assertRaises(InvalidArgumentsError):
            client.create_thread(PARENT_CHANNEL_ID, ROOT_MESSAGE_ID, "   ")
        with self.assertRaises(InvalidArgumentsError):
            client.create_thread(PARENT_CHANNEL_ID, ROOT_MESSAGE_ID, "x" * 101)
        with self.assertRaises(InvalidArgumentsError):
            client.create_thread(PARENT_CHANNEL_ID, ROOT_MESSAGE_ID, "ok", auto_archive_duration=999)

        # 403 on thread creation reports Create Public Threads
        transport.scripted_responses.append(
            HttpResponse(status=403, headers={}, body=b'{"code": 50013, "message": "Missing Permissions"}')
        )
        with self.assertRaises(PermissionDeniedError) as ctx:
            client.create_thread(PARENT_CHANNEL_ID, ROOT_MESSAGE_ID, "t")
        self.assertIn("Create Public Threads", str(ctx.exception))


class TestBidirectionalCLI(unittest.TestCase):
    def test_cli_end_to_end_json_and_table(self) -> None:
        """CLI supports thread create -> send --thread -> messages --after -> wait --after."""
        transport = BidirectionalFakeTransport()
        clock = FakeClock()
        env = {"DISCORD_BOT_TOKEN": FAKE_TOKEN}

        # 1. thread create (JSON)
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "thread",
                "create",
                "--channel",
                PARENT_CHANNEL_ID,
                "--message-id",
                ROOT_MESSAGE_ID,
                "--name",
                "CLI Thread",
                "--auto-archive-duration",
                "60",
                "--format",
                "json",
            ],
            env=env,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        thread_json = json.loads(stdout.getvalue())
        self.assertTrue(thread_json["ok"])
        thread_id = thread_json["thread_id"]
        self.assertEqual(thread_json["auto_archive_duration"], 60)
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

        # 2. send --thread (JSON)
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["send", "--thread", thread_id, "Any feedback?", "--format", "json"],
            env=env,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        send_json = json.loads(stdout.getvalue())
        question_id = send_json["message_id"]
        self.assertEqual(send_json["channel_id"], thread_id)

        # Add a human reply and a bot reply
        transport.add_message(
            _make_msg("1309000000000000300", thread_id, "Bot noise", author_id=BOT_AUTHOR_ID, is_bot=True)
        )
        transport.add_message(
            _make_msg("1309000000000000301", thread_id, "Looks good!", author_id=HUMAN_AUTHOR_ID)
        )

        # 3. messages --thread --after --exclude-bots (JSON)
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "messages",
                "--thread",
                thread_id,
                "--after",
                question_id,
                "--exclude-bots",
                "--format",
                "json",
            ],
            env=env,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        msg_json = json.loads(stdout.getvalue())
        self.assertTrue(msg_json["ok"])
        self.assertEqual(msg_json["count"], 1)
        self.assertEqual(msg_json["cursor"], "1309000000000000301")
        self.assertEqual(msg_json["messages"][0]["content"], "Looks good!")
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

        # 4. wait --thread --after <cursor> -> times out cleanly with ExitCode.OK and preserved cursor
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            [
                "wait",
                "--thread",
                thread_id,
                "--after",
                msg_json["cursor"],
                "--author-id",
                HUMAN_AUTHOR_ID,
                "--timeout",
                "4",
                "--poll-interval",
                "2",
                "--format",
                "json",
            ],
            env=env,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        wait_json = json.loads(stdout.getvalue())
        self.assertTrue(wait_json["timed_out"])
        self.assertEqual(wait_json["cursor"], "1309000000000000301")
        self.assertEqual(wait_json["messages"], [])

        # 5. Table output smoke checks
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["messages", "--thread", thread_id, "--after", question_id, "--exclude-bots"],
            env=env,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertIn("Looks good!", stdout.getvalue())
        self.assertIn("Cursor            : 1309000000000000301", stdout.getvalue())

    def test_cli_rejects_channel_and_thread_together(self) -> None:
        """Passing both --channel and --thread must fail with INVALID_ARGS."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["send", "--channel", PARENT_CHANNEL_ID, "--thread", "999", "hi"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, int(ExitCode.INVALID_ARGS))
        self.assertIn("--channel and --thread", stderr.getvalue())

    def test_public_package_exports(self) -> None:
        """Top-level discord_msg exports the documented Python API."""
        for name in (
            "DiscordClient",
            "create_thread",
            "send_message",
            "list_messages",
            "wait_for_messages",
            "ThreadResult",
            "SendResult",
            "MessageItem",
            "MessageListResult",
            "WaitResult",
        ):
            self.assertTrue(hasattr(discord_msg, name), f"Missing export: {name}")


if __name__ == "__main__":
    unittest.main()
