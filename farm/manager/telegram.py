"""Telegram Bot API integration for manager alerts.

Sends messages when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in the environment;
otherwise logs the message and skips network calls.
Includes deduplication and rate-limiting.
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

import httpx
import structlog

from farm.secrets import redact

log = structlog.get_logger(__name__)

# Rate limiting: max 20 messages per 60 seconds (Telegram allows ~30/sec, but group rate limit is 20/min)
MAX_MESSAGES_PER_WINDOW = 20
WINDOW_SECONDS = 60.0

_recent_timestamps: deque[float] = deque()
_sent_dedup_cache: dict[str, float] = {}
DEDUP_WINDOW_SECONDS = 3600.0  # 1 hour deduplication window


def _is_rate_limited(now: float) -> bool:
    while _recent_timestamps and _recent_timestamps[0] <= now - WINDOW_SECONDS:
        _recent_timestamps.popleft()
    if len(_recent_timestamps) >= MAX_MESSAGES_PER_WINDOW:
        return True
    return False


def _record_send(now: float) -> None:
    _recent_timestamps.append(now)


def is_telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def clear_telegram_state() -> None:
    """Clear rate limiting and deduplication state (useful for tests)."""
    _recent_timestamps.clear()
    _sent_dedup_cache.clear()


async def send_telegram_alert(
    message: str,
    *,
    dedup_key: str | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Send an alert message via Telegram Bot API.

    Returns True if sent, or False if skipped / rate-limited / error.
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    target_chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not target_chat:
        log.info("telegram.skipped", reason="not_configured", message=redact(message))
        return False

    now = time.monotonic()

    # Deduplication
    cache_key = dedup_key or message
    if cache_key in _sent_dedup_cache:
        last_sent = _sent_dedup_cache[cache_key]
        if now - last_sent < DEDUP_WINDOW_SECONDS:
            log.info("telegram.dedup_skipped", key=cache_key)
            return False

    # Clean old cache entries
    expired = [k for k, v in _sent_dedup_cache.items() if now - v >= DEDUP_WINDOW_SECONDS]
    for k in expired:
        del _sent_dedup_cache[k]

    # Rate limiting
    if _is_rate_limited(now):
        log.warning("telegram.rate_limited", message=redact(message))
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await http_client.post(url, json=payload)
        if resp.status_code == 200:
            _record_send(now)
            _sent_dedup_cache[cache_key] = now
            log.info("telegram.sent", chat_id=target_chat, length=len(message))
            return True
        else:
            log.warning("telegram.error", status_code=resp.status_code, body=resp.text)
            return False
    except Exception as exc:
        log.warning("telegram.failed", error=redact(str(exc)))
        return False
    finally:
        if owns_client:
            await http_client.aclose()
