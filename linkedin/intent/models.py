# linkedin/intent/models.py
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class CanonicalLinkedInUrlMixin:
    def clean(self):
        if hasattr(super(), "clean"):
            super().clean()
        if hasattr(self, 'linkedin_url') and self.linkedin_url:
            url = self.linkedin_url.strip().lower()
            if url.startswith("http://"):
                url = url.replace("http://", "https://")
            if "www.linkedin.com" not in url:
                url = url.replace("linkedin.com", "www.linkedin.com")
            self.linkedin_url = url.rstrip("/")
            
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class SignalConfiguration(models.Model):
    """
    Master configuration for the High-Intent Signal Layer.
    ALL new signal-layer code paths MUST be disabled by default.
    Enabling the feature must be an explicit configuration decision.
    """
    campaign = models.OneToOneField("linkedin.Campaign", on_delete=models.CASCADE, related_name="signal_config")
    
    # Feature flag: MUST be false by default
    enabled = models.BooleanField(
        default=False, 
        help_text="Master switch for the High-Intent Signal Layer. MUST be false by default."
    )
    
    # Specific agent flags
    top_icp_enabled = models.BooleanField(default=False)
    job_change_enabled = models.BooleanField(default=False)
    funding_enabled = models.BooleanField(default=False)
    engagement_enabled = models.BooleanField(default=False)
    competitor_enabled = models.BooleanField(default=False)
    influencer_enabled = models.BooleanField(default=False)
    your_company_enabled = models.BooleanField(default=False)
    
    # Simple JSON configurations for keyword-based agents
    job_change_keywords = models.JSONField(default=list, blank=True)
    funding_keywords = models.JSONField(default=list, blank=True)
    engagement_keywords = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "linkedin"
        verbose_name = "Signal Configuration"
        verbose_name_plural = "Signal Configurations"

    def __str__(self):
        return f"Signal Config for Campaign ID {self.campaign_id}"


class CompetitorTarget(CanonicalLinkedInUrlMixin, models.Model):
    config = models.ForeignKey(SignalConfiguration, on_delete=models.CASCADE, related_name="competitor_targets")
    linkedin_url = models.URLField(max_length=500)
    company_name = models.CharField(max_length=200, blank=True)
    
    class Meta:
        app_label = "linkedin"

    def __str__(self):
        return self.company_name or self.linkedin_url


class InfluencerTarget(CanonicalLinkedInUrlMixin, models.Model):
    config = models.ForeignKey(SignalConfiguration, on_delete=models.CASCADE, related_name="influencer_targets")
    linkedin_url = models.URLField(max_length=500)
    name = models.CharField(max_length=200, blank=True)
    
    class Meta:
        app_label = "linkedin"
        
    def __str__(self):
        return self.name or self.linkedin_url


class CompanyTarget(CanonicalLinkedInUrlMixin, models.Model):
    config = models.ForeignKey(SignalConfiguration, on_delete=models.CASCADE, related_name="company_targets")
    linkedin_url = models.URLField(max_length=500)
    company_name = models.CharField(max_length=200, blank=True)
    
    class Meta:
        app_label = "linkedin"
        
    def __str__(self):
        return self.company_name or self.linkedin_url


class IntentSignal(models.Model):
    """
    First-class domain object for intent evidence.
    Multiple signals can accumulate on a single candidate.
    Duplicate observations should be handled by updating `last_seen_at` instead of creating new records.
    """
    class SignalType(models.TextChoices):
        TOP_ICP = "TOP_ICP", "Top ICP"
        JOB_CHANGE = "JOB_CHANGE", "Job Change"
        FUNDING = "FUNDING", "Funding"
        ENGAGEMENT = "ENGAGEMENT", "Content Engagement"
        COMPETITOR = "COMPETITOR", "Competitor Engagement"
        INFLUENCER = "INFLUENCER", "Influencer Engagement"
        YOUR_COMPANY = "YOUR_COMPANY", "Company Engagement"
        
    campaign = models.ForeignKey("linkedin.Campaign", on_delete=models.CASCADE, related_name="intent_signals")
    signal_type = models.CharField(max_length=50, choices=SignalType.choices)
    
    # Provenance
    subject_id = models.CharField(
        max_length=255, 
        help_text="Public identifier of the candidate profile. Must match LinkedInProfile.public_id"
    )
    source = models.CharField(
        max_length=500, 
        help_text="Origin of the signal (e.g., specific competitor URL or query)"
    )
    evidence = models.JSONField(
        default=dict, 
        blank=True, 
        help_text="Raw evidence data explaining why this signal was created (e.g. comment text, post URL)"
    )
    confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Confidence score from 0.0 to 1.0"
    )
    
    # Timestamps
    observed_at = models.DateTimeField(
        default=timezone.now, 
        help_text="When the event occurred in the real world (e.g. comment date)"
    )
    first_seen_at = models.DateTimeField(
        auto_now_add=True, 
        help_text="When our system first discovered this signal"
    )
    last_seen_at = models.DateTimeField(
        auto_now=True, 
        help_text="When our system last updated this signal"
    )
    
    # Deduplication
    dedupe_key = models.CharField(
        max_length=255, 
        help_text="Unique key to prevent duplicate signals for the exact same observation. Format: <subject_id>:<signal_type>:<evidence_hash>"
    )
    
    class Meta:
        app_label = "linkedin"
        constraints = [
            models.UniqueConstraint(fields=["campaign", "dedupe_key"], name="unique_signal_dedupe"),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0.0) & models.Q(confidence__lte=1.0),
                name="confidence_range_check",
                violation_error_message="Confidence must be between 0.0 and 1.0"
            )
        ]
        indexes = [
            models.Index(fields=["campaign", "subject_id"]),
            models.Index(fields=["campaign", "signal_type"]),
        ]

    def __str__(self):
        return f"{self.signal_type} for {self.subject_id} (Campaign {self.campaign_id})"
