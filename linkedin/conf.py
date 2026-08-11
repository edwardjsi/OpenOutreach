# linkedin/conf.py
from __future__ import annotations

from pathlib import Path


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent

PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

DIAGNOSTICS_DIR = Path("/tmp/openoutreach-diagnostics")

# Hard deadline for failure-diagnostics capture. A frozen renderer can make
# page.content()/screenshot() block forever. The capture runs inline on the
# main thread — the Playwright sync API cannot be driven from another thread —
# so the bound comes from screenshot(timeout=...), which Playwright enforces
# client-side even on a frozen renderer; the untimed content() is only
# attempted after the screenshot succeeds.
DIAGNOSTICS_CAPTURE_TIMEOUT_S = 15

FASTEMBED_CACHE_DIR = ROOT_DIR / ".cache" / "fastembed"

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"
DUMP_PAGES = False

MIN_DELAY = 5
MAX_DELAY = 8

# ----------------------------------------------------------------------
# Browser config
# ----------------------------------------------------------------------
BROWSER_SLOW_MO = 200
BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_LOGIN_TIMEOUT_MS = 40_000
BROWSER_NAV_TIMEOUT_MS = 10_000
HUMAN_TYPE_MIN_DELAY_MS = 50
HUMAN_TYPE_MAX_DELAY_MS = 200

# Chromium runs headed on a bare Xvfb display (no window manager). Without a
# WM the window is never mapped/focused, Chromium treats it as occluded, and
# throttles rendering/timers — page work stalls until an X interaction (a VNC
# click) wakes the compositor. These flags disable that throttling, and the
# explicit window size keeps the window fully on-screen (not occluded).
BROWSER_ARGS = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--window-position=0,0",
    "--window-size=1920,1080",
    # Docker's default /dev/shm is 64MB; heavy pages (LinkedIn profiles)
    # exceed it and Chromium's renderer dies with "Target crashed". Use /tmp
    # instead of /dev/shm for shared memory.
    "--disable-dev-shm-usage",
]

# ----------------------------------------------------------------------
# Checkpoint challenge (security verification) manual resolution
# ----------------------------------------------------------------------
CHECKPOINT_POLL_INTERVAL_S = 5
CHECKPOINT_HEARTBEAT_INTERVAL_S = 60

# ----------------------------------------------------------------------
# Telegram Bot API for checkpoint alerts
# ----------------------------------------------------------------------
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_S = 10

# ----------------------------------------------------------------------
# Onboarding defaults (shown to user during interactive setup)
# ----------------------------------------------------------------------
DEFAULT_CONNECT_DAILY_LIMIT = 20
DEFAULT_CONNECT_WEEKLY_LIMIT = 100
DEFAULT_FOLLOW_UP_DAILY_LIMIT = 25

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
CAMPAIGN_CONFIG = {
    "check_pending_recheck_after_hours": 24,
    "min_action_interval": 120,
    "qualification_n_mc_samples": 100,
    "min_ready_to_connect_prob": 0.9,
    "min_positive_pool_prob": 0.20,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "connect_delay_seconds": 10,
    "connect_no_candidate_delay_seconds": 300,
    "enrich_min_delay_seconds": 6,
    "enrich_max_delay_seconds": 10,
    "enrich_max_per_page": 10,
    "burst_min_seconds": 2700,   # 45 min
    "burst_max_seconds": 3900,   # 65 min
    "break_min_seconds": 600,    # 10 min
    "break_max_seconds": 1200,   # 20 min
}

