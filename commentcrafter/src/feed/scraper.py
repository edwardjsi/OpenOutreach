"""Scrape LinkedIn feed and search results for posts to comment on.

Discovers relevant posts from:
  - The main feed
  - Search results by topic keyword
  - Specific target account pages
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional
from urllib.parse import urljoin, quote

from src.browser.nav import goto_page
from src.config import (
    FEED_SCROLL_COUNT,
    FEED_SCROLL_PAUSE_MIN,
    FEED_SCROLL_PAUSE_MAX,
    SEARCH_PAUSE_S,
)

logger = logging.getLogger(__name__)

FEED_URL = "https://www.linkedin.com/feed/"
SEARCH_URL = "https://www.linkedin.com/search/results/content/?keywords={keywords}"

# ── Selectors ──────────────────────────────────────────────────

POST_CARD_SELECTOR = 'div[data-test-id="main-feed-activity-card"], div[data-id^="urn:li:activity"], article.feed-shared-update-v2'
TEXT_SELECTORS = [
    'div[class*="feed-shared-update-v2__description"]',
    'div[class*="break-words"]',
    'span[class*="visually-hidden"]',
    'article div.feed-shared-text',
    'div[class*="update-components-text"]',
]
AUTHOR_SELECTORS = [
    'span[class*="feed-shared-actor__name"]',
    'a span[dir="auto"]',
    'span[class*="hoverable-link-text"]',
    'span[class*="actor-name"]',
]


class ScrapedPost:
    """A scraped LinkedIn post with metadata."""

    __slots__ = ("author_name", "text", "url", "post_urn", "author_urn")

    def __init__(self, author_name: str, text: str, url: Optional[str] = None,
                 post_urn: Optional[str] = None, author_urn: Optional[str] = None):
        self.author_name = author_name
        self.text = text
        self.url = url
        self.post_urn = post_urn
        self.author_urn = author_urn

    def __repr__(self) -> str:
        return f"ScrapedPost(author={self.author_name!r}, text={self.text[:80]}...)"

    def to_dict(self) -> dict:
        return {
            "author_name": self.author_name,
            "text": self.text,
            "url": self.url,
            "post_urn": self.post_urn,
            "author_urn": self.author_urn,
        }


# ── Feed scraping ─────────────────────────────────────────────


def _scroll_feed(page, count: int = FEED_SCROLL_COUNT):
    """Scroll the feed to load more posts."""
    for i in range(count):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(random.uniform(FEED_SCROLL_PAUSE_MIN, FEED_SCROLL_PAUSE_MAX))
        logger.debug("Scrolled feed %d/%d", i + 1, count)


def _extract_text(card) -> Optional[str]:
    """Extract post body text from a DOM card element."""
    seen = set()
    for sel in TEXT_SELECTORS:
        try:
            loc = card.locator(sel)
            count = loc.count()
            for i in range(count):
                raw = loc.nth(i).inner_text(timeout=3000).strip()
                if raw and raw not in seen:
                    seen.add(raw)
                    text = re.sub(r'\s+', ' ', raw)
                    return text
        except Exception:
            continue
    return None


def _extract_author(card) -> Optional[str]:
    """Extract author name from a DOM card."""
    for sel in AUTHOR_SELECTORS:
        try:
            loc = card.locator(sel)
            if loc.count() > 0:
                name = loc.first.inner_text(timeout=3000).strip()
                if name:
                    return re.sub(r'\s+', ' ', name)
        except Exception:
            continue
    return None


def _extract_post_urn(card) -> Optional[str]:
    """Extract activity URN from links in the card."""
    try:
        for link in card.locator('a[href*="/feed/update/urn:li:activity:"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r'urn:li:activity:\d+', href)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def _extract_post_url(card) -> Optional[str]:
    """Extract post permalink."""
    try:
        for link in card.locator('a[href*="/feed/update/"]').all():
            href = link.get_attribute("href") or ""
            if "/feed/update/" in href:
                return urljoin("https://www.linkedin.com", href)
    except Exception:
        pass
    return None


def scrape_feed(session, max_posts: int = 30) -> list[ScrapedPost]:
    """Navigate to feed, scroll, and extract posts.

    Returns posts sorted by recency (top of feed first), skipping
    posts without text or that are too short to comment on meaningfully.
    """
    session.ensure_browser()
    page = session.page

    goto_page(
        session,
        action=lambda: page.goto(FEED_URL, wait_until="domcontentloaded"),
        expected_url_pattern="/feed/",
        error_message="Failed to navigate to LinkedIn feed",
    )
    session.wait(3, 5)

    _scroll_feed(page)

    cards = page.locator(POST_CARD_SELECTOR).all()
    posts = []
    seen_texts = set()

    for card in cards:
        text = _extract_text(card)
        if not text or len(text) < 30:  # too short to comment on
            continue
        text_key = text[:100]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        author = _extract_author(card) or "Unknown"
        url = _extract_post_url(card)
        post_urn = _extract_post_urn(card)

        posts.append(ScrapedPost(
            author_name=author, text=text, url=url, post_urn=post_urn,
        ))

        if len(posts) >= max_posts:
            break

    logger.info("Scraped %d feed posts", len(posts))
    return posts


def search_posts_by_keyword(session, keyword: str, max_posts: int = 20) -> list[ScrapedPost]:
    """Search LinkedIn for posts containing *keyword*.

    Returns the top posts from search results.
    """
    session.ensure_browser()
    page = session.page

    encoded = quote(keyword, safe='')
    search_url = SEARCH_URL.format(keywords=encoded)

    goto_page(
        session,
        action=lambda: page.goto(search_url, wait_until="domcontentloaded"),
        expected_url_pattern="/search/results/content/",
        error_message=f"Failed to search for '{keyword}'",
    )
    session.wait(SEARCH_PAUSE_S, SEARCH_PAUSE_S + 2)

    # Scroll to load more results
    for _ in range(2):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(random.uniform(2, 3))

    cards = page.locator(POST_CARD_SELECTOR).all()
    posts = []
    seen_texts = set()

    for card in cards:
        text = _extract_text(card)
        if not text or len(text) < 30:
            continue
        text_key = text[:100]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        author = _extract_author(card) or "Unknown"
        url = _extract_post_url(card)

        posts.append(ScrapedPost(author_name=author, text=text, url=url))

        if len(posts) >= max_posts:
            break

    logger.info("Search '%s': found %d posts", keyword, len(posts))
    return posts


def scrape_profile_posts(session, public_identifier: str, max_posts: int = 10) -> list[ScrapedPost]:
    """Navigate to a profile's recent posts and scrape them."""
    session.ensure_browser()
    page = session.page

    profile_url = f"https://www.linkedin.com/in/{public_identifier}/recent-activity/all/"
    goto_page(
        session,
        action=lambda: page.goto(profile_url, wait_until="domcontentloaded"),
        expected_url_pattern=f"/in/{public_identifier}",
        error_message=f"Failed to navigate to {public_identifier}'s profile",
    )
    session.wait(3, 5)

    # Scroll to load posts
    for _ in range(2):
        page.evaluate("window.scrollBy(0, 800)")
        time.sleep(random.uniform(2, 3))

    cards = page.locator(POST_CARD_SELECTOR).all()
    posts = []
    seen_texts = set()

    author_name = public_identifier
    for card in cards:
        text = _extract_text(card)
        if not text or len(text) < 30:
            continue
        text_key = text[:100]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        url = _extract_post_url(card)

        posts.append(ScrapedPost(author_name=author_name, text=text, url=url))

        if len(posts) >= max_posts:
            break

    logger.info("Profile %s: found %d posts", public_identifier, len(posts))
    return posts
