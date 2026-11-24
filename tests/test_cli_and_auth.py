"""Additional unit tests for auth, config, and CLI options."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
import urllib.request
import urllib.response

from discord_msg.cli import run_cli
from discord_msg.client import HttpResponse, ReadOnlyDiscordClient, UrllibTransport, _NoRedirectHandler
from discord_msg.errors import ExitCode, FetchInterruptedError
from test_acceptance import FAKE_CHANNEL_ID, FAKE_TOKEN, JST, FakeDiscordTransport, make_fake_message


class TestCliOptionsAndAuth(unittest.TestCase):
    def test_token_never_accepted_as_cli_argument(self) -> None:
        """Passing --token on the command line must be rejected as an invalid argument."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["check", "--channel", FAKE_CHANNEL_ID, "--token", "SHOULD_NOT_WORK"],
            env={},
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, int(ExitCode.INVALID_ARGS))
        self.assertEqual(stdout.getvalue(), "")

    def test_token_via_stdin(self) -> None:
        """Bot token can be passed via standard input using --token-stdin without leaking."""
        transport = FakeDiscordTransport([])
        stdin = io.StringIO(f"{FAKE_TOKEN}\n")
        stdout, stderr = io.StringIO(), io.StringIO()

        code = run_cli(
            ["check", "--channel", FAKE_CHANNEL_ID, "--token-stdin", "--format", "json"],
            env={},
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["ok"])
        self.assertFalse(data["has_messages"])
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

    def test_config_rejects_stored_token(self) -> None:
        """Local config JSON must reject storing 'token' or 'bot_token'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.json"
            cfg_file.write_text(json.dumps({"channel": FAKE_CHANNEL_ID, "token": "SECRET"}), encoding="utf-8")

            stdout, stderr = io.StringIO(), io.StringIO()
            code = run_cli(
                ["check", "--config", str(cfg_file)],
                env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, int(ExitCode.INVALID_ARGS))
            self.assertIn("must not store tokens", stderr.getvalue())

    def test_default_24h_window_and_day_total_granularities(self) -> None:
        """Default period is last 24 hours in Asia/Tokyo; supports --by day and --by total."""
        fixed_now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=JST)
        msgs = [
            make_fake_message(1, fixed_now - timedelta(hours=25), "[ALERT] outside 24h"),
            make_fake_message(2, fixed_now - timedelta(hours=20), "[ALERT] yesterday"),
            make_fake_message(3, fixed_now - timedelta(hours=2), "[ALERT] today 1"),
            make_fake_message(4, fixed_now - timedelta(hours=1), "[ALERT] today 2"),
        ]
        transport = FakeDiscordTransport(msgs)

        # --by day
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["stats", "--channel", FAKE_CHANNEL_ID, "--by", "day", "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
            now=fixed_now,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        day_payload = json.loads(stdout.getvalue())
        self.assertEqual(day_payload["total"], 3)
        self.assertEqual(
            {b["label"]: b["count"] for b in day_payload["buckets"]},
            {"2026-11-17": 1, "2026-11-18": 2},
        )

        # --by total
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["stats", "--channel", FAKE_CHANNEL_ID, "--by", "total", "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
            now=fixed_now,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        total_payload = json.loads(stdout.getvalue())
        self.assertEqual(total_payload["total"], 3)
        self.assertEqual(len(total_payload["buckets"]), 1)
        self.assertEqual(total_payload["buckets"][0]["count"], 3)

    def test_urllib_transport_never_follows_redirects_or_leaks_token(self) -> None:
        """UrllibTransport must not follow 30x redirects so Authorization headers never leak."""
        requested_urls: list[tuple[str, dict[str, str]]] = []

        class _FakeRedirectHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                requested_urls.append((req.full_url, dict(req.header_items())))
                hdrs = Message()
                hdrs["Location"] = "https://evil.example.invalid/steal-token"
                hdrs["Content-Type"] = "application/json"
                fp = io.BytesIO(b'{"message": "Moved"}')
                resp = urllib.response.addinfourl(fp, hdrs, req.full_url, code=302)
                resp.msg = "Found"
                return resp

        transport = UrllibTransport()
        transport._opener = urllib.request.build_opener(_NoRedirectHandler, _FakeRedirectHTTPSHandler)

        resp = transport.request(
            "GET",
            "https://discord.com/api/v10/channels/123456789012345678/messages?limit=1",
            {"Authorization": f"Bot {FAKE_TOKEN}"},
            timeout=5.0,
        )
        self.assertEqual(resp.status, 302)
        self.assertEqual(len(requested_urls), 1, "Must not follow 302 redirect to secondary URL")
        self.assertEqual(
            requested_urls[0][0],
            "https://discord.com/api/v10/channels/123456789012345678/messages?limit=1",
        )

        client = ReadOnlyDiscordClient(FAKE_TOKEN, transport=transport, diag_fn=lambda _: None)
        with self.assertRaises(FetchInterruptedError):
            client.check_channel_access(FAKE_CHANNEL_ID)
        self.assertEqual(len(requested_urls), 2)

    def test_send_message_disables_mentions_by_default_and_never_echoes_body(self) -> None:
        """`send` must send allowed_mentions={'parse': []} by default and exclude message body from stdout/stderr."""
        transport = FakeDiscordTransport([])
        stdout, stderr = io.StringIO(), io.StringIO()
        secret_msg = "@everyone @here <@123456> CONFIDENTIAL_POST_BODY_777"

        code = run_cli(
            ["send", "--channel", FAKE_CHANNEL_ID, secret_msg, "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertEqual(len(transport.sent_payloads), 1)
        sent = transport.sent_payloads[0]
        self.assertEqual(sent["content"], secret_msg)
        self.assertEqual(sent["allowed_mentions"], {"parse": []})

        out_json = json.loads(stdout.getvalue())
        self.assertTrue(out_json["ok"])
        self.assertEqual(out_json["channel_id"], FAKE_CHANNEL_ID)
        self.assertEqual(out_json["message_id"], "1308123456789012345")
        self.assertFalse(out_json["mentions_allowed"])
        self.assertNotIn("CONFIDENTIAL_POST_BODY_777", stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

    def test_send_message_with_allow_mentions_and_stdin(self) -> None:
        """`send` supports --allow-mentions and --stdin, and rejects empty or >2000 char messages."""
        transport = FakeDiscordTransport([])
        stdout, stderr = io.StringIO(), io.StringIO()

        code = run_cli(
            ["send", "--channel", FAKE_CHANNEL_ID, "--stdin", "--allow-mentions", "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdin=io.StringIO("Hello from stdin\n"),
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertEqual(len(transport.sent_payloads), 1)
        sent = transport.sent_payloads[0]
        self.assertEqual(sent["content"], "Hello from stdin")
        self.assertEqual(sent["allowed_mentions"], {"parse": ["users", "roles", "everyone"]})
        self.assertTrue(json.loads(stdout.getvalue())["mentions_allowed"])

        # Empty message -> INVALID_ARGS
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["send", "--channel", FAKE_CHANNEL_ID, "   "],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.INVALID_ARGS))

        # >2000 characters -> INVALID_ARGS
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["send", "--channel", FAKE_CHANNEL_ID, "-m", "a" * 2001],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.INVALID_ARGS))

    def test_setup_saves_token_via_stdin_without_leaking(self) -> None:
        """`setup --token-stdin` persists DISCORD_BOT_TOKEN without echoing it."""
        saved: list[tuple[str, str]] = []
        stdout, stderr = io.StringIO(), io.StringIO()

        code = run_cli(
            ["setup", "--token-stdin", "--format", "json"],
            env={},
            stdin=io.StringIO(f"Bot {FAKE_TOKEN}\n"),
            stdout=stdout,
            stderr=stderr,
            set_env_fn=lambda name, val: saved.append((name, val)),
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertEqual(saved, [("DISCORD_BOT_TOKEN", FAKE_TOKEN)])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload,
            {
                "ok": True,
                "action": "saved",
                "env_var": "DISCORD_BOT_TOKEN",
                "scope": "User",
            },
        )
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

    def test_setup_prompts_interactively_and_clears(self) -> None:
        """`setup` prompts via getpass by default and supports `--clear`."""
        saved: list[tuple[str, str]] = []
        cleared: list[str] = []
        stdout, stderr = io.StringIO(), io.StringIO()

        with patch("discord_msg.auth.getpass.getpass", return_value=FAKE_TOKEN):
            code = run_cli(
                ["setup"],
                env={},
                stdout=stdout,
                stderr=stderr,
                set_env_fn=lambda name, val: saved.append((name, val)),
            )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertEqual(saved, [("DISCORD_BOT_TOKEN", FAKE_TOKEN)])
        self.assertIn("Setup Status     : OK (saved)", stdout.getvalue())
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["setup", "--clear", "--format", "json"],
            env={},
            stdout=stdout,
            stderr=stderr,
            clear_env_fn=lambda name: cleared.append(name),
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertEqual(cleared, ["DISCORD_BOT_TOKEN"])
        self.assertEqual(json.loads(stdout.getvalue())["action"], "cleared")

    def test_setup_rejects_invalid_option_combinations_and_empty_token(self) -> None:
        """`setup` rejects conflicting flags and empty tokens."""
        for bad_args in (
            ["setup", "--prompt-token", "--token-stdin"],
            ["setup", "--clear", "--token-stdin"],
            ["setup", "--clear", "--prompt-token"],
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            code = run_cli(bad_args, env={}, stdout=stdout, stderr=stderr)
            self.assertEqual(code, int(ExitCode.INVALID_ARGS))

        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["setup", "--token-stdin"],
            env={},
            stdin=io.StringIO("   \n"),
            stdout=stdout,
            stderr=stderr,
            set_env_fn=lambda _n, _v: None,
        )
        self.assertEqual(code, int(ExitCode.AUTH_FAILED))

    def test_channels_lists_text_and_all_channels(self) -> None:
        """`channels` lists text/announcement channels by default and all types with --all."""

        class _FakeChannelsTransport:
            def request(
                self,
                method: str,
                url: str,
                headers: dict[str, str],
                timeout: float,
                body: bytes | None = None,
            ) -> HttpResponse:
                if url.endswith("/users/@me/guilds?limit=200"):
                    payload = [{"id": "111", "name": "Ops Server"}]
                elif url.endswith("/guilds/111/channels"):
                    payload = [
                        {"id": "201", "name": "voice-room", "type": 2, "position": 2},
                        {"id": "200", "name": "alerts", "type": 0, "position": 1},
                        {"id": "199", "name": "category", "type": 4, "position": 0},
                        {"id": "202", "name": "announcements", "type": 5, "position": 3},
                    ]
                else:
                    raise AssertionError(f"Unexpected URL: {url}")
                return HttpResponse(
                    status=200,
                    headers={"content-type": "application/json"},
                    body=json.dumps(payload).encode("utf-8"),
                )

        transport = _FakeChannelsTransport()

        # Default: text and announcement only
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["channels", "--format", "json"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 2)
        self.assertEqual([c["channel_id"] for c in data["channels"]], ["200", "202"])
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())

        # --all with --guild and table output
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_cli(
            ["channels", "--guild", "111", "--all"],
            env={"DISCORD_BOT_TOKEN": FAKE_TOKEN},
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        self.assertEqual(code, int(ExitCode.OK), stderr.getvalue())
        self.assertIn("#alerts", stdout.getvalue())
        self.assertIn("#voice-room", stdout.getvalue())
        self.assertIn("#category", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
