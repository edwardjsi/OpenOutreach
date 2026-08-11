"""Main daemon application loop.

The lifecycle:
  1. Scrape feed + search for new posts
  2. Evaluate each new post for comment-worthiness (LLM)
  3. Generate comments for worthy posts (LLM)
  4. Post comments one at a time with human rhythm
  5. Sleep and repeat
"""
from __future__ import annotations

import logging
import time

from termcolor import colored

from src.browser.session import CommenterSession
from src.feed.scraper import scrape_feed, search_posts_by_keyword, scrape_profile_posts, ScrapedPost
from src.llm.evaluator import evaluate_post, should_comment
from src.llm.writer import write_comment
from src.actions.comment import post_comment
from src.scheduler import (
    Rhythm, comments_remaining_today, should_continue,
    seconds_until_midnight, get_pending_queue_size,
)
from src.models import (
    insert_post, get_uncommented_posts, insert_comment,
    mark_comment_posted, mark_comment_failed, mark_post_skipped,
    log_action, get_daily_stats, list_target_accounts, list_target_topics,
)

logger = logging.getLogger(__name__)


# ── Scrape phase ──────────────────────────────────────────────


def run_scrape_cycle(session, max_posts_per_source: int = 20) -> int:
    """Scrape feed + target accounts + topic searches.

    Returns the number of new posts discovered.
    """
    new_count = 0
    background = getattr(session, '_background', "")

    # 1. Scrape feed
    try:
        posts = scrape_feed(session, max_posts=max_posts_per_source)
        for p in posts:
            pid = insert_post(p.author_name, p.text, p.url, p.post_urn, source="feed")
            if pid is not None:
                new_count += 1
        logger.info("Feed: %d new posts", sum(1 for p in posts if p.post_urn))
    except Exception as e:
        logger.warning("Feed scrape failed: %s", e)

    # 2. Scrape target account posts
    targets = list_target_accounts()
    for target in targets:
        pid = target.get("public_identifier")
        if not pid:
            continue
        try:
            posts = scrape_profile_posts(session, pid, max_posts=5)
            for p in posts:
                existing = insert_post(p.author_name, p.text, p.url, p.post_urn, source="target")
                if existing is not None:
                    new_count += 1
        except Exception as e:
            logger.warning("Target %s scrape failed: %s", pid, e)
        session.wait(5, 8)  # Be polite between profiles

    # 3. Search by topics
    topics = list_target_topics()
    for topic in topics:
        try:
            posts = search_posts_by_keyword(session, topic["topic"], max_posts=10)
            for p in posts:
                existing = insert_post(p.author_name, p.text, p.url, p.post_urn, source="search")
                if existing is not None:
                    new_count += 1
        except Exception as e:
            logger.warning("Topic search '%s' failed: %s", topic["topic"], e)
        session.wait(5, 8)

    return new_count


# ── Evaluate & generate phase ──────────────────────────────────


def process_pending_posts(session, max_per_cycle: int = 5) -> int:
    """Evaluate pending posts and generate comments.

    Returns the number of new comments drafted.
    """
    posts = get_uncommented_posts(limit=20)
    if not posts:
        logger.info("No pending posts to evaluate")
        return 0

    drafted = 0
    background = getattr(session, '_background', "")

    for post in posts[:max_per_cycle]:
        post_text = post["text"]
        author = post["author_name"]
        post_id = post["id"]

        # Check daily limit before generating
        if not should_continue():
            break

        logger.info("Evaluating post #%d by %s: %s…", post_id, author, post_text[:80])

        # Evaluate
        evaluation = evaluate_post(post_text, author, background=background)
        if evaluation is None:
            continue

        if not should_comment(evaluation):
            logger.info("Skipping post #%d — score %d: %s", post_id, evaluation.score, evaluation.reason)
            mark_post_skipped(post_id)
            continue

        logger.info("Post #%d score %d — generating comment.", post_id, evaluation.score)

        # Generate comment
        body = write_comment(post_text, evaluation.approach, background=background)
        if body is None:
            logger.warning("Failed to generate comment for post #%d", post_id)
            continue

        # Save draft
        comment_id = insert_comment(post_id, body, status="draft",
                                    evaluation=f"score={evaluation.score}: {evaluation.reason}")
        drafted += 1
        logger.info("Drafted comment #%d for post #%d: %s", comment_id, post_id, body[:100])

    return drafted


# ── Post phase ─────────────────────────────────────────────────


