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
        f.write("| Date Drafted | Post Author | Post URN |\n")
        f.write("|--------------|-------------|----------|\n")
        for draft in drafts:
            date_str = draft.drafted_at.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"| {date_str} | {draft.author_name} | `{draft.post_urn}` |\n")

if __name__ == "__main__":
    main()
