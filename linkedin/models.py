# linkedin/models.py
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# action_type → (daily_limit_field, weekly_limit_field)
_RATE_LIMIT_FIELDS = {
    "connect": ("connect_daily_limit", "connect_weekly_limit"),
    "follow_up": ("follow_up_daily_limit", None),
    "engage": ("engage_daily_limit", None),
    "like": ("like_daily_limit", None),
}


class SiteConfig(models.Model):
    """Singleton model for global site configuration (LLM keys, etc.)."""

    class LLMProvider(models.TextChoices):
        OPENAI = "openai", "OpenAI"
        ANTHROPIC = "anthropic", "Anthropic"
        GOOGLE = "google", "Google"
        GROQ = "groq", "Groq"
        MISTRAL = "mistral", "Mistral"
        COHERE = "cohere", "Cohere"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"

    llm_provider = models.CharField(
        max_length=32,
        choices=LLMProvider.choices,
        default=LLMProvider.OPENAI,
    )
    llm_api_key = models.CharField(max_length=500, blank=True, default="")
    ai_model = models.CharField(max_length=200, blank=True, default="")
    llm_api_base = models.CharField(max_length=500, blank=True, default="")

    # ── AI Comment Tuning ──
    ai_persona_prompt = models.TextField(
        blank=True,
        default="You are an expert financial planner. Your goal is to react to LinkedIn posts with insightful, professional, and engaging comments that establish your authority and add value.",
        help_text="The system prompt instructing the LLM on its persona."
    )
    ai_tone_prompt = models.TextField(
        blank=True,
        default="Write exactly three distinct comments based on these three tones. Keep them max 3 sentences each:\n1. Polite: A standard, polished, and complimentary response.\n2. Contrarian: Politely challenging or offering an alternative perspective to spark debate. DO NOT start with 'While I' or use repetitive opening phrases. Vary your approach significantly each time.\n3. Cheerleading: Enthusiastic support and validation of the author's point or milestone.",
        help_text="The specific instructions for the 3 tones to generate."
    )

    # ── Work-shift schedule (editable in Admin)
    enable_active_hours = models.BooleanField(
        default=True,
        help_text="Limit the daemon to a work shift per start; uncheck for 24/7.",
    )
    last_shift_completed_date = models.DateField(null=True, blank=True, help_text="Date when the daemon last completed a full shift.")

    work_shift_hours = models.PositiveSmallIntegerField(
        default=2,
        help_text="Hours the daemon works from the moment it starts; "
        "then it idles until the daemon is restarted.",
    )

    # ── Telegram alerts (checkpoint manual-resolution notification)
    telegram_bot_token = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Telegram bot token from @BotFather /newbot.",
    )
    telegram_chat_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Your numeric chat ID (from @userinfobot) or @channelusername.",
    )

    # ── Circuit breaker (security-checkpoint halt) ──
    # When LinkedIn serves a checkpoint challenge, the daemon sets this flag
    # and idles with ZERO requests instead of pausing-and-resuming on the
    # flagged account. The operator solves the challenge via VNC, then clears
    # the flag here (Admin → Site Configuration) and restarts the daemon.
    daemon_halt = models.BooleanField(
        default=False,
        help_text="When set, the daemon idles without making any LinkedIn request. "
        "Set automatically when a security checkpoint is detected; clear it after "
        "solving the challenge to resume.",
    )
    daemon_halt_reason = models.TextField(
        blank=True,
        default="",
        help_text="Why the daemon was halted (set automatically on checkpoint).",
    )

    class Meta:
        app_label = "linkedin"
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def __str__(self):
        return "Site Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Campaign(models.Model):
    name = models.CharField(max_length=200, unique=True)
    users = models.ManyToManyField(User, blank=True, related_name="campaigns")
    product_docs = models.TextField(blank=True)
    campaign_objective = models.TextField(blank=True)
    booking_link = models.URLField(max_length=500, blank=True)
    is_freemium = models.BooleanField(default=False)
    action_fraction = models.FloatField(default=0.2)
    seed_public_ids = models.JSONField(default=list, blank=True)
    model_blob = models.BinaryField(null=True, blank=True)
    market_persona = models.JSONField(null=True, blank=True, default=None)
    search_geo_urn = models.CharField(
        max_length=256,
        blank=True,
        default="103544278,103644278,101165590,101174742,106442593,102454443",
        help_text=(
            "Comma-separated LinkedIn geo URNs scoping People search to "
            "those geographies (India=103544278, US=103644278, "
            "UK=101165590, Canada=101174742, UAE=106442593, "
            "Singapore=102454443). Empty = no geography filter."
        ),
    )

    def __str__(self):
        return self.name

    class Meta:
        app_label = "linkedin"


class LinkedInProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="linkedin_profile",
    )
    self_lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    linkedin_username = models.CharField(max_length=200)
    linkedin_password = models.CharField(max_length=200)
    subscribe_newsletter = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    connect_daily_limit = models.PositiveIntegerField(default=20)
    connect_weekly_limit = models.PositiveIntegerField(default=100)
    follow_up_daily_limit = models.PositiveIntegerField(default=25)
    like_daily_limit = models.PositiveIntegerField(default=5)
    legal_accepted = models.BooleanField(default=False)
    cookie_data = models.JSONField(null=True, blank=True)
    newsletter_processed = models.BooleanField(default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exhausted: dict[str, date] = {}

    def can_execute(self, action_type: str) -> bool:
        """Check if the action is allowed under daily/weekly rate limits."""
        # Reset exhaustion flag on a new day
        exhausted_date = self._exhausted.get(action_type)
        if exhausted_date is not None and exhausted_date != date.today():
            del self._exhausted[action_type]
        if action_type in self._exhausted:
            return False

        daily_field, weekly_field = _RATE_LIMIT_FIELDS[action_type]

        self.refresh_from_db(fields=[daily_field] + ([weekly_field] if weekly_field else []))

        daily_limit = getattr(self, daily_field)
        if daily_limit is not None and self._daily_count(action_type) >= daily_limit:
            return False

        if weekly_field:
            weekly_limit = getattr(self, weekly_field)
            if weekly_limit is not None and self._weekly_count(action_type) >= weekly_limit:
                return False

        return True

    def record_action(self, action_type: str, campaign: Campaign) -> None:
        """Persist a rate-limited action."""
        ActionLog.objects.create(
            linkedin_profile=self, campaign=campaign, action_type=action_type,
        )

    def mark_exhausted(self, action_type: str) -> None:
        """Mark the action type as externally exhausted for today."""
        self._exhausted[action_type] = date.today()
        logger.warning("Rate limit: %s externally exhausted for today", action_type)

    def _daily_count(self, action_type: str) -> int:
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return ActionLog.objects.filter(
            linkedin_profile=self, action_type=action_type,
            created_at__gte=today_start,
        ).count()

    def _weekly_count(self, action_type: str) -> int:
        now = timezone.now()
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return ActionLog.objects.filter(
            linkedin_profile=self, action_type=action_type,
            created_at__gte=monday,
        ).count()

    def __str__(self):
        return f"{self.user.username} ({self.linkedin_username})"

    class Meta:
        app_label = "linkedin"


class SearchKeyword(models.Model):
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="search_keywords",
    )
    keyword = models.CharField(max_length=500)
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "linkedin"
        unique_together = [("campaign", "keyword")]

    def __str__(self):
        return self.keyword


class ActionLog(models.Model):
    class ActionType(models.TextChoices):
        CONNECT = "connect", "Connect"
        FOLLOW_UP = "follow_up", "Follow Up"
        ENGAGE = "engage", "Feed Engagement"
        LIKE = "like", "Like Profile"

    linkedin_profile = models.ForeignKey(
        LinkedInProfile,
        on_delete=models.CASCADE,
        related_name="action_logs",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="action_logs",
    )
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "linkedin"
        indexes = [
            models.Index(fields=["linkedin_profile", "action_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action_type} by {self.linkedin_profile} at {self.created_at}"


class TaskQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=Task.Status.PENDING).order_by("scheduled_at")

    def claim_next(self) -> "Task | None":
        return self.pending().filter(scheduled_at__lte=timezone.now()).first()

    def seconds_to_next(self) -> float | None:
        """Seconds until the next pending task, or None if queue is empty."""
        next_task = self.pending().only("scheduled_at").first()
        if next_task is None:
            return None
        return max((next_task.scheduled_at - timezone.now()).total_seconds(), 0)


class Task(models.Model):
    class TaskType(models.TextChoices):
        CONNECT = "connect"
        CHECK_PENDING = "check_pending"
        FOLLOW_UP = "follow_up"
        LIKE = "like"
        DRAFT_COMMENTS = "draft_comments"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    scheduled_at = models.DateTimeField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    objects = TaskQuerySet.as_manager()

    class Meta:
        app_label = "linkedin"
        indexes = [
            models.Index(fields=["status", "scheduled_at"]),
        ]

    def __str__(self):
        return f"{self.task_type} [{self.status}] scheduled={self.scheduled_at}"

    def mark_running(self):
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_completed(self):
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])

    def mark_failed(self):
        self.failure_count = self.failure_count + 1
        self.status = self.Status.FAILED
        self.save(update_fields=["status", "failure_count"])


class Influencer(models.Model):
    """List of influencers to explicitly monitor for new posts."""
    GROUP_CHOICES = [
        ('daily', 'Daily Posters'),
        ('regular', 'Regular Posters (Coupla times a week)'),
        ('rare', 'Rare Posters'),
    ]
    linkedin_url = models.URLField(max_length=500, unique=True)
    username = models.CharField(max_length=200, blank=True)
    name = models.CharField(max_length=200, blank=True)
    is_organic = models.BooleanField(default=False)
    engagement_group = models.CharField(max_length=20, choices=GROUP_CHOICES, default='regular')
    follower_count = models.IntegerField(null=True, blank=True)
    avg_comments = models.FloatField(null=True, blank=True)
    last_classified_at = models.DateTimeField(null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "linkedin"
        verbose_name = "Influencer"
        verbose_name_plural = "Influencers"

    def __str__(self):
        return self.name or self.username or self.linkedin_url


class DraftedComment(models.Model):
    """Tracks posts we've already drafted comments for to prevent duplicates, and stores the drafts."""
    post_urn = models.CharField(max_length=200, unique=True)
    author_name = models.CharField(max_length=200, blank=True)
    post_url = models.URLField(max_length=500, blank=True)
    drafts_text = models.TextField(blank=True)
    drafted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "linkedin"

    def __str__(self):
        return f"Draft for {self.post_urn}"
