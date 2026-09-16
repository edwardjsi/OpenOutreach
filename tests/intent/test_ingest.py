import pytest
from unittest.mock import patch
from django.utils import timezone
from django.db.utils import IntegrityError
from datetime import timedelta
import threading

from crm.models import Lead
from linkedin.models import Campaign
from linkedin.intent.models import IntentSignal
from linkedin.intent.ingest import (
    resolve_candidate,
    build_dedupe_key,
    ingest_signal
)

@pytest.fixture
def campaign(db):
    return Campaign.objects.create(name="Ingest Test Campaign")

@pytest.mark.django_db
class TestIntentIngestion:
    
    # A. New candidate + New signal
    def test_new_candidate_and_signal(self, campaign):
        url = "https://www.linkedin.com/in/newguy"
        signal, lead, status = ingest_signal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.COMPETITOR,
            linkedin_url=url,
            source="Competitor Profile",
            evidence={"post": "123"},
            confidence=0.9,
            stable_event_id="urn:li:123"
        )
        assert status == "CREATED"
        assert lead.public_identifier == "newguy"
        assert signal.subject_id == "newguy"
        assert Lead.objects.count() == 1
        assert IntentSignal.objects.count() == 1
        
    # B. Existing candidate reused
    def test_existing_candidate_reused(self, campaign):
        Lead.objects.create(public_identifier="existingguy", linkedin_url="https://www.linkedin.com/in/existingguy")
        
        url = "https://www.linkedin.com/in/existingguy"
        signal, lead, status = ingest_signal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.TOP_ICP,
            linkedin_url=url,
            source="Search",
            evidence={},
            confidence=1.0,
            stable_event_id="search:001"
        )
        assert status == "CREATED"
        assert lead.public_identifier == "existingguy"
        assert Lead.objects.count() == 1 # Still 1 lead
        
    # C. Same signal twice (deduplicated, updates last_seen_at)
    def test_same_signal_twice_deduplicated(self, campaign):
        url = "https://www.linkedin.com/in/dup-guy"
        
        # First ingest
        sig1, _, stat1 = ingest_signal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.FUNDING,
            linkedin_url=url,
            source="News",
            evidence={"round": "Series A"},
            confidence=1.0,
            stable_event_id="news:111"
        )
        assert stat1 == "CREATED"
        
        # Modify last_seen_at to simulate time passing
        old_time = timezone.now() - timedelta(days=1)
        IntentSignal.objects.filter(pk=sig1.pk).update(last_seen_at=old_time)
        
        # Second ingest (exact same signal)
        sig2, _, stat2 = ingest_signal(
            campaign=campaign,
            signal_type=IntentSignal.SignalType.FUNDING,
            linkedin_url=url,
            source="News",
            evidence={"round": "Series A - Different text to be ignored"},
            confidence=1.0,
            stable_event_id="news:111"
        )
        
        assert stat2 == "DEDUPLICATED"
        assert sig2.pk == sig1.pk
        assert IntentSignal.objects.count() == 1
        assert sig2.last_seen_at > old_time
        
    # D. Same candidate, different signals (two signals accumulated)
    def test_same_candidate_different_signals(self, campaign):
        url = "https://www.linkedin.com/in/multi-guy"
        ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.JOB_CHANGE,
            linkedin_url=url, source="A", evidence={}, confidence=1.0, stable_event_id="job:1"
        )
        ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.JOB_CHANGE,
            linkedin_url=url, source="A", evidence={}, confidence=1.0, stable_event_id="job:2"
        )
        
        assert IntentSignal.objects.count() == 2
        assert Lead.objects.count() == 1

    # F. Invalid confidence rejected cleanly
    def test_invalid_confidence_rejected(self, campaign):
        url = "https://www.linkedin.com/in/bad-conf"
        with pytest.raises(ValueError, match="Confidence must be between 0.0 and 1.0"):
            ingest_signal(
                campaign=campaign, signal_type=IntentSignal.SignalType.TOP_ICP,
                linkedin_url=url, source="A", evidence={}, confidence=1.5, stable_event_id="x"
            )

    # G. Missing/invalid candidate identity fails safely
    def test_invalid_candidate_url_fails(self, campaign):
        with pytest.raises(ValueError, match="Invalid LinkedIn URL"):
            ingest_signal(
                campaign=campaign, signal_type=IntentSignal.SignalType.TOP_ICP,
                linkedin_url="not_a_url", source="A", evidence={}, confidence=1.0, stable_event_id="x"
            )

    # H. Evidence preservation confirmed during deduplication
    def test_evidence_preservation(self, campaign):
        url = "https://www.linkedin.com/in/evi-guy"
        original_evidence = {"important": "data"}
        
        ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.INFLUENCER,
            linkedin_url=url, source="Src", evidence=original_evidence, confidence=1.0, stable_event_id="ev:1"
        )
        
        # Second ingest with new evidence
        sig2, _, _ = ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.INFLUENCER,
            linkedin_url=url, source="Src", evidence={"less_important": "data"}, confidence=1.0, stable_event_id="ev:1"
        )
        
        assert sig2.evidence == original_evidence

    # I. Disqualified candidate retains signal
    def test_disqualified_candidate_retains_signal(self, campaign):
        Lead.objects.create(public_identifier="disq-guy", linkedin_url="https://www.linkedin.com/in/disq-guy", disqualified=True)
        
        signal, lead, status = ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.TOP_ICP,
            linkedin_url="https://www.linkedin.com/in/disq-guy", source="X", evidence={}, confidence=1.0, stable_event_id="disq:1"
        )
        
        assert status == "CREATED"
        assert lead.disqualified is True
        assert IntentSignal.objects.filter(subject_id="disq-guy").exists()

    # E. Duplicate concurrent ingestion
    def test_duplicate_concurrent_ingestion_race(self, campaign):
        url = "https://www.linkedin.com/in/race-guy"
        
        existing_sig, _, _ = ingest_signal(
            campaign=campaign, signal_type=IntentSignal.SignalType.FUNDING,
            linkedin_url=url, source="News", evidence={}, confidence=1.0, stable_event_id="race:1"
        )
        
        with patch("django.db.models.query.QuerySet.first", return_value=None):
            sig2, _, status = ingest_signal(
                campaign=campaign, signal_type=IntentSignal.SignalType.FUNDING,
                linkedin_url=url, source="News", evidence={}, confidence=1.0, stable_event_id="race:1"
            )
            
        assert status == "DEDUPLICATED"
        assert sig2.pk == existing_sig.pk
