# linkedin/diagnostics.py
"""Capture page state on automation failures for post-mortem debugging."""
from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from datetime import datetime

from playwright.sync_api import Error as PlaywrightError

from linkedin.conf import DIAGNOSTICS_CAPTURE_TIMEOUT_S, DIAGNOSTICS_DIR

logger = logging.getLogger(__name__)


def _target_is_dead(error: BaseException) -> bool:
    """True when the error means the page target/browser is gone.

    On a crashed/closed target every Playwright call raises immediately and
    there is nothing to capture. Skipping avoids the old thread-based
    capture's failure mode: calling the sync API from a non-owner thread
    died with greenlet.error and left orphaned asyncio tasks that surfaced
    as "Exception in callback" / "Task exception was never retrieved" noise
    when the daemon closed the session.
    """
    if not isinstance(error, PlaywrightError):
        return False
    msg = str(error).lower()
    return "crashed" in msg or "closed" in msg or "connection reset" in msg


def _capture_page_state(page, folder) -> None:
    """Write page HTML + screenshot into the failure folder. Best effort.

    Order matters: screenshot() is the only call with a hard client-side
    timeout (DIAGNOSTICS_CAPTURE_TIMEOUT_S), content() has none. A dead or
    frozen renderer raises/times out on the screenshot, so content() is only
    attempted on a renderer that just proved responsive — the daemon can
    never block on content() the way it did before the timeout existed.
    """
    try:
        page.screenshot(
            path=str(folder / "screenshot.png"),
            timeout=DIAGNOSTICS_CAPTURE_TIMEOUT_S * 1000,
        )
    except Exception as exc:
        logger.debug("Failed to capture screenshot: %s", exc)
        return

    try:
        (folder / "page.html").write_text(page.content())
    except Exception as exc:
        logger.debug("Failed to capture HTML: %s", exc)


def capture_failure(session, error: BaseException) -> None:
    """Save page HTML, screenshot, and error details into a per-failure folder."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    error_name = type(error).__name__
    folder = DIAGNOSTICS_DIR / f"{timestamp}_{error_name}"
    folder.mkdir(parents=True, exist_ok=True)

    # Error traceback
    tb = traceback.format_exception(type(error), error, error.__traceback__)
    (folder / "error.txt").write_text("".join(tb))

    page = getattr(session, "page", None)
    if page is None or page.is_closed():
        logger.debug("No live page — skipping HTML/screenshot capture")
        (folder / "page.html").write_text("<!-- page was None or closed -->")
        return

    if _target_is_dead(error):
        logger.debug("Target crashed/closed — skipping HTML/screenshot capture")
        (folder / "page.html").write_text(
            f"<!-- capture skipped: {type(error).__name__}: {error} -->"
        )
        return

    _capture_page_state(page, folder)

    logger.info("Failure diagnostics saved → %s", folder)


@contextmanager
def failure_diagnostics(session):
    """Context manager that captures diagnostics on unhandled exceptions."""
    try:
        yield
    except Exception as exc:
        try:
            capture_failure(session, exc)
        except Exception as cap_exc:
            logger.debug("Diagnostic capture itself failed: %s", cap_exc)
        raise
