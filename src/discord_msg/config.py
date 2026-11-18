"""Local non-secret configuration loader."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidArgumentsError


@dataclass(frozen=True)
class LocalConfig:
    channel_id: str | None = None
    tz: str = "Asia/Tokyo"


def default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "discord-msg" / "config.json"
    return Path.home() / ".config" / "discord-msg" / "config.json"


def load_local_config(path: Path | None = None) -> LocalConfig:
    cfg_path = path if path is not None else default_config_path()
    if not cfg_path.exists():
        return LocalConfig()

    try:
        raw: Any = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidArgumentsError(f"Failed to read config file {cfg_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise InvalidArgumentsError(f"Config file {cfg_path} must contain a JSON object.")

    if "token" in raw or "bot_token" in raw:
        raise InvalidArgumentsError(
            f"Config file {cfg_path} must not store tokens. Use DISCORD_BOT_TOKEN or --prompt-token."
        )

    channel = raw.get("channel") or raw.get("channel_id")
    tz = raw.get("tz", "Asia/Tokyo")
    return LocalConfig(
        channel_id=str(channel).strip() if channel is not None else None,
        tz=str(tz).strip() if tz else "Asia/Tokyo",
    )
