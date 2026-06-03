from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from linkedin.enums import ProfileState


class Outcome(models.TextChoices):
    CONVERTED = "converted"
    NOT_INTERESTED = "not_interested"
    WRONG_FIT = "wrong_fit"
    NO_BUDGET = "no_budget"
    HAS_SOLUTION = "has_solution"
    BAD_TIMING = "bad_timing"
    UNRESPONSIVE = "unresponsive"
    UNKNOWN = "unknown"


class Deal(models.Model):
    class Meta:
        verbose_name = _("Deal")
        verbose_name_plural = _("Deals")
        constraints = [
            models.UniqueConstraint(fields=["lead", "campaign"], name="unique_deal_per_campaign"),
        ]

    lead = models.ForeignKey("Lead", on_delete=models.CASCADE)
    campaign = models.ForeignKey(
        "linkedin.Campaign", on_delete=models.CASCADE, related_name="deals",
    )
    state = models.CharField(
        max_length=20,
        choices=[(s.value, s.value) for s in ProfileState],
        default=ProfileState.QUALIFIED,
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        blank=True,
        default="",
    )
    reason = models.TextField(blank=True, default="")
    connect_attempts = models.IntegerField(default=0)
    backoff_hours = models.IntegerField(default=0)
    profile_summary = models.JSONField(null=True, blank=True, default=None)
    chat_summary = models.JSONField(null=True, blank=True, default=None)
    creation_date = models.DateTimeField(default=timezone.now)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        lead_str = str(self.lead) if self.lead_id else "?"
        return f"{lead_str} [{self.state}]"

    def briefing(self) -> str:
        """Human-readable call briefing from all accumulated intelligence.

        Combines profile_summary, chat_summary, and campaign.market_persona
        into a compact, scannable text block. Designed for a quick read
        before a phone call or WhatsApp message.
        """
        sections: list[str] = []

        # ── Lead profile ──
        profile_facts = (self.profile_summary or {}).get("facts", [])
        if profile_facts:
            lines = ["## About the lead"]
            lines.extend(f"  • {f}" for f in profile_facts)
            sections.append("\n".join(lines))

        # ── Conversation so far ──
        chat_facts = (self.chat_summary or {}).get("facts", [])
        if chat_facts:
            lines = ["## From the conversation"]
            lines.extend(f"  • {f}" for f in chat_facts)
            sections.append("\n".join(lines))

        # ── Market context ──
        market_facts = (self.campaign.market_persona or {}).get("facts", []) if self.campaign_id else []
        if market_facts:
            lines = ["## Market context"]
            lines.extend(f"  • {f}" for f in market_facts)
            sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "(no intelligence gathered yet)"
