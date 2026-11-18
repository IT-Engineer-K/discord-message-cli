"""Secure Bot token resolution without command-line arguments or logging."""

from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Mapping

from .errors import AuthFailedError, InvalidArgumentsError

DEFAULT_TOKEN_ENV = "DISCORD_BOT_TOKEN"


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

    if raw_token.lower().startswith("bot "):
        raw_token = raw_token[4:].strip()

    if not raw_token:
        raise AuthFailedError(
            f"Discord Bot token is not provided. Set the {env_var} environment variable "
            "or use --prompt-token."
        )

    return raw_token
