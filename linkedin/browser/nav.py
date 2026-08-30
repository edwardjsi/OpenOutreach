# linkedin/browser/nav.py
import logging
import random
import time
from urllib.parse import unquote, urlparse, urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from termcolor import colored

from linkedin.conf import (
    BROWSER_NAV_TIMEOUT_MS,
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
    """Halt the daemon on a LinkedIn security checkpoint.

    Called from ``goto_page`` when the browser lands on a ``/checkpoint/challenge/``
    URL. Sends a single notification (bell + optional Telegram) and sets the
    ``SiteConfig.daemon_halt`` circuit breaker, then returns immediately — the
    daemon loop idles with ZERO requests until the operator clears the flag.

    The operator must: 1) solve the challenge via VNC (open
    ``http://localhost:6080/vnc.html`` or connect to ``localhost:5900``),
    2) clear ``daemon_halt`` in Admin → Site Configuration, and 3) restart
    the daemon. This deliberately replaces the old pause-and-auto-resume:
    an account that LinkedIn flags must not be touched again automatically.
    """
    from linkedin.models import SiteConfig

    config = SiteConfig.load()
    if config.daemon_halt:
        logger.debug("Checkpoint already halted the daemon — waiting for operator")
        return

    notify_checkpoint(session)
    config.daemon_halt = True
    config.daemon_halt_reason = (
        "LinkedIn security checkpoint — solve the challenge via VNC "
        "(http://localhost:6080/vnc.html), then clear this flag and restart the daemon."
    )
    config.save(update_fields=["daemon_halt", "daemon_halt_reason"])

    logger.warning(
        colored("Daemon halted", "red", attrs=["bold"])
        + " — LinkedIn checkpoint. Solve it via VNC, clear 'daemon_halt' in "
          "Admin → Site Configuration, then restart the daemon. No LinkedIn "
          "requests until then."
    )



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
    # Single atomic snapshot. Per-element get_attribute() re-resolves the
    # locator for every link and can hit the 30s default timeout per element
    # on heavy pages with detaching nodes — freezing a task for minutes.
    hrefs = page.locator('a[href*="/in/"]').evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    for href in hrefs:
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
    # LinkedIn's current profile experience (2025+): hashed CSS-module class
    # names, no h1, no data-member-id. The header card (name, headline,
    # connect-state button, More button) is the FIRST descendant <section>
    # inside the "Primary content" wrapper — verified against live page dumps.
    'section[aria-label="Primary content"] section',
    # Legacy layouts (pre-2025 redesign)
    'section:has(div.top-card-background-hero-image)',
    'section[data-member-id]',
    'section.artdeco-card:has(> div.pv-top-card)',
    'section:has(> div[class*="pv-top-card"])',
    'section[componentkey*="com.linkedin.sdui.profile.card"]',
    'section.artdeco-card:has(div.pv-top-card-v2)',
    'section:has(div.profile-card)',
    'div.profile-card',
    'section:has(div[class*="profile-card"])',
    '[data-test-id*="profile-card"]',
]


def find_top_card(session, timeout_s: float = 8.0):
    """Return the visible profile top card, waiting up to ``timeout_s``.

    The top card can render after ``domcontentloaded`` on slow profile
    pages, so a single one-shot selector check is unreliable. Cycle the
    selector list, waiting briefly for each to become visible, until the
    budget is spent. Raises ``SkipProfile`` on failure — callers treat it
    as a transient page-state issue, not a property of the profile.
    """
    page = session.page
    per_selector_ms = max(int(timeout_s * 1000 / max(len(TOP_CARD_SELECTORS), 1)), 50)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for selector in TOP_CARD_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=per_selector_ms)
                return locator
            except PlaywrightTimeoutError:
                continue
    logger.warning("Top card not found on %s", page.url)
    raise SkipProfile("Top Card section not found")


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
