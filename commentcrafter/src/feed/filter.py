"""Post filtering utilities — deduplication, relevance scoring, etc."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def is_relevant_post(text: str, author_name: str, topics: list[str]) -> bool:
    """Check if a post is relevant to our target topics.

    Simple keyword matching — the LLM evaluator does the real work.
    This is just a pre-filter to avoid sending obviously irrelevant
    posts to the LLM.
    """
    if not topics:
        return True  # No target topics = all posts are candidates

    lower_text = text.lower()
    lower_author = author_name.lower()

    for topic in topics:
        lower_topic = topic.lower()
        if lower_topic in lower_text or lower_topic in lower_author:
            return True

    return False


def deduplicate(text: str, seen: set) -> bool:
    """Check if a post text (normalized) has been seen before."""
    key = re.sub(r'\s+', ' ', text[:200].lower().strip())
    if key in seen:
        return False
    seen.add(key)
    return True