def post_comments(session, max_per_cycle: int = 3) -> int:
    """Post drafted comments, one at a time.

    Returns the number of comments posted.
    """
    from src.models import _get_conn as get_db
    conn = get_db()
    drafts = conn.execute(
        "SELECT c.id, c.body, p.url, p.id AS post_id FROM comments c JOIN posts p ON p.id=c.post_id WHERE c.status='draft' ORDER BY c.created_at ASC LIMIT ?",
        (max_per_cycle,),
    ).fetchall()

    if not drafts:
        return 0

    posted = 0
    for row in drafts:
        if not should_continue():
            break

        comment_id = row["id"]
        body = row["body"]
        url = row["url"]
        post_id = row["post_id"]

        if not url:
            logger.warning("Comment #%d has no post URL, skipping", comment_id)
            mark_comment_failed(comment_id, "No post URL")
            continue

        logger.info("Posting comment #%d on %s", comment_id, url)
        try:
            success = post_comment(session, url, body)
        except Exception as e:
            logger.warning("Comment post failed: %s", e)
            mark_comment_failed(comment_id, str(e))
            continue

        if success:
            mark_comment_posted(comment_id)
            log_action("comment", url)
            posted += 1
            logger.info(colored("✓ Comment posted on %s", "green"), url)
        else:
            mark_comment_failed(comment_id, "Post comment returned False")

        # Human-like pause between comments
        session.wait(30, 60)

    return posted


# ── Daemon main loop ────────────────────────────────────────────


class Heartbeat:
    """Logs an 'alive' line periodically during long sleeps."""

    def __init__(self, interval_s: int = 120):
        self._interval = interval_s
        self._last = time.monotonic()

    def maybe_log(self, context: str = ""):
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        ctx = f" — {context}" if context else ""
        logger.info(colored("alive", "cyan") + "%s", ctx)


def run_daemon(session: CommenterSession | None = None):
    """Main daemon loop.

    If no session is provided, creates one.
    """
    from src.models import get_config

    own_session = session is None
    if session is None:
        session = CommenterSession()

    background = get_config("background", "General professional background")
    session._background = background  # noqa: SLF001

    logger.info(colored("CommentCrafter daemon started", "green", attrs=["bold"]))

    heartbeat = Heartbeat(interval_s=120)
    rhythm = Rhythm()

    # Initial scrape + evaluate cycle (no posting yet — we want drafts queued)
    logger.info("Initial scrape cycle...")
    try:
        session.ensure_browser()
        new_posts = run_scrape_cycle(session)
        logger.info("Found %d new posts", new_posts)
        drafted = process_pending_posts(session, max_per_cycle=10)
        logger.info("Drafted %d comments", drafted)
    except Exception as e:
        logger.warning("Initial cycle failed: %s", e)

    # Main loop
    stats_counter = 0
    while True:
        try:
            # --- Check daily limit ---
            if not should_continue():
                secs = seconds_until_midnight()
                h, m = int(secs // 3600), int(secs % 3600 // 60)
                logger.info("Daily limit reached — sleeping %dh%02dm until reset", h, m)
                time.sleep(secs)
                rhythm.reset()
                continue

            # --- Post comments (drafts → posted) ---
            posted = post_comments(session, max_per_cycle=2)
            if posted > 0:
                logger.info("Posted %d comments this cycle", posted)
                rhythm.maybe_break()
                continue  # immediately check if we can post more

            # --- No drafts to post — scrape + evaluate ---
            logger.info("No drafts — scraping for new posts")
            try:
                new_posts = run_scrape_cycle(session)
                if new_posts > 0:
                    logger.info("Found %d new posts — evaluating", new_posts)
                    drafted = process_pending_posts(session, max_per_cycle=5)
                    if drafted > 0:
                        continue  # go back to posting
                else:
                    logger.info("No new posts found")
            except Exception as e:
                logger.warning("Scrape cycle failed: %s", e)

            # --- Nothing to do — sleep with heartbeat ---
            stats_counter += 1
            if stats_counter % 5 == 0:
                stats = get_daily_stats()
                from src.config import DAILY_COMMENT_LIMIT
                logger.info(
                    "Stats: %d/%d comments today | %d in queue | %d total",
                    stats.get("comment", 0), DAILY_COMMENT_LIMIT,
                    stats.get("queue", 0), stats.get("total_posted", 0),
                )

            sleep_s = 180  # 3 min between idle cycles
            heartbeat.maybe_log(f"sleeping {sleep_s}s, {comments_remaining_today()} comments remaining today")
            time.sleep(sleep_s)

        except KeyboardInterrupt:
            logger.info(colored("Daemon stopped by user", "yellow"))
            break
        except Exception as e:
            logger.exception("Daemon error: %s", e)
            time.sleep(30)  # Back off briefly on errors

    # Cleanup
    if own_session:
        session.close()
