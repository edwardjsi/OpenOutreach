"""Tests for checkpoint handling — circuit breaker (daemon halt).

A LinkedIn security checkpoint sets ``SiteConfig.daemon_halt``: the daemon
idles with zero network traffic until the operator solves the challenge,
clears the flag in Admin, and restarts. There is no auto-resume.
"""
import pytest
from unittest.mock import patch

from linkedin.browser.nav import goto_page, await_checkpoint_resolution
from linkedin.exceptions import CheckpointChallengeError
from linkedin.models import SiteConfig


class FakePage:
    """Fake Playwright page with a controllable URL."""

    def __init__(self, urls: list[str]):
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
        pass


class FakeSession:
    """Minimal session stand-in for goto_page."""

    def __init__(self, page):
        self.page = page

    def wait(self):
        pass


@pytest.mark.django_db
class TestCheckpointHalt:
    def _reset(self):
        cfg = SiteConfig.load()
        cfg.daemon_halt = False
        cfg.daemon_halt_reason = ""
        cfg.save(update_fields=["daemon_halt", "daemon_halt_reason"])

    def test_checkpoint_sets_halt_and_notifies_once(self):
        """First checkpoint detection notifies and sets the halt flag."""
        self._reset()
        page = FakePage(["https://www.linkedin.com/checkpoint/challenge/abc"])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint") as mock_notify:
            await_checkpoint_resolution(session, page)
            mock_notify.assert_called_once()

        cfg = SiteConfig.load()
        assert cfg.daemon_halt is True
        assert "checkpoint" in cfg.daemon_halt_reason.lower()

    def test_repeat_detection_does_not_renotify(self):
        """Subsequent detections are no-ops once the halt flag is set."""
        self._reset()
        page = FakePage(["https://www.linkedin.com/checkpoint/challenge/abc"])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint") as mock_notify:
            await_checkpoint_resolution(session, page)
            await_checkpoint_resolution(session, page)

        assert mock_notify.call_count == 1

    def test_goto_page_raises_challenge_error_when_still_on_checkpoint(self):
        """After halting, goto_page surfaces CheckpointChallengeError so the
        daemon loop drops the task and enters the halted idle state."""
        self._reset()
        page = FakePage(["https://www.linkedin.com/checkpoint/challenge/abc"])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint"):
            with pytest.raises(CheckpointChallengeError):
                goto_page(
                    session,
                    action=lambda: None,
                    expected_url_pattern="/feed",
                    error_message="should not see this",
                )

    def test_non_checkpoint_mismatch_still_raises_runtime_error(self):
        """The halt flag is enforced by the daemon loop, not per-navigation —
        a plain URL mismatch still raises RuntimeError."""
        self._reset()
        cfg = SiteConfig.load()
        cfg.daemon_halt = True
        cfg.daemon_halt_reason = "test"
        cfg.save(update_fields=["daemon_halt", "daemon_halt_reason"])

        page = FakePage(["https://www.linkedin.com/some-other-page/"])
        session = FakeSession(page)

        with patch("linkedin.browser.nav.notify_checkpoint"):
            with pytest.raises(RuntimeError, match="expected"):
                goto_page(
                    session,
                    action=lambda: None,
                    expected_url_pattern="/feed",
                    error_message="Login failed",
                )

    def test_404_still_raises_skipprofile(self):
        """A 404 URL still raises SkipProfile."""
        from linkedin.exceptions import SkipProfile

        self._reset()
        page = FakePage(["https://www.linkedin.com/404/"])
        session = FakeSession(page)

        with pytest.raises(SkipProfile):
            goto_page(
                session,
                action=lambda: None,
                expected_url_pattern="/feed",
                error_message="should not see this",
            )
