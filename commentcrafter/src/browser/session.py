"""Browser session management — adapted from OpenOutreach.

Manages a single Playwright browser instance with stealth protection,
cookie persistence, and login lifecycle.
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

from src.config import DATA_DIR, MIN_ACTION_DELAY_S, MAX_ACTION_DELAY_S

logger = logging.getLogger(__name__)

COOKIE_FILE = DATA_DIR / "session_cookies.json"
AUTH_COOKIE_NAME = "li_at"


def random_sleep(min_s: float, max_s: float):
    delay = random.uniform(min_s, max_s)
    logger.debug("Pause: %.2fs", delay)
    time.sleep(delay)


class CommenterSession:
    """Manages the Playwright browser for commenting on LinkedIn."""

    def __init__(self):
        # Browser objects — created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def ensure_browser(self):
        """Launch or recover browser + login."""
        from src.browser.login import start_browser

        if not self.page or self.page.is_closed():
            logger.debug("Launching/recovering browser")
            start_browser(self)
        else:
            self._maybe_refresh_cookies()

    def _load_cookies(self) -> dict | None:
        try:
            if COOKIE_FILE.exists():
                return json.loads(COOKIE_FILE.read_text())
        except Exception:
            logger.warning("Failed to load cookies, will re-login")
        return None

    def _save_cookies(self, state: dict):
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(json.dumps(state, indent=2))
        logger.debug("Cookies saved to %s", COOKIE_FILE)

    def _maybe_refresh_cookies(self):
        """Re-login if the auth cookie is expired."""
        state = self._load_cookies()
        if not state:
            return
        for cookie in state.get("cookies", []):
            if cookie.get("name") == AUTH_COOKIE_NAME:
                expires = cookie.get("expires", -1)
                if expires > 0 and expires < time.time():
                    logger.warning("Auth cookie expired — re-authenticating")
                    self.close()
                    from src.browser.login import start_browser
                    start_browser(self)
                    return

    def wait(self, min_s: float = MIN_ACTION_DELAY_S, max_s: float = MAX_ACTION_DELAY_S):
        """Human-like pause between actions."""
        random_sleep(min_s, max_s)

    def close(self):
        if self.context:
            try:
                self.context.close()
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
                logger.info("Browser closed gracefully")
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
            finally:
                self.page = self.context = self.browser = self.playwright = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
