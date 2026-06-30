"""Dump a fresh LinkedIn login session for the daemon.

Opens a **headful** Playwright browser (visible via VNC), waits for you to log in
naturally on the VNC screen, then saves the ``storage_state`` (cookies + localStorage)
to ``LinkedInProfile.cookie_data``. The daemon **never** sees a checkpoint challenge
after this — it reuses your real browser session.

Usage::

    python manage.py dumpcookies

Or via Docker::

    make dumpcookies

Then:

1. Open ``http://localhost:6080/vnc.html`` in your browser
2. Log into LinkedIn **naturally** (type your email/password manually)
3. When ``/feed`` loads, **press Enter** in the terminal running this command
4. The session is saved — run the daemon with ``make run``
"""
from __future__ import annotations

import logging
import time
from urllib.parse import unquote

from django.core.management.base import BaseCommand, CommandError

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
_DUMP_POLL_INTERVAL_S = 3
_DUMP_TIMEOUT_S = 300  # 5 minutes — should be plenty


class Command(BaseCommand):
    help = "Open a VNC-visible browser, let you log in naturally, then dump the session."

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile",
            type=str,
            default=None,
            help="LinkedInProfile ID (from DB). Default: first profile.",
        )

    def handle(self, *args, **options):
        from linkedin.models import LinkedInProfile

        profiles = list(LinkedInProfile.objects.all())
        if not profiles:
            raise CommandError(
                "No LinkedInProfile found. Run `onboard` first to add a profile."
            )

        profile_id = options.get("profile")
        if profile_id:
            lp = LinkedInProfile.objects.filter(pk=profile_id).first()
            if not lp:
                raise CommandError(f"Profile {profile_id} not found.")
        else:
            lp = profiles[0]

        self.stdout.write(
            self.style.NOTICE(
                f"Opening a headful Playwright browser for {lp}…\n"
                "1. Open http://localhost:6080/vnc.html in your browser\n"
                "2. Log into LinkedIn naturally (type your email/password manually)\n"
                "3. When /feed loads, press Enter in this terminal\n"
            )
        )

        # Launch a headful (non-headless) browser — visible via VNC
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        context.set_default_timeout(30_000)
        Stealth().apply_stealth_sync(context)
        page = context.new_page()

        page.goto(LINKEDIN_FEED_URL)

        self.stdout.write(
            self.style.WARNING(
                "Waiting for you to log in on the VNC screen… "
                f"(timeout: {_DUMP_TIMEOUT_S}s)"
            )
        )

        deadline = time.monotonic() + _DUMP_TIMEOUT_S
        while time.monotonic() < deadline:
            current = unquote(page.url)
            if "/feed" in current:
                # User is logged in — dump the session
                state = context.storage_state()
                lp.cookie_data = state
                lp.save(update_fields=["cookie_data"])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Session saved for {lp} — "
                        f"{len(state.get('cookies', []))} cookies, "
                        f"{len(state.get('origins', []))} origins. "
                        "The daemon will reuse this session on next run."
                    )
                )
                browser.close()
                return

            if "/login" in current or "/checkpoint/" in current:
                self.stdout.write(
                    "Still on login/checkpoint page — waiting… "
                    f"URL: {current[:80]}"
                )

            time.sleep(_DUMP_POLL_INTERVAL_S)

        # If we get here, it means /feed never loaded
        browser.close()
        raise CommandError(
            f"Timed out after {_DUMP_TIMEOUT_S}s — /feed never loaded. "
            "Check VNC at http://localhost:6080/vnc.html for the current state."
        )