"""Data models — plain SQLite via sqlite3 plus Pydantic for in-memory structs.

No Django dependency. Uses a single-file SQLite DB.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from src.config import DB_PATH

logger = logging.getLogger(__name__)

# ── Thread-local connection pool ─────────────────────────────────

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local connection (autocommit)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def tx():
    """Transaction context manager."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tx() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_accounts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                linkedin_url      TEXT NOT NULL UNIQUE,
                public_identifier TEXT,
                active            INTEGER NOT NULL DEFAULT 1,
                added_at          TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS target_topics (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                topic    TEXT NOT NULL UNIQUE,
                active   INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS posts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                source            TEXT NOT NULL DEFAULT 'feed',
                author_name       TEXT NOT NULL,
                author_urn        TEXT,
                post_urn          TEXT,
                text              TEXT NOT NULL,
                url               TEXT,
                scraped_at        TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(post_urn)  ON CONFLICT IGNORE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id           INTEGER NOT NULL REFERENCES posts(id),
                body              TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'draft',
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                posted_at         TEXT,
                error             TEXT,
                evaluation        TEXT
            );

            CREATE TABLE IF NOT EXISTS action_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type       TEXT NOT NULL,
                target_identifier TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_action_log_type_date
                ON action_log(action_type, created_at);

            CREATE INDEX IF NOT EXISTS idx_posts_scraped
                ON posts(scraped_at);

            CREATE INDEX IF NOT EXISTS idx_comments_status
                ON comments(status);

            CREATE INDEX IF NOT EXISTS idx_comments_post
                ON comments(post_id);
        """)


# ── Config accessors ──────────────────────────────────────────────


def get_config(key: str, default: str = "") -> str:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str):
    with tx() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ── Domain helpers ────────────────────────────────────────────────


def add_target_account(name: str, linkedin_url: str, public_identifier: str | None = None) -> bool:
    with tx() as conn:
        try:
            conn.execute(
                "INSERT INTO target_accounts (name, linkedin_url, public_identifier) VALUES (?, ?, ?)",
                (name, linkedin_url, public_identifier),
            )
            logger.info("Added target: %s (%s)", name, linkedin_url)
            return True
        except sqlite3.IntegrityError:
            logger.warning("Target already exists: %s", name)
            return False


def list_target_accounts(active_only: bool = True) -> list[dict]:
    conn = _get_conn()
    q = "SELECT * FROM target_accounts"
    params = []
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY added_at DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def add_target_topic(topic: str) -> bool:
    with tx() as conn:
        try:
            conn.execute("INSERT INTO target_topics (topic) VALUES (?)", (topic,))
            logger.info("Added topic: %s", topic)
            return True
        except sqlite3.IntegrityError:
            logger.warning("Topic already exists: %s", topic)
            return False


def list_target_topics(active_only: bool = True) -> list[dict]:
    conn = _get_conn()
    q = "SELECT * FROM target_topics"
    params = []
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY added_at DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def insert_post(author_name: str, text: str, url: str | None = None,
                post_urn: str | None = None, author_urn: str | None = None,
                source: str = "feed") -> int | None:
    with tx() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO posts
                   (author_name, author_urn, post_urn, text, url, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (author_name, author_urn, post_urn, text, url, source),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_uncommented_posts(limit: int = 30) -> list[dict]:
    """Posts without a non-draft comment, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT p.* FROM posts p
           WHERE p.id NOT IN (
               SELECT c.post_id FROM comments c WHERE c.status != 'skipped'
           )
           ORDER BY p.scraped_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_comment(post_id: int, body: str, status: str = "draft",
                   evaluation: str | None = None) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO comments (post_id, body, status, evaluation) VALUES (?, ?, ?, ?)",
            (post_id, body, status, evaluation),
        )
        return cur.lastrowid


def mark_comment_posted(comment_id: int):
    with tx() as conn:
        conn.execute(
            "UPDATE comments SET status='posted', posted_at=datetime('now') WHERE id=?",
            (comment_id,),
        )


def mark_comment_failed(comment_id: int, error: str):
    with tx() as conn:
        conn.execute(
            "UPDATE comments SET status='failed', error=? WHERE id=?",
            (error, comment_id),
        )


def mark_post_skipped(post_id: int):
    """Record that we evaluated a post and decided not to comment."""
    with tx() as conn:
        conn.execute(
            "INSERT INTO comments (post_id, body, status) VALUES (?, ?, 'skipped')",
            (post_id, ""),
        )


def count_daily_comments() -> int:
    conn = _get_conn()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM action_log WHERE action_type='comment' AND date(created_at)=?",
        (today,),
    ).fetchone()
    return row["cnt"] if row else 0


def log_action(action_type: str, target_identifier: str | None = None):
    with tx() as conn:
        conn.execute(
            "INSERT INTO action_log (action_type, target_identifier) VALUES (?, ?)",
            (action_type, target_identifier),
        )


def get_daily_stats() -> dict:
    conn = _get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        """SELECT action_type, COUNT(*) AS cnt FROM action_log
           WHERE date(created_at)=? GROUP BY action_type""",
        (today,),
    ).fetchall()
    stats = {r["action_type"]: r["cnt"] for r in rows}

    # Queue status
    pending = conn.execute(
        "SELECT COUNT(*) AS cnt FROM comments WHERE status='draft'",
    ).fetchone()["cnt"]

    total_comments = conn.execute(
        "SELECT COUNT(*) AS cnt FROM comments WHERE status='posted'",
    ).fetchone()["cnt"]

    stats["queue"] = pending
    stats["total_posted"] = total_comments
    return stats


def get_recent_comments(limit: int = 10) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.*, p.author_name, p.text AS post_text, p.url AS post_url
           FROM comments c
           JOIN posts p ON p.id = c.post_id
           ORDER BY c.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Pydantic schemas for LLM output ─────────────────────────────


class EvaluationResult(BaseModel):
    """A score and reasoning for whether to comment on a post."""

    score: int = Field(description="Comment-worthiness 0-10", ge=0, le=10)
    reason: str = Field(description="Brief reason for the score")
    approach: str = Field(description="The angle you'd take — what unique insight or experience you'd add")


class CommentDraft(BaseModel):
    """A generated comment to post on LinkedIn."""

    body: str = Field(description="The comment text, 1-3 sentences. Specific, personal, adds value.")
