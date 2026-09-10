import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
django.setup()

from linkedin.browser.registry import get_first_active_profile
from linkedin.browser.session import AccountSession
from linkedin.tasks.draft_comments_task import handle_draft_comments

def main():
    lp = get_first_active_profile()
    if not lp:
        print("No active profile")
        return
        
    session = AccountSession(lp)
    session.ensure_browser()
    
    print("Running background draft comments task manually for testing...")
    try:
        handle_draft_comments(None, session, None)
        print("Done!")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()
