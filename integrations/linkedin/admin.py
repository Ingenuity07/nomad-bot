from django.contrib import admin

from .models import ContentBrief, LinkedInAutomationSettings, LinkedInPost


@admin.register(LinkedInAutomationSettings)
class LinkedInAutomationSettingsAdmin(admin.ModelAdmin):
    list_display = ("page_name", "publisher", "approval_mode", "is_active", "updated_at")


@admin.register(ContentBrief)
class ContentBriefAdmin(admin.ModelAdmin):
    list_display = ("label", "settings", "is_evergreen", "is_active", "updated_at")
    list_filter = ("is_evergreen", "is_active")


@admin.register(LinkedInPost)
class LinkedInPostAdmin(admin.ModelAdmin):
    list_display = ("topic", "settings", "status", "scheduled_for", "published_at")
    list_filter = ("status", "settings")
    search_fields = ("topic", "body")

