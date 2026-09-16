import logging
import random
import time
import re
from django.utils import timezone
from linkedin.models import Influencer
from linkedin.api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)

def handle_classify_influencers(session):
    """
    Background task to visit influencer profiles, extract followers and comment metrics,
    and classify their engagement group.
    Runs silently in the background while the daemon is idle.
    """
    # Find influencers who haven't been classified yet
    influencers = list(Influencer.objects.filter(last_classified_at__isnull=True)[:5])
    if not influencers:
        logger.info("All influencers are classified. Standing by.")
        return

    logger.info(f"Classifying {len(influencers)} influencers in this idle cycle (staying below radar)...")
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session)

    for inf in influencers:
        try:
            username = inf.username
            if not username:
                match = re.search(r'linkedin\.com/in/([^/]+)', inf.linkedin_url)
                if match:
                    username = match.group(1)
            
            if not username:
                inf.last_classified_at = timezone.now()
                inf.save(update_fields=['last_classified_at'])
                continue

            # Sleep to stay below radar
            sleep_time = random.uniform(8, 15)
            logger.info(f"Sleeping {sleep_time:.1f}s before visiting {username}...")
            time.sleep(sleep_time)

            profile_data, raw_data = api.get_profile(public_identifier=username)
            profile_urn = profile_data.get("urn")
            
            # DEBUG DUMP
            try:
                import json
                with open(f"{username}_profile_debug.json", "w") as f:
                    json.dump(raw_data, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to dump profile: {e}")

            # Fetch follower count using networkinfo endpoint
            follower_count = 0
            try:
                network_res = api.get(f"https://www.linkedin.com/voyager/api/identity/profiles/{username}/networkinfo")
                if network_res.ok:
                    network_data = network_res.json()
                    follower_count = network_data.get("followersCount", 0)
            except Exception as e:
                logger.error(f"Error fetching networkinfo for {username}: {e}")

            if not profile_urn:
                logger.warning(f"No profile_urn for {username}")
                inf.last_classified_at = timezone.now()
                inf.save(update_fields=['last_classified_at'])
                continue

            # Sleep again before getting posts
            time.sleep(random.uniform(5, 10))

            # Fetch recent posts (up to 5) to calculate average comments
            post_count = 0
            total_comments = 0
            params = {"count": "5", "profileUrn": profile_urn, "q": "memberShareFeed"}
            res = api.get("https://www.linkedin.com/voyager/api/identity/profileUpdatesV2", params=params)
            
            if res.ok:
                data = res.json()
                for item in data.get("included", []):
                    if item.get("$type") == "com.linkedin.voyager.feed.render.UpdateV2":
                        post_count += 1
                    elif item.get("$type") == "com.linkedin.voyager.feed.shared.SocialActivityCounts":
                        total_comments += item.get("numComments", 0)

            avg_comments = (total_comments / post_count) if post_count > 0 else 0.0

            # Classification Logic
            if avg_comments >= 10.0 or follower_count > 20000:
                group = 'daily'
            elif avg_comments >= 3.0 or follower_count > 5000:
                group = 'regular'
            else:
                group = 'rare'
                
            inf.follower_count = follower_count
            inf.avg_comments = avg_comments
            inf.engagement_group = group
            inf.last_classified_at = timezone.now()
            inf.save(update_fields=['follower_count', 'avg_comments', 'engagement_group', 'last_classified_at'])
            
            logger.info(f"Classified {username} as '{group}' ({follower_count} followers, {avg_comments:.1f} avg comments)")

        except Exception as e:
            logger.error(f"Error classifying influencer {inf}: {e}")
            inf.last_classified_at = timezone.now()
            inf.save(update_fields=['last_classified_at'])
