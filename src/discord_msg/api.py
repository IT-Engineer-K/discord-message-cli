"""Public Python API convenience functions for discord-msg."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from .auth import DEFAULT_TOKEN_ENV, resolve_token
from .client import (
    DiscordClient,
    HttpTransport,
    MessageListResult,
    SendResult,
    ThreadResult,
    WaitResult,
)


def _resolve_client(
    *,
    client: DiscordClient | None = None,
    token: str | None = None,
    env: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> DiscordClient:
    if client is not None:
        return client
    if token is not None and token.strip():
        resolved_token = token.strip()
        if resolved_token.lower().startswith("bot "):
            resolved_token = resolved_token[4:].strip()
    else:
        environ = env if env is not None else os.environ
        resolved_token = resolve_token(
            prompt_token=False,
            token_stdin=False,
            env_var=DEFAULT_TOKEN_ENV,
            env=environ,
            stdin=None,
        )
    return DiscordClient(resolved_token, transport=transport)


def create_thread(
    channel_id: str,
    message_id: str,
    name: str,
    *,
    auto_archive_duration: int | None = None,
    rate_limit_per_user: int | None = None,
    token: str | None = None,
    client: DiscordClient | None = None,
    env: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> ThreadResult:
    """Create a thread starting from an existing message."""
    resolved = _resolve_client(client=client, token=token, env=env, transport=transport)
    return resolved.create_thread(
        channel_id,
        message_id,
        name,
        auto_archive_duration=auto_archive_duration,
        rate_limit_per_user=rate_limit_per_user,
    )


def send_message(
    channel_id: str | None = None,
    content: str = "",
    *,
    thread_id: str | None = None,
    allow_mentions: bool = False,
    token: str | None = None,
    client: DiscordClient | None = None,
    env: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> SendResult:
    """Send a message to a Discord channel or thread (mentions disabled by default)."""
    resolved = _resolve_client(client=client, token=token, env=env, transport=transport)
    return resolved.send_message(
        channel_id,
        content,
        thread_id=thread_id,
        allow_mentions=allow_mentions,
    )


def list_messages(
    channel_id: str | None = None,
    *,
    thread_id: str | None = None,
    after: str | None = None,
    limit: int = 50,
    author_id: str | None = None,
    author_ids: Sequence[str] | None = None,
    exclude_author_ids: Sequence[str] | None = None,
    exclude_bots: bool = False,
    max_pages: int = 10,
    token: str | None = None,
    client: DiscordClient | None = None,
    env: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> MessageListResult:
    """List messages in a channel or thread, optionally restricted to after a message_id cursor."""
    resolved = _resolve_client(client=client, token=token, env=env, transport=transport)
    return resolved.list_messages(
        channel_id,
        thread_id=thread_id,
        after=after,
        limit=limit,
        author_id=author_id,
        author_ids=author_ids,
        exclude_author_ids=exclude_author_ids,
        exclude_bots=exclude_bots,
        max_pages=max_pages,
    )


def wait_for_messages(
    channel_id: str | None = None,
    *,
    thread_id: str | None = None,
    after: str | None = None,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
    limit: int = 50,
    min_messages: int = 1,
    author_id: str | None = None,
    author_ids: Sequence[str] | None = None,
    exclude_author_ids: Sequence[str] | None = None,
    exclude_bots: bool = False,
    token: str | None = None,
    client: DiscordClient | None = None,
    env: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> WaitResult:
    """Wait until matching new messages arrive in a channel or thread, or timeout elapses."""
    resolved = _resolve_client(client=client, token=token, env=env, transport=transport)
    return resolved.wait_for_messages(
        channel_id,
        thread_id=thread_id,
        after=after,
        timeout=timeout,
        poll_interval=poll_interval,
        limit=limit,
        min_messages=min_messages,
        author_id=author_id,
        author_ids=author_ids,
        exclude_author_ids=exclude_author_ids,
        exclude_bots=exclude_bots,
    )
