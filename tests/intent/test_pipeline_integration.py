import pytest
from unittest.mock import patch, MagicMock
from django.utils import timezone
from linkedin.models import Campaign
from crm.models import Lead
from linkedin.intent.models import SignalConfiguration, IntentSignal
from linkedin.pipeline.qualify import _fetch_intent_signals_text, fetch_qualification_candidates

@pytest.fixture
def session():
    class MockSession:
        def __init__(self):
            self.campaign = Campaign.objects.create(name="Test Campaign", product_docs="docs", campaign_objective="obj")
            
    return MockSession()

@pytest.fixture
def other_campaign():
    return Campaign.objects.create(name="Other Campaign")

@pytest.fixture
def config(session):
    return SignalConfiguration.objects.create(campaign=session.campaign, enabled=True)

@pytest.fixture
def lead1():
    # Embedded lead
    l = Lead.objects.create(public_identifier="user1", linkedin_url="https://www.linkedin.com/in/user1/")
    l.embedding_array = [0.1] * 384
    l.save()
    return l

@pytest.fixture
def lead2():
    # Embedded lead
    l = Lead.objects.create(public_identifier="user2", linkedin_url="https://www.linkedin.com/in/user2/")
    l.embedding_array = [0.2] * 384
    l.save()
    return l

@pytest.fixture
def sparse_lead1():
    # Sparse lead without signal
    return Lead.objects.create(public_identifier="sparse1", linkedin_url="https://www.linkedin.com/in/sparse1/")

@pytest.fixture
def sparse_lead2():
    # Sparse lead WITH signal
    return Lead.objects.create(public_identifier="sparse2", linkedin_url="https://www.linkedin.com/in/sparse2/")

@pytest.mark.django_db
def test_feature_flag_off_no_signal_context(session, config, sparse_lead2):
    # A. Feature flag OFF: signal exists -> no signal context + no prioritization.
    config.enabled = False
    config.save()
    
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Test",
        dedupe_key="1"
    )
    
    # Context should be empty
    text = _fetch_intent_signals_text(session, sparse_lead2.public_identifier)
    assert text == ""

@pytest.mark.django_db
def test_feature_flag_on_context_reaches_llm(session, config, sparse_lead2):
    # B. Feature flag ON: signal exists -> signal context reaches LLM.
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Test",
        dedupe_key="1"
    )
    
    text = _fetch_intent_signals_text(session, sparse_lead2.public_identifier)
    assert "Top ICP" in text
    assert "Test" in text

@pytest.mark.django_db
def test_campaign_isolation(session, config, other_campaign, sparse_lead2):
    # C. Campaign isolation: current campaign receives only its own signals.
    IntentSignal.objects.create(
        campaign=other_campaign,
        signal_type=IntentSignal.SignalType.JOB_CHANGE,
        subject_id=sparse_lead2.public_identifier,
        source="Test",
        dedupe_key="1"
    )
    
    text = _fetch_intent_signals_text(session, sparse_lead2.public_identifier)
    assert text == ""  # No signals for current campaign

@pytest.mark.django_db
def test_multiple_signals_and_evidence_preservation(session, config, sparse_lead2):
    # D. Multiple signals preserved
    # E. Evidence preservation
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Source 1",
        evidence={"score": 0.99},
        dedupe_key="1"
    )
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.JOB_CHANGE,
        subject_id=sparse_lead2.public_identifier,
        source="Source 2",
        evidence={"previous_company": "Acme"},
        dedupe_key="2"
    )
    
    text = _fetch_intent_signals_text(session, sparse_lead2.public_identifier)
    assert "Top ICP" in text
    assert "Source 1" in text
    assert "0.99" in text
    
    assert "Job Change" in text
    assert "Source 2" in text
    assert "Acme" in text

@pytest.mark.django_db
def test_sparse_prioritization(session, config, sparse_lead1, sparse_lead2):
    # F. Sparse prioritization: signal-backed sparse lead is selected before an otherwise equivalent sparse lead without a signal when fallback is invoked.
    # sparse1 has NO signal. sparse2 has signal.
    # We must ensure sparse2 is selected first in the fallback.
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Source 1",
        dedupe_key="1"
    )
    
    # Mock get_leads_for_qualification to return both
    with patch("linkedin.db.leads.get_leads_for_qualification", return_value=[
        sparse_lead1.to_profile_dict(),
        sparse_lead2.to_profile_dict()
    ]):
        # Mock get_embedding so it returns "mock_embedding" when called
        with patch.object(Lead, 'get_embedding', return_value="mock_emb"):
            candidates = fetch_qualification_candidates(session)
            
            # Since neither has embedding, it goes to fallback.
            # Fallback should prioritize sparse2 and return it first.
            assert len(candidates) == 1
            assert candidates[0].public_identifier == sparse_lead2.public_identifier

@pytest.mark.django_db
def test_embedded_candidate_behavior(session, config, lead1, sparse_lead2):
    # G. Embedded-candidate behavior: existing embedded candidates continue through existing GP/BALD ordering; signal-backed sparse candidates do not jump ahead.
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Source 1",
        dedupe_key="1"
    )
    
    with patch("linkedin.db.leads.get_leads_for_qualification", return_value=[
        lead1.to_profile_dict(),
        sparse_lead2.to_profile_dict()
    ]):
        candidates = fetch_qualification_candidates(session)
        # Should return lead1 because it has an embedding. Fallback is NOT reached.
        assert len(candidates) == 1
        assert candidates[0].public_identifier == lead1.public_identifier

@pytest.mark.django_db
def test_existing_behavior_disabled(session, config, sparse_lead1, sparse_lead2):
    # H. Existing behavior: when the signal feature is disabled, qualification behavior is unchanged.
    config.enabled = False
    config.save()
    
    IntentSignal.objects.create(
        campaign=session.campaign,
        signal_type=IntentSignal.SignalType.TOP_ICP,
        subject_id=sparse_lead2.public_identifier,
        source="Source 1",
        dedupe_key="1"
    )
    
    with patch("linkedin.db.leads.get_leads_for_qualification", return_value=[
        sparse_lead1.to_profile_dict(),
        sparse_lead2.to_profile_dict()
    ]):
        with patch.object(Lead, 'get_embedding', return_value="mock_emb"):
            candidates = fetch_qualification_candidates(session)
            # Since feature is disabled, fallback just preserves the original order 
            # (which was sparse1 then sparse2). So sparse1 should be returned.
            assert len(candidates) == 1
            assert candidates[0].public_identifier == sparse_lead1.public_identifier
