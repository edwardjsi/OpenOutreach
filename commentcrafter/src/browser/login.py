"""Playwright browser startup, login, and cookie persistence.

Reuses the same stealth and selector patterns as OpenOutreach.
"""
from __future__ import annotations

import logging
from urllib.parse import unquote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from src.config import (
    BROWSER_DEFAULT_TIMEOUT_MS,
    BROWSER_LOGIN_TIMEOUT_MS,
    BROWSER_NAV_TIMEOUT_MS,
    BROWSER_SLOW_MS,
    HUMAN_TYPE_MIN_DELAY_MS,
    HUMAN_TYPE_MAX_DELAY_MS,
)
from src.browser.nav import (
    goto_page,
    human_type,
    resolve_locator,
    await_checkpoint_resolution,
    CheckpointChallengeError,
)

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_CHECKPOINT = "/checkpoint/challenge/"

# ── Selector chains ────────────────────────────────────────────

EMAIL_LOCATORS = [
    lambda p: p.get_by_role("textbox", name="Email or phone"),
    lambda p: p.get_by_label("Email or phone"),
    lambda p: p.locator('input[autocomplete="webauthn"]'),
    lambda p: p.locator('input[name="session_key"]'),
    lambda p: p.locator('input#username'),
]

PASSWORD_LOCATORS = [
    lambda p: p.locator('input[type="password"]'),
    lambda p: p.locator('input[autocomplete="current-password"]'),
    lambda p: p.get_by_role("textbox", name="Password"),
    lambda p: p.locator('input[name="session_password"]'),
    lambda p: p.locator('input#password'),
]

SUBMIT_LOCATORS = [
    lambda p: p.locator("form").get_by_role("button", name="Sign in", exact=True),
    lambda p: p.get_by_role("button", name="Sign in", exact=True),
    lambda p: p.locator('form button[type="submit"]'),
]

COMPLY_LOCATORS = [
    lambda p: p.locator('button#content__button--primary--muted'),
    lambda p: p.get_by_role("button", name="Agree to comply", exact=True),
    lambda p: p.locator('button.content__button--primary'),
]

COMPLY_PROBE_TIMEOUT_MS = 5000


def dismiss_comply_gate(page, timeout_ms: int = COMPLY_PROBE_TIMEOUT_MS) -> bool:
    """Click LinkedIn's 'Agree to comply' interstitial if present."""
    for factory in COMPLY_LOCATORS:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        logger.info(colored("Dismissing 'Agree to comply' interstitial", "yellow"))
        locator.click()
        return True
    return False


def playwright_login(session):
    """Perform a fresh LinkedIn login with email + password.

    Credentials are read from DB config or prompted interactively.
    """
    from src.models import get_config

    page = session.page
    logger.info(colored("Fresh login sequence starting", "cyan"))

    # Read credentials from config or prompt
    email = get_config("linkedin_email", "")
    password = get_config("linkedin_password", "")

    if not email:
        email = input("LinkedIn email/phone: ").strip()
        from src.models import set_config
        set_config("linkedin_email", email)

    if not password:
        import getpass
        password = getpass.getpass("LinkedIn password: ")
        from src.models import set_config
        set_config("linkedin_password", password)

    goto_page(
        session,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load login page",
        timeout=BROWSER_NAV_TIMEOUT_MS,
    )

    human_type(resolve_locator(page, EMAIL_LOCATORS), email)
    session.wait(1, 2)
    human_type(resolve_locator(page, PASSWORD_LOCATORS), password)
    session.wait(1, 2)

    submit = resolve_locator(page, SUBMIT_LOCATORS)
    submit.click()
    dismiss_comply_gate(page)

    goto_page(
        session,
        action=lambda: None,
        expected_url_pattern="/feed",
        timeout=BROWSER_LOGIN_TIMEOUT_MS,
        error_message="Login failed — no redirect to feed",
    )


def launch_browser(storage_state=None):
    """Create a new Playwright browser with stealth."""
    logger.debug("Launching Playwright")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False, slow_mo=BROWSER_SLOW_MS)
    context = browser.new_context(storage_state=storage_state)
    context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, pw


def start_browser(session):
    """Ensure a Playwright browser is running and authenticated.

    Uses saved cookies if available, otherwise performs a fresh login.
    """
    from src.models import get_config

    logger.debug("Starting browser session")

    cookie_data = session._load_cookies()  # noqa: SLF001
    storage_state = cookie_data if cookie_data else None

    if storage_state:
        logger.info("Loading saved session")

    session.page, session.context, session.browser, session.playwright = \
        launch_browser(storage_state=storage_state)

    if not storage_state:
        playwright_login(session)
        session._save_cookies(session.context.storage_state())  # noqa: SLF001
        logger.info(colored("Login successful – session saved", "green", attrs=["bold"]))
    else:
        session.page.goto(LINKEDIN_FEED_URL)
        dismiss_comply_gate(session.page)
        goto_page(
            session,
            action=lambda: None,
            expected_url_pattern="/feed",
            timeout=BROWSER_DEFAULT_TIMEOUT_MS,
            error_message="Saved session invalid",
        )
        session._save_cookies(session.context.storage_state())  # noqa: SLF001
        logger.info("Saved session restored — cookies refreshed")

    session.page.wait_for_load_state("domcontentloaded")
    logger.info(colored("Browser ready", "green", attrs=["bold"]))


def interactive_login(session):
    """Interactive login — prompts for credentials and saves session cookies.

    Called from the command line. Saves cookies to data/session_cookies.json.
    """
    from src.models import set_config

    session.ensure_browser()
    logger.info("Interactive login complete. Cookies saved to %s", session._load_cookies())  # noqa: SLF001
    return True
