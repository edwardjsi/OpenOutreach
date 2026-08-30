# linkedin/admin.py
from django.contrib import admin

from chat.models import ChatMessage

from linkedin.models import ActionLog, Campaign, LinkedInProfile, SearchKeyword, SiteConfig, Task


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "llm_provider", "ai_model", "work_shift_hours", "enable_active_hours")

    fieldsets = (
        ("LLM", {
            "fields": ("llm_provider", "llm_api_key", "ai_model", "llm_api_base"),
        }),
        ("Work Shift", {
            "fields": (
                "enable_active_hours",
                "work_shift_hours",
            ),
            "description": (
                "When enabled, the daemon works for the configured number of "
                "hours from the moment it starts, then idles until restarted. "
                "Each daemon start runs a fresh shift. Uncheck for 24/7."
            ),
        }),
        ("Telegram Alerts", {
            "fields": ("telegram_bot_token", "telegram_chat_id"),
            "description": (
                "Optional. When both fields are set, the daemon sends a Telegram "
                "push when LinkedIn blocks on a security checkpoint. "
                "Create a bot via @BotFather (/newbot), get your chat ID from "
                "@userinfobot, then test with: python manage.py testtelegram"
            ),
        }),
        ("Daemon Halt (circuit breaker)", {
            "fields": ("daemon_halt", "daemon_halt_reason"),
            "description": (
                "Set automatically when LinkedIn serves a security checkpoint — "
                "the daemon idles with ZERO requests. Solve the challenge via VNC, "
                "then uncheck 'daemon_halt' here and restart the daemon."
            ),
        }),
    )

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "booking_link", "is_freemium", "action_fraction")
    filter_horizontal = ("users",)


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "linkedin_username", "active", "legal_accepted")
    list_filter = ("active",)
    raw_id_fields = ("user", "self_lead")


@admin.register(SearchKeyword)
class SearchKeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "campaign", "used", "used_at")
    list_filter = ("used", "campaign")
    raw_id_fields = ("campaign",)


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("action_type", "linkedin_profile", "campaign", "created_at")
    list_filter = ("action_type", "campaign")
    raw_id_fields = ("linkedin_profile", "campaign")
    date_hierarchy = "created_at"
    readonly_fields = ("linkedin_profile", "campaign", "action_type", "created_at")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "status", "scheduled_at", "payload", "created_at")
    list_filter = ("task_type", "status")
    readonly_fields = (
        "task_type", "status", "scheduled_at", "payload",
        "created_at", "started_at", "completed_at",
    )
    date_hierarchy = "scheduled_at"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("content_type", "object_id", "owner", "creation_date")
    list_filter = ("content_type", "owner")
    raw_id_fields = ("owner", "answer_to", "topic")
    date_hierarchy = "creation_date"
    readonly_fields = ("content_type", "object_id", "content", "owner", "creation_date")
