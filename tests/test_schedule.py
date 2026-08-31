from __future__ import annotations

import time
from unittest.mock import Mock, patch

from linkedin.daemon import _now, sleep_with_heartbeat, work_shift_active


def _mock_config(*, enable=True, shift_hours=2):
    """Return a Mock that walks like a SiteConfig row."""
    cfg = Mock()
    cfg.enable_active_hours = enable
    cfg.work_shift_hours = shift_hours
    return cfg


class TestWorkShiftActive:
    """The daemon works for ``work_shift_hours`` from its start, then idles."""

    def test_within_shift(self):
        cfg = _mock_config(shift_hours=2)
        with patch("linkedin.daemon._now", return_value=100.0 + 1 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is True

    def test_exactly_at_shift_end(self):
        # End is exclusive: 2h elapsed means the shift is over.
        cfg = _mock_config(shift_hours=2)
        with patch("linkedin.daemon._now", return_value=100.0 + 2 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is False

    def test_past_shift_end(self):
        cfg = _mock_config(shift_hours=2)
        with patch("linkedin.daemon._now", return_value=100.0 + 5 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is False

    def test_disabled_runs_247(self):
        # With the flag off, the daemon keeps working regardless of elapsed time.
        cfg = _mock_config(enable=False)
        with patch("linkedin.daemon._now", return_value=100.0 + 48 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is True

    def test_custom_shift_length(self):
        cfg = _mock_config(shift_hours=5)
        with patch("linkedin.daemon._now", return_value=100.0 + 3 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is True
        with patch("linkedin.daemon._now", return_value=100.0 + 6 * 3600):
            assert work_shift_active(cfg, started_at=100.0) is False

    def test_zero_shift_hours_is_idle(self):
        # A 0-hour shift means the daemon idles immediately (paused).
        cfg = _mock_config(shift_hours=0)
        with patch("linkedin.daemon._now", return_value=100.0):
            assert work_shift_active(cfg, started_at=100.0) is False


class TestNow:
    """``_now`` is never behind the monotonic clock on Linux (BOOTTIME)."""

    def test_boottime_clock(self):
        assert _now() >= time.monotonic()


class TestSleepWithHeartbeat:
    """Idle sleeps end on BOOTTIME deadlines and support live-countdown context."""

    def test_callable_context_is_reinvoked_each_heartbeat(self):
        heartbeat = Mock()
        with (
            patch("linkedin.daemon.HEARTBEAT_SLICE", 0.001),
            patch("linkedin.daemon._now", side_effect=[100.0, 100.0, 100.5, 101.0]),
        ):
            sleep_with_heartbeat(
                1.0, heartbeat, lambda remaining: f"left {remaining:.1f}s",
            )
        assert heartbeat.maybe_log.call_count == 2
        messages = [call.args[0] for call in heartbeat.maybe_log.call_args_list]
        assert messages == ["left 1.0s", "left 0.5s"]

    def test_suspend_time_counts_against_the_sleep(self):
        # A 4h nap advances the BOOTTIME deadline past the end of a 1h sleep,
        # so the sleep ends right after wake instead of resuming the countdown.
        heartbeat = Mock()
        with (
            patch("linkedin.daemon.HEARTBEAT_SLICE", 0.001),
            patch("linkedin.daemon._now", side_effect=[100.0, 100.0, 100.0 + 5 * 3600]),
        ):
            sleep_with_heartbeat(1.0, heartbeat, "static")
        assert heartbeat.maybe_log.call_count == 1
        assert heartbeat.maybe_log.call_args.args[0] == "static"
