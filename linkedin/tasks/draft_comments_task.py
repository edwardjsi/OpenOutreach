import logging
import re
import boto3
from botocore.exceptions import ClientError
from openai import OpenAI
from django.utils import timezone
import datetime

from linkedin.models import SiteConfig, Influencer, DraftedComment
from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.notifications import _send_telegram

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert financial planner. Your goal is to react to LinkedIn posts with insightful, professional, and engaging comments that establish your authority and add value.

Rules:
- Strictly react to the post content.
- At most 3 sentences per comment.
- Do not mention anything about retirement planning.
- Always capitalize the author's name when addressing them.
- Format the output clearly as:

Polite:
[Draft]

Contrarian:
[Draft]

Cheerleading:
[Draft]
"""

def handle_draft_comments(task, session, qualifiers):
    """
    Background task to monitor both explicit Influencers and the Home Feed
    for new posts, drafting comments via AI, and sending them via email.
    """
    logger.info("Starting DRAFT_COMMENTS task")
    
    # Enforce Daily Limit (25)
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    drafts_today = DraftedComment.objects.filter(drafted_at__gte=today_start).count()
    if drafts_today >= 25:
        logger.info(f"Daily draft limit reached ({drafts_today}/25). Skipping until tomorrow.")
        return
        
    config = SiteConfig.objects.first()
    if not config or not config.llm_api_key:
        logger.error("SiteConfig/OpenAI API key missing. Cannot draft comments.")
        return

    client = OpenAI(api_key=config.llm_api_key)
    api = PlaywrightLinkedinAPI(session)
    
    # We will gather a list of posts to process: (post_urn, author_name, post_text, post_url, post_date)
    posts_to_process = []
    
    # 1. Check explicit Influencers
    influencers = Influencer.objects.all()
    for inf in influencers:
        try:
            username = inf.username
            if not username:
                match = re.search(r'linkedin\.com/in/([^/]+)', inf.linkedin_url)
                if match:
                    username = match.group(1)
            
            if not username:
                continue
                
            profile_data, _ = api.get_profile(public_identifier=username)
            profile_urn = profile_data.get("urn")
            if not profile_urn:
                continue
                
            params = {"count": "1", "profileUrn": profile_urn, "q": "memberShareFeed"}
            res = api.get("https://www.linkedin.com/voyager/api/identity/profileUpdatesV2", params=params)
            if not res.ok:
                continue
                
            data = res.json()
            _extract_and_add_posts(data, profile_data.get("full_name", username), inf.linkedin_url, posts_to_process)
            
        except Exception as e:
            logger.error(f"Error checking influencer {inf}: {e}")
            
    # 2. Check Home Feed
    try:
        res = api.get("https://www.linkedin.com/voyager/api/feed/updatesV2", params={"count": "5", "q": "feed"})
        if res.ok:
            data = res.json()
            _extract_and_add_posts(data, None, None, posts_to_process)
    except Exception as e:
        logger.error(f"Error checking home feed: {e}")

    # Process all gathered posts
    for post_urn, author_name, post_text, post_url, post_date in posts_to_process:
        # Check quota again inside loop
        if DraftedComment.objects.filter(drafted_at__gte=today_start).count() >= 25:
            logger.info("Daily limit of 25 reached during processing.")
            break
            
        if DraftedComment.objects.filter(post_urn=post_urn).exists():
            continue
            
        try:
            # Generate comments
            response = client.chat.completions.create(
                model=config.ai_model or "gpt-4o",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Author: {author_name}\nPost:\n{post_text}"}
                ],
                temperature=0.7
            )
            
            drafts = response.choices[0].message.content
            
            # Send via SES
            current_date_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = f"Drafted Comments for {author_name}\n"
            msg += f"Generated On: {current_date_str}\n"
            msg += f"Post Published: {post_date}\n\n"
            msg += f"Post URL: {post_url}\n\n"
            msg += f"Post Snippet:\n{post_text[:150]}...\n\n"
            msg += f"Drafts:\n{drafts}"
            
            ses_client = boto3.client('ses', region_name='ap-southeast-1')
            try:
                ses_client.send_email(
                    Destination={'ToAddresses': ['edwardjsi@gmail.com']},
                    Message={
                        'Body': {'Text': {'Charset': 'UTF-8', 'Data': msg}},
                        'Subject': {'Charset': 'UTF-8', 'Data': f"LinkedIn Drafts: {author_name}"}
                    },
                    Source='edwardjsi@gmail.com'
                )
                logger.info(f"Email sent successfully for {post_urn}")
            except ClientError as e:
                logger.error(f"Error sending SES email: {e.response['Error']['Message']}")
            
            # Mark as drafted
            DraftedComment.objects.create(post_urn=post_urn, author_name=author_name)
            logger.info(f"Successfully drafted comments for post {post_urn} by {author_name}")
            
        except Exception as e:
            logger.error(f"Error drafting comment for {post_urn}: {e}")

def _extract_and_add_posts(data, fallback_author, fallback_url, posts_list):
    included = data.get("included", [])
    for item in included:
        if item.get("$type") == "com.linkedin.voyager.feed.render.UpdateV2":
            urn = item.get("updateMetadata", {}).get("urn")
            if not urn:
                continue
                
            # Build a robust, universal URL from the URN that works flawlessly on mobile and desktop
            post_url = f"https://www.linkedin.com/feed/update/{urn}/"
            
            commentary_obj = item.get("commentary") or {}
            text_obj = commentary_obj.get("text") or {}
            commentary = text_obj.get("text")
            
            if not commentary:
                reshared = item.get("resharedUpdate") or {}
                if isinstance(reshared, dict):
                    val = reshared.get("value") or {}
                    inner_v2 = val.get("com.linkedin.voyager.feed.render.UpdateV2") or {}
                    inner_commentary = inner_v2.get("commentary") or {}
                    inner_text = inner_commentary.get("text") or {}
                    commentary = inner_text.get("text")
            
            if not commentary:
                continue
                
            # Author
            actor = item.get("actor") or {}
            author = fallback_author
            if not author:
                name_obj = actor.get("name") or {}
                author = name_obj.get("text", "Unknown Author")
                
            # Date (from accessibility text like '22 hours ago')
            sub_desc = actor.get("subDescription") or {}
            post_date = sub_desc.get("accessibilityText", "Unknown Date")
                
            posts_list.append((urn, author, commentary, post_url, post_date))
