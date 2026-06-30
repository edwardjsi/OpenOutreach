# linkedin/browser/nav.py
import logging
import random
import time
from urllib.parse import unquote, urlparse, urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.conf import (
    BROWSER_NAV_TIMEOUT_MS,
    CHECKPOINT_HEARTBEAT_INTERVAL_S,
    CHECKPOINT_POLL_INTERVAL_S,
    DUMP_PAGES,
    FIXTURE_PAGES_DIR,
    HUMAN_TYPE_MIN_DELAY_MS,
    HUMAN_TYPE_MAX_DELAY_MS,
)
from linkedin.exceptions import CheckpointChallengeError, SkipProfile
from linkedin.notifications import notify_checkpoint

logger = logging.getLogger(__name__)


def goto_page(session,
              action,
              expected_url_pattern: str,
              timeout: int = BROWSER_NAV_TIMEOUT_MS,
              error_message: str = "",
              ):
    page = session.page
    action()
    if not page:
        return

    try:
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass  # we still continue and check URL below

    session.wait()

    current = unquote(page.url)
    if expected_url_pattern not in current:
        if "/404" in current:
            raise SkipProfile(f"Profile returned 404 → {current}")
        if "/checkpoint/challenge/" in current:
            await_checkpoint_resolution(session, page)
            current = unquote(page.url)
            if "/checkpoint/challenge/" in current:
                raise CheckpointChallengeError(
                    f"Checkpoint not resolved → still on {current}"
                )
            # Resolved — fall through; expected pattern may now match.
            if _is_still_blocked(current):
                logger.warning(
                    "Post-checkpoint page is still blocked (%s) — "
                    "re-authenticating before proceeding.", current,
                )
                session.reauthenticate()
                current = unquote(session.page.url)
        if expected_url_pattern not in current:
            raise RuntimeError(f"{error_message} → expected '{expected_url_pattern}' | got '{current}'")

    logger.debug("Navigated to %s", page.url)


def await_checkpoint_resolution(session, page) -> None:
    """Block until the LinkedIn security checkpoint is resolved via VNC.

    Called from ``goto_page`` when the browser lands on a ``/checkpoint/challenge/``
    URL. Sends a single notification (bell + optional Telegram), then polls the
    page URL every ``CHECKPOINT_POLL_INTERVAL_S`` seconds. Logs a heartbeat once
    per ``CHECKPOINT_HEARTBEAT_INTERVAL_S`` so the daemon never goes silent.

    Returns when the URL no longer contains ``/checkpoint/challenge/``.
    No hard timeout — the operator solves the challenge via VNC (open
    ``http://localhost:6080/vnc.html`` or connect to ``localhost:5900``).
    Escape via Ctrl+C.
    """
    notify_checkpoint(session)

    logger.warning(
        "Blocking on LinkedIn checkpoint — daemon paused until manual VNC resolution. "
        "Open http://localhost:6080/vnc.html (or VNC localhost:5900) and solve the challenge."
    )

    next_heartbeat = time.monotonic() + CHECKPOINT_HEARTBEAT_INTERVAL_S
    while True:
        current = unquote(page.url)

        # ── Checkpoint resolved? ──────────────────────────────────────
        # Must leave /checkpoint/challenge/ entirely for the checkpoint to
        # be considered resolved. If the URL is /login or /flagship-web we
        # are still blocked — keep looping.
        is_resolved = "/checkpoint/challenge/" not in current
        if is_resolved and not _is_still_blocked(current):
            logger.info("Checkpoint resolved — URL is now %s. Resuming daemon.", current)
            return

        # Also check via JavaScript to catch VNC-initiated navigation
        try:
            js_url = page.evaluate("window.location.href")
            if js_url and "/checkpoint/challenge/" not in unquote(js_url):
                is_js_resolved = True
                if is_js_resolved and not _is_still_blocked(js_url):
                    logger.info(
                        "Checkpoint resolved (via JS) — URL is now %s. Resuming daemon.", unquote(js_url),
                    )
                    return
        except Exception:
            pass  # page might be closed or stale

        now = time.monotonic()
        if now >= next_heartbeat:
            logger.info(
                "alive — waiting for LinkedIn checkpoint resolution "
                "(open VNC and solve the challenge to resume) "
                "current URL: %s",
                current,
            )
            next_heartbeat = now + CHECKPOINT_HEARTBEAT_INTERVAL_S

        time.sleep(CHECKPOINT_POLL_INTERVAL_S)



_RESOLVED_PATTERNS = ("/feed", "/in/", "/mynetwork/", "/jobs/", "/messaging/")
_BLOCKED_PATTERNS = (
    "/login",
    "/checkpoint/",
    "/auth/",
    "flagship-web/login",
    "?vcd=",
)


def _is_still_blocked(url: str) -> bool:
    """Check if the URL is still on a blocked page after checkpoint resolution.

    After the checkpoint challenge page is resolved (URL leaves ``/checkpoint/challenge/``),
    the browser may land on a LinkedIn login page if the verification code was wrong,
    or on a ``flagship-web`` page if the session was consumed. In either case, the
    daemon should **not** unblock — the user needs to continue on VNC.

    Returns ``True`` if any blocked pattern is in the URL **and** no resolved pattern is
    present. ``False`` means the checkpoint is fully resolved and the daemon can resume.
    """
    url_lower = url.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat in url_lower:
            # Only "still blocked" if NO resolved pattern is present
            for rpat in _RESOLVED_PATTERNS:
                if rpat in url_lower:
                    return False
            return True
    return False


def extract_in_urls(page):
    """Extract all /in/ profile URLs from the current page."""
    from linkedin.url_utils import url_to_public_id

    seen = set()
    urls = []
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            if not url_to_public_id(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)
    logger.debug(f"Extracted {len(urls)} unique /in/ profiles")
    return urls


def find_first_visible(page, selectors: list[str]):
    """Try selectors in order, return first locator that matches."""
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    return None


def resolve_locator(page, candidates, timeout_per_ms: int = 5000):
    """Try locator factories in order, return the first one that becomes visible."""
    for factory in candidates:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_per_ms)
            return locator
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError(f"No locator matched on {page.url}")


TOP_CARD_SELECTORS = [
    'section:has(div.top-card-background-hero-image)',
    'section[data-member-id]',
    'section.artdeco-card:has(> div.pv-top-card)',
    'section:has(> div[class*="pv-top-card"])',
    'section[componentkey*="com.linkedin.sdui.profile.card"]',
]


def find_top_card(session):
    top_card = find_first_visible(session.page, TOP_CARD_SELECTORS)
    if top_card is None:
        logger.warning("Top card not found on %s", session.page.url)
        raise SkipProfile("Top Card section not found")
    return top_card


def human_type(locator, text: str, min_delay: int = HUMAN_TYPE_MIN_DELAY_MS, max_delay: int = HUMAN_TYPE_MAX_DELAY_MS):
    """Type text with randomized per-keystroke delay to mimic human input."""
    locator.type(text, delay=random.randint(min_delay, max_delay))


def dump_page_html(session: "AccountSession", profile: dict, category: str = "connect"):
    if not DUMP_PAGES:
        return
    dest = FIXTURE_PAGES_DIR / category
    dest.mkdir(parents=True, exist_ok=True)
    filepath = dest / f"{profile.get('public_identifier')}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved page snapshot → %s", filepath)
