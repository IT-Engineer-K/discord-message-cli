"""Additional unit tests for auth, config, and CLI options."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from discord_msg.cli import run_cli
from discord_msg.errors import ExitCode
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


if __name__ == "__main__":
    unittest.main()
