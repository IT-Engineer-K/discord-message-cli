"""Discord Message CLI and Python library."""

from .api import create_thread, list_messages, send_message, wait_for_messages
from .client import (
    ChannelInfo,
    CheckResult,
    DiscordClient,
    GuildInfo,
    HttpResponse,
    HttpTransport,
    MessageItem,
    MessageListResult,
    MessageRecord,
    ReadOnlyDiscordClient,
    SendResult,
    ThreadResult,
    UrllibTransport,
    WaitResult,
)
from .errors import (
    AuthFailedError,
    ChannelNotFoundError,
    DiscordMsgError,
    ExitCode,
    FetchInterruptedError,
    InvalidArgumentsError,
    PermissionDeniedError,
    RateLimitExceededError,
)

__version__ = "0.3.0"

__all__ = [
    "__version__",
    "DiscordClient",
    "ReadOnlyDiscordClient",
    "HttpResponse",
    "HttpTransport",
    "UrllibTransport",
    "CheckResult",
    "SendResult",
    "ThreadResult",
    "MessageItem",
    "MessageListResult",
    "MessageRecord",
    "WaitResult",
    "ChannelInfo",
    "GuildInfo",
    "create_thread",
    "send_message",
    "list_messages",
    "wait_for_messages",
    "ExitCode",
    "DiscordMsgError",
    "InvalidArgumentsError",
    "AuthFailedError",
    "PermissionDeniedError",
    "ChannelNotFoundError",
    "RateLimitExceededError",
    "FetchInterruptedError",
]
