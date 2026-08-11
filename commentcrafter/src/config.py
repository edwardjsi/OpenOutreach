"""App configuration, paths, and constants."""
from __future__ import annotations

from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
PROMPTS_DIR = ROOT_DIR / "templates" / "prompts"
DB_PATH = DATA_DIR / "commentcrafter.db"

# ── Browser ─────────────────────────────────────────────────────
BROWSER_SLOW_MS = 200
BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_LOGIN_TIMEOUT_MS = 40_000
BROWSER_NAV_TIMEOUT_MS = 15_000
HUMAN_TYPE_MIN_DELAY_MS = 50
HUMAN_TYPE_MAX_DELAY_MS = 200

# ── Timing / human rhythm ──────────────────────────────────────
MIN_ACTION_DELAY_S = 10
MAX_ACTION_DELAY_S = 20

BURST_MIN_S = 1800    # 30 min
BURST_MAX_S = 3000    # 50 min
BREAK_MIN_S = 600     # 10 min
BREAK_MAX_S = 1200    # 20 min

# Daily rate limit (how many comments per day on OTHER people's posts)
DAILY_COMMENT_LIMIT = 10

# ── Checkpoint ──────────────────────────────────────────────────
CHECKPOINT_POLL_INTERVAL_S = 5
CHECKPOINT_HEARTBEAT_INTERVAL_S = 60

# ── Feed / scrape ──────────────────────────────────────────────
FEED_SCROLL_COUNT = 3
FEED_SCROLL_PAUSE_MIN = 2
FEED_SCROLL_PAUSE_MAX = 4
SEARCH_PAUSE_S = 6

# ── LLM ────────────────────────────────────────────────────────
LLM_DEFAULT_PROVIDER = "openai"
LLM_DEFAULT_MODEL = "gpt-4o-mini"
LLM_MAX_RETRIES = 8
LLM_TIMEOUT_S = 90

# ── Comment quality ────────────────────────────────────────────
MIN_COMMENT_CHARS = 60
MAX_COMMENT_CHARS = 1250
