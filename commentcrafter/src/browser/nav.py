"""Navigation helpers — adapted from OpenOutreach.

Shared utilities: page navigation with checkpoint detection,
human-like typing, locator resolution.
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import unquote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.config import (
    BROWSER_NAV_TIMEOUT_MS,
    CHECKPOINT_HEARTBEAT_INTERVAL_S,
    CHECKPOINT_POLL_INTERVAL_S,
    HUMAN_TYPE_MIN_DELAY_MS,
    HUMAN_TYPE_MAX_DELAY_MS,
)

logger = logging.getLogger(__name__)


class CheckpointChallengeError(Exception):
    """LinkedIn security checkpoint could not be resolved."""
    pass


# ── Human typing ────────────────────────────────────────────────


def human_type(locator, text: str, min_delay: int = HUMAN_TYPE_MIN_DELAY_MS,
               max_delay: int = HUMAN_TYPE_MAX_DELAY_MS):
    """Type *text* into *locator* with random per-character delay."""
    locator.click()
    for char in text:
        locator.type(char, delay=random.randint(min_delay, max_delay))


# ── Page navigation ─────────────────────────────────────────────


_RESOLVED_PATTERNS = ("/feed", "/in/", "/mynetwork/", "/jobs/", "/messaging/")
_BLOCKED_PATTERNS = ("/login", "/checkpoint/", "/auth/", "flagship-web/login", "?vcd=")


def _is_still_blocked(url: str) -> bool:
    url_lower = url.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat in url_lower:
            for rpat in _RESOLVED_PATTERNS:
                if rpat in url_lower:
                    return False
            return True
    return False


def goto_page(session, action, expected_url_pattern: str,
              timeout: int = BROWSER_NAV_TIMEOUT_MS,
              error_message: str = ""):
    """Navigate, wait for URL pattern, handle checkpoint challenges."""
    page = session.page
    action()
    if not page:
        return

    try:
        page.wait_for_url(
            lambda url: expected_url_pattern in unquote(url),
            timeout=timeout,
        )
    except PlaywrightTimeoutError:
        pass

    session.wait(1, 2)

    current = unquote(page.url)
    if expected_url_pattern not in current:
        if "/checkpoint/challenge/" in current:
            await_checkpoint_resolution(session, page)
            current = unquote(page.url)
            if "/checkpoint/challenge/" in current:
                raise CheckpointChallengeError("Checkpoint not resolved")
            if _is_still_blocked(current):
                logger.warning("Post-checkpoint page is still blocked (%s)", current)
                raise CheckpointChallengeError("Post-checkpoint page is blocked")
        if expected_url_pattern not in current:
            raise RuntimeError(
                f"{error_message} → expected '{expected_url_pattern}' | got '{current}'"
            )

    logger.debug("Navigated to %s", page.url)


def await_checkpoint_resolution(session, page):
    """Block until LinkedIn security checkpoint is resolved manually.

    The operator connects via VNC and solves the challenge.
    """
    logger.warning(
        "Blocking on LinkedIn checkpoint — daemon paused until manual VNC resolution. "
        "Open http://localhost:6080/vnc.html (or VNC localhost:5900) and solve the challenge."
    )

    next_heartbeat = time.monotonic() + CHECKPOINT_HEARTBEAT_INTERVAL_S
    while True:
        current = unquote(page.url)
        is_resolved = "/checkpoint/challenge/" not in current
        if is_resolved and not _is_still_blocked(current):
            logger.info("Checkpoint resolved — URL: %s. Resuming.", current)
            return

        try:
            js_url = page.evaluate("window.location.href")
            if js_url and "/checkpoint/challenge/" not in unquote(js_url):
                if not _is_still_blocked(js_url):
                    logger.info("Checkpoint resolved (via JS) — resuming.")
                    return
        except Exception:
            pass

        now = time.monotonic()
        if now >= next_heartbeat:
            logger.info("alive — waiting for checkpoint resolution (URL: %s)", current)
            next_heartbeat = now + CHECKPOINT_HEARTBEAT_INTERVAL_S

        time.sleep(CHECKPOINT_POLL_INTERVAL_S)


# ── Locator helpers ─────────────────────────────────────────────


def resolve_locator(page, candidates, timeout_per_ms: int = 5000):
    """Try locator factories in order, return the first visible one."""
    for factory in candidates:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_per_ms)
            return locator
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError(f"No locator matched on {page.url}")
