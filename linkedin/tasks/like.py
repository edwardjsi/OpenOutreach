# linkedin/tasks/like.py
"""Like task — likes a profile's activity to warm account engagement."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.models import ActionLog

logger = logging.getLogger(__name__)


def handle_like(task, session, qualifiers):
    from linkedin.actions.like import like_profile
    from linkedin.actions.search import visit_profile
    from linkedin.exceptions import ProfileInaccessibleError
    from linkedin.tasks.scheduler import enqueue_like, seconds_until_tomorrow

    campaign = session.campaign
    campaign_id = task.payload.get("campaign_id")
    public_id = task.payload.get("public_id")

    if not public_id or not campaign_id:
        logger.error("Like task missing campaign_id/public_id: %s", task.payload)
        return

    # --- Rate limit check ---
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.LIKE):
        enqueue_like(campaign_id, public_id, delay_seconds=seconds_until_tomorrow())
        return

    profile = {"url": f"https://www.linkedin.com/in/{public_id}/",
               "public_identifier": public_id}

    logger.info("[%s] %s", campaign, colored("\u2764 like", "magenta", attrs=["bold"]))
    logger.info("[%s] %s", campaign, public_id)

    try:
        visit_profile(session, profile)
        liked = like_profile(session=session, profile=profile)

        if liked:
            session.linkedin_profile.record_action(
                ActionLog.ActionType.LIKE, session.campaign,
            )
        else:
            logger.debug("No Like button on %s — skipping", public_id)

    except ProfileInaccessibleError as e:
        logger.warning("Profile inaccessible for like — %s: %s", public_id, e)
