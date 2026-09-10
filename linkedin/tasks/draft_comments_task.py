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

# Default Fallbacks in case config is empty
DEFAULT_PERSONA = "You are an expert financial planner. Your goal is to react to LinkedIn posts with insightful, professional, and engaging comments that establish your authority and add value."
DEFAULT_TONES = "Write exactly three distinct comments based on these three tones. Keep them max 3 sentences each:\n1. Polite: A standard, polished, and complimentary response.\n2. Contrarian: Politely challenging or offering an alternative perspective to spark debate.\n3. Cheerleading: Enthusiastic support and validation of the author's point or milestone."

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
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session)
    
    # We will gather a list of posts to process: (post_urn, author_name, post_text, post_url, post_date, profile_url)
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
    for post_urn, author_name, post_text, post_url, post_date, profile_url in posts_to_process:
        # Check quota again inside loop
        if DraftedComment.objects.filter(drafted_at__gte=today_start).count() >= 25:
            logger.info("Daily limit of 25 reached during processing.")
            break
            
        if DraftedComment.objects.filter(post_urn=post_urn).exists():
            continue
            
        try:
            # Auto-save organic influencer if profile_url is provided
            if profile_url:
                username = ""
                match = re.search(r'linkedin\.com/in/([^/]+)', profile_url)
                if match:
                    username = match.group(1)
                if username:
                    Influencer.objects.get_or_create(
                        linkedin_url=profile_url,
                        defaults={"username": username, "name": author_name, "is_organic": True}
                    )

            # Build full system prompt from config
            persona = config.ai_persona_prompt or DEFAULT_PERSONA
            tones = config.ai_tone_prompt or DEFAULT_TONES
            system_msg = f"{persona}\n\nRules:\n- Strictly react to the post content.\n- At most 3 sentences per comment.\n- Always capitalize the author's name when addressing them.\n\n{tones}"

            # Generate comments
            response = client.chat.completions.create(
                model=config.ai_model or "gpt-4o",
                messages=[
                    {"role": "system", "content": system_msg},
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
            
            # Mark as drafted and store texts
            DraftedComment.objects.create(
                post_urn=post_urn, 
                author_name=author_name,
                post_url=post_url,
                drafts_text=drafts
            )
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
                
            # Author & Organic Tracking
            actor = item.get("actor") or {}
            author = fallback_author
            profile_url = fallback_url
            
            if not author:
                name_obj = actor.get("name") or {}
                author = name_obj.get("text", "Unknown Author")
                
                # Extract organic profile URL for home feed items
                nav_target = actor.get("navigationContext", {}).get("actionTarget", "")
                if "linkedin.com/in/" in nav_target:
                    profile_url = nav_target.split("?")[0]
                
            # Date (from accessibility text like '22 hours ago')
            sub_desc = actor.get("subDescription") or {}
            post_date = sub_desc.get("accessibilityText", "Unknown Date")
                
            posts_list.append((urn, author, commentary, post_url, post_date, profile_url))
