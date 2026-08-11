"""Comment writer — generates a thoughtful, specific LinkedIn comment.

The generated comment must:
  - Add unique insight, personal experience, or a genuine counterpoint
  - Be 1-3 sentences (60-1250 chars)
  - Never be generic praise ("Great article!", "Thanks for sharing!")
  - Read like a real person wrote it
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent

from src.llm.engine import get_llm_model, run_agent_sync
from src.models import CommentDraft
from src.config import MIN_COMMENT_CHARS, MAX_COMMENT_CHARS

logger = logging.getLogger(__name__)

WRITER_PROMPT = """You are writing a LinkedIn comment. Your goal is to add value.

## Rules — NEVER:
- "Great post!" / "Thanks for sharing" / "Interesting read" / anything generic
- Promote yourself, your product, or your services
- Repeat what the author already said
- Use emojis or hashtags
- Write more than 3 sentences
- Use clichés or corporate language

## Rules — ALWAYS:
- Write from personal experience: "I've seen this happen when..."
- Add a specific perspective the author didn't mention
- Be conversational, not academic
- If you disagree, disagree respectfully with specifics
- Ask a thoughtful question that extends the discussion
- Share a counterintuitive observation

## Your background:
{background}

## The evaluation (why we chose to comment):
{approach}

## The post (for context):
{post_text}

Write a comment that adds value. Maximum 3 sentences."""


def write_comment(post_text: str, approach: str,
                  background: str = "") -> str | None:
    """Generate a thoughtful LinkedIn comment for a post.

    Returns the comment body, or None if generation fails.
    """
    from src.models import get_config

    if not background:
        background = get_config("background", "General professional background")

    agent = Agent(
        get_llm_model(),
        output_type=CommentDraft,
        model_settings={"temperature": 0.7, "timeout": 60},
        system_prompt=WRITER_PROMPT.format(
            background=background,
            approach=approach,
            post_text=post_text,
        ),
    )

    try:
        result = run_agent_sync(agent.run("Write the comment.")).output
        if result is None:
            return None

        body = result.body.strip()

        # Validate length
        if len(body) < MIN_COMMENT_CHARS:
            logger.warning("Comment too short (%d chars): %s", len(body), body)
            return None
        if len(body) > MAX_COMMENT_CHARS:
            logger.warning("Comment too long (%d chars), truncating", len(body))
            body = body[:MAX_COMMENT_CHARS]

        # Reject generic patterns
        generic = ["great post", "great article", "thanks for sharing",
                    "thanks for this", "interesting read", "well said",
                    "spot on", "love this", "totally agree"]
        lower = body.lower()
        for phrase in generic:
            if lower.startswith(phrase) or lower.endswith(phrase):
                logger.warning("Generic comment detected: %s", body[:80])
                return None

        return body
    except Exception as e:
        logger.warning("Comment generation failed: %s", e)
        return None
