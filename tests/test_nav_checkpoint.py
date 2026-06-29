"""Tests for checkpoint detection in goto_page.

Uses a fake page object whose ``url`` property transitions from a checkpoint
URL to ``/feed`` after N polls, verifying that goto_page blocks (calls
await_checkpoint_resolution) instead of raising RuntimeError immediately.
"""
import pytest
from unittest.mock import patch, MagicMock

from linkedin.browser.nav import goto_page, await_checkpoint_resolution
from linkedin.exceptions import CheckpointChallengeError


class FakePage:
    """Fake Playwright page with a controllable URL transition."""

    def __init__(self, urls: list[str]):
        """``urls`` is a list of URLs to return on successive ``url`` reads.
        After the list is exhausted, the last URL is returned indefinitely."""
        self._urls = list(urls)
        self._idx = 0
        self._current = self._urls[0]

    @property
    def url(self):
        if self._idx < len(self._urls) - 1:
            self._idx += 1
            self._current = self._urls[self._idx]
        return self._current

    def wait_for_url(self, *args, **kwargs):
        pass  # no-op


class FakeSession:
    """Minimal session stand-in for goto_page."""

    def __init__(self, page):
        self.page = page

    def wait(self):
        pass


class TestGotoPageCheckpoint:
    def test_checkpoint_detected_blocks_then_resolves(self):
        """goto_page should block on checkpoint, then complete when URL → /feed."""
        # URL sequence: checkpoint → checkpoint → /feed (after 2 polls)
        page = FakePage([
            "https://www.linkedin.com/checkpoint/challenge/abc123",
            "https://www.linkedin.com/checkpoint/challenge/abc123",
            "https://www.linkedin.com/feed/",
        ])
        session = FakeSession(page)

        # Patch notify_checkpoint so we don't spam stderr/bell
        with patch("linkedin.browser.nav.notify_checkpoint") as mock_notify, \
             patch("linkedin.browser.nav.time.sleep"):  # no real sleeping
            goto_page(
                session,
                action=lambda: None,
                expected_url_pattern="/feed",
                error_message="should not see this",
            )

        # notify_checkpoint should have been called exactly once
        assert mock_notify.call_count == 1

    def test_non_checkpoint_mismatch_still_raises(self):
        """A non-checkpoint mismatched URL should still raise RuntimeError."""
        page = FakePage(["https://www.linkedin.com/some-other-page/"])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint"), \
             patch("linkedin.browser.nav.time.sleep"):
            with pytest.raises(RuntimeError, match="expected"):
                goto_page(
                    session,
                    action=lambda: None,
                    expected_url_pattern="/feed",
                    error_message="Login failed",
                )

    def test_404_still_raises_skipprofile(self):
        """A 404 URL should still raise SkipProfile."""
        from linkedin.exceptions import SkipProfile

        page = FakePage(["https://www.linkedin.com/404/"])
        session = FakeSession(page)

        with pytest.raises(SkipProfile):
            goto_page(
                session,
                action=lambda: None,
                expected_url_pattern="/feed",
                error_message="should not see this",
            )


class TestAwaitCheckpointResolution:
    def test_returns_when_url_leaves_checkpoint(self):
        """await_checkpoint_resolution returns when URL no longer has /checkpoint/challenge/."""
        page = FakePage([
            "https://www.linkedin.com/checkpoint/challenge/abc",
            "https://www.linkedin.com/feed/",
        ])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint") as mock_notify, \
             patch("linkedin.browser.nav.time.sleep"):
            await_checkpoint_resolution(session, page)

        assert mock_notify.call_count == 1

    def test_notify_called_exactly_once(self):
        """notify_checkpoint fires once, not per poll."""
        page = FakePage([
            "https://www.linkedin.com/checkpoint/challenge/abc",
            "https://www.linkedin.com/checkpoint/challenge/abc",
            "https://www.linkedin.com/checkpoint/challenge/abc",
            "https://www.linkedin.com/feed/",
        ])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint") as mock_notify, \
             patch("linkedin.browser.nav.time.sleep"):
            await_checkpoint_resolution(session, page)

        assert mock_notify.call_count == 1
