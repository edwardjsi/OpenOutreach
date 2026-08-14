"""Tests for linkedin.notifications — Telegram push + bell fallback."""
import json
from unittest.mock import patch
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from urllib.parse import parse_qs

import pytest

from linkedin.notifications import _send_telegram, notify_checkpoint
from linkedin.models import SiteConfig


class _EchoHandler(BaseHTTPRequestHandler):
    """Replies with a Telegram-style ``{"ok": true}`` JSON and records the request."""

    last_request = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        _EchoHandler.last_request = parse_qs(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())

    def log_message(self, *args):
        pass  # silence test server logs


@pytest.fixture
def echo_server():
    _EchoHandler.last_request = None
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


class TestSendTelegram:
    @pytest.mark.allow_network  # loopback echo server (127.0.0.1) test double — not live LinkedIn
    def test_success_returns_true(self, echo_server):
        """_send_telegram POSTs to /bot<token>/sendMessage and returns True on ok:true."""
        _EchoHandler.last_request = None
        with patch("linkedin.notifications.TELEGRAM_API_BASE", f"http://127.0.0.1:{echo_server}"):
            ok = _send_telegram(
                "123:abc",
                "987654",
                "test message",
            )
        assert ok is True
        assert _EchoHandler.last_request is not None
        assert _EchoHandler.last_request["chat_id"][0] == "987654"
        assert _EchoHandler.last_request["text"][0] == "test message"
        assert _EchoHandler.last_request["parse_mode"][0] == "Markdown"

    def test_dead_url_returns_false(self):
        """_send_telegram returns False (never raises) on network error."""
        with patch("linkedin.notifications.TELEGRAM_API_BASE", "http://127.0.0.1:1"):
            ok = _send_telegram("bad", "bad", "test")
        assert ok is False

    @pytest.mark.allow_network  # loopback echo server (127.0.0.1) test double — not live LinkedIn
    def test_ok_false_returns_false(self, echo_server):
        """_send_telegram returns False when the API responds ok:false."""
        class _FailHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(
                    {"ok": False, "description": "chat not found"}
                ).encode())
            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), _FailHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with patch("linkedin.notifications.TELEGRAM_API_BASE", f"http://127.0.0.1:{port}"):
            ok = _send_telegram("x", "y", "z")
        server.shutdown()
        assert ok is False


@pytest.mark.django_db
class TestNotifyCheckpoint:
    def test_no_config_logs_only_no_raise(self, capsys):
        """With no Telegram config, notify_checkpoint fires bell+banner and returns."""
        # Ensure SiteConfig has no Telegram fields set
        config = SiteConfig.load()
        config.telegram_bot_token = ""
        config.telegram_chat_id = ""
        config.save()

        session = type("S", (), {"linkedin_profile": type("P", (), {"linkedin_username": "test"})()})()

        # Should not raise
        notify_checkpoint(session)

        # Bell + banner should be in stderr
        captured = capsys.readouterr()
        assert "CHECKPOINT" in captured.err

    @pytest.mark.allow_network  # loopback echo server (127.0.0.1) test double — not live LinkedIn
    def test_with_config_sends_telegram(self, echo_server):
        """With Telegram config set, notify_checkpoint calls _send_telegram."""
        _EchoHandler.last_request = None
        config = SiteConfig.load()
        config.telegram_bot_token = "123:abc"
        config.telegram_chat_id = "987654"
        config.save()

        session = type("S", (), {"linkedin_profile": type("P", (), {"linkedin_username": "test"})()})()

        with patch("linkedin.notifications.TELEGRAM_API_BASE", f"http://127.0.0.1:{echo_server}"):
            notify_checkpoint(session)

        assert _EchoHandler.last_request is not None
        assert _EchoHandler.last_request["chat_id"][0] == "987654"
        assert "checkpoint" in _EchoHandler.last_request["text"][0].lower()


@pytest.mark.django_db
class TestSendDailyReport:
    """send_daily_report must reflect the day's activity and send via Telegram.

    Regression: the conversations/failed counters queried `state="CONNECTED"`
    / `state="FAILED"` while the DB stores title-case ProfileState values
    ("Connected"/"Failed"), so both lines always reported zero.
    """

    def test_counts_use_profile_state_values(self):
        from linkedin.enums import ProfileState
        from linkedin.models import ActionLog, Campaign, LinkedInProfile
        from linkedin.notifications import send_daily_report
        from tests.factories import DealFactory, LeadFactory, UserFactory

        campaign = Campaign.objects.create(name="Test Campaign")
        profile = LinkedInProfile.objects.create(
            user=UserFactory(),
            linkedin_username="tester@example.com",
            linkedin_password="secret",
        )

        # 1 connect action logged today
        ActionLog.objects.create(
            action_type=ActionLog.ActionType.CONNECT,
            campaign=campaign,
            linkedin_profile=profile,
        )
        # 1 Connected deal → new conversation
        DealFactory(lead=LeadFactory(), campaign=campaign, state=ProfileState.CONNECTED.value)
        # 1 Failed deal updated today
        DealFactory(lead=LeadFactory(), campaign=campaign, state=ProfileState.FAILED.value)

        config = SiteConfig.load()
        config.telegram_bot_token = "123:abc"
        config.telegram_chat_id = "987654"

        sent = {}

        def _capture(token, chat_id, text):
            sent["text"] = text
            return True

        with (
            patch("linkedin.models.SiteConfig.load", return_value=config),
            patch("linkedin.notifications._send_telegram", side_effect=_capture),
        ):
            send_daily_report(session=None)

        assert "Connects sent: 1" in sent["text"]
        assert "New conversations: 1" in sent["text"]
        assert "Failed: 1" in sent["text"]