# linkedin/actions/like.py
import logging
from typing import Dict, Any

from linkedin.browser.nav import find_top_card, dump_page_html

logger = logging.getLogger(__name__)

SELECTORS = {
    "like_button": (
        'button[aria-label="Like"]:visible, '
        'button[aria-label="Like post"]:visible, '
        'button[aria-label^="Like"]:visible, '
        'button:has(span:text-is("Like")):visible'
    ),
}


def like_profile(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> bool:
    """Click the first visible Like button on a LinkedIn profile page.

    Assumes the profile page is already loaded (caller navigates via
    ``visit_profile`` beforehand).  Likes the first post/activity
    with a visible Like button.

    Returns ``True`` if a Like button was found and clicked,
    ``False`` otherwise.
    """
    public_identifier = profile.get("public_identifier")
    session.wait()

    page = session.page
    like_btn = page.locator(SELECTORS["like_button"])

    if like_btn.count() == 0:
        logger.debug("No Like button found for %s", public_identifier)
        dump_page_html(session, profile, category="like")
        return False

    like_btn.first.click()
    session.wait()
    logger.info("Liked profile → %s", public_identifier)
    return True
