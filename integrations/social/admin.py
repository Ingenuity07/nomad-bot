from django import forms
from django.contrib import admin

from .models import (
    BrandProfile,
    BrandProfileVersion,
    AnalyticsSuggestion,
    ContentStudioOnboarding,
    ContentSource,
    MediaAsset,
    ProviderEvent,
    SocialMetricObservation,
    PublishAttempt,
    PublishJob,
    SocialConnection,
    SocialAuditEvent,
    SocialPost,
    SocialPostVariant,
    SocialPostVersion,
    SocialWorkspaceSettings,
    StoryInterview,
    VoiceRuleSuggestion,
)
from .services.publishing_routing import provider_readiness, selected_provider


PUBLISHER_CHOICES = (
    ("UPLOAD_POST", "Upload Post"),
    ("ZERNIO", "Zernio"),
)


@admin.register(ContentStudioOnboarding)
class ContentStudioOnboardingAdmin(admin.ModelAdmin):
    list_display = ("workspace", "current_step", "draft_only_mode", "started_at", "completed_at", "updated_at")
    readonly_fields = (
        "workspace", "current_step", "completed_steps", "answers", "draft_only_mode",
        "connection_provider", "connection_expires_at", "connection_error",
        "started_at", "completed_at", "created_at", "updated_at",
    )
    exclude = ("connection_state",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SocialWorkspaceSettingsAdminForm(forms.ModelForm):
    disabled_publishing_providers = forms.MultipleChoiceField(
        choices=PUBLISHER_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Disabled providers cannot receive new connections or publish jobs.",
    )

    class Meta:
        model = SocialWorkspaceSettings
        fields = "__all__"


@admin.register(SocialWorkspaceSettings)
class SocialWorkspaceSettingsAdmin(admin.ModelAdmin):
    form = SocialWorkspaceSettingsAdminForm
    list_display = (
        "brand_name",
        "workspace",
        "effective_publisher",
        "provider_readiness_summary",
        "approval_mode",
        "is_active",
        "updated_at",
    )
    readonly_fields = ("effective_publisher", "provider_readiness_summary")

    @admin.display(description="Effective publisher")
    def effective_publisher(self, obj):
        return selected_provider(obj.workspace).value if obj and obj.workspace_id else "-"

    @admin.display(description="Provider readiness")
    def provider_readiness_summary(self, obj):
        if not obj or not obj.workspace_id:
            return "Save the workspace settings to view readiness."
        return "; ".join(
            f"{row.provider.value}: "
            f"{'disabled' if not row.enabled else 'ready' if row.configured and row.healthy else 'not ready'}"
            for row in provider_readiness(obj.workspace)
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not (
            request.user.is_superuser
            or obj and obj.workspace.memberships.filter(
                user=request.user,
                role__in=["OWNER", "ADMIN"],
            ).exists()
        ):
            fields.extend(["publishing_provider_override", "disabled_publishing_providers"])
        return tuple(fields)


@admin.register(BrandProfile)
class BrandProfileAdmin(admin.ModelAdmin):
    list_display = ("settings", "voice", "updated_at")


@admin.register(ContentSource)
class ContentSourceAdmin(admin.ModelAdmin):
    list_display = ("label", "workspace", "owner", "source_type", "processing_status", "is_reusable", "is_active", "updated_at")
    list_filter = ("source_type", "processing_status", "is_reusable", "is_active")


@admin.register(BrandProfileVersion)
class BrandProfileVersionAdmin(admin.ModelAdmin):
    list_display = ("brand_profile", "version", "created_by", "created_at")
    readonly_fields = ("brand_profile", "version", "snapshot", "created_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VoiceRuleSuggestion)
class VoiceRuleSuggestionAdmin(admin.ModelAdmin):
    list_display = ("suggested_rule", "brand_profile", "evidence_count", "status", "updated_at")
    list_filter = ("status",)
    readonly_fields = ("brand_profile", "signal_key", "suggested_rule", "evidence_count", "evidence", "status", "confirmed_by", "confirmed_at", "created_at", "updated_at")


@admin.register(StoryInterview)
class StoryInterviewAdmin(admin.ModelAdmin):
    list_display = ("workspace", "week_of", "owner", "status", "approved_at")
    list_filter = ("status", "week_of")


@admin.register(SocialConnection)
class SocialConnectionAdmin(admin.ModelAdmin):
    list_display = ("display_name", "workspace", "network", "provider", "status", "updated_at")
    list_filter = ("network", "provider", "status")


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    list_display = ("idea_title", "workspace", "state", "created_at")
    list_filter = ("state",)


@admin.register(SocialPostVariant)
class SocialPostVariantAdmin(admin.ModelAdmin):
    list_display = ("post", "network", "status", "scheduled_for")
    list_filter = ("network", "status")


@admin.register(SocialPostVersion)
class SocialPostVersionAdmin(admin.ModelAdmin):
    list_display = ("variant", "version", "approved_at", "created_at")


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ("post", "workspace", "asset_type", "sort_order", "updated_at")
    list_filter = ("asset_type",)


@admin.register(PublishJob)
class PublishJobAdmin(admin.ModelAdmin):
    list_display = (
        "variant", "provider", "status", "scheduled_for", "attempt_count", "external_id", "updated_at",
    )
    list_filter = ("provider", "status")


@admin.register(PublishAttempt)
class PublishAttemptAdmin(admin.ModelAdmin):
    list_display = ("job", "attempt_number", "status", "external_id", "started_at", "completed_at")
    list_filter = ("status",)


@admin.register(ProviderEvent)
class ProviderEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "workspace", "provider", "normalized_status", "received_at")
    list_filter = ("provider", "normalized_status")


@admin.register(SocialMetricObservation)
class SocialMetricObservationAdmin(admin.ModelAdmin):
    list_display = ("metric_name", "value", "workspace", "provider", "measured_at")
    list_filter = ("metric_name", "provider")
    readonly_fields = tuple(field.name for field in SocialMetricObservation._meta.fields)


@admin.register(AnalyticsSuggestion)
class AnalyticsSuggestionAdmin(admin.ModelAdmin):
    list_display = ("segment", "dimension", "workspace", "status", "created_at")
    list_filter = ("dimension", "status")
    readonly_fields = ("fingerprint", "evidence", "decided_by", "decided_at", "applied_brand_version")


@admin.register(SocialAuditEvent)
class SocialAuditEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "workspace", "actor", "target_type", "target_id", "created_at")
    list_filter = ("event_type", "created_at")
    readonly_fields = tuple(field.name for field in SocialAuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
