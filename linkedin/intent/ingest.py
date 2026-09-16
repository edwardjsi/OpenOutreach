import logging
from typing import Dict, Any, Tuple, Optional
from django.utils import timezone
from django.db import transaction
from django.db.utils import IntegrityError

from crm.models import Lead
from linkedin.url_utils import url_to_public_id, public_id_to_url
from linkedin.intent.models import IntentSignal
from linkedin.models import Campaign

logger = logging.getLogger(__name__)

def resolve_candidate(linkedin_url: str) -> Tuple[Lead, bool]:
    """
    Resolve existing candidate identity or create using the existing mechanism.
    Returns (Lead, created_boolean).
    Raises ValueError if linkedin_url is invalid.
    """
    public_id = url_to_public_id(linkedin_url)
    if not public_id:
        raise ValueError(f"Invalid LinkedIn URL: {linkedin_url}")
    
    clean_url = public_id_to_url(public_id)
    
    lead, created = Lead.objects.get_or_create(
        public_identifier=public_id,
        defaults={"linkedin_url": clean_url}
    )
    
    if created:
        logger.info("CANDIDATE_CREATED: Created new sparse lead for %s", public_id)
    else:
        logger.info("CANDIDATE_REUSED: Reusing existing lead for %s", public_id)
        
    return lead, created

def build_dedupe_key(subject_id: str, signal_type: str, stable_event_id: str) -> str:
    """
    Construct a stable identifier for an observation.
    Format: <subject_id>:<signal_type>:<stable_event_id>
    """
    if not subject_id or not signal_type or not stable_event_id:
        raise ValueError("Missing components for dedupe key")
    return f"{subject_id}:{signal_type}:{stable_event_id}"

def ingest_signal(
    campaign: Campaign,
    signal_type: str,
    linkedin_url: str,
    source: str,
    evidence: Dict[str, Any],
    confidence: float,
    stable_event_id: str,
    observed_at: Optional[Any] = None
) -> Tuple[Optional[IntentSignal], Optional[Lead], str]:
    """
    Safely and idempotently ingest an intent signal without modifying
    existing qualification behaviors.
    """
    # 0. Feature Flag Enforcement
    # Normal runtime path cannot ingest new signals if the configuration is missing or disabled.
    from linkedin.intent.models import SignalConfiguration
    config = getattr(campaign, "signal_config", None)
    
    # We check if the overarching signal layer is active for this campaign
    # (or if this specific signal_type is enabled). For simplicity, we assume
    # the entire layer is governed by the existence and active status of SignalConfiguration.
    # We assume 'active=True' or we can check the specific boolean flag if known.
    # The requirement is that the feature is OFF by default.
    # If there is no config, the feature is OFF.
    if not config or not config.enabled:
        logger.info("SIGNAL_SKIPPED: High-Intent Signal Layer OFF for campaign %s", campaign.pk)
        return None, None, "DISABLED"
        
    # Example: Check if the specific signal type is enabled
    # Map SignalType to the config boolean fields if they exist
    # If the user hasn't explicitly enabled this signal agent, we skip.
    flag_field = f"{signal_type}_enabled"
    if hasattr(config, flag_field) and not getattr(config, flag_field):
        logger.info("SIGNAL_SKIPPED: %s is OFF for campaign %s", signal_type, campaign.pk)
        return None, None, "DISABLED"

    # 1. Application-level validation
    if not (0.0 <= confidence <= 1.0):
        logger.warning("SIGNAL_REJECTED: Invalid confidence %s for url %s", confidence, linkedin_url)
        raise ValueError(f"Confidence must be between 0.0 and 1.0, got {confidence}")
        
    # 2. Resolve Candidate Identity
    lead, _ = resolve_candidate(linkedin_url)
    subject_id = lead.public_identifier
    
    # Check disqualification purely for auditability/logging
    if lead.disqualified:
        # Note: We continue creating the signal for telemetry purposes, 
        # but the candidate won't enter downstream paths because existing
        # ReadyPools filter out disqualified=True.
        logger.info("SIGNAL_FOR_DISQUALIFIED_CANDIDATE: Candidate %s is disqualified.", subject_id)
        
    # 3. Deduplication Logic
    dedupe_key = build_dedupe_key(subject_id, signal_type, stable_event_id)
    
    existing = IntentSignal.objects.filter(campaign=campaign, dedupe_key=dedupe_key).first()
    
    if existing:
        # Update last_seen_at idempotently
        existing.last_seen_at = timezone.now()
        existing.save(update_fields=["last_seen_at"])
        logger.info("SIGNAL_DEDUPLICATED: Updated last_seen_at for signal %s", dedupe_key)
        return existing, lead, "DEDUPLICATED"
        
    # 4. Create Signal Safely (Race-Safe)
    try:
        # Must be atomic so the IntegrityError doesn't break the outer transaction
        with transaction.atomic():
            kwargs = {
                "campaign": campaign,
                "signal_type": signal_type,
                "subject_id": subject_id,
                "source": source,
                "evidence": evidence,
                "confidence": confidence,
                "dedupe_key": dedupe_key,
            }
            if observed_at is not None:
                kwargs["observed_at"] = observed_at
                
            new_signal = IntentSignal.objects.create(**kwargs)
            logger.info("SIGNAL_CREATED: Ingested new signal %s for %s", signal_type, subject_id)
            return new_signal, lead, "CREATED"
            
    except IntegrityError:
        # We hit a race condition, another worker created the exact dedupe_key
        logger.info("SIGNAL_DEDUPLICATED: Race detected, re-fetching signal %s", dedupe_key)
        raced_existing = IntentSignal.objects.get(campaign=campaign, dedupe_key=dedupe_key)
        raced_existing.last_seen_at = timezone.now()
        raced_existing.save(update_fields=["last_seen_at"])
        return raced_existing, lead, "DEDUPLICATED"

