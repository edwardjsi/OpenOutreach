# crm/admin.py
from django.contrib import admin

from crm.models.deal import Deal
from crm.models.lead import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("public_identifier", "linkedin_url", "disqualified", "creation_date")
    list_filter = ("disqualified",)
    search_fields = ("public_identifier", "linkedin_url")
    date_hierarchy = "creation_date"
    readonly_fields = ("creation_date", "update_date")


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = (
        "lead_identifier", "campaign", "state", "outcome",
        "connect_attempts", "creation_date",
    )
    list_filter = ("state", "outcome", "campaign")
    search_fields = ("lead__public_identifier",)
    date_hierarchy = "creation_date"
    readonly_fields = (
        "creation_date", "update_date", "briefing_display",
        "profile_summary_raw", "chat_summary_raw",
    )

    fieldsets = (
        (None, {
            "fields": (
                "lead", "campaign", "state", "outcome", "reason",
                "connect_attempts", "backoff_hours",
            ),
        }),
        ("Call Briefing", {
            "fields": ("briefing_display",),
            "description": "Everything you need before a call — lead profile, conversation history, and market context.",
        }),
        ("Raw Intelligence (debug)", {
            "fields": ("profile_summary_raw", "chat_summary_raw"),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("creation_date", "update_date"),
        }),
    )

    @admin.display(description="Lead", ordering="lead__public_identifier")
    def lead_identifier(self, obj):
        return obj.lead.public_identifier

    @admin.display(description="Call Briefing")
    def briefing_display(self, obj):
        from django.utils.html import format_html

        text = obj.briefing()
        if text == "(no intelligence gathered yet)":
            return text
        # Render markdown-ish headings as HTML for readability in admin.
        import re
        html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = re.sub(r"^## (.+)$", r"<h3 style='margin:12px 0 4px;font-size:14px;'>\1</h3>", html, flags=re.MULTILINE)
        html = html.replace("\n", "<br>")
        return format_html(html)

    @admin.display(description="Profile Facts (JSON)")
    def profile_summary_raw(self, obj):
        return obj.profile_summary

    @admin.display(description="Chat Facts (JSON)")
    def chat_summary_raw(self, obj):
        return obj.chat_summary
