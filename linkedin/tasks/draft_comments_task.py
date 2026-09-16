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
DEFAULT_TONES = "Write exactly three distinct comments based on these three tones. Keep them max 3 sentences each:\n1. Polite: A standard, polished, and complimentary response.\n2. Contrarian: Politely challenging or offering an alternative perspective to spark debate. DO NOT start with 'While I' or use repetitive opening phrases. Vary your approach significantly each time.\n3. Cheerleading: Enthusiastic support and validation of the author's point or milestone."

def handle_draft_comments(task, session, qualifiers):
    """
    Background task to monitor both explicit Influencers and the Home Feed
    for new posts, drafting comments via AI, and sending them via email.
    """
    logger.info("Starting DRAFT_COMMENTS task")
    
    # Enforce Daily Limit (30)
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    drafts_today = DraftedComment.objects.filter(drafted_at__gte=today_start).count()
    if drafts_today >= 30:
        logger.info(f"Daily draft limit reached ({drafts_today}/30). Skipping until tomorrow.")
        return
        
    config = SiteConfig.objects.first()
    if not config or not config.llm_api_key:
        logger.error("SiteConfig/OpenAI API key missing. Cannot draft comments.")
        return

    client = OpenAI(api_key=config.llm_api_key)
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session)
    
    # We will gather a list of posts to process: (post_urn, author_name, post_text, post_url, post_date, profile_url, engagement_group)
    posts_to_process = []
    batched_messages = []
    
    # 1. Check explicit Influencers (randomized to distribute engagement)
    influencers = list(Influencer.objects.all())
    import random
    random.shuffle(influencers)
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
            _extract_and_add_posts(data, profile_data.get("full_name", username), inf.linkedin_url, posts_to_process, group=inf.engagement_group)
            
        except Exception as e:
            logger.error(f"Error checking influencer {inf}: {e}")
            
    # 2. Check Home Feed
    try:
        res = api.get("https://www.linkedin.com/voyager/api/feed/updatesV2", params={"count": "5", "q": "feed"})
        if res.ok:
            data = res.json()
            _extract_and_add_posts(data, None, None, posts_to_process, group='regular')
    except Exception as e:
        logger.error(f"Error checking home feed: {e}")

    # Pre-filter valid posts
    valid_posts = []
    for post in posts_to_process:
        post_urn, author_name, post_text, post_url, post_date, profile_url, group = post
        if DraftedComment.objects.filter(post_urn=post_urn).exists():
            continue
        if post_url and DraftedComment.objects.filter(post_url=post_url).exists():
            continue
        if DraftedComment.objects.filter(author_name=author_name, drafted_at__gte=today_start).exists():
            continue
        valid_posts.append(post)

    # Bucket them
    daily_c = [p for p in valid_posts if p[6] == 'daily']
    regular_c = [p for p in valid_posts if p[6] == 'regular']
    rare_c = [p for p in valid_posts if p[6] == 'rare']

    selected_posts = []
    selected_posts.extend(rare_c[:5])
    selected_posts.extend(regular_c[:10])
    selected_posts.extend(daily_c[:15])

    remaining_quota = 30 - drafts_today
    need = remaining_quota - len(selected_posts)
    
    # Rollover logic (dip back to daily, then regular, then rare)
    if need > 0:
        leftover_daily = daily_c[15:]
        selected_posts.extend(leftover_daily[:need])
        need = remaining_quota - len(selected_posts)
        if need > 0:
            leftover_regular = regular_c[10:]
            selected_posts.extend(leftover_regular[:need])
            need = remaining_quota - len(selected_posts)
            if need > 0:
                leftover_rare = rare_c[5:]
                selected_posts.extend(leftover_rare[:need])

    # Shuffle selected posts so we don't always bias if quota is small, then truncate
    import random
    random.shuffle(selected_posts)
    selected_posts = selected_posts[:remaining_quota]

    # Process selected posts
    for post_urn, author_name, post_text, post_url, post_date, profile_url, group in selected_posts:
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
            msg = f"--- Drafted Comments for {author_name} ---\n"
            msg += f"Generated On: {current_date_str}\n"
            msg += f"Post Published: {post_date}\n\n"
            msg += f"Post URL: {post_url}\n\n"
            msg += f"Post Snippet:\n{post_text[:150]}...\n\n"
            msg += f"Drafts:\n{drafts}\n\n"
            
            # Store temporarily for batching
            batched_messages.append((post_urn, author_name, post_url, drafts, msg))
            
        except Exception as e:
            logger.error(f"Error drafting comment for {post_urn}: {e}")

    # Now send emails in batches of 10
    if batched_messages:
        ses_client = boto3.client('ses', region_name='ap-south-1')
        batch_size = 10
        for i in range(0, len(batched_messages), batch_size):
            batch = batched_messages[i:i + batch_size]
            
            msg_body = "".join([item[4] for item in batch])
            authors = ", ".join(list(dict.fromkeys([item[1] for item in batch])))
            
            try:
                ses_client.send_email(
                    Destination={'ToAddresses': ['edwardjsi@gmail.com']},
                    Message={
                        'Body': {'Text': {'Charset': 'UTF-8', 'Data': msg_body}},
                        'Subject': {'Charset': 'UTF-8', 'Data': f"LinkedIn Drafts ({len(batch)} posts): {authors}"[:98]}
                    },
                    Source='sales@goalsgap.in'
                )
                logger.info(f"Successfully sent batch email for {len(batch)} drafts.")
                
                # Save to DB only if email succeeds
                for item in batch:
                    DraftedComment.objects.create(
                        post_urn=item[0], 
                        author_name=item[1],
                        post_url=item[2],
                        drafts_text=item[3]
                    )
                    logger.info(f"Successfully drafted comments for post {item[0]} by {item[1]}")
            except ClientError as e:
                logger.error(f"Error sending SES batch email: {e.response['Error']['Message']}")

def _extract_and_add_posts(data, fallback_author, fallback_url, posts_list, group='regular'):
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
                
            posts_list.append((urn, author, commentary, post_url, post_date, profile_url, group))
