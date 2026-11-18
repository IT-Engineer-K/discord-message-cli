"""Domain errors and exit codes for Discord Message CLI."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INVALID_ARGS = 2
    AUTH_FAILED = 3
    PERMISSION_DENIED = 4
    NOT_FOUND = 5
    RATE_LIMITED = 6
    FETCH_FAILED = 7


class DiscordMsgError(Exception):
    """Base error with stable error code and exit code."""

    code: str = "ERROR"
    exit_code: int = int(ExitCode.FETCH_FAILED)

    def __init__(self, message: str, *, code: str | None = None, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = int(exit_code)


class InvalidArgumentsError(DiscordMsgError):
    code = "INVALID_ARGS"
    exit_code = int(ExitCode.INVALID_ARGS)


class AuthFailedError(DiscordMsgError):
    code = "AUTH_FAILED"
    exit_code = int(ExitCode.AUTH_FAILED)


class PermissionDeniedError(DiscordMsgError):
    code = "PERMISSION_DENIED"
    exit_code = int(ExitCode.PERMISSION_DENIED)


class ChannelNotFoundError(DiscordMsgError):
    code = "NOT_FOUND"
    exit_code = int(ExitCode.NOT_FOUND)


class RateLimitExceededError(DiscordMsgError):
    code = "RATE_LIMITED"
    exit_code = int(ExitCode.RATE_LIMITED)


class FetchInterruptedError(DiscordMsgError):
    code = "FETCH_FAILED"
    exit_code = int(ExitCode.FETCH_FAILED)
