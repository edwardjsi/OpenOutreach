# tests/conftest.py
import socket as _socket
from unittest.mock import patch

import numpy as np
import pytest

from linkedin.management.setup_crm import setup_crm
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _ensure_crm_data(db):
    """
    Ensure CRM bootstrap data exists before every test.
    Uses `db` fixture (not transactional_db) for compatibility.
    Since transaction=True tests rollback, we re-create data each time.
    """
    setup_crm()


@pytest.fixture(autouse=True)
def _mock_embeddings(request):
    """Stub fastembed so tests don't need the ONNX model."""
    if "no_embed_mock" in request.keywords:
        yield
    else:
        with patch("linkedin.ml.embeddings.embed_text", return_value=np.ones(384)):
            yield


class FakeAccountSession:
    """Minimal stand-in for AccountSession — exposes django_user + campaign."""

    def __init__(self, django_user, linkedin_profile, campaign):
        self.django_user = django_user
        self.linkedin_profile = linkedin_profile
        self.campaign = campaign
        self.self_profile = {
            "first_name": "Diego",
            "last_name": "Ramirez",
            "urn": "urn:li:fsd_profile:TEST",
        }

    @property
    def campaigns(self):
        from linkedin.models import Campaign
        return Campaign.objects.filter(users=self.django_user)

    def ensure_browser(self):
        pass


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from linkedin.models import Campaign, LinkedInProfile

    user = UserFactory(username="testuser")

    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = Campaign.objects.create(name="LinkedIn Outreach")
    campaign.users.add(user)

    linkedin_profile, _ = LinkedInProfile.objects.get_or_create(
        user=user,
        defaults={
            "linkedin_username": "testuser@example.com",
            "linkedin_password": "testpass",
        },
    )

    return FakeAccountSession(django_user=user, linkedin_profile=linkedin_profile, campaign=campaign)


# ── Outbound-network guard ────────────────────────────────────────────────
# The repo automates a real LinkedIn account. Tests must NEVER reach it (or
# any external service). This autouse fixture blocks all real sockets so an
# accidental live call (Playwright, requests, Voyager, LLM API, Telegram)
# fails fast inside the suite instead of touching the account.
# Opt out per-test only with @pytest.mark.allow_network — and never for live
# LinkedIn calls. Belt-and-suspenders with the static scanner at
# scripts/account_safety.py (`make safety-check`).


class _NetworkBlockedError(RuntimeError):
    pass


_ORIG_SOCKET = _socket.socket
_ORIG_CREATE_CONNECTION = _socket.create_connection


def _blocked(family=_socket.AF_INET, *args, **kwargs):
    # Only real internet sockets (TCP/UDP over IPv4/IPv6) are off-limits.
    # Local AF_UNIX sockets — asyncio's event-loop self-pipe, Playwright's
    # driver transport — are machinery, not network, and must keep working.
    if family in (-1, _socket.AF_INET, _socket.AF_INET6):
        raise _NetworkBlockedError(
            "Outbound network blocked in tests — tests must never reach "
            "LinkedIn or any external service. Use mocks (patch, "
            "FakeAccountSession). If a loopback socket is genuinely needed, "
            "mark the test @pytest.mark.allow_network (never for live "
            "LinkedIn calls)."
        )
    return _ORIG_SOCKET(family, *args, **kwargs)


@pytest.fixture(autouse=True)
def _block_outbound_network(request):
    if "allow_network" in request.keywords:
        yield
        return
    _socket.socket = _blocked
    _socket.create_connection = _blocked
    try:
        yield
    finally:
        _socket.socket = _ORIG_SOCKET
        _socket.create_connection = _ORIG_CREATE_CONNECTION
