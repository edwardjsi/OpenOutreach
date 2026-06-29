"""Checkpoint challenge notifications — terminal bell + optional Telegram push.

When LinkedIn serves a security checkpoint, ``notify_checkpoint(session)`` is
called **once** to alert the operator. The bell + colored banner always fires
(zero-config fallback). If ``SiteConfig.telegram_bot_token`` and
``telegram_chat_id`` are both set, a Telegram message is sent via the Bot API.

All network/parse errors are caught and logged at WARNING — this module never
raises, so a notification failure cannot crash the daemon.
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from linkedin.conf import TELEGRAM_API_BASE, TELEGRAM_TIMEOUT_S

logger = logging.getLogger(__name__)


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    """POST a message to the Telegram Bot API ``/sendMessage`` endpoint.

    Returns True on success, False on any failure. Never raises.
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return True
            logger.warning(
                "Telegram API returned ok=false: %s",
                body.get("description", "unknown"),
            )
            return False
    except Exception:
        logger.warning("Failed to send Telegram alert", exc_info=True)
        return False


def _telegram_message(session) -> str:
    """Build the Telegram alert message body."""
    # Try to identify the account; fall back gracefully.
    account = "unknown"
    try:
        profile = getattr(session, "linkedin_profile", None)
        if profile is not None:
            account = getattr(profile, "linkedin_username", None) or "unknown"
    except Exception:
        pass

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "🔒 *LinkedIn Security Checkpoint*\n\n"
        f"Account: `{account}`\n"
        f"Time: {ts}\n\n"
        "The daemon is *paused* and waiting for manual resolution.\n\n"
        "1. Open VNC: http://localhost:6080/vnc.html\n"
        "   (or connect a VNC client to localhost:5900)\n"
        "2. Solve the checkpoint challenge in the browser\n"
        "3. The daemon will *resume automatically* once you reach /feed\n\n"
        "_No further LinkedIn requests will be made until you intervene._"
    )


def notify_checkpoint(session) -> None:
    """Alert the operator that the daemon is blocked on a LinkedIn checkpoint.

    Always emits a terminal bell + colored log banner. Additionally sends a
    Telegram push if ``SiteConfig`` has both ``telegram_bot_token`` and
    ``telegram_chat_id`` configured. Never raises.
    """
    # ── Terminal bell + colored banner (always fires, zero-config) ──
    bell = "\a"
    banner = (
        f"\n{bell}"
        "╔══════════════════════════════════════════════════════════════╗\n"
        "║  ⚠️  LINKEDIN SECURITY CHECKPOINT — MANUAL ACTION REQUIRED   ║\n"
        "║                                                              ║\n"
        "║  The daemon is PAUSED and waiting for you to solve the      ║\n"
        "║  checkpoint challenge via VNC. No further LinkedIn requests  ║\n"
        "║  will be made until you intervene.                           ║\n"
        "║                                                              ║\n"
        "║  1. Open noVNC:  http://localhost:6080/vnc.html              ║\n"
        "║     (or VNC client → localhost:5900)                         ║\n"
        "║  2. Solve the challenge in the browser                       ║\n"
        "║  3. The daemon resumes automatically once /feed loads        ║\n"
        "╚══════════════════════════════════════════════════════════════╝\n"
    )
    print(banner, file=sys.stderr, flush=True)
    logger.error(banner)

    # ── Telegram push (optional, if configured) ──
    try:
        from linkedin.models import SiteConfig
        config = SiteConfig.load()
        token = (config.telegram_bot_token or "").strip()
        chat_id = (config.telegram_chat_id or "").strip()
        if token and chat_id:
            text = _telegram_message(session)
            if _send_telegram(token, chat_id, text):
                logger.info("Telegram checkpoint alert sent to %s", chat_id)
            else:
                logger.warning("Telegram checkpoint alert failed — see warnings above")
        else:
            logger.info(
                "Telegram not configured (telegram_bot_token/telegram_chat_id empty) "
                "— only bell+banner notification was sent"
            )
    except Exception:
        logger.warning("Failed to send Telegram checkpoint alert", exc_info=True)
