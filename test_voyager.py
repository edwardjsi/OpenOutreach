import os
import sys
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
django.setup()

from linkedin.browser.registry import get_first_active_profile
from linkedin.browser.session import AccountSession
from linkedin.api.client import PlaywrightLinkedinAPI

def main():
    lp = get_first_active_profile()
    if not lp:
        print("No active profile")
        return
        
    session = AccountSession(lp)
    session.ensure_browser()
    
    api = PlaywrightLinkedinAPI(session)
    
    params = {"count": "1", "q": "feed"}
    res = api.get("https://www.linkedin.com/voyager/api/feed/updatesV2", params=params)
    if res.ok:
        data = res.json()
        included = data.get("included", [])
        for item in included:
            if item.get("$type") == "com.linkedin.voyager.feed.render.UpdateV2":
                print(json.dumps(item, indent=2))
                break
    else:
        print(res.text())
        
    session.close()

if __name__ == "__main__":
    main()
