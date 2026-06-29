"""Test Telegram alert configuration end-to-end.

Usage::

    python manage.py testtelegram

Reads ``SiteConfig.telegram_bot_token`` and ``telegram_chat_id``, sends a
fixed test message via the Telegram Bot API, and prints the result. Use this
to verify your bot token and chat ID are correct before relying on
checkpoint alerts.
"""
from django.core.management.base import BaseCommand

from linkedin.models import SiteConfig
from linkedin.notifications import _send_telegram


class Command(BaseCommand):
    help = "Send a test Telegram message to verify alert configuration."

    def handle(self, *args, **options):
        config = SiteConfig.load()
        token = (config.telegram_bot_token or "").strip()
        chat_id = (config.telegram_chat_id or "").strip()

        if not token:
            self.stderr.write(
                self.style.ERROR(
                    "telegram_bot_token is not set. "
                    "Create a bot via @BotFather (/newbot) and set it in Admin → Site Configuration."
                )
            )
            return
        if not chat_id:
            self.stderr.write(
                self.style.ERROR(
                    "telegram_chat_id is not set. "
                    "Get your chat ID from @userinfobot and set it in Admin → Site Configuration."
                )
            )
            return

        self.stdout.write(f"Sending test message to chat_id={chat_id} ...")
        text = "✅ OpenOutreach Telegram alerts are configured correctly."
        if _send_telegram(token, chat_id, text):
            self.stdout.write(
                self.style.SUCCESS("Success! Check your Telegram for the message.")
            )
        else:
            self.stderr.write(
                self.style.ERROR(
                    "Failed — see WARNING logs above. "
                    "Verify the bot token and chat ID, and that you've started a "
                    "conversation with the bot (send /start to it first)."
                )
            )
