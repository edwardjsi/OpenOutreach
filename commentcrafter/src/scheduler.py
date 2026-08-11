"""Scheduler — manages rate limits, timing, and the commenting cycle.

Adapted from OpenOutreach's human-rhythm burst/break pattern.
"""
from __future__ import annotations

import logging
import random
import time

from src.config import (
    BURST_MIN_S, BURST_MAX_S,
    BREAK_MIN_S, BREAK_MAX_S,
    DAILY_COMMENT_LIMIT,
)
from src.models import count_daily_comments, get_uncommented_posts, get_daily_stats

logger = logging.getLogger(__name__)


class Rhythm:
    """Human-like activity rhythm: bursts of work separated by breaks."""

    def __init__(self):
        self._reset_burst()

    def _reset_burst(self):
        self._burst_start = time.monotonic()
        self._burst_duration = random.uniform(BURST_MIN_S, BURST_MAX_S)

    def reset(self):
        """Start a fresh burst without taking a break."""
        self._reset_burst()

    def maybe_break(self):
        """Sleep a random break if the current burst is done."""
        if time.monotonic() - self._burst_start < self._burst_duration:
            return
        break_seconds = random.uniform(BREAK_MIN_S, BREAK_MAX_S)
        logger.info("Taking a %dm break", int(break_seconds // 60))
        time.sleep(break_seconds)
        self._reset_burst()


def comments_remaining_today() -> int:
    """How many more comments we can make today before hitting the limit."""
    done = count_daily_comments()
    remaining = DAILY_COMMENT_LIMIT - done
    return max(0, remaining)


def seconds_until_midnight() -> float:
    """Seconds until the next midnight (for daily limit resets)."""
    now = time.localtime()
    midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return max(midnight - time.time(), 0)


def should_continue() -> bool:
    """Check if we can still comment today."""
    remaining = comments_remaining_today()
    if remaining <= 0:
        logger.info("Daily comment limit reached (%d). Sleeping until midnight.", DAILY_COMMENT_LIMIT)
        return False
    return True


def get_pending_queue_size() -> int:
    """Number of posts waiting for comment evaluation/drafting."""
    return len(get_uncommented_posts(limit=999))
