"""Post a comment on a LinkedIn post using Playwright browser automation.

Navigates to the post URL, finds the comment input box, types the
comment with human-like timing, and submits it.
"""
from __future__ import annotations

import logging
import random
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.browser.nav import goto_page, human_type
from src.config import HUMAN_TYPE_MIN_DELAY_MS, HUMAN_TYPE_MAX_DELAY_MS

logger = logging.getLogger(__name__)

# ── Selectors for the comment UI ──────────────────────────────

# The "Comment" button / text area on a post
COMMENT_STARTER_SELECTORS = [
    'button[aria-label*="Comment"i]:visible',
    'button:has(span:text-is("Comment")):visible',
    'button[class*="comment"]:visible',
]

COMMENT_INPUT_SELECTORS = [
    'div[role="textbox"][aria-label*="Write a comment"i]',
    'div[role="textbox"][aria-label*="Add a comment"i]',
    'div[class*="comments-comment-box__comment-editor"] div[contenteditable="true"]',
    'div[contenteditable="true"][class*="comment"]',
    'div[data-placeholder*="Add a comment"]',
    'div[aria-placeholder*="Add a comment"]',
    # Fallback: any visible contenteditable inside the comment box
    'div[class*="comments-comment-texteditor"] div[contenteditable="true"]',
]

SUBMIT_COMMENT_SELECTORS = [
    'button[class*="comments-comment-box__submit-button"]:visible',
    'button[type="submit"]:visible:has-text("Comment")',
    'button[aria-label*="Post comment"]:visible',
    'button[class*="comment-btn"]:visible',
    # The React inline submit
    'button[data-control-name*="comment_submit"]:visible',
]

CLOSE_BUTTON_SELECTORS = [
    'button[aria-label*="Close"i]:visible',
    'button[class*="artdeco-dismiss"]:visible',
]


def post_comment(session, post_url: str, comment_body: str) -> bool:
    """Navigate to the post URL and post a comment.

    Returns True if the comment was successfully posted.
    """
    session.ensure_browser()
    page = session.page

    # Navigate to the post
    goto_page(
        session,
        action=lambda: page.goto(post_url, wait_until="domcontentloaded"),
        expected_url_pattern="/feed/update/",
        error_message=f"Failed to navigate to post: {post_url}",
    )
    session.wait(3, 5)

    # --- Step 1: Find and click the "Comment" button ---
    logger.debug("Looking for Comment button")
    comment_btn = _find_comment_button(page)
    if comment_btn is None:
        logger.warning("No Comment button found on the post")
        return False

    try:
        comment_btn.click()
        session.wait(1, 2)
    except Exception as e:
        logger.warning("Failed to click Comment button: %s", e)
        return False

    # --- Step 2: Find the comment input and type ---
    logger.debug("Looking for comment input box")
    comment_input = _find_comment_input(page)
    if comment_input is None:
        logger.warning("No comment input box found")
        return False

    try:
        comment_input.click()
        session.wait(0.5, 1)

        # Type the comment with human-like delays
        human_type(comment_input, comment_body,
                   min_delay=HUMAN_TYPE_MIN_DELAY_MS,
                   max_delay=HUMAN_TYPE_MAX_DELAY_MS)
        session.wait(0.5, 1.5)
    except Exception as e:
        logger.warning("Failed to type comment: %s", e)
        return False

    # --- Step 3: Submit ---
    logger.debug("Looking for Submit button")
    submit_btn = _find_submit_button(page)
    if submit_btn is None:
        logger.warning("No Submit button found — pressing Ctrl+Enter as fallback")
        try:
            comment_input.press("Control+Enter")
            session.wait(1, 2)
        except Exception as e:
            logger.warning("Ctrl+Enter fallback failed: %s", e)
            return False
    else:
        try:
            submit_btn.click(delay=random.randint(100, 300))
            session.wait(1, 2)
        except Exception as e:
            logger.warning("Submit button click failed: %s", e)
            return False

    logger.info("Comment posted successfully on %s", post_url)
    return True


# ── Locator helpers ────────────────────────────────────────────


def _find_first_visible(page, selectors: list[str], timeout_ms: int = 5000):
    """Try each selector, return the first visible locator or None."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                return loc.first
        except (PlaywrightTimeoutError, Exception):
            continue
    return None


def _find_comment_button(page):
    return _find_first_visible(page, COMMENT_STARTER_SELECTORS)


def _find_comment_input(page):
    return _find_first_visible(page, COMMENT_INPUT_SELECTORS)


def _find_submit_button(page):
    return _find_first_visible(page, SUBMIT_COMMENT_SELECTORS)
