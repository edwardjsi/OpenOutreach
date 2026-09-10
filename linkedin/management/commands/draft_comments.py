import logging
import re
from textwrap import dedent

from django.core.management.base import BaseCommand
from openai import OpenAI

from linkedin.models import SiteConfig
from linkedin.browser.registry import get_first_active_profile
from linkedin.browser.session import AccountSession
from linkedin.notifications import _send_telegram

logger = logging.getLogger(__name__)

# The T1 Missing-Piece template
SYSTEM_PROMPT = """You are an expert B2B LinkedIn ghostwriter. Your job is to draft an engaging comment on a provided LinkedIn post.
Use the "Missing-Piece" template:
"[Name] the [their-thesis] argument misses one piece.. [what-moved]. when [their-condition], the real differentiator is [specific-skill], not [their-focus]."

Rules:
- 200-350 chars max.
- Always capitalize the author's name when addressing them by first name.
- Em dashes (—) capped at about 1 per 100 words.
- No hashtags, no emoji.
- No AI vocabulary (leverage, streamline, harness, delve, unlock).
- Use natural lowercase for sentence starts if appropriate, but keep names capitalized.

Return 2 distinct variants of the comment using this pattern. Format as plain text with blank lines between variants.
"""

class Command(BaseCommand):
    help = "Draft comments for influencer profiles and send via Telegram"

    def add_arguments(self, parser):
        parser.add_argument('profile_urls', nargs='+', type=str, help='LinkedIn profile URLs')

    def handle(self, *args, **options):
        profile_urls = options['profile_urls']

        config = SiteConfig.objects.first()
        if not config:
            self.stdout.write(self.style.ERROR("SiteConfig not found. Please complete onboarding."))
            return

        api_key = config.llm_api_key
        if not api_key:
            self.stdout.write(self.style.ERROR("OpenAI API key not configured."))
            return

        telegram_token = (config.telegram_bot_token or "").strip()
        telegram_chat_id = (config.telegram_chat_id or "").strip()
        if not telegram_token or not telegram_chat_id:
            self.stdout.write(self.style.ERROR("Telegram not fully configured in SiteConfig."))
            return

        lp = get_first_active_profile()
        if not lp:
            self.stdout.write(self.style.ERROR("No active LinkedInProfile found."))
            return

        session = AccountSession(lp)
        session.ensure_browser()

        client = OpenAI(api_key=api_key)

        for profile_url in profile_urls:
            self.stdout.write(f"Processing {profile_url}...")
            
            # Extract username from URL
            match = re.search(r'linkedin\.com/in/([^/]+)', profile_url)
            if not match:
                self.stdout.write(self.style.WARNING(f"Could not parse username from {profile_url}"))
                continue
            username = match.group(1)
            
            activity_url = f"https://www.linkedin.com/in/{username}/recent-activity/shares/"
            
            try:
                # Get profile first to resolve public_identifier to URN
                from linkedin.api.client import PlaywrightLinkedinAPI
                api = PlaywrightLinkedinAPI(session)
                
                profile_data, raw_data = api.get_profile(public_identifier=username)
                profile_urn = profile_data.get("urn")
                
                if not profile_urn:
                    self.stdout.write(self.style.ERROR(f"Could not resolve URN for {username}"))
                    continue
                
                # Fetch recent activity via Voyager API instead of DOM scraping
                params = {
                    "count": "1",
                    "profileUrn": profile_urn,
                    "q": "memberShareFeed"
                }
                
                res = api.get("https://www.linkedin.com/voyager/api/identity/profileUpdatesV2", params=params)
                
                if not res.ok:
                    self.stdout.write(self.style.ERROR(f"API returned {res.status} for {username} activity"))
                    continue
                    
                data = res.json()
                included = data.get("included", [])
                
                update = None
                for item in included:
                    if item.get("$type") == "com.linkedin.voyager.feed.render.UpdateV2":
                        update = item
                        break
                
                if not update:
                    self.stdout.write(self.style.WARNING(f"No recent posts found for {username}"))
                    continue
                
                # Try to extract the text
                post_text = ""
                post_url = ""
                
                try:
                    # Method 1: commentary
                    commentary = update.get("commentary", {}).get("text", {}).get("text")
                    
                    # Extract URL
                    post_url = update.get("socialContent", {}).get("shareUrl", "")
                    
                    # Method 2: Check for reshared update commentary
                    if not commentary:
                        reshared = update.get("resharedUpdate", {})
                        if isinstance(reshared, dict):
                            val = reshared.get("value", {}).get("com.linkedin.voyager.feed.render.UpdateV2", {})
                            commentary = val.get("commentary", {}).get("text", {}).get("text")
                            if not post_url:
                                post_url = val.get("socialContent", {}).get("shareUrl", "")
                        
                    post_text = commentary or "Could not extract post text from API response."
                except Exception as e:
                    post_text = f"Error parsing post text: {e}"
                
                # Fallback URL if shareUrl is missing
                if not post_url:
                    post_url = profile_url
                
                post_author = profile_data.get("full_name", username)
                
                self.stdout.write(f"Extracted post from {post_author} ({len(post_text)} chars)")
                
                # Generate comments
                response = client.chat.completions.create(
                    model=config.ai_model or "gpt-4o",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Author: {post_author}\nPost:\n{post_text}"}
                    ],
                    temperature=0.7
                )
                
                drafts = response.choices[0].message.content
                
                # Send to Telegram
                msg = f"📝 *Drafted Comments for {post_author}*\n\n"
                msg += f"Post URL: {post_url}\n\n"
                msg += f"*Post Snippet:*\n{post_text[:150]}...\n\n"
                msg += f"*Drafts:*\n{drafts}"
                
                _send_telegram(telegram_token, telegram_chat_id, msg)
                self.stdout.write(self.style.SUCCESS(f"Sent drafts for {username} to Telegram!"))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing {profile_url}: {e}"))
                logger.exception(f"Error processing {profile_url}")

        session.close()
