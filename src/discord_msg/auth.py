"""Secure Bot token resolution without command-line arguments or logging."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import getpass
import os
import sys
from collections.abc import Mapping

from .errors import AuthFailedError, InvalidArgumentsError

DEFAULT_TOKEN_ENV = "DISCORD_BOT_TOKEN"


@dataclass(frozen=True)
class SetupResult:
    env_var: str
    action: str
    scope: str = "User"


def _strip_bot_prefix(raw_token: str) -> str:
    token = raw_token.strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    return token


def read_new_token(
    *,
    token_stdin: bool = False,
    stdin: object | None = None,
) -> str:
    """Read a new Bot token interactively (hidden) or from stdin."""
    in_stream = stdin if stdin is not None else sys.stdin
    if token_stdin:
        line = getattr(in_stream, "readline", lambda: "")()
        raw_token = line.strip()
    else:
        raw_token = getpass.getpass("Discord Bot Token: ", stream=sys.stderr).strip()

    token = _strip_bot_prefix(raw_token)
    if not token:
        raise AuthFailedError("Discord Bot token is empty.")
    return token


def _broadcast_env_change() -> None:
    """Notify Windows Explorer/shells that User environment variables changed."""
    if os.name != "nt":
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            1000,
            None,
        )
    except Exception:
        # Best-effort notification; registry update already succeeded.
        pass


def save_user_env_var(name: str, value: str) -> None:
    """Persist an environment variable in the current Windows User scope."""
    if os.name != "nt":
        raise InvalidArgumentsError(
            "Persisting environment variables via 'setup' is only supported on Windows."
        )
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    _broadcast_env_change()


def clear_user_env_var(name: str) -> None:
    """Remove an environment variable from the current Windows User scope."""
    if os.name != "nt":
        raise InvalidArgumentsError(
            "Clearing environment variables via 'setup' is only supported on Windows."
        )
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return
    _broadcast_env_change()


def resolve_token(
    *,
    prompt_token: bool = False,
    token_stdin: bool = False,
    env_var: str = DEFAULT_TOKEN_ENV,
    env: Mapping[str, str] | None = None,
    stdin: object | None = None,
) -> str:
    """Resolve the Discord Bot token securely.

    Priority:
    1. Explicit --token-stdin (reads first line from stdin)
    2. Explicit --prompt-token (reads via getpass on stderr)
    3. Environment variable (default: DISCORD_BOT_TOKEN)
    4. Interactive prompt fallback if stdin is a TTY
    """
    if prompt_token and token_stdin:
        raise InvalidArgumentsError(
            "--prompt-token and --token-stdin cannot be used together."
        )

    in_stream = stdin if stdin is not None else sys.stdin
    environ = env if env is not None else os.environ

    raw_token = ""
    if token_stdin:
        line = getattr(in_stream, "readline", lambda: "")()
        raw_token = line.strip()
    elif prompt_token:
        raw_token = getpass.getpass("Discord Bot Token: ", stream=sys.stderr).strip()
    else:
        raw_token = environ.get(env_var, "").strip()
        if not raw_token and hasattr(in_stream, "isatty") and in_stream.isatty():
            raw_token = getpass.getpass("Discord Bot Token: ", stream=sys.stderr).strip()

    raw_token = _strip_bot_prefix(raw_token)

    if not raw_token:
        raise AuthFailedError(
            f"Discord Bot token is not provided. Set the {env_var} environment variable "
            "or use --prompt-token."
        )

    return raw_token
