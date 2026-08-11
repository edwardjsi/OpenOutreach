"""Tests for saved-session restore in start_browser_session.

A saved session can be restored onto a LinkedIn deep link (e.g. a messaging
thread) instead of /feed. The strict /feed check must not reject that — only
truly blocked pages (login/checkpoint) should trigger a fresh login.

These tests use fake page/profile objects; goto_page is mocked so the real
Playwright navigation never runs.
"""
from types import SimpleNamespace
from unittest.mock import patch

from linkedin.browser.login import start_browser_session


def _make_session(page_url, cookie_data):
    """Build a fake AccountSession-shaped object with a fake page."""
    profile = SimpleNamespace(
        cookie_data=cookie_data,
        refresh_from_db=lambda fields: None,
        save=lambda **kwargs: None,
    )
    page = SimpleNamespace(
        url=page_url,
        goto=lambda *a, **k: None,
        wait_for_load_state=lambda *a, **k: None,
    )
    context = SimpleNamespace(storage_state=lambda: {"cookies": []})
    session = SimpleNamespace(
        linkedin_profile=profile,
        page=page,
        context=context,
        browser=None,
        playwright=None,
    )
    return session, profile, page


def _restore(session, page, goto_error=None):
    """Run start_browser_session with launch/goto mocked."""
    launch = patch(
        "linkedin.browser.login.launch_browser",
        return_value=(page, session.context, None, None),
    )
    goto = patch(
        "linkedin.browser.login.goto_page",
        side_effect=goto_error or RuntimeError("goto_page not expected to run"),
    )
    with launch, goto, patch("linkedin.browser.login.dismiss_comply_gate"), \
            patch("linkedin.browser.login.playwright_login") as pl, \
            patch("linkedin.browser.login._save_cookies") as save:
        start_browser_session(session)
    return pl, save


def test_restore_onto_messaging_deep_link_accepted():
    """Restoring onto a messaging thread must not fail the session.

    Regression for: RuntimeError "Saved session invalid → expected '/feed' |
    got 'https://www.linkedin.com/messaging/thread/2-.../'" seen in production.
    """
    page_url = "https://www.linkedin.com/messaging/thread/2-YzYyYTJjNTktMWIxYS00NzYwLWI4OGUtMWE5MjJhMDQ0MjkxXzEwMA==/"
    session, profile, page = _make_session(page_url, {"cookies": []})
    goto_error = RuntimeError(
        f"Saved session invalid → expected '/feed' | got '{page_url}'"
    )

    playwright_login, save_cookies = _restore(session, page, goto_error)

    playwright_login.assert_not_called()
    save_cookies.assert_called_once_with(session)
    assert profile.cookie_data is not None, "valid deep-link session must keep cookies"


def test_restore_onto_feed_is_accepted():
    """Normal restore straight onto /feed — goto_page succeeds, no re-login."""
    session, profile, page = _make_session(
        "https://www.linkedin.com/feed/", {"cookies": []}
    )

    with patch(
        "linkedin.browser.login.launch_browser",
        return_value=(page, session.context, None, None),
    ), patch("linkedin.browser.login.dismiss_comply_gate"), \
            patch("linkedin.browser.login.goto_page") as goto, \
            patch("linkedin.browser.login.playwright_login") as pl, \
            patch("linkedin.browser.login._save_cookies") as save:
        start_browser_session(session)

    goto.assert_called_once()
    pl.assert_not_called()
    save.assert_called_once_with(session)
    assert profile.cookie_data is not None


def test_restore_onto_login_page_triggers_fresh_login():
    """A blocked page (login) means the saved session is dead — re-login."""
    session, profile, page = _make_session(
        "https://www.linkedin.com/login", {"cookies": []}
    )
    goto_error = RuntimeError(
        "Saved session invalid → expected '/feed' | got 'https://www.linkedin.com/login'"
    )

    playwright_login, save_cookies = _restore(session, page, goto_error)

    playwright_login.assert_called_once_with(session)
    save_cookies.assert_called_once_with(session)
    assert profile.cookie_data is None, "dead session must wipe cookies before re-login"
