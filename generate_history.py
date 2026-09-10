import os
import sys
import django
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
django.setup()

from linkedin.models import DraftedComment

def main():
    drafts = DraftedComment.objects.all().order_by('-drafted_at')
    
    with open('/home/immanuels/.gemini/antigravity/brain/b8546926-5d28-4557-86a5-f4a7bb62caea/comment_history.md', 'w') as f:
        f.write("# 📝 Drafted Comments History\n\n")
        f.write("This artifact automatically tracks every post we have drafted comments for, to ensure we never send you duplicates and stay under the daily limit.\n\n")
        f.write("| Date Drafted | Post Author | Post URN | Post URL |\n")
        f.write("|--------------|-------------|----------|----------|\n")
        for draft in drafts:
            date_str = draft.drafted_at.strftime("%Y-%m-%d %H:%M:%S")
            post_url_str = f"[Link]({draft.post_url})" if draft.post_url else "N/A"
            f.write(f"| {date_str} | {draft.author_name} | `{draft.post_urn}` | {post_url_str} |\n")
            if draft.drafts_text:
                f.write(f"\n<details><summary>View Drafts for {draft.post_urn}</summary>\n\n```text\n{draft.drafts_text}\n```\n</details>\n\n")

if __name__ == "__main__":
    main()
