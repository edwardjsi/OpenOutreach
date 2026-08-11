"""Post evaluation agent — decides whether a post is worth commenting on.

The agent scores each post 0-10 based on:
  - Can I add unique insight or personal experience?
  - Is the topic relevant to my expertise/brand?
  - Can I contribute something specific, not generic praise?
  - Would a thoughtful comment drive profile visits?

Only posts scoring >= 7 proceed to comment generation.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic_ai import Agent

from src.llm.engine import get_llm_model, run_agent_sync
from src.models import EvaluationResult

logger = logging.getLogger(__name__)

EVALUATION_THRESHOLD = 7

# ── System prompt for the evaluator ────────────────────────────

EVALUATOR_PROMPT = """You are a LinkedIn commenting strategist. Your job is to evaluate whether a post is worth commenting on.

You must be ruthless. Most posts don't deserve a comment. A good comment-worthy post is one where YOU can add value — not just agree.

## Scoring criteria:

**9-10 (Exceptional)**: The post makes a claim, shares an experience, or raises a question where you have DIRECT, SPECIFIC, PERSONAL experience to add. You can write something only YOU could write. The comment itself would attract profile visits.

**7-8 (Good)**: The post is interesting and relevant. You have something genuine to say — an insight, a counterpoint, or a complementary perspective. Your comment would be read and appreciated but won't go viral.

**5-6 (Borderline)**: You could leave a decent comment but it would be generic. Someone else could write the same thing. Skip unless you're low on options.

**0-4 (Skip)**: "Great article!" territory. The post is fine but you have nothing real to add. No specific experience. No counterpoint. No unique angle.

## Hard rules:
- NEVER comment just to promote yourself
- NEVER leave a generic compliment
- NEVER comment on posts where you'd just be repeating the author
- If your comment starts with "Great post" or "Thanks for sharing" — STOP. That's a 0.

## Your expertise / background:
{background}

## Your output:
Score 0-10, a brief reason, and the specific angle/approach you'd take."""


def evaluate_post(post_text: str, author_name: str,
                  background: str = "") -> Optional[EvaluationResult]:
    """Evaluate a post for comment-worthiness.

    Returns an EvaluationResult if the post is worth commenting on,
    or None if the score is below threshold or evaluation failed.
    """
    from src.models import get_config

    if not background:
        background = get_config("background", "General professional background")

    agent = Agent(
        get_llm_model(),
        output_type=EvaluationResult,
        model_settings={"temperature": 0.5, "timeout": 60},
        system_prompt=EVALUATOR_PROMPT.format(background=background),
    )

    prompt = f"""Post by {author_name}:

---
{post_text}
---

Evaluate this post for comment-worthiness. Score it 0-10."""
    try:
        result = run_agent_sync(agent.run(prompt)).output
        if result is None:
            return None
        logger.debug("Evaluation: score=%d reason=%s", result.score, result.reason)
        return result
    except Exception as e:
        logger.warning("Evaluation failed: %s", e)
        return None


def should_comment(result: EvaluationResult) -> bool:
    """Check if an evaluation result exceeds the threshold."""
    return result.score >= EVALUATION_THRESHOLD
