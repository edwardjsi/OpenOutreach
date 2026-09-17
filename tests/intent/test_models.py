import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from linkedin.models import Campaign, LinkedInProfile
from linkedin.intent.models import IntentSignal, SignalConfiguration

@pytest.fixture
def campaign(db):
    return Campaign.objects.create(name="Test Campaign")

@pytest.fixture
def profile(db):
    return LinkedInProfile.objects.create(linkedin_username="johndoe", public_id="johndoe")

@pytest.mark.django_db
class TestSignalConfiguration:
    def test_disabled_by_default(self, campaign):
        """ALL new signal-layer code paths MUST be disabled by default."""
        config = SignalConfiguration.objects.create(campaign=campaign)
        assert config.enabled is False
        assert config.top_icp_enabled is False
        assert config.job_change_enabled is False
        assert config.funding_enabled is False
        assert config.engagement_enabled is False
        assert config.competitor_enabled is False
        assert config.influencer_enabled is False
        assert config.your_company_enabled is False

@pytest.mark.django_db
class TestIntentSignal:
    def test_create_signal_provenance(self, campaign):
        """Test that IntentSignal correctly stores provenance and evidence."""
        signal = IntentSignal.objects.create(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.COMPETITOR,
            subject_id="johndoe",
            source="https://linkedin.com/company/competitor",
            evidence={"comment": "Great post!", "post_urn": "urn:li:activity:123"},
            confidence=0.9,
            dedupe_key="johndoe:COMPETITOR:urn:li:activity:123"
        )
        
        assert signal.campaign == campaign
        assert signal.signal_type == "COMPETITOR"
        assert signal.subject_id == "johndoe"
        assert signal.source == "https://linkedin.com/company/competitor"
        assert signal.evidence["comment"] == "Great post!"
        assert signal.confidence == 0.9
        assert signal.dedupe_key == "johndoe:COMPETITOR:urn:li:activity:123"
        assert signal.first_seen_at is not None
        assert signal.last_seen_at is not None
        assert signal.observed_at <= timezone.now()

    def test_deduplication_constraint(self, campaign):
        """Test that duplicate signals for the exact same observation are prevented."""
        IntentSignal.objects.create(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.JOB_CHANGE,
            subject_id="johndoe",
            source="Search Query",
            dedupe_key="johndoe:JOB_CHANGE:2024-01"
        )
        
        with pytest.raises(IntegrityError) as excinfo:
            IntentSignal.objects.create(
                campaign=campaign,
                signal_type=IntentSignal.SignalType.JOB_CHANGE,
                subject_id="johndoe",
                source="Search Query",
                dedupe_key="johndoe:JOB_CHANGE:2024-01"
            )
        assert "unique_signal_dedupe" in str(excinfo.value) or "UNIQUE constraint failed" in str(excinfo.value)

    def test_multiple_signals_different_keys(self, campaign):
        """Test that one candidate can accumulate multiple associated signals."""
        IntentSignal.objects.create(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.JOB_CHANGE,
            subject_id="johndoe",
            source="Search Query",
            dedupe_key="johndoe:JOB_CHANGE:2024-01"
        )
        
        IntentSignal.objects.create(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.COMPETITOR,
            subject_id="johndoe",
            source="https://linkedin.com/company/competitor",
            dedupe_key="johndoe:COMPETITOR:urn:li:activity:123"
        )
        
        assert IntentSignal.objects.filter(campaign=campaign, subject_id="johndoe").count() == 2

    def test_confidence_validation(self, campaign):
        from django.core.exceptions import ValidationError
        signal = IntentSignal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.COMPETITOR,
            subject_id="johndoe",
            source="Test",
            confidence=1.5,
            dedupe_key="johndoe:COMPETITOR:urn:1"
        )
        with pytest.raises(ValidationError):
            signal.full_clean()

@pytest.mark.django_db
class TestCanonicalLinkedInUrlMixin:
    def test_url_canonicalization(self, campaign):
        from linkedin.intent.models import CompetitorTarget
        config = SignalConfiguration.objects.create(campaign=campaign)
        target = CompetitorTarget(config=config, linkedin_url="http://linkedin.com/company/apple/")
        target.save()
        assert target.linkedin_url == "https://www.linkedin.com/company/apple"

    def test_confidence_db_constraint(self, campaign):
        from django.db.utils import IntegrityError
        signal = IntentSignal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.COMPETITOR,
            subject_id="johndoe",
            source="Test",
            confidence=1.5,
            dedupe_key="johndoe:COMPETITOR:urn:2"
        )
        with pytest.raises(IntegrityError):
            signal.save()
